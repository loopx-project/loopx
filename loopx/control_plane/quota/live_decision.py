from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from ...agent_registry import (
    registered_agent_ids_for_goal as registered_agent_ids_for_goal,
)
from ...quota import build_quota_should_run as build_quota_should_run
from ..capability_hooks import (
    InteractionProjectionHookRegistration,
    TurnStartContextProjector,
    dispatch_interaction_projection_hooks,
)
from ..scheduler.execution_context import SchedulerExecutionContextResolution
from ..scheduler.scheduler_hint import build_scheduler_hint
from ..scheduler.state import load_app_automation_scheduler_state
from ..todos.contract import normalize_todo_id
from ..work_items.action_portfolio import reconcile_retained_action_selection
from ..work_items.autonomous_replan_obligation import (
    replan_obligation_id_from_packet,
)
from ..work_items.interaction_contract import (
    build_interaction_contract as build_interaction_contract,
)
from .effect_program import ReceiptBoundReplayPhase as ReceiptBoundReplayPhase
from .effective_action import EffectiveAction as EffectiveAction
from .live_decision_contracts import (
    BoundedResearchFrontierProjector as BoundedResearchFrontierProjector,
    HostObservationResolver as HostObservationResolver,
    OperatorInboxUrgencyProjector,
    ProgressReviewContextReader as ProgressReviewContextReader,
    ReconcileRetainedSelectionFn,
    mapping_or_empty,
    optional_json_map,
    require_mapping,
)
from .live_decision_recovery import (
    apply_live_decision_post_hooks_recovery,
    resolve_live_decision_preflight,
    run_remote_authority_wait_preflight,
)
from .live_decision_replan import (
    _historical_ack_writeback_required as _historical_ack_writeback_required,
    _original_replan_settlement_required as _original_replan_settlement_required,
    _prepared_execution_needs_readback as _prepared_execution_needs_readback,
    _project_receipt_replan_settlement as _project_receipt_replan_settlement,
    _receipt_bound_guard_fields as _receipt_bound_guard_fields,
    _receipt_bound_recovery_todo as _receipt_bound_recovery_todo,
    _restore_replan_obligation_without_foreign_acks as _restore_replan_obligation_without_foreign_acks,
    _split_receipt_bound_guard_fields,
    prepare_live_decision_status_payload,
    project_before_plan_agent_context,
)
from .live_decision_routes import (
    _bind_followup_turn_argument as _bind_followup_turn_argument,
    _scheduler_fallback_launcher as _scheduler_fallback_launcher,
    bind_action_selection_cli_routes as bind_action_selection_cli_routes,
    bind_scheduler_followup_cli_routes as bind_scheduler_followup_cli_routes,
)
from .live_decision_turn_reads import (
    _fresh_operator_inbox_observation_count as _fresh_operator_inbox_observation_count,
    _fresh_operator_inbox_read_required as _fresh_operator_inbox_read_required,
    _fresh_read_covers_all_pending_material as _fresh_read_covers_all_pending_material,
    _fresh_read_covers_pending_count,
    _turn_start_required_reads,
    merge_turn_start_required_reads,
)
from .settlement import (
    QuotaSettlementReadback as QuotaSettlementReadback,
    attach_settlement_progress as attach_settlement_progress,
    read_heartbeat_settlement as read_heartbeat_settlement,
)
from .unsettled_host_turn import (
    apply_receipt_bound_wait_recovery as apply_receipt_bound_wait_recovery,
    apply_unsettled_host_turn_recovery_if_required as apply_unsettled_host_turn_recovery_if_required,
)


def _apply_retained_action_selection_reentry(
    payload: dict[str, object],
    *,
    retained_todo_id: str | None,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    registry_path: Path,
    runtime_root: Path,
    reconcile_retained_fn: ReconcileRetainedSelectionFn = reconcile_retained_action_selection,
) -> None:
    """Fence a no-argument reentry with its last explicit Todo choice."""

    normalized_retained = normalize_todo_id(retained_todo_id)
    if normalized_retained is None:
        return
    selected = mapping_or_empty(payload.get("selected_todo"))
    verdict = reconcile_retained_fn(
        retained_todo_id=normalized_retained,
        projected_todo_id=normalize_todo_id(selected.get("todo_id")),
        effective_action=str(payload.get("effective_action") or ""),
        replan_obligation_id=replan_obligation_id_from_packet(
            payload.get("replan_action_packet")
        ),
    )
    payload["retained_action_selection"] = verdict
    disposition = verdict.get("disposition")
    consumed_fields = verdict.get("clear_fields", [])
    if not isinstance(consumed_fields, list) or not all(
        isinstance(field, str) for field in consumed_fields
    ):
        raise RuntimeError("TypeScript retained action-selection fields are malformed")
    for field in consumed_fields:
        payload.pop(field, None)
    if disposition == "preserve_retained_todo":
        pass
    elif disposition == "bind_autonomous_replan":
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
            raise RuntimeError("TypeScript retained action-selection projection is missing")
        decision_patch = projection.get("decision_patch")
        obligation_patch = projection.get("execution_obligation_patch")
        clear_fields = projection.get("clear_fields")
        if (
            not isinstance(decision_patch, Mapping)
            or not isinstance(obligation_patch, Mapping)
            or not isinstance(clear_fields, list)
            or not all(isinstance(field, str) for field in clear_fields)
        ):
            raise RuntimeError("TypeScript retained action-selection projection is malformed")
        payload.update(cast(Mapping[str, object], decision_patch))
        obligation = dict(mapping_or_empty(payload.get("execution_obligation")))
        obligation.update(cast(Mapping[str, object], obligation_patch))
        payload["execution_obligation"] = obligation
        for field in clear_fields:
            payload.pop(field, None)
    else:
        raise RuntimeError("TypeScript retained action-selection disposition is unsupported")
    payload["interaction_contract"] = build_interaction_contract(
        payload, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        registry_path=str(registry_path), runtime_root=str(runtime_root),
    )


def _project_turn_start_required_reads(
    payload: dict[str, object],
    dispatch: Mapping[str, object] | None,
    *,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    registry_path: Path,
    runtime_root: Path,
    context_projector: TurnStartContextProjector | None = None,
) -> Mapping[str, object] | None:
    """Order evidence after selection and retain its source dispatch."""

    if context_projector is not None:
        dispatch = context_projector(payload, dispatch)
    if dispatch:
        payload["turn_start_capability_hook_dispatch"] = {
            key: value for key, value in dispatch.items() if key != "contexts"
        }
    projected = _turn_start_required_reads(dispatch)
    if not projected:
        return dispatch
    payload["required_reads"] = merge_turn_start_required_reads(
        payload.get("required_reads"), projected
    )
    if _fresh_operator_inbox_read_required(dispatch):
        recommendation = dict(mapping_or_empty(payload.get("heartbeat_recommendation")))
        recommendation.update({"notify": "NOTIFY"})
        payload["heartbeat_recommendation"] = recommendation
    payload["interaction_contract"] = build_interaction_contract(
        payload, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        registry_path=str(registry_path), runtime_root=str(runtime_root),
    )
    return dispatch


def _apply_pending_capability_intent_precedence(
    payload: dict[str, object],
    projection: Mapping[str, object] | None,
    *,
    available_capabilities: object = None,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ) = None,
    turn_instance_id: str | None = None,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
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
        payload, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        registry_path=str(registry_path) if registry_path is not None else None,
        runtime_root=str(runtime_root) if runtime_root is not None else None,
    )
    return True


def _project_unbound_work_fields(payload: dict[str, object]) -> None:
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    if not isinstance(cli, dict) or cli.get("turn_binding_required") is not True:
        return
    reason = cli["spend_policy"]
    next_actions = cli["next_cli_actions"]
    if not isinstance(next_actions, Sequence) or isinstance(
        next_actions, (str, bytes, bytearray)
    ):
        raise TypeError(
            "Expected sequence for cli_channel.next_cli_actions, "
            f"got {type(next_actions).__name__}"
        )
    recommended = next_actions[0]
    payload["normal_delivery_allowed"] = False
    payload["recommended_action"] = recommended
    payload.pop("agent_lane_next_action", None)
    obligation = payload.get("execution_obligation")
    if isinstance(obligation, dict):
        obligation.update(must_attempt_work=False, delivery_allowed=False, reason=reason)
    lane = payload.get("work_lane_contract")
    if isinstance(lane, dict):
        lane.update(must_attempt_work=False, action=recommended)
    recommendation = payload.get("heartbeat_recommendation")
    if isinstance(recommendation, dict):
        recommendation["spend_policy"] = reason


def build_live_quota_should_run_decision(
    status_payload: dict[str, object],
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
    scheduler_execution_context: Mapping[str, object]
    | SchedulerExecutionContextResolution
    | None = None,
    operator_inbox_urgency_projector: OperatorInboxUrgencyProjector | None = None,
    bounded_research_frontier_projector: BoundedResearchFrontierProjector | None = None,
    progress_review_context_reader: ProgressReviewContextReader | None = None,
    receipt_bound_todo_id: str | None = None,
    requested_action_todo_id: str | None = None,
    receipt_bound_replan_obligation_id: str | None = None,
    retained_action_selection_todo_id: str | None = None,
    turn_instance_id: str | None = None,
    interaction_projection_hooks: Sequence[InteractionProjectionHookRegistration]
    | None = None,
    turn_start_hook_dispatch: Mapping[str, object] | None = None,
    turn_context_projector: TurnStartContextProjector | None = None,
    context_navigation: bool = False,
    workspace_path: Path | None = None,
    goal_ref: Mapping[str, object] | None = None,
    inline_goal_source: bool = False,
    tolerate_malformed_settlement_history: bool = False,
    reuse_fresh_canonical_snapshot: bool = False,
) -> dict[str, object]:
    """Build one live CLI decision while keeping host observation injectable."""

    (
        resolved_context,
        registered_goal,
        codex_app_host,
        observed_rrule,
        observed_automation_id,
        decision_status_payload,
        remote_config,
    ) = resolve_live_decision_preflight(
        status_payload, goal_id=goal_id, agent_id=agent_id,
        codex_app_current_rrule=codex_app_current_rrule,
        registry_path=registry_path, runtime_root=runtime_root,
        scheduler_execution_context=scheduler_execution_context,
        host_observation_resolver=host_observation_resolver,
        bounded_research_frontier_projector=bounded_research_frontier_projector,
    )
    if remote_config is not None:
        remote_payload = _build_remote_authority_wait_decision(
            decision_status_payload, configured_goal=registered_goal or {},
            goal_id=goal_id, agent_id=agent_id, remote_config=remote_config,
            registry_path=registry_path, runtime_root=runtime_root, goal_ref=goal_ref,
            scheduler_context=resolved_context, available_capabilities=available_capabilities,
            include_scheduler_detail=include_scheduler_detail,
            codex_app_rrule=observed_rrule, codex_app_automation_id=observed_automation_id or None,
            turn_instance_id=turn_instance_id, receipt_bound_todo_id=receipt_bound_todo_id,
            receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        )
        bind_scheduler_followup_cli_routes(
            remote_payload, registry_path=registry_path, runtime_root=runtime_root,
            turn_instance_id=turn_instance_id, source=route_source,
        )
        return remote_payload
    settlement_readback = read_heartbeat_settlement(
        runtime_root, goal_id=goal_id, agent_id=agent_id, todo_id=receipt_bound_todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=(
            None if receipt_bound_todo_id else receipt_bound_replan_obligation_id
        ),
        registry_path=registry_path, goal_ref=goal_ref,
        tolerate_malformed_history=tolerate_malformed_settlement_history,
    )
    receipt_bound_monitor_phase = (
        settlement_readback.monitor_phase if settlement_readback else None
    )
    receipt_bound_replay_phase = (
        settlement_readback.replay_phase if settlement_readback else None
    )
    guard_kwargs, admission_event_id = _split_receipt_bound_guard_fields(
        settlement_readback, receipt_bound_replan_obligation_id,
    )
    fresh_inbox_count = _fresh_operator_inbox_observation_count(turn_start_hook_dispatch)
    decision_status_payload, authoritative_fallback_todo_items = (
        prepare_live_decision_status_payload(
            decision_status_payload, goal_id=goal_id, agent_id=agent_id, goal_ref=goal_ref,
            registered_goal=registered_goal, registry_path=registry_path,
            runtime_root=runtime_root, guard_kwargs=guard_kwargs,
            admission_event_id=admission_event_id,
            receipt_bound_replay_phase=receipt_bound_replay_phase,
            receipt_bound_todo_id=receipt_bound_todo_id,
            requested_action_todo_id=requested_action_todo_id,
            retained_action_selection_todo_id=retained_action_selection_todo_id,
            receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
            progress_review_context_reader=progress_review_context_reader,
        )
    )
    app_scheduler_state: dict[str, object] | None = None

    def observe_app_scheduler_state(readback: Mapping[str, object]) -> None:
        nonlocal app_scheduler_state
        state = readback.get("state")
        app_scheduler_state = dict(state) if isinstance(state, Mapping) else None

    payload: dict[str, object] = build_quota_should_run(
        decision_status_payload, goal_id=goal_id, agent_id=agent_id,
        available_capabilities=available_capabilities,
        include_scheduler_detail=include_scheduler_detail,
        include_agent_todo_detail=include_agent_todo_detail,
        codex_app_current_rrule=observed_rrule,
        codex_app_automation_id=observed_automation_id or None,
        scheduler_execution_context=resolved_context,
        operator_inbox_urgency_projector=(
            _fresh_read_covers_pending_count(
                fresh_inbox_count, operator_inbox_urgency_projector,
            )
            if fresh_inbox_count > 0
            else operator_inbox_urgency_projector
        ),
        receipt_bound_todo_id=receipt_bound_todo_id,
        requested_action_todo_id=requested_action_todo_id,
        retained_action_selection_todo_id=retained_action_selection_todo_id,
        receipt_bound_monitor_phase=receipt_bound_monitor_phase,
        receipt_bound_replay_phase=receipt_bound_replay_phase,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        **guard_kwargs,
        turn_instance_id=turn_instance_id, runtime_root=runtime_root,
        app_scheduler_state_observer=observe_app_scheduler_state,
        workspace_path=workspace_path, goal_ref=goal_ref,
        authoritative_fallback_todo_items=authoritative_fallback_todo_items,
        reuse_fresh_canonical_snapshot=reuse_fresh_canonical_snapshot,
    )
    _project_receipt_replan_settlement(
        payload, settlement_readback,
        available_capabilities=available_capabilities,
        scheduler_execution_context=resolved_context,
        turn_instance_id=turn_instance_id, registry_path=registry_path,
        runtime_root=runtime_root, goal_ref=goal_ref,
        attach_settlement_fn=attach_settlement_progress,
        build_interaction_contract_fn=build_interaction_contract,
    )
    _apply_retained_action_selection_reentry(
        payload, retained_todo_id=retained_action_selection_todo_id,
        available_capabilities=available_capabilities,
        scheduler_execution_context=resolved_context,
        turn_instance_id=turn_instance_id, registry_path=registry_path,
        runtime_root=runtime_root,
    )
    raw_identity = payload.get("agent_identity")
    agent_identity = (
        require_mapping(raw_identity, label="agent_identity") if raw_identity else {}
    )
    remembered_runtime = agent_identity.get("runtime_available_capabilities")
    if isinstance(remembered_runtime, list):
        available_capabilities = cast(list[str], remembered_runtime)
    if route_source.startswith("loopx_turn_"):
        payload["runtime_root"] = str(runtime_root)
    if codex_app_host and agent_id:
        from ..heartbeat.prompt_upgrade_hook import extend_prompt_upgrade_reads

        turn_start_hook_dispatch = extend_prompt_upgrade_reads(
            turn_start_hook_dispatch, registry=registry_path,
            runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id,
        )
    turn_start_hook_dispatch = _project_turn_start_required_reads(
        payload, turn_start_hook_dispatch,
        available_capabilities=available_capabilities,
        scheduler_execution_context=resolved_context,
        turn_instance_id=turn_instance_id, registry_path=registry_path,
        runtime_root=runtime_root, context_projector=turn_context_projector,
    )
    hook_dispatch = dispatch_interaction_projection_hooks(interaction_projection_hooks)
    projections = hook_dispatch["projections"]
    if isinstance(projections, Mapping):
        _apply_pending_capability_intent_precedence(
            payload, projections.get("pending_capability_intent"),
            available_capabilities=available_capabilities,
            scheduler_execution_context=resolved_context,
            turn_instance_id=turn_instance_id, registry_path=registry_path,
            runtime_root=runtime_root,
        )
        interaction = payload.get("interaction_contract")
        if isinstance(interaction, dict):
            interaction.update(projections)
    apply_live_decision_post_hooks_recovery(
        payload, settlement_readback=settlement_readback,
        receipt_bound_monitor_phase=receipt_bound_monitor_phase,
        receipt_bound_replay_phase=receipt_bound_replay_phase,
        receipt_bound_todo_id=receipt_bound_todo_id,
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, agent_id=agent_id, turn_instance_id=turn_instance_id,
        available_capabilities=available_capabilities,
        include_scheduler_detail=include_scheduler_detail,
        resolved_context=resolved_context,
        app_scheduler_state=app_scheduler_state,
        observed_rrule=observed_rrule,
        observed_automation_id=observed_automation_id,
        goal_ref=goal_ref, registered_goal=registered_goal,
        hook_dispatch=hook_dispatch,
        apply_recovery_fn=apply_unsettled_host_turn_recovery_if_required,
        attach_settlement_fn=attach_settlement_progress,
        apply_wait_recovery_fn=apply_receipt_bound_wait_recovery,
        build_scheduler_hint_fn=build_scheduler_hint,
    )
    _project_unbound_work_fields(payload)
    if goal_id and agent_id:
        raw_boundary = payload.get("goal_boundary")
        boundary = require_mapping(raw_boundary, label="goal_boundary") if raw_boundary else {}
        raw_orch = boundary.get("orchestration")
        orchestration = (
            require_mapping(raw_orch, label="goal_boundary.orchestration")
            if raw_orch
            else {}
        )
        project_before_plan_agent_context(
            payload, goal_id=goal_id, agent_id=agent_id,
            registered_goal=registered_goal, orchestration=orchestration,
            registry_path=registry_path, runtime_root=runtime_root,
        )
    bind_scheduler_followup_cli_routes(
        payload, registry_path=registry_path, runtime_root=runtime_root,
        turn_instance_id=turn_instance_id, source=route_source,
    )
    bind_action_selection_cli_routes(
        payload, registry_path=registry_path, runtime_root=runtime_root,
    )
    from ..work_items.context_readback import attach_work_context

    attach_work_context(
        payload, registry_path=registry_path, runtime_root=runtime_root,
        hook_dispatch=optional_json_map(turn_start_hook_dispatch),
        context_navigation=context_navigation, inline_goal_source=inline_goal_source,
    )
    return payload


def _build_remote_authority_wait_decision(
    status_payload: dict[str, object],
    *,
    configured_goal: dict[str, object],
    goal_id: str,
    agent_id: str | None,
    remote_config: Mapping[str, object],
    registry_path: Path,
    runtime_root: Path,
    goal_ref: Mapping[str, object] | None,
    scheduler_context: SchedulerExecutionContextResolution,
    available_capabilities: list[str] | None,
    turn_instance_id: str | None,
    include_scheduler_detail: bool,
    codex_app_rrule: str,
    codex_app_automation_id: str | None,
    receipt_bound_todo_id: str | None,
    receipt_bound_replan_obligation_id: str | None,
) -> dict[str, object]:
    early_payload, payload, scope_matches, settled = run_remote_authority_wait_preflight(
        status_payload, configured_goal=configured_goal, goal_id=goal_id,
        agent_id=agent_id, registry_path=registry_path, runtime_root=runtime_root,
        goal_ref=goal_ref, scheduler_context=scheduler_context,
        available_capabilities=available_capabilities,
        turn_instance_id=turn_instance_id,
        include_scheduler_detail=include_scheduler_detail,
        codex_app_rrule=codex_app_rrule,
        codex_app_automation_id=codex_app_automation_id,
        receipt_bound_todo_id=receipt_bound_todo_id,
        receipt_bound_replan_obligation_id=receipt_bound_replan_obligation_id,
        read_settlement_fn=read_heartbeat_settlement,
        attach_settlement_fn=attach_settlement_progress,
        apply_wait_recovery_fn=apply_receipt_bound_wait_recovery,
        apply_recovery_fn=apply_unsettled_host_turn_recovery_if_required,
        build_scheduler_hint_fn=build_scheduler_hint,
        load_scheduler_state_fn=load_app_automation_scheduler_state,
    )
    if early_payload is not None:
        return early_payload

    from ..coordination.remote_authority_gateway import (
        check_remote_authority_gateway_status,
        get_remote_authority_projection,
    )
    from .settlement_precedence import settled_replay_fields

    if settled:
        payload.update(cast(Mapping[str, object], settled_replay_fields()))
    else:
        gateway = (
            check_remote_authority_gateway_status(goal_id, agent_id, remote_config)
            if scope_matches
            else {
                **get_remote_authority_projection(goal_id, remote_config),
                "status": "unavailable",
                "reason_code": "remote_authority_local_scope_mismatch",
            }
        )
        reason_code = gateway.get(
            "reason_code", "remote_authority_execution_not_admitted"
        )
        reason = (
            "Remote authority read verified; shared Turn admission is unavailable"
            if gateway["status"] == "verified"
            else "Waiting for a verified read from the configured remote authority"
        )
        action = EffectiveAction.BLOCKED_WAIT.value
        payload.update(
            decision="wait", state="waiting", effective_action=action,
            remote_gateway=gateway, reason_code=reason_code,
            reason=reason, recommended_action=reason,
            execution_obligation={
                "kind": action, "must_attempt_work": False, "delivery_allowed": False,
                "reason": reason,
                "spend_policy": "no quota spend for remote authority observation",
            },
        )
    payload["interaction_contract"] = build_interaction_contract(
        payload, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_context, turn_instance_id=turn_instance_id,
        registry_path=str(registry_path), runtime_root=str(runtime_root),
    )
    return payload
