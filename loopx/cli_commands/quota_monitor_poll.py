from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path

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
) -> dict[str, object]:
    """Execute the monitor-poll CLI request with receipt-bound settlement identity."""
    return monitor_poll_recorder(
        status_payload,
        goal_id=args.goal_id,
        registry_path=registry_path,
        execute=bool(args.execute),
        source=args.source,
        reason_summary=args.reason_summary,
        agent_id=args.agent_id,
        available_capabilities=args.available_capabilities,
        todo_id=args.todo_id,
        target_key=args.target_key,
        result_hash=args.result_hash,
        material_change=bool(args.material_change),
        cadence=args.cadence,
        next_due_at=args.next_due_at,
        next_agent_todo=args.next_agent_todo,
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
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        status_reloader=status_reloader,
    )
