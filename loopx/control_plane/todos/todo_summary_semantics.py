from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from .contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from .todo_item_semantics import (
    TodoMapping,
    TodoObject,
    positive_int,
    todo_item_allows_normalized_agent,
    todo_item_is_actionable_open,
    todo_item_is_due_monitor,
    todo_item_is_watch_only_monitor,
    todo_item_missing_monitor_schedule,
    todo_item_task_class,
    todo_projection_sort_key,
)

_WORK_COUNT_CORE_KEYS = ("open", "advancement", "monitor", "hidden")
_MONITOR_SUMMARY_SOURCE_KEYS = (
    "monitor_due_items",
    "current_agent_claimed_monitor_items",
    "monitor_open_items",
    "claimed_monitor_open_items",
    "first_open_items",
)
_ADVANCEMENT_CONFLICT_KEYS = (
    "current_agent_claimed_advancement_items",
    "unclaimed_priority_open_items",
    "first_executable_items",
    "executable_backlog_items",
)


def todo_summary_claim_scope_agent_id(summary: TodoMapping | None) -> str | None:
    if not isinstance(summary, Mapping):
        return None
    claim_scope = summary.get("claim_scope")
    if not isinstance(claim_scope, Mapping):
        return None
    return normalize_todo_claimed_by(claim_scope.get("agent_id"))


def todo_summary_monitor_writeback_contract(
    summary: TodoMapping | None,
) -> TodoObject | None:
    if not isinstance(summary, Mapping):
        return None
    contract = summary.get("monitor_writeback")
    if not isinstance(contract, Mapping):
        return None
    if contract.get("supported") is not False:
        return None
    compact: TodoObject = {"supported": False}
    source = str(contract.get("source") or "").strip()
    if source:
        compact["source"] = source
    return compact


def todo_summary_monitor_writeback_supported(summary: TodoMapping | None) -> bool:
    contract = todo_summary_monitor_writeback_contract(summary)
    if not contract:
        return True
    return contract.get("supported") is not False


def todo_summary_monitor_items(summary: TodoMapping | None) -> list[TodoObject]:
    if not isinstance(summary, Mapping):
        return []
    items: list[TodoObject] = []
    seen: set[tuple[str, int]] = set()
    for key in _MONITOR_SUMMARY_SOURCE_KEYS:
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            typed_value = cast(TodoObject, value)
            if not todo_item_is_actionable_open(typed_value):
                continue
            if todo_item_task_class(typed_value) != TODO_TASK_CLASS_MONITOR:
                continue
            identity = (
                normalize_todo_id(typed_value.get("todo_id")) or "",
                id(value),
            )
            if identity in seen:
                continue
            seen.add(identity)
            items.append(typed_value)
    return items


def _summary_has_monitor_item(summary: TodoMapping | None) -> bool:
    if not isinstance(summary, Mapping):
        return False
    for key in _MONITOR_SUMMARY_SOURCE_KEYS:
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            typed_value = cast(TodoObject, value)
            if (
                todo_item_is_actionable_open(typed_value)
                and todo_item_task_class(typed_value) == TODO_TASK_CLASS_MONITOR
            ):
                return True
    return False


def _summary_monitor_items(
    summary: TodoMapping | None,
    *,
    projected_key: str,
    predicate: Callable[[TodoObject], bool],
    task_text_keys: tuple[str, ...],
    text_mode: str,
    sort_items: bool = True,
) -> list[TodoObject]:
    if not isinstance(summary, Mapping):
        return []
    if not todo_summary_monitor_writeback_supported(summary):
        return []
    agent_id = todo_summary_claim_scope_agent_id(summary)
    projected_items = summary.get(projected_key)
    items: list[TodoObject] = []
    if isinstance(projected_items, list):
        for raw in projected_items:
            if not isinstance(raw, dict):
                continue
            item = cast(TodoObject, raw)
            if agent_id and not todo_item_allows_normalized_agent(
                item, normalized_agent_id=agent_id
            ):
                continue
            if (
                todo_item_is_actionable_open(item)
                and todo_item_task_class(item, task_text_keys=task_text_keys)
                == TODO_TASK_CLASS_MONITOR
                and predicate(item)
            ):
                items.append(item)
    else:
        raw_items = summary.get("monitor_open_items")
        if isinstance(raw_items, list):
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                item = cast(TodoObject, raw)
                if agent_id and not todo_item_allows_normalized_agent(
                    item, normalized_agent_id=agent_id
                ):
                    continue
                if predicate(item):
                    items.append(item)
    if not sort_items:
        return items
    return sorted(
        items,
        key=lambda item: todo_projection_sort_key(item, text_mode=text_mode),
    )


def todo_summary_monitor_due_items(
    summary: TodoMapping | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[TodoObject]:
    return _summary_monitor_items(
        summary,
        projected_key="monitor_due_items",
        predicate=lambda item: todo_item_is_due_monitor(
            item,
            task_text_keys=task_text_keys,
        ),
        task_text_keys=task_text_keys,
        text_mode=text_mode,
    )


def _summary_partitioned_due_monitor_items(
    summary: TodoMapping | None,
    *,
    projected_key: str,
    expect_watch_only: bool,
    task_text_keys: tuple[str, ...],
    text_mode: str,
) -> list[TodoObject]:
    if isinstance(summary, Mapping) and isinstance(summary.get(projected_key), list):
        return _summary_monitor_items(
            summary,
            projected_key=projected_key,
            predicate=lambda _item: True,
            task_text_keys=task_text_keys,
            text_mode=text_mode,
        )
    return [
        item
        for item in todo_summary_monitor_due_items(
            summary,
            task_text_keys=task_text_keys,
            text_mode=text_mode,
        )
        if todo_item_is_watch_only_monitor(item) is expect_watch_only
    ]


def todo_summary_watch_only_monitor_due_items(
    summary: TodoMapping | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[TodoObject]:
    """Consume the typed watch-only partition; legacy summaries fall back safely."""
    return _summary_partitioned_due_monitor_items(
        summary,
        projected_key="watch_only_monitor_due_items",
        expect_watch_only=True,
        task_text_keys=task_text_keys,
        text_mode=text_mode,
    )


def todo_summary_non_watch_only_monitor_due_items(
    summary: TodoMapping | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[TodoObject]:
    """Consume the typed ordinary-due partition; classify only legacy summaries."""
    return _summary_partitioned_due_monitor_items(
        summary,
        projected_key="non_watch_only_monitor_due_items",
        expect_watch_only=False,
        task_text_keys=task_text_keys,
        text_mode=text_mode,
    )


def _count_scoped_raw_monitor_items(
    raw_items: list[object],
    *,
    agent_id: str,
    predicate: Callable[[TodoObject], bool],
) -> int:
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = cast(TodoObject, raw)
        if predicate(item) and todo_item_allows_normalized_agent(
            item, normalized_agent_id=agent_id
        ):
            total += 1
    return total


def todo_summary_monitor_due_count(
    summary: TodoMapping | None,
    *,
    due_items: list[TodoObject] | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> int:
    if not isinstance(summary, Mapping):
        return 0
    if not todo_summary_monitor_writeback_supported(summary):
        return 0
    projected_count = summary.get("monitor_due_count")
    if isinstance(projected_count, int):
        return max(0, projected_count)
    agent_id = todo_summary_claim_scope_agent_id(summary)
    if agent_id and isinstance(raw_items := summary.get("monitor_open_items"), list):
        return _count_scoped_raw_monitor_items(
            raw_items,
            agent_id=agent_id,
            predicate=lambda item: todo_item_is_due_monitor(
                item, task_text_keys=task_text_keys
            ),
        )
    if due_items is not None:
        return len(due_items)
    return len(
        _summary_monitor_items(
            summary,
            projected_key="monitor_due_items",
            predicate=lambda item: todo_item_is_due_monitor(
                item, task_text_keys=task_text_keys
            ),
            task_text_keys=task_text_keys,
            text_mode=text_mode,
            sort_items=False,
        )
    )


def todo_summary_monitor_schedule_gap_items(
    summary: TodoMapping | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[TodoObject]:
    return _summary_monitor_items(
        summary,
        projected_key="monitor_schedule_gap_items",
        predicate=lambda item: todo_item_missing_monitor_schedule(
            item,
            task_text_keys=task_text_keys,
        ),
        task_text_keys=task_text_keys,
        text_mode=text_mode,
    )


def todo_summary_monitor_schedule_gap_count(
    summary: TodoMapping | None,
    *,
    gap_items: list[TodoObject] | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> int:
    if not isinstance(summary, Mapping):
        return 0
    if not todo_summary_monitor_writeback_supported(summary):
        return 0
    agent_id = todo_summary_claim_scope_agent_id(summary)
    if agent_id:
        raw_items = summary.get("monitor_open_items")
        if isinstance(raw_items, list):
            return _count_scoped_raw_monitor_items(
                raw_items,
                agent_id=agent_id,
                predicate=lambda item: todo_item_missing_monitor_schedule(
                    item, task_text_keys=task_text_keys
                ),
            )
    else:
        projected_count = summary.get("monitor_schedule_gap_count")
        if isinstance(projected_count, int):
            return max(0, projected_count)
    if gap_items is not None:
        return len(gap_items)
    return len(
        _summary_monitor_items(
            summary,
            projected_key="monitor_schedule_gap_items",
            predicate=lambda item: todo_item_missing_monitor_schedule(
                item, task_text_keys=task_text_keys
            ),
            task_text_keys=task_text_keys,
            text_mode=text_mode,
            sort_items=False,
        )
    )


def todo_summary_open_count(summary: TodoMapping | None) -> int:
    if not isinstance(summary, Mapping):
        return 0
    return positive_int(summary.get("open_count") or 0)


def todo_summary_open_task_counts(summary: TodoMapping | None) -> dict[str, int]:
    """Consume pre-limit counts; old display-only summaries provide lower bounds."""
    from ..effect_runtime import effect_runtime_result

    resolved_summary: TodoMapping = summary if isinstance(summary, Mapping) else {}
    scoped_agent_id = todo_summary_claim_scope_agent_id(resolved_summary)
    counts = resolved_summary.get("work_counts")
    if counts is None:
        rows: list[TodoObject] = []
        for key in (
            "items",
            "executable_backlog_items",
            "first_executable_items",
            "first_open_items",
            "monitor_open_items",
        ):
            for raw in cast(list[object], resolved_summary.get(key) or []):
                if not isinstance(raw, dict) or raw.get("done") is True:
                    continue
                item = cast(TodoObject, raw)
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                rows.append(
                    {
                        "identity": str(
                            item.get("todo_id") or (str(item.get("index")) + ":" + text)
                        ),
                        "actionable": todo_item_is_actionable_open(item),
                        "task_class": todo_item_task_class(item),
                    }
                )
        counts = effect_runtime_result(
            "todo.work_counts.project",
            {
                "schema_version": "todo_work_counts_request_v0",
                "rows": rows,
                "source_open_count": resolved_summary.get("open_count"),
                "agent_id": scoped_agent_id,
            },
        )
    if (
        not isinstance(counts, Mapping)
        or counts.get("schema_version") != "todo_work_counts_v0"
        or not isinstance(counts.get("complete"), bool)
        or any(
            type(counts.get(key)) is not int or cast(int, counts[key]) < 0
            for key in _WORK_COUNT_CORE_KEYS
        )
        or counts.get("agent_id") != scoped_agent_id
    ):
        raise ValueError("invalid or differently scoped Todo work counts")
    open_count = cast(int, counts["open"])
    advancement_count = cast(int, counts["advancement"])
    monitor_count = cast(int, counts["monitor"])
    hidden_count = cast(int, counts["hidden"])
    complete = cast(bool, counts["complete"])
    if (
        hidden_count > open_count
        or advancement_count + monitor_count > open_count - hidden_count
        or (complete and hidden_count != 0)
    ):
        raise ValueError("inconsistent Todo work count envelope")
    return {
        "open": open_count,
        "advancement": advancement_count,
        "monitor": monitor_count,
        "hidden": hidden_count,
        "complete": complete,
        "monitor_due": todo_summary_monitor_due_count(resolved_summary),
        "monitor_schedule_gap": todo_summary_monitor_schedule_gap_count(resolved_summary),
    }


def todo_summary_has_only_future_scoped_monitor_work(
    summary: TodoMapping | None,
) -> bool:
    """Return true when the scoped agent has only non-due monitor work left."""

    agent_id = todo_summary_claim_scope_agent_id(summary)
    if not agent_id or not isinstance(summary, Mapping):
        return False
    if not _summary_has_monitor_item(summary):
        return False
    counts = todo_summary_open_task_counts(summary)
    if counts["complete"] is not True or counts["advancement"] > 0:
        return False
    if counts["monitor_due"] > 0 or counts["monitor_schedule_gap"] > 0:
        return False
    if positive_int(summary.get("current_agent_claimed_advancement_count")) > 0:
        return False
    for key in _ADVANCEMENT_CONFLICT_KEYS:
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for raw in values:
            if not isinstance(raw, dict):
                continue
            item = cast(TodoObject, raw)
            if (
                todo_item_is_actionable_open(item)
                and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
                and todo_item_allows_normalized_agent(
                    item, normalized_agent_id=agent_id
                )
            ):
                return False
    return True


def todo_summary_first_executable_item(
    summary: TodoMapping | None,
) -> TodoObject | None:
    if not isinstance(summary, Mapping):
        return None
    raw_items = summary.get("first_executable_items")
    if not isinstance(raw_items, list):
        return None
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = cast(TodoObject, raw)
        if (
            todo_item_is_actionable_open(item)
            and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        ):
            return item
    return None
