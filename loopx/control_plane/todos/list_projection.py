from __future__ import annotations

from typing import Any, Literal

from .todo_semantics import todo_blocker_reason

AGENT_LANE_TODO_LIST_PROJECTION_SCHEMA_VERSION = "agent_lane_todo_list_projection_v0"
AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION = (
    "agent_lane_todo_list_summary_compaction_v0"
)
EXPLICIT_LIMIT_TODO_LIST_SUMMARY_SCHEMA_VERSION = (
    "explicit_limit_todo_list_summary_compaction_v0"
)
THIN_TODO_LIST_PROJECTION_SCHEMA_VERSION = "todo_list_thin_projection_v0"
THIN_TODO_LIST_SUMMARY_SCHEMA_VERSION = "todo_list_thin_summary_compaction_v0"
AGENT_LANE_TODO_LIST_ITEM_LIMIT = 12
AGENT_LANE_TODO_LIST_TEXT_LIMIT = 180
# Two items per role keeps the maximally populated retained field shape inside
# the registered 20k/600-line JSON budget; the regression owns that invariant.
THIN_TODO_LIST_ITEM_LIMIT_PER_ROLE = 2
THIN_TODO_LIST_NESTED_ITEM_LIMIT = 3
THIN_TODO_LIST_NESTED_DICT_FIELD_LIMIT = 8
AGENT_LANE_HOT_PATH_VIEW: Literal["agent_lane_hot_path"] = "agent_lane_hot_path"
EXPLICIT_LIMIT_COLD_PATH_VIEW: Literal["explicit_limit_cold_path"] = (
    "explicit_limit_cold_path"
)
THIN_EXPLICIT_VIEW: Literal["thin_explicit_view"] = "thin_explicit_view"
TodoListProjectionView = Literal[
    "agent_lane_hot_path",
    "explicit_limit_cold_path",
]

_RETAINED_LANE_LIMITS = {
    "items": AGENT_LANE_TODO_LIST_ITEM_LIMIT,
    "first_open_items": 3,
    "first_executable_items": 3,
    "deferred_items": 3,
    "monitor_due_items": 2,
    "monitor_schedule_gap_items": 2,
    "blocker_items": 2,
    "resume_blocked_items": 2,
    "active_next_action_items": 3,
    "active_next_action_executable_items": 3,
}
_RETAINED_DICTS = {
    "work_counts",
    "monitor_writeback",
    "source_proof",
    "terminal_closure_proof",
}
_ITEM_FIELDS = (
    "schema_version",
    "gate_state",
    "successor_count",
    "excluded_agents",
    "index",
    "todo_id",
    "role",
    "status",
    "priority",
    "text",
    "title",
    "note",
    "task_class",
    "action_kind",
    "task_domain",
    "capability_binding_ref",
    "task_repository",
    "continuation_policy",
    "required_capabilities",
    "target_capabilities",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "global_gate",
    "unblocks_todo_id",
    "successor_todo_ids",
    "completion_continuation",
    "completion_recovery",
    "resume_when",
    "resume_monitor_generation",
    "resume_ready",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "watch_only",
)

THIN_TODO_LIST_ITEM_FIELDS = (
    "todo_id",
    "role",
    "status",
    "priority",
    "text",
    "task_class",
    "action_kind",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "global_gate",
    "unblocks_todo_id",
    "decision_scope",
    "required_decision_scopes",
    "resume_when",
    "resume_ready",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "watch_only",
)
_COMPACT_ITEM_TEXT_FIELDS = frozenset({"text", "title", "note"})
_THIN_ITEM_TEXT_FIELDS = frozenset({"text"})


def _compact_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= AGENT_LANE_TODO_LIST_TEXT_LIMIT:
        return text
    return text[: AGENT_LANE_TODO_LIST_TEXT_LIMIT - 3].rstrip() + "..."


def _project_item_fields(
    value: Any,
    *,
    fields: tuple[str, ...],
    text_fields: frozenset[str],
) -> Any:
    if not isinstance(value, dict):
        return value
    compact: dict[str, Any] = {}
    for key in fields:
        child = value.get(key)
        if child is None:
            continue
        compact[key] = (
            _compact_text(child) if key in text_fields else child
        )
    return compact


def _compact_item(value: Any) -> Any:
    compact = _project_item_fields(
        value,
        fields=_ITEM_FIELDS,
        text_fields=_COMPACT_ITEM_TEXT_FIELDS,
    )
    if isinstance(value, dict) and isinstance(compact, dict):
        reason = todo_blocker_reason(value)
        if reason:
            compact["reason"] = reason
    return compact


def _compact_thin_item(value: Any) -> Any:
    projected = _project_item_fields(
        value,
        fields=THIN_TODO_LIST_ITEM_FIELDS,
        text_fields=_THIN_ITEM_TEXT_FIELDS,
    )
    if not isinstance(projected, dict):
        return projected
    return {key: _compact_thin_value(child) for key, child in projected.items()}


def _compact_thin_value(value: Any) -> Any:
    """Bound every retained thin field, including nested scope metadata."""

    if isinstance(value, str):
        return _compact_text(value)
    if isinstance(value, list):
        return [
            _compact_thin_value(child)
            for child in value[:THIN_TODO_LIST_NESTED_ITEM_LIMIT]
        ]
    if isinstance(value, dict):
        return {
            key: _compact_thin_value(child)
            for key, child in list(value.items())[
                :THIN_TODO_LIST_NESTED_DICT_FIELD_LIMIT
            ]
        }
    return value


def _summary_source_view(value: Any) -> str:
    if not isinstance(value, dict):
        return "full_detail"
    view = value.get("view")
    if isinstance(view, str) and view:
        return view
    if value.get("schema_version") == AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION:
        return AGENT_LANE_HOT_PATH_VIEW
    return "full_detail"


def compact_thin_todo_summary(
    summary: dict[str, Any],
    *,
    role: str,
    items_matched: int,
    items_returned: int,
    item_limit_per_role: int,
) -> dict[str, Any]:
    """Project one role summary to a bounded field-only Todo list view."""

    compact: dict[str, Any] = {}
    omitted_nonempty_lane_count = 0
    omitted_nonempty_dict_count = 0
    source_view = "full_detail"
    for key, value in summary.items():
        if isinstance(value, list):
            if key != "items":
                omitted_nonempty_lane_count += bool(value)
            continue
        if isinstance(value, dict):
            if key == "work_counts":
                compact[key] = value
            elif key == "payload_compaction":
                source_view = _summary_source_view(value)
            else:
                omitted_nonempty_dict_count += bool(value)
            continue
        compact[key] = value
    items_omitted = max(0, items_matched - items_returned)
    compacted_lanes = {}
    if items_omitted:
        compacted_lanes["items"] = {
            "shown": items_returned,
            "total": items_matched,
        }
    compact["payload_compaction"] = {
        "schema_version": THIN_TODO_LIST_SUMMARY_SCHEMA_VERSION,
        "view": THIN_EXPLICIT_VIEW,
        "source_view": source_view,
        "role": role,
        "items_projected_to": "todos",
        "item_limit_per_role": item_limit_per_role,
        "item_fields": list(THIN_TODO_LIST_ITEM_FIELDS),
        "item_text_limit": AGENT_LANE_TODO_LIST_TEXT_LIMIT,
        "nested_item_limit": THIN_TODO_LIST_NESTED_ITEM_LIMIT,
        "items_matched": items_matched,
        "items_returned": items_returned,
        "items_omitted": items_omitted,
        "compacted_lanes": compacted_lanes,
        "omitted_nonempty_lane_count": omitted_nonempty_lane_count,
        "omitted_nonempty_dict_count": omitted_nonempty_dict_count,
        "full_detail_cold_path": "todo list without --thin or active state",
    }
    return compact


def thin_todo_list_field_projection_contract(
    *,
    matched_todo_count: int,
    returned_todo_count: int,
    item_limit_per_role: int,
    source_view: str,
) -> dict[str, Any]:
    return {
        "schema_version": THIN_TODO_LIST_PROJECTION_SCHEMA_VERSION,
        "view": THIN_EXPLICIT_VIEW,
        "source_view": source_view,
        "item_container": "todos",
        "matched_todo_count": matched_todo_count,
        "returned_todo_count": returned_todo_count,
        "omitted_todo_count": max(
            0,
            matched_todo_count - returned_todo_count,
        ),
        "item_limit_per_role": item_limit_per_role,
        "counts_cover_full_match": True,
        "item_fields": list(THIN_TODO_LIST_ITEM_FIELDS),
        "item_text_limit": AGENT_LANE_TODO_LIST_TEXT_LIMIT,
        "nested_item_limit": THIN_TODO_LIST_NESTED_ITEM_LIMIT,
        "nested_dict_field_limit": THIN_TODO_LIST_NESTED_DICT_FIELD_LIMIT,
        "full_detail_cold_paths": [
            "todo list without --thin",
            "todo list --todo-id <id> without --thin",
            "active state",
        ],
    }


def compact_thin_todo_list_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the opt-in Todo list field and cardinality projection."""

    compact = dict(payload)
    compact.pop("state_file", None)
    compact.pop("project", None)
    compact.pop("filter_semantics", None)

    explicit_limit = compact.get("explicit_limit")
    item_limit_per_role = THIN_TODO_LIST_ITEM_LIMIT_PER_ROLE
    if isinstance(explicit_limit, int):
        item_limit_per_role = min(item_limit_per_role, explicit_limit)

    source_projection = compact.get("todo_list_projection")
    source_projection = source_projection if isinstance(source_projection, dict) else {}
    source_view = str(source_projection.get("view") or "full_detail")
    matched_todo_count = int(
        source_projection.get("matched_todo_count") or compact.get("todo_count") or 0
    )

    projected_items: list[dict[str, Any]] = []
    for key, role in (("user_todos", "user"), ("agent_todos", "agent")):
        summary = compact.get(key)
        if not isinstance(summary, dict):
            continue
        source_items = [
            item for item in summary.get("items") or [] if isinstance(item, dict)
        ]
        retained_items = source_items[:item_limit_per_role]
        projected_role_items = [_compact_thin_item(item) for item in retained_items]
        projected_items.extend(projected_role_items)
        items_matched = int(summary.get("total_count") or len(source_items))
        projected = compact_thin_todo_summary(
            summary,
            role=role,
            items_matched=items_matched,
            items_returned=len(projected_role_items),
            item_limit_per_role=item_limit_per_role,
        )
        compact[key] = projected
    compact["todos"] = projected_items
    returned_todo_count = len(projected_items)
    omitted_todo_count = max(0, matched_todo_count - returned_todo_count)
    compact["matched_todo_count"] = matched_todo_count
    compact["returned_todo_count"] = returned_todo_count
    compact["omitted_todo_count"] = omitted_todo_count

    todo_id_filter = compact.get("todo_id_filter")
    if isinstance(todo_id_filter, str):
        matched = next(
            (
                item
                for item in projected_items
                if item.get("todo_id") == todo_id_filter
            ),
            None,
        )
        compact["todo"] = matched
        compact["relations"] = todo_item_relations(matched) if matched else {}

    compact["thin"] = True
    compact["todo_list_field_projection"] = thin_todo_list_field_projection_contract(
        matched_todo_count=matched_todo_count,
        returned_todo_count=returned_todo_count,
        item_limit_per_role=item_limit_per_role,
        source_view=source_view,
    )
    return compact


def todo_item_relations(item: dict[str, Any]) -> dict[str, Any]:
    """Project the stable relationship fields for one todo list item."""

    relations: dict[str, Any] = {}
    for key in (
        "claimed_by",
        "bound_agent",
        "goal_bound",
        "blocks_agent",
        "excluded_agents",
        "global_gate",
        "unblocks_todo_id",
        "successor_todo_ids",
        "completion_continuation",
        "completion_recovery",
        "superseded_by",
        "resume_when",
        "resume_monitor_generation",
        "resume_condition",
        "resume_ready",
        "decision_scope",
        "required_decision_scopes",
        "required_write_scopes",
        "required_capabilities",
        "target_capabilities",
        "task_class",
        "action_kind",
        "continuation_policy",
        "target_key",
        "cadence",
        "next_due_at",
        "expires_at",
    ):
        value = item.get(key)
        if value is not None and value != []:
            relations[key] = value
    return relations


def compact_agent_lane_todo_summary(
    summary: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    """Bound one agent-lane list summary while retaining actionable identities."""
    compact: dict[str, Any] = {}
    compacted_lanes: dict[str, dict[str, int]] = {}
    omitted_nonempty_lane_count = 0
    omitted_nonempty_dict_count = 0
    for key, value in summary.items():
        if isinstance(value, list):
            limit = _RETAINED_LANE_LIMITS.get(key)
            if limit is None:
                omitted_nonempty_lane_count += bool(value)
                continue
            if key == "items":
                retained = [
                    item
                    for item in value
                    if not isinstance(item, dict) or item.get("status") != "done"
                ]
            else:
                retained = value
            compact[key] = [_compact_item(item) for item in retained[:limit]]
            if len(retained) > limit:
                compacted_lanes[key] = {"shown": limit, "total": len(retained)}
            continue
        if isinstance(value, dict):
            if key in _RETAINED_DICTS:
                compact[key] = value
            else:
                omitted_nonempty_dict_count += bool(value)
            continue
        compact[key] = value
    compact["payload_compaction"] = {
        "schema_version": AGENT_LANE_TODO_LIST_SUMMARY_SCHEMA_VERSION,
        "role": role,
        "item_limit": AGENT_LANE_TODO_LIST_ITEM_LIMIT,
        "item_text_limit": AGENT_LANE_TODO_LIST_TEXT_LIMIT,
        "terminal_items_in_hot_path": False,
        "compacted_lanes": compacted_lanes,
        "omitted_nonempty_lane_count": omitted_nonempty_lane_count,
        "omitted_nonempty_dict_count": omitted_nonempty_dict_count,
        "full_detail_cold_path": (
            "todo list with --role, --status, or --todo-id; "
            "todo list without --agent-id; or active state"
        ),
    }
    return compact


def compact_explicit_limit_todo_summary(
    summary: dict[str, Any],
    *,
    role: str,
    item_limit: int,
) -> dict[str, Any]:
    """Bound every item-bearing lane for the explicit-limit cold path."""
    compact: dict[str, Any] = {}
    compacted_lanes: dict[str, dict[str, int]] = {}
    omitted_nonempty_dict_count = 0
    for key, value in summary.items():
        if isinstance(value, list):
            compact[key] = [_compact_item(item) for item in value[:item_limit]]
            if len(value) > item_limit:
                compacted_lanes[key] = {
                    "shown": item_limit,
                    "total": len(value),
                }
            continue
        if isinstance(value, dict):
            if key in _RETAINED_DICTS:
                compact[key] = value
            else:
                omitted_nonempty_dict_count += bool(value)
            continue
        compact[key] = value
    compact["payload_compaction"] = {
        "schema_version": EXPLICIT_LIMIT_TODO_LIST_SUMMARY_SCHEMA_VERSION,
        "view": EXPLICIT_LIMIT_COLD_PATH_VIEW,
        "role": role,
        "item_limit": item_limit,
        "item_text_limit": AGENT_LANE_TODO_LIST_TEXT_LIMIT,
        "compacted_lanes": compacted_lanes,
        "omitted_nonempty_dict_count": omitted_nonempty_dict_count,
        "full_detail_cold_path": "todo list without --limit or active state",
    }
    return compact


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
