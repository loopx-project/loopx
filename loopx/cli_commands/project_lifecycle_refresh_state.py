"""Registration and dispatch for the `project refresh-state` command.

Refs GH-C06. This group was carved out of `project_lifecycle.py`, which was
close to the 1000-line default module budget in
`examples/cli-command-module-size-ownership-command-modularization-smoke.py`.
`refresh-state` is one command and one rule group: its parser flags and its
dispatch branch move together, so the public invocation is unchanged.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ..capabilities.explore.activation import (
    sync_explore_graph_after_material_refresh,
)
from ..control_plane.agents.capability_gate import (
    runtime_capabilities_for_cli_projection,
)
from ..control_plane.capability_hooks import PostWritebackHookRegistration
from ..control_plane.goals.goal_vision_policy import (
    GOAL_VISION_ADVANCEMENT_POLICY_CHOICES,
)
from ..control_plane.goals.first_party_host_admission import (
    capture_first_party_host_goal_ref,
)
from ..control_plane.goals.source_session_registry_state import exact_goal_ref
from ..control_plane.quota.settlement import (
    attach_settlement_progress,
    read_heartbeat_settlement,
    settlement_result_payload,
)
from ..control_plane.work_items.delivery_batch_scale import (
    DELIVERY_BATCH_SCALE_INPUT_CHOICES,
)
from ..control_plane.work_items.delivery_outcome import DELIVERY_OUTCOME_CHOICES
from ..control_plane.work_items.progress_observation import ProgressResultClass
from ..control_plane.work_items.semantic_replan_writeback import (
    ReplanWritebackRejected,
    project_replan_writeback_rejection,
)
from ..extensions.lark.goal_channel_lifecycle import (
    goal_channel_gate_sync_failure,
    sync_human_gate_after_refresh,
    blocked_notice_sync_failure,
    sync_blocked_notice_after_refresh,
)
from ..history import load_registry
from ..paths import resolve_runtime_root
from ..state_refresh import (
    DEFAULT_REFRESH_ACTION,
    DEFAULT_REFRESH_CLASSIFICATION,
    PROGRESS_SCOPE_CHOICES,
    REPAIR_DELTA_KIND_CHOICES,
    refresh_state_run,
    render_state_refresh_markdown,
)
from .post_writeback import (
    PostWritebackProjectionBuilder,
    dispatch_committed_cli_post_writeback_hooks,
)
from .project_lifecycle_inputs import (
    inline_agent_vision_packet,
    inline_progress_observation,
    reject_non_standard_json_constant,
)
from .project_lifecycle_sinks import (
    apply_external_sink_postcondition,
    lark_explore_graph_syncer,
)
from .quota_reward_memory import (
    reward_memory_candidate_details,
    stage_reward_memory_outcome_candidate,
)


def register_refresh_state_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    context_parser = subparsers.add_parser(
        "checkpoint-context", help="Read a fresh decision basis for an existing Turn's missing checkpoint.",
    )
    add_subcommand_format(context_parser)
    for option in ("goal-id", "agent-id", "turn-instance-id"):
        context_parser.add_argument(f"--{option}", required=True)
    binding = context_parser.add_mutually_exclusive_group(required=True)
    binding.add_argument("--todo-id")
    binding.add_argument("--replan-obligation-id")
    context_parser.add_argument("--goal-instance-id", help=argparse.SUPPRESS)
    context_parser.add_argument("--project")
    context_parser.add_argument("--state-file")
    context_parser.add_argument("--dependency-todo-id", action="append", default=[],
        help="Additional upstream Todo result used in the judgment; declared dependencies are included automatically.")
    refresh_state_parser = subparsers.add_parser(
        "refresh-state",
        help="Append a read-only run from active goal state after state-only updates.",
    )
    add_subcommand_format(refresh_state_parser)
    refresh_state_parser.add_argument(
        "--goal-id",
        required=True,
        help="Goal id whose active state should be refreshed.",
    )
    refresh_state_parser.add_argument("--project", help="Project root. Defaults to the registry goal repo.")
    refresh_state_parser.add_argument(
        "--state-file",
        help="Active goal state path. Defaults to the registry goal state_file.",
    )
    refresh_state_parser.add_argument(
        "--classification",
        default=DEFAULT_REFRESH_CLASSIFICATION,
        help=f"Refresh run classification. Defaults to {DEFAULT_REFRESH_CLASSIFICATION}.",
    )
    refresh_state_parser.add_argument(
        "--recommended-action",
        help=(
            "Local-control next action. Private project refs are allowed; "
            f"inline secrets are rejected. Defaults to: {DEFAULT_REFRESH_ACTION}"
        ),
    )
    refresh_state_parser.add_argument(
        "--next-action",
        help=(
            "Explicitly update the active state's durable ## Next Action before "
            "appending the refresh run. Without this flag, --recommended-action "
            "only describes the run record."
        ),
    )
    refresh_state_parser.add_argument(
        "--delivery-batch-scale",
        choices=DELIVERY_BATCH_SCALE_INPUT_CHOICES,
        help=(
            "Explicit delivery scale for this refresh run; missing scale stays unknown. "
            "Choose the canonical scale explicitly; historical single_segment/"
            "bounded_segment values remain readable but are not valid for new writes."
        ),
    )
    refresh_state_parser.add_argument(
        "--delivery-outcome",
        choices=DELIVERY_OUTCOME_CHOICES,
        help="Optional explicit outcome-floor signal for this refresh run.",
    )
    refresh_state_parser.add_argument(
        "--delivery-boundary",
        choices=("in_flight_continuation", "semantic_closeout"),
        help=(
            "Typed semantic boundary for vision checkpointing. Defaults to "
            "semantic_closeout; in_flight_continuation is valid only for an "
            "open agent-bound Todo reporting outcome_progress."
        ),
    )
    refresh_state_parser.add_argument(
        "--delivery-workspace-path",
        help=(
            "Local git worktree that produced this accountable delivery. Use when "
            "refresh-state must run from a separate registry checkout; the local "
            "path is validated but is not persisted."
        ),
    )
    refresh_state_parser.add_argument(
        "--todo-id",
        help=(
            "Selected Todo from the original turn-scoped quota guard. Requires "
            "--turn-instance-id and an accountable delivery outcome."
        ),
    )
    refresh_state_parser.add_argument(
        "--replan-obligation-id",
        help=(
            "Autonomous replan obligation from the original turn-scoped quota "
            "guard. Requires --turn-instance-id and an accountable delivery "
            "outcome; cannot be combined with --todo-id."
        ),
    )
    refresh_state_parser.add_argument(
        "--turn-instance-id",
        help=(
            "Stable quota guard turn id for settlement writeback. Reuse the same "
            "value on retries."
        ),
    )
    refresh_state_parser.add_argument("--goal-instance-id", help=argparse.SUPPRESS)
    refresh_state_parser.add_argument("--completion-todo-id", help=argparse.SUPPRESS)
    refresh_state_parser.add_argument("--completion-turn-key", help=argparse.SUPPRESS)
    refresh_state_parser.add_argument(
        "--autonomous-replan-recorded",
        action="store_true",
        help=(
            "Mark this refresh as the explicit autonomous replan ACK. "
            "Use only after the agent has performed and written back the bounded replan slice."
        ),
    )
    refresh_state_parser.add_argument(
        "--progress-result-class",
        choices=[item.value for item in ProgressResultClass],
        help=(
            "Typed result for this bounded work slice. Semantics come only from "
            "this enum and stable identifiers, never from classification prose."
        ),
    )
    refresh_state_parser.add_argument("--progress-surface-id")
    refresh_state_parser.add_argument("--progress-hypothesis-id")
    refresh_state_parser.add_argument("--progress-probe-kind")
    refresh_state_parser.add_argument("--progress-blocker-id")
    refresh_state_parser.add_argument("--progress-coverage-scope-id")
    refresh_state_parser.add_argument(
        "--progress-evidence-id",
        dest="progress_evidence_ids",
        action="append",
    )
    refresh_state_parser.add_argument(
        "--progress-coverage-complete",
        action="store_true",
        default=None,
    )
    refresh_state_parser.add_argument(
        "--reward-memory-reflection-json",
        help=(
            "Optional compact turn_reward_memory_reflection_v1 JSON for a "
            "Todo-bound accountable Codex App refresh. LoopX stages it privately, "
            "requires the Todo's caller-declared validator to attest the exact "
            "reflection and evidence, and performs no provider write until the "
            "matching quota spend is read back."
        ),
    )
    refresh_state_parser.add_argument(
        "--repair-delta-kind",
        dest="repair_delta_kinds",
        choices=REPAIR_DELTA_KIND_CHOICES,
        action="append",
        help=(
            "Machine-visible frontier changed by this repair/replan ACK. Repeat for "
            "multiple deltas. Without a delta, --autonomous-replan-recorded is stored "
            "as replan_noop/repair_noop and does not clear the obligation."
        ),
    )
    refresh_state_parser.add_argument(
        "--agent-vision-json",
        help=(
            "Path to a complete generated goal_vision_replan_contract_v0 update. "
            "The CLI enforces budgets; any autonomous replan that changes durable "
            "mainline fields requires goal_path_delta_v0."
        ),
    )
    refresh_state_parser.add_argument(
        "--vision-state",
        help=(
            "Optional lower snake_case lifecycle state for an inline "
            "goal_vision_replan_contract_v0 patch. Closure aliases such as "
            "satisfied and vision_satisfied normalize to vision_closed; "
            "custom states remain open until explicitly closed."
        ),
    )
    refresh_state_parser.add_argument(
        "--vision-summary",
        help=(
            "Inline bounded vision_summary for a field-level patch merged into the "
            "current agent's latest active vision."
        ),
    )
    refresh_state_parser.add_argument(
        "--vision-role-scope",
        help="Inline bounded role_scope for the current agent's vision patch.",
    )
    refresh_state_parser.add_argument(
        "--vision-acceptance",
        help="Inline bounded acceptance_summary for the current agent's vision patch.",
    )
    refresh_state_parser.add_argument(
        "--vision-advancement-policy",
        choices=GOAL_VISION_ADVANCEMENT_POLICY_CHOICES,
        help=(
            "Whether open acceptance needs advancement only as needed or must "
            "keep a runnable advancement frontier until the vision closes."
        ),
    )
    refresh_state_parser.add_argument(
        "--vision-replan-trigger",
        help="Inline bounded replan_trigger_summary that quota can project as an acceptance gap.",
    )
    refresh_state_parser.add_argument(
        "--vision-dreaming-policy",
        help="Inline bounded dreaming_policy for the current agent's vision patch.",
    )
    refresh_state_parser.add_argument(
        "--vision-last-patch",
        help="Inline bounded last_patch_summary for the current agent's vision patch.",
    )
    refresh_state_parser.add_argument(
        "--vision-todo-delta",
        action="append",
        help="Compact todo delta for an inline vision patch. Repeat for multiple deltas.",
    )
    refresh_state_parser.add_argument(
        "--vision-unchanged-reason",
        help=(
            "Compact reason why a required vision checkpoint is intentionally unchanged."
        ),
    )
    refresh_state_parser.add_argument(
        "--checkpoint-read-context", metavar="READ_CONTEXT_ID",
        help="Echo the fresh checkpoint-context receipt when supplementing a missing checkpoint on the original Turn.",
    )
    refresh_state_parser.add_argument(
        "--agent-id",
        help=(
            "Registered agent id for agent-lane state refreshes. When set, the "
            "refresh is visible in run history but does not replace goal-level status."
        ),
    )
    refresh_state_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "Preserve one observed public-safe runtime capability from the scoped "
            "quota decision. Repeatable; this context does not grant authority or "
            "change refresh-state write scope."
        ),
    )
    refresh_state_parser.add_argument(
        "--agent-lane",
        help="Public-safe lane label for --agent-id scoped refreshes, such as productization_frontstage.",
    )
    refresh_state_parser.add_argument(
        "--progress-scope",
        choices=PROGRESS_SCOPE_CHOICES,
        help=(
            "Refresh scope. In multi-agent goals, use agent_lane for per-agent runnable "
            "status, or goal with any registered peer for durable goal-level status/Next Action."
        ),
    )
    refresh_state_parser.add_argument(
        "--usage-codex-session",
        help=(
            "Path to the local Codex session rollout JSONL that produced this "
            "run. Only aggregate token_count totals, the model id, and event "
            "timestamps are read; prompts, completions, and tool output never "
            "enter run history. The session must be bound explicitly; when the "
            "rollout is unknown, omit the flag and usage stays unknown. Cannot "
            "be combined with --usage-json."
        ),
    )
    refresh_state_parser.add_argument(
        "--usage-json",
        help=(
            "Inline JSON object with a provider-neutral per-run usage "
            "measurement: input_tokens, output_tokens, provider, model, "
            "source_snapshot_id, plus optional cache_tokens/cost_usd/"
            "duration_ms. Must be strict JSON; malformed, negative, or "
            "non-finite usage fails the refresh closed. Cannot be combined "
            "with --usage-codex-session."
        ),
    )
    refresh_state_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the refresh payload without appending.",
    )
    refresh_state_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not refresh the shared global registry after writing the state run.",
    )
    delivery_flags = refresh_state_parser.add_mutually_exclusive_group()
    delivery_flags.add_argument(
        "--suppress-external-sinks",
        action="store_true",
        help=(
            "Keep enabled local projections active but suppress configured external "
            "sink writes for this refresh. Turn-bound retries require an explicit resume acknowledgement."
        ),
    )

    delivery_flags.add_argument(
        "--resume-external-sinks", metavar="RESUME_KEY",
        help="Acknowledge the current pause returned by a Turn-bound refresh recovery. Does not grant provider permissions.",
    )


def handle_refresh_state_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: Callable[
        [dict[str, object], str, Callable[[dict[str, object]], str]], None
    ],
    output_format: Callable[[argparse.Namespace], str],
    append_cli_rollout_event: Callable[..., dict[str, object]],
    post_writeback_hooks: Sequence[PostWritebackHookRegistration] | None = None,
    post_writeback_projection_builder: PostWritebackProjectionBuilder | None = None,
) -> int | None:
    if args.command == "checkpoint-context":
        from ..control_plane.goals.checkpoint_context_io import read_checkpoint_context, render_checkpoint_context
        try:
            goal_instance_id = str(
                getattr(args, "goal_instance_id", None) or ""
            ).strip()
            goal_ref = (
                exact_goal_ref(args.goal_id, goal_instance_id)
                if goal_instance_id
                else capture_first_party_host_goal_ref(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                )
            )
            payload = read_checkpoint_context(
                registry_path=registry_path, runtime_root_override=args.runtime_root,
                goal_id=args.goal_id, agent_id=args.agent_id, todo_id=args.todo_id,
                turn_instance_id=args.turn_instance_id, replan_obligation_id=args.replan_obligation_id,
                project=Path(args.project).expanduser() if args.project else None,
                state_file=Path(args.state_file).expanduser() if args.state_file else None,
                dependency_todo_ids=args.dependency_todo_id,
                goal_ref=goal_ref,
            )
        except Exception as exc:
            payload = {"ok": False, "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})}
                   if isinstance(getattr(exc, "code", None), str) else {})}
        print_payload(payload, output_format(args), render_checkpoint_context)
        return 0 if payload.get("ok") else 1
    if args.command != "refresh-state":
        return None
    fmt = output_format(args)
    agent_vision_packet: dict[str, object] | None = None
    progress_observation: dict[str, object] | None = None
    merge_agent_vision_patch = False
    goal_ref: dict[str, str] | None = None
    try:
        inline_vision_packet = inline_agent_vision_packet(args)
        if args.agent_vision_json and inline_vision_packet:
            raise ValueError(
                "--agent-vision-json cannot be combined with inline --vision-* fields"
            )
        if args.agent_vision_json:
            agent_vision_packet = json.loads(
                Path(args.agent_vision_json).expanduser().read_text(encoding="utf-8")
            )
        elif inline_vision_packet:
            agent_vision_packet = inline_vision_packet
            merge_agent_vision_patch = True
        progress_observation = inline_progress_observation(args)
        reward_memory_reflection_json = str(
            getattr(args, "reward_memory_reflection_json", None) or ""
        ).strip()
        if reward_memory_reflection_json and not (
            args.agent_id
            and getattr(args, "todo_id", None)
            and getattr(args, "turn_instance_id", None)
        ):
            raise ValueError(
                "--reward-memory-reflection-json requires --agent-id, "
                "--todo-id, and --turn-instance-id"
            )
        usage_measurement: dict[str, object] | None = None
        if getattr(args, "usage_json", None):
            loaded_usage = json.loads(
                args.usage_json,
                parse_constant=reject_non_standard_json_constant,
            )
            if not isinstance(loaded_usage, dict):
                raise ValueError("--usage-json must be a JSON object")
            usage_measurement = loaded_usage
        goal_instance_id = str(
            getattr(args, "goal_instance_id", None) or ""
        ).strip()
        if goal_instance_id and not getattr(args, "turn_instance_id", None):
            raise ValueError("--goal-instance-id requires --turn-instance-id")
        if getattr(args, "turn_instance_id", None):
            goal_ref = (
                exact_goal_ref(args.goal_id, goal_instance_id)
                if goal_instance_id
                else capture_first_party_host_goal_ref(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                )
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": args.runtime_root,
            "goal_id": args.goal_id,
            "classification": args.classification,
            "appended": False,
            "dry_run": bool(args.dry_run),
            "error": str(exc),
            **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
        }
        print_payload(payload, fmt, render_state_refresh_markdown)
        return 1
    try:
        if getattr(args, "resume_external_sinks", None) and not getattr(args, "turn_instance_id", None):
            raise ValueError("--resume-external-sinks requires the original --turn-instance-id")
        payload = refresh_state_run(
            external_delivery={"suppress": bool(args.suppress_external_sinks),
                               "resume_key": getattr(args, "resume_external_sinks", None)},
            registry_path=registry_path,
            runtime_root_override=args.runtime_root,
            goal_id=args.goal_id,
            project=Path(args.project).expanduser() if args.project else None,
            state_file=Path(args.state_file).expanduser() if args.state_file else None,
            classification=args.classification,
            recommended_action=args.recommended_action,
            next_action=args.next_action,
            delivery_batch_scale=args.delivery_batch_scale,
            delivery_outcome=args.delivery_outcome,
            delivery_boundary=getattr(args, "delivery_boundary", None),
            delivery_workspace_path=(
                Path(args.delivery_workspace_path).expanduser()
                if args.delivery_workspace_path
                else None
            ),
            todo_id=getattr(args, "todo_id", None),
            turn_instance_id=getattr(args, "turn_instance_id", None),
            replan_obligation_id=getattr(
                args, "replan_obligation_id", None
            ),
            completion_todo_id=getattr(args, "completion_todo_id", None),
            completion_turn_key=getattr(args, "completion_turn_key", None),
            agent_id=args.agent_id,
            agent_lane=args.agent_lane,
            progress_scope=args.progress_scope,
            autonomous_replan_recorded=bool(args.autonomous_replan_recorded),
            repair_delta_kinds=args.repair_delta_kinds,
            agent_vision_packet=agent_vision_packet,
            merge_agent_vision_patch=merge_agent_vision_patch,
            vision_unchanged_reason=args.vision_unchanged_reason,
            checkpoint_read_context_id=getattr(args, "checkpoint_read_context", None),
            progress_observation=progress_observation,
            usage_measurement=usage_measurement,
            usage_codex_session=(
                Path(args.usage_codex_session).expanduser()
                if getattr(args, "usage_codex_session", None)
                else None
            ),
            dry_run=bool(args.dry_run),
            sync_global=not bool(args.no_global_sync),
            goal_ref=goal_ref,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": args.runtime_root,
            "goal_id": args.goal_id,
            "classification": args.classification,
            "appended": False,
            "dry_run": bool(args.dry_run),
            "error": str(exc),
            **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
        }
        if isinstance(exc, ReplanWritebackRejected):
            transition = project_replan_writeback_rejection(
                exc,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                runtime_root=args.runtime_root,
            )
            payload["replan_transition"] = transition
            payload["error"] += " Required transition: " + "; ".join(
                transition["next_cli_actions"]
            )
    projected_capabilities = runtime_capabilities_for_cli_projection(
        args.available_capabilities
    )
    if projected_capabilities:
        payload["available_capabilities"] = projected_capabilities
    payload.setdefault(
        "external_sink_delivery_authorized",
        not bool(args.suppress_external_sinks or getattr(args, "turn_instance_id", None)),
    )
    material_refresh_ready = bool(
        payload.get("ok")
        and (
            payload.get("appended")
            or payload.get("idempotent_replay")
        )
        and not payload.get("dry_run")
    )
    if material_refresh_ready and reward_memory_reflection_json:
        validation_workspace_text = str(
            getattr(args, "delivery_workspace_path", None)
            or getattr(args, "project", None)
            or payload.get("project")
            or ""
        ).strip()
        stage_reward_memory_outcome_candidate(
            payload,
            registry_path=registry_path,
            runtime_root=resolve_runtime_root(
                load_registry(registry_path), args.runtime_root
            ),
            goal_id=args.goal_id,
            agent_id=str(args.agent_id),
            todo_id=str(args.todo_id),
            turn_instance_id=str(args.turn_instance_id),
            reflection_json=reward_memory_reflection_json,
            validation_workspace=Path(validation_workspace_text),
        )
    settlement_receipt_repair = bool(
        payload.get("ok")
        and payload.get("receipt_repair_required")
        and getattr(args, "turn_instance_id", None)
        and (
            getattr(args, "todo_id", None)
            or getattr(args, "replan_obligation_id", None)
        )
    )
    if material_refresh_ready or settlement_receipt_repair:
        append_cli_rollout_event(
            payload,
            registry_path=registry_path,
            runtime_root_arg=args.runtime_root,
            event_kind="refresh_state",
            goal_ref=goal_ref,
            agent_id=args.agent_id,
            todo_id=getattr(args, "todo_id", None),
            run_id=getattr(args, "turn_instance_id", None),
            status=(
                "receipt_repaired"
                if settlement_receipt_repair
                else "appended"
            ),
            summary=(
                "refresh-state appended compact control-plane state with "
                f"classification={payload.get('classification')}"
            ),
            details={
                "command": "refresh-state",
                "progress_scope": payload.get("progress_scope") or "",
                "agent_lane": payload.get("agent_lane") or "",
                "autonomous_replan_recorded": bool(
                    payload.get("autonomous_replan_recorded")
                ),
                "global_sync_wrote": bool(
                    isinstance(payload.get("global_sync"), dict)
                    and payload["global_sync"].get("wrote")
                ),
                "settlement_effect_id": (
                    payload.get("settlement_identity", {}).get("effect_id")
                    if isinstance(payload.get("settlement_identity"), dict)
                    else None
                ),
                "replan_obligation_id": getattr(
                    args, "replan_obligation_id", None
                )
                or "",
                **reward_memory_candidate_details(payload),
            },
            idempotency_fields=(
                [
                    "goal_id",
                    "event_kind",
                    "agent_id",
                    *(
                        ["todo_id"]
                        if getattr(args, "todo_id", None)
                        else []
                    ),
                    "run_id",
                    *(["goal_ref"] if goal_ref is not None else []),
                ]
                if getattr(args, "turn_instance_id", None)
                else None
            ),
        )
        if getattr(args, "turn_instance_id", None) and (
            getattr(args, "todo_id", None)
            or getattr(args, "replan_obligation_id", None)
        ):
            runtime_root = resolve_runtime_root(
                load_registry(registry_path),
                args.runtime_root,
            )
            settlement_readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                todo_id=getattr(args, "todo_id", None),
                turn_instance_id=getattr(args, "turn_instance_id", None),
                replan_obligation_id=getattr(
                    args, "replan_obligation_id", None
                ),
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
            if settlement_readback is None:
                raise RuntimeError(
                    "exact settlement readback unexpectedly returned not-found"
                )
            settlement_result = settlement_readback.delivery
            attach_settlement_progress(
                payload, settlement_readback, registry_path=registry_path, runtime_root=runtime_root,
                goal_ref=goal_ref,
            )
            payload["settlement_result"] = settlement_result_payload(
                settlement_result
            )
            if settlement_result.failure is not None:
                payload["ok"] = False
                payload["receipt_repair_required"] = True
                payload["error"] = settlement_result.failure.reason
            elif settlement_receipt_repair:
                payload["receipt_repair_required"] = False
                payload["receipt_repaired"] = True
        if material_refresh_ready and post_writeback_hooks:
            settlement_identity = (
                payload.get("settlement_identity")
                if isinstance(payload.get("settlement_identity"), Mapping)
                else {}
            )
            payload["post_writeback_hooks"] = (
                dispatch_committed_cli_post_writeback_hooks(
                    payload=payload,
                    registry_path=registry_path,
                    runtime_root_arg=args.runtime_root,
                    goal_id=args.goal_id,
                    event_kind="refresh_state",
                    identity={
                        "agent_id": str(args.agent_id or ""),
                        "todo_id": str(getattr(args, "todo_id", None) or ""),
                        "turn_instance_id": str(
                            getattr(args, "turn_instance_id", None) or ""
                        ),
                        "effect_id": str(
                            settlement_identity.get("effect_id") or ""
                        ),
                    },
                    state_version=str(payload.get("generated_at") or ""),
                    committed_at=str(payload.get("generated_at") or ""),
                    hooks=post_writeback_hooks,
                    projection_builder=post_writeback_projection_builder,
                )
            )
        if not material_refresh_ready:
            print_payload(payload, fmt, render_state_refresh_markdown)
            return 0 if payload.get("ok") else 1
        graph_sync = sync_explore_graph_after_material_refresh(
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            project=Path(args.project).expanduser() if args.project else None,
            state_file=Path(args.state_file).expanduser() if args.state_file else None,
            external_sink_delivery_authorized=payload["external_sink_delivery_authorized"] is True,
            syncer=lark_explore_graph_syncer(
                args.runtime_root,
                registry_path=registry_path,
            ),
        )
        payload["explore_graph_sync"] = graph_sync
        apply_external_sink_postcondition(
            payload,
            sink_result=graph_sync,
            warning=(
                "enabled Explore Graph delivery postcondition is unsatisfied; "
                "the unchanged sink digest keeps it retryable"
            ),
            error=(
                "enabled Explore Graph sync/readback failed after the material "
                "refresh; retry it before delivery"
            ),
        )
        try:
            gate_sync = sync_human_gate_after_refresh(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                external_sink_delivery_authorized=payload["external_sink_delivery_authorized"] is True,
            )
        except Exception as error:
            gate_sync = goal_channel_gate_sync_failure(
                registry_path=registry_path,
                goal_id=args.goal_id,
                exception=error,
            )
        try:
            blocked_sync = sync_blocked_notice_after_refresh(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                external_sink_delivery_authorized=payload[
                    "external_sink_delivery_authorized"
                ]
                is True,
            )
        except Exception as error:
            blocked_sync = blocked_notice_sync_failure(
                registry_path=registry_path,
                goal_id=args.goal_id,
                exception=error,
            )
        payload["goal_channel_blocked_notice_sync"] = blocked_sync
        payload["goal_channel_gate_sync"] = gate_sync
        apply_external_sink_postcondition(
            payload,
            sink_result=gate_sync,
            warning=(
                "enabled Goal Channel human-gate delivery postcondition is "
                "unsatisfied; the notification remains retryable"
            ),
            error=(
                "enabled Goal Channel human-gate notification/readback failed "
                "after the refresh; retry it before delivery"
                + (
                    "; " + gate_sync["failure_summary"]
                    if gate_sync.get("failure_summary") else ""
                )
            ),
        )
    print_payload(payload, fmt, render_state_refresh_markdown)
    return 0 if payload.get("ok") else 1
