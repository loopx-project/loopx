from __future__ import annotations
from .effective_action import EffectiveAction

import shlex
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...quota import build_quota_should_run
from ...agent_registry import load_goal_from_registry
from ..agent_context import project_agent_context, project_goal_agent_context
from ..runtime.time import now_utc_iso
from ..capability_hooks import (
    InteractionProjectionHookRegistration,
    dispatch_interaction_projection_hooks,
)
from .effect_program import ReceiptBoundReplayPhase
from .blocked_retry import overlay_active_turn_retries
from .settlement import (
    read_heartbeat_settlement,
)
from ..work_items.interaction_contract import build_interaction_contract
from ..work_items.action_portfolio import reconcile_retained_action_selection
from ..work_items.autonomous_replan_obligation import (
    replan_obligation_id_from_packet,
)
from ..todos.contract import normalize_todo_id
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    resolve_scheduler_execution_context,
)
from .unsettled_host_turn import (
    apply_unsettled_host_turn_recovery_if_required,
)


HostObservationResolver = Callable[..., Mapping[str, Any]]
BoundedResearchFrontierProjector = Callable[..., Mapping[str, Any] | None]


def _apply_retained_action_selection_reentry(
    payload: dict[str, Any],
    *,
    retained_todo_id: str | None,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    runtime_root: Path,
) -> None:
    """Fence a no-argument reentry with its last explicit Todo choice."""

    normalized_retained = normalize_todo_id(retained_todo_id)
    if normalized_retained is None:
        return
    selected = (
        payload.get("selected_todo")
        if isinstance(payload.get("selected_todo"), Mapping)
        else {}
    )
    projected_todo_id = normalize_todo_id(selected.get("todo_id"))
    verdict = reconcile_retained_action_selection(
        retained_todo_id=normalized_retained,
        projected_todo_id=projected_todo_id,
        effective_action=str(payload.get("effective_action") or ""),
        replan_obligation_id=replan_obligation_id_from_packet(
            payload.get("replan_action_packet")
        ),
    )
    payload["retained_action_selection"] = verdict
    disposition = verdict.get("disposition")
    if disposition == "preserve_retained_todo":
        return
    if disposition == "bind_autonomous_replan":
        payload.pop("selected_todo", None)
        payload.pop("todo_id", None)
        payload.pop("agent_lane_next_action", None)
        payload["deferred_action_selection"] = {
            "todo_id": normalized_retained,
            "reason": "autonomous_replan_preemption",
            "resume": "fresh_turn_after_replan_closeout",
        }
    elif disposition == "require_explicit_selection":
        projection = verdict.get("projection")
        if not isinstance(projection, Mapping):
            raise RuntimeError(
                "TypeScript retained action-selection projection is missing"
            )
        decision_patch = projection.get("decision_patch")
        obligation_patch = projection.get("execution_obligation_patch")
        clear_fields = projection.get("clear_fields")
        if (
            not isinstance(decision_patch, Mapping)
            or not isinstance(obligation_patch, Mapping)
            or not isinstance(clear_fields, list)
            or not all(isinstance(field, str) for field in clear_fields)
        ):
            raise RuntimeError(
                "TypeScript retained action-selection projection is malformed"
            )
        payload.update(decision_patch)
        obligation = (
            dict(payload.get("execution_obligation") or {})
            if isinstance(payload.get("execution_obligation"), Mapping)
            else {}
        )
        obligation.update(obligation_patch)
        payload["execution_obligation"] = obligation
        for field in clear_fields:
            payload.pop(field, None)
    else:
        raise RuntimeError(
            "TypeScript retained action-selection disposition is unsupported"
        )
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        runtime_root=str(runtime_root),
    )


def _fresh_read_covers_all_pending_material(
    dispatch: Mapping[str, Any] | None,
    projector: Callable[..., dict[str, Any]] | None,
) -> Callable[..., dict[str, Any]] | None:
    if projector is None:
        return None
    fresh_count = _fresh_operator_inbox_observation_count(dispatch)

    def project(**kwargs: Any) -> dict[str, Any]:
        urgency = dict(projector(**kwargs))
        pending_count = max(0, int(urgency.get("pending_count") or 0))
        if pending_count <= fresh_count:
            urgency["material_review_due"] = False
        return urgency

    return project


def _turn_start_required_reads(
    dispatch: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Read only the generic, kernel-validated pre-work projection."""

    if not isinstance(dispatch, Mapping):
        return []
    reads = dispatch.get("required_reads")
    if not isinstance(reads, list):
        return []
    projected: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    for read in reads:
        if not isinstance(read, Mapping):
            continue
        command = read.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        normalized = command.strip()
        if normalized in seen_commands:
            continue
        projected.append(
            {
                key: read[key]
                for key in ("kind", "command", "reason", "source", "ordering", "prompt_budget_bytes")
                if key in read
            }
        )
        seen_commands.add(normalized)
    return projected


def _project_turn_start_required_reads(
    payload: dict[str, Any],
    dispatch: Mapping[str, Any] | None,
    *,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    runtime_root: Path,
) -> bool:
    """Order evidence before work and report whether the decision changed."""

    projected = _turn_start_required_reads(dispatch)
    if not projected:
        return False
    existing = payload.get("required_reads")
    required_reads = (
        [dict(item) for item in existing if isinstance(item, Mapping)]
        if isinstance(existing, list)
        else []
    )
    seen_commands = {
        str(item.get("command") or "").strip()
        for item in required_reads
        if str(item.get("command") or "").strip()
    }
    for required_read in projected:
        command = str(required_read.get("command") or "").strip()
        if command in seen_commands:
            continue
        required_reads.append(required_read)
        seen_commands.add(command)
    payload["required_reads"] = required_reads
    if _fresh_operator_inbox_read_required(dispatch):
        recommendation = (
            dict(payload.get("heartbeat_recommendation") or {})
            if isinstance(payload.get("heartbeat_recommendation"), Mapping)
            else {}
        )
        recommendation.update(
            {
                "notify": "NOTIFY",
            }
        )
        payload["heartbeat_recommendation"] = recommendation
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        runtime_root=str(runtime_root),
    )
    return True


def _fresh_operator_inbox_observation_count(
    dispatch: Mapping[str, Any] | None,
) -> int:
    if not isinstance(dispatch, Mapping):
        return 0
    required_reads = dispatch.get("required_reads")
    results = dispatch.get("results")
    if not isinstance(required_reads, list) or not isinstance(results, list):
        return 0
    operator_inbox_hook_ids = {
        str(read.get("hook_id") or "")
        for read in required_reads
        if isinstance(read, Mapping)
        and read.get("kind") == "operator_inbox"
        and str(read.get("hook_id") or "")
    }
    return sum(
        max(0, int(result.get("observation_count") or 0))
        for result in results
        if isinstance(result, Mapping)
        and result.get("agent_read_required") is True
        and result.get("hook_id") in operator_inbox_hook_ids
    )


def _fresh_operator_inbox_read_required(
    dispatch: Mapping[str, Any] | None,
) -> bool:
    return _fresh_operator_inbox_observation_count(dispatch) > 0


def _apply_pending_capability_intent_precedence(
    payload: dict[str, Any],
    projection: Mapping[str, Any] | None,
    *,
    available_capabilities: Any = None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
    turn_instance_id: str | None = None,
) -> bool:
    """Apply intent precedence and report whether the decision changed."""

    if not isinstance(projection, Mapping) or projection.get("state") != "pending":
        return False
    summary = str(projection.get("action_summary") or "").strip()
    command = str(projection.get("command") or "").strip()
    if not summary or not command:
        return False
    payload.update(
        {
            "decision": "run",
            "should_run": True,
            "state": "eligible",
            "effective_action": EffectiveAction.GOVERNED_CAPABILITY_INTENT.value,
            "actionable_by_codex": True,
            "normal_delivery_allowed": False,
            "recovery_delivery_allowed": False,
            "capability_intent_execution_allowed": True,
            "reason": "a validated pending capability intent requires governed local execution",
            "recommended_action": summary,
            "pending_capability_intent": dict(projection),
        }
    )
    payload["heartbeat_recommendation"] = {
        "source": "pending_capability_intent",
        "recommended_mode": "governed_capability_intent",
        "notify": "DONT_NOTIFY",
        "spend_policy": "intent consumption owns its durable receipt; no external delivery",
        "reason": summary,
        "agent_must_attempt": True,
    }
    payload["execution_obligation"] = {
        "must_attempt_work": True,
        "kind": "pending_capability_intent",
        "contract": "governed_capability_intent",
        "contract_obligation": "execute_exact_projected_command",
        "notify_is_execution_gate": False,
        "reason": summary,
    }
    payload["work_lane_contract"] = {
        "schema_version": "work_lane_contract_v1",
        "lane": "capability_intent",
        "next_lane": "user_gate",
        "obligation": "execute_exact_projected_command",
        "must_attempt_work": True,
        "reason_codes": ["pending_capability_intent"],
        "monitor_policy": "not_applicable",
        "action": summary,
    }
    payload["automation_liveness"] = {
        "schema_version": "automation_liveness_v0",
        "keep_active": True,
        "pause_allowed": False,
        "automation_action": "execute_bounded_work",
        "reason": summary,
        "spend_policy": "no external delivery; exact intent receipt is authoritative",
    }
    payload["interaction_contract"] = build_interaction_contract(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
    )
    return True


def bind_scheduler_followup_cli_routes(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    turn_instance_id: str | None = None,
    source: str = "quota_cli_invocation",
) -> None:
    """Bind scheduler follow-ups to the registry/runtime/Turn that built the hint."""

    scheduler_hint = payload.get("scheduler_hint")
    if not isinstance(scheduler_hint, dict):
        return
    app_packets = [
        packet
        for packet_key in ("app_automation", "codex_app")
        if isinstance((packet := scheduler_hint.get(packet_key)), dict)
    ]
    for app_packet in app_packets:
        for hint_name in ("ack_hint", "failure_hint", "fallback_hint"):
            followup_hint = app_packet.get(hint_name)
            if not isinstance(followup_hint, dict):
                continue
            cli_args = followup_hint.get("cli_args")
            if not isinstance(cli_args, list) or not cli_args:
                continue
            if hint_name == "fallback_hint":
                if cli_args[0] != "loopx-apply-rrule" or "--registry" in cli_args:
                    continue
                followup_hint["cli_args"] = [
                    cli_args[0],
                    "--registry",
                    str(registry_path.expanduser().resolve()),
                    *cli_args[1:],
                ]
                followup_hint["route_binding"] = {
                    "schema_version": "codex_app_scheduler_fallback_route_v0",
                    "source": source,
                    "registry_bound": True,
                    "runtime_root_bound": False,
                }
                continue
            bound_cli_args = list(cli_args)
            if bound_cli_args[0] != "--registry":
                bound_cli_args = [
                    "--registry",
                    str(registry_path.expanduser().resolve()),
                    "--runtime-root",
                    str(runtime_root.expanduser().resolve()),
                    *bound_cli_args,
                ]
            safe_turn_instance_id = str(turn_instance_id or "").strip()
            if safe_turn_instance_id and "--turn-instance-id" not in bound_cli_args:
                execute_index = (
                    bound_cli_args.index("--execute")
                    if "--execute" in bound_cli_args
                    else len(bound_cli_args)
                )
                bound_cli_args[execute_index:execute_index] = [
                    "--turn-instance-id",
                    safe_turn_instance_id,
                ]
                args_value = followup_hint.get("args")
                if isinstance(args_value, dict):
                    args_value["turn_instance_id"] = safe_turn_instance_id
            followup_hint["cli_args"] = bound_cli_args
            followup_hint["route_binding"] = {
                "schema_version": (
                    "scheduler_ack_cli_route_v0"
                    if hint_name == "ack_hint"
                    else "scheduler_failure_cli_route_v0"
                ),
                "source": source,
                "registry_bound": True,
                "runtime_root_bound": True,
                "turn_instance_bound": bool(safe_turn_instance_id),
            }


def bind_action_selection_cli_routes(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
) -> None:
    """Bind two-phase action-selection commands to this live CLI source."""

    interaction_value = payload.get("interaction_contract")
    interaction: Mapping[str, Any] = (
        interaction_value if isinstance(interaction_value, Mapping) else {}
    )
    cli_channel_value = interaction.get("cli_channel")
    if not isinstance(cli_channel_value, dict):
        return
    cli_channel: dict[str, Any] = cli_channel_value
    if cli_channel.get("selection_required") is not True:
        return
    selection_command = cli_channel.get("selection_command")
    if not isinstance(selection_command, dict):
        return
    route_prefix = selection_command.get("route_prefix")
    if not isinstance(route_prefix, str):
        return
    try:
        tokens = shlex.split(route_prefix)
    except ValueError:
        return
    if len(tokens) >= 3 and tokens[0] == "loopx":
        try:
            format_index = tokens.index("--format")
        except ValueError:
            return
        if tokens[format_index : format_index + 2] != ["--format", "json"]:
            return
        if "--registry" not in tokens:
            tokens[1:1] = ["--registry", str(registry_path.expanduser().resolve())]
        if "--runtime-root" not in tokens:
            format_index = tokens.index("--format")
            tokens[format_index:format_index] = [
                "--runtime-root",
                str(runtime_root.expanduser().resolve()),
            ]
        selection_command["route_prefix"] = shlex.join(tokens)


def build_live_quota_should_run_decision(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    available_capabilities: list[str] | None,
    include_scheduler_detail: bool,
    include_agent_todo_detail: bool = False,
    codex_app_current_rrule: str | None,
    registry_path: Path,
    runtime_root: Path,
    host_observation_resolver: HostObservationResolver | None = None,
    route_source: str = "quota_cli_invocation",
    scheduler_execution_context: Mapping[str, Any]
    | SchedulerExecutionContextResolution
    | None = None,
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]] | None = None,
    bounded_research_frontier_projector: BoundedResearchFrontierProjector | None = None,
    receipt_bound_todo_id: str | None = None,
    requested_action_todo_id: str | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    retained_action_selection_todo_id: str | None = None,
    turn_instance_id: str | None = None,
    interaction_projection_hooks: Sequence[InteractionProjectionHookRegistration]
    | None = None,
    turn_start_hook_dispatch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one live CLI decision while keeping host observation injectable."""
    resolved_context = resolve_scheduler_execution_context(scheduler_execution_context)
    app_automation_applicable = (
        resolved_context.ok
        and resolved_context.context is not None
        and resolved_context.context.app_automation_applicable
    )
    codex_app_host = bool(
        app_automation_applicable
        and resolved_context.context is not None
        and resolved_context.context.host_surface.value == "codex_app"
    )
    observed_rrule = str(codex_app_current_rrule or "").strip()
    observed_automation_id = ""
    if (
        codex_app_host
        and not observed_rrule
        and host_observation_resolver is not None
    ):
        observation = host_observation_resolver(goal_id=goal_id, agent_id=agent_id)
        if observation.get("available") is True:
            observed_rrule = str(observation.get("rrule") or "")
            observed_automation_id = str(observation.get("automation_id") or "").strip()
    decision_status_payload = {
        **status_payload,
        "runtime_root": str(runtime_root),
    }
    if bounded_research_frontier_projector is not None:
        frontier = bounded_research_frontier_projector(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            status_payload=status_payload,
        )
        if isinstance(frontier, Mapping):
            decision_status_payload = {
                **decision_status_payload,
                "bounded_research_frontier": dict(frontier),
            }
    settlement_readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=receipt_bound_todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=receipt_bound_replan_obligation_id,
    )
    receipt_bound_monitor_phase = (
        settlement_readback.monitor_phase if settlement_readback else None
    )
    receipt_bound_replay_phase = (
        settlement_readback.replay_phase if settlement_readback else None
    )
    fresh_operator_inbox_read = _fresh_operator_inbox_read_required(
        turn_start_hook_dispatch
    )
    if (
        requested_action_todo_id or retained_action_selection_todo_id
    ) and not receipt_bound_todo_id:
        # Candidate discovery, admission, and retained-selection reentry use the
        # same provider-first reader.  A selection can be deferred by a hard
        # frontier that is visible only in the complete Todo snapshot; reentry
        # must not fall back to the earlier compact status projection and lose
        # the obligation that caused the deferral.
        # Keep the complete snapshot internal; presentation is bounded later.
        from ...todos import list_goal_todos

        source = list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(runtime_root), goal_id=goal_id,
        )
        todo_fields = {key: source[key] for key in ("user_todos", "agent_todos")}
        queue = decision_status_payload.get("attention_queue") or {}
        decision_status_payload["attention_queue"] = {
            **queue,
            "items": [
                {**item, **todo_fields} if item.get("goal_id") == goal_id else item
                for item in queue.get("items") or []
            ],
        }
    decision_status_payload = overlay_active_turn_retries(
        decision_status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
        observed_at=now_utc_iso(),
    )
    payload = build_quota_should_run(
        decision_status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
        available_capabilities=available_capabilities,
        include_scheduler_detail=include_scheduler_detail,
        include_agent_todo_detail=include_agent_todo_detail,
        codex_app_current_rrule=observed_rrule,
        codex_app_automation_id=observed_automation_id or None,
        scheduler_execution_context=resolved_context,
        operator_inbox_urgency_projector=(
            _fresh_read_covers_all_pending_material(
                turn_start_hook_dispatch,
                operator_inbox_urgency_projector,
            )
            if fresh_operator_inbox_read
            else operator_inbox_urgency_projector
        ),
        receipt_bound_todo_id=receipt_bound_todo_id,
        requested_action_todo_id=requested_action_todo_id,
        receipt_bound_monitor_phase=receipt_bound_monitor_phase,
        receipt_bound_replay_phase=receipt_bound_replay_phase,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )
    _apply_retained_action_selection_reentry(
        payload,
        retained_todo_id=retained_action_selection_todo_id,
        available_capabilities=available_capabilities,
        scheduler_execution_context=resolved_context,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )
    remembered_runtime = (payload.get("agent_identity") or {}).get(
        "runtime_available_capabilities"
    )
    if isinstance(remembered_runtime, list):
        available_capabilities = remembered_runtime
    if route_source.startswith("loopx_turn_"):
        payload["runtime_root"] = str(runtime_root)
    if codex_app_host and agent_id:
        from ..heartbeat.prompt_upgrade_hook import extend_prompt_upgrade_reads

        turn_start_hook_dispatch = extend_prompt_upgrade_reads(
            turn_start_hook_dispatch, registry=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, agent_id=agent_id,
        )
    _project_turn_start_required_reads(
        payload,
        turn_start_hook_dispatch,
        available_capabilities=available_capabilities,
        scheduler_execution_context=resolved_context,
        turn_instance_id=turn_instance_id,
        runtime_root=runtime_root,
    )
    hook_dispatch = dispatch_interaction_projection_hooks(interaction_projection_hooks)
    projections = hook_dispatch["projections"]
    if isinstance(projections, Mapping):
        _apply_pending_capability_intent_precedence(
            payload,
            projections.get("pending_capability_intent"),
            available_capabilities=available_capabilities,
            scheduler_execution_context=resolved_context,
            turn_instance_id=turn_instance_id,
        )
        interaction = payload.get("interaction_contract")
        if isinstance(interaction, dict):
            interaction.update(projections)
    # A settled receipt owns this host Turn until it ends.  Looking for an older
    # unsettled Turn here can overwrite the settled-skip route with a recovery
    # obligation and then select a successor against the immutable receipt
    # identity.  Leave prior-Turn recovery to the next fresh Turn instead.
    if receipt_bound_replay_phase is not ReceiptBoundReplayPhase.SETTLED:
        apply_unsettled_host_turn_recovery_if_required(
            payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            current_turn_instance_id=turn_instance_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=resolved_context,
        )
    if hook_dispatch["failures"]:
        payload["capability_hook_dispatch"] = {
            key: value for key, value in hook_dispatch.items() if key != "projections"
        }
    interaction = payload.get("interaction_contract")
    if isinstance(interaction, dict) and goal_id and agent_id:
        scope = {
            "goal_id": goal_id,
            "agent_id": agent_id,
            "todo_id": (payload.get("selected_todo") or {}).get("todo_id"),
        }
        goal = load_goal_from_registry(registry_path, goal_id)
        if goal is not None:
            context = project_goal_agent_context(
                phase="before_plan",
                scope=scope,
                goal=goal,
                registry_path=registry_path,
                runtime_root=runtime_root,
            )
        else:
            context = project_agent_context(
                phase="before_plan",
                scope=scope,
                orchestration=(payload.get("goal_boundary") or {}).get("orchestration") or {},
            )
        if context is not None:
            interaction["agent_context"] = context
    bind_scheduler_followup_cli_routes(
        payload,
        registry_path=registry_path,
        runtime_root=runtime_root,
        turn_instance_id=turn_instance_id,
        source=route_source,
    )
    bind_action_selection_cli_routes(
        payload,
        registry_path=registry_path,
        runtime_root=runtime_root,
    )
    return payload
