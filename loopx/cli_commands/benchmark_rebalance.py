from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..capabilities.benchmark_toolkit import (
    build_benchmark_rebalance_plan_from_inventory,
    render_benchmark_rebalance_plan_markdown,
)

BENCHMARK_REBALANCE_COMMANDS = {"rebalance-plan"}
PrintPayload = Callable[..., None]
OutputFormat = Callable[..., str]


def register_benchmark_rebalance_commands(
    benchmark_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = benchmark_subparsers.add_parser(
        "rebalance-plan",
        help="Select one typed runner-owned drain candidate without mutating a run.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--inventory-json",
        required=True,
        help="Public-safe benchmark_rebalance_inventory_v0 JSON path, or - for stdin.",
    )
    parser.set_defaults(benchmark_rebalance_parser=parser)


def _read_inventory(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    return json.loads(Path(value).expanduser().read_text(encoding="utf-8"))


def handle_benchmark_rebalance_command(
    args: argparse.Namespace,
    *,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.benchmark_command not in BENCHMARK_REBALANCE_COMMANDS:
        return None
    try:
        payload = build_benchmark_rebalance_plan_from_inventory(
            _read_inventory(args.inventory_json)
        )
    except (OSError, TypeError, ValueError):
        args.benchmark_rebalance_parser.error("invalid benchmark rebalance inventory")
    print_payload(payload, output_format(args), render_benchmark_rebalance_plan_markdown)
    return 0
