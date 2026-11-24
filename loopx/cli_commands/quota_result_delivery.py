"""Result projection, capture, and bounded delivery for quota CLI commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..control_plane.quota.error_codes import (
    QuotaCommandValidationError,
    QuotaDecisionTransportError,
)
from ..control_plane.turn_driver.decision_capture import (
    context_capture_read_command,
    project_captured_context,
)
from .context_delivery_retention import maybe_prune_context_delivery_root
from .quota_capture import (
    bind_capture_selection_transport,
    budgeted_settlement_result,
    capture_decision,
    handoff_decision,
    native_context_activation,
    progressive_context_or_current,
    project_native_quota_context,
    quota_context_surface,
    settlement_capture_failure_view,
)
from .quota_presentation import (
    compose_quota_work_contract,
    _project_quota_cli_payload,
    _quota_renderer,
    project_scheduler_observation,
)
from .quota_reward_memory import attach_reward_memory_recall_after_should_run

if TYPE_CHECKING:
    from .quota_context import QuotaCommandContext

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def _prune_ephemeral_context_delivery(
    args: argparse.Namespace,
    *,
    context: QuotaCommandContext | None,
    capture_directory: Path | None,
) -> None:
    if getattr(args, "dry_run", False) is True:
        return
    runtime_root = (
        context.runtime_root
        if context is not None
        else getattr(args, "_progressive_context_runtime", None)
    )
    if not isinstance(runtime_root, (str, Path)):
        return
    resolved_runtime = Path(runtime_root).expanduser().resolve()
    managed_root = resolved_runtime / "context-delivery"
    capture_parent: Path | None = None
    if isinstance(capture_directory, Path):
        try:
            capture_parent = capture_directory.expanduser().resolve().parent
        except OSError:
            capture_parent = capture_directory.expanduser().absolute().parent
    protected = (
        frozenset({capture_directory.name})
        if capture_directory is not None and capture_parent == managed_root
        else frozenset()
    )
    maybe_prune_context_delivery_root(
        managed_root,
        runtime_root=resolved_runtime,
        protected_names=protected,
    )


def _emit_and_prune_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    renderer: Callable[[dict[str, object]], str],
    print_payload: PrintPayload,
    context: QuotaCommandContext | None,
    capture_directory: Path | None,
    exit_code: int,
) -> int:
    print_payload(payload, args.format, renderer)
    _prune_ephemeral_context_delivery(
        args,
        context=context,
        capture_directory=capture_directory,
    )
    return exit_code


def _emit_bounded_settlement(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    reader_argv: list[str],
    print_payload: PrintPayload,
) -> int:
    """Preserve the committed receipt even when bounded result delivery fails."""
    try:
        view = budgeted_settlement_result(
            payload,
            runtime_root=runtime_root,
            output_bytes=args.max_page_output_bytes,
            reader_argv=reader_argv,
        )
    except QuotaDecisionTransportError as exc:
        view = settlement_capture_failure_view(
            payload,
            output_bytes=args.max_page_output_bytes,
            error=exc,
        )
        print_payload(view, args.format, _quota_renderer(args, failure=True))
        return 1
    print_payload(
        view,
        args.format,
        _quota_renderer(args, failure=payload.get("ok") is not True),
    )
    return 0 if payload.get("ok") is True else 1


def prepare_quota_result_context(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    attach_recall: bool = True,
) -> tuple[dict[str, object], dict[str, Any]]:
    """Resolve presentation and optionally attach recall before composition."""
    try:
        activation = native_context_activation(args)
    except QuotaCommandValidationError:
        if payload.get("ok") is True:
            raise
        # Keep the original diagnostic on a refused guard.
        activation = {"progressive_enabled": False, "enabled": False}
    if attach_recall and args.quota_command == "should-run":
        attach_reward_memory_recall_after_should_run(
            payload,
            quota_command=args.quota_command,
            heartbeat_turn_id=heartbeat_turn_id,
            registry_path=registry_path,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
    return payload, activation


def _observe_live_quota_result(
    args: argparse.Namespace,
    payload: Mapping[str, object],
    *,
    quota_module: Any,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    started_at: int,
) -> None:
    preview = args.quota_command == "should-run" and getattr(args, "dry_run", False)
    if context is None or preview:
        return
    quota_module.observe_quota_result(
        args,
        payload,
        registry_path=registry_path,
        runtime_root=context.runtime_root,
        turn_id=quota_module._effective_spend_turn_instance_id(
            payload, heartbeat_turn_id=heartbeat_turn_id
        ),
        started_at=started_at,
    )


def _emit_scheduler_observation_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    quota_module: Any,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    usage_quota_started: int,
    print_payload: PrintPayload,
) -> int:
    """Emit the bounded observer without entering native capture or settlement."""
    _observe_live_quota_result(
        args,
        payload,
        quota_module=quota_module,
        context=context,
        registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id,
        started_at=usage_quota_started,
    )
    succeeded = payload.get("ok") is True
    print_payload(
        project_scheduler_observation(payload),
        args.format,
        _quota_renderer(args, failure=not succeeded),
    )
    return 0 if succeeded else 1


def emit_quota_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    quota_module: Any,
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

    def record_capture(decision: dict[str, object], digest: str | None) -> None:
        nonlocal saved_decision, capture_sha256
        saved_decision = decision
        if digest is not None:
            capture_sha256 = digest

    try:
        return _publish_quota_result(
            payload,
            args,
            quota_module=quota_module,
            context=context,
            registry_path=registry_path,
            heartbeat_turn_id=heartbeat_turn_id,
            usage_quota_started=usage_quota_started,
            capture_directory=capture_directory,
            detail_sections=detail_sections,
            print_payload=print_payload,
            record_capture=record_capture,
        )
    except Exception as exc:  # noqa: BLE001 - publication cannot revoke a durable guard.
        receipt = payload.get("heartbeat_receipt")
        if not isinstance(exc, QuotaDecisionTransportError) and (
            args.quota_command != "should-run"
            or not isinstance(receipt, Mapping)
            or not receipt.get("event_id")
        ):
            raise
        failure = quota_module._export("quota_decision_transport_failure_payload")(
            args,
            saved_decision,
            exc,
            capture_directory=capture_directory,
            capture_sha256=capture_sha256,
        )
        print_payload(failure, args.format, _quota_renderer(args, failure=True))
        return 1


def _publish_quota_result(
    payload: dict[str, object],
    args: argparse.Namespace,
    *,
    quota_module: Any,
    context: QuotaCommandContext | None,
    registry_path: Path,
    heartbeat_turn_id: str | None,
    usage_quota_started: int,
    capture_directory: Path | None,
    detail_sections: frozenset[str],
    print_payload: PrintPayload,
    record_capture: Callable[[dict[str, object], str | None], None],
) -> int:
    from ..command_invocation import current_loopx_cli_argv

    if getattr(args, "scheduler_observation", False):
        return _emit_scheduler_observation_result(
            payload,
            args,
            quota_module=quota_module,
            context=context,
            registry_path=registry_path,
            heartbeat_turn_id=heartbeat_turn_id,
            usage_quota_started=usage_quota_started,
            print_payload=print_payload,
        )
    reader_argv = current_loopx_cli_argv()
    budgeted_spend = (
        args.quota_command == "spend-slot"
        and args.format == "json"
        and context is not None
        and getattr(args, "max_page_output_bytes", None) is not None
    )
    activation = None
    if not budgeted_spend:
        payload, activation = quota_module._prepare_quota_result_context(
            payload,
            args,
            registry_path=registry_path,
            heartbeat_turn_id=heartbeat_turn_id,
            attach_recall=False,
        )
    # Compose optional capability guidance after recovery has chosen the plan,
    # before capture/compaction. The quota decision owner stays capability-neutral.
    interaction_contract = payload.get("interaction_contract")
    cli = (
        interaction_contract.get("cli_channel")
        if isinstance(interaction_contract, Mapping)
        else None
    )
    if isinstance(cli, dict) and isinstance(cli.get("settlement_plan"), Mapping):
        from ..capabilities.explore.turn_context import project_settlement_attachment

        cli["settlement_plan"] = project_settlement_attachment(
            cli["settlement_plan"],
            registry_path=registry_path,
            runtime_root=context.runtime_root if context is not None else None,
        )
    _observe_live_quota_result(
        args,
        payload,
        quota_module=quota_module,
        context=context,
        registry_path=registry_path,
        heartbeat_turn_id=heartbeat_turn_id,
        started_at=usage_quota_started,
    )
    if budgeted_spend:
        assert context is not None
        return _emit_bounded_settlement(
            payload,
            args,
            runtime_root=context.runtime_root,
            reader_argv=reader_argv,
            print_payload=print_payload,
        )
    assert activation is not None
    context_delivery_enabled = activation["enabled"]
    bind_capture_selection_transport(payload, args, capture_directory=capture_directory)
    delivery_request = None
    if capture_directory is not None and payload.get("replan_action_packet"):
        payload = {
            **payload,
            "replan_read_path": str(capture_directory / "decision.json"),
            "replan_read_argv": reader_argv,
        }
    if (
        args.quota_command == "should-run"
        and not getattr(args, "dry_run", False)
        and context is not None
    ):
        # Capture the one liveness observation with the decision that produced
        # it. Presentation and evidence recovery must not record it again.
        quota_module._export("attach_host_poll_receipt")(
            context.status_payload, args, payload, registry_path=registry_path,
        )
    record_capture(payload, None)
    native_context = context_delivery_enabled and bool(
        getattr(args, "codex_context_dir", None)
        or getattr(args, "codex_context_root", None)
    )
    scheduler_context = context.scheduler_context if context is not None else None
    if (capture_directory is not None and args.quota_command == "should-run"
            and isinstance(payload.get("interaction_contract"), Mapping)):
        # Bind the existing immutable reader route before signing. Its sidecar
        # receives the final byte digest after publication, avoiding a self-hash.
        source = capture_directory / "decision.json"
        existing_detail_ref = payload.get("detail_ref")
        detail_ref = (
            dict(existing_detail_ref)
            if isinstance(existing_detail_ref, Mapping)
            else {}
        )
        payload = {**payload, "detail_ref": {
            **detail_ref,
            "full_decision": context_capture_read_command(source, "",
                reference_file=source.with_suffix(".reader.json"), reader_argv=reader_argv),
        }}
    pre_contract_decision = payload
    payload = compose_quota_work_contract(
        payload,
        args,
        scheduler_context=scheduler_context,
        capture_directory=capture_directory,
        record_capture=record_capture,
    )
    if args.quota_command == "should-run":
        from ..control_plane.quota.turn_envelope import (
            COMPLETE_TURN_DECISION_VERSION,
            attach_complete_turn_context,
        )

        if payload.get("schema_version") == COMPLETE_TURN_DECISION_VERSION:
            hook_target = dict(payload)
            attach_reward_memory_recall_after_should_run(
                hook_target,
                quota_command=args.quota_command,
                heartbeat_turn_id=heartbeat_turn_id,
                registry_path=registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                decision=pre_contract_decision,
            )
            additions = {
                key: value
                for key, value in hook_target.items()
                if key not in payload
            }
            if additions:
                payload = attach_complete_turn_context(payload, additions)
        else:
            attach_reward_memory_recall_after_should_run(
                payload,
                quota_command=args.quota_command,
                heartbeat_turn_id=heartbeat_turn_id,
                registry_path=registry_path,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                decision=pre_contract_decision,
            )
    captured_digest = capture_decision(
        capture_directory,
        payload,
        args=args,
        native_context=native_context,
        record_capture=record_capture,
    )
    if getattr(args, "prepared_capture_reference", False) and payload.get("ok") is True:
        assert captured_digest is not None  # Preflight requires an explicit capture directory.
        from ..control_plane.turn_driver.codex_execution_preparation import (
            prepared_capture_reference,
        )

        return _emit_and_prune_result(
            prepared_capture_reference(payload, captured_digest),
            args,
            renderer=_quota_renderer(args),
            print_payload=print_payload,
            context=context,
            capture_directory=capture_directory,
            exit_code=0,
        )
    if (
        payload.get("ok") is False
        and context_delivery_enabled
        and capture_directory
        and args.format == "json"
        and not getattr(args, "defer_context_presentation", False)
    ):
        return _emit_and_prune_result(
            quota_module._export("captured_quota_failure_view")(
                payload,
                capture_directory=capture_directory,
                max_page_output_bytes=getattr(args, "max_page_output_bytes", None),
            ),
            args,
            renderer=_quota_renderer(args, failure=True),
            print_payload=print_payload,
            context=context,
            capture_directory=capture_directory,
            exit_code=1,
        )
    if payload.get("ok") is True:
        delivery_request = handoff_decision(
            capture_directory,
            getattr(args, "decision_handoff_socket", None),
            context_delivery_enabled,
            capture_sha256=captured_digest,
        )
    captured_payload = payload
    decision_identity = ("", "")
    if (
        delivery_request is not None
        or getattr(args, "codex_context_dir", None)
        or getattr(args, "codex_context_root", None)
    ):
        agent = captured_payload.get("agent_identity")
        decision_identity = (
            str(captured_payload.get("goal_id") or ""),
            str(agent.get("agent_id") or "")
            if isinstance(agent, Mapping)
            else str(args.agent_id or ""),
        )
    payload = _project_quota_cli_payload(
        captured_payload,
        args,
        detail_sections,
        scheduler_context,
        captured_decision_path=(
            str(capture_directory / "decision.json")
            if capture_directory is not None
            else None
        ),
    )
    succeeded = bool(payload.get("ok"))
    # Handoff releases the immutable original before any presentation. Native
    # actual-input observation owns its projection when explicitly selected;
    # never wrap one context-delivery wire in a second projection.
    deferred_context = getattr(args, "defer_context_presentation", False)
    if (
        not deferred_context
        and args.format == "json"
        and succeeded
        and delivery_request is not None
        and not native_context
        and context_delivery_enabled
    ):
        payload = project_captured_context(
            payload,
            directory=capture_directory,
            request=delivery_request[0],
            capture_id=delivery_request[1],
            goal_id=decision_identity[0],
            agent_id=decision_identity[1],
            surface=quota_context_surface(captured_payload),
        )
    if (
        not deferred_context
        and args.format == "json"
        and succeeded
        and args.quota_command == "should-run"
        and activation["progressive_enabled"]
        and not native_context
        and delivery_request is None
    ):
        payload = progressive_context_or_current(
            payload,
            args,
            runtime_root=context.runtime_root
            if context is not None
            else Path(args._progressive_context_runtime),
            directory=capture_directory,
            reader_argv=reader_argv,
        )
    if (
        not deferred_context
        and args.format == "json"
        and succeeded
        and capture_directory
        and native_context
    ):
        assert captured_digest is not None  # Native publication requires the captured original.
        try:
            payload = project_native_quota_context(
                payload,
                args,
                capture_directory=capture_directory,
                capture_id=captured_digest,
                decision_identity=decision_identity,
                progressive_enabled=activation["progressive_enabled"],
                reader_argv=reader_argv,
            )
        except (OSError, ValueError) as exc:
            raise QuotaDecisionTransportError(
                "Current context recovery could not be prepared. Read the saved original decision and receipt."
            ) from exc
    return _emit_and_prune_result(
        payload,
        args,
        renderer=_quota_renderer(args, failure=not succeeded),
        print_payload=print_payload,
        context=context,
        capture_directory=capture_directory,
        exit_code=0 if succeeded else 1,
    )
