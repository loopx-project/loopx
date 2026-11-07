"""Check a receiver's native login route without starting a model Turn.

This startup-only transport check owns neither quota policy nor recovery of an
already dispatched request. A failed observer leaves the original worker,
session and scheduler records untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from loopx.control_plane.turn_driver.codex_stdio import (
    NativeGoalProtocolError,
    native_checkpoint_reader,
)

# These are exact machine codes emitted by the shared native transport, not
# classifications of remote error messages. Unknown errors remain terminal.
_TRANSPORT_TIMEOUTS = frozenset({
    "app_server_response_timeout:initialize",
    "app_server_response_timeout:account/read",
    "app_server_response_timeout:account/rateLimits/read",
})
_TRANSPORT_DISCONNECTS = frozenset({
    "app_server_stream_closed", "app_server_stdin_closed",
    "app_server_write_failed", "app_server_exited_before_response",
})


class _ObservationExpired(TimeoutError):
    """The local absolute observation deadline was exhausted."""


class _InvalidObservation(Exception):
    """A local validation failure; never populated from a remote response."""


def _failure(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, _ObservationExpired):
        return "timeout", True
    if isinstance(exc, NativeGoalProtocolError):
        if str(exc) in _TRANSPORT_TIMEOUTS:
            return "timeout", True
        if str(exc) in _TRANSPORT_DISCONNECTS:
            return "disconnected", True
        return "protocol_rejected", False
    if isinstance(exc, _InvalidObservation):
        return str(exc), False
    if isinstance(exc, OSError):
        return "local_io", False
    if isinstance(exc, KeyError):
        return "configuration", False
    return "unexpected", False


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _ObservationExpired
    return remaining


def _observe_once(env: dict[str, str], deadline: float) -> dict:
    started = time.monotonic()
    stage = "inspection_home"
    try:
        _remaining(deadline)
        # Native app-server can install bundled skills during account-only
        # reads. Each observer gets fresh copied login/config, never the solver's
        # session store; a retry cannot carry the previous observer's writes.
        home = Path(env["LOOPX_CODEX_HOME"])
        with tempfile.TemporaryDirectory(prefix="loopx-native-inspection-") as temporary:
            observer = Path(temporary)
            for name in ("auth.json", "config.toml"):
                source = home / name
                if source.is_file():
                    shutil.copyfile(source, observer / name)
                    (observer / name).chmod(0o600)
            native_env = {**env, "CODEX_HOME": str(observer)}
            timeout = _remaining(deadline)
            stage = "initialize"
            with native_checkpoint_reader(native_env, timeout=timeout, deadline=deadline) as reader:
                def request(method, params):
                    reader.response_timeout_sec = _remaining(deadline)
                    return reader.request(method, params)

                stage = "account_read"
                account = request("account/read", {"refreshToken": False}).get("account")
                if not isinstance(account, dict) or account.get("type") != "chatgpt":
                    raise _InvalidObservation("invalid_account")
                stage = "rate_limits"
                usage = request("account/rateLimits/read", {})
                if not isinstance(usage, dict) or not usage:
                    raise _InvalidObservation("unavailable_rate_limits")
                _remaining(deadline)
    except Exception as exc:
        error_kind, retryable = _failure(exc)
        return {"ok": False, "stage": stage, "error_kind": error_kind,
                "retryable": retryable, "elapsed_seconds": round(time.monotonic() - started, 3)}
    return {"ok": True, "stage": stage,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def check_native_receiver(env: dict[str, str]) -> dict:
    # Retain the existing total observation budget, including home preparation,
    # initialization, reads and retry backoff. Process cleanup may finish later;
    # it never grants another request beyond this deadline or starts a model.
    started = time.monotonic()
    deadline = started + 25
    attempts = []
    for index in range(3):
        if index:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))
            if time.monotonic() >= deadline:
                break
        observation = _observe_once(env, deadline)
        attempts.append(observation)
        if observation["ok"] or not observation["retryable"]:
            break
    ok = attempts[-1]["ok"]
    result = {"ok": ok, "inference_invoked": False, "attempts": attempts,
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    if not ok:
        result["code"] = "native_receiver_unavailable"
    return result


def main() -> int:
    # Emit only bounded local stage/category/timing diagnostics. Never include
    # raw exception text, native stderr, login values or account/RPC payloads.
    result = check_native_receiver(dict(os.environ))
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
