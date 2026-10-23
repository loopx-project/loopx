"""Compatibility exports for complete native Todo presentation lenses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from ..effect_runtime import effect_runtime_result

_READ_CODEC = json.loads(Path(__file__).with_name("read_legacy_codec.json").read_text(encoding="utf-8"))
_READ_LIST_LAYOUT = _READ_CODEC["list_constants"]
AGENT_LANE_TODO_LIST_PROJECTION_SCHEMA_VERSION = _READ_LIST_LAYOUT["AGENT_LANE_TODO_LIST_PROJECTION_SCHEMA_VERSION"]
AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION = _READ_LIST_LAYOUT["AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION"]
EXPLICIT_LIMIT_TODO_LIST_SUMMARY_SCHEMA_VERSION = _READ_LIST_LAYOUT["EXPLICIT_LIMIT_TODO_LIST_SUMMARY_SCHEMA_VERSION"]
THIN_TODO_LIST_PROJECTION_SCHEMA_VERSION = _READ_LIST_LAYOUT["THIN_TODO_LIST_PROJECTION_SCHEMA_VERSION"]
THIN_TODO_LIST_SUMMARY_SCHEMA_VERSION = _READ_LIST_LAYOUT["THIN_TODO_LIST_SUMMARY_SCHEMA_VERSION"]
AGENT_LANE_TODO_LIST_ITEM_LIMIT = _READ_LIST_LAYOUT["AGENT_LANE_TODO_LIST_ITEM_LIMIT"]
AGENT_LANE_TODO_LIST_TEXT_LIMIT = _READ_LIST_LAYOUT["AGENT_LANE_TODO_LIST_TEXT_LIMIT"]
THIN_TODO_LIST_ITEM_LIMIT_PER_ROLE = _READ_LIST_LAYOUT["THIN_TODO_LIST_ITEM_LIMIT_PER_ROLE"]
THIN_TODO_LIST_NESTED_ITEM_LIMIT = _READ_LIST_LAYOUT["THIN_TODO_LIST_NESTED_ITEM_LIMIT"]
THIN_TODO_LIST_NESTED_DICT_FIELD_LIMIT = _READ_LIST_LAYOUT["THIN_TODO_LIST_NESTED_DICT_FIELD_LIMIT"]
THIN_TODO_LIST_ITEM_FIELDS = tuple(_READ_LIST_LAYOUT["THIN_TODO_LIST_ITEM_FIELDS"])
AGENT_LANE_HOT_PATH_VIEW = "agent_lane_hot_path"
EXPLICIT_LIMIT_COLD_PATH_VIEW = "explicit_limit_cold_path"
THIN_EXPLICIT_VIEW = "thin_explicit_view"
TodoListProjectionView = Literal["agent_lane_hot_path", "explicit_limit_cold_path"]


def compact_thin_todo_list_payload(payload):
    return effect_runtime_result("todo.context.page", {"thin_payload": payload}, large_local_snapshot=True)


def compact_agent_lane_todo_summary(summary, *, role):
    return effect_runtime_result("todo.context.page", {"summary_payload": summary, "role": role, "limit": None}, large_local_snapshot=True)


def compact_explicit_limit_todo_summary(summary, *, role, item_limit):
    return effect_runtime_result("todo.context.page", {"summary_payload": summary, "role": role, "limit": item_limit}, large_local_snapshot=True)


def compact_thin_todo_summary(summary, *, role, items_matched, items_returned, item_limit_per_role):
    return effect_runtime_result("todo.context.page", {"thin_summary_payload": summary, "details": {
        "role": role, "items_matched": items_matched, "items_returned": items_returned,
        "item_limit_per_role": item_limit_per_role}}, large_local_snapshot=True)


def todo_item_relations(item: dict[str, Any]) -> dict[str, Any]:
    """Project the stable relationship fields for one todo list item."""

    relations: dict[str, Any] = {}
    for key in _READ_CODEC["relation_fields"]:
        value = item.get(key)
        if value is not None and value != []:
            relations[key] = value
    return relations


def todo_list_projection_contract(
    *,
    matched_todo_count: int,
    returned_todo_count: int,
    view: TodoListProjectionView = AGENT_LANE_HOT_PATH_VIEW,
    item_limit_per_role: int = AGENT_LANE_TODO_LIST_ITEM_LIMIT,
    full_detail_cold_paths: tuple[str, ...] = (
        "todo list with --role, --status, or --todo-id",
        "todo list without --agent-id",
        "active state",
    ),
) -> dict[str, Any]:
    return {
        "schema_version": AGENT_LANE_TODO_LIST_PROJECTION_SCHEMA_VERSION,
        "view": view,
        "matched_todo_count": matched_todo_count,
        "returned_todo_count": returned_todo_count,
        "item_limit_per_role": item_limit_per_role,
        "counts_cover_full_match": True,
        "full_detail_cold_paths": list(full_detail_cold_paths),
    }
