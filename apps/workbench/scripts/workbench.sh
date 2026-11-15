#!/bin/bash
# Starts the Workbench daemon (which also serves the built web UI) in isolation from any other LooChat.
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
  printf 'LOOCHAT_TOKEN=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)" > "$ENVF"
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
    # Do not inherit an enclosing LooChat/Calli session's credentials, web build or hub wiring:
    # the UI served must be this checkout's own packages/web/dist, and no socket may point at
    # another daemon. Both name generations are unset: the daemon's legacy adoption would
    # otherwise turn a stray CALLI_* back into LOOCHAT_*.
    unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_EXECPATH CLAUDECODE \
      LOOCHAT_WEB_DIR LOOCHAT_TOKEN LOOCHAT_AGENTS_JSON LOOCHAT_HUB_URL LOOCHAT_HUB_TOKEN \
      LOOCHAT_BIN_DIR LOOCHAT_TOOLS_SOCKET LOOCHAT_TOOLS_KEY LOOCHAT_TEAM_SOCKET LOOCHAT_TEAM_KEY \
      LOOCHAT_HANDOVER_SOCKET LOOCHAT_HANDOVER_KEY LOOCHAT_TODO_SOCKET LOOCHAT_TODO_KEY \
      CALLI_WEB_DIR CALLI_TOKEN CALLI_AGENTS_JSON CALLI_HUB_URL CALLI_HUB_TOKEN \
      CALLI_BIN_DIR CALLI_TOOLS_SOCKET CALLI_TOOLS_KEY CALLI_TEAM_SOCKET CALLI_TEAM_KEY \
      CALLI_HANDOVER_SOCKET CALLI_HANDOVER_KEY CALLI_TODO_SOCKET CALLI_TODO_KEY \
      RAH_WEB_DIR RAH_TOKEN RAH_AGENTS_JSON RAH_HUB_URL RAH_HUB_TOKEN RAH_BIN_DIR \
      LIMI_SOCKET LIMI_KEY LIMI_TOOLS_KEY
    LOOCHAT_ENV_FILE="$ENVF" LOOCHAT_HOST=0.0.0.0 LOOCHAT_PORT="$PORT" LOOCHAT_PREVIEW_PORT=$((PORT+1)) \
    LOOCHAT_DATA_DIR="$STATE/data" LOOCHAT_SOCKET="$STATE/daemon.sock" \
    LOOCHAT_WORKDIRS="${WORKBENCH_WORKDIRS:-/home/liuhuadong.hans/code,/tmp}" \
      setsid nohup node dist/index.js > "$STATE/daemon.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3; alive && echo "started pid $(cat "$PIDF"), log $STATE/daemon.log" || { tail -20 "$STATE/daemon.log"; exit 1; };;
esac
