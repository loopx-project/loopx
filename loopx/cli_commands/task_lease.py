from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.work_items.local_lease_record import TaskLeaseError
from ..control_plane.work_items.task_lease import (
    inspect_task_lease,
    release_task_lease,
    renew_task_lease,
    runtime_root_from_registry,
    transfer_task_lease,
)
from ..control_plane.work_items.task_lease_acquire_adapter import (
    execute_native_task_lease_acquire,
)
from ..file_lock import LockAcquireTimeoutError
from ..presentation.markdown import append_operator_action_markdown
from .action_help import install_action_help
from .task_lease_arguments import LEASE_ACTION_FIELDS, TaskLeaseArgumentError, validate_task_lease_arguments


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def render_task_lease_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Task Lease",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- action: `{payload.get('action')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    if payload.get("error_code"):
        lines.append(f"- error_code: `{payload.get('error_code')}`")
    recovery = payload.get("recovery")
    if isinstance(recovery, dict):
        import shlex
        lines.append(f"- repair: `{shlex.join(['loopx', *recovery['cli_args']])}`")
        lines.append(f"- supply: `{', '.join(recovery['requires_flags']) or 'none'}`")
        lines.append(f"- remove: `{', '.join(recovery['remove_flags']) or 'none'}`")
        lines.append(f"- {recovery['reason']}")
    lease = payload.get("lease")
    if isinstance(lease, dict):
        lines.extend(
            [
                f"- goal_id: `{lease.get('goal_id')}`",
                f"- todo_id: `{lease.get('todo_id')}`",
                f"- owner: `{lease.get('owner')}`",
                f"- version: `{lease.get('version')}`",
                f"- lease_epoch: `{lease.get('lease_epoch')}`",
                f"- status: `{lease.get('status')}`",
                f"- expires_at: `{lease.get('expires_at')}`",
                f"- write_scopes: `{', '.join(lease.get('write_scopes') or [])}`",
                f"- write_repository: `{lease.get('write_repository') or 'unknown (conservative overlap)'}`",
            ]
        )
    advisories = payload.get("integration_overlap_advisories")
    if isinstance(advisories, list) and advisories:
        lines.append("- Independent worktree path overlap: coordinate changes and validate integration before merge.")
        for row in advisories:
            if not isinstance(row, dict):
                continue
            lines.append(f"  - `{row['todo_id']}`: {', '.join(row['write_scopes'])}")
    if payload.get("lease_path"):
        lines.append(f"- lease_path: `{payload.get('lease_path')}`")
    if payload.get("transfer_claim") is True:
        lines.append(f"- committed claim owner: `{payload.get('claimed_by')}`")
        lines.append(f"- Todo projection delivery: `{payload.get('projection_delivery')}`")
        lines.append("- A replay reports the original handover; use task-lease inspect for current authority.")
    conflicts = payload.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        lines.append("- conflicts:")
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            lines.append(
                f"  - `{conflict.get('todo_id')}` owner=`{conflict.get('owner')}` "
                f"expires_at=`{conflict.get('expires_at')}` "
                f"write_repository=`{conflict.get('write_repository') or 'unknown'}` "
                f"write_scopes=`{', '.join(conflict.get('write_scopes') or [])}`"
            )
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)


def register_task_lease_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "task-lease",
        help="Acquire, renew, transfer, release, or inspect a per-(goal_id,todo_id) hard task lease.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "task_lease_command",
        choices=["acquire", "renew", "transfer", "release", "inspect"],
        help="Lease lifecycle action.",
    )
    parser.add_argument("--goal-id", required=True, help="Goal id that owns the todo.")
    parser.add_argument("--todo-id", required=True, help="Structured todo id such as todo_ab12cd34ef56.")
    parser.add_argument("--owner", help="Registered public-safe agent id that owns the lease.")
    parser.add_argument("--idempotency-key", help="Public-safe token used for idempotent retries and CAS.")
    parser.add_argument("--new-owner", help="For transfer, target registered public-safe agent id.")
    parser.add_argument("--new-idempotency-key", help="For transfer, target idempotency key.")
    parser.add_argument("--transfer-claim", action="store_true",
        help="For transfer on canonical hard_lease authority, atomically move the source owner's Todo claim with its lease.")
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        help="Lease TTL in seconds. Defaults to 45 minutes and is capped at 24 hours.",
    )
    parser.add_argument(
        "--write-scope",
        dest="write_scopes",
        action="append",
        help="Relative write scope protected by this lease, such as loopx/**. Repeatable.",
    )
    parser.add_argument("--write-worktree", help="Independent Git worktree root for code-edit scopes (canonical File/SQLite). Sibling worktree overlaps are advisory; shared-state leases stay exclusive.")
    parser.add_argument(
        "--expected-version",
        type=int,
        help=(
            "CAS version that must match the current lease. Required for "
            "renew, transfer, and release; optional for acquire."
        ),
    )
    install_action_help(parser, command_dest="task_lease_command",
                        action_fields=LEASE_ACTION_FIELDS,
                        common_fields=frozenset({"goal_id", "todo_id", "subcommand_format"}))


def handle_task_lease_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "task-lease":
        return None
    try:
        validate_task_lease_arguments(args)
        runtime_root = runtime_root_from_registry(registry_path, runtime_root_arg)
        if args.task_lease_command == "acquire":
            payload = execute_native_task_lease_acquire(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                owner=args.owner,
                todo_id=args.todo_id,
                idempotency_key=args.idempotency_key,
                write_scopes=args.write_scopes,
                **({"write_worktree": args.write_worktree} if args.write_worktree is not None else {}),
                ttl_seconds=args.ttl_seconds,
                expected_version=args.expected_version,
            )
        elif args.task_lease_command == "renew":
            payload = renew_task_lease(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                owner=args.owner,
                idempotency_key=args.idempotency_key,
                ttl_seconds=args.ttl_seconds,
                expected_version=args.expected_version,
            )
        elif args.task_lease_command == "transfer":
            payload = transfer_task_lease(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                owner=args.owner,
                idempotency_key=args.idempotency_key,
                new_owner=args.new_owner,
                new_idempotency_key=args.new_idempotency_key,
                transfer_claim=args.transfer_claim,
                ttl_seconds=args.ttl_seconds,
                expected_version=args.expected_version,
            )
        elif args.task_lease_command == "release":
            payload = release_task_lease(
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                owner=args.owner,
                idempotency_key=args.idempotency_key,
                expected_version=args.expected_version,
                registry_path=registry_path,
            )
        else:
            payload = inspect_task_lease(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
            )
    except TaskLeaseArgumentError as exc:
        payload = {
            "ok": False, "schema_version": "task_lease_v0",
            "action": args.task_lease_command, "error": str(exc),
            "error_code": "ValueError",
            "recovery": exc.recovery(args, registry_path=registry_path,
                                     runtime_root_arg=runtime_root_arg),
        }
    except TaskLeaseError as exc:
        payload = {
            **exc.payload,
            "ok": False,
            "schema_version": "task_lease_v0",
            "action": getattr(args, "task_lease_command", None),
            "error": str(exc),
            "error_code": exc.code,
        }
    except LockAcquireTimeoutError as exc:
        payload = {
            "ok": False,
            "schema_version": "task_lease_v0",
            "action": getattr(args, "task_lease_command", None),
            "error": str(exc),
            **exc.to_payload(),
        }
    except Exception as exc:
        # Envelope-owned keys always win over a typed exception payload.
        typed_payload = (
            dict(getattr(exc, "payload", {}) or {})
            if isinstance(getattr(exc, "code", None), str)
            else {}
        )
        payload = {
            **typed_payload,
            "ok": False,
            "schema_version": "task_lease_v0",
            "action": getattr(args, "task_lease_command", None),
            "error": str(exc),
            "error_code": getattr(exc, "code", exc.__class__.__name__),
        }
    print_payload(payload, output_format(args), render_task_lease_markdown)
    return 0 if payload.get("ok") else 1
