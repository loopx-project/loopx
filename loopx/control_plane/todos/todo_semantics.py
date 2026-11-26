from __future__ import annotations

from collections.abc import Mapping
import re
from typing import cast

from .contract import (
    TODO_STATUS_BLOCKED as TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED as TODO_STATUS_DEFERRED,
    TODO_TASK_CLASS_ADVANCEMENT as TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_BLOCKER as TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_MONITOR as TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_id,
)
from .todo_item_semantics import (
    TODO_LEGACY_ITEM_SCHEMA as TODO_LEGACY_ITEM_SCHEMA,
    TODO_MISSING_INDEX as TODO_MISSING_INDEX,
    TODO_MISSING_PRIORITY_RANK as TODO_MISSING_PRIORITY_RANK,
    TODO_NATIVE_ITEM_SCHEMA as TODO_NATIVE_ITEM_SCHEMA,
    TODO_PRESENTATION_METADATA_SCHEMA as TODO_PRESENTATION_METADATA_SCHEMA,
    TODO_PRIORITY_LABEL_PATTERN as TODO_PRIORITY_LABEL_PATTERN,
    TODO_PRIORITY_PREFIX_PATTERN as TODO_PRIORITY_PREFIX_PATTERN,
    TodoMapping,
    TodoObject,
    positive_int as _positive_int,
    todo_blocker_reason as todo_blocker_reason,
    todo_index_rank as todo_index_rank,
    todo_item_claimed_by_agent_or_unclaimed as todo_item_claimed_by_agent_or_unclaimed,
    todo_item_excludes_agent as todo_item_excludes_agent,
    todo_item_expires_at as todo_item_expires_at,
    todo_item_has_removed_continuation_policy as todo_item_has_removed_continuation_policy,
    todo_item_is_actionable_open as todo_item_is_actionable_open,
    todo_item_is_deferred as todo_item_is_deferred,
    todo_item_is_due_monitor as todo_item_is_due_monitor,
    todo_item_is_expired_monitor as todo_item_is_expired_monitor,
    todo_item_is_watch_only_monitor as todo_item_is_watch_only_monitor,
    todo_item_missing_monitor_schedule as todo_item_missing_monitor_schedule,
    todo_item_next_due_at as todo_item_next_due_at,
    todo_item_task_class as todo_item_task_class,
    todo_item_task_text as todo_item_task_text,
    todo_presentation_metadata as todo_presentation_metadata,
    todo_presentation_sort_key as todo_presentation_sort_key,
    todo_priority_label as todo_priority_label,
    todo_priority_parts as todo_priority_parts,
    todo_priority_rank as todo_priority_rank,
    todo_projection_sort_key as todo_projection_sort_key,
)
from .todo_summary_semantics import (
    _summary_monitor_items as _summary_monitor_items,
    todo_summary_claim_scope_agent_id as todo_summary_claim_scope_agent_id,
    todo_summary_first_executable_item as todo_summary_first_executable_item,
    todo_summary_has_only_future_scoped_monitor_work as todo_summary_has_only_future_scoped_monitor_work,
    todo_summary_monitor_due_count as todo_summary_monitor_due_count,
    todo_summary_monitor_due_items as todo_summary_monitor_due_items,
    todo_summary_monitor_items as todo_summary_monitor_items,
    todo_summary_monitor_schedule_gap_count as todo_summary_monitor_schedule_gap_count,
    todo_summary_monitor_schedule_gap_items as todo_summary_monitor_schedule_gap_items,
    todo_summary_monitor_writeback_contract as todo_summary_monitor_writeback_contract,
    todo_summary_monitor_writeback_supported as todo_summary_monitor_writeback_supported,
    todo_summary_non_watch_only_monitor_due_items as todo_summary_non_watch_only_monitor_due_items,
    todo_summary_open_count as todo_summary_open_count,
    todo_summary_open_task_counts as todo_summary_open_task_counts,
    todo_summary_watch_only_monitor_due_items as todo_summary_watch_only_monitor_due_items,
)

_FRONTIER_GROUP_KEYS = (
    "current_agent_claimed_items",
    "unclaimed_items",
    "other_agent_claimed_items",
)
_FRONTIER_COUNT_KEYS = {
    "current_agent_claimed_advancement_count",
    "unclaimed_advancement_count",
    "other_agent_claimed_advancement_count",
}


def _dict_items(raw: object) -> list[TodoObject]:
    if not isinstance(raw, list):
        return []
    return [cast(TodoObject, item) for item in raw if isinstance(item, dict)]


def _encode_frontier_items(items: list[TodoObject] | None) -> list[TodoObject] | None:
    if items is None:
        return None
    return [
        {
            "claim": normalize_todo_claimed_by(item.get("claimed_by")),
            "excluded": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "advancement": todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT,
            "actionable": todo_item_is_actionable_open(item),
        }
        for item in items
    ]


def _advancement_frontier_projection(
    summary: TodoMapping,
    *,
    agent_id: str | None,
) -> tuple[dict[str, list[TodoObject]], dict[str, int]]:
    """Legacy fact codec; TS owns precedence, claim lanes and count floors."""
    from ..effect_runtime import effect_runtime_result

    keys = (
        "executable_backlog_items",
        "unclaimed_priority_open_items",
        "claimed_advancement_open_items",
    )
    sources: dict[str, list[TodoObject] | None] = {
        key: _dict_items(raw) if isinstance(raw := summary.get(key), list) else None
        for key in keys
    }
    claim_scope = summary.get("claim_scope")
    diagnostic = (
        claim_scope.get("other_agent_claimed_items")
        if isinstance(claim_scope, Mapping)
        else None
    )
    result = effect_runtime_result(
        "todo.frontier_revision.project",
        {
            "schema_version": "todo_frontier_revision_request_v0",
            "operation": "classify",
            "agent_id": normalize_todo_claimed_by(agent_id),
            "sources": {
                key: _encode_frontier_items(items) for key, items in sources.items()
            },
            "diagnostic_peers": _encode_frontier_items(_dict_items(diagnostic))
            if isinstance(diagnostic, list)
            else None,
            "claimed_count_floor": str(
                _positive_int(summary.get("current_agent_claimed_advancement_count"))
            ),
        },
    )
    if (
        not isinstance(result, Mapping)
        or not isinstance(result.get("groups"), Mapping)
        or not isinstance(result.get("counts"), Mapping)
    ):
        raise TypeError("invalid typed advancement frontier projection")
    raw_groups = cast(Mapping[str, object], result["groups"])
    groups: dict[str, list[TodoObject]] = {}
    for key in _FRONTIER_GROUP_KEYS:
        group = raw_groups.get(key)
        if not isinstance(group, Mapping):
            raise TypeError("invalid typed advancement frontier source")
        source_name = group.get("source")
        indices = group.get("indices")
        if (
            not isinstance(source_name, str)
            or source_name not in sources
            or not isinstance(indices, list)
        ):
            raise TypeError("invalid typed advancement frontier source")
        source = sources[source_name] or []
        if any(
            type(index) is not int or index < 0 or index >= len(source)
            for index in indices
        ):
            raise TypeError("invalid typed advancement frontier index")
        groups[key] = [source[index] for index in indices]
    raw_counts = dict(cast(Mapping[str, object], result["counts"]))
    current_key = "current_agent_claimed_advancement_count"
    encoded_count = raw_counts.get(current_key)
    if isinstance(encoded_count, str) and re.fullmatch(r"0|[1-9][0-9]*", encoded_count):
        raw_counts[current_key] = int(encoded_count)
    if set(raw_counts) != _FRONTIER_COUNT_KEYS or any(
        type(count) is not int or count < 0 for count in raw_counts.values()
    ):
        raise TypeError("invalid typed advancement frontier counts")
    return groups, {key: cast(int, value) for key, value in raw_counts.items()}


def todo_advancement_frontier_items(
    summary: TodoMapping | None,
    *,
    agent_id: str | None,
) -> dict[str, list[TodoObject]]:
    """Read the typed frontier, retaining the caller's original display rows."""
    if not isinstance(summary, Mapping):
        return {key: [] for key in _FRONTIER_GROUP_KEYS}
    return _advancement_frontier_projection(summary, agent_id=agent_id)[0]


def agent_scoped_selectable_advancement_todo_ids(
    agent_todo_summary: TodoMapping | None,
    *,
    agent_id: str | None,
) -> set[str]:
    frontier = todo_advancement_frontier_items(agent_todo_summary, agent_id=agent_id)
    return {
        todo_id
        for item in (
            frontier["current_agent_claimed_items"] + frontier["unclaimed_items"]
        )
        if (todo_id := normalize_todo_id(item.get("todo_id")))
    }


def todo_scoped_runnable_advancement_count(
    summary: TodoMapping | None,
    *,
    agent_id: str | None,
) -> int | None:
    """Decode the typed selection count; unknown sources keep legacy fences.

    Commitment floors intentionally include work held by receipt-owned waits.
    They do not override a complete same-lane runnable-work projection.
    """
    counts = summary.get("work_counts") if isinstance(summary, Mapping) else None
    if (
        isinstance(counts, Mapping)
        and counts.get("schema_version") == "todo_work_counts_v0"
        and counts.get("complete") is True
        and counts.get("agent_id") == agent_id
        and type(counts.get("advancement")) is int
        and cast(int, counts["advancement"]) >= 0
    ):
        return cast(int, counts["advancement"])
    return None


def todo_advancement_frontier_counts(
    summary: TodoMapping | None,
    *,
    agent_id: str | None,
) -> dict[str, int]:
    """Read commitment counts; diagnostic floors do not grant execution."""
    if not isinstance(summary, Mapping):
        return {key: 0 for key in _FRONTIER_COUNT_KEYS}
    return _advancement_frontier_projection(summary, agent_id=agent_id)[1]
