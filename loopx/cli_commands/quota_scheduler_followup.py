from __future__ import annotations
from ..control_plane.quota.effective_action import EffectiveAction

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..capabilities.repository_change_window import (
    repository_delivery_interaction_hook,
)
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_settlement_replan_obligation_id,
    heartbeat_receipt_settlement_todo_id,
)
from ..control_plane.quota.live_decision import build_live_quota_should_run_decision
from ..control_plane.quota.scheduler_ack import (
    record_quota_scheduler_failure_for_decision,
)
from ..control_plane.scheduler.execution_context import (
    HostSurface,
    SchedulerExecutionContextResolution,
    resolve_scheduler_execution_context,
)
from ..quota import record_quota_scheduler_ack
from ..upgrade import resolve_codex_app_automation_rrule


def _build_scheduler_followup_decision(
    status_payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    heartbeat_receipt: Mapping[str, object] | None,
    turn_instance_id: str | None,
    codex_app_current_rrule: str | None,
    scheduler_context: Mapping[str, object]
    | SchedulerExecutionContextResolution
    | None,
    operator_inbox_urgency_projector: Callable[..., dict[str, object]],
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Rebuild a scheduler follow-up from the originating receipt-bound Turn."""

    receipt_todo_id = (
        heartbeat_receipt_settlement_todo_id(heartbeat_receipt)
        if heartbeat_receipt
        else None
    )
    receipt_replan_id = (
        heartbeat_receipt_settlement_replan_obligation_id(heartbeat_receipt)
        if heartbeat_receipt
        else None
    )
    return build_live_quota_should_run_decision(
        status_payload,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        include_scheduler_detail=False,
        codex_app_current_rrule=codex_app_current_rrule,
        registry_path=registry_path,
        runtime_root=runtime_root,
        host_observation_resolver=resolve_codex_app_automation_rrule,
        scheduler_execution_context=scheduler_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        receipt_bound_todo_id=receipt_todo_id,
        receipt_bound_replan_obligation_id=receipt_replan_id,
        turn_instance_id=turn_instance_id,
        interaction_projection_hooks=(
            repository_delivery_interaction_hook(repo_path=Path.cwd()),
        ),
        goal_ref=goal_ref,
    )


def build_scheduler_followup_payload(
    status_payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    turn_instance_id: str | None,
    scheduler_context: Mapping[str, object]
    | SchedulerExecutionContextResolution
    | None,
    operator_inbox_urgency_projector: Callable[..., dict[str, object]],
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Execute one scheduler ACK/failure command against its live decision."""

    heartbeat_receipt = (
        find_heartbeat_receipt(
            runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        )
        if turn_instance_id
        else None
    )
    if turn_instance_id and heartbeat_receipt is None:
        return {
            "ok": False,
            "schema_version": "quota_scheduler_followup_receipt_failure_v0",
            "mode": args.quota_command,
            "goal_id": args.goal_id,
            "agent_id": args.agent_id,
            "turn_instance_id": turn_instance_id,
            "decision": "skip",
            "should_run": False,
            "status": "heartbeat_receipt_missing",
            "state": "blocked_receipt",
            "error_code": "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING",
            "reason": (
                "scheduler follow-up requires the originating heartbeat receipt; "
                "refusing to rebuild authority from the current live frontier"
            ),
            "retry_guidance": (
                "retry with the exact Turn id from a committed quota should-run "
                "heartbeat receipt"
            ),
            "write_performed": False,
            "appended": False,
            "registry_mutated": False,
            "scheduler_state_mutated": False,
            "quota_spend_performed": False,
            "delivery_outcome": "surface_only",
        }

    observed_rrule = str(
        getattr(args, "app_automation_current_rrule", None)
        or getattr(args, "codex_app_current_rrule", None)
        or ""
    ).strip()
    resolved_context = resolve_scheduler_execution_context(scheduler_context)
    codex_app_host = bool(
        resolved_context.ok
        and resolved_context.context is not None
        and resolved_context.context.host_surface is HostSurface.CODEX_APP
    )
    if (
        args.quota_command == "scheduler-fail-current"
        and not observed_rrule
        and codex_app_host
    ):
        host_observation = resolve_codex_app_automation_rrule(
            goal_id=args.goal_id,
            agent_id=args.agent_id,
        )
        if host_observation.get("available") is True:
            observed_rrule = str(host_observation.get("rrule") or "")

    before_decision = _build_scheduler_followup_decision(
        status_payload,
        args,
        registry_path=registry_path,
        runtime_root=runtime_root,
        heartbeat_receipt=heartbeat_receipt,
        turn_instance_id=turn_instance_id,
        codex_app_current_rrule=(
            args.applied_rrule
            if bool(getattr(args, "host_match_observed", False))
            else observed_rrule
        ),
        scheduler_context=scheduler_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        goal_ref=goal_ref,
    )
    receipt_todo_id = (
        heartbeat_receipt_settlement_todo_id(heartbeat_receipt)
        if heartbeat_receipt
        else None
    )
    receipt_replan_id = (
        heartbeat_receipt_settlement_replan_obligation_id(heartbeat_receipt)
        if heartbeat_receipt
        else None
    )
    if (
        turn_instance_id
        and receipt_todo_id is None
        and receipt_replan_id is not None
        and before_decision.get("effective_action") == EffectiveAction.HEARTBEAT_SETTLED_SKIP.value
    ):
        return {
            "ok": True,
            "schema_version": "quota_scheduler_followup_settled_replay_v0",
            "mode": args.quota_command,
            "goal_id": args.goal_id,
            "agent_id": args.agent_id,
            "turn_instance_id": turn_instance_id,
            "decision": "skip",
            "should_run": False,
            "status": "heartbeat_settled_skip",
            "state": "settled_replay",
            "reason": (
                "the receipt-bound heartbeat turn is already settled; "
                "a fresh turn owns scheduler cadence reconciliation"
            ),
            "idempotent_replay": True,
            "write_performed": False,
            "appended": False,
            "registry_mutated": False,
            "scheduler_state_mutated": False,
            "quota_spend_performed": False,
            "delivery_outcome": "surface_only",
        }
    if args.quota_command == "scheduler-fail-current":
        return record_quota_scheduler_failure_for_decision(
            before_decision,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            execute=bool(args.execute),
            surface=args.surface,
            state_key=args.state_key,
            failed_rrule=args.failed_rrule,
            observed_host_rrule=observed_rrule,
            failure_kind=args.failure_kind,
        )
    return record_quota_scheduler_ack(
        status_payload,
        goal_id=args.goal_id,
        execute=bool(args.execute),
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        surface=args.surface,
        state_key=args.state_key,
        applied_rrule=args.applied_rrule,
        reset_token=args.reset_token,
        identity_signature=args.identity_signature,
        reason_summary=args.reason_summary,
        use_current_hint=bool(
            args.use_current_hint or args.quota_command == "scheduler-ack-current"
        ),
        host_match_observed=bool(getattr(args, "host_match_observed", False)),
        scheduler_execution_context=scheduler_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        before_decision=before_decision,
    )
