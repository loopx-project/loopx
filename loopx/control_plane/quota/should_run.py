from __future__ import annotations
from .effective_action import EffectiveAction

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..goals.goal_frontier.fallback_disposition import FallbackTodoSource

from ...quota import (
    _build_quota_plan_for_goal,
)
from ..agents.agent_scope import (
    _attach_agent_identity_contracts,
)
from ..agents.identity import (
    build_quota_agent_identity,
)
from ..agents.workspace_guard import (
    build_agent_workspace_guard,
    observe_goal_local_workspace,
)
from ..quota.decision_summary import (
    quota_plan_items as _quota_plan_items,
)
from ..quota.goal_boundary import (
    registry_goal_by_id as _registry_goal_by_id,
)
from ..quota.projection_repair import build_boundary_projection_repair_hint
from ..quota.selected_todo_projection import selected_todo_projection
from ..quota.states import (
    AutomaticTurnPauseCause,
    automatic_turn_pause_cause,
)
from ..quota.states import (
    quota_item_is_paused as _quota_item_is_paused,
)
from ..scheduler.automation_liveness import build_automation_liveness
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    resolve_scheduler_execution_context,
)
from ..todos.write_hint import build_todo_write_hint
from ..work_items.interaction_contract import (
    build_interaction_contract,
)
from .effect_program import (
    ReceiptBoundMonitorPhase,
    ReceiptBoundReplayPhase,
    ReceiptBoundTerminalPhase,
)
from .should_run_packet import (
    _build_quota_should_run_payload,
    _execution_obligation,
    _QuotaDecisionRoute,
    _resolve_quota_should_run_route,
    _scheduler_hint,
)
from .should_run_prepare import (
    _prepare_quota_should_run_item,
    _QuotaDecisionPreparation,
)

QUOTA_PAUSED_MODE = "quota_paused"
GOAL_STOPPED_MODE = "goal_stopped"


def _apply_selected_todo_guards(
    prepared: _QuotaDecisionPreparation,
    route: _QuotaDecisionRoute,
    *,
    workspace_path: Path | None = None,
) -> _QuotaDecisionRoute:
    """Bind workspace and boundary guards to the exact projected Todo.

    Delivery continuity and agent steering can reorder runnable candidates.  These
    guards therefore run after the delivery route has selected a Todo, then freeze
    that selection while the policy route is recomputed with any resulting repair.
    """

    selected_todo = selected_todo_projection(
        agent_lane_next_action=route.agent_lane_next_action,
        work_lane_contract=(None if route.replan_decision_allowed else route.payload_work_lane_contract),
        agent_scope_frontier=route.agent_scope_frontier,
    )
    if (
        selected_todo is None
        and prepared.requested_action_todo_id is not None
        and (prepared.action_selection_qualification or {}).get("state")
        in {"deferred", "rejected"}
    ):
        # The typed selection owner refused this choice before workspace checks.
        # A guard's legacy default fallback must not replace that refusal with
        # a repair for a different, unselected Todo.
        return route
    summary = prepared.agent_todo_summary or {}
    acceptance = summary.get("goal_acceptance_contract")
    if isinstance(acceptance, dict) and acceptance.get("enabled") is True:
        held_ids = acceptance.get("held_todo_ids") or []
        held_selection = bool(selected_todo and selected_todo.get("todo_id") in held_ids)
        # Native guards already filtered these lanes. Do not let a generic
        # Goal recommendation recreate advancement permission from held work.
        no_runnable_work = bool(held_ids) and not selected_todo and not any(
            summary.get(field) for field in (
                "first_executable_items", "executable_backlog_items", "monitor_due_items",
            )
        )
        if (
            (route.normal_delivery_allowed or route.recovery_allowed)
            and not prepared.inbox_priority_due
            and not route.replan_decision_allowed
            and (held_selection or no_runnable_work)
        ):
            prepared.normal_delivery_allowed = False
            prepared.recovery_allowed = False
            prepared.reason = (
                "Goal acceptance holds the current work; inspect its contract and "
                "ask the owner to configure or rebind the current Todo."
            )
            route = _resolve_quota_should_run_route(prepared)
    workspace_guard = None
    # A compact legacy projection may legitimately omit every boundary field.
    # Observe the selected workspace without inventing a write grant.
    goal_boundary = prepared.goal_boundary or {}
    local_workspace = observe_goal_local_workspace(
        prepared.item, selected_todo, goal_boundary.get("write_scope", [])
    )
    if not prepared.inbox_priority_due:
        workspace_guard = build_agent_workspace_guard(
            prepared.item,
            prepared.agent_identity,
            agent_todo_summary=prepared.agent_todo_summary,
            selected_todo=selected_todo,
            current_path=workspace_path,
            local_workspace=local_workspace,
        )
    boundary_projection_repair = build_boundary_projection_repair_hint(
        {**goal_boundary, "write_scope": [
            *goal_boundary.get("write_scope", []),
            *local_workspace.get("allowed_write_scopes", []),
        ]},
        prepared.agent_todo_summary,
        candidate_should_run=bool(route.should_run),
        capability_gate=prepared.capability_gate,
        selected_todo=selected_todo,
    )
    if not workspace_guard and not boundary_projection_repair:
        return route

    prepared.workspace_guard = workspace_guard
    if isinstance(route.agent_lane_next_action, dict):
        prepared.guarded_agent_lane_next_action = route.agent_lane_next_action
    if boundary_projection_repair:
        prepared.boundary_projection_repair = boundary_projection_repair
        prepared.stall_self_repair = boundary_projection_repair
        prepared.self_repair_allowed = True
        prepared.normal_delivery_allowed = False
        prepared.recovery_allowed = False
        prepared.reason = str(
            boundary_projection_repair.get("reason") or prepared.reason
        )
    return _resolve_quota_should_run_route(prepared)


def build_quota_paused_should_run_payload(
    status_payload: dict[str, Any],
    *,
    safe_goal_id: str,
    requested_agent_id: str | None,
    item: dict[str, Any],
    plan: dict[str, Any],
    goal_health_ok: bool,
    include_scheduler_detail: bool,
    codex_app_current_rrule: Any,
    codex_app_automation_id: Any = None,
    resolved_scheduler_context: SchedulerExecutionContextResolution,
    runtime_root: str | Path | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Project one canonical hard-pause contract with no lane contradiction.

    Whether the owner stopped the Goal lifecycle or set its compute quota to zero,
    every automatic authority field resolves to `should_run=false`, all
    delivery/repair permissions false, `DONT_NOTIFY`, no quota spend, and a
    scheduler cadence that is never `run_now`. The typed pause cause preserves the
    distinct resume authority for the host scheduler.
    """

    quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
    quota = {**quota, "state": "paused"}
    pause_cause = automatic_turn_pause_cause(
        {"quota": quota}
    ) or AutomaticTurnPauseCause.COMPUTE_QUOTA_ZERO
    goal_stopped = pause_cause is AutomaticTurnPauseCause.GOAL_STOPPED
    recommended_mode = GOAL_STOPPED_MODE if goal_stopped else QUOTA_PAUSED_MODE
    reason = str(
        quota.get("reason")
        or (
            "goal is stopped by owner; automatic agent turns are paused"
            if goal_stopped
            else "compute quota is 0; automatic agent turns are paused"
        )
    )
    agent_identity = build_quota_agent_identity(item, agent_id=requested_agent_id)
    heartbeat_recommendation = {
        "source": "quota.should-run",
        "recommended_mode": recommended_mode,
        "notify": "DONT_NOTIFY",
        "reason": reason,
        "spend_policy": (
            "do not append quota spend while the Goal lifecycle is stopped"
            if goal_stopped
            else "do not append quota spend while compute quota is paused"
        ),
    }
    execution_obligation = _execution_obligation(
        should_run=False,
        effective_action=EffectiveAction.QUOTA_SKIP.value,
        heartbeat_recommendation=heartbeat_recommendation,
    )
    payload: dict[str, Any] = {
        "ok": goal_health_ok,
        "status_health_ok": goal_health_ok,
        "mode": "should-run",
        "goal_id": safe_goal_id,
        "decision": "skip",
        "should_run": False,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "effective_action": EffectiveAction.QUOTA_SKIP.value,
        "actionable_by_codex": False,
        "reason": reason,
        "quota": quota,
        "pause_cause": pause_cause.value,
        "state": "paused",
        "safe_bypass_allowed": False,
        "waiting_on": item.get("waiting_on"),
        "status": item.get("status"),
        "lifecycle_phase": item.get("lifecycle_phase"),
        "lifecycle_flags": item.get("lifecycle_flags"),
        "source": item.get("source"),
        "recommended_action": reason,
        "requires_user_action": False,
        "heartbeat_recommendation": heartbeat_recommendation,
        "execution_obligation": execution_obligation,
        "plan_summary": plan.get("summary"),
        "todo_write_hint": build_todo_write_hint(safe_goal_id),
    }
    payload = _attach_agent_identity_contracts(
        payload=payload,
        agent_identity=agent_identity,
    )
    if goal_ref is not None:
        payload["goal_ref"] = dict(goal_ref)
    payload["automation_liveness"] = build_automation_liveness(payload)
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=None,
        scheduler_execution_context=resolved_scheduler_context,
        runtime_root=(
            str(runtime_root)
            if runtime_root
            else str(status_payload.get("runtime_root"))
            if status_payload.get("runtime_root")
            else None
        ),
    )
    payload["scheduler_hint"] = _scheduler_hint(
        payload,
        include_detail=include_scheduler_detail,
        available_capabilities=None,
        codex_app_current_rrule=codex_app_current_rrule,
        codex_app_automation_id=codex_app_automation_id,
        scheduler_execution_context=resolved_scheduler_context,
    )
    return payload


def build_quota_should_run(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None = None,
    available_capabilities: Any = None,
    include_scheduler_detail: bool = False,
    include_agent_todo_detail: bool = False,
    codex_app_current_rrule: Any = None,
    codex_app_automation_id: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    receipt_bound_todo_id: str | None = None,
    requested_action_todo_id: str | None = None,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None = None,
    receipt_bound_replay_phase: ReceiptBoundReplayPhase | None = None,
    receipt_bound_terminal_phase: ReceiptBoundTerminalPhase | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    receipt_bound_replan_guard_scoped: bool = False,
    turn_instance_id: str | None = None,
    runtime_root: str | Path | None = None,
    workspace_path: Path | None = None,
    goal_ref: Mapping[str, object] | None = None,
    authoritative_fallback_todo_items: FallbackTodoSource = None,
) -> dict[str, Any]:
    safe_goal_id = str(goal_id or "").strip()
    resolved_scheduler_context = resolve_scheduler_execution_context(
        scheduler_execution_context
    )
    if receipt_bound_replay_phase is None:
        receipt_bound_replay_phase = receipt_bound_terminal_phase
    registry_goal = _registry_goal_by_id(status_payload).get(safe_goal_id) or {}
    plan, goal_health_ok = _build_quota_plan_for_goal(
        status_payload,
        goal_id=safe_goal_id,
    )
    item = next(
        (
            candidate
            for candidate in _quota_plan_items(plan)
            if candidate.get("goal_id") == safe_goal_id
        ),
        None,
    )
    health_items = (
        plan.get("health_items")
        if isinstance(plan.get("health_items"), list)
        else []
    )
    health_item = next(
        (
            candidate
            for candidate in health_items
            if isinstance(candidate, dict) and candidate.get("goal_id") == safe_goal_id
        ),
        None,
    )
    if item:
        if _quota_item_is_paused(item):
            return build_quota_paused_should_run_payload(
                status_payload,
                safe_goal_id=safe_goal_id,
                requested_agent_id=agent_id,
                item=item,
                plan=plan,
                goal_health_ok=goal_health_ok,
                include_scheduler_detail=include_scheduler_detail,
                codex_app_current_rrule=codex_app_current_rrule,
                codex_app_automation_id=codex_app_automation_id,
                resolved_scheduler_context=resolved_scheduler_context,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
            )
        prepared = _prepare_quota_should_run_item(
            status_payload,
            safe_goal_id=safe_goal_id,
            requested_agent_id=agent_id,
            available_capabilities=available_capabilities,
            include_scheduler_detail=include_scheduler_detail,
            codex_app_current_rrule=codex_app_current_rrule,
            codex_app_automation_id=codex_app_automation_id,
            resolved_scheduler_context=resolved_scheduler_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            registry_goal=registry_goal,
            plan=plan,
            goal_health_ok=goal_health_ok,
            item=item,
            health_items=health_items,
            receipt_bound_todo_id=receipt_bound_todo_id,
            requested_action_todo_id=requested_action_todo_id,
            receipt_bound_monitor_phase=receipt_bound_monitor_phase,
            receipt_bound_replay_phase=receipt_bound_replay_phase,
            receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
            receipt_bound_replan_guard_scoped=receipt_bound_replan_guard_scoped,
            authoritative_fallback_todo_items=authoritative_fallback_todo_items,
        )
        route = _resolve_quota_should_run_route(prepared)
        route = _apply_selected_todo_guards(prepared, route, workspace_path=workspace_path)
        return _build_quota_should_run_payload(
            prepared,
            route,
            turn_instance_id=turn_instance_id,
            include_agent_todo_detail=include_agent_todo_detail,
            runtime_root=runtime_root,
            goal_ref=goal_ref,
        )
    if health_item:
        return {
            "ok": False,
            "mode": "should-run",
            "goal_id": safe_goal_id,
            "decision": "skip",
            "should_run": False,
            "reason": str(
                health_item.get("recommended_action")
                or "health item blocks automatic compute"
            ),
            "state": "blocked_health",
            "waiting_on": health_item.get("waiting_on"),
            "status": health_item.get("status"),
            "source": health_item.get("source"),
            "recommended_action": health_item.get("recommended_action"),
            "plan_summary": plan.get("summary"),
        }
    return {
        "ok": False,
        "mode": "should-run",
        "goal_id": safe_goal_id,
        "decision": "skip",
        "should_run": False,
        "reason": "goal is not present in the registered quota plan",
        "state": "unknown",
        "waiting_on": None,
        "status": "goal_not_found",
        "source": "quota",
        "recommended_action": (
            "run `loopx registry` and connect or sync the goal before spending compute"
        ),
        "plan_summary": plan.get("summary"),
    }
