from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..capabilities.benchmark_toolkit import (
    admit_benchmark_token_capacity,
    build_benchmark_token_capacity_policy,
    build_benchmark_token_capacity_status,
    configure_benchmark_token_capacity,
    default_benchmark_token_capacity_path,
    observe_benchmark_token_usage,
    read_benchmark_token_capacity_state,
    record_benchmark_token_capacity_rejection,
    release_benchmark_token_capacity,
    render_benchmark_token_capacity_markdown,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]
OutputFormat = Callable[[argparse.Namespace], str]

BENCHMARK_TOKEN_CAPACITY_COMMANDS = {
    "token-capacity-status",
    "token-capacity-configure",
    "token-capacity-observe",
    "token-capacity-reject",
    "token-capacity-admit",
    "token-capacity-release",
}


def _add_location_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--project",
        help="Explicit project root. Defaults to the registry goal repository.",
    )
    parser.set_defaults(benchmark_token_capacity_parser=parser)


def _add_execute_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write project-local domain state. Without this flag, preview only.",
    )
    parser.add_argument(
        "--agent-id",
        help="Optional public-safe actor id recorded only in lock-holder diagnostics.",
    )


def register_benchmark_token_capacity_commands(
    benchmark_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    status = benchmark_subparsers.add_parser(
        "token-capacity-status",
        help="Read rolling token usage, reservations, and provider cooldown.",
    )
    add_subcommand_format(status)
    _add_location_arguments(status)

    configure = benchmark_subparsers.add_parser(
        "token-capacity-configure",
        help="Preview or configure a provider-neutral tokens-per-minute envelope.",
    )
    add_subcommand_format(configure)
    _add_location_arguments(configure)
    configure.add_argument("--quota-tokens-per-minute", required=True, type=int)
    configure.add_argument("--target-utilization-basis-points", type=int, default=9000)
    configure.add_argument(
        "--cold-start-reserve-tokens-per-minute", type=int, default=0
    )
    configure.add_argument("--evaluator-reserve-tokens-per-minute", type=int, default=0)
    configure.add_argument("--cooldown-seconds", type=int, default=60)
    _add_execute_argument(configure)

    observe = benchmark_subparsers.add_parser(
        "token-capacity-observe",
        help="Record one typed, request-level token usage sample.",
    )
    add_subcommand_format(observe)
    _add_location_arguments(observe)
    observe.add_argument(
        "--sample-json",
        required=True,
        help="benchmark_token_usage_sample_v0 JSON path, or - for stdin.",
    )
    _add_execute_argument(observe)

    reject = benchmark_subparsers.add_parser(
        "token-capacity-reject",
        help="Record a provider capacity rejection and start a bounded cooldown.",
    )
    add_subcommand_format(reject)
    _add_location_arguments(reject)
    reject.add_argument("--retry-after-seconds", type=int)
    _add_execute_argument(reject)

    admit = benchmark_subparsers.add_parser(
        "token-capacity-admit",
        help="Atomically reserve forecast token capacity before case admission.",
    )
    add_subcommand_format(admit)
    _add_location_arguments(admit)
    admit.add_argument("--run-id", required=True)
    admit.add_argument(
        "--workload-class", required=True, choices=("solver", "evaluator")
    )
    admit.add_argument("--tokens-per-minute", required=True, type=int)
    _add_execute_argument(admit)

    release = benchmark_subparsers.add_parser(
        "token-capacity-release",
        help="Release one exact run's token capacity reservation.",
    )
    add_subcommand_format(release)
    _add_location_arguments(release)
    release.add_argument("--run-id", required=True)
    _add_execute_argument(release)


def _read_json_argument(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    return json.loads(Path(value).expanduser().read_text(encoding="utf-8"))


def handle_benchmark_token_capacity_command(
    args: argparse.Namespace,
    *,
    project: Path | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.benchmark_command not in BENCHMARK_TOKEN_CAPACITY_COMMANDS:
        return None
    try:
        if project is None:
            raise ValueError("benchmark project route is required")
        path = default_benchmark_token_capacity_path(
            project=project, goal_id=args.goal_id
        )
        if args.benchmark_command == "token-capacity-status":
            payload: dict[str, Any] = build_benchmark_token_capacity_status(
                read_benchmark_token_capacity_state(path)
            )
        elif args.benchmark_command == "token-capacity-configure":
            payload = configure_benchmark_token_capacity(
                path,
                build_benchmark_token_capacity_policy(
                    quota_tokens_per_minute=args.quota_tokens_per_minute,
                    target_utilization_basis_points=(
                        args.target_utilization_basis_points
                    ),
                    cold_start_reserve_tokens_per_minute=(
                        args.cold_start_reserve_tokens_per_minute
                    ),
                    evaluator_reserve_tokens_per_minute=(
                        args.evaluator_reserve_tokens_per_minute
                    ),
                    cooldown_seconds=args.cooldown_seconds,
                ),
                execute=args.execute,
                agent_id=args.agent_id,
            )
        elif args.benchmark_command == "token-capacity-observe":
            payload = observe_benchmark_token_usage(
                path,
                _read_json_argument(args.sample_json),
                execute=args.execute,
                agent_id=args.agent_id,
            )
        elif args.benchmark_command == "token-capacity-reject":
            payload = record_benchmark_token_capacity_rejection(
                path,
                retry_after_seconds=args.retry_after_seconds,
                execute=args.execute,
                agent_id=args.agent_id,
            )
        elif args.benchmark_command == "token-capacity-admit":
            payload = admit_benchmark_token_capacity(
                path,
                run_id=args.run_id,
                workload_class=args.workload_class,
                tokens_per_minute=args.tokens_per_minute,
                execute=args.execute,
                agent_id=args.agent_id,
            )
        else:
            payload = release_benchmark_token_capacity(
                path,
                run_id=args.run_id,
                execute=args.execute,
                agent_id=args.agent_id,
            )
        payload["goal_id"] = args.goal_id
        payload["path_recorded"] = False
    except (OSError, TypeError, ValueError):
        args.benchmark_token_capacity_parser.error(
            "invalid benchmark token capacity input"
        )
    print_payload(
        payload, output_format(args), render_benchmark_token_capacity_markdown
    )
    return 0 if payload.get("ok") is not False else 2
