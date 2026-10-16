from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...agent_registry import (
    load_goal_from_registry,
    registered_agent_ids_for_goal,
)
from .contract import normalize_todo_claimed_by


TODO_LIFECYCLE_AUTHORITY_ACTIONS = frozenset(
    {"complete", "reassign", "supersede", "update"}
)


def todo_lifecycle_facts(
    registry_path: Path,
    goal_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Project the existing registry owner for all canonical Todo mutations."""
    goal = load_goal_from_registry(registry_path, goal_id)
    registered = registered_agent_ids_for_goal(goal)
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    grants = normalize_todo_lifecycle_authority(
        coordination.get("todo_lifecycle_authority")
        if isinstance(coordination, Mapping)
        else None,
        registered_agents=registered,
    )
    return registered, grants


def normalize_todo_lifecycle_authority(
    values: Any,
    *,
    registered_agents: list[str],
) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("coordination.todo_lifecycle_authority must be a list")
    normalized: list[dict[str, Any]] = []
    seen_agents: set[str] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            raise ValueError("each todo lifecycle authority grant must be an object")
        agent_id = normalize_todo_claimed_by(raw.get("agent_id"))
        if not agent_id:
            raise ValueError(
                "todo lifecycle authority agent_id must be a public-safe token"
            )
        if agent_id not in registered_agents:
            raise ValueError(
                f"todo lifecycle authority agent_id={agent_id!r} must already be registered"
            )
        if agent_id in seen_agents:
            raise ValueError(
                f"duplicate todo lifecycle authority grant for {agent_id!r}"
            )
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ValueError(
                "todo lifecycle authority actions must be a non-empty list"
            )
        actions: list[str] = []
        for value in raw_actions:
            action = str(value or "").strip().lower()
            if action not in TODO_LIFECYCLE_AUTHORITY_ACTIONS:
                raise ValueError(
                    f"unsupported todo lifecycle authority action={action!r}; "
                    "expected one of: "
                    + ", ".join(sorted(TODO_LIFECYCLE_AUTHORITY_ACTIONS))
                )
            if action not in actions:
                actions.append(action)
        requires_reason = raw.get("requires_reason", True)
        if not isinstance(requires_reason, bool):
            raise ValueError("todo lifecycle authority requires_reason must be boolean")
        normalized.append(
            {
                "agent_id": agent_id,
                "actions": actions,
                "requires_reason": requires_reason,
            }
        )
        seen_agents.add(agent_id)
    return normalized
