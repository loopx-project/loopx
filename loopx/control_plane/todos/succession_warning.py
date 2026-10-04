"""Succession read-policy adapter and existing warning presentation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..effect_runtime import effect_runtime_result
from ..agents.agent_scope import agent_scope_item_claimed_by_agent_or_unclaimed
from .compact_projection import compact_todo_projection_item
from .contract import (
    TODO_STATUS_OPEN,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_status,
    normalize_todo_task_class,
    normalize_todo_no_followup,
    normalize_todo_resume_when,
    normalize_todo_excluded_agents,
)


# Both adapters consume the same positional schema; TS owns semantic validation.
_SUCCESSION_WIRE = json.loads(Path(__file__).with_name("succession_wire_v1.json").read_text(encoding="utf-8"))
SUCCESSION_FACT_COLUMNS = tuple(_SUCCESSION_WIRE["fact_columns"])
SUCCESSION_EVALUATION_COLUMNS = tuple(_SUCCESSION_WIRE["evaluation_columns"])


TODO_SUCCESSION_WARNING_SCHEMA_VERSION = "todo_succession_warning_v0"
TODO_SUCCESSION_WARNING_REASON_CODE = "completed_advancement_without_successor"
TODO_PARENT_SUCCESSOR_ADVISORY_SCHEMA_VERSION = "todo_parent_successor_advisory_v0"


def build_open_parent_successor_advisory(
    *,
    todo_id: Any,
    status: Any,
    successor_todo_ids: Any,
) -> dict[str, Any]:
    """Warn at authoring time that successor links do not suspend an open parent."""

    normalized_todo_id = normalize_todo_id(todo_id)
    normalized_successor_ids = normalize_todo_id_list(successor_todo_ids)
    if status != TODO_STATUS_OPEN or not normalized_todo_id or not normalized_successor_ids:
        return {}
    return {
        "schema_version": TODO_PARENT_SUCCESSOR_ADVISORY_SCHEMA_VERSION,
        "reason_code": "open_parent_remains_runnable_after_successor_link",
        "todo_id": normalized_todo_id,
        "status": TODO_STATUS_OPEN,
        "successor_todo_ids": normalized_successor_ids,
        "successor_semantics": "lineage_only",
        "parent_remains_quota_runnable": True,
        "automatic_transition_applied": False,
        "authoring_decision_required": True,
        "recommended_action": (
            "If the parent has no independent immediate action, explicitly defer it with "
            "resume_when or complete it; otherwise leave it open intentionally."
        ),
    }


def build_todo_succession_warning_lanes(
    summary: dict[str, Any],
    *,
    item_limit: int,
) -> dict[str, Any]:
    warning = summary.get("todo_succession_warning")
    warning = warning if isinstance(warning, dict) else {}
    source_items = (
        warning.get("items")
        if isinstance(warning.get("items"), list)
        else summary.get("completed_without_successor_items")
    )
    items = [
        compact_todo_projection_item(
            item, text=item.get("text"), task_class_text=str(item.get("text") or ""),
            extra_fields=("completion_continuation", "completion_recovery", "completion_turn_key",
                          "done", "succession_tracked", "recommended_action"),
        )
        for item in (source_items or [])
        if isinstance(item, dict)
    ][:item_limit]
    count = warning.get("count", summary.get("completed_without_successor_count"))
    try:
        count = max(0, int(count)) if count is not None else len(items)
    except (TypeError, ValueError):
        count = len(items)
    if count <= 0 and not items:
        return {}

    payload = {
        "schema_version": warning.get(
            "schema_version",
            TODO_SUCCESSION_WARNING_SCHEMA_VERSION,
        ),
        "reason_code": warning.get(
            "reason_code",
            TODO_SUCCESSION_WARNING_REASON_CODE,
        ),
        "count": count,
        "items": items,
        "recommended_action": warning.get(
            "recommended_action",
            (
                "run loopx todo complete --no-follow-up for the completed Todo, "
                "or add/link a successor Todo; do not invent a user gate"
            ),
        ),
    }
    return {
        "completed_without_successor_count": count,
        "completed_without_successor_items": items,
        "todo_succession_warning": payload,
    }


def todo_succession_gap_items(
    summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    """Return current typed succession gaps owned by this agent lane."""

    if not isinstance(summary, dict):
        return []
    raw_warning = summary.get("todo_succession_warning")
    warning = raw_warning if isinstance(raw_warning, dict) else {}
    if warning and warning.get("reason_code") != TODO_SUCCESSION_WARNING_REASON_CODE:
        return []
    warning_items = warning.get("items")
    completed_items = summary.get("completed_without_successor_items")
    source_items = (
        warning_items
        if isinstance(warning_items, list)
        else completed_items
        if isinstance(completed_items, list)
        else []
    )
    items = [item for item in source_items if isinstance(item, dict)]
    if not agent_id:
        return items
    return [
        item
        for item in items
        if agent_scope_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
    ]


class _EvaluatedTodo(dict[str, Any]):
    """Ephemeral graph evidence stays outside the public row/JSON contract."""

    def __init__(self, item: dict[str, Any], evaluation: dict[str, Any]) -> None:
        super().__init__(item)
        self.succession_evaluation = evaluation


def succession_facts(item: dict[str, Any]) -> dict[str, Any]:
    resume = normalize_todo_resume_when(item.get("resume_when")) or ""
    return {
        "todo_id": normalize_todo_id(item.get("todo_id")),
        "status": normalize_todo_status(item.get("status")) or ("done" if item.get("done") else "open"),
        "active": (item.get("archive_state") or "active") == "active",
        "advancement": normalize_todo_task_class(item.get("task_class"), text=str(item.get("text") or ""),
            action_kind=item.get("action_kind")) == "advancement_task",
        "no_followup": normalize_todo_no_followup(item.get("no_followup")) is True,
        "successors": normalize_todo_id_list(item.get("successor_todo_ids")),
        "superseded_by": normalize_todo_id(item.get("superseded_by")),
        "unblocks": normalize_todo_id(item.get("unblocks_todo_id")),
        "resumes": normalize_todo_id(resume.partition(":")[2]) if resume.startswith("todo_done:") else None,
        "handoff": bool(normalize_todo_excluded_agents(item.get("excluded_agents")))
            and bool(normalize_todo_id(item.get("unblocks_todo_id"))),
        "context_fields": sorted(key for key, value in item.items()
            if value is not None and key != "succession_evaluation"),
    }


def succession_request(items: list[dict[str, Any]], *, reuse: bool = False) -> dict[str, Any]:
    """Encode the existing owner request; do not validate or decide in Python."""
    rows = [succession_facts(item) for item in items]
    # Archived histories repeat field-presence shapes thousands of times. Intern
    # those shapes, preserving the complete graph inside the existing RPC budget.
    contexts: list[list[str]] = []
    context_ids: dict[tuple[str, ...], int] = {}
    for row in rows:
        shape = tuple(row["context_fields"])
        if shape not in context_ids:
            context_ids[shape] = len(contexts)
            contexts.append(list(shape))
        row["context_fields"] = context_ids[shape]
    request: dict[str, Any] = {"schema_version": _SUCCESSION_WIRE["request_schema"],
        "row_columns": list(SUCCESSION_FACT_COLUMNS),
        "rows": [[row[name] for name in SUCCESSION_FACT_COLUMNS] for row in rows],
        "context_field_sets": contexts}
    if reuse:
        request["evaluation_columns"] = list(SUCCESSION_EVALUATION_COLUMNS)
        request["evaluations"] = [
            [item.succession_evaluation.get(name) for name in SUCCESSION_EVALUATION_COLUMNS]
            if isinstance(item, _EvaluatedTodo) else None for item in items]
    return request


def succession_evaluations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Carry ephemeral evidence to a fused owner; it must still validate it."""
    if any(not isinstance(item, _EvaluatedTodo) for item in items):
        raise ValueError("Todo display requires a matching full-source succession evaluation")
    return [item.succession_evaluation for item in items]


def project_succession(items: list[dict[str, Any]], *, reuse: bool = False) -> list[dict[str, Any]]:
    if items == []:
        # No graph/evidence is supplied and no row can be returned. Empty
        # summary closure still goes through its ordinary typed projection.
        return []
    result = effect_runtime_result("todo.succession.project", succession_request(items, reuse=reuse))
    if (not isinstance(result, dict) or result.get("schema_version") != _SUCCESSION_WIRE["result_schema"]
            or result.get("evaluation_columns") != list(SUCCESSION_EVALUATION_COLUMNS)):
        raise ValueError("invalid typed Todo succession result")
    evaluations = result.get("evaluations")
    if (not isinstance(evaluations, list) or len(evaluations) != len(items)
            or any(not isinstance(row, list) or len(row) != len(SUCCESSION_EVALUATION_COLUMNS) for row in evaluations)):
        raise ValueError("invalid typed Todo succession cardinality")
    return [dict(zip(SUCCESSION_EVALUATION_COLUMNS, row, strict=True)) for row in evaluations]


def evaluate_succession(items: list[dict[str, Any]], lineage: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    # Active evaluated rows override their unevaluated source versions; retained
    # history and other roles remain graph evidence but never become active rows.
    if not items:
        return []
    # Replace one matching source row, not every occurrence of its identity.
    # Retained generations and conflicting authorities stay visible to TS.
    selected = {(normalize_todo_id(item.get("todo_id")), item.get("role"), item.get("archive_state") or "active")
        for item in items}
    source = []
    for item in lineage or []:
        key = (normalize_todo_id(item.get("todo_id")), item.get("role"), item.get("archive_state") or "active")
        if key in selected:
            selected.remove(key)
        else:
            source.append(item)
    source.extend(items)
    evaluations = project_succession(source)
    selected_evaluations = evaluations[len(source) - len(items):]
    items[:] = [_EvaluatedTodo(item, evaluation)
        for item, evaluation in zip(items, selected_evaluations, strict=True)]
    return selected_evaluations


def public_todo_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Drop the internal full-graph handoff once a consumer has selected rows."""
    return {**summary, "items": [
        dict(item) if isinstance(item, dict) else item
        for item in summary.get("items") or []
    ]}
