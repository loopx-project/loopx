"""Host adapter for canonical, owner-configured Goal acceptance.

TypeScript owns revisions, work bindings, admission and CAS. Python resolves
the registered Goal and executes only validation commands read from that owner.
No caller-supplied pass/fail result is accepted by the public CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..coordination.local_authority import local_authority_is_promoted
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from ..runtime.runtime_projection_route import resolve_goal_source_runtime_route
from .acceptance_verification import (
    _acceptance_pins_match as _acceptance_pins_match,
    _criterion_effects as _criterion_effects,
    _stale_acceptance_receipt as _stale_acceptance_receipt,
    observe_canonical_goal_acceptance as observe_canonical_goal_acceptance,
    observe_goal_acceptance_oracles as observe_goal_acceptance_oracles,
    run_goal_acceptance_effects as run_goal_acceptance_effects,
    run_goal_acceptance_validation_effect as run_goal_acceptance_validation_effect,
    validation_effect_files_current as validation_effect_files_current,
)
from .activation import goal_is_stopped
from .goal_ref_validation import exact_goal_ref

_DEFAULT_OBSERVE_ORACLES = observe_goal_acceptance_oracles

Payload = dict[str, object]
PayloadView = Mapping[str, object]

_INSPECT_METHOD = "goal.acceptance.inspect"
_VALID_RESULT_STATUSES = frozenset(
    {"loaded", "applied", "recovered", "replayed", "planned", "no_change"}
)
_PUBLIC_ACCEPTANCE_KEYS: tuple[str, ...] = (
    "status",
    "goal_id",
    "provider_revision",
    "source_authority",
    "changed",
    "dry_run",
    "operation_id",
    "goal_acceptance_contract",
    "objective_work_digest",
    "handoff",
    "goal_objective_guard",
    "reason_code",
)


class GoalAcceptanceRejected(ValueError):
    """Carry owner-qualified detail through the existing CLI error boundary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.payload = {"error_detail": detail}


class GoalAcceptanceRoute(TypedDict):
    registry_path: Path
    runtime_root: str
    goal_id: str


def goal_acceptance_route(
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
) -> GoalAcceptanceRoute:
    """Route a presentation request; its runtime owns the proposal store.

    Keep the service runtime for a directly served project, but never let the
    global App's conversation store override a Goal's declared source runtime.
    """
    source = resolve_goal_source_runtime_route(registry_path=registry_path, goal_id=goal_id)
    root = str(source["source_runtime_root"])
    if runtime_root is not None and not source["routed_to_source_registry"]:
        root = runtime_root
    return {"registry_path": Path(str(source["source_registry"])), "runtime_root": root, "goal_id": goal_id}


def _goal_is_active(goal: PayloadView) -> bool:
    return goal.get("status", "active") in {"active", "ready"} and not goal_is_stopped(goal)


def _routing(
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None,
    agent_id: str | None = None,
) -> tuple[Path, Payload, Payload]:
    # A direct CLI override remains an explicit diagnostic/recovery route.
    source = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        runtime_root_override=runtime_root,
    )
    resolved_registry = Path(str(source["source_registry"]))
    goal = load_goal_from_registry(resolved_registry, goal_id)
    if goal is None:
        raise ValueError("Goal acceptance requires a registered Goal")
    if agent_id is not None and agent_id not in registered_agent_ids_for_goal(goal):
        raise ValueError("Goal acceptance caller must be a registered Agent")
    root = Path(str(source["source_runtime_root"]))
    if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
        raise ValueError(
            "Goal acceptance requires an existing canonical authority; activation never promotes a provider"
        )
    route: Payload = {"runtime_root": str(root.resolve()), "goal_id": goal_id}
    goal_instance_id = goal.get("goal_instance_id")
    if goal_instance_id is not None:
        route["goal_ref"] = exact_goal_ref(goal_id, cast(str, goal_instance_id))
    return resolved_registry, goal, route


def _result(method: str, request: PayloadView, *, timeout: float | None = None) -> Payload:
    payload = dict(request)
    value = (
        effect_runtime_result(method, payload)
        if timeout is None
        else effect_runtime_result(method, payload, timeout=timeout)
    )
    if not isinstance(value, dict):
        raise TypeError("Goal acceptance authority returned an invalid result")
    if value.get("status") not in _VALID_RESULT_STATUSES:
        reason = str(
            value.get("reason_code")
            or value.get("reason")
            or "Goal acceptance authority is unavailable"
        )
        detail = value.get("error_detail")
        if isinstance(value.get("reason_code"), str) and isinstance(detail, str) and detail:
            # This field is emitted only for typed, fixed-rule validation.
            # Never promote the generic reason, which can contain private IO.
            raise GoalAcceptanceRejected(reason, detail)
        raise ValueError(reason)
    return value


def transition_goal_acceptance_lifecycle(
    *,
    runtime_root: Path,
    goal_id: str,
    transition: PayloadView,
    operation_id: str,
) -> Payload | None:
    """Commit one source-owned acceptance lifecycle transition if promoted."""
    root = runtime_root.resolve()
    if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
        return None
    return _result(
        "goal.acceptance.lifecycle.transition",
        {
            "runtime_root": str(root),
            "goal_id": goal_id,
            "actor_agent_id": None,
            "operation_id": operation_id,
            "transition": dict(transition),
        },
        timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    )


def refine_goal_outcome(
    *,
    registry_path: Path,
    goal_id: str,
    refinement: PayloadView,
    expected_provider_revision: str,
    operation_id: str,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    execute: bool = False,
    reviewed_work_update: PayloadView | None = None,
) -> Payload:
    """Record scoped planning intent, never configure validators or attest success."""
    _, goal, route = _routing(registry_path, goal_id, runtime_root, agent_id)
    if (
        "owner_agent_id" in refinement
        and refinement["owner_agent_id"] not in registered_agent_ids_for_goal(goal)
    ):
        raise ValueError("Outcome owner must be a registered Agent")
    # Registration eligibility is independent of the typed activation gate.
    if not _goal_is_active(goal):
        raise ValueError("Outcome refinement requires an active Goal")
    request: Payload = {
        **route,
        "actor_agent_id": agent_id,
        "expected_provider_revision": expected_provider_revision,
        "operation_id": operation_id,
        "refinement": dict(refinement),
        "dry_run": not execute,
    }
    if reviewed_work_update is not None:
        request["reviewed_work_update"] = dict(reviewed_work_update)
    return _result("goal.acceptance.refine", request, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def require_active_goal_for_objective_update(goal: PayloadView | None) -> None:
    if goal is None:
        raise ValueError("Goal acceptance requires a registered Goal")
    if not _goal_is_active(goal):
        raise ValueError("Objective updates require an active Goal")


def handoff_goal_outcomes(
    *,
    registry_path: Path,
    goal_id: str,
    document: PayloadView,
    expected_provider_revision: str,
    operation_id: str,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    execute: bool = False,
) -> Payload:
    """Delegate only explicitly selected orphaned outcomes through the local owner route."""
    if agent_id is not None:
        raise ValueError("Outcome handoff requires the local Goal owner")
    from ..collaboration.goal_instance_scope import collaboration_goal_scope

    source = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        runtime_root_override=runtime_root,
    )
    source_registry = Path(str(source["source_registry"])).resolve()
    # Roster changes do not advance the outcome store's CAS. Hold the same
    # Goal -> registry -> work-store order as other scoped handoffs through commit.
    with collaboration_goal_scope(source_registry, goal_id=goal_id, agents=(), lock_registry=True) as scope:
        resolved_registry, goal, route = _routing(registry_path, goal_id, runtime_root, agent_id)
        if resolved_registry.resolve() != scope.registry_path:
            raise ValueError("Goal source changed while preparing the outcome handoff")
        require_active_goal_for_objective_update(goal)
        return _result(
            "goal.acceptance.handoff",
            {
                **route,
                "actor_agent_id": agent_id,
                "operation_id": operation_id,
                "document": dict(document),
                "expected_provider_revision": expected_provider_revision,
                "dry_run": not execute,
                "registered_agents": registered_agent_ids_for_goal(goal),
            },
            timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
        )


def update_goal_objective(
    *,
    registry_path: Path,
    goal_id: str,
    document: PayloadView,
    operation: str,
    expected_provider_revision: str,
    operation_id: str,
    agent_id: str | None = None,
    runtime_root: str | None = None,
    execute: bool = False,
    review_objective_digest: str | None = None,
) -> Payload:
    """Adapt explicit owner revisions or a registered Agent's own adoption."""
    from ..work_items.replan_context_codec import goal_context_facts

    resolved_registry, goal, route = _routing(registry_path, goal_id, runtime_root, agent_id)
    require_active_goal_for_objective_update(goal)
    request: Payload = {
        **route,
        "actor_agent_id": agent_id,
        "expected_provider_revision": expected_provider_revision,
        "operation_id": operation_id,
        "operation": operation,
        "document": dict(document),
        "dry_run": not execute,
        "source_objective": goal_context_facts({"registry": str(resolved_registry)}, goal_id, None),
        "registered_agents": registered_agent_ids_for_goal(goal),
    }
    if review_objective_digest is not None:
        request["review_objective_digest"] = review_objective_digest
    return _result("goal.acceptance.objective", request, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def inspect_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    todo_id: str | None = None,
    include_work_details: bool = True,
    context_agent_id: str | None = None,
    delivery_workspace: PayloadView | None = None,
    validation_workspace_path: Path | None = None,
) -> Payload:
    """Read one canonical basis; command declarations stay inside the host."""
    resolved_registry, goal, route = _routing(registry_path, goal_id, runtime_root, agent_id)
    request: Payload = {**route, "registered_agents": registered_agent_ids_for_goal(goal)}
    if agent_id is not None:
        request["agent_id"] = agent_id
    if todo_id is not None:
        request["todo_id"] = todo_id
    if context_agent_id is not None:
        request["context_agent_id"] = context_agent_id
    if not include_work_details:
        request["include_work_details"] = False
    basis = _result(_INSPECT_METHOD, request)

    raw_contract = basis.get("contract")
    raw_criteria = raw_contract.get("criteria", []) if isinstance(raw_contract, Mapping) else []
    criteria = [cast(PayloadView, item) for item in raw_criteria] if isinstance(raw_criteria, list) else []
    raw_todo = basis.get("todo")
    task_repo = raw_todo.get("task_repository") if isinstance(raw_todo, Mapping) else None
    raw_acceptance = cast(PayloadView, basis["goal_acceptance_contract"])
    has_oracles = any(isinstance(c, Mapping) and c.get("oracle") is not None for c in criteria)
    # Skip deep-copying raw_acceptance when outcome_context is absent or no oracle/test hook can rewrite it.
    captured_contract = (
        deepcopy(raw_acceptance)
        if "outcome_context" in basis
        and (has_oracles or observe_goal_acceptance_oracles is not _DEFAULT_OBSERVE_ORACLES)
        else None
    )
    observed_contract = observe_goal_acceptance_oracles(
        registry_path=resolved_registry,
        goal_id=goal_id,
        contract=raw_acceptance,
        criteria=criteria,
        task_repository=cast(str | None, task_repo),
        delivery_workspace=delivery_workspace,
        validation_workspace_path=validation_workspace_path,
    )
    basis["goal_acceptance_contract"] = observed_contract
    if captured_contract is not None and observed_contract != captured_contract:
        # The optional navigation binds the exact owner's observation. Do not
        # reuse it after host-side oracle readback changes those facts.
        basis.pop("outcome_context", None)
    return basis


def configure_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    expected_provider_revision: str,
    document: PayloadView | None,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    operation_id: str | None = None,
    execute: bool = False,
    review_context_digest: str | None = None,
) -> Payload:
    """Explicit local-owner configuration; Agent tool callers have no writer."""
    _, _, route = _routing(registry_path, goal_id, runtime_root, agent_id)
    request: Payload = {
        **route,
        "actor_agent_id": agent_id,
        "expected_provider_revision": expected_provider_revision,
        "document": dict(document) if document is not None else None,
        "dry_run": not execute,
        "operation_id": operation_id or f"goal-acceptance:{uuid4().hex}",
    }
    if review_context_digest is not None:
        request["review_context_digest"] = review_context_digest
    return _result("goal.acceptance.configure", request)


def public_goal_acceptance(value: PayloadView, *, include_contract: bool = True) -> Payload:
    """Expose projections and revision tokens, never executable declarations."""
    return {
        "ok": True,
        **{
            key: value[key]
            for key in _PUBLIC_ACCEPTANCE_KEYS
            if key in value and (include_contract or key != "goal_acceptance_contract")
        },
    }


def verify_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    execute: bool = False,
    expected_provider_revision: str | None = None,
    operation_id: str | None = None,
) -> Payload:
    """Run the configured acceptance checks against a frozen canonical basis."""
    resolved_registry, _, route = _routing(registry_path, goal_id, runtime_root, agent_id)
    basis = inspect_goal_acceptance(
        registry_path=resolved_registry,
        goal_id=goal_id,
        runtime_root=runtime_root,
        agent_id=agent_id,
    )
    if expected_provider_revision is not None and basis["provider_revision"] != expected_provider_revision:
        raise ValueError("Acceptance changed; reload the current contract before checking.")
    contract = basis.get("contract")
    if contract is None:
        raise ValueError("No executable checks are configured")
    if not isinstance(contract, Mapping):
        raise TypeError("Goal acceptance contract shape is invalid")
    criteria = contract.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("Goal acceptance authority omitted its criteria")
    if not execute:
        return {**public_goal_acceptance(basis), "status": "planned", "executed": False}
    effects = _criterion_effects(criteria)
    receipts = run_goal_acceptance_effects(
        effects=effects,
        registry_path=resolved_registry,
        goal_id=goal_id,
    )
    acceptance_contract = cast(PayloadView, basis["goal_acceptance_contract"])
    result = _result(
        "goal.acceptance.verify.commit",
        {
            **route,
            "expected_provider_revision": basis["provider_revision"],
            "actor_agent_id": None,
            "operation_id": operation_id or f"goal-acceptance-verify:{uuid4().hex}",
            "contract_digest": acceptance_contract["digest"],
            "revision": acceptance_contract["revision"],
            "results": receipts,
        },
    )
    public_result = public_goal_acceptance(result)
    if any("oracle" in criterion for criterion in criteria):
        observed = inspect_goal_acceptance(
            registry_path=resolved_registry,
            goal_id=goal_id,
            runtime_root=runtime_root,
            agent_id=agent_id,
        )
        public_result["goal_acceptance_contract"] = observed["goal_acceptance_contract"]
    raw_result_contract = result.get("goal_acceptance_contract")
    acceptance_ready = (
        isinstance(raw_result_contract, Mapping)
        and raw_result_contract.get("status") == "accepted"
    )
    return {
        **public_result,
        "executed": True,
        "checks_passed": all(row.get("passed") is True for row in receipts),
        "acceptance_ready": acceptance_ready,
    }
