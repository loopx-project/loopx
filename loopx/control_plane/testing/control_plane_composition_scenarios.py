from __future__ import annotations

from typing import Any

from ...quota import build_quota_should_run
from ..quota.turn_envelope import build_turn_envelope
from ..scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from .quota_fixtures import quota_status_payload, quota_todo_item, quota_todo_summary


def _scheduler_context() -> dict[str, Any]:
    return scheduler_execution_context_for_runtime_profile("codex_app_heartbeat")


def _required_vision_replan_source(
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    peer_agent_id = "codex-portfolio-reviewer"
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [agent_id, peer_agent_id],
        "agent_profiles": {
            agent_id: {
                "schema_version": "agent_profile_v1",
                "agent_id": agent_id,
                "profile_role": "quality-qualification",
                "scope_summary": "Continuous qualification and maintainability work.",
                "default_task_classes": [
                    "advancement_task",
                    "continuous_monitor",
                ],
            }
        },
    }
    status = quota_status_payload(
        goal_id=goal_id,
        status="active",
        recommended_action="Monitor the release signal for a material transition.",
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_portfolio_monitor",
                index=1,
                priority="P1",
                task_class="continuous_monitor",
                action_kind="monitor",
                claimed_by=agent_id,
                text="[P1] Monitor the release signal for a material transition.",
                target_key="portfolio-release-signal",
                cadence="15m",
                next_due_at="2999-01-01T00:00:00+00:00",
            ),
            quota_todo_item(
                todo_id="todo_portfolio_peer",
                index=2,
                priority="P0",
                task_class="advancement_task",
                claimed_by=peer_agent_id,
                text="[P0] Continue the peer-owned release implementation.",
            ),
        ],
        coordination=coordination,
        claim_scope_agent_id=agent_id,
    )
    return build_quota_should_run(
        status,
        goal_id=goal_id,
        agent_id=agent_id,
        scheduler_execution_context=_scheduler_context(),
    )


def _scoped_gate_successor_replan_source(
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    completed = quota_todo_item(
        todo_id="todo_portfolio_prerequisite",
        status="done",
        claimed_by=agent_id,
        text="[P0] Complete the quality prerequisite.",
    )
    deferred = quota_todo_item(
        todo_id="todo_portfolio_deferred",
        status="deferred",
        claimed_by=agent_id,
        action_kind="quality_hardening",
        text="[P1] Continue the ready quality hardening successor.",
        resume_when="todo_done:todo_portfolio_prerequisite",
        done=True,
    )
    gate = quota_todo_item(
        todo_id="todo_portfolio_first_screen",
        role="user",
        status="open",
        task_class="user_gate",
        action_kind="approve_product_first_screen",
        decision_scope={"kind": "write_scope", "granularity": "action", "scope_key": "first-screen"},
        blocks_agent=agent_id,
        text="[P2] Review the unrelated product first screen.",
    )
    status = quota_status_payload(
        goal_id=goal_id,
        status="active",
        recommended_action="Continue the ready quality hardening successor.",
        agent_todos=quota_todo_summary(
            [completed, deferred],
            role="agent",
            claim_scope_agent_id=agent_id,
        ),
        user_todos=quota_todo_summary([gate], role="user"),
        quota_state="operator_gate",
        safe_bypass=True,
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [agent_id],
        },
    )
    return build_quota_should_run(
        status,
        goal_id=goal_id,
        agent_id=agent_id,
        scheduler_execution_context=_scheduler_context(),
    )


def _capability_monitor_repair_source(
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any]:
    latest_runs = [
        {
            "classification": "portfolio_replan_ack",
            "agent_id": agent_id,
            "progress_scope": "agent_lane",
            "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0",
                "recorded": True,
                "source": "fixture",
                "delta_contract": {
                    "schema_version": "repair_delta_contract_v0",
                    "delta_present": True,
                    "delta_kinds": ["watch_lane_continuation"],
                },
            },
        }
    ]
    status = quota_status_payload(
        goal_id=goal_id,
        status="active",
        recommended_action="Continue the quality lane.",
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_portfolio_private_read",
                index=1,
                priority="P1",
                claimed_by=agent_id,
                text="[P1] Read a private source before implementation.",
                required_capabilities=["private_read"],
            ),
            quota_todo_item(
                todo_id="todo_portfolio_monitor_schedule",
                index=2,
                priority="P0",
                task_class="continuous_monitor",
                action_kind="monitor",
                claimed_by=agent_id,
                text="[P0] Repair the release monitor schedule.",
                target_key="portfolio-release-monitor",
            ),
        ],
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [agent_id],
        },
        latest_runs=latest_runs,
        claim_scope_agent_id=agent_id,
    )
    return build_quota_should_run(
        status,
        goal_id=goal_id,
        agent_id=agent_id,
        available_capabilities=["network"],
        scheduler_execution_context=_scheduler_context(),
    )


def build_control_plane_composition_scenario_sources(
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, dict[str, Any]]:
    return {
        "turn_required_vision_replan": _required_vision_replan_source(
            goal_id=goal_id,
            agent_id=agent_id,
        ),
        "turn_scoped_gate_successor_replan": _scoped_gate_successor_replan_source(
            goal_id=goal_id,
            agent_id=agent_id,
        ),
        "turn_capability_monitor_repair": _capability_monitor_repair_source(
            goal_id=goal_id,
            agent_id=agent_id,
        ),
    }


def build_control_plane_composition_scenario_packets(
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, dict[str, Any]]:
    sources = build_control_plane_composition_scenario_sources(
        goal_id=goal_id,
        agent_id=agent_id,
    )
    return {
        scenario_id: build_turn_envelope(source)
        for scenario_id, source in sources.items()
    }
