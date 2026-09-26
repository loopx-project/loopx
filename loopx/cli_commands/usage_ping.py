from __future__ import annotations

import argparse
import os
from collections.abc import Callable

from .. import usage_ping
from ..cli_runtime import output_format


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
AddFormat = Callable[[argparse.ArgumentParser], None]


def render_usage_ping_markdown(payload: dict[str, object]) -> str:
    consent = payload["consent"]
    lines = [f"LoopX usage ping: {consent}"]
    if payload["sending"]:
        lines.append("- Sending at most one ping per UTC day.")
    elif consent == "enabled" and payload["blocked_by"]:
        lines.append(f"- Consent recorded, but {payload['blocked_by']} blocks sending on this shell.")
    elif consent == "enabled":
        lines.append("- Consent recorded; no collector endpoint is configured yet, so nothing is sent.")
    else:
        lines.append("- Nothing is sent. Enable with `loopx usage-ping enable`.")
    lines.append(f"- Endpoint: {payload['endpoint'] or 'not configured'}")
    lines.append(f"- Last sent: {payload['last_sent_day'] or 'never'}")
    lines.append(f"- State file: {payload['state_path']}")
    preview = payload.get("next_payload")
    if preview:
        lines += ["", "Exact payload that would be sent:"]
        lines += [f"  {key}: {value}" for key, value in preview.items()]
    lines += ["", "What is and is not collected: docs/reference/usage-ping.md"]
    return "\n".join(lines)


def register_usage_ping_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "usage-ping",
        help="Show, enable, or disable the opt-in anonymous daily usage ping (off by default).",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("status", "enable", "disable"),
        default="status",
        help="status (default) prints the exact payload; enable opts in; disable opts out and forgets the id.",
    )
    add_subcommand_format(parser)
    return parser


def handle_usage_ping_command(args: argparse.Namespace, print_payload: PrintPayload) -> int:
    path = usage_ping.state_path()
    if args.action == "enable":
        usage_ping.enable(path)
    elif args.action == "disable":
        usage_ping.disable(path)
    payload = usage_ping.status(path, env=os.environ)
    print_payload(payload, output_format(args), render_usage_ping_markdown)
    return 0
