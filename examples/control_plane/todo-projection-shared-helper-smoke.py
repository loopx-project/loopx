#!/usr/bin/env python3
"""Smoke-test shared todo projection ordering across status and quota."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import (  # noqa: E402
    _todo_projection_sort_key as quota_todo_projection_sort_key,
    build_quota_should_run,
)
from loopx.control_plane.quota.should_run_prepare import (  # noqa: E402
    _todo_task_class as quota_todo_task_class,
)
from loopx.control_plane.testing.quota_fixtures import (  # noqa: E402
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.agents.agent_scope import (  # noqa: E402
    agent_scope_blocking_handoff_gates,
    agent_scope_count_advancement_items,
    agent_scope_item_claimed_by_agent_or_unclaimed,
    agent_scope_item_matches_agent_or_unclaimed,
)
from loopx.control_plane.goals.goal_frontier import (  # noqa: E402
    build_goal_frontier_projection_from_summaries,
)
from loopx.status import (  # noqa: E402
    claimed_visibility_items as status_claimed_visibility_items,
    todo_item_is_deferred as status_todo_item_is_deferred,
    todo_projection_sort_key,
)
from loopx.control_plane.todos.contract import (  # noqa: E402
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
)
from loopx.control_plane.todos.projection import (  # noqa: E402
    todo_claimed_visibility_items as shared_claimed_visibility_items,
    todo_item_claimed_by_agent_or_unclaimed as shared_todo_item_claimed_by_agent_or_unclaimed,
    todo_item_is_deferred as shared_todo_item_is_deferred,
    todo_projection_sort_key as shared_todo_projection_sort_key,
    todo_summary_first_executable_item as shared_first_executable_todo_item,
    todo_summary_monitor_items as shared_todo_summary_monitor_items,
    todo_summary_open_task_counts as shared_todo_summary_open_task_counts,
)
from loopx.control_plane.scheduler.external_evidence_observation import (  # noqa: E402
    projected_monitor_handle,
)


GOAL_ID = "todo-projection-shared-helper-goal"
AGENT_ID = "codex-product-capability"


def build_agent_todo_summary() -> dict:
    return quota_todo_summary(
        [
            quota_todo_item(
                todo_id="todo_advancement_p2",
                index=4,
                priority="P2",
                text="[P2] Continue low-risk canary cleanup after the core projection parity lands.",
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
            ),
            quota_todo_item(
                todo_id="todo_monitor_unscheduled",
                index=5,
                priority="P2",
                text="[P2] Monitor unscheduled public smoke signal after schedule metadata is added.",
                task_class=TODO_TASK_CLASS_MONITOR,
                target_key="public-smoke:unscheduled",
            ),
            quota_todo_item(
                todo_id="todo_monitor_p0",
                index=2,
                text="[P0] Monitor public smoke signal and only write back if it changed.",
                task_class=TODO_TASK_CLASS_MONITOR,
                next_due_at="2026-01-01T00:00:00+00:00",
                target_key="public-smoke:due",
            ),
            quota_todo_item(
                todo_id="todo_advancement_p0",
                index=3,
                text="[P0] Extract todo projection helper for status and quota parity.",
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
                claimed_by=AGENT_ID,
            ),
            quota_todo_item(
                todo_id="todo_advancement_p1",
                index=1,
                priority="P1",
                text="[P1] Add characterization before moving more control-plane code.",
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
            ),
        ],
        role="agent",
    )


def status_payload(agent_todos: dict) -> dict:
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Use the first executable advancement todo.",
        next_action="Use the first executable advancement todo.",
        agent_todos=agent_todos,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": ["codex-main-control", AGENT_ID],
        },
        latest_runs=[],
    )


def assert_status_summary_lanes(summary: dict) -> None:
    assert [item["todo_id"] for item in summary["first_open_items"]] == [
        "todo_monitor_p0",
        "todo_advancement_p0",
        "todo_advancement_p1",
    ], summary
    assert [item["todo_id"] for item in summary["first_executable_items"]] == [
        "todo_advancement_p0",
        "todo_advancement_p1",
        "todo_advancement_p2",
    ], summary
    assert [item["todo_id"] for item in summary["monitor_due_items"]] == [
        "todo_monitor_p0",
    ], summary
    assert summary["monitor_due_count"] == 1, summary
    assert [item["todo_id"] for item in summary["monitor_schedule_gap_items"]] == [
        "todo_monitor_unscheduled",
    ], summary
    assert summary["monitor_schedule_gap_count"] == 1, summary


def assert_shared_ordering_parity(summary: dict) -> None:
    first_open = [item for item in summary["first_open_items"] if isinstance(item, dict)]
    assert [item["todo_id"] for item in sorted(first_open, key=todo_projection_sort_key)] == [
        "todo_monitor_p0",
        "todo_advancement_p0",
        "todo_advancement_p1",
    ], first_open
    assert [item["todo_id"] for item in sorted(first_open, key=quota_todo_projection_sort_key)] == [
        "todo_monitor_p0",
        "todo_advancement_p0",
        "todo_advancement_p1",
    ], first_open
    assert [item["todo_id"] for item in sorted(first_open, key=shared_todo_projection_sort_key)] == [
        "todo_monitor_p0",
        "todo_advancement_p0",
        "todo_advancement_p1",
    ], first_open
    embedded_priority = {
        "index": 9,
        "text": "Keep this text with embedded P0 wording but no bracket prefix.",
    }
    assert todo_projection_sort_key(embedded_priority) == (50, 9), embedded_priority
    assert quota_todo_projection_sort_key(embedded_priority) == (0, 9), embedded_priority


def assert_claimed_visibility_parity() -> None:
    items = [
        quota_todo_item(
            todo_id="todo_a1",
            index=1,
            priority="P1",
            text="[P1] Claimed by A one.",
            task_class=TODO_TASK_CLASS_ADVANCEMENT,
            claimed_by="agent-a",
        ),
        quota_todo_item(
            todo_id="todo_a2",
            index=2,
            priority="P1",
            text="[P1] Claimed by A two.",
            task_class=TODO_TASK_CLASS_ADVANCEMENT,
            claimed_by="agent-a",
        ),
        quota_todo_item(
            todo_id="todo_b1",
            index=3,
            priority="P1",
            text="[P1] Claimed by B one.",
            task_class=TODO_TASK_CLASS_ADVANCEMENT,
            claimed_by="agent-b",
        ),
        quota_todo_item(
            todo_id="todo_unclaimed",
            index=4,
            priority="P1",
            text="[P1] Unclaimed filler.",
            task_class=TODO_TASK_CLASS_ADVANCEMENT,
        ),
    ]
    for selector in (
        shared_claimed_visibility_items,
        status_claimed_visibility_items,
    ):
        selected_two = selector(items, limit=2)
        assert [item["todo_id"] for item in selected_two] == ["todo_a1", "todo_b1"], selected_two
        selected_three = selector(items, limit=3)
        assert [item["todo_id"] for item in selected_three] == [
            "todo_a1",
            "todo_a2",
            "todo_b1",
        ], selected_three
    claimed_by_current = items[0]
    claimed_by_other = items[2]
    unclaimed = items[3]
    for predicate in (
        shared_todo_item_claimed_by_agent_or_unclaimed,
        agent_scope_item_claimed_by_agent_or_unclaimed,
    ):
        assert predicate(claimed_by_current, agent_id="agent-a") is True, claimed_by_current
        assert predicate(claimed_by_other, agent_id="agent-a") is False, claimed_by_other
        assert predicate(unclaimed, agent_id="agent-a") is True, unclaimed


def assert_agent_scope_frontier_routing_parity() -> None:
    current = {
        "todo_id": "todo_current_spaced_claim",
        "index": 1,
        "priority": "P1",
        "text": "[P1] Current agent with whitespace-normalized claim.",
        "task_class": TODO_TASK_CLASS_ADVANCEMENT,
        "claimed_by": " agent-a ",
    }
    other = {
        "todo_id": "todo_other",
        "index": 2,
        "priority": "P1",
        "text": "[P1] Other agent work.",
        "task_class": TODO_TASK_CLASS_ADVANCEMENT,
        "claimed_by": "agent-b",
    }
    unclaimed = {
        "todo_id": "todo_unclaimed_scope",
        "index": 3,
        "priority": "P1",
        "text": "[P1] Unclaimed shared lane work.",
        "task_class": TODO_TASK_CLASS_ADVANCEMENT,
    }
    excluded_for_current = {
        "todo_id": "todo_excludes_current",
        "index": 4,
        "priority": "P1",
        "text": "[P1] Handoff work excluding the current agent.",
        "task_class": TODO_TASK_CLASS_ADVANCEMENT,
        "claimed_by": "agent-b",
        "excluded_agents": ["agent-a"],
        "unblocks_todo_id": "todo_current_spaced_claim",
    }
    excluded_for_other = {
        "todo_id": "todo_excludes_other",
        "index": 5,
        "priority": "P1",
        "text": "[P1] Handoff work excluding a different agent.",
        "task_class": TODO_TASK_CLASS_ADVANCEMENT,
        "claimed_by": "agent-a",
        "excluded_agents": ["agent-b"],
        "unblocks_todo_id": "todo_other",
    }
    assert agent_scope_count_advancement_items(
        [current, other, unclaimed],
        claimed_by="agent-a",
    ) == 1
    assert agent_scope_count_advancement_items(
        [current, other, unclaimed],
        claimed_by="__unclaimed__",
    ) == 1
    assert agent_scope_item_matches_agent_or_unclaimed(
        excluded_for_current,
        agent_id="agent-a",
    ) is False
    assert agent_scope_item_matches_agent_or_unclaimed(
        excluded_for_other,
        agent_id="agent-a",
    ) is True

    agent_summary = {
        "executable_backlog_items": [current, other, unclaimed],
        "unclaimed_priority_open_items": [unclaimed],
        "claim_scope": {"other_agent_claimed_items": [other]},
        "deferred_resume_candidates": [
            {
                "todo_id": "todo_deferred_current",
                "text": "[P1] Current agent deferred successor.",
                "task_class": TODO_TASK_CLASS_ADVANCEMENT,
                "claimed_by": " agent-a ",
            },
            {
                "todo_id": "todo_deferred_other",
                "text": "[P1] Other agent deferred successor.",
                "task_class": TODO_TASK_CLASS_ADVANCEMENT,
                "claimed_by": "agent-b",
            },
        ],
        "handoff_gates": [
            {
                "todo_id": "todo_gate_current",
                "index": 6,
                "text": "[P1] Current agent blocking handoff.",
                "task_class": TODO_TASK_CLASS_ADVANCEMENT,
                "excluded_agents": [" agent-a "],
                "unblocks_todo_id": "todo_deferred_current",
                "gate_state": "blocking",
            },
            {
                "todo_id": "todo_gate_other",
                "index": 7,
                "text": "[P1] Other agent blocking handoff.",
                "task_class": TODO_TASK_CLASS_ADVANCEMENT,
                "excluded_agents": ["agent-b"],
                "unblocks_todo_id": "todo_deferred_other",
                "gate_state": "blocking",
            },
            {
                "todo_id": "todo_gate_cleared",
                "index": 8,
                "text": "[P1] Current agent cleared handoff.",
                "task_class": TODO_TASK_CLASS_ADVANCEMENT,
                "excluded_agents": ["agent-a"],
                "unblocks_todo_id": "todo_deferred_current",
                "gate_state": "cleared_without_successor",
            },
        ],
    }
    projection = build_goal_frontier_projection_from_summaries(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        user_todo_summary={"open_count": 0},
        agent_todo_summary=agent_summary,
        work_lane_contract={"lane": TODO_TASK_CLASS_ADVANCEMENT},
        replan_obligation=None,
    )
    frontier = projection["remaining_advancement_frontier"]
    assert frontier["current_agent_claimed_advancement_count"] == 1, projection
    assert frontier["unclaimed_advancement_count"] == 1, projection
    assert frontier["other_agent_claimed_advancement_count"] == 1, projection
    assert projection["deferred_successors"]["current_agent_ready_count"] == 1, projection
    blocking = agent_scope_blocking_handoff_gates(agent_summary, agent_id="agent-a")
    assert [item["todo_id"] for item in blocking] == ["todo_gate_current"], blocking


def assert_deferred_helper_parity() -> None:
    deferred = quota_todo_item(
        todo_id="todo_deferred",
        index=6,
        priority="P2",
        text="[P2] Resume after dependency lands.",
        task_class=TODO_TASK_CLASS_ADVANCEMENT,
        status="deferred",
    )
    open_item = quota_todo_item(
        todo_id="todo_open",
        index=7,
        priority="P2",
        text="[P2] Still executable.",
        task_class=TODO_TASK_CLASS_ADVANCEMENT,
    )
    for predicate in (
        shared_todo_item_is_deferred,
        status_todo_item_is_deferred,
    ):
        assert predicate(deferred) is True, deferred
        assert predicate(open_item) is False, open_item


def assert_monitor_item_collection_parity(summary: dict) -> None:
    # Preserve current lane-projection semantics: repeated references to the
    # same dict object are de-duplicated, while separate projected copies of the
    # same todo_id can appear across monitor summary lanes. This is not a
    # long-term identity contract; pin it before replacing it with stable lane
    # keys.
    expected_ids = [
        "todo_monitor_p0",
        "todo_monitor_p0",
        "todo_monitor_unscheduled",
        "todo_monitor_p0",
    ]
    selected_ids = [item["todo_id"] for item in shared_todo_summary_monitor_items(summary)]
    assert selected_ids == expected_ids, selected_ids

    handle = projected_monitor_handle(summary)
    assert isinstance(handle, dict), summary
    assert handle["schema_version"] == "projected_monitor_handle_v0", handle
    assert handle["todo_id"] == "todo_monitor_p0", handle


def assert_open_task_count_state_machine(summary: dict) -> None:
    counts = shared_todo_summary_open_task_counts(summary)
    assert counts == {
        "open": 5,
        "advancement": 3,
        "monitor": 2,
        "monitor_due": 1,
        "monitor_schedule_gap": 1,
        "hidden": 0,
        "complete": True,
    }, counts

    hidden_summary = {
        "open_count": 3,
        "first_open_items": [
            quota_todo_item(
                todo_id="todo_visible_advancement",
                index=1,
                priority="P1",
                text="[P1] Visible advancement work.",
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
            )
        ],
    }
    hidden_counts = shared_todo_summary_open_task_counts(hidden_summary)
    assert hidden_counts["open"] == 3, hidden_counts
    # A bounded legacy projection cannot classify the two unseen items.
    assert hidden_counts["advancement"] == 1, hidden_counts
    assert hidden_counts["complete"] is False, hidden_counts
    assert hidden_counts["monitor"] == 0, hidden_counts
    assert hidden_counts["hidden"] == 2, hidden_counts

    explicit_backlog_summary = {
        "open_count": 4,
        "executable_backlog_items": [
            quota_todo_item(
                todo_id="todo_explicit_backlog",
                index=4,
                priority="P2",
                text="[P2] Explicit executable backlog item.",
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
            )
        ],
        "monitor_open_items": [
            quota_todo_item(
                todo_id="todo_explicit_monitor",
                index=5,
                priority="P2",
                text="[P2] Explicit monitor backlog item.",
                task_class=TODO_TASK_CLASS_MONITOR,
                target_key="public-smoke:explicit",
            )
        ],
    }
    explicit_counts = shared_todo_summary_open_task_counts(explicit_backlog_summary)
    assert explicit_counts["open"] == 4, explicit_counts
    assert explicit_counts["advancement"] == 1, explicit_counts
    assert explicit_counts["monitor"] == 1, explicit_counts


def assert_first_executable_item_parity(summary: dict) -> None:
    selected = shared_first_executable_todo_item(summary)
    assert isinstance(selected, dict), summary
    assert selected["todo_id"] == "todo_advancement_p0", selected


def assert_quota_uses_executable_advancement(summary: dict) -> None:
    payload = build_quota_should_run(
        status_payload(summary),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    quota_summary = payload["agent_todo_summary"]
    assert quota_summary["first_open_items"][0]["todo_id"] == "todo_advancement_p0", payload
    assert quota_summary["first_executable_items"][0]["todo_id"] == "todo_advancement_p0", payload
    assert quota_summary["monitor_open_items"][0]["todo_id"] == "todo_monitor_p0", payload
    assert quota_summary["monitor_schedule_gap_items"][0]["todo_id"] == (
        "todo_monitor_unscheduled"
    ), payload
    assert quota_summary["monitor_schedule_gap_count"] == 1, payload
    assert quota_todo_task_class(quota_summary["monitor_open_items"][0]) == TODO_TASK_CLASS_MONITOR
    assert quota_todo_task_class(
        quota_summary["first_executable_items"][0]
    ) == TODO_TASK_CLASS_ADVANCEMENT
    lane = payload["work_lane_contract"]
    assert lane["lane"] == "advancement_task", payload
    assert payload["agent_lane_next_action"]["todo_id"] == "todo_advancement_p0", payload


def main() -> int:
    summary = build_agent_todo_summary()
    assert_status_summary_lanes(summary)
    assert_shared_ordering_parity(summary)
    assert_claimed_visibility_parity()
    assert_agent_scope_frontier_routing_parity()
    assert_deferred_helper_parity()
    assert_monitor_item_collection_parity(summary)
    assert_open_task_count_state_machine(summary)
    assert_first_executable_item_parity(summary)
    assert_quota_uses_executable_advancement(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
