from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re
from typing import cast

from ..coordination.coordination_state_contract_generated import (
    COORDINATION_STATE_CONTRACT,
)
from ..runtime.public_safety import public_safe_compact_text
from ..scheduler.monitor_todo import (
    monitor_todo_expires_at,
    monitor_todo_is_actionable_open,
    monitor_todo_is_due,
    monitor_todo_is_expired,
    monitor_todo_missing_schedule,
    monitor_todo_next_due_at,
    monitor_todo_task_class,
)
from .contract import (
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
    TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_MONITOR,
    normalize_removed_todo_continuation_policy,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_status,
    normalize_todo_watch_only,
)

TodoObject = dict[str, object]
TodoMapping = Mapping[str, object]

_CONTRACT_MAP = cast(Mapping[str, object], COORDINATION_STATE_CONTRACT)
_PRIORITY_CONTRACT = cast(Mapping[str, object], _CONTRACT_MAP["todo_priority"])
_READ_RECORD_CONTRACT = cast(Mapping[str, object], _CONTRACT_MAP["todo_read_record"])
_DOMAIN_RECORD_CONTRACT = cast(Mapping[str, object], _CONTRACT_MAP["todo_domain_record"])
_PRIORITY_VALUES = cast(tuple[object, ...], _PRIORITY_CONTRACT["values"])
_PRIORITY_RANK_BY_VALUE: dict[str, int] = {
    str(label): index for index, label in enumerate(_PRIORITY_VALUES)
}

TODO_MISSING_PRIORITY_RANK = int(cast(int, _PRIORITY_CONTRACT["missing_rank"]))
TODO_MISSING_INDEX = 999999
TODO_PRIORITY_PREFIX_PATTERN = re.compile(
    str(_PRIORITY_CONTRACT["legacy_prefix_pattern"]), re.IGNORECASE
)
TODO_PRIORITY_LABEL_PATTERN = re.compile(
    str(_PRIORITY_CONTRACT["legacy_label_pattern"]), re.IGNORECASE
)
TODO_PRESENTATION_METADATA_SCHEMA = "loopx_todo_presentation_metadata_v0"
TODO_LEGACY_ITEM_SCHEMA = str(_READ_RECORD_CONTRACT["item_schema_version"])
TODO_NATIVE_ITEM_SCHEMA = str(_DOMAIN_RECORD_CONTRACT["item_schema_version"])


def _object_dict(item: TodoMapping) -> TodoObject:
    return cast(TodoObject, item) if isinstance(item, dict) else dict(item)


def _non_bool_non_negative_int(value: object) -> int | None:
    return (
        int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _has_source_section(item: TodoMapping) -> bool:
    source_section = item.get("source_section")
    return isinstance(source_section, str) and bool(source_section.strip())


def positive_int(value: object) -> int:
    try:
        parsed = int(cast(int | float | str | bytes, value))
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def todo_blocker_reason(item: TodoMapping) -> str | None:
    """Project a bounded, public-safe cause for blocked work of any task class."""

    if (
        normalize_todo_status(item.get("status")) != TODO_STATUS_BLOCKED
        and todo_item_task_class(item) != TODO_TASK_CLASS_BLOCKER
    ):
        return None
    return public_safe_compact_text(item.get("reason"), limit=220)


def todo_item_is_watch_only_monitor(item: TodoMapping) -> bool:
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
    item: TodoMapping,
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


def todo_priority_rank(value: object, *, text_mode: str = "label") -> int:
    mapping = cast(TodoMapping, value) if isinstance(value, Mapping) else {"priority": value}
    priority = todo_priority_label(mapping, text_mode=text_mode)
    return _PRIORITY_RANK_BY_VALUE.get(priority, TODO_MISSING_PRIORITY_RANK) if priority else TODO_MISSING_PRIORITY_RANK


def todo_index_rank(item: TodoMapping) -> int:
    raw_index = item.get("index")
    if raw_index is None:
        return TODO_MISSING_INDEX
    try:
        return int(cast(int | float | str | bytes, raw_index))
    except (TypeError, ValueError):
        return TODO_MISSING_INDEX


def todo_presentation_metadata(item: TodoMapping) -> TodoObject:
    """Project one Todo's display address without making it domain state.

    The v0 ``source_section``/``index`` pair is the wire shape's canonical
    presentation coordinate. Native domain records derive a section from role
    and archival state and intentionally receive no synthetic persisted index.
    """

    if not isinstance(item, Mapping):
        raise ValueError("Todo presentation input must be an object")
    schema = item.get("schema_version")
    if schema not in {None, TODO_LEGACY_ITEM_SCHEMA, TODO_NATIVE_ITEM_SCHEMA}:
        raise ValueError(f"unsupported Todo presentation schema: {schema!r}")
    has_section = _has_source_section(item)
    legacy = schema == TODO_LEGACY_ITEM_SCHEMA or (schema is None and has_section)
    if legacy:
        section: object = item.get("source_section") if has_section else None
        display_order = _non_bool_non_negative_int(item.get("index"))
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


def _presentation_display_order(item: TodoMapping) -> int | None:
    if not isinstance(item, Mapping):
        raise ValueError("Todo presentation input must be an object")
    schema = item.get("schema_version")
    if schema == TODO_NATIVE_ITEM_SCHEMA:
        return None
    if schema is None and not _has_source_section(item):
        return None
    return _non_bool_non_negative_int(item.get("index"))


def todo_presentation_sort_key(
    item: TodoMapping,
    *,
    text_mode: str = "label",
) -> tuple[int, int, str, str]:
    """Sort display rows while preserving source coordinates and native determinism.

    Legacy rows with an equal priority/index retain Python's stable input order.
    Native rows have no fabricated index, so timestamp and Todo identity provide
    a deterministic tie-break that matches the TypeScript presentation adapter.
    """

    priority = todo_priority_rank(item, text_mode=text_mode)
    display_order = _presentation_display_order(item)
    if display_order is not None:
        return (priority, display_order, "", "")
    timestamp = str(item.get("completed_at") or item.get("updated_at") or "")
    todo_id = str(item.get("todo_id") or "")
    return (priority, TODO_MISSING_INDEX, timestamp, todo_id)


def todo_projection_sort_key(
    item: TodoMapping,
    *,
    text_mode: str = "label",
) -> tuple[int, int]:
    return (todo_priority_rank(item, text_mode=text_mode), todo_index_rank(item))


def todo_item_task_text(
    item: TodoMapping,
    *,
    keys: tuple[str, ...] = ("title", "text"),
) -> str:
    parts: list[str] = []
    for key in keys:
        text = str(item.get(key) or "")
        if text.strip():
            parts.append(text)
    return " ".join(parts)


def todo_item_task_class(
    item: TodoMapping,
    *,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> str:
    return monitor_todo_task_class(
        item,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_is_actionable_open(item: TodoMapping) -> bool:
    guard = item.get("goal_acceptance_guard")
    if isinstance(guard, Mapping) and guard.get("allowed") is False:
        return False
    return monitor_todo_is_actionable_open(_object_dict(item))


def todo_item_is_deferred(item: TodoMapping) -> bool:
    return (normalize_todo_status(item.get("status")) or "") == TODO_STATUS_DEFERRED


def todo_item_next_due_at(item: TodoMapping) -> datetime | None:
    return monitor_todo_next_due_at(_object_dict(item))


def todo_item_expires_at(item: TodoMapping) -> datetime | None:
    return monitor_todo_expires_at(_object_dict(item))


def todo_item_is_expired_monitor(
    item: TodoMapping, *, now: datetime | None = None
) -> bool:
    return monitor_todo_is_expired(_object_dict(item), now=now)


def todo_item_is_due_monitor(
    item: TodoMapping,
    *,
    now: datetime | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> bool:
    return monitor_todo_is_due(
        _object_dict(item),
        now=now,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_missing_monitor_schedule(
    item: TodoMapping,
    *,
    now: datetime | None = None,
    task_text_keys: tuple[str, ...] = ("title", "text"),
) -> bool:
    return monitor_todo_missing_schedule(
        _object_dict(item),
        now=now,
        task_text=todo_item_task_text(item, keys=task_text_keys),
    )


def todo_item_has_removed_continuation_policy(item: TodoMapping) -> bool:
    return bool(
        normalize_removed_todo_continuation_policy(
            item.get("removed_continuation_policy")
        )
    )


def todo_item_excludes_agent(
    item: TodoMapping,
    *,
    agent_id: str | None,
) -> bool:
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    return bool(
        normalized_agent_id
        and normalized_agent_id
        in normalize_todo_excluded_agents(item.get("excluded_agents"))
    )


def todo_item_allows_normalized_agent(
    item: TodoMapping,
    *,
    normalized_agent_id: str | None,
) -> bool:
    """Check claim and exclusion boundaries when agent_id is pre-normalized."""

    if todo_item_has_removed_continuation_policy(item):
        return False
    if not normalized_agent_id:
        return True
    if normalized_agent_id in normalize_todo_excluded_agents(
        item.get("excluded_agents")
    ):
        return False
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    return not claimed_by or claimed_by == normalized_agent_id


def todo_item_claimed_by_agent_or_unclaimed(
    item: TodoMapping,
    *,
    agent_id: str | None,
) -> bool:
    return todo_item_allows_normalized_agent(
        item,
        normalized_agent_id=normalize_todo_claimed_by(agent_id),
    )
