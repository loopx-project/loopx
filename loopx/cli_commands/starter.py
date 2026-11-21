from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.handoff.cross_runtime_impl_review import (
    DEFAULT_GOAL_ID as DEFAULT_IMPL_REVIEW_GOAL_ID,
    DEFAULT_IMPLEMENTER_AGENT_ID,
    DEFAULT_REQUIREMENT,
    DEFAULT_REVIEWER_AGENT_ID,
    DEFAULT_VERIFIER,
    build_cross_runtime_impl_review_demo_packet,
    render_cross_runtime_impl_review_demo_markdown,
)
from ..demo import (
    DEFAULT_DEMO_AGENT_TODO,
    DEFAULT_DEMO_GOAL_ID,
    DEFAULT_DEMO_OBJECTIVE,
    DEFAULT_DEMO_PROJECT,
    DEFAULT_DEMO_USER_TODO,
    render_demo_markdown,
    run_demo,
)
from .starter_bootstrap import handle_starter_bootstrap_command
from .starter_bootstrap_registration import register_starter_bootstrap_commands


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def register_starter_commands(subparsers: argparse._SubParsersAction) -> None:
    register_starter_bootstrap_commands(subparsers)

    demo_parser = subparsers.add_parser(
        "demo",
        help="Create a disposable local demo goal and show status/quota output.",
    )
    demo_parser.add_argument(
        "demo_kind",
        nargs="?",
        choices=["impl-review"],
        help="Optional dry-run demo packet to render instead of creating a disposable goal.",
    )
    demo_parser.add_argument(
        "--preset",
        default="claude-codex",
        help="Demo preset for packet mode. Defaults to claude-codex.",
    )
    demo_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render a no-write packet for demo_kind modes.",
    )
    demo_parser.add_argument(
        "--requirement",
        default=DEFAULT_REQUIREMENT,
        help="Public-safe bounded requirement for impl-review packet mode.",
    )
    demo_parser.add_argument(
        "--implementer-agent-id",
        default=DEFAULT_IMPLEMENTER_AGENT_ID,
        help="Agent id that would own the implementation todo.",
    )
    demo_parser.add_argument(
        "--reviewer-agent-id",
        default=DEFAULT_REVIEWER_AGENT_ID,
        help="Agent id that would own the review todo.",
    )
    demo_parser.add_argument(
        "--verifier",
        default=DEFAULT_VERIFIER,
        help="Verifier command or smoke label for the review verdict contract.",
    )
    demo_parser.add_argument(
        "--generated-at",
        default="2026-06-30T00:00:00Z",
        help="Stable timestamp to include in dry-run packets.",
    )
    demo_parser.add_argument(
        "--project",
        default=str(DEFAULT_DEMO_PROJECT),
        help=f"Disposable demo project directory. Defaults to {DEFAULT_DEMO_PROJECT}.",
    )
    demo_parser.add_argument("--goal-id", default=DEFAULT_DEMO_GOAL_ID)
    demo_parser.add_argument("--objective", default=DEFAULT_DEMO_OBJECTIVE)
    demo_parser.add_argument("--user-todo", default=DEFAULT_DEMO_USER_TODO)
    demo_parser.add_argument("--agent-todo", default=DEFAULT_DEMO_AGENT_TODO)


def handle_demo_command(args: argparse.Namespace, print_payload: PrintPayload) -> int:
    if getattr(args, "demo_kind", None) == "impl-review":
        goal_id = args.goal_id
        if goal_id == DEFAULT_DEMO_GOAL_ID:
            goal_id = DEFAULT_IMPL_REVIEW_GOAL_ID
        payload = build_cross_runtime_impl_review_demo_packet(
            preset=args.preset,
            dry_run=bool(args.dry_run),
            goal_id=goal_id,
            requirement=args.requirement,
            implementer_agent_id=args.implementer_agent_id,
            reviewer_agent_id=args.reviewer_agent_id,
            verifier=args.verifier,
            generated_at=args.generated_at,
        )
        print_payload(payload, args.format, render_cross_runtime_impl_review_demo_markdown)
        return 0 if payload.get("ok") else 1

    if getattr(args, "dry_run", False):
        payload = {
            "ok": False,
            "project": args.project,
            "goal_id": args.goal_id,
            "error": "--dry-run is only supported with `loopx demo impl-review`",
        }
        print_payload(payload, args.format, render_demo_markdown)
        return 1

    try:
        payload = run_demo(
            project=Path(args.project).expanduser(),
            runtime_root=Path(args.runtime_root).expanduser() if args.runtime_root else None,
            goal_id=args.goal_id,
            objective=args.objective,
            user_todo=args.user_todo,
            agent_todo=args.agent_todo,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "project": args.project,
            "goal_id": args.goal_id,
            "error": str(exc),
        }
    print_payload(payload, args.format, render_demo_markdown)
    return 0 if payload.get("ok") else 1


def handle_starter_command(
    args: argparse.Namespace,
    print_payload: PrintPayload,
) -> int | None:
    bootstrap_result = handle_starter_bootstrap_command(args, print_payload)
    if bootstrap_result is not None:
        return bootstrap_result
    if str(getattr(args, "command", "")) == "demo":
        return handle_demo_command(args, print_payload)
    return None
