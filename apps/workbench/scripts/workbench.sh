#!/bin/bash
# Starts the Workbench daemon (which also serves the built web UI) in isolation from any other LooChat.
# Everything it writes lives under WORKBENCH_STATE_DIR; its port, token and unix socket are its own.
# Usage: scripts/workbench.sh [start|stop|status]
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
STATE="${WORKBENCH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/loochat-workbench}"
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
  local WS="${LOOPX_GOAL_WORKSPACES:-$REPO_ROOT/loopx}" scan=() assets=() item
  [ -n "${LOOPX_CHAT_ASSETS:-}" ] && assets=(--assets-dir "$LOOPX_CHAT_ASSETS")
  IFS=',' read -ra items <<< "$WS"
  for item in "${items[@]}"; do [ -n "$item" ] && scan+=(--scan-path "$item"); done
  ( cd "$REPO_ROOT" && LOOPX_USAGE_PING=0 setsid nohup python3 -m loopx.cli chat --global-registry --host 127.0.0.1 --port "$CORE_PORT" --no-open \
      "${assets[@]}" "${scan[@]}" --project-workspace-grant workspace_read \
      > "$STATE/core.log" 2>&1 < /dev/null & echo $! > "$CORE_PIDF" )
}
case "${1:-start}" in
  stop) alive && kill "$(cat "$PIDF")" && echo stopped || echo "not running";;
  status) alive && echo "running pid $(cat "$PIDF") on :$PORT" || echo "not running";;
  # The permanent token, for filling into 设置 › 访问 after arriving through a login link.
  token) sed -n 's/^LOOCHAT_TOKEN=//p' "$ENVF";;
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
    LOOCHAT_LOOPX_CORE_URL="${LOOCHAT_LOOPX_CORE_URL:-http://127.0.0.1:8792}" \
    LOOCHAT_LOOPX_CMD="${LOOCHAT_LOOPX_CMD:-python3 -m loopx.cli}" LOOCHAT_LOOPX_CWD="${LOOCHAT_LOOPX_CWD:-$REPO_ROOT}" \
    LOOCHAT_LOOPX_REGISTRY="${LOOCHAT_LOOPX_REGISTRY:-$HOME/.loopx/registry.global.json}" \
    LOOCHAT_ENV_FILE="$ENVF" LOOCHAT_HOST="${WORKBENCH_HOST:-127.0.0.1}" LOOCHAT_PORT="$PORT" LOOCHAT_PREVIEW_PORT=$((PORT+1)) \
    LOOCHAT_DATA_DIR="$STATE/data" LOOCHAT_SOCKET="$STATE/daemon.sock" \
    LOOCHAT_WORKDIRS="${WORKBENCH_WORKDIRS:-$REPO_ROOT,/tmp}" \
      setsid nohup node dist/index.js > "$STATE/daemon.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3; alive || { tail -20 "$STATE/daemon.log"; exit 1; }
    echo "started pid $(cat "$PIDF"), log $STATE/daemon.log"
    # A login link to hand to a phone: a fresh 7-day link token, printed once as a full address.
    # WORKBENCH_PRINT_LINK=0 skips it (links can also be made and revoked in 设置 › 访问).
    if [ "${WORKBENCH_PRINT_LINK:-1}" != 0 ] && command -v python3 >/dev/null; then
      TOKEN="$(sed -n 's/^LOOCHAT_TOKEN=//p' "$ENVF")" PORT="$PORT" python3 - <<'PY' || echo "login link not issued; make one in Settings → Access"
import json, os, urllib.request
req = urllib.request.Request(f"http://127.0.0.1:{os.environ['PORT']}/api/auth/link-tokens", method="POST",
    headers={"Authorization": "Bearer " + os.environ["TOKEN"]}, data=b"{}")
link = json.load(urllib.request.urlopen(req, timeout=10))
print(f"login link (valid until {link['expiresAt']}): http://127.0.0.1:{os.environ['PORT']}/#token={link['token']}")
PY
    fi
    # First start of a state dir only: switch on the built-in extensions the Workbench wants (the session board),
    # as the Extensions page would. Done once, so uninstalling one later sticks. WORKBENCH_EXTENSIONS="" turns it off.
    if [ ! -f "$STATE/.extensions-seeded" ]; then
      TOKEN="$(sed -n 's/^LOOCHAT_TOKEN=//p' "$ENVF")" PORT="$PORT" EXTS="${WORKBENCH_EXTENSIONS-board}" python3 - <<'PY' && : > "$STATE/.extensions-seeded" || echo "extensions not seeded; enable them in Settings → Extensions"
import json, os, urllib.request
base, head = f"http://127.0.0.1:{os.environ['PORT']}/api/extensions", {"Authorization": "Bearer " + os.environ["TOKEN"], "Content-Type": "application/json"}
def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, headers=head, data=None if body is None else json.dumps(body).encode())
    return json.load(urllib.request.urlopen(req, timeout=10))
catalog = {e["manifest"]["id"]: e for e in call("GET", base)}
for ext in filter(None, os.environ["EXTS"].split(",")):
    state = catalog.get(ext)
    if state is None or state["installed"]: continue
    call("POST", f"{base}/{ext}/install", {"granted": state["manifest"]["permissions"]})
    call("PATCH", f"{base}/{ext}", {"enabled": True})
PY
    fi;;
esac
