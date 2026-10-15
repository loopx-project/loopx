#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DASHBOARD_DIR="$(cd "${DESKTOP_DIR}/../../presentation/dashboard" && pwd)"

node_is_supported() {
  "$1" -e '
const [major, minor] = process.versions.node.split(".").map(Number);
const supported = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22;
process.exit(supported ? 0 : 1);
' 2>/dev/null
}

select_node_runtime() {
  if command -v node >/dev/null 2>&1 \
    && command -v npm >/dev/null 2>&1 \
    && node_is_supported "$(command -v node)"; then
    return 0
  fi

  local nvm_root="${NVM_DIR:-${HOME}/.nvm}"
  local node_candidate
  for node_candidate in \
    "${nvm_root}"/versions/node/*/bin/node \
    "${HOME}"/.npm/_npx/*/node_modules/node/bin/node; do
    [ -x "${node_candidate}" ] || continue
    if node_is_supported "${node_candidate}"; then
      export PATH="$(dirname "${node_candidate}"):${PATH}"
      if command -v npm >/dev/null 2>&1; then
        return 0
      fi
    fi
  done

  echo "LoopX desktop build requires Node.js 20.19+ or 22.12+." >&2
  return 1
}

select_node_runtime
cd "${DASHBOARD_DIR}"

case "${1:-}" in
  dev)
    export VITE_LOOPX_CHAT_ORIGIN=""
    exec npm run dev:web
    ;;
  build)
    npm ci
    export VITE_LOOPX_CHAT_ORIGIN="http://127.0.0.1:8767"
    exec npm run build:desktop
    ;;
  *)
    echo "usage: $0 dev|build" >&2
    exit 2
    ;;
esac
