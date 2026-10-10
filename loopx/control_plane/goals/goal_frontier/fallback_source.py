"""One source snapshot, bounded transport, no fallback-specific state authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....history import load_registry
from ....state_refresh import resolve_goal_state
from ...coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ...effect_runtime import EffectRuntimeRemoteError
from ...todos.active_state_todo_parser import parse_todo_source
from .fallback_disposition import (
    FallbackTodoReadState, FallbackTodoSource, parse_fallback_declarations,
    select_fallback_source_items,
)
from .semantic_history import latest_agent_vision_from_status_payload


def read_fallback_source_snapshot(
    *, registry_path: Path, runtime_root: Path, goal_id: str,
) -> list[dict[str, Any]]:
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    if canonical is not None:
        # Failure after promotion must not consult the Markdown projection.
        return canonical["todos"]
    goal, _, state_file = resolve_goal_state(
        registry=load_registry(registry_path), goal_id=goal_id,
        project_override=None, state_file_override=None,
    )
    if goal is None:
        raise ValueError("fallback source goal is absent")
    groups, archived, _ = parse_todo_source(state_file.read_text(encoding="utf-8"))
    return [*groups["user"], *groups["agent"], *archived]


def live_fallback_authority_items(
    status_payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str | None,
) -> FallbackTodoSource:
    vision = latest_agent_vision_from_status_payload(
        status_payload, goal_id=goal_id, agent_id=agent_id,
    )
    requested = {
        todo_id for declaration in parse_fallback_declarations(vision)
        for todo_id in declaration.candidate_todo_ids
    }
    if not requested:
        return None
    try:
        source = read_fallback_source_snapshot(
            registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        )
    except (EffectRuntimeRemoteError, LocalCoordinationAuthorityUnavailable, OSError, ValueError):
        return FallbackTodoReadState.UNAVAILABLE
    return select_fallback_source_items(source, requested)
