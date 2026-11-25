from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.goals.goal_vision import normalize_goal_vision_packet
from loopx.control_plane.work_items.autonomous_replan_ack import (
    AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW,
    AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT,
    attach_historical_ack_writeback_order,
    autonomous_replan_ack_candidates_for_obligation,
    compact_autonomous_replan_ack,
    latest_autonomous_replan_ack_for_projection,
)
from loopx.control_plane.work_items.progress_observation import (
    semantic_delta_from_writeback,
)
from loopx.status import compact_run


FRESH_VISION_PATH_OUTCOME = "fresh_vision_path_outcome"
GOAL_ID = "goal-replan-3338"
AGENT_ID = "codex-replan-agent"
NEUTRAL_CLASSIFICATIONS = {"quota_slot_spent"}
LONG_CHAIN_REVISION = "todo_frontier_revision_v0:0123456789abcdef01234567"
LONG_CHAIN_OBLIGATION_ID = "replan-0123456789abcdef"


def _material_run(index: int) -> dict[str, Any]:
    return {
        "classification": f"bounded_progress_{index}",
        "generated_at": f"2026-08-19T12:{index:02d}:00+08:00",
        "agent_id": AGENT_ID,
    }


def _long_chain_ack_run(*, checkpoints: bool) -> dict[str, Any]:
    semantic_delta: dict[str, Any] = {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": True,
        "outcomes": ["new_surface"],
        "satisfying_outcomes": ["new_surface"],
        "required_any_of": ["new_surface"],
        "trigger_kinds": ["long_todo_chain"],
        "obligation_id": LONG_CHAIN_OBLIGATION_ID,
    }
    if checkpoints:
        semantic_delta["trigger_checkpoints"] = [
            {"kind": "long_todo_chain", "frontier_revision": LONG_CHAIN_REVISION}
        ]
    return {
        "classification": "bounded_replan_progress",
        "generated_at": "2026-08-19T13:00:00+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state_semantic_delta",
            "semantic_delta": semantic_delta,
        },
    }


def _projection_ack(*, checkpoints: bool, newer_material_runs: int) -> dict[str, Any] | None:
    """Newest-first history: fresh material runs, then the ACK, then older runs."""

    latest_runs = [_material_run(index) for index in range(newer_material_runs)]
    latest_runs.append(_long_chain_ack_run(checkpoints=checkpoints))
    latest_runs.extend(_material_run(index) for index in range(100, 103))
    return latest_autonomous_replan_ack_for_projection(
        latest_runs,
        neutral_classifications=NEUTRAL_CLASSIFICATIONS,
    )


def test_checkpoint_ack_stays_visible_beyond_the_material_run_window() -> None:
    # The frontier rules compare the exact revision, so an old checkpoint ACK
    # cannot suppress a changed frontier; it must not expire on run count alone.
    ack = _projection_ack(
        checkpoints=True,
        newer_material_runs=AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW * 2,
    )

    assert ack is not None
    assert ack["semantic_delta"]["obligation_id"] == LONG_CHAIN_OBLIGATION_ID
    assert ack["semantic_delta"]["trigger_checkpoints"] == [
        {"kind": "long_todo_chain", "frontier_revision": LONG_CHAIN_REVISION}
    ]


def test_legacy_ack_still_expires_with_the_material_run_window() -> None:
    expired = _projection_ack(
        checkpoints=False,
        newer_material_runs=AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW,
    )
    visible = _projection_ack(checkpoints=False, newer_material_runs=2)

    assert expired is None
    assert visible is not None
    assert visible["semantic_delta"]["obligation_id"] == LONG_CHAIN_OBLIGATION_ID


def test_obligation_recovery_candidates_ignore_newer_unrelated_ack_and_are_bounded() -> None:
    def run(index: int, obligation_id: str, *, goal_id: str = GOAL_ID) -> dict[str, Any]:
        return {
            "goal_id": goal_id,
            "agent_id": AGENT_ID,
            "turn_instance_id": f"guided-start:{index}",
            "generated_at": f"2026-08-19T14:{index:02d}:00+08:00",
            "classification": "bounded_replan_progress",
            "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0",
                "recorded": True,
                "source": "refresh_state_semantic_delta",
                "semantic_delta": {
                    "schema_version": "replan_semantic_delta_v0",
                    "accepted": True,
                    "outcomes": ["fresh_vision_path_outcome"],
                    "satisfying_outcomes": ["fresh_vision_path_outcome"],
                    "obligation_id": obligation_id,
                },
            },
        }

    newest_first = [
        run(59, "replan-ffffffffffffffff"),
        run(58, LONG_CHAIN_OBLIGATION_ID, goal_id="other-goal"),
        *(run(index, LONG_CHAIN_OBLIGATION_ID) for index in range(57, 40, -1)),
    ]

    candidates = autonomous_replan_ack_candidates_for_obligation(
        newest_first,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
    )

    assert len(candidates) == AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT
    assert [candidate["turn_instance_id"] for candidate in candidates] == [
        f"guided-start:{index}" for index in range(57, 49, -1)
    ]
    assert all(
        candidate["semantic_delta"]["obligation_id"] == LONG_CHAIN_OBLIGATION_ID
        for candidate in candidates
    )


def test_noop_review_records_do_not_expire_an_applicable_legacy_ack() -> None:
    from loopx.control_plane.status.autonomous_replan_projection import (
        latest_autonomous_replan_ack_for_projection as project_current_ack,
    )

    prior = _long_chain_ack_run(checkpoints=False)
    for classification in ("replan_noop", "repair_noop"):
        observations = [{**_material_run(index), "classification": classification}
                        for index in range(AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW * 2)]
        ack = project_current_ack([*observations, prior])
        assert ack is not None
        assert ack["semantic_delta"]["obligation_id"] == LONG_CHAIN_OBLIGATION_ID
        actual_work = [_material_run(index) for index in range(AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW)]
        assert project_current_ack([*observations, *actual_work, prior]) is None


def _validated_agent_vision(path_outcome: str) -> dict[str, Any]:
    return normalize_goal_vision_packet(
        {
            "agent_id": AGENT_ID,
            "state": "active",
            "vision_patch": {
                "acceptance_summary": "Keep the bounded path decision auditable."
            },
            "path_delta": {
                "schema_version": "goal_path_delta_v0",
                "outcome": path_outcome,
                "prior_assumption": "The current path remains sufficient.",
                "observed_reality": "The latest evidence supports this disposition.",
                "retained": ["Keep the validated evidence boundary."],
                "evidence_refs": ["evidence:replan-3338"],
            },
        },
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )


def _accepted_ack_run(
    *,
    path_outcome: str | None,
    satisfying_outcomes: list[str] | None = None,
    outcomes: list[str] | None = None,
    semantic_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_satisfying_outcomes = (
        satisfying_outcomes
        if satisfying_outcomes is not None
        else [FRESH_VISION_PATH_OUTCOME]
    )
    resolved_outcomes = outcomes if outcomes is not None else resolved_satisfying_outcomes
    resolved_semantic_delta = semantic_delta or {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": True,
        "outcomes": resolved_outcomes,
        "satisfying_outcomes": resolved_satisfying_outcomes,
        "required_any_of": resolved_satisfying_outcomes,
        "obligation_id": "replan-3338",
    }
    run: dict[str, Any] = {
        "classification": "bounded_replan_progress",
        "generated_at": "2026-08-19T12:00:00+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state_semantic_delta",
            "semantic_delta": resolved_semantic_delta,
        },
    }
    if path_outcome is not None:
        if path_outcome == "unexpected":
            run["agent_vision"] = {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": AGENT_ID,
                "path_delta": {"outcome": path_outcome},
            }
        else:
            run["agent_vision"] = _validated_agent_vision(path_outcome)
    return run


@pytest.mark.parametrize("path_disposition", ["continue", "no_change", "replan"])
def test_compact_ack_projects_validated_path_disposition(
    path_disposition: str,
) -> None:
    from loopx.control_plane.work_items.progress_observation import build_replan_context
    from loopx.control_plane.work_items.replan_semantics_codec import project_replan_semantics

    obligation = {
        "obligation_id": "replan-3338000000000000",
        "satisfying_semantic_outcomes": ["new_surface"],
    }
    obligation["replan_context"] = build_replan_context(
        obligation, goal_id=GOAL_ID, agent_id=AGENT_ID, newest_first_runs=[],
    )
    vision = _validated_agent_vision(path_disposition)
    vision["stride_decision"] = project_replan_semantics({
        "operation": "requirements", "obligation": obligation,
    })["stride_control"]["minimal_example"]
    semantic_delta = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "work_item_id": "todo-replan-3338",
            "surface_id": "surface-new",
            "result_class": "advanced",
            "evidence_ids": ["evidence:new-surface"],
        },
        agent_vision=vision,
    )

    assert semantic_delta["accepted"] is True
    assert semantic_delta["outcomes"] == [
        "new_surface",
        FRESH_VISION_PATH_OUTCOME,
    ]
    assert semantic_delta["satisfying_outcomes"] == ["new_surface"]
    compact = compact_autonomous_replan_ack(
        _accepted_ack_run(
            path_outcome=path_disposition,
            semantic_delta=semantic_delta,
        )
    )

    assert compact is not None
    assert compact["path_disposition"] == path_disposition
    assert compact["semantic_delta"]["satisfying_outcomes"] == ["new_surface"]
    assert "path_disposition" not in compact["semantic_delta"]


@pytest.mark.parametrize(
    ("path_outcome", "satisfying_outcomes"),
    [
        (None, [FRESH_VISION_PATH_OUTCOME]),
        ("wait", [FRESH_VISION_PATH_OUTCOME]),
        ("unexpected", [FRESH_VISION_PATH_OUTCOME]),
        ("replan", ["new_concrete_blocker"]),
    ],
)
def test_compact_ack_does_not_invent_path_disposition(
    path_outcome: str | None,
    satisfying_outcomes: list[str],
) -> None:
    compact = compact_autonomous_replan_ack(
        _accepted_ack_run(
            path_outcome=path_outcome,
            satisfying_outcomes=satisfying_outcomes,
        )
    )

    assert compact is not None
    assert "path_disposition" not in compact


def test_compact_run_exposes_path_disposition_to_status_consumers() -> None:
    compact = compact_run(_accepted_ack_run(path_outcome="replan"))

    assert compact["autonomous_replan_ack"]["path_disposition"] == "replan"


def test_long_chain_trigger_checkpoint_survives_ack_compaction() -> None:
    frontier_revision = "todo_frontier_revision_v0:0123456789abcdef01234567"
    semantic_delta = semantic_delta_from_writeback(
        obligation={
            "obligation_id": "replan-3338",
            "satisfying_semantic_outcomes": ["new_surface"],
            "triggers": [
                {
                    "kind": "long_todo_chain",
                    "frontier_revision": frontier_revision,
                }
            ],
        },
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "work_item_id": "todo-replan-3338",
            "surface_id": "surface-new",
            "result_class": "advanced",
            "evidence_ids": ["evidence:new-surface"],
        },
    )

    compact = compact_autonomous_replan_ack(
        _accepted_ack_run(path_outcome=None, semantic_delta=semantic_delta)
    )

    assert compact is not None
    assert compact["semantic_delta"]["trigger_checkpoints"] == [
        {"kind": "long_todo_chain", "frontier_revision": frontier_revision}
    ]


def test_same_second_candidate_keeps_the_todo_bound_writeback_order() -> None:
    turn = "guided-start:later"
    run = {
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "turn_instance_id": turn,
        "todo_id": "todo-original",
        "settlement_identity": {"todo_id": "todo-original", "effect_id": "effect-later"},
        "autonomous_replan_ack": {"semantic_delta": {"obligation_id": LONG_CHAIN_OBLIGATION_ID}},
    }
    admission = {"event_kind": "quota_should_run", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "run_id": "guided-start:original", "recorded_at": "2026-08-01T00:00:01.500000Z"}
    # A Todo-bound refresh leaves the obligation field empty. The semantic
    # delta and the Todo receipt identify the writeback.
    todo_event = {"event_kind": "refresh_state", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "run_id": turn, "todo_id": "todo-original", "recorded_at": "2026-08-01T00:00:01.900000Z",
        "details": {"autonomous_replan_recorded": True, "replan_obligation_id": None,
            "settlement_effect_id": "effect-later"}}
    other_obligation = {"event_kind": "refresh_state", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "run_id": turn, "recorded_at": "2026-08-01T00:00:01.100000Z", "details": {
            "autonomous_replan_recorded": True, "replan_obligation_id": "replan-ffffffffffffffff"}}
    candidate = {"turn_instance_id": turn, "generated_at": "2026-08-01T00:00:01Z"}
    attach_historical_ack_writeback_order(
        [candidate], [other_obligation, admission, todo_event], goal_id=GOAL_ID, agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=admission["recorded_at"], guard_turn_instance_id="guided-start:original",
        runs=[run],
    )
    assert candidate["writeback_recorded_at"] == "2026-08-01T00:00:01.900000Z"
    assert candidate["writeback_follows_admission"] is True
    assert candidate["writeback_obligation_id"] == LONG_CHAIN_OBLIGATION_ID
    mismatched = {"turn_instance_id": turn}
    mismatched_event = {**todo_event, "details": {**todo_event["details"], "settlement_effect_id": "effect-other"}}
    attach_historical_ack_writeback_order(
        [mismatched], [admission, mismatched_event], goal_id=GOAL_ID, agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=admission["recorded_at"], guard_turn_instance_id="guided-start:original",
        runs=[run],
    )
    assert "writeback_recorded_at" not in mismatched
    sparse_run = {
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "turn_instance_id": turn,
        "autonomous_replan_ack": {"semantic_delta": {"obligation_id": LONG_CHAIN_OBLIGATION_ID}},
    }
    later_receipt = {"event_kind": "quota_should_run", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "run_id": turn, "todo_id": "todo-original", "recorded_at": "2026-08-01T00:00:01.200000Z",
        "details": {"settlement_effect_id": "effect-later", "todo_id": "todo-original"}}
    filled = {"turn_instance_id": turn}
    attach_historical_ack_writeback_order(
        [filled], [admission, later_receipt, todo_event], goal_id=GOAL_ID, agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=admission["recorded_at"], guard_turn_instance_id="guided-start:original",
        runs=[sparse_run],
    )
    assert filled["writeback_recorded_at"] == todo_event["recorded_at"]
    foreign = {"turn_instance_id": turn}
    foreign_event = {**todo_event, "todo_id": "todo-other",
        "details": {**todo_event["details"], "settlement_effect_id": "effect-other"}}
    attach_historical_ack_writeback_order(
        [foreign], [admission, later_receipt, foreign_event], goal_id=GOAL_ID, agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=admission["recorded_at"], guard_turn_instance_id="guided-start:original",
        runs=[sparse_run],
    )
    assert "writeback_recorded_at" not in foreign
    blank = {"turn_instance_id": turn}
    blank_event = {**todo_event, "todo_id": "", "details": {
        "autonomous_replan_recorded": True, "replan_obligation_id": None, "settlement_effect_id": None}}
    attach_historical_ack_writeback_order(
        [blank], [admission, later_receipt, blank_event], goal_id=GOAL_ID, agent_id=AGENT_ID,
        obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=admission["recorded_at"], guard_turn_instance_id="guided-start:original",
        runs=[sparse_run],
    )
    assert "writeback_recorded_at" not in blank


def test_exact_goal_instance_same_second_ack_uses_its_own_writeback(tmp_path: Path) -> None:
    """Two instances can mint one obligation id and reuse the ACK Turn.

    The exact owner records its same-second ACK only from its own refresh.
    A retired instance's earlier refresh, and an alias row with no GoalRef,
    stay out of that order.
    """
    from loopx.control_plane.work_items.autonomous_replan_ack import (
        load_receipt_replan_ack_candidates,
    )
    from loopx.control_plane.work_items.autonomous_replan_obligation import (
        ensure_replan_novelty_policy,
    )
    from loopx.control_plane.work_items.replan_semantics_codec import (
        project_replan_semantics,
    )
    from loopx.rollout_event_log import build_rollout_event

    obligation_source = {
        "schema_version": "autonomous_replan_obligation_v0",
        "agent_id": AGENT_ID,
        "frontier_identity": "shared-frontier",
        "triggers": [{"kind": "long_todo_chain", "frontier_revision": LONG_CHAIN_REVISION}],
    }
    obligation = ensure_replan_novelty_policy(dict(obligation_source))["obligation_id"]
    assert obligation == ensure_replan_novelty_policy({
        **obligation_source, "goal_instance_id": "ginst_" + "b" * 32,
    })["obligation_id"]
    current = {"goal_id": GOAL_ID, "goal_instance_id": "ginst_" + "b" * 32}
    retired = {"goal_id": GOAL_ID, "goal_instance_id": "ginst_" + "a" * 32}
    guard_turn = "guided-start:original"
    ack_turn = "guided-start:later"
    admitted_at = "2026-08-01T00:00:01.500000Z"
    runtime = tmp_path
    log_path = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True)

    def ack_run(goal_ref: dict[str, str], generated_at: str, outcome: str) -> dict[str, Any]:
        return {
            "goal_id": GOAL_ID, "agent_id": AGENT_ID, "turn_instance_id": ack_turn,
            "generated_at": generated_at, "goal_ref": goal_ref,
            "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "source": "refresh_state",
                "semantic_delta": {
                    "schema_version": "replan_semantic_delta_v0", "accepted": True,
                    "obligation_id": obligation, "outcomes": [outcome],
                    "satisfying_outcomes": [outcome],
                },
            },
        }

    index_path.write_text("".join(json.dumps(row) + "\n" for row in (
        ack_run(current, "2026-08-01T00:00:01Z", FRESH_VISION_PATH_OUTCOME),
        ack_run(retired, "2026-08-01T00:00:03Z", "new_surface"),
    )), encoding="utf-8")
    refresh = {
        "autonomous_replan_recorded": True, "replan_obligation_id": obligation,
    }
    events = [
        build_rollout_event(
            goal_id=GOAL_ID, event_kind="refresh_state", agent_id=AGENT_ID,
            run_id=ack_turn, recorded_at="2026-08-01T00:00:00.100000Z", details=refresh,
        ),
        build_rollout_event(
            goal_id=GOAL_ID, event_kind="refresh_state", goal_ref=retired,
            agent_id=AGENT_ID, run_id=ack_turn, recorded_at="2026-08-01T00:00:00.200000Z",
            details=refresh,
        ),
        build_rollout_event(
            goal_id=GOAL_ID, event_kind="quota_should_run", goal_ref=current,
            agent_id=AGENT_ID, run_id=guard_turn, recorded_at=admitted_at,
        ),
        build_rollout_event(
            goal_id=GOAL_ID, event_kind="refresh_state", goal_ref=current,
            agent_id=AGENT_ID, run_id=ack_turn, recorded_at="2026-08-01T00:00:01.900000Z",
            details=refresh,
        ),
    ]
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    def decision_for(goal_ref: dict[str, str] | None) -> dict[str, Any]:
        candidates = load_receipt_replan_ack_candidates(
            runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=obligation,
            guard_recorded_at=admitted_at, guard_turn_instance_id=guard_turn,
            goal_ref=goal_ref,
        )
        return project_replan_semantics({
            "operation": "receipt_bound_obligation", "guard_scoped": True,
            "replay_phase": "open", "selected_obligation_id": obligation,
            "goal_id": GOAL_ID, "agent_id": AGENT_ID, "guard_recorded_at": admitted_at,
            "guard_turn_instance_id": guard_turn, "current_obligation": None,
            "transition_candidates": [
                {"historical_ack": candidate} for candidate in candidates
            ],
        })

    packet = decision_for(current)["obligation"]["settlement_action_packet"]
    assert packet["decision"] == "historical_ack_recorded"
    assert packet["semantic_delta"]["outcomes"] == [FRESH_VISION_PATH_OUTCOME]
    with pytest.raises(Exception, match="conflicts with the current autonomous replan"):
        decision_for(None)


def test_same_turn_receipt_upgrade_keeps_writeback_after_original_admission() -> None:
    """A later same-Turn upgrade must not become the admission log index.

    The compact ACK stamp is the admission second. Its refresh writeback is
    after the original quota_should_run and before the upgrade. Passing the
    upgrade's recorded_at, which is what the latest receipt exposes, still
    has to order that writeback after the first bound receipt.
    """

    turn = "guided-start:later"
    guard_turn = "guided-start:original"
    binding = {
        "replan_obligation_id": LONG_CHAIN_OBLIGATION_ID,
        "settlement_effect_id": "effect-original",
    }
    retired = {
        "event_kind": "quota_should_run", "event_id": "evt-retired",
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "run_id": guard_turn,
        "recorded_at": "2026-07-01T00:00:00.000000Z",
        "goal_ref": {
            "goal_id": GOAL_ID,
            "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        },
        "details": {
            "replan_obligation_id": "replan-ffffffffffffffff",
            "settlement_effect_id": "effect-retired",
        },
    }
    admission = {
        "event_kind": "quota_should_run", "event_id": "evt-admission",
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "run_id": guard_turn,
        "recorded_at": "2026-08-01T00:00:01.500000Z", "details": binding,
    }
    upgrade = {
        "event_kind": "quota_should_run", "event_id": "evt-upgrade",
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "run_id": guard_turn,
        "recorded_at": "2026-08-01T00:00:05.000000Z",
        "causality": {"source_event_id": "evt-admission", "caused_by": "evt-admission"},
        "details": {**binding, "settlement_receipt_revision": "work_admission"},
    }
    todo_event = {
        "event_kind": "refresh_state", "goal_id": GOAL_ID, "agent_id": AGENT_ID,
        "run_id": turn, "recorded_at": "2026-08-01T00:00:01.900000Z",
        "details": {
            "autonomous_replan_recorded": True,
            "replan_obligation_id": LONG_CHAIN_OBLIGATION_ID,
        },
    }
    candidate = {"turn_instance_id": turn, "generated_at": "2026-08-01T00:00:01Z"}
    attach_historical_ack_writeback_order(
        [candidate], [retired, admission, todo_event, upgrade],
        goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=LONG_CHAIN_OBLIGATION_ID,
        guard_recorded_at=upgrade["recorded_at"], guard_turn_instance_id=guard_turn,
        admission_event_id=admission["event_id"],
    )
    assert candidate["writeback_recorded_at"] == todo_event["recorded_at"]
    assert candidate["writeback_follows_admission"] is True
    assert candidate["writeback_obligation_id"] == LONG_CHAIN_OBLIGATION_ID
