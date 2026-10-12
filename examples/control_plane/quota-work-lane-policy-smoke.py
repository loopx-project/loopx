#!/usr/bin/env python3
"""Smoke-test the extracted quota work-lane policy helper."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.work_items.work_lane import (  # noqa: E402
    build_work_lane_contract,
    due_monitor_can_preempt_advancement,
    due_monitor_preempts_advancement,
)
from loopx.control_plane.work_items.work_lane_context import (  # noqa: E402
    build_work_lane_context_contract,
    item_progress_scope,
    latest_run_progress_scope,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402


GOAL_ID = "work-lane-policy-fixture"
PAST_DUE_AT = "2000-01-01T00:00:00+00:00"


def assert_contract(name: str, contract: dict | None, **expected: object) -> None:
    assert isinstance(contract, dict), (name, contract)
    for key, value in expected.items():
        assert contract.get(key) == value, (name, key, contract)


def status_payload(
    *,
    status: str,
    agent_todo_items: list[dict] | None = None,
    next_action: str = "Observe dependency state and then advance backlog if unchanged.",
    post_handoff_latest_run: dict | None = None,
    latest_runs: list[dict] | None = None,
) -> dict:
    if agent_todo_items is None:
        agent_todo_items = [
            {
                "index": 1,
                "text": "[P1] Advance the self-repair planning slice with a validation-backed patch.",
                "role": "agent",
                "status": "open",
                "priority": "P1",
            }
        ]
    return quota_status_payload(
        goal_id=GOAL_ID,
        status=status,
        agent_todo_items=agent_todo_items,
        recommended_action=next_action,
        next_action=next_action,
        post_handoff_latest_run=post_handoff_latest_run,
        latest_runs=latest_runs,
    )


def assert_unchanged_monitor_attempt_yields_to_advancement() -> None:
    due_todo = "[P0] Monitor one overdue dependency."
    advancement_todo = "[P1] Advance the bounded product slice."
    items = [
        {
            "index": 1,
            "text": due_todo,
            "role": "agent",
            "status": "open",
            "priority": "P0",
            "task_class": "continuous_monitor",
            "action_kind": "monitor",
            "next_due_at": PAST_DUE_AT,
        },
        {
            "index": 2,
            "text": advancement_todo,
            "role": "agent",
            "status": "open",
            "priority": "P1",
            "task_class": "advancement_task",
        },
    ]
    unchanged_poll = {
        "classification": "quota_monitor_poll",
        "agent_id": "codex-fixture",
        "health_check": "due monitor observation unchanged; no quota spend",
        "monitor_event": {
            "monitor_mode": "due_monitor_observed_without_material_transition",
            "material_change": False,
        },
    }
    payload = status_payload(
        status="monitor_backlog_fairness",
        agent_todo_items=items,
        latest_runs=[unchanged_poll],
    )
    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "advancement_task", lane
    assert lane["reason_codes"] == [
        "open_agent_todo",
        "due_monitor_context",
        "monitor_attempt_already_recorded",
    ], lane
    assert guard["recommended_action"] == advancement_todo, guard

    payload = status_payload(
        status="monitor_priority_reset_after_delivery",
        agent_todo_items=items,
        latest_runs=[
            {
                "classification": "validated_delivery",
                "delivery_outcome": "outcome_progress",
            },
            unchanged_poll,
        ],
    )
    guard = build_quota_should_run(payload, goal_id=GOAL_ID)
    lane = guard["work_lane_contract"]
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["selected_todo_id"], lane


def assert_work_lane_context_matches_quota_state_machine_cases() -> None:
    due_todo = "[P0] Monitor the overdue update-note draft PR before feature work."
    executable_todo = "[P1] Implement the bounded runtime repair slice."
    dependency_payload = status_payload(status="side_bypass_dependency_observation")
    dependency_payload["attention_queue"]["items"][0]["project_asset"]["progress_scope"] = "dependency_observation"
    cases = [
        (
            "dependency_observation",
            dependency_payload,
        ),
        (
            "surface_only_followthrough",
            status_payload(
                status="runner_contract_delivered",
                next_action="Execute the compact runner batch or write the exact blocker.",
                post_handoff_latest_run={
                    "classification": "runner_contract_v0_delivered",
                    "delivery_batch_scale": "implementation",
                    "delivery_outcome": "surface_only",
                },
            ),
        ),
        (
            "due_monitor_preempts_lower_priority_advancement",
            status_payload(
                status="monitor_due_preempts_lower_priority_advancement",
                agent_todo_items=[
                    {
                        "index": 1,
                        "text": due_todo,
                        "role": "agent",
                        "status": "open",
                        "priority": "P0",
                        "task_class": "continuous_monitor",
                        "action_kind": "monitor",
                        "next_due_at": PAST_DUE_AT,
                    },
                    {
                        "index": 2,
                        "text": executable_todo,
                        "role": "agent",
                        "status": "open",
                        "priority": "P1",
                        "task_class": "advancement_task",
                    },
                ],
            ),
        ),
    ]
    for name, payload in cases:
        item = payload["attention_queue"]["items"][0]
        guard = build_quota_should_run(payload, goal_id=GOAL_ID)
        direct = build_work_lane_context_contract(
            item,
            agent_todo_summary=guard["agent_todo_summary"],
        )
        assert direct == guard["work_lane_contract"], (name, direct, guard)
        for lane_item in guard["work_lane_contract"].get("monitor_due_items") or []:
            assert "title" not in lane_item, (name, lane_item)
            assert "handoff_note" not in lane_item, (name, lane_item)


def assert_work_lane_context_progress_scope_sources() -> None:
    dependency_payload = status_payload(status="side_bypass_dependency_observation")
    dependency_item = dependency_payload["attention_queue"]["items"][0]
    assert item_progress_scope(dependency_item) == "primary_goal"
    assert latest_run_progress_scope(
        {"classification": "runner_dependency_observed"}
    ) == "primary_goal"
    assert latest_run_progress_scope(
        {
            "classification": "runner_dependency_observed",
            "progress_scope": "primary_goal",
        }
    ) == "primary_goal"
    dependency_item["progress_scope"] = "dependency_observation"
    assert item_progress_scope(dependency_item) == "dependency_observation"


def main() -> int:
    assert_unchanged_monitor_attempt_yields_to_advancement()
    assert_work_lane_context_matches_quota_state_machine_cases()
    assert_work_lane_context_progress_scope_sources()

    advancement = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=True,
        todo_counts={"open": 2, "advancement": 1, "monitor": 1},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement={"todo_id": "todo_adv"},
        due_monitor_preempts_advancement=False,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )
    assert_contract(
        "advancement-with-monitor-context",
        advancement,
        lane="advancement_task",
        obligation="advance_one_bounded_segment",
        must_attempt_work=True,
        monitor_policy="material_transition_only",
    )
    assert advancement["reason_codes"] == [
        "open_agent_todo",
        "external_monitor_context",
    ], advancement

    due_monitor = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=False,
        todo_counts={"open": 2, "advancement": 1, "monitor": 1},
        monitor_due_count=2,
        due_monitor_items=[
            {"todo_id": "todo_due", "next_due_at": "2000-01-01T00:00:00+00:00"},
            {"todo_id": "todo_due_2", "next_due_at": "2000-01-02T00:00:00+00:00"},
        ],
        first_advancement={"todo_id": "todo_adv"},
        due_monitor_preempts_advancement=True,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )
    assert_contract(
        "due-monitor-preempts",
        due_monitor,
        lane="continuous_monitor",
        monitor_kind="todo_monitor_due",
        obligation="attempt_due_monitor",
        must_attempt_work=True,
        monitor_due_count=2,
        selected_todo_id="todo_due",
    )
    assert len(due_monitor["monitor_due_items"]) == 1, due_monitor

    private_due_monitor = {
        "todo_id": "todo_private_due",
        "priority": "P0-LOCAL",
        "action_kind": "local_department_doc_todo_projection_monitor",
        "result_hash": "private_boundary_no_authorized_read",
    }
    public_due_monitor = {
        "todo_id": "todo_public_due",
        "priority": "P0",
        "action_kind": "monitor",
    }
    lower_priority_due_monitor = {
        "todo_id": "todo_lower_due",
        "priority": "P2",
        "action_kind": "monitor",
    }
    first_advancement = {"todo_id": "todo_adv", "priority": "P1"}
    assert not due_monitor_can_preempt_advancement(private_due_monitor), private_due_monitor
    assert not due_monitor_preempts_advancement(
        private_due_monitor,
        first_advancement=first_advancement,
    )
    assert due_monitor_preempts_advancement(
        public_due_monitor,
        first_advancement=first_advancement,
    )
    assert not due_monitor_preempts_advancement(
        lower_priority_due_monitor,
        first_advancement=first_advancement,
    )

    quiet_monitor = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=False,
        todo_counts={"open": 1, "advancement": 0, "monitor": 1},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement=None,
        due_monitor_preempts_advancement=False,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )
    assert_contract(
        "quiet-monitor",
        quiet_monitor,
        lane="continuous_monitor",
        monitor_kind="todo_monitor",
        obligation="quiet_until_material_monitor_transition",
        must_attempt_work=False,
    )

    explicit_next_action = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=False,
        todo_counts={"open": 1, "advancement": 0, "monitor": 1},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement=None,
        due_monitor_preempts_advancement=False,
        outcome_followthrough=None,
        next_action_requires_advancement=True,
        monitor_due_item_limit=1,
        monitor_schedule_gap_count=1,
        monitor_schedule_gap_items=[{"todo_id": "todo_monitor_without_schedule"}],
    )
    assert_contract(
        "explicit-next-action-over-monitor-schedule-gap",
        explicit_next_action,
        lane="advancement_task",
        next_lane="advancement_task",
        obligation="materialize_advancement_todo_or_blocker",
        must_attempt_work=True,
        monitor_policy="material_transition_only",
    )
    assert explicit_next_action["reason_codes"] == [
        "monitor_todo_only",
        "next_action_requires_advancement",
    ], explicit_next_action

    resume_blocked_by_monitor = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=False,
        todo_counts={"open": 2, "advancement": 0, "monitor": 1},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement=None,
        due_monitor_preempts_advancement=False,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
        resume_blocked_by_monitor_count=1,
        resume_blocked_by_monitor_items=[
            {
                "todo_id": "todo_gated_refactor",
                "resume_when": "todo_done:todo_standing_gate",
            }
        ],
    )
    assert_contract(
        "resume-blocked-by-monitor",
        resume_blocked_by_monitor,
        lane="advancement_task",
        obligation="repair_resume_gate_or_close_standing_monitor",
        must_attempt_work=True,
        monitor_policy="material_transition_only",
        selected_todo_id="todo_gated_refactor",
    )
    assert resume_blocked_by_monitor["reason_codes"] == [
        "monitor_todo_only",
        "resume_blocked_by_open_monitor",
    ], resume_blocked_by_monitor

    dependency_advancement = build_work_lane_contract(
        progress_scope="dependency_observation",
        external_poll_signal=False,
        todo_counts={"open": 1, "advancement": 1, "monitor": 0},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement={"todo_id": "todo_adv"},
        due_monitor_preempts_advancement=False,
        outcome_followthrough=None,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )
    assert_contract(
        "dependency-advancement",
        dependency_advancement,
        lane="continuous_monitor",
        monitor_kind="dependency_observation",
        obligation="advance_unless_material_monitor_transition",
        must_attempt_work=True,
        next_lane="advancement_task",
    )

    outcome_followthrough = {
        "source": "post_handoff_latest_run",
        "required": True,
    }
    followthrough = build_work_lane_contract(
        progress_scope="agent_lane",
        external_poll_signal=False,
        todo_counts={"open": 1, "advancement": 1, "monitor": 0},
        monitor_due_count=0,
        due_monitor_items=[],
        first_advancement={"todo_id": "todo_adv"},
        due_monitor_preempts_advancement=False,
        outcome_followthrough=outcome_followthrough,
        next_action_requires_advancement=False,
        monitor_due_item_limit=1,
    )
    assert_contract(
        "outcome-followthrough",
        followthrough,
        lane="advancement_task",
        obligation="advance_primary_outcome_or_write_blocker",
        must_attempt_work=True,
    )
    assert followthrough["outcome_followthrough"] == outcome_followthrough, followthrough

    print("quota-work-lane-policy-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
