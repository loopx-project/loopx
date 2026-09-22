"""Project todo summaries and items from one active-state text.

The text is a parameter rather than a file read so a writer that still holds
the state-file lock can project the exact bytes it is about to commit;
``loopx.todos.list_goal_todos`` passes the on-disk text. Everything here is a
deterministic function of the text, the goal record, and the event projection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..status.active_state_projection import active_state_event_projection_fields
from .active_state_editing import TODO_SECTION_HEADINGS
from .active_state_todo_parser import parse_active_state_todos
from .list_projection import compact_explicit_limit_todo_summary
from .succession_warning import public_todo_summary
from .contract import (
    build_todo_id,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_status,
)
from .todo_summary import compact_evaluated_todo_group, compact_todo_group


def empty_todo_summary(*, role: str) -> dict[str, Any]:
    return {
        "schema_version": "todo_summary_v0",
        "role": role,
        "source_section": TODO_SECTION_HEADINGS[role],
        "total_count": 0,
        "open_count": 0,
        "done_count": 0,
        "items": [],
        "first_open_items": [],
    }

def filtered_todo_summary(
    summary: dict[str, Any] | None,
    *,
    role: str,
    status: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    item_limit: int | None = None,
) -> dict[str, Any]:
    items = list((summary or {}).get("items") or [])
    selection = {"role": role, "status": normalize_todo_status(status),
        "todo_id": normalize_todo_id(todo_id) if todo_id else None,
        "agent_id": normalize_todo_claimed_by(agent_id) if agent_id else None}
    source_section = str((summary or {}).get("source_section") or TODO_SECTION_HEADINGS[role])
    return (
        compact_evaluated_todo_group(
            items,
            source_section=source_section,
            role=role,
            item_limit=item_limit,
            selection=selection,
        )
        or empty_todo_summary(role=role)
    )

def summary_items(fields: dict[str, Any], role: str) -> list[dict[str, Any]]:
    summary = fields.get(f"{role}_todos") if isinstance(fields, dict) else None
    if not isinstance(summary, dict):
        return []
    return [item for item in summary.get("items") or [] if isinstance(item, dict)]

def merge_todo_projection_fields(
    *,
    markdown_fields: dict[str, Any],
    event_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged: dict[str, Any] = {}
    merged_items: dict[str, list[dict[str, Any]]] = {"user": [], "agent": []}
    source_sections: dict[str, str] = {}
    overlay: dict[str, Any] = {
        "schema_version": "todo_list_projection_overlay_v0",
        "base": "markdown_active_state",
        "overlay": "event_projection",
        "markdown_only_todo_ids": [],
        "event_only_todo_ids": [],
        "overlaid_todo_ids": [],
    }

    # A todo_id is goal-wide identity. Merge both sources before splitting by
    # role so an event-projected role change replaces the stale Markdown item.
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    markdown_ids: set[str] = set()
    markdown_ids_by_role: dict[str, set[str]] = {"user": set(), "agent": set()}
    event_ids: set[str] = set()
    event_order: list[str] = []
    for role in ("user", "agent"):
        markdown_items = summary_items(markdown_fields, role)
        for item in markdown_items:
            todo_id = normalize_todo_id(item.get("todo_id")) or build_todo_id(
                role=role,
                source_section=item.get("source_section"),
                index=item.get("index"),
                text=item.get("text"),
            )
            if todo_id not in by_id:
                order.append(todo_id)
            markdown_ids.add(todo_id)
            markdown_ids_by_role[role].add(todo_id)
            by_id[todo_id] = dict(item)

    for role in ("user", "agent"):
        event_items = summary_items(event_fields, role)
        for item in event_items:
            todo_id = normalize_todo_id(item.get("todo_id")) or build_todo_id(
                role=role,
                source_section=item.get("source_section"),
                index=item.get("index"),
                text=item.get("text"),
            )
            if todo_id not in by_id:
                order.append(todo_id)
            if todo_id not in event_ids:
                event_order.append(todo_id)
                event_ids.add(todo_id)
            by_id[todo_id] = dict(item)

    markdown_only_todo_ids: list[str] = []
    seen_markdown_only_ids: set[str] = set()
    for role in ("user", "agent"):
        for todo_id in sorted(markdown_ids_by_role[role] - event_ids):
            if todo_id not in seen_markdown_only_ids:
                markdown_only_todo_ids.append(todo_id)
                seen_markdown_only_ids.add(todo_id)
    overlay["markdown_only_todo_ids"] = markdown_only_todo_ids
    overlay["event_only_todo_ids"] = [
        todo_id for todo_id in event_order if todo_id not in markdown_ids
    ]
    overlay["overlaid_todo_ids"] = [
        todo_id for todo_id in event_order if todo_id in markdown_ids
    ]

    for todo_id in order:
        item = by_id[todo_id]
        final_role = "user" if item.get("role") == "user" else "agent"
        merged_items[final_role].append(item)

    for role in ("user", "agent"):
        source_section = str(
            (markdown_fields.get(f"{role}_todos") or {}).get("source_section")
            or (event_fields.get(f"{role}_todos") or {}).get("source_section")
            or TODO_SECTION_HEADINGS[role]
        )
        source_sections[role] = source_section

    resume_source_items = [*merged_items["user"], *merged_items["agent"]]
    for role in ("user", "agent"):
        if not merged_items[role]:
            continue
        summary = compact_todo_group(
            merged_items[role],
            source_section=source_sections[role],
            role=role,
            resume_source_items=resume_source_items,
            item_limit=None,
        )
        if summary:
            merged[f"{role}_todos"] = summary
    return merged, overlay

class GoalTodoSummaries:
    """Role summaries and todo items projected from one active-state text."""

    __slots__ = (
        "source",
        "projection_fields",
        "projection_overlay",
        "summaries",
        "todos",
        "unfiltered_count",
        "uncapped_todo_count",
    )

    def __init__(
        self,
        *,
        source: str,
        projection_fields: dict[str, Any],
        projection_overlay: dict[str, Any] | None,
        summaries: dict[str, dict[str, Any]],
        todos: list[dict[str, Any]],
        unfiltered_count: int,
        uncapped_todo_count: int,
    ) -> None:
        self.source = source
        self.projection_fields = projection_fields
        self.projection_overlay = projection_overlay
        self.summaries = summaries
        self.todos = todos
        self.unfiltered_count = unfiltered_count
        self.uncapped_todo_count = uncapped_todo_count

def goal_todo_summaries(
    goal: dict[str, Any] | None,
    *,
    state_text: str,
    state_path: Path,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str | None,
    agent_id: str | None,
    limit: int | None,
) -> GoalTodoSummaries:
    """Project todo summaries from active-state text plus its event projection.

    The text is a parameter rather than a file read so a writer that still
    holds the state-file lock can project the exact bytes it is about to
    commit; ``list_goal_todos`` passes the on-disk text.
    """

    projection_fields = active_state_event_projection_fields(
        goal or {},
        state_path=state_path,
        item_limit=None,
        rollout_events=rollout_events,
    )
    projection_has_todos = bool(
        projection_fields.get("user_todos") or projection_fields.get("agent_todos")
    )
    markdown_fields = parse_active_state_todos(
        state_text,
        goal=goal,
        state_path=state_path,
        item_limit=None,
        rollout_events=rollout_events,
    )
    markdown_has_todos = bool(
        markdown_fields.get("user_todos") or markdown_fields.get("agent_todos")
    )
    projection_overlay: dict[str, Any] | None = None
    if projection_has_todos and markdown_has_todos:
        fields, projection_overlay = merge_todo_projection_fields(
            markdown_fields=markdown_fields,
            event_fields=projection_fields,
        )
        source = "event_projection_with_markdown_overlay"
    elif projection_has_todos:
        fields = projection_fields
        source = "event_projection"
    else:
        fields = markdown_fields
        source = "markdown_active_state"

    return todo_summaries_from_fields(
        fields=fields,
        source=source,
        projection_fields=projection_fields,
        projection_overlay=projection_overlay,
        rollout_events=rollout_events,
        roles=roles,
        status=status,
        todo_id=todo_id,
        agent_id=agent_id,
        limit=limit,
    )


def todo_summaries_from_fields(
    *,
    fields: dict[str, Any],
    source: str,
    projection_fields: dict[str, Any] | None,
    projection_overlay: dict[str, Any] | None,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str | None,
    agent_id: str | None,
    limit: int | None,
) -> GoalTodoSummaries:
    """Apply the shared Todo consumer semantics to an authority read model."""

    summaries: dict[str, dict[str, Any]] = {}
    todos: list[dict[str, Any]] = []
    unfiltered_count = 0
    uncapped_todo_count = 0
    for item_role in roles:
        key = f"{item_role}_todos"
        raw_summary = fields.get(key) if isinstance(fields, dict) else None
        unfiltered_count += len((raw_summary or {}).get("items") or [])
        summary = filtered_todo_summary(
            raw_summary,
            role=item_role,
            status=status,
            todo_id=todo_id,
            agent_id=agent_id,
            item_limit=limit,
        )
        if limit is not None:
            summary = compact_explicit_limit_todo_summary(
                summary,
                role=item_role,
                item_limit=limit,
            )
        summary = public_todo_summary(summary)
        summaries[key] = summary
        todos.extend(summary.get("items") or [])
        uncapped_todo_count += int(summary.get("total_count") or 0)
    return GoalTodoSummaries(
        source=source,
        projection_fields=projection_fields or {},
        projection_overlay=projection_overlay,
        summaries=summaries,
        todos=todos,
        unfiltered_count=unfiltered_count,
        uncapped_todo_count=uncapped_todo_count,
    )


def exact_archived_todo_summaries(
    *,
    archived_items: list[dict[str, Any]],
    source: str,
    projection_fields: dict[str, Any] | None,
    projection_overlay: dict[str, Any] | None,
    rollout_events: list[dict[str, Any]],
    roles: list[str],
    status: str | None,
    todo_id: str,
    agent_id: str | None,
    limit: int | None,
) -> GoalTodoSummaries | None:
    """Project one exact retained Todo without widening normal active lists."""

    item = next(
        (
            dict(candidate)
            for candidate in archived_items
            if normalize_todo_id(candidate.get("todo_id")) == todo_id
            and candidate.get("archive_state") == "archive"
        ),
        None,
    )
    if item is None:
        return None
    item_role = item.get("role")
    if item_role not in {"user", "agent"} or item_role not in roles:
        return None
    summary = compact_todo_group(
        [item],
        source_section=str(item.get("source_section") or "Completed Work Archive"),
        role=item_role,
        include_empty_source=True,
        resume_source_items=archived_items,
        rollout_events=rollout_events,
        item_limit=None,
    )
    if summary is None:
        return None
    return todo_summaries_from_fields(
        fields={f"{item_role}_todos": summary},
        source=source,
        projection_fields=projection_fields,
        projection_overlay=projection_overlay,
        rollout_events=rollout_events,
        roles=roles,
        status=status,
        todo_id=todo_id,
        agent_id=agent_id,
        limit=limit,
    )

def project_goal_todo_items(
    goal: dict[str, Any] | None,
    *,
    state_text: str,
    state_path: Path,
    rollout_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every user and agent todo item projected from one active-state text.

    Same items ``list_goal_todos`` returns without filters, computed from the
    caller's text instead of the file so it can run inside the writer's lock.
    """

    return goal_todo_summaries(
        goal,
        state_text=state_text,
        state_path=state_path,
        rollout_events=rollout_events,
        roles=["user", "agent"],
        status=None,
        todo_id=None,
        agent_id=None,
        limit=None,
    ).todos


__all__ = [
    "GoalTodoSummaries",
    "empty_todo_summary",
    "exact_archived_todo_summaries",
    "filtered_todo_summary",
    "goal_todo_summaries",
    "merge_todo_projection_fields",
    "project_goal_todo_items",
    "summary_items",
    "todo_summaries_from_fields",
]
