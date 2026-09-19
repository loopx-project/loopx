"""Host adapter for canonical, owner-configured Goal acceptance.

TypeScript owns revisions, work bindings, admission and CAS. Python resolves
the registered Goal and executes only validation commands read from that owner.
No caller-supplied pass/fail result is accepted by the public CLI.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..coordination.local_authority import local_authority_is_promoted
from ..coordination.local_authority_shadow_adapter import effective_runtime_root
from ..effect_runtime import effect_runtime_result
from ..todos.completion_validation import (
    _resolve_completion_validation_workspace,
    run_declared_completion_validation_effect,
)


_INSPECT_METHOD = "goal.acceptance.inspect"

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
    return {"runtime_root": str(root.resolve()), "goal_id": goal_id}


def _result(method: str, request: Mapping[str, Any]) -> dict[str, Any]:
    value = effect_runtime_result(method, dict(request))
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
        raise ValueError(
            str(
                value.get("reason_code")
                or value.get("reason")
                or "Goal acceptance authority is unavailable"
            )
        )
    return value


def inspect_goal_acceptance(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Read one canonical basis; command declarations stay inside the host."""
    return _result(
        _INSPECT_METHOD,
        _routing(registry_path, goal_id, runtime_root, agent_id),
    )


def _criterion_effects(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "caller_validation",
            "criterion_id": row["id"],
            "validation_argv": row["validation_argv"],
            "validation_label": f"Goal acceptance: {row['id']}",
            "validation_timeout_seconds": row.get("validation_timeout_seconds", 29),
            "validation_files": row.get("validation_files", []),
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
    for effect in _criterion_effects(basis["criteria"]):
        pins = effect["validation_files"]
        workspace = None
        if pins:
            workspace, failure = _resolve_completion_validation_workspace(
                registry_path=registry_path, goal_id=goal_id,
                task_repository=effect.get("task_repository"), delivery_workspace=None,
                validation_workspace_path=None, label=effect["validation_label"],
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
        raise ValueError("delegated task requires enabled owner-bound acceptance")
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
    disable: bool = False,
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
            "disable": disable,
            "dry_run": not execute,
            "operation_id": operation_id or f"goal-acceptance:{uuid4().hex}",
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
            }
        )
    return results


def _acceptance_pins_match(pins: list, workspace: Path | None) -> bool:
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
    if pins:
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
    receipt = run_declared_completion_validation_effect(
        effect={
            key: value for key, value in effect.items() if key != "validation_files"
        },
        registry_path=registry_path,
        goal_id=goal_id,
        delivery_workspace=delivery_workspace,
        validation_workspace_path=validation_workspace_path,
    )
    return receipt if _acceptance_pins_match(pins, workspace) else stale_receipt()


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
) -> dict[str, Any]:
    """Run the configured acceptance checks against a frozen canonical basis."""
    route = _routing(registry_path, goal_id, runtime_root, agent_id)
    basis = _result(_INSPECT_METHOD, route)
    contract = basis.get("contract")
    if contract is None:
        raise ValueError("Goal acceptance is not enabled")
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
            "operation_id": f"goal-acceptance-verify:{uuid4().hex}",
            "contract_digest": basis["goal_acceptance_contract"]["digest"],
            "revision": basis["goal_acceptance_contract"]["revision"],
            "results": receipts,
        },
    )
    return {
        **public_goal_acceptance(result),
        "executed": True,
        "checks_passed": all(row.get("passed") is True for row in receipts),
        "acceptance_ready": result.get("goal_acceptance_contract", {}).get("status")
        == "accepted",
    }
