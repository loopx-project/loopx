"""Antigravity CLI (``agy``) & Antigravity App (``antigravity-app``) host-surface constants and helpers.

Supports both:
- Legacy Antigravity CLI (``agy`` 1.x) flat skills in ``~/.gemini/antigravity-cli/skills/<name>.md`` with native ``/goal`` and ``schedule`` tools.
- Antigravity 2.0 / Antigravity App (``antigravity-app``) directory skills in ``~/.gemini/config/skills/<name>/SKILL.md``, in-session ``schedule(DurationSeconds=..., Prompt=...)`` closed-loop timer suspension (for ``waiting_external`` / CI waits), background ``sidecar.json`` periodic heartbeat template, and optional ``mcp_config.json`` + ``hooks.json`` bounded Stop recovery registration.
"""

from __future__ import annotations

from collections.abc import Sequence
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ...command_invocation import loopx_cli_argv
from ..capabilities import (
    HostHarnessCapabilities,
    _register_builtin_host_capabilities,
)

AGY_HOST_CAPABILITIES: HostHarnessCapabilities = _register_builtin_host_capabilities(
    HostHarnessCapabilities(
        host_id="agy",
        integration_tier="orchestrated_app",
        adapter_module="loopx.hosts.agy",
        supported_capabilities=(),
        hook_events=("Stop",),
        requires_utf8_stdio=True,
        native_goal_primitive=True,
        native_scheduler_primitive=True,
        description=(
            "Antigravity CLI and App orchestrated adapter with native /goal, "
            "schedule wake tools, Stop EOF recovery hook, and "
            "ANTIGRAVITY_CONVERSATION_ID binding."
        ),
    )
)

AGY_INSTALL_SURFACE = "agy"
ANTIGRAVITY_APP_INSTALL_SURFACE = "antigravity-app"
ANTIGRAVITY_APP_AGENT_TYPE = "antigravity-app"
ANTIGRAVITY_CONVERSATION_ID_ENV = "ANTIGRAVITY_CONVERSATION_ID"

# Every token normalize_agent_type and the slash-commands installer accept for
# the Antigravity CLI surface.
AGY_ACCEPTED_INPUTS: tuple[str, ...] = (
    "agy",
    "antigravity",
    "antigravity-cli",
    "antigravity_cli",
    "antigravity cli",
    "google-antigravity",
    "google antigravity",
)

ANTIGRAVITY_APP_ACCEPTED_INPUTS: tuple[str, ...] = (
    "antigravity-app",
    "antigravity_app",
    "antigravity app",
    "agy-app",
    "agy_app",
    "agy app",
    "antigravity-2",
    "antigravity 2.0",
)

# Native in-session automation primitives agy and Antigravity App ship.
AGY_NATIVE_WAKE_TOOLS: tuple[str, ...] = (
    "schedule",
    "manage_task",
    "invoke_subagent",
    "send_message",
    "manage_inbox",
)

AGY_NATIVE_WAKE_FACTS: tuple[str, ...] = (
    "The built-in `schedule` tool arms a one-shot timer (`DurationSeconds` + "
    "wake `Prompt`; optional `TimerCondition`: `never`, `any`, or a sender ID) "
    "or a recurring cron (`CronExpression` + `Prompt`; optional `MaxIterations`, "
    "`IsDaemon`, `ExactTiming`) that delivers its `Prompt` as a high-priority "
    "wakeup notification when it fires; `manage_task` lists or kills active "
    "schedules.",
    "In an Antigravity App fixed resident session (`antigravity-app`), arm "
    "`schedule(DurationSeconds=max(180, recommended_interval_minutes * 60), "
    "TimerCondition=\"any\", Prompt=...)` (3-minute / 180s minimum floor; "
    "unchanged-wait backoff `3 -> 6 -> 10` minutes) before ending every patrol "
    "turn (including `waiting_external`, CI/PR waits, settled turns, or quiet "
    "no-ops) unless `scheduler_hint.action=stop_until_explicit_resume`, so the "
    "host wakes the same conversation reactively instead of running shell sleep "
    "polling loops or spawning `new-conversation` sidecars.",
    "Background `run_command` tasks and `invoke_subagent` runs also resume the "
    "session reactively on completion or message arrival, so the agent does "
    "not need a polling loop inside a turn.",
    "For fixed in-session patrols, bind the current `<user_information>` "
    "`Conversation ID` (or `$ANTIGRAVITY_CONVERSATION_ID`) via `bind-agent-thread` "
    "+ `sync-global` and configure bound `hooks.json` `Stop` recovery in "
    "`~/.gemini/config` without creating a `new-conversation` `sidecar.json`; "
    "use `sidecar.json` only when explicitly requesting detached fresh-conversation ticks.",
)

# Native goal primitive (built into the agy binary; live-verified on 1.1.18 in
# both the TUI and headless `agy -p`).
AGY_GOAL_COMMAND = "/goal"
AGY_GOAL_COMMAND_DESCRIPTION = (
    "Pursue a goal relentlessly until it is completely finished"
)
AGY_GOAL_COMPLETE_TOKEN = "<!-- GOAL_COMPLETE -->"
AGY_GOAL_CANCELLED_TOKEN = "<!-- GOAL_CANCELLED -->"

DEFAULT_AGY_HOME = ".gemini/antigravity-cli"
DEFAULT_ANTIGRAVITY_CONFIG_HOME = ".gemini/config"
ANTIGRAVITY_SKILLS_ROOT_LABEL = "~/.gemini/config/skills"
ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL = "~/.gemini/antigravity-cli/skills"
ANTIGRAVITY_MCP_CONFIG_FILENAME = "mcp_config.json"
ANTIGRAVITY_HOOKS_CONFIG_FILENAME = "hooks.json"
ANTIGRAVITY_SIDECAR_FILENAME = "sidecar.json"
ANTIGRAVITY_MCP_CONFIG_LABEL = "~/.gemini/config/mcp_config.json"
ANTIGRAVITY_HOOKS_CONFIG_LABEL = "~/.gemini/config/hooks.json"
ANTIGRAVITY_SIDECAR_CONFIG_LABEL = (
    "~/.gemini/config/sidecars/loopx-antigravity-heartbeat/sidecar.json"
)

ANTIGRAVITY_APP_AGENT_TYPE_CATALOG_ENTRY: dict[str, Any] = {
    "display_name": "Antigravity App",
    "host_loop": (
        "Antigravity App fixed-session in-place schedule timer closed loop + "
        "hooks.json bound Stop recovery (or optional detached sidecar.json)"
    ),
    "entry": (
        f"the LoopX skill installed in {ANTIGRAVITY_SKILLS_ROOT_LABEL} "
        f"(compat: {ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL})"
    ),
    "accepted_inputs": list(ANTIGRAVITY_APP_ACCEPTED_INPUTS),
}


def build_antigravity_sidecar_template(
    *,
    goal_id: str = "<goal-id>",
    agent_id: str | None = None,
    runtime_root: str | None = None,
    registry: str | None = None,
    cli_bin: str = "loopx",
    interval_seconds: int = 180,
) -> dict[str, Any]:
    """Build a public-safe Antigravity 2.0 ``sidecar.json`` periodic heartbeat template."""
    cli_args = [*loopx_cli_argv(cli_bin), "--format", "json"]
    if runtime_root:
        cli_args.extend(["--runtime-root", runtime_root])
    if registry:
        cli_args.extend(["--registry", registry])
    profile_args = ["--goal-id", goal_id, "--runtime-profile", "antigravity_app"]
    quota_args = [*cli_args, "quota", "should-run", *profile_args]
    if agent_id:
        quota_args.extend(["--agent-id", agent_id])
    heartbeat_args = [*cli_args, "heartbeat-prompt", "--thin", *profile_args]
    if agent_id:
        heartbeat_args.extend(["--agent-id", agent_id])
    bootstrap_prompt = (
        "LoopX managed heartbeat bootstrap v2\n"
        "每次唤醒先执行：\n"
        f"```sh\n{shlex.join(heartbeat_args)}\n```\n"
        "读取完整结果；仅 ok=true 时按本次 task_body 推进，不复用旧指令。"
        '通知与执行分开，等待按当前调度契约（遇到 waiting_external / CI 等待时使用原生 schedule(DurationSeconds=..., TimerCondition="any") 挂起，不跑 shell sleep 空查）。'
        "入口异常先做权限内恢复；契约仍不可用时不执行任务或记账，并报告阻塞。"
    )
    interval_minutes = max(1, int(interval_seconds) // 60)
    cron_expr = cron_for_interval_minutes(interval_minutes)
    return {
        "schema_version": "loopx_antigravity_sidecar_template_v0",
        "config_path": ANTIGRAVITY_SIDECAR_CONFIG_LABEL,
        "sidecar_config_path": ANTIGRAVITY_SIDECAR_CONFIG_LABEL,
        "sidecar_json": {
            "builtin": "schedule",
            "args": [
                f"CRON_TZ=UTC {cron_expr}",
                "agentapi",
                "new-conversation",
                "--model=pro",
                "--",
                bootstrap_prompt,
            ],
            "restart_policy": "always",
            "display_name": "LoopX Antigravity Heartbeat",
            "description": (f"LoopX periodic quota-gated heartbeat for goal {goal_id}"),
            "agent_permissions": {
                "access_grants": [
                    f"command({cli_bin})",
                ]
            },
        },
        "sidecar": {
            "loopx-heartbeat": {
                "description": (
                    f"LoopX periodic quota-gated heartbeat for goal {goal_id}"
                ),
                "cron": cron_expr,
                "interval_seconds": int(interval_seconds),
                "gate_command": shlex.join(quota_args),
                "wake_when_json_field_true": "should_run",
                "prompt_command": shlex.join(heartbeat_args),
                "prompt_json_field": "task_body",
                "bootstrap_prompt": bootstrap_prompt,
                "suspend_on_waiting_external": {
                    "tool": "schedule",
                    "interval_minutes_field": (
                        "scheduler_hint.app_automation.recommended_interval_minutes"
                    ),
                    "fallback_interval_minutes_field": (
                        "scheduler_hint.local_scheduler.recommended_interval_minutes"
                    ),
                    "default_duration_seconds": int(interval_seconds),
                },
                "stateful_backoff": {
                    "state_key": "scheduler_hint.app_automation.stateful_backoff",
                    "recommended_rrule_field": (
                        "scheduler_hint.app_automation.recommended_rrule"
                    ),
                    "recommended_interval_minutes_field": (
                        "scheduler_hint.app_automation.recommended_interval_minutes"
                    ),
                    "ack_hint_field": (
                        "scheduler_hint.app_automation.ack_hint.cli_args"
                    ),
                    "failure_hint_field": (
                        "scheduler_hint.app_automation.failure_hint.cli_args"
                    ),
                },
            }
        },
    }


_HOST_RUNTIME_MODULE = "loopx.hosts.agy.host_runtime"
_RELEASE_ROOT_BOOTSTRAP = (
    "import os,runpy,shutil,sys;from pathlib import Path;"
    "a=sys.argv[1:];"
    'c=next((a[i+1] for i,v in enumerate(a) if v=="--cli-bin" and i+1<len(a)),'
    'next((v[10:] for v in a if v.startswith("--cli-bin=")),None));'
    'c=(shutil.which(c) or c) if c and os.sep not in c and (os.altsep is None or os.altsep not in c) and not c.startswith(".") else c;'
    "p=Path(c).expanduser().resolve() if c else None;"
    'ok=p is not None and p.is_file() and (os.name=="nt" or os.access(p,os.X_OK)) and len(p.parents)>1 and p.parent.name in ("scripts","bin") and (p.parents[1]/"loopx"/"__init__.py").is_file() and (p.parents[1]/"loopx"/"hosts"/"agy"/"__init__.py").is_file() and (p.parents[1]/"loopx"/"hosts"/"agy"/"host_runtime.py").is_file();'
    'not ok and sys.exit("loopx release root not found from --cli-bin");'
    "r=p.parents[1];"
    'sys.path[:]=[str(r),*(x for x in sys.path if x not in ("",".",str(r)))];'
    'os.environ["LOOPX_RELEASE_ROOT"]=str(r);'
    f'sys.argv=["{_HOST_RUNTIME_MODULE}",*a];'
    f'runpy.run_module("{_HOST_RUNTIME_MODULE}",run_name="__main__",alter_sys=True)'
)
_POSIX_RELEASE_SHELL_BOOTSTRAP = (
    "set -eu\n"
    'fallback_py="$1"\n'
    'boot="$2"\n'
    "shift 2\n"
    'cli=""\n'
    'prev=""\n'
    'for arg in "$@"; do\n'
    '  if [ "$prev" = "--cli-bin" ]; then\n'
    '    cli="$arg"\n'
    "    break\n"
    "  fi\n"
    '  case "$arg" in\n'
    '    --cli-bin=*) cli="${arg#--cli-bin=}"; break ;;\n'
    "  esac\n"
    '  prev="$arg"\n'
    "done\n"
    'case "$cli" in\n'
    '  "") p="" ;;\n'
    '  */*) p="$cli" ;;\n'
    '  *) p="$(command -v "$cli" 2>/dev/null || printf \'%s\' "$cli")" ;;\n'
    "esac\n"
    'while [ -n "$p" ] && [ -L "$p" ]; do\n'
    '  case "$p" in\n'
    '    */*) p_dir="${p%/*}"; [ -n "$p_dir" ] || p_dir="/" ;;\n'
    '    *) p_dir="." ;;\n'
    "  esac\n"
    '  d="$(cd -P "$p_dir" && pwd)"\n'
    '  t="$(readlink "$p")"\n'
    '  case "$t" in\n'
    '    /*) p="$t" ;;\n'
    '    *) p="$d/$t" ;;\n'
    "  esac\n"
    "done\n"
    'if [ -z "$p" ] || [ ! -f "$p" ] || [ ! -x "$p" ]; then\n'
    '  printf \'loopx release root not found from --cli-bin\\n\' >&2\n'
    "  exit 1\n"
    "fi\n"
    'case "$p" in\n'
    '  */*) p_dir="${p%/*}"; [ -n "$p_dir" ] || p_dir="/" ;;\n'
    '  *) p_dir="." ;;\n'
    "esac\n"
    'p_dir="$(cd -P "$p_dir" && pwd)"\n'
    'parent_name="${p_dir##*/}"\n'
    'r="$(cd -P "$p_dir/.." && pwd)"\n'
    'if { [ "$parent_name" != "scripts" ] && [ "$parent_name" != "bin" ]; } \\\n'
    '   || [ ! -f "$r/loopx/__init__.py" ] \\\n'
    '   || [ ! -f "$r/loopx/hosts/agy/__init__.py" ] \\\n'
    '   || [ ! -f "$r/loopx/hosts/agy/host_runtime.py" ]; then\n'
    '  printf \'loopx release root not found from --cli-bin\\n\' >&2\n'
    "  exit 1\n"
    "fi\n"
    'py=""\n'
    'if [ -n "${LOOPX_PYTHON:-}" ]; then\n'
    '  case "$LOOPX_PYTHON" in\n'
    '    */*) py="$LOOPX_PYTHON" ;;\n'
    '    *) py="$(command -v "$LOOPX_PYTHON" 2>/dev/null || true)" ;;\n'
    "  esac\n"
    '  if [ -z "$py" ] || [ ! -f "$py" ] || [ ! -x "$py" ]; then\n'
    '    printf \'loopx configured LOOPX_PYTHON not found: %s\\n\' "$LOOPX_PYTHON" >&2\n'
    "    exit 1\n"
    "  fi\n"
    'elif [ -f "$r/.loopx-python" ]; then\n'
    '  if [ -f "$r/scripts/loopx-python.sh" ]; then\n'
    '    py="$(bash "$r/scripts/loopx-python.sh" 2>/dev/null || true)"\n'
    "  else\n"
    '    IFS= read -r py <"$r/.loopx-python" || true\n'
    "    cr=\"$(printf '\\r')\"\n"
    '    case "$py" in\n'
    '      *"$cr") py="${py%"$cr"}" ;;\n'
    "    esac\n"
    "  fi\n"
    "fi\n"
    'if [ -z "$py" ] || [ ! -f "$py" ] || [ ! -x "$py" ]; then\n'
    '  if [ -n "$fallback_py" ] && [ -f "$fallback_py" ] && [ -x "$fallback_py" ]; then\n'
    '    py="$fallback_py"\n'
    '  elif [ -f "$r/scripts/loopx-python.sh" ]; then\n'
    '    py="$(bash "$r/scripts/loopx-python.sh" 2>/dev/null || true)"\n'
    "  else\n"
    '    py=""\n'
    "  fi\n"
    "fi\n"
    'if [ -z "$py" ] || [ ! -f "$py" ] || [ ! -x "$py" ]; then\n'
    '  py="$(command -v python3 2>/dev/null || true)"\n'
    "fi\n"
    'if [ -z "$py" ] || [ ! -f "$py" ] || [ ! -x "$py" ]; then\n'
    '  printf \'loopx python interpreter not found for release root: %s\\n\' "$r" >&2\n'
    "  exit 1\n"
    "fi\n"
    'exec "$py" -E -P -c "$boot" "$@"\n'
)


def _resolved_executable_path(binary: str) -> str:
    # Keep symlinked CLI paths (e.g. ~/.local/bin/loopx) un-dereferenced so
    # hooks and plists track future `loopx upgrade` targets instead of pinning one release.
    return os.path.abspath(os.path.expanduser(binary))


def _is_explicit_cli_path(cli_bin: str) -> bool:
    return (
        os.sep in cli_bin
        or (os.altsep is not None and os.altsep in cli_bin)
        or cli_bin.startswith((".", "~"))
    )


def _find_dangling_path_symlink(cli_bin: str) -> str | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / cli_bin
        if os.path.islink(candidate) and not candidate.exists():
            return str(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return None
    return None


def _has_host_runtime_files(release_root: Path) -> bool:
    return (
        (release_root / "loopx" / "__init__.py").is_file()
        and (release_root / "loopx" / "hosts" / "agy" / "__init__.py").is_file()
        and (release_root / "loopx" / "hosts" / "agy" / "host_runtime.py").is_file()
    )


def _is_release_tree_launcher(path: Path) -> bool:
    return (
        path.is_file()
        and (os.name == "nt" or os.access(path, os.X_OK))
        and len(path.parents) > 1
        and path.parent.name in ("scripts", "bin")
        and (
            (path.parents[1] / "loopx").is_dir()
            or (path.parents[1] / "release.json").is_file()
        )
    )


def _is_valid_release_launcher(path: Path) -> bool:
    return _is_release_tree_launcher(path) and _has_host_runtime_files(path.parents[1])


def _validate_cli_launcher(
    resolved_cli_bin: str, *, require_release_layout: bool
) -> Path | None:
    if _is_explicit_cli_path(resolved_cli_bin):
        target = Path(resolved_cli_bin).expanduser().resolve()
        if not (target.is_file() and (os.name == "nt" or os.access(target, os.X_OK))):
            raise FileNotFoundError(
                f"LoopX CLI launcher not found or not executable: {resolved_cli_bin}"
            )
        if require_release_layout and not _is_valid_release_launcher(target):
            detail = (
                "host_runtime missing"
                if _is_release_tree_launcher(target)
                else "layout invalid"
            )
            raise FileNotFoundError(
                f"LoopX release snapshot launcher {detail}: {resolved_cli_bin}"
            )
        return target
    if require_release_layout:
        raise FileNotFoundError(
            f"LoopX release snapshot launcher not found for --cli-bin: {resolved_cli_bin}"
        )
    return None


def _validated_release_shim_candidate(
    raw_path: str, *, expected_root: Path | None
) -> tuple[str, bool] | None:
    """Validate a candidate release CLI path, preserving symlinks lexically."""
    stripped = raw_path.strip()
    if not stripped:
        return None
    normalized = _resolved_executable_path(stripped)
    shim_path = Path(normalized)
    try:
        target = shim_path.resolve()
    except (OSError, RuntimeError):
        return None
    if not _is_release_tree_launcher(target):
        return None
    if expected_root is not None:
        try:
            if not target.is_relative_to(expected_root):
                return None
        except (OSError, RuntimeError):
            return None
    return normalized, shim_path.is_symlink()


def _find_path_release_shim_candidate(
    cli_bin: str, *, expected_root: Path
) -> tuple[str, bool] | None:
    """Scan all PATH entries for a launcher resolving inside ``expected_root``."""
    first_regular: tuple[str, bool] | None = None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        which_raw = shutil.which(cli_bin, path=entry)
        if which_raw is None:
            continue
        candidate = _validated_release_shim_candidate(
            which_raw, expected_root=expected_root
        )
        if candidate is None:
            continue
        if candidate[1]:
            return candidate
        if first_regular is None:
            first_regular = candidate
    return first_regular


def _resolve_release_cli_shim(cli_bin: str) -> str | None:
    release_root_raw = os.environ.get("LOOPX_RELEASE_ROOT", "").strip()
    shim_raw = os.environ.get("LOOPX_CLI_SHIM", "").strip()
    if cli_bin != "loopx" or (not release_root_raw and not shim_raw):
        return None
    expected_root: Path | None = None
    if release_root_raw:
        try:
            expected_root = Path(release_root_raw).expanduser().resolve()
        except (OSError, RuntimeError):
            expected_root = None

    shim_candidate = (
        _validated_release_shim_candidate(shim_raw, expected_root=expected_root)
        if shim_raw
        else None
    )
    if shim_candidate is not None and shim_candidate[1]:
        return shim_candidate[0]

    launcher_name = "loopx.ps1" if os.name == "nt" else "loopx"
    if expected_root is not None:
        which_candidate = _find_path_release_shim_candidate(
            cli_bin, expected_root=expected_root
        )
        if which_candidate is not None and which_candidate[1]:
            return which_candidate[0]

        default_shim = Path.home() / ".local" / "bin" / launcher_name
        if default_shim.is_symlink():
            default_candidate = _validated_release_shim_candidate(
                str(default_shim), expected_root=expected_root
            )
            if default_candidate is not None:
                return default_candidate[0]

        if which_candidate is not None:
            return which_candidate[0]
        if shim_candidate is not None:
            return shim_candidate[0]

        root_launcher = expected_root / "scripts" / launcher_name
        if _is_release_tree_launcher(root_launcher):
            return str(root_launcher)
        return None

    if shim_candidate is not None:
        return shim_candidate[0]
    return None


def resolve_antigravity_runtime_bins(
    cli_bin: str, *, resolve_local_executable: bool = True
) -> tuple[str, str]:
    """Resolve ``(python_bin, cli_bin)`` while preserving symlinked CLI shims."""
    if not resolve_local_executable:
        return "python3", cli_bin
    python_bin = _resolved_executable_path(sys.executable)
    if _is_explicit_cli_path(cli_bin):
        return python_bin, _resolved_executable_path(cli_bin)
    found_cli = _resolve_release_cli_shim(cli_bin)
    if found_cli is None:
        dangling = _find_dangling_path_symlink(cli_bin)
        found_cli = dangling if dangling is not None else shutil.which(cli_bin)
    resolved_cli_bin = (
        _resolved_executable_path(found_cli) if found_cli is not None else cli_bin
    )
    return python_bin, resolved_cli_bin


def _python_supports_isolated_module(
    python_bin: str, *, expected_root: Path | None = None
) -> bool:
    """Check whether ``python_bin -E -P`` imports ``host_runtime`` (and matches ``expected_root`` if given)."""
    probe_code = (
        "import importlib.util, sys; from pathlib import Path; "
        'top = importlib.util.find_spec("loopx"); '
        f'mod = importlib.util.find_spec("{_HOST_RUNTIME_MODULE}") if top else None; '
        "ok = mod is not None; "
        "origin = getattr(mod, 'origin', None) if ok else None; "
        "ok = ok and ("
        "len(sys.argv) <= 1 or ("
        "isinstance(origin, str) and origin not in ('frozen', 'built-in') "
        "and Path(origin).is_file() "
        "and Path(origin).resolve().is_relative_to(Path(sys.argv[1]).resolve())"
        ")); "
        "raise SystemExit(0 if ok else 1)"
    )
    cmd = [python_bin, "-E", "-P", "-c", probe_code]
    if expected_root is not None:
        cmd.append(str(expected_root.resolve()))
    try:
        probe = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return expected_root is None
    return probe.returncode == 0


def _use_isolated_host_module(python_bin: str, resolved_cli_bin: str) -> bool:
    """Decide between ``-m`` and ``-c _RELEASE_ROOT_BOOTSTRAP`` without editable shadowing."""
    target = _validate_cli_launcher(resolved_cli_bin, require_release_layout=False)
    if target is not None and _is_release_tree_launcher(target):
        if not _has_host_runtime_files(target.parents[1]):
            raise FileNotFoundError(
                f"LoopX release snapshot host_runtime missing: {resolved_cli_bin}"
            )
        cli_path = Path(resolved_cli_bin).expanduser()
        if cli_path.is_symlink() or (target.parents[1] / "release.json").is_file():
            # Symlinked release shims or packaged snapshots always bootstrap from the
            # CLI launcher so older .pth installs cannot shadow upgrades.
            return False
        return _python_supports_isolated_module(
            python_bin, expected_root=target.parents[1]
        )
    if _python_supports_isolated_module(python_bin):
        return True
    _validate_cli_launcher(resolved_cli_bin, require_release_layout=True)
    return False


def build_antigravity_host_runtime_args(
    operation: str,
    *,
    cli_bin: str,
    goal_id: str | None,
    agent_id: str | None,
    conversation_id: str | None,
    runtime_root: str | None = None,
    registry: str | None = None,
    resolve_local_executable: bool = True,
    extra_args: Sequence[str] = (),
    resolved_bins: tuple[str, str] | None = None,
) -> list[str]:
    """Build argv to launch ``loopx.hosts.agy.host_runtime`` across venv and release snapshots."""
    python_bin, resolved_cli_bin = (
        resolved_bins
        if resolved_bins is not None
        else resolve_antigravity_runtime_bins(
            cli_bin, resolve_local_executable=resolve_local_executable
        )
    )
    uses_isolated_module = (
        not resolve_local_executable
        or _use_isolated_host_module(python_bin, resolved_cli_bin)
    )
    operation_tail = [
        operation,
        "--cli-bin",
        resolved_cli_bin,
        "--goal-id",
        goal_id or "<goal-id>",
        "--agent-id",
        agent_id or "<agent-id>",
        "--conversation-id",
        conversation_id or "<native-conversation-id>",
        *extra_args,
    ]
    if runtime_root:
        operation_tail.extend(["--runtime-root", runtime_root])
    if registry:
        operation_tail.extend(["--registry", registry])
    if not uses_isolated_module and os.name != "nt":
        return [
            "/bin/sh",
            "-c",
            _POSIX_RELEASE_SHELL_BOOTSTRAP,
            "loopx-agy-host-runtime",
            python_bin,
            _RELEASE_ROOT_BOOTSTRAP,
            *operation_tail,
        ]
    launcher_prefix = (
        ["-m", _HOST_RUNTIME_MODULE]
        if uses_isolated_module
        else ["-c", _RELEASE_ROOT_BOOTSTRAP]
    )
    return [
        python_bin,
        "-E",
        "-P",
        *launcher_prefix,
        *operation_tail,
    ]


def cron_for_interval_minutes(interval_minutes: int) -> str:
    """Convert a LoopX ``recommended_interval_minutes`` into a 5-field cron expression."""
    minutes = max(1, int(interval_minutes))
    if minutes >= 60:
        return "0 * * * *"
    return f"*/{minutes} * * * *"


def build_antigravity_hooks_template(
    *,
    cli_bin: str = "loopx",
    goal_id: str | None = None,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    conversation_id: str | None = None,
    registry: str | None = None,
    resolve_local_executable: bool = True,
) -> dict[str, Any]:
    """Native Stop hook; installation alone never binds an arbitrary chat."""
    conversation_id = conversation_id or os.environ.get(ANTIGRAVITY_CONVERSATION_ID_ENV)
    args = build_antigravity_host_runtime_args(
        "stop-hook",
        cli_bin=cli_bin,
        goal_id=goal_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        runtime_root=runtime_root,
        registry=registry,
        resolve_local_executable=resolve_local_executable,
    )
    return {
        "loopx": {
            "enabled": bool(
                conversation_id and agent_id and goal_id and goal_id != "<goal-id>"
            ),
            "Stop": [{"type": "command", "command": shlex.join(args), "timeout": 30}],
        }
    }


def build_antigravity_launchd_command(
    *,
    cli_bin: str = "loopx",
    goal_id: str | None = None,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    conversation_id: str | None = None,
    registry: str | None = None,
    resolve_local_executable: bool = True,
) -> list[str]:
    """Return argv to preview or install the reboot-surviving LaunchAgent watchdog."""
    conversation_id = conversation_id or os.environ.get(ANTIGRAVITY_CONVERSATION_ID_ENV)
    return build_antigravity_host_runtime_args(
        "install-launchd",
        cli_bin=cli_bin,
        goal_id=goal_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        runtime_root=runtime_root,
        registry=registry,
        resolve_local_executable=resolve_local_executable,
    )


def agy_activation_extras() -> dict[str, Any]:
    """Keyword overrides for ``agy_cli_activation``'s facade call.

    Keeps the agy host facts (goal command, tokens, wake tools, gate text,
    activation steps) in the agy package instead of growing
    ``host_loop_activation.py`` past its module metric budget.
    """
    return {
        "activation_method": "bind_native_goal_with_advisory_quota_entry",
        "extra_host_mutation": {
            "host_loop_primitive": "agy-/goal-and-schedule-tool",
            "loop_driver": "agy_native_goal_loop_with_schedule_wakes",
            "quota_gate_enforcement": "advisory_only",
            "native_goal_command": AGY_GOAL_COMMAND,
            "goal_complete_token": AGY_GOAL_COMPLETE_TOKEN,
            "goal_cancelled_token": AGY_GOAL_CANCELLED_TOKEN,
            "native_wake_tools": list(AGY_NATIVE_WAKE_TOOLS),
            "native_wake_facts": list(AGY_NATIVE_WAKE_FACTS),
            "skills_roots": [
                ANTIGRAVITY_SKILLS_ROOT_LABEL,
                ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL,
            ],
            "missing_host_tool_gate": (
                "agy's /goal loop and native wakes fire only while the CLI "
                "session stays alive (there is no background daemon), and "
                "agy exposes no hook to intercept `/goal`'s forced-"
                "continuation audit, so `quota should-run` is advisory "
                "guidance rather than a host-enforced gate. If the session "
                "cannot keep entering through `quota should-run`, show the "
                "exact heartbeat-prompt command for the user to run and do "
                "not claim unattended heartbeat support."
            ),
        },
        "extra_activation_steps": [
            "Bind the objective to agy's native goal loop by running "
            "`/goal <task_body>` — agy's forced continuation audits the work "
            "at the end of each turn and keeps the agent going until every "
            f"aspect is verified and `{AGY_GOAL_COMPLETE_TOKEN}` is emitted "
            f"(or `{AGY_GOAL_CANCELLED_TOKEN}` to abort).",
            "At the start of every turn, wake and `/goal` audit-continuation, "
            "run `quota should-run` first; when it says stop, emit "
            f"`{AGY_GOAL_CANCELLED_TOKEN}` (or `{AGY_GOAL_COMPLETE_TOKEN}` "
            "when the LoopX goal is genuinely complete) so the native goal "
            "loop terminates instead of free-running past the quota.",
            "When `quota should-run` allows continuation and more work or a "
            "deferred check remains (such as `waiting_external` or CI checks), "
            "arm the next wakeup in-session with agy's built-in `schedule` tool "
            "(`DurationSeconds` + wake `Prompt`, or `CronExpression` with "
            "`MaxIterations` for bounded recurring checks) instead of blind "
            "polling; cancel obsolete timers via `manage_task` before stopping.",
        ],
        "host_scheduler_note": (
            "use the built-in `schedule` tool (`DurationSeconds` or "
            "`CronExpression` with `MaxIterations`) for in-session wakes, and "
            f"emit `{AGY_GOAL_COMPLETE_TOKEN}` / `{AGY_GOAL_CANCELLED_TOKEN}` "
            "to end the native `/goal` loop (neither survives after the agy "
            "process exits)."
        ),
    }


def antigravity_app_activation_extras(
    *,
    goal_id: str = "<goal-id>",
    agent_id: str | None = None,
    runtime_root: str | None = None,
    registry: str | None = None,
    cli_bin: str = "loopx",
) -> dict[str, Any]:
    """Keyword overrides for ``antigravity_app_activation``'s facade call."""
    sidecar_template = build_antigravity_sidecar_template(
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_root=runtime_root,
        registry=registry,
        cli_bin=cli_bin,
    )
    hooks_template = build_antigravity_hooks_template(
        cli_bin=cli_bin,
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_root=runtime_root,
        registry=registry,
        resolve_local_executable=True,
    )
    launchd_command = build_antigravity_launchd_command(
        cli_bin=cli_bin,
        goal_id=goal_id,
        agent_id=agent_id,
        runtime_root=runtime_root,
        registry=registry,
        resolve_local_executable=True,
    )
    wake_prompt = sidecar_template["sidecar"]["loopx-heartbeat"]["bootstrap_prompt"]
    return {
        "activation_method": "bind_antigravity_schedule_loop_and_sidecar",
        "extra_host_mutation": {
            "host_loop_primitive": "antigravity-schedule-tool-and-sidecar",
            "loop_driver": "antigravity_app_schedule_timer_and_sidecar_loop",
            "quota_gate_enforcement": "advisory_agent_entry_with_bound_stop_recovery",
            "native_wake_tools": list(AGY_NATIVE_WAKE_TOOLS),
            "native_wake_facts": list(AGY_NATIVE_WAKE_FACTS),
            "skills_roots": [
                ANTIGRAVITY_SKILLS_ROOT_LABEL,
                ANTIGRAVITY_LEGACY_SKILLS_ROOT_LABEL,
            ],
            "host_mcp_server": "loopx",
            "host_mcp_config": ANTIGRAVITY_MCP_CONFIG_LABEL,
            "host_hooks_config": ANTIGRAVITY_HOOKS_CONFIG_LABEL,
            "host_sidecar_config": ANTIGRAVITY_SIDECAR_CONFIG_LABEL,
            "reboot_watchdog_command": shlex.join([*launchd_command, "--execute"]),
            "in_session_schedule_call": {
                "tool": "schedule",
                "arguments": {
                    "DurationSeconds": 180,
                    "Prompt": wake_prompt,
                    "TimerCondition": "any",
                },
                "min_duration_seconds": 180,
                "backoff_progression_minutes": [3, 6, 10],
                "when_to_use": (
                    "Before ending every fixed-session patrol turn (after settlement, "
                    "on `waiting_external`/CI/PR review waits, or when idle/no-op) "
                    "unless `scheduler_hint.action=stop_until_explicit_resume`, call "
                    "`schedule(DurationSeconds=max(180, recommended_interval_minutes * 60), "
                    "TimerCondition=\"any\", Prompt=...)` (`3 -> 6 -> 10` minute "
                    "unchanged backoff) and end the turn; never use shell sleep loops."
                ),
            },
            "fixed_session_patrol_contract": {
                "mode": "in_place_resident_session",
                "conversation_id_sources": [
                    "<user_information> Conversation ID",
                    ANTIGRAVITY_CONVERSATION_ID_ENV,
                ],
                "bind_and_hooks_sync_supported": True,
                "detached_sidecar_required": False,
                "isolated_worktree_root": ".worktrees/",
                "min_duration_seconds": 180,
                "backoff_progression_minutes": [3, 6, 10],
            },
            "sidecar_template": sidecar_template,
            "hooks_template": hooks_template,
            "missing_host_tool_gate": (
                "Antigravity App in-session `schedule(DurationSeconds>=180, "
                "TimerCondition=\"any\", Prompt=...)` wakes this exact resident "
                "conversation while the app session is active (with bound "
                f"`{ANTIGRAVITY_HOOKS_CONFIG_LABEL}` `Stop` recovery for network EOFs); "
                "only configure detached `sidecar.json` in "
                f"`{ANTIGRAVITY_SIDECAR_CONFIG_LABEL}` when explicitly asked for "
                "new-conversation background ticks."
            ),
        },
        "extra_activation_steps": [
            f"Install or refresh the Antigravity skill and optional MCP/hooks surface (`{cli_bin} slash-commands --install --surface antigravity-app --with-agy-mcp --with-agy-hooks` and `{cli_bin} workflow-skills --install --host-surface antigravity-app`).",
            "For a fixed resident session, read `<user_information>` `Conversation ID` (or `$ANTIGRAVITY_CONVERSATION_ID`), bind it via `bind-agent-thread --with-agy-hooks --execute` (plus `sync-global`) so `~/.gemini/config/hooks.json` carries the active `loopx` CLI and `--conversation-id`, run peer edits and `quota should-run` inside an isolated `.worktrees/` git worktree, and before ending every turn call `schedule(DurationSeconds=max(180, recommended_interval_minutes * 60), TimerCondition=\"any\", Prompt=...)` (`3 -> 6 -> 10`m backoff; no shell sleep or `new-conversation` sidecar).",
            f"Only when explicitly requesting detached new-conversation ticks instead of fixed in-session patrol, write `sidecar_template.sidecar_json` to `{ANTIGRAVITY_SIDECAR_CONFIG_LABEL}` preserving its existing id, model, permissions and host enable choice. Apply bound `hooks_template` at the top level of hooks.json; never wrap it in a `hooks` object.",
        ],
        "host_scheduler_note": (
            "for fixed in-session patrol, call `schedule(DurationSeconds>=180, "
            "TimerCondition=\"any\", Prompt=...)` (`3 -> 6 -> 10`m backoff) before "
            "ending every turn unless stopped; use `sidecar.json` only for explicit "
            "detached new-conversation heartbeats."
        ),
    }


def agy_home(value: str | None = None) -> Path:
    """Resolve the legacy Antigravity CLI user home directory (``~/.gemini/antigravity-cli``).

    Honours an explicit ``--agy-home`` override or ``AGY_CLI_HOME`` environment
    variable while defaulting to ``~/.gemini/antigravity-cli`` for flat
    ``skills/<name>.md`` compatibility.
    """
    raw = value or os.environ.get("AGY_CLI_HOME")
    if raw:
        return Path(raw).expanduser()
    return (Path.home() / DEFAULT_AGY_HOME).expanduser()


def antigravity_config_home(value: str | None = None) -> Path:
    """Resolve the Antigravity 2.0 / Antigravity App config home (``~/.gemini/config``).

    Honours an explicit ``--agy-home`` override or ``AGY_HOME`` / ``ANTIGRAVITY_HOME``
    environment variables, defaulting to ``~/.gemini/config`` where
    ``skills/<name>/SKILL.md``, ``mcp_config.json``, ``hooks.json``, and
    ``sidecar.json`` live.
    """
    raw = (
        value
        or os.environ.get("AGY_HOME")
        or os.environ.get("ANTIGRAVITY_HOME")
        or str(Path.home() / DEFAULT_ANTIGRAVITY_CONFIG_HOME)
    )
    return Path(raw).expanduser()


def antigravity_skill_roots(value: str | None = None) -> tuple[Path, ...]:
    """Return Antigravity skill roots in discovery order (2.0 config first, then legacy CLI)."""
    if value:
        base = Path(value).expanduser()
        return (base / "skills",)
    return (
        antigravity_config_home() / "skills",
        agy_home() / "skills",
    )
