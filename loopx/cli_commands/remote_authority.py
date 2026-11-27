"""Explicit operator entry point for read-only remote authority inspection."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path


def register_remote_authority_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser("remote-authority", help="Inspect a Goal's configured remote authority without admitting work.")
    add_subcommand_format(parser)
    actions = parser.add_subparsers(dest="remote_authority_action", required=True)
    inspect = actions.add_parser("inspect", help="Read the bound probe Todo once and report safe diagnostics.")
    inspect.add_argument("--goal-id", required=True)
    inspect.add_argument("--agent-id", required=True)
    inspect.add_argument("--format", dest="inspection_format", choices=("markdown", "json"))


def handle_remote_authority_command(
    args: argparse.Namespace, *, registry_path: Path,
    output_format: Callable[..., str],
    print_payload: Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None],
) -> int | None:
    if args.command != "remote-authority":
        return None
    from ..control_plane.coordination.remote_authority_gateway import inspect_remote_authority

    result = inspect_remote_authority(registry_path, args.goal_id, args.agent_id)
    print_payload(result, output_format(args, "inspection_format"), lambda payload: "\n".join((
        f"Remote authority: {payload['status']} ({payload['reason_code']})",
        str(payload["message"]), str(payload["recommended_action"]),
        "Execution admitted: no.",
    )))
    return 0 if result["ok"] else 1
