from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ...state_projection import actions_are_projection_aligned
from ..runtime.public_safety import compact_text
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..goals.goal_vision_policy import DEFAULT_PLANNING_HORIZON_GOAL
from ..coordination.coordination_state_contract_generated import (
    ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_V1_SCHEMA,
    ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_V1_SCHEMA,
    ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA,
    ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA,
)
from ..todos.contract import (
    normalize_todo_id, normalize_todo_task_class, normalize_todo_status,
    normalize_todo_claimed_by, normalize_todo_blocks_agent, normalize_todo_excluded_agents,
)
from .planning_inventory import (
    build_quota_planning_inventory_request,
    compact_planning_candidate,
)

ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION = ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA
ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION = ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA
QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION = ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_V1_SCHEMA
QUOTA_PLANNING_PACKET_SCHEMA_VERSION = ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_V1_SCHEMA
RETAINED_ACTION_SELECTION_REENTRY_REQUEST_SCHEMA_VERSION = (
    "retained_action_selection_reentry_request_v0"
)
RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION = (
    "retained_action_selection_reentry_v0"
)


def _compact_candidate(value: Mapping[str, Any]) -> dict[str, Any] | None:
    return compact_planning_candidate(value)


def _frontier_acceptance_gaps(
    projection: Mapping[str, Any] | None,
) -> list[Any]:
    if not isinstance(projection, Mapping):
        return []
    acceptance_gaps = projection.get("acceptance_gaps")
    return acceptance_gaps if isinstance(acceptance_gaps, list) else []


def capture_goal_route_context(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    agent_lane_next_action: dict[str, Any] | None,
    agent_scope_frontier: dict[str, Any] | None,
    agent_lane_frontier_hint: dict[str, Any] | None,
    active_state_next_action: Any,
    latest_run_recommended_action: Any,
    selected_recommended_action: Any,
    goal_route_hint_override: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Snapshot compatibility facts, without selecting, counting or routing work.

    Python normalization and text alignment remain shared owners. Every source
    retains its order and list-presence semantics; TypeScript owns all choices.
    """
    agent_id = normalize_todo_claimed_by(
        agent_identity.get("agent_id") if isinstance(agent_identity, dict) else None
    )
    if not agent_id:
        return None

    def row(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        text = compact_text(str(value.get("text") or ""), limit=220)
        return {
            "done": value.get("done") is True,
            "status": normalize_todo_status(value.get("status")),
            "task_class": normalize_todo_task_class(
                value.get("task_class"), text=str(value.get("text") or ""),
                action_kind=value.get("action_kind"),
            ),
            "todo_id": normalize_todo_id(value.get("todo_id")),
            "claimed_by": normalize_todo_claimed_by(value.get("claimed_by")),
            "blocks_agent": normalize_todo_blocks_agent(value.get("blocks_agent")),
            "unblocks_todo_id": normalize_todo_id(value.get("unblocks_todo_id")),
            "excluded_agents": normalize_todo_excluded_agents(value.get("excluded_agents")),
            "text": text,
            "display_text": compact_text(text, limit=120),
            "gate_identity": str(value.get("todo_id") or value.get("index") or value.get("text") or ""),
            "gate_state": value.get("gate_state"),
            **{key: value[key] for key in ("priority", "action_kind") if value.get(key) is not None},
        }

    def integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    summary = agent_todo_summary if isinstance(agent_todo_summary, dict) else {}
    summary_facts: dict[str, Any] = {}
    for field in (
        "claimed_advancement_open_items", "claimed_by_others_items",
        "executable_backlog_items", "unclaimed_priority_open_items",
        "current_agent_handoff_gates", "handoff_gates",
    ):
        values = summary.get(field)
        summary_facts[field] = [row(value) for value in values] if isinstance(values, list) else None
    for field in ("current_agent_claimed_advancement_count", "claimed_advancement_open_count"):
        summary_facts[field] = integer(summary.get(field))
    current = None
    if isinstance(agent_lane_next_action, dict):
        current = {
            "todo_id": normalize_todo_id(agent_lane_next_action.get("todo_id")),
            "text": compact_text(str(agent_lane_next_action.get("text") or ""), limit=120),
            "claim_required_before_work": agent_lane_next_action.get("claim_required_before_work") is True,
            **{key: agent_lane_next_action[key] for key in ("selected_by", "confidence", "source")
               if agent_lane_next_action.get(key) is not None},
        }
    active = compact_text(str(active_state_next_action or ""), limit=260)
    selected = compact_text(str(selected_recommended_action or ""), limit=260)
    return deepcopy({
        "agent_id": agent_id,
        "agent_todo_summary": summary_facts,
        "agent_lane_next_action": current,
        "agent_scope_frontier": (
            {key: str(agent_scope_frontier.get(key) or "") for key in ("action", "effective_action", "reason")}
            if isinstance(agent_scope_frontier, dict) else None
        ),
        "agent_lane_frontier_hint": (
            {key: agent_lane_frontier_hint[key] for key in
             ("decision", "source", "reason_code", "target_todo_id", "quiet_noop_allowed")
             if agent_lane_frontier_hint.get(key) is not None}
            if isinstance(agent_lane_frontier_hint, dict) else None
        ),
        "active_state_next_action": active,
        "selected_recommended_action": selected,
        "latest_run_recommended_action": compact_text(str(latest_run_recommended_action or ""), limit=260),
        "actions_projection_aligned": actions_are_projection_aligned(active, selected),
        "goal_route_hint_override": goal_route_hint_override,
    })


def build_quota_planning_packet(
    *,
    projection_enabled: bool,
    include_detail: bool,
    goal_id: str,
    selected: Mapping[str, Any] | None,
    agent_id: str | None,
    agent_todo_summary: Mapping[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]],
    capability_gate: Mapping[str, Any] | None,
    blocked_priority_fallback: Mapping[str, Any] | None,
    goal_frontier_projection: Mapping[str, Any] | None,
    replan_selection_context: Mapping[str, Any] | None = None,
    source_status: Mapping[str, Any] | None = None,
    planning_horizon_goal: bool = DEFAULT_PLANNING_HORIZON_GOAL,
    goal_route_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project every requested planning lens through one TypeScript request."""

    if not isinstance(planning_horizon_goal, bool):
        raise ValueError("planning_horizon_goal must be a boolean")
    planning_requested = projection_enabled or include_detail or replan_selection_context is not None
    inventory_request = (
        build_quota_planning_inventory_request(
            goal_id=goal_id, selected=selected, agent_id=agent_id,
            agent_todo_summary=agent_todo_summary,
            agent_todo_source_items=agent_todo_source_items,
            capability_gate=capability_gate,
            blocked_priority_fallback=blocked_priority_fallback,
        ) if planning_requested else None
    )
    planning = None
    if inventory_request is not None:
        from .replan_context_codec import goal_context_facts

        goal_facts = (
            goal_context_facts(source_status, goal_id, (agent_todo_summary or {}).get("goal_acceptance_contract"))
            if planning_horizon_goal and source_status and source_status.get("registry") else None
        )
        planning = {
            "planning_inventory_request": inventory_request,
            "projection_enabled": projection_enabled,
            "include_detail": include_detail,
            "acceptance_gaps": _frontier_acceptance_gaps(goal_frontier_projection),
            **({"goal_facts": goal_facts} if goal_facts is not None else {}),
            **({"replan_selection_context": dict(replan_selection_context)}
               if replan_selection_context is not None else {}),
        }
    if planning is None and goal_route_context is None:
        return {}
    try:
        projected = effect_runtime_result(
            "work_item.action_portfolio.project",
            {
                "schema_version": QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION,
                "planning": planning,
                "goal_route_context": dict(goal_route_context) if goal_route_context is not None else None,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(projected, Mapping) or (
        projected.get("schema_version") != QUOTA_PLANNING_PACKET_SCHEMA_VERSION
    ):
        raise RuntimeError("TypeScript quota planning packet shape mismatch")
    result: dict[str, Any] = {}
    for field in (
        "goal_route_hint",
        "action_portfolio",
        "planning_horizon",
        "agent_todo_planning_inventory",
    ):
        value = projected.get(field)
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise TypeError(f"TypeScript quota planning packet {field} mismatch")
        result[field] = dict(value)
    return result


def qualify_action_selection(
    *,
    requested_todo_id: str,
    candidate: Mapping[str, Any] | None,
    requested_task_class: str | None,
    should_run: bool,
    normal_delivery_allowed: bool,
    delivery_preemptions: list[str],
    scoped_fallback_todo_id: str | None = None,
    quota_state: str | None = None,
    goal_health_ok: bool = False,
) -> dict[str, Any]:
    """Adapt current Python projections into the TS-owned selection reducer."""

    compact_candidate = _compact_candidate(candidate) if candidate is not None else None
    try:
        result = effect_runtime_result(
            "work_item.action_selection.qualify",
            {
                "schema_version": ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
                "requested_todo_id": requested_todo_id,
                "candidate": compact_candidate,
                "requested_task_class": requested_task_class,
                "should_run": should_run,
                "normal_delivery_allowed": normal_delivery_allowed,
                "delivery_preemptions": delivery_preemptions,
                "scoped_fallback_todo_id": scoped_fallback_todo_id,
                "quota_state": quota_state,
                "goal_health_ok": goal_health_ok,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or (
        result.get("schema_version") != ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION
    ):
        raise RuntimeError("TypeScript action-selection qualification shape mismatch")
    return dict(result)


def qualify_action_selection_from_inventory(
    *,
    requested_todo_id: str,
    candidate: Mapping[str, Any] | None,
    source_items: list[dict[str, Any]],
    should_run: bool,
    normal_delivery_allowed: bool,
    delivery_preemptions: list[str],
    scoped_fallback_todo_id: str | None = None,
    quota_state: str | None = None,
    goal_health_ok: bool = False,
) -> dict[str, Any]:
    """Resolve the requested task class before invoking the typed reducer."""

    requested_item = next(
        (
            item
            for item in source_items
            if normalize_todo_id(item.get("todo_id")) == requested_todo_id
        ),
        None,
    )
    requested_task_class = (
        normalize_todo_task_class(
            requested_item.get("task_class"),
            text=str(requested_item.get("text") or ""),
            action_kind=requested_item.get("action_kind"),
        )
        if requested_item is not None
        else None
    )
    return qualify_action_selection(
        requested_todo_id=requested_todo_id,
        candidate=candidate,
        requested_task_class=requested_task_class,
        should_run=should_run,
        normal_delivery_allowed=normal_delivery_allowed,
        delivery_preemptions=delivery_preemptions,
        scoped_fallback_todo_id=scoped_fallback_todo_id,
        quota_state=quota_state,
        goal_health_ok=goal_health_ok,
    )


def reconcile_retained_action_selection(
    *,
    retained_todo_id: str,
    projected_todo_id: str | None,
    effective_action: str,
    replan_obligation_id: str | None,
) -> dict[str, Any]:
    """Ask the typed owner whether a reentry may bind its new projection."""

    try:
        result = effect_runtime_result(
            "work_item.action_selection.reconcile_retained",
            {
                "schema_version": (
                    RETAINED_ACTION_SELECTION_REENTRY_REQUEST_SCHEMA_VERSION
                ),
                "retained_todo_id": retained_todo_id,
                "projected_todo_id": projected_todo_id,
                "effective_action": effective_action,
                "replan_obligation_id": replan_obligation_id,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or (
        result.get("schema_version")
        != RETAINED_ACTION_SELECTION_REENTRY_SCHEMA_VERSION
    ):
        raise RuntimeError(
            "TypeScript retained action-selection reentry shape mismatch"
        )
    return dict(result)
