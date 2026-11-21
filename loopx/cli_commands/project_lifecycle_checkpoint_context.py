"""Option wiring and handler for the `checkpoint-context` recovery command."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.goals.first_party_host_admission import (
    capture_first_party_host_goal_ref,
)
from ..control_plane.goals.source_session_registry_state import exact_goal_ref


def configure_checkpoint_context_parser(
    context_parser: argparse.ArgumentParser,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    add_subcommand_format(context_parser)
    for option in ("goal-id", "agent-id", "turn-instance-id"):
        context_parser.add_argument(f"--{option}", required=True)
    binding = context_parser.add_mutually_exclusive_group(required=True)
    binding.add_argument("--todo-id")
    binding.add_argument("--replan-obligation-id")
    context_parser.add_argument("--goal-instance-id", help=argparse.SUPPRESS)
    context_parser.add_argument("--project")
    context_parser.add_argument("--state-file")
    context_parser.add_argument(
        "--dependency-todo-id",
        action="append",
        default=[],
        help=(
            "Additional upstream Todo result used in the judgment; "
            "declared dependencies are included automatically."
        ),
    )


def handle_checkpoint_context_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: Callable[
        [dict[str, object], str, Callable[[dict[str, object]], str]], None
    ],
    output_format: Callable[[argparse.Namespace], str],
) -> int:
    from ..control_plane.goals.checkpoint_context_io import (
        read_checkpoint_context,
        render_checkpoint_context,
    )

    try:
        goal_instance_id = str(getattr(args, "goal_instance_id", None) or "").strip()
        goal_ref = (
            exact_goal_ref(args.goal_id, goal_instance_id)
            if goal_instance_id
            else capture_first_party_host_goal_ref(
                registry_path=registry_path,
                goal_id=args.goal_id,
            )
        )
        payload = read_checkpoint_context(
            registry_path=registry_path,
            runtime_root_override=args.runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=args.todo_id,
            turn_instance_id=args.turn_instance_id,
            replan_obligation_id=args.replan_obligation_id,
            project=Path(args.project).expanduser() if args.project else None,
            state_file=Path(args.state_file).expanduser() if args.state_file else None,
            dependency_todo_ids=args.dependency_todo_id,
            goal_ref=goal_ref,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            **(
                {"error_code": exc.code, **getattr(exc, "payload", {})}
                if isinstance(getattr(exc, "code", None), str)
                else {}
            ),
        }
    print_payload(payload, output_format(args), render_checkpoint_context)
    return 0 if payload.get("ok") else 1
