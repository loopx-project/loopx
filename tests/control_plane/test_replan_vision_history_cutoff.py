"""A historical review needs an actual admission timestamp."""
import pytest

from loopx.control_plane.goals.goal_frontier.semantic_history import receipt_bound_vision_history_candidate
from loopx.control_plane.goals.goal_frontier import build_goal_frontier_projection_context_from_status
from loopx.control_plane.status.autonomous_replan_projection import AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS
from test_replan_vision_history_recovery import _bound


@pytest.mark.parametrize("cutoff", [None, "not-a-timestamp"])
def test_history_cannot_recover_without_a_valid_guard_time(cutoff):
    args, admitted = _bound()
    assert receipt_bound_vision_history_candidate(
        args["newest_first_runs"], goal_id=args["goal_id"], agent_id=args["agent_id"],
        selected_obligation_id=admitted["obligation_id"], guard_recorded_at=cutoff,
        neutral_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
        goal_status="active",
    ) is None


def test_read_projection_uses_full_history_instead_of_current_semantic_slots():
    args, admitted = _bound()
    current = args["newest_first_runs"][0]
    status = {"run_history": {"goals": [{"id": args["goal_id"], "latest_runs": [current],
        "semantic_history": {"agents": [{"agent_id": args["agent_id"],
            "latest_agent_vision_run": current, "latest_vision_checkpoint_run": current}]}}]}}
    projection = dict(goal_id=args["goal_id"], agent_id=args["agent_id"], status_payload=status,
        item={}, project_asset=None, user_todo_summary=None, agent_todo_summary=None,
        work_lane_contract=None, neutral_replan_ack_classifications=AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
        receipt_bound_replan_obligation_id=admitted["obligation_id"],
        receipt_bound_guard_recorded_at=args["receipt_bound_guard_recorded_at"])
    unavailable = build_goal_frontier_projection_context_from_status(**projection)
    assert not any((row.get("obligation") or {}).get("obligation_id") == admitted["obligation_id"]
        for row in unavailable["replan_transition_candidates"])
    recovered = build_goal_frontier_projection_context_from_status(
        **projection, receipt_bound_history_runs=args["newest_first_runs"])
    row = next(row for row in recovered["replan_transition_candidates"]
        if (row.get("obligation") or {}).get("obligation_id") == admitted["obligation_id"])
    assert row["reconstructed_vision_review"] is True
    assert row["obligation"]["triggers"] == admitted["triggers"]
    assert row["ack"] is None
