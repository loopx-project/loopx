#!/usr/bin/env bash
set -Eeuo pipefail

CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOPX_ROOT="$(cd "$CODE_DIR/../.." && pwd)"

if [[ -f "$CODE_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$CODE_DIR/.env"
fi

die() { echo "FATAL: $*" >&2; exit 1; }

[[ -n "${LHTB_ROOT:-}" ]] || die "Set LHTB_ROOT to an LHTB checkout"
[[ -d "$LHTB_ROOT/upstream/tasks" ]] || die "LHTB tasks not found under: $LHTB_ROOT"
LHTB_ROOT="$(cd "$LHTB_ROOT" && pwd)"

MODE="${1:-preflight}"
SMOKE_TASK="${2:-tabular-data-feature-covshift}"
case "$MODE" in
  prepare|preflight|smoke|full) ;;
  *)
    echo "Usage: $0 {prepare|preflight|smoke [task-name]|full}" >&2
    exit 2
    ;;
esac

VENV="${LHTB_VENV:-$LHTB_ROOT/.venv}"
[[ -x "$VENV/bin/harbor" ]] || die "Harbor not found: $VENV/bin/harbor"
[[ -x "$VENV/bin/python" ]] || die "Python not found: $VENV/bin/python"

OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
[[ -n "$OPENAI_BASE_URL" ]] || die "Set OPENAI_BASE_URL to the model gateway"
[[ -n "$OPENAI_API_KEY" ]] || die "Set OPENAI_API_KEY (a placeholder is sufficient for an injecting gateway)"
MODEL_NAME="${MODEL_NAME:-openai/gpt-5.6-sol}"
REASONING_EFFORT="${REASONING_EFFORT:-max}"
CONCURRENCY="${CONCURRENCY:-4}"
AGENT_TIMEOUT_SEC="${AGENT_TIMEOUT_SEC:-5400}"
LOOPX_SCHEDULER_TIMEOUT_SEC="${LOOPX_SCHEDULER_TIMEOUT_SEC:-5080}"
LOOPX_CODEX_TURN_TIMEOUT_SEC="${LOOPX_CODEX_TURN_TIMEOUT_SEC:-}"
LHTB_MAX_RETRIES="${LHTB_MAX_RETRIES:-2}"
RUNNER_RESTARTS="${RUNNER_RESTARTS:-2}"
LHTB_MODELONLY_NETWORK="${LHTB_MODELONLY_NETWORK:-lhtb-modelonly}"
LHTB_MODELONLY_SUBNET="${LHTB_MODELONLY_SUBNET:-192.0.2.0/24}"
LHTB_MODELONLY_GATEWAY="${LHTB_MODELONLY_GATEWAY:-192.0.2.1}"
LOOPX_SRC_DIR="${LOOPX_SRC_DIR:-$LOOPX_ROOT}"
export PYTHONPATH="$LOOPX_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"
LOOPX_EXECUTION_MODE="${LOOPX_EXECUTION_MODE:-heartbeat}"
LOOPX_TASK_ENTRY="${LOOPX_TASK_ENTRY:-}"
LOOPX_PLANNING_TIMEOUT_SEC="${LOOPX_PLANNING_TIMEOUT_SEC:-300}"
LOOPX_ITERATION_CONTEXT="${LOOPX_ITERATION_CONTEXT:-fresh}"
LOOPX_VALIDATION_COMMAND_JSON="${LOOPX_VALIDATION_COMMAND_JSON:-[]}"
SHARED_CODEX_AGENT_DIR="$LOOPX_SRC_DIR/benchmark/runtime"
[[ -f "$SHARED_CODEX_AGENT_DIR/codex_offline.py" ]] || \
  die "Shared offline Codex adapter not found: $SHARED_CODEX_AGENT_DIR/codex_offline.py"
LOOPX_EXPECTED_COMMIT="${LOOPX_EXPECTED_COMMIT:-$(git -C "$LOOPX_SRC_DIR" rev-parse HEAD)}"

if [[ -z "${CODEX_BIN:-}" ]]; then
  npm_root="$(npm root -g 2>/dev/null || true)"
  candidate="$npm_root/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
  [[ -x "$candidate" ]] && CODEX_BIN="$candidate"
fi
[[ -n "${CODEX_BIN:-}" && -x "$CODEX_BIN" ]] || die "Set CODEX_BIN to the native Codex binary"
CODEX_OFFLINE_DIR="${CODEX_OFFLINE_DIR:-$(dirname "$CODEX_BIN")}"

LOOPX_PORTABLE_PYTHON="${LOOPX_PORTABLE_PYTHON:-}"
[[ -n "$LOOPX_PORTABLE_PYTHON" ]] || die "Set LOOPX_PORTABLE_PYTHON to a portable Python >=3.11 root"
[[ -x "$LOOPX_PORTABLE_PYTHON/bin/python3" ]] || die "Set LOOPX_PORTABLE_PYTHON to a portable Python >=3.11 root"

if [[ -z "${LOOPX_NODE_DIR:-}" ]]; then
  node_command="$(command -v node 2>/dev/null || true)"
  node_binary="$(readlink -f "$node_command" 2>/dev/null || true)"
  [[ -n "$node_binary" ]] && LOOPX_NODE_DIR="$(cd "$(dirname "$node_binary")/.." && pwd)"
fi
[[ -n "${LOOPX_NODE_DIR:-}" && -x "$LOOPX_NODE_DIR/bin/node" ]] || die "Set LOOPX_NODE_DIR to a supported Node root"

for value in "$CONCURRENCY" "$AGENT_TIMEOUT_SEC" "$LOOPX_SCHEDULER_TIMEOUT_SEC" \
  "$LOOPX_CODEX_TURN_TIMEOUT_SEC" "$LHTB_MAX_RETRIES" "$RUNNER_RESTARTS"; do
  [[ "$value" =~ ^[0-9]+$ ]] || die "numeric configuration expected, got: $value"
done
(( LOOPX_CODEX_TURN_TIMEOUT_SEC + 150 < LOOPX_SCHEDULER_TIMEOUT_SEC )) || die "scheduler timeout must exceed Codex timeout plus 150s cleanup allowance"
(( LOOPX_SCHEDULER_TIMEOUT_SEC < AGENT_TIMEOUT_SEC )) || die "scheduler timeout must be below Harbor agent timeout"

gateway_host="$($VENV/bin/python -c 'from urllib.parse import urlsplit; import sys; print(urlsplit(sys.argv[1]).hostname or "")' "$OPENAI_BASE_URL")"
[[ -n "$gateway_host" ]] || die "Invalid OPENAI_BASE_URL: $OPENAI_BASE_URL"

echo "=== prepare Harbor model-only networking ==="
HARBOR_DOCKER_DIR="$LHTB_ROOT/upstream/harbor/src/harbor/environments/docker"
if [[ "$MODE" != preflight ]] && ! grep -q 'LHTB_MODELONLY_NET' "$HARBOR_DOCKER_DIR/docker.py" 2>/dev/null; then
  LHTB_HARBOR_SRC="$LHTB_ROOT/upstream/harbor/src/harbor" \
    "$VENV/bin/python" "$CODE_DIR/harbor_patch/prepare_harbor_modelonly.py"
fi
if [[ "$MODE" != preflight ]] && ! docker network inspect "$LHTB_MODELONLY_NETWORK" >/dev/null 2>&1; then
  docker network create --driver bridge --internal \
    --subnet "$LHTB_MODELONLY_SUBNET" \
    --gateway "$LHTB_MODELONLY_GATEWAY" \
    "$LHTB_MODELONLY_NETWORK" >/dev/null
fi
network_internal="$(docker network inspect "$LHTB_MODELONLY_NETWORK" --format '{{.Internal}}')"
network_gateway="$(docker network inspect "$LHTB_MODELONLY_NETWORK" --format '{{(index .IPAM.Config 0).Gateway}}')"
[[ "$network_internal" == true ]] || die "$LHTB_MODELONLY_NETWORK must be internal"
[[ "$network_gateway" == "$LHTB_MODELONLY_GATEWAY" ]] || die "network gateway mismatch: $network_gateway"
[[ "$gateway_host" == "$network_gateway" ]] || die "offline tasks can only reach $network_gateway; gateway uses $gateway_host"

if [[ "$MODE" == prepare ]]; then
  echo "Harbor model-only networking prepared. Run preflight next."
  exit 0
fi

run_stamp="$(date +%Y%m%d-%H%M%S)"
task_args=()
expected_task_count=46
job_suffix="full46"
if [[ "$MODE" == smoke ]]; then
  task_args=(--task "$SMOKE_TASK")
  expected_task_count=1
  job_suffix="smoke-${SMOKE_TASK}"
fi
job_name="lhtb-${LOOPX_EXECUTION_MODE}-${LOOPX_TASK_ENTRY:-default}-${LOOPX_ITERATION_CONTEXT}-${job_suffix}-${run_stamp}"
generated_config="$CODE_DIR/.generated/${job_name}.yaml"
jobs_dir="$CODE_DIR/runs"

task_entry_args=()
if [[ -n "$LOOPX_TASK_ENTRY" ]]; then
  task_entry_args=(--task-entry "$LOOPX_TASK_ENTRY")
fi
turn_timeout_args=()
if [[ -n "$LOOPX_CODEX_TURN_TIMEOUT_SEC" ]]; then
  turn_timeout_args=(--turn-timeout "$LOOPX_CODEX_TURN_TIMEOUT_SEC")
fi
"$VENV/bin/python" "$CODE_DIR/scripts/render_config.py" \
  --template "$CODE_DIR/configs/heartbeat-generic-cli.yaml" \
  --output "$generated_config" \
  --job-name "$job_name" \
  --jobs-dir "$jobs_dir" \
  --concurrency "$CONCURRENCY" \
  --model "$MODEL_NAME" \
  --effort "$REASONING_EFFORT" \
  --timeout "$AGENT_TIMEOUT_SEC" \
  --execution-mode "$LOOPX_EXECUTION_MODE" \
  "${task_entry_args[@]}" \
  --planning-timeout "$LOOPX_PLANNING_TIMEOUT_SEC" \
  --iteration-context "$LOOPX_ITERATION_CONTEXT" \
  --validation-command-json "$LOOPX_VALIDATION_COMMAND_JSON" \
  "${turn_timeout_args[@]}" \
  --scheduler-timeout "$LOOPX_SCHEDULER_TIMEOUT_SEC" \
  "${task_args[@]}"

export OPENAI_BASE_URL OPENAI_API_KEY MODEL_NAME REASONING_EFFORT
export CODEX_BIN CODEX_OFFLINE_DIR CODEX_WIRE_API="${CODEX_WIRE_API:-responses}"
export LOOPX_SRC_DIR LOOPX_EXPECTED_COMMIT LOOPX_PORTABLE_PYTHON LOOPX_NODE_DIR
export LOOPX_SCHEDULER_TIMEOUT_SEC LOOPX_CODEX_TURN_TIMEOUT_SEC
export LHTB_MODELONLY_NETWORK CONCURRENCY AGENT_TIMEOUT_SEC
export LHTB_MODELONLY_NET=1 HB_VERIFIER_FEEDBACK_MODE=binary
export DOCKER_DEFAULT_PLATFORM="${DOCKER_DEFAULT_PLATFORM:-linux/amd64}"
export LITELLM_LOCAL_MODEL_COST_MAP="${LITELLM_LOCAL_MODEL_COST_MAP:-True}"
export PYTHONPATH="$LOOPX_SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"
export NO_PROXY="127.0.0.1,localhost,$gateway_host,${NO_PROXY:-}"
export no_proxy="$NO_PROXY"

# Preserve LHTB's task declarations: 44 shared verifier tasks, two separate.
unset HARBOR_FORCE_SEPARATE_VERIFIER HARBOR_AGENT_URL_FILTER_DIR HARBOR_AGENT_URL_FILTER_IMAGE
unset HB_CONTINUE_MODE HB_PROCESS_REWARD

echo "=== fail-closed preflight ==="
"$VENV/bin/python" "$CODE_DIR/scripts/preflight.py" \
  --config "$generated_config" \
  --lhtb-root "$LHTB_ROOT" \
  --loopx-src "$LOOPX_SRC_DIR" \
  --codex-bin "$CODEX_BIN" \
  --portable-python "$LOOPX_PORTABLE_PYTHON" \
  --node-dir "$LOOPX_NODE_DIR" \
  --gateway "$OPENAI_BASE_URL" \
  --network "$LHTB_MODELONLY_NETWORK" \
  --expected-commit "$LOOPX_EXPECTED_COMMIT" \
  --expected-task-count "$expected_task_count"

mkdir -p "$CODE_DIR/reports" "$CODE_DIR/runs"
receipt="$CODE_DIR/reports/${job_name}.env"
{
  printf 'job_name=%s\nmode=%s\nmodel=%s\nreasoning_effort=%s\n' "$job_name" "$MODE" "$MODEL_NAME" "$REASONING_EFFORT"
  printf 'concurrency=%s\nagent_timeout_sec=%s\nscheduler_timeout_sec=%s\n' "$CONCURRENCY" "$AGENT_TIMEOUT_SEC" "$LOOPX_SCHEDULER_TIMEOUT_SEC"
  printf 'gateway=%s\nwire_api=%s\nweb_search=disabled\n' "$OPENAI_BASE_URL" "$CODEX_WIRE_API"
  printf 'execution_mode=%s\niteration_context=%s\ncodex_home_scope=trial\n' "$LOOPX_EXECUTION_MODE" "$LOOPX_ITERATION_CONTEXT"
  printf 'scheduler_terminal_packet_compatibility=true\n'
  printf 'replan_after_completed_todos=3\nverifier_policy=44_shared_2_separate\n'
  printf 'loopx_commit=%s\n' "$(git -C "$LOOPX_SRC_DIR" rev-parse HEAD)"
  "$CODEX_BIN" --version 2>/dev/null | sed 's/^/codex_version=/' || true
} | tee "$receipt"

if [[ "$MODE" == preflight ]]; then
  echo "Preflight passed. Generated config: $generated_config"
  exit 0
fi

if [[ "$MODE" == full ]]; then
  echo "=== prepare separate verifier images ==="
  LHTB_ROOT="$LHTB_ROOT" "$CODE_DIR/scripts/build_verifier_images.sh"
fi

job_dir="$CODE_DIR/runs/$job_name"
job_complete() {
  [[ -f "$job_dir/result.json" ]] || return 1
  "$VENV/bin/python" - "$job_dir/result.json" <<'PY'
import json, sys
try:
    result = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if result.get("finished_at") else 1)
PY
}

echo "=== run $MODE ==="
attempt=0
while :; do
  set +e
  (
    cd "$LHTB_ROOT"
    "$VENV/bin/harbor" run --yes -c "$generated_config" --max-retries "$LHTB_MAX_RETRIES"
  ) 2>&1 | tee -a "$CODE_DIR/reports/${job_name}.log"
  rc=${PIPESTATUS[0]}
  set -e
  if job_complete; then
    break
  fi
  if (( attempt >= RUNNER_RESTARTS )); then
    die "Harbor exited rc=$rc without a finished result; partial job: $job_dir"
  fi
  attempt=$((attempt + 1))
  echo "Harbor exited rc=$rc; resuming the same job ($attempt/$RUNNER_RESTARTS)"
  sleep 5
done

"$VENV/bin/python" "$CODE_DIR/scripts/summarize_results.py" "$job_dir" || true
echo "Completed: $job_dir"
