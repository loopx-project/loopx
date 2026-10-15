"""Local owner configuration and real validation for a Goal acceptance basis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..control_plane.goals.acceptance import (
    configure_goal_acceptance,
    inspect_goal_acceptance,
    public_goal_acceptance,
    verify_goal_acceptance,
)


def register_goal_acceptance_command(subparsers: Any, add_format: Any) -> None:
    parser = subparsers.add_parser(
        "goal-acceptance",
        help="Configure, inspect or verify a versioned Goal acceptance basis.",
    )
    add_format(parser)
    parser.add_argument("action", choices=("inspect", "prepare", "configure", "verify", "disable"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--agent-id",
        help="Registered caller; Agents may inspect, prepare or verify, but preparation never approves or executes a plan.",
    )
    parser.add_argument(
        "--document",
        type=Path,
        help="Prepared check plan with explicit scope; prepare validates it without approval/execution, configure requires the owner.",
    )
    parser.add_argument(
        "--expected-provider-revision",
        help="Exact revision from inspect; required by configure/disable.",
    )
    parser.add_argument(
        "--operation-id", help="Stable identity for an unchanged configuration retry."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="With prepare, save a reviewable plan only; with configure/disable/verify, apply the named operation. Otherwise preview only.",
    )


def render_goal_acceptance(payload: dict[str, Any]) -> str:
    lines = ["# Goal acceptance", "", f"- ok: {payload.get('ok')}"]
    if payload.get("error"):
        return "\n".join([*lines, f"- error: {payload['error']}"])
    if payload.get("provider_revision"):
        lines.append(f"- provider revision: {payload['provider_revision']}")
    contract = payload.get("goal_acceptance_contract") or {}
    lines.append(f"- enabled: {contract.get('enabled', False)}")
    if contract.get("enabled"):
        lines.extend(
            [
                f"- coverage: {(contract.get('scope') or {}).get('kind', 'all_advancement')}",
                f"- acceptance revision: {contract.get('revision')}",
                f"- objective: {contract.get('objective')}",
            ]
        )
        for task in contract.get("tasks", []):
            lines.append(
                f"- {task.get('todo_id')}: {task.get('state')} ({', '.join(task.get('criterion_ids', []))})"
            )
        lines.append(
            f"- configured artifact checks: {contract.get('status', 'unverified')}"
        )
    return "\n".join(lines)


def handle_goal_acceptance_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Any,
    print_payload: Any,
) -> int | None:
    if args.command != "goal-acceptance":
        return None
    try:
        common = {
            "registry_path": registry_path,
            "goal_id": args.goal_id,
            "runtime_root": runtime_root_arg,
            "agent_id": args.agent_id,
        }
        if args.action == "prepare":
            from ..control_plane.goals.acceptance_actions import prepare_acceptance_check
            if args.document is None or not args.operation_id:
                raise ValueError("prepare requires --document and a stable --operation-id")
            if args.expected_provider_revision:
                raise ValueError("prepare reads the current basis itself")
            document = json.loads(args.document.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("acceptance document must be a JSON object")
            payload = prepare_acceptance_check(**common, document=document,
                operation_id=args.operation_id, execute=args.execute)
        elif args.action in {"configure", "disable"}:
            if not args.expected_provider_revision:
                raise ValueError(
                    "configure/disable requires --expected-provider-revision from inspect"
                )
            if args.action == "configure" and args.document is None:
                raise ValueError("configure requires --document")
            if args.action == "disable" and args.document is not None:
                raise ValueError("disable does not accept --document")
            document = (
                json.loads(args.document.read_text(encoding="utf-8"))
                if args.document
                else None
            )
            if document is not None and not isinstance(document, dict):
                raise ValueError("acceptance document must be a JSON object")
            payload = public_goal_acceptance(
                configure_goal_acceptance(
                    **common,
                    document=document,
                    expected_provider_revision=args.expected_provider_revision,
                    operation_id=args.operation_id,
                    execute=args.execute,
                    disable=args.action == "disable",
                )
            )
        else:
            if args.document or args.expected_provider_revision or args.operation_id:
                raise ValueError("configuration arguments require configure or disable")
            if args.action == "inspect":
                if args.execute:
                    raise ValueError("inspect is read-only")
                payload = public_goal_acceptance(inspect_goal_acceptance(**common))
            else:
                payload = verify_goal_acceptance(**common, execute=args.execute)
        code = (
            1
            if payload.get("checks_passed") is False
            or payload.get("acceptance_ready") is False
            else 0
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        payload, code = {"ok": False, "error": str(exc)}, 1
    print_payload(payload, output_format(args), render_goal_acceptance)
    return code
