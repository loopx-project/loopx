from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from ..capabilities.explore.quantitative_planning import (
    evaluate_budgeted_continuation_plan,
    evaluate_counterfactual_continuations,
    list_stage_methodology_operators,
)
from ..capabilities.explore.worker_branch_plan import (
    DEFAULT_WORKER_HARNESS_PROFILE,
    worker_harness_profile_names,
)

STATELESS_EXPLORE_PLANNING_COMMANDS = frozenset(
    {
        "budgeted-plan",
        "counterfactual-eval",
        "methodology-operators",
    }
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

    yardstick_fidelity = subparsers.add_parser(
        "yardstick-fidelity",
        help=(
            "Evaluate scoped multi-point Yardstick rank concordance, frontier bias cliffs, "
            "and unconfirmed proxy winners from recorded Explore findings. Advisory only."
        ),
    )
    add_subcommand_format(yardstick_fidelity)
    yardstick_fidelity.add_argument("--goal-id", required=True)
    yardstick_fidelity.add_argument("--node-id", help="Filter to one Explore question node.")
    yardstick_fidelity.add_argument("--todo-id", help="Filter declared Yardsticks to one Todo.")
    yardstick_fidelity.add_argument("--agent-id", help="Filter declared Yardsticks to one Agent.")
    yardstick_fidelity.add_argument(
        "--frontier-k",
        type=int,
        help="Requested top-K cutoff (at least 2). Includes candidates tied at the cutoff.",
    )
    yardstick_fidelity.add_argument(
        "--profiles-json",
        help="Optional JSON file specifying explicit {authoritative, proxy, node_id} profiles.",
    )

    evolutionary_plan = subparsers.add_parser(
        "evolutionary-plan",
        help=(
            "Select mechanism-diverse evolutionary elites, orthogonal crossover pairs, "
            "and bottleneck mutations while quarantining Goodharted proxy winners. Advisory only."
        ),
    )
    add_subcommand_format(evolutionary_plan)
    evolutionary_plan.add_argument("--goal-id", required=True)
    evolutionary_plan.add_argument("--node-id", help="Filter to one Explore question node.")
    evolutionary_plan.add_argument("--todo-id", help="Filter declared Yardsticks to one Todo.")
    evolutionary_plan.add_argument("--agent-id", help="Filter declared Yardsticks to one Agent.")
    evolutionary_plan.add_argument("--elite-limit", type=int, help="Maximum confirmed elites (2..12).")
    evolutionary_plan.add_argument("--crossover-limit", type=int, help="Maximum orthogonal crossover pairs (0..6).")
    evolutionary_plan.add_argument("--mutation-limit", type=int, help="Maximum targeted mutations (0..6).")
    evolutionary_plan.add_argument(
        "--profiles-json",
        help="Optional JSON file specifying explicit {authoritative, proxy, node_id, mechanism_by_candidate}.",
    )

    budgeted_plan = subparsers.add_parser(
        "budgeted-plan",
        help=(
            "Evaluate confirmation-deadline back-scheduling, reversible probe slack, and "
            "break-even thresholds across candidate continuation routes. Advisory only."
        ),
    )
    add_subcommand_format(budgeted_plan)
    budgeted_plan.add_argument(
        "--document",
        required=True,
        help="Path to a budgeted_continuation_plan_v0 request JSON file.",
    )

    counterfactual_eval = subparsers.add_parser(
        "counterfactual-eval",
        help=(
            "Evaluate same-checkpoint paired counterfactual continuations (tau_B, "
            "identification/non-interference guards, action divergence, complete marginal costs)."
        ),
    )
    add_subcommand_format(counterfactual_eval)
    counterfactual_eval.add_argument(
        "--document",
        required=True,
        help="Path to a counterfactual_continuation_eval_v0 request JSON file.",
    )
    counterfactual_eval.add_argument(
        "--strict-identification",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fail closed on checkpoint/isolation contamination (default true) or audit with --no-strict-identification.",
    )

    methodology_ops = subparsers.add_parser(
        "methodology-operators",
        help="List registered 5-stage methodology operators, enablement gates, and anti-entropy invariants.",
    )
    add_subcommand_format(methodology_ops)
    methodology_ops.add_argument(
        "--stage",
        choices=["admission", "compile_context", "execute_verify", "writeback_settle", "feedback_evolve"],
        help="Filter operators participating in one control-loop stage.",
    )


def handle_stateless_explore_planning_command(args: argparse.Namespace) -> dict[str, Any]:
    cmd = str(args.explore_command)
    if cmd == "budgeted-plan":
        doc = json.loads(Path(args.document).read_text(encoding="utf-8-sig"))
        if not isinstance(doc, dict):
            raise ValueError("budgeted-plan --document must contain a JSON object")
        return evaluate_budgeted_continuation_plan(doc)
    if cmd == "counterfactual-eval":
        doc = json.loads(Path(args.document).read_text(encoding="utf-8-sig"))
        if not isinstance(doc, dict):
            raise ValueError("counterfactual-eval --document must contain a JSON object")
        return evaluate_counterfactual_continuations(
            doc,
            strict_identification=args.strict_identification,
        )
    if cmd == "methodology-operators":
        return list_stage_methodology_operators(stage=getattr(args, "stage", None))
    raise ValueError(f"unsupported stateless explore command: {cmd}")
