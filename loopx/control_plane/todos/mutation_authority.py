from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...agent_registry import (
    load_goal_from_registry,
    registered_agent_ids_for_goal,
)
from ..coordination.authority_core import (
    CoordinationSnapshot,
    DecisionOutcome,
    LifecycleGrant,
    TodoAction,
    TodoMutationCommand,
    TodoSnapshot,
    decide,
)
from ..coordination.local_snapshot import todo_snapshot_from_mapping
from ..effect_runtime import effect_runtime_result
from .contract import (
    normalize_removed_todo_continuation_policy,
    normalize_todo_claimed_by,
    normalize_todo_decision_outcome,
    normalize_todo_decision_scope,
)


TODO_MUTATION_AUTHORITY_SCHEMA_VERSION = "todo_mutation_authority_v0"
TODO_LIFECYCLE_AUTHORITY_ACTIONS = frozenset(
    {"complete", "reassign", "supersede", "update"}
)


def todo_lifecycle_facts(
    registry_path: Path, goal_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Project the existing registry owner for all canonical Todo mutations."""
    goal = load_goal_from_registry(registry_path, goal_id)
    registered = registered_agent_ids_for_goal(goal)
    coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
    grants = normalize_todo_lifecycle_authority(
        coordination.get("todo_lifecycle_authority")
        if isinstance(coordination, Mapping) else None,
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
            raise ValueError("todo lifecycle authority agent_id must be a public-safe token")
        if agent_id not in registered_agents:
            raise ValueError(
                f"todo lifecycle authority agent_id={agent_id!r} must already be registered"
            )
        if agent_id in seen_agents:
            raise ValueError(f"duplicate todo lifecycle authority grant for {agent_id!r}")
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ValueError("todo lifecycle authority actions must be a non-empty list")
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


def todo_update_authority_action(
    *,
    existing_role: str,
    role: str | None,
    claimed_by: str | None,
    clear_claim: bool,
    other_values: tuple[Any, ...],
    monitor_metadata: Mapping[str, Any] | None,
) -> str | None:
    if claimed_by is None and not clear_claim:
        return None
    has_other_change = bool(
        (role is not None and role != existing_role)
        or any(value is not None and value is not False for value in other_values)
        or any(value is not None for value in (monitor_metadata or {}).values())
    )
    return "update" if has_other_change else "reassign"


def _raise_core_authority_rejection(
    *,
    code: str,
    goal_id: str,
    registered_agents: list[str],
    command: str,
    actor: str | None,
    requested_owner: str | None,
    todo: TodoSnapshot,
    action: str,
) -> None:
    if code == "actor_not_registered" and actor:
        if not registered_agents:
            raise ValueError(
                f"agent_id={actor!r} cannot be used because goal {goal_id!r} "
                "has no coordination.registered_agents list. Register this peer "
                "identity first: loopx configure-goal --goal-id "
                f"{goal_id} --registered-agent {actor} --execute"
            )
        raise ValueError(
            f"agent_id={actor!r} is not registered for goal {goal_id!r}; "
            f"registered_agents={', '.join(registered_agents)}"
        )
    if code == "actor_required":
        raise ValueError(
            f"multi-agent todo {command} requires --agent-id to attribute the "
            "lifecycle actor; only completion of an exactly linked user_gate "
            "decision_scope may use the typed owner/controller override"
        )
    if code == "actor_excluded":
        raise ValueError(
            f"agent_id={actor!r} is excluded from mutating todo_id={todo.todo_id!r}"
        )
    if code == "bound_agent_mismatch":
        bound_agent = todo.bound_agent or todo.blocks_agent
        raise ValueError(
            f"agent_id={actor!r} cannot {command} user todo_id={todo.todo_id!r}; "
            f"its response continuation is bound to bound_agent={bound_agent!r}"
        )
    if code == "claim_owner_mismatch":
        raise ValueError(
            f"agent_id={actor!r} cannot {command} todo_id={todo.todo_id!r}; "
            f"it is claimed_by={todo.claimed_by!r}"
        )
    if code == "delegation_action_not_granted":
        raise ValueError(
            "coordination.todo_lifecycle_authority for "
            f"agent_id={actor!r} does not grant action={action!r}"
        )
    if code == "delegation_reason_required":
        raise ValueError(f"delegated {action} override requires --authority-reason")
    if code == "claim_actor_mismatch":
        raise ValueError(
            "todo claim requires --claimed-by to match the lifecycle "
            "--agent-id; use todo update for an owner-attributed transfer"
        )
    raise ValueError(
        f"todo lifecycle authority rejected command={command!r} code={code!r} "
        f"actor={actor!r} requested_owner={requested_owner!r}"
    )


def _raise_claim_ineligible_rejection(
    *,
    code: str,
    todo: Mapping[str, Any],
    core_todo: TodoSnapshot,
) -> None:
    """Preserve legacy claim messages for ineligible-Todo TypeScript codes."""

    if code == "todo_not_open":
        raise ValueError(
            f"todo claim requires status=open; todo_id {core_todo.todo_id!r} "
            f"is status={core_todo.status!r}"
        )
    if code == "todo_not_agent":
        raise ValueError(
            "claimed_by is execution ownership for agent todos, not a user-todo "
            "binding; use --bound-agent or --goal-bound"
        )
    if code == "removed_continuation_policy":
        raw_policy = str(todo.get("removed_continuation_policy") or "").strip()
        policy = normalize_removed_todo_continuation_policy(raw_policy) or raw_policy
        raise ValueError(
            f"todo_id {core_todo.todo_id!r} uses removed continuation_policy="
            f"{policy}; repair it before claiming"
        )
    if code in {"todo_archived", "todo_role_mismatch"}:
        raise ValueError(
            f"todo_id {core_todo.todo_id!r} was not found in active user or agent todos"
        )


def _authorize_typescript_claim(
    *,
    goal_id: str,
    todo: Mapping[str, Any],
    core_todo: TodoSnapshot,
    registered_agents: list[str],
    actor: str | None,
    requested_owner: str | None,
) -> dict[str, Any]:
    payload = effect_runtime_result(
        "todo.claim.decide",
        {
            "goal_id": goal_id,
            "todo_id": core_todo.todo_id,
            "todo": {
                "todo_id": core_todo.todo_id,
                "role": core_todo.role,
                "status": core_todo.status,
                "archive_state": str(todo.get("archive_state") or "active"),
                "claimed_by": core_todo.claimed_by,
                "excluded_agents": sorted(core_todo.excluded_agents),
                "removed_continuation_policy": todo.get(
                    "removed_continuation_policy"
                ),
            },
            "claimed_by": requested_owner,
            "actor_agent_id": actor,
            "expected_role": core_todo.role,
            "registered_agents": registered_agents,
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript Todo claim decision shape mismatch")
    if payload.get("status") != "accepted":
        code = str(payload.get("reason_code") or "claim_rejected")
        if code in {
            "actor_not_registered",
            "actor_required",
            "actor_excluded",
            "claim_owner_mismatch",
            "claim_actor_mismatch",
        }:
            _raise_core_authority_rejection(
                code=code,
                goal_id=goal_id,
                registered_agents=registered_agents,
                command="claim",
                actor=actor,
                requested_owner=requested_owner,
                todo=core_todo,
                action="claim",
            )
        _raise_claim_ineligible_rejection(code=code, todo=todo, core_todo=core_todo)
        raise ValueError(str(payload.get("reason") or "Todo claim was rejected"))
    mutation_authority = payload.get("mutation_authority")
    if not isinstance(mutation_authority, Mapping):
        raise RuntimeError("TypeScript Todo claim authority shape mismatch")
    return dict(mutation_authority)


def authorize_todo_lifecycle_mutation(
    *,
    registry_path: Path,
    goal_id: str,
    command: str,
    todo: Mapping[str, Any],
    actor_agent_id: str | None,
    authority_action: str | None = None,
    authority_reason: str | None = None,
    requested_claimed_by: str | None = None,
    decision_outcome: str | None = None,
    decision_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Authorize one existing-todo lifecycle mutation before state changes."""

    goal = load_goal_from_registry(registry_path, goal_id)
    registered_agents = registered_agent_ids_for_goal(goal)
    normalized_actor = normalize_todo_claimed_by(actor_agent_id)
    core_todo = todo_snapshot_from_mapping(todo, infer_status_from_done=True)
    assert core_todo is not None
    core_target = todo_snapshot_from_mapping(
        decision_target,
        infer_status_from_done=True,
    )
    requested_owner = normalize_todo_claimed_by(requested_claimed_by)
    # Legacy single-agent callers historically omitted ``agent_id`` while the
    # Todo itself carried the sole registered owner/binding. Attribute those
    # calls to that unambiguous owner before entering the shared decision
    # function; genuinely unowned actorless records remain compatibility-safe.
    if normalized_actor is None and len(registered_agents) == 1:
        sole_agent = registered_agents[0]
        bound_agent = core_todo.bound_agent or (
            core_todo.blocks_agent if core_todo.role == "user" else None
        )
        if requested_owner == sole_agent or core_todo.claimed_by == sole_agent or bound_agent == sole_agent:
            normalized_actor = sole_agent
    # A pre-coordination goal has no declared registration list, but explicit
    # legacy callers still supplied an actor. Preserve that behavior without
    # weakening the shared ownership/exclusion checks: the supplied actor is
    # the only admissible peer for this decision.
    decision_registered_agents = registered_agents or (
        [normalized_actor] if normalized_actor is not None else []
    )
    effective_action = str(authority_action or command).strip().lower()
    if command == "claim":
        return _authorize_typescript_claim(
            goal_id=goal_id,
            todo=todo,
            core_todo=core_todo,
            registered_agents=decision_registered_agents,
            actor=normalized_actor,
            requested_owner=requested_owner,
        )
    try:
        core_action = TodoAction(command)
    except ValueError as exc:
        raise ValueError(f"unsupported todo lifecycle command={command!r}") from exc
    normalized_decision_outcome = normalize_todo_decision_outcome(decision_outcome)
    core_command = TodoMutationCommand(
        action=core_action,
        actor_agent_id=normalized_actor,
        requested_claimed_by=requested_owner,
        authority_action=effective_action,
        authority_reason=authority_reason,
        decision_outcome=normalized_decision_outcome,
    )
    core_snapshot = CoordinationSnapshot(
        registered_agents=tuple(decision_registered_agents),
        todo=core_todo,
        decision_target=core_target,
    )
    raw_grants: list[dict[str, Any]] = []
    plan = decide(core_snapshot, core_command)
    if plan.outcome is DecisionOutcome.REJECTED and plan.code == "claim_owner_mismatch":
        coordination = goal.get("coordination") if isinstance(goal, Mapping) else None
        raw_grants = normalize_todo_lifecycle_authority(
            coordination.get("todo_lifecycle_authority")
            if isinstance(coordination, Mapping)
            else None,
            registered_agents=decision_registered_agents,
        )
        plan = decide(
            CoordinationSnapshot(
                registered_agents=core_snapshot.registered_agents,
                lifecycle_grants=tuple(
                    LifecycleGrant(
                        agent_id=grant["agent_id"],
                        actions=frozenset(grant["actions"]),
                        requires_reason=grant["requires_reason"],
                    )
                    for grant in raw_grants
                ),
                todo=core_todo,
                decision_target=core_target,
            ),
            core_command,
        )
    if plan.outcome is DecisionOutcome.REJECTED:
        _raise_core_authority_rejection(
            code=plan.code,
            goal_id=goal_id,
            registered_agents=registered_agents,
            command=command,
            actor=normalized_actor,
            requested_owner=requested_owner,
            todo=core_todo,
            action=effective_action,
        )
    base = {
        "schema_version": TODO_MUTATION_AUTHORITY_SCHEMA_VERSION,
        "command": command,
        "mode": plan.authority_mode,
        "actor_agent_id": normalized_actor,
        "todo_id": core_todo.todo_id,
        "registered_agent_count": len(registered_agents),
    }
    if plan.authority_mode == "exact_user_gate_decision_scope_override":
        gate_scope = normalize_todo_decision_scope(todo.get("decision_scope"))
        return {
            **base,
            "actor_agent_id": None,
            "target_todo_id": core_todo.unblocks_todo_id,
            "decision_outcome": normalized_decision_outcome,
            "decision_scope": gate_scope,
            "authority_source": "linked_user_gate_decision_scope",
        }
    if plan.authority_mode == "delegated_orchestration_override":
        grant = next(item for item in raw_grants if item["agent_id"] == normalized_actor)
        normalized_reason = str(authority_reason or "").strip()
        return {
            **base,
            "claim_owner": core_todo.claimed_by,
            "authority_action": effective_action,
            "authority_source": "coordination.todo_lifecycle_authority",
            "authority_reason": normalized_reason or None,
            "requires_reason": grant["requires_reason"],
        }
    if plan.authority_mode == "registered_peer_actor":
        base["claim_owner"] = core_todo.claimed_by
    return base
