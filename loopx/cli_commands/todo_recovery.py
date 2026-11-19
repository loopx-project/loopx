"""Typed claim-only execution recovery for `loopx todo` lifecycle commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
from pathlib import Path
import sys

from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    LocalCoordinationAuthorityUnavailable,
)
from ..control_plane.todos.mutation_api import update_goal_todo


def todo_path_args(args: argparse.Namespace) -> dict[str, Path | None]:
    return {
        "project": Path(args.project).expanduser() if args.project else None,
        "state_file": Path(args.state_file).expanduser() if args.state_file else None,
    }


def _recovery_claim_operation_id(
    args: argparse.Namespace,
    *,
    completion_turn_key: str | None = None,
) -> str | None:
    command = getattr(args, "todo_command", None)
    if command == "complete":
        scope = str(completion_turn_key or "unscoped")
    elif command == "update" and getattr(args, "update_operation_id", None):
        scope = str(args.update_operation_id)
    else:
        return None
    digest = hashlib.sha256(
        (
            "loopx-todo-execution-recovery-claim-v0\0"
            f"{command}\0{args.goal_id}\0{args.todo_id}\0"
            f"{args.agent_id}\0{scope}"
        ).encode("utf-8")
    ).hexdigest()
    return f"todo-claim:recover:{digest[:32]}"


def _recover_todo_execution_once(
    args: argparse.Namespace,
    initial_error: LocalCoordinationAuthorityRejection,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    retry_operation: Callable[[str], dict[str, object]],
    completion_turn_key: str | None = None,
) -> dict[str, object]:
    """Consume only the typed, claim-only lifecycle recovery in one CLI call.

    The TypeScript lifecycle owner decides whether a new ordinary claim is a
    legal repair.
    Recovery that requires reconciliation/inspection
    remains visible to the caller instead of being silently rebased.
    """

    recovery_claim_operation_id = _recovery_claim_operation_id(
        args,
        completion_turn_key=completion_turn_key,
    )
    if (
        initial_error.code == "coordination_operation_identity_mismatch"
        and recovery_claim_operation_id is not None
    ):
        try:
            result = retry_operation(recovery_claim_operation_id)
        except LocalCoordinationAuthorityRejection:
            raise initial_error from None
        result["execution_recovery"] = {
            "schema_version": "todo_execution_recovery_v0",
            "status": "replayed" if result.get("idempotent_replay") else "recovered",
            "initial_error_code": initial_error.code,
            "claim_operation_id": recovery_claim_operation_id,
            "claim_status": "replayed",
            "external_invocations": 1,
            "external_failed_calls": 0,
        }
        return result

    recovery = initial_error.payload.get("recovery")
    if not isinstance(recovery, dict):
        raise initial_error
    claim = recovery.get("claim")
    retry = recovery.get("retry")
    if not (
        recovery.get("action") == "claim_task"
        and recovery.get("execution_authority_granted") is False
        and isinstance(claim, dict)
        and isinstance(retry, dict)
        and retry.get("reference_source") == "claim_operation_id_from_claim"
        and retry.get("requires_flags") == ["--claim-operation-id"]
        and "inspect" not in recovery
    ):
        raise initial_error

    todo_module = sys.modules.get("loopx.cli_commands.todo")
    claim_todo = (
        getattr(todo_module, "update_goal_todo", update_goal_todo)
        if todo_module is not None
        else update_goal_todo
    )
    prepared = claim_todo(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        goal_id=args.goal_id,
        todo_id=args.todo_id,
        role=args.role,
        claimed_by=args.agent_id,
        agent_id=args.agent_id,
        claim_only=True,
        claim_operation_id=recovery_claim_operation_id,
        manage_execution=True,
        **todo_path_args(args),
        dry_run=False,
    )
    claim_operation_id = str(prepared.get("claim_operation_id") or "").strip()
    if not (
        prepared.get("ok") is True
        and prepared.get("execution_managed") is True
        and claim_operation_id
    ):
        raise LocalCoordinationAuthorityUnavailable(
            "ordinary Todo claim returned without a usable execution reference",
            code="todo_execution_recovery_claim_incomplete",
            payload={
                "execution_recovery": {
                    "schema_version": "todo_execution_recovery_v0",
                    "status": "claim_incomplete",
                    "initial_error_code": initial_error.code,
                    "claim_status": prepared.get("status"),
                }
            },
        )
    try:
        result = retry_operation(claim_operation_id)
    except Exception as retry_error:
        retry_code = getattr(retry_error, "code", type(retry_error).__name__)
        retry_payload = getattr(retry_error, "payload", None)
        raise LocalCoordinationAuthorityUnavailable(
            "Todo execution claim committed but the unchanged lifecycle retry failed",
            code="todo_execution_recovery_retry_failed",
            payload={
                "execution_recovery": {
                    "schema_version": "todo_execution_recovery_v0",
                    "status": "claim_committed_retry_failed",
                    "initial_error_code": initial_error.code,
                    "claim_operation_id": claim_operation_id,
                    "claim_status": prepared.get("status"),
                    "retry_error_code": str(retry_code),
                },
                **(
                    {"retry_failure": dict(retry_payload)}
                    if isinstance(retry_payload, dict)
                    else {}
                ),
            },
        ) from retry_error
    result["execution_recovery"] = {
        "schema_version": "todo_execution_recovery_v0",
        "status": "recovered",
        "initial_error_code": initial_error.code,
        "claim_operation_id": claim_operation_id,
        "claim_status": prepared.get("status"),
        "external_invocations": 1,
        "external_failed_calls": 0,
    }
    return result
