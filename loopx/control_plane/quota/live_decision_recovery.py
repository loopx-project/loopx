from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import cast

from ...agent_registry import load_goal_from_registry, registered_agent_ids_for_goal
from ..agents.identity import build_quota_agent_identity
from ..scheduler.arbitration import SchedulerArbitration
from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    resolve_scheduler_execution_context,
)
from .effect_program import ReceiptBoundMonitorPhase, ReceiptBoundReplayPhase
from .live_decision_contracts import (
    ApplyUnsettledRecoveryFn,
    ApplyWaitRecoveryFn,
    AttachSettlementProgressFn,
    BoundedResearchFrontierProjector,
    BuildSchedulerHintFn,
    HostObservationResolver,
    LoadAppSchedulerStateFn,
    ReadHeartbeatSettlementFn,
    as_json_map,
)
from .live_decision_replan import (
    _original_replan_settlement_required,
    _prepared_execution_needs_readback,
    _receipt_bound_recovery_todo,
    owner_resume_action_id,
    resolve_host_rrule_and_automation,
)
from .settlement import QuotaSettlementReadback


def resolve_live_decision_preflight(
    status_payload: dict[str, object],
    *,
    goal_id: str,
    agent_id: str | None,
    codex_app_current_rrule: str | None,
    registry_path: Path,
    runtime_root: Path,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ),
    host_observation_resolver: HostObservationResolver | None,
    bounded_research_frontier_projector: BoundedResearchFrontierProjector | None,
) -> tuple[
    SchedulerExecutionContextResolution,
    dict[str, object] | None,
    bool,
    str,
    str,
    dict[str, object],
    Mapping[str, object] | None,
]:
    """Resolve host context, observed schedule state, frontier, and remote config."""

    resolved_context = resolve_scheduler_execution_context(scheduler_execution_context)
    from ..coordination.remote_authority_gateway import remote_authority_config

    registered_goal = load_goal_from_registry(registry_path, goal_id)
    host_ctx = resolved_context.context
    app_automation_applicable = (
        resolved_context.ok
        and host_ctx is not None
        and host_ctx.app_automation_applicable
    )
    codex_app_host = bool(
        app_automation_applicable
        and host_ctx is not None
        and host_ctx.host_surface.value == "codex_app"
    )
    observed_rrule, observed_automation_id = resolve_host_rrule_and_automation(
        codex_app_host=codex_app_host,
        codex_app_current_rrule=codex_app_current_rrule,
        host_observation_resolver=host_observation_resolver,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    decision_status_payload: dict[str, object] = {
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
    remote_config = remote_authority_config(registered_goal)
    return (
        resolved_context,
        registered_goal,
        codex_app_host,
        observed_rrule,
        observed_automation_id,
        decision_status_payload,
        remote_config,
    )


def apply_live_decision_post_hooks_recovery(
    payload: dict[str, object],
    *,
    settlement_readback: QuotaSettlementReadback | None,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None,
    receipt_bound_replay_phase: ReceiptBoundReplayPhase | None,
    receipt_bound_todo_id: str | None,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    turn_instance_id: str | None,
    available_capabilities: list[str] | None,
    include_scheduler_detail: bool,
    resolved_context: SchedulerExecutionContextResolution,
    app_scheduler_state: dict[str, object] | None,
    observed_rrule: str,
    observed_automation_id: str,
    goal_ref: Mapping[str, object] | None,
    registered_goal: dict[str, object] | None,
    hook_dispatch: Mapping[str, object],
    apply_recovery_fn: ApplyUnsettledRecoveryFn,
    attach_settlement_fn: AttachSettlementProgressFn,
    apply_wait_recovery_fn: ApplyWaitRecoveryFn,
    build_scheduler_hint_fn: BuildSchedulerHintFn,
) -> None:
    """Apply unsettled host recovery, receipt-bound wait recovery, and hook failure projection."""

    original_replan_settlement = _original_replan_settlement_required(
        payload, settlement_readback
    )
    if (
        receipt_bound_replay_phase is not ReceiptBoundReplayPhase.SETTLED
        and not original_replan_settlement
    ):
        apply_recovery_fn(
            payload,
            inspect_current_execution_loss=_prepared_execution_needs_readback(
                payload, settlement_readback, receipt_bound_todo_id
            ),
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            current_turn_instance_id=turn_instance_id,
            available_capabilities=available_capabilities,
            project_scheduler_hint=partial(
                build_scheduler_hint_fn,
                include_detail=include_scheduler_detail,
                scheduler_execution_context=resolved_context,
                available_capabilities=available_capabilities,
                codex_app_scheduler_state=app_scheduler_state,
                codex_app_current_rrule=observed_rrule,
                codex_app_automation_id=observed_automation_id or None,
            ),
            scheduler_execution_context=resolved_context,
            goal_ref=goal_ref,
            owner_action_id=owner_resume_action_id(registered_goal),
        )
    if (
        (
            recovery_todo_id := _receipt_bound_recovery_todo(
                payload,
                receipt_bound_todo_id,
                agent_id=agent_id,
                turn_instance_id=turn_instance_id,
                replay_phase=receipt_bound_replay_phase,
            )
        )
        and agent_id
        and turn_instance_id
    ):
        if settlement_readback is not None:
            attach_settlement_fn(
                payload,
                settlement_readback,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
            )
        apply_wait_recovery_fn(
            payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=recovery_todo_id,
            turn_instance_id=turn_instance_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=resolved_context,
            monitor_phase=(
                receipt_bound_monitor_phase.value
                if receipt_bound_monitor_phase
                else None
            ),
            settlement_progress=as_json_map(settlement_readback.progress)
            if settlement_readback
            else None,
            replay_phase=receipt_bound_replay_phase,
            heartbeat_receipt=settlement_readback.heartbeat_receipt
            if settlement_readback
            else None,
        )
    if hook_dispatch["failures"]:
        payload["capability_hook_dispatch"] = {
            key: value for key, value in hook_dispatch.items() if key != "projections"
        }


def run_remote_authority_wait_preflight(
    status_payload: dict[str, object],
    *,
    configured_goal: dict[str, object],
    goal_id: str,
    agent_id: str | None,
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
    read_settlement_fn: ReadHeartbeatSettlementFn,
    attach_settlement_fn: AttachSettlementProgressFn,
    apply_wait_recovery_fn: ApplyWaitRecoveryFn,
    apply_recovery_fn: ApplyUnsettledRecoveryFn,
    build_scheduler_hint_fn: BuildSchedulerHintFn,
    load_scheduler_state_fn: LoadAppSchedulerStateFn,
) -> tuple[dict[str, object] | None, dict[str, object], bool, bool]:
    """Run hard-pause check and recovery preflight for remote-authority wait decisions."""

    from ...quota import _build_quota_plan_for_goal
    from .decision_summary import quota_plan_items
    from .should_run import build_quota_paused_should_run_payload
    from .states import quota_item_is_paused

    plan, health_ok = _build_quota_plan_for_goal(status_payload, goal_id=goal_id)
    item = next(
        (row for row in quota_plan_items(plan) if row.get("goal_id") == goal_id),
        None,
    )
    if item is not None and quota_item_is_paused(item):
        paused = cast(
            dict[str, object],
            build_quota_paused_should_run_payload(
                status_payload,
                safe_goal_id=goal_id,
                requested_agent_id=agent_id,
                item=item,
                plan=plan,
                goal_health_ok=health_ok,
                include_scheduler_detail=include_scheduler_detail,
                codex_app_current_rrule=codex_app_rrule,
                codex_app_automation_id=codex_app_automation_id,
                resolved_scheduler_context=scheduler_context,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
            ),
        )
        return paused, paused, False, False

    scope_matches = (
        goal_ref is None
        or goal_ref.get("goal_instance_id") == configured_goal.get("goal_instance_id")
    ) and agent_id in registered_agent_ids_for_goal(configured_goal)
    readback = (
        read_settlement_fn(
            runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=receipt_bound_todo_id,
            turn_instance_id=turn_instance_id,
            registry_path=registry_path,
            goal_ref=goal_ref,
            replan_obligation_id=(
                None if receipt_bound_todo_id else receipt_bound_replan_obligation_id
            ),
        )
        if scope_matches and turn_instance_id
        else None
    )
    settled = (
        readback is not None and readback.replay_phase is ReceiptBoundReplayPhase.SETTLED
    )
    payload: dict[str, object] = {
        "ok": True,
        "mode": "should-run",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "should_run": False,
        "normal_delivery_allowed": False,
        "requires_user_action": False,
    }
    if goal_ref is not None:
        payload["goal_ref"] = dict(goal_ref)

    def project_recovery_scheduler_hint(
        decision: dict[str, object],
        *,
        arbitration: SchedulerArbitration,
    ) -> dict[str, object]:
        decision["agent_identity"] = build_quota_agent_identity(
            configured_goal, agent_id=agent_id
        )
        host = scheduler_context.context
        state = None
        if scheduler_context.ok and host is not None and host.app_automation_applicable:
            state = load_scheduler_state_fn(
                runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                surface=host.host_surface.value,
            )
        return build_scheduler_hint_fn(
            decision,
            arbitration=arbitration,
            include_detail=include_scheduler_detail,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_context,
            codex_app_scheduler_state=state,
            codex_app_current_rrule=codex_app_rrule,
            codex_app_automation_id=codex_app_automation_id,
        )

    if scope_matches and not settled:
        if (
            receipt_bound_todo_id
            and agent_id
            and turn_instance_id
            and readback is not None
            and readback.replay_phase is ReceiptBoundReplayPhase.SETTLEMENT_PENDING
        ):
            attach_settlement_fn(
                payload,
                readback,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
            )
            if apply_wait_recovery_fn(
                payload,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                todo_id=receipt_bound_todo_id,
                turn_instance_id=turn_instance_id,
                available_capabilities=available_capabilities,
                scheduler_execution_context=scheduler_context,
                monitor_phase=(
                    readback.monitor_phase.value if readback.monitor_phase else None
                ),
                settlement_progress=as_json_map(readback.progress),
                replay_phase=readback.replay_phase,
                heartbeat_receipt=readback.heartbeat_receipt,
            ):
                return payload, payload, scope_matches, settled
        if apply_recovery_fn(
            payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            current_turn_instance_id=turn_instance_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=scheduler_context,
            project_scheduler_hint=project_recovery_scheduler_hint,
            goal_ref=goal_ref,
            owner_action_id=owner_resume_action_id(configured_goal),
        ):
            return payload, payload, scope_matches, settled
    return None, payload, scope_matches, settled
