from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..coordination.coordination_state_contract_generated import (
    ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA,
    ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA,
    ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA,
    ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA,
)
from ..todos.contract import normalize_todo_id, normalize_todo_task_class
from .planning_inventory import (
    build_quota_planning_inventory_request,
    compact_planning_candidate,
)

ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION = ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA
ACTION_SELECTION_QUALIFICATION_SCHEMA_VERSION = ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA
QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION = ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA
QUOTA_PLANNING_PACKET_SCHEMA_VERSION = ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA
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
) -> dict[str, Any]:
    """Project every requested planning lens through one TypeScript request."""

    if not projection_enabled and not include_detail and replan_selection_context is None:
        return {}
    inventory_request = build_quota_planning_inventory_request(
        goal_id=goal_id,
        selected=selected,
        agent_id=agent_id,
        agent_todo_summary=agent_todo_summary,
        agent_todo_source_items=agent_todo_source_items,
        capability_gate=capability_gate,
        blocked_priority_fallback=blocked_priority_fallback,
    )
    if inventory_request is None:
        return {}
    try:
        projected = effect_runtime_result(
            "work_item.action_portfolio.project",
            {
                "schema_version": QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION,
                "planning_inventory_request": inventory_request,
                "projection_enabled": projection_enabled,
                "include_detail": include_detail,
                "acceptance_gaps": _frontier_acceptance_gaps(
                    goal_frontier_projection
                ),
                **({"replan_selection_context": dict(replan_selection_context)}
                   if replan_selection_context is not None else {}),
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
