"""Bounded display Todo summary and projection-gap helpers for `project_asset`."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from itertools import islice
from typing import SupportsInt, TypedDict

from ..runtime.public_safety import compact_text as _compact_text
from ..todos.todo_semantics import todo_blocker_reason


TODO_PROJECTION_VIEW_SCHEMA_VERSION = "todo_projection_view_v0"
TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION = "todo_projection_detail_pointer_v0"
PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION = "project_asset_todo_projection_gap_v0"

PROJECT_ASSET_TODO_DISPLAY_FIELDS: tuple[str, ...] = (
    "index",
    "done",
    "schema_version",
    "todo_id",
    "role",
    "status",
    "priority",
    "archive_state",
    "source_section",
    "task_class",
    "action_kind",
    "capability_binding_ref",
    "task_domain",
    "task_repository",
    "required_write_scopes",
    "required_capabilities",
    "target_capabilities",
    "decision_scope",
    "required_decision_scopes",
    "created_by",
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
    "blocking_monitor_todo_id",
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
)

_TODO_LANE_KEYS: tuple[str, ...] = (
    "gate_open_items",
    "current_agent_claimed_open_items",
    "active_next_action_items",
    "active_next_action_executable_items",
)


class ProjectAssetTodoProjectionView(TypedDict):
    schema_version: str
    view: str
    truth: str
    canonical_source: str
    item_limit: int
    deferred_item_limit: int


class ProjectAssetTodoDetailPointer(TypedDict):
    schema_version: str
    cold_path: str
    active_state_source: str
    full_list_included: bool


class ProjectAssetTodoProjectionMetadata(TypedDict):
    projection_view: ProjectAssetTodoProjectionView
    detail_pointer: ProjectAssetTodoDetailPointer


def _optional_int(value: object) -> int | None:
    if not isinstance(value, (SupportsInt, str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: object) -> int:
    if value is None or value == "" or value == 0 or value is False:
        return 0
    parsed = _optional_int(value)
    if parsed is None:
        raise TypeError(f"cannot coerce {type(value).__name__} to int")
    return parsed


def _bounded_dict_items(raw: object, *, limit: int) -> list[dict[str, object]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or limit <= 0:
        return []
    return list(islice((item for item in raw if isinstance(item, dict)), limit))


def project_asset_todo_projection_metadata(
    *,
    role: str | None,
    item_limit: int,
    deferred_item_limit: int,
) -> ProjectAssetTodoProjectionMetadata:
    todo_role = str(role or "").strip().lower()
    if todo_role == "user":
        canonical_source = "attention_queue.items[].user_todos"
    elif todo_role == "agent":
        canonical_source = "attention_queue.items[].agent_todos"
    else:
        canonical_source = "attention_queue.items[].{user_todos,agent_todos}"
    return {
        "projection_view": {
            "schema_version": TODO_PROJECTION_VIEW_SCHEMA_VERSION,
            "view": "project_asset_overview",
            "truth": "derived",
            "canonical_source": canonical_source,
            "item_limit": item_limit,
            "deferred_item_limit": deferred_item_limit,
        },
        "detail_pointer": {
            "schema_version": TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION,
            "cold_path": "loopx status --format json",
            "active_state_source": "registry goal state_file",
            "full_list_included": False,
        },
    }


def project_asset_todo_projection_gap(
    *,
    user_todos: dict[str, object] | None,
    agent_todos: dict[str, object] | None,
) -> dict[str, object] | None:
    missing_roles: list[str] = []
    if not isinstance(user_todos, dict):
        missing_roles.append("user")
    if not isinstance(agent_todos, dict):
        missing_roles.append("agent")
    if not missing_roles:
        return None
    return {
        "schema_version": PROJECT_ASSET_TODO_PROJECTION_GAP_SCHEMA_VERSION,
        "kind": "project_asset_todo_projection_gap",
        "missing_roles": missing_roles,
        "source": "active_state_todo_projection",
        "recommended_action": (
            "add parseable User Todo / Agent Todo sections or repair the active state_file "
            "before treating this project_asset as first-screen complete"
        ),
    }


def _project_asset_display_todo_item(item: dict[str, object]) -> dict[str, object]:
    display: dict[str, object] = {}
    for key in PROJECT_ASSET_TODO_DISPLAY_FIELDS:
        if (val := item.get(key)) is not None:
            display[key] = val
    text = _compact_text(str(item.get("text") or ""), limit=220)
    if text:
        display["text"] = text
    title = _compact_text(str(item.get("title") or ""), limit=220)
    if title:
        display["title"] = title
    reason = todo_blocker_reason(item)
    if reason:
        display["reason"] = reason
    return display


def _project_asset_display_todo_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_project_asset_display_todo_item(item) for item in items if isinstance(item, dict)]


def build_project_asset_todo_summary(
    todos: dict[str, object] | None,
    *,
    role: str | None = None,
    item_limit: int,
    deferred_item_limit: int,
    advancement_task_class: str,
    open_todo_items: Callable[..., list[dict[str, object]]],
    compact_todo_item: Callable[[dict[str, object]], dict[str, object]],
    todo_lane_items: Callable[..., list[dict[str, object]]],
    todo_item_is_actionable_open: Callable[[dict[str, object]], bool],
    todo_item_task_class: Callable[[dict[str, object]], str],
) -> MutableMapping[str, object] | None:
    if not isinstance(todos, dict):
        return None

    def lane_count(lane: str) -> int:
        items = todos.get(lane)
        if not isinstance(items, list):
            return 0
        return sum(1 for item in items if isinstance(item, dict))

    open_count = todos.get("open_count", 0)
    done_count = todos.get("done_count", 0)
    total_count = todos.get("total_count", 0)
    raw_role = role or todos.get("role")
    todo_role = str(raw_role if raw_role is not None else "").strip().lower()
    metadata = project_asset_todo_projection_metadata(
        role=todo_role,
        item_limit=item_limit,
        deferred_item_limit=deferred_item_limit,
    )
    summary: dict[str, object] = {
        "schema_version": todos.get("schema_version") or "todo_summary_v0",
        "source_section": "project_asset",
        "open": open_count,
        "done": done_count,
        "total": total_count,
        **metadata,
    }
    capability_actions = todos.get("capability_actions")
    if todo_role == "user" and isinstance(capability_actions, dict):
        # Derived from the full canonical User Todo source before its generic
        # display items are capped. This is a bounded projection, not another
        # review or decision authority.
        summary["capability_actions"] = dict(capability_actions)
    if (adv_done := todos.get("advancement_done_count")) is not None:
        summary["advancement_done_count"] = adv_done
    open_items = open_todo_items(todos, limit=item_limit)
    claimed_open_count = todos.get("claimed_open_count")
    if claimed_open_count is None:
        claimed_open_count = lane_count("claimed_open_items") or sum(
            1 for item in open_items if item.get("claimed_by")
        )
    if claimed_open_count:
        summary["claimed_open_count"] = claimed_open_count
        if "unclaimed_open_count" in todos:
            summary["unclaimed_open_count"] = todos["unclaimed_open_count"]
        else:
            summary["unclaimed_open_count"] = max(
                0, _int_or_zero(open_count) - _int_or_zero(claimed_open_count),
            )
    display_open_items = _project_asset_display_todo_items(open_items)
    if display_open_items:
        summary["items"] = display_open_items
        summary["next"] = display_open_items[0]["text"]
        if (next_idx := display_open_items[0].get("index")) is not None:
            summary["next_index"] = next_idx
        if (next_claimed := display_open_items[0].get("claimed_by")):
            summary["next_claimed_by"] = next_claimed
    recent_completed_items = [
        _project_asset_display_todo_item(dict(item))
        for item in _bounded_dict_items(todos.get("recent_completed_advancement_items"), limit=item_limit)
    ]
    if recent_completed_items:
        summary["recent_completed_advancement_items"] = recent_completed_items
    monitor_writeback = todos.get("monitor_writeback")
    if isinstance(monitor_writeback, dict):
        summary["monitor_writeback"] = dict(monitor_writeback)
    deferred_items = [
        _project_asset_display_todo_item(compact_todo_item(item))
        for item in _bounded_dict_items(todos.get("deferred_items"), limit=deferred_item_limit)
    ]
    deferred_resume_candidates = [
        _project_asset_display_todo_item(compact_todo_item(item))
        for item in _bounded_dict_items(todos.get("deferred_resume_candidates"), limit=deferred_item_limit)
    ]
    if (deferred_count := todos.get("deferred_count")) is not None:
        summary["deferred_count"] = deferred_count
        summary["deferred_visibility_limit"] = deferred_item_limit
    if deferred_items:
        summary["deferred_items"] = deferred_items
    if deferred_resume_candidates:
        summary["deferred_resume_candidates"] = deferred_resume_candidates
    executable_items = [
        item
        for item in open_todo_items(
            todos,
            limit=item_limit,
            source_keys=("first_executable_items", "executable_backlog_items", "items"),
        )
        if todo_item_is_actionable_open(item)
        if todo_item_task_class(item) == advancement_task_class
    ]
    if executable_items:
        summary["first_executable_items"] = _project_asset_display_todo_items(
            executable_items[:item_limit]
        )
    for lane in _TODO_LANE_KEYS:
        lane_items = todo_lane_items(todos, lane, limit=item_limit)
        if lane_items:
            summary[lane] = _project_asset_display_todo_items(lane_items)
    for count_key in ("claimed_advancement_open_count", "claimed_monitor_open_count"):
        if (explicit_count := todos.get(count_key)) is not None:
            summary[count_key] = explicit_count
            continue
        lane = count_key.removesuffix("_count") + "_items"
        count = lane_count(lane)
        if count:
            summary[count_key] = count
    return summary
