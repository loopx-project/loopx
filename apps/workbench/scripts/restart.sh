#!/usr/bin/env bash
# Rebuilds and restarts the daemon's systemd user service (see install-service.sh) without
# cutting off agents mid-turn: it waits until no thread is running, unless --force.
#
#   scripts/restart.sh             build, wait for running turns to finish, restart, check health
#   scripts/restart.sh --force     build and restart now; running turns end with an error
#   scripts/restart.sh --no-build  skip `npm run build`
#
# A hub that fails to answer counts as busy, never as idle, and idleness is confirmed by two
# consecutive empty polls; a hub with nothing listening at all is restarted right away.
#
# Run from inside the hub (by an agent in one of its threads), it cannot wait in the foreground:
# its own turn would never finish. It schedules the restart in the background instead, for when
# every turn, including the caller's, has ended. Log: $XDG_STATE_HOME/calli/restart.log
set -euo pipefail

NAME=calli
# The service's name before the rename to Calli; an installation not moved yet still runs it.
if ! systemctl --user cat "$NAME" >/dev/null 2>&1 && systemctl --user cat remote-agent-hub >/dev/null 2>&1; then
  NAME=remote-agent-hub
fi
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$NAME"
LOG="$STATE_DIR/restart.log"
POLL="${CALLI_RESTART_POLL:-5}" # seconds between checks; mainly a knob for tests
# Consecutive empty polls that confirm idleness before restarting. A single clean poll after a
# missed answer once restarted the hub under four running turns; two make that impossible.
CLEAN_POLLS="${CALLI_RESTART_CLEAN_POLLS:-2}"

FORCE=0 BUILD=1 BACKGROUND=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --no-build) BUILD=0 ;;
    --background) BACKGROUND=1 ;; # internal: the detached waiter
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "$*" >&2; exit 1; }

systemctl --user cat "$NAME" >/dev/null 2>&1 \
  || die "No $NAME service. Install it first: scripts/install-service.sh"

# The service must run this checkout; restarting it from another worktree would not load this build.
SERVICE_DIR="$(systemctl --user show -p WorkingDirectory --value "$NAME")"
[ "$(realpath "$SERVICE_DIR")" = "$REPO" ] \
  || die "The service runs $SERVICE_DIR, not $REPO. Restart it from there."

ENV_FILE="$(systemctl --user show -p Environment --value "$NAME" | tr ' ' '\n' | sed -nE 's/^(CALLI|RAH)_ENV_FILE=//p' | head -1)"
ENV_FILE="${ENV_FILE:-$HOME/.config/$NAME/.env}"
env_raw() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1 | sed -E "s/^['\"]|['\"]$//g"; }
# CALLI_<name>, else RAH_<name> from before the rename.
env_get() { local v; v="$(env_raw "CALLI_$1")"; [ -n "$v" ] || v="$(env_raw "RAH_$1")"; echo "$v"; }
HOST="$(env_get HOST)"; HOST="${HOST:-127.0.0.1}"
PORT="$(env_get PORT)"; PORT="${PORT:-8787}"
TOKEN="$(env_get TOKEN)"
case "$HOST" in 0.0.0.0 | ::) HOST=127.0.0.1 ;; esac
BASE="http://$HOST:$PORT"

# Fetches a thread list. Status 0: answered, body on stdout. Status 1: no answer (busy or
# broken; callers must keep waiting, not restart). Status 2: nothing is listening at $BASE at
# all, so no turn can be running and restarting is safe.
fetch_threads() {
  local body rc=0
  body="$(curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "$BASE/api/threads$1")" || rc=$?
  [ "$rc" -ne 7 ] || return 2 # curl 7: connection refused
  [ "$rc" -eq 0 ] || return 1
  printf '%s' "$body"
}

# Prints "<title> (<agent>)" for each running thread, archived ones included.
running_threads() {
  local active archived
  active="$(fetch_threads "")" || return
  archived="$(fetch_threads "?archived=1")" || return
  # The thread list outgrew the kernel's per-argument limit (~128K), which used to fail exec
  # ("Argument list too long") and read as "no threads running", restarting under active turns.
  # So the lists reach node through stdin, NUL-separated, never through argv.
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
  local list rc last="" clean=0
  while :; do
    rc=0
    list="$(running_threads)" || rc=$?
    if [ "$rc" -eq 2 ]; then
      say "Nothing answers at $BASE; restarting brings the hub back."
      return 0
    fi
    if [ "$rc" -ne 0 ]; then
      # A busy hub can miss the timeout; that is "unknown", never "idle".
      if [ "$last" != "?no-answer" ]; then
        say "Hub did not answer; retrying."
        last="?no-answer"
      fi
      clean=0
    elif [ -n "$list" ]; then
      clean=0
      if [ "$list" != "$last" ]; then
        say "Waiting for $(echo "$list" | wc -l) running thread(s):"
        echo "$list" | sed 's/^/  - /'
        last="$list"
      fi
    else
      clean=$((clean + 1))
      if [ "$clean" -ge "$CLEAN_POLLS" ]; then
        return 0
      fi
    fi
    sleep "$POLL"
  done
}

check_health() {
  for _ in $(seq 1 30); do
    if body="$(curl -sf -m 2 "$BASE/api/health")"; then
      say "Up: $body"
      return 0
    fi
    sleep 1
  done
  say "No answer from $BASE/api/health after 30s."
  say "Status: systemctl --user status $NAME    Logs: journalctl --user -u $NAME -n 50"
  return 1
}

# Whether this script runs under the service, e.g. an agent's shell inside a hub thread.
inside_hub() {
  local main pid
  main="$(systemctl --user show -p MainPID --value "$NAME")"
  [ "$main" != 0 ] || return 1
  pid=$$
  while [ "$pid" -gt 1 ]; do
    [ "$pid" = "$main" ] && return 0
    pid="$(ps -o ppid= -p "$pid" | tr -d ' ')"
    [ -n "$pid" ] || return 1
  done
  return 1
}

if [ "$BACKGROUND" = 1 ]; then
  # Only one waiter at a time; a second request is already covered by the first.
  exec 9>"$STATE_DIR/restart.lock"
  flock -n 9 || { say "A restart is already scheduled."; exit 0; }
  wait_until_idle
  say "Restarting $NAME."
  # --no-block: restarting stops this process too (it lives in the service's cgroup); the job
  # itself belongs to systemd and finishes regardless.
  systemctl --user restart --no-block "$NAME"
  exit 0
fi

if [ "$BUILD" = 1 ]; then
  # The workspaces were renamed with the app (@rah/* to @calli/*); their links come from npm install.
  if [ ! -e "$REPO/node_modules/@calli/extension-api" ]; then
    say "Linking the renamed workspaces."
    (cd "$REPO" && npm install --no-audit --no-fund >/dev/null) || die "npm install failed; the running hub was left alone."
  fi
  say "Building."
  (cd "$REPO" && npm run build >/dev/null) || die "Build failed; the running hub was left alone."
fi

# A service installed before the rename starts `npm start -w @rah/daemon`, a workspace that is
# gone. Point it at the new one; scripts/migrate-name.sh moves the rest when you choose to.
UNIT_FILE="$(systemctl --user show -p FragmentPath --value "$NAME")"
if [ -n "$UNIT_FILE" ] && grep -q '@rah/daemon' "$UNIT_FILE"; then
  say "Pointing $NAME at the renamed workspace @calli/daemon."
  sed -i 's#@rah/daemon#@calli/daemon#' "$UNIT_FILE"
  systemctl --user daemon-reload
fi

if inside_hub; then
  if [ "$FORCE" = 1 ]; then
    say "Restarting now. This turn ends with an error; send a message in the thread to go on."
    systemctl --user restart --no-block "$NAME"
    exit 0
  fi
  mkdir -p "$STATE_DIR"
  setsid nohup "$0" --background --no-build >>"$LOG" 2>&1 </dev/null &
  say "Scheduled: the hub restarts once every thread is idle, including this one when this turn ends."
  say "Log: $LOG"
  exit 0
fi

if [ "$FORCE" = 0 ]; then
  wait_until_idle
fi
say "Restarting $NAME."
systemctl --user restart "$NAME"
check_health
