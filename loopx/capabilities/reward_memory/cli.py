from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ..issue_fix.reward_memory import ingest_issue_fix_reward_memory_event
from .architecture import (
    build_reward_memory_architecture_packet,
    build_reward_memory_route_packet,
    pr_3237_regression_observation,
)
from .candidate_review import (
    issue_fix_verified_contributor_candidate_fixture,
    review_reward_memory_candidate,
)
from .health import (
    build_reward_memory_corpus_health_packet,
    reward_memory_health_case,
)
from .dogfood import (
    build_reward_memory_dogfood_batch,
    build_reward_memory_dogfood_receipt,
    build_reward_memory_operator_control,
)
from .evaluation import run_reward_memory_evaluation
from .experiment import (
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
)
from .registry import build_reward_memory_corpus_registry_packet
from .scoped_feedback import (
    SCOPED_FEEDBACK_ADAPTER,
    ingest_scoped_feedback_reward_memory_event,
)


def _render(payload: dict[str, object]) -> str:
    lines = ["# Reward Memory", ""]
    for key in (
        "status",
        "decision",
        "effective_decision",
        "health_state",
        "case_ref",
    ):
        if key in payload:
            lines.append(f"- {key}: `{payload[key]}`")
    classes = payload.get("memory_classes")
    if isinstance(classes, list):
        lines.append(f"- memory_classes: `{len(classes)}`")
    if "corpus_count" in payload:
        lines.append(f"- corpus_count: `{payload['corpus_count']}`")
    reasons = payload.get("reason_codes")
    if isinstance(reasons, list):
        lines.append("- reason_codes: `" + ", ".join(map(str, reasons)) + "`")
    return "\n".join(lines) + "\n"


def _load_json_object(path_value: str) -> dict[str, object]:
    path = Path(path_value).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read input JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("input JSON must contain one object")
    return payload


def register_reward_memory_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "reward-memory",
        help="Inspect reward-memory classes and pilot/meta routing boundaries.",
    )
    sub = parser.add_subparsers(dest="reward_memory_command", required=True)
    architecture = sub.add_parser(
        "architecture",
        help="Render the provider-neutral Stage-0 architecture contract.",
    )
    add_subcommand_format(architecture)

    registry = sub.add_parser(
        "corpus-registry",
        help="Render the Stage-1 corpus ownership and maintenance registry.",
    )
    add_subcommand_format(registry)

    health = sub.add_parser(
        "health-check",
        help="Exercise a Stage-1 scope, freshness, retrieval, and readback state.",
    )
    add_subcommand_format(health)
    health.add_argument(
        "--case",
        choices=[
            "unavailable",
            "empty",
            "stale",
            "wrong-project",
            "wrong-surface",
            "index-unavailable",
            "retrieval-failed",
            "readback-unverified",
            "retrieval-verified",
            "applied-verified",
        ],
        default="retrieval-verified",
        help="Use a compact public health fixture.",
    )

    candidate = sub.add_parser(
        "candidate-review",
        help="Exercise the stateless Stage-2 candidate and review seam.",
    )
    add_subcommand_format(candidate)
    candidate.add_argument(
        "--case",
        choices=["issue-fix-verified-contributor"],
        default="issue-fix-verified-contributor",
        help="Use a compact public Issue Fix adapter fixture.",
    )
    candidate.add_argument(
        "--decision",
        choices=["accept", "edit", "reject", "retire", "no_write"],
        default="accept",
        help="Apply one review decision without persisting provider state.",
    )

    ingest = sub.add_parser(
        "ingest-event",
        help=(
            "Plan or execute one compact standing-policy write, exact readback, "
            "and receipt."
        ),
    )
    add_subcommand_format(ingest)
    ingest.add_argument(
        "--input",
        required=True,
        help=(
            "With --goal-id/--agent-id: compact JSON containing event and the "
            "immutable observed_at (adapter is optional). Without them: a "
            "full public fixture for no-write evaluation. Raw comments are rejected."
        ),
    )
    ingest.add_argument(
        "--goal-id",
        help=(
            "Resolve the goal's default-off experiment config. Must be paired "
            "with --agent-id."
        ),
    )
    ingest.add_argument(
        "--agent-id",
        help=(
            "Use one registered, explicitly enabled agent lane. Must be paired "
            "with --goal-id."
        ),
    )
    ingest.add_argument(
        "--execute",
        action="store_true",
        help="Perform the configured provider write and exact readback.",
    )

    status = sub.add_parser(
        "experiment-status",
        help="Inspect one agent-scoped experimental route without provider access.",
    )
    add_subcommand_format(status)
    status.add_argument("--goal-id", required=True)
    status.add_argument("--agent-id", required=True)

    evaluate = sub.add_parser(
        "evaluate",
        help="Run the bounded Stage-4 core-contract suite and release gate.",
    )
    add_subcommand_format(evaluate)

    dogfood = sub.add_parser(
        "dogfood-evaluate",
        help="Evaluate compact Stage-5 module outcomes after the Stage-4 gate.",
    )
    add_subcommand_format(dogfood)
    dogfood.add_argument(
        "--input",
        required=True,
        help=(
            "JSON object containing observations and operator_controls; no raw "
            "provider content is accepted."
        ),
    )

    control = sub.add_parser(
        "operator-control",
        help="Prepare an authorized Stage-5 edit or retire decision without writing.",
    )
    add_subcommand_format(control)
    control.add_argument("--input", required=True)
    control.add_argument("--action", choices=["edit", "retire"], required=True)
    control.add_argument("--control-ref", required=True)
    control.add_argument("--reasoning-summary", required=True)
    control.add_argument("--edited-content-summary")

    route = sub.add_parser(
        "route-check",
        help="Route a compact issue-fix observation to pilot, meta, or evidence hold.",
    )
    add_subcommand_format(route)
    route.add_argument(
        "--case",
        choices=["pr-3237"],
        help="Use a built-in public regression observation.",
    )
    route.add_argument(
        "--behavior-status",
        choices=["bug_confirmed", "by_design", "uncertain"],
        default="bug_confirmed",
    )
    route.add_argument("--surface-count", type=int, default=1)
    route.add_argument("--semantic-contract-change", action="store_true")
    route.add_argument("--generic-boundary-for-specific-policy", action="store_true")
    route.add_argument("--user-visible-behavior-change", action="store_true")
    route.add_argument("--hot-path-or-storage-change", action="store_true")
    route.add_argument("--retrieval-or-memory-quality-claim", action="store_true")
    route.add_argument(
        "--edge-case-complexity",
        choices=["low", "medium", "high"],
        default="low",
    )
    route.add_argument("--named-reproduction", action="store_true")
    route.add_argument("--named-validation", action="store_true")
    route.add_argument("--effect-evidence", action="store_true")
    route.add_argument("--ux-evidence", action="store_true")
    route.add_argument("--benchmark-evidence", action="store_true")
    route.add_argument("--performance-evidence", action="store_true")


def handle_reward_memory_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    output_format: Callable[..., str],
    print_payload: Callable[[dict[str, object], str, Callable], None],
) -> int | None:
    if args.command != "reward-memory":
        return None
    try:
        if args.reward_memory_command == "architecture":
            payload = build_reward_memory_architecture_packet()
        elif args.reward_memory_command == "corpus-registry":
            payload = build_reward_memory_corpus_registry_packet()
        elif args.reward_memory_command == "health-check":
            corpus, observation = reward_memory_health_case(args.case)
            payload = build_reward_memory_corpus_health_packet(corpus, observation)
        elif args.reward_memory_command == "candidate-review":
            adapter = issue_fix_verified_contributor_candidate_fixture()
            candidate = adapter["shared_candidate"]
            review = {
                "decision": args.decision,
                "reviewer_ref": "github:user:maintainer",
                "review_ref": f"review:fixture:{args.decision}",
                "reasoning_summary": "The compact evidence and scope were reviewed.",
            }
            if args.decision == "edit":
                review["edited_content_summary"] = (
                    "Keep focused fixes within the affected module unless "
                    "current evidence requires a broader surface."
                )
            if args.decision == "retire":
                candidate = review_reward_memory_candidate(
                    candidate,
                    review
                    | {
                        "decision": "accept",
                        "review_ref": "review:fixture:accept-before-retire",
                    },
                )
            payload = review_reward_memory_candidate(candidate, review)
            payload["adapter"] = adapter
        elif args.reward_memory_command == "experiment-status":
            payload, _ = resolve_reward_memory_experiment(
                registry_path=registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
            )
        elif args.reward_memory_command == "ingest-event":
            source = _load_json_object(args.input)
            configured_route = bool(args.goal_id or args.agent_id)
            if configured_route and not (args.goal_id and args.agent_id):
                raise ValueError("--goal-id and --agent-id must be supplied together")
            if args.execute and not configured_route:
                raise ValueError(
                    "--execute requires an enabled experiment route via "
                    "--goal-id and --agent-id"
                )
            allowed = (
                {"adapter", "event", "observed_at"}
                if configured_route
                else {
                    "adapter",
                    "event",
                    "corpus",
                    "standing_policy",
                    "provider_binding",
                    "observed_at",
                }
            )
            unexpected = sorted(set(source) - allowed)
            if unexpected:
                raise ValueError(
                    "ingest input contains unsupported fields: " + ", ".join(unexpected)
                )
            experiment_status: dict[str, object] | None = None
            experiment_config: dict[str, object] | None = None
            if configured_route:
                experiment_status, experiment_config = resolve_reward_memory_experiment(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                )
                if experiment_config is None:
                    raise ValueError(
                        "Reward Memory experiment is unavailable for this agent: "
                        + str(experiment_status.get("status") or "unavailable")
                    )
            for key in ("event",):
                if not isinstance(source.get(key), Mapping):
                    raise ValueError(f"{key} must be an object")
            supplied_adapter = source.get("adapter")
            experiment_route: dict[str, object] | None = None
            if experiment_config is not None:
                surface_id = str(source["event"].get("surface_id") or "")
                experiment_route = resolve_reward_memory_surface_config(
                    experiment_config,
                    surface_id,
                    adapter=(str(supplied_adapter) if supplied_adapter else None),
                )
            configured_adapter = (
                experiment_route.get("adapter") if experiment_route else None
            )
            adapter = configured_adapter or supplied_adapter
            corpus = (
                experiment_route["corpus"]
                if experiment_route
                else source.get("corpus")
            )
            standing_policy = (
                experiment_route["standing_policy"]
                if experiment_route
                else source.get("standing_policy")
            )
            provider_binding = (
                experiment_route["provider_binding"]
                if experiment_route
                else source.get("provider_binding")
            )
            for key, value in (
                ("corpus", corpus),
                ("standing_policy", standing_policy),
                ("provider_binding", provider_binding),
            ):
                if not isinstance(value, Mapping):
                    raise ValueError(f"{key} must be an object")
            ingest_adapter = {
                "issue_fix_maintainer_feedback": ingest_issue_fix_reward_memory_event,
                SCOPED_FEEDBACK_ADAPTER: ingest_scoped_feedback_reward_memory_event,
            }.get(str(adapter or ""))
            if ingest_adapter is None:
                raise ValueError(
                    "adapter is unsupported; semantic routing is intentionally not inferred"
                )
            payload = ingest_adapter(
                source["event"],
                corpus=corpus,
                standing_policy=standing_policy,
                provider_binding=provider_binding,
                observed_at=str(source.get("observed_at") or ""),
                execute=args.execute,
            )
            if experiment_status is not None:
                payload["experiment"] = experiment_status
                next_recall = payload.get("next_recall")
                if isinstance(next_recall, Mapping):
                    payload["next_recall"] = dict(next_recall) | {
                        "automatic_recall": bool(
                            experiment_status.get("automatic_recall")
                        )
                    }
        elif args.reward_memory_command == "evaluate":
            payload = run_reward_memory_evaluation()
        elif args.reward_memory_command == "dogfood-evaluate":
            source = _load_json_object(args.input)
            observations = source.get("observations")
            controls = source.get("operator_controls")
            if not isinstance(observations, Sequence) or isinstance(
                observations, (str, bytes)
            ):
                raise ValueError("observations must be a list")
            if not isinstance(controls, Sequence) or isinstance(controls, (str, bytes)):
                raise ValueError("operator_controls must be a list")
            if any(not isinstance(item, Mapping) for item in observations):
                raise ValueError("each observation must be an object")
            if any(not isinstance(item, Mapping) for item in controls):
                raise ValueError("each operator control must be an object")
            payload = build_reward_memory_dogfood_batch(
                [build_reward_memory_dogfood_receipt(item) for item in observations],
                [dict(item) for item in controls],
                evaluation=run_reward_memory_evaluation(),
            )
        elif args.reward_memory_command == "operator-control":
            source = _load_json_object(args.input)
            reviewed_record = source.get("reviewed_record")
            corpus = source.get("corpus")
            checkpoint = source.get("operator_checkpoint")
            if not isinstance(reviewed_record, Mapping):
                raise ValueError("reviewed_record must be an object")
            if not isinstance(corpus, Mapping):
                raise ValueError("corpus must be an object")
            if not isinstance(checkpoint, Mapping):
                raise ValueError("operator_checkpoint must be an object")
            payload = build_reward_memory_operator_control(
                reviewed_record,
                corpus,
                action=args.action,
                operator_checkpoint=checkpoint,
                control_ref=args.control_ref,
                reasoning_summary=args.reasoning_summary,
                edited_content_summary=args.edited_content_summary,
            )
        else:
            observation = (
                pr_3237_regression_observation()
                if args.case == "pr-3237"
                else {
                    "behavior_status": args.behavior_status,
                    "surface_count": args.surface_count,
                    "semantic_contract_change": args.semantic_contract_change,
                    "generic_boundary_for_specific_policy": (
                        args.generic_boundary_for_specific_policy
                    ),
                    "user_visible_behavior_change": (args.user_visible_behavior_change),
                    "hot_path_or_storage_change": (args.hot_path_or_storage_change),
                    "retrieval_or_memory_quality_claim": (
                        args.retrieval_or_memory_quality_claim
                    ),
                    "edge_case_complexity": args.edge_case_complexity,
                    "named_reproduction": args.named_reproduction,
                    "named_validation": args.named_validation,
                    "effect_evidence": args.effect_evidence,
                    "ux_evidence": args.ux_evidence,
                    "benchmark_evidence": args.benchmark_evidence,
                    "performance_evidence": args.performance_evidence,
                }
            )
            payload = build_reward_memory_route_packet(observation)
    except ValueError as exc:
        payload = {
            "ok": False,
            "schema_version": "reward_memory_error_v0",
            "status": "invalid_request",
            "error": str(exc),
        }
        print_payload(payload, output_format(args), _render)
        return 2
    print_payload(payload, output_format(args), _render)
    if args.reward_memory_command in {"evaluate", "dogfood-evaluate"} and (
        payload.get("ok") is not True
    ):
        return 2
    return 0
