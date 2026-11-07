"""Quota CLI presentation transport; TypeScript retains decision ownership."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.effect_runtime import EffectRuntimeRejected


def budgeted_spend_result(payload, args, *, runtime_root, reader_argv):
    """Present an already evaluated spend, retaining failed-delivery controls."""
    from .quota_capture import budgeted_settlement_result
    from ..control_plane.quota.error_codes import QuotaDecisionTransportError

    try:
        return budgeted_settlement_result(payload, runtime_root=runtime_root,
            output_bytes=args.max_page_output_bytes, reader_argv=reader_argv), 0 if payload.get("ok") is True else 1
    except QuotaDecisionTransportError as exc:
        view = {key: value for key, value in payload.items() if key not in ("before", "after")}
        view["result_delivery_error"] = {"error_code": exc.error_code, "reason": str(exc),
            "recommended_action": "Preserve the original result controls and Turn identity. "
                "Repair capture privacy or remove --max-page-output-bytes and read back the "
                "same operation; do not repeat admission or assume it did not commit.",
            "omitted_diagnostics": [key for key in ("before", "after") if key in payload]}
        return view, 1


def native_quota_result(payload, args, *, capture_directory: Path,
                        decision_identity, captured_payload, reader_argv, capture_id: str,
                        progressive_enabled: bool):
    """IO presentation of the immutable decision, without another guard."""
    from .quota_capture import (
        context_capture_read_command, model_context_reference, native_context_capture_view, quota_context_surface,
    )
    from ..control_plane.turn_driver.codex_context import (
        NativeContextSessionUnavailable, codex_context_directory, prepare_codex_context,
    )

    try:
        directory = codex_context_directory(directory=args.codex_context_dir,
            root=getattr(args, "codex_context_root", None), create=True)
    except NativeContextSessionUnavailable:
        # An uninitialized host receives full context without a baseline claim.
        directory = None
    prepared = prepare_codex_context(payload, directory=directory,
        capture_directory=capture_directory, goal_id=decision_identity[0], agent_id=decision_identity[1],
        surface=quota_context_surface(captured_payload, args),
        capture_id=capture_id, continuity_generation=getattr(args, "_context_delivery_generation", None),
        progressive_enabled=progressive_enabled, captured_recovery={
            "path": str(capture_directory / "decision.json"), "sha256": capture_id,
            "command": context_capture_read_command(capture_directory / "decision.json", capture_id),
        } if directory is not None else None)
    payload = prepared.delivery
    recovery = payload.get("recovery")
    payload = native_context_capture_view(payload, capture_directory, context_directory=directory,
        progressive_enabled=progressive_enabled,
        metadata={"capture_sha256": capture_id, **({"recovery": recovery} if recovery is not None else {})},
        max_page_output_bytes=getattr(args, "max_page_output_bytes", None), reader_argv=reader_argv,
        initial_read_plan=prepared.initial_read_plan)
    return model_context_reference(payload, reader_argv=reader_argv)


def _quota_renderer(args: argparse.Namespace) -> Callable[[dict[str, object]], str]:
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
    command = args.quota_command
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
) -> dict[str, object]:
    """Project decided contracts; failed collection retains its diagnostic.

    Collection failures must not reenter their unavailable runtime. Decided
    should-run inputs already carry their complete source (issue #3687).
    """
    from ..control_plane.effect_program import reject_retired_quota_format
    from ..control_plane.quota.cli_projection import (
        compact_quota_monitor_poll_cli_payload,
        compact_quota_plan_cli_payload,
    )

    reject_retired_quota_format(payload)
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
    if args.quota_command == "should-run":
        if (payload.get("schema_version") == "loopx_turn_envelope_v1"
                and isinstance(payload.get("interaction_contract"), Mapping)):
            return payload
        raise EffectRuntimeRejected(
            "should-run emission requires a composed complete decision; "
            "historical captures require their explicit recovery reader"
        )
    # The request owner admits --turn-envelope only on should-run, whose
    # complete decision returned above. No normal route builds a v0 adapter.
    if args.quota_command in {"status", "plan"}:
        return compact_quota_plan_cli_payload(payload, detail_sections=detail_sections)
    if args.quota_command == "monitor-poll":
        return compact_quota_monitor_poll_cli_payload(
            payload, include_decision_detail="decisions" in detail_sections,
        )
    return payload
