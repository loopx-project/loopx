from __future__ import annotations

import json
from io import BytesIO
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError

import pytest

from loopx.control_plane.testing.doubao_model_behavior_actor import (
    DOUBAO_2_1_PRO_MODEL,
    DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
    DOUBAO_SEED_EVOLVING_MODEL,
    MODEL_BEHAVIOR_PROVIDER_INPUT_SCHEMA_VERSION,
    DoubaoActorTransportError,
    DoubaoModelBehaviorActor,
    _direct_ark_transport,
    _decision_instruction,
    _provider_input,
)
from loopx.control_plane.testing.model_behavior_qualification import (
    build_model_behavior_actor_request,
    normalize_model_behavior_actor_result,
)


def _request() -> dict[str, Any]:
    return build_model_behavior_actor_request(
        {
            "schema_version": "loopx_turn_envelope_v0",
            "action": {"selected_todo": {"todo_id": "todo_fixture001"}},
        },
        qualification_id="case-direct-doubao-001",
        arm="candidate_packet",
    )


def _decision() -> dict[str, Any]:
    return {
        "schema_version": "model_behavior_decision_v0",
        "decision": "execute",
        "selected_todo_id": "todo_fixture001",
        "user_action_required": False,
        "must_attempt_work": True,
        "delivery_allowed": True,
        "quiet_noop_allowed": False,
        "external_write_requested": False,
        "intended_action_kinds": ["inspect", "test", "writeback"],
        "reason_codes": ["bounded_delivery"],
    }


def test_provider_input_does_not_select_a_diagnostic_todo() -> None:
    request = build_model_behavior_actor_request(
        {
            "mode": "should-run",
            "goal_id": "fixture-goal",
            "interaction_contract": {
                "schema_version": "loopx_interaction_contract_v0",
                "mode": "waiting",
                "user_channel": {
                    "action_required": False,
                    "notify": "DONT_NOTIFY",
                },
                "agent_channel": {
                    "must_attempt": False,
                    "delivery_allowed": False,
                    "quiet_noop_allowed": True,
                },
                "cli_channel": {"next_cli_actions": []},
            },
            "selected_todo": None,
            "agent_todo_summary": {
                "first_executable_items": [{"todo_id": "todo_diagnostic001"}]
            },
        },
        qualification_id="case-diagnostic-todo-001",
        arm="full_packet",
    )

    provider_input = _provider_input(request)

    assert "canonical_selected_todo_id" not in provider_input
    assert (
        provider_input["packet"]["agent_todo_summary"]["first_executable_items"][0][
            "todo_id"
        ]
        == "todo_diagnostic001"
    )


def test_semantic_instruction_requires_exact_peer_route() -> None:
    instruction = " ".join(
        _decision_instruction(
            arm="candidate_packet", semantic_contract_required=True
        ).split()
    )

    assert "peer_route: always include exactly agent_id" in instruction
    assert "selected_todo_claimed_by" in instruction
    assert "same_agent_non_delivery" in instruction


def test_todo_validation_does_not_extend_the_actor_result_vocabulary() -> None:
    for arm in ("full_packet", "candidate_packet"):
        instruction = _decision_instruction(arm=arm, semantic_contract_required=False)
        assert "Represent\nvalidation as test" in instruction
    decision = _decision()
    decision["intended_action_kinds"] = ["validate"]
    with pytest.raises(ValueError, match="unknown action kind"):
        normalize_model_behavior_actor_result({
            "schema_version": "model_behavior_actor_result_v0",
            "actor_ref": "fixture-validation", "decision": decision, "tool_calls": [],
        })


def test_semantic_instruction_preserves_candidate_scheduler_and_vision_exactly() -> (
    None
):
    instruction = " ".join(
        _decision_instruction(
            arm="candidate_packet", semantic_contract_required=True
        ).split()
    )

    assert "copy packet.scheduler exactly" in instruction
    assert "without filtering or reconstruction" in instruction
    assert "packet.contract_capsule.vision_continuation_audit exactly" in instruction
    assert "including trigger_kinds" in instruction
    assert "summarize packet.action.planning_horizon" in instruction
    assert "begin intended_action_kinds with inspect" in instruction
    assert "full packet" not in instruction


def test_semantic_instruction_is_arm_scoped() -> None:
    full_instruction = " ".join(
        _decision_instruction(
            arm="full_packet", semantic_contract_required=True
        ).split()
    )

    assert "first interaction_contract.user_channel.actions value" in full_instruction
    assert "project scheduler_hint" in full_instruction
    assert "summarize packet.planning_horizon" in full_instruction
    assert "copy packet.scheduler exactly" not in full_instruction


def test_planning_horizon_instruction_excludes_unrelated_semantic_fields() -> None:
    instruction = " ".join(
        _decision_instruction(
            arm="full_packet",
            semantic_contract_required=True,
            semantic_contract_fields=("planning_horizon",),
        ).split()
    )

    assert "exactly these semantic_contract fields: planning_horizon" in instruction
    assert "summarize packet.planning_horizon" in instruction
    assert '"planning_horizon"' in instruction
    assert "peer_route" not in instruction
    assert "scheduler_action" not in instruction


def test_direct_actor_uses_canonical_endpoint_without_tools_or_raw_retention() -> None:
    captured: dict[str, Any] = {}

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
        return {"choices": [{"message": {"content": json.dumps(_decision())}}]}

    actor = DoubaoModelBehaviorActor(
        api_key="fixture-key-not-a-secret",
        transport=transport,
        timeout_seconds=12,
    )
    result = normalize_model_behavior_actor_result(actor(_request()))

    assert captured["endpoint"] == DOUBAO_CHAT_COMPLETIONS_ENDPOINT
    expected_authorization = "Bearer " + "fixture-key-not-a-secret"
    assert captured["headers"]["Authorization"] == expected_authorization
    assert captured["body"]["model"] == DOUBAO_2_1_PRO_MODEL
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["max_tokens"] == 4096
    assert "tools" not in captured["body"]
    assert captured["timeout_seconds"] == 12
    system_instruction = captured["body"]["messages"][0]["content"]
    compact_instruction = " ".join(system_instruction.lower().split())
    assert "canonical_selected_todo_id" not in compact_instruction
    assert "never infer a todo id from summaries" in compact_instruction
    assert "follow any packet response_plan exactly" in compact_instruction
    assert "when user_action_required=true, choose decision=ask_user" not in (
        compact_instruction
    )
    provider_input = json.loads(captured["body"]["messages"][1]["content"])
    assert provider_input == {
        "schema_version": MODEL_BEHAVIOR_PROVIDER_INPUT_SCHEMA_VERSION,
        "arm": "candidate_packet",
        "semantic_contract_required": False,
        "semantic_contract_fields": [],
        "packet": {
            "schema_version": "loopx_turn_envelope_v0",
            "action": {"selected_todo": {"todo_id": "todo_fixture001"}},
        },
    }
    assert "sandbox" not in provider_input
    assert "response_contract" not in provider_input
    assert "actor_instruction" not in provider_input
    assert result["actor_ref"] == f"ark:{DOUBAO_2_1_PRO_MODEL}"
    assert result["tool_calls"] == []
    assert "fixture-key" not in json.dumps(result, sort_keys=True)


def test_optional_semantic_contract_does_not_grade_auxiliary_model_output() -> None:
    decision = _decision()
    decision["semantic_contract"] = {"partial": "ungraded"}

    result = normalize_model_behavior_actor_result(
        {
            "schema_version": "model_behavior_actor_result_v0",
            "actor_ref": f"ark:{DOUBAO_2_1_PRO_MODEL}",
            "decision": decision,
            "tool_calls": [],
        },
        semantic_contract_required=False,
    )

    assert "semantic_contract" not in result["decision"]


def test_required_semantic_contract_remains_strict() -> None:
    decision = _decision()
    decision["semantic_contract"] = {"partial": "invalid"}

    with pytest.raises(ValueError, match="unknown semantic contract field"):
        normalize_model_behavior_actor_result(
            {
                "schema_version": "model_behavior_actor_result_v0",
                "actor_ref": f"ark:{DOUBAO_2_1_PRO_MODEL}",
                "decision": decision,
                "tool_calls": [],
            },
            semantic_contract_required=True,
        )


def test_environment_factory_fails_closed_without_injected_key() -> None:
    with pytest.raises(RuntimeError, match="ARK_API_KEY is not injected"):
        DoubaoModelBehaviorActor.from_environment(environ={})

    with pytest.raises(ValueError, match="explicitly allowlisted"):
        DoubaoModelBehaviorActor.from_environment(
            environ={
                "ARK_API_KEY": "fixture-key-not-a-secret",
                "LOOPX_MODEL_BEHAVIOR_MODEL": "future-model-v9",
            }
        )


def test_environment_factory_accepts_doubao_seed_evolving() -> None:
    actor = DoubaoModelBehaviorActor.from_environment(
        environ={
            "ARK_API_KEY": "fixture-key-not-a-secret",
            "LOOPX_MODEL_BEHAVIOR_MODEL": DOUBAO_SEED_EVOLVING_MODEL,
        },
        transport=lambda **_: {
            "choices": [{"message": {"content": json.dumps(_decision())}}]
        },
    )

    result = normalize_model_behavior_actor_result(actor(_request()))

    assert result["actor_ref"] == f"ark:{DOUBAO_SEED_EVOLVING_MODEL}"


@pytest.mark.parametrize(
    "response, message",
    [
        ({}, "exactly one choice"),
        ({"choices": [{"message": {"content": "not-json"}}]}, "not valid JSON"),
        (
            {"choices": [{"message": {"content": "[]"}}]},
            "decision must be an object",
        ),
    ],
)
def test_actor_rejects_malformed_provider_responses(
    response: Mapping[str, Any], message: str
) -> None:
    actor = DoubaoModelBehaviorActor(
        api_key="fixture-key-not-a-secret",
        transport=lambda **_: response,
    )
    with pytest.raises(RuntimeError, match=message):
        actor(_request())


def test_actor_sanitizes_unexpected_transport_errors() -> None:
    def transport(**_: Any) -> Mapping[str, Any]:
        raise OSError("provider error containing private transport detail")

    actor = DoubaoModelBehaviorActor(
        api_key="fixture-key-not-a-secret",
        transport=transport,
    )
    with pytest.raises(DoubaoActorTransportError) as exc_info:
        actor(_request())

    assert str(exc_info.value) == "Doubao actor provider transport failed"
    assert exc_info.value.error_code == "provider_transport_failed"


@pytest.mark.parametrize(
    ("status", "error_code", "message"),
    [
        (
            401,
            "provider_authentication_failed",
            "Doubao actor authentication failed; refresh ARK_API_KEY before retrying",
        ),
        (
            403,
            "provider_http_error",
            "Doubao actor request failed with HTTP status 403",
        ),
        (
            429,
            "provider_http_error",
            "Doubao actor request failed with HTTP status 429",
        ),
    ],
)
def test_direct_transport_classifies_http_errors_without_exposing_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    error_code: str,
    message: str,
) -> None:
    private_response = b'{"error":"provider detail must remain private"}'

    class UnauthorizedOpener:
        def open(self, *_: Any, **__: Any) -> Any:
            raise HTTPError(
                DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
                status,
                "provider detail must remain private",
                hdrs=None,
                fp=BytesIO(private_response),
            )

    monkeypatch.setattr(
        "loopx.control_plane.testing.doubao_model_behavior_actor.build_opener",
        lambda *_: UnauthorizedOpener(),
    )

    with pytest.raises(DoubaoActorTransportError) as exc_info:
        _direct_ark_transport(
            endpoint=DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
            headers={},
            body=b"{}",
            timeout_seconds=1.0,
        )

    assert exc_info.value.error_code == error_code
    assert str(exc_info.value) == message
    assert "provider detail" not in str(exc_info.value)


def test_actor_rejects_noncanonical_request_before_transport() -> None:
    called = False

    def transport(**_: Any) -> Mapping[str, Any]:
        nonlocal called
        called = True
        return {}

    actor = DoubaoModelBehaviorActor(
        api_key="fixture-key-not-a-secret",
        transport=transport,
    )
    request = _request()
    request["sandbox"] = {**request["sandbox"], "tools_enabled": True}

    with pytest.raises(ValueError, match="canonical no-write contract"):
        actor(request)
    assert called is False
