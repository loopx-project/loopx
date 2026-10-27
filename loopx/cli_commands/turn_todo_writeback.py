"""Todo writeback adapters that preserve the Turn's effective runtime root."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from ..control_plane.effect_runtime import EffectRuntimeRejected
from ..control_plane.quota.turn_envelope import _turn_envelope_result

from ..control_plane.todos.mutation_api import complete_goal_todo
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
)


_ABSENT_WORK_CONTRACT = object()


def captured_todo_text_sha256(
    envelope: Mapping[str, Any], *, todo_id: str,
    work_contract: object = _ABSENT_WORK_CONTRACT,
) -> str | None:
    """Use the shared task-source owner; omitted contracts decode old journals."""
    try:
        result = _turn_envelope_result(
            "captured_todo_text", envelope=dict(envelope), todo_id=todo_id,
            **({"payload": work_contract} if work_contract is not _ABSENT_WORK_CONTRACT else {}),
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    digest = result.get("sha256")
    if digest is not None and not isinstance(digest, str):
        raise RuntimeError("Turn captured task source hash must be a string or null")
    return digest


def write_turn_validated_completion(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    completion_turn_key: str,
    evidence: str,
    note: str,
    agent_id: str | None,
    expected_todo_text_sha256: str | None = None,
    completion_delivery_workspace: Mapping[str, Any] | None = None,
    completion_validation_workspace_path: Path | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> dict[str, Any]:
    """Complete one validated Todo under the effective runtime root."""

    try:
        return complete_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            role="agent",
            completion_turn_key=completion_turn_key,
            completion_identity_source="turn_settlement",
            expected_todo_text_sha256=expected_todo_text_sha256,
            require_captured_todo_text=True,
            completion_delivery_workspace=completion_delivery_workspace,
            completion_validation_workspace_path=completion_validation_workspace_path,
            task_lease_idempotency_key=task_lease_idempotency_key,
            task_lease_expected_version=task_lease_expected_version,
            evidence=evidence,
            note=note,
            agent_id=agent_id,
            project=None,
            dry_run=False,
            runtime_root_arg=runtime_root_arg,
        )
    except LocalCoordinationAuthorityRejection as exc:
        # A typed owner rejection proves that this attempt did not write. Lost
        # responses and unavailable authority still raise for receipt recovery.
        diagnostics = dict(exc.payload)
        diagnostics.setdefault("reason_code", exc.code)
        diagnostics.setdefault("reason", str(exc))
        return {
            **diagnostics,
            "ok": False,
            "appended": False,
            "completed": False,
        }
