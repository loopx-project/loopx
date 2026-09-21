from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ..scheduler.monitor_todo import (
    monitor_todo_expires_at,
    monitor_todo_has_schedule,
    monitor_todo_is_actionable_open,
    monitor_todo_is_due,
    monitor_todo_is_expired,
    monitor_todo_missing_schedule,
    monitor_todo_next_due_at,
    monitor_todo_task_class,
)
from ..coordination.coordination_state_contract_generated import (
    COORDINATION_STATE_CONTRACT,
)
from .contract import (
    TODO_STATUS_DEFERRED,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_removed_todo_continuation_policy,
    normalize_todo_id,
    normalize_todo_status,
    normalize_todo_watch_only,
)


_PRIORITY_CONTRACT = COORDINATION_STATE_CONTRACT["todo_priority"]
TODO_MISSING_PRIORITY_RANK = int(_PRIORITY_CONTRACT["missing_rank"])
TODO_MISSING_INDEX = 999999
TODO_PRIORITY_PREFIX_PATTERN = re.compile(_PRIORITY_CONTRACT["legacy_prefix_pattern"], re.IGNORECASE)
TODO_PRIORITY_LABEL_PATTERN = re.compile(_PRIORITY_CONTRACT["legacy_label_pattern"], re.IGNORECASE)
TODO_PRESENTATION_METADATA_SCHEMA = "loopx_todo_presentation_metadata_v0"
TODO_LEGACY_ITEM_SCHEMA = str(
    COORDINATION_STATE_CONTRACT["todo_read_record"]["item_schema_version"]
)
TODO_NATIVE_ITEM_SCHEMA = str(
    COORDINATION_STATE_CONTRACT["todo_domain_record"]["item_schema_version"]
)


def todo_item_is_watch_only_monitor(item: dict[str, Any]) -> bool:
    return bool(
        todo_item_task_class(item) == TODO_TASK_CLASS_MONITOR
        and normalize_todo_watch_only(item.get("watch_only")) is True
    )


def todo_priority_parts(text: str) -> tuple[str | None, str]:
    match = TODO_PRIORITY_PREFIX_PATTERN.match(text)
    if not match:
        return None, text
    return match.group(1).strip().upper(), match.group(2).strip()


def todo_priority_label(
    item: dict[str, Any],
    *,
    text_mode: str = "label",
) -> str | None:
    # Read compatibility codec only. Vocabulary/grammar/rank are generated
    # from the shared contract; mutations are planned by todos/priority.ts.
    # text_mode remains an import/API compatibility argument, not another rule.
    if "priority" in item:
        value = item["priority"]
    else:
        value, _ = todo_priority_parts(str(item.get("text") or item.get("title") or ""))
    match = TODO_PRIORITY_LABEL_PATTERN.match(value.strip()) if isinstance(value, str) else None
    return match.group(1).upper() if match else None


def todo_priority_rank(value: Any, *, text_mode: str = "label") -> int:
    priority = todo_priority_label(value if isinstance(value, dict) else {"priority": value}, text_mode=text_mode)
    return int(_PRIORITY_CONTRACT["values"].index(priority)) if priority else TODO_MISSING_PRIORITY_RANK


def todo_index_rank(item: dict[str, Any]) -> int:
    raw_index = item.get("index")
    try:
        return int(raw_index) if raw_index is not None else TODO_MISSING_INDEX
    except (TypeError, ValueError):
        return TODO_MISSING_INDEX


def todo_presentation_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Project one Todo's display address without making it domain state.

    The v0 ``source_section``/``index`` pair is the wire shape's canonical
    presentation coordinate. Native domain records derive a section from role
    and archival state and intentionally receive no synthetic persisted index.
    """

    if not isinstance(item, dict):
        raise ValueError("Todo presentation input must be an object")
    schema = item.get("schema_version")
    has_section = isinstance(item.get("source_section"), str) and bool(
        item["source_section"].strip()
    )
    if schema not in {None, TODO_LEGACY_ITEM_SCHEMA, TODO_NATIVE_ITEM_SCHEMA}:
        raise ValueError(f"unsupported Todo presentation schema: {schema!r}")
    legacy = schema == TODO_LEGACY_ITEM_SCHEMA or (schema is None and has_section)
    if legacy:
        section = item.get("source_section") if has_section else None
        raw_index = item.get("index")
        display_order = (
            int(raw_index)
            if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0
            else None
        )
        order_source = "source_index" if display_order is not None else "todo_id"
    else:
        section = None
        display_order = None
        order_source = "todo_id"
    if not section:
        section = (
            "Completed Work Archive"
            if item.get("archive_state") == "archive"
            else "Agent Todo"
            if item.get("role") == "agent"
            else "User Todo"
        )
    return {
        "schema_version": TODO_PRESENTATION_METADATA_SCHEMA,
        "todo_id": str(item.get("todo_id") or ""),
        "display_section": section,
        "display_order": display_order,
        "order_source": order_source,
    }


def todo_presentation_sort_key(
    item: dict[str, Any],
    *,
    text_mode: str = "label",
) -> tuple[int, int, str, str]:
    """Sort display rows while preserving source coordinates and native determinism.

    Legacy rows with an equal priority/index retain Python's stable input order.
    Native rows have no fabricated index, so timestamp and Todo identity provide
    a deterministic tie-break that matches the TypeScript presentation adapter.
    """

    priority = todo_priority_rank(item, text_mode=text_mode)
    try:
        metadata = todo_presentation_metadata(item)
    except ValueError:
        # Compact display envelopes can carry their own outer schema marker.
        # They are not authority records, so ordering may use the untyped
        # compatibility fields without weakening the strict boundary helper.
        metadata = {
            "display_order": (
                int(item["index"])
                if isinstance(item.get("index"), int)
                and not isinstance(item.get("index"), bool)
                and item["index"] >= 0
                else None
            ),
        }
    display_order = metadata["display_order"]
    if display_order is not None:
        return (priority, display_order, "", "")
    timestamp = str(item.get("completed_at") or item.get("updated_at") or "")
    todo_id = str(item.get("todo_id") or "")
    return (priority, TODO_MISSING_INDEX, timestamp, todo_id)


def todo_projection_sort_key(
    item: dict[str, Any],
    *,
    text_mode: str = "label",
) -> tuple[int, int]:
    return (todo_priority_rank(item, text_mode=text_mode), todo_index_rank(item))


def todo_claimed_visibility_items(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or len(items) <= limit:
        return items[:limit]
    claim_order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
        if not claimed_by:
            continue
        if claimed_by not in buckets:
            buckets[claimed_by] = []
            claim_order.append(claimed_by)
        buckets[claimed_by].append(item)
    if not buckets:
        return items[:limit]

    original_index = {id(item): index for index, item in enumerate(items)}
    per_claimant_cap = max(1, limit // len(buckets))
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    for claimed_by in claim_order:
        taken = 0
        for item in buckets[claimed_by]:
            if taken >= per_claimant_cap:
                break
            if len(selected) >= limit:
                break
            selected.append(item)
            selected_ids.add(id(item))
            taken += 1
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for item in items:
            if id(item) in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(id(item))
            if len(selected) >= limit:
                break

    return sorted(
        selected, key=lambda item: original_index.get(id(item), TODO_MISSING_INDEX)
    )[:limit]


def todo_item_task_text(
    item: dict[str, Any],
    *,
    keys: tuple[str, ...] = ("title", "text"),
) -> str:
    return " ".join(
        str(item.get(key) or "") for key in keys if str(item.get(key) or "").strip()
    )


def todo_item_task_class(
    item: dict[str, Any],
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> str:
    return monitor_todo_task_class(
        item,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_is_actionable_open(item: dict[str, Any]) -> bool:
    guard = item.get("goal_acceptance_guard")
    if isinstance(guard, dict) and guard.get("allowed") is False:
        return False
    return monitor_todo_is_actionable_open(item)


def todo_item_is_deferred(item: dict[str, Any]) -> bool:
    return (normalize_todo_status(item.get("status")) or "") == TODO_STATUS_DEFERRED


def todo_item_next_due_at(item: dict[str, Any]) -> datetime | None:
    return monitor_todo_next_due_at(item)


def todo_item_has_monitor_schedule(item: dict[str, Any]) -> bool:
    return monitor_todo_has_schedule(item)


def todo_item_expires_at(item: dict[str, Any]) -> datetime | None:
    return monitor_todo_expires_at(item)


def todo_item_is_expired_monitor(
    item: dict[str, Any], *, now: datetime | None = None
) -> bool:
    return monitor_todo_is_expired(item, now=now)


def todo_item_is_due_monitor(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> bool:
    return monitor_todo_is_due(
        item,
        now=now,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_missing_monitor_schedule(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> bool:
    return monitor_todo_missing_schedule(
        item,
        now=now,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_claimed_by_agent_or_unclaimed(
    item: dict[str, Any],
    *,
    agent_id: str | None,
) -> bool:
    if todo_item_has_removed_continuation_policy(item):
        return False
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not normalized_agent_id:
        return True
    if normalized_agent_id in normalize_todo_excluded_agents(
        item.get("excluded_agents")
    ):
        return False
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    return not claimed_by or claimed_by == normalized_agent_id


def todo_advancement_frontier_items(
    summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the authoritative advancement frontier items grouped by claim ownership.

    Preserves the slot precedence of executable backlog first, falling back to
    unclaimed priority and claimed advancement open items when the executable backlog
    is omitted. Peer-claimed items are tracked separately and excluded from the current
    agent's selectable advancement frontier.
    """

    empty: dict[str, list[dict[str, Any]]] = {
        "current_agent_claimed_items": [],
        "unclaimed_items": [],
        "other_agent_claimed_items": [],
    }
    if not isinstance(summary, dict):
        return empty

    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    executable_items = summary.get("executable_backlog_items")
    if isinstance(executable_items, list):
        current_items: list[dict[str, Any]] = []
        unclaimed_items: list[dict[str, Any]] = []
        other_items: list[dict[str, Any]] = []
        for value in executable_items:
            if not isinstance(value, dict):
                continue
            if not todo_item_is_actionable_open(value):
                continue
            if todo_item_task_class(value) != TODO_TASK_CLASS_ADVANCEMENT:
                continue
            claimed_by = normalize_todo_claimed_by(value.get("claimed_by"))
            if claimed_by:
                if normalized_agent_id and claimed_by == normalized_agent_id:
                    if not todo_item_excludes_agent(
                        value, agent_id=normalized_agent_id
                    ):
                        current_items.append(value)
                elif normalized_agent_id:
                    other_items.append(value)
                else:
                    current_items.append(value)
                continue
            if not todo_item_excludes_agent(value, agent_id=normalized_agent_id):
                unclaimed_items.append(value)
        return {
            "current_agent_claimed_items": current_items,
            "unclaimed_items": unclaimed_items,
            "other_agent_claimed_items": other_items,
        }

    unclaimed_items = [
        value
        for value in summary.get("unclaimed_priority_open_items") or []
        if isinstance(value, dict)
        and todo_item_is_actionable_open(value)
        and todo_item_task_class(value) == TODO_TASK_CLASS_ADVANCEMENT
        and not todo_item_excludes_agent(value, agent_id=normalized_agent_id)
    ]
    current_items = [
        value
        for value in summary.get("claimed_advancement_open_items") or []
        if isinstance(value, dict)
        and todo_item_is_actionable_open(value)
        and todo_item_task_class(value) == TODO_TASK_CLASS_ADVANCEMENT
        and (
            not normalized_agent_id
            or normalize_todo_claimed_by(value.get("claimed_by")) == normalized_agent_id
        )
        and not todo_item_excludes_agent(value, agent_id=normalized_agent_id)
    ]
    other_items = [
        value
        for value in summary.get("claimed_advancement_open_items") or []
        if isinstance(value, dict)
        and todo_item_is_actionable_open(value)
        and todo_item_task_class(value) == TODO_TASK_CLASS_ADVANCEMENT
        and normalized_agent_id
        and normalize_todo_claimed_by(value.get("claimed_by"))
        and normalize_todo_claimed_by(value.get("claimed_by")) != normalized_agent_id
    ]
    return {
        "current_agent_claimed_items": current_items,
        "unclaimed_items": unclaimed_items,
        "other_agent_claimed_items": other_items,
    }


def agent_scoped_selectable_advancement_todo_ids(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> set[str]:
    """Return the ids the agent-scoped selectable advancement frontier holds.

    Derived directly from the authoritative ``todo_advancement_frontier_items``
    helper so that slot precedence and claim ownership predicates never diverge
    from the frontier counter.
    """

    frontier_items = todo_advancement_frontier_items(
        agent_todo_summary,
        agent_id=agent_id,
    )
    selectable: set[str] = set()
    for item in (
        frontier_items["current_agent_claimed_items"]
        + frontier_items["unclaimed_items"]
    ):
        if todo_id := normalize_todo_id(item.get("todo_id")):
            selectable.add(todo_id)
    return selectable


def todo_advancement_frontier_counts(
    summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
) -> dict[str, int]:
    """Classify the durable advancement frontier by exact claim ownership."""

    if not isinstance(summary, dict):
        return {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 0,
            "other_agent_claimed_advancement_count": 0,
        }
    frontier_items = todo_advancement_frontier_items(summary, agent_id=agent_id)
    claim_scope = summary.get("claim_scope")
    other_items = (
        claim_scope.get("other_agent_claimed_items")
        if isinstance(claim_scope, dict)
        else []
    )
    diagnostic_other_count = sum(
        1
        for value in other_items or []
        if isinstance(value, dict)
        and todo_item_is_actionable_open(value)
        and todo_item_task_class(value) == TODO_TASK_CLASS_ADVANCEMENT
    )
    return {
        "current_agent_claimed_advancement_count": max(
            len(frontier_items["current_agent_claimed_items"]),
            _positive_int(summary.get("current_agent_claimed_advancement_count")),
        ),
        "unclaimed_advancement_count": len(frontier_items["unclaimed_items"]),
        "other_agent_claimed_advancement_count": max(
            len(frontier_items["other_agent_claimed_items"]),
            diagnostic_other_count,
        ),
    }


def todo_item_has_removed_continuation_policy(item: dict[str, Any]) -> bool:
    return bool(
        normalize_removed_todo_continuation_policy(
            item.get("removed_continuation_policy")
        )
    )


def todo_item_excludes_agent(
    item: dict[str, Any],
    *,
    agent_id: str | None,
) -> bool:
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    return bool(
        normalized_agent_id
        and normalized_agent_id
        in normalize_todo_excluded_agents(item.get("excluded_agents"))
    )


def todo_summary_claim_scope_agent_id(summary: dict[str, Any] | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    claim_scope = summary.get("claim_scope")
    if not isinstance(claim_scope, dict):
        return None
    return normalize_todo_claimed_by(claim_scope.get("agent_id"))


def todo_summary_monitor_writeback_contract(
    summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    contract = summary.get("monitor_writeback")
    if not isinstance(contract, dict):
        return None
    if contract.get("supported") is not False:
        return None
    compact: dict[str, Any] = {"supported": False}
    source = str(contract.get("source") or "").strip()
    if source:
        compact["source"] = source
    return compact


def todo_summary_monitor_writeback_supported(summary: dict[str, Any] | None) -> bool:
    contract = todo_summary_monitor_writeback_contract(summary)
    if not contract:
        return True
    return contract.get("supported") is not False


def todo_summary_monitor_items(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for key in (
        "monitor_due_items",
        "current_agent_claimed_monitor_items",
        "monitor_open_items",
        "claimed_monitor_open_items",
        "first_open_items",
    ):
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            if not todo_item_is_actionable_open(value):
                continue
            if todo_item_task_class(value) != TODO_TASK_CLASS_MONITOR:
                continue
            identity = (normalize_todo_id(value.get("todo_id")) or "", id(value))
            if identity in seen:
                continue
            seen.add(identity)
            items.append(value)
    return items


def _summary_monitor_items(
    summary: dict[str, Any] | None,
    *,
    projected_key: str,
    predicate: Any,
    task_text_keys: tuple[str, ...],
    text_mode: str,
) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    if not todo_summary_monitor_writeback_supported(summary):
        return []
    projected_items = summary.get(projected_key)
    if isinstance(projected_items, list):
        items = [
            item
            for item in projected_items
            if isinstance(item, dict)
            if todo_item_is_actionable_open(item)
            if todo_item_task_class(item, task_text_keys=task_text_keys)
            == TODO_TASK_CLASS_MONITOR
            if predicate(item)
        ]
    else:
        raw_items = summary.get("monitor_open_items")
        items = [
            item
            for item in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(item, dict)
            if predicate(item)
        ]
    agent_id = todo_summary_claim_scope_agent_id(summary)
    if agent_id:
        items = [
            item
            for item in items
            if todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
        ]
    return sorted(
        items,
        key=lambda item: todo_projection_sort_key(item, text_mode=text_mode),
    )


def todo_summary_monitor_due_items(
    summary: dict[str, Any] | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[dict[str, Any]]:
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


def todo_summary_watch_only_monitor_due_items(
    summary: dict[str, Any] | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[dict[str, Any]]:
    """Consume the typed watch-only partition; legacy summaries fall back safely."""

    if isinstance(summary, dict) and isinstance(
        summary.get("watch_only_monitor_due_items"), list
    ):
        return _summary_monitor_items(
            summary,
            projected_key="watch_only_monitor_due_items",
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
        if todo_item_is_watch_only_monitor(item)
    ]


def todo_summary_non_watch_only_monitor_due_items(
    summary: dict[str, Any] | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[dict[str, Any]]:
    """Consume the typed ordinary-due partition; classify only legacy summaries."""

    if isinstance(summary, dict) and isinstance(
        summary.get("non_watch_only_monitor_due_items"), list
    ):
        return _summary_monitor_items(
            summary,
            projected_key="non_watch_only_monitor_due_items",
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
        if not todo_item_is_watch_only_monitor(item)
    ]


def todo_summary_monitor_due_count(
    summary: dict[str, Any] | None,
    *,
    due_items: list[dict[str, Any]] | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> int:
    if not isinstance(summary, dict):
        return 0
    if not todo_summary_monitor_writeback_supported(summary):
        return 0
    projected_count = summary.get("monitor_due_count")
    if isinstance(projected_count, int):
        return max(0, projected_count)
    agent_id = todo_summary_claim_scope_agent_id(summary)
    if agent_id:
        raw_items = summary.get("monitor_open_items")
        if isinstance(raw_items, list):
            return len(
                [
                    item
                    for item in raw_items
                    if isinstance(item, dict)
                    if todo_item_is_due_monitor(item, task_text_keys=task_text_keys)
                    if todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
                ]
            )
        return len(
            due_items
            if due_items is not None
            else todo_summary_monitor_due_items(
                summary,
                task_text_keys=task_text_keys,
                text_mode=text_mode,
            )
        )
    return len(
        due_items
        if due_items is not None
        else todo_summary_monitor_due_items(
            summary,
            task_text_keys=task_text_keys,
            text_mode=text_mode,
        )
    )


def todo_summary_monitor_schedule_gap_items(
    summary: dict[str, Any] | None,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> list[dict[str, Any]]:
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
    summary: dict[str, Any] | None,
    *,
    gap_items: list[dict[str, Any]] | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
    text_mode: str = "label",
) -> int:
    if not isinstance(summary, dict):
        return 0
    if not todo_summary_monitor_writeback_supported(summary):
        return 0
    agent_id = todo_summary_claim_scope_agent_id(summary)
    if agent_id:
        raw_items = summary.get("monitor_open_items")
        if isinstance(raw_items, list):
            return len(
                [
                    item
                    for item in raw_items
                    if isinstance(item, dict)
                    if todo_item_missing_monitor_schedule(
                        item,
                        task_text_keys=task_text_keys,
                    )
                    if todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id)
                ]
            )
        return len(
            gap_items
            if gap_items is not None
            else todo_summary_monitor_schedule_gap_items(
                summary,
                task_text_keys=task_text_keys,
                text_mode=text_mode,
            )
        )
    projected_count = summary.get("monitor_schedule_gap_count")
    if isinstance(projected_count, int):
        return max(0, projected_count)
    return len(
        gap_items
        if gap_items is not None
        else todo_summary_monitor_schedule_gap_items(
            summary,
            task_text_keys=task_text_keys,
            text_mode=text_mode,
        )
    )


def todo_summary_open_count(summary: dict[str, Any] | None) -> int:
    if not isinstance(summary, dict):
        return 0
    try:
        return max(0, int(summary.get("open_count") or 0))
    except (TypeError, ValueError):
        return 0


def todo_summary_open_task_counts(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Consume pre-limit counts; old display-only summaries provide lower bounds."""
    from ..effect_runtime import effect_runtime_result

    summary = summary if isinstance(summary, dict) else {}
    counts = summary.get("work_counts")
    if counts is None:
        rows = []
        for key in ("items", "executable_backlog_items", "first_executable_items", "first_open_items", "monitor_open_items"):
            for item in summary.get(key) or []:
                if not isinstance(item, dict) or item.get("done") is True:
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                rows.append({"identity": str(item.get("todo_id") or (str(item.get("index")) + ":" + text)),
                    "actionable": todo_item_is_actionable_open(item), "task_class": todo_item_task_class(item)})
        counts = effect_runtime_result("todo.work_counts.project", {
            "schema_version": "todo_work_counts_request_v0", "rows": rows,
            "source_open_count": summary.get("open_count"),
            "agent_id": todo_summary_claim_scope_agent_id(summary),
        })
    if (not isinstance(counts, dict) or counts.get("schema_version") != "todo_work_counts_v0"
        or not isinstance(counts.get("complete"), bool)
        or any(type(counts.get(key)) is not int or counts[key] < 0
               for key in ("open", "advancement", "monitor", "hidden"))
        or counts.get("agent_id") != todo_summary_claim_scope_agent_id(summary)):
        raise ValueError("invalid or differently scoped Todo work counts")
    if (counts["hidden"] > counts["open"]
        or counts["advancement"] + counts["monitor"] > counts["open"] - counts["hidden"]
        or (counts["complete"] and counts["hidden"] != 0)):
        raise ValueError("inconsistent Todo work count envelope")
    return {key: counts[key] for key in ("open", "advancement", "monitor", "hidden", "complete")} | {
        "monitor_due": todo_summary_monitor_due_count(summary),
        "monitor_schedule_gap": todo_summary_monitor_schedule_gap_count(summary),
    }


def todo_summary_has_only_future_scoped_monitor_work(
    summary: dict[str, Any] | None,
) -> bool:
    """Return true when the scoped agent has only non-due monitor work left."""

    agent_id = todo_summary_claim_scope_agent_id(summary)
    if not agent_id or not isinstance(summary, dict):
        return False
    if not todo_summary_monitor_items(summary):
        return False
    counts = todo_summary_open_task_counts(summary)
    if counts["complete"] is not True or counts["advancement"] > 0:
        return False
    if todo_summary_monitor_due_count(summary) > 0:
        return False
    if todo_summary_monitor_schedule_gap_count(summary) > 0:
        return False
    if _positive_int(summary.get("current_agent_claimed_advancement_count")) > 0:
        return False

    for key in (
        "current_agent_claimed_advancement_items",
        "unclaimed_priority_open_items",
        "first_executable_items",
        "executable_backlog_items",
    ):
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            if not todo_item_is_actionable_open(item):
                continue
            if todo_item_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
                continue
            if todo_item_claimed_by_agent_or_unclaimed(item, agent_id=agent_id):
                return False
    return True


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def todo_summary_first_executable_item(
    summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    raw_items = summary.get("first_executable_items")
    items = raw_items if isinstance(raw_items, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not todo_item_is_actionable_open(item):
            continue
        if todo_item_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        return item
    return None
