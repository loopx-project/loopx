"""Todo writeback adapters that preserve the Turn's effective runtime root."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..control_plane.todos.mutation_api import complete_goal_todo, update_goal_todo


def write_turn_repair_update(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    note: str,
    evidence: str,
    agent_id: str | None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> None:
    """Record one repair-required Todo note under the effective runtime root.

    ``runtime_root_arg`` is required on purpose: the Turn settlement must
    hand down the same ``--runtime-root`` override the dispatch resolved, so
    the canonical provider route and its maintenance guard cannot split from
    the Turn writeback path.
    """

    update_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        role="agent",
        note=note,
        evidence=evidence,
        agent_id=agent_id,
        project=None,
        dry_run=False,
        runtime_root_arg=runtime_root_arg,
        task_lease_idempotency_key=task_lease_idempotency_key,
        task_lease_expected_version=task_lease_expected_version,
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
    completion_delivery_workspace: Mapping[str, Any] | None = None,
    completion_validation_workspace_path: Path | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
) -> dict[str, Any]:
    """Complete one validated Todo under the effective runtime root."""
    from ..control_plane.goals.planned_transfer_io import read_reviewed_transfer_result_file

    result_file = read_reviewed_transfer_result_file(registry_path=registry_path, goal_id=goal_id,
        todo_id=todo_id, agent_id=agent_id, workspace=completion_validation_workspace_path,
        runtime_root_arg=runtime_root_arg)

    return complete_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        role="agent",
        completion_turn_key=completion_turn_key,
        completion_identity_source="turn_settlement",
        completion_delivery_workspace=completion_delivery_workspace,
        completion_validation_workspace_path=completion_validation_workspace_path,
        completion_result_file=result_file,
        task_lease_idempotency_key=task_lease_idempotency_key,
        task_lease_expected_version=task_lease_expected_version,
        evidence=evidence,
        note=note,
        agent_id=agent_id,
        project=None,
        dry_run=False,
        runtime_root_arg=runtime_root_arg,
    )
