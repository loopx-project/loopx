"""Metadata-only source route shared by named Goal settings owners."""
from pathlib import Path

from ..control_plane.projects.registry_codec import load_project_registry
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route


def source_registry_for_goal(invoked: Path, goal_id: str) -> Path:
    payload = load_project_registry(invoked)
    if payload.get("profile_id") == "source_session_v1":
        return invoked
    route = resolve_goal_source_runtime_route(registry_path=invoked, goal_id=goal_id)
    path = route.get("source_registry")
    if not isinstance(path, str) or not path:
        raise ValueError("Goal source authority could not be resolved")
    return Path(path)
