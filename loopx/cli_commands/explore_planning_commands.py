from __future__ import annotations

import argparse
from typing import Callable

from ..capabilities.explore.harness_gate import (
    DEFAULT_WORKER_HARNESS_PROFILE,
    worker_harness_profile_names,
)


def _add_resource_portfolio_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--resource-capacity",
        action="append",
        default=[],
        metavar="KEY=N",
        help=(
            "Declare a resource-lane capacity for resource_lane:<key> todos. "
            "Repeat for multiple lanes."
        ),
    )
    parser.add_argument(
        "--resource-usage",
        action="append",
        default=[],
        metavar="KEY=N",
        help=(
            "Declare current occupancy for a resource lane. Requires a matching "
            "--resource-capacity and may be repeated."
        ),
    )


def register_explore_planning_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
    add_projection_limit_args: Callable[[argparse.ArgumentParser], None],
) -> None:
    branch_plan = subparsers.add_parser(
        "todo-branch-plan",
        help=(
            "Experimentally rank multiple open agent todos as CPU-style predicted "
            "exploration branches. Read-only; emits commands but does not execute them."
        ),
    )
    add_subcommand_format(branch_plan)
    branch_plan.add_argument("--goal-id", required=True)
    add_projection_limit_args(branch_plan)
    branch_plan.add_argument("--agent-id", help="Prefer this agent's claimed todos, then unclaimed todos.")
    branch_plan.add_argument("--width", type=int, default=3, help="Maximum predicted branch issue width.")
    _add_resource_portfolio_args(branch_plan)
    branch_plan.add_argument(
        "--scheduler-strategy",
        choices=["dspark"],
        default="dspark",
        help="Use DSpark-style confidence scheduled verification.",
    )
    branch_plan.add_argument(
        "--scheduler-load",
        type=float,
        default=0.2,
        help="0..1 load factor for DSpark-style verification-budget scheduling.",
    )
    branch_plan.add_argument(
        "--allow-unscoped-parallel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Treat todos without declared write scopes as speculative read/coordination "
            "branches. Disable to select only one unscoped branch."
        ),
    )

    worker_branch_plan = subparsers.add_parser(
        "worker-branch-plan",
        help=(
            "Experimentally group open LoopX todos into worker-lane branches. "
            "Read-only; emits LoopX claim/lease suggestions but launches no workers."
        ),
    )
    add_subcommand_format(worker_branch_plan)
    worker_branch_plan.add_argument("--goal-id", required=True)
    add_projection_limit_args(worker_branch_plan)
    worker_branch_plan.add_argument(
        "--agent-id",
        help="Prefer this agent's claimed todos, then unclaimed todos.",
    )
    worker_branch_plan.add_argument(
        "--harness-profile",
        choices=worker_harness_profile_names(),
        default=DEFAULT_WORKER_HARNESS_PROFILE,
        help=(
            "Harness design profile. adaptive-resilient captures the best observed "
            "long-horizon traits without forcing N, duration, or full branch fill."
        ),
    )
    worker_branch_plan.add_argument(
        "--worker-width",
        type=int,
        default=3,
        help="Maximum predicted worker lanes; the planner may select fewer.",
    )
    _add_resource_portfolio_args(worker_branch_plan)
    worker_branch_plan.add_argument(
        "--max-todos-per-branch",
        type=int,
        default=None,
        help=(
            "Optional safety ceiling for LoopX todos bundled into one worker lane. "
            "When omitted, adaptive profiles choose bundle size from marginal value."
        ),
    )
    worker_branch_plan.add_argument(
        "--branch-fill-policy",
        choices=["bundle-by-affinity", "value-first"],
        help=(
            "bundle-by-affinity chunks related todos up to the ceiling; value-first "
            "only adds lower-ranked todos when their marginal score clears the floor."
        ),
    )
    worker_branch_plan.add_argument(
        "--marginal-score-floor",
        type=float,
        help="For value-first fill, require added todos to meet this fraction of the seed score.",
    )
    worker_branch_plan.add_argument(
        "--scheduler-load",
        type=float,
        default=0.2,
        help=(
            "0..1 fallback load factor for worker-lane scheduling; superseded by "
            "--load-profile observations when provided."
        ),
    )
    worker_branch_plan.add_argument(
        "--allow-unscoped-parallel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow multiple worker lanes whose todos do not declare write scopes.",
    )
    worker_branch_plan.add_argument(
        "--router-state",
        help=(
            "Path to a loopx_explore_router_state_v0 JSON file with cross-epoch "
            "per-family routing statistics (used by router-enabled profiles such "
            "as moe-router; maintained by the runner between epochs)."
        ),
    )
    worker_branch_plan.add_argument(
        "--load-profile",
        help=(
            "Path to a JSON file with observed parallel timings "
            "(parallel_wall_minutes, max_branch_minutes, branch_count) used to "
            "calibrate the lane-admission load factor from measurements."
        ),
    )
