from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from ..control_plane.capability_hooks import (
    PostWritebackHookRegistration,
)
from ..feedback import (
    LESSON_KINDS,
    append_human_reward,
    compact_reward,
    render_reward_markdown,
)
from ..operator_gate import (
    DEFAULT_OPERATOR_GATE,
    OPERATOR_GATE_DECISIONS,
    record_operator_gate,
    render_operator_gate_markdown,
)
from ..project_map import (
    DEFAULT_PROJECT_MAP_CLASSIFICATION,
    read_only_project_map_run,
    render_read_only_project_map_markdown,
)
from .post_writeback import (
    PostWritebackProjectionBuilder,
)
from .project_lifecycle_refresh_state import (
    handle_refresh_state_command,
    register_refresh_state_command,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
OutputFormat = Callable[[argparse.Namespace], str]
AppendCliRolloutEvent = Callable[..., dict[str, object]]

PROJECT_LIFECYCLE_COMMANDS = {
    "refresh-state",
    "read-only-map",
    "reward",
    "operator-gate",
}


def register_project_lifecycle_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    register_refresh_state_command(subparsers, add_subcommand_format)

    read_only_map_parser = subparsers.add_parser(
        "read-only-map",
        help="Append a generic read-only project-map run for a connected project.",
    )
    add_subcommand_format(read_only_map_parser)
    read_only_map_parser.add_argument(
        "--goal-id",
        required=True,
        help="Goal id whose project should be mapped.",
    )
    read_only_map_parser.add_argument("--project", help="Project root. Defaults to the registry goal repo.")
    read_only_map_parser.add_argument(
        "--state-file",
        help="Active goal state path. Defaults to the registry goal state_file.",
    )
    read_only_map_parser.add_argument(
        "--classification",
        default=DEFAULT_PROJECT_MAP_CLASSIFICATION,
        help=f"Project-map run classification. Defaults to {DEFAULT_PROJECT_MAP_CLASSIFICATION}.",
    )
    read_only_map_parser.add_argument(
        "--recommended-action",
        help=(
            "Local-control next action. Private project refs are allowed; "
            "inline secrets are rejected. Defaults to the first item from the "
            "active state's Next Action."
        ),
    )
    read_only_map_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the project-map payload without appending.",
    )
    read_only_map_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not refresh the shared global registry after writing the project-map run.",
    )

    reward_parser = subparsers.add_parser(
        "reward",
        help="Append a compact human reward overlay to a goal run index.",
    )
    add_subcommand_format(reward_parser)
    reward_parser.add_argument("--goal-id", required=True, help="Goal id whose latest run should receive feedback.")
    reward_parser.add_argument(
        "--run-generated-at",
        help="Exact run generated_at timestamp. Defaults to the latest compact run for the goal.",
    )
    reward_parser.add_argument("--recorded-at", help="Reward timestamp. Defaults to current UTC time.")
    reward_parser.add_argument(
        "--actor-kind",
        choices=("owner", "controller"),
        help=(
            "Explicit non-Agent actor for the durable append. Anonymous dry-run "
            "remains available when this option is omitted."
        ),
    )
    reward_parser.add_argument("--decision", required=True, help="Operator decision label, such as continue_route.")
    reward_parser.add_argument(
        "--reward",
        required=True,
        choices=["positive", "negative", "mixed", "neutral"],
        help="Compact reward polarity.",
    )
    reward_parser.add_argument(
        "--reason-summary",
        required=True,
        help="Short public-safe reason. Do not include raw private evidence.",
    )
    reward_parser.add_argument("--follow-up", help="Optional next handoff or experiment condition.")
    reward_parser.add_argument(
        "--lesson-kind",
        choices=sorted(LESSON_KINDS),
        help="Optional public-safe lesson kind when this reward records an explicit user correction.",
    )
    reward_parser.add_argument(
        "--lesson-summary",
        help="Short public-safe lesson summary. Required when --lesson-kind is set.",
    )
    reward_parser.add_argument(
        "--lesson-avoid",
        action="append",
        default=[],
        help="Public-safe phrase/action that future recommended_action should avoid. Repeatable.",
    )
    reward_parser.add_argument(
        "--lesson-prefer",
        action="append",
        default=[],
        help="Public-safe phrase/action that future recommended_action should prefer. Repeatable.",
    )
    reward_parser.add_argument(
        "--state-file",
        help="Active goal state path for optional summary writeback. Defaults to the registry goal state_file.",
    )
    reward_parser.add_argument(
        "--write-active-state-summary",
        action="store_true",
        help="After a real append, also add the returned active_state_summary to the active state's Progress Ledger. With --dry-run, preview only.",
    )
    reward_parser.add_argument("--dry-run", action="store_true", help="Print the overlay without appending it.")

    gate_parser = subparsers.add_parser(
        "operator-gate",
        help="Record an operator gate decision such as read-only map opt-in.",
    )
    add_subcommand_format(gate_parser)
    gate_parser.add_argument("--goal-id", required=True, help="Goal id whose operator gate is being judged.")
    gate_parser.add_argument("--gate", default=DEFAULT_OPERATOR_GATE, help=f"Gate id. Defaults to {DEFAULT_OPERATOR_GATE}.")
    gate_parser.add_argument(
        "--decision",
        required=True,
        choices=sorted(OPERATOR_GATE_DECISIONS),
        help="Operator decision for this gate.",
    )
    gate_parser.add_argument("--recorded-at", help="Decision timestamp. Defaults to current local time.")
    gate_parser.add_argument(
        "--operator-question",
        help="Human-facing question being answered. Defaults from --gate and --goal-id.",
    )
    gate_parser.add_argument(
        "--reason-summary",
        required=True,
        help="Short public-safe reason. Do not include raw private evidence.",
    )
    gate_parser.add_argument("--follow-up", help="Optional next handoff or evidence condition.")
    gate_parser.add_argument(
        "--agent-command",
        help="Target-agent command that becomes valid after approval. Defaults for read_only_map_opt_in approvals.",
    )
    gate_parser.add_argument(
        "--recommended-action",
        help="Local-control next action for status/dashboard; inline secrets are rejected.",
    )
    gate_parser.add_argument("--dry-run", action="store_true", help="Print the decision run without appending it.")
    gate_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not refresh the shared global registry after writing the gate decision.",
    )


def handle_project_lifecycle_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
    output_format: OutputFormat,
    append_cli_rollout_event: AppendCliRolloutEvent,
    post_writeback_hooks: Sequence[PostWritebackHookRegistration] | None = None,
    post_writeback_projection_builder: PostWritebackProjectionBuilder | None = None,
) -> int | None:
    if args.command not in PROJECT_LIFECYCLE_COMMANDS:
        return None

    fmt = output_format(args)
    refresh_state_result = handle_refresh_state_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
        output_format=output_format,
        append_cli_rollout_event=append_cli_rollout_event,
        post_writeback_hooks=post_writeback_hooks,
        post_writeback_projection_builder=post_writeback_projection_builder,
    )
    if refresh_state_result is not None:
        return refresh_state_result

    if args.command == "read-only-map":
        try:
            payload = read_only_project_map_run(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                project=Path(args.project).expanduser() if args.project else None,
                state_file=Path(args.state_file).expanduser() if args.state_file else None,
                classification=args.classification,
                recommended_action=args.recommended_action,
                dry_run=bool(args.dry_run),
                sync_global=not bool(args.no_global_sync),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": args.runtime_root,
                "goal_id": args.goal_id,
                "classification": args.classification,
                "appended": False,
                "dry_run": bool(args.dry_run),
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, fmt, render_read_only_project_map_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "reward":
        try:
            reward = compact_reward(
                recorded_at=args.recorded_at,
                decision=args.decision,
                reward=args.reward,
                reason_summary=args.reason_summary,
                follow_up=args.follow_up,
                lesson={
                    "kind": args.lesson_kind,
                    "summary": args.lesson_summary,
                    "avoid": args.lesson_avoid,
                    "prefer": args.lesson_prefer,
                }
                if args.lesson_kind
                else None,
            )
            payload = append_human_reward(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                run_generated_at=args.run_generated_at,
                reward=reward,
                actor_kind=args.actor_kind,
                dry_run=bool(args.dry_run),
                state_file_override=Path(args.state_file).expanduser() if args.state_file else None,
                write_active_state_summary=bool(args.write_active_state_summary),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": args.runtime_root,
                "goal_id": args.goal_id,
                "appended": False,
                "dry_run": bool(args.dry_run),
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, fmt, render_reward_markdown)
        return 0 if payload.get("ok") else 1

    try:
        payload = record_operator_gate(
            registry_path=registry_path,
            runtime_root_override=args.runtime_root,
            goal_id=args.goal_id,
            gate=args.gate,
            decision=args.decision,
            operator_question=args.operator_question,
            reason_summary=args.reason_summary,
            follow_up=args.follow_up,
            agent_command=args.agent_command,
            recommended_action=args.recommended_action,
            recorded_at=args.recorded_at,
            dry_run=bool(args.dry_run),
            sync_global=not bool(args.no_global_sync),
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": args.runtime_root,
            "goal_id": args.goal_id,
            "appended": False,
            "dry_run": bool(args.dry_run),
            "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
        }
    print_payload(payload, fmt, render_operator_gate_markdown)
    return 0 if payload.get("ok") else 1
