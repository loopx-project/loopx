"""Execution and heartbeat receipt reconciliation for `loopx quota should-run`."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager, nullcontext
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
from ..rollout_event_log import RolloutAppendPreconditionChanged
from .quota_presentation import is_undeclared_json_quota_caller

if TYPE_CHECKING:
    from .quota_action_selection import RequestedQuotaActionSelection
    from .quota_context import QuotaCommandContext

RolloutEventAppender = Callable[..., dict[str, object]]


@contextmanager
def _native_heartbeat_admission_request(
    *, identity: SettlementIdentity, runtime_root: Path, registry_path: Path,
    goal_ref: Mapping[str, object] | None, goal_admission: Any = None,
    publication: bool = False,
) -> Iterator[dict[str, Any]]:
    from ..control_plane.coordination.shadow_management import shadow_maintenance_lock_target
    from ..control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
    from ..control_plane.quota.accounting_admission import QUOTA_NATIVE_ADMISSION_SCHEMA, quota_accounting_admission
    from ..file_lock import exclusive_cross_runtime_file_lock, exclusive_mutation_file_lock
    from ..rollout_event_log import rollout_event_log_path

    root = runtime_root.expanduser().resolve()
    registry = registry_path.expanduser().resolve()
    admission = goal_admission or FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id=identity.goal_id, planned_goal_ref=goal_ref, agent_id=identity.agent_id)
    with quota_accounting_admission(runtime_root=root, registry_path=registry,
            goal_id=identity.goal_id, goal_ref=goal_ref, operation="native-work-admission",
            handoff_legacy_index=True, native_agent_id=identity.agent_id,
            native_publication=publication) as source_admission, ExitStack() as locks:
        locks.enter_context(nullcontext() if source_admission is not None
            else admission.launch_lifetime(operation="native-work-admission"))
        if publication and (source_admission is None
                or source_admission.get("schema_version") != QUOTA_NATIVE_ADMISSION_SCHEMA):
            locks.enter_context(exclusive_mutation_file_lock(shadow_maintenance_lock_target(root, identity.goal_id),
                timeout_seconds=0, operation="native-work-admission"))
            locks.enter_context(exclusive_cross_runtime_file_lock(rollout_event_log_path(root, identity.goal_id),
                timeout_seconds=0, operation="native-work-admission"))
        yield {"schema_version": "loopx_quota_settlement_readback_request_v0",
            "runtime_root": str(root), "goal_id": identity.goal_id, "agent_id": identity.agent_id,
            "todo_id": identity.todo_id, "turn_instance_id": identity.turn_instance_id,
            "infer_turn_instance_id": False, "allow_unbound_binding": False,
            **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
            **({"source_admission": source_admission, "borrow_source_admission": True}
               if source_admission is not None else {})}


def _read_native_heartbeat_admission(request: Mapping[str, Any]) -> dict[str, Any]:
    from ..control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result
    from ..control_plane.quota.execution_preparation import RefreshTaskLeaseUnavailable

    try:
        result = effect_runtime_result("quota.settlement.read", dict(request), large_local_snapshot=True)
    except EffectRuntimeRemoteError as exc:
        rejected = RefreshTaskLeaseUnavailable(str(exc))
        rejected.code = exc.diagnostic_code
        rejected.payload = {"original_ts_refusal": {
            "kind": exc.error_kind, "code": exc.diagnostic_code,
            "message": str(exc), "details": list(getattr(exc, "details", ())),
        }}
        raise rejected from exc
    if not isinstance(result, dict) or not isinstance(result.get("native_work_admission"), dict):
        raise RefreshTaskLeaseUnavailable("Native work admission returned no typed evidence.")
    return result


def check_fresh_native_heartbeat_prior_closeout(
    *, identity: SettlementIdentity, runtime_root: Path, registry_path: Path,
    goal_ref: Mapping[str, object] | None, goal_admission: Any = None,
) -> dict[str, Any]:
    """Read same-Todo prior debt before preparation; this grants no execution."""
    from ..control_plane.quota.execution_preparation import RefreshTaskLeaseUnavailable

    with _native_heartbeat_admission_request(identity=identity, runtime_root=runtime_root,
            registry_path=registry_path, goal_ref=goal_ref, goal_admission=goal_admission) as request:
        evidence = _read_native_heartbeat_admission({**request,
            "native_work_admission": {"check_prior_closeout_only": True}})["native_work_admission"]
        if evidence.get("prior_closeout_checked") is not True or evidence.get("append") is not False:
            raise RefreshTaskLeaseUnavailable("The original Todo's prior closeout could not be checked.")
        return evidence


def record_fresh_native_heartbeat_receipt(
    decision: Mapping[str, Any], *, identity: SettlementIdentity,
    runtime_root: Path, registry_path: Path, goal_ref: Mapping[str, object] | None,
    goal_admission: Any,
) -> dict[str, Any]:
    """Publish actual native start facts under the existing receipt resources.

    The typed settlement owner alone decides first admission, current proof,
    prior debt and immutable replay. No legacy guard is written before it.
    """
    from ..control_plane.quota.execution_preparation import RefreshTaskLeaseUnavailable
    from ..control_plane.quota.settlement_cli import quota_rollout_details
    from ..rollout_event_log import _append_rollout_event_line, build_rollout_event, rollout_event_log_path

    root = runtime_root.expanduser().resolve()
    details = quota_rollout_details(decision,
        argparse.Namespace(quota_command="should-run", agent_id=identity.agent_id),
        todo_id=identity.todo_id, replan_obligation_id=identity.replan_obligation_id)
    details.update(turn_instance_id=identity.turn_instance_id,
        settlement_effect_id=identity.effect_id, stall_observation="not_applicable")
    log_path = rollout_event_log_path(root, identity.goal_id)
    with _native_heartbeat_admission_request(identity=identity, runtime_root=root,
            registry_path=registry_path, goal_ref=goal_ref, goal_admission=goal_admission,
            publication=True) as base_request:
        request = {**base_request, "native_work_admission": {"details": details}}

        def read() -> dict[str, Any]:
            result = _read_native_heartbeat_admission(request)
            if result["native_work_admission"].get("state") != "ready":
                raise RefreshTaskLeaseUnavailable("Fresh native work admission could not be qualified.")
            return result

        qualified = read()["native_work_admission"]
        if qualified.get("append") is True:
            offered_details = qualified.get("details")
            parent = qualified.get("source_event_id")
            if not isinstance(offered_details, dict) or (parent is not None and not isinstance(parent, str)):
                raise RuntimeError("typed native admission omitted its publication facts")
            event = build_rollout_event(goal_id=identity.goal_id, event_kind="quota_should_run",
                goal_ref=goal_ref, agent_id=identity.agent_id, todo_id=identity.todo_id,
                run_id=identity.turn_instance_id, status=str(decision.get("effective_action") or "should-run"),
                summary=f"Native work admission recorded for turn={identity.turn_instance_id}",
                source_event_id=parent, caused_by=parent, details=offered_details)
            _append_rollout_event_line(log_path, event)
        elif qualified.get("append") is not False:
            raise RuntimeError("typed native admission omitted its append disposition")
        committed = read()
        disposition = committed["native_work_admission"]
        receipt = committed.get("heartbeat_receipt")
        if (disposition.get("append") is not False or not isinstance(receipt, dict)
                or receipt.get("goal_id") != identity.goal_id or receipt.get("agent_id") != identity.agent_id
                or receipt.get("run_id") != identity.turn_instance_id
                or disposition.get("original_prepared") != qualified.get("original_prepared")):
            raise RefreshTaskLeaseUnavailable("Fresh native work admission could not be read back after publication.")
        return disposition


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
    try:
        with recovery_admission_append_precondition(
            payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        ) as precondition:
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
        precondition is not None
        and receipt.get("status")
        != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value
    ):
        raise RolloutAppendPreconditionChanged(
            "A different guard was recorded for this Turn."
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
            reason="recovery admission changed repeatedly before append; retry the same Turn",
        )
        return payload, False
