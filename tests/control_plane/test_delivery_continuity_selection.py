from __future__ import annotations

import pytest

from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)


GOAL_ID = "delivery-continuity-fixture"
AGENT_ID = "codex-main"
CURRENT_TODO_ID = "todo_current001"
QUEUE_HEAD_TODO_ID = "todo_queuehead001"


def _status(
    *, delivery_outcome: str = "outcome_progress", current_status: str = "open"
):
    queue_head = quota_todo_item(
        todo_id=QUEUE_HEAD_TODO_ID,
        index=1,
        priority="P1",
        title="Start the newly queued sibling slice.",
        claimed_by=AGENT_ID,
    )
    current = quota_todo_item(
        todo_id=CURRENT_TODO_ID,
        index=2,
        priority="P1",
        status=current_status,
        title="Continue the already validated implementation slice.",
        claimed_by=AGENT_ID,
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        agent_todo_items=[queue_head, current],
        recommended_action=queue_head["text"],
        next_action=queue_head["text"],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
        claim_scope_agent_id=AGENT_ID,
        latest_runs=[
            {
                "generated_at": "2026-08-22T10:00:00Z",
                "classification": "bounded_implementation_progress",
                "progress_scope": "agent_lane",
                "agent_id": AGENT_ID,
                "todo_id": CURRENT_TODO_ID,
                "delivery_outcome": delivery_outcome,
                "recommended_action": current["text"],
            }
        ],
    )


def test_same_open_todo_survives_cross_heartbeat_queue_reordering() -> None:
    packet = build_quota_should_run(
        _status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == CURRENT_TODO_ID
    assert packet["agent_lane_next_action"]["selected_by"] == "in_flight_todo"
    assert packet["agent_lane_next_action"]["selection_reason"] == (
        "same_open_todo_after_progress"
    )
    assert packet["selected_todo"]["delivery_boundary"] == (
        "in_flight_continuation"
    )
    assert "delivery_boundary" not in packet["agent_lane_next_action"]
    assert "delivery_continuity" not in packet


def test_outcome_gap_releases_normal_queue_selection() -> None:
    packet = build_quota_should_run(
        _status(delivery_outcome="outcome_gap"),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == QUEUE_HEAD_TODO_ID
    assert packet["selected_todo"].get("selected_by") != "in_flight_todo"
    assert "delivery_continuity" not in packet


@pytest.mark.parametrize("prior", [
    {"vision_checkpoint": {"schema_version": "vision_checkpoint_v0", "agent_id": AGENT_ID}},
    {"agent_vision": {"agent_id": AGENT_ID, "path_delta": {"outcome": "replan",
        "prior_assumption": "The experiment might reveal a defect.", "observed_reality": "The scoped question was answered.",
        "changed": ["Select the independent remaining work"], "evidence_refs": ["evidence:result"]}}},
])
def test_closed_or_replanned_delivery_does_not_pin_the_old_open_todo(prior) -> None:
    status = _status()
    status["run_history"]["goals"][0]["latest_runs"][0].update(prior)
    packet = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert packet["selected_todo"]["todo_id"] == QUEUE_HEAD_TODO_ID
    assert packet["selected_todo"].get("selected_by") != "in_flight_todo"
    # Releasing an old preference is not permission to mutate its Todo or skip
    # current eligibility. The ordinary portfolio remains the decision surface.
    assert packet["interaction_contract"]["cli_channel"]["selection_required"]


def test_replan_prose_or_another_agents_vision_does_not_change_continuity() -> None:
    status = _status()
    status["run_history"]["goals"][0]["latest_runs"][0].update({
        "recommended_action": "Replan the finished experiment",
        "agent_vision": {"agent_id": "peer", "path_delta": {"outcome": "replan"}},
    })
    packet = build_quota_should_run(status, goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert packet["selected_todo"]["todo_id"] == CURRENT_TODO_ID


def test_closed_or_blocked_todo_releases_normal_queue_selection() -> None:
    packet = build_quota_should_run(
        _status(current_status="blocked"),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert packet["selected_todo"]["todo_id"] == QUEUE_HEAD_TODO_ID
    assert packet["selected_todo"].get("selected_by") != "in_flight_todo"
    assert "delivery_continuity" not in packet


def test_same_turn_receipt_still_outranks_cross_heartbeat_continuity() -> None:
    packet = build_quota_should_run(
        _status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        receipt_bound_todo_id=QUEUE_HEAD_TODO_ID,
    )

    assert packet["selected_todo"]["todo_id"] == QUEUE_HEAD_TODO_ID
    assert packet["agent_lane_next_action"]["selection_binding"] == (
        "heartbeat_receipt"
    )
    assert packet["selected_todo"].get("selected_by") != "in_flight_todo"
    assert "delivery_continuity" not in packet


def test_empty_agent_lane_does_not_call_delivery_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_route(**_kwargs):
        pytest.fail("an empty projected candidate set must not cross the runtime")

    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run_packet.evaluate_delivery_route",
        unexpected_route,
    )
    packet = build_quota_should_run(
        quota_status_payload(
            goal_id=GOAL_ID,
            status="active",
            agent_todo_items=[],
            recommended_action="Wait for new work.",
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": [AGENT_ID],
            },
            claim_scope_agent_id=AGENT_ID,
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert "selected_todo" not in packet
    assert "agent_lane_next_action" not in packet
