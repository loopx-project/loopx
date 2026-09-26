from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..coordination.local_authority import (
    canonical_todo_summary_fields,
    read_canonical_todos_if_promoted,
)

from .succession_warning import public_todo_summary

def _redacted_status_todo_fields(fields: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(fields)
    for key in ("user_todos", "agent_todos"):
        group = redacted.get(key)
        if not isinstance(group, dict):
            continue
        group_copy = public_todo_summary(group)
        items: list[Any] = []
        for item in group_copy.get("items") or []:
            if not isinstance(item, dict):
                items.append(item)
                continue
            item_copy = dict(item)
            materials = item_copy.get("review_materials")
            if isinstance(materials, list):
                redacted_materials = []
                for material in materials:
                    if not isinstance(material, dict):
                        redacted_materials.append(material)
                        continue
                    material_copy = dict(material)
                    material_copy.pop("resolved_path", None)
                    redacted_materials.append(material_copy)
                item_copy["review_materials"] = redacted_materials
            items.append(item_copy)
        group_copy["items"] = items
        redacted[key] = group_copy
    return redacted


def redacted_status_todo_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return _redacted_status_todo_fields(fields)


def active_state_todo_fields(
    goal: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    rollout_events: Sequence[Mapping[str, Any]] | None = None,
    resolve_goal_local_path: Callable[..., Path | None],
    active_state_next_action_entries: Callable[..., list[str]],
    active_next_action_todo_ids: Callable[[str], set[str]],
    load_rollout_events: Callable[..., list[dict[str, Any]]],
    rollout_event_log_path: Callable[[Path, str], Path],
    max_todo_index_rollout_events_per_goal: int,
    parse_active_state_todos: Callable[..., dict[str, Any]],
    parse_issue_meta_surface: Callable[[str], dict[str, Any] | None],
    backlog_hygiene_warning: Callable[..., dict[str, Any] | None],
    completed_todo_archive_warning: Callable[[dict[str, Any] | None], dict[str, Any] | None],
    state_projection_gap_warning: Callable[..., dict[str, Any] | None],
    redacted_status_todo_fields: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    todo_field_redactor = redacted_status_todo_fields or _redacted_status_todo_fields
    goal_id = str(goal.get("id") or "").strip()
    # Inspect authority before the display file. A missing/stale projection is
    # not an empty Todo collection, and an unavailable provider must fail closed.
    canonical = (
        read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
        if runtime_root is not None and goal_id else None
    )
    state_path = resolve_goal_local_path(goal.get("state_file"), goal, fallback_base=Path.cwd())
    if canonical is None and state_path is not None:
        from ..goals.legacy_event_source import require_no_legacy_todo_events
        require_no_legacy_todo_events(goal, state_path=state_path)
    if canonical is None and (state_path is None or not state_path.exists()):
        return {}
    try:
        state_text = state_path.read_text(encoding="utf-8") if state_path is not None else ""
    except OSError:
        if canonical is None:
            return {}
        state_text = ""
    except UnicodeError:
        if canonical is None:
            raise
        state_text = ""
    next_action_entries = active_state_next_action_entries(state_text, limit=3)
    preferred_todo_ids: set[str] = set()
    for entry in next_action_entries:
        preferred_todo_ids.update(active_next_action_todo_ids(entry))
    events = (
        [dict(event) for event in rollout_events]
        if rollout_events is not None
        else []
    )
    if rollout_events is None and runtime_root is not None and goal_id:
        events = load_rollout_events(
            rollout_event_log_path(runtime_root, goal_id),
            limit=max_todo_index_rollout_events_per_goal,
        )
    if canonical is not None:
        fields = canonical_todo_summary_fields(
            canonical["todos"],
            rollout_events=events,
            goal_acceptance_contract=canonical.get("goal_acceptance_contract"),
            goal_acceptance_work_guards=canonical.get(
                "goal_acceptance_work_guards"
            ),
        )
        # Canonical observation/successor transactions now support current
        # lease proof. Scheduling exposes due work; mutation admission still
        # validates the caller's proof and never falls back to the old writer.
    else:
        fields = parse_active_state_todos(
            state_text,
            goal=goal,
            state_path=state_path,
            preferred_todo_ids=preferred_todo_ids,
            rollout_events=events,
        )
    issue_meta_surface = parse_issue_meta_surface(state_text)
    if issue_meta_surface:
        fields["issue_meta_surface"] = issue_meta_surface
    if next_action_entries:
        fields["active_state_next_action"] = next_action_entries[0]
        fields["active_state_next_action_entries"] = next_action_entries
    warning = backlog_hygiene_warning(
        state_text,
        agent_todos=fields.get("agent_todos") if isinstance(fields.get("agent_todos"), dict) else None,
    )
    if warning:
        fields["backlog_hygiene_warning"] = warning
    archive_warning = completed_todo_archive_warning(
        fields.get("agent_todos") if isinstance(fields.get("agent_todos"), dict) else None
    )
    if archive_warning:
        fields["completed_todo_archive_warning"] = archive_warning
    projection_gap = state_projection_gap_warning(
        state_text,
        user_todos=fields.get("user_todos") if isinstance(fields.get("user_todos"), dict) else None,
        agent_todos=fields.get("agent_todos") if isinstance(fields.get("agent_todos"), dict) else None,
    )
    if projection_gap:
        fields["state_projection_gap"] = projection_gap
    if fields:
        fields = todo_field_redactor(fields)
    return fields
