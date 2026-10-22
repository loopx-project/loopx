from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..goal_portfolio import build_goal_portfolio, render_goal_portfolio
from ..global_risks import (
    build_global_risks,
    build_global_risks_error,
    render_global_risks_markdown,
)
from ..global_todos import (
    build_global_todos,
    build_global_todos_error,
    render_global_todos_markdown,
)
from ..summary_all import (
    build_global_gates,
    build_global_gates_error,
    build_summary_all,
    render_global_gates_markdown,
    render_summary_all_markdown,
)

from .summary_all_registration import register_summary_all_command as register_summary_all_command


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def handle_summary_all_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in {
        "goal-portfolio",
        "global-summary",
        "global-gates",
        "global-todos",
        "global-risks",
    }:
        return None
    if args.command == "goal-portfolio":
        if getattr(args, "context_todo_id", None) is not None and not args.manager_view:
            payload = {"ok": False, "error": "--todo-id requires --manager-view todos and one --goal-id.", "rows": []}
            print_payload(payload, output_format(args), lambda p: json.dumps(p, ensure_ascii=False, indent=2))
            return 1
        if args.manager_view:
            from ..capabilities.manager_context.evidence_export import export_page
            try:
                payload = export_page(registry_path, runtime_root_arg, args)
            except (OSError, ValueError, TypeError):
                payload = {"ok": False, "error": "manager_evidence_unavailable_or_invalid", "rows": []}
            print_payload(payload, output_format(args), lambda p: json.dumps(p, ensure_ascii=False, indent=2))
            return 0 if payload.get("ok") else 1
        try:
            payload = build_goal_portfolio(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                goal_ids=args.portfolio_goal_ids,
                limit=args.limit,
                max_age_hours=args.max_age_hours,
            )
        except ValueError:
            payload = {
                "ok": False, "error": "Invalid portfolio scope or bounds.",
                "coverage": {"discovered": None, "verified": 0, "complete": False},
                "goals": [],
            }
        print_payload(payload, output_format(args), render_goal_portfolio)
        return 0 if payload.get("ok") else 1
    if args.command == "global-risks":
        try:
            payload = build_global_risks(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                agent_id=args.agent_id,
                time_range=args.time_range,
                limit=max(1, args.limit),
            )
        except Exception as exc:
            payload = build_global_risks_error(exc, time_range=args.time_range)
        print_payload(payload, output_format(args), render_global_risks_markdown)
        return 0 if payload.get("ok") else 1
    if args.command == "global-gates":
        try:
            payload = build_global_gates(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                agent_id=args.agent_id,
                limit=max(1, args.limit),
            )
        except Exception as exc:
            payload = build_global_gates_error(exc)
        print_payload(payload, output_format(args), render_global_gates_markdown)
        return 0 if payload.get("ok") else 1
    if args.command == "global-todos":
        try:
            payload = build_global_todos(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                agent_id=args.agent_id,
                limit=max(1, args.limit),
            )
        except Exception as exc:
            payload = build_global_todos_error(exc)
        print_payload(payload, output_format(args), render_global_todos_markdown)
        return 0 if payload.get("ok") else 1

    try:
        payload = build_summary_all(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            agent_id=args.agent_id,
            time_range=args.time_range,
            limit=max(1, args.limit),
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "global_manager_command_response_v0",
            "request": {
                "schema_version": "global_manager_command_request_v0",
                "command": "/loopx-global-summary",
                "legacy_aliases": ["/loop-global-summary"],
                "privacy_mode": "public_safe_summary",
                "dry_run": True,
            },
            "error": str(exc),
        }
    print_payload(payload, output_format(args), render_summary_all_markdown)
    return 0 if payload.get("ok") else 1
