from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result
from .active_state_editing import section_bounds, todo_blocks
from .contract import (
    TODO_STATUS_OPEN,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_status,
)
from .todo_semantics import todo_priority_label


TODO_NEXT_ACTION_REQUEST_SCHEMA = "loopx_todo_next_action_transition_v1"
TODO_NEXT_ACTION_RESULT_SCHEMA = "loopx_todo_next_action_result_v0"
NEXT_ACTION_HEADING = "## Next Action"


def _next_action_projection_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() == NEXT_ACTION_HEADING
        ),
        None,
    )
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return start, end


def _agent_todo_snapshots(lines: list[str]) -> list[dict[str, Any]]:
    bounds = section_bounds(lines, "agent")
    if bounds is None:
        return []
    start, end, section = bounds
    snapshots: list[dict[str, Any]] = []
    for block in todo_blocks(
        lines,
        start,
        end,
        role="agent",
        source_section=section,
    ):
        todo_id = normalize_todo_id(block.get("todo_id"))
        if not todo_id:
            raise RuntimeError("Agent Todo projection produced an invalid todo_id")
        snapshots.append(
            {
                "todo_id": todo_id,
                "status": normalize_todo_status(block.get("status"))
                or TODO_STATUS_OPEN,
                "task_class": str(block.get("task_class") or "").strip() or None,
                "priority": todo_priority_label(block, text_mode="prefix"),
                "claimed_by": str(block.get("claimed_by") or "").strip() or None,
                "text": str(block.get("text") or "").strip(),
                "index": int(block.get("index") or 0),
                "completion_continuation": (
                    str(block.get("completion_continuation") or "").strip() or None
                ),
                "successor_todo_ids": normalize_todo_id_list(
                    block.get("successor_todo_ids")
                ),
            }
        )
    return snapshots


def _materialized_todo_ids(lines: list[str]) -> list[str]:
    todo_ids: list[str] = []
    for role in ("user", "agent"):
        bounds = section_bounds(lines, role)
        if bounds is None:
            continue
        start, end, section = bounds
        for block in todo_blocks(
            lines,
            start,
            end,
            role=role,
            source_section=section,
        ):
            todo_id = normalize_todo_id(block.get("todo_id"))
            if todo_id and todo_id not in todo_ids:
                todo_ids.append(todo_id)
    return todo_ids


def _apply_transition(
    lines: list[str],
    *,
    operation: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    projection_bounds = _next_action_projection_bounds(lines)
    if projection_bounds is None:
        projection_lines: list[str] = []
    else:
        start, end = projection_bounds
        projection_lines = lines[start:end]
    result = effect_runtime_result(
        "todo.next_action.transition",
        {
            "schema_version": TODO_NEXT_ACTION_REQUEST_SCHEMA,
            "operation": operation,
            "lines": projection_lines,
            **dict(params),
        },
    )
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Todo Next Action result must be an object")
    if (
        result.get("schema_version") != TODO_NEXT_ACTION_RESULT_SCHEMA
        or result.get("operation") != operation
        or not isinstance(result.get("changed"), bool)
        or not isinstance(result.get("matched"), bool)
        or not isinstance(result.get("outcome"), str)
    ):
        raise RuntimeError("TypeScript Todo Next Action result shape mismatch")
    updated_lines = result.get("lines")
    if not isinstance(updated_lines, list) or not all(
        isinstance(line, str) for line in updated_lines
    ):
        raise RuntimeError("TypeScript Todo Next Action lines shape mismatch")
    changed = updated_lines != projection_lines
    if changed is not result["changed"]:
        raise RuntimeError("TypeScript Todo Next Action changed flag mismatch")
    if changed:
        if projection_bounds is None:
            raise RuntimeError(
                "TypeScript Todo Next Action changed a missing projection"
            )
        start, end = projection_bounds
        lines[start:end] = updated_lines
    materialized_result = dict(result)
    materialized_result["lines"] = list(lines)
    return materialized_result


def reconcile_added_todo_next_action(
    lines: list[str],
    *,
    added_todo_id: str,
) -> bool:
    """Let higher-priority task work replace a lower-priority typed route."""

    normalized_todo_id = normalize_todo_id(added_todo_id)
    if not normalized_todo_id:
        raise ValueError("Next Action reconciliation requires a valid added todo_id")
    result = _apply_transition(
        lines,
        operation="reconcile_added",
        params={
            "todo_id": normalized_todo_id,
            "agent_todos": _agent_todo_snapshots(lines),
        },
    )
    return bool(result["changed"])


def apply_added_todo_next_action(
    lines: list[str],
    *,
    role: str,
    add_result: dict[str, Any],
) -> bool:
    """Return whether todo addition or its typed Next Action projection changed."""

    changed = bool(add_result["changed"])
    todo_id = str(add_result.get("todo_id") or "")
    if (
        not changed
        or role != "agent"
        or add_result.get("task_class") != "advancement_task"
        or not todo_id
    ):
        return changed
    return reconcile_added_todo_next_action(lines, added_todo_id=todo_id) or changed


def settle_completed_todo_next_action(
    lines: list[str],
    *,
    completed_todo_id: str,
) -> bool:
    """Reconcile the generated Next Action from the final Todo transaction state."""

    normalized_todo_id = normalize_todo_id(completed_todo_id)
    if not normalized_todo_id:
        raise ValueError("Next Action settlement requires a valid completed todo_id")
    result = _apply_transition(
        lines,
        operation="settle_completion",
        params={
            "todo_id": normalized_todo_id,
            "agent_todos": _agent_todo_snapshots(lines),
            "materialized_todo_ids": _materialized_todo_ids(lines),
        },
    )
    return bool(result["changed"])


def bound_next_action_todo_ids(state_text: str) -> set[str]:
    """Decode the existing typed binding, without inferring IDs from prose."""
    if "loopx:next-action" not in state_text:
        return set()
    lines = state_text.splitlines()
    bounds = _next_action_projection_bounds(lines)
    if bounds is None:
        return set()
    result = effect_runtime_result("todo.next_action.binding", {"lines": lines[bounds[0]:bounds[1]]})
    return {result["todo_id"]} if result.get("todo_id") else set()
