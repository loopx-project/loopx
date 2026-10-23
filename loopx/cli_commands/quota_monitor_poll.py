from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.coordination.local_authority import CanonicalAuthorityRequired
from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..control_plane.quota.error_codes import HeartbeatReceiptIdentityConflictError
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_settlement_todo_id,
)
from ..control_plane.scheduler.execution_context import (
    SchedulerExecutionContextResolution,
)


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
