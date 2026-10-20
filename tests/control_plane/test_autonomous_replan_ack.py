from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.goals.goal_vision import normalize_goal_vision_packet
from loopx.control_plane.work_items.autonomous_replan_ack import (
    AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW,
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
    semantic_delta = semantic_delta_from_writeback(
        obligation={
            "obligation_id": "replan-3338",
            "satisfying_semantic_outcomes": ["new_surface"],
        },
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "work_item_id": "todo-replan-3338",
            "surface_id": "surface-new",
            "result_class": "advanced",
            "evidence_ids": ["evidence:new-surface"],
        },
        agent_vision=_validated_agent_vision(path_disposition),
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
