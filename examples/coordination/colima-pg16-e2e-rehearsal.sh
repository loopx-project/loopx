#!/usr/bin/env bash
# ==============================================================================
# Colima / Docker PostgreSQL 16 Dual-Actor Coordination Rehearsal Suite
#
# Provides zero-cost local rehearsal for:
# - Schema initialization & least-privilege role assembly
# - Genesis seed ingestion (loopx-quality-review canary)
# - Dual-actor (operator.local / worker.cloud) lease competition & expiration takeover over HTTPS/TLS
# - Default-deny boundary rejection
# - Verified task completion & cross-machine snapshot readback
#
# Usage:
#   ./colima-pg16-e2e-rehearsal.sh --simulate                 # Fast in-memory PG + real TLS simulation (preflight)
#   ./colima-pg16-e2e-rehearsal.sh --database-url <url>      # Rehearse against existing PostgreSQL instance
#   ./colima-pg16-e2e-rehearsal.sh --rehearsal               # Fail-closed rehearsal against disposable postgres:16-alpine
#   ./colima-pg16-e2e-rehearsal.sh --start-colima            # Ensure Colima is started, then run containerized rehearsal
# ==============================================================================

set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "${SCRIPT_SOURCE}" ]; do
  LINK_DIR="$(cd -P "$(dirname "${SCRIPT_SOURCE}")" && pwd)"
  LINK_TARGET="$(readlink "${SCRIPT_SOURCE}")"
  case "${LINK_TARGET}" in
    /*) SCRIPT_SOURCE="${LINK_TARGET}" ;;
    *) SCRIPT_SOURCE="${LINK_DIR}/${LINK_TARGET}" ;;
  esac
done
SCRIPT_DIR="$(cd -P "$(dirname "${SCRIPT_SOURCE}")" && pwd)"
REPO_ROOT="$(cd -P "${SCRIPT_DIR}/../.." && pwd)"

MODE="simulate"
EXPLICIT_MODE=""
EXPLICIT_FLAG=""
DATABASE_URL="${LOOPX_AUTHORITY_DATABASE_URL:-}"
START_COLIMA=false
CONTAINER_NAME="loopx-canary-pg16-rehearsal-$$-$(date +%s)"
PG_PORT=""
PORT_EXPLICIT=false
CONTAINER_STARTED=false

set_explicit_mode() {
  local target_mode="$1"
  local flag="$2"
  if [ -n "${EXPLICIT_MODE}" ] && { [ "${EXPLICIT_MODE}" != "${target_mode}" ] || [ "${flag}" = "--database-url" ]; }; then
    echo "Error: conflicting mode flags (${EXPLICIT_FLAG} and ${flag}) are mutually exclusive." >&2
    exit 1
  fi
  EXPLICIT_MODE="${target_mode}"
  EXPLICIT_FLAG="${flag}"
  MODE="${target_mode}"
}

cleanup() {
  if [ "${CONTAINER_STARTED}" = true ]; then
    echo "[colima-rehearsal] Cleaning up disposable PostgreSQL container ${CONTAINER_NAME}..." >&2
    docker rm -f -v "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --simulate              Run fast, deterministic in-memory DB + TLS simulation preflight (default).
  --rehearsal             Spin up disposable postgres:16-alpine container, run real PG + TLS rehearsal (fails closed if Docker is unavailable).
  --database-url <url>    Run against an explicit PostgreSQL database URL.
  --start-colima          Start colima VM first if not running, then execute --rehearsal.
  --port <port>           Local host port for disposable container (default: random ephemeral 127.0.0.1 port).
  --help                  Show this help message.

Environment Variables:
  LOOPX_AUTHORITY_DATABASE_URL    Default database connection URL if set.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --simulate)
      set_explicit_mode "simulate" "--simulate"
      shift
      ;;
    --rehearsal)
      set_explicit_mode "container" "--rehearsal"
      shift
      ;;
    --start-colima)
      START_COLIMA=true
      set_explicit_mode "container" "--start-colima"
      shift
      ;;
    --database-url)
      if [ $# -lt 2 ] || [ -z "${2// }" ]; then
        echo "Error: --database-url requires a non-empty PostgreSQL URL." >&2
        exit 1
      fi
      set_explicit_mode "real_url" "--database-url"
      DATABASE_URL="$2"
      shift 2
      ;;
    --port)
      if [ $# -lt 2 ] || [ -z "${2// }" ]; then
        echo "Error: --port requires a non-empty port number." >&2
        exit 1
      fi
      PG_PORT="$2"
      PORT_EXPLICIT=true
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
    echo "Error: docker CLI not found on PATH; --rehearsal requires Docker/Colima (use --simulate for preflight)." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker daemon is unreachable; --rehearsal cannot fall back to simulation (use --simulate for preflight)." >&2
    exit 1
  fi

  PUBLISH_SPEC="127.0.0.1::5432"
  if [ "${PORT_EXPLICIT}" = true ]; then
    PUBLISH_SPEC="127.0.0.1:${PG_PORT}:5432"
  fi

  echo "[colima-rehearsal] Starting ephemeral PostgreSQL 16 container: ${CONTAINER_NAME} (${PUBLISH_SPEC})..." >&2
  docker run -d \
    --name "${CONTAINER_NAME}" \
    -e POSTGRES_PASSWORD=loopx_canary_secret \
    -e POSTGRES_DB=loopx_canary \
    -p "${PUBLISH_SPEC}" \
    postgres:16-alpine >/dev/null
  CONTAINER_STARTED=true

  PG_PORT="$(docker port "${CONTAINER_NAME}" 5432/tcp | head -n 1 | awk -F: '{print $NF}')"
  if [ -z "${PG_PORT}" ]; then
    echo "Error: Failed to resolve published host port for ${CONTAINER_NAME}." >&2
    exit 1
  fi

  # Await TCP PostgreSQL readiness (not just Unix-socket initdb startup)
  echo "[colima-rehearsal] Waiting for PostgreSQL readiness on 127.0.0.1:${PG_PORT}..." >&2
  PG_READY=false
  for _ in {1..40}; do
    if docker exec -e PGPASSWORD=loopx_canary_secret "${CONTAINER_NAME}" \
      psql -h 127.0.0.1 -p 5432 -U postgres -d loopx_canary -c "SELECT 1" >/dev/null 2>&1; then
      echo "[colima-rehearsal] PostgreSQL 16 is ready." >&2
      PG_READY=true
      break
    fi
    sleep 0.5
  done
  if [ "${PG_READY}" != true ]; then
    echo "Error: PostgreSQL 16 container ${CONTAINER_NAME} failed to become ready within timeout." >&2
    exit 1
  fi

  DATABASE_URL="postgresql://postgres:loopx_canary_secret@127.0.0.1:${PG_PORT}/loopx_canary"
  MODE="real_url"
fi

cd "${REPO_ROOT}"

if [ "${MODE}" = "real_url" ]; then
  echo "[colima-rehearsal] Running dual-actor coordination rehearsal against isolated real PostgreSQL + TLS..." >&2
  node --no-warnings --experimental-sqlite --experimental-strip-types \
    "${SCRIPT_DIR}/colima-pg16-e2e-rehearsal.ts" \
    --database-url "${DATABASE_URL}"
else
  echo "[colima-rehearsal] Running dual-actor coordination rehearsal in in-memory simulation + TLS mode..." >&2
  node --no-warnings --experimental-sqlite --experimental-strip-types \
    "${SCRIPT_DIR}/colima-pg16-e2e-rehearsal.ts" \
    --simulate
fi
