from __future__ import annotations

from typing import Any

from .contract import normalize_todo_task_class, parse_todo_metadata_line

from ..effect_runtime import effect_runtime_result
from .active_state_editing import (
    COMPLETED_WORK_ARCHIVE_HEADING,
    TODO_SECTION_HEADINGS,
    insert_archive_blocks,
    section_bounds,
    todo_blocks,
)

DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE = 12
DEFAULT_COMPLETED_TODO_ARCHIVE_HEADROOM = 2
ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE = max(
    0, DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE - DEFAULT_COMPLETED_TODO_ARCHIVE_HEADROOM
)
COMPLETED_TODO_ARCHIVE_COMMAND_TEMPLATE = (
    "loopx todo archive-completed --goal-id <goal-id> "
    "--max-active-done {max_active_done} --execute"
)


def completed_todo_archive_command_template(max_active_done: int) -> str:
    return COMPLETED_TODO_ARCHIVE_COMMAND_TEMPLATE.format(max_active_done=max_active_done)


def completed_todo_count(todo_summary: dict[str, Any] | None) -> int:
    """Decode explicit completions from the v0 terminal-count projection.

    ``todo_summary_v0.done_count`` intentionally includes deferred Todos because
    both states are terminal for scheduling.  Completed-work compaction has a
    narrower contract and must remove the deferred lane before applying archive
    pressure.
    """

    if not isinstance(todo_summary, dict):
        return 0
    try:
        terminal_count = int(todo_summary["done_count"])
        deferred_count = int(todo_summary["deferred_count"])
    except (KeyError, TypeError, ValueError):
        return 0
    return max(0, terminal_count - deferred_count)


def completed_todo_archive_warning(
    agent_todos: dict[str, Any] | None,
    *,
    max_active_done_todos: int = DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE,
) -> dict[str, Any] | None:
    if not isinstance(agent_todos, dict):
        return None
    done_count = completed_todo_count(agent_todos)
    if done_count <= max_active_done_todos:
        return None
    try:
        open_count = int(agent_todos.get("open_count") or 0)
    except (TypeError, ValueError):
        open_count = 0
    archive_keep_count = max(
        0,
        max_active_done_todos - DEFAULT_COMPLETED_TODO_ARCHIVE_HEADROOM,
    )
    return {
        "kind": "completed_agent_todo_archive_required",
        "requires_archive": True,
        "archive_section": COMPLETED_WORK_ARCHIVE_HEADING,
        "active_done_count": done_count,
        "active_open_count": open_count,
        "max_active_done_count": max_active_done_todos,
        "default_archive_keep_count": archive_keep_count,
        "archive_command_template": completed_todo_archive_command_template(archive_keep_count),
        "recommended_action": (
            "move older completed Agent Todo entries into a dedicated Completed Work Archive "
            "until the active Agent Todo section keeps only current open work and a small recent-done tail"
        ),
    }


def archive_completed_todo_lines(
    lines: list[str],
    *,
    role: str = "agent",
    max_active_done: int = DEFAULT_MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE,
) -> dict[str, Any]:
    if role not in TODO_SECTION_HEADINGS:
        raise ValueError("todo role must be one of: user, agent")
    if max_active_done < 0:
        raise ValueError("max_active_done must be non-negative")

    updated_lines = list(lines)
    bounds = section_bounds(updated_lines, role)
    section = bounds[2] if bounds else TODO_SECTION_HEADINGS[role]
    moved_blocks: list[list[str]] = []
    active_done_count = 0
    moved_count = 0
    kept_done_count = 0
    retained_standing_decision_count = 0

    if bounds:
        blocks = todo_blocks(updated_lines, bounds[0], bounds[1], role=role, source_section=section)
        selection = effect_runtime_result(
            "todo.archive.select",
            {
                "role": role,
                "max_active_done": max_active_done,
                "todos": [
                    {**block, "role": role, "archive_state": "active"}
                    for block in blocks
                ],
            },
        )
        if not isinstance(selection, dict) or selection.get("schema_version") != (
            "loopx_coordination_todo_archive_selection_v0"
        ):
            raise RuntimeError("typed Todo archive selector returned an invalid result")
        moved_todo_ids = selection.get("moved_todo_ids")
        if not isinstance(moved_todo_ids, list) or not all(
            isinstance(todo_id, str) and todo_id for todo_id in moved_todo_ids
        ):
            raise RuntimeError("typed Todo archive selector returned invalid Todo ids")
        blocks_by_id = {str(block["todo_id"]): block for block in blocks}
        if len(blocks_by_id) != len(blocks) or any(
            todo_id not in blocks_by_id for todo_id in moved_todo_ids
        ):
            raise RuntimeError("typed Todo archive selection does not match the parsed batch")
        blocks_to_move = [blocks_by_id[todo_id] for todo_id in moved_todo_ids]
        active_done_count = int(selection["active_done_before"])
        kept_done_count = int(selection["active_done_after"])
        move_count = int(selection["moved_count"])
        retained_standing_decision_count = int(
            selection["retained_standing_decision_count"]
        )
        if move_count != len(blocks_to_move):
            raise RuntimeError("typed Todo archive selector returned inconsistent counts")
        move_starts = {int(block["start"]) for block in blocks_to_move}
        for block in blocks_to_move:
            moved_lines = updated_lines[int(block["start"]) : int(block["end"])]
            # Preserve the section identity before moving into the mixed archive.
            # Append a narrow metadata line; do not reserialize/drop unknown
            # fields in the original receipt while performing a storage move.
            roles = [metadata["role"] for line in moved_lines
                if (metadata := parse_todo_metadata_line(line)) and "role" in metadata]
            if any(value != role for value in roles):
                raise ValueError("Todo archive source role contradicts its active section")
            retained = [] if roles else [f"role={role}"]
            if role == "agent" and block.get("task_class") is None:
                task_class = normalize_todo_task_class(None,
                    text=str(block.get("text") or ""), action_kind=block.get("action_kind"))
                retained.append(f"task_class={task_class}")
            if retained:
                insert_at = len(moved_lines)
                while insert_at > 1 and not moved_lines[insert_at - 1].strip():
                    insert_at -= 1
                moved_lines.insert(insert_at, f"  <!-- loopx:todo {' '.join(retained)} -->")
            moved_blocks.append(moved_lines)
        if move_starts:
            new_lines: list[str] = []
            index = 0
            while index < len(updated_lines):
                if index in move_starts:
                    matching = next(
                        block for block in blocks_to_move if int(block["start"]) == index
                    )
                    index = int(matching["end"])
                    while (
                        new_lines
                        and not new_lines[-1].strip()
                        and index < len(updated_lines)
                        and not updated_lines[index].strip()
                    ):
                        index += 1
                    continue
                new_lines.append(updated_lines[index])
                index += 1
            updated_lines = new_lines
            insert_archive_blocks(updated_lines, moved_blocks)
            moved_count = move_count

    return {
        "lines": updated_lines,
        "changed": moved_count > 0,
        "role": role,
        "section": section,
        "archive_section": COMPLETED_WORK_ARCHIVE_HEADING,
        "active_done_before": active_done_count,
        "active_done_after": kept_done_count,
        "max_active_done": max_active_done,
        "moved_count": moved_count,
        "retained_standing_decision_count": retained_standing_decision_count,
    }
