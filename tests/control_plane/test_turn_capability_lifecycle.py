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


def test_invalid_or_duplicate_context_registration_never_reinvokes_provider() -> None:
    calls = []

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

    # Retrying composition on an already populated packet must not repeat IO.
    dispatch_turn_context_hooks(payload, (valid,))
    assert calls == ["read"]


@pytest.mark.parametrize("serialized", [True, 3, "invalid", {"fixture": "invalid"}])
def test_invalid_recovered_context_is_omitted_without_gating_work(serialized) -> None:
    assert project_turn_context({"turn_context_contributions": serialized}) == {}


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
