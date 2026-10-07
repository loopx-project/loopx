from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any

from .contract import (
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
    normalize_todo_status,
    todo_done_for_status,
)

TODO_HANDOFF_GATE_SCHEMA_VERSION = "todo_handoff_gate_v1"


class HandoffGateState(str, Enum):
    BLOCKING = "blocking"
    CLEARED_WITHOUT_SUCCESSOR = "cleared_without_successor"
    CLEARED_WITH_SUCCESSOR = "cleared_with_successor"
    CLEARED_NO_FOLLOWUP = "cleared_no_followup"
    SUPERSEDED = "superseded"
    DEFERRED = "deferred"


def _todo_status(item: dict[str, Any]) -> str:
    explicit = normalize_todo_status(item.get("status"))
    if explicit:
        return explicit
    return TODO_STATUS_DONE if item.get("done") is True else TODO_STATUS_OPEN


def _todo_done(item: dict[str, Any]) -> bool:
    return item.get("done") is True or todo_done_for_status(_todo_status(item))


def _todo_text(item: dict[str, Any]) -> str:
    return str(item.get("text") or "").strip()


def _stale_handoff_closeout_replan_required(gate: dict[str, Any]) -> bool:
    # Legacy compatibility only: old authors did not write the typed route flag.
    # Do not use this prose hint for successor existence, gate state or permission.
    # Retire it after the remaining route-closeout writers emit the typed flag.
    if isinstance(gate.get("route_continuation_replan_required"), bool):
        return gate["route_continuation_replan_required"] is True
    if _todo_done(gate):
        return False
    label = " ".join(
        str(gate.get(key) or "")
        for key in ("action_kind", "title", "text")
        if str(gate.get(key) or "").strip()
    ).lower()
    return "stale" in label and "handoff" in label and "closeout" in label


def _compact_handoff_gate(
    gate: dict[str, Any],
    *,
    state: HandoffGateState,
    successor_ids: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": TODO_HANDOFF_GATE_SCHEMA_VERSION,
        "gate_state": state.value,
        "index": gate.get("index"),
        "done": _todo_done(gate),
        "status": _todo_status(gate),
        "text": _todo_text(gate),
        "excluded_agents": normalize_todo_excluded_agents(gate.get("excluded_agents")),
        "successor_count": len(successor_ids),
    }
    for key in (
        "todo_id",
        "role",
        "task_class",
        "action_kind",
        "task_domain",
        "task_repository",
        "continuation_policy",
        "claimed_by",
        "unblocks_todo_id",
        "resume_when",
        "no_followup",
        "superseded_by",
        "route_continuation_replan_required",
        "route_continuation_reason",
        "route_id",
        "route_key",
        "next_due_at",
    ):
        value = gate.get(key)
        if value is not None:
            payload[key] = value
    claimed_by = normalize_todo_claimed_by(payload.get("claimed_by"))
    if claimed_by:
        payload["claimed_by"] = claimed_by
    unblocks_todo_id = normalize_todo_id(payload.get("unblocks_todo_id"))
    if unblocks_todo_id:
        payload["unblocks_todo_id"] = unblocks_todo_id
    superseded_by = normalize_todo_id(payload.get("superseded_by"))
    if superseded_by:
        payload["superseded_by"] = superseded_by
    if _stale_handoff_closeout_replan_required(gate):
        payload["route_continuation_replan_required"] = True
        payload.setdefault(
            "route_continuation_reason",
            "stale handoff closeout requires a bounded route continuation replan",
        )
    if successor_ids:
        payload["successor_todo_ids"] = successor_ids
    return {key: value for key, value in payload.items() if value not in (None, "")}


def build_todo_handoff_gate_states(
    items: Iterable[Any], *, evaluations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Render the typed full-source handoff decision; retain presentation only."""
    from .succession_warning import project_succession

    todo_items = [item for item in items if isinstance(item, dict)]
    decisions = evaluations if evaluations is not None else project_succession(todo_items)
    gates = [
        _compact_handoff_gate(item, state=HandoffGateState(decision["handoff_state"]),
            successor_ids=decision["successor_todo_ids"])
        for item, decision in zip(todo_items, decisions, strict=True)
        if decision["handoff_state"] is not None
    ]
    return sorted(gates, key=lambda item: (int(item.get("index") or 999999), str(item.get("todo_id") or "")))


def todo_summary_handoff_gates(value: dict[str, Any]) -> list[dict[str, Any]]:
    projected = value.get("handoff_gates")
    if isinstance(projected, list):
        return [item for item in projected if isinstance(item, dict)]
    raw_items = value.get("items")
    source_items = raw_items if isinstance(raw_items, list) else []
    return build_todo_handoff_gate_states(source_items)


def handoff_ready_successor_todo_ids(value: dict[str, Any]) -> set[str]:
    ready: set[str] = set()
    for gate in todo_summary_handoff_gates(value):
        if not isinstance(gate, dict):
            continue
        if str(gate.get("gate_state") or "") != HandoffGateState.CLEARED_WITH_SUCCESSOR.value:
            continue
        successor_ids = gate.get("successor_todo_ids")
        if not isinstance(successor_ids, list):
            continue
        for todo_id in successor_ids:
            normalized = normalize_todo_id(todo_id)
            if normalized:
                ready.add(normalized)
    return ready
