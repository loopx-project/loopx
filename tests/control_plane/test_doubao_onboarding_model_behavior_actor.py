from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from loopx.control_plane.testing.doubao_model_behavior_actor import (
    DOUBAO_2_1_PRO_MODEL,
    DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
    DoubaoOnboardingModelBehaviorActor,
    _onboarding_decision_instruction,
)
from loopx.control_plane.testing.onboarding_model_behavior_qualification import (
    ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
    ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION,
    build_onboarding_model_behavior_actor_request,
    normalize_onboarding_model_behavior_actor_result,
    onboarding_entry_semantic_contract,
)


def _packet() -> dict[str, Any]:
    return {
        "schema_version": "loopx_start_goal_guided_v0",
        "goal_id": "fixture-goal",
        "agent_id": "codex-fixture",
        "guided_transaction": {
            "writes_now": False,
            "spends_quota_now": False,
            "ordered_steps": [{"id": "connect_if_needed"}],
        },
        "commands": {
            "goal_start_connect_if_needed": "loopx bootstrap --project $PROJECT",
            "goal_start_refresh_state": "loopx refresh-state --goal-id fixture-goal",
            "goal_start_host_loop_activation": "loopx heartbeat-prompt --thin",
            "goal_start_quota_should_run": "loopx quota should-run",
        },
        "host_loop_activation": {
            "agent_id": "codex-fixture",
            "activation_required_after_todo_write": True,
        },
    }


def test_goal_selection_descriptor_presence_does_not_grant_activation() -> None:
    packet = {
        "schema_version": "loopx_start_goal_guided_v0",
        "goal_id": None,
        "agent_id": None,
        "host_surface": "codex-app",
        "guided_transaction": {
            "blocked_by": "goal_selection", "writes_now": False,
            "spends_quota_now": False,
        },
        "command_pack": {"host_loop_activation": {
            "activation_state": "goal_selection_required",
            "activation_allowed": False,
            "activation_required_after_todo_write": False,
        }},
    }
    contract = onboarding_entry_semantic_contract(packet)
    assert contract["route"] == "select_goal"
    assert contract["host_loop_activation_available"] is True
    assert contract["host_loop_activation_after_todo_write"] is False
    assert contract["host_surface"] is None
    assert contract["activation_method"] is None
    assert contract["writes_now"] is contract["spends_quota_now"] is False
    packet["command_pack"]["host_loop_activation"] = {}
    assert onboarding_entry_semantic_contract(packet)["host_loop_activation_available"] is False


def test_direct_onboarding_actor_uses_no_tool_provider_boundary() -> None:
    captured: dict[str, Any] = {}
    contract = onboarding_entry_semantic_contract(_packet())
    decision = {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "phase": "entry",
        "next_action": "connect_if_needed",
        "semantic_contract": contract,
        "reason_codes": ["source_aligned"],
    }

    def transport(
        *,
        endpoint: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        captured.update(
            endpoint=endpoint,
            headers=dict(headers),
            body=json.loads(body),
            timeout_seconds=timeout_seconds,
        )
        return {"choices": [{"message": {"content": json.dumps(decision)}}]}

    actor = DoubaoOnboardingModelBehaviorActor(
        api_key="fixture-key-not-a-secret",
        transport=transport,
        timeout_seconds=18,
    )
    request = build_onboarding_model_behavior_actor_request(
        _packet(),
        qualification_id="onboarding-direct-doubao-001",
        phase="entry",
    )
    result = normalize_onboarding_model_behavior_actor_result(
        actor(request),
        phase="entry",
    )

    assert captured["endpoint"] == DOUBAO_CHAT_COMPLETIONS_ENDPOINT
    assert captured["body"]["model"] == DOUBAO_2_1_PRO_MODEL
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "tools" not in captured["body"]
    provider_input = json.loads(captured["body"]["messages"][1]["content"])
    assert set(provider_input) == {"schema_version", "phase", "packet"}
    assert "qualification_id" not in provider_input
    assert "sandbox" not in provider_input
    assert result["schema_version"] == ONBOARDING_MODEL_BEHAVIOR_RESULT_SCHEMA_VERSION
    assert result["tool_calls"] == []
    assert "fixture-key" not in json.dumps(result, sort_keys=True)


@pytest.mark.parametrize("phase", ["entry", "postcondition"])
def test_onboarding_reason_code_instruction_matches_the_strict_result_boundary(phase):
    instruction = _onboarding_decision_instruction(phase)
    assert "reason_codes is required and must contain 1 to 12" in instruction
    assert "^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$" in instruction
    assert "including when the observation is healthy" in instruction


@pytest.mark.parametrize(
    "reasons", [[], ["not a token"], [f"reason_{index}" for index in range(13)]],
)
def test_onboarding_invalid_reason_codes_are_not_repaired_or_retried(reasons):
    calls = 0
    decision = {
        "schema_version": ONBOARDING_MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
        "phase": "entry",
        "next_action": "connect_if_needed",
        "semantic_contract": onboarding_entry_semantic_contract(_packet()),
        "reason_codes": reasons,
    }

    def transport(**kwargs):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": json.dumps(decision)}}]}

    actor = DoubaoOnboardingModelBehaviorActor(
        api_key="fixture-key-not-a-secret", transport=transport,
    )
    request = build_onboarding_model_behavior_actor_request(
        _packet(), qualification_id="onboarding-invalid-reasons", phase="entry",
    )
    raw = actor(request)
    with pytest.raises(ValueError, match="reason_codes"):
        normalize_onboarding_model_behavior_actor_result(raw, phase="entry")
    assert raw["decision"]["reason_codes"] == decision["reason_codes"]
    assert calls == 1
