from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ...file_lock import exclusive_cross_runtime_file_lock
from ..effect_runtime import effect_runtime_result
from ..goals.source_session_registry_state import exact_goal_ref, guard_path
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)


@dataclass(frozen=True, slots=True)
class CollaborationGoalScope:
    registry_path: Path
    goal_id: str
    goal: dict[str, Any]
    profile_id: str | None
    caller_goal_ref: dict[str, str] | None
    current_goal_ref: dict[str, str] | None

    @property
    def exact(self) -> bool:
        return self.profile_id == SOURCE_SESSION_PROFILE_ID

    def target(self, agent_id: str) -> dict[str, Any]:
        if self.exact:
            return {
                "goal_ref": dict(self.caller_goal_ref or {}),
                "agent_id": agent_id,
            }
        return {"goal_id": self.goal_id, "agent_id": agent_id}

    def record_identity(self) -> dict[str, Any]:
        result: dict[str, Any] = {"goal_id": self.goal_id}
        if self.exact:
            result["goal_ref"] = dict(self.caller_goal_ref or {})
        return result


def _registered_goal(
    registry: dict[str, Any],
    *,
    goal_id: str,
    agents: tuple[str, ...],
    require_active: bool,
) -> dict[str, Any]:
    goal = next(
        (
            candidate
            for candidate in registry.get("goals", [])
            if isinstance(candidate, dict) and candidate.get("id") == goal_id
        ),
        None,
    )
    if goal is None or any(
        agent not in registered_agent_ids_for_goal(goal) for agent in agents
    ):
        raise ValueError("collaboration requires registered Agents of the same Goal")
    if require_active and goal.get("status") in {"stopped", "archived"}:
        raise ValueError("collaboration Goal is stopped or archived")
    return goal


def _source_goal_ref(goal_id: str, goal: dict[str, Any]) -> dict[str, str]:
    instance_id = goal.get("goal_instance_id")
    if not isinstance(instance_id, str):
        raise ValueError("source-session Goal is missing goal_instance_id")
    return exact_goal_ref(goal_id, instance_id)


def _caller_ref(
    *,
    goal_id: str,
    requested: dict[str, str] | None,
    current: dict[str, str],
) -> dict[str, str]:
    if requested is None:
        return dict(current)
    return exact_goal_ref(
        str(requested.get("goal_id") or goal_id),
        str(requested.get("goal_instance_id") or ""),
    )


@contextmanager
def collaboration_goal_scope(
    registry_path: Path,
    *,
    goal_id: str,
    agents: tuple[str, ...],
    caller_goal_ref: dict[str, str] | None = None,
    require_active: bool = False,
) -> Iterator[CollaborationGoalScope]:
    """Hold the alias lifetime guard while one collaboration operation commits."""

    registry_path = Path(registry_path).expanduser().resolve()
    with exclusive_cross_runtime_file_lock(
        guard_path(registry_path, goal_id),
        operation="collaboration_goal_lifetime",
    ):
        registry = load_project_registry(registry_path)
        goal = _registered_goal(
            registry,
            goal_id=goal_id,
            agents=agents,
            require_active=require_active,
        )
        profile = registry.get("profile_id")
        if profile != SOURCE_SESSION_PROFILE_ID:
            yield CollaborationGoalScope(
                registry_path=registry_path,
                goal_id=goal_id,
                goal=goal,
                profile_id=None,
                caller_goal_ref=None,
                current_goal_ref=None,
            )
            return
        current = _source_goal_ref(goal_id, goal)
        yield CollaborationGoalScope(
            registry_path=registry_path,
            goal_id=goal_id,
            goal=goal,
            profile_id=SOURCE_SESSION_PROFILE_ID,
            caller_goal_ref=_caller_ref(
                goal_id=goal_id,
                requested=caller_goal_ref,
                current=current,
            ),
            current_goal_ref=current,
        )


def capture_collaboration_goal_ref(
    registry_path: Path,
    *,
    goal_id: str,
    agent_id: str,
) -> dict[str, str] | None:
    with collaboration_goal_scope(
        registry_path,
        goal_id=goal_id,
        agents=(agent_id,),
    ) as scope:
        return (
            dict(scope.caller_goal_ref or {})
            if scope.exact
            else None
        )


def decide_collaboration_lifecycle(
    scope: CollaborationGoalScope,
    *,
    operation: str,
    record: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    initial_delivery_proved: bool = False,
) -> dict[str, Any]:
    if not scope.exact:
        return {"kind": "legacy"}
    result = effect_runtime_result(
        "collaboration.goal_instance.decide",
        {
            "profile_id": scope.profile_id,
            "operation": operation,
            "caller_goal_ref": scope.caller_goal_ref,
            "current_goal_ref": scope.current_goal_ref,
            "record_goal_ref": (
                record.get("goal_ref") if isinstance(record, dict) else None
            ),
            "route_goal_ref": (
                route.get("goal_ref") if isinstance(route, dict) else None
            ),
            "initial_delivery_proved": initial_delivery_proved,
        },
    )
    if not isinstance(result, dict):
        raise RuntimeError("collaboration lifecycle decision must be an object")
    if result.get("kind") == "reject":
        raise ValueError(
            f"collaboration lifecycle rejected: {result.get('code')}"
        )
    return result
