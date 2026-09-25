from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from loopx.event_sourced_state import (
    AppendOnlyStateEventStore,
    StateEventError,
    build_state_projection,
    render_active_state_sections,
)


DEFAULT_STATE_EVENT_LOG_BASENAME = "events.jsonl"
STATE_EVENT_PROJECTION_SCHEMA_VERSION = "event_sourced_state_status_projection_v0"
STATE_EVENT_READ_WARNING_SCHEMA_VERSION = "event_sourced_state_read_warning_v0"

ResolveGoalLocalPath = Callable[..., Optional[Path]]
ParseActiveStateTodos = Callable[..., dict[str, Any]]


def state_event_log_candidates(
    goal: dict[str, Any],
    *,
    state_path: Path,
    resolve_goal_local_path: ResolveGoalLocalPath,
    event_log_basename: str = DEFAULT_STATE_EVENT_LOG_BASENAME,
) -> list[Path]:
    candidates: list[Path] = []
    for key in ("state_event_log", "state_events_file", "event_log"):
        resolved = resolve_goal_local_path(goal.get(key), goal, fallback_base=state_path.parent)
        if resolved is not None:
            candidates.append(resolved)
    candidates.append(state_path.with_name(event_log_basename))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def active_state_event_projection_fields(
    goal: dict[str, Any],
    *,
    state_path: Path,
    resolve_goal_local_path: ResolveGoalLocalPath,
    parse_active_state_todos: ParseActiveStateTodos,
    preferred_todo_ids: set[str] | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    item_limit: int | None = None,
    event_log_basename: str = DEFAULT_STATE_EVENT_LOG_BASENAME,
) -> dict[str, Any]:
    goal_id = str(goal.get("id") or "").strip()
    first_warning: dict[str, Any] | None = None
    for event_log_path in state_event_log_candidates(
        goal,
        state_path=state_path,
        resolve_goal_local_path=resolve_goal_local_path,
        event_log_basename=event_log_basename,
    ):
        if not event_log_path.exists():
            continue
        try:
            events = AppendOnlyStateEventStore(event_log_path).load()
            if not events:
                continue
            projection = build_state_projection(events, goal_id=goal_id or None)
            projection_markdown = render_active_state_sections(projection)
            fields = parse_active_state_todos(
                projection_markdown,
                goal=goal,
                state_path=state_path,
                preferred_todo_ids=preferred_todo_ids,
                rollout_events=rollout_events,
                item_limit=item_limit,
            )
        except (OSError, StateEventError) as exc:
            if first_warning is None:
                first_warning = {
                    "state_event_projection_warning": {
                        "schema_version": STATE_EVENT_READ_WARNING_SCHEMA_VERSION,
                        "source": "event_log",
                        "event_log": event_log_path.name,
                        "fallback": "markdown_active_state",
                        "reason": type(exc).__name__,
                    }
                }
            continue
        if fields:
            fields["state_event_projection"] = {
                "schema_version": STATE_EVENT_PROJECTION_SCHEMA_VERSION,
                "source": "event_log",
                "event_log": event_log_path.name,
                "source_event_count": projection.get("source_event_count"),
                "source_checksum": projection.get("source_checksum"),
                "last_event_id": projection.get("last_event_id"),
                "last_append_sequence": projection.get("last_append_sequence"),
                "projection_version": projection.get("projection_version"),
            }
            return fields
    return first_warning or {}
