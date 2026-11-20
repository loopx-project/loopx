"""Quota CLI presentation transport; TypeScript retains decision ownership."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.quota.turn_envelope import COMPLETE_TURN_DECISION_VERSION
from ..control_plane.scheduler.execution_context import SchedulerExecutionContextResolution


def compose_quota_work_contract(payload: dict[str, object], args: argparse.Namespace, *,
                                   scheduler_context: Mapping[str, object] | SchedulerExecutionContextResolution | None,
                                   capture_directory: Path | None,
                                   record_capture: Callable[[dict[str, object], str], None] | None = None) -> dict[str, object]:
    """Compose Complete Turn sources before capture and display."""
    if args.quota_command != "should-run" or not isinstance(payload.get("interaction_contract"), Mapping):
        return payload
    from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from ..control_plane.quota.error_codes import QuotaDecisionTransportError
    from ..control_plane.quota.turn_envelope import compose_turn_decision

    try:
        return compose_turn_decision(payload, scheduler_execution_context=scheduler_context,
            composed_settlement=True)
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError) as exc:
        from .quota_capture import capture_decision
        from .quota_failure_report import quota_decision_transport_diagnostic

        error = QuotaDecisionTransportError("Complete Turn context could not be composed.")
        # Admission may be durable. Retain its receipts through the existing
        # failure projection so saved-context readers cannot deliver the
        # unsigned input or register it as a current native claim source.
        capture_decision(
            capture_directory, quota_decision_transport_diagnostic(args, payload, error), record_capture=record_capture,
        )
        raise error from exc


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
    )
    from ..presentation.renderers.turn_envelope_markdown import (
        render_turn_context_markdown,
    )

    command = args.quota_command
    if command == "should-run":
        return render_turn_context_markdown
    return {
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
    )

    reject_retired_quota_format(payload)
    if payload.get("schema_version") == COMPLETE_TURN_DECISION_VERSION:
        return payload
    if payload.get("ok") is False and not isinstance(payload.get("interaction_contract"), Mapping):
        if (getattr(args, "quota_command", None) == "monitor-poll"
                and isinstance(payload.get("before"), Mapping)):
            # A refused poll still has its decided before snapshot. Its
            # existing compaction is pure and does not reenter the runtime.
            return compact_quota_monitor_poll_cli_payload(
                payload, include_decision_detail="decisions" in detail_sections,
            )
        # A collection failure has no decided contract and must not reenter
        # its unavailable runtime. Decided refusals still need signed views.
        # Keep the decided diagnostic, including its original recovery/receipt.
        return payload
    if args.quota_command in {"status", "plan"}:
        return compact_quota_plan_cli_payload(payload, detail_sections=detail_sections)
    if args.quota_command == "monitor-poll":
        return compact_quota_monitor_poll_cli_payload(
            payload, include_decision_detail="decisions" in detail_sections,
        )
    return payload
