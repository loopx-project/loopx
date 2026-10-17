"""CLI transport for the existing Goal-owned planned transfer operation."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..control_plane.goals.planned_transfer_io import (
    cancel_registered_goal_transfer,
    inspect_registered_goal_transfer,
)


def register_goal_transfer(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], add_format: Callable[..., Any]) -> None:
    parser = subparsers.add_parser("goal-transfer", help="Inspect original Agent work or cancel its prepared move; destination continuation is not available yet.")
    actions = parser.add_subparsers(dest="goal_transfer_action", required=True)
    for action in ("inspect", "cancel"):
        command = actions.add_parser(action)
        add_format(command)
        command.add_argument("--goal-id", required=True)
        if action == "cancel":
            command.add_argument("--operation-id", required=True)
            command.add_argument("--execute", required=True, action="store_true")


def render_goal_transfer(payload: dict[str, Any]) -> str:
    lines = ["# Agent transfer", ""]
    for key in ("goal_id", "source_state", "cancel_available", "continuation_available", "error"):
        if key in payload:
            lines.append(f"- {key}: {payload[key]}")
    if "work" in payload:
        lines.append(f"- Original work: {payload['work']['objective']}")
        lines.extend(f"  - {row['text']}" for row in payload["work"]["todos"])
    lines.extend(f"- Hold: {row['code']} ({row['owner']})" for row in payload.get("holds", []))
    if "readback" in payload:
        lines.append(f"- Original source restored: {payload['readback']['original_writer_fence_restored']}")
    return "\n".join(lines) + "\n"


def handle_goal_transfer(args: argparse.Namespace, *, registry_path: Path,
    print_payload: Callable[..., Any], output_format: Callable[..., str]) -> int:
    try:
        payload = (inspect_registered_goal_transfer(registry_path=registry_path, goal_id=args.goal_id)
            if args.goal_transfer_action == "inspect" else
            {"ok": True, **cancel_registered_goal_transfer(registry_path=registry_path, goal_id=args.goal_id,
                operation_id=args.operation_id)})
    except Exception as error:
        payload = {"ok": False, "error": str(error)}
    print_payload(payload, output_format(args), render_goal_transfer)
    return 0 if payload.get("ok") else 1
