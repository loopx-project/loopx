from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..control_plane.coordination.local_authority import CanonicalAuthorityRequired
from ..control_plane.quota.error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
    QuotaCommandValidationError,
)
from ..usage_goal import observe_quota_result as observe_quota_result  # noqa: PLC0414
from .quota_capture import handle_native_context_command, prepare_quota_capture
from .quota_presentation import (
    _project_quota_cli_payload as _project_quota_cli_payload,
    _quota_renderer as _quota_renderer,
    project_scheduler_observation as project_scheduler_observation,
)
from .quota_registration import (
    register_quota_command as register_quota_command,  # noqa: PLC0414
)
from .quota_reward_memory import attach_reward_memory_ingest_after_spend

if TYPE_CHECKING:
    from ..control_plane.scheduler.execution_context import (
        SchedulerExecutionContextResolution,
    )
    from .quota_action_selection import RequestedQuotaActionSelection
    from .quota_context import QuotaCommandContext
    from .quota_should_run import ShouldRunExecutionState

# Native context and replan-view routes return before any authority read, so
# authority, decision, and capability owners load on first use. Call sites
# resolve them through ``_export`` so module-level patches stay effective.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    name: (module, name)
    for module, names in {
        "loopx.capabilities.explore.composition_frontier": (
            "project_live_explore_composition_frontier",
        ),
        "loopx.capabilities.periodic_report.pending_intent": (
            "periodic_report_pending_intent_interaction_hook",
        ),
        "loopx.capabilities.repository_change_window": (
            "repository_delivery_interaction_hook",
        ),
        "loopx.control_plane.quota.live_decision": (
            "build_live_quota_should_run_decision",
        ),
        "loopx.control_plane.quota.monitor_poll": ("find_quota_monitor_poll_turn",),
        "loopx.control_plane.quota.settlement_cli": (
            "attach_spend_settlement_result",
            "quota_rollout_details",
            "quota_rollout_replan_obligation_id",
            "quota_rollout_todo_id",
            "reconcile_existing_heartbeat_receipt_for_turn",
            "render_existing_heartbeat_receipt_payload",
        ),
        "loopx.quota": (
            "build_quota_plan",
            "record_quota_monitor_poll",
            "spend_quota_slot",
            "void_quota_slot",
        ),
        "loopx.status": ("collect_status",),
        "loopx.upgrade": ("resolve_codex_app_automation_rrule",),
        "loopx.cli_commands.lark_inbox": (
            "build_lark_operator_inbox_urgency_projector",
        ),
        "loopx.cli_commands.quota_action_selection": (
            "commit_requested_action_selection",
            "load_requested_quota_action_selection",
            "inline_action_selection_reentry_args",
            "reconcile_requested_quota_action_selection",
        ),
        "loopx.cli_commands.quota_context": (
            "prepare_quota_command_context",
            "validate_quota_command_context_request",
        ),
        "loopx.cli_commands.quota_failure_report": (
            "QUOTA_EVENT_KINDS",
            "captured_quota_failure_view",
            "quota_decision_transport_failure_payload",
            "quota_failure_payload",
            "quota_validation_failure_payload",
            "should_log_quota",
        ),
        "loopx.cli_commands.quota_host_poll": ("attach_host_poll_receipt",),
        "loopx.cli_commands.quota_monitor_poll": (
            "record_quota_monitor_poll_for_cli",
            "recover_quota_monitor_poll_for_cli",
        ),
        "loopx.cli_commands.quota_scheduler_followup": (
            "build_scheduler_followup_payload",
        ),
    }.items()
    for name in names
}
_LAZY_EXPORTS["_record_automatic_heartbeat_stall"] = (
    "loopx.cli_commands.quota_monitor_poll",
    "record_automatic_heartbeat_stall",
)


def __getattr__(name: str) -> Any:
    owner = _LAZY_EXPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(owner[0]), owner[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


def _export(name: str) -> Any:
    return getattr(sys.modules[__name__], name)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
RolloutEventAppender = Callable[..., dict[str, object]]


def _quota_goal_ref(
    args: argparse.Namespace,
    *,
    registry_path: Path,
) -> dict[str, str] | None:
    goal_id = str(getattr(args, "goal_id", None) or "").strip()
    if not goal_id:
        return None
    goal_instance_id = str(
        getattr(args, "goal_instance_id", None) or ""
    ).strip()
    if goal_instance_id:
        from ..control_plane.goals.source_session_registry_state import exact_goal_ref

        return exact_goal_ref(goal_id, goal_instance_id)
    if args.quota_command not in {
        "should-run",
        "receipt",
        "monitor-poll",
        "scheduler-ack",
        "scheduler-ack-current",
        "scheduler-fail-current",
        "spend-slot",
        "void-slot",
    }:
        return None
    from ..control_plane.goals.first_party_host_admission import (
        capture_first_party_host_goal_ref,
    )

    return capture_first_party_host_goal_ref(
        registry_path=registry_path,
        goal_id=goal_id,
    )


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


def _dispatch_quota_turn_start_hooks(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> tuple[dict[str, object] | None, bool]:
    from .quota_should_run import dispatch_quota_turn_start_hooks

    def _dispatch_lark(
        *,
        registry_path: Path,
        runtime_root_arg: str | None,
        goal_id: str | None,
        agent_id: str | None,
    ) -> dict[str, object]:
        from .lark_inbox import dispatch_goal_lark_turn_start_hooks

        return dispatch_goal_lark_turn_start_hooks(
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_id=goal_id,
            agent_id=agent_id,
        )

    return dispatch_quota_turn_start_hooks(
        args,
        quota_module=sys.modules[__name__],
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        lark_turn_start_hook_dispatcher=_dispatch_lark,
    )


def _prepare_quota_command_execution(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> tuple[
    dict[str, str] | None,
    dict[str, object] | None,
    QuotaCommandContext,
]:
    goal_ref = _quota_goal_ref(args, registry_path=registry_path)
    turn_start_hook_dispatch, turn_start_mutated = _dispatch_quota_turn_start_hooks(
        args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    context = _export("prepare_quota_command_context")(
        args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        status_collector=_export("collect_status"),
        operator_inbox_urgency_projector_factory=(
            _export("build_lark_operator_inbox_urgency_projector")
        ),
        force_projection_refresh=turn_start_mutated,
    )
    return goal_ref, turn_start_hook_dispatch, context


def _prepare_quota_result_context(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    attach_recall: bool = True,
) -> tuple[dict[str, object], dict[str, Any]]:
    from .quota_result_delivery import prepare_quota_result_context

    return prepare_quota_result_context(
        payload,
        args,
        registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id,
        attach_recall=attach_recall,
    )


def _emit_quota_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    usage_quota_started: int,
    capture_directory: Path | None,
    detail_sections: frozenset[str],
    print_payload: PrintPayload,
) -> int:
    from .quota_result_delivery import emit_quota_result

    return emit_quota_result(
        payload,
        args,
        quota_module=sys.modules[__name__],
        context=context,
        registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id,
        usage_quota_started=usage_quota_started,
        capture_directory=capture_directory,
        detail_sections=detail_sections,
        print_payload=print_payload,
    )


def _record_non_guard_quota_event(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    context: QuotaCommandContext | None,
    goal_ref: dict[str, str] | None,
    rollout_todo_id: str | None,
    rollout_replan_obligation_id: str | None,
    spend_turn_instance_id: str | None,
    rollout_details: dict[str, Any],
    append_cli_rollout_event: RolloutEventAppender,
) -> None:
    """Record the CLI event before settlement readback and optional memory ingest."""
    append_cli_rollout_event(
        payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        event_kind=_export("QUOTA_EVENT_KINDS")[args.quota_command],
        goal_ref=goal_ref,
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
        idempotency_fields=(
            ["goal_id", "event_kind", "agent_id", "run_id"]
            + (["todo_id"] if rollout_todo_id else [])
            + (["goal_ref"] if goal_ref is not None else [])
            if args.quota_command == "spend-slot" and spend_turn_instance_id
            else None
        ),
    )
    if args.quota_command == "spend-slot" and spend_turn_instance_id:
        assert context is not None  # A settlement requires a prepared authority context.
        readback = _export("attach_spend_settlement_result")(
            payload,
            runtime_root=context.runtime_root,
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            todo_id=rollout_todo_id,
            turn_instance_id=spend_turn_instance_id,
            replan_obligation_id=rollout_replan_obligation_id,
            goal_ref=goal_ref,
        )
        attach_reward_memory_ingest_after_spend(
            payload,
            execute=bool(args.execute),
            runtime_root=context.runtime_root,
            registry_path=registry_path,
            readback=readback,
        )


def handle_quota_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    append_cli_rollout_event: RolloutEventAppender,
    capture_directory: Path | None = None,
    retained_action_selection_todo_id: str | None = None,
) -> int:
    if args.quota_command == "receipt":
        from .quota_receipt import read_quota_receipt, render_quota_receipt

        payload = read_quota_receipt(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_ref_resolver=lambda: _quota_goal_ref(args, registry_path=registry_path),
        )
        print_payload(payload, args.format, render_quota_receipt)
        return 0 if payload["ok"] else 1
    native_result = handle_native_context_command(
        args,
        print_payload=print_payload,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    if native_result is not None:
        return native_result
    limit = getattr(args, "max_page_output_bytes", None)

    def execute() -> int:
        return _execute_quota_command(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            print_payload=print_payload,
            append_cli_rollout_event=append_cli_rollout_event,
            capture_directory=capture_directory,
            retained_action_selection_todo_id=retained_action_selection_todo_id,
        )

    if args.quota_command != "should-run" or args.format != "json" or limit is None:
        return execute()
    from .quota_failure_report import run_with_quota_failure_reader

    return run_with_quota_failure_reader(args, limit, print_payload=print_payload, run=execute)


def _execute_quota_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    append_cli_rollout_event: RolloutEventAppender,
    capture_directory: Path | None = None,
    retained_action_selection_todo_id: str | None = None,
    recovery_refreshes_remaining: int = 3,
) -> int:
    usage_quota_started = time.time_ns() // 1_000_000
    heartbeat_turn_id: str | None = None
    heartbeat_receipt_existing: dict[str, object] | None = None
    heartbeat_receipt_existing_status = "replayed"
    heartbeat_receipt_existing_appended = False
    heartbeat_receipt_ready = False
    upgrade_recovery_wait = False
    action_selection_preflight_failed = False
    closeout_query_unavailable = False
    action_selection: RequestedQuotaActionSelection | None = None
    heartbeat_stall_observation = "not_evaluated"
    detail_sections: frozenset[str] = frozenset()
    context: QuotaCommandContext | None = None
    scheduler_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ) = None
    goal_ref: dict[str, str] | None = None
    status_payload: Mapping[str, object] | None = None
    reentry: tuple[argparse.Namespace, str | None] | None = None
    should_run_state: ShouldRunExecutionState | None = None
    try:
        capture_directory = prepare_quota_capture(args, capture_directory)
        goal_ref, turn_start_hook_dispatch, context = (
            _prepare_quota_command_execution(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
        )
        heartbeat_turn_id = context.heartbeat_turn_id
        detail_sections = context.detail_sections
        runtime_root = context.runtime_root
        status_limit = context.status_limit
        status_goal_id = context.status_goal_id
        status_payload = context.status_payload
        cache_metadata = context.cache_metadata
        scheduler_context = context.scheduler_context
        operator_inbox_urgency_projector = context.operator_inbox_urgency_projector
        if args.quota_command == "should-run":
            from .quota_should_run import (
                ShouldRunExecutionState,
                execute_quota_should_run,
            )

            should_run_state = ShouldRunExecutionState(
                status_payload=status_payload,
                cache_metadata=cache_metadata,
            )
            execute_quota_should_run(
                args,
                quota_module=sys.modules[__name__],
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                context=context,
                goal_ref=goal_ref,
                turn_start_hook_dispatch=turn_start_hook_dispatch,
                retained_action_selection_todo_id=retained_action_selection_todo_id,
                state=should_run_state,
            )
            assert should_run_state.payload is not None
            payload = should_run_state.payload
            status_payload = should_run_state.status_payload
            cache_metadata = should_run_state.cache_metadata
        elif args.quota_command == "monitor-poll":
            payload = _export("record_quota_monitor_poll_for_cli")(
                args,
                status_payload=status_payload,
                registry_path=registry_path,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                goal_ref=goal_ref,
                monitor_poll_recorder=_export("record_quota_monitor_poll"),
                status_reloader=lambda: _export("collect_status")(
                    registry_path=registry_path,
                    runtime_root_override=runtime_root_arg,
                    limit=status_limit,
                    goal_id=status_goal_id,
                    available_capabilities=args.available_capabilities,
                    include_presentation_projections=False,
                ),
            )
        elif args.quota_command in {
            "scheduler-ack",
            "scheduler-ack-current",
            "scheduler-fail-current",
        }:
            payload = _export("build_scheduler_followup_payload")(
                status_payload,
                args,
                registry_path=registry_path,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                scheduler_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                goal_ref=goal_ref,
            )
        elif args.quota_command == "spend-slot":
            payload = _export("spend_quota_slot")(
                status_payload,
                goal_id=args.goal_id,
                slots=args.slots,
                execute=bool(args.execute),
                source=args.source,
                agent_id=args.agent_id,
                workspace_path=(
                    Path(args.delivery_workspace_path).expanduser()
                    if getattr(args, "delivery_workspace_path", None)
                    else None
                ),
                available_capabilities=args.available_capabilities,
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                todo_id=args.todo_id,
                turn_instance_id=heartbeat_turn_id,
                replan_obligation_id=args.replan_obligation_id,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
        elif args.quota_command == "void-slot":
            payload = _export("void_quota_slot")(
                status_payload,
                goal_id=args.goal_id,
                voided_run_generated_at=args.void_generated_at,
                execute=bool(args.execute),
                source=args.source,
                reason_summary=args.reason_summary,
                agent_id=args.agent_id,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                registry_path=registry_path,
                goal_ref=goal_ref,
            )
        else:
            payload = _export("build_quota_plan")(status_payload, mode=args.quota_command)
        if cache_metadata:
            payload["status_projection_cache"] = cache_metadata
    except CanonicalAuthorityRequired as exc:
        payload = _export("recover_quota_monitor_poll_for_cli")(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            error=exc,
            goal_ref_loader=lambda: _quota_goal_ref(args, registry_path=registry_path),
        )
    except QuotaCommandValidationError as exc:
        # Only typed CLI validation diagnostics are public-safe by contract.
        payload = _export("quota_validation_failure_payload")(
            args,
            exc,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
        )
    except Exception as exc:  # noqa: BLE001 - CLI fail-safe boundary; error_code is typed below.
        closeout_query_unavailable = isinstance(exc, CloseoutQueryUnavailableError)
        # A rejected rebind performed no receipt write. Preserve the committed
        # guard and the identity diagnostic instead of calling it failed IO.
        if isinstance(exc, HeartbeatReceiptIdentityConflictError):
            action_selection_preflight_failed = True
        payload = _export("quota_failure_payload")(
            args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            error=exc,
        )
    if should_run_state is not None:
        action_selection = should_run_state.action_selection
        action_selection_preflight_failed = (
            action_selection_preflight_failed
            or should_run_state.action_selection_preflight_failed
        )
        heartbeat_receipt_existing = should_run_state.heartbeat_receipt_existing
        heartbeat_receipt_existing_status = (
            should_run_state.heartbeat_receipt_existing_status
        )
        heartbeat_receipt_existing_appended = (
            should_run_state.heartbeat_receipt_existing_appended
        )
        heartbeat_stall_observation = should_run_state.heartbeat_stall_observation
        heartbeat_receipt_ready = should_run_state.heartbeat_receipt_ready
        upgrade_recovery_wait = should_run_state.upgrade_recovery_wait
        reentry = should_run_state.reentry
    if reentry is not None:
        # The next evaluation owns its collection and publication failures.
        # This frame may not yet know about a receipt committed by that evaluation.
        reentry_args, retained_todo_id = reentry
        return _execute_quota_command(
            reentry_args,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            print_payload=print_payload,
            append_cli_rollout_event=append_cli_rollout_event,
            capture_directory=capture_directory,
            retained_action_selection_todo_id=retained_todo_id,
            recovery_refreshes_remaining=recovery_refreshes_remaining,
        )
    preview = args.quota_command == "should-run" and getattr(args, "dry_run", False)
    if not preview and _export("should_log_quota")(args.quota_command, payload):
        spend_turn_instance_id = _effective_spend_turn_instance_id(
            payload,
            heartbeat_turn_id=heartbeat_turn_id,
        )
        rollout_todo_id = _export("quota_rollout_todo_id")(payload, args)
        rollout_replan_obligation_id = _export("quota_rollout_replan_obligation_id")(
            payload, args
        )
        rollout_details = _export("quota_rollout_details")(
            payload,
            args,
            todo_id=rollout_todo_id,
            replan_obligation_id=rollout_replan_obligation_id,
            scheduler_execution_context=scheduler_context,
            status_payload=status_payload,
        )
        if heartbeat_turn_id and args.quota_command == "should-run":
            from .quota_should_run import record_should_run_heartbeat_receipt

            payload, retry_execution = record_should_run_heartbeat_receipt(
                payload,
                args,
                quota_module=sys.modules[__name__],
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
                append_cli_rollout_event=append_cli_rollout_event,
                heartbeat_turn_id=heartbeat_turn_id,
                rollout_todo_id=rollout_todo_id,
                rollout_replan_obligation_id=rollout_replan_obligation_id,
                rollout_details=rollout_details,
                action_selection=action_selection,
                action_selection_preflight_failed=action_selection_preflight_failed,
                closeout_query_unavailable=closeout_query_unavailable,
                heartbeat_receipt_existing=heartbeat_receipt_existing,
                heartbeat_receipt_existing_status=heartbeat_receipt_existing_status,
                heartbeat_receipt_existing_appended=heartbeat_receipt_existing_appended,
                heartbeat_stall_observation=heartbeat_stall_observation,
                heartbeat_receipt_ready=heartbeat_receipt_ready,
                upgrade_recovery_wait=upgrade_recovery_wait,
                recovery_refreshes_remaining=recovery_refreshes_remaining,
            )
            if retry_execution:
                return _execute_quota_command(
                    args,
                    registry_path=registry_path,
                    runtime_root_arg=runtime_root_arg,
                    print_payload=print_payload,
                    append_cli_rollout_event=append_cli_rollout_event,
                    capture_directory=capture_directory,
                    retained_action_selection_todo_id=retained_action_selection_todo_id,
                    recovery_refreshes_remaining=recovery_refreshes_remaining - 1,
                )
        else:
            _record_non_guard_quota_event(
                payload,
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                context=context,
                goal_ref=goal_ref,
                rollout_todo_id=rollout_todo_id,
                rollout_replan_obligation_id=rollout_replan_obligation_id,
                spend_turn_instance_id=spend_turn_instance_id,
                rollout_details=rollout_details,
                append_cli_rollout_event=append_cli_rollout_event,
            )
    return _emit_quota_result(
        payload,
        args,
        context=context,
        registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id,
        usage_quota_started=usage_quota_started,
        capture_directory=capture_directory,
        detail_sections=detail_sections,
        print_payload=print_payload,
    )
