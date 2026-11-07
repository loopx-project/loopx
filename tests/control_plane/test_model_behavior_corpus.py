from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.testing.model_behavior_corpus import (
    build_model_behavior_corpus,
    run_model_behavior_corpus,
)
from loopx.control_plane.testing.model_behavior_qualification import (
    MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
    MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
    model_behavior_semantic_contract_from_packet,
    run_model_behavior_qualification_pair,
)


STATE_MATRIX = json.loads(
    (
        Path(__file__).parents[1] / "fixtures" / "turn_envelope_state_matrix.json"
    ).read_text(encoding="utf-8")
)


def _full_packet() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "should-run",
        "goal_id": "fixture-goal",
        "decision": "run",
        "should_run": True,
        "effective_action": "normal_run",
        "state": "eligible",
        "normal_delivery_allowed": True,
        "action_required": False,
        "open_count": 0,
        "recommended_action": "Implement one bounded public-safe slice.",
        "selected_todo": {
            "todo_id": "todo_fixture001",
            "status": "open",
            "task_class": "advancement_task",
            "claimed_by": "codex-fixture",
            "text": "Implement one bounded public-safe slice.",
        },
        "agent_identity": {"agent_id": "codex-fixture"},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "primary_action": "Implement one bounded public-safe slice.",
            },
            "cli_channel": {
                "next_cli_actions": [
                    "loopx refresh-state --goal-id fixture-goal",
                    "loopx quota spend-slot --goal-id fixture-goal --execute",
                ],
                "spend_allowed_now": False,
                "spend_after_validation": True,
            },
            "required_reads": [{"kind": "repository", "command": "git status --short"}],
        },
        "goal_boundary": {
            "write_scope": ["loopx/**", "tests/**"],
            "guards": ["stop before external writes"],
        },
        "scheduler_hint": {"action": "run_now"},
        "automation_liveness": {
            "keep_active": True,
            "pause_allowed": False,
            "automation_action": "execute_bounded_work",
        },
    }


def _decision(**patch: Any) -> dict[str, Any]:
    decision = {
        "schema_version": MODEL_BEHAVIOR_DECISION_SCHEMA_VERSION,
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
    decision.update(patch)
    return decision


def _actor(request: Mapping[str, Any]) -> dict[str, Any]:
    arm = str(request["arm"])
    packet = request["packet"]
    signature = (
        quota_action_signature_document(packet)
        if arm == "full_packet"
        else turn_envelope_action_signature_document(packet)
    )
    response_plan = dict(signature.get("response_plan") or {})
    gate_patch = (
        {
            "decision": "ask_user",
            "selected_todo_id": None,
            "user_action_required": True,
            "must_attempt_work": False,
            "delivery_allowed": False,
            "quiet_noop_allowed": False,
            "intended_action_kinds": response_plan["action_sequence"],
        }
        if response_plan.get("kind") == "surface_user_gate"
        else {}
    )
    return {
        "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
        "actor_ref": "fixture-model-v1",
        "decision": _decision(
            **gate_patch,
            semantic_contract=model_behavior_semantic_contract_from_packet(
                packet,
                arm=arm,
            ),
        ),
        "tool_calls": [],
    }


def test_corpus_covers_matrix_retained_counterfactual_and_ablation() -> None:
    base = _full_packet()
    corpus = build_model_behavior_corpus(
        base,
        state_matrix=STATE_MATRIX,
        retained_packets=[{"case_id": "retained-001", "packet": base}],
        counterfactuals=[
            {
                "case_id": "counterfactual-warning-001",
                "patch": {"recommended_action": "Inspect one actionable warning."},
            }
        ],
        candidate_ablations=[
            {
                "case_id": "ablation-delivery-allowed-001",
                "path": "action.delivery_allowed",
            }
        ],
    )

    result = run_model_behavior_corpus(corpus, actor=_actor, repeats=3, seed=7)

    assert result["case_count"] == len(STATE_MATRIX["cases"]) + 3
    assert result["all_cases_passed"] is True
    assert result["corpus_gate_passed"] is True
    assert result["promotion_eligible"] is False
    assert result["promotion_blockers"] == [
        "repeated_live_model_evidence_required",
        "explicit_owner_review_required",
    ]
    assert result["coverage"]["graded_semantic_contract"] == [
        "concrete_user_question",
        "required_reads",
        "gate_or_stop",
        "peer_route",
        "write_scope",
        "spend_rule",
        "scheduler_action",
        "vision_continuation",
        "planning_horizon",
        "actionable_warnings",
    ]
    assert result["coverage"]["ungraded_required_dimensions"] == []
    ablation = next(
        case for case in result["cases"] if case["source_kind"] == "candidate_ablation"
    )
    assert ablation["passed"] is True
    assert {run["status"] for run in ablation["runs"]} == {"fail_closed"}
    observed_orders = {
        tuple(run["arm_order"])
        for case in result["cases"]
        if case["source_kind"] != "candidate_ablation"
        for run in case["runs"]
    }
    assert observed_orders == {
        ("full_packet", "candidate_packet"),
        ("candidate_packet", "full_packet"),
    }
    encoded = json.dumps(result, sort_keys=True)
    assert "Implement one bounded public-safe slice" not in encoded
    assert "Inspect one actionable warning" not in encoded
    assert result["persistence_boundary"]["raw_packets_persisted"] is False


def test_corpus_reports_hard_and_stochastic_drift_separately() -> None:
    def drifting_actor(request: Mapping[str, Any]) -> dict[str, Any]:
        patch = (
            {
                "decision": "wait",
                "must_attempt_work": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
                "intended_action_kinds": ["wait"],
                "reason_codes": ["candidate_waited"],
            }
            if request["arm"] == "candidate_packet"
            else {}
        )
        return {
            "schema_version": MODEL_BEHAVIOR_ACTOR_RESULT_SCHEMA_VERSION,
            "actor_ref": "fixture-model-v1",
            "decision": _decision(
                **patch,
                semantic_contract=model_behavior_semantic_contract_from_packet(
                    request["packet"],
                    arm=str(request["arm"]),
                ),
            ),
            "tool_calls": [],
        }

    corpus = build_model_behavior_corpus(
        _full_packet(),
        retained_packets=[{"case_id": "retained-drift-001", "packet": _full_packet()}],
    )
    result = run_model_behavior_corpus(corpus, actor=drifting_actor, repeats=2)

    assert result["all_cases_passed"] is False
    run = result["cases"][0]["runs"][0]
    assert set(run["hard_invariant_drift_fields"]) == {
        "decision",
        "must_attempt_work",
        "delivery_allowed",
        "quiet_noop_allowed",
    }
    assert run["behavior_signal_drift_fields"] == ["intended_action_kinds"]
    assert set(run["stochastic_drift_fields"]) == {
        "first_action_kind",
        "trajectory_action_kinds",
    }


def test_pair_recomputes_semantics_after_candidate_ablation() -> None:
    full = _full_packet()
    candidate = build_turn_envelope(full)
    del candidate["action"]["delivery_allowed"]
    assert candidate["action_signature"]["matches"] is True

    try:
        run_model_behavior_qualification_pair(
            full,
            candidate,
            qualification_id="ablation-lineage-001",
            actor=_actor,
        )
    except ValueError as exc:
        assert "action semantics drift" in str(exc)
    else:
        raise AssertionError("candidate ablation must fail before actor execution")


def test_fail_closed_expectation_does_not_hide_invalid_actor_output() -> None:
    corpus = build_model_behavior_corpus(
        _full_packet(),
        retained_packets=[{"case_id": "invalid-actor-001", "packet": _full_packet()}],
    )
    corpus["cases"][0]["expected_outcome"] = "fail_closed"

    def invalid_actor(_: Mapping[str, Any]) -> dict[str, Any]:
        return {"schema_version": "unknown_actor_result_v9"}

    result = run_model_behavior_corpus(corpus, actor=invalid_actor, repeats=2)

    assert result["all_cases_passed"] is False
    assert result["cases"][0]["passed"] is False
    for run in result["cases"][0]["runs"]:
        assert run["status"] == "actor_failed"
        assert run["actor_error_code"] == "actor_result_invalid"
        assert run["failed_arm"] in {"full_packet", "candidate_packet"}
        assert run["safety_violations"] == [
            f"actor_arm_failed:{run['failed_arm']}:actor_result_invalid"
        ]
