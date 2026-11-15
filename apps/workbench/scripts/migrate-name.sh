#!/usr/bin/env bash
# Moves an installation from before the rename to Calli onto the new names, in one short stop:
#
#   - the systemd user service remote-agent-hub becomes calli, and companion units named
#     remote-agent-hub-*.service become calli-*.service
#   - ~/.config/remote-agent-hub becomes ~/.config/calli, and the companions' directories under
#     ~/.config, ~/.local/state and ~/.local/lib move the same way
#   - in .env, RAH_* and LIMI_* keys become CALLI_*; every value, the token included, stays
#
# Every old path is left as a symbolic link to the new one. Two directories are not moved, since
# threads work inside them and an agent may find its session by the directory it runs in:
# ~/.local/state/remote-agent-hub (the data and the scratch area), which ~/.local/state/calli
# links to, and ~/.limi, which Calli keeps using while there is no ~/.calli.
#
#   scripts/migrate-name.sh             build, wait for running turns to finish, switch, check health
#   scripts/migrate-name.sh --no-build  skip `npm install` and `npm run build`
#   scripts/migrate-name.sh --dry-run   say what would change, change nothing
#
# The service comes back on this build. If it does not answer within 30 seconds, everything is put
# back and the old service started again, on this build too. From inside the hub it runs in the
# background, like restart.sh. Log: ~/.local/state/remote-agent-hub/migrate.log
set -Eeuo pipefail

# Overridable only to try the script on a throwaway service.
FROM="${MIGRATE_FROM:-remote-agent-hub}"
TO="${MIGRATE_TO:-calli}"
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
LIB_HOME="$HOME/.local/lib"
STATE_DIR="$STATE_HOME/$FROM"
LOG="$STATE_DIR/migrate.log"
BACKUP="$STATE_DIR/migrate-backup"
POLL=5

DRY=0 BACKGROUND=0 BUILD=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --no-build) BUILD=0 ;;
    --background) BACKGROUND=1 ;; # internal: the detached waiter
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "$*" >&2; exit 1; }
# A condition for switching: a dry run says what is missing and goes on.
need() { if [ "$DRY" = 1 ]; then echo "Before switching: $*"; else die "$*"; fi; }

systemctl --user cat "$FROM" >/dev/null 2>&1 || die "No $FROM service: nothing to move."
! systemctl --user cat "$TO" >/dev/null 2>&1 || die "A $TO service exists already."
UNIT="$(systemctl --user show -p FragmentPath --value "$FROM")"
UNIT_DIR="$(dirname "$UNIT")"
SERVICE_DIR="$(systemctl --user show -p WorkingDirectory --value "$FROM")"
[ "$(realpath "$SERVICE_DIR")" = "$REPO" ] || need "The service runs $SERVICE_DIR, not $REPO. Run this from there."

ENV_FILE="$(systemctl --user show -p Environment --value "$FROM" | tr ' ' '\n' | sed -nE 's/^(CALLI|RAH)_ENV_FILE=//p' | head -1)"
ENV_FILE="${ENV_FILE:-$CONFIG_HOME/$FROM/.env}"
[ -f "$ENV_FILE" ] || die "No .env at $ENV_FILE."
env_raw() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1 | sed -E "s/^['\"]|['\"]$//g"; }
env_get() { local v; v="$(env_raw "CALLI_$1")"; [ -n "$v" ] || v="$(env_raw "RAH_$1")"; echo "$v"; }
HOST="$(env_get HOST)"; HOST="${HOST:-127.0.0.1}"
PORT="$(env_get PORT)"; PORT="${PORT:-8787}"
TOKEN="$(env_get TOKEN)"
case "$HOST" in 0.0.0.0 | ::) HOST=127.0.0.1 ;; esac
BASE="http://$HOST:$PORT"

# Companion units, e.g. remote-agent-hub-https.service, by their suffix ("https").
COMPANIONS=()
for f in "$UNIT_DIR/$FROM"-*.service; do
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  name="$(basename "$f" .service)"
  COMPANIONS+=("${name#"$FROM"-}")
done

# Directories that move, old then new: the config directory and the companions' own.
MOVES=()
[ -d "$CONFIG_HOME/$FROM" ] && [ ! -L "$CONFIG_HOME/$FROM" ] && MOVES+=("$CONFIG_HOME/$FROM" "$CONFIG_HOME/$TO")
for s in "${COMPANIONS[@]}"; do
  for root in "$CONFIG_HOME" "$STATE_HOME" "$LIB_HOME"; do
    [ -d "$root/$FROM-$s" ] && [ ! -L "$root/$FROM-$s" ] && MOVES+=("$root/$FROM-$s" "$root/$TO-$s")
  done
done
for ((i = 0; i < ${#MOVES[@]}; i += 2)); do
  old="${MOVES[i]}" new="${MOVES[i+1]}"
  [ ! -e "$new" ] || need "$new exists already; move it aside first."
  # A rename within one file system is instant; across two it would be a long copy.
  [ "$(stat -c %d "$old")" = "$(stat -c %d "$(dirname "$new")")" ] || need "$old and $new are on different file systems."
done

# Where .env is afterwards: it moves with the config directory when it is inside it.
NEW_ENV="$ENV_FILE"
if [ -d "$CONFIG_HOME/$FROM" ]; then
  real_env="$(realpath "$ENV_FILE")" real_cfg="$(realpath "$CONFIG_HOME/$FROM")"
  case "$real_env" in "$real_cfg"/*) NEW_ENV="$CONFIG_HOME/$TO/${real_env#"$real_cfg"/}" ;; esac
fi

# The units under their new names. Only the names change: the companions' paths into their moved
# directories and their ordering after the main service; nothing else, not the repository's path.
new_unit() {
  sed -E -e "s#^Environment=(RAH|CALLI)_ENV_FILE=.*#Environment=CALLI_ENV_FILE=$NEW_ENV#" \
    -e 's#^Description=.*#Description=Calli#' -e 's#@rah/daemon#@calli/daemon#' "$1"
}
new_companion() {
  sed -E -e "s#$FROM-#$TO-#g" -e "s#$FROM\\.service#$TO.service#g" -e 's#^(Description=.*)Limi#\1Calli#' "$1"
}
# .env keys under their new names; values as they are.
new_env() {
  sed -E -e 's/^(export +)?LIMI_SOCKET=/\1CALLI_TEAM_SOCKET=/' -e 's/^(export +)?LIMI_KEY=/\1CALLI_TEAM_KEY=/' \
    -e 's/^(export +)?(RAH|LIMI)_/\1CALLI_/' "$ENV_FILE"
}

if [ "$DRY" = 1 ]; then
  echo "Service: $FROM -> $TO (stops for a few seconds)"
  for s in "${COMPANIONS[@]}"; do echo "Service: $FROM-$s -> $TO-$s"; done
  for ((i = 0; i < ${#MOVES[@]}; i += 2)); do echo "Move: ${MOVES[i]} -> ${MOVES[i+1]} (old path links to it)"; done
  [ -e "$STATE_HOME/$TO" ] || echo "Link: $STATE_HOME/$TO -> $FROM (not moved: threads work inside it)"
  echo ".env: $NEW_ENV"
  diff <(cat "$ENV_FILE") <(new_env) | sed -nE 's/^([<>]) ([A-Za-z_ ]+)=.*/  \1 \2=…/p' || true
  echo "--- $TO.service"; new_unit "$UNIT" | diff "$UNIT" - || true
  for s in "${COMPANIONS[@]}"; do
    echo "--- $TO-$s.service"; new_companion "$UNIT_DIR/$FROM-$s.service" | diff "$UNIT_DIR/$FROM-$s.service" - || true
  done
  exit 0
fi

# Prints "<title> (<agent>)" for each running thread; fails if the hub does not answer. The lists
# reach node through stdin, never argv, which they outgrew (see restart.sh).
running_threads() {
  local active archived
  active="$(curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "$BASE/api/threads")" || return 1
  archived="$(curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "$BASE/api/threads?archived=1")" || return 1
  printf '%s\0%s\0' "$active" "$archived" | node -e '
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (buf += chunk));
    process.stdin.on("end", () => {
      for (const doc of buf.split("\0")) {
        if (!doc.trim()) continue;
        for (const t of JSON.parse(doc)) if (t.status === "running") console.log(`${t.title || t.id} (${t.agent})`);
      }
    });
  '
}
wait_until_idle() {
  local list last=""
  while list="$(running_threads)" && [ -n "$list" ]; do
    if [ "$list" != "$last" ]; then
      say "Waiting for $(echo "$list" | wc -l) running thread(s):"
      echo "$list" | sed 's/^/  - /'
      last="$list"
    fi
    sleep "$POLL"
  done
}
healthy() {
  local end=$((SECONDS + 30)) body
  while [ "$SECONDS" -lt "$end" ]; do
    if body="$(curl -sf -m 2 "$BASE/api/health")"; then say "Up: $body"; return 0; fi
    sleep 1
  done
  return 1
}
inside_hub() {
  local main pid
  main="$(systemctl --user show -p MainPID --value "$FROM")"
  [ "$main" != 0 ] || return 1
  pid=$$
  while [ "$pid" -gt 1 ]; do
    [ "$pid" = "$main" ] && return 0
    pid="$(ps -o ppid= -p "$pid" | tr -d ' ')"
    [ -n "$pid" ] || return 1
  done
  return 1
}

if [ "$BUILD" = 1 ] && [ "$BACKGROUND" = 0 ]; then
  say "Building."
  (cd "$REPO" && npm install --no-audit --no-fund >/dev/null && npm run build >/dev/null) || die "Build failed; nothing was changed."
fi

if [ "$BACKGROUND" = 0 ] && inside_hub; then
  # Stopping the service would stop this script with it. systemd-run puts the job outside the
  # service's cgroup, so it lives through the stop.
  systemd-run --user --unit="$TO-migrate" --collect -p WorkingDirectory="$REPO" \
    -E HOME="$HOME" -E PATH="$PATH" ${XDG_STATE_HOME:+-E XDG_STATE_HOME="$XDG_STATE_HOME"} ${XDG_CONFIG_HOME:+-E XDG_CONFIG_HOME="$XDG_CONFIG_HOME"} \
    ${MIGRATE_FROM:+-E MIGRATE_FROM="$MIGRATE_FROM"} ${MIGRATE_TO:+-E MIGRATE_TO="$MIGRATE_TO"} \
    /bin/bash -c "exec \"$0\" --background >>\"$LOG\" 2>&1" >/dev/null
  say "Scheduled: the switch happens once every thread is idle, including this one when this turn ends."
  say "Log: $LOG"
  exit 0
fi

wait_until_idle

mkdir -p "$BACKUP"
cp -p "$UNIT" "$BACKUP/"
for s in "${COMPANIONS[@]}"; do cp -p "$UNIT_DIR/$FROM-$s.service" "$BACKUP/"; done
cp -p "$ENV_FILE" "$BACKUP/env"
LINKED_STATE=0

rollback() {
  trap - ERR
  say "Putting everything back."
  systemctl --user disable --now "$TO" "${COMPANIONS[@]/#/$TO-}" 2>/dev/null || true
  rm -f "$UNIT_DIR/$TO.service"
  for s in "${COMPANIONS[@]}"; do rm -f "$UNIT_DIR/$TO-$s.service"; done
  for ((i = 0; i < ${#MOVES[@]}; i += 2)); do
    old="${MOVES[i]}" new="${MOVES[i+1]}"
    if [ -L "$old" ] && [ -d "$new" ] && [ ! -L "$new" ]; then rm "$old"; mv "$new" "$old"; fi
  done
  [ "$LINKED_STATE" = 0 ] || rm -f "$STATE_HOME/$TO"
  cp -p "$BACKUP/env" "$ENV_FILE"
  # The old name, on this build: the workspace it used to start is gone (see restart.sh).
  sed 's#@rah/daemon#@calli/daemon#' "$BACKUP/$(basename "$UNIT")" >"$UNIT"
  for s in "${COMPANIONS[@]}"; do cp -p "$BACKUP/$FROM-$s.service" "$UNIT_DIR/"; done
  systemctl --user daemon-reload
  systemctl --user enable "$FROM" "${COMPANIONS[@]/#/$FROM-}" >/dev/null 2>&1 || true
  systemctl --user start "$FROM" "${COMPANIONS[@]/#/$FROM-}"
  healthy && say "Back on $FROM." || say "$FROM does not answer either. Logs: journalctl --user -u $FROM -n 50"
  exit 1
}
trap rollback ERR

say "Stopping $FROM."
systemctl --user stop "${COMPANIONS[@]/#/$FROM-}" "$FROM"
for ((i = 0; i < ${#MOVES[@]}; i += 2)); do
  old="${MOVES[i]}" new="${MOVES[i+1]}"
  mv "$old" "$new"
  ln -s "$(basename "$new")" "$old"
done
if [ -d "$STATE_DIR" ] && [ ! -e "$STATE_HOME/$TO" ]; then ln -s "$FROM" "$STATE_HOME/$TO"; LINKED_STATE=1; fi
new_env >"$NEW_ENV.tmp" && chmod --reference="$NEW_ENV" "$NEW_ENV.tmp" && mv "$NEW_ENV.tmp" "$NEW_ENV"
new_unit "$UNIT" >"$UNIT_DIR/$TO.service"
for s in "${COMPANIONS[@]}"; do new_companion "$UNIT_DIR/$FROM-$s.service" >"$UNIT_DIR/$TO-$s.service"; done
systemctl --user disable "$FROM" "${COMPANIONS[@]/#/$FROM-}" >/dev/null 2>&1 || true
rm -f "$UNIT"
for s in "${COMPANIONS[@]}"; do rm -f "$UNIT_DIR/$FROM-$s.service"; done
systemctl --user daemon-reload
systemctl --user enable "$TO" "${COMPANIONS[@]/#/$TO-}" >/dev/null
say "Starting $TO."
systemctl --user start "$TO" "${COMPANIONS[@]/#/$TO-}"
healthy || { say "No answer from $BASE/api/health after 30s."; rollback; }
trap - ERR
say "Done. The old paths link to the new ones; backups are in $BACKUP."
say "Status: systemctl --user status $TO    Logs: journalctl --user -u $TO -f"
