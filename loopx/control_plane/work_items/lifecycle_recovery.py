"""Adapt canonical lifecycle facts to the existing typed replan owner."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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
    if not facts.get("items"):
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
            "ack": None, "lifecycle_settled": facts["settled"]}


def attach_receipt_replan_context(
    current: dict[str, Any] | None, lifecycle_candidate: dict[str, Any] | None, *,
    goal_id: str, agent_id: str | None, source_status: Mapping[str, Any] | None,
    stride_strategy: str,
    explore_reader: Callable[[str, str | None], Mapping[str, Any] | None] | None,
    goal_acceptance_contract: Mapping[str, Any] | None,
    todo_items: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Contextualize pending receipt actions without borrowing a new frontier."""
    from .progress_observation import build_replan_context

    current = dict(current) if current else None
    obligations = [current] if current else []
    pending = lifecycle_candidate if lifecycle_candidate and not lifecycle_candidate["lifecycle_settled"] else None
    if pending:
        obligations.append(pending["source_obligation"])
    if not obligations:
        return current
    todos = list(todo_items) if todo_items is not None else None
    explore_context = explore_reader(goal_id, agent_id) if stride_strategy != "off" and explore_reader else None
    for obligation in obligations:
        obligation["replan_context"] = build_replan_context(
            obligation, goal_id=goal_id, agent_id=agent_id, newest_first_runs=(),
            source_status=source_status, stride_strategy=stride_strategy,
            explore_context=explore_context, goal_acceptance_contract=goal_acceptance_contract,
            todo_items=todos,
        )
    if pending:
        pending["obligation"]["replan_context"] = pending["source_obligation"]["replan_context"]
    return current
