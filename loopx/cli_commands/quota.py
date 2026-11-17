from __future__ import annotations

import argparse
import shlex
import sys
import time
from collections.abc import Callable, Mapping
from importlib import import_module
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ..cli_runtime import PrintPayload

from .quota_presentation import (
    _compose_explicit_turn_context,
    _project_native_cli_settlement,
    _project_quota_cli_payload as _project_quota_cli_payload,
    _quota_renderer as _quota_renderer,
    project_scheduler_observation,
)
from .quota_capture import (
    bind_capture_selection_transport, budgeted_settlement_result, capture_decision, handoff_decision,
    handle_native_context_command, prepare_quota_capture,
    native_context_activation, progressive_context_or_current, project_native_quota_context,
    settlement_capture_failure_view,
)
from ..control_plane.turn_driver.decision_capture import (
    project_captured_context,
)
from ..usage_goal import observe_quota_result
from ..file_lock import LockAcquireTimeoutError
from ..rollout_event_log import RolloutAppendPreconditionChanged

from ..control_plane.coordination.local_authority import CanonicalAuthorityRequired
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.effect_program import SettlementIdentity
from ..control_plane.quota.error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
    QuotaCommandValidationError,
    QuotaDecisionTransportError,
)
from ..control_plane.quota.heartbeat_receipt import (
    attach_uncommitted_heartbeat_receipt,
    capture_heartbeat_admission_source,
    fail_heartbeat_receipt,
    find_heartbeat_receipt,
    heartbeat_receipt_request_subject,
    heartbeat_receipt_view,
)
from .quota_registration import (
    register_quota_command as register_quota_command,  # noqa: PLC0414
)
from .quota_reward_memory import (
    attach_reward_memory_ingest_after_spend,
    attach_reward_memory_recall_after_should_run,
)

if TYPE_CHECKING:
    from .quota_action_selection import RequestedQuotaActionSelection
    from .quota_context import QuotaCommandContext

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
            "quota_result_work_admission_details",
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
    _export("validate_quota_command_context_request")(args)
    if args.quota_command != "should-run":
        return None, False
    preview = bool(getattr(args, "dry_run", False))
    if preview and not args.agent_id:
        return None, False
    dispatch = {}
    if not preview:
        from .lark_inbox import dispatch_goal_lark_turn_start_hooks

        dispatch = dispatch_goal_lark_turn_start_hooks(
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
    if args.agent_id:
        from ..capabilities.manager_context import turn_start_hook
        from ..control_plane.capability_hooks import dispatch_turn_start_hooks
        from ..history import load_registry
        from ..paths import resolve_runtime_root
        root = resolve_runtime_root(load_registry(registry_path), runtime_root_arg, registry_path=registry_path)
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
        model_read = (not getattr(args, "scheduler_observation", False)
                      and native_context_activation(args)["progressive_enabled"])
        context_dispatch = dispatch_turn_start_hooks((
            turn_start_hook(root, registry_path, args.goal_id, args.agent_id, model_read=model_read),
        ))
        from ..capabilities.semantic_preference.agent_preferences import extend_turn_start_dispatch as extend_preferences
        context_dispatch = extend_preferences(context_dispatch, runtime_root=root, registry_path=registry_path,
            goal_id=args.goal_id, agent_id=args.agent_id)
        dispatch = dict(dispatch)
        for key in ("results", "required_reads", "failures", "contexts"):
            if key == "contexts" and not context_dispatch.get(key):
                continue
            dispatch[key] = list(dispatch.get(key) or []) + list(context_dispatch.get(key) or [])
        for key in ("registered_count", "invoked_count"):
            dispatch[key] = int(dispatch.get(key) or 0) + int(context_dispatch.get(key) or 0)
        if not preview:
            from ..capabilities.periodic_report.cadence_runtime import (
                extend_cadence_turn_start_dispatch,
            )
            dispatch = extend_cadence_turn_start_dispatch(dispatch, registry_path=registry_path,
                runtime_root=root, goal_id=args.goal_id, agent_id=args.agent_id)
    local_private_state_mutated = bool(dispatch.get("uncertain_local_effects")) or any(
        isinstance(result, Mapping)
        and result.get("local_private_state_mutated") is not False
        for result in (dispatch.get("results") or [])
    )
    return dispatch, local_private_state_mutated


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
        **({"goal_ref": goal_ref} if goal_ref is not None else {}),
    )
    return goal_ref, turn_start_hook_dispatch, context


def _quota_turn_context_projector(args, *, registry_path: Path, runtime_root: Path):
    from ..capabilities.explore.turn_context import turn_context_projector

    return turn_context_projector(registry_path=registry_path, runtime_root=runtime_root,
        goal_id=args.goal_id, agent_id=args.agent_id, todo_id=args.todo_id)


def _emit_bounded_settlement(payload, args, *, runtime_root, reader_argv, print_payload):
    """Preserve the committed receipt even when bounded result delivery fails."""
    try:
        view = budgeted_settlement_result(payload, runtime_root=runtime_root,
            output_bytes=args.max_page_output_bytes, reader_argv=reader_argv)
    except QuotaDecisionTransportError as exc:
        view = settlement_capture_failure_view(payload, output_bytes=args.max_page_output_bytes, error=exc)
        print_payload(view, args.format, _quota_renderer(args, failure=True))
        return 1
    print_payload(view, args.format, _quota_renderer(args, failure=payload.get("ok") is not True))
    return 0 if payload.get("ok") is True else 1


def _prepare_quota_result_context(
    payload: dict[str, object], args: argparse.Namespace, *,
    registry_path: Path, heartbeat_turn_id: str | None, capture_directory: Path | None,
) -> tuple[dict[str, object], dict[str, Any]]:
    """Prepare current settlement and capability binding before publishing it."""
    try:
        activation = native_context_activation(args)
    except QuotaCommandValidationError:
        if payload.get("ok") is True:
            raise
        # Keep the original diagnostic on a refused guard.
        activation = {"progressive_enabled": False, "enabled": False}
    if args.quota_command == "should-run":
        if (args.format == "json" and capture_directory is not None and activation["progressive_enabled"]
                and activation.get("scenario") == "codex_app_heartbeat"
                and (getenv("CODEX_THREAD_ID") or getattr(args, "prepared_capture_reference", False))
                and (getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None))
                and not getattr(args, "turn_envelope", False)
                and payload.get("ok") is True and payload.get("effective_action") == EffectiveAction.NORMAL_RUN.value
                and payload.get("normal_delivery_allowed") is True):
            payload = _project_native_cli_settlement(payload)
        attach_reward_memory_recall_after_should_run(payload, quota_command=args.quota_command,
            heartbeat_turn_id=heartbeat_turn_id, registry_path=registry_path, goal_id=args.goal_id, agent_id=args.agent_id)
    return payload, activation


def _observe_live_quota_result(
    args: argparse.Namespace,
    payload: Mapping[str, object],
    *,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    started_at: int,
) -> None:
    preview = args.quota_command == "should-run" and getattr(args, "dry_run", False)
    if context is None or preview:
        return
    observe_quota_result(
        args, payload, registry_path=registry_path, runtime_root=context.runtime_root,
        turn_id=_effective_spend_turn_instance_id(payload, heartbeat_turn_id=heartbeat_turn_id),
        started_at=started_at,
    )


def _emit_scheduler_observation_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    usage_quota_started: int,
    print_payload: PrintPayload,
) -> int:
    """Emit the bounded observer without entering native capture or settlement."""
    _observe_live_quota_result(
        args, payload, context=context, registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id, started_at=usage_quota_started,
    )
    succeeded = payload.get("ok") is True
    print_payload(project_scheduler_observation(payload), args.format,
                  _quota_renderer(args, failure=not succeeded))
    return 0 if succeeded else 1


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
    """Capture the full decision before projecting and printing the CLI view."""
    capture_sha256: str | None = None
    saved_decision = payload

    def record_capture(decision: dict[str, object], digest: str) -> None:
        nonlocal saved_decision, capture_sha256
        saved_decision = decision
        capture_sha256 = digest

    try:
        return _publish_quota_result(
            payload, args, context=context, registry_path=registry_path,
            heartbeat_turn_id=heartbeat_turn_id, usage_quota_started=usage_quota_started,
            capture_directory=capture_directory, detail_sections=detail_sections,
            print_payload=print_payload, record_capture=record_capture,
        )
    except Exception as exc:  # noqa: BLE001 - publication cannot revoke a durable guard.
        receipt = payload.get("heartbeat_receipt")
        if not isinstance(exc, QuotaDecisionTransportError) and (
            args.quota_command != "should-run"
            or not isinstance(receipt, Mapping)
            or not receipt.get("event_id")
        ):
            raise
        failure = _export("quota_decision_transport_failure_payload")(
            args, saved_decision, exc, capture_directory=capture_directory, capture_sha256=capture_sha256,
        )
        print_payload(failure, args.format, _quota_renderer(args, failure=True))
        return 1


def _publish_quota_result(
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
    record_capture: Callable[[dict[str, object], str], None],
) -> int:
    from ..command_invocation import current_loopx_cli_argv

    if getattr(args, "scheduler_observation", False):
        return _emit_scheduler_observation_result(
            payload, args, context=context, registry_path=registry_path,
            heartbeat_turn_id=heartbeat_turn_id, usage_quota_started=usage_quota_started,
            print_payload=print_payload,
        )
    reader_argv = current_loopx_cli_argv()
    budgeted_spend = (args.quota_command == "spend-slot" and args.format == "json" and context is not None
        and getattr(args, "max_page_output_bytes", None) is not None)
    activation = None
    if not budgeted_spend:
        payload, activation = _prepare_quota_result_context(
            payload, args, registry_path=registry_path, heartbeat_turn_id=heartbeat_turn_id,
            capture_directory=capture_directory,
        )
    # Compose optional capability guidance after recovery has chosen the plan,
    # before capture/compaction. The quota decision owner stays capability-neutral.
    cli = (payload.get("interaction_contract") or {}).get("cli_channel")
    if isinstance(cli, dict) and isinstance(cli.get("settlement_plan"), Mapping):
        from ..capabilities.explore.turn_context import project_settlement_attachment

        cli["settlement_plan"] = project_settlement_attachment(
            cli["settlement_plan"], registry_path=registry_path,
            runtime_root=context.runtime_root if context is not None else None,
        )
    _observe_live_quota_result(
        args, payload, context=context, registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id, started_at=usage_quota_started,
    )
    if budgeted_spend:
        assert context is not None
        return _emit_bounded_settlement(payload, args, runtime_root=context.runtime_root,
            reader_argv=reader_argv, print_payload=print_payload)
    assert activation is not None
    context_delivery_enabled = activation["enabled"]
    bind_capture_selection_transport(payload, args, capture_directory=capture_directory)
    delivery_request = None
    if capture_directory is not None and payload.get("replan_action_packet"):
        payload = {**payload, "replan_read_path": str(capture_directory / "decision.json"),
            "replan_read_argv": reader_argv}
    if args.quota_command == "should-run" and not getattr(args, "dry_run", False) and context is not None:
        # Capture the one liveness observation with the decision that produced
        # it. Presentation and evidence recovery must not record it again.
        _export("attach_host_poll_receipt")(
            context.status_payload, args, payload, registry_path=registry_path,
        )
    native_context = context_delivery_enabled and bool(getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None))
    scheduler_context = context.scheduler_context if context is not None else None
    payload = _compose_explicit_turn_context(payload, args,
        scheduler_context=scheduler_context,
        capture_directory=capture_directory, native_context=native_context, record_capture=record_capture)
    captured_digest = capture_decision(
        capture_directory, payload, args=args, native_context=native_context, record_capture=record_capture,
    )
    if getattr(args, "prepared_capture_reference", False) and payload.get("ok") is True:
        assert captured_digest is not None  # Preflight requires an explicit capture directory.
        from ..control_plane.turn_driver.codex_execution_preparation import prepared_capture_reference

        print_payload(prepared_capture_reference(payload, captured_digest), args.format, _quota_renderer(args))
        return 0
    if (payload.get("ok") is False and context_delivery_enabled and capture_directory
            and args.format == "json" and not getattr(args, "defer_context_presentation", False)):
        print_payload(_export("captured_quota_failure_view")(payload, capture_directory=capture_directory,
            max_page_output_bytes=getattr(args, "max_page_output_bytes", None)),
            args.format, _quota_renderer(args, failure=True))
        return 1
    if payload.get("ok") is True:
        delivery_request = handoff_decision(
            capture_directory, getattr(args, "decision_handoff_socket", None),
            context_delivery_enabled, capture_sha256=captured_digest,
        )
    captured_payload = payload
    decision_identity = ("", "")
    if (delivery_request is not None or getattr(args, "codex_context_dir", None)
            or getattr(args, "codex_context_root", None)):
        agent = captured_payload.get("agent_identity")
        decision_identity = (str(captured_payload.get("goal_id") or ""),
            str(agent.get("agent_id") or "") if isinstance(agent, Mapping) else str(args.agent_id or ""))
    payload = _project_quota_cli_payload(
        captured_payload, args, detail_sections,
        scheduler_context,
        captured_decision_path=(
            str(capture_directory / "decision.json") if capture_directory is not None else None
        ),
    )
    if args.quota_command == "should-run" and "host_poll_receipt" in captured_payload:
        # Legacy envelope presentation omits this source field. Preserve its
        # existing visible receipt by copying the observation, not polling.
        payload["host_poll_receipt"] = captured_payload["host_poll_receipt"]
    succeeded = bool(payload.get("ok"))
    # Handoff releases the immutable original before any presentation. Native
    # actual-input observation owns its projection when explicitly selected;
    # never wrap one context-delivery wire in a second projection.
    deferred_context = getattr(args, "defer_context_presentation", False)
    if (not deferred_context and args.format == "json" and succeeded and delivery_request is not None and not native_context
            and context_delivery_enabled):
        payload = project_captured_context(payload, directory=capture_directory,
            request=delivery_request[0], capture_id=delivery_request[1],
            goal_id=decision_identity[0], agent_id=decision_identity[1],
            surface="turn_envelope" if getattr(args, "turn_envelope", False) else "quota_packet")
    if (not deferred_context and args.format == "json" and succeeded and args.quota_command == "should-run"
            and activation["progressive_enabled"] and not native_context and delivery_request is None):
        payload = progressive_context_or_current(payload, args,
            runtime_root=context.runtime_root if context is not None else Path(args._progressive_context_runtime),
            directory=capture_directory, reader_argv=reader_argv)
    if (not deferred_context and args.format == "json" and succeeded and capture_directory
            and native_context):
        assert captured_digest is not None  # Native publication requires the captured original.
        try:
            payload = project_native_quota_context(payload, args, capture_directory=capture_directory,
                capture_id=captured_digest, decision_identity=decision_identity,
                progressive_enabled=activation["progressive_enabled"], reader_argv=reader_argv)
        except (OSError, ValueError) as exc:
            raise QuotaDecisionTransportError(
                "Current context recovery could not be prepared. Read the saved original decision and receipt."
            ) from exc
    print_payload(payload, args.format, _quota_renderer(args, failure=not succeeded))
    return 0 if succeeded else 1


def _record_non_guard_quota_event(
    payload: dict[str, Any], args: argparse.Namespace, *, registry_path: Path,
    runtime_root_arg: str | None, context: QuotaCommandContext | None, goal_ref: dict[str, str] | None,
    rollout_todo_id: str | None, rollout_replan_obligation_id: str | None,
    spend_turn_instance_id: str | None, rollout_details: dict[str, Any],
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
            **({"registry_path": registry_path} if goal_ref is not None else {}),
        )
        attach_reward_memory_ingest_after_spend(
            payload,
            execute=bool(args.execute),
            runtime_root=context.runtime_root,
            registry_path=registry_path,
            readback=readback,
        )


def _read_back_committed_heartbeat(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str,
    stall_observation: str,
    requested_todo_id: str | None,
    goal_ref: dict[str, str] | None,
) -> None:
    """Expose selection only after the original heartbeat receipt is read back."""
    receipt = find_heartbeat_receipt(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )
    if receipt:
        rollout_event_value = payload.get("rollout_event")
        rollout_event: Mapping[str, object] = (
            rollout_event_value if isinstance(rollout_event_value, Mapping) else {}
        )
        payload["heartbeat_receipt"] = heartbeat_receipt_view(
            receipt,
            turn_instance_id=turn_instance_id,
            status="committed" if rollout_event.get("appended") else "replayed",
        )
        _export("commit_requested_action_selection")(payload, requested_todo_id=requested_todo_id)
    else:
        fail_heartbeat_receipt(
            payload,
            turn_instance_id=turn_instance_id,
            stall_observation=stall_observation,
            reason=(
                "heartbeat receipt append could not be read back; retry "
                "quota should-run with the same --turn-instance-id"
            ),
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
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            goal_ref_resolver=lambda: _quota_goal_ref(args, registry_path=registry_path),
        )
        print_payload(payload, args.format, render_quota_receipt)
        return 0 if payload["ok"] else 1
    native_result = handle_native_context_command(args, print_payload=print_payload,
        registry_path=registry_path, runtime_root_arg=runtime_root_arg)
    if native_result is not None:
        return native_result
    limit = getattr(args, "max_page_output_bytes", None)
    def execute() -> int:
        return _execute_quota_command(
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            print_payload=print_payload, append_cli_rollout_event=append_cli_rollout_event,
            capture_directory=capture_directory, retained_action_selection_todo_id=retained_action_selection_todo_id,
        )

    if args.quota_command != "should-run" or args.format != "json" or limit is None:
        return execute()
    from .quota_failure_report import run_with_quota_failure_reader

    return run_with_quota_failure_reader(args, limit, print_payload=print_payload, run=execute)


def _append_quota_heartbeat_receipt(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
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
    from ..control_plane.quota.unsettled_host_turn import recovery_admission_append_precondition

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
            "settlement_effect_id": settlement_identity.effect_id if settlement_identity is not None else "",
        }
    )
    if settlement_identity is not None:
        rollout_details.update(_export("quota_result_work_admission_details")(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=args.goal_id, todo_id=rollout_todo_id, goal_ref=goal_ref,
        ))
    rollout_details = capture_heartbeat_admission_source(
        payload, details=rollout_details, identity=settlement_identity,
        registry_path=registry_path, runtime_root=runtime_root,
    )
    try:
        with recovery_admission_append_precondition(
            payload, registry_path=registry_path, runtime_root=runtime_root,
            goal_id=args.goal_id, agent_id=args.agent_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        ) as precondition:
            append_cli_rollout_event(
                payload,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                event_kind="quota_should_run",
                goal_ref=goal_ref,
                agent_id=args.agent_id,
                run_id=turn_instance_id,
                status=str(payload.get("effective_action") or payload.get("decision") or "should-run"),
                summary=f"heartbeat quota receipt committed for turn={turn_instance_id} stall={stall_observation}",
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
            runtime_root, goal_id=args.goal_id, agent_id=args.agent_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        )
    except LockAcquireTimeoutError as exc:
        failure = _export("quota_failure_payload")(
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg, error=exc,
        )
        fail_heartbeat_receipt(
            failure, turn_instance_id=turn_instance_id, stall_observation=stall_observation,
            reason="recovery receipt append could not acquire the quota authority lock",
        )
        return failure
    if receipt is None:
        fail_heartbeat_receipt(
            payload, turn_instance_id=turn_instance_id, stall_observation=stall_observation,
            reason="heartbeat receipt append could not be read back; retry quota should-run with the same --turn-instance-id",
        )
        return payload
    if precondition is not None and receipt.get("status") != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value:
        raise RolloutAppendPreconditionChanged("A different guard was recorded for this Turn.")

    rollout_event = payload.get("rollout_event")
    status = "replayed"
    if isinstance(rollout_event, Mapping) and rollout_event.get("appended"):
        status = "upgraded" if upgrade_recovery_wait else "committed"
    payload["heartbeat_receipt"] = heartbeat_receipt_view(receipt, turn_instance_id=turn_instance_id, status=status)
    _export("commit_requested_action_selection")(payload, requested_todo_id=requested_todo_id)
    return payload


def _can_upgrade_recovery_wait(
    payload: Mapping[str, object], selection: RequestedQuotaActionSelection,
) -> bool:
    receipt = selection.receipt
    if receipt is None or payload.get("effective_action") != EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value:
        return False
    details = receipt.get("details")
    if payload.get("should_run") is not True or not isinstance(details, Mapping) or details.get("should_run") is not False:
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
    successor_admission_deferred = False
    action_selection: RequestedQuotaActionSelection | None = None
    heartbeat_stall_observation = "not_evaluated"
    detail_sections: frozenset[str] = frozenset()
    context: QuotaCommandContext | None = None
    goal_ref: dict[str, str] | None = None
    reentry: tuple[argparse.Namespace, str | None] | None = None
    try:
        capture_directory = prepare_quota_capture(args, capture_directory)
        goal_ref, turn_start_hook_dispatch, context = (
            _prepare_quota_command_execution(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
        )
        (
            heartbeat_turn_id,
            detail_sections,
            runtime_root,
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
            context.status_limit,
            context.status_goal_id,
            context.status_payload,
            context.cache_metadata,
            context.scheduler_context,
            context.operator_inbox_urgency_projector,
        )
        if args.quota_command == "should-run":
            interaction_projection_hooks = (
                _export("repository_delivery_interaction_hook")(repo_path=Path.cwd()),
                _export("periodic_report_pending_intent_interaction_hook")(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                ),
            )
            action_selection = _export("load_requested_quota_action_selection")(
                args,
                runtime_root=runtime_root,
                turn_instance_id=heartbeat_turn_id,
                goal_ref=goal_ref,
                retained_todo_id=retained_action_selection_todo_id,
            )
            heartbeat_receipt_existing = action_selection.receipt
            context_projector = _quota_turn_context_projector(args,
                registry_path=registry_path, runtime_root=runtime_root)
            progressive = (not getattr(args, "scheduler_observation", False)
                           and native_context_activation(args)["progressive_enabled"])
            payload = _export("build_live_quota_should_run_decision")(
                status_payload,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                inline_goal_source=progressive,
                available_capabilities=args.available_capabilities,
                include_scheduler_detail="scheduler" in detail_sections,
                include_agent_todo_detail=(
                    "agent-todos" in detail_sections
                    and not bool(getattr(args, "turn_envelope", False))
                ),
                codex_app_current_rrule=args.app_automation_current_rrule,
                registry_path=registry_path,
                runtime_root=runtime_root,
                host_observation_resolver=_export("resolve_codex_app_automation_rrule"),
                scheduler_execution_context=scheduler_context,
                operator_inbox_urgency_projector=operator_inbox_urgency_projector,
                bounded_research_frontier_projector=(
                    _export("project_live_explore_composition_frontier")
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
                turn_context_projector=context_projector,
                context_navigation=(
                    progressive and not bool(getattr(args, "turn_envelope", False))
                ),
                goal_ref=goal_ref,
            )
            recovery = payload.get("unsettled_host_turn_recovery")
            successor_admission_deferred = (
                heartbeat_receipt_existing is None and isinstance(recovery, Mapping)
                and recovery.get("defer_successor_admission") is True
            )
            if successor_admission_deferred:
                # The original Turn still owns any scheduler follow-up. A new
                # guard receipt here would supersede it, even if left unbound.
                payload.pop("scheduler_hint", None)
            action_selection_preflight = (
                None if successor_admission_deferred else
                _export("reconcile_requested_quota_action_selection")(
                    payload,
                    args,
                    registry_path=registry_path,
                    context=context,
                    selection=action_selection,
                    goal_ref=goal_ref,
                )
            )
            if action_selection_preflight is not None and action_selection_preflight.rejected:
                action_selection_preflight_failed = True
                reentry_args = _export("inline_action_selection_reentry_args")(
                    payload, args, selection=action_selection,
                    preflight=action_selection_preflight,
                    turn_instance_id=heartbeat_turn_id,
                )
                if reentry_args is not None:
                    reentry = (
                        reentry_args,
                        action_selection.requested_todo_id
                        if action_selection_preflight.receipt is None else None,
                    )
                else:
                    heartbeat_receipt_existing = action_selection_preflight.receipt
                    heartbeat_receipt_existing_status = (
                        action_selection_preflight.receipt_status
                    )
                    heartbeat_receipt_existing_appended = (
                        action_selection_preflight.receipt_appended
                    )
            if (heartbeat_turn_id and reentry is None and not action_selection_preflight_failed
                    and not successor_admission_deferred):
                # A wait receipt grants no recovery allowance. After a valid
                # resume, bind its first grant under the ordinary append fence.
                upgrade_recovery_wait = _can_upgrade_recovery_wait(payload, action_selection)
                if heartbeat_receipt_existing and not upgrade_recovery_wait:
                    (
                        heartbeat_receipt_existing,
                        heartbeat_receipt_existing_status,
                        heartbeat_receipt_existing_appended,
                        heartbeat_stall_observation,
                        heartbeat_receipt_ready,
                    ) = _export("reconcile_existing_heartbeat_receipt_for_turn")(
                        payload,
                        args,
                        runtime_root=runtime_root,
                        turn_instance_id=heartbeat_turn_id,
                        existing=heartbeat_receipt_existing,
                        registry_path=registry_path,
                        goal_ref=goal_ref,
                    )
                else:
                    (
                        payload,
                        status_payload,
                        cache_metadata,
                        heartbeat_stall_observation,
                    ) = _export("_record_automatic_heartbeat_stall")(
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
                        find_monitor_poll_turn=_export("find_quota_monitor_poll_turn"),
                        monitor_poll_recorder=_export("record_quota_monitor_poll"),
                        status_collector=_export("collect_status"),
                        decision_builder=_export("build_live_quota_should_run_decision"),
                        host_observation_resolver=_export("resolve_codex_app_automation_rrule"),
                    )
                    heartbeat_receipt_ready = True
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
            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            error=exc, goal_ref_loader=lambda: _quota_goal_ref(args, registry_path=registry_path),
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
    if reentry is not None:
        # The next evaluation owns its collection and publication failures.
        # This frame may not yet know about a receipt committed by that evaluation.
        reentry_args, retained_todo_id = reentry
        return _execute_quota_command(
            reentry_args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
            print_payload=print_payload, append_cli_rollout_event=append_cli_rollout_event,
            capture_directory=capture_directory, retained_action_selection_todo_id=retained_todo_id,
            recovery_refreshes_remaining=recovery_refreshes_remaining,
        )
    preview = args.quota_command == "should-run" and getattr(args, "dry_run", False)
    if not preview and _export("should_log_quota")(args.quota_command, payload):
        spend_turn_instance_id = _effective_spend_turn_instance_id(
            payload,
            heartbeat_turn_id=heartbeat_turn_id,
        )
        rollout_todo_id = _export("quota_rollout_todo_id")(payload, args)
        rollout_replan_obligation_id = _export("quota_rollout_replan_obligation_id")(payload, args)
        rollout_details = _export("quota_rollout_details")(
            payload,
            args,
            todo_id=rollout_todo_id,
            replan_obligation_id=rollout_replan_obligation_id,
        )
        if heartbeat_turn_id and args.quota_command == "should-run":
            if action_selection_preflight_failed or closeout_query_unavailable or successor_admission_deferred:
                if heartbeat_receipt_existing:
                    _export("render_existing_heartbeat_receipt_payload")(
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
            elif heartbeat_receipt_existing and not upgrade_recovery_wait:
                _export("render_existing_heartbeat_receipt_payload")(
                    payload,
                    receipt=heartbeat_receipt_existing,
                    turn_instance_id=heartbeat_turn_id,
                    status=heartbeat_receipt_existing_status,
                    appended=heartbeat_receipt_existing_appended,
                )
                _export("commit_requested_action_selection")(
                    payload,
                    requested_todo_id=(
                        action_selection.requested_todo_id
                        if action_selection is not None
                        else None
                    ),
                )
            else:
                try:
                    payload = _append_quota_heartbeat_receipt(
                        payload, args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
                        runtime_root=runtime_root, goal_ref=goal_ref, append_cli_rollout_event=append_cli_rollout_event,
                        turn_instance_id=heartbeat_turn_id, stall_observation=heartbeat_stall_observation,
                        rollout_todo_id=rollout_todo_id, rollout_replan_obligation_id=rollout_replan_obligation_id,
                        rollout_details=rollout_details, upgrade_recovery_wait=upgrade_recovery_wait,
                        requested_todo_id=action_selection.requested_todo_id if action_selection is not None else None,
                    )
                except RolloutAppendPreconditionChanged:
                    if recovery_refreshes_remaining:
                        return _execute_quota_command(
                            args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
                            print_payload=print_payload, append_cli_rollout_event=append_cli_rollout_event,
                            capture_directory=capture_directory,
                            retained_action_selection_todo_id=retained_action_selection_todo_id,
                            recovery_refreshes_remaining=recovery_refreshes_remaining - 1,
                        )
                    fail_heartbeat_receipt(
                        payload,
                        turn_instance_id=heartbeat_turn_id,
                        stall_observation=heartbeat_stall_observation,
                        reason="recovery admission changed repeatedly before append; retry the same Turn",
                    )
        else:
            _record_non_guard_quota_event(
                payload, args, registry_path=registry_path,
                runtime_root_arg=runtime_root_arg, context=context, goal_ref=goal_ref,
                rollout_todo_id=rollout_todo_id,
                rollout_replan_obligation_id=rollout_replan_obligation_id,
                spend_turn_instance_id=spend_turn_instance_id, rollout_details=rollout_details,
                append_cli_rollout_event=append_cli_rollout_event,
            )
    if (args.quota_command == "should-run" and heartbeat_turn_id and payload.get("ok") is True
            and not successor_admission_deferred):
        payload = _restore_quota_work_input(args, payload, heartbeat_turn_id=heartbeat_turn_id,
            heartbeat_receipt_existing=heartbeat_receipt_existing, registry_path=registry_path,
            runtime_root=runtime_root, runtime_root_arg=runtime_root_arg, goal_ref=goal_ref)
    if getattr(args, "scheduler_observation", False):
        attach_reward_memory_recall_after_should_run(
            payload,
            quota_command=args.quota_command,
            heartbeat_turn_id=heartbeat_turn_id,
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
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


def _restore_quota_work_input(args, payload, *, heartbeat_turn_id, heartbeat_receipt_existing,
                              registry_path, runtime_root, runtime_root_arg, goal_ref):
    """Restore the original captured input after a successful quota guard."""
    if args.quota_command == "should-run" and heartbeat_turn_id and payload.get("ok") is True:
        from ..control_plane.goals.checkpoint_context_io import (
            TurnResultWork, checkpoint_continuation_required, pending_checkpoint_cli_actions,
            project_completed_first_delivery_cli, read_checkpoint_context, replan_checkpoint_cli_actions,
            result_work_context, result_work_context_exists, result_work_required,
        )
        from ..control_plane.quota.settlement import read_heartbeat_settlement, render_owed_host_command
        from ..control_plane.quota.live_decision import historical_ack_writeback_required
        from ..control_plane.turn_driver.native_context_profile import NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
        from ..control_plane.work_items.action_selection_contract import render_cli_command_prefix

        try:
            work_readback = read_heartbeat_settlement(runtime_root, goal_id=args.goal_id,
                agent_id=args.agent_id, todo_id=getattr(args, "todo_id", None),
                turn_instance_id=heartbeat_turn_id, registry_path=registry_path, goal_ref=goal_ref,
                resolve_original_binding=True)
            if (work_readback is not None and work_readback.identity.value is not None
                    and result_work_required(work_readback)):
                payload["work_input_restored"] = result_work_context_exists(runtime_root, work_readback.identity.value)
                progress = work_readback.progress or {}
                if hold := progress.get("work_authority_hold"):
                    interaction = payload["interaction_contract"]
                    channel = interaction["cli_channel"]
                    channel.pop("settlement_plan", None)
                    channel.update(next_cli_actions=[], spend_allowed_now=False, spend_after_validation=False)
                    payload["settlement_progress"] = progress
                    interaction["agent_channel"]["primary_action"] = payload["recommended_action"] = hold
                    return payload
                committed = heartbeat_receipt_existing is not None and TurnResultWork(
                    registry_path, runtime_root, work_readback.identity.value, goal_ref).result_committed()
                if progress.get("state") == "direction_pending":
                    interaction = payload["interaction_contract"]
                    channel = interaction["cli_channel"]
                    channel["next_cli_actions"] = pending_checkpoint_cli_actions(work_readback,
                        command_prefix=render_cli_command_prefix(
                            registry_path=str(registry_path), runtime_root=str(runtime_root)), goal_ref=goal_ref)
                    channel.update(spend_allowed_now=False, spend_after_validation=True)
                    interaction["agent_channel"]["primary_action"] = payload["recommended_action"] = (
                        "Result retained; read the original Turn's post-result context and complete its direction before spending."
                    )
                if committed:
                    payload["result_committed"] = True
                    delivery = payload.get("first_delivery_progress")
                    if not isinstance(delivery, dict):
                        delivery = {}
                    identity = work_readback.identity.value
                    payload["recommended_action"] = (
                        delivery["next_action"] if delivery.get("settlement_identity") == identity.as_dict()
                        else "Resume this original Turn's pending settlement; its Todo result is already committed."
                    )
                    if (work_readback.writeback_run is None and progress.get("state") == "writeback_required"
                            and delivery.get("stage") == "direction_pending" and identity.todo_id is not None
                            and delivery.get("settlement_identity") == identity.as_dict()
                            and checkpoint_continuation_required(work_readback)):
                        direction = read_checkpoint_context(
                            registry_path=registry_path, runtime_root_override=str(runtime_root),
                            goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
                            turn_instance_id=identity.turn_instance_id, goal_ref=goal_ref, purpose="first_delivery",
                            reuse_current_first_delivery=True,
                        )
                        read_id = direction.get("read_context_id")
                        if (direction.get("ok") is not True or direction.get("settlement_identity") != identity.as_dict()
                                or not isinstance(read_id, str) or not read_id):
                            raise ValueError("First delivery context is unavailable; recover this original Turn.")
                        context_view = budgeted_settlement_result(
                            direction, runtime_root=runtime_root, output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
                            deferred_fields=("basis", "context", "replan_context"),
                            read_instruction=(
                                "Read the saved result details before judging direction. Keep this read ID and original Turn."
                            ),
                        )
                        interaction = payload["interaction_contract"]
                        project_completed_first_delivery_cli(
                            interaction["cli_channel"], identity, direction_context=context_view, read_context_id=read_id,
                        )
                        action = (
                            "Read the original Turn's checkpoint_context in the writeback step, "
                            "judge the direction, and submit it with the generated command."
                        )
                        delivery["next_action"] = action
                        interaction["agent_channel"]["primary_action"] = payload["recommended_action"] = action
                elif work_readback.writeback_run is None:
                    # A hard-lease admission may bind the Turn before the claim.
                    # Capture the work basis only after its execution lease exists.
                    if (payload.get("execution_preparation") or {}).get("state") == "claim_required":
                        return payload
                    payload["delivery_result_context"] = result_work_context(registry_path=registry_path,
                        runtime_root=runtime_root, identity=work_readback.identity.value, goal_ref=goal_ref)
                    interaction = payload.get("interaction_contract", {})
                    if interaction.get("mode") == "autonomous_replan":
                        channel = interaction["cli_channel"]
                        actions = channel["next_cli_actions"]
                        historical_ack = historical_ack_writeback_required(payload)
                        staged_actions = replan_checkpoint_cli_actions(
                            actions, work_readback, reread_delivery_result=historical_ack,
                            command_prefix=render_cli_command_prefix(
                                registry_path=str(registry_path), runtime_root=str(runtime_root)))
                        channel["next_cli_actions"] = staged_actions
                        if historical_ack and staged_actions is not actions:
                            action = ("Reread the original Turn's result basis and recheck its candidate. "
                                      "Use the returned delivery read ID to retain the result from its producing workspace, "
                                      "then read and judge the direction before spending.")
                            interaction["agent_channel"]["primary_action"] = payload["recommended_action"] = action
                    recovery = payload.get("unsettled_host_turn_recovery")
                    identity = work_readback.identity.value
                    if (isinstance(recovery, dict)
                            and recovery.get("repair") in {"blocked_writeback", "durable_writeback"}
                            and recovery.get("scope") == "current_turn"
                            and recovery.get("binding_kind") == "todo" and identity.todo_id is not None
                            and recovery.get("binding_id") == identity.todo_id
                            and recovery.get("turn_instance_id") == identity.turn_instance_id):
                        context_args = ["checkpoint-context", "--purpose", "delivery_result",
                                        "--goal-id", identity.goal_id, "--agent-id", identity.agent_id,
                                        "--todo-id", identity.todo_id, "--turn-instance-id", identity.turn_instance_id]
                        if goal_ref is not None:
                            context_args.extend(["--goal-instance-id", goal_ref["goal_instance_id"]])
                        prefix = render_cli_command_prefix(registry_path=str(registry_path),
                                                           runtime_root=str(runtime_root))
                        channel = interaction["cli_channel"]
                        writeback_prefix = f"{prefix} refresh-state "
                        read_command = f"{prefix} --format json {shlex.join(context_args)}"
                        if recovery["repair"] == "durable_writeback":
                            read_command = render_owed_host_command(shlex.split(read_command))
                            plan = channel["settlement_plan"]
                            writeback = next(
                                step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback"
                            )
                            spend = next(
                                step for step in plan["ordered_steps"] if step["kind"] == "quota_spend"
                            )
                            # Recover the result before authoring its post-result direction.
                            writeback.pop("semantic_closeout_command_template", None)
                            writeback["command_template"] = render_owed_host_command([
                                *shlex.split(writeback["command_template"]), "--delivery-read-context", "<read_context_id>",
                            ])
                            spend["command_template"] = render_owed_host_command(shlex.split(spend["command_template"]))
                            writeback["precondition"] = (
                                "Run the checkpoint-context command first. Revalidate the retained result "
                                "against that basis and use its read_context_id. Keep the original Turn and wait "
                                "condition unchanged. If the result returns a pending direction, follow its "
                                "post-result read and direction commands before spending."
                            )
                            channel["next_cli_actions"] = [
                                read_command, writeback["precondition"], writeback["command_template"],
                                spend["command_template"],
                            ]
                            return payload
                        channel["next_cli_actions"] = [
                            read_command,
                            "Verify the wait against the returned basis. Substitute its read_context_id in the blocked writeback.",
                            *[
                                f"{prefix} --format json refresh-state {action.removeprefix(writeback_prefix)}"
                                " --delivery-read-context '<read_context_id>'"
                                if action.startswith(writeback_prefix) else action
                                for action in channel["next_cli_actions"]
                            ],
                        ]
        except (ValueError, OSError, RuntimeError) as error:
            admitted_receipt = payload.get("heartbeat_receipt")
            payload = _export("quota_failure_payload")(args, registry_path=registry_path,
                runtime_root_arg=runtime_root_arg, error=error)
            payload.update(error_code=getattr(error, "code", "delivery_work_context_unknown"),
                error=str(error),
                status="delivery_work_context_unavailable", heartbeat_receipt=admitted_receipt)
    return payload
