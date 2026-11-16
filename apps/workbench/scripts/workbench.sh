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
# The LoopX core (Python chat server) the daemon proxies to. A Goal can only be created in a directory the
# core has been given, one --scan-path each, the way the dashboard is started. Add projects with
# LOOPX_GOAL_WORKSPACES=/path/a,/path/b and restart the core: scripts/workbench.sh core restart
CORE_PORT="${LOOPX_CORE_PORT:-8792}"
CORE_PIDF="$STATE/core.pid"
core_alive(){ [ -f "$CORE_PIDF" ] && kill -0 "$(cat "$CORE_PIDF")" 2>/dev/null; }
core_start(){
  local REPO_ROOT; REPO_ROOT="$(cd "$HERE/../.." && pwd)"
  local WS="${LOOPX_GOAL_WORKSPACES:-$REPO_ROOT/loopx}" scan=() item
  IFS=',' read -ra items <<< "$WS"
  for item in "${items[@]}"; do [ -n "$item" ] && scan+=(--scan-path "$item"); done
  ( cd "$REPO_ROOT" && LOOPX_USAGE_PING=0 setsid nohup python3 -m loopx.cli chat --global-registry --host 127.0.0.1 --port "$CORE_PORT" --no-open \
      --assets-dir "${LOOPX_CHAT_ASSETS:-/data00/home/liuhuadong.hans/code/loopx/loopx/web/chat}" "${scan[@]}" --project-workspace-grant workspace_read \
      > "$STATE/core.log" 2>&1 < /dev/null & echo $! > "$CORE_PIDF" )
}
case "${1:-start}" in
  stop) alive && kill "$(cat "$PIDF")" && echo stopped || echo "not running";;
  status) alive && echo "running pid $(cat "$PIDF") on :$PORT" || echo "not running";;
  core)
    case "${2:-start}" in
      stop) core_alive && kill "$(cat "$CORE_PIDF")" && echo "core stopped" || echo "core not running";;
      status) core_alive && echo "core running pid $(cat "$CORE_PIDF") on :$CORE_PORT" || echo "core not running";;
      restart) core_alive && kill "$(cat "$CORE_PIDF")"; sleep 2; core_start; sleep 6; echo "core restarted, log $STATE/core.log";;
      start) if core_alive; then echo "core already running"; else core_start; sleep 6; echo "core started, log $STATE/core.log"; fi;;
    esac;;
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
    # LoopX Goal conversations: the core (Python chat server) is reached over HTTP, status is read through the CLI.
    REPO_ROOT="$(cd "$HERE/../.." && pwd)"
    LOOCHAT_LOOPX_CORE_URL="${LOOCHAT_LOOPX_CORE_URL:-http://127.0.0.1:8792}" \
    LOOCHAT_LOOPX_CMD="${LOOCHAT_LOOPX_CMD:-python3 -m loopx.cli}" LOOCHAT_LOOPX_CWD="${LOOCHAT_LOOPX_CWD:-$REPO_ROOT}" \
    LOOCHAT_LOOPX_REGISTRY="${LOOCHAT_LOOPX_REGISTRY:-$HOME/.loopx/registry.global.json}" \
    LOOCHAT_ENV_FILE="$ENVF" LOOCHAT_HOST=0.0.0.0 LOOCHAT_PORT="$PORT" LOOCHAT_PREVIEW_PORT=$((PORT+1)) \
    LOOCHAT_DATA_DIR="$STATE/data" LOOCHAT_SOCKET="$STATE/daemon.sock" \
    LOOCHAT_WORKDIRS="${WORKBENCH_WORKDIRS:-/home/liuhuadong.hans/code,/tmp}" \
      setsid nohup node dist/index.js > "$STATE/daemon.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3; alive && echo "started pid $(cat "$PIDF"), log $STATE/daemon.log" || { tail -20 "$STATE/daemon.log"; exit 1; };;
esac
