#!/usr/bin/env python3
"""Prove the operator-facing managed flow: start, read back, and fail closed.

One entry point carries the whole managed contract, so an operator never has to
reconcile three readbacks:

1. this machine's resolved operator credential (the machine store, then the
   service environment) is what authenticates the managed host;
2. the same resolution drives the planned executor readback and the steward
   channel readback, which must agree on mode, executor and status;
3. an unavailable delivery refuses with a typed reason instead of launching, and
   a single managed binding refuses a second concurrent executor.

Nothing here starts a live provider call, writes repository state, or spends
quota: the managed runtime and the segment runner are injected, exactly as the
governing surfaces inject them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_dsh import DshChatAdapter  # noqa: E402
from loopx.chat_manager import manager_channel_binding  # noqa: E402
from loopx.control_plane.operator_provider import (  # noqa: E402
    operator_provider_environ,
    write_operator_provider,
)
from loopx.control_plane.turn_driver.execution_profile import (  # noqa: E402
    INVALID_REASONING_EFFORT,
    managed_execution_profile,
)
from loopx.control_plane.turn_driver.host_binding import (  # noqa: E402
    OPERATOR_CREDENTIAL_UNCONFIGURED,
    managed_executor_binding,
)

SECOND_EXECUTOR = "managed_host_chat_segment_in_flight"
MANAGED_DEFAULTS = {
    "status": "ready",
    "executor_endpoint": "dsh",
    "executor_model": "deepseek-v4-flash",
    "executor_reasoning_effort": "high",
}


def fail(message: str) -> None:
    raise SystemExit(f"managed turn operator flow smoke failed: {message}")


def _runtime_installed(*_args: object, **_kwargs: object) -> bool:
    return True


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)

        # 1. Without a credential the managed delivery refuses, and names the
        #    fact it refuses on instead of launching on something else.
        unconfigured = operator_provider_environ(root, environ={})
        refusal = managed_executor_binding(
            "dsh", environ=unconfigured, module_probe=_runtime_installed
        )
        if refusal.get("available") is not False:
            fail(f"an unconfigured managed delivery must not be available: {refusal}")
        if refusal.get("unavailable_reason") != OPERATOR_CREDENTIAL_UNCONFIGURED:
            fail(f"the refusal must name the credential: {refusal}")

        # 2. Storing one credential resolves every surface below from the same
        #    value, so the executor, the profile and the channel agree.
        write_operator_provider(
            runtime_root=root,
            api_key="sk-managed-flow-smoke",
            base_url="https://endpoint.smoke.invalid/v1",
        )
        environ = operator_provider_environ(root, environ={})
        planned = managed_executor_binding(
            "dsh", environ=environ, module_probe=_runtime_installed
        )
        if planned.get("available") is not True:
            fail(f"a stored credential must make the managed host launchable: {planned}")
        if planned.get("execution_profile") != "deepseek-flash@high":
            fail(f"the planned profile must be projected: {planned}")

        channel = manager_channel_binding(
            environ=environ,
            machine_defaults=MANAGED_DEFAULTS,
            credential_source="machine_store",
            session=None,
            module_probe=_runtime_installed,
        )
        if channel.get("executor_endpoint") != "dsh":
            fail(f"the channel must resolve the machine's executor: {channel}")
        if channel.get("executor_kind") != "managed":
            fail(f"the channel must report the executor kind: {channel}")
        if channel.get("execution_profile") != planned.get("execution_profile"):
            fail("the channel and the planned Turn must project one profile")
        if channel.get("available") is not True:
            fail(f"the channel must be able to serve the managed host: {channel}")
        if channel.get("operator_credential_source") != "machine_store":
            fail(f"the channel must name the credential source: {channel}")
        # An unbound channel reads back `unbound` rather than an absent field, so
        # the status projection is always present and never guessed.
        if channel.get("session_mode_source") != "unbound":
            fail(f"an unbound channel must project an explicit mode source: {channel}")

        # A profile the provider is known to reject also fails closed, so the
        # flow cannot spend a Turn on a request the endpoint will refuse.
        unsupported = managed_execution_profile(environ, reasoning_effort="unsupported")
        if (
            managed_executor_binding(
                "dsh",
                environ=environ,
                module_probe=_runtime_installed,
                reasoning_effort="unsupported",
            ).get("unavailable_reason")
            != INVALID_REASONING_EFFORT
            or unsupported.get("reasoning_effort_supported") is not False
        ):
            fail("an unsupported reasoning effort must fail closed")

        # 3. One managed binding is one executor: a second concurrent turn is
        #    refused with a typed state rather than queued behind the first.
        entered = threading.Event()
        release = threading.Event()

        def blocking_runner(**_kwargs: object) -> dict[str, object]:
            entered.set()
            release.wait(20)
            return {"final_response": "ok", "finish_reason": None, "events": []}

        adapter = DshChatAdapter(
            objective="managed flow smoke",
            work_dir=root,
            provider="deepseek-official",
            model="deepseek-v4-flash",
            reasoning_effort="high",
            timeout_sec=30.0,
            runner=blocking_runner,
        )
        first = threading.Thread(
            target=lambda: adapter.start_turn("first", lambda *a, **k: None),
            daemon=True,
        )
        try:
            first.start()
            if not entered.wait(10):
                fail("the injected managed runner never started")
            try:
                adapter.start_turn("second", lambda *a, **k: None)
            except Exception as exc:  # noqa: BLE001 - the typed code is the assertion
                if getattr(exc, "error_code", "") != SECOND_EXECUTOR:
                    fail(f"the second executor must be refused as {SECOND_EXECUTOR}: {exc}")
            else:
                fail("a second concurrent executor was accepted")
        finally:
            release.set()
            first.join(10)

        print(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": "loopx_managed_turn_operator_flow_smoke_v0",
                    "checks": [
                        "unconfigured_delivery_refuses_with_a_typed_reason",
                        "stored_credential_resolves_executor_profile_and_channel",
                        "channel_and_planned_turn_project_one_profile",
                        "unsupported_profile_fails_closed",
                        "second_concurrent_executor_is_refused",
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
