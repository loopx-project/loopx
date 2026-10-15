"""Explicit cold Goal direction operations; no autonomous runtime activation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError


def register_goal_direction(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], add_format: Callable[..., Any]) -> None:
    parser = subparsers.add_parser("goal-direction", help="Declare Goal materials, read as one Agent, acknowledge and inspect its direction basis.")
    parser.add_argument("--operation", required=True, metavar="OPERATION",
        help="inspect, configure, read, acknowledge, receipt or disable; writes require --execute.")
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--goal-instance-id", required=True)
    parser.add_argument("--agent-id")
    parser.add_argument("--request-json")
    parser.add_argument("--execute", action="store_true")
    add_format(parser)


def handle_goal_direction(args: argparse.Namespace, *, registry_path: Path,
                          output_format: Callable[..., str], print_payload: Callable[..., Any]) -> int | None:
    if args.command != "goal-direction":
        return None
    try:
        from ..control_plane.goals.direction_runtime import execute_goal_direction
        parameters = {}
        if args.request_json:
            with Path(args.request_json).expanduser().open("rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError("Direction request exceeds 256 KiB")
            parameters = json.loads(raw)
            if not isinstance(parameters, dict):
                raise ValueError("Direction request must be an object")
        result = execute_goal_direction(registry_path=registry_path, goal_id=args.goal_id, goal_instance_id=args.goal_instance_id,
            operation=args.operation, agent_id=args.agent_id, parameters=parameters, execute=args.execute)
    except (OSError, ValueError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        result = {"ok": False, "status": "unavailable", "reason": "Direction source or operation is unavailable; inspect its current Goal basis before retrying.",
                  "execution_authority_granted": False, "acceptance_assessed": False}
    print_payload(result, output_format(args), lambda value: json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") is True else 1
