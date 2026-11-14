"""Quota CLI presentation transport; TypeScript retains decision ownership."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..control_plane.scheduler.execution_context import SchedulerExecutionContextResolution

from ..control_plane.effect_runtime import EffectRuntimeRejected


def attach_quota_settlement_guidance(
    payload: dict[str, object], *, registry_path: Path, runtime_root: Path | None,
) -> None:
    # Attach only to the plan chosen by recovery, before model capture/rendering.
    contract = payload.get("interaction_contract")
    cli = contract.get("cli_channel") if isinstance(contract, Mapping) else None
    if isinstance(cli, dict) and isinstance(cli.get("settlement_plan"), Mapping):
        from ..capabilities.explore.turn_context import project_settlement_attachment

        cli["settlement_plan"] = project_settlement_attachment(
            cli["settlement_plan"], registry_path=registry_path, runtime_root=runtime_root,
        )


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
        app = source.get("app_automation")
        if isinstance(app, Mapping) and app.get("host_surface") == "antigravity_app":
            # The sidecar reads these existing scheduler fields directly.
            hint["app_automation"] = select(
                app, "recommended_interval_minutes", "recommended_rrule", "stateful_backoff",
            )
            for key in ("ack_hint", "failure_hint"):
                if key in app:
                    hint["app_automation"][key] = select(app[key], "cli_args")
        for key, field in (("reset_policy", "reset_token"), ("cold_path_detail", "local_scheduler")):
            if key in source:
                hint[key] = select(source[key], field)
        if isinstance(unchanged := source.get("unchanged_poll"), Mapping):
            hint["unchanged_poll"] = {
                **select(unchanged, "local_scheduler", "final_quota_replan_check_enabled",
                         "final_quota_replan_check_action"),
                **{key: select(unchanged[key], "local_scheduler")
                   for key in ("limits", "after_limits") if key in unchanged},
            }
    view["scheduler_hint"] = hint
    if isinstance(frontier := payload.get("goal_frontier_projection"), Mapping):
        view["goal_frontier_projection"] = {
            "terminal_state": select(frontier.get("terminal_state"), "schema_version", "kind", "derived", "source"),
            "source_completeness": select(frontier.get("source_completeness"),
                "schema_version", "user_todos", "agent_todos"),
        }
    for key, fields in (
        ("selected_todo", ("todo_id",)),
        ("agent_identity", ("agent_id",)),
        ("autonomous_replan_obligation", ("required", "obligation_id", "agent_id", "resolution_mode")),
        ("heartbeat_receipt", ("settlement_identity",)),
        ("host_poll_receipt", ("recorded", "poll_count", "last_poll_at", "expected_continuation", "error")),
    ):
        if key in payload:
            view[key] = select(payload[key], *fields)
    return view


def budgeted_spend_result(payload, args, *, runtime_root, reader_argv):
    """Present an already evaluated spend, retaining failed-delivery controls."""
    from .quota_capture import budgeted_settlement_result, settlement_capture_failure_view
    from ..control_plane.quota.error_codes import QuotaDecisionTransportError

    try:
        return budgeted_settlement_result(payload, runtime_root=runtime_root,
            output_bytes=args.max_page_output_bytes, reader_argv=reader_argv), 0 if payload.get("ok") is True else 1
    except QuotaDecisionTransportError as exc:
        return settlement_capture_failure_view(payload, output_bytes=args.max_page_output_bytes, error=exc), 1


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
    decision_path = capture_directory / "decision.json"
    prepared = prepare_codex_context(payload, directory=directory,
        capture_directory=capture_directory, goal_id=decision_identity[0], agent_id=decision_identity[1],
        surface=quota_context_surface(captured_payload, args),
        capture_id=capture_id, continuity_generation=getattr(args, "_context_delivery_generation", None),
        captured_decision_path=str(decision_path) if progressive_enabled else None,
        progressive_enabled=progressive_enabled, captured_recovery={
            "path": str(decision_path), "sha256": capture_id,
            "command": context_capture_read_command(decision_path, capture_id),
        })
    payload = prepared.delivery
    recovery = payload.get("recovery")
    payload = native_context_capture_view(payload, capture_directory,
        context_directory=directory if prepared.stateless_recovery is None else None,
        captured_recovery=prepared.stateless_recovery,
        source_replan_review=prepared.source_replan_review,
        progressive_enabled=progressive_enabled,
        metadata={"capture_sha256": capture_id, **({"recovery": recovery} if recovery is not None else {})},
        max_page_output_bytes=getattr(args, "max_page_output_bytes", None), reader_argv=reader_argv,
        initial_read_plan=prepared.initial_read_plan)
    return model_context_reference(payload, reader_argv=reader_argv)


def compose_quota_cli_source(
    payload: dict[str, object], args: argparse.Namespace, activation: Mapping[str, object], *,
    scheduler_context: Mapping[str, object] | SchedulerExecutionContextResolution | None,
    capture_directory: Path | None,
    compose: Callable[..., dict[str, object]],
    capability_context: list[dict[str, object]],
    share_inline_diagnostics: bool = False,
) -> tuple[dict[str, object], bool, dict[str, object] | None]:
    """Prepare one decided source and an optional bounded compatibility display."""
    # Explicit complete-source JSON and Markdown readers share one source.
    # The legacy flag with delivery off selects the bounded compatibility view.
    raw_envelope = (getattr(args, "turn_envelope", False)
        and not activation["enabled"]
        and activation.get("requested") == "off"
        and not getattr(args, "decision_handoff_socket", None))
    if not isinstance(payload.get("interaction_contract"), Mapping):
        return payload, raw_envelope, None
    if not raw_envelope:
        # Reuse the complete composer's lossless diagnostic encoding for
        # inline readers; native and compatibility views keep their layout.
        inline_diagnostics = payload.get("ok") is True and (share_inline_diagnostics or (
            getattr(args, "complete_turn", False)
            and getattr(args, "context_delivery", None) == "off" and not activation["enabled"]
            and capture_directory is None
            and not getattr(args, "turn_instance_id", None) and not getattr(args, "begin_turn", False)))
        return compose(payload, scheduler_execution_context=scheduler_context,
            **({"capability_context": capability_context} if capability_context else {}),
            **({"share_inline_diagnostics": True} if inline_diagnostics else {})), False, None

    from ..control_plane.quota.turn_envelope import compose_turn_decision_with_raw_envelope

    source, view = compose_turn_decision_with_raw_envelope(payload,
        scheduler_execution_context=scheduler_context, capability_context=capability_context,
        captured_decision_path=str(capture_directory / "decision.json") if capture_directory else None)
    return source, True, view


def raw_quota_turn_view(
    source: dict[str, object], view: dict[str, object] | None,
) -> dict[str, object]:
    """Finalize the display after the complete source has been captured."""
    from ..control_plane.quota.error_codes import QuotaDecisionTransportError

    if view is None:
        if isinstance(source.get("interaction_contract"), Mapping):
            raise QuotaDecisionTransportError(
                "Bounded Turn envelope projection failed after guard evaluation. "
                "Inspect the original capture or Turn receipt; do not rerun admission."
            )
        return {**source, "turn_envelope_skipped": "quota request failed; original diagnostic retained"}
    if "host_poll_receipt" in source:
        view["host_poll_receipt"] = source["host_poll_receipt"]
    return view


def _project_native_cli_settlement(payload: dict[str, object], *, explicit_complete: bool = False) -> dict[str, object]:
    """Publish one projected plan before capability binding and capture."""
    from ..control_plane.effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError, effect_runtime_result
    from ..control_plane.quota.turn_envelope import _apply_context_projection_changes

    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, Mapping) else None
    todo = payload.get("selected_todo")
    if not isinstance(cli, Mapping) or not isinstance(todo, Mapping):
        return payload
    complete_scope = {}
    if explicit_complete:
        agent = payload.get("agent_identity")
        channel = interaction.get("agent_channel") if isinstance(interaction, Mapping) else None
        # The typed owner needs eligibility, not another copy of Goal/context.
        complete_scope = {"complete_scope": {
            **{key: payload.get(key) for key in (
                "goal_id", "should_run", "effective_action", "normal_delivery_allowed",
            )},
            "agent_id": agent.get("agent_id") if isinstance(agent, Mapping) else None,
            "delivery_allowed": channel.get("delivery_allowed") if isinstance(channel, Mapping) else None,
        }}
    try:
        projection = effect_runtime_result("settlement.native_cli_projection", {
            "cli": cli, "receipt": payload.get("heartbeat_receipt"), "todo_id": todo.get("todo_id"),
            **complete_scope,
        })
    except (EffectRuntimeRemoteError, EffectRuntimeStartupError):
        # The already admitted Turn still has its canonical manual plan.
        return payload
    if not projection:
        return payload
    return {**payload, "interaction_contract": {
        **interaction, "cli_channel": _apply_context_projection_changes(dict(cli), projection),
    }}


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
    *,
    ordinary_source: bool = False,
    capture_directory: Path | None = None,
) -> dict[str, object]:
    """Project decided contracts; failed collection retains its diagnostic.

    Collection failures must not reenter their unavailable runtime (issue #3687).
    Explicit complete-source inputs have already been composed.
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
        # its unavailable runtime. An explicit complete-source refusal still
        # keeps its signed view.
        # Keep the decided diagnostic, including its original recovery/receipt.
        return payload
    if args.quota_command == "should-run":
        if ordinary_source:
            from ..control_plane.quota.cli_projection import compact_quota_should_run_cli_payload
            from ..control_plane.quota.turn_envelope import project_captured_replan_cli_view

            if capture_directory is not None and "vision" not in detail_sections:
                payload = project_captured_replan_cli_view(payload)
            return compact_quota_should_run_cli_payload(
                payload,
                include_todo_summary_detail="agent-todos" in detail_sections,
                include_user_todo_summary_detail="user-todos" in detail_sections,
                include_goal_boundary_detail="goal-boundary" in detail_sections,
                include_vision_detail="vision" in detail_sections,
            )
        if (payload.get("schema_version") == "loopx_turn_envelope_v1"
                and isinstance(payload.get("interaction_contract"), Mapping)):
            return payload
        raise EffectRuntimeRejected(
            "should-run emission requires a composed complete decision; "
            "historical captures require their explicit recovery reader"
        )
    if args.quota_command in {"status", "plan"}:
        return compact_quota_plan_cli_payload(payload, detail_sections=detail_sections)
    if args.quota_command == "monitor-poll":
        return compact_quota_monitor_poll_cli_payload(
            payload, include_decision_detail="decisions" in detail_sections,
        )
    return payload
