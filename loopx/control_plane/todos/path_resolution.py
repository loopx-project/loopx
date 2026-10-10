"""Canonical active-state path resolution shared by Todo facade callers."""

from __future__ import annotations

from pathlib import Path

from ..projects.registry_codec import load_registry
from ..goals.state_resolution import resolve_goal_state


def resolve_todo_state_path(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
    require_existing: bool = True,
) -> tuple[Path | None, Path]:
    registry = load_registry(registry_path)
    goal, resolved_project, resolved_state_file = resolve_goal_state(
        registry=registry,
        goal_id=goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    if goal is None:
        raise ValueError(f"goal {goal_id!r} is not present in the registry")
    if require_existing and not resolved_state_file.exists():
        raise ValueError(f"active state file does not exist: {resolved_state_file}")
    return resolved_project, resolved_state_file


def resolve_todo_state(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
) -> tuple[Path | None, Path, str, list[str]]:
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    original = resolved_state_file.read_text(encoding="utf-8")
    return resolved_project, resolved_state_file, original, original.splitlines()
