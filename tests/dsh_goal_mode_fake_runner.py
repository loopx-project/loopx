"""Hermetic fake dsh runner for the goal-mode adapter tests.

Exposes the ``run_dsh_turn`` keyword contract of
``loopx.hosts.dsh.turn_host_adapter`` without a real DeepSeek Harness
SDK/runtime or network access. Not a pytest module (no ``test_`` prefix).
"""

from __future__ import annotations

import json

from loopx.control_plane.turn_driver.execution_profile import REASONING_EFFORTS


def run_dsh_turn(
    *,
    prompt: str,
    session_id: str,
    workspace: str,
    session_root: str,
    provider: str,
    model: str,
    reasoning_effort: str,
    max_tokens: int | None,
    cordis: str | None,
    runtime_bin: str | None,
    request_timeout_seconds: float | None,
) -> str:
    assert prompt, "the adapter must deliver the bounded task body"
    assert session_id, "the adapter must key the dsh session"
    # The resolved managed execution profile is part of the runner contract, so
    # a regression that drops it fails here instead of reaching a real endpoint.
    assert provider and model, "the adapter must hand over the resolved profile"
    assert reasoning_effort in REASONING_EFFORTS, (
        "the adapter must hand over a supported reasoning effort"
    )
    return json.dumps(
        {
            "result_kind": "validated_progress",
            "classification": "fake dsh typed result",
            "summary": "one bounded fake work segment",
            "recommended_action": "validate the fake typed result",
            "next_action": "run the LoopX validator",
            "vision_unchanged_reason": "the fake runner never replans",
        }
    )
