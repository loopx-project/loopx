#!/usr/bin/env sh
# Runs in a separate user-service unit so updating the node cannot interrupt the updater.
set -u
service=$1 socket=$2 repo=$3 dry=$4 poll=$5
say() { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "fail $*"; exit 0; }
case "$repo" in
  '')
    repo="$HOME/code/loochat"
    for candidate in "${XDG_DATA_HOME:-$HOME/.local/share}/loochat" "$HOME/.local/share/loochat" "$HOME/code/loochat"; do
      if [ -d "$candidate/.git" ]; then repo="$candidate"; break; fi
    done
    ;;
  '~'*) repo="$HOME/${repo#\~/}" ;;
esac
case "$socket" in '~'*) socket="$HOME/${socket#\~/}" ;; esac

unit="$(systemctl --user show -p FragmentPath --value "$service" 2>/dev/null || true)"
[ -n "$unit" ] && [ -f "$unit" ] || fail "no $service user service on this machine"
workdir="$(systemctl --user show -p WorkingDirectory --value "$service")"

git_root=''
if [ -d "$repo/.git" ]; then
  git_root="$(cd "$repo" 2>/dev/null && pwd -P || true)"
elif [ -d "$repo" ]; then
  top="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$top" ] && [ -d "$top/.git" ] && [ -d "$top/apps/workbench" ] \
    && [ "$(cd "$repo" && pwd -P)" = "$(cd "$top/apps/workbench" && pwd -P)" ]; then
    git_root="$(cd "$top" && pwd -P)"
  fi
fi
[ -n "$git_root" ] || fail "no clone at $repo (set \"repo\" in the hub's hosts.json)"
cd "$git_root" || fail "cannot enter $git_root"

running_rev=''
if [ -n "$workdir" ] && [ -f "$workdir/.loochat-release" ]; then
  running_rev="$(cat "$workdir/.loochat-release")"
fi

[ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ] \
  || fail "the clone has local changes; it needs a person"
head_branch="$(git symbolic-ref -q --short HEAD 2>/dev/null || true)"
if [ -n "$head_branch" ]; then
  git fetch --quiet origin 2>/dev/null || fail "git fetch from origin failed (offline?)"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "origin/$head_branch")"
  if ! git merge-base --is-ancestor "$upstream" HEAD; then
    git merge --ff-only --quiet "$upstream" 2>/dev/null || fail "the clone has moved away from $upstream; it needs a person"
  fi
elif [ -f .git/loochat-installed-revision ]; then
  [ "$(git rev-parse HEAD)" = "$(cat .git/loochat-installed-revision)" ] \
    || fail "the clone has local commits; it needs a person"
  git fetch --quiet --depth 1 origin main 2>/dev/null || fail "git fetch from origin failed (offline?)"
  git checkout --detach --quiet FETCH_HEAD 2>/dev/null || fail "cannot advance detached install checkout"
  git rev-parse HEAD >.git/loochat-installed-revision
else
  git fetch --quiet origin 2>/dev/null || fail "git fetch from origin failed (offline?)"
  upstream="origin/main"
  if ! git merge-base --is-ancestor "$upstream" HEAD; then
    git merge --ff-only --quiet "$upstream" 2>/dev/null || fail "the clone has moved away from $upstream; it needs a person"
  fi
fi
new="$(git rev-parse --short HEAD)"

if [ -f "$git_root/apps/workbench/package.json" ]; then
  app_src="$git_root/apps/workbench"
elif [ -f "$git_root/package.json" ]; then
  app_src="$git_root"
else
  fail "no workbench package in $git_root"
fi

if [ "$(basename "$workdir")" = "loochat-$new" ] || [ "$running_rev" = "$new" ]; then
  echo "current: the service already runs loochat-$new"
  exit 0
fi
if [ "$dry" = 1 ]; then
  echo "would update: ${workdir##*/} -> loochat-$new"
  exit 0
fi

# A snapshot per version, so the previous one stays to fall back on. It lives next to the ones
# agents have been making by hand; the state home passed in is where the daemon keeps its data.
snap="$HOME/.local/share/loochat-$new"
if [ ! -d "$snap" ]; then
  say "copying the tree into ${snap##*/}"
  mkdir -p "$snap"
  ( cd "$app_src" && tar --exclude=./.git --exclude=./.worktrees --exclude=./shots -cf - . ) | tar -C "$snap" -xf -
fi
say "building ${snap##*/}"
if ! ( cd "$snap" && npm install --no-audit --no-fund >/dev/null 2>&1 && npm run build >/dev/null 2>&1 ); then
  rm -rf "$snap"
  fail "build failed; the running daemon was left alone"
fi
echo "$new" >"$snap/.loochat-release"

# Its own turns end first, the way restart.sh waits for this hub's.
busy_seen=''
while :; do
  busy="$(curl -sf -m 5 --unix-socket "$socket" http://localhost/api/threads 2>/dev/null \
    | node -e 'let b="";process.stdin.setEncoding("utf8");process.stdin.on("data",c=>b+=c);process.stdin.on("end",()=>{try{console.log(JSON.parse(b).filter(t=>t.status==="running").length)}catch{console.log("?")}})' \
    || echo '?')"
  [ "$busy" = "0" ] && break
  [ "$busy" != "$busy_seen" ] && { say "waiting for $busy running thread(s) over there"; busy_seen="$busy"; }
  sleep "$poll"
done

# Which file pins the running release: the lexically last drop-in that sets WorkingDirectory — a
# drop-in outranks the unit file's own line, so editing the unit file alone switches nothing on a
# machine that pins releases this way — or the conventional 90-loochat-release.conf when there is
# none yet. The unit file itself is never touched.
pin_dir="$HOME/.config/systemd/user/${service}.service.d"
pin="$(ls "$pin_dir"/*.conf 2>/dev/null | sort | while IFS= read -r f; do grep -q '^WorkingDirectory=' "$f" && printf '%s\n' "$f"; done | tail -n 1)"
[ -n "$pin" ] || pin="$pin_dir/90-loochat-release.conf"
pin_write() { mkdir -p "$pin_dir"; printf '[Service]\nWorkingDirectory=%s\n' "$1" >"$pin"; }
put_back() {
  [ -n "$workdir" ] && pin_write "$workdir"
  systemctl --user daemon-reload
  "$workdir/scripts/restart.sh" --no-build 2>/dev/null || true
}

say "switching the service onto loochat-$new"
mkdir -p "$pin_dir"
[ -f "$pin" ] && cp "$pin" "$pin.bak-$(date -u +%Y%m%dT%H%M%SZ)"
pin_write "$snap"
if ! systemctl --user daemon-reload || ! "$snap/scripts/restart.sh" --no-build; then
  put_back
  fail "the switch failed; back on ${workdir##*/}"
fi
up=0
for _ in $(seq 1 30); do
  curl -sf -m 2 --unix-socket "$socket" http://localhost/api/health >/dev/null 2>&1 && { up=1; break; }
  sleep 1
done
if [ "$up" != 1 ]; then
  put_back
  fail "no answer after the switch; rolled back onto ${workdir##*/}"
fi
echo "done: the service runs loochat-$new (was ${workdir##*/})"
