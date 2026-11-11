#!/usr/bin/env python3
"""Characterize quota/status control-plane decisions before refactors.

This smoke is intentionally fixture-only. It pins current high-risk routing
behavior so later quota.py/status.py decomposition can move code behind stable
contracts before changing semantics.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.review_packet import build_review_packet  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (  # noqa: E402
    quota_status_payload,
    quota_todo_item as todo_item,
    quota_todo_summary,
)


APP_SCHEDULER_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


GOAL_ID = "control-plane-risk-characterization"
AGENT_ID = "codex-product-capability"
PRIMARY_AGENT_ID = "codex-main-control"


def status_payload(
    agent_items: list[dict[str, Any]],
    *,
    agent_todos: dict[str, Any] | None = None,
    user_items: list[dict[str, Any]] | None = None,
    quota_state: str = "eligible",
    safe_bypass: bool = False,
) -> dict[str, Any]:
    next_action = "Goal-level route should remain human-facing."
    return quota_status_payload(
        goal_id=GOAL_ID,
        status=(
            "operator_gate"
            if quota_state == "operator_gate"
            else "active_state_agent_todo"
        ),
        source="active_state",
        project_asset_source="project_asset",
        recommended_action=next_action,
        next_action=next_action,
        active_state_next_action=next_action,
        agent_todo_items=agent_items,
        agent_todos=agent_todos,
        user_todo_items=user_items or [],
        quota_state=quota_state,
        safe_bypass=safe_bypass,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT_ID, AGENT_ID],
        },
        latest_runs=[],
        registry_status="active",
    )


def assert_agent_lane_delivery() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_characterize",
                title="Characterize the control-plane route.",
                claimed_by=AGENT_ID,
            )
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["decision"] == "run", quota
    assert quota["effective_action"] == "normal_run", quota
    assert quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, quota
    lane = quota["agent_lane_next_action"]
    assert lane["schema_version"] == "agent_lane_next_action_v0", lane
    assert lane["todo_id"] == "todo_characterize", lane
    assert lane["selected_by"] == "current_agent_claimed_todo", lane
    assert lane["preserves_goal_next_action"] is True, lane

    packet = build_review_packet(payload, goal_id=GOAL_ID)
    assert packet["ok"] is True, packet
    assert packet["project_asset_source"] == "project_asset", packet
    assert packet["agent_todo_items"] == [
        "[P0] Characterize the control-plane route. claimed_by=codex-product-capability"
    ], packet


def assert_foreign_operator_gate_keeps_normal_admission() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_refactor_slice",
                title="Continue the refactor slice.",
                claimed_by=AGENT_ID,
            )
        ],
        user_items=[
            todo_item(
                todo_id="todo_primary_gate",
                title="Primary agent reviews an unrelated benchmark gate.",
                role="user",
                task_class="user_gate",
                blocks_agent=PRIMARY_AGENT_ID,
            )
        ],
        quota_state="operator_gate",
        safe_bypass=True,
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["should_run"] is True, quota
    assert quota["effective_action"] == "normal_run", quota
    assert quota["safe_bypass_allowed"] is False, quota
    scoped = quota["agent_scoped_user_gate_override"]
    assert scoped["agent_id"] == AGENT_ID, scoped
    assert scoped["from_state"] == "operator_gate", scoped
    assert scoped["to_state"] == "eligible", scoped
    assert quota["interaction_contract"]["user_channel"]["action_required"] is False, quota
    assert quota["agent_lane_next_action"]["todo_id"] == "todo_refactor_slice", quota


def assert_due_monitor_context_does_not_steal_advancement() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_advancement",
                title="Run the implementation slice.",
                claimed_by=AGENT_ID,
                index=1,
            ),
            todo_item(
                todo_id="todo_monitor",
                title="Poll a lower-priority scheduled monitor.",
                priority="P1",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                index=2,
                next_due_at="2026-01-01T00:00:00+00:00",
            ),
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    contract = quota["work_lane_contract"]
    assert contract["lane"] == "advancement_task", contract
    assert "due_monitor_context" in contract["reason_codes"], contract
    assert quota["agent_lane_next_action"]["todo_id"] == "todo_advancement", quota


def assert_current_agent_claimed_advancement_beats_other_agent_frontier() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_primary_first",
                title="Primary agent owns earlier work.",
                claimed_by=PRIMARY_AGENT_ID,
                index=1,
            ),
            todo_item(
                todo_id="todo_side_advancement",
                title="Side agent owns the implementation slice.",
                claimed_by=AGENT_ID,
                index=2,
            ),
            todo_item(
                todo_id="todo_side_monitor",
                title="Side agent has due monitor context.",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                index=3,
                next_due_at="2026-01-01T00:00:00+00:00",
            ),
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["decision"] == "run", quota
    assert quota["effective_action"] == "normal_run", quota

    summary = quota["agent_todo_summary"]
    assert summary["first_executable_items"][0]["todo_id"] == (
        "todo_side_advancement"
    ), summary
    assert summary["current_agent_claimed_advancement_items"][0]["todo_id"] == (
        "todo_side_advancement"
    ), summary
    assert summary["current_agent_claimed_monitor_items"][0]["todo_id"] == (
        "todo_side_monitor"
    ), summary
    assert summary["claimed_by_others_items"][0]["todo_id"] == (
        "todo_primary_first"
    ), summary
    claim_scope = summary["claim_scope"]
    assert claim_scope["selection_order"] == (
        "current_agent_claimed_then_unclaimed"
    ), claim_scope
    assert claim_scope["other_agent_claimed_weight"] == "diagnostic_only", claim_scope
    assert claim_scope["blocked_claimed_items"][0]["todo_id"] == (
        "todo_primary_first"
    ), claim_scope

    contract = quota["work_lane_contract"]
    assert contract["lane"] == "advancement_task", contract
    assert "due_monitor_context" in contract["reason_codes"], contract
    lane = quota["agent_lane_next_action"]
    assert lane["todo_id"] == "todo_side_advancement", lane
    assert lane["selected_by"] == "current_agent_claimed_todo", lane
    assert lane["source"] == "agent_todo_summary.first_executable_items", lane

    primary_quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=PRIMARY_AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert primary_quota["agent_lane_next_action"]["todo_id"] == (
        "todo_primary_first"
    ), primary_quota
    primary_scope = primary_quota["agent_todo_summary"]["claim_scope"]
    assert primary_scope["other_agent_claimed_items"][0]["todo_id"] == (
        "todo_side_advancement"
    ), primary_scope

    packet = build_review_packet(payload, goal_id=GOAL_ID)
    assert packet["ok"] is True, packet
    assert packet["agent_todo_items"] == [
        "[P0] Primary agent owns earlier work. claimed_by=codex-main-control",
        "[P0] Side agent owns the implementation slice. claimed_by=codex-product-capability",
        "[P0] Side agent has due monitor context. claimed_by=codex-product-capability",
    ], packet


def assert_higher_priority_due_monitor_preempts_advancement() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_due_monitor",
                title="Poll the due monitor first.",
                priority="P0",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                index=1,
                next_due_at="2026-01-01T00:00:00+00:00",
            ),
            todo_item(
                todo_id="todo_later_advancement",
                title="Run the later implementation slice.",
                priority="P1",
                claimed_by=AGENT_ID,
                index=2,
            ),
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    contract = quota["work_lane_contract"]
    assert contract["lane"] == "continuous_monitor", contract
    assert contract["monitor_kind"] == "todo_monitor_due", contract
    assert contract["selected_todo_id"] == "todo_due_monitor", contract
    assert "due_monitor_priority_preempts_advancement" in contract["reason_codes"], contract
    assert quota.get("agent_lane_next_action") is None, quota
    assert quota["recommended_action"] == "[P0] Poll the due monitor first.", quota


def assert_future_monitor_frontier_waits_without_delta() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_monitor_wait",
                title="Watch unchanged monitor.",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                cadence="15m",
                next_due_at="2099-01-01T00:00:00+00:00",
            )
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["decision"] == "skip", quota
    assert quota["should_run"] is False, quota
    assert quota["effective_action"] == "monitor_quiet_skip", quota

    lane = quota["work_lane_contract"]
    assert lane["lane"] == "continuous_monitor", lane
    assert lane["obligation"] == "quiet_until_material_monitor_transition", lane
    assert lane["must_attempt_work"] is False, lane
    frontier = quota["goal_frontier_projection"]
    assert frontier["replan_required"] is False, quota
    assert quota.get("autonomous_replan_obligation") is None, quota

    contract = quota["interaction_contract"]
    assert contract["mode"] == "monitor_quiet_skip", contract
    assert contract["user_channel"]["action_required"] is False, contract
    assert contract["user_channel"]["notify"] == "DONT_NOTIFY", contract
    assert contract["agent_channel"]["must_attempt"] is False, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is True, contract
    assert contract["cli_channel"]["spend_allowed_now"] is False, contract
    assert contract["cli_channel"]["spend_after_validation"] is False, contract

    scheduler = quota["scheduler_hint"]
    assert scheduler["action"] == "backoff_until_material_transition", scheduler
    assert scheduler["cadence_class"] == "monitor_wait", scheduler

    packet = build_review_packet(payload, goal_id=GOAL_ID)
    assert packet["ok"] is True, packet
    assert packet["project_asset_source"] == "project_asset", packet
    assert packet["handoff_interface_budget"]["within_budget"] is True, packet
    assert packet["agent_todo_items"] == [
        "[P0] Watch unchanged monitor. claimed_by=codex-product-capability"
    ], packet


def assert_standing_monitor_gate_does_not_quiet_skip_gated_advancement() -> None:
    agent_todos = quota_todo_summary(
        [
            todo_item(
                todo_id="todo_standing_gate",
                title="Monitor the product refactor/catalog canary gate.",
                priority="P1",
                task_class="continuous_monitor",
                claimed_by=AGENT_ID,
                index=1,
            ),
            todo_item(
                todo_id="todo_gated_refactor",
                title="Triage and fix product/core smoke regressions.",
                priority="P2",
                task_class="advancement_task",
                claimed_by=AGENT_ID,
                index=2,
                resume_when="todo_done:todo_standing_gate",
            ),
        ],
        role="agent",
        item_limit=None,
    )
    assert agent_todos is not None, agent_todos
    assert agent_todos["resume_blocked_count"] == 1, agent_todos
    blocked = agent_todos["resume_blocked_items"][0]
    assert blocked["todo_id"] == "todo_gated_refactor", blocked
    assert blocked["resume_ready"] is False, blocked
    assert blocked["resume_condition"]["target_task_class"] == "continuous_monitor", blocked

    quota = build_quota_should_run(
        status_payload([], agent_todos=agent_todos),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["decision"] == "run", quota
    assert quota["should_run"] is True, quota
    assert quota["effective_action"] == "normal_run", quota

    summary = quota["agent_todo_summary"]
    assert summary["current_agent_monitor_blocked_resume_count"] == 1, summary
    assert summary["current_agent_claimed_advancement_count"] == 0, summary
    assert summary["current_agent_claimed_monitor_count"] == 1, summary

    contract = quota["work_lane_contract"]
    assert contract["lane"] == "advancement_task", contract
    assert contract["obligation"] == "repair_resume_gate_or_close_standing_monitor", contract
    assert "resume_blocked_by_open_monitor" in contract["reason_codes"], contract
    assert quota.get("agent_scope_frontier") is None, quota

    top = contract["resume_blocked_by_monitor_items"][0]
    assert top["todo_id"] == "todo_gated_refactor", top
    assert top["blocking_monitor_todo_id"] == "todo_standing_gate", top

    interaction = quota["interaction_contract"]
    assert interaction["mode"] == "bounded_delivery", interaction
    assert interaction["agent_channel"]["must_attempt"] is True, interaction
    assert interaction["agent_channel"]["quiet_noop_allowed"] is False, interaction


def assert_reassignment_scheduler_contract() -> None:
    payload = status_payload(
        [
            todo_item(
                todo_id="todo_primary_only",
                title="Primary agent owns the only runnable work.",
                claimed_by=PRIMARY_AGENT_ID,
            )
        ]
    )
    quota = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=APP_SCHEDULER_CONTEXT,
    )
    assert quota["decision"] == "reassignment_required", quota
    assert quota["should_run"] is False, quota
    assert quota["effective_action"] == "reassignment_required", quota
    assert quota["agent_lane_frontier_hint"]["reason_code"] == (
        "blocked_by_other_agent_frontier"
    ), quota
    assert quota["agent_lane_frontier_hint"]["target_todo_id"] == (
        "todo_primary_only"
    ), quota

    contract = quota["interaction_contract"]
    assert contract["mode"] == "reassignment_required", contract
    assert contract["user_channel"]["action_required"] is False, contract
    assert contract["user_channel"]["notify"] == "DONT_NOTIFY", contract
    assert contract["agent_channel"]["must_attempt"] is False, contract
    assert contract["agent_channel"]["delivery_allowed"] is False, contract
    assert contract["agent_channel"]["quiet_noop_allowed"] is True, contract
    assert contract["cli_channel"]["spend_after_validation"] is False, contract

    scheduler = quota["scheduler_hint"]
    assert scheduler["action"] == "backoff_until_reassigned", scheduler
    assert scheduler["cadence_class"] == "agent_scope_wait", scheduler
    assert scheduler["codex_app"]["recommended_rrule"] == (
        "FREQ=MINUTELY;INTERVAL=10"
    ), scheduler
    assert scheduler["codex_app"]["recommended_interval_minutes"] == 10, scheduler
    assert scheduler["codex_app"]["stateful_backoff"]["apply_needed"] is True, scheduler
    assert scheduler["codex_app"]["no_spend_for_cadence_change"] is True, scheduler

    packet = build_review_packet(payload, goal_id=GOAL_ID)
    assert packet["ok"] is True, packet
    assert packet["handoff_interface_budget"]["within_budget"] is True, packet
    assert packet["agent_todo_items"] == [
        "[P0] Primary agent owns the only runnable work. claimed_by=codex-main-control"
    ], packet


def main() -> None:
    assert_agent_lane_delivery()
    assert_foreign_operator_gate_keeps_normal_admission()
    assert_due_monitor_context_does_not_steal_advancement()
    assert_current_agent_claimed_advancement_beats_other_agent_frontier()
    assert_higher_priority_due_monitor_preempts_advancement()
    assert_future_monitor_frontier_waits_without_delta()
    assert_standing_monitor_gate_does_not_quiet_skip_gated_advancement()
    assert_reassignment_scheduler_contract()
    print("control-plane-risk-characterization-smoke ok")


if __name__ == "__main__":
    main()
