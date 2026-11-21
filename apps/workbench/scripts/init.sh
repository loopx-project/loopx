#!/usr/bin/env bash
# Gets a checkout ready to work in and proves it runs: installs dependencies if missing, builds,
# then starts a throwaway hub (own port, data dir and fake agents; never the real hub) and runs
# one turn through it. Run this first in a fresh session, before changing anything.
#
#   scripts/init.sh             install if needed, build, start a test hub and keep it running
#   scripts/init.sh --check     same, but stop the hub once the smoke test passes
#   scripts/init.sh --no-build  reuse the existing build
#
# It prints the hub's URL, token and PIDs. Stop it with Ctrl-C or by that PID, never pkill.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
BUILD=1 ARGS=()
for arg in "$@"; do
  case "$arg" in
    --check) ARGS+=(--check) ;;
    --no-build) BUILD=0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { echo "[$(date '+%H:%M:%S')] $*"; }
cd "$REPO"

say "$(git rev-parse --abbrev-ref HEAD) @ $(git log -1 --format='%h %s')"
[ -z "$(git status --porcelain)" ] || say "Uncommitted changes: $(git status --porcelain | wc -l) file(s)."

if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  say "Installing dependencies."
  npm ci
fi

if [ "$BUILD" = 1 ]; then
  say "Building."
  npm run build >/dev/null
fi

say "Starting a test hub."
exec node packages/e2e/dev-instance.mjs ${ARGS[@]+"${ARGS[@]}"}
