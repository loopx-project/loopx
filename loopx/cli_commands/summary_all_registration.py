from __future__ import annotations

import argparse
from collections.abc import Callable


GLOBAL_READ_COMMANDS = frozenset({
    "goal-portfolio", "global-summary", "global-gates", "global-todos", "global-risks",
})


def _add_current_manager_flags(
    parser: argparse.ArgumentParser,
    *,
    agent_help: str,
    limit_help: str,
) -> None:
    parser.add_argument("--agent-id", help=agent_help)
    parser.add_argument("--limit", type=int, default=8, help=limit_help)



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
