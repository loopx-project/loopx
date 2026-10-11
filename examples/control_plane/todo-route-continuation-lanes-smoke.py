#!/usr/bin/env python3
"""Smoke-test todo route-continuation lane projection helpers."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.todos.quota_selection import project_quota_planning  # noqa: E402


def assert_route_continuation_lanes_filter_current_unclaimed_and_other_agents() -> None:
    summary = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "route_continuation_replan_candidates": [
            {
                "index": 2,
                "todo_id": "todo_current_claimed_route",
                "text": "[P1] Continue current claimed route.",
                "task_class": "advancement_task",
                "claimed_by": "codex-side-bypass",
                "route_id": "current-route",
                "route_continuation_replan_required": True,
                "required_write_scopes": [" loopx/** ", "loopx/**"],
                "decision_scope": "direction:action:route",
            },
            {
                "index": 1,
                "route_key": "unclaimed-route",
                "recommended_action": "[P1] Continue unclaimed route.",
                "task_class": "advancement_task",
                "route_continuation_replan_required": True,
            },
            {
                "index": 3,
                "todo_id": "todo_other_agent_route",
                "text": "[P1] Continue other agent route.",
                "task_class": "advancement_task",
                "claimed_by": "codex-main-control",
                "route_id": "other-route",
                "route_continuation_replan_required": True,
            },
            {
                "index": 4,
                "todo_id": "todo_false_route",
                "text": "[P1] Do not project this route.",
                "task_class": "advancement_task",
                "route_continuation_replan_required": False,
            },
            {
                "index": 5,
                "todo_id": "todo_monitor_route",
                "text": "[P1] Monitor route is not advancement replan.",
                "task_class": "continuous_monitor",
                "route_continuation_replan_required": True,
            },
        ],
        "handoff_gates": [
            {
                "index": 0,
                "todo_id": "todo_blocking_route_gate",
                "text": "[P0-review] Review route gate.",
                "task_class": "advancement_task",
                "claimed_by": "codex-side-bypass",
                "excluded_agents": ["codex-main-control"],
                "route_id": "blocked-route",
                "route_continuation_replan_required": True,
                "route_continuation_reason": "same route has a next slice",
            }
        ],
    }

    lanes = project_quota_planning(
        summary,
        all_open_items=[],
        source_open_count=0,
        agent_identity={"agent_id": "codex-side-bypass"},
        filter_user_gate_blocks_agent=False,
        available_capabilities=[],
    )["route_lanes"]
    assert lanes["route_continuation_replan_count"] == 4, lanes
    assert [
        item.get("route_id") or item.get("route_key")
        for item in lanes["route_continuation_replan_candidates"]
    ] == [
        "blocked-route",
        "unclaimed-route",
        "current-route",
        "other-route",
    ], lanes
    assert lanes["current_agent_route_continuation_replan_count"] == 3, lanes
    assert [
        item.get("route_id") or item.get("route_key")
        for item in lanes["current_agent_route_continuation_replan_candidates"]
    ] == ["blocked-route", "unclaimed-route", "current-route"], lanes
    assert lanes["unclaimed_route_continuation_replan_count"] == 1, lanes
    assert (
        lanes["unclaimed_route_continuation_replan_candidates"][0]["route_key"]
        == "unclaimed-route"
    ), lanes
    assert lanes["other_agent_route_continuation_replan_count"] == 1, lanes
    assert lanes["other_agent_route_continuation_replan_candidates"][0]["route_id"] == (
        "other-route"
    ), lanes
    current = lanes["current_agent_route_continuation_replan_candidates"][2]
    assert current["required_write_scopes"] == ["loopx/**"], current
    assert current["decision_scope"]["scope_key"] == "route", current
    assert (
        lanes["route_continuation_replan_selection_policy"]
        == "quota may wake the current peer for route continuation replan "
        "candidates claimed by that agent or unclaimed; other-agent route candidates remain diagnostic visibility"
    ), lanes


def main() -> int:
    assert_route_continuation_lanes_filter_current_unclaimed_and_other_agents()
    print("todo-route-continuation-lanes-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
