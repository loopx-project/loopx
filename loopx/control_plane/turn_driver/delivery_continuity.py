from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..coordination.coordination_state_contract_generated import (
    DELIVERY_BOUNDARY_RESULT_SCHEMA,
    DELIVERY_CONTINUITY_RESULT_SCHEMA,
    DELIVERY_ROUTING_REQUEST_SCHEMA,
    DELIVERY_ROUTING_RESULT_SCHEMA,
)
from ..todos.contract import (
    TODO_STATUS_OPEN,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_status,
)
from ..todos.todo_semantics import todo_item_task_class

DELIVERY_BOUNDARY_IN_FLIGHT = "in_flight_continuation"
DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT = "semantic_closeout"
DELIVERY_BOUNDARY_VALUES = (
    DELIVERY_BOUNDARY_IN_FLIGHT,
    DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT,
)
DELIVERY_CONTINUITY_DECISIONS = {
    "resume_in_flight",
    "release_for_reselection",
    "preempt",
}
DELIVERY_CONTINUITY_PREEMPTIONS = {
    "heartbeat_receipt",
    "blocking_work_lane",
    "autonomous_replan",
    "control_repair",
    "delivery_not_allowed",
}
DELIVERY_CONTINUITY_REASONS = {
    *DELIVERY_CONTINUITY_PREEMPTIONS,
    "no_previous_todo",
    "previous_delivery_not_progress",
    "current_todo_missing",
    "todo_identity_changed",
    "todo_not_open",
    "todo_not_advancement",
    "todo_not_actionable",
    "todo_capability_blocked",
    "todo_claimed_by_other_agent",
    "same_open_todo_after_progress",
}
DELIVERY_BOUNDARY_REASONS = {
    *DELIVERY_CONTINUITY_PREEMPTIONS,
    "no_selected_todo",
    "todo_not_open",
    "todo_not_advancement",
    "todo_not_actionable",
    "todo_capability_blocked",
    "todo_claimed_by_other_agent",
    "open_advancement_todo",
}
DELIVERY_ROUTING_SELECTIONS = {"continuity", "fallback", "replan", "none"}


def normalize_delivery_boundary(value: Any) -> str:
    boundary = str(value or DELIVERY_BOUNDARY_SEMANTIC_CLOSEOUT).strip()
    if boundary not in DELIVERY_BOUNDARY_VALUES:
        raise ValueError(
            "delivery_boundary must be one of: " + ", ".join(DELIVERY_BOUNDARY_VALUES)
        )
    return boundary


def _delivery_todo_payload(
    current_todo: Mapping[str, Any] | None,
    *,
    actionable: bool,
    capability_ready: bool,
) -> dict[str, Any] | None:
    if current_todo is None:
        return None
    todo_id = normalize_todo_id(current_todo.get("todo_id"))
    if not todo_id:
        raise ValueError("delivery continuity current Todo requires a typed todo_id")
    return {
        "todo_id": todo_id,
        "status": normalize_todo_status(current_todo.get("status")) or TODO_STATUS_OPEN,
        "task_class": todo_item_task_class(dict(current_todo)),
        "claimed_by": normalize_todo_claimed_by(current_todo.get("claimed_by")),
        "actionable": bool(actionable),
        "capability_ready": bool(capability_ready),
        **({"replan_obligation_id": current_todo["replan_obligation_id"]}
           if current_todo.get("replan_obligation_id") else {}),
    }


def _normalize_preemptions(preemptions: Sequence[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(item).strip() for item in preemptions))
    if any(item not in DELIVERY_CONTINUITY_PREEMPTIONS for item in normalized):
        raise ValueError("delivery continuity received an unsupported preemption")
    return normalized


def _valid_continuity_result(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("schema_version") == DELIVERY_CONTINUITY_RESULT_SCHEMA
        and value.get("decision") in DELIVERY_CONTINUITY_DECISIONS
        and value.get("reason") in DELIVERY_CONTINUITY_REASONS
        and value.get("delivery_boundary") in DELIVERY_BOUNDARY_VALUES
        and (value.get("todo_id") is None or isinstance(value.get("todo_id"), str))
    )


def _valid_boundary_result(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("schema_version") == DELIVERY_BOUNDARY_RESULT_SCHEMA
        and value.get("delivery_boundary") in DELIVERY_BOUNDARY_VALUES
        and value.get("reason") in DELIVERY_BOUNDARY_REASONS
        and (value.get("todo_id") is None or isinstance(value.get("todo_id"), str))
    )


def evaluate_delivery_route(
    *,
    agent_id: str,
    previous_todo_id: str | None,
    previous_delivery_outcome: str | None,
    continuity_todo: Mapping[str, Any] | None,
    continuity_actionable: bool,
    continuity_capability_ready: bool,
    fallback_todo: Mapping[str, Any] | None,
    fallback_actionable: bool,
    fallback_capability_ready: bool,
    preemptions: Sequence[str] = (),
    replan_todo: Mapping[str, Any] | None = None,
    latest_replan_ack: Mapping[str, Any] | None = None,
    replan_actionable: bool = False,
    replan_capability_ready: bool = False,
) -> dict[str, Any]:
    """Resolve continuity selection and settlement boundary in one TS request."""

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not safe_agent_id:
        raise ValueError("delivery routing requires a valid agent_id")
    safe_previous_todo_id = normalize_todo_id(previous_todo_id)
    continuity_payload = (
        _delivery_todo_payload(
            continuity_todo,
            actionable=continuity_actionable,
            capability_ready=continuity_capability_ready,
        )
        if safe_previous_todo_id
        else None
    )
    fallback_payload = _delivery_todo_payload(
        fallback_todo,
        actionable=fallback_actionable,
        capability_ready=fallback_capability_ready,
    )
    replan_payload = _delivery_todo_payload(
        replan_todo,
        actionable=replan_actionable,
        capability_ready=replan_capability_ready,
    )
    try:
        result = effect_runtime_result(
            "turn.delivery_route.evaluate",
            {
                "schema_version": DELIVERY_ROUTING_REQUEST_SCHEMA,
                "agent_id": safe_agent_id,
                "previous_todo_id": safe_previous_todo_id,
                "previous_delivery_outcome": (
                    str(previous_delivery_outcome or "").strip() or None
                ),
                "continuity_todo": continuity_payload,
                "fallback_todo": fallback_payload,
                "preemptions": _normalize_preemptions(preemptions),
                **({"replan_todo": replan_payload} if replan_payload else {}),
                **({"latest_replan_ack": dict(latest_replan_ack)}
                   if latest_replan_ack else {}),
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise TypeError("TypeScript delivery routing result must be an object")
    continuity = result.get("continuity")
    boundary = result.get("boundary")
    selection = result.get("selection")
    continuity_expected = safe_previous_todo_id is not None
    selected_todo_id = (
        (continuity_payload or {}).get("todo_id")
        if selection == "continuity"
        else (fallback_payload or {}).get("todo_id")
        if selection == "fallback"
        else (replan_payload or {}).get("todo_id")
        if selection == "replan"
        else None
    )
    if (
        result.get("schema_version") != DELIVERY_ROUTING_RESULT_SCHEMA
        or selection not in DELIVERY_ROUTING_SELECTIONS
        or not (continuity is None or _valid_continuity_result(continuity))
        or not (boundary is None or _valid_boundary_result(boundary))
        or continuity_expected != isinstance(continuity, Mapping)
        or (
            selection == "continuity"
            and (
                continuity_payload is None
                or continuity is None
                or continuity.get("decision") != "resume_in_flight"
                or continuity.get("todo_id") != selected_todo_id
                or not isinstance(boundary, Mapping)
                or boundary.get("delivery_boundary")
                != continuity.get("delivery_boundary")
            )
        )
        or (
            isinstance(continuity, Mapping)
            and continuity.get("decision") == "resume_in_flight"
            and selection not in {"continuity", "replan"}
        )
        or (selection == "fallback" and fallback_payload is None)
        or (selection == "replan" and (replan_payload is None or preemptions))
        or (selection == "none" and boundary is not None)
        or (selection != "none" and boundary is None)
        or (
            isinstance(boundary, Mapping)
            and boundary.get("todo_id") != selected_todo_id
        )
    ):
        raise RuntimeError("TypeScript delivery routing result shape mismatch")
    return dict(result)
