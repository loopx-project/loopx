from __future__ import annotations

from pathlib import Path
from typing import Any

from ...materials import extract_review_materials
from ...orchestration import compact_orchestration_policy
from .contract import (
    normalize_todo_id,
    TODO_TASK_PATTERN,
)
from .standing_decision import build_standing_decision_authority
from .machine_region import find_todo_source_regions, visible_markdown_lines
from .todo_block_codec import decode_todo_blocks
from .todo_summary import (
    MAX_STATUS_TODOS_PER_ROLE,
    compact_todo_group,
    count_advancement_todos,
    normalize_todo_text,
)
from ..runtime.time import now_utc_iso


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
    result: dict[str, Any] = {}
    archived_resume_source_items = [
        item for item in archive_items if normalize_todo_id(item.get("todo_id"))
    ]
    resume_source_items = [*items["user"], *items["agent"], *archived_resume_source_items]
    user = compact_todo_group(
        items["user"],
        source_section=source_sections["user"],
        role="user",
        include_empty_source=source_sections["user"] is not None,
        preferred_todo_ids=preferred_todo_ids,
        resume_source_items=resume_source_items,
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
        item_limit=item_limit,
        include_task_orchestration_authority=include_task_orchestration_authority,
        evaluated_at=resume_evaluated_at,
    )
    agent = compact_todo_group(
        items["agent"],
        source_section=source_sections["agent"],
        role="agent",
        include_empty_source=source_sections["agent"] is not None,
        preferred_todo_ids=preferred_todo_ids,
        resume_source_items=resume_source_items,
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
        item_limit=item_limit,
        include_task_orchestration_authority=include_task_orchestration_authority,
        vision_runs=(goal or {}).get("latest_runs"),
        evaluated_at=resume_evaluated_at,
    )
    archived_advancement_done_count = count_advancement_todos(
        # Lossless source text must not widen legacy attention classification.
        [
            {**item, "text": normalize_todo_text(str(item.get("text") or ""))}
            for item in archive_items if item.get("done") is True
        ]
    )
    if agent and archived_advancement_done_count:
        agent["archived_advancement_done_count"] = archived_advancement_done_count
        agent["advancement_done_count"] = (
            int(agent.get("advancement_done_count") or 0)
            + archived_advancement_done_count
        )
    if user:
        result["user_todos"] = user
    if agent:
        result["agent_todos"] = agent
    archived_decisions = [item for item in archive_items if item.get("role") == "user"]
    standing_authority = build_standing_decision_authority(
        [*items["user"], *archived_decisions],
        # Separate sections are not a single chronological append log.
        legacy_source_order=not archived_decisions,
    )
    if standing_authority:
        result["standing_decision_authority"] = standing_authority
    return result
