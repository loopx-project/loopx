from __future__ import annotations

from ..control_plane.coordination.legacy_writer_fence import LegacyCoordinationWriterFenced
from ..control_plane.coordination.shadow_management import ShadowManagementError

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.projects.registry import (
    PROJECT_KINDS,
    bind_session,
    recreate_goal,
    register_project_goal,
    resolve_project,
    unbind_session,
)

PrintPayload = Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None]


def register_project_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    project_parser = subparsers.add_parser(
        "project",
        help="Register and deterministically resolve durable Projects and foreground Goals.",
    )
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)
    register_parser = project_sub.add_parser(
        "register",
        help="Register one Project and its first active Goal in one registry mutation.",
    )
    add_subcommand_format(register_parser)
    register_parser.add_argument("--project-id", required=True)
    register_parser.add_argument("--project-kind", required=True, choices=PROJECT_KINDS)
    register_parser.add_argument("--knowledge-root", required=True)
    register_parser.add_argument("--goal-id", required=True)
    register_parser.add_argument("--objective", required=True)
    register_parser.add_argument("--non-goal", action="append", default=[])
    register_parser.add_argument("--acceptance", action="append", default=[], required=True)
    register_parser.add_argument("--unknown", action="append", default=[])
    register_parser.add_argument("--next-effect", required=True)
    register_parser.add_argument("--stop-condition", required=True)
    register_parser.add_argument("--repository", action="append", default=[])
    register_parser.add_argument("--external-locator", action="append", default=[])
    register_parser.add_argument(
        "--goal-instance-profile",
        choices=("source_session_v1",),
    )
    register_parser.add_argument("--operation-id")

    bind_parser = project_sub.add_parser(
        "bind-session",
        help="Bind one host session to one active foreground Goal.",
    )
    add_subcommand_format(bind_parser)
    bind_parser.add_argument("--session-id", required=True)
    bind_parser.add_argument("--goal-id", required=True)
    bind_parser.add_argument("--goal-instance-id")
    bind_parser.add_argument("--operation-id")

    unbind_parser = project_sub.add_parser(
        "unbind-session",
        help="Remove one exact host session binding from its expected foreground Goal.",
    )
    add_subcommand_format(unbind_parser)
    unbind_parser.add_argument("--session-id", required=True)
    unbind_parser.add_argument("--goal-id", required=True)
    unbind_parser.add_argument("--goal-instance-id")
    unbind_parser.add_argument("--operation-id")

    recreate_parser = project_sub.add_parser(
        "recreate-goal",
        help="Retire one exact Goal instance and publish its reserved successor.",
    )
    add_subcommand_format(recreate_parser)
    recreate_parser.add_argument("--goal-id", required=True)
    recreate_parser.add_argument("--goal-instance-id", required=True)
    recreate_parser.add_argument("--operation-id", required=True)
    recreate_parser.add_argument("--execute", action="store_true")

    resolve_parser = project_sub.add_parser(
        "resolve",
        help="Resolve Project identity from explicit, session, or exact locator evidence.",
    )
    add_subcommand_format(resolve_parser)
    resolve_parser.add_argument("--project-id")
    resolve_parser.add_argument("--session-id")
    resolve_parser.add_argument("--goal-id")
    resolve_parser.add_argument("--goal-instance-id")
    resolve_parser.add_argument("--repository")
    resolve_parser.add_argument("--external-locator")


def render_project_command_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# LoopX Project",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- registry: `{payload.get('registry')}`",
    ]
    for field in (
        "status",
        "changed",
        "replayed",
        "gate_state",
        "resolution",
        "source",
        "project_id",
        "foreground_goal_id",
    ):
        if field in payload:
            lines.append(f"- {field}: `{payload.get(field)}`")
    if payload.get("recovery_action"):
        lines.append(f"- recovery_action: {payload.get('recovery_action')}")
    pending_effects = payload.get("pending_effects")
    if isinstance(pending_effects, list) and pending_effects:
        lines.extend(["", "## Pending Turn effects", ""])
        for pending in pending_effects:
            if not isinstance(pending, Mapping):
                continue
            lines.append(
                "- "
                f"turn_key=`{pending.get('turn_key')}`; "
                f"step_kind=`{pending.get('step_kind')}`; "
                f"reason=`{pending.get('reason')}`; "
                f"recovery_action={pending.get('recovery_action')}"
            )
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
    return "\n".join(lines)


def handle_project_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[[argparse.Namespace], str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "project":
        return None
    try:
        if args.project_command == "register":
            payload = register_project_goal(
                registry_path=registry_path,
                runtime_root=Path(runtime_root_arg).expanduser() if runtime_root_arg else None,
                project_id=args.project_id,
                project_kind=args.project_kind,
                knowledge_root=Path(args.knowledge_root),
                goal_id=args.goal_id,
                objective=args.objective,
                non_goals=args.non_goal,
                acceptance=args.acceptance,
                unknowns=args.unknown,
                next_effect=args.next_effect,
                stop_condition=args.stop_condition,
                repository_bindings=args.repository,
                external_locator_bindings=args.external_locator,
                goal_instance_profile=args.goal_instance_profile,
                operation_id=args.operation_id,
            )
        elif args.project_command == "bind-session":
            payload = bind_session(
                registry_path=registry_path,
                session_id=args.session_id,
                goal_id=args.goal_id,
                goal_instance_id=args.goal_instance_id,
                operation_id=args.operation_id,
            )
        elif args.project_command == "unbind-session":
            payload = unbind_session(
                registry_path=registry_path,
                session_id=args.session_id,
                goal_id=args.goal_id,
                goal_instance_id=args.goal_instance_id,
                operation_id=args.operation_id,
            )
        elif args.project_command == "resolve":
            payload = resolve_project(
                registry_path=registry_path,
                explicit_project_id=args.project_id,
                session_id=args.session_id,
                repository=args.repository,
                external_locator=args.external_locator,
                goal_id=args.goal_id,
                goal_instance_id=args.goal_instance_id,
            )
        else:
            payload = recreate_goal(
                registry_path=registry_path,
                goal_id=args.goal_id,
                goal_instance_id=args.goal_instance_id,
                operation_id=args.operation_id,
                execute=args.execute,
            )
    except (OSError, TypeError, ValueError, LegacyCoordinationWriterFenced, ShadowManagementError) as exc:
        payload = {
            "ok": False,
            "changed": False,
            "registry": str(registry_path),
            "error": str(exc),
            **({"error_code": exc.code, **exc.payload} if isinstance(exc, (LegacyCoordinationWriterFenced, ShadowManagementError)) else {}),
        }
    payload.setdefault("registry", str(registry_path))
    print_payload(payload, output_format(args), render_project_command_markdown)
    return 0 if payload.get("ok") else 1
