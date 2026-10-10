from __future__ import annotations

from hashlib import sha256
from typing import Any

from .contract import (
    normalize_removed_todo_continuation_policy,
    normalize_required_write_scopes,
    normalize_todo_decision_outcome,
    normalize_todo_decision_scope,
    normalize_todo_decision_scope_outcomes,
    normalize_todo_excluded_agents,
    normalize_todo_id,
    normalize_todo_required_decision_scopes,
    normalize_todo_replan_obligation_id,
    normalize_todo_resume_when,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
)
from .handoff_gate import handoff_ready_successor_todo_ids
from .handoff_note import handoff_context_source, project_handoff_context
from .todo_semantics import todo_blocker_reason, todo_item_task_class
from .frontier_revision import FRONTIER_REVISION_FIELDS

TODO_SUMMARY_COMPACT_FIELDS = (
    "goal_acceptance_guard",
    "schema_version",
    "todo_id",
    "content_revision",
    "role",
    "status",
    "priority",
    "title",
    "archive_state",
    "source_section",
    "task_class",
    "action_kind",
    "task_domain",
    "capability_binding_ref",
    "task_repository",
    "continuation_policy",
    "removed_continuation_policy",
    "required_write_scopes",
    "required_capabilities",
    "target_capabilities",
    "decision_scope",
    "required_decision_scopes",
    "decision_outcome",
    "decision_scope_outcomes",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "excluded_agents",
    "global_gate",
    "unblocks_todo_id",
    "resume_when",
    "resume_monitor_generation",
    "resume_condition",
    "resume_ready",
    "no_followup",
    "successor_todo_ids",
    "completion_continuation",
    "completion_recovery",
    "replan_obligation_id",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "watch_only",
    "last_checked_at",
    "result_hash",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "max_no_change_before_replan",
    "route_continuation_replan_required",
    "route_continuation_reason",
    "route_id",
    "route_key",
    "completed_at",
    "updated_at",
    "completion_turn_key",
    "superseded_by",
    "handoff_note",
)

TODO_SUMMARY_SOURCE_KEYS = (
    "active_next_action_items",
    "active_next_action_executable_items",
    "first_open_items",
    "backlog_items",
    "unclaimed_priority_open_items",
    "claimed_open_items",
    "claimed_advancement_open_items",
    "claimed_monitor_open_items",
    "monitor_open_items",
    "current_agent_claimed_open_items",
    "current_agent_claimed_advancement_items",
    "current_agent_claimed_monitor_items",
    "blocker_items",
    "resume_blocked_items",
    "monitor_blocked_resume_candidates",
    "current_agent_monitor_blocked_resume_candidates",
    "unclaimed_monitor_blocked_resume_candidates",
    "items",
)

TODO_PLANNING_SOURCE_KEYS = (
    "items",
    "deferred_items",
    "blocker_items",
    "monitor_open_items",
)


def todo_text_content_revision(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return "sha256:" + sha256(normalized.encode("utf-8")).hexdigest()


def _compact_todo_summary_item_record(
    item: dict[str, Any],
    *,
    text: str | None = None,
) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "index": item.get("index"),
        "text": text if text is not None else item.get("text"),
    }
    content_revision = item.get("content_revision")
    if content_revision:
        compact["content_revision"] = content_revision
    for key in TODO_SUMMARY_COMPACT_FIELDS:
        if item.get(key) is not None:
            compact[key] = item.get(key)
    required_write_scopes = normalize_required_write_scopes(
        compact.get("required_write_scopes")
    )
    task_domain = normalize_todo_task_domain(compact.get("task_domain"))
    if task_domain:
        compact["task_domain"] = task_domain
    else:
        compact.pop("task_domain", None)
    if required_write_scopes:
        compact["required_write_scopes"] = required_write_scopes
    else:
        compact.pop("required_write_scopes", None)
    task_repository = normalize_todo_task_repository(compact.get("task_repository"))
    if task_repository:
        compact["task_repository"] = task_repository
    else:
        compact.pop("task_repository", None)
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
    decision_outcome = normalize_todo_decision_outcome(compact.get("decision_outcome"))
    if decision_outcome:
        compact["decision_outcome"] = decision_outcome
    else:
        compact.pop("decision_outcome", None)
    decision_scope_outcomes = normalize_todo_decision_scope_outcomes(
        compact.get("decision_scope_outcomes")
    )
    if decision_scope_outcomes:
        compact["decision_scope_outcomes"] = decision_scope_outcomes
    else:
        compact.pop("decision_scope_outcomes", None)
    excluded_agents = normalize_todo_excluded_agents(compact.get("excluded_agents"))
    if excluded_agents:
        compact["excluded_agents"] = excluded_agents
    else:
        compact.pop("excluded_agents", None)
    removed_continuation_policy = normalize_removed_todo_continuation_policy(
        compact.get("removed_continuation_policy")
    )
    if removed_continuation_policy:
        compact["removed_continuation_policy"] = removed_continuation_policy
    else:
        compact.pop("removed_continuation_policy", None)
    replan_obligation_id = normalize_todo_replan_obligation_id(
        compact.get("replan_obligation_id")
    )
    if replan_obligation_id:
        compact["replan_obligation_id"] = replan_obligation_id
    else:
        compact.pop("replan_obligation_id", None)
    compact["task_class"] = todo_item_task_class(compact)
    reason = todo_blocker_reason(item)
    if reason:
        compact["reason"] = reason
    return compact


def compact_todo_summary_items(
    items: list[dict[str, Any]], *, texts: list[str | None] | None = None, strip_text: bool = False,
) -> list[dict[str, Any]]:
    if texts is None:
        texts = [str(item.get("text") or "").strip() for item in items] if strip_text else [None] * len(items)
    records = [_compact_todo_summary_item_record(item, text=text)
               for item, text in zip(items, texts, strict=True)]
    contexts = project_handoff_context([
        handoff_context_source(item) for pair in zip(items, records, strict=True) for item in pair
    ])
    for index, record in enumerate(records):
        hint, note = contexts[2 * index]["continuation_hint"], contexts[2 * index + 1]["note"]
        if record["task_class"] == "advancement_task" and record.get("status") == "open" and hint:
            record["continuation_hint"] = hint
        if note:
            record["handoff_note"] = note
    return records


def compact_todo_summary_item(item: dict[str, Any], *, text: str | None = None) -> dict[str, Any]:
    return compact_todo_summary_items([item], texts=[text])[0]


def todo_summary_source_items(value: dict[str, Any]) -> list[dict[str, Any]]:
    ready_successor_todo_ids = handoff_ready_successor_todo_ids(value)
    open_items: list[dict[str, Any]] = []
    originals: list[dict[str, Any]] = []
    for key in TODO_SUMMARY_SOURCE_KEYS:
        raw_items = value.get(key)
        source_items = raw_items if isinstance(raw_items, list) else []
        for item in source_items:
            if not isinstance(item, dict) or item.get("done") is True:
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            duplicate = any(
                existing.get("todo_id") == item.get("todo_id")
                if item.get("todo_id") and existing.get("todo_id")
                else existing.get("index") == item.get("index")
                and str(existing.get("text") or "").strip() == text
                for existing in open_items
            )
            if duplicate:
                continue
            compact = _compact_todo_summary_item_record(item, text=text)
            todo_id = normalize_todo_id(compact.get("todo_id"))
            if (
                todo_id
                and todo_id in ready_successor_todo_ids
                and normalize_todo_resume_when(compact.get("resume_when"))
                and "resume_ready" not in compact
            ):
                compact["resume_ready"] = True
                compact["resume_condition"] = {
                    "schema_version": "todo_resume_condition_v0",
                    "resume_when": compact.get("resume_when"),
                    "satisfied": True,
                    "source": "handoff_gate_cleared_with_successor",
                }
            open_items.append(compact)
            originals.append(item)
    projected = compact_todo_summary_items(originals, texts=[row["text"] for row in open_items])
    for compact, prepared in zip(projected, open_items, strict=True):
        for key in ("resume_ready", "resume_condition"):
            if key in prepared:
                compact[key] = prepared[key]
    return projected


def todo_planning_source_items(
    value: dict[str, Any],
    *,
    include_terminal: bool = False,
) -> list[dict[str, Any]]:
    """Return canonical Todo state rows shared by planning read models.

    Unlike presentation-lane expansion, these four sources correspond to
    distinct Todo domain states. They preserve waiting/blocking/scheduled nodes
    that may sit outside the status ``items`` budget without making every
    consumer rediscover visibility-lane policy.
    """

    planning_items: list[dict[str, Any]] = []
    originals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in TODO_PLANNING_SOURCE_KEYS:
        raw_items = value.get(key)
        source_items = raw_items if isinstance(raw_items, list) else []
        for item in source_items:
            if not isinstance(item, dict) or (
                not include_terminal and item.get("status") == "done"
            ):
                continue
            todo_id = normalize_todo_id(item.get("todo_id"))
            if include_terminal and todo_id is None:
                legacy_todo_id = str(item.get("todo_id") or "").strip()
                todo_id = (
                    legacy_todo_id
                    if legacy_todo_id.startswith("todo_")
                    else None
                )
            text = str(item.get("text") or "").strip()
            if not todo_id or not text or todo_id in seen:
                continue
            seen.add(todo_id)
            compact = _compact_todo_summary_item_record(item, text=text)
            if include_terminal:
                # Terminal-inclusive planning also proves exact frontier causality.
                for field in (*FRONTIER_REVISION_FIELDS, "evidence", "note", "last_actor_agent_id"):
                    if item.get(field) is not None:
                        compact[field] = item[field]
            planning_items.append(compact)
            originals.append(item)
    projected = compact_todo_summary_items(originals, texts=[row["text"] for row in planning_items])
    if include_terminal:
        for compact, prepared in zip(projected, planning_items, strict=True):
            for field in (*FRONTIER_REVISION_FIELDS, "evidence", "note", "last_actor_agent_id"):
                if field in prepared:
                    compact[field] = prepared[field]
    return projected
