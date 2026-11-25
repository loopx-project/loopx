from __future__ import annotations

from pathlib import Path
from typing import Any

from ..hook_runtime.request_scope import load_scoped_registry
from ...history import load_registry
from ...paths import registry_project_root
from ...registry import registry_goals
from .runtime_projection_route import resolve_goal_source_runtime_route


def resolve_goal_project_route(
    *,
    registry_path: Path,
    goal_id: str,
    project_override: str | Path | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Resolve a goal-scoped control-plane command to its canonical project.

    Shared registries are read models.  A command invoked from a linked worktree
    must therefore follow ``goal.source_registry`` before resolving relative,
    local-private configuration pointers.  An explicit project remains a
    consistency assertion; it must not silently replace the registered route.
    """

    route = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
    )
    source_registry = Path(str(route["source_registry"])).expanduser().resolve()

    def load_goal(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload = load_scoped_registry(path, loader=load_registry)
        matched = next(
            (
                item
                for item in registry_goals(payload)
                if str(item.get("id") or "") == goal_id
            ),
            None,
        )
        return payload, matched if isinstance(matched, dict) else None

    _, goal = load_goal(source_registry)
    if goal is None and project_override is not None:
        requested = Path(project_override).expanduser().resolve()
        project_registry = requested / ".loopx" / "registry.json"
        if project_registry.is_file() and project_registry.resolve() != source_registry:
            route = resolve_goal_source_runtime_route(
                registry_path=project_registry,
                goal_id=goal_id,
            )
            source_registry = Path(str(route["source_registry"])).expanduser().resolve()
            _, goal = load_goal(source_registry)
    if not isinstance(goal, dict):
        raise ValueError(f"goal_id not found in canonical source registry: {goal_id}")
    raw_repo = str(goal.get("repo") or "").strip()
    if not raw_repo:
        raise ValueError("connected goal repository is required")
    project = Path(raw_repo).expanduser()
    if not project.is_absolute():
        project = registry_project_root(source_registry) / project
    project = project.resolve()
    if project_override is not None:
        requested = Path(project_override).expanduser().resolve()
        if requested != project:
            raise ValueError(
                "--project must match the canonical connected goal repository"
            )
    return goal, project, route
