from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...agent_registry import (
    load_goal_from_registry,
    registered_agent_ids_for_goal,
)
from .active_state_editing import find_todo_block
from .contract import (
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
)


TODO_COMPLETION_POLICY_REQUEST_SCHEMA = "loopx_todo_completion_policy_request_v0"
TODO_COMPLETION_POLICY_RESULT_SCHEMA = "loopx_todo_completion_policy_result_v0"
TODO_COMPLETION_POLICY_FAILURE_SCHEMA = "loopx_todo_completion_policy_failure_v0"


@dataclass(frozen=True)
class LinkedSuccessor:
    todo_id: str
    role: str | None = None
    status: str | None = None
    task_class: str | None = None
    action_kind: str | None = None
    continuation_policy: str | None = None
    claimed_by: str | None = None


@dataclass(frozen=True)
class CompletionPolicy:
    effective_claimed_by: str | None
    registered_agents: list[str]
    effective_next_claimed_by: str | None
    effective_next_excluded_agents: list[str]
    self_merged: bool
    linked_successor_id: str | None = None


def linked_successor_from_todo(todo: Mapping[str, Any]) -> LinkedSuccessor:
    return LinkedSuccessor(
        todo_id=str(todo.get("todo_id") or ""),
        role=str(todo.get("role") or "").strip() or None,
        status=str(todo.get("status") or "").strip() or None,
        task_class=str(todo.get("task_class") or "").strip() or None,
        action_kind=str(todo.get("action_kind") or "").strip() or None,
        continuation_policy=normalize_todo_continuation_policy(
            todo.get("continuation_policy")
        ),
        claimed_by=normalize_todo_claimed_by(todo.get("claimed_by")),
    )


def linked_successors_from_state(
    *,
    lines: list[str],
    successor_todo_ids: Iterable[str],
) -> list[LinkedSuccessor]:
    """Resolve declared successor rows from Markdown."""

    successors: list[LinkedSuccessor] = []
    for todo_id in successor_todo_ids:
        match = find_todo_block(lines, todo_id=todo_id)
        if match:
            role, _section, _start, _end, block = match
            successors.append(linked_successor_from_todo({**block, "role": role}))
            continue
    return successors


def build_completion_policy_request(
    *,
    registry_path: Path,
    goal_id: str,
    claimed_by: str | None = None,
    next_claimed_by: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_continuation_policy: str | None = None,
    next_excluded_agents: Iterable[str] = (),
    self_merged: bool = False,
    evidence: str | None = None,
    linked_successors: Iterable[LinkedSuccessor] = (),
) -> dict[str, Any]:
    """Project registry and source facts without deciding successor authority."""

    del next_action_kind
    goal = load_goal_from_registry(registry_path, goal_id)
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    agent_model = (
        coordination.get("agent_model") if isinstance(coordination, Mapping) else None
    )
    if not agent_model and isinstance(goal, Mapping):
        agent_model = goal.get("agent_model")
    successor_rows = [
        {
            "todo_id": successor.todo_id,
            "role": successor.role,
            "status": successor.status,
        }
        for successor in linked_successors
    ]
    return {
        "schema_version": TODO_COMPLETION_POLICY_REQUEST_SCHEMA,
        "goal_id": goal_id,
        "agent_model": agent_model,
        "claimed_by": claimed_by,
        "registered_agents": registered_agent_ids_for_goal(
            dict(goal) if isinstance(goal, Mapping) else None
        ),
        "next_claimed_by": next_claimed_by,
        "next_agent_todo": next_agent_todo,
        "next_continuation_policy": next_continuation_policy,
        "next_excluded_agents": list(next_excluded_agents),
        "self_merged": bool(self_merged),
        "evidence": evidence,
        "linked_successors": successor_rows,
    }


def completion_policy_from_transaction(
    transaction: Mapping[str, Any],
) -> CompletionPolicy:
    """Adapt the TypeScript-owned completion policy into legacy Python fields."""

    if transaction.get("decision") == "replay":
        # These fields are dead on the event-projected replay path; the TS
        # completion fence has already prohibited every write.
        return CompletionPolicy(None, [], None, [], False)
    if transaction.get("decision") == "policy_reject":
        failure = transaction.get("completion_policy_failure")
        if not (
            isinstance(failure, Mapping)
            and failure.get("schema_version")
            == TODO_COMPLETION_POLICY_FAILURE_SCHEMA
            and failure.get("kind") == "completion_policy_rejected"
            and isinstance(failure.get("diagnostic_code"), str)
            and bool(failure.get("diagnostic_code"))
            and isinstance(failure.get("summary"), str)
            and bool(failure.get("summary"))
            and transaction.get("completion_policy") is None
        ):
            raise RuntimeError(
                "TypeScript Todo completion policy failure shape mismatch"
            )
        raise ValueError(str(failure["summary"]))
    policy = transaction.get("completion_policy")
    if not isinstance(policy, Mapping) or (
        policy.get("schema_version") != TODO_COMPLETION_POLICY_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript Todo completion policy result shape mismatch")
    registered_agents = policy.get("registered_agents")
    excluded_agents = policy.get("effective_next_excluded_agents")
    claimed_by = policy.get("effective_claimed_by")
    next_claimed_by = policy.get("effective_next_claimed_by")
    linked_successor_id = policy.get("linked_successor_id")
    scalar_shape_is_valid = all(
        value is None or isinstance(value, str)
        for value in (claimed_by, next_claimed_by, linked_successor_id)
    )
    if (
        not isinstance(registered_agents, list)
        or not all(isinstance(value, str) for value in registered_agents)
        or not isinstance(excluded_agents, list)
        or not all(isinstance(value, str) for value in excluded_agents)
        or not scalar_shape_is_valid
        or not isinstance(policy.get("self_merged"), bool)
    ):
        raise RuntimeError("TypeScript Todo completion policy result shape mismatch")
    return CompletionPolicy(
        effective_claimed_by=claimed_by,
        registered_agents=list(registered_agents),
        effective_next_claimed_by=next_claimed_by,
        effective_next_excluded_agents=list(excluded_agents),
        self_merged=bool(policy["self_merged"]),
        linked_successor_id=linked_successor_id,
    )
