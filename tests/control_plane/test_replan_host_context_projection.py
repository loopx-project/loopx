from __future__ import annotations

import copy

import pytest

from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.testing.control_plane_composition_scenarios import (
    _required_vision_replan_source,
)

from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.work_items.autonomous_replan_obligation import (
    build_autonomous_replan_obligation_payload,
)
from loopx.quota import build_quota_should_run

GOAL_ID = "replan-context-goal"
AGENT_ID = "codex-replan-context"


def _obligation() -> dict[str, object]:
    return build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0",
        stall_threshold=2,
        trigger_count=1,
        triggers=[
            {
                "kind": "typed_progress_repeat",
                "progress_fingerprint": "repeat-fingerprint",
                "progress_baseline": {
                    "schema_version": "typed_progress_observation_v0",
                    "result_class": "unchanged",
                    "work_item_id": "todo-replan-context",
                    "surface_id": "surface-existing",
                    "fingerprint": "repeat-fingerprint",
                },
                "latest_generated_at": "2026-08-13T01:02:00Z",
            }
        ],
        guidance_actions=["create_successor"],
        todo_actions=[],
        stop_condition="stop on owner-only authority",
        recommended_action="run a bounded replan",
        agent_id=AGENT_ID,
        include_agent_id=True,
        extra_fields={
            "progress_baseline": {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "unchanged",
                "work_item_id": "todo-replan-context",
                "surface_id": "surface-existing",
                "fingerprint": "repeat-fingerprint",
            }
        },
    )


def _quota_payload(*, include_legacy_receipt: bool = False) -> dict[str, object]:
    obligation = _obligation()
    item_extra: dict[str, object] = {
        "autonomous_replan_obligation": obligation,
    }
    if include_legacy_receipt:
        item_extra["evidence_log_read_receipts"] = [
            {
                "schema_version": "evidence_log_read_receipt_v0",
                "status": "completed",
                "required_read_id": obligation["obligation_id"],
            }
        ]
    status_payload = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="run a bounded replan",
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        latest_runs=[
            {
                "goal_id": GOAL_ID,
                "generated_at": "2026-08-13T01:02:00Z",
                "agent_id": AGENT_ID,
                "classification": "bounded_probe",
                "progress_observation": {
                    "schema_version": "typed_progress_observation_v0",
                    "result_class": "unchanged",
                    "work_item_id": "todo-replan-context",
                    "surface_id": "surface-existing",
                },
            }
        ],
        item_extra=item_extra,
        project_asset_extra={"autonomous_replan_obligation": obligation},
    )
    return build_quota_should_run(
        status_payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )


def test_quota_delivers_coverage_context_and_minimal_replan_action() -> None:
    payload = _quota_payload()

    assert payload["decision"] == "autonomous_replan_required"
    assert payload.get("required_reads") in (None, [])
    obligation = payload["autonomous_replan_obligation"]
    context = obligation["replan_context"]
    action = payload["replan_action_packet"]
    assert context["evidence_source"] == "agent_scoped_evidence_log"
    assert context["delivery"] == "host_projected"
    assert context["delivery_receipt"] == {
        "schema_version": "replan_context_delivery_receipt_v0",
        "context_id": context["context_id"],
        "obligation_id": obligation["obligation_id"],
        "status": "delivered",
        "delivered_by": "quota_host_projection",
    }
    assert context["coverage_ledger"][0]["surface_id"] == "surface-existing"
    assert len(action["planning_guidance"]) == 2
    assert {key: value for key, value in action.items() if key != "planning_guidance"} == {
        "schema_version": "replan_action_packet_v0",
        "decision": "replan_required",
        "obligation_id": obligation["obligation_id"],
        "uncovered_frontier": context["uncovered_frontier"],
        "required_outcome": "semantic_delta",
        "writeback_contract": {},
        "allowed_terminal": [
            "exploration_exhausted",
            "blocked",
            "no_followup",
        ],
    }
    assert payload["interaction_contract"]["agent_channel"][
        "primary_action"
    ] == "produce one typed outcome from the host-projected replan action packet"


def test_manual_evidence_read_receipt_cannot_close_replan() -> None:
    payload = _quota_payload(include_legacy_receipt=True)

    assert payload["decision"] == "autonomous_replan_required"
    assert payload["autonomous_replan_obligation"]["required"] is True
    assert payload.get("replan_ack_feedback") is None


@pytest.mark.parametrize("vision_gap", [False, True])
def test_replan_guidance_survives_cli_and_host_compaction_without_new_authority(vision_gap: bool) -> None:
    source = (_required_vision_replan_source(goal_id=GOAL_ID, agent_id=AGENT_ID)
              if vision_gap else _quota_payload())
    guidance = source["replan_action_packet"]["planning_guidance"]
    # These are delivery assertions, not a claim that a model follows the advice.
    assert len(guidance) == 2
    assert "Never shrink requested goals" in guidance[0]
    assert "honor user scope, authority, budget and stops" in guidance[0]
    assert "current authoritative evidence for every requirement" in guidance[1]
    assert "unproven" in guidance[1]
    assert "blocked/exhausted/superseded is not achieved" in guidance[1]
    compact = compact_quota_should_run_cli_payload(source)
    envelope = build_turn_envelope(source)
    for packet in (compact, envelope):
        assert packet["replan_action_packet"]["planning_guidance"] == guidance

    baseline = copy.deepcopy(source)
    baseline["replan_action_packet"].pop("planning_guidance")
    old_envelope = build_turn_envelope(baseline)
    # Prompt advice cannot change executable actions, authority or settlement.
    assert envelope["action"] == old_envelope["action"]
    signature = quota_action_signature_document(source)
    assert turn_envelope_action_signature_document(envelope) == signature
    signature["replan_action_packet"].pop("planning_guidance")
    assert signature == quota_action_signature_document(baseline)
    # Preserve an existing budget warning; bounded advice adds no more than 360 bytes.
    assert envelope["compaction"]["within_budget"] == old_envelope["compaction"]["within_budget"]
    assert (envelope["compaction"]["envelope_json_bytes"]
            - old_envelope["compaction"]["envelope_json_bytes"]) <= 360
    envelope["replan_action_packet"].pop("planning_guidance")
    assert envelope["replan_action_packet"] == old_envelope["replan_action_packet"]
