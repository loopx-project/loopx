from __future__ import annotations

from typing import Any, Sequence

from collections.abc import Mapping

from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from ..todos.todo_semantics import (
    todo_item_is_actionable_open,
    todo_item_task_class,
)
from ..todos.summary_item import compact_todo_summary_items
from .primary_action import protocol_action_text

TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION = (
    "todo_planning_inventory_request_v0"
)


def _prepare_planning_candidates(
    values: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str | None]]:
    sources: list[dict[str, Any]] = []
    texts: list[str | None] = []
    for value in values:
        todo_id = normalize_todo_id(value.get("todo_id"))
        text = protocol_action_text(value.get("text"), limit=500)
        if todo_id and text:
            sources.append(dict(value))
            texts.append(text)
    return sources, texts


def _attach_candidate_metadata(
    sources: Sequence[Mapping[str, Any]],
    compacts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value, compact in zip(sources, compacts, strict=True):
        for field in ("source", "selected_by", "availability_reason"):
            if value.get(field) is not None:
                compact[field] = value[field]
        result.append(compact)
    return result


def compact_planning_candidates(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sources, texts = _prepare_planning_candidates(values)
    compacts = compact_todo_summary_items(sources, texts=texts)
    return _attach_candidate_metadata(sources, compacts)


def compact_planning_candidate(value: Mapping[str, Any]) -> dict[str, Any] | None:
    compacts = compact_planning_candidates([value])
    return compacts[0] if compacts else None


def _raw_runnable_action_candidates(
    *,
    agent_id: str,
    agent_todo_summary: Mapping[str, Any] | None,
    capability_gate: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Adapt already-gated Python eligibility facts into raw inventory inputs."""

    if not isinstance(agent_todo_summary, Mapping):
        return []
    capability_candidates = (
        capability_gate.get("runnable_candidates")
        if isinstance(capability_gate, Mapping)
        else None
    )
    if isinstance(capability_candidates, list):
        sources: list[list[Any]] = [capability_candidates]
    else:
        sources = []
        for field in (
            "active_next_action_executable_items",
            "first_executable_items",
            "executable_backlog_items",
        ):
            items = agent_todo_summary.get(field)
            if isinstance(items, list):
                sources.append(items)

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for items in sources:
        for item in items:
            if not isinstance(item, Mapping):
                continue
            candidate = dict(item)
            claimed_by = normalize_todo_claimed_by(candidate.get("claimed_by"))
            if (
                not todo_item_is_actionable_open(candidate)
                or todo_item_task_class(candidate) != TODO_TASK_CLASS_ADVANCEMENT
                or (claimed_by is not None and claimed_by != agent_id)
            ):
                continue
            todo_id = normalize_todo_id(candidate.get("todo_id"))
            if not todo_id or not protocol_action_text(candidate.get("text"), limit=500) or todo_id in seen:
                continue
            seen.add(todo_id)
            candidates.append(candidate)
    return candidates


def quota_runnable_action_candidates(
    *,
    agent_id: str,
    agent_todo_summary: Mapping[str, Any] | None,
    capability_gate: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Adapt already-gated Python eligibility facts into inventory inputs."""
    return compact_planning_candidates(
        _raw_runnable_action_candidates(
            agent_id=agent_id,
            agent_todo_summary=agent_todo_summary,
            capability_gate=capability_gate,
        )
    )


def _unavailable_reason(candidate: Mapping[str, Any]) -> str:
    condition = candidate.get("resume_condition")
    if candidate.get("resume_when") and candidate.get("resume_ready") is False:
        availability_reason = (
            condition.get("availability_reason")
            if isinstance(condition, Mapping)
            else None
        )
        return str(availability_reason or "resume_condition_pending")
    if (
        todo_item_task_class(candidate) == TODO_TASK_CLASS_MONITOR
        and candidate.get("next_due_at")
    ):
        return "scheduled_for_future"
    if candidate.get("status"):
        return f"status_{str(candidate['status']).strip().lower()}"
    return "not_currently_executable"


def _raw_unavailable_higher_priority_candidates(
    blocked_priority_fallback: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(blocked_priority_fallback, Mapping):
        return []
    items = blocked_priority_fallback.get("blocked_items")
    unavailable: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        candidate = dict(item)
        candidate["availability_reason"] = _unavailable_reason(candidate)
        unavailable.append(candidate)
    return unavailable


def unavailable_higher_priority_candidates(
    blocked_priority_fallback: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    return compact_planning_candidates(
        _raw_unavailable_higher_priority_candidates(blocked_priority_fallback)
    )


def _source_context_count(summary: Mapping[str, Any] | None) -> int:
    if not isinstance(summary, Mapping):
        return 0
    counts = (summary.get("open_count"), summary.get("deferred_count"))
    return sum(value for value in counts if type(value) is int and value >= 0)


def build_quota_planning_inventory_request(
    *,
    goal_id: str,
    selected: Mapping[str, Any] | None,
    agent_id: str | None,
    agent_todo_summary: Mapping[str, Any] | None,
    agent_todo_source_items: list[dict[str, Any]],
    capability_gate: Mapping[str, Any] | None,
    blocked_priority_fallback: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Adapt Python Todo sources into one TypeScript inventory request."""

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    selected_sources, selected_texts = _prepare_planning_candidates(
        [selected] if selected is not None else []
    )
    if not goal_id or not safe_agent_id or not selected_sources:
        return None
    source_sources, source_texts = _prepare_planning_candidates(agent_todo_source_items)
    runnable_sources, runnable_texts = _prepare_planning_candidates(
        _raw_runnable_action_candidates(
            agent_id=safe_agent_id,
            agent_todo_summary=agent_todo_summary,
            capability_gate=capability_gate,
        )
    )
    unavail_sources, unavail_texts = _prepare_planning_candidates(
        _raw_unavailable_higher_priority_candidates(blocked_priority_fallback)
    )
    all_sources = [*selected_sources, *source_sources, *runnable_sources, *unavail_sources]
    all_texts = [*selected_texts, *source_texts, *runnable_texts, *unavail_texts]
    all_compacts = compact_todo_summary_items(all_sources, texts=all_texts)
    attached = _attach_candidate_metadata(all_sources, all_compacts)
    n0 = len(selected_sources)
    n1 = n0 + len(source_sources)
    n2 = n1 + len(runnable_sources)
    return {
        "schema_version": TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": safe_agent_id,
        "selected_todo": attached[0],
        "source_items": attached[n0:n1],
        "runnable_candidates": attached[n1:n2],
        "unavailable_higher_priority": attached[n2:],
        "source_context_todo_count": _source_context_count(agent_todo_summary),
    }
