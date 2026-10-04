"""Read-only registry-to-state path resolution, re-exported by state_refresh."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...registry import registry_goals, resolve_state_file


def resolve_goal_state(
    *,
    registry: dict[str, Any],
    goal_id: str,
    project_override: Path | None,
    state_file_override: Path | None,
) -> tuple[dict[str, Any] | None, Path | None, Path]:
    goal = next((item for item in registry_goals(registry) if str(item.get("id")) == goal_id), None)
    project = project_override.expanduser().resolve() if project_override else None
    if project is None and goal and goal.get("repo"):
        project = Path(str(goal.get("repo"))).expanduser()

    registered_state_file = (
        resolve_state_file(project, goal.get("state_file"))
        if project and goal and goal.get("state_file")
        else None
    )
    state_file = state_file_override.expanduser() if state_file_override else None
    if state_file is None and goal:
        state_file = registered_state_file
    if state_file is None:
        raise ValueError("state file is required when the goal is not resolvable from registry")
    if not state_file.is_absolute():
        if project is None:
            raise ValueError("relative state file requires --project or registry repo")
        state_file = project / state_file
    state_file = state_file.resolve()
    if state_file_override is not None:
        if project is None:
            raise ValueError("--state-file override requires --project or a registry goal with repo")
        registered_resolved = (
            registered_state_file.resolve() if registered_state_file is not None else None
        )
        if state_file != registered_resolved and not state_file.is_relative_to(project):
            raise ValueError(
                f"--state-file {state_file} escapes project root {project}"
            )
    return goal, project, state_file
