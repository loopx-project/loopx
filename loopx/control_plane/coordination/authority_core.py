"""Provider-neutral coordination decisions shared by current state writers.

Adapters normalize their persisted state while holding the existing lock, call
``decide``, and only then perform their current write.  A returned transition is
a proposal, not proof that any write committed.  Durable execution results and
storage outcomes deliberately live outside this module. Todo lifecycle admission,
ownership routing and terminal fences adapt to canonical TypeScript decisions.
Lease and handoff writers use their whole native transactions directly; their
unconsumed Python decision facades and lease-mode input are retired. Python
retains the live Todo snapshot/result adaptation, not a second lease rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ..effect_runtime import effect_runtime_result


DecisionScope = tuple[str, str, str]


class HandoffMode(StrEnum):
    LEGACY = "legacy"
    SOFT_CLAIM = "soft_claim"
    HARD_LEASE = "hard_lease"


class TodoAction(StrEnum):
    CLAIM = "claim"
    UPDATE = "update"
    COMPLETE = "complete"
    SUPERSEDE = "supersede"


class DecisionOutcome(StrEnum):
    APPLY = "apply"
    NO_CHANGE = "no_change"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class OwnershipGate(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRE_HOLDER = "require_holder"
    DELEGATED_OVERRIDE = "delegated_override"


class LeaseFence(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    AUTO_ACQUIRE = "auto_acquire"
    DELEGATED_OVERRIDE = "delegated_override"


@dataclass(frozen=True)
class LifecycleGrant:
    agent_id: str
    actions: frozenset[str]
    requires_reason: bool = True


@dataclass(frozen=True)
class TodoSnapshot:
    todo_id: str
    status: str
    role: str
    task_class: str | None = None
    claimed_by: str | None = None
    excluded_agents: frozenset[str] = frozenset()
    bound_agent: str | None = None
    blocks_agent: str | None = None
    decision_scope: DecisionScope | None = None
    required_decision_scopes: frozenset[DecisionScope] = frozenset()
    unblocks_todo_id: str | None = None


@dataclass(frozen=True)
class LeaseSnapshot:
    present: bool = False
    active: bool = False
    status: str | None = None
    owner: str | None = None
    idempotency_key: str | None = None
    version: int = 0
    lease_epoch: int = 0
    write_scopes: tuple[str, ...] = ()
    acquire_ttl_seconds: int | None = None


@dataclass(frozen=True)
class CoordinationSnapshot:
    handoff_mode: HandoffMode = HandoffMode.LEGACY
    registered_agents: tuple[str, ...] = ()
    lifecycle_grants: tuple[LifecycleGrant, ...] = ()
    todo: TodoSnapshot | None = None
    decision_target: TodoSnapshot | None = None
    lease: LeaseSnapshot | None = None


@dataclass(frozen=True)
class TodoMutationCommand:
    action: TodoAction
    actor_agent_id: str | None
    requested_claimed_by: str | None = None
    clear_claim: bool = False
    authority_action: str | None = None
    authority_reason: str | None = None
    decision_outcome: str | None = None
    ownership_mutation: bool = False
    lease_idempotency_key: str | None = None
    lease_expected_version: int | None = None
    allow_user_gate_auto_acquire: bool = False


@dataclass(frozen=True)
class TransitionPlan:
    outcome: DecisionOutcome
    code: str
    next_snapshot: CoordinationSnapshot | None = None
    authority_mode: str | None = None
    ownership_gate: OwnershipGate = OwnershipGate.NOT_REQUIRED
    lease_fence: LeaseFence = LeaseFence.NOT_REQUIRED
    idempotent: bool = False


def _result(
    outcome: DecisionOutcome,
    code: str,
    *,
    next_snapshot: CoordinationSnapshot | None = None,
    authority_mode: str | None = None,
    ownership_gate: OwnershipGate = OwnershipGate.NOT_REQUIRED,
    lease_fence: LeaseFence = LeaseFence.NOT_REQUIRED,
    idempotent: bool = False,
) -> TransitionPlan:
    return TransitionPlan(
        outcome=outcome,
        code=code,
        next_snapshot=next_snapshot,
        authority_mode=authority_mode,
        ownership_gate=ownership_gate,
        lease_fence=lease_fence,
        idempotent=idempotent,
    )


def _invalid_lease_snapshot(lease: LeaseSnapshot | None) -> bool:
    """Reject contradictory normalized states at the pure-core boundary."""

    return bool(
        lease is not None
        and lease.active
        and (not lease.present or lease.status == "released")
    )


def write_scopes_overlap(
    left: tuple[str, ...] | list[str],
    right: tuple[str, ...] | list[str],
) -> bool:
    """Return the canonical native overlap decision for normalized scopes."""

    payload = effect_runtime_result(
        "task_lease.write_scopes.overlap",
        {"left": list(left), "right": list(right)},
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("overlap"), bool):
        raise RuntimeError("native task-lease write-scope decision shape mismatch")
    return bool(payload["overlap"])


def _decision_scope_payload(scope: DecisionScope | None) -> dict[str, str] | None:
    if scope is None:
        return None
    return {
        "kind": scope[0],
        "granularity": scope[1],
        "scope_key": scope[2],
    }


def _todo_fact_payload(todo: TodoSnapshot) -> dict[str, Any]:
    return {
        "todo_id": todo.todo_id,
        "status": todo.status,
        "role": todo.role,
        "task_class": todo.task_class,
        "claimed_by": todo.claimed_by,
        "excluded_agents": sorted(todo.excluded_agents),
        "bound_agent": todo.bound_agent,
        "blocks_agent": todo.blocks_agent,
        "decision_scope": _decision_scope_payload(todo.decision_scope),
        "required_decision_scopes": [
            _decision_scope_payload(scope)
            for scope in sorted(todo.required_decision_scopes)
        ],
        "unblocks_todo_id": todo.unblocks_todo_id,
    }


def _lease_fact_payload(lease: LeaseSnapshot | None) -> dict[str, Any] | None:
    if lease is None:
        return None
    return {
        "present": lease.present,
        "active": lease.active,
        "status": lease.status,
        "owner": lease.owner,
        "idempotency_key": lease.idempotency_key,
        "version": lease.version,
        "lease_epoch": lease.lease_epoch,
        "write_scopes": list(lease.write_scopes),
        "acquire_ttl_seconds": lease.acquire_ttl_seconds,
    }


def _lease_fact_from_payload(value: Any) -> LeaseSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("TypeScript terminal decision next_lease shape mismatch")
    return LeaseSnapshot(
        present=bool(value.get("present")),
        active=bool(value.get("active")),
        status=str(value["status"]) if value.get("status") is not None else None,
        owner=str(value["owner"]) if value.get("owner") is not None else None,
        idempotency_key=(
            str(value["idempotency_key"])
            if value.get("idempotency_key") is not None
            else None
        ),
        version=int(value.get("version") or 0),
        lease_epoch=int(value.get("lease_epoch") or 0),
        write_scopes=tuple(str(item) for item in value.get("write_scopes") or []),
        acquire_ttl_seconds=(
            int(value["acquire_ttl_seconds"])
            if value.get("acquire_ttl_seconds") is not None
            else None
        ),
    )


def _typescript_todo_decision(
    snapshot: CoordinationSnapshot,
    command: TodoMutationCommand,
) -> TransitionPlan:
    """Project the typed lifecycle decision; storage and locks stay with callers."""

    todo = snapshot.todo
    if todo is None:
        return _result(DecisionOutcome.REJECTED, "todo_not_found")
    terminal = command.action in {TodoAction.COMPLETE, TodoAction.SUPERSEDE}
    operation = "terminal" if terminal else "mutation"
    payload = effect_runtime_result(
        f"todo.{operation}.decide",
        {
            "schema_version": f"loopx_coordination_todo_{operation}_decision_request_v0",
            "command": command.action.value,
            "handoff_mode": snapshot.handoff_mode.value,
            "registered_agents": list(snapshot.registered_agents),
            "lifecycle_grants": [
                {
                    "agent_id": grant.agent_id,
                    "actions": sorted(grant.actions),
                    "requires_reason": grant.requires_reason,
                }
                for grant in snapshot.lifecycle_grants
            ],
            "todo": _todo_fact_payload(todo),
            "decision_target": (
                _todo_fact_payload(snapshot.decision_target)
                if snapshot.decision_target is not None
                else None
            ),
            "lease": _lease_fact_payload(snapshot.lease),
            "actor_agent_id": command.actor_agent_id,
            "authority_action": command.authority_action or command.action.value,
            "authority_reason": command.authority_reason,
            "decision_outcome": command.decision_outcome,
            "lease_idempotency_key": command.lease_idempotency_key,
            "lease_expected_version": command.lease_expected_version,
            "allow_user_gate_auto_acquire": command.allow_user_gate_auto_acquire,
            "requested_claimed_by": command.requested_claimed_by,
            "clear_claim": command.clear_claim,
            "ownership_mutation": command.ownership_mutation,
        },
    )
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        f"loopx_coordination_todo_{operation}_decision_result_v0"
    ):
        raise RuntimeError("TypeScript Todo lifecycle decision result shape mismatch")
    try:
        outcome = DecisionOutcome(str(payload["outcome"]))
        ownership_gate = OwnershipGate(str(payload["ownership_gate"]))
        lease_fence = LeaseFence(str(payload["lease_fence"]))
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            "TypeScript Todo lifecycle decision result shape mismatch"
        ) from exc
    next_snapshot = None
    if outcome is DecisionOutcome.APPLY:
        if payload.get("next_todo_status") != ("done" if terminal else todo.status):
            raise RuntimeError(
                "TypeScript Todo lifecycle decision returned invalid next Todo state"
            )
        next_lease_payload = payload.get("next_lease")
        next_snapshot = replace(
            snapshot,
            todo=replace(
                todo,
                status=str(payload["next_todo_status"]),
                claimed_by=(
                    todo.claimed_by if terminal else payload["next_todo_claimed_by"]
                ),
            ),
            lease=(
                snapshot.lease
                if next_lease_payload is None
                else _lease_fact_from_payload(next_lease_payload)
            ),
        )
    elif outcome is DecisionOutcome.NO_CHANGE:
        next_snapshot = snapshot
    return TransitionPlan(
        outcome=outcome,
        code=str(payload.get("code") or "terminal_decision_invalid"),
        next_snapshot=next_snapshot,
        authority_mode=(
            str(payload["authority_mode"])
            if payload.get("authority_mode") is not None
            else None
        ),
        ownership_gate=ownership_gate,
        lease_fence=lease_fence,
        idempotent=bool(payload.get("idempotent")),
    )


def ownership_gate_requirement(
    *,
    handoff_mode: HandoffMode,
    ownership_mutation: bool,
    authority_mode: str | None,
) -> OwnershipGate:
    """Route one claimed_by mutation on an existing todo into the holder gate.

    This is the single owner of the routing rule: only a hard_lease ownership
    mutation needs the holder gate, and the delegated orchestration override
    is the one audited door through it. Writers consult this instead of
    re-deriving the mode/door predicates at the edge.
    """

    payload = effect_runtime_result(
        "todo.ownership_gate.decide",
        {
            "handoff_mode": handoff_mode.value,
            "ownership_mutation": ownership_mutation,
            "authority_mode": authority_mode,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("TypeScript ownership gate result shape mismatch")
    return OwnershipGate(payload["ownership_gate"])


def decide(
    snapshot: CoordinationSnapshot,
    command: TodoMutationCommand,
) -> TransitionPlan:
    """Evaluate one normalized command without reading or writing state."""

    if _invalid_lease_snapshot(snapshot.lease):
        return _result(DecisionOutcome.REJECTED, "invalid_lease_snapshot")
    if isinstance(command, TodoMutationCommand):
        return _typescript_todo_decision(snapshot, command)
    raise TypeError(f"unsupported coordination command: {type(command).__name__}")
