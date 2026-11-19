from __future__ import annotations

from pathlib import Path
from typing import Any

from ...materials import extract_review_materials
from ...orchestration import compact_orchestration_policy
from .contract import (
    normalize_todo_id,
    TODO_TASK_PATTERN,
)
from .machine_region import find_todo_source_regions, visible_markdown_lines
from .todo_block_codec import decode_todo_blocks
from .todo_summary import (
    MAX_STATUS_TODOS_PER_ROLE,
)
from ..runtime.time import now_utc_iso


REQUIRED_TODO_SOURCE_SECTIONS: tuple[tuple[str, str], ...] = (("agent", "Agent Todo"),)


def active_state_incomplete_warning(state_text: str) -> dict[str, Any] | None:
    """Identify required Todo source headings absent from legacy Markdown.

    A present-but-empty heading is a valid empty projection. A missing heading
    is not: callers cannot distinguish a truncated source from a goal with no
    work, so repairs stay visible to status and Todo readback.
    """
    lines = state_text.splitlines()
    visible = visible_markdown_lines(lines)
    present_roles = {
        region.role
        for region in find_todo_source_regions(lines, visible=visible)
    }
    missing_sections = [
        (role, heading)
        for role, heading in REQUIRED_TODO_SOURCE_SECTIONS
        if role not in present_roles
    ]
    if not missing_sections:
        return None
    return {
        "kind": "state_incomplete",
        "source": "markdown_active_state",
        "roles": [role for role, _heading in missing_sections],
        "required_roles": [role for role, _heading in REQUIRED_TODO_SOURCE_SECTIONS],
        "missing_sections": [heading for _role, heading in missing_sections],
        "recommended_action": (
            "restore the missing Agent Todo section from the last known good "
            "active state, then rerun `loopx status`"
        ),
    }


def parse_todo_source(
    state_text: str,
    *,
    goal: dict[str, Any] | None = None,
    state_path: Path | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, str | None]]:
    """Decode full source rows; attention summaries compact only after this read."""
    source_sections: dict[str, str | None] = {"user": None, "agent": None}
    items: dict[str, list[dict[str, Any]]] = {"user": [], "agent": []}
    archive_items: list[dict[str, Any]] = []
    lines = state_text.splitlines()
    visible = visible_markdown_lines(lines)
    for region in find_todo_source_regions(lines, visible=visible):
        archive = region.role == "archive"
        target = archive_items if archive else items[region.role]
        if not archive and source_sections[region.role] is None:
            source_sections[region.role] = region.heading
        for block in decode_todo_blocks(
            lines, region.start, region.body_end, visible=visible, text_limit=None,
        ):
            todo = {"archive_state": "archive" if archive else "active",
                    "source_section": region.heading if archive else source_sections[region.role],
                    **({} if archive else {"role": region.role}),
                    **{key: value for key, value in block.items() if key not in {"start", "end"}},
                    "index": len(target) + 1}
            if goal is not None:
                match = TODO_TASK_PATTERN.match(lines[block["start"]])
                assert match is not None  # The shared decoder only emits matched task lines.
                materials = extract_review_materials(match.group(2), goal=goal, state_path=state_path)
                if materials:
                    todo["review_materials"] = materials
            target.append(todo)
    return items, archive_items, source_sections


def parse_active_state_todos(
    state_text: str,
    *,
    goal: dict[str, Any] | None = None,
    state_path: Path | None = None,
    preferred_todo_ids: set[str] | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    item_limit: int | None = MAX_STATUS_TODOS_PER_ROLE,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    resume_evaluated_at = evaluated_at or now_utc_iso()
    orchestration = compact_orchestration_policy(
        goal.get("spawn_policy") if isinstance(goal, dict) else None
    )
    include_task_orchestration_authority = bool(
        orchestration.get("mode") == "multi_subagent"
        and orchestration.get("spawn_allowed") is True
        and int(orchestration.get("max_children") or 0) > 0
    )
    items, archive_items, source_sections = parse_todo_source(state_text, goal=goal, state_path=state_path)
    from .goal_todo_projection import source_todo_fields
    from .standing_decision import standing_decision_source_items

    lineage = [*items["user"], *items["agent"],
        *[item for item in archive_items if normalize_todo_id(item.get("todo_id"))]]
    archived_decisions = [item for item in archive_items if item.get("role") == "user"]
    result = source_todo_fields([
        {"role": role, "source_section": source_sections[role], "items": items[role],
         "include_empty_source": source_sections[role] is not None} for role in ("user", "agent")],
        lineage=lineage, rollout_events=rollout_events, available_capabilities=available_capabilities,
        preferred_todo_ids=preferred_todo_ids, item_limit=item_limit, evaluated_at=resume_evaluated_at,
        archived_items=archive_items,
        standing_items=standing_decision_source_items([*items["user"], *archived_decisions]),
        legacy_source_order=not archived_decisions,
        include_task_orchestration_authority=include_task_orchestration_authority,
        vision_runs=(goal or {}).get("latest_runs"))
    return result
