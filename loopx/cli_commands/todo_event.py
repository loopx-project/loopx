from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path

from ..command_invocation import cli_option_args, loopx_cli_argv
from ..control_plane.todos.contract import decision_scope_metadata_value
from ..file_lock import lock_timeout_error_fields
from .todo_argument_validation import TODO_OPTION_FIELDS


RolloutEventAppender = Callable[..., dict[str, object]]

TODO_EVENT_KINDS = {
    "add": "todo_add",
    "claim": "todo_claim",
    "update": "todo_update",
    "complete": "todo_complete",
    "supersede": "todo_supersede",
    "archive-completed": "todo_archive_completed",
}


def _original_todo_arguments(args: argparse.Namespace) -> list[str]:
    """Carry parsed caller inputs; Core still decides whether a retry is legal."""
    arguments = ["todo", args.todo_command, "--goal-id", args.goal_id]
    for flag, field in (*TODO_OPTION_FIELDS, ("--project", "project"),
                        ("--dry-run", "dry_run"), ("--result-file", "result_file")):
        value = getattr(args, field, None)
        if isinstance(value, bool):
            if value:
                arguments.append(flag)
        elif isinstance(value, list):
            for item in value:
                arguments.extend(cli_option_args(flag, item))
        elif value is not None:
            arguments.extend(cli_option_args(flag, value))
    return arguments


def _route_native_recovery(
    payload: dict[str, object], args: argparse.Namespace, *,
    registry_path: Path, runtime_root_arg: str | None,
) -> None:
    """Adapt native guidance to this CLI without changing its recovery decision."""
    recovery = payload.get("recovery")
    if not isinstance(recovery, dict):
        return
    route = loopx_cli_argv()
    routed = dict(recovery)
    for step, descriptor in recovery.items():
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("command"), str):
            continue
        try:
            command = shlex.split(descriptor["command"])
        except ValueError:
            continue  # Malformed optional guidance must not hide the refusal.
        if not command or command[0] != "loopx":
            continue
        arguments = descriptor.get("cli_args")
        if descriptor.get("reference_source") == "claim_operation_id_from_claim":
            arguments = _original_todo_arguments(args)
            required = descriptor.get("requires_flags")
            if isinstance(required, list) and "--update-expected-provider-revision" in required:
                # Core requires a post-claim targeted read. Project its pending
                # references, not the old values or the claim's new revision.
                for flag, placeholder in (
                    ("--claim-operation-id", "<claim_operation_id_from_claim>"),
                    ("--update-expected-provider-revision", "<provider_revision_from_reconciled_todo_read>"),
                ):
                    for index, argument in enumerate(arguments):
                        if argument == flag and index + 1 < len(arguments):
                            arguments[index + 1] = placeholder
                            break
                        if argument.startswith(flag + "="):
                            arguments[index] = flag + "=" + placeholder
                            break
                    else:
                        arguments.extend(cli_option_args(flag, placeholder))
        else:
            arguments = list(arguments) if isinstance(arguments, list) else command[1:]
        # The update adapter may already bind selectors. Inspect only the global
        # prelude, never user prose that happens to resemble an option.
        start = next((i for i in range(len(arguments) - 1)
                      if arguments[i:i + 2] == command[1:3]), 0)
        prelude = arguments[:start]
        selectors = list(route[1:])
        for flag, value in (("--registry", str(registry_path)),
                            ("--runtime-root", runtime_root_arg), ("--format", "json")):
            if value is not None and not any(part == flag or part.startswith(flag + "=") for part in prelude):
                selectors.extend(cli_option_args(flag, value))
        for flag, field in (("--goal-id", "goal_id"), ("--todo-id", "todo_id")):
            value = descriptor.get(field) or getattr(args, field, None)
            if flag not in arguments and value is not None:
                arguments.extend(cli_option_args(flag, value))
        if step in {"claim", "retry"} and arguments[start:start + 2] == command[1:3]:
            for flag, field in (("--agent-id", "agent_id"), ("--role", "role"),
                                ("--project", "project"), ("--state-file", "state_file")):
                value = getattr(args, field, None)
                if flag not in arguments and value is not None:
                    arguments.extend(cli_option_args(flag, value))
            if getattr(args, "dry_run", False) and "--dry-run" not in arguments:
                arguments.append("--dry-run")
        routed[step] = {**descriptor, "command": shlex.join([*route, *command[1:]]),
                        "cli_args": [*selectors, *arguments]}
    payload["recovery"] = routed


def todo_error_payload(
    args: argparse.Namespace, exc: Exception, *, registry_path: Path,
    runtime_root_arg: str | None,
) -> dict[str, object]:
    from ..control_plane.coordination.local_authority import (
        LocalCoordinationAuthorityRejection, LocalCoordinationAuthorityUnavailable,
    )
    from ..control_plane.coordination.legacy_writer_fence import (
        ActiveStateAuthorityMutationError, LegacyCoordinationWriterFenced,
    )
    from ..control_plane.coordination.shadow_management import ShadowManagementError
    from ..control_plane.todos.external_wait_contract import TodoExternalWaitAuthoringError
    from ..control_plane.todos.handoff_mode import HandoffModeError
    from ..control_plane.work_items.local_lease_record import TaskLeaseError
    from .todo_argument_validation import TodoClaimArgumentError

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
    if isinstance(exc, TodoClaimArgumentError):
        payload["error_code"] = "todo_claim_invalid_arguments"
        payload["recovery"] = exc.recovery(
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
        )
    elif isinstance(exc, (TaskLeaseError, HandoffModeError, LegacyCoordinationWriterFenced, ShadowManagementError, ActiveStateAuthorityMutationError, LocalCoordinationAuthorityUnavailable)):
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
    _route_native_recovery(payload, args, registry_path=registry_path, runtime_root_arg=runtime_root_arg)
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
    identity = payload.get("settlement_identity")
    if turn_instance_id is None and isinstance(identity, Mapping):
        # Lifecycle reentry carries the TS-verified historical binding; the
        # opaque completion key is not a Turn id and must never be parsed.
        turn_instance_id = identity.get("turn_instance_id")
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
    goal_ref = (
        payload.get("goal_ref")
        if isinstance(payload.get("goal_ref"), Mapping)
        else None
    )
    append_cli_rollout_event(
        payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        event_kind=TODO_EVENT_KINDS.get(args.todo_command, "todo_update"),
        goal_ref=goal_ref,
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
                identity.get("effect_id")
                if isinstance(identity, dict)
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
                *(["goal_ref"] if goal_ref is not None else []),
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
