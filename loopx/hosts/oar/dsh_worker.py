"""JSONL transport for the pinned DeepSeek Harness Python SDK.

OAR owns the session controls and event stream. This worker only translates one
SDK call, including its native notifications and terminal result. It stays in
the caller's process group so LoopX's existing process runner can stop the
entire runtime tree. The SDK remains responsible for composing DSH permissions.
"""

from __future__ import annotations

import contextlib
import json
import signal
import sys
from collections.abc import Mapping
from typing import Any


def main() -> int:
    output = sys.stdout
    if hasattr(output, "reconfigure"):
        output.reconfigure(encoding="utf-8")

    def emit(value: Mapping[str, Any]) -> None:
        output.write(json.dumps(value, ensure_ascii=True) + "\n")
        output.flush()

    def interrupted(_signum: int, _frame: object) -> None:
        # Unwind the SDK context manager: its bounded close reaps the runtime.
        # A group stop and the owning Node Session may signal us together.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        # SDK logging must not corrupt the JSONL protocol.
        with contextlib.redirect_stdout(sys.stderr):
            try:
                from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
            except ImportError as exc:
                raise RuntimeError(
                    "deepseek-harness-sdk is required; install it with "
                    "`python -m pip install 'loopx[deepseek-harness]'`"
                ) from exc
            if sys.argv[1:] == ["--probe"]:
                emit({"available": True})
                return 0
            request = json.loads(sys.stdin.readline())
            if not isinstance(request, dict):
                raise ValueError("DSH worker request must be an object")
            prompt, session_id = request.get("prompt"), request.get("session_id")
            config = request.get("config")
            if not isinstance(prompt, str) or not isinstance(session_id, str) or not session_id:
                raise ValueError("DSH worker requires a prompt and a session_id")
            if not isinstance(config, dict):
                raise ValueError("DSH worker requires an SDK config object")

            def notification(value: Any) -> None:
                emit({"type": "notification", "notification": {
                    "method": value.method, "payload": value.payload,
                }})

            with DeepSeekHarness(DeepSeekHarnessConfig(**config)) as harness:
                result = harness.run(prompt, session_id=session_id, on_notification=notification)
            # Emit only after the SDK closes, so a consumer of the terminal
            # frame cannot strand the native runtime by disposing immediately.
            emit({"type": "result", "result": {
                "final_response": result.final_response,
                "finish_reason": result.finish_reason,
                "events": result.events,
            }})
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from loopx.dsh_goal_mode.host_failure_map import classify_dsh_failure

        emit({"type": "error", "error": {
            "name": type(exc).__name__, "message": str(exc)[:4000],
            "failure_kind": classify_dsh_failure(exc),
        }})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
