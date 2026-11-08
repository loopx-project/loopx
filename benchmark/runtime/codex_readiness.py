"""Check a receiver's native login route without starting a model Turn.

This startup-only transport check owns neither quota policy nor recovery of an
already dispatched request. A failed observer leaves the original worker,
session and scheduler records untouched.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
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
_ACCOUNT_TIMEOUTS = _TRANSPORT_TIMEOUTS - {"app_server_response_timeout:initialize"}


@dataclass(frozen=True)
class _Observation:
    receipt: dict
    account_timeout: bool = False


def native_readiness_budget(total_seconds=None, attempt_seconds=None) -> tuple[float, float] | None:
    """Validate an explicit startup-only budget; omission preserves the old path."""
    if total_seconds is None and attempt_seconds is None:
        return None
    attempt_seconds = 25 if attempt_seconds is None else attempt_seconds
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in (total_seconds, attempt_seconds)):
        raise ValueError("Native readiness budgets must be finite numbers")
    if not 0 < total_seconds <= 75 or not 0 < attempt_seconds <= 25:
        raise ValueError("Native readiness requires total seconds in (0, 75] and attempt seconds in (0, 25]")
    return float(total_seconds), float(attempt_seconds)


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


def _observe_once(env: dict[str, str], deadline: float, *, require_allowance: bool = False) -> _Observation:
    started = time.monotonic()
    stage = "inspection_home"
    allowance = None
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
                if require_allowance:
                    # Reuse the optional native provider's typed availability
                    # policy. A single observed receiver is not an account ring:
                    # this read cannot switch login or enable API fallback.
                    stage = "allowance"
                    try:
                        from loopx_codex_provider_routing.native import policy
                    except ImportError:
                        raise _InvalidObservation("allowance_provider_unavailable") from None
                    observed_at = time.time()
                    decision = policy({"now": observed_at,
                        "policy": {"enabled": True, "slots": ["receiver"], "cooldown_seconds": 0},
                        "active": "receiver", "observations": [{"slot": "receiver",
                            "observed_at": observed_at, "status": "ok", "usage": usage}],
                        "last_switch_at": None, "recovery_required": False, "login_matches": True})
                    _remaining(deadline)
                    if decision.get("action") != "ready":
                        raise _InvalidObservation("allowance_unavailable")
                    allowance = {"availability": "available", "observed_at": observed_at}
    except Exception as exc:
        error_kind, retryable = _failure(exc)
        receipt = {"ok": False, "stage": stage, "error_kind": error_kind,
                "retryable": retryable, "elapsed_seconds": round(time.monotonic() - started, 3)}
        # Retry permission comes from the local transport exception, never a
        # remote message or the sanitized category printed in the receipt.
        return _Observation(receipt, isinstance(exc, NativeGoalProtocolError)
                            and str(exc) in _ACCOUNT_TIMEOUTS)
    return _Observation({"ok": True, "stage": stage, **({"allowance": allowance} if allowance else {}),
                         "elapsed_seconds": round(time.monotonic() - started, 3)})


def _outer_remaining(env: dict[str, str]) -> float:
    # The native runner snapshots the existing execution allowance immediately
    # before exec. Charge entry/setup delay and convert it only once to a local
    # monotonic deadline. Missing or invalid provenance cannot grant more time.
    try:
        remaining = float(env["SFORGE_AGENT_REMAINING_SECONDS"])
        sampled_at = float(env["SFORGE_AGENT_SEGMENT_STARTED_AT"])
    except (KeyError, TypeError, ValueError):
        raise _InvalidObservation("unavailable_outer_budget") from None
    age = time.time() - sampled_at
    if not all(math.isfinite(value) for value in (remaining, sampled_at, age)) or age < 0:
        raise _InvalidObservation("unavailable_outer_budget")
    if remaining - age <= 0:
        raise _ObservationExpired
    return remaining - age


def check_native_receiver(env: dict[str, str], *, total_seconds=None, attempt_seconds=None) -> dict:
    # Retain the unconfigured total budget; an explicit startup cap also charges
    # preparation, initialization, reads and retry backoff. Cleanup may finish later;
    # it never grants another request beyond this deadline or starts a model.
    started = time.monotonic()
    deadline = started + 25
    attempts = []
    budget = native_readiness_budget(total_seconds, attempt_seconds)
    if budget is not None:
        try:
            # Reading the snapshot is part of setup cost too.
            deadline = min(started + budget[0], time.monotonic() + _outer_remaining(env))
        except (_InvalidObservation, _ObservationExpired) as exc:
            kind, _ = _failure(exc)
            attempts.append({"ok": False, "stage": "outer_budget", "error_kind": kind,
                             "retryable": False, "account_timeout": False,
                             "elapsed_seconds": round(time.monotonic() - started, 3)})
    for index in range(3):
        if attempts and not attempts[-1]["retryable"]:
            break
        if index:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))
            if time.monotonic() >= deadline:
                break
        attempt_deadline = min(deadline, time.monotonic() + budget[1]) if budget else deadline
        observation = _observe_once(env, attempt_deadline)
        attempts.append({**observation.receipt,
                         **({"account_timeout": observation.account_timeout} if budget is not None else {})})
        if observation.receipt["ok"] or not observation.receipt["retryable"]:
            break
        if budget is not None and not observation.account_timeout:
            break
    ok = attempts[-1]["ok"]
    result = {"ok": ok, "inference_invoked": False, "attempts": attempts,
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    if not ok:
        result["code"] = "native_receiver_unavailable"
    return result


def require_native_receiver_allowance(env: dict[str, str]) -> dict:
    """Fresh, account-only evidence for an explicit failed-Host continuation.

    The startup probe's default behavior stays unchanged. This stricter caller
    refuses unknown/exhausted allowance and missing optional provider support;
    it never refreshes login, switches accounts, or starts inference.
    """
    observation = _observe_once(env, time.monotonic() + 25, require_allowance=True).receipt
    if not observation["ok"]:
        raise ValueError("Explicit Host continuation requires fresh native allowance: "
                         + observation["stage"] + "/" + observation["error_kind"])
    return observation["allowance"]


def main(argv=None) -> int:
    # Emit only bounded local stage/category/timing diagnostics. Never include
    # raw exception text, native stderr, login values or account/RPC payloads.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total-seconds", type=float)
    parser.add_argument("--attempt-seconds", type=float)
    args = parser.parse_args(argv)
    try:
        native_readiness_budget(args.total_seconds, args.attempt_seconds)
    except ValueError as exc:
        parser.error(str(exc))
    result = check_native_receiver(dict(os.environ), total_seconds=args.total_seconds,
                                   attempt_seconds=args.attempt_seconds)
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
