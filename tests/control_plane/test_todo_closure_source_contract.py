from __future__ import annotations

from copy import deepcopy

import pytest

from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.quota import build_quota_should_run


SOURCE = """## User Todo / Owner Review Reading Queue

## Agent Todo

- [x] [P1] Complete bounded work with independent acceptance.
  <!-- loopx:todo todo_id=todo_closed status=done task_class=advancement_task no_followup=true -->
"""


@pytest.mark.parametrize("patch", [
    {"item_count": True},
    {"monitor_open_count": "bad", "watch_only_monitor_count": "bad"},
    {"monitor_open_count": None, "watch_only_monitor_count": None},
    {"monitor_open_count": -1, "watch_only_monitor_count": -1},
    {"monitor_open_count": False, "watch_only_monitor_count": False},
    {"item_count": -1},
    {"item_count": 1.5},
    {"item_count": "1"},
    {"role": "user"},
    {"monitor_open_count": 1},
    {"successor_gap_count": 1},
    {"route_replan_count": 1},
    {"derived": "true"},
    {"no_followup_count": True},
])
def test_malformed_terminal_proof_cannot_stop_quota(patch: dict[str, object]) -> None:
    parsed = parse_active_state_todos(SOURCE)
    source = deepcopy(parsed["agent_todos"])
    source["terminal_closure_proof"].update(patch)
    summary = summarize_user_todos_for_quota(source)
    assert summary["source_completeness"]["status"] == "invalid"
    assert "closure_intent" not in summary
    status = quota_status_payload(goal_id="goal-proof-test", status="active",
        recommended_action="Inspect exact closure evidence.",
        user_todos=parsed["user_todos"], agent_todos=source)
    decision = build_quota_should_run(status, goal_id="goal-proof-test")
    assert decision["effective_action"] != "terminal_no_followup"
    assert "terminal_state" not in decision["goal_frontier_projection"]


def test_valid_complete_source_still_closes_without_changing_its_proof() -> None:
    parsed = parse_active_state_todos(SOURCE)
    before = deepcopy(parsed)
    summary = summarize_user_todos_for_quota(parsed["agent_todos"])
    assert summary["source_completeness"]["status"] == "valid"
    assert summary["closure_intent"]["count"] == 1
    status = quota_status_payload(goal_id="goal-proof-test", status="active",
        recommended_action="Observe completed work.", **parsed)
    decision = build_quota_should_run(status, goal_id="goal-proof-test")
    assert decision["effective_action"] == "terminal_no_followup"
    assert decision["should_run"] is False
    assert parsed == before
