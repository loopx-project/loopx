"""Host adapter for canonical, owner-configured Goal acceptance.

TypeScript owns revisions, work bindings, admission and CAS. Python resolves
the registered Goal and executes only validation commands read from that owner.
No caller-supplied pass/fail result is accepted by the public CLI.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ...paths import effective_runtime_root
from ..coordination.local_authority import local_authority_is_promoted
from ..effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from ..todos.completion_validation import (
    _resolve_completion_validation_workspace,
    run_declared_completion_validation_effect,
)
from .goal_ref_validation import exact_goal_ref
from .acceptance_oracle_io import oracle_versions, run_snapshot_oracle
from .activation import goal_is_stopped


_INSPECT_METHOD = "goal.acceptance.inspect"


class GoalAcceptanceRejected(ValueError):
    """Carry owner-qualified detail through the existing CLI error boundary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.payload = {"error_detail": detail}


def _routing(
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    goal = load_goal_from_registry(registry_path, goal_id)
    if goal is None:
        raise ValueError("Goal acceptance requires a registered Goal")
    if agent_id is not None and agent_id not in registered_agent_ids_for_goal(goal):
        raise ValueError("Goal acceptance caller must be a registered Agent")
    root = effective_runtime_root(registry_path, runtime_root)
    if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
        raise ValueError(
            "Goal acceptance requires an existing canonical authority; activation never promotes a provider"
        )
    route: dict[str, Any] = {
        "runtime_root": str(root.resolve()),
        "goal_id": goal_id,
    }
    goal_instance_id = goal.get("goal_instance_id")
    if goal_instance_id is not None:
        route["goal_ref"] = exact_goal_ref(goal_id, goal_instance_id)
    return route


def _result(
    method: str,
    request: Mapping[str, Any],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    value = (effect_runtime_result(method, dict(request)) if timeout is None
        else effect_runtime_result(method, dict(request), timeout=timeout))
    if not isinstance(value, dict):
        raise TypeError("Goal acceptance authority returned an invalid result")
    if value.get("status") not in {
        "loaded",
        "applied",
        "recovered",
        "replayed",
        "planned",
        "no_change",
    }:
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
    transition: Mapping[str, Any],
    operation_id: str,
) -> dict[str, Any] | None:
    """Commit one source-owned acceptance lifecycle transition if promoted."""
    root = runtime_root.resolve()
    if not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
        return None
    return _result(
        "goal.acceptance.lifecycle.transition",
        {
            "runtime_root": str(root.resolve()),
            "goal_id": goal_id,
            "actor_agent_id": None,
            "operation_id": operation_id,
            "transition": dict(transition),
        },
        timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    )


def refine_goal_outcome(
    *, registry_path: Path, goal_id: str, refinement: dict[str, Any],
    expected_provider_revision: str, operation_id: str, agent_id: str | None = None,
    runtime_root: str | None = None, execute: bool = False,
) -> dict[str, Any]:
    """Record scoped planning intent, never configure validators or attest success."""
    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    goal = load_goal_from_registry(registry_path, goal_id)
    if "owner_agent_id" in refinement and refinement["owner_agent_id"] not in registered_agent_ids_for_goal(goal):
        raise ValueError("Outcome owner must be a registered Agent")
    # The registry's legacy omitted-status form denotes an active Goal too.
    if goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
        raise ValueError("Outcome refinement requires an active Goal")
    return _result("goal.acceptance.refine", {**route, "actor_agent_id": agent_id,
        "expected_provider_revision": expected_provider_revision, "operation_id": operation_id,
        "refinement": refinement, "dry_run": not execute}, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def update_goal_objective(
    *, registry_path: Path, goal_id: str, document: dict[str, Any], operation: str,
    expected_provider_revision: str, operation_id: str, agent_id: str | None = None,
    runtime_root: str | None = None, execute: bool = False, review_objective_digest: str | None = None,
) -> dict[str, Any]:
    """Adapt explicit owner revisions or a registered Agent's own adoption."""
    from ..work_items.replan_context_codec import goal_context_facts

    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    goal = load_goal_from_registry(registry_path, goal_id)
    if goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
        raise ValueError("Objective updates require an active Goal")
    return _result("goal.acceptance.objective", {**route, "actor_agent_id": agent_id,
        "expected_provider_revision": expected_provider_revision, "operation_id": operation_id,
        "operation": operation, "document": document, "dry_run": not execute,
        **({"review_objective_digest": review_objective_digest} if review_objective_digest is not None else {}),
        "source_objective": goal_context_facts({"registry": str(registry_path)}, goal_id, None),
        "registered_agents": registered_agent_ids_for_goal(goal)}, timeout=CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS)


def inspect_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    todo_id: str | None = None,
    include_work_details: bool = True,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Read one canonical basis; command declarations stay inside the host."""
    basis = _result(
        _INSPECT_METHOD,
        {**_routing(registry_path, goal_id, runtime_root, agent_id),
         **({"agent_id": agent_id} if agent_id is not None else {}),
         **({"todo_id": todo_id} if todo_id is not None else {}),
         **({"include_work_details": False} if not include_work_details else {})},
    )
    basis["goal_acceptance_contract"] = observe_goal_acceptance_oracles(
        registry_path=registry_path, goal_id=goal_id,
        contract=basis["goal_acceptance_contract"], criteria=(basis.get("contract") or {}).get("criteria", []),
        task_repository=(basis.get("todo") or {}).get("task_repository"),
        delivery_workspace=delivery_workspace, validation_workspace_path=validation_workspace_path)
    return basis


def observe_goal_acceptance_oracles(
    *, registry_path: Path | None, goal_id: str, contract: dict[str, Any],
    criteria: list[dict[str, Any]], task_repository: str | None = None,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Observe pinned refs supplied by one canonical read, without another read.

    Inspect and status share this host IO. The caller retains the same snapshot
    for Todos, checks and observation inputs; TS still classifies the evidence.
    """
    observations = []
    for criterion in criteria:
        oracle = criterion.get("oracle")
        if oracle is None:
            continue
        workspace, failure = (None, True) if registry_path is None else _resolve_completion_validation_workspace(
            registry_path=registry_path, goal_id=goal_id, task_repository=task_repository,
            delivery_workspace=delivery_workspace, validation_workspace_path=validation_workspace_path, label=criterion["id"])
        versions = None if failure else oracle_versions(workspace, oracle)
        observations.append({"criterion_id": criterion["id"], "available": versions is not None,
            "source_current": _acceptance_pins_match(criterion["validation_files"], workspace),
            **(versions or {})})
    return effect_runtime_result("goal.acceptance.oracle.observe", {
        "contract": contract, "observations": observations,
    }) if observations else contract


def observe_canonical_goal_acceptance(
    canonical: dict[str, Any], *, registry_path: Path | None, goal_id: str,
) -> dict[str, Any] | None:
    """Add current host observations to this exact canonical planning read.

    The private basis only carries configured oracle refs. The original source
    snapshot and its provider identity remain unchanged for historical replay.
    """
    acceptance = canonical.get("goal_acceptance_contract")
    oracle_basis = canonical.get("goal_acceptance_oracle_observation_basis")
    if oracle_basis is None:
        return acceptance
    if not isinstance(acceptance, dict) or not isinstance(oracle_basis, dict) or any(
        oracle_basis.get(key) != acceptance.get(key) for key in ("revision", "digest")
    ) or not isinstance(oracle_basis.get("criteria"), list):
        raise ValueError("oracle observation basis does not match the canonical acceptance snapshot")
    return observe_goal_acceptance_oracles(registry_path=registry_path, goal_id=goal_id,
        contract=acceptance, criteria=oracle_basis["criteria"])


def _criterion_effects(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "caller_validation",
            "criterion_id": row["id"],
            "validation_argv": row["validation_argv"],
            "validation_label": f"Goal acceptance: {row['id']}",
            "validation_timeout_seconds": row.get("validation_timeout_seconds", 29),
            "validation_files": row.get("validation_files", []),
            **({"oracle": row["oracle"]} if "oracle" in row else {}),
        }
        for row in criteria
    ]


def goal_task_validation_files_current(
    *, registry_path: Path, runtime_root: str, goal_id: str, agent_id: str, todo_id: str,
) -> bool:
    """Check declared validator assets without executing output validation."""
    route = {**_routing(registry_path, goal_id, runtime_root, agent_id), "todo_id": todo_id}
    basis = _result(_INSPECT_METHOD, route).get("completion_requirements")
    if not isinstance(basis, dict) or not basis.get("criteria"):
        return False
    return validation_effect_files_current(
        effects=_criterion_effects(basis["criteria"]), registry_path=registry_path,
        goal_id=goal_id,
    )


def validation_effect_files_current(
    *, effects: list[dict[str, Any]], registry_path: Path, goal_id: str,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> bool:
    """Observe verifier assets and explicit repository workspaces for a typed plan."""
    for effect in effects:
        pins = effect.get("validation_files", [])
        workspace = None
        if pins or effect.get("task_repository"):
            workspace, failure = _resolve_completion_validation_workspace(
                registry_path=registry_path, goal_id=goal_id,
                task_repository=effect.get("task_repository"),
                delivery_workspace=delivery_workspace,
                validation_workspace_path=validation_workspace_path,
                label=effect["validation_label"],
            )
            if failure is not None:
                return False
        if not _acceptance_pins_match(pins, workspace):
            return False
    return True


def validate_goal_task_acceptance(
    *, registry_path: Path, runtime_root: str, goal_id: str, agent_id: str, todo_id: str,
) -> dict[str, Any]:
    """Read-only Turn validator for a task's current owner-pinned criteria.

    Completion still runs its own fresh validation and atomic TS commit. This
    entrypoint cannot accept supplied commands, pass flags or saved receipts.
    """
    route = {**_routing(registry_path, goal_id, runtime_root, agent_id), "todo_id": todo_id}
    basis = _result(_INSPECT_METHOD, route).get("completion_requirements")
    if not isinstance(basis, dict) or not basis.get("criteria"):
        raise ValueError("delegated task requires configured owner-bound checks")
    results = run_goal_acceptance_effects(
        effects=_criterion_effects(basis["criteria"]),
        registry_path=registry_path, goal_id=goal_id,
    )
    current = _result(_INSPECT_METHOD, route).get("completion_requirements")
    if current != basis:
        raise ValueError("delegated task acceptance changed during validation")
    return {"passed": all(row["passed"] for row in results), "results": results}


def configure_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    expected_provider_revision: str,
    document: dict[str, Any] | None,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    operation_id: str | None = None,
    execute: bool = False,
    review_context_digest: str | None = None,
) -> dict[str, Any]:
    """Explicit local-owner configuration; Agent tool callers have no writer."""
    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    return _result(
        "goal.acceptance.configure",
        {
            **route,
            "actor_agent_id": agent_id,
            "expected_provider_revision": expected_provider_revision,
            "document": document,
            "dry_run": not execute,
            "operation_id": operation_id or f"goal-acceptance:{uuid4().hex}",
            **({"review_context_digest": review_context_digest} if review_context_digest is not None else {}),
        },
    )


def run_goal_acceptance_effects(
    *,
    effects: list[dict[str, Any]],
    registry_path: Path,
    goal_id: str,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Execute a finite plan supplied by the typed authority, never by CLI JSON."""
    results = []
    for effect in effects:
        criterion_id = effect.get("criterion_id")
        if not isinstance(criterion_id, str) or not criterion_id:
            raise ValueError("Goal acceptance effect requires a criterion identity")
        receipt = run_goal_acceptance_validation_effect(
            effect=effect,
            registry_path=registry_path,
            goal_id=goal_id,
            delivery_workspace=delivery_workspace,
            validation_workspace_path=validation_workspace_path,
        )
        exit_code = receipt["exit_code"]
        results.append(
            {
                "criterion_id": criterion_id,
                "passed": receipt["passed"],
                "exit_code": exit_code
                if isinstance(exit_code, int) and 0 <= exit_code <= 255
                else None,
                **({"oracle_evidence": receipt["oracle_evidence"]} if "oracle_evidence" in receipt else {}),
            }
        )
    return results


def _acceptance_pins_match(pins: list[dict[str, Any]], workspace: Path | None) -> bool:
    if not pins:
        return True
    if workspace is None:
        return False
    for pin in pins:
        if not isinstance(pin, dict):
            return False
        relative = pin.get("path")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            return False
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return False
        candidate = workspace
        try:
            for part in path.parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    return False
            if not candidate.is_file() or hashlib.sha256(
                candidate.read_bytes()
            ).hexdigest() != pin.get("sha256"):
                return False
        except OSError:
            return False
    return True


def run_goal_acceptance_validation_effect(
    *,
    effect: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    delivery_workspace: Mapping[str, Any] | None = None,
    validation_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Use the ordinary runner while preserving declared verifier-file identity.

    Pins cover explicitly declared verifier assets, not inferred dependencies.
    The local execution environment remains the existing host trust boundary.
    """
    pins = effect.get("validation_files", [])
    if not isinstance(pins, list):
        raise TypeError("acceptance validation_files must be an array")
    label = str(effect.get("validation_label") or "Goal acceptance")
    workspace = None
    if pins or effect.get("oracle"):
        workspace, failure = _resolve_completion_validation_workspace(
            registry_path=registry_path,
            goal_id=goal_id,
            task_repository=effect.get("task_repository"),
            delivery_workspace=delivery_workspace,
            validation_workspace_path=validation_workspace_path,
            label=label,
        )
        if failure is not None:
            return failure

    def stale_receipt() -> dict[str, Any]:
        return {
            "schema_version": "issue_fix_validation_command_v0",
            "command_label": label,
            "exit_code": None,
            "passed": False,
            "status": "validation_basis_changed",
            "summary": "Declared verifier files changed or are unavailable; review and reconfigure the acceptance basis.",
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
        }

    if not _acceptance_pins_match(pins, workspace):
        return stale_receipt()
    oracle = effect.get("oracle")
    plan = effect_runtime_result("goal.acceptance.oracle.plan", {"criterion": dict(effect)}) if oracle else None
    before = oracle_versions(workspace, oracle) if oracle else None
    if oracle and before is None:
        return stale_receipt()
    if oracle:
        if workspace is None or before is None or plan is None:
            return stale_receipt()
        try:
            receipt, stable_snapshot = run_snapshot_oracle(workspace=workspace, criterion=dict(effect), before=before)
        except OSError:
            return stale_receipt()
        if not stable_snapshot:
            return stale_receipt()
    else:
        receipt = run_declared_completion_validation_effect(
            effect={key: value for key, value in effect.items() if key != "validation_files"},
            registry_path=registry_path, goal_id=goal_id,
            delivery_workspace=delivery_workspace, validation_workspace_path=validation_workspace_path,
        )
    if not _acceptance_pins_match(pins, workspace):
        return stale_receipt()
    if oracle:
        after = oracle_versions(workspace, oracle)
        if after is None or after != before:
            return stale_receipt()
        if receipt["passed"] and plan is not None:
            receipt["oracle_evidence"] = {"source_id": plan["source_id"], "source_digest": plan["source_digest"],
                "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), **after}
    return receipt


def public_goal_acceptance(value: dict[str, Any]) -> dict[str, Any]:
    """Expose projections and revision tokens, never executable declarations."""
    keys = (
        "status",
        "goal_id",
        "provider_revision",
        "source_authority",
        "changed",
        "dry_run",
        "operation_id",
        "goal_acceptance_contract",
        "objective_work_digest",
        "goal_objective_guard",
        "reason_code",
    )
    return {"ok": True, **{key: value[key] for key in keys if key in value}}


def verify_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
    execute: bool = False,
    expected_provider_revision: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Run the configured acceptance checks against a frozen canonical basis."""
    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    basis = inspect_goal_acceptance(registry_path=registry_path, goal_id=goal_id, runtime_root=runtime_root, agent_id=agent_id)
    if expected_provider_revision is not None and basis["provider_revision"] != expected_provider_revision:
        raise ValueError("Acceptance changed; reload the current contract before checking.")
    contract = basis.get("contract")
    if contract is None:
        raise ValueError("No executable checks are configured")
    if not isinstance(contract, dict):
        raise TypeError("Goal acceptance contract shape is invalid")
    criteria = contract.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("Goal acceptance authority omitted its criteria")
    if not execute:
        return {**public_goal_acceptance(basis), "status": "planned", "executed": False}
    effects = _criterion_effects(criteria)
    receipts = run_goal_acceptance_effects(
        effects=effects, registry_path=registry_path, goal_id=goal_id
    )
    result = _result(
        "goal.acceptance.verify.commit",
        {
            **route,
            "expected_provider_revision": basis["provider_revision"],
            "actor_agent_id": None,
            "operation_id": operation_id or f"goal-acceptance-verify:{uuid4().hex}",
            "contract_digest": basis["goal_acceptance_contract"]["digest"],
            "revision": basis["goal_acceptance_contract"]["revision"],
            "results": receipts,
        },
    )
    public_result = public_goal_acceptance(result)
    if any("oracle" in criterion for criterion in criteria):
        observed = inspect_goal_acceptance(registry_path=registry_path, goal_id=goal_id,
            runtime_root=runtime_root, agent_id=agent_id)
        public_result["goal_acceptance_contract"] = observed["goal_acceptance_contract"]
    return {
        **public_result,
        "executed": True,
        "checks_passed": all(row.get("passed") is True for row in receipts),
        "acceptance_ready": result.get("goal_acceptance_contract", {}).get("status")
        == "accepted",
    }
