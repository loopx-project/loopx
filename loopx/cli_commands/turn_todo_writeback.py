"""Native Todo completion writeback transport."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..control_plane.todos.mutation_api import complete_goal_todo
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
)


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
