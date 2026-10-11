#!/usr/bin/env python3
"""Smoke-test cleared blocker handoffs waking the blocked agent for replan."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.status import compact_todo_group  # noqa: E402
from loopx.control_plane.todos.handoff_gate import (  # noqa: E402
    build_todo_handoff_gate_states,
    handoff_ready_successor_todo_ids,
)
from loopx.control_plane.todos.quota_selection import project_quota_planning  # noqa: E402


GOAL_ID = "cleared-blocker-successor-gate-fixture"
BLOCKED_AGENT = "codex-value-explorer"
PRIMARY_AGENT = "codex-main-control"
APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


def todo_item(
    *,
    todo_id: str,
    text: str,
    claimed_by: str | None = None,
    status: str = "open",
    action_kind: str | None = None,
    excluded_agents: list[str] | None = None,
    unblocks_todo_id: str | None = None,
    resume_when: str | None = None,
    superseded_by: str | None = None,
    no_followup: bool = False,
) -> dict:
    item = {
        "todo_id": todo_id,
        "index": 1,
        "status": status,
        "done": status == "done",
        "role": "agent",
        "task_class": "advancement_task",
        "text": text,
    }
    if claimed_by:
        item["claimed_by"] = claimed_by
    if action_kind:
        item["action_kind"] = action_kind
    if excluded_agents:
        item["excluded_agents"] = excluded_agents
    if unblocks_todo_id:
        item["unblocks_todo_id"] = unblocks_todo_id
    if resume_when:
        item["resume_when"] = resume_when
    if superseded_by:
        item["superseded_by"] = superseded_by
    if no_followup:
        item["no_followup"] = True
    return item


def agent_todo_summary(items: list[dict]) -> dict:
    open_items = [item for item in items if not item["done"]]
    executable = [
        item
        for item in open_items
        if item.get("task_class") == "advancement_task"
        and item.get("status") == "open"
    ]
    summary = {
        "schema_version": "todo_summary_v0",
        "source_section": "Agent Todo",
        "total_count": len(items),
        "open_count": len(open_items),
        "done_count": len(items) - len(open_items),
        "first_open_items": open_items[:3],
        "first_executable_items": executable[:3],
        "executable_backlog_items": executable,
        "items": items,
    }
    handoff_gates = build_todo_handoff_gate_states(items)
    if handoff_gates:
        summary["handoff_gates"] = handoff_gates
    return summary


def assert_handoff_gate_lanes_and_ready_successors_are_todo_context_helpers() -> None:
    review_gate = todo_item(
        todo_id="todo_review_gate",
        text="[P0] Review the completed launch slice.",
        claimed_by=PRIMARY_AGENT,
        status="done",
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_launch_slice",
    )
    successor = todo_item(
        todo_id="todo_followup_slice",
        text="[P1] Continue the follow-up slice.",
        claimed_by=BLOCKED_AGENT,
        status="open",
        unblocks_todo_id="todo_review_gate",
    )
    blocked_gate = todo_item(
        todo_id="todo_unresolved_gate",
        text="[P0] Still blocking the value explorer.",
        claimed_by=PRIMARY_AGENT,
        status="done",
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_unresolved_slice",
    )
    summary = agent_todo_summary([review_gate, successor, blocked_gate])

    assert handoff_ready_successor_todo_ids(summary) == {"todo_followup_slice"}, summary
    open_items = [item for item in summary["items"] if not item["done"]]
    lanes = project_quota_planning(
        summary,
        all_open_items=open_items,
        source_open_count=len(open_items),
        agent_identity={"agent_id": BLOCKED_AGENT},
        filter_user_gate_blocks_agent=False,
        available_capabilities=[],
    )["handoff_lanes"]
    assert lanes["handoff_gate_count"] == 2, lanes
    assert lanes["current_agent_handoff_gate_count"] == 2, lanes
    assert lanes["current_agent_cleared_without_successor_handoff_count"] == 1, lanes
    assert (
        lanes["current_agent_cleared_without_successor_handoff_gates"][0]["todo_id"]
        == "todo_unresolved_gate"
    ), lanes


def status_payload(items: list[dict], *, recommended_action: str) -> dict:
    return {
        "ok": True,
        "goal_count": 1,
        "run_count": 0,
        "attention_queue": {
            "items": [
                {
                    "goal_id": GOAL_ID,
                    "status": "active",
                    "waiting_on": "",
                    "severity": "active",
                    "source": "active_state",
                    "recommended_action": recommended_action,
                    "quota": {
                        "compute": 1.0,
                        "window_hours": 24,
                        "slot_minutes": 1,
                        "allowed_slots": 1440,
                        "spent_slots": 0,
                        "state": "eligible",
                        "reason": "eligible",
                    },
                    "user_todos": {
                        "schema_version": "todo_summary_v0",
                        "source_section": "User Todo",
                        "total_count": 0,
                        "open_count": 0,
                        "done_count": 0,
                        "first_open_items": [],
                        "items": [],
                    },
                    "agent_todos": agent_todo_summary(items),
                }
            ]
        },
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "registry_member": True,
                    "status": "active",
                    "adapter_kind": "fixture_adapter_v0",
                    "adapter_status": "connected",
                    "coordination": {
                        "agent_model": "peer_v1",
                        "registered_agents": [PRIMARY_AGENT, BLOCKED_AGENT],
                    },
                    "latest_runs": [],
                }
            ]
        },
    }


def primary_owned_todo() -> dict:
    return todo_item(
        todo_id="todo_primary_followup",
        text="[P0] Main-control validates the public handoff contract.",
        claimed_by=PRIMARY_AGENT,
    )


def handoff_review(*, status: str = "open", superseded_by: str | None = None) -> dict:
    return todo_item(
        todo_id="todo_review_gate",
        text="[P0-review] Review value explorer handoff and decide next work.",
        claimed_by=PRIMARY_AGENT,
        status=status,
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_value_explorer_slice",
        superseded_by=superseded_by,
    )


def no_followup_handoff_review() -> dict:
    return todo_item(
        todo_id="todo_review_gate",
        text="[P0-review] Review value explorer handoff and decide next work.",
        claimed_by=PRIMARY_AGENT,
        status="done",
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_value_explorer_slice",
        no_followup=True,
    )


def archived_handoff_review() -> dict:
    item = handoff_review(status="done")
    item["archive_state"] = "completed_archive"
    return item


def value_successor() -> dict:
    return todo_item(
        todo_id="todo_value_successor",
        text="[P0] Continue value exploration with the reviewed connector scope.",
        claimed_by=BLOCKED_AGENT,
        resume_when="todo_done:todo_review_gate",
    )


def completed_value_successor() -> dict:
    return todo_item(
        todo_id="todo_value_successor",
        text="[P0] Completed value exploration successor for the reviewed scope.",
        claimed_by=BLOCKED_AGENT,
        status="done",
        unblocks_todo_id="todo_review_gate",
    )


def stale_handoff_closeout() -> dict:
    return todo_item(
        todo_id="todo_stale_review_closeout",
        text=(
            "[P2] Close or supersede the stale PR review handoff now that the "
            "blocked slice is complete."
        ),
        claimed_by=PRIMARY_AGENT,
        action_kind="stale_pr_review_closeout",
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_value_explorer_slice",
    )


def followup_review_gate() -> dict:
    return todo_item(
        todo_id="todo_followup_review_gate",
        text="[P0-review] Review the follow-up value connector PR.",
        claimed_by=PRIMARY_AGENT,
        excluded_agents=[BLOCKED_AGENT],
        unblocks_todo_id="todo_value_successor",
    )


def assert_open_blocker_waits_on_owner() -> None:
    payload = build_quota_should_run(
        status_payload(
            [handoff_review(status="open")],
            recommended_action="Wait for main-control to review the value explorer handoff.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "agent_scope_wait", payload
    frontier = payload["agent_scope_frontier"]
    assert frontier["action"] == "agent_scope_wait", frontier
    assert frontier["blocking_handoff_claimants"] == [PRIMARY_AGENT], frontier
    assert frontier["blocking_handoff_gates"][0]["gate_state"] == "blocking", frontier


def assert_cleared_blocker_requires_successor_replan() -> None:
    payload = build_quota_should_run(
        status_payload(
            [primary_owned_todo(), handoff_review(status="done")],
            recommended_action="Continue after the review gate clears.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert payload["decision"] == "successor_replan_required", payload
    assert payload["should_run"] is True, payload
    assert payload["effective_action"] == "successor_replan_required", payload
    assert payload["normal_delivery_allowed"] is False, payload
    assert payload.get("agent_lane_next_action") is None, payload
    frontier = payload["agent_scope_frontier"]
    assert frontier["action"] == "successor_replan_required", frontier
    assert frontier["quiet_noop_allowed"] is False, frontier
    assert frontier["cleared_without_successor_handoff_gates"][0]["todo_id"] == "todo_review_gate", frontier
    assert frontier["cleared_without_successor_handoff_gates"][0]["gate_state"] == "cleared_without_successor", frontier
    hint = payload["agent_lane_frontier_hint"]
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0", hint
    assert hint["decision"] == "record_no_followup", hint
    assert hint["source"] == "agent_scope_frontier", hint
    assert hint["reason_code"] == "cleared_handoff_without_successor", hint
    assert hint["target_todo_id"] == "todo_review_gate", hint
    assert hint["quiet_noop_allowed"] is False, hint
    assert "loopx todo complete" in hint["next_cli_action"], hint
    assert frontier["frontier_hint"] == hint, frontier
    assert (
        payload["agent_todo_summary"]["current_agent_cleared_without_successor_handoff_count"] == 1
    ), payload
    contract = payload["interaction_contract"]
    assert contract["agent_channel"]["must_attempt"] is True, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is False, contract
    assert contract["cli_channel"]["spend_allowed_now"] is False, contract
    assert contract["cli_channel"]["spend_after_validation"] is True, contract
    assert contract["user_channel"]["action_required"] is False, contract
    assert any(
        "loopx todo update" in action for action in contract["cli_channel"]["next_cli_actions"]
    ), contract
    assert any(
        "quota spend-slot" in action for action in contract["cli_channel"]["next_cli_actions"]
    ), contract

    scheduler = payload["scheduler_hint"]
    assert scheduler["action"] == "run_now", scheduler
    assert scheduler["cadence_class"] == "active_work", scheduler
    assert scheduler["codex_app"]["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler
    assert scheduler["codex_app"]["recommended_interval_minutes"] == 3, scheduler
    assert scheduler["codex_app"]["stateful_backoff"]["apply_needed"] is True, scheduler
    assert scheduler["codex_app"]["no_spend_for_cadence_change"] is True, scheduler


def assert_stale_handoff_closeout_stays_with_its_executor() -> None:
    payload = build_quota_should_run(
        status_payload(
            [primary_owned_todo(), stale_handoff_closeout()],
            recommended_action="Continue after the blocked side-agent target completed.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "agent_scope_wait", payload
    assert payload["should_run"] is False, payload
    assert payload["effective_action"] == "agent_scope_wait", payload
    frontier = payload["agent_scope_frontier"]
    assert frontier["action"] == "agent_scope_wait", frontier
    gate = frontier["blocking_handoff_gates"][0]
    assert gate["todo_id"] == "todo_stale_review_closeout", frontier
    assert gate["status"] == "open", frontier
    assert gate["unblocks_todo_id"] == "todo_value_explorer_slice", frontier
    assert gate["route_continuation_replan_required"] is True, frontier
    assert "stale handoff closeout" in gate["route_continuation_reason"], frontier
    assert payload["agent_todo_summary"]["current_agent_route_continuation_replan_count"] == 0, payload
    assert payload["agent_todo_summary"]["other_agent_route_continuation_replan_count"] == 1, payload


def assert_status_summary_projects_handoff_gate_state() -> None:
    summary = compact_todo_group(
        [handoff_review(status="done")],
        source_section="Agent Todo",
        role="agent",
    )
    assert summary is not None
    gate = summary["handoff_gates"][0]
    assert gate["schema_version"] == "todo_handoff_gate_v1", gate
    assert gate["gate_state"] == "cleared_without_successor", gate
    assert gate["excluded_agents"] == [BLOCKED_AGENT], gate


def assert_archived_completed_blocker_does_not_wake_agent() -> None:
    summary = compact_todo_group(
        [archived_handoff_review()],
        source_section="Agent Todo",
        role="agent",
    )
    assert summary is not None
    assert "handoff_gates" not in summary, summary

    payload = build_quota_should_run(
        status_payload(
            [primary_owned_todo(), archived_handoff_review()],
            recommended_action="Archived handoff history should not wake the blocked agent.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "reassignment_required", payload
    assert payload["should_run"] is False, payload
    assert payload["agent_scope_frontier"]["action"] == "reassignment_required", payload
    assert "current_agent_handoff_gates" not in payload["agent_todo_summary"], payload
    assert "current_agent_cleared_without_successor_handoff_count" not in (
        payload["agent_todo_summary"]
    ), payload


def assert_existing_successor_runs_normally() -> None:
    payload = build_quota_should_run(
        status_payload(
            [value_successor(), handoff_review(status="done")],
            recommended_action="Continue the reviewed value explorer successor.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "run", payload
    assert payload["effective_action"] == "normal_run", payload
    assert payload["agent_lane_next_action"]["todo_id"] == "todo_value_successor", payload
    assert payload["agent_todo_summary"]["current_agent_handoff_gates"][0]["gate_state"] == (
        "cleared_with_successor"
    ), payload
    assert "agent_scope_frontier" not in payload, payload


def assert_completed_successor_keeps_old_gate_cleared() -> None:
    payload = build_quota_should_run(
        status_payload(
            [
                handoff_review(status="done"),
                completed_value_successor(),
                followup_review_gate(),
            ],
            recommended_action="Wait for the follow-up value connector review.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "agent_scope_wait", payload
    frontier = payload["agent_scope_frontier"]
    assert frontier["action"] == "agent_scope_wait", frontier
    assert (
        frontier["blocking_handoff_gates"][0]["todo_id"] == "todo_followup_review_gate"
    ), frontier
    summary = payload["agent_todo_summary"]
    by_id = {
        item["todo_id"]: item
        for item in summary["current_agent_handoff_gates"]
        if item.get("todo_id")
    }
    assert by_id["todo_review_gate"]["gate_state"] == "cleared_with_successor", summary
    assert by_id["todo_review_gate"]["successor_todo_ids"] == [
        "todo_value_successor"
    ], summary
    assert summary["current_agent_cleared_without_successor_handoff_count"] == 0, payload


def assert_resolved_supersession_does_not_wake_agent() -> None:
    payload = build_quota_should_run(
        status_payload(
            [
                primary_owned_todo(),
                handoff_review(status="done", superseded_by="todo_value_successor"),
                todo_item(todo_id="todo_value_successor", text="Verified replacement",
                          status="done", no_followup=True),
            ],
            recommended_action="Wait for main-control after superseded handoff.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "reassignment_required", payload
    assert payload["should_run"] is False, payload
    assert payload["agent_scope_frontier"]["action"] == "reassignment_required", payload
    summary = payload["agent_todo_summary"]
    assert summary["current_agent_handoff_gates"][0]["gate_state"] == "superseded", payload
    assert summary["current_agent_cleared_without_successor_handoff_count"] == 0, payload


def assert_no_followup_completed_blocker_does_not_wake_agent() -> None:
    payload = build_quota_should_run(
        status_payload(
            [primary_owned_todo(), no_followup_handoff_review()],
            recommended_action="No follow-up is required after the review gate.",
        ),
        goal_id=GOAL_ID,
        agent_id=BLOCKED_AGENT,
    )
    assert payload["decision"] == "reassignment_required", payload
    assert payload["should_run"] is False, payload
    summary = payload["agent_todo_summary"]
    gate = summary["current_agent_handoff_gates"][0]
    assert gate["gate_state"] == "cleared_no_followup", payload
    assert gate["no_followup"] is True, payload
    assert summary["current_agent_cleared_without_successor_handoff_count"] == 0, payload


def main() -> int:
    assert_handoff_gate_lanes_and_ready_successors_are_todo_context_helpers()
    assert_open_blocker_waits_on_owner()
    assert_cleared_blocker_requires_successor_replan()
    assert_stale_handoff_closeout_stays_with_its_executor()
    assert_status_summary_projects_handoff_gate_state()
    assert_archived_completed_blocker_does_not_wake_agent()
    assert_existing_successor_runs_normally()
    assert_completed_successor_keeps_old_gate_cleared()
    assert_resolved_supersession_does_not_wake_agent()
    assert_no_followup_completed_blocker_does_not_wake_agent()
    print("quota-cleared-blocker-successor-gate-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
