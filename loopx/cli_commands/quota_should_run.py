"""Execution and heartbeat receipt reconciliation for `loopx quota should-run`."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..capabilities.progress_review.context import external_progress_review_context
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.effect_program import SettlementIdentity
from ..control_plane.quota.error_codes import HeartbeatReceiptIdentityConflictError
from ..control_plane.quota.heartbeat_receipt import (
    attach_uncommitted_heartbeat_receipt,
    fail_heartbeat_receipt,
    find_heartbeat_receipt,
    heartbeat_receipt_request_subject,
    heartbeat_receipt_view,
)
from ..file_lock import LockAcquireTimeoutError
from ..rollout_event_log import (
    RolloutAppendPreconditionChanged,
    iter_rollout_events,
    rollout_event_log_path,
)
from .quota_presentation import is_undeclared_json_quota_caller

if TYPE_CHECKING:
    from .quota_action_selection import RequestedQuotaActionSelection
    from .quota_context import QuotaCommandContext

RolloutEventAppender = Callable[..., dict[str, object]]


@dataclass(slots=True)
class ShouldRunExecutionState:
    payload: dict[str, object] | None = None
    status_payload: dict[str, object] = field(default_factory=dict)
    cache_metadata: dict[str, object] | None = None
    action_selection: RequestedQuotaActionSelection | None = None
    action_selection_preflight_failed: bool = False
    heartbeat_receipt_existing: dict[str, object] | None = None
    heartbeat_receipt_existing_status: str = "replayed"
    heartbeat_receipt_existing_appended: bool = False
    heartbeat_stall_observation: str = "not_evaluated"
    heartbeat_receipt_ready: bool = False
    upgrade_recovery_wait: bool = False
    reentry: tuple[argparse.Namespace, str | None] | None = None


def dispatch_quota_turn_start_hooks(
    args: argparse.Namespace,
    *,
    quota_module: Any,
    registry_path: Path,
    runtime_root_arg: str | None,
    lark_turn_start_hook_dispatcher: Callable[..., dict[str, object]] | None = None,
) -> tuple[dict[str, object] | None, bool]:
    _, scheduler_context, _ = quota_module._export(
        "validate_quota_command_context_request"
    )(args)
    if args.quota_command != "should-run":
        return None, False
    preview = bool(getattr(args, "dry_run", False))
    if preview and not args.agent_id:
        return None, False
    dispatch: dict[str, object] = {}
    if not preview and lark_turn_start_hook_dispatcher is not None:
        dispatch = lark_turn_start_hook_dispatcher(
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
    if args.agent_id:
        from ..capabilities.manager_context import turn_start_hook
        from ..capabilities.semantic_preference.agent_preferences import (
            extend_turn_start_dispatch as extend_preferences,
        )
        from ..control_plane.capability_hooks import dispatch_turn_start_hooks
        from ..history import load_registry
        from ..paths import resolve_runtime_root

        root = resolve_runtime_root(
            load_registry(registry_path),
            runtime_root_arg,
            registry_path=registry_path,
        )
        if not preview:
            from ..capabilities.turn_start import (
                extend_turn_start_dispatch as extend_registered_capabilities,
            )

            dispatch = extend_registered_capabilities(
                dispatch,
                registry_path=registry_path,
                runtime_root=root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                available=args.available_capabilities,
            )
        # A preview still needs the current local Inbox and preferences. These
        # hooks observe existing context without recording reads or delivery.
        model_read = (
            not getattr(args, "scheduler_observation", False)
            and not is_undeclared_json_quota_caller(args, scheduler_context)
        )
        context_dispatch = dispatch_turn_start_hooks(
            (
                turn_start_hook(
                    root,
                    registry_path,
                    args.goal_id,
                    args.agent_id,
                    model_read=model_read,
                ),
            )
        )
        context_dispatch = extend_preferences(
            context_dispatch,
            runtime_root=root,
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
        dispatch = dict(dispatch)
        for key in ("results", "required_reads", "failures", "contexts"):
            if key == "contexts" and not context_dispatch.get(key):
                continue
            dispatch[key] = list(dispatch.get(key) or []) + list(
                context_dispatch.get(key) or []
            )
        for key in ("registered_count", "invoked_count"):
            dispatch[key] = int(dispatch.get(key) or 0) + int(
                context_dispatch.get(key) or 0
            )
        if not preview:
            from ..capabilities.periodic_report.cadence_runtime import (
                extend_cadence_turn_start_dispatch,
            )

            dispatch = extend_cadence_turn_start_dispatch(
                dispatch,
                registry_path=registry_path,
                runtime_root=root,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
            )
    local_private_state_mutated = bool(dispatch.get("uncertain_local_effects")) or any(
        isinstance(result, Mapping)
        and result.get("local_private_state_mutated") is not False
        for result in (dispatch.get("results") or [])
    )
    return dispatch, local_private_state_mutated


def _can_upgrade_recovery_wait(
    payload: Mapping[str, object],
    selection: RequestedQuotaActionSelection,
) -> bool:
    receipt = selection.receipt
    if (
        receipt is None
        or payload.get("effective_action")
        != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value
    ):
        return False
    details = receipt.get("details")
    if (
        payload.get("should_run") is not True
        or not isinstance(details, Mapping)
        or details.get("should_run") is not False
    ):
        return False
    if (
        receipt.get("status") != EffectiveAction.BLOCKED_WAIT.value
        or selection.receipt_bound_todo_id
        or selection.receipt_bound_replan_obligation_id
        or heartbeat_receipt_request_subject(receipt) is not None
    ):
        raise HeartbeatReceiptIdentityConflictError(
            "The existing Turn has no recovery grant; use a fresh Turn for recovery."
        )
    return True


def _check_vision_baseline_admission(
    log_path: Path,
    *,
    goal_id: str,
    goal_ref: Mapping[str, object] | None,
    agent_id: str,
) -> None:
    """Serialize the once-only admission against the actual committed receipt."""
    from ..control_plane.goals.source_session_registry_state import exact_goal_ref

    expected_ref = (
        exact_goal_ref(str(goal_ref["goal_id"]), str(goal_ref["goal_instance_id"]))
        if goal_ref is not None else None
    )
    for event in iter_rollout_events(log_path, committed_only=True):
        if (
            event.get("event_kind") != "quota_should_run"
            or event.get("goal_id") != goal_id
            or event.get("agent_id") != agent_id
            or event.get("status") != EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value
            or not isinstance(event.get("run_id"), str)
            or not event["run_id"].strip()
            or (event.get("goal_ref") != expected_ref if expected_ref is not None else "goal_ref" in event)
        ):
            continue
        details = event.get("details")
        if (
            isinstance(details, Mapping)
            and details.get("should_run") is True
            and details.get("vision_baseline_initialization") is True
        ):
            raise RolloutAppendPreconditionChanged(
                "The scoped Vision baseline admission was already committed."
            )


def _append_quota_heartbeat_receipt(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    quota_module: Any,
    registry_path: Path,
    runtime_root_arg: str | None,
    runtime_root: Path,
    goal_ref: dict[str, str] | None,
    append_cli_rollout_event: RolloutEventAppender,
    turn_instance_id: str,
    stall_observation: str,
    rollout_todo_id: str | None,
    rollout_replan_obligation_id: str | None,
    rollout_details: dict[str, object],
    upgrade_recovery_wait: bool,
    requested_todo_id: str | None,
) -> dict[str, object]:
    from ..control_plane.quota.unsettled_host_turn import (
        recovery_admission_append_precondition,
    )

    settlement_identity = (
        SettlementIdentity(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=rollout_todo_id,
            turn_instance_id=turn_instance_id,
            replan_obligation_id=rollout_replan_obligation_id,
        )
        if rollout_todo_id or rollout_replan_obligation_id
        else None
    )
    rollout_details.update(
        {
            "turn_instance_id": turn_instance_id,
            "stall_observation": stall_observation,
            "settlement_effect_id": (
                settlement_identity.effect_id if settlement_identity is not None else ""
            ),
        }
    )
    baseline_admission = (
        payload.get("vision_baseline_initialization") is True
        and payload.get("ok") is True
        and payload.get("should_run") is True
        and payload.get("effective_action") == EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value
        and rollout_replan_obligation_id is not None
    )
    if baseline_admission:
        rollout_details["vision_baseline_initialization"] = True

    def check_baseline_commit() -> None:
        _check_vision_baseline_admission(
            rollout_event_log_path(runtime_root, args.goal_id),
            goal_id=args.goal_id,
            goal_ref=goal_ref,
            agent_id=args.agent_id,
        )

    try:
        with recovery_admission_append_precondition(
            payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        ) as recovery_precondition:
            precondition = check_baseline_commit if baseline_admission else recovery_precondition
            append_cli_rollout_event(
                payload,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                event_kind="quota_should_run",
                goal_ref=goal_ref,
                agent_id=args.agent_id,
                run_id=turn_instance_id,
                status=str(
                    payload.get("effective_action")
                    or payload.get("decision")
                    or "should-run"
                ),
                summary=(
                    f"heartbeat quota receipt committed for turn={turn_instance_id} "
                    f"stall={stall_observation}"
                ),
                details=rollout_details,
                allow_failed=True,
                idempotency_fields=[
                    "goal_id",
                    "event_kind",
                    "agent_id",
                    "run_id",
                    *(["goal_ref"] if goal_ref is not None else []),
                    *(["status"] if upgrade_recovery_wait else []),
                ],
                **({"precondition": precondition} if precondition is not None else {}),
            )
        receipt = find_heartbeat_receipt(
            runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
    except LockAcquireTimeoutError as exc:
        failure = quota_module._export("quota_failure_payload")(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            error=exc,
        )
        fail_heartbeat_receipt(
            failure,
            turn_instance_id=turn_instance_id,
            stall_observation=stall_observation,
            reason="recovery receipt append could not acquire the quota authority lock",
        )
        return failure
    if receipt is None:
        fail_heartbeat_receipt(
            payload,
            turn_instance_id=turn_instance_id,
            stall_observation=stall_observation,
            reason=(
                "heartbeat receipt append could not be read back; "
                "retry quota should-run with the same --turn-instance-id"
            ),
        )
        return payload
    if (
        recovery_precondition is not None
        and receipt.get("status")
        != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value
    ):
        raise RolloutAppendPreconditionChanged(
            "A different guard was recorded for this Turn."
        )

    receipt_details = receipt.get("details")
    if baseline_admission and not (
        isinstance(receipt_details, Mapping)
        and receipt_details.get("vision_baseline_initialization") is True
        and receipt_details.get("should_run") is True
        and receipt.get("status") == EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value
    ):
        raise RolloutAppendPreconditionChanged(
            "A different guard was committed for this Vision initialization Turn."
        )

    rollout_event = payload.get("rollout_event")
    status = "replayed"
    if isinstance(rollout_event, Mapping) and rollout_event.get("appended"):
        status = "upgraded" if upgrade_recovery_wait else "committed"
    payload["heartbeat_receipt"] = heartbeat_receipt_view(
        receipt, turn_instance_id=turn_instance_id, status=status
    )
    quota_module._export("commit_requested_action_selection")(
        payload, requested_todo_id=requested_todo_id
    )
    return payload


def execute_quota_should_run(
    args: argparse.Namespace,
    *,
    quota_module: Any,
    registry_path: Path,
    runtime_root_arg: str | None,
    context: QuotaCommandContext,
    goal_ref: dict[str, str] | None,
    turn_start_hook_dispatch: dict[str, object] | None,
    retained_action_selection_todo_id: str | None,
    state: ShouldRunExecutionState | None = None,
) -> ShouldRunExecutionState:
    from ..capabilities.explore.turn_context import turn_context_projector

    export = quota_module._export
    runtime_root = context.runtime_root
    status_payload = context.status_payload
    cache_metadata = context.cache_metadata
    heartbeat_turn_id = context.heartbeat_turn_id
    detail_sections = context.detail_sections
    scheduler_context = context.scheduler_context
    operator_inbox_urgency_projector = context.operator_inbox_urgency_projector
    execution = state or ShouldRunExecutionState(
        status_payload=status_payload,
        cache_metadata=cache_metadata,
    )

    interaction_projection_hooks = (
        export("repository_delivery_interaction_hook")(repo_path=Path.cwd()),
        export("periodic_report_pending_intent_interaction_hook")(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        ),
    )
    action_selection = export("load_requested_quota_action_selection")(
        args,
        runtime_root=runtime_root,
        turn_instance_id=heartbeat_turn_id,
        goal_ref=goal_ref,
        retained_todo_id=retained_action_selection_todo_id,
    )
    # Persist intermediate state eagerly so partial failures retain prior receipts.
    execution.action_selection = action_selection
    execution.heartbeat_receipt_existing = action_selection.receipt
    execution.heartbeat_receipt_existing_status = "replayed"
    execution.heartbeat_receipt_existing_appended = False

    context_projector = turn_context_projector(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        todo_id=args.todo_id,
    )
    complete_context = (
        not getattr(args, "scheduler_observation", False)
        and not is_undeclared_json_quota_caller(args, scheduler_context)
    )
    payload = export("build_live_quota_should_run_decision")(
        status_payload,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        inline_goal_source=complete_context,
        available_capabilities=args.available_capabilities,
        include_scheduler_detail="scheduler" in detail_sections,
        include_agent_todo_detail="agent-todos" in detail_sections,
        codex_app_current_rrule=args.app_automation_current_rrule,
        registry_path=registry_path,
        runtime_root=runtime_root,
        host_observation_resolver=export("resolve_codex_app_automation_rrule"),
        scheduler_execution_context=scheduler_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        bounded_research_frontier_projector=export(
            "project_live_explore_composition_frontier"
        ),
        progress_review_context_reader=external_progress_review_context,
        receipt_bound_todo_id=action_selection.receipt_bound_todo_id,
        requested_action_todo_id=action_selection.requested_todo_id_for_decision,
        receipt_bound_replan_obligation_id=(
            action_selection.receipt_bound_replan_obligation_id
        ),
        retained_action_selection_todo_id=(
            action_selection.retained_todo_id_for_decision
        ),
        turn_instance_id=heartbeat_turn_id,
        interaction_projection_hooks=interaction_projection_hooks,
        turn_start_hook_dispatch=turn_start_hook_dispatch,
        turn_context_projector=context_projector,
        context_navigation=complete_context,
        goal_ref=goal_ref,
    )
    execution.payload = payload
    action_selection_preflight = export("reconcile_requested_quota_action_selection")(
        payload,
        args,
        registry_path=registry_path,
        context=context,
        selection=action_selection,
        goal_ref=goal_ref,
    )
    execution.action_selection_preflight_failed = action_selection_preflight.rejected
    if action_selection_preflight.rejected:
        reentry_args = export("inline_action_selection_reentry_args")(
            payload,
            args,
            selection=action_selection,
            preflight=action_selection_preflight,
            turn_instance_id=heartbeat_turn_id,
        )
        if reentry_args is not None:
            execution.reentry = (
                reentry_args,
                action_selection.requested_todo_id
                if action_selection_preflight.receipt is None
                else None,
            )
        else:
            execution.heartbeat_receipt_existing = action_selection_preflight.receipt
            execution.heartbeat_receipt_existing_status = (
                action_selection_preflight.receipt_status
            )
            execution.heartbeat_receipt_existing_appended = (
                action_selection_preflight.receipt_appended
            )
    if (
        heartbeat_turn_id
        and execution.reentry is None
        and not execution.action_selection_preflight_failed
    ):
        # A wait receipt grants no recovery allowance. After a valid
        # resume, bind its first grant under the ordinary append fence.
        execution.upgrade_recovery_wait = _can_upgrade_recovery_wait(
            payload, action_selection
        )
        if (
            execution.heartbeat_receipt_existing
            and not execution.upgrade_recovery_wait
        ):
            (
                execution.heartbeat_receipt_existing,
                execution.heartbeat_receipt_existing_status,
                execution.heartbeat_receipt_existing_appended,
                execution.heartbeat_stall_observation,
                execution.heartbeat_receipt_ready,
            ) = export("reconcile_existing_heartbeat_receipt_for_turn")(
                payload,
                args,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                existing=execution.heartbeat_receipt_existing,
                goal_ref=goal_ref,
                scheduler_execution_context=context.scheduler_context,
            )
        else:
            (
                execution.payload,
                execution.status_payload,
                execution.cache_metadata,
                execution.heartbeat_stall_observation,
            ) = export("_record_automatic_heartbeat_stall")(
                payload,
                status_payload,
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                context=context,
                interaction_projection_hooks=interaction_projection_hooks,
                turn_start_hook_dispatch=turn_start_hook_dispatch,
                turn_context_projector=context_projector,
                action_selection=action_selection,
                cache_metadata=cache_metadata,
                goal_ref=goal_ref,
                find_monitor_poll_turn=export("find_quota_monitor_poll_turn"),
                monitor_poll_recorder=export("record_quota_monitor_poll"),
                status_collector=export("collect_status"),
                decision_builder=export("build_live_quota_should_run_decision"),
                host_observation_resolver=export("resolve_codex_app_automation_rrule"),
            )
            execution.heartbeat_receipt_ready = True
    return execution


def record_should_run_heartbeat_receipt(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    quota_module: Any,
    registry_path: Path,
    runtime_root_arg: str | None,
    runtime_root: Path,
    goal_ref: dict[str, str] | None,
    append_cli_rollout_event: RolloutEventAppender,
    heartbeat_turn_id: str,
    rollout_todo_id: str | None,
    rollout_replan_obligation_id: str | None,
    rollout_details: dict[str, object],
    action_selection: RequestedQuotaActionSelection | None,
    action_selection_preflight_failed: bool,
    closeout_query_unavailable: bool,
    heartbeat_receipt_existing: dict[str, object] | None,
    heartbeat_receipt_existing_status: str,
    heartbeat_receipt_existing_appended: bool,
    heartbeat_stall_observation: str,
    heartbeat_receipt_ready: bool,
    upgrade_recovery_wait: bool,
    recovery_refreshes_remaining: int,
) -> tuple[dict[str, object], bool]:
    export = quota_module._export
    if action_selection_preflight_failed or closeout_query_unavailable:
        if heartbeat_receipt_existing:
            export("render_existing_heartbeat_receipt_payload")(
                payload,
                receipt=heartbeat_receipt_existing,
                turn_instance_id=heartbeat_turn_id,
                status=heartbeat_receipt_existing_status,
                appended=heartbeat_receipt_existing_appended,
            )
        else:
            attach_uncommitted_heartbeat_receipt(
                payload,
                turn_instance_id=heartbeat_turn_id,
            )
        return payload, False
    if not heartbeat_receipt_ready:
        prior_reason = str(payload.get("reason") or "").strip()
        fail_heartbeat_receipt(
            payload,
            turn_instance_id=heartbeat_turn_id,
            stall_observation=heartbeat_stall_observation,
            reason=(
                "heartbeat receipt was not committed because quota or stall "
                "writeback did not complete"
                + (f": {prior_reason}" if prior_reason else "")
            ),
        )
        return payload, False
    if heartbeat_receipt_existing and not upgrade_recovery_wait:
        export("render_existing_heartbeat_receipt_payload")(
            payload,
            receipt=heartbeat_receipt_existing,
            turn_instance_id=heartbeat_turn_id,
            status=heartbeat_receipt_existing_status,
            appended=heartbeat_receipt_existing_appended,
        )
        export("commit_requested_action_selection")(
            payload,
            requested_todo_id=(
                action_selection.requested_todo_id
                if action_selection is not None
                else None
            ),
        )
        return payload, False
    try:
        payload = _append_quota_heartbeat_receipt(
            payload,
            args,
            quota_module=quota_module,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            runtime_root=runtime_root,
            goal_ref=goal_ref,
            append_cli_rollout_event=append_cli_rollout_event,
            turn_instance_id=heartbeat_turn_id,
            stall_observation=heartbeat_stall_observation,
            rollout_todo_id=rollout_todo_id,
            rollout_replan_obligation_id=rollout_replan_obligation_id,
            rollout_details=rollout_details,
            upgrade_recovery_wait=upgrade_recovery_wait,
            requested_todo_id=(
                action_selection.requested_todo_id
                if action_selection is not None
                else None
            ),
        )
        return payload, False
    except RolloutAppendPreconditionChanged:
        if recovery_refreshes_remaining:
            return payload, True
        fail_heartbeat_receipt(
            payload,
            turn_instance_id=heartbeat_turn_id,
            stall_observation=heartbeat_stall_observation,
            reason="heartbeat admission changed repeatedly before append; retry the same Turn",
        )
        return payload, False
