from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..control_plane.projects.registry_codec import load_registry
from ..control_plane.todos.todo_summary import canonical_todo_read_record
from ..history import goal_registry_digest
from ..registry import find_registry_goal
from ..status import active_state_todo_fields


def _goal_status_row(
    payload: Mapping[str, object], goal_id: str
) -> dict[str, object] | None:
    history = payload.get("run_history")
    goals = history.get("goals") if isinstance(history, Mapping) else None
    if not isinstance(goals, list):
        return None
    return next(
        (
            item
            for item in goals
            if isinstance(item, dict) and item.get("id") == goal_id
        ),
        None,
    )


def _goal_attention_item(
    payload: Mapping[str, object], goal_id: str
) -> dict[str, object]:
    queue = payload.get("attention_queue")
    items = queue.get("items") if isinstance(queue, Mapping) else None
    matches = [
        item
        for item in items or ()
        if isinstance(item, dict) and item.get("goal_id") == goal_id
    ]
    return matches[0] if len(matches) == 1 else {}


def _todo_source_projection(fields: Mapping[str, object]) -> dict[str, object]:
    projection: dict[str, object] = {}
    for role in ("agent", "user"):
        summary = fields.get(f"{role}_todos")
        items = summary.get("items") if isinstance(summary, Mapping) else None
        source_summary = {
            key: value
            for key, value in summary.items()
            if not isinstance(value, list)
            and key
            not in {
                "advancement_frontier_revision_index",
                "payload_compaction",
            }
        } if isinstance(summary, Mapping) else {}
        source_summary["items"] = [
            canonical_todo_read_record(item)
            for item in items or ()
            if isinstance(item, dict)
        ]
        projection[f"{role}_todos"] = source_summary
    for key in (
        "active_state_next_action_entries",
        "next_action_basis",
        "recommendation_context",
        "standing_decision_authority",
    ):
        if key in fields:
            projection[key] = fields[key]
    return projection


def cached_goal_projection_miss_reason(
    payload: Mapping[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
) -> str | None:
    """Return why a scheduler must reject this cached Goal projection."""

    cached_goal = _goal_status_row(payload, goal_id)
    if cached_goal is None:
        return "goal_registry_changed"
    registry = load_registry(registry_path)
    current_goal = find_registry_goal(registry, goal_id)
    if current_goal is None or cached_goal.get(
        "registry_goal_digest"
    ) != goal_registry_digest(current_goal):
        return "goal_registry_changed"
    try:
        cached_projection = _todo_source_projection(
            _goal_attention_item(payload, goal_id)
        )
    except ValueError:
        return "invalid_goal_todo_projection"
    current_fields = active_state_todo_fields(
        current_goal,
        runtime_root=runtime_root,
        registry_path=registry_path,
    )
    if cached_projection != _todo_source_projection(current_fields):
        return "goal_todo_projection_changed"
    return None
