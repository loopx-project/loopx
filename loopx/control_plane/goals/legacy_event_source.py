"""Admission for retired Todo event sources; no event projection or writer."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .path_resolution import resolve_goal_local_path

def state_event_log_candidates(
    goal: dict[str, Any],
    *,
    state_path: Path,
) -> list[Path]:
    candidates: list[Path] = []
    for key in ("state_event_log", "state_events_file", "event_log"):
        resolved = resolve_goal_local_path(goal.get(key), goal, fallback_base=state_path.parent)
        if resolved is not None:
            candidates.append(resolved)
    candidates.append(state_path.with_name("events.jsonl"))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


class RetiredTodoEventSourceError(ValueError):
    reason_code = "legacy_todo_event_source_retired"


def require_no_legacy_todo_events(goal: dict[str, Any], *, state_path: Path) -> None:
    for path in state_event_log_candidates(goal, state_path=state_path):
        if path.exists() and path.stat().st_size:
            raise RetiredTodoEventSourceError(
                "legacy_todo_event_source_retired: preserve the legacy event file and "
                "export its Todo records with a compatible older release before migration; "
                "this release refuses to substitute Markdown for event-owned Todos"
            )
