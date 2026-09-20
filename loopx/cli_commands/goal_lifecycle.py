from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.actor_identity import OWNER_CONTROLLER_ACTOR_CHOICES
from ..control_plane.goals.activation_service import (
    render_goal_activation_markdown,
    set_goal_activation_state,
)
from ..file_lock import lock_timeout_error_fields


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def register_goal_lifecycle_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "goal-lifecycle",
        help="Preview or apply a reversible Goal stop/resume transition.",
    )
    parser.add_argument(
        "--goal-id", required=True, help="Goal id present in the active registry."
    )
    parser.add_argument(
        "--operation",
        choices=("stop", "resume"),
        required=True,
        help="Stop automatic advancement or restore eligibility.",
    )
    parser.add_argument("--reason", help="Bounded owner-visible transition reason.")
    parser.add_argument(
        "--actor-kind",
        choices=OWNER_CONTROLLER_ACTOR_CHOICES,
        help=(
            "Explicit non-Agent actor for --execute. Anonymous preview remains "
            "available when this option is omitted."
        ),
    )
    parser.add_argument(
        "--expected-state-fingerprint",
        help="SHA-256 registry fingerprint from a fresh goal-actions projection.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the source registry and verify the shared projection; preview is the default.",
    )


def handle_goal_lifecycle_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int:
    try:
        payload = set_goal_activation_state(
            registry_path=registry_path,
            goal_id=args.goal_id,
            state="stopped" if args.operation == "stop" else "active",
            reason=args.reason,
            runtime_root_override=args.runtime_root,
            expected_state_fingerprint=args.expected_state_fingerprint,
            actor_kind=args.actor_kind,
            execute=bool(args.execute),
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_goal_activation_transition_v1",
            "dry_run": not bool(args.execute),
            "execute": bool(args.execute),
            "goal_id": args.goal_id,
            "actor_kind": args.actor_kind,
            "changed": False,
            "written": False,
            "error": str(exc),
            **lock_timeout_error_fields(exc),
        }
    print_payload(payload, args.format, render_goal_activation_markdown)
    return 0 if payload.get("ok") else 1
