from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..control_plane.coordination.local_authority import CanonicalAuthorityRequired
from ..control_plane.capability_hooks import InteractionProjectionHookRegistration
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.error_codes import HeartbeatReceiptIdentityConflictError
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_settlement_todo_id,
)
from ..control_plane.scheduler.execution_context import (
    SchedulerExecutionContextResolution,
)

if TYPE_CHECKING:
    from .quota_action_selection import RequestedQuotaActionSelection
    from .quota_context import QuotaCommandContext


def _receipt_bound_monitor_todo_id(
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    turn_instance_id: str | None,
    goal_ref: Mapping[str, object] | None,
) -> str | None:
    if not turn_instance_id:
        return None
    receipt = find_heartbeat_receipt(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )
    if receipt is None:
        raise HeartbeatReceiptIdentityConflictError(
            "turn-scoped monitor-poll requires a committed heartbeat receipt; "
            "run quota should-run with the same --turn-instance-id first"
        )
    todo_id = heartbeat_receipt_settlement_todo_id(receipt)
    if todo_id is None:
        raise HeartbeatReceiptIdentityConflictError(
            "turn-scoped monitor-poll requires a heartbeat receipt bound to a Todo"
        )
    return todo_id


def record_quota_monitor_poll_for_cli(
    args: argparse.Namespace,
    *,
    status_payload: dict[str, object],
    registry_path: Path,
    runtime_root: Path,
    turn_instance_id: str | None,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ),
    operator_inbox_urgency_projector: Callable[..., dict[str, object]],
    status_reloader: Callable[[], dict[str, object]],
    monitor_poll_recorder: Callable[..., dict[str, object]],
    goal_ref: Mapping[str, object] | None = None,
    original_receipt_only: bool = False,
) -> dict[str, object]:
    """Execute the monitor-poll CLI request with receipt-bound settlement identity."""
    arguments = dict(
        goal_id=args.goal_id,
        registry_path=registry_path,
        execute=bool(args.execute),
        source=args.source,
        reason_summary=args.reason_summary,
        agent_id=args.agent_id,
        todo_id=args.todo_id,
        target_key=args.target_key,
        result_hash=args.result_hash,
        material_change=bool(args.material_change),
        cadence=args.cadence,
        next_due_at=args.next_due_at,
        next_agent_todo=args.next_agent_todo,
        next_agent_priority=getattr(args, "next_agent_priority", None),
        next_user_priority=getattr(args, "next_user_priority", None),
        next_action_kind=args.next_action_kind,
        next_task_repository=args.next_task_repository,
        next_required_capabilities=args.next_required_capabilities,
        next_continuation_policy=args.next_continuation_policy,
        next_target_key=args.next_target_key,
        next_user_todo=args.next_user_todo,
        next_user_task_class=args.next_user_task_class,
        next_claimed_by=args.next_claimed_by,
        task_lease_idempotency_key=getattr(args, "task_lease_idempotency_key", None),
        task_lease_expected_version=getattr(args, "task_lease_expected_version", None),
        use_current_task_lease=bool(getattr(args, "use_current_task_lease", False)),
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
        receipt_bound_todo_id=_receipt_bound_monitor_todo_id(
            args,
            runtime_root=runtime_root,
            turn_instance_id=turn_instance_id,
            goal_ref=goal_ref,
        ),
    )
    if original_receipt_only:
        from ..control_plane.quota.monitor_poll import record_quota_monitor_poll_for_decision

        # No current execution is admitted when normal reads require migration.
        # The typed transaction must find its exact retained admission/receipt;
        # the recorder refuses a missing original before reserving fresh work.
        before = {"goal_id": args.goal_id, "should_run": False,
                  "state": "blocked_health", "agent_identity": {"agent_id": args.agent_id}}
        settlement_todo_id = arguments.pop("receipt_bound_todo_id")
        return record_quota_monitor_poll_for_decision(
            before, status_payload, **arguments,
            settlement_todo_id=settlement_todo_id,
            after_decision=lambda _status: before, render_markdown=lambda _payload: "",
        )
    from ..capabilities.explore.composition_frontier import (
        project_live_explore_composition_frontier,
    )

    return monitor_poll_recorder(
        status_payload, **arguments,
        available_capabilities=args.available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        status_reloader=status_reloader,
    )


def recover_quota_monitor_poll_for_cli(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    error: CanonicalAuthorityRequired,
    goal_ref_loader: Callable[[], Mapping[str, object] | None],
) -> dict[str, object]:
    """Recover an original Monitor after normal status requires migration."""
    from ..paths import effective_runtime_root
    from ..quota import record_quota_monitor_poll
    from ..turn_identity import normalize_turn_instance_id
    from .quota_failure_report import quota_failure_payload

    if args.quota_command == "monitor-poll" and args.execute:
        try:
            root = effective_runtime_root(registry_path, runtime_root_arg)
            return record_quota_monitor_poll_for_cli(
                args, registry_path=registry_path, runtime_root=root,
                status_payload={"runtime_root": str(root)},
                turn_instance_id=normalize_turn_instance_id(args.turn_instance_id),
                goal_ref=goal_ref_loader(), scheduler_execution_context=None,
                operator_inbox_urgency_projector=lambda **_kwargs: {},
                status_reloader=lambda: {}, monitor_poll_recorder=record_quota_monitor_poll,
                original_receipt_only=True,
            )
        except Exception as recovery_error:  # noqa: BLE001 - typed CLI fail-safe boundary.
            return quota_failure_payload(
                args, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
                error=recovery_error,
            )
    return quota_failure_payload(
        args, registry_path=registry_path, runtime_root_arg=runtime_root_arg, error=error,
    )


def record_automatic_heartbeat_stall(
    payload: dict[str, object],
    status_payload: dict[str, object],
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    context: QuotaCommandContext,
    interaction_projection_hooks: tuple[InteractionProjectionHookRegistration, ...],
    turn_start_hook_dispatch: Mapping[str, object] | None,
    action_selection: RequestedQuotaActionSelection,
    cache_metadata: object,
    goal_ref: Mapping[str, object] | None,
    find_monitor_poll_turn: Callable[..., Mapping[str, object] | None],
    monitor_poll_recorder: Callable[..., dict[str, object]],
    status_collector: Callable[..., dict[str, object]],
    decision_builder: Callable[..., dict[str, object]],
    host_observation_resolver: Callable[..., object],
) -> tuple[dict[str, object], dict[str, object], object, str]:
    """Commit and reproject the automatic no-spend heartbeat observation."""

    turn_id = context.heartbeat_turn_id
    if turn_id is None:
        return payload, status_payload, cache_metadata, "not_applicable"
    existing_stall = find_monitor_poll_turn(
        context.runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        turn_instance_id=turn_id,
        goal_ref=goal_ref,
    )
    if (
        payload.get("effective_action")
        != EffectiveAction.MONITOR_QUIET_SKIP.value
        and existing_stall is None
    ):
        return payload, status_payload, cache_metadata, "not_applicable"
    from ..capabilities.explore.composition_frontier import (
        project_live_explore_composition_frontier,
    )

    poll = monitor_poll_recorder(
        status_payload,
        goal_id=args.goal_id,
        registry_path=registry_path,
        execute=True,
        source="heartbeat",
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        turn_instance_id=turn_id,
        goal_ref=goal_ref,
        scheduler_execution_context=context.scheduler_context,
        operator_inbox_urgency_projector=context.operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
    )
    if not poll.get("ok"):
        raise RuntimeError(
            "heartbeat stall observation writeback failed: "
            f"{poll.get('reason') or 'missing follow-up quota decision'}"
        )
    reloaded = status_collector(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        limit=context.status_limit,
        goal_id=context.status_goal_id,
        available_capabilities=args.available_capabilities,
        include_presentation_projections=False,
    )
    rebuilt = decision_builder(
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
        host_observation_resolver=host_observation_resolver,
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
        turn_start_hook_dispatch=turn_start_hook_dispatch,
        goal_ref=goal_ref,
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
