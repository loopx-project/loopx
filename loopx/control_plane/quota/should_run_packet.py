from __future__ import annotations
from .effective_action import EffectiveAction
from ..effect_runtime import effect_runtime_result
from .effect_program import ReceiptBoundReplayPhase
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...long_task_cadence import reconcile_long_task_cadence_hint
from ...state_projection import (
    next_action_projection_warning,
    state_action_projection_warning as build_state_action_projection_warning,
)
from .. import compact_control_plane_policy
from ..agents.agent_lane_recommendation import (
    build_agent_lane_next_action,
    selected_action_with_agent_lane,
    selected_recommended_action_from_work_lane,
)
from ..agents.agent_scope import (
    AgentScopeFrontierAction,
    _agent_lane_frontier_hint,
    _agent_scope_deferred_resume_candidates,
    _agent_scope_frontier_action,
    _agent_scope_no_candidate_frontier,
    _selected_candidate_priority_frontier,
    _attach_agent_identity_contracts,
)
from ..agents.capability_gate import missing_required_capabilities
from ..agents.agent_scope_frontier import read_frontier_action
from ..goals.goal_frontier import (
    AUTONOMOUS_REPLAN_REQUIRED_MODE,
)
from ..quota.decision_summary import (
    quota_decision_agent_id,
    refine_quota_recommended_action,
    resolve_quota_run_decision,
)
from ..quota.goal_boundary import (
    quota_execution_profile_summary as _quota_execution_profile_summary,
)
from ..quota.heartbeat_recommendation import (
    build_action_selection_recovery_recommendation,
    build_heartbeat_recommendation,
    refine_heartbeat_recommendation,
)
from ..quota.policy_constants import (
    AUTONOMOUS_CANDIDATE_CONTEXT_FIELDS,
    MONITOR_DUE_ITEM_LIMIT,
)
from ..quota.recent_runs import (
    recent_external_monitor_observation_unchanged as _recent_external_monitor_observation_unchanged,
)
from ..quota.selected_todo_projection import (
    selected_todo_projection as _selected_todo_projection,
)
from ..quota.stall_repair import (
    stall_repair_blocked_action_scope,
    stall_repair_payload,
)
from ..quota.stall_repair import (
    standing_decision_authority_payload_from_status_item as _standing_decision_authority_payload_from_status_item,
)
from ..quota.task_orchestration import (
    PEER_COORDINATION_BLOCKED_ACTION,
    attach_task_orchestration_payload,
    task_goal_route_hint,
    task_orchestration_contract_is_actionable,
    task_orchestration_requires_material_change_stop,
)
from ..quota.task_orchestration import (
    payload_work_lane_contract as _payload_work_lane_contract,
)
from ..scheduler.automation_liveness import build_automation_liveness
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
)
from ..scheduler.external_evidence_observation import (
    build_external_evidence_observation_obligation,
)
from ..scheduler.scheduler_hint import build_scheduler_hint
from ..scheduler.state import (
    load_app_automation_scheduler_state,
)
from ..todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from ..todos.todo_semantics import (
    todo_item_is_actionable_open as projection_todo_item_is_actionable_open,
)
from ..todos.quota_summary import (
    compact_quota_todo_summary_for_payload,
)
from ..todos.user_gate import (
    apply_scoped_user_gate_fallback_projection as _apply_scoped_user_gate_fallback_projection,
)
from ..todos.user_gate import (
    build_user_todo_notification as _build_user_todo_notification,
)
from ..todos.write_hint import build_todo_write_hint
from ..turn_driver.delivery_continuity import evaluate_delivery_route
from ..work_items.action_portfolio import (
    build_quota_planning_packet,
    qualify_action_selection_from_inventory,
)
from ..work_items.execution_obligation import build_execution_obligation
from ..work_items.action_selection_contract import (
    apply_action_selection_recovery_projection,
)
from ..work_items.goal_route_hint import build_goal_route_hint
from ..work_items.interaction_contract import (
    build_interaction_contract,
    unadmitted_action_selection,
    finalize_user_gate_notification_cooldown,
)
from ..work_items.interaction_contract import (
    user_channel_action_required as _user_channel_action_required,
)
from ..work_items.primary_action import protocol_action_text as _protocol_action_text
from ..work_items.progress_observation import build_replan_action_packet
from ..work_items.user_action_frontier import (
    user_action_owns_empty_agent_lane_from_summaries as _user_action_owns_empty_agent_lane,
)
from ..work_items.work_lane import (
    WORK_LANE_RECEIPT_BOUND_DEFERRED_OBLIGATION,
    work_lane_contract_is_due_monitor_attempt,
    work_lane_contract_is_receipt_bound_monitor_settled,
)
from .settlement_precedence import (
    settled_replay_fields,
    HEARTBEAT_SETTLED_REPLAY_REASON,
    clear_quota_action_projections,
)
from .should_run_prepare import _QuotaDecisionPreparation
from ._supporting_projections import (
    _attach_quota_supporting_projections,
    _attach_truthy_fields,
    _dict_field,
)


def _interaction_runtime_root(
    runtime_root: str | Path | None,
    status_payload: Mapping[str, Any],
) -> str | None:
    if runtime_root:
        return str(runtime_root)
    raw_runtime_root = status_payload.get("runtime_root")
    return str(raw_runtime_root) if raw_runtime_root else None


def _compact_autonomous_candidate_context(
    value: Any,
    *,
    goal_id: str | None = None,
    limit: int = 3,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = {field: value[field] for field in AUTONOMOUS_CANDIDATE_CONTEXT_FIELDS if field in value}
    items = compact.get("items")
    if isinstance(items, list):
        compact_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if goal_id and str(item.get("goal_id") or "") != goal_id:
                continue
            compact_item = {
                key: item[key]
                for key in ("goal_id", "task_class", "text")
                if item.get(key) is not None
            }
            if compact_item:
                compact_items.append(compact_item)
            if len(compact_items) >= limit:
                break
        if not compact_items:
            return None
        compact["items"] = compact_items
        compact["open_count"] = len(compact_items)
    return compact or None


def _scheduler_hint(
    payload: dict[str, Any], *, include_detail: bool = False,
    codex_app_scheduler_state: dict[str, Any] | None = None, available_capabilities: Any = None, codex_app_current_rrule: Any = None,
    codex_app_automation_id: Any = None,
    scheduler_execution_context: Mapping[str, Any] | SchedulerExecutionContextResolution | None = None,
) -> dict[str, Any]:
    return build_scheduler_hint(
        payload,
        user_action_required=_user_channel_action_required(payload),
        agent_scope_frontier_actions=[action.value for action in AgentScopeFrontierAction],
        include_detail=include_detail,
        codex_app_scheduler_state=codex_app_scheduler_state,
        available_capabilities=available_capabilities, codex_app_current_rrule=codex_app_current_rrule,
        codex_app_automation_id=codex_app_automation_id,
        scheduler_execution_context=scheduler_execution_context,
    )


def _load_app_automation_scheduler_state(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    surface: str,
) -> dict[str, Any] | None:
    raw_runtime_root = status_payload.get("runtime_root")
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not raw_runtime_root or not safe_agent_id:
        return None
    return load_app_automation_scheduler_state(
        Path(str(raw_runtime_root)).expanduser(),
        goal_id=goal_id,
        agent_id=safe_agent_id,
        surface=surface,
    )


def _execution_obligation(
    *,
    should_run: bool,
    effective_action: str,
    heartbeat_recommendation: dict[str, Any],
    work_lane_contract: dict[str, Any] | None = None,
    external_evidence_observation: dict[str, Any] | None = None,
    user_gate_owns_frontier: bool = False,
) -> dict[str, Any]:
    return build_execution_obligation(
        should_run=should_run,
        effective_action=effective_action,
        heartbeat_recommendation=heartbeat_recommendation,
        work_lane_contract=work_lane_contract,
        external_evidence_observation=external_evidence_observation,
        user_gate_owns_frontier=user_gate_owns_frontier,
        successor_replan_mode=AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value,
    )


def _apply_agent_monitor_only_precedence(
    payload: dict[str, Any],
    *,
    monitor_only: bool,
    inbox_priority_due: bool,
) -> None:
    """Keep monitor/reply lanes alive while suppressing advancement work.

    This final precedence pass prevents autonomous replan, repair, or fallback
    from turning a configured monitor-only peer back into an advancement lane.
    """

    if not monitor_only or inbox_priority_due:
        return
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), dict)
        else {}
    )
    monitor_lane = work_lane.get("lane") == "continuous_monitor"
    monitor_due = monitor_lane and work_lane.get("must_attempt_work") is True
    if monitor_lane:
        reason = (
            "agent work mode is monitor_only; run the due monitor without "
            "opening an advancement or autonomous replan lane"
            if monitor_due
            else "agent work mode is monitor_only; wait quietly for the next "
            "material monitor transition or direct operator reply"
        )
        payload.update(
            {
                "decision": "run" if monitor_due else "skip",
                "should_run": monitor_due,
                "normal_delivery_allowed": monitor_due,
                "recovery_delivery_allowed": False,
                "self_repair_allowed": False,
                "capability_repair_allowed": False,
                "workspace_repair_allowed": False,
                "effective_action": EffectiveAction.MONITOR_DUE.value if monitor_due else EffectiveAction.MONITOR_QUIET_SKIP.value,
                "actionable_by_codex": monitor_due,
                "reason": reason,
                "blocked_action_scope": "advancement_work",
                "safe_bypass_allowed": False,
                "safe_bypass_kind": None,
                "safe_bypass_policy": None,
                "requires_user_action": False,
                "agent_work_mode": "monitor_only",
                "recommended_action": work_lane.get("action"),
                "heartbeat_recommendation": {
                    "recommended_mode": (
                        "monitor_due"
                        if monitor_due
                        else "monitor_quiet_until_material_transition"
                    ),
                    "notify": "DONT_NOTIFY",
                    "reason": reason,
                    "spend_policy": (
                        "spend only after a validated material monitor transition; "
                        "unchanged monitor polls are no-spend"
                    ),
                },
                "execution_obligation": {
                    "must_attempt_work": monitor_due,
                    "kind": "work_lane_contract" if monitor_due else "monitor_quiet_skip",
                    "contract": "work_lane_contract",
                    "contract_obligation": work_lane.get("obligation"),
                    "delivery_allowed": monitor_due,
                    "notify_is_execution_gate": False,
                    "reason": reason,
                },
            }
        )
    else:
        reason = (
            "agent work mode is monitor_only; advancement, autonomous replan, "
            "repair, and fallback remain paused until an explicit mode change"
        )
        payload.update(
            {
                "decision": "skip",
                "should_run": False,
                "normal_delivery_allowed": False,
                "recovery_delivery_allowed": False,
                "self_repair_allowed": False,
                "capability_repair_allowed": False,
                "workspace_repair_allowed": False,
                "effective_action": EffectiveAction.AGENT_MONITOR_ONLY.value,
                "actionable_by_codex": False,
                "reason": reason,
                "blocked_action_scope": "advancement_work",
                "safe_bypass_allowed": False,
                "safe_bypass_kind": None,
                "safe_bypass_policy": None,
                "requires_user_action": False,
                "agent_work_mode": "monitor_only",
                "recommended_action": (
                    "Wait for a due monitor, verified direct operator reply, or "
                    "explicit work-mode change."
                ),
                "heartbeat_recommendation": {
                    "recommended_mode": "agent_monitor_only",
                    "notify": "DONT_NOTIFY",
                    "reason": reason,
                    "spend_policy": "do not append quota spend while advancement is paused",
                },
                "execution_obligation": {
                    "must_attempt_work": False,
                    "kind": "agent_monitor_only",
                    "delivery_allowed": False,
                    "notify_is_execution_gate": False,
                    "reason": reason,
                    "spend_policy": "do not append quota spend while advancement is paused",
                },
            }
        )
    monitor_selected_todo = (
        _selected_todo_projection(
            agent_lane_next_action=None,
            work_lane_contract=work_lane,
        )
        if monitor_due
        else None
    )
    if monitor_selected_todo:
        payload["selected_todo"] = monitor_selected_todo
    else:
        payload.pop("selected_todo", None)
    frontier = payload.get("goal_frontier_projection")
    if isinstance(frontier, dict):
        frontier.pop("vision_continuation_audit", None)
    clear_quota_action_projections(payload)


def _apply_unadmitted_action_selection_precedence(
    payload: dict[str, Any],
    *,
    replay_phase: ReceiptBoundReplayPhase | None,
) -> None:
    """Finalize one closed selection recovery before shared projections."""

    if replay_phase is ReceiptBoundReplayPhase.SETTLED or not (
        unadmitted_action_selection(payload)
    ):
        return
    for field in (
        "agent_lane_next_action",
        "agent_scope_frontier",
        "autonomous_replan_obligation",
        "execution_profile",
        "goal_route_hint",
        "handoff_readiness",
        "replan_action_packet",
        "scoped_user_gate_fallback",
        "selected_todo",
        "task_orchestration_contract",
        "todo_id",
        "todo_write_hint",
        "work_lane_contract",
        "workspace_guard",
    ):
        payload.pop(field, None)
    apply_action_selection_recovery_projection(payload)
    payload["heartbeat_recommendation"] = (
        build_action_selection_recovery_recommendation(
            reason=str(payload.get("reason") or "")
        )
    )
def _delivery_preemptions_for_route(
    prepared: _QuotaDecisionPreparation,
    *,
    due_monitor_attempt: bool,
    receipt_bound_replan_decision: bool,
    external_evidence_observation: dict[str, Any] | None,
    normal_delivery_allowed: bool,
    replan_decision_allowed: bool,
    self_repair_allowed: bool,
    capability_repair_allowed: bool,
    workspace_repair_allowed: bool,
) -> list[str]:
    preemptions: list[str] = []
    if prepared.receipt_bound_todo_id or receipt_bound_replan_decision:
        preemptions.append("heartbeat_receipt")
    if (
        due_monitor_attempt
        or prepared.inbox_priority_due
        or task_orchestration_contract_is_actionable(
            prepared.task_orchestration_contract
        )
        or prepared.capability_monitor_fallback
        or external_evidence_observation
    ):
        preemptions.append("blocking_work_lane")
    if replan_decision_allowed:
        preemptions.append("autonomous_replan")
    if (
        self_repair_allowed
        or capability_repair_allowed
        or workspace_repair_allowed
        or prepared.automation_prompt_upgrade_required
    ):
        preemptions.append("control_repair")
    if not normal_delivery_allowed:
        preemptions.append("delivery_not_allowed")
    requested_candidate = prepared.requested_action_candidate
    agent_id = normalize_todo_claimed_by(
        (prepared.agent_identity or {}).get("agent_id")
    )
    if (
        not preemptions
        and agent_id
        and isinstance(prepared.agent_todo_summary, dict)
        and isinstance(requested_candidate, dict)
        and _selected_candidate_priority_frontier(
            agent_id=agent_id,
            summary=prepared.agent_todo_summary,
            selected=requested_candidate,
        )
    ):
        preemptions.append("ready_deferred_successor_priority_preemption")
    return preemptions


def _resolve_agent_lane_delivery_route(
    prepared: _QuotaDecisionPreparation,
    *,
    due_monitor_attempt: bool,
    receipt_bound_replan_decision: bool,
    delivery_preemptions: list[str],
    should_run: bool,
    normal_delivery_allowed: bool,
) -> dict[str, Any] | None:
    """Project candidates once, then let TypeScript own their delivery route."""

    if prepared.requested_action_todo_id is not None:
        qualification = qualify_action_selection_from_inventory(
            requested_todo_id=prepared.requested_action_todo_id,
            candidate=prepared.requested_action_candidate,
            source_items=prepared.agent_todo_planning_source_items,
            should_run=should_run,
            normal_delivery_allowed=normal_delivery_allowed,
            delivery_preemptions=delivery_preemptions,
        )
        prepared.action_selection_qualification = qualification

    if isinstance(prepared.guarded_agent_lane_next_action, dict):
        # Workspace/scope guards can change admission after initial selection.
        # Keep the selected identity frozen, but report the final qualification.
        return prepared.guarded_agent_lane_next_action

    if prepared.requested_action_todo_id is not None:
        qualification = prepared.action_selection_qualification or {}
        if qualification.get("state") != "qualified":
            return None
        if not isinstance(prepared.requested_action_candidate, dict):
            raise RuntimeError(
                "qualified action selection is missing its projected candidate"
            )
        return {
            **prepared.requested_action_candidate,
            "selected_by": "agent_action_selection",
            "selection_binding": "pending_action_selection",
        }

    item = prepared.item
    fallback = prepared.receipt_bound_agent_next_action
    if (
        fallback is None
        and not due_monitor_attempt
        and not prepared.inbox_priority_due
        and not task_orchestration_contract_is_actionable(
            prepared.task_orchestration_contract
        )
        and not prepared.capability_monitor_fallback
    ):
        fallback = build_agent_lane_next_action(
            agent_identity=prepared.agent_identity,
            agent_todo_summary=prepared.agent_todo_summary,
            capability_gate=prepared.capability_gate,
            scoped_user_gate_fallback=prepared.scoped_user_gate_fallback,
            receipt_bound_todo_id=prepared.receipt_bound_todo_id,
            active_next_action=(
                item.get("active_state_next_action")
                or (
                    item.get("project_asset", {}).get("next_action")
                    if isinstance(item.get("project_asset"), dict)
                    else None
                )
            ),
        )
    if receipt_bound_replan_decision:
        # The replan obligation is the immutable settlement authority for this
        # decision. A newly runnable Todo remains visible in summaries, but it
        # cannot become the selected settlement target in the same packet.
        fallback = None
    if (
        prepared.receipt_bound_todo_id
        and isinstance(fallback, dict)
        and normalize_todo_id(fallback.get("todo_id"))
        != prepared.receipt_bound_todo_id
    ):
        # Feed only the committed identity into the TS delivery router.  The
        # independent successor remains discoverable on a fresh Turn.
        fallback = None

    delivery_agent_id = normalize_todo_claimed_by(
        (prepared.agent_identity or {}).get("agent_id")
    )
    if not delivery_agent_id:
        return fallback

    continuity_todo = prepared.delivery_continuity_todo
    delivery_anchor = prepared.delivery_continuity_anchor
    if not isinstance(delivery_anchor, dict) and fallback is None:
        return None
    delivery_route = evaluate_delivery_route(
        agent_id=delivery_agent_id,
        previous_todo_id=(
            delivery_anchor.get("todo_id")
            if isinstance(delivery_anchor, dict)
            else None
        ),
        previous_delivery_outcome=(
            delivery_anchor.get("delivery_outcome")
            if isinstance(delivery_anchor, dict)
            else None
        ),
        continuity_todo=continuity_todo,
        continuity_actionable=bool(
            isinstance(continuity_todo, dict)
            and projection_todo_item_is_actionable_open(continuity_todo)
        ),
        continuity_capability_ready=bool(
            isinstance(continuity_todo, dict)
            and not missing_required_capabilities(
                continuity_todo,
                available_capabilities=prepared.effective_available_capabilities,
            )
        ),
        fallback_todo=fallback,
        fallback_actionable=bool(
            isinstance(fallback, dict)
            and projection_todo_item_is_actionable_open(fallback)
        ),
        fallback_capability_ready=bool(
            isinstance(fallback, dict)
            and not missing_required_capabilities(
                fallback,
                available_capabilities=prepared.effective_available_capabilities,
            )
        ),
        preemptions=delivery_preemptions,
    )

    selection = delivery_route["selection"]
    if selection == "continuity":
        delivery_continuity = delivery_route.get("continuity") or {}
        selected_action = build_agent_lane_next_action(
            agent_identity=prepared.agent_identity,
            agent_todo_summary=prepared.agent_todo_summary,
            capability_gate=prepared.capability_gate,
            scoped_user_gate_fallback=prepared.scoped_user_gate_fallback,
            receipt_bound_todo_id=prepared.receipt_bound_todo_id,
            selected_todo_override={
                "todo_id": delivery_continuity.get("todo_id"),
                "selected_by": "in_flight_todo",
                "source": "delivery_continuity.latest_accountable_delivery",
                "selection_reason": delivery_continuity.get("reason"),
            },
            active_next_action=(
                item.get("active_state_next_action")
                or (
                    item.get("project_asset", {}).get("next_action")
                    if isinstance(item.get("project_asset"), dict)
                    else None
                )
            ),
        )
        if not isinstance(selected_action, dict):
            raise RuntimeError(
                "TypeScript selected delivery continuity without a "
                "projectable Todo candidate"
            )
    elif selection == "fallback":
        selected_action = fallback
    else:
        selected_action = None

    if (
        prepared.receipt_bound_todo_id
        and isinstance(selected_action, dict)
        and normalize_todo_id(selected_action.get("todo_id"))
        != prepared.receipt_bound_todo_id
    ):
        # The router may find an independent successor after the bound Todo
        # becomes unavailable.  That successor cannot replace an already
        # committed settlement identity inside the same heartbeat Turn.
        return None

    boundary = delivery_route.get("boundary")
    if (
        isinstance(selected_action, dict)
        and isinstance(boundary, dict)
        and boundary["delivery_boundary"] == "in_flight_continuation"
    ):
        return {
            **selected_action,
            "delivery_boundary": boundary["delivery_boundary"],
        }
    return selected_action


@dataclass(slots=True)
class _QuotaDecisionRoute:
    normal_delivery_allowed: bool
    recovery_allowed: bool
    self_repair_allowed: bool
    capability_repair_allowed: bool
    workspace_repair_allowed: bool
    should_run: bool
    effective_action: str
    reason: str
    state: str
    quota: dict[str, Any]
    replan_decision_allowed: bool
    receipt_bound_replan_decision: bool
    heartbeat_recommendation: dict[str, Any]
    external_evidence_observation: dict[str, Any] | None
    external_evidence_observation_recent: dict[str, Any] | None
    selected_recommended_action: Any
    agent_lane_next_action: dict[str, Any] | None
    agent_scope_frontier: dict[str, Any] | None
    agent_lane_frontier_hint: dict[str, Any] | None
    state_action_projection_warning: dict[str, Any] | None
    active_state_next_action_text: str
    latest_run_recommended_action_text: str
    next_action_warning: dict[str, Any] | None
    goal_route_hint: dict[str, Any] | None
    payload_work_lane_contract: dict[str, Any] | None


def _planning_projections(
    prepared: _QuotaDecisionPreparation,
    route: _QuotaDecisionRoute,
    *,
    include_detail: bool,
) -> dict[str, Any]:
    selection_available = route.normal_delivery_allowed or bool(
        route.workspace_repair_allowed
        and prepared.workspace_guard
        and prepared.normal_delivery_allowed
    ) or bool(
        route.effective_action == EffectiveAction.BOUNDARY_PROJECTION_REPAIR.value
        and prepared.boundary_projection_repair
    )
    projection_enabled = bool(
        route.should_run
        and selection_available
        and prepared.receipt_bound_todo_id is None
        and prepared.requested_action_todo_id is None
        and not route.receipt_bound_replan_decision
        and not prepared.agent_monitor_only
    )
    return build_quota_planning_packet(
        projection_enabled=projection_enabled,
        include_detail=include_detail,
        goal_id=prepared.safe_goal_id,
        selected=route.agent_lane_next_action,
        agent_id=normalize_todo_claimed_by(
            (prepared.agent_identity or {}).get("agent_id")
        ),
        agent_todo_summary=prepared.agent_todo_summary,
        agent_todo_source_items=prepared.agent_todo_planning_source_items,
        capability_gate=prepared.capability_gate,
        blocked_priority_fallback=prepared.blocked_priority_fallback,
        goal_frontier_projection=prepared.goal_frontier_projection,
    )


def _resolve_quota_should_run_route(
    prepared: _QuotaDecisionPreparation,
) -> _QuotaDecisionRoute:
    item = prepared.item
    run_decision = resolve_quota_run_decision(
        normal_delivery_allowed=prepared.normal_delivery_allowed,
        recovery_delivery_allowed=prepared.recovery_allowed,
        self_repair_allowed=prepared.self_repair_allowed,
        stall_self_repair=prepared.stall_self_repair,
        state=prepared.state,
        quota=prepared.quota,
        reason=prepared.reason,
        capability_gate=prepared.capability_gate,
        capability_monitor_fallback=prepared.capability_monitor_fallback,
        workspace_guard=prepared.workspace_guard,
        automation_prompt_upgrade=prepared.automation_prompt_upgrade,
        automation_prompt_upgrade_required=prepared.automation_prompt_upgrade_required,
        replan_obligation=prepared.replan_obligation,
        goal_health_ok=prepared.goal_health_ok,
        inbox_reply_due=prepared.inbox_reply_due,
        inbox_material_review_due=prepared.inbox_material_review_due,
        agent_frontier_id=prepared.agent_frontier_id,
        registered_agent_ids=prepared.registered_agent_ids,
        goal_frontier_projection=prepared.goal_frontier_projection,
        task_orchestration_contract=prepared.task_orchestration_contract,
    )
    if prepared.receipt_bound_replay_phase is ReceiptBoundReplayPhase.SETTLED:
        fields = settled_replay_fields()
        return _QuotaDecisionRoute(
            normal_delivery_allowed=fields["normal_delivery_allowed"],
            recovery_allowed=fields["recovery_delivery_allowed"],
            self_repair_allowed=fields["self_repair_allowed"],
            capability_repair_allowed=fields["capability_repair_allowed"],
            workspace_repair_allowed=fields["workspace_repair_allowed"],
            should_run=fields["should_run"], effective_action=fields["effective_action"],
            reason=fields["reason"], state=run_decision.state, quota=run_decision.quota,
            replan_decision_allowed=False, receipt_bound_replan_decision=False,
            heartbeat_recommendation=fields["heartbeat_recommendation"],
            external_evidence_observation=None, external_evidence_observation_recent=None,
            selected_recommended_action=HEARTBEAT_SETTLED_REPLAY_REASON,
            agent_lane_next_action=None, agent_scope_frontier=None,
            agent_lane_frontier_hint=None, state_action_projection_warning=None,
            active_state_next_action_text=_protocol_action_text(
                item.get("active_state_next_action")
                or prepared.project_asset.get("active_state_next_action")
                or prepared.project_asset.get("next_action"), limit=320,
            ),
            latest_run_recommended_action_text=_protocol_action_text(
                item.get("latest_run_recommended_action")
                or prepared.project_asset.get("latest_run_recommended_action"), limit=320,
            ),
            next_action_warning=None, goal_route_hint=None,
            payload_work_lane_contract=_payload_work_lane_contract(
                prepared.work_lane_contract, effective_action=fields["effective_action"],
                recovery_allowed=False, agent_scope_frontier=None,
            ),
        )
    normal_delivery_allowed = run_decision.normal_delivery_allowed
    recovery_allowed = run_decision.recovery_delivery_allowed
    self_repair_allowed = run_decision.self_repair_allowed
    capability_repair_allowed = run_decision.capability_repair_allowed
    workspace_repair_allowed = run_decision.workspace_repair_allowed
    should_run = run_decision.should_run
    effective_action = run_decision.effective_action
    reason = run_decision.reason
    state = run_decision.state
    quota = run_decision.quota
    replan_decision_allowed = run_decision.replan_decision_allowed
    receipt_bound_replan_decision = bool(
        replan_decision_allowed
        and isinstance(prepared.replan_obligation, Mapping)
        and prepared.replan_obligation.get("selection_binding")
        == "heartbeat_receipt"
    )
    heartbeat_recommendation = build_heartbeat_recommendation(
        {**item, "quota": quota},
        goal_id=prepared.safe_goal_id,
        state=state,
        should_run=should_run,
        user_todo_summary=prepared.user_todo_summary,
        agent_todo_summary=prepared.agent_todo_summary,
        work_lane_contract=prepared.work_lane_contract,
        stall_self_repair=prepared.stall_self_repair,
        replan_obligation=prepared.replan_obligation,
        select_replan_obligation=False,
        monitor_due_item_limit=MONITOR_DUE_ITEM_LIMIT,
    )
    heartbeat_recommendation = refine_heartbeat_recommendation(
        heartbeat_recommendation,
        should_run=should_run,
        capability_gate=prepared.capability_gate,
        capability_monitor_fallback=prepared.capability_monitor_fallback,
        workspace_guard=prepared.workspace_guard,
        automation_prompt_upgrade=prepared.automation_prompt_upgrade,
        automation_prompt_upgrade_required=prepared.automation_prompt_upgrade_required,
        blocked_priority_fallback=prepared.blocked_priority_fallback,
    )
    external_evidence_observation = build_external_evidence_observation_obligation(
        item,
        state=state,
        agent_todo_summary=prepared.agent_todo_summary,
        work_lane_contract=prepared.work_lane_contract,
    )
    external_evidence_observation_recent = None
    if external_evidence_observation:
        external_evidence_observation_recent = _recent_external_monitor_observation_unchanged(
            prepared.status_payload,
            goal_id=prepared.safe_goal_id,
            agent_id=(
                normalize_todo_claimed_by(prepared.agent_identity.get("agent_id"))
                if isinstance(prepared.agent_identity, dict)
                else None
            ),
        )
        if external_evidence_observation_recent or (
            external_evidence_observation.get("poll_window_status") == "before_next_due"
        ):
            external_evidence_observation = None
    ready_deferred_resume_candidates: list[dict[str, Any]] = []
    if isinstance(prepared.agent_identity, dict) and isinstance(
        prepared.agent_todo_summary, dict
    ):
        ready_deferred_resume_candidates = _agent_scope_deferred_resume_candidates(
            prepared.agent_todo_summary,
            agent_id=normalize_todo_claimed_by(
                prepared.agent_identity.get("agent_id")
            ),
        )
    if (
        external_evidence_observation
        and not prepared.workspace_guard
        and not prepared.inbox_priority_due
    ):
        normal_delivery_allowed = False
        should_run = True
        heartbeat_recommendation = {
            **heartbeat_recommendation,
            "recommended_mode": "external_evidence_observe_or_blocker",
            "notify": "DONT_NOTIFY",
            "reason": (
                "waiting external evidence requires a read-only observation "
                "or compact blocker before quiet no-op"
            ),
            "spend_policy": external_evidence_observation.get("spend_policy")
            or heartbeat_recommendation.get("spend_policy"),
        }
        effective_action = EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE.value
        reason = "external evidence monitor requires read-only observation before quiet no-op"
    receipt_bound_monitor_settled = (
        work_lane_contract_is_receipt_bound_monitor_settled(
            prepared.work_lane_contract
        )
    )
    if receipt_bound_monitor_settled:
        normal_delivery_allowed = False
        recovery_allowed = False
        self_repair_allowed = False
        should_run = False
        effective_action = EffectiveAction.HEARTBEAT_SETTLED_SKIP.value
        reason = (
            "the receipt-bound monitor poll and required settlement receipts are "
            "complete for this heartbeat turn; defer successor selection to a new turn"
        )
        heartbeat_recommendation = {
            **heartbeat_recommendation,
            "recommended_mode": effective_action,
            "notify": "DONT_NOTIFY",
            "reason": reason,
            "spend_policy": "no quota spend for an already-settled heartbeat turn",
        }
    receipt_bound_deferred_wait = bool(
        prepared.receipt_bound_todo_id
        and isinstance(prepared.work_lane_contract, dict)
        and prepared.work_lane_contract.get("obligation")
        == WORK_LANE_RECEIPT_BOUND_DEFERRED_OBLIGATION
    )
    if receipt_bound_deferred_wait:
        normal_delivery_allowed = recovery_allowed = self_repair_allowed = False
        capability_repair_allowed = workspace_repair_allowed = False
        replan_decision_allowed = receipt_bound_replan_decision = False
        should_run = False
        effective_action = EffectiveAction.QUOTA_SKIP.value
        reason = (
            "the Todo bound to this heartbeat receipt is deferred; do not "
            "select or spend an independent successor in the same Turn"
        )
        quota = {**quota, "safe_bypass_allowed": False}
        heartbeat_recommendation = {
            **heartbeat_recommendation,
            "recommended_mode": effective_action,
            "notify": "DONT_NOTIFY",
            "reason": reason,
            "spend_policy": "no quota spend for a deferred receipt-bound Todo",
            "stop_if_unchanged": True,
        }
    monitor_quiet_skip = (
        not replan_decision_allowed
        and normal_delivery_allowed
        and not recovery_allowed
        and not self_repair_allowed
        and isinstance(prepared.work_lane_contract, dict)
        and prepared.work_lane_contract.get("obligation")
        == "quiet_until_material_monitor_transition"
        and prepared.work_lane_contract.get("must_attempt_work") is False
        and heartbeat_recommendation.get("recommended_mode")
        == "monitor_quiet_until_material_transition"
        and heartbeat_recommendation.get("notify") == "DONT_NOTIFY"
        and not ready_deferred_resume_candidates
    )
    if monitor_quiet_skip:
        normal_delivery_allowed = False
        should_run = False
        effective_action = EffectiveAction.MONITOR_QUIET_SKIP.value
        reason = str(
            heartbeat_recommendation.get("reason")
            or "monitor-only polling has no material transition; skip delivery compute"
        )
    selected_recommended_action = selected_recommended_action_from_work_lane(
        item,
        agent_todo_summary=prepared.agent_todo_summary,
        work_lane_contract=prepared.work_lane_contract,
        agent_lane_recommendation=prepared.agent_lane_recommendation,
        prefer_agent_lane_recommendation=(
            monitor_quiet_skip or receipt_bound_monitor_settled
        ),
    )
    selected_recommended_action = refine_quota_recommended_action(
        selected_recommended_action,
        task_orchestration_contract=prepared.task_orchestration_contract,
        capability_gate=prepared.capability_gate,
        capability_monitor_fallback=prepared.capability_monitor_fallback,
        work_lane_contract=prepared.work_lane_contract,
        workspace_guard=prepared.workspace_guard,
        automation_prompt_upgrade=prepared.automation_prompt_upgrade,
        automation_prompt_upgrade_required=prepared.automation_prompt_upgrade_required,
        replan_obligation=prepared.replan_obligation,
        replan_decision_allowed=replan_decision_allowed,
    )
    due_monitor_attempt = work_lane_contract_is_due_monitor_attempt(
        prepared.work_lane_contract
    )
    delivery_preemptions = _delivery_preemptions_for_route(
        prepared,
        due_monitor_attempt=due_monitor_attempt,
        receipt_bound_replan_decision=receipt_bound_replan_decision,
        external_evidence_observation=external_evidence_observation,
        normal_delivery_allowed=normal_delivery_allowed,
        replan_decision_allowed=replan_decision_allowed,
        self_repair_allowed=self_repair_allowed,
        capability_repair_allowed=capability_repair_allowed,
        workspace_repair_allowed=workspace_repair_allowed,
    )
    agent_lane_next_action = _resolve_agent_lane_delivery_route(
        prepared,
        due_monitor_attempt=due_monitor_attempt,
        receipt_bound_replan_decision=receipt_bound_replan_decision,
        delivery_preemptions=delivery_preemptions,
        should_run=should_run,
        normal_delivery_allowed=normal_delivery_allowed,
    )
    agent_scope_frontier = None
    agent_lane_frontier_hint = None
    if not replan_decision_allowed and not receipt_bound_monitor_settled:
        selected_recommended_action = selected_action_with_agent_lane(
            selected_recommended_action,
            agent_lane_next_action=agent_lane_next_action,
        )
        agent_scope_frontier = _agent_scope_no_candidate_frontier(
            agent_identity=prepared.agent_identity,
            agent_todo_summary=prepared.agent_todo_summary,
            agent_lane_next_action=agent_lane_next_action,
            work_lane_contract=prepared.work_lane_contract,
            candidate_should_run=bool(
                (should_run and normal_delivery_allowed)
                or ready_deferred_resume_candidates
            ),
        )
        if (
            isinstance(agent_scope_frontier, dict)
            and agent_scope_frontier.get("priority_preemption") is True
        ):
            agent_lane_next_action = None
        agent_lane_frontier_hint = _agent_lane_frontier_hint(
            goal_id=prepared.safe_goal_id,
            agent_identity=prepared.agent_identity,
            agent_todo_summary=prepared.agent_todo_summary,
            agent_lane_next_action=agent_lane_next_action,
            agent_scope_frontier=agent_scope_frontier,
            work_lane_contract=prepared.work_lane_contract,
        )
        if agent_scope_frontier and agent_lane_frontier_hint:
            agent_scope_frontier["frontier_hint"] = agent_lane_frontier_hint
        if agent_scope_frontier:
            frontier_action = read_frontier_action(agent_scope_frontier)
            successor_replan_required = (
                frontier_action
                == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value
            )
            normal_delivery_allowed = False
            should_run = bool(successor_replan_required)
            effective_action = frontier_action
            reason = str(agent_scope_frontier.get("reason") or reason)
            selected_recommended_action = (
                agent_scope_frontier.get("recommended_action")
                or selected_recommended_action
            )
            heartbeat_recommendation = {
                **heartbeat_recommendation,
                "recommended_mode": effective_action,
                "notify": "DONT_NOTIFY",
                "reason": reason,
                "spend_policy": agent_scope_frontier.get("spend_policy")
                or "do not append quota spend while the current agent has no in-scope runnable candidate",
            }
            if task_orchestration_requires_material_change_stop(
                prepared.task_orchestration_contract,
                effective_action=effective_action,
            ):
                effective_action = EffectiveAction.PEER_COORDINATION_BLOCKED.value
                reason = (
                    "the explicitly selected peer task bundle is blocked and the "
                    "coordinator has no in-scope runnable fallback; return control "
                    "until peer capability, peer readiness, coordinator config, or "
                    "the coordinator's own frontier changes"
                )
                selected_recommended_action = reason
                heartbeat_recommendation = {
                    **heartbeat_recommendation,
                    "recommended_mode": PEER_COORDINATION_BLOCKED_ACTION,
                    "notify": "DONT_NOTIFY",
                    "reason": reason,
                    "spend_policy": (
                        "no quota spend while explicit peer coordination is blocked"
                    ),
                }
    state_action_projection_warning = build_state_action_projection_warning(
        item,
        agent_todo_summary=prepared.agent_todo_summary,
        selected_action=selected_recommended_action,
        work_lane_contract=prepared.work_lane_contract,
    )
    active_state_next_action_text = _protocol_action_text(
        item.get("active_state_next_action")
        or prepared.project_asset.get("active_state_next_action")
        or prepared.project_asset.get("next_action"),
        limit=320,
    )
    latest_run_recommended_action_text = _protocol_action_text(
        item.get("latest_run_recommended_action")
        or prepared.project_asset.get("latest_run_recommended_action"),
        limit=320,
    )
    next_action_warning = next_action_projection_warning(
        active_state_next_action=active_state_next_action_text,
        latest_run_recommended_action=latest_run_recommended_action_text,
        agent_lane_next_action=agent_lane_next_action,
    )
    goal_route_hint = build_goal_route_hint(
        agent_identity=prepared.agent_identity,
        agent_todo_summary=prepared.agent_todo_summary,
        agent_lane_next_action=agent_lane_next_action,
        agent_scope_frontier=agent_scope_frontier,
        agent_lane_frontier_hint=agent_lane_frontier_hint,
        active_state_next_action=active_state_next_action_text,
        latest_run_recommended_action=latest_run_recommended_action_text,
        selected_recommended_action=selected_recommended_action,
    )
    goal_route_hint = task_goal_route_hint(
        goal_route_hint,
        prepared.task_orchestration_contract,
    )
    payload_work_lane_contract = _payload_work_lane_contract(
        prepared.work_lane_contract,
        effective_action=effective_action,
        recovery_allowed=recovery_allowed,
        agent_scope_frontier=agent_scope_frontier,
    )
    return _QuotaDecisionRoute(
        normal_delivery_allowed=normal_delivery_allowed,
        recovery_allowed=recovery_allowed,
        self_repair_allowed=self_repair_allowed,
        capability_repair_allowed=capability_repair_allowed,
        workspace_repair_allowed=workspace_repair_allowed,
        should_run=should_run,
        effective_action=effective_action,
        reason=reason,
        state=state,
        quota=quota,
        replan_decision_allowed=replan_decision_allowed,
        receipt_bound_replan_decision=receipt_bound_replan_decision,
        heartbeat_recommendation=heartbeat_recommendation,
        external_evidence_observation=external_evidence_observation,
        external_evidence_observation_recent=external_evidence_observation_recent,
        selected_recommended_action=selected_recommended_action,
        agent_lane_next_action=agent_lane_next_action,
        agent_scope_frontier=agent_scope_frontier,
        agent_lane_frontier_hint=agent_lane_frontier_hint,
        state_action_projection_warning=state_action_projection_warning,
        active_state_next_action_text=active_state_next_action_text,
        latest_run_recommended_action_text=latest_run_recommended_action_text,
        next_action_warning=next_action_warning,
        goal_route_hint=goal_route_hint,
        payload_work_lane_contract=payload_work_lane_contract,
    )


def _quota_payload_context(prepared: _QuotaDecisionPreparation, route: _QuotaDecisionRoute) -> dict[str, Any]:
    return {
        **_standing_decision_authority_payload_from_status_item(
            prepared.item,
            project_asset=prepared.project_asset,
            agent_id=normalize_todo_claimed_by(
                (prepared.agent_identity or {}).get("agent_id")
            ),
        ),
        "ok": prepared.goal_health_ok
            or route.self_repair_allowed
            or route.capability_repair_allowed
            or route.workspace_repair_allowed,
        "status_health_ok": prepared.goal_health_ok,
        "mode": "should-run",
        "goal_id": prepared.safe_goal_id,
        "quota": route.quota,
        "state": route.state,
        "blocked_action_scope": prepared.boundary_projection_repair.get("blocked_action_scope")
            if prepared.boundary_projection_repair
            else stall_repair_blocked_action_scope(prepared.stall_self_repair)
            or route.quota.get("blocked_action_scope"),
        "safe_bypass_allowed": bool(route.quota.get("safe_bypass_allowed")),
        "safe_bypass_kind": route.quota.get("safe_bypass_kind"),
        "safe_bypass_policy": route.quota.get("safe_bypass_policy"),
        "waiting_on": prepared.item.get("waiting_on"),
        "status": prepared.item.get("status"),
        "lifecycle_phase": prepared.item.get("lifecycle_phase"),
        "lifecycle_flags": prepared.item.get("lifecycle_flags"),
        "source": prepared.item.get("source"),
        "project_asset_source": prepared.item.get("project_asset_source"),
        "active_state_next_action": route.active_state_next_action_text or None,
        "latest_run_recommended_action": route.latest_run_recommended_action_text or None,
        "execution_profile": _quota_execution_profile_summary(
                prepared.project_asset.get("execution_profile")
            )
            if prepared.project_asset
            else None,
        "long_task_cadence_hint": prepared.item.get("long_task_cadence_hint")
            if isinstance(prepared.item.get("long_task_cadence_hint"), dict)
            else None,
        "handoff_readiness": prepared.item.get("handoff_readiness"),
        "goal_boundary": prepared.goal_boundary,
        "plan_summary": prepared.plan.get("summary")
    }


def _build_active_quota_payload(
    prepared: _QuotaDecisionPreparation,
    route: _QuotaDecisionRoute,
    *,
    include_agent_todo_detail: bool = False,
) -> dict[str, Any]:
    agent_scope_action = _agent_scope_frontier_action(route.effective_action)
    execution_obligation = _execution_obligation(
        should_run=route.should_run,
        effective_action=route.effective_action,
        heartbeat_recommendation=route.heartbeat_recommendation,
        work_lane_contract=route.payload_work_lane_contract,
        external_evidence_observation=route.external_evidence_observation,
        user_gate_owns_frontier=_user_action_owns_empty_agent_lane(
            prepared.user_todo_summary,
            prepared.agent_todo_summary,
        ),
    )
    # Single-field mirror so heartbeat prompts can stay thin: agents key work
    # obligation off this boolean instead of parsing notify semantics.
    route.heartbeat_recommendation["agent_must_attempt"] = bool(
        execution_obligation.get("must_attempt_work")
    )
    payload = {
        **_quota_payload_context(prepared, route),
        "decision": AUTONOMOUS_REPLAN_REQUIRED_MODE
            if route.replan_decision_allowed
            else "run"
            if route.normal_delivery_allowed
            else "observe"
            if route.external_evidence_observation
            else "safe_bypass_recovery"
            if route.recovery_allowed
            else "self_repair"
            if route.self_repair_allowed
            else "repair_bridge"
            if route.capability_repair_allowed
            else "workspace_guard"
            if route.workspace_repair_allowed
            else "automation_prompt_upgrade"
            if prepared.automation_prompt_upgrade_required
            else agent_scope_action.value
            if agent_scope_action is not None
            else PEER_COORDINATION_BLOCKED_ACTION
            if route.effective_action == PEER_COORDINATION_BLOCKED_ACTION
            else "skip",
        "should_run": route.should_run,
        "normal_delivery_allowed": route.normal_delivery_allowed,
        "recovery_delivery_allowed": route.recovery_allowed,
        "self_repair_allowed": route.self_repair_allowed,
        "capability_repair_allowed": route.capability_repair_allowed,
        "workspace_repair_allowed": route.workspace_repair_allowed,
        "effective_action": route.effective_action,
        "actionable_by_codex": bool(
            route.should_run
            or route.recovery_allowed
            or route.external_evidence_observation
            or route.capability_repair_allowed
            or route.workspace_repair_allowed
        ),
        "reason": str(prepared.stall_self_repair.get("reason"))
            if route.self_repair_allowed
            and isinstance(prepared.stall_self_repair, dict)
            else route.reason,
        "recommended_action": route.selected_recommended_action,
        "heartbeat_recommendation": route.heartbeat_recommendation,
        "execution_obligation": execution_obligation,
        "goal_frontier_projection": prepared.goal_frontier_projection,
        "todo_write_hint": build_todo_write_hint(prepared.safe_goal_id)
    }
    if payload["safe_bypass_policy"] is None:
        payload.pop("safe_bypass_policy")
    payload = attach_task_orchestration_payload(
        payload,
        prepared.task_orchestration_contract,
    )
    for key in (
        "autonomous_replan_decision",
        "vision_continuation_audit",
        "vision_wait_state",
        "replan_ack_feedback",
    ):
        if isinstance(value := prepared.goal_frontier_projection.get(key), dict):
            payload[key] = value
    if prepared.replan_scope.get("required"):
        payload["autonomous_replan_scope"] = prepared.replan_scope
    payload = _attach_agent_identity_contracts(
        payload=payload,
        agent_identity=prepared.agent_identity,
    )
    public_agent_lane_next_action = route.agent_lane_next_action
    if (
        isinstance(public_agent_lane_next_action, dict)
        and "delivery_boundary" in public_agent_lane_next_action
    ):
        public_agent_lane_next_action = {
            key: value
            for key, value in public_agent_lane_next_action.items()
            if key != "delivery_boundary"
        }
    _attach_truthy_fields(
        payload,
        agent_lane_next_action=public_agent_lane_next_action,
        action_selection_qualification=prepared.action_selection_qualification,
    )
    selected_todo_projection = (
        None
        if route.receipt_bound_replan_decision
        else _selected_todo_projection(
            agent_lane_next_action=route.agent_lane_next_action,
            work_lane_contract=(None if route.replan_decision_allowed else route.payload_work_lane_contract),
            agent_scope_frontier=route.agent_scope_frontier,
        )
    )
    if selected_todo_projection:
        payload["selected_todo"] = selected_todo_projection
    elif route.replan_decision_allowed:
        payload["selected_todo"] = None
    payload.update(
        _planning_projections(
            prepared,
            route,
            include_detail=include_agent_todo_detail,
        )
    )
    _attach_truthy_fields(
        payload,
        agent_lane_frontier_hint=route.agent_lane_frontier_hint,
        goal_route_hint=route.goal_route_hint,
        agent_scope_frontier=route.agent_scope_frontier,
        workspace_guard=prepared.workspace_guard,
        automation_prompt_upgrade=prepared.automation_prompt_upgrade,
    )
    if prepared.agent_scoped_user_todo_override:
        payload[str(prepared.agent_scoped_user_todo_override["kind"])] = (
            prepared.agent_scoped_user_todo_override
        )
    _attach_truthy_fields(
        payload,
        work_lane_contract=route.payload_work_lane_contract,
        monitor_debt_arbitration=(
            prepared.monitor_debt_arbitration
            if prepared.monitor_debt_arbitration.get("active")
            else None
        ),
    )
    if prepared.capability_gate:
        payload["capability_gate"] = prepared.capability_gate
        if prepared.capability_gate.get("owner_missing"):
            payload["notify_user_on_capability_gate"] = True
    _attach_truthy_fields(
        payload,
        capability_monitor_fallback=prepared.capability_monitor_fallback,
        external_evidence_observation=route.external_evidence_observation,
        external_evidence_observation_recent=(
            route.external_evidence_observation_recent
        ),
    )
    control_plane = compact_control_plane_policy(prepared.item.get("control_plane"))
    if control_plane:
        payload["control_plane"] = control_plane
    if prepared.stall_self_repair:
        payload["stall_self_repair"] = prepared.stall_self_repair
        payload.update(stall_repair_payload(prepared.stall_self_repair))
    _attach_truthy_fields(
        payload,
        state_projection_gap=prepared.projection_gap,
        boundary_projection_gap=prepared.boundary_projection_repair,
        operator_question=prepared.item.get("operator_question"),
        missing_gates=prepared.item.get("missing_gates"),
    )
    if prepared.user_todo_summary:
        payload["user_todo_summary"] = compact_quota_todo_summary_for_payload(
            prepared.user_todo_summary
        )
        payload.update(
            _build_user_todo_notification(
                prepared.user_todo_summary,
                state=route.state,
                waiting_on=str(prepared.item.get("waiting_on") or ""),
                repeat_notification_required=(
                    route.heartbeat_recommendation.get(
                        "repeat_notification_required"
                    )
                    is True
                ),
                repeat_notification_reason=route.heartbeat_recommendation.get(
                    "reason"
                ),
            )
        )
    payload = _apply_scoped_user_gate_fallback_projection(
        payload,
        fallback=prepared.scoped_user_gate_fallback,
        replan_decision_allowed=route.replan_decision_allowed,
    )
    payload["requires_user_action"] = bool(
        route.state == "operator_gate"
        or payload.get("notify_user_on_gate") is True
        or payload.get("notify_user_on_open_todo") is True
        or payload.get("notify_user_on_capability_gate") is True
    )
    _attach_truthy_fields(
        payload,
        agent_todo_summary=(
            compact_quota_todo_summary_for_payload(prepared.agent_todo_summary)
            if prepared.agent_todo_summary
            else None
        ),
        blocked_priority_fallback=prepared.blocked_priority_fallback,
    )
    attention_queue = (
        prepared.status_payload.get("attention_queue")
        if isinstance(prepared.status_payload.get("attention_queue"), dict)
        else {}
    )
    _attach_truthy_fields(
        payload,
        autonomous_backlog_candidates=_compact_autonomous_candidate_context(
            attention_queue.get("autonomous_backlog_candidates"),
            goal_id=prepared.safe_goal_id,
        ),
        autonomous_monitor_candidates=_compact_autonomous_candidate_context(
            attention_queue.get("autonomous_monitor_candidates"),
            goal_id=prepared.safe_goal_id,
        ),
    )
    _attach_quota_supporting_projections(
        payload,
        status_payload=prepared.status_payload,
        item=prepared.item,
        project_asset=prepared.project_asset,
        goal_id=prepared.safe_goal_id,
        selected_recommended_action=route.selected_recommended_action,
        state=route.state,
        user_todo_summary=prepared.user_todo_summary,
        should_run=route.should_run,
        state_action_projection_warning=route.state_action_projection_warning,
        next_action_warning=route.next_action_warning,
        replan_obligation=prepared.replan_obligation,
    )
    bounded_research_frontier = _dict_field(
        prepared.status_payload, "bounded_research_frontier"
    )
    _attach_truthy_fields(
        payload,
        bounded_research_frontier=bounded_research_frontier,
    )
    _apply_agent_monitor_only_precedence(
        payload,
        monitor_only=prepared.agent_monitor_only,
        inbox_priority_due=prepared.inbox_priority_due,
    )
    _apply_unadmitted_action_selection_precedence(
        payload,
        replay_phase=prepared.receipt_bound_replay_phase,
    )
    if isinstance(
        payload.get("autonomous_replan_obligation"), dict
    ) and not unadmitted_action_selection(payload):
        payload["replan_action_packet"] = build_replan_action_packet(
            payload["autonomous_replan_obligation"],
            goal_id=prepared.safe_goal_id,
            agent_id=(
                quota_decision_agent_id(payload) or prepared.requested_agent_id
            ),
            bounded_research_frontier=bounded_research_frontier,
        )
    return payload


def _build_quota_should_run_payload(
    prepared: _QuotaDecisionPreparation,
    route: _QuotaDecisionRoute,
    *,
    turn_instance_id: str | None = None,
    include_agent_todo_detail: bool = False,
    runtime_root: str | Path | None = None,
) -> dict[str, Any]:
    if prepared.receipt_bound_replay_phase is ReceiptBoundReplayPhase.SETTLED:
        payload = _build_settled_quota_payload(prepared, route)
    else:
        payload = _build_active_quota_payload(
            prepared, route, include_agent_todo_detail=include_agent_todo_detail,
        )
    cadence_root = _interaction_runtime_root(runtime_root, prepared.status_payload)
    if cadence_root:
        cadence = effect_runtime_result("quota.automation_cadence.manage", {
            "runtime_root": str(cadence_root), "goal_id": prepared.safe_goal_id,
            "agent_id": quota_decision_agent_id(payload) or prepared.requested_agent_id,
            "automation_id": prepared.codex_app_automation_id, "operation": "read",
        })
        if cadence["enabled"]:
            payload["automation_cadence"] = cadence
    payload["automation_liveness"] = build_automation_liveness(payload)
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=prepared.runtime_available_capabilities,
        scheduler_execution_context=prepared.resolved_scheduler_context,
        turn_instance_id=turn_instance_id,
        runtime_root=_interaction_runtime_root(runtime_root, prepared.status_payload),
    )
    payload["scheduler_hint"] = _scheduler_hint(
        payload,
        include_detail=prepared.include_scheduler_detail,
        available_capabilities=prepared.runtime_available_capabilities,
        codex_app_scheduler_state=(
            _load_app_automation_scheduler_state(
                prepared.status_payload,
                goal_id=prepared.safe_goal_id,
                agent_id=quota_decision_agent_id(payload)
                or prepared.requested_agent_id,
                surface=prepared.resolved_scheduler_context.context.host_surface.value,
            )
            if prepared.resolved_scheduler_context.ok
            and prepared.resolved_scheduler_context.context is not None
            and prepared.resolved_scheduler_context.context.app_automation_applicable
            else None
        ),
        codex_app_current_rrule=prepared.codex_app_current_rrule,
        codex_app_automation_id=prepared.codex_app_automation_id,
        scheduler_execution_context=prepared.resolved_scheduler_context,
    )
    finalize_user_gate_notification_cooldown(
        payload,
        available_capabilities=prepared.runtime_available_capabilities,
        scheduler_execution_context=prepared.resolved_scheduler_context,
        turn_instance_id=turn_instance_id,
        runtime_root=_interaction_runtime_root(runtime_root, prepared.status_payload),
    )
    payload["long_task_cadence_hint"] = reconcile_long_task_cadence_hint(
        payload.get("long_task_cadence_hint"),
        interaction_contract=payload.get("interaction_contract"),
        scheduler_hint=payload.get("scheduler_hint"),
    )
    return payload


def _build_settled_quota_payload(
    prepared: _QuotaDecisionPreparation, route: _QuotaDecisionRoute,
) -> dict[str, Any]:
    payload = {
        **_quota_payload_context(prepared, route),
        **settled_replay_fields(),
        "goal_frontier_projection": {
            key: value for key, value in prepared.goal_frontier_projection.items()
            if key not in {"autonomous_replan_decision", "replan_ack_feedback",
                           "replan_obligation", "vision_continuation_audit", "vision_wait_state"}
        },
    }
    if payload["safe_bypass_policy"] is None:
        payload.pop("safe_bypass_policy")
    _attach_agent_identity_contracts(payload=payload, agent_identity=prepared.agent_identity)
    _attach_truthy_fields(
        payload,
        action_selection_qualification=prepared.action_selection_qualification,
        automation_prompt_upgrade=prepared.automation_prompt_upgrade,
        work_lane_contract=route.payload_work_lane_contract,
        monitor_debt_arbitration=prepared.monitor_debt_arbitration if prepared.monitor_debt_arbitration.get("active") else None,
        external_evidence_observation_recent=route.external_evidence_observation_recent,
        control_plane=compact_control_plane_policy(prepared.item.get("control_plane")),
        missing_gates=prepared.item.get("missing_gates"),
        agent_todo_summary=compact_quota_todo_summary_for_payload(prepared.agent_todo_summary) if prepared.agent_todo_summary else None,
        user_todo_summary=compact_quota_todo_summary_for_payload(prepared.user_todo_summary) if prepared.user_todo_summary else None,
        bounded_research_frontier=_dict_field(prepared.status_payload, "bounded_research_frontier"),
    )
    if prepared.agent_scoped_user_todo_override:
        payload[str(prepared.agent_scoped_user_todo_override["kind"])] = prepared.agent_scoped_user_todo_override
    payload.update(stall_repair_payload(prepared.stall_self_repair))
    attention_queue = _dict_field(prepared.status_payload, "attention_queue") or {}
    for field in ("autonomous_backlog_candidates", "autonomous_monitor_candidates"):
        value = _compact_autonomous_candidate_context(attention_queue.get(field), goal_id=prepared.safe_goal_id)
        if value:
            payload[field] = value
    _attach_quota_supporting_projections(
        payload, status_payload=prepared.status_payload, item=prepared.item,
        project_asset=prepared.project_asset, goal_id=prepared.safe_goal_id,
        selected_recommended_action=route.selected_recommended_action, state=route.state,
        user_todo_summary=prepared.user_todo_summary, should_run=False,
        state_action_projection_warning=None, next_action_warning=None,
        replan_obligation=None, notify_gate=False,
    )
    # Work-mode diagnostics survive settlement, while execution authority comes
    # only from the settled result, never a fallback readback or the monitor
    # precedence mutator.
    if prepared.agent_monitor_only and not prepared.inbox_priority_due:
        payload.update(agent_work_mode="monitor_only", blocked_action_scope="advancement_work")
    return payload
