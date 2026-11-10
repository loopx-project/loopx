"""Capability IO remains optional and uses the original transaction's receipts."""

from __future__ import annotations

import json

import pytest

from loopx.capabilities.reward_memory.turn_hooks import (
    MEMORY_CONTEXT_MAX_RESULT_BYTES,
    memory_context_result,
)
from loopx.control_plane.capability_hooks import (
    TurnContextHookRegistration,
    dispatch_turn_context_hooks,
    project_turn_context,
)
from loopx.control_plane.turn_driver.post_settlement import (
    PostSettlementHookRegistration,
    registered_post_settlement,
    run_post_settlement_callback,
)


def test_invalid_or_duplicate_context_registration_never_reinvokes_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.control_plane import capability_hooks

    calls = []
    validation_requests = []
    invoke = capability_hooks.effect_runtime_result

    def observe(method, params, **kwargs):
        if method == "capability_hook.turn_context.validate":
            validation_requests.append(params)
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(capability_hooks, "effect_runtime_result", observe)

    def produce():
        calls.append("read")
        return {
            "readback": {"fixture_readback": {}},
            "context_fields": {},
            "instructions": [],
            "result_fields": {},
        }

    valid = TurnContextHookRegistration(
        "fixture.context", "fixture", ("fixture_readback",), produce
    )
    invalid = TurnContextHookRegistration(
        "fixture.invalid", "fixture", ("primary_action",), produce
    )
    payload = {"goal_id": "fixture", "agent_id": "fixture"}
    dispatch_turn_context_hooks(payload, (invalid, valid, valid))
    assert calls == ["read"]
    assert len(payload["turn_context_hook_failures"]) == 2
    assert payload["fixture_readback"] == {}
    assert "turn_context_contributions" not in payload
    request, = validation_requests
    assert request["readback_only"] is True and "source" not in request

    # Retrying composition on an already populated packet must not repeat IO.
    dispatch_turn_context_hooks(payload, (valid,))
    assert calls == ["read"]


@pytest.mark.parametrize("field", ["work_contract", "source_decision_hash"])
@pytest.mark.parametrize("location", ["readback", "context_fields", "result_fields"])
def test_optional_context_cannot_contribute_core_work_source(field, location) -> None:
    calls = []
    slot = field if location == "readback" else "fixture_readback"

    def produce():
        calls.append("read")
        result = {"readback": {slot: {}}, "context_fields": {},
                  "instructions": [], "result_fields": {}}
        if location != "readback":
            result[location] = {field: ("optional advice" if location == "context_fields"
                                       else {"type": "string", "maxLength": 240})}
        return result

    envelope = {"goal_id": "fixture", "agent_id": "fixture", "boundary": {"write_scope": []}}
    payload = {"turn_envelope": envelope}
    dispatch_turn_context_hooks(payload, (TurnContextHookRegistration(
        "fixture.context", "fixture", (slot,), produce,
    ),))
    assert calls == ([] if location == "readback" else ["read"])
    assert payload["turn_envelope"] == envelope
    assert payload["turn_context_hook_failures"] == [
        {"hook_id": "fixture.context", "error_code": "context_hook_failed"}]
    assert "turn_context_contributions" not in payload
    assert project_turn_context(payload) == {}


@pytest.mark.parametrize("serialized", [True, 3, "invalid", {"fixture": "invalid"}])
def test_invalid_recovered_context_is_omitted_without_gating_work(serialized) -> None:
    assert project_turn_context({"turn_context_contributions": serialized}) == {}


@pytest.mark.parametrize("fault", ["none", "readback", "context-collision", "result-collision", "malformed"])
def test_context_projection_isolates_one_contribution_and_keeps_order(fault) -> None:
    from copy import deepcopy

    payload = {"turn_envelope": {
        "goal_id": "fixture-goal", "agent_id": "fixture-agent", "boundary": {"write_scope": []},
    }}
    calls = []
    registrations = []
    for name in ("left", "middle", "right"):
        context_key = "left_context" if name == "middle" and fault == "context-collision" else f"{name}_context"
        result_key = "left_result" if name == "middle" and fault == "result-collision" else f"{name}_result"
        result = {
            "readback": {f"{name}_readback": {"value": name}},
            "context_fields": {context_key: f"Private {name} advice."},
            "instructions": [f"Use {name} only as advice."],
            "result_fields": {result_key: {"type": "string", "maxLength": 128}},
        }

        def produce(value=result, label=name):
            calls.append(label)
            return deepcopy(value)

        registrations.append(TurnContextHookRegistration(
            f"fixture.{name}", "fixture", (f"{name}_readback",), produce,
        ))
    dispatch_turn_context_hooks(payload, registrations)
    assert calls == ["left", "middle", "right"]
    if fault == "readback":
        payload["middle_readback"] = {"value": "changed after registration"}
    elif fault == "malformed":
        payload["turn_context_contributions"][1] = {"readback_slots": 3}
    before = deepcopy(payload)
    accepted = ["left", "middle", "right"] if fault == "none" else ["left", "right"]
    expected = {
        **{f"{name}_context": f"Private {name} advice." for name in accepted},
        "capability_context_fields": [f"{name}_context" for name in accepted],
        "capability_instructions": [f"Use {name} only as advice." for name in accepted],
        "capability_result_fields": {
            f"{name}_result": {"type": "string", "maxLength": 128} for name in accepted
        },
    }
    assert project_turn_context(payload) == expected
    assert project_turn_context(payload) == expected
    assert payload == before and calls == ["left", "middle", "right"]
    payload["turn_envelope"]["goal_id"] = "another-goal"
    assert project_turn_context(payload) == {}


def test_instruction_only_context_stays_available_without_optional_fields() -> None:
    payload = {"turn_envelope": {"goal_id": "fixture-goal", "agent_id": "fixture-agent"}}
    dispatch_turn_context_hooks(payload, (TurnContextHookRegistration(
        "fixture.advice", "fixture", ("fixture_readback",),
        lambda: {"readback": {"fixture_readback": None}, "context_fields": {},
                 "instructions": ["Keep the existing stop condition."], "result_fields": {}},
    ),))
    assert project_turn_context(payload) == {
        "capability_context_fields": [],
        "capability_instructions": ["Keep the existing stop condition."],
        "capability_result_fields": {},
    }


def test_context_projection_keeps_advice_near_the_runtime_request_limit() -> None:
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    payload = {"turn_envelope": {
        "goal_id": "fixture-goal", "agent_id": "fixture-agent",
        "observations": "x" * (MAX_REQUEST_BYTES - 50_000),
    }}
    registrations = []
    for name in ("left", "right"):
        result = {"readback": {f"{name}_readback": None},
                  "context_fields": {f"{name}_context": name[0] * 32_000},
                  "instructions": [], "result_fields": {}}
        registrations.append(TurnContextHookRegistration(
            f"fixture.{name}", "fixture", (f"{name}_readback",), lambda value=result: value,
        ))
    dispatch_turn_context_hooks(payload, registrations)
    assert "turn_context_hook_failures" not in payload
    projected = project_turn_context(payload)
    assert projected["left_context"] == "l" * 32_000
    assert projected["right_context"] == "r" * 32_000
    assert projected["capability_context_fields"] == ["left_context", "right_context"]


@pytest.mark.parametrize("guidance_chars", [40_000, 70_000])
def test_optional_recall_size_never_disables_enabled_ingest(
    guidance_chars: int,
) -> None:
    payload = {
        "turn_envelope": {
            "goal_id": "fixture-goal",
            "agent_id": "fixture-agent",
            "boundary": {
                "capabilities": {
                    "reward_memory": {
                        "automatic_recall": True,
                        "automatic_ingest": True,
                    }
                }
            },
        }
    }
    recall = {
        "schema_version": "agent_turn_recall_v0",
        "status": "applied",
        "experiment": {
            "goal_id": "fixture-goal",
            "agent_id": "fixture-agent",
            "enabled": True,
            "available": True,
            "configured_for_agent": True,
            "automatic_recall": True,
            "automatic_ingest": True,
        },
        "context": {"guidance": [{"content_summary": "x" * guidance_chars}]},
        "grants_new_action_authority": False,
    }

    result = memory_context_result(payload, recall)

    assert result["readback"]["reward_memory_recall"]["context"] is None
    assert (
        result["result_fields"]["reward_memory_reflection_json"]["maxLength"]
        == 2400
    )
    assert any(
        "Set reward_memory_reflection_json" in item
        for item in result["instructions"]
    )
    delivered = result["context_fields"]["reward_memory_recall"]
    assert (delivered["context"] is not None) is (guidance_chars == 40_000)
    assert len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ) <= MEMORY_CONTEXT_MAX_RESULT_BYTES


def test_post_settlement_retry_keeps_completed_peer_hook_and_original_evidence() -> (
    None
):
    calls = []

    def completed(_plan, _result, evidence):
        assert evidence["writeback"]["appended"] is True
        assert evidence["quota_spend"]["appended"] is True
        calls.append("completed")
        return {"status": "activated", "external_writes_performed": True}

    def uncertain(_plan, _result, _evidence):
        calls.append("uncertain")
        if calls.count("uncertain") == 1:
            raise OSError("lost provider response")
        return {"status": "activated", "external_writes_performed": False}

    callback = registered_post_settlement(
        (
            PostSettlementHookRegistration(
                "fixture.complete", "fixture-one", completed
            ),
            PostSettlementHookRegistration("fixture.pending", "fixture-two", uncertain),
        )
    )
    journal = {
        "task_validation": {"ok": True},
        "writeback": {"ok": True, "appended": True},
        "quota_spend": {"ok": True, "appended": True},
    }
    saved = []
    for _ in range(3):
        run_post_settlement_callback(
            plan={},
            result={},
            post_settlement=callback,
            journal=journal,
            persist_journal=lambda value: saved.append(dict(value)),
        )
    assert calls == ["completed", "uncertain", "uncertain"]
    assert len(saved) == 2
    assert (
        saved[0]["post_settlement"]["results"]["fixture.pending"][
            "external_writes_performed"
        ]
        is None
    )
    assert journal["post_settlement"]["status"] == "observed"


def test_duplicate_settlement_registration_is_rejected_before_io() -> None:
    hook = PostSettlementHookRegistration(
        "fixture.duplicate", "fixture", lambda *_: pytest.fail("no IO")
    )
    with pytest.raises(ValueError, match="duplicate"):
        registered_post_settlement((hook, hook))
