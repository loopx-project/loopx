from __future__ import annotations

import copy
import hashlib
import json
import shlex
import subprocess
import sys

import pytest

from loopx.control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, project_captured_replan_cli_view, quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.testing.control_plane_composition_scenarios import (
    _required_vision_replan_source,
)

from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
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


def _quota_payload(*, include_legacy_receipt: bool = False, stride_strategy: str = "off") -> dict[str, object]:
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
        project_asset_extra={"autonomous_replan_obligation": obligation,
            **({"execution_profile": {"stride_strategy": stride_strategy, "replan_after_effective_turns": 6}} if stride_strategy != "off" else {})},
    )
    return build_quota_should_run(
        status_payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )


@pytest.mark.parametrize("complete", [False, True], ids=["historical", "complete-v1"])
def test_staged_replan_preserves_source_numbers_and_distinct_outcome_refs(tmp_path, complete) -> None:
    def encoded(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def legacy_ref(normalized: dict[str, object]) -> str:
        return "sha256:" + hashlib.sha256(encoded(normalized).encode()).hexdigest()

    capture = tmp_path / "decision.json"
    source = _quota_payload()
    source["replan_read_path"] = str(capture)
    exact = [1.0, -0.0, 2**53 + 1]
    rounded = [1, 0, 2**53]
    outcomes = [
        {"id": "outcome", "revision": 1, "requirements": [{"values": values}]}
        for values in (exact, rounded)
    ]
    separate = {**outcomes[0], "id": "unambiguous"}
    context = source["autonomous_replan_obligation"]["replan_context"]
    core = {
        "evidence_value": 2**53 + 1,
        "acceptance_contract": {"outcome_requirements": {
            "revision": 2**53 + 1, "outcomes": [*outcomes, separate],
        }},
    }
    context["core_goal"] = core
    source["agent_todo_summary"]["goal_acceptance_contract"] = copy.deepcopy(core["acceptance_contract"])
    context["explore_basis"] = {"availability": "available", "revision": -0.0, "values": exact}
    context["decision_followthrough"] = {"recorded_progress_count": 2**53 + 1}
    context["evidence"][0]["occurrences"] = 1.0
    source["planning_horizon"] = {"core_goal": {**core, "evidence_value": 2**53}}
    source["replan_action_packet"]["writeback_contract"] = {
        "vision_authoring": {"sample_count": 1.0},
        "captured_scalar": -0.0,
        "captured_values": exact,
    }
    if complete:
        from loopx.control_plane.quota.turn_envelope import compose_turn_decision

        source = compose_turn_decision(source)
    raw = encoded(source).encode()
    capture.write_bytes(raw)
    envelope = build_turn_envelope(source)
    digest = envelope["action_signature"]["source_decision_hash"]
    registry = tmp_path / "absent-registry.json"
    runtime = tmp_path / "absent-runtime"
    cli = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
           "--runtime-root", str(runtime), "--format", "json"]

    def read(*args: str, expected: int = 0):
        process = subprocess.run([*cli, *args], capture_output=True, text=True, timeout=15)
        result = json.loads(process.stdout)
        assert process.returncode == expected, result
        return result

    route = ("quota", "replan-view", "--decision-file", str(capture), "--source-hash", digest)
    review = read(*route, "--replan-stage", "review")
    assert review["core_goal"]["evidence_value"] == 2**53 + 1
    assert review["planning_horizon"]["core_goal"]["evidence_value"] == 2**53
    assert review["decision_followthrough"]["observation_count"] == 2**53 + 1
    assert encoded(review["evidence"][0]["occurrences"]) == "1.0"
    assert encoded(review["capabilities"]["explore"]["revision"]) == "-0.0"
    index = review["acceptance_index"]
    refs = [row["outcome_ref"] for row in index["outcomes"]]
    assert len(set(refs)) == 3
    assert envelope["action"]["core_goal"]["outcome_requirements_index"]["outcome_refs"] == refs
    saved_entry = project_captured_replan_cli_view(source)
    saved_index = saved_entry["agent_todo_summary"]["outcome_requirements_index"]
    assert saved_index["outcome_refs"] == refs
    assert saved_index["revision"] == 2**53 + 1
    for ref, outcome in zip(refs, [*outcomes, separate], strict=True):
        detail = shlex.split(index["read_action_template"].replace("{outcome_ref}", ref))
        assert detail[detail.index("quota"):][:2] == ["quota", "replan-view"]
        assert encoded(read(*detail[detail.index("quota"):])["outcome"]) == encoded(outcome)

    old_ambiguous = read(*route, "--replan-stage", "acceptance", "--outcome-ref",
                         legacy_ref(outcomes[1]), expected=1)
    assert not old_ambiguous["ok"] and "ambiguous" in old_ambiguous["reason"]
    old_separate = legacy_ref({**outcomes[1], "id": "unambiguous"})
    assert refs[-1] == old_separate
    assert encoded(read(*route, "--replan-stage", "acceptance", "--outcome-ref", old_separate)["outcome"]) == encoded(separate)

    explore = read(*route, "--replan-stage", "explore")
    assert encoded(explore["explore_basis"]) == encoded(context["explore_basis"])
    writer = read(*route, "--replan-stage", "writeback")
    assert encoded(writer["writeback_contract"]) == encoded(source["replan_action_packet"]["writeback_contract"])
    assert capture.read_bytes() == raw
    assert not registry.exists() and not runtime.exists()


def test_quota_delivers_coverage_context_and_minimal_replan_action() -> None:
    payload = _quota_payload()

    assert payload["decision"] == "autonomous_replan_required"
    assert payload.get("required_reads") in (None, [])
    obligation = payload["autonomous_replan_obligation"]
    context = obligation["replan_context"]
    action = payload["replan_action_packet"]
    assert context["evidence_source"] == "compact_run_history"
    assert context["delivery"] == "host_projected"
    assert context["delivery_receipt"] == {
        "schema_version": "replan_context_delivery_receipt_v0",
        "context_id": context["context_id"],
        "obligation_id": obligation["obligation_id"],
        "status": "delivered",
        "delivered_by": "quota_host_projection",
    }
    assert context["coverage_ledger"][0]["surface_id"] == "surface-existing"
    assert 0 < len(action["planning_guidance"]) <= 3
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


def test_replan_obligation_outlives_quiet_monitor_and_scoped_user_gate() -> None:
    monitor = quota_todo_item(
        todo_id="todo_waiting_monitor",
        title="Watch a pending review.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        target_key="github:example/project:pr:123",
        cadence="30m",
        next_due_at="2099-01-01T00:00:00+00:00",
    )
    gate = quota_todo_item(
        todo_id="todo_test_scope_gate",
        role="user",
        title="Provide a non-production test scope.",
        task_class="user_gate",
        blocks_agent=AGENT_ID,
    )
    status = quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        quota_state="operator_gate",
        safe_bypass=True,
        recommended_action="Wait for the test scope.",
        agent_todos=quota_todo_summary([monitor], role="agent"),
        user_todos=quota_todo_summary([gate], role="user"),
        project_asset_extra={"autonomous_replan_obligation": _obligation()},
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
    )

    packet = build_quota_should_run(
        status,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    assert packet["decision"] == "autonomous_replan_required"
    assert packet["work_lane_contract"]["obligation"] == "quiet_until_material_monitor_transition"
    assert packet["heartbeat_recommendation"]["recommended_mode"] == "ask_operator_gate"
    assert packet["execution_obligation"]["must_attempt_work"] is True
    assert packet["execution_obligation"]["kind"] == "autonomous_replan_required"
    assert packet["execution_obligation"]["stall_threshold"] == 2
    assert packet["interaction_contract"]["mode"] == "autonomous_replan"
    assert packet["interaction_contract"]["user_channel"]["notify"] == "NOTIFY"
    assert packet["interaction_contract"]["user_channel"]["actions"]
    assert packet["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert packet["selected_todo"] is None
    assert packet["scheduler_hint"]["action"] == "run_now"
    assert packet["scheduler_hint"].get("consistency_error") is None


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
    # Legacy callers without a capture keep their complete source guidance.
    # Transport equality, not dated wording, is the invariant here.
    assert guidance and all(isinstance(item, str) and item for item in guidance)
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
    # Semantic boundaries have a bounded allocation; the existing envelope
    # warning and executable authority remain independent of that advice.
    assert envelope["compaction"]["within_budget"] == old_envelope["compaction"]["within_budget"]
    assert (envelope["compaction"]["envelope_json_bytes"]
            - old_envelope["compaction"]["envelope_json_bytes"]) <= 3000
    envelope["replan_action_packet"].pop("planning_guidance")
    assert envelope["replan_action_packet"] == old_envelope["replan_action_packet"]


@pytest.mark.parametrize("enabled", [False, True])
def test_outer_explore_reader_runs_only_for_enabled_replan_context(monkeypatch, enabled):
    from loopx.capabilities.explore import turn_context

    calls = []
    def read_source(**kwargs):
        calls.append(kwargs)
        return {"ok": False, "goal_id": GOAL_ID, "agent_id": AGENT_ID}

    monkeypatch.setattr(turn_context, "read_replan_explore_context", read_source)
    result = _quota_payload(stride_strategy="evidence_conditioned_v1" if enabled else "off")
    assert result["replan_action_packet"]
    assert len(calls) == int(enabled)
    if enabled:
        assert calls[0]["goal_id"] == GOAL_ID and calls[0]["agent_id"] == AGENT_ID
        assert result["replan_action_packet"]["stride_control"]["explore_basis"]["availability"] == "unavailable"
    else:
        assert "stride_control" not in result["replan_action_packet"]
