"""Adapt canonical lifecycle facts to the existing typed replan owner."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .replan_semantics_codec import project_replan_semantics


def receipt_bound_lifecycle_candidate(
    *,
    selected_obligation_id: str,
    guard_recorded_at: str,
    agent_id: str | None,
    items: list[dict[str, Any]],
    derive: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any] | None:
    facts = project_replan_semantics({
        "operation": "lifecycle_source", "guard_recorded_at": guard_recorded_at,
        "agent_id": agent_id, "items": items,
    })
    if not facts.get("settled"):
        return None
    gaps = facts["items"]
    source = derive({"completed_without_successor_items": gaps,
                     "completed_without_successor_count": len(gaps)})
    if not source or source.get("obligation_id") != selected_obligation_id:
        return None
    result = project_replan_semantics({
        "operation": "lifecycle_obligation", "obligation": source,
        "selected_obligation_id": selected_obligation_id,
        "guard_recorded_at": guard_recorded_at, "agent_id": agent_id, "items": items,
    })
    return {"obligation": result["obligation"], "source_obligation": source,
            "guard_recorded_at": guard_recorded_at, "items": items,
            "ack": None, "lifecycle_settled": True}
