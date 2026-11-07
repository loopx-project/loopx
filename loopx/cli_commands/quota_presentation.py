"""Quota CLI presentation transport; TypeScript retains decision ownership."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping

from ..control_plane.effect_runtime import EffectRuntimeRejected
from ..control_plane.quota.turn_envelope import build_turn_envelope


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
                from ..control_plane.quota.turn_envelope import _turn_envelope_result
                payload = _turn_envelope_result("replan_cli_view", payload=payload)
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
