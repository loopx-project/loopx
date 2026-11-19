"""Shared compatibility display codec; no Todo selection or mutation authority.

Callers own text presentation and explicit extension fields. Omission and
scope normalization stay common; canonical records never round-trip through it.
"""

from __future__ import annotations

from typing import Any

from .contract import (
    normalize_required_write_scopes,
    normalize_todo_decision_scope,
    normalize_todo_required_decision_scopes,
    normalize_todo_task_class,
)


def projection_task_class(item: dict[str, Any]) -> str:
    text = " ".join(
        str(value or "")
        for value in (item.get("title"), item.get("text"))
        if str(value or "").strip()
    )
    return normalize_todo_task_class(
        item.get("task_class"),
        text=text,
        action_kind=item.get("action_kind"),
    )


def compact_todo_projection_item(
    item: dict[str, Any],
    *,
    text: Any,
    extra_fields: tuple[str, ...] = (),
    task_class_text: str | None = None,
) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "index": item.get("index"),
        "text": text,
    }
    for key in (
        "schema_version",
        "todo_id",
        "role",
        "status",
        "priority",
        "title",
        "archive_state",
        "source_section",
        "task_class",
        "action_kind",
        "task_domain",
        "task_repository",
        "continuation_policy",
        "required_write_scopes",
        "required_capabilities",
        "target_capabilities",
        "decision_scope",
        "required_decision_scopes",
        "claimed_by",
        "blocks_agent",
        "excluded_agents",
        "unblocks_todo_id",
        "resume_when",
        "resume_monitor_generation",
        "resume_condition",
        "resume_ready",
        "no_followup",
        "successor_todo_ids",
        "target_key",
        "cadence",
        "next_due_at",
        "expires_at",
        "last_checked_at",
        "result_hash",
        "consecutive_no_change",
        "material_change",
        "material_change_generation",
        "material_change_scope",
        "material_change_scope_generations",
        "max_no_change_before_replan",
        "route_continuation_replan_required",
        "route_continuation_reason",
        "route_id",
        "route_key",
        "completed_at",
        "updated_at",
        "superseded_by",
    ) + extra_fields:
        if item.get(key) is not None:
            compact[key] = item.get(key)
    required_write_scopes = normalize_required_write_scopes(compact.get("required_write_scopes"))
    if required_write_scopes:
        compact["required_write_scopes"] = required_write_scopes
    else:
        compact.pop("required_write_scopes", None)
    decision_scope = normalize_todo_decision_scope(compact.get("decision_scope"))
    if decision_scope:
        compact["decision_scope"] = decision_scope
    else:
        compact.pop("decision_scope", None)
    required_decision_scopes = normalize_todo_required_decision_scopes(
        compact.get("required_decision_scopes")
    )
    if required_decision_scopes:
        compact["required_decision_scopes"] = required_decision_scopes
    else:
        compact.pop("required_decision_scopes", None)
    compact["task_class"] = (
        projection_task_class(compact)
        if task_class_text is None
        else normalize_todo_task_class(
            compact.get("task_class"),
            text=task_class_text,
            action_kind=compact.get("action_kind"),
        )
    )
    return compact
