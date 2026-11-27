"""Verification, oracle readback and verifier-pin guards for Goal acceptance.

Host execution inspects workspace files and pinned snapshot oracles without
replacing the TypeScript owner's contract revisions or evidence judgments.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ..effect_runtime import effect_runtime_result
from ..todos.completion_validation import (
    _resolve_completion_validation_workspace,
    run_declared_completion_validation_effect,
)
from .acceptance_oracle_io import oracle_versions, run_snapshot_oracle

Payload = dict[str, object]
PayloadView = Mapping[str, object]


def _oracle_dict(oracle: object) -> Payload:
    if isinstance(oracle, dict):
        return cast(Payload, oracle)
    if isinstance(oracle, Mapping):
        return dict(cast(PayloadView, oracle))
    return cast(Payload, oracle)


def observe_goal_acceptance_oracles(
    *,
    registry_path: Path | None,
    goal_id: str,
    contract: PayloadView,
    criteria: Sequence[PayloadView],
    task_repository: str | None = None,
    delivery_workspace: PayloadView | None = None,
    validation_workspace_path: Path | None = None,
) -> Payload:
    """Observe pinned refs supplied by one canonical read, without another read.

    Inspect and status share this host IO. The caller retains the same snapshot
    for Todos, checks and observation inputs; TS still classifies the evidence.
    """
    observations: list[Payload] = []
    for criterion in criteria:
        oracle = criterion.get("oracle")
        if oracle is None:
            continue
        workspace, failure = (
            (None, True)
            if registry_path is None
            else _resolve_completion_validation_workspace(
                registry_path=registry_path,
                goal_id=goal_id,
                task_repository=task_repository,
                delivery_workspace=delivery_workspace,
                validation_workspace_path=validation_workspace_path,
                label=cast(str, criterion["id"]),
            )
        )
        versions = None if failure else oracle_versions(workspace, _oracle_dict(oracle))
        observations.append(
            {
                "criterion_id": criterion["id"],
                "available": versions is not None,
                "source_current": _acceptance_pins_match(criterion["validation_files"], workspace),
                **(versions or {}),
            }
        )
    if observations:
        return cast(
            Payload,
            effect_runtime_result(
                "goal.acceptance.oracle.observe",
                {"contract": dict(contract), "observations": observations},
            ),
        )
    return dict(contract)


def observe_canonical_goal_acceptance(
    canonical: PayloadView,
    *,
    registry_path: Path | None,
    goal_id: str,
) -> Payload | None:
    """Add current host observations to this exact canonical planning read.

    The private basis only carries configured oracle refs. The original source
    snapshot and its provider identity remain unchanged for historical replay.
    """
    acceptance = canonical.get("goal_acceptance_contract")
    oracle_basis = canonical.get("goal_acceptance_oracle_observation_basis")
    if oracle_basis is None:
        if acceptance is None:
            return None
        return dict(cast(PayloadView, acceptance)) if isinstance(acceptance, Mapping) else cast(Payload, acceptance)
    if (
        not isinstance(acceptance, Mapping)
        or not isinstance(oracle_basis, Mapping)
        or any(oracle_basis.get(key) != acceptance.get(key) for key in ("revision", "digest"))
        or not isinstance(oracle_basis.get("criteria"), list)
    ):
        raise ValueError("oracle observation basis does not match the canonical acceptance snapshot")
    return observe_goal_acceptance_oracles(
        registry_path=registry_path,
        goal_id=goal_id,
        contract=acceptance,
        criteria=cast(list[PayloadView], oracle_basis["criteria"]),
    )


def _criterion_effects(criteria: Sequence[PayloadView]) -> list[Payload]:
    effects: list[Payload] = []
    for row in criteria:
        effect: Payload = {
            "kind": "caller_validation",
            "criterion_id": row["id"],
            "validation_argv": row["validation_argv"],
            "validation_label": f"Goal acceptance: {row['id']}",
            "validation_timeout_seconds": row.get("validation_timeout_seconds", 29),
            "validation_files": row.get("validation_files", []),
        }
        if "oracle" in row:
            effect["oracle"] = row["oracle"]
        effects.append(effect)
    return effects


def validation_effect_files_current(
    *,
    effects: Sequence[PayloadView],
    registry_path: Path,
    goal_id: str,
    delivery_workspace: PayloadView | None = None,
    validation_workspace_path: Path | None = None,
) -> bool:
    """Observe verifier assets and explicit repository workspaces for a typed plan."""
    for effect in effects:
        pins = effect.get("validation_files", [])
        workspace = None
        if pins or effect.get("task_repository"):
            workspace, failure = _resolve_completion_validation_workspace(
                registry_path=registry_path,
                goal_id=goal_id,
                task_repository=cast(str | None, effect.get("task_repository")),
                delivery_workspace=delivery_workspace,
                validation_workspace_path=validation_workspace_path,
                label=cast(str, effect["validation_label"]),
            )
            if failure is not None:
                return False
        if not _acceptance_pins_match(pins, workspace):
            return False
    return True


def run_goal_acceptance_effects(
    *,
    effects: Sequence[PayloadView],
    registry_path: Path,
    goal_id: str,
    delivery_workspace: PayloadView | None = None,
    validation_workspace_path: Path | None = None,
) -> list[Payload]:
    """Execute a finite plan supplied by the typed authority, never by CLI JSON."""
    results: list[Payload] = []
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
        row: Payload = {
            "criterion_id": criterion_id,
            "passed": receipt["passed"],
            "exit_code": exit_code if isinstance(exit_code, int) and 0 <= exit_code <= 255 else None,
        }
        if "oracle_evidence" in receipt:
            row["oracle_evidence"] = receipt["oracle_evidence"]
        results.append(row)
    return results


def _acceptance_pins_match(pins: object, workspace: Path | None) -> bool:
    if not pins:
        return True
    if workspace is None or not isinstance(pins, list):
        return False
    for pin in pins:
        if not isinstance(pin, Mapping):
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
            if not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != pin.get("sha256"):
                return False
        except OSError:
            return False
    return True


def _stale_acceptance_receipt(label: str) -> Payload:
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


def run_goal_acceptance_validation_effect(
    *,
    effect: PayloadView,
    registry_path: Path,
    goal_id: str,
    delivery_workspace: PayloadView | None = None,
    validation_workspace_path: Path | None = None,
) -> Payload:
    """Use the ordinary runner while preserving declared verifier-file identity.

    Pins cover explicitly declared verifier assets, not inferred dependencies.
    The local execution environment remains the existing host trust boundary.
    """
    pins = effect.get("validation_files", [])
    if not isinstance(pins, list):
        raise TypeError("acceptance validation_files must be an array")
    label = str(effect.get("validation_label") or "Goal acceptance")
    oracle = effect.get("oracle")
    workspace = None
    if pins or oracle:
        workspace, failure = _resolve_completion_validation_workspace(
            registry_path=registry_path,
            goal_id=goal_id,
            task_repository=cast(str | None, effect.get("task_repository")),
            delivery_workspace=delivery_workspace,
            validation_workspace_path=validation_workspace_path,
            label=label,
        )
        if failure is not None:
            return cast(Payload, failure)

    if not _acceptance_pins_match(pins, workspace):
        return _stale_acceptance_receipt(label)

    effect_dict = dict(effect)
    oracle_dict = _oracle_dict(oracle) if oracle else None
    plan = (
        cast(Payload, effect_runtime_result("goal.acceptance.oracle.plan", {"criterion": effect_dict}))
        if oracle_dict
        else None
    )
    before = oracle_versions(workspace, oracle_dict) if oracle_dict else None
    if oracle_dict:
        if workspace is None or before is None or plan is None:
            return _stale_acceptance_receipt(label)
        try:
            receipt, stable_snapshot = run_snapshot_oracle(
                workspace=workspace, criterion=effect_dict, before=before
            )
        except OSError:
            return _stale_acceptance_receipt(label)
        if not stable_snapshot:
            return _stale_acceptance_receipt(label)
    else:
        receipt = run_declared_completion_validation_effect(
            effect={key: value for key, value in effect.items() if key != "validation_files"},
            registry_path=registry_path,
            goal_id=goal_id,
            delivery_workspace=delivery_workspace,
            validation_workspace_path=validation_workspace_path,
        )
    if not _acceptance_pins_match(pins, workspace):
        return _stale_acceptance_receipt(label)
    if oracle_dict:
        after = oracle_versions(workspace, oracle_dict)
        if after is None or after != before:
            return _stale_acceptance_receipt(label)
        if receipt["passed"] and plan is not None:
            receipt["oracle_evidence"] = {
                "source_id": plan["source_id"],
                "source_digest": plan["source_digest"],
                "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **after,
            }
    return receipt
