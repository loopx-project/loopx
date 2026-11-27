from __future__ import annotations

from pathlib import Path
from typing import Any

from .control_plane.todos.contract import normalize_todo_claimed_by


def normalize_registered_agents(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    agents: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("id") or value.get("agent_id") or value.get("name")
        agent = normalize_todo_claimed_by(value)
        if agent and agent not in agents:
            agents.append(agent)
    return agents


def agent_registration_tokens(values: Any) -> tuple[list[str], list[str]]:
    """Split requested agent ids and report every token the public shape rejects.

    Repeated flags and comma-separated values describe one registration write,
    so one unusable token must fail the batch instead of disappearing from a
    `written=true` receipt. Reads stay lenient through `normalize_registered_agents`.
    """
    items = values if isinstance(values, list) else [values]
    accepted: list[str] = []
    rejected: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("id") or item.get("agent_id") or item.get("name")
        raw = "" if item is None else str(item)
        for token in raw.split(","):
            candidate = token.strip()
            if not candidate:
                continue
            agent = normalize_todo_claimed_by(candidate)
            if not agent:
                rejected.append(candidate)
            elif agent not in accepted:
                accepted.append(agent)
    return accepted, rejected


def registered_agent_ids_for_goal(goal: dict[str, Any] | None) -> list[str]:
    if not isinstance(goal, dict):
        return []
    candidates: list[Any] = []
    coordination = goal.get("coordination")
    if isinstance(coordination, dict):
        candidates.append(coordination.get("registered_agents"))
    candidates.append(goal.get("registered_agents"))
    spawn_policy = goal.get("spawn_policy")
    if isinstance(spawn_policy, dict):
        candidates.append(spawn_policy.get("registered_agents"))
    agents: list[str] = []
    for candidate in candidates:
        for agent in normalize_registered_agents(candidate):
            if agent not in agents:
                agents.append(agent)
    return agents


def agent_profile_for_goal(goal: dict[str, Any] | None, agent_id: str | None) -> dict[str, Any] | None:
    normalized_agent_id = normalize_todo_claimed_by(agent_id)
    if not isinstance(goal, dict) or not normalized_agent_id:
        return None
    coordination = goal.get("coordination")
    if not isinstance(coordination, dict):
        return None
    profiles = coordination.get("agent_profiles")
    raw_profile: Any = None
    if isinstance(profiles, dict):
        raw_profile = profiles.get(normalized_agent_id)
    elif isinstance(profiles, list):
        for item in profiles:
            if not isinstance(item, dict):
                continue
            item_id = normalize_todo_claimed_by(item.get("agent_id") or item.get("id") or item.get("name"))
            if item_id == normalized_agent_id:
                raw_profile = item
                break
    if not isinstance(raw_profile, dict):
        return None
    profile = dict(raw_profile)
    profile["agent_id"] = normalized_agent_id
    return profile


def load_goal_from_registry(registry_path: Path, goal_id: str) -> dict[str, Any] | None:
    from .control_plane.hook_runtime.request_scope import current_hook_request_scope
    from .control_plane.projects.registry_codec import load_registry
    from .registry import registry_goals

    scope = current_hook_request_scope()
    if scope is not None:
        return scope.load_goal(registry_path, str(goal_id), loader=load_registry)
    if not registry_path.exists():
        return None
    registry = load_registry(registry_path)
    return next(
        (goal for goal in registry_goals(registry) if str(goal.get("id")) == str(goal_id)),
        None,
    )


def registered_agent_ids_from_registry(registry_path: Path, goal_id: str) -> list[str]:
    return registered_agent_ids_for_goal(load_goal_from_registry(registry_path, goal_id))


def agent_profile_from_registry(registry_path: Path, goal_id: str, agent_id: str | None) -> dict[str, Any] | None:
    return agent_profile_for_goal(load_goal_from_registry(registry_path, goal_id), agent_id)


def require_registered_agent_id(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None,
    field: str = "claimed_by",
) -> str:
    normalized = normalize_todo_claimed_by(agent_id)
    if not normalized:
        raise ValueError(f"{field} must be a public-safe registered agent id")
    registered = registered_agent_ids_from_registry(registry_path, goal_id)
    if not registered:
        raise ValueError(
            f"{field}={normalized!r} cannot be used because goal {goal_id!r} "
            "has no coordination.registered_agents list. Register this peer identity first: "
            "loopx configure-goal --goal-id "
            f"{goal_id} --registered-agent {normalized} --execute"
        )
    if normalized not in registered:
        raise ValueError(
            f"{field}={normalized!r} is not registered for goal {goal_id!r}; "
            f"registered_agents={', '.join(registered)}"
        )
    return normalized
