"""Antigravity IO for the existing LoopX heartbeat and native Stop hook.

Quota and scheduler policy remain in Core. This provider translates native
events and reserves continuation decisions before replying to the host.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import unquote, urlparse

from ..file_lock import exclusive_file_lock


def retryable_native_error(error: Any) -> bool:
    """Conservative mapping of the native provider's unstructured error field.

    Only known Google model transport errors qualify. User/model prose, quota,
    auth, cancellation and permission errors do not establish retry authority.
    """
    if isinstance(error, dict):
        error = error.get("shortError")
    if not isinstance(error, str):
        return False
    return error.startswith("agent executor error: generating and executing:") and (
        error.endswith("stream reading error: unexpected EOF")
        or (
            'request failed: Post "https://' in error
            and ':streamGenerateContent?alt=sse": EOF' in error
        )
    )


def _core_args(args: argparse.Namespace, command: list[str]) -> list[str]:
    result = [args.cli_bin, "--format", "json"]
    for flag, value in (
        ("--registry", args.registry),
        ("--runtime-root", args.runtime_root),
    ):
        if value:
            result.extend([flag, value])
    return (
        result
        + command
        + [
            "--goal-id",
            args.goal_id,
            "--agent-id",
            args.agent_id,
            "--runtime-profile",
            "antigravity_app",
        ]
    )


def _admitted(args: argparse.Namespace) -> bool:
    read = subprocess.run(
        _core_args(args, ["quota", "should-run"]),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    return json.loads(read.stdout).get("should_run") is True


def bootstrap_prompt(args: argparse.Namespace) -> str:
    import shlex

    return (
        "LoopX managed heartbeat bootstrap v2\n"
        "Run this command and read its complete fresh JSON result:\n"
        + shlex.join(_core_args(args, ["heartbeat-prompt", "--thin"]))
        + "\nFollow task_body only when ok=true, including current scope, stop, "
        "settlement and scheduler backoff. Do not replay the failed user request, "
        "create another Goal/Agent, or reuse an old Turn receipt. Remain quiet "
        "when the fresh user_channel requests no notification."
    )


@contextmanager
def _state(args: argparse.Namespace):
    # Native conversation ids never become path components.
    key = hashlib.sha256(args.conversation_id.encode()).hexdigest()
    path = Path(args.state_dir).expanduser() / (key + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path, timeout_seconds=2):
        data = json.loads(path.read_text()) if path.exists() else {}
        yield data
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            stream.write(json.dumps(data))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)


def _reserve_retry(state: dict[str, Any], input_key: str) -> bool:
    if state.get("input_key") != input_key:
        state.update(input_key=input_key, retries=0)
    if state.get("retries", 0) >= 2 or time.time() - state.get("retry_at", 0) < 60:
        return False
    state.update(retries=state.get("retries", 0) + 1, retry_at=time.time())
    return True


def _native_input(args: argparse.Namespace, text: str) -> tuple[str, str, int] | None:
    """Freeze the native input identity, including repeated identical messages."""
    from ..capabilities.native_chat.antigravity_adapter import (
        _local_url,
        _observed_turn_steps,
        _rpc_post,
        discover_antigravity_language_server,
    )

    address = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if not address and not token:
        endpoint = discover_antigravity_language_server()
        if endpoint is None:
            return None
        address, token = endpoint["url"], endpoint["csrf_token"]
    elif not address or not token:
        return None
    url = _local_url(address if "://" in address else "http://" + address)
    payload = {"cascadeId": args.conversation_id}
    before = _rpc_post(url, token, "GetCascadeTrajectory", payload, timeout=2)
    total = before.get("numTotalSteps")
    status = before.get("status")
    if (
        type(total) is not int
        or total < 1
        or status not in {"CASCADE_RUN_STATUS_IDLE", "CASCADE_RUN_STATUS_RUNNING"}
        or (before.get("trajectory") or {}).get("cascadeId") != args.conversation_id
    ):
        return None
    raw = _rpc_post(
        url,
        token,
        "GetCascadeTrajectorySteps",
        {**payload, "startIndex": 0, "endIndex": total},
        timeout=2,
    ).get("steps")
    indexed, covered = _observed_turn_steps(raw, 0, total)
    inputs = [
        (index, step)
        for index, step in indexed
        if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"
    ]
    after = _rpc_post(url, token, "GetCascadeTrajectory", payload, timeout=2)
    if (
        not inputs
        or covered != total
        or after.get("status") != status
        or after.get("numTotalSteps") != total
        or (after.get("trajectory") or {}).get("cascadeId") != args.conversation_id
    ):
        return None
    index, user = inputs[-1]
    created = (user.get("metadata") or {}).get("createdAt")
    if (
        not isinstance(created, str)
        or not created
        or (user.get("userInput") or {}).get("userResponse") != text
    ):
        return None
    anchor = json.dumps([args.conversation_id, index, created, text])
    return hashlib.sha256(anchor.encode()).hexdigest(), status, total


def stop_hook(args: argparse.Namespace, event: dict[str, Any]) -> dict[str, Any]:
    if (
        event.get("conversationId") != args.conversation_id
        or event.get("terminationReason") != "ERROR"
        or event.get("fullyIdle") is not True
        or not retryable_native_error(event.get("error"))
    ):
        return {}
    text = event.get("lastUserInput")
    if not isinstance(text, str) or not text:
        return {}
    native = _native_input(args, text)
    if native is None or not _admitted(args) or _native_input(args, text) != native:
        return {}
    with _state(args) as state:
        if not _reserve_retry(state, native[0]):
            return {}
    # Reservation is durable before returning the native continuation decision.
    return {"decision": "continue", "reason": bootstrap_prompt(args)}


def _ls_endpoint() -> tuple[str, str] | None:
    from ..capabilities.native_chat.antigravity_adapter import (
        _local_url,
        discover_antigravity_language_server,
    )

    address = os.environ.get("ANTIGRAVITY_LS_ADDRESS") or os.environ.get(
        "ANTIGRAVITY_LS_URL"
    )
    token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if not address and not token:
        endpoint = discover_antigravity_language_server()
        if endpoint is None:
            return None
        address, token = endpoint["url"], endpoint["csrf_token"]
    elif not address or not token:
        return None
    url = _local_url(address if "://" in address else "http://" + address)
    return url, token


def _uri_to_resolved_path(uri: Any) -> Path | None:
    if not isinstance(uri, str) or not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
    elif not parsed.scheme:
        raw_path = uri
    else:
        return None
    try:
        return Path(raw_path).expanduser().resolve()
    except OSError:
        return None


def _git_common_dir(path: Path) -> Path | None:
    git_bin = shutil.which("git") or "/usr/bin/git"
    try:
        proc = subprocess.run(
            [git_bin, "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = path / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _workspace_matches(summary: dict[str, Any], target_workspace: Path) -> bool:
    workspaces = summary.get("workspaces")
    if not isinstance(workspaces, list) or not workspaces:
        return False
    target_common_git = _git_common_dir(target_workspace)
    for ws in workspaces:
        if not isinstance(ws, dict):
            continue
        for key in ("workspaceFolderAbsoluteUri", "gitRootAbsoluteUri"):
            ws_path = _uri_to_resolved_path(ws.get(key))
            if ws_path is None:
                continue
            if (
                ws_path == target_workspace
                or target_workspace.is_relative_to(ws_path)
                or ws_path.is_relative_to(target_workspace)
            ):
                return True
            if target_common_git is not None:
                ws_common_git = _git_common_dir(ws_path)
                if ws_common_git is not None and ws_common_git == target_common_git:
                    return True
    return False


def discover_active_conversation_id(
    workspace: str | Path | None = None,
) -> str | None:
    """Discover the currently active Antigravity conversation ID from Language Server."""
    env_cid = (os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "").strip()
    if env_cid:
        return env_cid
    endpoint = _ls_endpoint()
    if endpoint is None:
        return None
    from ..capabilities.native_chat.antigravity_adapter import _rpc_post

    url, token = endpoint
    try:
        summaries_resp = _rpc_post(
            url, token, "GetAllCascadeTrajectories", {}, timeout=2
        )
    except Exception:
        return None
    summaries = (summaries_resp or {}).get("trajectorySummaries")
    if not isinstance(summaries, dict) or not summaries:
        return None

    target_workspace: Path | None = None
    if workspace is not None:
        try:
            target_workspace = Path(workspace).expanduser().resolve()
        except OSError:
            target_workspace = None
    if target_workspace is None:
        try:
            target_workspace = Path.cwd().resolve()
        except OSError:
            target_workspace = None

    running_workspace_matches: list[tuple[str, int, str]] = []
    running_root_candidates: list[tuple[str, int, str]] = []
    idle_workspace_matches: list[tuple[str, int, str]] = []

    for cid, summary in summaries.items():
        if not isinstance(cid, str) or not cid or not isinstance(summary, dict):
            continue
        meta = summary.get("trajectoryMetadata")
        if isinstance(meta, dict) and meta.get("parentConversationId"):
            # Skip subagent trajectories when binding root session
            continue
        status = str(summary.get("status") or "")
        modified = str(summary.get("lastModifiedTime") or summary.get("createdTime") or "")
        steps = int(summary.get("stepCount") or 0)
        ws_match = (
            _workspace_matches(summary, target_workspace)
            if target_workspace is not None
            else False
        )
        if status == "CASCADE_RUN_STATUS_RUNNING":
            if ws_match:
                running_workspace_matches.append((modified, steps, cid))
            running_root_candidates.append((modified, steps, cid))
        elif status == "CASCADE_RUN_STATUS_IDLE" and ws_match:
            if (
                summary.get("latestExecutorTerminationReason")
                != "EXECUTOR_TERMINATION_REASON_USER_CANCELED"
            ):
                idle_workspace_matches.append((modified, steps, cid))

    for bucket in (
        running_workspace_matches,
        running_root_candidates if len(running_root_candidates) == 1 else [],
        idle_workspace_matches if len(idle_workspace_matches) == 1 else [],
    ):
        if bucket:
            bucket.sort(reverse=True)
            return bucket[0][2]
    return None


def _resolve_bound_session(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None]:
    goal_id = (getattr(args, "goal_id", None) or "").strip() or None
    agent_id = (getattr(args, "agent_id", None) or "").strip() or None
    conversation_id = (getattr(args, "conversation_id", None) or "").strip() or None
    if goal_id and agent_id and conversation_id:
        return goal_id, agent_id, conversation_id

    try:
        from ..cli_commands.support_control_registry import explicit_global_registry
        from ..history import load_registry
        from ..registry import registry_goals
        from ..thread_agent_binding import normalize_thread_agent_bindings

        registry_path = (
            Path(args.registry).expanduser()
            if getattr(args, "registry", None)
            else explicit_global_registry(getattr(args, "runtime_root", None))
        )
        if not registry_path.exists():
            return goal_id, agent_id, conversation_id
        payload = load_registry(registry_path)
        candidates: list[tuple[str, str, str, str]] = []
        for goal in registry_goals(payload):
            gid = str(goal.get("id") or "").strip()
            if not gid or (goal_id and gid != goal_id):
                continue
            if str(goal.get("status") or "active") in {
                "completed",
                "archived",
                "cancelled",
            }:
                continue
            coordination = (
                goal.get("coordination")
                if isinstance(goal.get("coordination"), dict)
                else {}
            )
            bindings = normalize_thread_agent_bindings(
                coordination.get("thread_agent_bindings")
            )
            for item in bindings:
                if item.get("host_surface") not in {
                    "agy",
                    "antigravity-app",
                    "antigravity_app",
                }:
                    continue
                aid = str(item.get("agent_id") or "").strip()
                tid = str(item.get("thread_id") or "").strip()
                if not aid or not tid:
                    continue
                if agent_id and aid != agent_id:
                    continue
                if conversation_id and tid != conversation_id:
                    continue
                bound_at = str(item.get("bound_at") or "")
                candidates.append((bound_at, gid, aid, tid))
        if candidates:
            candidates.sort(reverse=True)
            _, resolved_gid, resolved_aid, resolved_tid = candidates[0]
            return (
                goal_id or resolved_gid,
                agent_id or resolved_aid,
                conversation_id or resolved_tid,
            )
    except Exception:
        pass
    return goal_id, agent_id, conversation_id


def _inspect_conversation_liveness(conversation_id: str) -> dict[str, Any]:
    """Query Antigravity Language Server to determine whether the bound session is alive, running, or idle."""
    endpoint = _ls_endpoint()
    if endpoint is None:
        return {
            "alive": False,
            "running": False,
            "idle": False,
            "user_canceled": False,
            "reason": "language_server_not_running",
        }
    from ..capabilities.native_chat.antigravity_adapter import _rpc_post

    url, token = endpoint
    try:
        traj = _rpc_post(
            url,
            token,
            "GetCascadeTrajectory",
            {"cascadeId": conversation_id},
            timeout=2,
        )
    except Exception:
        return {
            "alive": False,
            "running": False,
            "idle": False,
            "user_canceled": False,
            "reason": "trajectory_rpc_error",
        }
    if (
        not isinstance(traj, dict)
        or (traj.get("trajectory") or {}).get("cascadeId") != conversation_id
    ):
        return {
            "alive": False,
            "running": False,
            "idle": False,
            "user_canceled": False,
            "reason": "session_not_found",
        }
    status = str(traj.get("status") or "")
    termination_reason = ""
    try:
        all_resp = _rpc_post(
            url, token, "GetAllCascadeTrajectories", {}, timeout=2
        )
        summary = ((all_resp or {}).get("trajectorySummaries") or {}).get(
            conversation_id
        )
        if isinstance(summary, dict):
            termination_reason = str(
                summary.get("latestExecutorTerminationReason") or ""
            )
            if not status:
                status = str(summary.get("status") or "")
    except Exception:
        pass

    user_canceled = (
        termination_reason == "EXECUTOR_TERMINATION_REASON_USER_CANCELED"
    )
    running = status == "CASCADE_RUN_STATUS_RUNNING"
    idle = status == "CASCADE_RUN_STATUS_IDLE"
    return {
        "alive": running or idle,
        "running": running,
        "idle": idle,
        "user_canceled": user_canceled,
        "status": status,
        "termination_reason": termination_reason,
        "num_total_steps": traj.get("numTotalSteps"),
        "reason": (
            "session_running"
            if running
            else (
                "session_user_canceled"
                if user_canceled
                else ("session_idle" if idle else "session_inactive")
            )
        ),
    }


def _sync_sidecar_cron(sidecar_path: Path, cron_expression: str) -> bool:
    if not sidecar_path.exists():
        return False
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    args_list = data.get("args")
    if not isinstance(args_list, list) or not args_list:
        return False
    desired = f"CRON_TZ=UTC {cron_expression}"
    if args_list[0] == desired:
        return False
    args_list[0] = desired
    tmp = sidecar_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(sidecar_path)
    return True


def _run_core_json(
    args: argparse.Namespace, command: list[str]
) -> dict[str, Any] | None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    proc = subprocess.run(
        _core_args(args, command),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=env,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return None
    try:
        parsed = json.loads(proc.stdout)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _run_cli_raw(args: argparse.Namespace, cli_args: list[str]) -> bool:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    cmd = [args.cli_bin, "--format", "json"]
    for flag, value in (
        ("--registry", args.registry),
        ("--runtime-root", args.runtime_root),
    ):
        if value:
            cmd.extend([flag, value])
    cmd.extend(cli_args)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=env,
    )
    return proc.returncode == 0


def sidecar_tick(args: argparse.Namespace) -> dict[str, Any]:
    """Execute one stateful-backoff-aware Antigravity sidecar tick.

    1. Resolves the bound (goal_id, agent_id, conversation_id).
    2. Verifies the bound Antigravity session exists, is not user-canceled, and is IDLE
       (if the session is already RUNNING or not alive, skips automation).
    3. Queries `loopx quota should-run --runtime-profile antigravity_app`, syncs the
       sidecar cron expression with LoopX `stateful_backoff`, and acknowledges the
       scheduler state (`quota scheduler-ack-current --antigravity_app`).
    4. Wakes the existing bound conversation in-place via `agentapi send-message`
       (never spawning orphan conversations via `agentapi new-conversation`).
    """
    from . import (
        antigravity_config_home,
        build_antigravity_backoff_schedule_action,
    )

    goal_id, agent_id, conversation_id = _resolve_bound_session(args)
    if not goal_id or not agent_id or not conversation_id:
        return {
            "ok": True,
            "decision": "noop",
            "reason": "unbound_session",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
        }

    bound_args = argparse.Namespace(**vars(args))
    bound_args.goal_id = goal_id
    bound_args.agent_id = agent_id
    bound_args.conversation_id = conversation_id

    liveness = _inspect_conversation_liveness(conversation_id)
    if not liveness.get("alive"):
        return {
            "ok": True,
            "decision": "noop",
            "reason": liveness.get("reason", "session_not_alive"),
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "liveness": liveness,
        }
    if liveness.get("running"):
        return {
            "ok": True,
            "decision": "noop",
            "reason": "session_already_running",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "liveness": liveness,
        }
    if liveness.get("user_canceled"):
        return {
            "ok": True,
            "decision": "noop",
            "reason": "session_user_canceled",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "liveness": liveness,
        }
    if not liveness.get("idle"):
        return {
            "ok": True,
            "decision": "noop",
            "reason": "session_not_idle",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "liveness": liveness,
        }

    quota_payload = _run_core_json(bound_args, ["quota", "should-run"])
    if quota_payload is None:
        return {
            "ok": False,
            "decision": "noop",
            "reason": "quota_check_failed",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
        }

    backoff_action = build_antigravity_backoff_schedule_action(quota_payload)
    sidecar_path = (
        Path(args.sidecar_config).expanduser()
        if getattr(args, "sidecar_config", None)
        else (
            antigravity_config_home(getattr(args, "agy_home", None))
            / "sidecars"
            / "loopx-antigravity-heartbeat"
            / "sidecar.json"
        )
    )
    target_cron = str(backoff_action.get("cron_expression") or "*/3 * * * *")
    cron_updated = False
    if backoff_action.get("action") != "pause_or_delete_current_heartbeat":
        cron_updated = _sync_sidecar_cron(sidecar_path, target_cron)

    ack_cli_args = backoff_action.get("ack_cli_args")
    ack_executed = False
    if (
        isinstance(ack_cli_args, list)
        and ack_cli_args
        and (backoff_action.get("apply_needed") or cron_updated)
    ):
        ack_executed = _run_cli_raw(
            bound_args, [str(item) for item in ack_cli_args]
        )

    should_run = quota_payload.get("should_run") is True
    if (
        not should_run
        or backoff_action.get("action") == "pause_or_delete_current_heartbeat"
    ):
        return {
            "ok": True,
            "decision": "noop",
            "reason": "quota_should_not_run",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "effective_action": quota_payload.get("effective_action"),
            "recommended_interval_minutes": backoff_action.get(
                "recommended_interval_minutes"
            ),
            "cron_expression": target_cron,
            "cron_updated": cron_updated,
            "ack_executed": ack_executed,
        }

    # Re-verify session is still idle right before sending the wake message.
    recheck = _inspect_conversation_liveness(conversation_id)
    if not recheck.get("idle") or recheck.get("running") or recheck.get("user_canceled"):
        return {
            "ok": True,
            "decision": "noop",
            "reason": "session_state_changed_before_wake",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "liveness": recheck,
            "cron_updated": cron_updated,
            "ack_executed": ack_executed,
        }

    agentapi_bin = (
        getattr(args, "agentapi", None)
        or shutil.which("agentapi")
        or str(Path.home() / ".antigravity" / "antigravity" / "bin" / "agentapi")
    )
    prompt = bootstrap_prompt(bound_args)
    wake = subprocess.run(
        [
            agentapi_bin,
            "send-message",
            "--title=LoopX Heartbeat",
            conversation_id,
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if wake.returncode != 0:
        return {
            "ok": False,
            "decision": "wake_failed",
            "reason": "agentapi_send_message_failed",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "returncode": wake.returncode,
            "cron_updated": cron_updated,
            "ack_executed": ack_executed,
        }
    return {
        "ok": True,
        "decision": "woke_bound_session",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "recommended_interval_minutes": backoff_action.get(
            "recommended_interval_minutes"
        ),
        "cron_expression": target_cron,
        "cron_updated": cron_updated,
        "ack_executed": ack_executed,
    }


def bind_antigravity_session_configs(
    *,
    goal_id: str,
    agent_id: str,
    conversation_id: str,
    cli_bin: str = "loopx",
    registry: str | None = None,
    runtime_root: str | None = None,
    agy_home: str | None = None,
    interval_minutes: int = 3,
    write_sidecar: bool = True,
    write_hooks: bool = True,
) -> dict[str, Any]:
    """Atomically bind local Antigravity `hooks.json` and `sidecar.json` to `(goal_id, agent_id, conversation_id)`."""
    from . import antigravity_config_home, build_antigravity_sidecar_template
    from ..slash_command_install import _merge_managed_hooks_json

    config_root = antigravity_config_home(agy_home)
    hooks_path = config_root / "hooks.json"
    sidecar_path = (
        config_root
        / "sidecars"
        / "loopx-antigravity-heartbeat"
        / "sidecar.json"
    )
    hooks_status = "skipped"
    if write_hooks:
        hooks_status = _merge_managed_hooks_json(
            config_path=hooks_path,
            cli_bin=cli_bin,
            uninstall=False,
            execute=True,
            goal_id=goal_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            registry=registry,
            runtime_root=runtime_root,
        )
    sidecar_status = "skipped"
    if write_sidecar:
        existing_cron: str | None = None
        if sidecar_path.exists():
            try:
                existing_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
                existing_args = existing_data.get("args") if isinstance(existing_data, dict) else None
                if isinstance(existing_args, list) and existing_args and isinstance(existing_args[0], str):
                    existing_cron = existing_args[0]
            except Exception:
                existing_cron = None
        sidecar_template = build_antigravity_sidecar_template(
            goal_id=goal_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            cli_bin=cli_bin,
            interval_seconds=max(60, int(interval_minutes) * 60),
            registry=registry,
            runtime_root=runtime_root,
            resolve_local_executable=True,
        )
        sidecar_json = dict(sidecar_template["sidecar_json"])
        if existing_cron and existing_cron.startswith("CRON_TZ=UTC ") and interval_minutes == 3:
            sidecar_args = list(sidecar_json.get("args") or [])
            if sidecar_args:
                sidecar_args[0] = existing_cron
                sidecar_json["args"] = sidecar_args
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = sidecar_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sidecar_json, indent=2) + "\n", encoding="utf-8")
        tmp.replace(sidecar_path)
        sidecar_status = "written"
    return {
        "ok": True,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "hooks_path": str(hooks_path),
        "hooks_status": hooks_status,
        "sidecar_path": str(sidecar_path),
        "sidecar_status": sidecar_status,
    }


def bind_session(args: argparse.Namespace) -> dict[str, Any]:
    conversation_id = (getattr(args, "conversation_id", None) or "").strip() or None
    if not conversation_id:
        conversation_id = discover_active_conversation_id(
            workspace=getattr(args, "workspace", None)
        )
    if not conversation_id:
        return {
            "ok": False,
            "error_kind": "conversation_id_unresolved",
            "error": "Could not auto-discover active Antigravity conversation ID; pass --conversation-id explicitly.",
        }
    if not getattr(args, "goal_id", None) or not getattr(args, "agent_id", None):
        return {
            "ok": False,
            "error_kind": "missing_goal_or_agent_id",
            "error": "--goal-id and --agent-id are required for bind-session.",
        }
    return bind_antigravity_session_configs(
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        conversation_id=conversation_id,
        cli_bin=args.cli_bin,
        registry=args.registry,
        runtime_root=args.runtime_root,
        agy_home=getattr(args, "agy_home", None),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("stop-hook", "sidecar-tick", "bind-session"),
    )
    parser.add_argument("--cli-bin", default="loopx")
    parser.add_argument("--registry")
    parser.add_argument("--runtime-root")
    parser.add_argument("--goal-id")
    parser.add_argument("--agent-id")
    parser.add_argument("--conversation-id")
    parser.add_argument("--workspace")
    parser.add_argument("--agy-home")
    parser.add_argument("--sidecar-config")
    parser.add_argument("--agentapi")
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".loopx/host-state/antigravity")
    )
    args = parser.parse_args()
    try:
        if args.operation == "stop-hook":
            if not args.goal_id or not args.agent_id or not args.conversation_id:
                result = {}
            else:
                result = stop_hook(args, json.load(sys.stdin))
        elif args.operation == "sidecar-tick":
            result = sidecar_tick(args)
        else:
            result = bind_session(args)
    except Exception as exc:
        # Hook stdout must remain valid native JSON. Provider detail may contain
        # credentials or private context; expose only the failure class.
        print(
            f"LoopX Antigravity {args.operation} unavailable ({type(exc).__name__})",
            file=sys.stderr,
        )
        result = {}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
