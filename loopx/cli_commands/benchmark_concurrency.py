from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..capabilities.benchmark_toolkit.experiment_identity import ARM_ROLE_CHOICES
from ..capabilities.benchmark_toolkit import (
    admit_benchmark_case,
    build_benchmark_adaptive_concurrency_policy,
    build_benchmark_concurrency_config,
    build_benchmark_concurrency_status,
    configure_benchmark_concurrency_envelope,
    default_benchmark_concurrency_envelope_path,
    read_benchmark_concurrency_envelope,
    release_benchmark_case,
    render_benchmark_concurrency_markdown,
    tune_benchmark_concurrency_target,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]
OutputFormat = Callable[[argparse.Namespace], str]

BENCHMARK_CONCURRENCY_COMMANDS = {
    "concurrency-status",
    "concurrency-configure",
    "concurrency-admit",
    "concurrency-release",
    "concurrency-tune",
}


def _add_location_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--project",
        help="Explicit project root. Defaults to the registry goal repository.",
    )
    parser.set_defaults(benchmark_concurrency_parser=parser)


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


def register_benchmark_concurrency_commands(
    benchmark_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    status_parser = benchmark_subparsers.add_parser(
        "concurrency-status",
        help="Read the project-local benchmark concurrency envelope.",
    )
    add_subcommand_format(status_parser)
    _add_location_arguments(status_parser)

    configure_parser = benchmark_subparsers.add_parser(
        "concurrency-configure",
        help="Preview or configure combined baseline and test case concurrency.",
    )
    add_subcommand_format(configure_parser)
    _add_location_arguments(configure_parser)
    configure_parser.add_argument("--max-active-cases", required=True, type=int)
    configure_parser.add_argument(
        "--target-active-cases",
        type=int,
        help="Desired active occupancy; defaults to the hard maximum.",
    )
    configure_parser.add_argument("--max-baseline-cases", type=int)
    configure_parser.add_argument("--max-test-cases", type=int)
    configure_parser.add_argument("--reserved-test-cases", type=int, default=0)
    configure_parser.add_argument(
        "--require-resource-headroom-receipt",
        action="store_true",
        help=(
            "Fail closed unless each new admission supplies a fresh typed resource "
            "headroom receipt."
        ),
    )
    _add_execute_argument(configure_parser)

    admit_parser = benchmark_subparsers.add_parser(
        "concurrency-admit",
        help="Atomically reserve one configured benchmark case slot before launch.",
    )
    add_subcommand_format(admit_parser)
    _add_location_arguments(admit_parser)
    admit_parser.add_argument("--run-id", required=True)
    admit_parser.add_argument("--case-id", required=True)
    admit_parser.add_argument(
        "--arm-role",
        required=True,
        choices=ARM_ROLE_CHOICES,
    )
    admit_parser.add_argument(
        "--resource-headroom-json",
        help=(
            "Public-safe benchmark_resource_headroom_receipt_v0 JSON path, or - "
            "for stdin. Required when the configured envelope enables the gate."
        ),
    )
    _add_execute_argument(admit_parser)

    release_parser = benchmark_subparsers.add_parser(
        "concurrency-release",
        help="Release one active benchmark case slot after a terminal transition.",
    )
    add_subcommand_format(release_parser)
    _add_location_arguments(release_parser)
    release_parser.add_argument("--run-id", required=True)
    _add_execute_argument(release_parser)

    tune_parser = benchmark_subparsers.add_parser(
        "concurrency-tune",
        help="Adapt desired occupancy within the operator-owned hard ceiling.",
    )
    add_subcommand_format(tune_parser)
    _add_location_arguments(tune_parser)
    tune_parser.add_argument(
        "--feedback-json",
        required=True,
        help="Public-safe benchmark_concurrency_feedback_v0 JSON path, or - for stdin.",
    )
    tune_parser.add_argument(
        "--resource-headroom-json",
        help="Fresh benchmark_resource_headroom_receipt_v0 JSON path.",
    )
    tune_parser.add_argument("--minimum-target-active-cases", type=int, default=1)
    tune_parser.add_argument("--increase-step", type=int, default=1)
    tune_parser.add_argument("--decrease-step", type=int, default=1)
    tune_parser.add_argument(
        "--saturated-healthy-windows-required", type=int, default=2
    )
    _add_execute_argument(tune_parser)


def _read_json_argument(value: str) -> Any:
    if value == "-":
        return json.load(sys.stdin)
    return json.loads(Path(value).expanduser().read_text(encoding="utf-8"))


def _tune_payload(args: argparse.Namespace, *, path: Path) -> dict[str, Any]:
    if args.feedback_json == "-" and args.resource_headroom_json == "-":
        raise ValueError("only one JSON input may use stdin")
    feedback = _read_json_argument(args.feedback_json)
    resource_headroom_receipt = (
        _read_json_argument(args.resource_headroom_json)
        if args.resource_headroom_json
        else None
    )
    policy = build_benchmark_adaptive_concurrency_policy(
        minimum_target_active_cases=args.minimum_target_active_cases,
        increase_step=args.increase_step,
        decrease_step=args.decrease_step,
        saturated_healthy_windows_required=args.saturated_healthy_windows_required,
    )
    return tune_benchmark_concurrency_target(
        path,
        policy=policy,
        feedback=feedback,
        resource_headroom_receipt=resource_headroom_receipt,
        execute=args.execute,
        agent_id=args.agent_id,
    )


def handle_benchmark_concurrency_command(
    args: argparse.Namespace,
    *,
    project: Path | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.benchmark_command not in BENCHMARK_CONCURRENCY_COMMANDS:
        return None
    try:
        if project is None:
            raise ValueError("benchmark project route is required")
        path = default_benchmark_concurrency_envelope_path(
            project=project,
            goal_id=args.goal_id,
        )
        if args.benchmark_command == "concurrency-status":
            payload: dict[str, Any] = build_benchmark_concurrency_status(
                read_benchmark_concurrency_envelope(path)
            )
        elif args.benchmark_command == "concurrency-configure":
            config = build_benchmark_concurrency_config(
                max_active_cases=args.max_active_cases,
                target_active_cases=args.target_active_cases,
                max_baseline_cases=args.max_baseline_cases,
                max_test_cases=args.max_test_cases,
                reserved_test_cases=args.reserved_test_cases,
                require_resource_headroom_receipt=(
                    args.require_resource_headroom_receipt
                ),
            )
            payload = configure_benchmark_concurrency_envelope(
                path,
                config,
                execute=args.execute,
                agent_id=args.agent_id,
            )
        elif args.benchmark_command == "concurrency-admit":
            resource_headroom_receipt = None
            if args.resource_headroom_json:
                resource_headroom_receipt = _read_json_argument(
                    args.resource_headroom_json
                )
            payload = admit_benchmark_case(
                path,
                run_id=args.run_id,
                case_id=args.case_id,
                arm_role=args.arm_role,
                execute=args.execute,
                agent_id=args.agent_id,
                resource_headroom_receipt=resource_headroom_receipt,
            )
        elif args.benchmark_command == "concurrency-release":
            payload = release_benchmark_case(
                path,
                run_id=args.run_id,
                execute=args.execute,
                agent_id=args.agent_id,
            )
        else:
            payload = _tune_payload(args, path=path)
        payload["goal_id"] = args.goal_id
        payload["path_recorded"] = False
    except (OSError, TypeError, ValueError):
        args.benchmark_concurrency_parser.error(
            "invalid benchmark concurrency envelope input"
        )
    print_payload(payload, output_format(args), render_benchmark_concurrency_markdown)
    return 0 if payload.get("ok") is not False else 2
