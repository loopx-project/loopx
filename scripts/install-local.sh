#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install-local.sh [--help]

Install LoopX from the current checkout. Installation behavior is configured
through environment variables; positional arguments are not supported.

Options:
  -h, --help  Show this help and exit.

Common environment variables:
  LOOPX_PYTHON=/path/to/python    Use a Python 3.11+ executable for the release.
  LOOPX_PROMOTE_DEFAULT=1          Promote this checkout as the default loopx.
  LOOPX_INSTALL_CANARY=0           Skip the loopx-canary executable.
  LOOPX_INSTALL_SKILL=0            Skip packaged workflow skills.
  LOOPX_SKILLS_DIR=/path           Install workflow skills into this host-native root.
  LOOPX_SKILL_DEDUPE_OTHER_ROOT=1  Retire managed LoopX skill copies from the alternate well-known root.
  LOOPX_ENTRY_HOST_SURFACE=...     Bind generated $loopx to an exact host (ark-managed-agent).
  LOOPX_INSTALL_SLASH_COMMANDS=0   Skip Codex and Claude command skills.
  LOOPX_INSTALL_OPENCODE=0         Install the OpenCode goal bridge surface.
  CODEX_HOME=/path                 Override the Codex home directory.
EOF
}

case "${1:-}" in
  -h|--help)
    if [[ "$#" -ne 1 ]]; then
      echo "loopx installer error: --help does not accept additional arguments" >&2
      usage >&2
      exit 2
    fi
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    echo "loopx installer error: unknown argument: $1" >&2
    usage >&2
    exit 2
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

bin_dir="${LOOPX_BIN_DIR:-$HOME/.local/bin}"
shell_profile="${LOOPX_SHELL_PROFILE:-}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_dir_explicit=0
if [[ "${LOOPX_SKILLS_DIR+x}" == "x" ]]; then
  if [[ -z "$LOOPX_SKILLS_DIR" ]]; then
    echo "loopx installer error: LOOPX_SKILLS_DIR cannot be empty" >&2
    exit 2
  fi
  skills_dir_explicit=1
fi
skills_dir="${LOOPX_SKILLS_DIR:-$codex_home/skills}"
entry_host_surface="${LOOPX_ENTRY_HOST_SURFACE:-}"
if [[ -n "$entry_host_surface" && "$entry_host_surface" != "ark-managed-agent" ]]; then
  echo "loopx installer error: unsupported LOOPX_ENTRY_HOST_SURFACE: $entry_host_surface" >&2
  exit 2
fi
man_root="${LOOPX_MAN_ROOT:-$HOME/.local/share/man}"
man_dir="${LOOPX_MAN_DIR:-$man_root/man1}"
install_skill="${LOOPX_INSTALL_SKILL:-1}"
install_canary="${LOOPX_INSTALL_CANARY:-1}"
promote_default_request="${LOOPX_PROMOTE_DEFAULT:-auto}"
releases_dir="${LOOPX_RELEASES_DIR:-$HOME/.local/share/loopx/releases}"
release_id_request="${LOOPX_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
release_id=""
release_dir=""
release_tmp=""
install_lock=""
install_lock_owned=0
skill_install_lock=""
skill_install_lock_owned=0
legacy_line=""
installed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

configure_python_runtime() {
  local requested="${LOOPX_PYTHON:-python3}"
  local resolved
  if ! command -v "$requested" >/dev/null 2>&1; then
    echo "loopx installer error: Python executable not found: $requested" >&2
    echo "Set LOOPX_PYTHON to a Python 3.11+ executable." >&2
    exit 2
  fi
  if ! resolved="$("$requested" - <<'PY'
import sys

if sys.version_info < (3, 11):
    print(
        "loopx installer error: Python 3.11+ is required; "
        f"selected Python is {sys.version_info.major}.{sys.version_info.minor}",
        file=sys.stderr,
    )
    raise SystemExit(2)
print(sys.executable)
PY
)"; then
    echo "Set LOOPX_PYTHON to a Python 3.11+ executable." >&2
    exit 2
  fi
  export LOOPX_PYTHON="$resolved"
}

cleanup_install_lock() {
  if [[ -n "$release_tmp" && -d "$release_tmp" ]]; then
    rm -rf "$release_tmp"
  fi
  if [[ "$install_lock_owned" == "1" && -n "$install_lock" && -d "$install_lock" ]]; then
    rm -rf "$install_lock"
  fi
  if [[ "$skill_install_lock_owned" == "1" && -n "$skill_install_lock" && -d "$skill_install_lock" ]]; then
    rm -rf "$skill_install_lock"
  fi
}

trap cleanup_install_lock EXIT

run_under_install_guard() {
  if [[ "${LOOPX_INSTALL_GUARD_HELD:-0}" == "1" ]]; then
    return 0
  fi
  local python_bin="${LOOPX_PYTHON:-python3}"
  local guard_file="$releases_dir/.install-guard"
  LOOPX_INSTALL_GUARD_FILE="$guard_file" \
    LOOPX_INSTALL_SCRIPT="$repo_root/scripts/install-local.sh" \
    exec "$python_bin" - "$@" <<'PY'
from pathlib import Path
import fcntl
import os
import sys
import time

guard_path = Path(os.environ["LOOPX_INSTALL_GUARD_FILE"])
script = os.environ["LOOPX_INSTALL_SCRIPT"]
timeout = 60.0

guard_path.parent.mkdir(parents=True, exist_ok=True)
with guard_path.open("a+", encoding="utf-8") as guard:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                print(
                    "loopx installer error: timed out waiting for another local install to finish",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            time.sleep(0.1)

    os.set_inheritable(guard.fileno(), True)
    env = dict(os.environ)
    env["LOOPX_INSTALL_GUARD_HELD"] = "1"
    os.execve(script, [script, *sys.argv[1:]], env)
PY
}

reserve_release_id() {
  if [[ ! "$release_id_request" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "loopx installer error: LOOPX_RELEASE_ID must be a safe release directory name" >&2
    exit 2
  fi
  local candidate="$release_id_request"
  local suffix=2
  while [[ -e "$releases_dir/$candidate" || -L "$releases_dir/$candidate" ]]; do
    candidate="$release_id_request-$suffix"
    suffix=$((suffix + 1))
  done
  release_id="$candidate"
  release_dir="$releases_dir/$release_id"
  release_tmp="$(mktemp -d "$releases_dir/.${release_id}.tmp.XXXXXX")"
}

acquire_install_lock() {
  install_lock="$releases_dir/.install-lock"
  local attempt=0
  until mkdir "$install_lock" 2>/dev/null; do
    local owner_pid=""
    if [[ -f "$install_lock/pid" ]]; then
      owner_pid="$(cat "$install_lock/pid" 2>/dev/null || true)"
    fi
    if [[ ! "$owner_pid" =~ ^[0-9]+$ ]]; then
      # A live installer can briefly own the directory before publishing its
      # PID. Give that handoff a grace period, then reclaim interrupted locks
      # that never acquired an owner.
      sleep 1
      if [[ -f "$install_lock/pid" ]]; then
        owner_pid="$(cat "$install_lock/pid" 2>/dev/null || true)"
      fi
    fi
    if [[ ! "$owner_pid" =~ ^[0-9]+$ ]] || ! kill -0 "$owner_pid" 2>/dev/null; then
      local stale_lock="$install_lock.stale.$$"
      if mv "$install_lock" "$stale_lock" 2>/dev/null; then
        rm -rf "$stale_lock"
        continue
      fi
    fi
    attempt=$((attempt + 1))
    if [[ "$attempt" -ge 600 ]]; then
      echo "loopx installer error: timed out waiting for another local install to finish" >&2
      exit 1
    fi
    sleep 0.1
  done
  install_lock_owned=1
  printf '%s\n' "$$" >"$install_lock/pid"
}

warn_stale_promotion_readiness() {
  local python_bin="${LOOPX_PYTHON:-python3}"
  local runtime_root="${LOOPX_RUNTIME_ROOT:-$codex_home/loopx}"
  # Reuse the same collector and registry resolution without importing every CLI.
  LOOPX_PROMOTION_WARNING_RUNTIME_ROOT="$runtime_root" \
    PYTHONSAFEPATH=1 PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}" \
    "$python_bin" - <<'PY_WARNING' || true
import argparse
import os
import sys

from loopx.cli_runtime import resolve_cli_registry
from loopx.paths import default_registry_path
from loopx.promotion_gate import build_promotion_gate

runtime_root = os.environ["LOOPX_PROMOTION_WARNING_RUNTIME_ROOT"]
args = argparse.Namespace(command="promotion-gate", registry=str(default_registry_path()), runtime_root=runtime_root)
registry_path, _ = resolve_cli_registry(args, [])
try:
    payload = build_promotion_gate(registry_path=registry_path, runtime_root_override=runtime_root)
except Exception:
    print("loopx install warning: promotion readiness could not be checked", file=sys.stderr)
else:
    if payload.get("should_warn"):
        message = payload.get("warning_message") or "promotion-readiness evidence requires a canary readiness run before promotion."
        print(f"loopx install warning: {message}", file=sys.stderr)
PY_WARNING
}

copy_platform="$(uname -s)"
copy_path() {
  local src="$1"
  local dst="$2"
  if [[ -e "$src" ]]; then
    # A release owns independent files. APFS clones avoid recopying their data;
    # copy-on-write keeps later cleanup or edits separate from the checkout.
    if [[ "$copy_platform" == "Darwin" ]]; then
      if cp -cR "$src" "$dst" 2>/dev/null; then
        return 0
      fi
      # Cloning may be unsupported across filesystems. Discard only this fresh
      # staging target before ordinary copy, so a partial directory cannot nest.
      rm -rf "$dst"
    fi
    cp -R "$src" "$dst"
  fi
}

append_legacy_line() {
  local message="$1"
  if [[ -z "$legacy_line" ]]; then
    legacy_line="- $message"
  else
    legacy_line="$legacy_line"$'\n'"- $message"
  fi
}

disable_legacy_shim() {
  local name="$1"
  local legacy="$bin_dir/$name"
  local disabled="$bin_dir/$name.legacy-disabled"
  if [[ ! -e "$legacy" && ! -L "$legacy" ]]; then
    return 0
  fi
  if [[ ! -L "$legacy" ]]; then
    append_legacy_line "legacy command left untouched: $legacy is not a symlink"
    return 0
  fi
  local target
  target="$(readlink "$legacy" || true)"
  if [[ "$target" != *"/goal-harness/"* && "$target" != *".local/share/goal-harness/"* ]]; then
    append_legacy_line "legacy command left untouched: $legacy does not point at a legacy release"
    return 0
  fi
  rm -f "$disabled"
  mv "$legacy" "$disabled"
  append_legacy_line "legacy command disabled: $disabled"
}

install_symlink() {
  local target="$1"
  local link="$2"
  local tmp="$link.tmp.$$"
  rm -f "$tmp"
  if [[ ! -L "$link" && -d "$link" ]]; then
    echo "loopx installer error: $link is a directory; remove it before installing" >&2
    return 1
  fi
  ln -s "$target" "$tmp"
  LOOPX_LINK_TMP="$tmp" LOOPX_LINK_TARGET="$link" "${LOOPX_PYTHON:-python3}" - <<'PY'
import os

os.replace(os.environ["LOOPX_LINK_TMP"], os.environ["LOOPX_LINK_TARGET"])
PY
}

install_workflow_skills() {
  local skills_source="$1"
  local source_root="$2"
  local entry_cli_bin="$3"
  local skill_source skill_name skill_scope skill_target skill_tmp entry_status skill_plan
  local installed_skill_ids_text=""
  local -a installed_skill_ids=()
  skill_line="- skill: skipped"
  if [[ "$install_skill" == "0" || ! -d "$skills_source" ]]; then
    return 0
  fi

  # Delivery scope is a product rule, not an installer convention: ask the
  # owning capability which sources this host install may materialize instead
  # of re-reading the scope marker here. ``repo_only`` sources have no marker
  # on purpose and must never be copied onto a host.
  skill_plan="$(mktemp "${TMPDIR:-/tmp}/loopx-skill-plan.XXXXXX")" || return 1
  if ! LOOPX_SKILL_SCOPE_ROOT="$skills_source" \
      PYTHONSAFEPATH=1 \
      PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}" \
      "${LOOPX_PYTHON:-python3}" - >"$skill_plan" <<'PY'
import os
from pathlib import Path

from loopx.capabilities.project_skill_delivery import classify_host_skill_sources

projection = classify_host_skill_sources(Path(os.environ["LOOPX_SKILL_SCOPE_ROOT"]))
for skill_id, scope in (
    [(skill_id, "deliverable") for skill_id in projection["deliverable_skill_ids"]]
    + [(skill_id, "project") for skill_id in projection["project_skill_ids"]]
    + [(skill_id, "repo_only") for skill_id in projection["repo_only_skill_ids"]]
):
    print(f"{skill_id}\t{scope}")
PY
  then
    rm -f "$skill_plan"
    echo "loopx installer error: cannot classify workflow skill sources under $skills_source" >&2
    return 1
  fi

  mkdir -p "$skills_dir"
  skill_install_lock="$skills_dir/.loopx-install-lock"
  local lock_attempt=0
  until mkdir "$skill_install_lock" 2>/dev/null; do
    local owner_pid=""
    if [[ -f "$skill_install_lock/pid" ]]; then
      owner_pid="$(cat "$skill_install_lock/pid" 2>/dev/null || true)"
    fi
    if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
      local stale_lock="$skill_install_lock.stale.$$"
      if mv "$skill_install_lock" "$stale_lock" 2>/dev/null; then
        rm -rf "$stale_lock"
        continue
      fi
    fi
    lock_attempt=$((lock_attempt + 1))
    if [[ "$lock_attempt" -ge 600 ]]; then
      echo "loopx installer error: timed out waiting for workflow skill installation" >&2
      return 1
    fi
    sleep 0.1
  done
  skill_install_lock_owned=1
  printf '%s\n' "$$" >"$skill_install_lock/pid"

  skill_line=""
  while IFS=$'\t' read -r skill_name skill_scope; do
    [[ -n "$skill_name" ]] || continue
    skill_source="$skills_source/$skill_name"
    if [[ "$skill_scope" == "project" ]]; then
      skill_line="${skill_line}- project skill source: $skill_source (install explicitly per project)"$'\n'
      continue
    fi
    if [[ "$skill_scope" == "repo_only" ]]; then
      skill_line="${skill_line}- repo-only skill source: $skill_source (kept in the checkout; never delivered by an install)"$'\n'
      continue
    fi
    skill_target="$skills_dir/$skill_name"
    skill_tmp="$(mktemp -d "$skills_dir/.${skill_name}.tmp.XXXXXX")"
    if ! cp -R "$skill_source"/. "$skill_tmp"/; then
      rm -rf "$skill_tmp"
      return 1
    fi
    rm -rf "$skill_target"
    mv "$skill_tmp" "$skill_target"
    installed_skill_ids+=("$skill_name")
    skill_line="${skill_line}- skill: $skill_target"$'\n'
  done <"$skill_plan"
  rm -f "$skill_plan"

  # The generated `$loopx` entry is the core LoopX route and must be
  # materialized independently of the rich workflow selection.
  # Keep its installation/readback independent from the optional global
  # workflow copies so project-scope filtering cannot hide the main entry.
  if [[ "${#installed_skill_ids[@]}" -gt 0 ]]; then
    installed_skill_ids_text="$(printf '%s\n' "${installed_skill_ids[@]}")"
  fi
  if ! entry_status="$(
      LOOPX_SKILL_INSTALL_DIR="$skills_dir" \
        LOOPX_SKILL_INSTALL_SOURCE_ROOT="$source_root" \
        LOOPX_SKILL_INSTALL_IDS="$installed_skill_ids_text" \
        LOOPX_SKILL_INSTALLED_AT="$installed_at" \
        LOOPX_SKILL_ENTRY_CLI_BIN="$entry_cli_bin" \
        LOOPX_SKILL_ENTRY_HOST_SURFACE="$entry_host_surface" \
        PYTHONSAFEPATH=1 \
        PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}" \
        "${LOOPX_PYTHON:-python3}" - <<'PY'
import os
import sys
from pathlib import Path

from loopx.skill_install_readback import (
    SKILL_INSTALL_READBACK_FILENAME,
    retire_duplicate_managed_skills,
    write_skill_install_readback,
)
from loopx.slash_command_install import materialize_loopx_entry_skill

result = materialize_loopx_entry_skill(
    skills_dir=Path(os.environ["LOOPX_SKILL_INSTALL_DIR"]),
    execute=True,
    cli_bin=os.environ["LOOPX_SKILL_ENTRY_CLI_BIN"],
    host_surface=os.environ.get("LOOPX_SKILL_ENTRY_HOST_SURFACE") or None,
)
status = str(result["status"])
materialized_skill_ids = os.environ["LOOPX_SKILL_INSTALL_IDS"].splitlines()
managed_statuses = {"created", "updated", "unchanged", "upgraded_legacy_managed"}
if status in managed_statuses:
    materialized_skill_ids.append("loopx")
elif os.environ.get("LOOPX_SKILL_ENTRY_HOST_SURFACE"):
    (
        Path(os.environ["LOOPX_SKILL_INSTALL_DIR"])
        / SKILL_INSTALL_READBACK_FILENAME
    ).unlink(missing_ok=True)
    print(
        "loopx installer error: exact-host entry skill was not materialized "
        f"(status={status}, path={result['path']})",
        file=sys.stderr,
    )
    raise SystemExit(1)
write_skill_install_readback(
    skills_dir=Path(os.environ["LOOPX_SKILL_INSTALL_DIR"]),
    skill_ids=materialized_skill_ids,
    source_root=Path(os.environ["LOOPX_SKILL_INSTALL_SOURCE_ROOT"]),
    installed_at=os.environ["LOOPX_SKILL_INSTALLED_AT"],
)
if os.environ.get("LOOPX_SKILL_DEDUPE_OTHER_ROOT") != "0":
    dedupe = retire_duplicate_managed_skills(
        skills_dir=Path(os.environ["LOOPX_SKILL_INSTALL_DIR"]),
        execute=True,
    )
    print(f"skill dedupe: {dedupe['reason']}", file=sys.stderr)
print(status)
PY
    )"; then
      return 1
    fi
    if [[ "$entry_status" == "created" || "$entry_status" == "updated" \
      || "$entry_status" == "unchanged" || "$entry_status" == "upgraded_legacy_managed" ]]; then
      skill_line="${skill_line}- generated skill: $skills_dir/loopx"$'\n'
    else
      skill_line="${skill_line}- generated skill: not materialized ($entry_status)"$'\n'
    fi
    skill_line="${skill_line}- skill readback: $skills_dir/.loopx-skill-install.json"$'\n'
  skill_line="${skill_line%$'\n'}"
  rm -rf "$skill_install_lock"
  skill_install_lock_owned=0
  skill_install_lock=""
}

preflight_workflow_skills() {
  local skills_source="$1"
  local source_root="$2"
  local entry_cli_bin="$3"
  if [[ "$install_skill" == "0" || ! -d "$skills_source" ]]; then
    return 0
  fi

  LOOPX_SKILL_INSTALL_DIR="$skills_dir" \
    LOOPX_SKILL_ENTRY_CLI_BIN="$entry_cli_bin" \
    LOOPX_SKILL_ENTRY_HOST_SURFACE="$entry_host_surface" \
    PYTHONSAFEPATH=1 \
    PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}" \
    "${LOOPX_PYTHON:-python3}" - <<'PY'
import os
import sys
from pathlib import Path

from loopx.skill_install_readback import SKILL_INSTALL_READBACK_FILENAME
from loopx.slash_command_install import materialize_loopx_entry_skill

result = materialize_loopx_entry_skill(
    skills_dir=Path(os.environ["LOOPX_SKILL_INSTALL_DIR"]),
    execute=False,
    cli_bin=os.environ["LOOPX_SKILL_ENTRY_CLI_BIN"],
    host_surface=os.environ.get("LOOPX_SKILL_ENTRY_HOST_SURFACE") or None,
)
if os.environ.get("LOOPX_SKILL_ENTRY_HOST_SURFACE") and result["status"] in {
    "preserved_existing_loopx_skill",
    "skipped_user_file",
}:
    readback = Path(os.environ["LOOPX_SKILL_INSTALL_DIR"]) / SKILL_INSTALL_READBACK_FILENAME
    print(
        "loopx installer error: exact-host entry skill cannot be materialized "
        f"(status={result['status']}, path={result['path']}, readback={readback})",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

verify_default_promotion() {
  LOOPX_VERIFY_LINK="$bin_dir/loopx" \
    LOOPX_VERIFY_RELEASE_DIR="$release_dir" \
    LOOPX_VERIFY_SOURCE_COMMIT="${LOOPX_RESOLVED_SOURCE_GIT_COMMIT:-$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)}" \
    "${LOOPX_PYTHON:-python3}" - <<'PY'
from pathlib import Path
import json
import os
import sys

link = Path(os.environ["LOOPX_VERIFY_LINK"])
release_dir = Path(os.environ["LOOPX_VERIFY_RELEASE_DIR"])
expected = (release_dir / "scripts" / "loopx").resolve()
try:
    actual = link.resolve(strict=True)
except OSError as exc:
    print(f"loopx installer error: default executable cannot be resolved: {exc}", file=sys.stderr)
    raise SystemExit(1)
if actual != expected:
    print(
        "loopx installer error: default executable changed before promotion verification",
        file=sys.stderr,
    )
    raise SystemExit(1)

manifest_path = release_dir / "release.json"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"loopx installer error: release manifest is unreadable: {exc}", file=sys.stderr)
    raise SystemExit(1)
if manifest.get("release_id") != release_dir.name:
    print("loopx installer error: release manifest id does not match its directory", file=sys.stderr)
    raise SystemExit(1)

expected_commit = os.environ.get("LOOPX_VERIFY_SOURCE_COMMIT") or ""
source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
if expected_commit and source.get("git_commit") != expected_commit:
    print("loopx installer error: release manifest does not match the intended source commit", file=sys.stderr)
    raise SystemExit(1)
PY
}

validate_release_candidate() {
  local candidate="$1"
  local candidate_wrapper="$candidate/scripts/loopx"
  if [[ ! -x "$candidate_wrapper" ]]; then
    echo "loopx installer error: release candidate is missing an executable wrapper" >&2
    return 1
  fi
  local fallback_wrapper="$candidate/scripts/loopx-apply-rrule"
  if [[ ! -x "$fallback_wrapper" ]]; then
    echo "loopx installer error: release candidate is missing the executable Codex App fallback wrapper" >&2
    return 1
  fi

  local doctor_json
  if ! doctor_json="$(PATH="$candidate/scripts:$PATH" "$candidate_wrapper" --format json doctor --deep --installation-only)"; then
    echo "loopx installer error: release candidate doctor validation failed" >&2
    return 1
  fi
  LOOPX_CANDIDATE_DOCTOR_JSON="$doctor_json" "${LOOPX_PYTHON:-python3}" - <<'PY'
import json
import os
import sys

try:
    payload = json.loads(os.environ["LOOPX_CANDIDATE_DOCTOR_JSON"])
except (KeyError, json.JSONDecodeError) as exc:
    print(f"loopx installer error: release candidate doctor output is invalid: {exc}", file=sys.stderr)
    raise SystemExit(1)

required_checks = {
    "command_package_same_root",
    "representative_cli_commands",
    "representative_cli_imports",
    "representative_package_paths",
}
checks = {
    str(item.get("id")): item
    for item in payload.get("checks", [])
    if isinstance(item, dict)
}
missing = sorted(required_checks - checks.keys())
failed = sorted(
    check_id
    for check_id in required_checks
    if not checks.get(check_id, {}).get("ok")
)
if payload.get("mode") != "deep" or not payload.get("ok") or missing or failed:
    print(
        "loopx installer error: release candidate is incomplete "
        f"(missing_checks={missing}, failed_checks={failed})",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

resolve_default_promotion() {
  case "$promote_default_request" in
    1|true|yes)
      promotion_mode="${LOOPX_PROMOTION_MODE:-explicit_override}"
      return 0
      ;;
    0|false|no)
      promotion_mode="canary_only_explicit"
      return 1
      ;;
    auto)
      ;;
    *)
      echo "loopx installer error: LOOPX_PROMOTE_DEFAULT must be auto, 1, or 0" >&2
      exit 2
      ;;
  esac

  if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    promotion_mode="canary_only_unverified_source"
    return 1
  fi

  local source_commit approved_commit source_status approved_ref
  source_commit="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
  source_status="$(git -C "$repo_root" status --porcelain 2>/dev/null || true)"
  approved_ref="${LOOPX_APPROVED_DEFAULT_REF:-refs/remotes/origin/main}"
  approved_commit="$(git -C "$repo_root" rev-parse "$approved_ref" 2>/dev/null || true)"
  if [[ -z "$approved_commit" ]]; then
    approved_commit="$(git -C "$repo_root" rev-parse refs/heads/main 2>/dev/null || true)"
  fi
  if [[ -n "$source_commit" && "$source_commit" == "$approved_commit" && -z "$source_status" ]]; then
    promotion_mode="trusted_main_auto"
    return 0
  fi

  promotion_mode="canary_only_untrusted_checkout"
  return 1
}

if [[ -z "$shell_profile" ]]; then
  case "${SHELL:-}" in
    */zsh) shell_profile="$HOME/.zshrc" ;;
    */bash) shell_profile="$HOME/.bashrc" ;;
    *) shell_profile="$HOME/.profile" ;;
  esac
fi

configure_python_runtime

promote_default=0
if resolve_default_promotion; then
  promote_default=1
fi

if [[ "$promote_default" == "0" ]]; then
  if [[ "$install_canary" == "0" ]]; then
    echo "loopx installer error: default promotion is guarded and LOOPX_INSTALL_CANARY=0 leaves no install target" >&2
    echo "Set LOOPX_PROMOTE_DEFAULT=1 only after explicitly approving this checkout." >&2
    exit 2
  fi
  mkdir -p "$bin_dir"
  chmod +x "$repo_root/scripts/loopx"
  install_symlink "$repo_root/scripts/loopx" "$bin_dir/loopx-canary"
  export PATH="$bin_dir:$PATH"
  "$bin_dir/loopx-canary" doctor >/dev/null
  skill_line="- skill: unchanged (set LOOPX_SKILLS_DIR to install canary workflow skills)"
  if [[ "$skills_dir_explicit" == "1" ]]; then
    install_workflow_skills "$repo_root/skills" "$repo_root" "$bin_dir/loopx-canary"
  fi
  cat <<EOF
loopx checkout installed as canary only
- default executable: unchanged
- canary executable: $bin_dir/loopx-canary
- promotion mode: $promotion_mode
$skill_line
- promote explicitly: LOOPX_PROMOTE_DEFAULT=1 $repo_root/scripts/install-local.sh

Current shell can use the canary with:
  export PATH="$bin_dir:\$PATH"
  loopx-canary doctor
EOF
  exit 0
fi

export LOOPX_PROMOTION_MODE="$promotion_mode"

mkdir -p "$releases_dir"
run_under_install_guard "$@"
warn_stale_promotion_readiness
acquire_install_lock
mkdir -p "$bin_dir"
disable_legacy_shim "goal-harness"
disable_legacy_shim "goal-harness-canary"
if [[ -z "$legacy_line" ]]; then
  legacy_line="- legacy command disabled: not present"
fi
reserve_release_id
copy_path "$repo_root/loopx" "$release_tmp/loopx"
copy_path "$repo_root/scripts" "$release_tmp/scripts"
copy_path "$repo_root/skills" "$release_tmp/skills"
copy_path "$repo_root/docs" "$release_tmp/docs"
copy_path "$repo_root/man" "$release_tmp/man"
copy_path "$repo_root/examples" "$release_tmp/examples"
copy_path "$repo_root/apps" "$release_tmp/apps"
copy_path "$repo_root/.github" "$release_tmp/.github"
copy_path "$repo_root/README.md" "$release_tmp/README.md"
copy_path "$repo_root/LICENSE" "$release_tmp/LICENSE"
copy_path "$repo_root/pyproject.toml" "$release_tmp/pyproject.toml"
printf '%s\n' "$LOOPX_PYTHON" >"$release_tmp/.loopx-python"
find "$release_tmp" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$release_tmp" -name '*.pyc' -type f -delete
if [[ -d "$release_tmp/apps" ]]; then
  find "$release_tmp/apps" \
    \( -name node_modules -o -name .next -o -name dist -o -name build -o -name coverage \) \
    -type d -prune -exec rm -rf {} +
fi
PYTHONPATH="$release_tmp" "${LOOPX_PYTHON:-python3}" \
  "$release_tmp/scripts/render-manpage.py" \
  --output "$release_tmp/man/loopx.1"
(
  cd "$release_tmp"
  PYTHONPATH="$release_tmp" "${LOOPX_PYTHON:-python3}" -m loopx.release_manifest \
    "$release_tmp" \
    --release-id "$release_id" \
    --source-root "$repo_root" \
    --installed-at "$installed_at"
)
chmod +x "$release_tmp/scripts/loopx" "$release_tmp/scripts/loopx-apply-rrule"
mv "$release_tmp" "$release_dir"
release_tmp=""
if ! validate_release_candidate "$release_dir"; then
  rm -rf "$release_dir"
  exit 1
fi
if ! preflight_workflow_skills "$release_dir/skills" "$release_dir" "$bin_dir/loopx"; then
  rm -rf "$release_dir"
  exit 1
fi
install_symlink "$release_dir/scripts/loopx" "$bin_dir/loopx"
install_symlink "$release_dir/scripts/loopx-apply-rrule" "$bin_dir/loopx-apply-rrule"
verify_default_promotion

canary_line="- canary executable: skipped"
if [[ "$install_canary" != "0" ]]; then
  chmod +x "$repo_root/scripts/loopx"
  install_symlink "$repo_root/scripts/loopx" "$bin_dir/loopx-canary"
  canary_line="- canary executable: $bin_dir/loopx-canary"
fi

if [[ -n "$shell_profile" ]]; then
  touch "$shell_profile"
  if ! grep -F "$bin_dir" "$shell_profile" >/dev/null 2>&1 \
    && ! grep -F '$HOME/.local/bin' "$shell_profile" >/dev/null 2>&1; then
    {
      printf '\n# LoopX local CLI\n'
      if [[ "$bin_dir" == "$HOME/.local/bin" ]]; then
        printf 'export PATH="$HOME/.local/bin:$PATH"\n'
      else
        printf 'export PATH="%s:$PATH"\n' "$bin_dir"
      fi
    } >>"$shell_profile"
  fi
  if ! grep -F "$man_root" "$shell_profile" >/dev/null 2>&1 \
    && ! grep -F '$HOME/.local/share/man' "$shell_profile" >/dev/null 2>&1; then
    {
      printf '\n# LoopX local manual\n'
      if [[ "$man_root" == "$HOME/.local/share/man" ]]; then
        printf 'export MANPATH="$HOME/.local/share/man:${MANPATH:-}"\n'
      else
        printf 'export MANPATH="%s:${MANPATH:-}"\n' "$man_root"
      fi
    } >>"$shell_profile"
  fi
fi

man_line="- manpage: skipped (source missing)"
man_source="$release_dir/man/loopx.1"
man_target="$man_dir/loopx.1.gz"
if [[ -f "$man_source" ]]; then
  mkdir -p "$man_dir"
  MAN_SOURCE="$man_source" MAN_TARGET="$man_target" "${LOOPX_PYTHON:-python3}" - <<'PY'
from pathlib import Path
import gzip
import os
import shutil

source = Path(os.environ["MAN_SOURCE"])
target = Path(os.environ["MAN_TARGET"])
target.parent.mkdir(parents=True, exist_ok=True)
with source.open("rb") as raw, gzip.open(target, "wb", compresslevel=9) as zipped:
    shutil.copyfileobj(raw, zipped)
PY
  chmod 0644 "$man_target"
  man_line="- manpage: $man_target"
fi

export PATH="$bin_dir:$PATH"
"$bin_dir/loopx" doctor --installation-only >/dev/null
if [[ "${LOOPX_INSTALL_REVALIDATE_EXTENSIONS:-1}" != "0" ]] && ! "$bin_dir/loopx" --format json extension doctor --all-enabled --execute >/dev/null; then
  echo "loopx installer warning: one or more enabled extensions failed post-install doctor revalidation" >&2
  echo "Run 'loopx extension doctor --all-enabled --execute' after repairing the provider." >&2
fi
if [[ "$install_canary" != "0" ]]; then
  "$bin_dir/loopx-canary" doctor --installation-only >/dev/null
fi

install_workflow_skills "$release_dir/skills" "$release_dir" "$bin_dir/loopx"

slash_line="- slash commands: skipped"
install_slash_commands="${LOOPX_INSTALL_SLASH_COMMANDS:-1}"
slash_surfaces="${LOOPX_INSTALL_SLASH_COMMAND_SURFACES:-all}"
if [[ "$install_slash_commands" != "0" ]]; then
  slash_args=(slash-commands --install)
  IFS=',' read -ra slash_surface_list <<<"$slash_surfaces"
  for slash_surface in "${slash_surface_list[@]}"; do
    if [[ -n "$slash_surface" ]]; then
      slash_args+=(--surface "$slash_surface")
    fi
  done
  if slash_json="$("$bin_dir/loopx" --format json "${slash_args[@]}" 2>/dev/null)"; then
    slash_line="$(LOOPX_SLASH_INSTALL_JSON="$slash_json" "${LOOPX_PYTHON:-python3}" - <<'PY'
import json
import os

payload = json.loads(os.environ["LOOPX_SLASH_INSTALL_JSON"])
summary = payload.get("summary") or {}
counts = summary.get("status_counts") or {}
count_text = ",".join(f"{key}={counts[key]}" for key in sorted(counts)) or "none"
parts = []
if summary.get("codex_prompt_dir"):
    parts.append(f"codex prompts: {summary['codex_prompt_dir']}")
if summary.get("codex_skill_dir"):
    parts.append(f"codex skills: {summary['codex_skill_dir']}")
if summary.get("claude_skill_dir"):
    parts.append(f"claude skills: {summary['claude_skill_dir']}")
if not parts:
    parts.append("no supported surfaces selected")
print(f"- slash commands: {'; '.join(parts)} ({count_text})")
PY
)"
  else
    slash_line="- slash commands: install attempted; run manually: loopx slash-commands --install"
  fi
fi

# loopx Claude Code adapter: OPT-IN, OFF by default — the normal loopx install
# does not install MCP, hooks, or settings. The lightweight slash-command
# skills above may write ~/.claude/skills so Claude Code can discover /loopx,
# while the run-loop adapter remains explicit. Enable the adapter with
# LOOPX_INSTALL_CLAUDE=1 (installs at USER scope: MCP + /loopx command). Add
# the optional should_run gate later with `install.py --scope <user|project>
# --harden`. Prefer PROJECT scope:
# `python <release>/loopx/claude_goal_mode/scripts/install.py --scope project`.
claude_installer="$release_dir/loopx/claude_goal_mode/scripts/install.py"
install_claude="${LOOPX_INSTALL_CLAUDE:-0}"
claude_line="- loopx Claude adapter: skipped (opt-in; LOOPX_INSTALL_CLAUDE=1, or run install.py --scope project|user)"
if [[ "$install_claude" != "0" && -f "$claude_installer" ]]; then
  if ! command -v claude >/dev/null 2>&1; then
    claude_line="- loopx Claude adapter: skipped (Claude Code not found on PATH)"
  else
    claude_python="${LOOPX_PYTHON:-python3}"
    command -v "$claude_python" >/dev/null 2>&1 || claude_python="python"
    if "$claude_python" "$claude_installer" --scope user >/dev/null 2>&1; then
      claude_line="- loopx Claude adapter: ~/.claude (MCP + /loopx, user scope; no hooks — add with --harden)"
    else
      claude_line="- loopx Claude adapter: install attempted; run manually: $claude_python \"$claude_installer\" --scope user"
    fi
  fi
fi

# loopx OpenCode goal bridge: OPT-IN, OFF by default. Static OpenCode commands
# remain part of the ordinary slash-command install; this flag additionally
# provisions the plugin, runtime, and pinned dependencies. The bridge wraps
# opencode-goal-plugin@0.7.0 and gates idle continuation plus timer wakes
# through LoopX quota should-run. Install manually with:
#   loopx slash-commands --install --surface opencode --with-goal-bridge
install_opencode="${LOOPX_INSTALL_OPENCODE:-0}"
opencode_line="- loopx OpenCode bridge: skipped (opt-in; LOOPX_INSTALL_OPENCODE=1, or run: loopx slash-commands --install --surface opencode --with-goal-bridge)"
if [[ "$install_opencode" != "0" ]]; then
  if "$bin_dir/loopx" slash-commands --install --surface opencode --with-goal-bridge >/dev/null 2>&1; then
    opencode_line="- loopx OpenCode bridge: ~/.config/opencode (commands, plugin, runtime, pinned deps; restart OpenCode after install)"
  else
    opencode_line="- loopx OpenCode bridge: install attempted; run manually: loopx slash-commands --install --surface opencode --with-goal-bridge"
  fi
fi

cat <<EOF
loopx installed locally
- executable: $bin_dir/loopx
- Codex App fallback executable: $bin_dir/loopx-apply-rrule
- release: $release_dir
- promotion mode: $promotion_mode
- manual root: $man_root
$man_line
$canary_line
- executable compatibility: none
$legacy_line
- profile: $shell_profile
$skill_line
$slash_line
$claude_line
$opencode_line
- first-run feedback (optional): https://github.com/loopx-project/loopx/issues/new?template=first_run.yml

Current shell can use it with:
  export PATH="$bin_dir:\$PATH"
  export MANPATH="$man_root:\${MANPATH:-}"
  loopx doctor
  loopx first-run-report
  man loopx
EOF
