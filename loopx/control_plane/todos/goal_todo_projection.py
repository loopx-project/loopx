"""Project todo summaries and items from one active-state text.

The text is a parameter rather than a file read so a writer that still holds
the state-file lock can project the exact bytes it is about to commit;
``loopx.todos.list_goal_todos`` passes the on-disk text. Everything here is a
projection of those bytes and rollout metadata. Retired event sources are
rejected before any Markdown substitution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..goals.legacy_event_source import require_no_legacy_todo_events
from .active_state_editing import TODO_SECTION_HEADINGS
from .active_state_todo_parser import parse_active_state_todos
from .list_projection import compact_explicit_limit_todo_summary
from .succession_warning import public_todo_summary
from .contract import (
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


class GoalTodoSummaries:
    """Role summaries and todo items projected from one active-state text."""

    __slots__ = (
        "source",
        "summaries",
        "todos",
        "unfiltered_count",
        "uncapped_todo_count",
    )

    def __init__(
        self,
        *,
        source: str,
        summaries: dict[str, dict[str, Any]],
        todos: list[dict[str, Any]],
        unfiltered_count: int,
        uncapped_todo_count: int,
    ) -> None:
        self.source = source
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

    require_no_legacy_todo_events(goal or {}, state_path=state_path)
    markdown_fields = parse_active_state_todos(
        state_text,
        goal=goal,
        state_path=state_path,
        item_limit=None,
        rollout_events=rollout_events,
    )
    return todo_summaries_from_fields(
        fields=markdown_fields,
        source="markdown_active_state",
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
        summaries=summaries,
        todos=todos,
        unfiltered_count=unfiltered_count,
        uncapped_todo_count=uncapped_todo_count,
    )


def exact_archived_todo_summaries(
    *,
    archived_items: list[dict[str, Any]],
    source: str,
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
    "project_goal_todo_items",
    "summary_items",
    "todo_summaries_from_fields",
]
