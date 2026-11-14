#!/usr/bin/env bash
# ==============================================================================
# Colima / Docker PostgreSQL 16 Dual-Actor Coordination Rehearsal Suite
#
# Provides zero-cost local rehearsal for:
# - Schema initialization & least-privilege role assembly
# - Genesis seed ingestion (loopx-quality-review canary)
# - Dual-actor (ruiteng.macbook / lusen.cloud) lease competition & expiration takeover
# - Default-deny boundary rejection
# - Verified task completion & cross-machine snapshot readback
#
# Usage:
#   ./colima-pg16-e2e-rehearsal.sh --simulate                 # Fast in-process simulation (default in CI/tests)
#   ./colima-pg16-e2e-rehearsal.sh --database-url <url>      # Rehearse against existing PostgreSQL instance
#   ./colima-pg16-e2e-rehearsal.sh --rehearsal               # Run against disposable local Docker container (postgres:16-alpine)
#   ./colima-pg16-e2e-rehearsal.sh --start-colima            # Ensure Colima is started, then run containerized rehearsal
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODE="simulate"
DATABASE_URL="${LOOPX_AUTHORITY_DATABASE_URL:-}"
START_COLIMA=false
CONTAINER_NAME="loopx-canary-pg16-rehearsal-$$-$(date +%s)"
PG_PORT=15432
CONTAINER_STARTED=false

cleanup() {
  if [ "${CONTAINER_STARTED}" = true ]; then
    echo "[colima-rehearsal] Cleaning up disposable PostgreSQL container ${CONTAINER_NAME}..." >&2
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --simulate              Run fast, deterministic in-process simulation (default).
  --rehearsal             Spin up disposable postgres:16-alpine container, run tests, and tear down.
  --database-url <url>    Run against an explicit PostgreSQL database URL.
  --start-colima          Start colima VM first if not running, then execute --rehearsal.
  --port <port>           Local host port for disposable container (default: 15432).
  --help                  Show this help message.

Environment Variables:
  LOOPX_AUTHORITY_DATABASE_URL    Default database connection URL if set.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --simulate)
      MODE="simulate"
      shift
      ;;
    --rehearsal)
      MODE="container"
      shift
      ;;
    --start-colima)
      START_COLIMA=true
      MODE="container"
      shift
      ;;
    --database-url)
      DATABASE_URL="$2"
      MODE="real_url"
      shift 2
      ;;
    --port)
      PG_PORT="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help
      exit 1
      ;;
  esac
done

if [ "${START_COLIMA}" = true ]; then
  if ! command -v colima >/dev/null 2>&1; then
    echo "Error: colima CLI not found on PATH." >&2
    exit 1
  fi
  if ! colima status >/dev/null 2>&1; then
    echo "[colima-rehearsal] Starting Colima VM (2 CPU, 2GB memory)..." >&2
    colima start --cpu 2 --memory 2
  else
    echo "[colima-rehearsal] Colima is already running." >&2
  fi
fi

if [ "${MODE}" = "container" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker CLI not found on PATH. Falling back to simulation." >&2
    MODE="simulate"
  elif ! docker info >/dev/null 2>&1; then
    echo "Warning: Docker daemon is unreachable. Falling back to simulation mode." >&2
    MODE="simulate"
  else
    echo "[colima-rehearsal] Starting ephemeral PostgreSQL 16 container: ${CONTAINER_NAME} on port ${PG_PORT}..." >&2
    docker run -d \
      --name "${CONTAINER_NAME}" \
      -e POSTGRES_PASSWORD=loopx_canary_secret \
      -e POSTGRES_DB=loopx_canary \
      -p "${PG_PORT}:5432" \
      postgres:16-alpine >/dev/null
    CONTAINER_STARTED=true

    # Await PostgreSQL readiness
    echo "[colima-rehearsal] Waiting for PostgreSQL readiness..." >&2
    for i in {1..30}; do
      if docker exec "${CONTAINER_NAME}" pg_isready -U postgres >/dev/null 2>&1; then
        echo "[colima-rehearsal] PostgreSQL 16 is ready." >&2
        break
      fi
      sleep 0.5
    done

    DATABASE_URL="postgresql://postgres:loopx_canary_secret@127.0.0.1:${PG_PORT}/loopx_canary"
    MODE="real_url"
  fi
fi

cd "${REPO_ROOT}"

if [ "${MODE}" = "real_url" ]; then
  echo "[colima-rehearsal] Running dual-actor coordination rehearsal against PostgreSQL at ${DATABASE_URL}..." >&2
  node --no-warnings --experimental-strip-types \
    "${SCRIPT_DIR}/colima-pg16-e2e-rehearsal.ts" \
    --database-url "${DATABASE_URL}"
else
  echo "[colima-rehearsal] Running dual-actor coordination rehearsal in high-fidelity simulation mode..." >&2
  node --no-warnings --experimental-strip-types \
    "${SCRIPT_DIR}/colima-pg16-e2e-rehearsal.ts" \
    --simulate
fi
