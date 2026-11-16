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
import subprocess
import sys
import time
from typing import Any

from ..command_invocation import loopx_cli_argv
from ..entrypoint import pin_utf8_stdio
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
        "when the fresh user_channel requests no notification."
    )


@contextmanager
def _state(args: argparse.Namespace):
    # Native conversation ids never become path components.
    key = hashlib.sha256(args.conversation_id.encode()).hexdigest()
    path = Path(args.state_dir).expanduser() / (key + ".json")
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


def main() -> int:
    pin_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("stop-hook",))
    parser.add_argument("--cli-bin", default="loopx")
    parser.add_argument("--registry")
    parser.add_argument("--runtime-root")
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".loopx/host-state/antigravity")
    )
    args = parser.parse_args()
    try:
        result = stop_hook(args, json.load(sys.stdin))
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
