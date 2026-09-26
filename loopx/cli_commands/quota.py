from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from ..usage_goal import observe_quota_result

from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..capabilities.periodic_report.pending_intent import (
    periodic_report_pending_intent_interaction_hook,
)
from ..capabilities.repository_change_window import (
    repository_delivery_interaction_hook,
)
from ..control_plane.effect_runtime import EffectRuntimeRejected
from ..control_plane.capability_hooks import InteractionProjectionHookRegistration
from ..control_plane.quota.cli_projection import (
    compact_quota_monitor_poll_cli_payload,
    compact_quota_should_run_cli_payload,
)
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.effect_program import SettlementIdentity
from ..control_plane.quota.error_codes import (
    CloseoutQueryUnavailableError,
    QuotaCommandValidationError,
)
from ..control_plane.quota.heartbeat_receipt import (
    attach_uncommitted_heartbeat_receipt,
    fail_heartbeat_receipt,
    find_heartbeat_receipt,
    heartbeat_receipt_view,
)
from ..control_plane.quota.live_decision import build_live_quota_should_run_decision
from ..control_plane.quota.monitor_poll import find_quota_monitor_poll_turn
from ..control_plane.quota.settlement_cli import (
    attach_spend_settlement_result,
    quota_rollout_details,
    quota_rollout_replan_obligation_id,
    quota_rollout_todo_id,
    reconcile_existing_heartbeat_receipt_for_turn,
    render_existing_heartbeat_receipt_payload,
)
from ..control_plane.quota.turn_envelope import build_turn_envelope
from ..presentation.renderers.quota_event_markdown import (
    render_quota_monitor_poll_markdown,
    render_quota_slot_preview_markdown,
)
from ..presentation.renderers.quota_markdown import (
    render_quota_markdown,
    render_quota_scheduler_ack_markdown,
    render_quota_scheduler_failure_markdown,
    render_quota_should_run_markdown,
)
from ..presentation.renderers.turn_envelope_markdown import (
    render_turn_envelope_markdown,
)
from ..quota import (
    build_quota_plan,
    record_quota_monitor_poll,
    spend_quota_slot,
    void_quota_slot,
)
from ..status import collect_status
from ..upgrade import resolve_codex_app_automation_rrule
from .lark_inbox import (
    build_lark_operator_inbox_urgency_projector,
    dispatch_goal_lark_turn_start_hooks,
)
from .quota_action_selection import (
    RequestedQuotaActionSelection,
    commit_requested_action_selection,
    load_requested_quota_action_selection,
    reconcile_requested_quota_action_selection,
)
from .quota_context import (
    QuotaCommandContext,
    prepare_quota_command_context,
    validate_quota_command_context_request,
)
from .quota_failure_report import (
    QUOTA_EVENT_KINDS,
    quota_failure_payload,
    quota_validation_failure_payload,
    should_log_quota,
)
from .quota_host_poll import attach_host_poll_receipt
from .quota_monitor_poll import record_quota_monitor_poll_for_cli
from .quota_registration import (
    register_quota_command as register_quota_command,  # noqa: PLC0414
)
from .quota_reward_memory import (
    attach_reward_memory_ingest_after_spend,
    attach_reward_memory_recall_after_should_run,
)
from .quota_scheduler_followup import build_scheduler_followup_payload

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
RolloutEventAppender = Callable[..., dict[str, object]]


def _effective_spend_turn_instance_id(
    payload: Mapping[str, object],
    *,
    heartbeat_turn_id: str | None,
) -> str | None:
    """Use an exact spend recovery as rollout receipt authority.

    A visible-goal spend may intentionally omit CLI settlement arguments.  When
    quota accounting recovers the exact persisted identity, the receipt must be
    written under that recovered Turn or a replay cannot find the committed
    effect.  Do not project a loose payload field into receipt authority: require
    the typed identity and payload Turn to agree.
    """

    if heartbeat_turn_id:
        return heartbeat_turn_id
    identity_value = payload.get("settlement_identity")
    if not isinstance(identity_value, Mapping):
        return None
    payload_turn_id = str(payload.get("turn_instance_id") or "").strip()
    identity_turn_id = str(identity_value.get("turn_instance_id") or "").strip()
    effect_id = str(identity_value.get("effect_id") or "").strip()
    if not payload_turn_id or payload_turn_id != identity_turn_id or not effect_id:
        return None
    return payload_turn_id


def _quota_renderer(
    args: argparse.Namespace,
) -> Callable[[dict[str, object]], str]:
    command = args.quota_command
    if bool(getattr(args, "turn_envelope", False)):
        return render_turn_envelope_markdown
    return {
        "should-run": render_quota_should_run_markdown,
        "monitor-poll": render_quota_monitor_poll_markdown,
        "scheduler-ack": render_quota_scheduler_ack_markdown,
        "scheduler-ack-current": render_quota_scheduler_ack_markdown,
        "scheduler-fail-current": render_quota_scheduler_failure_markdown,
        "spend-slot": render_quota_slot_preview_markdown,
        "void-slot": render_quota_slot_preview_markdown,
    }.get(command, render_quota_markdown)


def _record_automatic_heartbeat_stall(
    payload: dict[str, object],
    status_payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    context: QuotaCommandContext,
    interaction_projection_hooks: tuple[InteractionProjectionHookRegistration, ...],
    action_selection: RequestedQuotaActionSelection,
    cache_metadata: object,
) -> tuple[dict[str, object], dict[str, object], object, str]:
    """Commit and reproject the automatic no-spend heartbeat observation."""

    turn_id = context.heartbeat_turn_id
    if turn_id is None:
        return payload, status_payload, cache_metadata, "not_applicable"
    existing_stall = find_quota_monitor_poll_turn(
        context.runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_id,
    )
    if (
        payload.get("effective_action")
        != EffectiveAction.MONITOR_QUIET_SKIP.value
        and existing_stall is None
    ):
        return payload, status_payload, cache_metadata, "not_applicable"
    poll = record_quota_monitor_poll(
        status_payload,
        goal_id=args.goal_id,
        registry_path=registry_path,
        execute=True,
        source="heartbeat",
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        turn_instance_id=turn_id,
        scheduler_execution_context=context.scheduler_context,
        operator_inbox_urgency_projector=context.operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
    )
    if not poll.get("ok"):
        raise RuntimeError(
            "heartbeat stall observation writeback failed: "
            f"{poll.get('reason') or 'missing follow-up quota decision'}"
        )
    reloaded = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        scan_roots=context.scan_roots,
        limit=context.status_limit,
        goal_id=context.status_goal_id,
        available_capabilities=args.available_capabilities,
    )
    rebuilt = build_live_quota_should_run_decision(
        reloaded,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        include_scheduler_detail="scheduler" in context.detail_sections,
        include_agent_todo_detail=(
            "agent-todos" in context.detail_sections
            and not bool(getattr(args, "turn_envelope", False))
        ),
        codex_app_current_rrule=args.app_automation_current_rrule,
        registry_path=registry_path,
        runtime_root=context.runtime_root,
        host_observation_resolver=resolve_codex_app_automation_rrule,
        scheduler_execution_context=context.scheduler_context,
        operator_inbox_urgency_projector=context.operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        receipt_bound_todo_id=action_selection.receipt_bound_todo_id,
        receipt_bound_replan_obligation_id=(
            action_selection.receipt_bound_replan_obligation_id
        ),
        retained_action_selection_todo_id=(
            action_selection.retained_todo_id_for_decision
        ),
        turn_instance_id=turn_id,
        interaction_projection_hooks=interaction_projection_hooks,
    )
    rebuilt["heartbeat_stall_writeback"] = {
        "turn_instance_id": turn_id,
        "status": "replayed" if poll.get("replayed") else "appended",
        "generated_at": poll.get("generated_at"),
    }
    return (
        rebuilt,
        reloaded,
        None,
        "replayed" if poll.get("replayed") else "appended",
    )


def _dispatch_quota_turn_start_hooks(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> tuple[dict[str, object] | None, bool]:
    validate_quota_command_context_request(args)
    if args.quota_command != "should-run":
        return None, False
    dispatch = dispatch_goal_lark_turn_start_hooks(
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
    )
    if args.agent_id:
        from ..capabilities.manager_context import turn_start_hook
        from ..control_plane.agents.capability_memory import (
            extend_turn_start_dispatch as extend_capability_memory_dispatch,
        )
        from ..control_plane.capability_hooks import dispatch_turn_start_hooks
        from ..history import load_registry
        from ..paths import resolve_runtime_root
        root = resolve_runtime_root(load_registry(registry_path), runtime_root_arg, registry_path=registry_path)
        dispatch = extend_capability_memory_dispatch(
            dispatch,
            registry_path=registry_path,
            runtime_root=root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            available=args.available_capabilities,
        )
        context_dispatch = dispatch_turn_start_hooks((turn_start_hook(root, registry_path, args.goal_id, args.agent_id),))
        dispatch = dict(dispatch)
        for key in ("results", "required_reads", "failures"):
            dispatch[key] = list(dispatch.get(key) or []) + list(context_dispatch.get(key) or [])
        for key in ("registered_count", "invoked_count"):
            dispatch[key] = int(dispatch.get(key) or 0) + int(context_dispatch.get(key) or 0)
        from ..capabilities.periodic_report.cadence_runtime import (
            extend_cadence_turn_start_dispatch,
        )
        dispatch = extend_cadence_turn_start_dispatch(dispatch, registry_path=registry_path,
            runtime_root=root, goal_id=args.goal_id, agent_id=args.agent_id)
    local_private_state_mutated = any(
        isinstance(result, Mapping)
        and result.get("local_private_state_mutated") is True
        for result in (dispatch.get("results") or [])
    )
    return dispatch, local_private_state_mutated


def _attach_turn_start_hook_dispatch(
    payload: dict[str, object],
    dispatch: Mapping[str, object] | None,
) -> None:
    if dispatch and (dispatch.get("registered_count") or dispatch.get("failures")):
        payload["turn_start_capability_hook_dispatch"] = dict(dispatch)



def _project_quota_cli_payload(
    payload: dict[str, object],
    args: argparse.Namespace,
    detail_sections: frozenset[str],
    scheduler_context: object,
) -> dict[str, object]:
    """Project already-decided facts; preserve typed failures on envelope rejection.

    The envelope is an additive hot-path view over a decided payload. A typed
    validation/failure payload has no interaction contract to project, so a
    renderer rejection keeps the typed diagnostic itself (with the skip reason)
    instead of masking it with a crash (issue #3687).
    """
    if not bool(getattr(args, "turn_envelope", False)):
        if args.quota_command == "should-run":
            return compact_quota_should_run_cli_payload(
                payload,
                include_todo_summary_detail="agent-todos" in detail_sections,
                include_user_todo_summary_detail="user-todos" in detail_sections,
                include_goal_boundary_detail="goal-boundary" in detail_sections,
                include_vision_detail="vision" in detail_sections,
            )
        if args.quota_command == "monitor-poll":
            return compact_quota_monitor_poll_cli_payload(
                payload, include_decision_detail="decisions" in detail_sections,
            )
        return payload
    try:
        return build_turn_envelope(
            payload,
            scheduler_execution_context=scheduler_context,
        )
    except EffectRuntimeRejected as envelope_error:
        degraded = dict(payload)
        degraded["turn_envelope_skipped"] = str(envelope_error)[:200]
        return degraded

def handle_quota_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    append_cli_rollout_event: RolloutEventAppender,
) -> int:
    usage_quota_started = time.time_ns() // 1_000_000
    heartbeat_turn_id: str | None = None
    heartbeat_receipt_existing: dict[str, object] | None = None
    heartbeat_receipt_existing_status = "replayed"
    heartbeat_receipt_existing_appended = False
    heartbeat_receipt_ready = False
    action_selection_preflight_failed = False
    closeout_query_unavailable = False
    action_selection: RequestedQuotaActionSelection | None = None
    heartbeat_stall_observation = "not_evaluated"
    detail_sections: frozenset[str] = frozenset()
    context: QuotaCommandContext | None = None
    try:
        turn_start_hook_dispatch, turn_start_mutated = _dispatch_quota_turn_start_hooks(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
        )
        context = prepare_quota_command_context(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            status_collector=collect_status,
            operator_inbox_urgency_projector_factory=(
                build_lark_operator_inbox_urgency_projector
            ),
            force_projection_refresh=turn_start_mutated,
        )
        (
            heartbeat_turn_id,
            detail_sections,
            runtime_root,
            scan_roots,
            status_limit,
            status_goal_id,
            status_payload,
            cache_metadata,
            scheduler_context,
            operator_inbox_urgency_projector,
        ) = (
            context.heartbeat_turn_id,
            context.detail_sections,
            context.runtime_root,
            context.scan_roots,
            context.status_limit,
            context.status_goal_id,
            context.status_payload,
            context.cache_metadata,
            context.scheduler_context,
            context.operator_inbox_urgency_projector,
        )
        if args.quota_command == "should-run":
            interaction_projection_hooks = (
                repository_delivery_interaction_hook(repo_path=Path.cwd()),
                periodic_report_pending_intent_interaction_hook(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                ),
            )
            action_selection = load_requested_quota_action_selection(
                args,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
            )
            heartbeat_receipt_existing = action_selection.receipt
            payload = build_live_quota_should_run_decision(
                status_payload,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                available_capabilities=args.available_capabilities,
                include_scheduler_detail="scheduler" in detail_sections,
                include_agent_todo_detail=(
                    "agent-todos" in detail_sections
                    and not bool(getattr(args, "turn_envelope", False))
                ),
                codex_app_current_rrule=args.app_automation_current_rrule,
                registry_path=registry_path,
                runtime_root=runtime_root,
                host_observation_resolver=resolve_codex_app_automation_rrule,
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                bounded_research_frontier_projector=(
                    project_live_explore_composition_frontier
                ),
                receipt_bound_todo_id=action_selection.receipt_bound_todo_id,
                requested_action_todo_id=(
                    action_selection.requested_todo_id_for_decision
                ),
                receipt_bound_replan_obligation_id=(
                    action_selection.receipt_bound_replan_obligation_id
                ),
                retained_action_selection_todo_id=(
                    action_selection.retained_todo_id_for_decision
                ),
                turn_instance_id=heartbeat_turn_id,
                interaction_projection_hooks=interaction_projection_hooks,
                turn_start_hook_dispatch=turn_start_hook_dispatch,
            )
            _attach_turn_start_hook_dispatch(payload, turn_start_hook_dispatch)
            action_selection_preflight = (
                reconcile_requested_quota_action_selection(
                    payload,
                    args,
                    registry_path=registry_path,
                    context=context,
                    selection=action_selection,
                )
            )
            action_selection_preflight_failed = action_selection_preflight.rejected
            if action_selection_preflight.rejected:
                heartbeat_receipt_existing = action_selection_preflight.receipt
                heartbeat_receipt_existing_status = (
                    action_selection_preflight.receipt_status
                )
                heartbeat_receipt_existing_appended = (
                    action_selection_preflight.receipt_appended
                )
            if heartbeat_turn_id:
                if action_selection_preflight_failed:
                    heartbeat_receipt_ready = True
                elif heartbeat_receipt_existing:
                    (
                        heartbeat_receipt_existing,
                        heartbeat_receipt_existing_status,
                        heartbeat_receipt_existing_appended,
                        heartbeat_stall_observation,
                        heartbeat_receipt_ready,
                    ) = reconcile_existing_heartbeat_receipt_for_turn(
                        payload,
                        args,
                        runtime_root=runtime_root,
                        turn_instance_id=heartbeat_turn_id,
                        existing=heartbeat_receipt_existing,
                    )
                else:
                    (
                        payload,
                        status_payload,
                        cache_metadata,
                        heartbeat_stall_observation,
                    ) = _record_automatic_heartbeat_stall(
                        payload,
                        status_payload,
                        args,
                        registry_path=registry_path,
                        runtime_root_arg=runtime_root_arg,
                        context=context,
                        interaction_projection_hooks=interaction_projection_hooks,
                        action_selection=action_selection,
                        cache_metadata=cache_metadata,
                    )
                    heartbeat_receipt_ready = True
        elif args.quota_command == "monitor-poll":
            payload = record_quota_monitor_poll_for_cli(
                args,
                status_payload=status_payload,
                registry_path=registry_path,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                monitor_poll_recorder=record_quota_monitor_poll,
                status_reloader=lambda: collect_status(
                    registry_path=registry_path,
                    runtime_root_override=runtime_root_arg,
                    scan_roots=scan_roots,
                    limit=status_limit,
                    goal_id=status_goal_id,
                    available_capabilities=args.available_capabilities,
                ),
            )
        elif args.quota_command in {
            "scheduler-ack",
            "scheduler-ack-current",
            "scheduler-fail-current",
        }:
            payload = build_scheduler_followup_payload(
                status_payload,
                args,
                registry_path=registry_path,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                scheduler_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            )
        elif args.quota_command == "spend-slot":
            payload = spend_quota_slot(
                status_payload,
                goal_id=args.goal_id,
                slots=args.slots,
                execute=bool(args.execute),
                source=args.source,
                agent_id=args.agent_id,
                available_capabilities=args.available_capabilities,
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                todo_id=args.todo_id,
                turn_instance_id=heartbeat_turn_id,
                replan_obligation_id=args.replan_obligation_id,
            )
        elif args.quota_command == "void-slot":
            payload = void_quota_slot(
                status_payload,
                goal_id=args.goal_id,
                voided_run_generated_at=args.void_generated_at,
                execute=bool(args.execute),
                source=args.source,
                reason_summary=args.reason_summary,
                agent_id=args.agent_id,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            )
        else:
            payload = build_quota_plan(status_payload, mode=args.quota_command)
        if cache_metadata:
            payload["status_projection_cache"] = cache_metadata
    except QuotaCommandValidationError as exc:
        # Only typed CLI validation diagnostics are public-safe by contract.
        payload = quota_validation_failure_payload(
            args,
            exc,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
        )
    except Exception as exc:  # noqa: BLE001 - CLI fail-safe boundary; error_code is typed below.
        closeout_query_unavailable = isinstance(exc, CloseoutQueryUnavailableError)
        payload = quota_failure_payload(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            error=exc,
        )
    if should_log_quota(args.quota_command, payload):
        spend_turn_instance_id = _effective_spend_turn_instance_id(
            payload,
            heartbeat_turn_id=heartbeat_turn_id,
        )
        rollout_todo_id = quota_rollout_todo_id(payload, args)
        rollout_replan_obligation_id = quota_rollout_replan_obligation_id(payload, args)
        rollout_details = quota_rollout_details(
            payload,
            args,
            todo_id=rollout_todo_id,
            replan_obligation_id=rollout_replan_obligation_id,
        )
        if heartbeat_turn_id and args.quota_command == "should-run":
            if action_selection_preflight_failed or closeout_query_unavailable:
                if heartbeat_receipt_existing:
                    render_existing_heartbeat_receipt_payload(
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
            elif not heartbeat_receipt_ready:
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
            elif heartbeat_receipt_existing:
                render_existing_heartbeat_receipt_payload(
                    payload,
                    receipt=heartbeat_receipt_existing,
                    turn_instance_id=heartbeat_turn_id,
                    status=heartbeat_receipt_existing_status,
                    appended=heartbeat_receipt_existing_appended,
                )
                commit_requested_action_selection(
                    payload,
                    requested_todo_id=(
                        action_selection.requested_todo_id
                        if action_selection is not None
                        else None
                    ),
                )
            else:
                settlement_identity = (
                    SettlementIdentity(
                        goal_id=args.goal_id,
                        agent_id=args.agent_id,
                        todo_id=rollout_todo_id,
                        turn_instance_id=heartbeat_turn_id,
                        replan_obligation_id=rollout_replan_obligation_id,
                    )
                    if rollout_todo_id or rollout_replan_obligation_id
                    else None
                )
                rollout_details.update(
                    {
                        "turn_instance_id": heartbeat_turn_id,
                        "stall_observation": heartbeat_stall_observation,
                        "settlement_effect_id": (
                            settlement_identity.effect_id
                            if settlement_identity is not None
                            else ""
                        ),
                    }
                )
                append_cli_rollout_event(
                    payload,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    event_kind="quota_should_run",
                    agent_id=args.agent_id,
                    run_id=heartbeat_turn_id,
                    status=str(
                        payload.get("effective_action")
                        or payload.get("decision")
                        or "should-run"
                    ),
                    summary=(
                        "heartbeat quota receipt committed for "
                        f"turn={heartbeat_turn_id} stall={heartbeat_stall_observation}"
                    ),
                    details=rollout_details,
                    allow_failed=True,
                    idempotency_fields=["goal_id", "event_kind", "agent_id", "run_id"],
                )
                receipt = find_heartbeat_receipt(
                    runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    turn_instance_id=heartbeat_turn_id,
                )
                if receipt:
                    rollout_event_value = payload.get("rollout_event")
                    rollout_event: Mapping[str, object] = (
                        rollout_event_value
                        if isinstance(rollout_event_value, Mapping)
                        else {}
                    )
                    payload["heartbeat_receipt"] = heartbeat_receipt_view(
                        receipt,
                        turn_instance_id=heartbeat_turn_id,
                        status="committed"
                        if rollout_event.get("appended")
                        else "replayed",
                    )
                    commit_requested_action_selection(
                        payload,
                        requested_todo_id=(
                            action_selection.requested_todo_id
                            if action_selection is not None
                            else None
                        ),
                    )
                else:
                    fail_heartbeat_receipt(
                        payload,
                        turn_instance_id=heartbeat_turn_id,
                        stall_observation=heartbeat_stall_observation,
                        reason=(
                            "heartbeat receipt append could not be read back; retry "
                            "quota should-run with the same --turn-instance-id"
                        ),
                    )
        else:
            append_cli_rollout_event(
                payload,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                event_kind=QUOTA_EVENT_KINDS[args.quota_command],
                agent_id=args.agent_id,
                todo_id=rollout_todo_id,
                run_id=(
                    spend_turn_instance_id
                    if args.quota_command == "spend-slot"
                    else None
                ),
                status=str(
                    payload.get("effective_action")
                    or payload.get("decision")
                    or payload.get("mode")
                    or args.quota_command
                ),
                summary=(
                    f"quota {args.quota_command} decision="
                    f"{payload.get('decision') or payload.get('mode')} "
                    f"state={payload.get('state') or ''}"
                ),
                details=rollout_details,
                allow_failed=args.quota_command == "should-run",
            )
            if args.quota_command == "spend-slot" and spend_turn_instance_id:
                attach_spend_settlement_result(
                    payload,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    todo_id=rollout_todo_id,
                    turn_instance_id=spend_turn_instance_id,
                    replan_obligation_id=rollout_replan_obligation_id,
                )
                attach_reward_memory_ingest_after_spend(
                    payload,
                    execute=bool(args.execute),
                    runtime_root=runtime_root,
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                    todo_id=rollout_todo_id,
                    turn_instance_id=spend_turn_instance_id,
                    replan_obligation_id=rollout_replan_obligation_id,
                )
    attach_reward_memory_recall_after_should_run(
        payload,
        quota_command=args.quota_command,
        heartbeat_turn_id=heartbeat_turn_id,
        registry_path=registry_path,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
    )
    if context is not None:
        observe_quota_result(
            args, payload, registry_path=registry_path, runtime_root=context.runtime_root,
            turn_id=_effective_spend_turn_instance_id(payload, heartbeat_turn_id=heartbeat_turn_id),
            started_at=usage_quota_started,
        )
    payload = _project_quota_cli_payload(
        payload, args, detail_sections,
        context.scheduler_context if context is not None else None,
    )
    if args.quota_command == "should-run" and context is not None:
        attach_host_poll_receipt(
            context.status_payload,
            args,
            payload,
            registry_path=registry_path,
        )
    print_payload(payload, args.format, _quota_renderer(args))
    return 0 if payload.get("ok") else 1
