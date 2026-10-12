#!/usr/bin/env python3
"""Canary the quota heartbeat recommendation state machine."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.quota.heartbeat_recommendation import (  # noqa: E402
    build_heartbeat_recommendation,
)
from loopx.control_plane.work_items.delivery_outcome import DeliveryOutcome  # noqa: E402
from loopx.control_plane.work_items.work_lane import build_work_lane_contract  # noqa: E402


GOAL_ID = "heartbeat-recommendation-fixture"


def user_todos(open_count: int) -> dict[str, Any]:
    return {
        "schema_version": "todo_summary_v0",
        "source_section": "User Todo",
        "open_count": open_count,
        "first_open_items": [
            {
                "todo_id": f"todo_user_{index}",
                "status": "open",
                "task_class": "user_gate",
                "text": "[P0-user] Decide the owner gate.",
            }
            for index in range(open_count)
        ],
    }


def agent_todos(open_count: int) -> dict[str, Any]:
    return {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "open_count": open_count,
        "first_open_items": [
            {
                "todo_id": f"todo_agent_{index}",
                "status": "open",
                "task_class": "advancement_task",
                "text": "[P1] Advance the durable heartbeat canary.",
            }
            for index in range(open_count)
        ],
    }


def work_lane(
    *,
    open_count: int,
    advancement_count: int,
    monitor_count: int,
    monitor_due_count: int = 0,
    progress_scope: str = "agent_lane",
) -> dict[str, Any] | None:
    due_items = [
        {
            "todo_id": "todo_monitor_due",
            "status": "open",
            "task_class": "continuous_monitor",
            "next_due_at": "2000-01-01T00:00:00+00:00",
            "text": "[P0-monitor] Observe a due material transition.",
        }
    ][:monitor_due_count]
    return build_work_lane_contract(
        progress_scope=progress_scope,
        external_poll_signal=False,
        todo_counts={
            "open": open_count,
            "advancement": advancement_count,
            "monitor": monitor_count,
        },
        monitor_due_count=monitor_due_count,
        due_monitor_items=due_items,
        first_advancement=(
            {
                "todo_id": "todo_agent_advancement",
                "status": "open",
                "task_class": "advancement_task",
                "text": "[P1] Advance the durable heartbeat canary.",
            }
            if advancement_count
            else None
        ),
        due_monitor_preempts_advancement=monitor_due_count > 0 and advancement_count == 0,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )


def recommendation(
    item: dict[str, Any],
    *,
    state: str = "eligible",
    should_run: bool = True,
    user_summary: dict[str, Any] | None = None,
    agent_summary: dict[str, Any] | None = None,
    lane: dict[str, Any] | None = None,
    stall_self_repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_heartbeat_recommendation(
        item,
        goal_id=GOAL_ID,
        state=state,
        should_run=should_run,
        user_todo_summary=user_summary or user_todos(0),
        agent_todo_summary=agent_summary or agent_todos(0),
        work_lane_contract=lane,
        stall_self_repair=stall_self_repair,
        select_replan_obligation=False,
    )


def assert_gate_and_repair_precedence() -> None:
    operator = recommendation({}, state="operator_gate")
    assert operator["recommended_mode"] == "ask_operator_gate", operator
    assert operator["notify"] == "NOTIFY", operator

    repair = recommendation(
        {},
        stall_self_repair={
            "allowed": True,
            "recommended_mode": "repair_control_plane_stall",
            "repair_focus": "state projection is stale",
        },
    )
    assert repair["recommended_mode"] == "repair_control_plane_stall", repair
    assert repair["repair_focus"] == "state projection is stale", repair


def assert_quota_skip_and_blocker_push() -> None:
    skipped = recommendation({}, state="throttled", should_run=False)
    assert skipped["recommended_mode"] == "quota_skip", skipped
    assert "throttled" in skipped["reason"], skipped

    blocker = recommendation(
        {"waiting_on": "external_evidence"},
        state="focus_wait",
        user_summary=user_todos(1),
    )
    assert blocker["recommended_mode"] == "blocker_push_notify", blocker
    assert blocker["repeat_notification_required"] is True, blocker
    assert blocker["notify"] == "NOTIFY", blocker


def assert_monitor_lanes_do_not_collapse_due_work() -> None:
    quiet_lane = work_lane(open_count=1, advancement_count=0, monitor_count=1)
    quiet = recommendation({}, lane=quiet_lane)
    assert quiet_lane and quiet_lane["must_attempt_work"] is False, quiet_lane
    assert quiet["recommended_mode"] == "monitor_quiet_until_material_transition", quiet
    assert quiet["notify"] == "DONT_NOTIFY", quiet

    due_lane = work_lane(
        open_count=1,
        advancement_count=0,
        monitor_count=1,
        monitor_due_count=1,
    )
    due = recommendation(
        {
            "handoff_readiness": {
                "post_handoff_latest_run": {
                    "classification": "dependency_observed",
                    "delivery_outcome": "outcome_progress",
                }
            }
        },
        lane=due_lane,
    )
    assert due_lane and due_lane["must_attempt_work"] is True, due_lane
    assert due_lane["obligation"] == "attempt_due_monitor", due_lane
    assert due["recommended_mode"] == "follow_work_lane_contract", due
    assert due["latest_run"]["progress_scope"] == "primary_goal", due


def assert_read_only_map_lifecycle_modes() -> None:
    first = recommendation(
        {
            "status": "connected_without_run",
            "adapter_kind": "docs_read_only_map_v0",
        }
    )
    assert first["recommended_mode"] == "run_first_read_only_map", first
    assert first["command"] == f"loopx read-only-map --goal-id {GOAL_ID}", first
    assert first["notify"] == "NOTIFY", first

    mapped = recommendation(
        {
            "status": "read_only_project_map",
            "lifecycle_flags": ["mapped"],
        }
    )
    assert mapped["recommended_mode"] == "mapped_noop_if_unchanged", mapped
    assert mapped["stop_if_unchanged"] is True, mapped
    assert mapped["notify"] == "DONT_NOTIFY", mapped


def assert_post_handoff_primary_outcome_modes() -> None:
    base_item = {
        "status": "post_handoff_run_seen",
        "adapter_kind": "harness_self_improvement",
        "control_plane": {"self_repair": {"enabled": True}},
        "handoff_readiness": {
            "post_handoff_run_seen": True,
            "handoff_status": "post_handoff_run_seen",
            "post_handoff_latest_run": {
                "classification": "implementation_merged",
                "delivery_outcome": DeliveryOutcome.PRIMARY_GOAL_OUTCOME.value,
            },
        },
    }
    no_agent = recommendation(base_item)
    assert no_agent["recommended_mode"] == "post_handoff_observe_if_unchanged", no_agent
    assert no_agent["stop_if_unchanged"] is True, no_agent
    assert no_agent["latest_run"]["progress_scope"] == "primary_goal", no_agent

    dependency_item = {
        **base_item,
        "handoff_readiness": {
            **base_item["handoff_readiness"],
            "post_handoff_latest_run": {
                **base_item["handoff_readiness"]["post_handoff_latest_run"],
                "classification": "dependency_observed_after_merge",
                "progress_scope": "dependency_observation",
            },
        },
    }
    with_agent = recommendation(
        dependency_item,
        agent_summary=agent_todos(1),
        lane=work_lane(
            open_count=1,
            advancement_count=1,
            monitor_count=0,
            progress_scope="dependency_observation",
        ),
    )
    assert with_agent["recommended_mode"] == "follow_work_lane_contract", with_agent
    assert with_agent.get("stop_if_unchanged") is not True, with_agent
    assert with_agent["latest_run"]["progress_scope"] == "dependency_observation", with_agent


def assert_default_bounded_delivery_mode() -> None:
    normal = recommendation(
        {"status": "active-read-only"},
        agent_summary=agent_todos(1),
        lane=work_lane(open_count=1, advancement_count=1, monitor_count=0),
    )
    assert normal["recommended_mode"] == "steering_audit_then_one_step", normal
    assert "scope-bounded work" in normal["spend_policy"], normal


def main() -> int:
    assert_gate_and_repair_precedence()
    assert_quota_skip_and_blocker_push()
    assert_monitor_lanes_do_not_collapse_due_work()
    assert_read_only_map_lifecycle_modes()
    assert_post_handoff_primary_outcome_modes()
    assert_default_bounded_delivery_mode()
    print("quota-heartbeat-recommendation-state-machine-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
