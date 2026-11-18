#!/usr/bin/env python3
"""Characterize agent-scope quota projection before module extraction."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.agents.agent_scope import (  # noqa: E402
    _agent_lane_frontier_hint,
    _agent_scope_deferred_resume_candidates,
    _agent_scope_monitor_blocked_resume_candidates,
    _agent_scope_no_candidate_frontier,
    _agent_scope_route_continuation_replan_candidates,
    _agent_scoped_user_todo_override,
    _scoped_user_gate_fallback,
)
from loopx.control_plane.agents.agent_scope_frontier import (  # noqa: E402
    AgentScopeFrontierAction,
    build_agent_scope_frontier_payload,
)


from loopx.control_plane.todos.quota_selection import project_quota_planning  # noqa: E402


GOAL_ID = "agent-scope-projection-characterization"
AGENT_ID = "codex-product-capability"
PRIMARY_AGENT = "codex-main-control"


def todo(
    todo_id: str,
    text: str,
    *,
    index: int,
    role: str = "agent",
    task_class: str = "advancement_task",
    status: str = "open",
    claimed_by: str | None = None,
    blocks_agent: str | None = None,
    action_kind: str | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "schema_version": "todo_item_v0",
        "todo_id": todo_id,
        "index": index,
        "role": role,
        "task_class": task_class,
        "status": status,
        "done": status == "done",
        "text": text,
    }
    if claimed_by:
        item["claimed_by"] = claimed_by
    if blocks_agent:
        item["blocks_agent"] = blocks_agent
    if action_kind:
        item["action_kind"] = action_kind
    item.update(metadata)
    return item


def agent_identity() -> dict[str, Any]:
    return {
        "agent_id": AGENT_ID,
        "agent_model": "peer_v1",
    }


def assert_agent_scope_user_gate_filter_contract() -> None:
    current_gate = todo(
        "todo_current_gate",
        "Approve the product-capability release surface.",
        index=1,
        role="user",
        task_class="user_gate",
        blocks_agent=AGENT_ID,
    )
    other_gate = todo(
        "todo_primary_gate",
        "Approve the primary benchmark run.",
        index=2,
        role="user",
        task_class="user_gate",
        blocks_agent=PRIMARY_AGENT,
    )
    other_claimed_gate = todo(
        "todo_value_gate",
        "Approve the value explorer public reply.",
        index=3,
        role="user",
        task_class="user_gate",
        claimed_by="codex-value-explorer",
    )
    unscoped_gate = todo(
        "todo_global_gate",
        "Approve the shared release note.",
        index=4,
        role="user",
        task_class="user_gate",
    )

    planning = project_quota_planning(
        {}, all_open_items=[current_gate, other_gate, other_claimed_gate, unscoped_gate],
        source_open_count=4, agent_identity=agent_identity(),
        filter_user_gate_blocks_agent=True, available_capabilities=None,
    )
    current_items = planning["lanes"]["blocking_open_items"]
    other_items = planning["lanes"]["other_agent_scoped_items"]
    projection = planning["lanes"]["agent_scope_filter"]

    assert [item["todo_id"] for item in current_items] == [
        "todo_current_gate",
        "todo_global_gate",
    ], current_items
    assert [item["todo_id"] for item in other_items] == [
        "todo_primary_gate",
        "todo_value_gate",
    ], other_items
    assert projection == {
        "schema_version": "agent_scoped_user_gate_filter_v0",
        "agent_id": AGENT_ID,
        "policy": (
            "user todos scoped to another agent by blocks_agent or claimed_by "
            "remain visible but do not block this agent's quota lane"
        ),
        "current_agent_blocking_open_count": 2,
        "other_agent_scoped_open_count": 2,
    }, projection


def assert_scoped_user_gate_fallback_contract() -> None:
    gated = todo(
        "todo_gated_benchmark",
        "[P0] Run the owner-selected benchmark target.",
        index=1,
        claimed_by=PRIMARY_AGENT,
        action_kind="benchmark_run",
    )
    fallback = todo(
        "todo_public_doc_cleanup",
        "[P1] Clean public-safe release docs while the benchmark gate waits.",
        index=2,
        claimed_by=PRIMARY_AGENT,
        action_kind="docs_cleanup",
    )
    user_gate = todo(
        "todo_benchmark_gate",
        "Owner must choose the benchmark target before benchmark execution.",
        index=1,
        role="user",
        task_class="user_gate",
        action_kind="benchmark_run",
        blocks_agent=PRIMARY_AGENT,
        unblocks_todo_id="todo_gated_benchmark",
    )
    user_summary = {
        "open_count": 1,
        "gate_open_items": [user_gate],
        "first_open_items": [user_gate],
    }
    agent_summary = {
        "executable_backlog_items": [gated, fallback],
        "claim_scope": {"agent_id": PRIMARY_AGENT},
    }

    fallback_projection = _scoped_user_gate_fallback(
        user_summary,
        agent_summary,
    )

    assert fallback_projection is not None, fallback_projection
    assert fallback_projection["schema_version"] == "scoped_user_gate_fallback_v0"
    assert fallback_projection["kind"] == "scoped_user_gate_fallback"
    assert fallback_projection["requires_user_action"] is True
    assert fallback_projection["blocked_user_gate"]["todo_id"] == "todo_benchmark_gate"
    assert [item["todo_id"] for item in fallback_projection["blocked_agent_items"]] == [
        "todo_gated_benchmark"
    ]
    assert fallback_projection["selected_executable"]["todo_id"] == "todo_public_doc_cleanup"

    no_fallback = _scoped_user_gate_fallback(
        user_summary,
        {"executable_backlog_items": [gated], "claim_scope": {"agent_id": PRIMARY_AGENT}},
    )
    assert no_fallback is None, no_fallback


def assert_agent_scoped_user_gate_override_contract() -> None:
    user_summary = {
        "open_count": 0,
        "other_agent_scoped_open_count": 1,
    }
    agent_summary = {
        "first_executable_items": [
            todo(
                "todo_product_work",
                "[P1] Continue product capability characterization.",
                index=1,
                claimed_by=AGENT_ID,
            )
        ]
    }
    override = _agent_scoped_user_todo_override(
        state="operator_gate",
        item={"goal_id": GOAL_ID},
        user_todo_summary=user_summary,
        agent_todo_summary=agent_summary,
        agent_identity=agent_identity(),
    )

    assert override == {
        "schema_version": "agent_scoped_user_gate_override_v0",
        "kind": "agent_scoped_user_gate_override",
        "agent_id": AGENT_ID,
        "from_state": "operator_gate",
        "to_state": "eligible",
        "other_agent_scoped_open_count": 1,
        "selected_action": "[P1] Continue product capability characterization.",
        "reason": (
            "the open user gate is scoped to another agent via blocks_agent or claimed_by; "
            "this agent still has an executable in-scope todo"
        ),
        "item_patch": {
            "status": "active_state_agent_todo",
            "waiting_on": "codex",
            "recommended_action": "[P1] Continue product capability characterization.",
        },
    }, override

    blocked = _agent_scoped_user_todo_override(
        state="operator_gate",
        item={"goal_id": GOAL_ID, "operator_question": "Need owner input."},
        user_todo_summary=user_summary,
        agent_todo_summary=agent_summary,
        agent_identity=agent_identity(),
    )
    assert blocked is None, blocked


def assert_agent_scope_frontier_builder_contract() -> None:
    payload = build_agent_scope_frontier_payload(
        agent_id=AGENT_ID,
        action=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED,
        quiet_noop_allowed=False,
        spend_policy="spend once after validated successor replan/todo writeback",
        reason="frontier has no runnable advancement but a successor is required",
        recommended_action="record a successor todo or an explicit no-follow-up rationale",
        requires_replan=True,
        candidate_counts={
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 0,
            "cleared_without_successor_handoff_count": 1,
        },
        extra_fields={"cleared_without_successor_handoff_gates": [{"todo_id": "todo_gate"}]},
    )

    assert payload["schema_version"] == "agent_scope_frontier_v1"
    assert payload["action"] == "successor_replan_required"
    assert "effective_action" not in payload
    assert payload["blocks_delivery"] is True
    assert payload["requires_replan"] is True
    assert payload["quiet_noop_allowed"] is False
    assert payload["cleared_without_successor_handoff_gates"][0]["todo_id"] == "todo_gate"


def assert_agent_scope_frontier_and_hint_contract() -> None:
    blocked_resume = todo(
        "todo_waiting_advancement",
        "[P1] Resume product slice after monitor evidence closes.",
        index=1,
        claimed_by=AGENT_ID,
        resume_ready=False,
        # Frontier consumes an already diagnosed resume-planning projection.
        blocking_monitor_todo_id="todo_open_monitor",
        resume_condition={
            "target_status": "open",
            "target_task_class": "continuous_monitor",
            "target_todo_id": "todo_open_monitor",
        },
    )
    agent_summary = {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_priority_open_items": [],
        "current_agent_monitor_blocked_resume_candidates": [blocked_resume],
    }
    work_lane = {
        "lane": "advancement_task",
        "must_attempt_work": True,
        "obligation": "open_agent_todo",
    }

    frontier = _agent_scope_no_candidate_frontier(
        agent_identity=agent_identity(),
        agent_todo_summary=agent_summary,
        agent_lane_next_action=None,
        work_lane_contract=work_lane,
        candidate_should_run=True,
    )

    assert frontier is not None, frontier
    assert frontier["schema_version"] == "agent_scope_frontier_v1"
    assert frontier["action"] == "successor_replan_required"
    assert frontier["requires_replan"] is True
    assert frontier["quiet_noop_allowed"] is False
    assert frontier["candidate_counts"] == {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0,
        "monitor_blocked_resume_candidate_count": 1,
    }, frontier
    assert frontier["monitor_blocked_resume_candidates"][0]["todo_id"] == "todo_waiting_advancement"
    assert (
        frontier["monitor_blocked_resume_candidates"][0]["blocking_monitor_todo_id"]
        == "todo_open_monitor"
    )

    hint = _agent_lane_frontier_hint(
        goal_id=GOAL_ID,
        agent_identity=agent_identity(),
        agent_todo_summary=agent_summary,
        agent_lane_next_action=None,
        agent_scope_frontier=frontier,
        work_lane_contract=work_lane,
    )

    assert hint is not None, hint
    assert hint["schema_version"] == "agent_lane_frontier_hint_v0"
    assert hint["decision"] == "add_next_advancement"
    assert hint["source"] == "agent_scope_frontier"
    assert hint["reason_code"] == "resume_blocked_by_open_monitor"
    assert hint["target_todo_id"] == "todo_waiting_advancement"
    assert hint["quiet_noop_allowed"] is False


def assert_executor_exclusions_survive_precomputed_resume_lanes() -> None:
    excluded_ready = todo(
        "todo_excluded_ready",
        "[P1] Ready work owned by another peer.",
        index=1,
        status="deferred",
        resume_ready=True,
        excluded_agents=[AGENT_ID],
    )
    eligible_ready = todo(
        "todo_eligible_ready",
        "[P1] Ready work available to this peer.",
        index=2,
        status="deferred",
        resume_ready=True,
    )
    ready = _agent_scope_deferred_resume_candidates(
        {
            "unclaimed_deferred_resume_candidates": [
                excluded_ready,
                eligible_ready,
            ]
        },
        agent_id=AGENT_ID,
    )
    assert [item["todo_id"] for item in ready] == ["todo_eligible_ready"], ready

    blocked_condition = {
        "target_status": "open",
        "target_task_class": "continuous_monitor",
        "target_todo_id": "todo_monitor",
    }
    excluded_blocked = todo(
        "todo_excluded_blocked",
        "[P1] Monitor-gated work owned by another peer.",
        index=3,
        resume_ready=False,
        resume_condition=blocked_condition,
        excluded_agents=[AGENT_ID],
    )
    assert (
        _agent_scope_monitor_blocked_resume_candidates(
            {"unclaimed_monitor_blocked_resume_candidates": [excluded_blocked]},
            agent_id=AGENT_ID,
        )
        == []
    )

    excluded_route = todo(
        "todo_excluded_route",
        "[P1] Route continuation owned by another peer.",
        index=4,
        route_continuation_replan_required=True,
        excluded_agents=[AGENT_ID],
    )
    assert (
        _agent_scope_route_continuation_replan_candidates(
            {"unclaimed_route_continuation_replan_candidates": [excluded_route]},
            agent_id=AGENT_ID,
        )
        == []
    )


def assert_other_agent_frontier_wait_contract() -> None:
    other_agent_item = todo(
        "todo_primary_owned",
        "[P0] Primary owns the remaining benchmark fix.",
        index=1,
        claimed_by=PRIMARY_AGENT,
    )
    agent_summary = {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_priority_open_items": [],
        "claimed_advancement_open_items": [other_agent_item],
        "claim_scope": {"other_agent_claimed_open_count": 1},
    }
    work_lane = {
        "lane": "advancement_task",
        "must_attempt_work": True,
        "obligation": "open_agent_todo",
    }

    frontier = _agent_scope_no_candidate_frontier(
        agent_identity=agent_identity(),
        agent_todo_summary=agent_summary,
        agent_lane_next_action=None,
        work_lane_contract=work_lane,
        candidate_should_run=True,
    )

    assert frontier is not None, frontier
    assert frontier["action"] == "reassignment_required"
    assert frontier["quiet_noop_allowed"] is True
    assert frontier["spend_policy"] == (
        "no quota spend while the current agent has no in-scope runnable candidate"
    )
    assert frontier["candidate_counts"] == {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 1,
        "other_agent_claimed_open_count": 1,
    }, frontier
    assert frontier["other_claimants"] == [PRIMARY_AGENT]
    assert frontier["other_agent_claimed_items"][0]["todo_id"] == "todo_primary_owned"

    hint = _agent_lane_frontier_hint(
        goal_id=GOAL_ID,
        agent_identity=agent_identity(),
        agent_todo_summary=agent_summary,
        agent_lane_next_action=None,
        agent_scope_frontier=frontier,
        work_lane_contract=work_lane,
    )

    assert hint is not None, hint
    assert hint["decision"] == "quiet_noop_blocker"
    assert hint["reason_code"] == "blocked_by_other_agent_frontier"
    assert hint["target_todo_id"] == "todo_primary_owned"
    assert hint["quiet_noop_allowed"] is True


def assert_no_candidate_rule_precedence() -> None:
    monitor_blocked = todo(
        "todo_monitor_blocked",
        "[P1] Resume after monitor evidence.",
        index=1,
        claimed_by=AGENT_ID,
        resume_ready=False,
        resume_condition={
            "target_status": "open",
            "target_task_class": "continuous_monitor",
            "target_todo_id": "todo_monitor",
        },
    )
    deferred_ready = todo(
        "todo_deferred_ready",
        "[P1] Resume the ready deferred successor.",
        index=2,
        status="deferred",
        claimed_by=AGENT_ID,
        resume_ready=True,
    )
    route_replan = todo(
        "todo_route_replan",
        "[P1] Continue the projected route.",
        index=3,
        route_continuation_replan_required=True,
    )
    other_agent_item = todo(
        "todo_primary_owned",
        "[P1] Work owned by another peer.",
        index=4,
        claimed_by=PRIMARY_AGENT,
    )
    base_summary = {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_priority_open_items": [],
        "claimed_advancement_open_items": [other_agent_item],
        "claim_scope": {"other_agent_claimed_open_count": 1},
    }
    cases = [
        (
            {
                "current_agent_monitor_blocked_resume_candidates": [monitor_blocked],
                "current_agent_deferred_resume_candidates": [deferred_ready],
                "unclaimed_route_continuation_replan_candidates": [route_replan],
            },
            "monitor_blocked_resume_candidates",
            "successor_replan_required",
        ),
        (
            {
                "current_agent_deferred_resume_candidates": [deferred_ready],
                "unclaimed_route_continuation_replan_candidates": [route_replan],
            },
            "deferred_resume_candidates",
            "successor_replan_required",
        ),
        (
            {"unclaimed_route_continuation_replan_candidates": [route_replan]},
            "route_continuation_replan_candidates",
            "successor_replan_required",
        ),
        ({}, "other_agent_claimed_items", "reassignment_required"),
    ]
    for extra_summary, expected_field, expected_action in cases:
        frontier = _agent_scope_no_candidate_frontier(
            agent_identity=agent_identity(),
            agent_todo_summary={**base_summary, **extra_summary},
            agent_lane_next_action=None,
            work_lane_contract={
                "lane": "advancement_task",
                "must_attempt_work": True,
                "obligation": "open_agent_todo",
            },
            candidate_should_run=True,
        )
        assert frontier is not None, (expected_field, frontier)
        assert frontier["action"] == expected_action, frontier
        assert expected_field in frontier, frontier


def main() -> None:
    assert_agent_scope_user_gate_filter_contract()
    assert_scoped_user_gate_fallback_contract()
    assert_agent_scoped_user_gate_override_contract()
    assert_agent_scope_frontier_builder_contract()
    assert_agent_scope_frontier_and_hint_contract()
    assert_executor_exclusions_survive_precomputed_resume_lanes()
    assert_other_agent_frontier_wait_contract()
    assert_no_candidate_rule_precedence()
    print("agent-scope-projection-characterization-smoke ok")


if __name__ == "__main__":
    main()
