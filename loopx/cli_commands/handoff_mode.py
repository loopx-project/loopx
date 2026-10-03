from __future__ import annotations

from ..control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable

from ..control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced
from ..control_plane.coordination.shadow_management import ShadowManagementError

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.todos.handoff_mode import (
    HANDOFF_MODE_VALUES,
    HandoffModeError,
    set_goal_handoff_mode,
    show_goal_handoff_mode,
)
from ..file_lock import LockAcquireTimeoutError


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def render_handoff_mode_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Goal Handoff Mode",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- action: `{payload.get('action')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
    ]
    if payload.get("handoff_mode"):
        lines.append(f"- handoff_mode: `{payload.get('handoff_mode')}`")
    if payload.get("previous_mode"):
        lines.append(f"- previous_mode: `{payload.get('previous_mode')}`")
    if payload.get("source"):
        lines.append(f"- source: `{payload.get('source')}`")
    if "changed" in payload:
        lines.append(f"- changed: `{payload.get('changed')}`")
    for key in ("status", "plan_sha256", "backup_path", "preserved_claim_count", "reason_code", "reason"):
        if payload.get(key) is not None:
            lines.append(f"- {key}: `{payload[key]}`")
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    if payload.get("error_code"):
        lines.append(f"- error_code: `{payload.get('error_code')}`")
    claimed = payload.get("claimed_todos")
    if isinstance(claimed, list) and claimed:
        lines.append("- claimed open todos:")
        for item in claimed:
            if isinstance(item, dict):
                lines.append(
                    f"  - `{item.get('todo_id')}` claimed_by=`{item.get('claimed_by')}`"
                )
    leases = payload.get("active_leases")
    if isinstance(leases, list) and leases:
        lines.append("- time-active leases:")
        for item in leases:
            if isinstance(item, dict):
                lines.append(
                    f"  - `{item.get('todo_id')}` owner=`{item.get('owner')}` "
                    f"expires_at=`{item.get('expires_at')}`"
                )
    return "\n".join(lines)


def register_handoff_mode_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "handoff-mode",
        help=(
            "Show or set the authoritative per-goal handoff_mode that "
            "selects which ownership authority governs todo handoffs."
        ),
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "handoff_mode_command",
        choices=["show", "set", "plan-migration", "migrate"],
        help=(
            "Use show to read the effective mode (absent front-matter means "
            "legacy) or set to change it. Setting requires a quiescent goal: "
            "no open todo with claimed_by and no time-active task lease."
        ),
    )
    parser.add_argument("--goal-id", required=True, help="Goal id whose active state carries the mode.")
    parser.add_argument(
        "--mode",
        choices=HANDOFF_MODE_VALUES,
        help=(
            "Target ownership policy: soft_claim or hard_lease. legacy is accepted "
            "only to recover an existing canonical set receipt with its original --operation-id. "
            "soft_claim rejects lease acquire/renew/"
            "transfer, hard_lease requires the actor to hold the todo's lease "
            "for ownership changes and makes the completion fence mandatory."
        ),
    )
    parser.add_argument("--operation-id", help="Stable canonical set intent id for recovery after a lost response.")
    parser.add_argument("--dry-run", action="store_true", help="Validate set without committing the mode or a receipt.")
    parser.add_argument("--project", help="Project root. Defaults to the registry goal repo.")
    parser.add_argument("--state-file", help="Active goal state path. Defaults to the registry goal state_file.")
    parser.add_argument("--plan", type=Path, help="Immutable reviewed migration plan path.")
    parser.add_argument("--plan-sha256", help="Exact reviewed plan digest for migrate.")
    parser.add_argument("--execute", action="store_true", help="Apply a reviewed migration after verifying its full backup.")


def handle_handoff_mode_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
    runtime_root_arg: str | None = None,
) -> int | None:
    if args.command != "handoff-mode":
        return None
    path_args = {
        "project": Path(args.project).expanduser() if args.project else None,
        "state_file": Path(args.state_file).expanduser() if args.state_file else None,
    }
    try:
        action = args.handoff_mode_command
        plan = getattr(args, "plan", None)
        plan_sha256 = getattr(args, "plan_sha256", None)
        execute = getattr(args, "execute", False)
        if action in {"plan-migration", "migrate"}:
            if not plan or args.operation_id or args.dry_run or args.project or args.state_file:
                raise ValueError("migration requires --plan and canonical authority; set/source override options are unsupported")
            if action == "plan-migration" and (not args.mode or plan_sha256 or execute):
                raise ValueError("plan-migration requires --mode; apply options are unsupported")
            if action == "migrate" and (not plan_sha256 or args.mode):
                raise ValueError("migrate requires --plan-sha256; the reviewed plan owns --mode")
            from ..control_plane.todos.provider_handoff_mode import migrate_registered_handoff_mode
            payload = migrate_registered_handoff_mode(
                registry_path=registry_path,
                runtime_root=runtime_root_arg,
                goal_id=args.goal_id, action=action, plan=plan, mode=args.mode,
                plan_sha256=plan_sha256, execute=execute,
            )
        elif plan or plan_sha256 or execute:
            raise ValueError("migration options require plan-migration or migrate")
        elif action == "show":
            if args.mode or args.operation_id or args.dry_run:
                raise ValueError("handoff-mode show does not accept set options")
            payload = show_goal_handoff_mode(
                registry_path=registry_path,
                goal_id=args.goal_id,
                runtime_root_arg=runtime_root_arg,
                **path_args,
            )
        else:
            if not args.mode:
                raise ValueError("handoff-mode set requires --mode")
            payload = set_goal_handoff_mode(
                registry_path=registry_path,
                goal_id=args.goal_id,
                mode=args.mode,
                runtime_root_arg=runtime_root_arg,
                operation_id=args.operation_id, dry_run=args.dry_run,
                **path_args,
            )
    except (HandoffModeError, LegacyCoordinationWriterFenced, ShadowManagementError, LocalCoordinationAuthorityUnavailable) as exc:
        payload = {
            **exc.payload,
            "ok": False,
            "schema_version": "goal_handoff_mode_v0",
            "action": getattr(args, "handoff_mode_command", None),
            "goal_id": args.goal_id,
            "error": str(exc),
            "error_code": exc.code,
        }
    except LockAcquireTimeoutError as exc:
        payload = {
            "ok": False,
            "schema_version": "goal_handoff_mode_v0",
            "action": getattr(args, "handoff_mode_command", None),
            "goal_id": args.goal_id,
            "error": str(exc),
            **exc.to_payload(),
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "goal_handoff_mode_v0",
            "action": getattr(args, "handoff_mode_command", None),
            "goal_id": args.goal_id,
            "error": str(exc),
            "error_code": exc.__class__.__name__,
        }
    print_payload(payload, output_format(args), render_handoff_mode_markdown)
    return 0 if payload.get("ok") else 1
