"""Option wiring and handler for the `checkpoint-context` recovery command."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.goals.first_party_host_admission import (
    capture_first_party_host_goal_ref,
    load_first_party_registry_metadata,
)
from ..control_plane.goals.source_session_registry_state import exact_goal_ref
from ..paths import resolve_runtime_root


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
    context_parser.add_argument(
        "--purpose",
        choices=("supplement_checkpoint", "first_delivery", "delivery_result"),
        default="supplement_checkpoint",
        help="Read result requirements, post-result direction, or the original missing checkpoint.",
    )
    context_parser.add_argument(
        "--decision-scope",
        choices=("agent_lane", "goal"),
        help="Complete work collection used by the first direction judgment.",
    )
    context_parser.add_argument("--project")
    context_parser.add_argument("--state-file")
    context_parser.add_argument(
        "--max-page-output-bytes",
        type=int,
        help="Bound JSON delivery using a complete private capture with the same read identity.",
    )
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

    fmt = output_format(args)
    try:
        output_limit = args.max_page_output_bytes
        if output_limit is not None and (output_limit <= 0 or fmt != "json"):
            raise ValueError("max-page-output-bytes requires a positive JSON output budget")
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
            purpose=args.purpose,
            decision_scope=args.decision_scope,
            goal_ref=goal_ref,
        )
        if payload.get("ok") and output_limit is not None:
            from ..control_plane.turn_driver.native_context_profile import NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
            from .quota_capture import budgeted_settlement_result

            payload = budgeted_settlement_result(
                payload,
                runtime_root=resolve_runtime_root(
                    load_first_party_registry_metadata(registry_path, goal_ref),
                    args.runtime_root,
                    registry_path=registry_path,
                ),
                output_bytes=min(output_limit, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES),
                deferred_fields=("basis", "context", "replan_context"),
                read_instruction=(
                    "Read result_details.command to reconstruct the complete saved result and its basis before judging direction. "
                    "Do not request a new checkpoint-context to page this saved read. "
                    "Preserve the original read_context_id and Turn. The capture grants no authority or freshness."
                ),
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
    print_payload(payload, fmt, render_checkpoint_context)
    return 0 if payload.get("ok") else 1
