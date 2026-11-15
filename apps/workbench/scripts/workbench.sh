#!/bin/bash
# Starts the Workbench daemon (which also serves the built web UI) in isolation from any other Calli.
# Everything it writes lives under WORKBENCH_STATE_DIR; its port, token and unix socket are its own.
# Usage: scripts/workbench.sh [start|stop|status]
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${WORKBENCH_STATE_DIR:-/data00/home/liuhuadong.hans/code/.workbench-state}"
PORT="${WORKBENCH_PORT:-8796}"
mkdir -p "$STATE"
ENVF="$STATE/.env"
if [ ! -f "$ENVF" ]; then
  umask 077
  printf 'CALLI_TOKEN=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)" > "$ENVF"
fi
PIDF="$STATE/daemon.pid"
alive(){ [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-start}" in
  stop) alive && kill "$(cat "$PIDF")" && echo stopped || echo "not running";;
  status) alive && echo "running pid $(cat "$PIDF") on :$PORT" || echo "not running";;
  start)
    if alive; then echo "already running pid $(cat "$PIDF")"; exit 0; fi
    [ -d "$HERE/packages/daemon/dist" ] && [ -d "$HERE/packages/web/dist" ] || (cd "$HERE" && npm run build)
    cd "$HERE/packages/daemon"
    # Do not inherit another agent session's model credentials.
    unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_EXECPATH CLAUDECODE
    CALLI_ENV_FILE="$ENVF" CALLI_HOST=0.0.0.0 CALLI_PORT="$PORT" CALLI_PREVIEW_PORT=$((PORT+1)) \
    CALLI_DATA_DIR="$STATE/data" CALLI_SOCKET="$STATE/daemon.sock" \
    CALLI_WORKDIRS="${WORKBENCH_WORKDIRS:-/data00/home/liuhuadong.hans/code,/home/liuhuadong.hans/code,/tmp}" \
      setsid nohup node dist/index.js > "$STATE/daemon.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3; alive && echo "started pid $(cat "$PIDF"), log $STATE/daemon.log" || { tail -20 "$STATE/daemon.log"; exit 1; };;
esac
