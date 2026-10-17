"""CLI transport for the existing Goal-owned planned transfer operation."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..control_plane.goals.planned_transfer_io import (
    cancel_registered_goal_transfer,
    inspect_registered_goal_transfer,
    local_goal_transfer_cutover,
    read_goal_transfer_host_context,
)


def register_goal_transfer(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], add_format: Callable[..., Any]) -> None:
    parser = subparsers.add_parser("goal-transfer", help="Inspect original Agent work, recover preparation, or review a local cutover (development profile).")
    actions = parser.add_subparsers(dest="goal_transfer_action", required=True)
    for action in ("inspect", "cancel", "cutover-preview", "cutover", "context"):
        command = actions.add_parser(action)
        add_format(command)
        command.add_argument("--goal-id", required=True)
        if action not in ("inspect", "context"):
            command.add_argument("--operation-id", required=True)
        if action == "context":
            command.add_argument("--agent-id", required=True)
            command.add_argument("--todo-id", required=True)
        if action in ("cutover-preview", "cutover"):
            command.add_argument("--continuation", choices=("task_only", "reviewed_context"), default=None,
                help="Review original decision context and new local runtime admission; never copy native sessions or credentials.")
            command.add_argument("--destination-registry", required=True, type=Path,
                help="Existing staged destination registry on this POSIX host; not a portable authorization token.")
        if action in ("cancel", "cutover"):
            command.add_argument("--execute", required=True, action="store_true")
        if action == "cutover":
            command.add_argument("--expected-preview-sha256", required=True)


def render_goal_transfer(payload: dict[str, Any]) -> str:
    lines = ["# Agent transfer", ""]
    for key in ("goal_id", "source_state", "cancel_available", "continuation_available", "preview_sha256", "source_final", "target_task_admitted", "error"):
        if key in payload:
            lines.append(f"- {key}: {payload[key]}")
    if "work" in payload:
        lines.append(f"- Original work: {payload['work']['objective']}")
        lines.extend(f"  - {row['text']}" for row in payload["work"]["todos"])
    lines.extend(f"- Hold: {row['code']} ({row['owner']})" for row in payload.get("holds", []))
    if "readback" in payload:
        lines.append(f"- Original source restored: {payload['readback']['original_writer_fence_restored']}")
    if "context" in payload:
        lines.extend(["", "```json", json.dumps(payload["context"], ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def handle_goal_transfer(args: argparse.Namespace, *, registry_path: Path,
    print_payload: Callable[..., Any], output_format: Callable[..., str]) -> int:
    try:
        if args.goal_transfer_action == "inspect":
            payload = inspect_registered_goal_transfer(registry_path=registry_path, goal_id=args.goal_id)
        elif args.goal_transfer_action == "context":
            payload = {"ok": True, **read_goal_transfer_host_context(registry_path=registry_path,
                goal_id=args.goal_id, agent_id=args.agent_id, todo_id=args.todo_id)}
        elif args.goal_transfer_action == "cancel":
            payload = {"ok": True, **cancel_registered_goal_transfer(registry_path=registry_path, goal_id=args.goal_id,
                operation_id=args.operation_id)}
        else:
            payload = local_goal_transfer_cutover(registry_path=registry_path, goal_id=args.goal_id,
                operation_id=args.operation_id, destination_registry=args.destination_registry,
                expected_preview_sha256=getattr(args, "expected_preview_sha256", None), continuation=args.continuation)
    except Exception as error:
        payload = {"ok": False, "error": str(error)}
    print_payload(payload, output_format(args), render_goal_transfer)
    return 0 if payload.get("ok") else 1
