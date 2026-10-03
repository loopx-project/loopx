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
from ..paths import default_public_scan_root
from ..summary_all import (
    build_global_gates,
    build_global_gates_error,
    build_summary_all,
    render_global_gates_markdown,
    render_summary_all_markdown,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
def _scan_roots(args: argparse.Namespace) -> list[Path]:
    scan_roots = [Path(item).expanduser() for item in args.scan_path]
    return scan_roots or [Path(args.scan_root).expanduser()]


def _add_current_manager_flags(
    parser: argparse.ArgumentParser,
    *,
    agent_help: str,
    limit_help: str,
) -> None:
    parser.add_argument("--agent-id", help=agent_help)
    parser.add_argument("--limit", type=int, default=8, help=limit_help)
    parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help=(
            "Public files to scan for obvious private material. "
            "Defaults to the LoopX install root."
        ),
    )
    parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help=(
            "Specific public file or directory to scan. Repeatable. "
            "Overrides --scan-root when set."
        ),
    )


def register_summary_all_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    portfolio = subparsers.add_parser(
        "goal-portfolio", help="Read scoped Goal evidence with explicit source coverage."
    )
    add_subcommand_format(portfolio)
    portfolio.add_argument("--manager-view", choices=("portfolio", "todos", "deliveries", "agents"), help="Export an audience-safe manager evidence page from this registry.")
    portfolio.add_argument("--query", default="", help="Search registered Agent identities and responsibilities with --manager-view agents.")
    portfolio.add_argument("--todo-id", dest="context_todo_id", help="Read an exact permitted Todo with --manager-view todos and one --goal-id.")
    portfolio.add_argument("--offset", type=int, default=0)
    portfolio.add_argument("--days", type=int, default=1)
    portfolio.add_argument("--include-stopped", action="store_true")
    portfolio.add_argument(
        "--goal-id", action="append", dest="portfolio_goal_ids",
        help="Exact registered Goal to include; repeat to narrow scope.",
    )
    portfolio.add_argument(
        "--limit", type=int, default=8,
        help="Maximum Goals to read, 1..128; omitted Goals remain in coverage.",
    )
    portfolio.add_argument(
        "--max-age-hours", type=float, default=24,
        help="Maximum age of a progress source before it is marked stale.",
    )
    parser = subparsers.add_parser(
        "global-summary",
        help=(
            "Read a public-safe /loopx-global-summary progress digest; "
            "see `loopx slash-commands` for slash help."
        ),
    )
    add_subcommand_format(parser)
    _add_current_manager_flags(
        parser,
        agent_help="Registered agent id for agent-lane quota projection.",
        limit_help="Maximum items per digest section.",
    )
    parser.add_argument(
        "--time-range",
        default="24h",
        help="Recent progress window, e.g. 24h or 7d.",
    )

    gates_parser = subparsers.add_parser(
        "global-gates",
        help=(
            "Read public-safe /loopx-global-gates user/controller decisions; "
            "see `loopx slash-commands` for slash help."
        ),
    )
    add_subcommand_format(gates_parser)
    _add_current_manager_flags(
        gates_parser,
        agent_help="Registered agent id used to narrow gate projection.",
        limit_help="Maximum returned gate count.",
    )

    todos_parser = subparsers.add_parser(
        "global-todos",
        help=(
            "Read a public-safe current-state todo inbox across visible goals; "
            "see `loopx slash-commands` for slash help."
        ),
    )
    add_subcommand_format(todos_parser)
    _add_current_manager_flags(
        todos_parser,
        agent_help="Registered agent id used to narrow todo projection.",
        limit_help="Maximum returned todo count.",
    )

    risks_parser = subparsers.add_parser(
        "global-risks",
        help=(
            "Read a public-safe current risk inbox across visible goals; "
            "see `loopx slash-commands` for slash help."
        ),
    )
    add_subcommand_format(risks_parser)
    _add_current_manager_flags(
        risks_parser,
        agent_help="Registered agent id used to narrow risk projection.",
        limit_help="Maximum returned risk count.",
    )
    risks_parser.add_argument(
        "--time-range",
        default="24h",
        help=(
            "Risk request window for protocol compatibility, e.g. 24h or 7d; "
            "current risks remain visible."
        ),
    )


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
                scan_roots=_scan_roots(args),
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
                scan_roots=_scan_roots(args),
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
                scan_roots=_scan_roots(args),
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
            scan_roots=_scan_roots(args),
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
