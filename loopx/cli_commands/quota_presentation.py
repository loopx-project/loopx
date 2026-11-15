"""Quota CLI presentation transport; TypeScript retains decision ownership."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.effect_runtime import EffectRuntimeRejected
from ..control_plane.quota.turn_envelope import build_turn_envelope, project_captured_replan_cli_view
from ..control_plane.scheduler.execution_context import SchedulerExecutionContextResolution


def _compose_explicit_turn_context(payload: dict[str, object], args: argparse.Namespace, *,
                                   scheduler_context: Mapping[str, object] | SchedulerExecutionContextResolution | None,
                                   capture_directory: Path | None,
                                   native_context: bool,
                                   record_capture: Callable[[dict[str, object], str], None] | None = None) -> dict[str, object]:
    """Select the existing complete source before capture, preserving a failed guard."""
    if not getattr(args, "complete_turn", False) or not isinstance(payload.get("interaction_contract"), Mapping):
        return payload
    from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from ..control_plane.quota.error_codes import QuotaDecisionTransportError
    from ..control_plane.quota.turn_envelope import compose_turn_decision

    try:
        return compose_turn_decision(payload, scheduler_execution_context=scheduler_context,
            composed_settlement=True)
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        from .quota_capture import capture_decision

        # Admission already happened. Keep its source/receipt; never emit a
        # false v1, discard the receipt or rerun the guard to recover context.
        capture_decision(
            capture_directory, payload, args=args, native_context=native_context, record_capture=record_capture,
        )
        raise QuotaDecisionTransportError(
            "Complete Turn context could not be composed. Read the saved original decision and receipt; do not rerun admission."
        ) from exc


def _project_native_cli_settlement(payload: dict[str, object]) -> dict[str, object]:
    """Publish one projected plan before capability binding and capture."""
    from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError, effect_runtime_result
    from ..control_plane.quota.turn_envelope import _apply_context_projection_changes

    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, Mapping) else None
    todo = payload.get("selected_todo")
    if not isinstance(cli, Mapping) or not isinstance(todo, Mapping):
        return payload
    try:
        projection = effect_runtime_result("settlement.native_cli_projection", {
            "cli": cli, "receipt": payload.get("heartbeat_receipt"), "todo_id": todo.get("todo_id"),
        })
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError):
        # The already admitted Turn still has its canonical manual plan.
        return payload
    if not projection:
        return payload
    return {**payload, "interaction_contract": {
        **interaction, "cli_channel": _apply_context_projection_changes(dict(cli), projection),
    }}


def project_scheduler_observation(payload: Mapping[str, object]) -> dict[str, object]:
    """Select existing scheduling and recovery facts; do not decide a wake here."""
    def select(source: object, *keys: str) -> dict[str, object]:
        return {key: source[key] for key in keys if key in source} if isinstance(source, Mapping) else {}

    view = select(
        payload, "ok", "goal_id", "goal_ref", "runtime_root", "state",
        "error", "error_code", "reason", "status", "recommended_action", "effective_action", "should_run",
    )
    if payload.get("ok") is not True:
        return view
    contract = select(payload.get("interaction_contract"), "agent_channel")
    view["interaction_contract"] = {"agent_channel": select(contract.get("agent_channel"), "must_attempt")}
    hint = select(payload.get("scheduler_hint"), "action", "cadence_class", "reason")
    if isinstance(source := payload.get("scheduler_hint"), Mapping):
        for key, field in (
            ("unchanged_poll", "local_scheduler"),
            ("reset_policy", "reset_token"),
            ("cold_path_detail", "local_scheduler"),
        ):
            if key in source:
                hint[key] = select(source[key], field)
    view["scheduler_hint"] = hint
    for key, fields in (
        ("selected_todo", ("todo_id",)),
        ("agent_identity", ("agent_id",)),
        ("autonomous_replan_obligation", ("required", "obligation_id", "agent_id", "resolution_mode")),
        ("heartbeat_receipt", ("settlement_identity",)),
    ):
        if key in payload:
            view[key] = select(payload[key], *fields)
    return view


def _quota_renderer(
    args: argparse.Namespace,
    *,
    failure: bool = False,
) -> Callable[[dict[str, object]], str]:
    # Renderers and compaction load only when a route prints a quota view;
    # native context delivery owns its own presentation.
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

    command = args.quota_command
    if not failure and bool(getattr(args, "turn_envelope", False)):
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


def _project_quota_cli_payload(
    payload: dict[str, object],
    args: argparse.Namespace,
    detail_sections: frozenset[str],
    scheduler_context: object,
    *,
    captured_decision_path: str | None = None,
) -> dict[str, object]:
    """Project decided contracts; failed collection retains its diagnostic.

    Optional presentation must not call a runtime that failed during collection.
    A rejected envelope likewise retains the already-decided facts (issue #3687).
    """
    from ..control_plane.effect_program import reject_retired_quota_format
    from ..control_plane.quota.cli_projection import (
        compact_quota_monitor_poll_cli_payload,
        compact_quota_plan_cli_payload,
        compact_quota_should_run_cli_payload,
    )

    reject_retired_quota_format(payload)
    if getattr(args, "complete_turn", False) and payload.get("schema_version") == "loopx_turn_envelope_v1":
        return payload
    if payload.get("ok") is False and not isinstance(payload.get("interaction_contract"), Mapping):
        if (not bool(getattr(args, "turn_envelope", False))
                and getattr(args, "quota_command", None) == "monitor-poll"
                and isinstance(payload.get("before"), Mapping)):
            # A refused poll still has its decided before snapshot. Its
            # existing compaction is pure and does not reenter the runtime.
            return compact_quota_monitor_poll_cli_payload(
                payload, include_decision_detail="decisions" in detail_sections,
            )
        # A collection failure has no decided contract and must not reenter
        # its unavailable runtime. Decided refusals still need signed views.
        # Keep the decided diagnostic, including its original recovery/receipt.
        if bool(getattr(args, "turn_envelope", False)):
            return {**payload, "turn_envelope_skipped": "quota request failed; original diagnostic retained"}
        return payload
    if not bool(getattr(args, "turn_envelope", False)):
        if args.quota_command in {"status", "plan"}:
            return compact_quota_plan_cli_payload(payload, detail_sections=detail_sections)
        if args.quota_command == "should-run":
            if captured_decision_path and "vision" not in detail_sections:
                payload = project_captured_replan_cli_view(payload)
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
            captured_decision_path=captured_decision_path,
        )
    except EffectRuntimeRejected as envelope_error:
        degraded = dict(payload)
        degraded["turn_envelope_skipped"] = str(envelope_error)[:200]
        return degraded
