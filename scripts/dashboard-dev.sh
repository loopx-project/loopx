#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DASHBOARD_DIR="${REPO_ROOT}/apps/presentation/dashboard"
STATUS_PID=""
CHAT_PID=""
PYTHON_BIN=""
CODEX_BIN="codex"
CLAUDE_BIN="claude"
LARK_CLI_BIN=""
LARK_CLI_ARGS=()
GOAL_SUBAGENT_ARGS=()
if [[ "${LOOPX_ENABLE_GOAL_SUBAGENT_CONFIGURATION:-0}" = "1" ]]; then
  GOAL_SUBAGENT_ARGS=(--enable-goal-subagent-configuration)
fi

node_is_supported() {
  "$1" --no-warnings --experimental-strip-types --input-type=module -e '
import { pathToFileURL } from "node:url";
const { isSupportedNodeVersion } = await import(pathToFileURL(process.argv[1]).href);
process.exit(isSupportedNodeVersion(process.versions.node) ? 0 : 1);
' "${REPO_ROOT}/loopx/control_plane/runtime/node_version.ts" 2>/dev/null
}

select_node_runtime() {
  if command -v node >/dev/null 2>&1 \
    && command -v npm >/dev/null 2>&1 \
    && node_is_supported "$(command -v node)"; then
    return 0
  fi

  local nvm_root="${NVM_DIR:-${HOME}/.nvm}"
  local node_candidate
  local npm_candidate
  for node_candidate in "${nvm_root}"/versions/node/*/bin/node; do
    [ -x "${node_candidate}" ] || continue
    npm_candidate="$(dirname "${node_candidate}")/npm"
    if [ -x "${npm_candidate}" ] && node_is_supported "${node_candidate}"; then
      export PATH="$(dirname "${node_candidate}"):${PATH}"
      echo "Using compatible Node.js from $(dirname "${node_candidate}")"
      return 0
    fi
  done

  echo "LoopX dashboard requires Node.js 22.22.3 or newer for its control plane." >&2
  echo "Activate a compatible Node.js runtime, then retry: loopx dashboard" >&2
  return 1
}

if ! select_node_runtime; then
  exit 1
fi

if [ ! -x "${DASHBOARD_DIR}/node_modules/.bin/vite" ]; then
  echo "Installing LoopX dashboard dependencies (first run only)..."
  cd "${DASHBOARD_DIR}"
  if ! npm ci; then
    echo "LoopX dashboard dependency installation failed." >&2
    exit 1
  fi
fi

wait_for_service() {
  local service_name="$1"
  local service_url="$2"
  local service_pid="$3"
  local probe_status=0
  local service_status=0
  "${PYTHON_BIN}" - "${service_name}" "${service_url}" "${service_pid}" <<'PY'
import os
import sys
import time
import urllib.error
import urllib.request

name, url, pid_text = sys.argv[1:]
pid = int(pid_text)
deadline = time.monotonic() + 15
last_error = "service did not respond"
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise SystemExit(2)
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
            last_error = f"HTTP {response.status}"
    except (OSError, urllib.error.URLError) as exc:
        last_error = str(exc)
    time.sleep(0.1)
print(f"LoopX {name} service failed to start: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
  probe_status=$?
  if [ "${probe_status}" -eq 2 ]; then
    wait "${service_pid}"
    service_status=$?
    echo "LoopX ${service_name} service exited before it became ready (exit status ${service_status})." >&2
    echo "The original service error output is shown above." >&2
    return 1
  fi
  return "${probe_status}"
}

cleanup() {
  if [ -n "${STATUS_PID}" ]; then
    kill "${STATUS_PID}" 2>/dev/null || true
  fi
  if [ -n "${CHAT_PID}" ]; then
    kill "${CHAT_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if ! PYTHON_BIN="$(bash "${SCRIPT_DIR}/loopx-python.sh")"; then
  echo "LoopX requires Python 3.11 or newer to start status and Chat services." >&2
  echo "From the repository root, prepare a project environment and retry:" >&2
  echo "  uv sync --extra test" >&2
  echo "  uv run --extra test bash scripts/dashboard-dev.sh" >&2
  echo "Or set LOOPX_PYTHON to an existing Python 3.11+ executable." >&2
  echo "Starting the Vite UI only; use 'npm run dev:web' for the same UI-only preview." >&2
  cd "${DASHBOARD_DIR}"
  exec npm run dev:web
fi
echo "Using LoopX Python: ${PYTHON_BIN}"

resolve_agent_binary() {
  local binary_name="$1"
  local discovered=""
  if command -v "${binary_name}" >/dev/null 2>&1; then
    command -v "${binary_name}"
    return 0
  fi
  for discovered in \
    "${HOME}/.npm-global/bin/${binary_name}" \
    "${HOME}/.local/bin/${binary_name}" \
    "${HOME}"/.nvm/versions/node/*/bin/"${binary_name}"; do
    if [ -x "${discovered}" ]; then
      printf '%s\n' "${discovered}"
      return 0
    fi
  done
  printf '%s\n' "${binary_name}"
}

resolve_lark_cli_binary() {
  local discovered=""
  if command -v lark-cli >/dev/null 2>&1; then
    command -v lark-cli
    return 0
  fi
  discovered="${NVM_BIN:-}/lark-cli"
  if [ -n "${NVM_BIN:-}" ] && [ -x "${discovered}" ]; then
    printf '%s\n' "${discovered}"
    return 0
  fi

  local candidate=""
  local version_name=""
  local major=0
  local minor=0
  local patch=0
  local best_candidate=""
  local best_major=-1
  local best_minor=-1
  local best_patch=-1
  for candidate in "${HOME}"/.nvm/versions/node/*/bin/lark-cli; do
    [ -x "${candidate}" ] || continue
    version_name="$(basename "$(dirname "$(dirname "${candidate}")")")"
    if [[ ! "${version_name}" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
      continue
    fi
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    patch="${BASH_REMATCH[3]}"
    if (( major > best_major \
      || (major == best_major && minor > best_minor) \
      || (major == best_major && minor == best_minor && patch > best_patch) )); then
      best_candidate="${candidate}"
      best_major="${major}"
      best_minor="${minor}"
      best_patch="${patch}"
    fi
  done
  if [ -n "${best_candidate}" ]; then
    printf '%s\n' "${best_candidate}"
    return 0
  fi

  for discovered in \
    "${HOME}/.local/bin/lark-cli" \
    "${HOME}/.npm-global/bin/lark-cli" \
    /opt/homebrew/bin/lark-cli \
    /usr/local/bin/lark-cli \
    /usr/bin/lark-cli \
    /bin/lark-cli; do
    [ -n "${discovered}" ] || continue
    if [ -x "${discovered}" ]; then
      printf '%s\n' "${discovered}"
      return 0
    fi
  done
  return 1
}

CODEX_BIN="$(resolve_agent_binary codex)"
CLAUDE_BIN="$(resolve_agent_binary claude)"
if LARK_CLI_BIN="$(resolve_lark_cli_binary)"; then
  LARK_CLI_ARGS=(--lark-cli-bin "${LARK_CLI_BIN}")
fi
echo "LoopX Agent executables: Codex=${CODEX_BIN}; Claude Code=${CLAUDE_BIN}"
if [ -n "${LARK_CLI_BIN}" ]; then
  echo "LoopX Lark CLI: discovered"
else
  echo "LoopX Lark CLI: runtime discovery pending"
fi

cd "${REPO_ROOT}"
"${PYTHON_BIN}" "${REPO_ROOT}/scripts/chat_bundle.py" ensure || exit 1

"${PYTHON_BIN}" -m loopx.cli serve-status \
  --global-registry \
  --host 127.0.0.1 \
  --port 8766 \
  ${GOAL_SUBAGENT_ARGS[@]+"${GOAL_SUBAGENT_ARGS[@]}"} \
  --limit 80 &
STATUS_PID=$!

"${PYTHON_BIN}" -m loopx.cli chat \
  --global-registry \
  --host 127.0.0.1 \
  --port 8767 \
  --codex-bin "${CODEX_BIN}" \
  --claude-bin "${CLAUDE_BIN}" \
  ${GOAL_SUBAGENT_ARGS[@]+"${GOAL_SUBAGENT_ARGS[@]}"} \
  ${LARK_CLI_ARGS[@]+"${LARK_CLI_ARGS[@]}"} \
  --no-open &
CHAT_PID=$!

if ! wait_for_service "status" "http://127.0.0.1:8766/healthz" "${STATUS_PID}"; then
  exit 1
fi
if ! wait_for_service "Chat" "http://127.0.0.1:8767/api/chat/capabilities" "${CHAT_PID}"; then
  exit 1
fi

echo "LoopX dashboard services:"
echo "  UI:     http://127.0.0.1:5173/"
echo "  Status: http://127.0.0.1:8766/status.json"
echo "  Chat:   http://127.0.0.1:8767/api/chat/capabilities"

cd "${DASHBOARD_DIR}"
npm run dev:web
