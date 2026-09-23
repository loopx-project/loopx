from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
)
from ..control_plane.todos.external_wait_contract import TodoExternalWaitAuthoringError
from ..control_plane.todos.handoff_mode import HandoffModeError
from ..control_plane.todos.contract import decision_scope_metadata_value
from ..control_plane.work_items.task_lease import TaskLeaseError
from ..file_lock import lock_timeout_error_fields
from ..control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced
from ..control_plane.coordination.shadow_management import ShadowManagementError
from ..control_plane.coordination.runtime_shadow_writer_adapter import ActiveStateAuthorityMutationError
from ..control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable


RolloutEventAppender = Callable[..., dict[str, object]]

TODO_EVENT_KINDS = {
    "add": "todo_add",
    "claim": "todo_claim",
    "update": "todo_update",
    "complete": "todo_complete",
    "supersede": "todo_supersede",
    "archive-completed": "todo_archive_completed",
}


def todo_error_payload(args: argparse.Namespace, exc: Exception) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": False,
        "dry_run": not bool(args.execute)
        if args.todo_command in {"archive-completed", "project-markdown"}
        else bool(args.dry_run),
        "added": False,
        "already_exists": False,
        "goal_id": args.goal_id,
        "role": args.role,
        "todo": args.text or "",
        "error": str(exc),
        **lock_timeout_error_fields(exc),
    }
    if isinstance(exc, (TaskLeaseError, HandoffModeError, LegacyCoordinationWriterFenced, ShadowManagementError, ActiveStateAuthorityMutationError, LocalCoordinationAuthorityUnavailable)):
        payload["error_code"] = exc.code
        payload.update(exc.payload)
    elif isinstance(exc, LocalCoordinationAuthorityRejection):
        payload["error_code"] = exc.code
        payload["code"] = exc.code
        for key, value in exc.payload.items():
            if key not in {
                "schema_version",
                "status",
                "failure_kind",
                "reason_code",
                "reason",
            }:
                payload[key] = value
    elif isinstance(exc, TodoExternalWaitAuthoringError):
        payload["error_code"] = exc.code
        if exc.authoring_contract is not None:
            payload["authoring_contract"] = exc.authoring_contract
    return payload


def append_todo_rollout_event(
    payload: dict[str, object],
    *,
    args: argparse.Namespace,
    registry_path: Path,
    runtime_root_arg: str | None,
    append_cli_rollout_event: RolloutEventAppender,
) -> None:
    turn_instance_id = getattr(args, "turn_instance_id", None)
    terminal_closeout = bool(
        turn_instance_id
        and args.todo_command == "complete"
        and getattr(args, "no_follow_up", False)
    )
    if (
        args.todo_command == "receipt"
        or not payload.get("ok")
        or payload.get("dry_run")
        or (payload.get("idempotent_replay") and not turn_instance_id)
    ):
        return
    append_cli_rollout_event(
        payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        event_kind=TODO_EVENT_KINDS.get(args.todo_command, "todo_update"),
        agent_id=args.agent_id or args.claimed_by,
        todo_id=args.todo_id or str(payload.get("todo_id") or "").strip() or None,
        run_id=turn_instance_id,
        status=(
            "terminal_no_followup"
            if terminal_closeout
            else str(payload.get("status") or args.todo_command or "").strip()
        ),
        summary=(
            f"todo {args.todo_command} recorded for "
            f"{payload.get('todo_id') or args.todo_id or 'unstructured todo'}"
        ),
        details={
            "command": "todo",
            "todo_command": args.todo_command,
            "role": payload.get("role") or args.role or "",
            "task_class": (
                payload.get("task_class")
                or getattr(args, "task_class", None)
                or ""
            ),
            "decision_scope": decision_scope_metadata_value(
                payload.get("decision_scope")
            ),
            "decision_outcome": payload.get("decision_outcome"),
            "changed": bool(payload.get("changed")),
            "added": bool(payload.get("added")),
            "already_exists": bool(payload.get("already_exists")),
            "no_followup": bool(getattr(args, "no_follow_up", False)),
            "completion_continuation": payload.get("completion_continuation"),
            "completion_recovery": payload.get("completion_recovery"),
            "mutation_authority": payload.get("mutation_authority"),
            "replan_transition": payload.get("replan_transition"),
            "settlement_effect_id": (
                payload.get("settlement_identity", {}).get("effect_id")
                if isinstance(payload.get("settlement_identity"), dict)
                else None
            ),
        },
        idempotency_fields=(
            [
                "goal_id",
                "event_kind",
                "agent_id",
                "todo_id",
                "run_id",
                *(["status"] if terminal_closeout else []),
            ]
            if turn_instance_id
            else None
        ),
    )
    capability_gap_status = str(
        getattr(args, "capability_gap_status", None) or ""
    ).strip()
    if not capability_gap_status:
        return
    gap_payload: dict[str, object] = {
        "ok": True,
        "goal_id": payload.get("goal_id"),
    }
    append_cli_rollout_event(
        gap_payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        event_kind="capability_gap",
        agent_id=args.agent_id or args.claimed_by,
        todo_id=args.todo_id or str(payload.get("todo_id") or "").strip() or None,
        status=capability_gap_status,
        summary=(
            f"capability gap {capability_gap_status} for "
            f"{payload.get('todo_id') or args.todo_id}"
        ),
        details={
            "command": "todo",
            "todo_command": args.todo_command,
            "target_capabilities": ",".join(args.target_capabilities or []),
            "evidence": args.evidence or "not_required_for_found",
        },
    )
    if gap_payload.get("rollout_event"):
        payload["capability_gap_event"] = gap_payload["rollout_event"]
    elif gap_payload.get("rollout_event_log_error"):
        payload["capability_gap_event_error"] = gap_payload[
            "rollout_event_log_error"
        ]
