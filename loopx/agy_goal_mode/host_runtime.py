"""Antigravity IO for the existing LoopX heartbeat and native Stop hook.

Quota and scheduler policy remain in Core. This provider translates native
events and reserves continuation decisions before replying to the host.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from ..command_invocation import loopx_cli_argv
from ..entrypoint import pin_utf8_stdio
from ..file_lock import exclusive_file_lock
from .launchd_watchdog import (
    DEFAULT_LAUNCHD_INTERVAL_SECONDS,
    DEFAULT_LONG_IDLE_SECONDS,
    MIN_STALL_COOLDOWN_SECONDS,
    build_antigravity_launchd_plist as build_antigravity_launchd_plist,
    install_launchd as install_launchd,
)

DEFAULT_STALL_COOLDOWN_SECONDS = MIN_STALL_COOLDOWN_SECONDS
LS_RESTART_SKEW_SECONDS = 5.0


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
    result = [*loopx_cli_argv(args.cli_bin), "--format", "json"]
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
    # The retry gate observes Core. The resumed heartbeat reads its own work.
    read = subprocess.run(
        _core_args(args, ["quota", "should-run", "--scheduler-observation"]),
        capture_output=True,
        text=True,
        encoding="utf-8",
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
        "when the fresh user_channel requests no notification. Before ending the "
        "recovered turn (unless stopped), re-arm in-session "
        "schedule(DurationSeconds>=180, TimerCondition='any', Prompt=...) with "
        "the current backoff cadence instead of starting a new conversation."
    )


def _state_path(args: argparse.Namespace) -> Path:
    # Native conversation ids never become path components.
    key = hashlib.sha256(str(args.conversation_id).encode("utf-8")).hexdigest()
    return Path(str(args.state_dir)).expanduser().resolve() / f"{key}.json"


def _read_state(args: argparse.Namespace) -> dict[str, Any]:
    path = _state_path(args)
    if not path.exists():
        return {}
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


@contextmanager
def _state(args: argparse.Namespace) -> Iterator[dict[str, Any]]:
    path = _state_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path, timeout_seconds=2):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    from ..chat_agent import CodexChatAgentError
    from ..capabilities.native_chat.antigravity_adapter import (
        _local_url,
        _read_trajectory_steps,
        _rpc_post,
        discover_antigravity_language_server,
    )

    address = os.environ.get("ANTIGRAVITY_LS_ADDRESS") or os.environ.get("ANTIGRAVITY_LS_URL")
    token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if not address and not token:
        endpoint = discover_antigravity_language_server()
        if endpoint is None:
            return None
        address, token = endpoint["url"], endpoint["csrf_token"]
    elif not address or not token:
        return None
    try:
        url = _local_url(address if "://" in address else "http://" + address)
        payload = {"cascadeId": args.conversation_id}
        before = _rpc_post(url, token, "GetCascadeTrajectory", payload, timeout=2)
        total = before.get("numTotalSteps")
        status = before.get("status")
        trajectory = before.get("trajectory")
        if (
            type(total) is not int
            or total < 1
            or not isinstance(status, str)
            or status not in {"CASCADE_RUN_STATUS_IDLE", "CASCADE_RUN_STATUS_RUNNING"}
            or not isinstance(trajectory, dict)
            or trajectory.get("cascadeId") != args.conversation_id
        ):
            return None

        def fetch_steps(offset: int, end: int) -> Any:
            return _rpc_post(
                url,
                token,
                "GetCascadeTrajectorySteps",
                {**payload, "stepOffset": offset, "startIndex": offset, "endIndex": end},
                timeout=2,
            ).get("steps")

        start = max(0, total - 128)
        loaded = _read_trajectory_steps(fetch_steps, start=start, total=total)
        if loaded is None:
            return None
        indexed, covered = loaded
        inputs = [
            (index, step)
            for index, step in indexed
            if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"
        ]
        if not inputs and start:
            loaded_full = _read_trajectory_steps(fetch_steps, start=0, total=total)
            if loaded_full is None:
                return None
            indexed, covered = loaded_full
            inputs = [
                (index, step)
                for index, step in indexed
                if step.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"
            ]
        after = _rpc_post(url, token, "GetCascadeTrajectory", payload, timeout=2)
    except (ValueError, CodexChatAgentError, OSError):
        return None
    trajectory = after.get("trajectory")
    if (
        not inputs
        or covered != total
        or after.get("status") != status
        or after.get("numTotalSteps") != total
        or not isinstance(trajectory, dict)
        or trajectory.get("cascadeId") != args.conversation_id
    ):
        return None
    index, user = inputs[-1]
    metadata = user.get("metadata")
    created = metadata.get("createdAt") if isinstance(metadata, dict) else None
    user_input = user.get("userInput")
    if (
        not isinstance(created, str)
        or not created
        or not isinstance(user_input, dict)
        or user_input.get("userResponse") != text
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


def _parse_iso_epoch(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    normalized = raw.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _process_start_epoch(pid: int) -> float | None:
    if pid <= 0:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=2,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    normalized = " ".join(completed.stdout.strip().split())
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError:
        return None


def _discover_endpoint() -> tuple[str, str, int | None] | None:
    from ..capabilities.native_chat.antigravity_adapter import (
        discover_antigravity_language_server,
    )

    endpoint = discover_antigravity_language_server()
    if endpoint is None:
        return None
    raw_pid = endpoint.get("pid")
    pid = raw_pid if type(raw_pid) is int and raw_pid > 0 else None
    return endpoint["url"], endpoint["csrf_token"], pid


def _probe_cascade_summary(
    url: str, csrf_token: str, conversation_id: str
) -> tuple[str, float | None] | None:
    from ..capabilities.native_chat.antigravity_adapter import _local_url, _rpc_post
    from ..chat_agent import CodexChatAgentError

    try:
        resp = _rpc_post(
            _local_url(url), csrf_token, "GetAllCascadeTrajectories", {}, timeout=3.0
        )
    except (ValueError, CodexChatAgentError, OSError):
        return None
    summaries = resp.get("trajectorySummaries")
    if not isinstance(summaries, dict):
        return None
    entry = summaries.get(conversation_id)
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    if not isinstance(status, str) or not status:
        return None
    return status, _parse_iso_epoch(entry.get("lastModifiedTime"))


def _send_wake_message(
    url: str, csrf_token: str, conversation_id: str, prompt: str
) -> None:
    from ..capabilities.native_chat.antigravity_adapter import _local_url, _rpc_post

    _rpc_post(
        _local_url(url),
        csrf_token,
        "SendUserCascadeMessage",
        {"cascadeId": conversation_id, "items": [{"text": prompt}]},
        timeout=15.0,
    )


def _wake_reason(
    *,
    state: Mapping[str, object],
    now: float,
    last_modified_epoch: float,
    ls_pid: int | None,
    ls_start_epoch: float | None,
    long_idle_seconds: int,
    cooldown_seconds: int,
) -> str | None:
    # A newly started Language Server older turns never saw proves an OS reboot
    # or LS restart; active swarm_wait / schedule sleeps keep the same LS PID.
    if (
        ls_pid is not None
        and ls_start_epoch is not None
        and ls_start_epoch > last_modified_epoch + LS_RESTART_SKEW_SECONDS
    ):
        prior_pid = state.get("last_woken_ls_pid")
        prior_start = state.get("last_woken_ls_start_epoch")
        same_ls_instance = prior_pid == ls_pid and (
            not isinstance(prior_start, (int, float))
            or isinstance(prior_start, bool)
            or abs(float(prior_start) - ls_start_epoch) <= 2.0
        )
        if not same_ls_instance:
            return "ls_restarted_since_last_turn"

    effective_long_idle = max(60, int(long_idle_seconds))
    effective_cooldown = max(MIN_STALL_COOLDOWN_SECONDS, int(cooldown_seconds))
    prior_wake = state.get("last_woken_at")
    since_wake = (
        now - float(prior_wake)
        if isinstance(prior_wake, (int, float)) and not isinstance(prior_wake, bool)
        else float("inf")
    )
    if (now - last_modified_epoch) >= effective_long_idle and since_wake >= effective_cooldown:
        return "long_idle_fallback"
    return None


def heartbeat_tick(args: argparse.Namespace) -> dict[str, object]:
    """Wake an idle Antigravity conversation only after LS restart or long stall."""
    from ..chat_agent import CodexChatAgentError

    endpoint = _discover_endpoint()
    if endpoint is None:
        return {"ok": True, "action": "skipped", "reason": "language_server_unavailable"}
    url, token, ls_pid = endpoint
    summary = _probe_cascade_summary(url, token, args.conversation_id)
    if summary is None:
        return {"ok": True, "action": "skipped", "reason": "conversation_not_found"}
    status, last_modified_epoch = summary
    if status != "CASCADE_RUN_STATUS_IDLE":
        return {"ok": True, "action": "skipped", "reason": "not_idle", "status": status}
    if last_modified_epoch is None:
        return {"ok": True, "action": "skipped", "reason": "missing_last_modified_time", "status": status}

    ls_start_epoch = _process_start_epoch(ls_pid) if ls_pid is not None else None
    long_idle = int(getattr(args, "long_idle_seconds", DEFAULT_LONG_IDLE_SECONDS))
    cooldown = int(getattr(args, "cooldown_seconds", DEFAULT_STALL_COOLDOWN_SECONDS))
    if _wake_reason(
        state=_read_state(args), now=time.time(), last_modified_epoch=last_modified_epoch,
        ls_pid=ls_pid, ls_start_epoch=ls_start_epoch, long_idle_seconds=long_idle, cooldown_seconds=cooldown,
    ) is None:
        return {"ok": True, "action": "skipped", "reason": "active_wait_or_cooldown", "status": status}

    if not _admitted(args):
        return {"ok": True, "action": "skipped", "reason": "not_admitted", "status": status}
    if getattr(args, "dry_run", False):
        return {"ok": True, "action": "would_wake", "status": status, "ls_pid": ls_pid}

    latest = _probe_cascade_summary(url, token, args.conversation_id)
    if latest is None or latest[0] != "CASCADE_RUN_STATUS_IDLE" or latest[1] != last_modified_epoch:
        return {
            "ok": True, "action": "skipped", "reason": "conversation_changed",
            "status": latest[0] if latest is not None else None,
        }

    reserved_reason: str | None = None
    with _state(args) as state:
        locked_now = time.time()
        reserved_reason = _wake_reason(
            state=state, now=locked_now, last_modified_epoch=last_modified_epoch,
            ls_pid=ls_pid, ls_start_epoch=ls_start_epoch, long_idle_seconds=long_idle, cooldown_seconds=cooldown,
        )
        if reserved_reason is None:
            return {"ok": True, "action": "skipped", "reason": "cooldown_or_dedup_under_lock", "status": status}
        state["last_woken_at"] = locked_now
        state["last_wake_reason"] = reserved_reason
        state["last_woken_modified_at"] = last_modified_epoch
        if reserved_reason == "ls_restarted_since_last_turn" and ls_pid is not None and ls_start_epoch is not None:
            state["last_woken_ls_pid"] = ls_pid
            state["last_woken_ls_start_epoch"] = ls_start_epoch

    try:
        _send_wake_message(url, token, args.conversation_id, bootstrap_prompt(args))
    except (ValueError, CodexChatAgentError, OSError) as exc:
        return {"ok": False, "action": "send_failed", "reason": reserved_reason, "error_kind": type(exc).__name__, "ls_pid": ls_pid}
    return {"ok": True, "action": "woken", "reason": reserved_reason, "status": status, "ls_pid": ls_pid}


def main() -> int:
    pin_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("stop-hook", "heartbeat-tick", "install-launchd")
    )
    parser.add_argument("--cli-bin", default="loopx")
    parser.add_argument("--registry")
    parser.add_argument("--runtime-root")
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".loopx/host-state/antigravity")
    )
    parser.add_argument("--long-idle-seconds", type=int, default=DEFAULT_LONG_IDLE_SECONDS)
    parser.add_argument(
        "--cooldown-seconds", type=int, default=DEFAULT_STALL_COOLDOWN_SECONDS
    )
    parser.add_argument(
        "--interval-seconds", type=int, default=DEFAULT_LAUNCHD_INTERVAL_SECONDS
    )
    parser.add_argument("--label")
    parser.add_argument("--launch-agents-dir")
    parser.add_argument("--log-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-load", action="store_true")
    args = parser.parse_args()
    exit_code = 0
    try:
        result: dict[str, object]
        if args.operation == "stop-hook":
            result = stop_hook(args, json.load(sys.stdin))
        elif args.operation == "heartbeat-tick":
            result = heartbeat_tick(args)
            exit_code = 0 if result.get("ok") is True else 1
        else:
            result = install_launchd(args)
            exit_code = 0 if result.get("ok") is True else 1
    except Exception as exc:
        # Hook stdout must remain valid native JSON. Provider detail may contain
        # credentials or private context; expose only the failure class.
        print(
            f"LoopX Antigravity {args.operation} unavailable ({type(exc).__name__})",
            file=sys.stderr,
        )
        if args.operation == "stop-hook":
            result = {}
        else:
            result = {"ok": False, "error": type(exc).__name__}
            exit_code = 1
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
