from __future__ import annotations

from copy import deepcopy

from loopx.control_plane.runtime.run_context_retention import (
    GOAL_SEMANTIC_HISTORY_SCHEMA_VERSION,
    compact_goal_semantic_history,
    goal_semantic_history_from_runs,
    latest_runs_with_agent_context,
)
from loopx.control_plane.status.agent_lane_projection import (
    compact_agent_lane_status_payload_for_display,
)
from loopx.status import compact_run

AGENT_ID = "quality-agent"
OTHER_AGENT_ID = "other-agent"


def _run(
    generated_at: str,
    *,
    agent_id: str = AGENT_ID,
    classification: str = "state_refreshed",
    **fields: object,
) -> dict[str, object]:
    return {
        "generated_at": generated_at,
        "goal_id": "semantic-goal",
        "agent_id": agent_id,
        "classification": classification,
        **fields,
    }


def _checkpoint(
    *,
    decision: str,
    delivery_outcome: str | None = None,
) -> dict[str, object]:
    triggers = []
    if delivery_outcome:
        triggers.append(
            {
                "kind": "material_delivery_outcome",
                "delivery_outcome": delivery_outcome,
            }
        )
    return {
        "schema_version": "vision_checkpoint_v0",
        "agent_id": AGENT_ID,
        "required": True,
        "satisfied": True,
        "decision": decision,
        "triggers": triggers,
    }


def _agent_context(history: dict[str, object], agent_id: str) -> dict[str, object]:
    return next(
        context
        for context in history["agents"]  # type: ignore[index]
        if context["agent_id"] == agent_id
    )


def test_semantic_history_uses_independent_agent_slots() -> None:
    runs = [
        _run(
            "2026-08-09T00:05:00Z",
            vision_checkpoint=_checkpoint(decision="not_required"),
        ),
        _run(
            "2026-08-09T00:04:00Z",
            classification="quota_slot_spent",
        ),
        _run(
            "2026-08-09T00:03:00Z",
            classification="human_reward_recorded",
            human_reward={"decision": "correct_direction"},
        ),
        _run(
            "2026-08-09T00:02:00Z",
            classification="milestone_closeout",
            delivery_outcome="outcome_progress",
            vision_checkpoint=_checkpoint(
                decision="unchanged_with_reason",
                delivery_outcome="outcome_progress",
            ),
        ),
        _run(
            "2026-08-09T00:01:00Z",
            agent_vision={
                "agent_id": AGENT_ID,
                "state": "vision_replanned",
                "vision_patch": {"acceptance_summary": "Prove the outcome."},
            },
            autonomous_replan_ack={
                "recorded": True,
                "semantic_delta": {
                    "accepted": True,
                    "outcomes": ["fresh_vision_path_outcome"],
                },
            },
        ),
        _run(
            "2026-08-09T00:00:00Z",
            agent_id=OTHER_AGENT_ID,
            agent_vision={
                "agent_id": OTHER_AGENT_ID,
                "state": "vision_replanned",
                "vision_patch": {"acceptance_summary": "Other lane."},
            },
        ),
    ]

    semantic_history = goal_semantic_history_from_runs(runs)
    context = _agent_context(semantic_history, AGENT_ID)

    assert semantic_history["schema_version"] == GOAL_SEMANTIC_HISTORY_SCHEMA_VERSION
    assert context["latest_vision_checkpoint_run"] is runs[0]
    assert context["latest_outcome_vision_checkpoint_run"] is runs[3]
    assert context["latest_material_milestone_run"] is runs[3]
    assert context["latest_agent_vision_run"] is runs[4]
    assert context["latest_autonomous_replan_ack_run"] is runs[4]
    assert semantic_history["latest_owner_correction_run"] is runs[2]
    assert (
        _agent_context(semantic_history, OTHER_AGENT_ID)["latest_agent_vision_run"]
        is runs[5]
    )


def test_rejected_replan_feedback_survives_compaction_and_semantic_history() -> None:
    run = _run(
        "2026-08-09T00:06:00Z",
        classification="replan_noop",
        autonomous_replan_noop={
            "classification": "replan_noop",
            "requested_classification": "successor_replan_recorded",
        },
        autonomous_replan_ack={
            "recorded": False,
            "delta_contract": {
                "rejected_claims": [
                    {
                        "kind": "watch_lane_continuation",
                        "reason": "monitor is missing expires_at, resume_when, or watch_only",
                    }
                ]
            },
        },
    )

    compacted = compact_run(run)
    history = goal_semantic_history_from_runs([compacted])
    context = _agent_context(history, AGENT_ID)

    assert compacted["replan_ack_feedback"]["rejected_claims"][0]["kind"] == (
        "watch_lane_continuation"
    )
    assert context["latest_replan_ack_feedback_run"] is compacted


def test_rejected_ack_does_not_shadow_older_accepted_semantic_ack() -> None:
    accepted = _run(
        "2026-08-09T00:05:00Z",
        autonomous_replan_ack={
            "recorded": True,
            "semantic_delta": {
                "accepted": True,
                "outcomes": ["fresh_vision_path_outcome"],
            },
        },
    )
    rejected = _run(
        "2026-08-09T00:06:00Z",
        classification="replan_noop",
        autonomous_replan_ack={"recorded": False},
        replan_ack_feedback={
            "schema_version": "replan_ack_feedback_v0",
            "reason": "the attempted delta did not satisfy the obligation",
        },
    )

    context = _agent_context(
        goal_semantic_history_from_runs([rejected, accepted]),
        AGENT_ID,
    )

    assert context["latest_autonomous_replan_ack_run"] is accepted
    assert context["latest_replan_ack_feedback_run"] is rejected


def test_in_flight_todo_binding_survives_run_compaction() -> None:
    compacted = compact_run(
        _run(
            "2026-08-09T00:07:00Z",
            todo_id="todo_current001",
            delivery_outcome="outcome_progress",
            vision_checkpoint={
                "schema_version": "vision_checkpoint_v0",
                "agent_id": AGENT_ID,
                "required": False,
                "satisfied": True,
                "decision": "not_required",
                "delivery_boundary": "in_flight_continuation",
                "triggers": [
                    {
                        "kind": "in_flight_continuation",
                        "todo_id": "todo_current001",
                    }
                ],
            },
        )
    )

    assert compacted["todo_id"] == "todo_current001"
    assert compacted["vision_checkpoint"]["delivery_boundary"] == (
        "in_flight_continuation"
    )
    assert compacted["vision_checkpoint"]["triggers"] == [
        {"kind": "in_flight_continuation", "todo_id": "todo_current001"}
    ]


def test_unchanged_vision_revision_survives_run_compaction() -> None:
    compacted = compact_run(
        _run(
            "2026-08-09T00:08:00Z",
            delivery_outcome="outcome_progress",
            vision_checkpoint={
                "schema_version": "vision_checkpoint_v0",
                "agent_id": AGENT_ID,
                "required": True,
                "satisfied": True,
                "decision": "unchanged_with_reason",
                "unchanged_reason": "The evidence-linked route remains current.",
                "continuity_basis": {
                    "kind": "existing_vision_unchanged",
                    "vision_generated_at": "2026-08-09T00:05:00Z",
                    "private_extra": "drop-me",
                },
                "triggers": [
                    {
                        "kind": "material_delivery_outcome",
                        "delivery_outcome": "outcome_progress",
                    }
                ],
            },
        )
    )

    assert compacted["vision_checkpoint"]["continuity_basis"] == {
        "kind": "existing_vision_unchanged",
        "vision_generated_at": "2026-08-09T00:05:00Z",
    }


def test_default_delivery_boundary_is_implicit_after_run_compaction() -> None:
    compacted = compact_run(
        _run(
            "2026-08-09T00:07:00Z",
            delivery_outcome="outcome_progress",
            vision_checkpoint={
                "schema_version": "vision_checkpoint_v0",
                "agent_id": AGENT_ID,
                "required": True,
                "satisfied": True,
                "decision": "satisfied",
                "delivery_boundary": "semantic_closeout",
            },
        )
    )

    assert "delivery_boundary" not in compacted["vision_checkpoint"]


def test_retirement_prevents_stale_vision_selection() -> None:
    runs = [
        _run(
            "2026-08-09T00:02:00Z",
            vision_checkpoint=_checkpoint(decision="retired_or_superseded"),
        ),
        _run(
            "2026-08-09T00:01:00Z",
            agent_vision={
                "agent_id": AGENT_ID,
                "state": "vision_replanned",
                "vision_patch": {"acceptance_summary": "Stale vision."},
            },
        ),
    ]

    context = _agent_context(goal_semantic_history_from_runs(runs), AGENT_ID)

    assert context["vision_retired_at"] == "2026-08-09T00:02:00Z"
    assert "latest_agent_vision_run" not in context


def test_recent_runs_are_strictly_bounded() -> None:
    runs = [
        _run(
            f"2026-08-09T00:00:{second:02d}Z",
            classification="quota_slot_spent",
        )
        for second in range(40)
    ]

    assert latest_runs_with_agent_context(runs, limit=30) == runs[:30]
    assert latest_runs_with_agent_context(runs, limit=0) == []


def test_explicit_lane_decision_keeps_source_identity_beyond_recent_history() -> None:
    from loopx.control_plane.goals.goal_frontier.semantic_history import latest_lane_recommendation_from_status

    decision = _run("2026-08-09T00:01:00Z", json_path="runs/decision.json",
        recommended_action_resolution={"schema_version": "refresh_recommendation_v0",
            "recommended_action_source": "explicit_arg", "recommended_action": "Choose another method."})
    runs = [
        _run(f"2026-08-09T00:02:{second:02d}Z") for second in reversed(range(40))
    ] + [
        _run("2026-08-09T00:01:30Z", agent_id=OTHER_AGENT_ID,
            recommended_action_resolution={"recommended_action_source": "explicit_arg",
                "recommended_action": "Review the independent route."}),
        decision,
        _run("2026-08-09T00:00:00Z",
            recommended_action_resolution={"step_revision": "old-step"}),
    ]
    history = compact_goal_semantic_history(goal_semantic_history_from_runs(runs), compact_run=compact_run)
    assert _agent_context(history, AGENT_ID)["latest_lane_step_run"]["json_path"] == "runs/decision.json"
    status = {"run_history": {"goals": [{"id": "semantic-goal",
        "semantic_history": history, "latest_runs": latest_runs_with_agent_context(runs, limit=1)}]}}
    resolution = latest_lane_recommendation_from_status(status, goal_id="semantic-goal", agent_id=AGENT_ID)
    assert resolution["recommended_action_source"] == "explicit_arg"
    assert "step_revision" not in resolution
    assert resolution["journal_source"] == {
        "generated_at": decision["generated_at"], "json_path": "runs/decision.json"}
    assert resolution["agent_id"] == AGENT_ID and resolution["goal_id"] == "semantic-goal"


def test_agent_lane_status_keeps_only_selected_semantic_context() -> None:
    raw_history = goal_semantic_history_from_runs(
        [
            _run(
                "2026-08-09T00:02:00Z",
                agent_vision={
                    "agent_id": AGENT_ID,
                    "state": "vision_replanned",
                    "vision_patch": {"acceptance_summary": "Current lane."},
                },
            ),
            _run(
                "2026-08-09T00:01:00Z",
                agent_id=OTHER_AGENT_ID,
                agent_vision={
                    "agent_id": OTHER_AGENT_ID,
                    "state": "vision_replanned",
                    "vision_patch": {"acceptance_summary": "Other lane."},
                },
            ),
        ]
    )
    semantic_history = compact_goal_semantic_history(
        raw_history,
        compact_run=lambda run: deepcopy(run),
    )
    payload: dict[str, object] = {
        "run_history": {
            "available": True,
            "goal_count": 1,
            "run_count": 2,
            "recent_runs": [],
            "goals": [
                {
                    "id": "semantic-goal",
                    "semantic_history": semantic_history,
                }
            ],
        }
    }

    compact_agent_lane_status_payload_for_display(payload, agent_id=AGENT_ID)

    run_history = payload["run_history"]
    assert isinstance(run_history, dict)
    goals = run_history["goals"]
    assert isinstance(goals, list)
    contexts = goals[0]["semantic_history"]["agents"]
    assert [context["agent_id"] for context in contexts] == [AGENT_ID]
