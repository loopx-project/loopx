#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(cd "$PACKAGE_ROOT/../.." && pwd -P)"
DSH_SMOKE_PID=''
LOG_PREVIEW_RANGE='1,240p'

container_smoke() {
  export DEBIAN_FRONTEND=noninteractive
  local smoke_root
  smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/dsh-loopx-container.XXXXXX")"
  chmod 700 "$smoke_root"
  export DSH_HOME="$smoke_root/dsh-home"
  export DSH_AGENTS_HOME="$smoke_root/agents"
  export PYTHON_BIN=python3
  export PIP_FIND_LINKS=/artifact
  export PIP_NO_INDEX=1
  local dsh_log="$smoke_root/dsh.log"
  local pep668_log="$smoke_root/pep668.log"

  print_dsh_log() {
    sed -n "$LOG_PREVIEW_RANGE" "$dsh_log" \
      | sed -E 's/([?&][A-Za-z0-9_-]+=)[A-Za-z0-9_-]+/\1<redacted>/g'
  }

  if python3 -m pip install --dry-run --no-index loopx >"$pep668_log" 2>&1; then
    echo 'clean Docker smoke: PEP 668 guard was not active' >&2
    return 1
  fi
  grep -qi 'externally.managed' "$pep668_log" || {
    sed -n '1,80p' "$pep668_log"
    echo 'clean Docker smoke: pip did not report the PEP 668 guard' >&2
    return 1
  }

  local workspace="$smoke_root/workspace"
  mkdir -p "$workspace"
  timeout 180 dsh plugin --profile web add \
    /artifact/dsh-loopx-plugin.tgz --ignore-scripts
  [[ "$(dsh --version)" == '0.1.5-rc.2' ]] || {
    echo 'clean Docker smoke: unexpected DSH regression version' >&2
    return 1
  }

  dsh --profile web --port 0 --no-open >"$dsh_log" 2>&1 &
  DSH_SMOKE_PID=$!
  cleanup_dsh() {
    [[ "$DSH_SMOKE_PID" =~ ^[0-9]+$ ]] || return 0
    kill "$DSH_SMOKE_PID" 2>/dev/null || true
    wait "$DSH_SMOKE_PID" 2>/dev/null || true
  }
  trap cleanup_dsh EXIT

  local base_url=''
  for _attempt in $(seq 1 180); do
    base_url="$(awk '/dsh web: / { value = $NF } END { print value }' "$dsh_log")"
    [[ -n "$base_url" ]] && break
    if ! kill -0 "$DSH_SMOKE_PID" 2>/dev/null; then
      print_dsh_log
      return 1
    fi
    sleep 1
  done

  if [[ -z "$base_url" ]]; then
    print_dsh_log
    echo 'clean Docker smoke: DSH did not publish a URL' >&2
    return 1
  fi

  local runtime_dir="$DSH_AGENTS_HOME/runtime/dsh-loopx-plugin"
  for expected in \
    "$runtime_dir/loopx_cli.py" \
    "$runtime_dir/site-packages/loopx" \
    "$DSH_AGENTS_HOME/skills/loopx/SKILL.md"; do
    if [[ ! -e "$expected" ]]; then
      print_dsh_log
      find "$DSH_AGENTS_HOME" "$HOME/.agents" \
        -maxdepth 4 -type f -print 2>/dev/null | sort || true
      if [[ -f "$runtime_dir/loopx_cli.py" ]]; then
        python3 "$runtime_dir/loopx_cli.py" --version || true
        python3 "$runtime_dir/loopx_cli.py" --format json workflow-skills \
          --skills-dir "$DSH_AGENTS_HOME/skills" \
          --host-surface deepseek-harness-native || true
      fi
      echo "clean Docker smoke: automatic bootstrap omitted $expected" >&2
      return 1
    fi
  done
  python3 "$runtime_dir/loopx_cli.py" --version | grep -E '^loopx '
  if ! node "$SCRIPT_DIR/dsh-clean-docker-probe.mjs" "$base_url" "$workspace"; then
    print_dsh_log
    return 1
  fi
  printf '\nclean Docker smoke passed\n'
}

if [[ "${1:-}" == '--container' ]]; then
  [[ "$#" -eq 1 ]] || { echo 'usage: dsh-clean-docker-smoke.sh [--container]' >&2; exit 2; }
  container_smoke
  exit 0
fi
[[ "$#" -eq 0 ]] || { echo 'usage: dsh-clean-docker-smoke.sh [--container]' >&2; exit 2; }

for command in docker pnpm uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "clean Docker smoke: required command not found: $command" >&2
    exit 2
  }
done

if [[ -n "${DSH_DOCKER_SMOKE_TMPDIR:-}" ]]; then
  artifact_parent="$DSH_DOCKER_SMOKE_TMPDIR"
elif [[ "$(uname -s)" == 'Darwin' ]]; then
  # Docker Desktop and Colima share the user's home by default, but not every
  # per-user macOS TMPDIR under /var/folders.
  artifact_parent="$HOME/.cache/loopx-dsh-smoke"
else
  artifact_parent="${TMPDIR:-/tmp}"
fi
mkdir -p "$artifact_parent"
artifact_dir="$(mktemp -d "$artifact_parent/dsh-loopx-clean-docker.XXXXXX")"
image_id=''
cleanup_artifact() {
  if [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    docker image rm --force "$image_id" >/dev/null 2>&1 || true
  fi
  [[ -n "$artifact_dir" && -d "$artifact_dir" ]] || return 0
  [[ "$artifact_dir" == "$artifact_parent"/dsh-loopx-clean-docker.* ]] || {
    echo "clean Docker smoke: refusing unexpected cleanup path: $artifact_dir" >&2
    return 1
  }
  rm -rf -- "$artifact_dir"
}
trap cleanup_artifact EXIT

pnpm --dir "$PACKAGE_ROOT" pack --out "$artifact_dir/dsh-loopx-plugin.tgz"
if ! uv build --wheel --out-dir "$artifact_dir" "$REPO_ROOT" \
  >"$artifact_dir/loopx-wheel-build.log" 2>&1; then
  sed -n "$LOG_PREVIEW_RANGE" "$artifact_dir/loopx-wheel-build.log"
  echo 'clean Docker smoke: LoopX release-candidate wheel build failed' >&2
  exit 1
fi
wheel_count="$(find "$artifact_dir" -maxdepth 1 -name 'loopx-*.whl' -print | wc -l | tr -d '[:space:]')"
[[ "$wheel_count" == '1' ]] || {
  echo "clean Docker smoke: expected one LoopX wheel, found $wheel_count" >&2
  exit 1
}
chmod 755 "$artifact_dir"
chmod 644 "$artifact_dir/dsh-loopx-plugin.tgz" "$artifact_dir"/loopx-*.whl
install -m 755 "$SCRIPT_DIR/dsh-clean-docker-smoke.sh" \
  "$artifact_dir/dsh-clean-docker-smoke.sh"
install -m 644 "$SCRIPT_DIR/dsh-clean-docker-probe.mjs" \
  "$artifact_dir/dsh-clean-docker-probe.mjs"
docker build \
  --file "$SCRIPT_DIR/Dockerfile.clean" \
  --iidfile "$artifact_dir/image-id" \
  "$SCRIPT_DIR"
image_id="$(tr -d '[:space:]' <"$artifact_dir/image-id")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo 'clean Docker smoke: Docker did not return an exact image id' >&2
  exit 1
}
docker run --rm \
  --mount "type=bind,src=$artifact_dir,dst=/artifact,readonly" \
  "$image_id" \
  bash /artifact/dsh-clean-docker-smoke.sh --container
