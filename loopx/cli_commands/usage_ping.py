from __future__ import annotations

import argparse
from collections.abc import Callable

from .. import usage_ping
from ..cli_runtime import output_format


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
AddFormat = Callable[[argparse.ArgumentParser], None]


def render_usage_ping_markdown(payload: dict[str, object]) -> str:
    lines = [f"LoopX basic usage statistics: {payload['consent']}",
             f"- Sending eligible: {payload['sending']}; blocked by: {payload['blocked_by'] or 'none'}",
             f"- Endpoint: {payload['endpoint'] or 'not configured'}",
             f"- Last heartbeat: {payload['last_sent_day'] or 'never'}",
             str(payload['disclosure']), "", "Payload previews (aggregate sends after the UTC day closes):"]
    import json
    lines.append(json.dumps({"heartbeat": payload.get("next_payload"),
                             "aggregate": payload.get("aggregate_preview")}, indent=2))
    lines.append("Details: docs/reference/usage-ping.md")
    return "\n".join(lines)


def register_usage_ping_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "usage-ping",
        help="Inspect or disable default-on basic usage statistics; explicit enable is available.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("status", "enable", "disable"),
        default="status",
        help="status previews payloads; enable accepts collection; disable clears the ID and pending counts.",
    )
    add_subcommand_format(parser)
    return parser


def handle_usage_ping_command(args: argparse.Namespace, print_payload: PrintPayload) -> int:
    payload = usage_ping.control(args.action)
    print_payload(payload, output_format(args), render_usage_ping_markdown)
    return 0
