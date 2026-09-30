from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_cross_runtime_file_lock
from ...registry import find_registry_goal
from ..goals.source_session_registry_state import current_goal_ref, guard_path
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)


@dataclass(frozen=True, slots=True)
class ShadowGoalScope:
    registry: dict[str, Any]
    goal: dict[str, Any]
    goal_ref: dict[str, str] | None

    @property
    def exact(self) -> bool:
        return self.goal_ref is not None


def _legacy_scope(registry: dict[str, Any], goal_id: str) -> ShadowGoalScope:
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, dict):
        raise ValueError(f"goal {goal_id!r} is not registered")
    return ShadowGoalScope(registry=registry, goal=goal, goal_ref=None)


def resolve_shadow_goal_scope(
    registry: dict[str, Any],
    *,
    goal_id: str,
) -> ShadowGoalScope:
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        return _legacy_scope(registry, goal_id)
    goal_ref, goal = current_goal_ref(registry, goal_id=goal_id)
    return ShadowGoalScope(
        registry=registry,
        goal=goal,
        goal_ref=goal_ref,
    )


@contextmanager
def shadow_goal_scope(
    registry_path: Path,
    *,
    goal_id: str,
) -> Iterator[ShadowGoalScope]:
    """Hold the source lifetime guard while an exact shadow effect runs."""

    path = registry_path.expanduser().resolve()
    registry = load_project_registry(path)
    if registry.get("profile_id") != SOURCE_SESSION_PROFILE_ID:
        yield resolve_shadow_goal_scope(registry, goal_id=goal_id)
        return
    with exclusive_cross_runtime_file_lock(
        guard_path(path, goal_id),
        operation="shadow_outbox_goal_lifetime",
    ):
        registry = load_project_registry(path)
        yield resolve_shadow_goal_scope(registry, goal_id=goal_id)
