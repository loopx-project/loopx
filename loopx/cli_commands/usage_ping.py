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
             f"- Deployment context: {payload.get('effective_context', 'unknown')} ({payload.get('context_source', 'default')})",
             str(payload['disclosure']), "", "Payload previews (first CLI result immediately; later activity at most every 15 minutes):"]
    import json
    lines.append(json.dumps({"heartbeat": payload.get("next_payload"),
                             "aggregate": payload.get("aggregate_preview"),
                             "diagnostics": payload.get("diagnostic_preview"),
                             "goals": payload.get("goal_preview"),
                             "installation": payload.get("installation_preview"),
                             "diagnostic_dropped": payload.get("diagnostic_dropped", 0),
                             "identity_scope": payload.get("identity_scope"),
                             "delivery_history": payload.get("delivery_history", [])}, indent=2))
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
        choices=("status", "enable", "disable", "context"),
        default="status",
        help="status previews payloads; enable accepts collection; disable clears the ID and pending counts.",
    )
    parser.add_argument("--context", choices=("unknown", "personal", "shared_service", "ephemeral", "organization_managed", "maintainer"),
                        help="Persistent device label; only with context. Does not enable collection; environment takes precedence.")
    add_subcommand_format(parser)
    return parser


def handle_usage_ping_command(args: argparse.Namespace, print_payload: PrintPayload) -> int:
    if (args.action == "context") != (args.context is not None):
        raise ValueError("use usage-ping context --context <value>; other actions take no context")
    payload = usage_ping.control(args.action, **({"context": args.context} if args.context is not None else {}))
    print_payload(payload, output_format(args), render_usage_ping_markdown)
    return 0
