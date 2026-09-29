from __future__ import annotations

from pathlib import Path
from typing import Any

from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_USER_GATE,
    normalize_todo_id,
)
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..todos.monitor_metadata import MonitorPollObservation


def resolve_monitor_todo_item(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: Path | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
) -> dict[str, Any]:
    from ...todos import list_goal_todos

    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    safe_target_key = str(target_key or "").strip()
    if not normalized_todo_id and not safe_target_key:
        raise ValueError("monitor todo writeback requires --todo-id or --target-key")
    payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
        runtime_root_arg=str(runtime_root) if runtime_root is not None else None,
    )
    items = payload.get("todos") if isinstance(payload.get("todos"), list) else []
    try:
        result = effect_runtime_result("scheduler.monitor_target.select", {
            "schema_version": "loopx_monitor_target_request_v0", "items": items,
            "todo_id": normalized_todo_id, "target_key": safe_target_key or None,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict) or result.get("schema_version") != "loopx_monitor_target_result_v0":
        raise TypeError("TypeScript Monitor target selection shape mismatch")
    return result["todo"]


def write_monitor_poll_todo_state(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    generated_at: str,
    execute: bool,
    monitor_effect_id: str | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
    result_hash: str | None = None,
    material_change: bool = False,
    cadence: str | None = None,
    next_due_at: str | None = None,
    reason_summary: str | None = None,
    next_agent_todo: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: list[str] | None = None,
    next_continuation_policy: str | None = None,
    next_target_key: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    agent_id: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    gate_scope_guard: bool = False,
) -> dict[str, Any] | None:
    """Apply one monitor poll observation as a complete Todo writeback.

    ``runtime_root`` is the effective runtime root of the calling CLI
    composition (the ``--runtime-root`` override when given).  Every legacy
    Todo mutation below shares that root, so the writer fence and the todo
    mutex of a promotion cannot split from the writeback path.  The parameter
    is required on purpose: callers must compose the root instead of relying
    on a registry-derived fallback that promotion cannot fence.
    """

    from ...todos import add_goal_todo, update_goal_todo
    from ..coordination.legacy_writer_fence import (
        require_legacy_coordination_write_allowed,
    )

    lease_proof = ({"idempotency_key": task_lease_idempotency_key,
                    "expected_version": task_lease_expected_version}
                   if task_lease_idempotency_key is not None or task_lease_expected_version is not None else None)
    if not todo_id and not target_key:
        if lease_proof is not None:
            raise ValueError("lease proof requires a Monitor target")
        return None
    from .provider_monitor_poll import poll_canonical_monitor_if_promoted

    canonical = poll_canonical_monitor_if_promoted(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        execute=execute, monitor_effect_id=monitor_effect_id, agent_id=agent_id,
        lease_proof=lease_proof, gate_scope_guard=gate_scope_guard,
        observation={"todo_id": todo_id, "target_key": target_key,
            "result_hash": result_hash, "material_change": material_change,
            "generated_at": generated_at, "cadence": cadence,
            "next_due_at": next_due_at, "reason_summary": reason_summary},
        intent={"next_agent_todo": next_agent_todo, "next_action_kind": next_action_kind,
            "next_task_repository": next_task_repository,
            "next_required_capabilities": next_required_capabilities or [],
            "next_continuation_policy": next_continuation_policy,
            "next_target_key": next_target_key, "next_claimed_by": next_claimed_by,
            "next_user_todo": next_user_todo, "next_user_task_class": next_user_task_class},
    )
    if canonical is not None:
        return canonical
    if lease_proof is not None:
        raise ValueError("Monitor lease proof requires promoted canonical authority; no legacy write attempted")
    if execute:
        require_legacy_coordination_write_allowed(
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
    safe_result_hash = str(result_hash or "").strip()
    if not safe_result_hash:
        raise ValueError("monitor todo writeback requires --result-hash")
    item = resolve_monitor_todo_item(
        registry_path=registry_path,
        goal_id=goal_id,
        runtime_root=runtime_root,
        todo_id=todo_id,
        target_key=target_key,
    )
    resolved_todo_id = normalize_todo_id(item.get("todo_id"))
    if not resolved_todo_id:
        raise ValueError("resolved monitor todo has no stable todo_id")
    # The typed plan validates the complete successor intent before the first
    # write. Source lookup and actual effects remain in this adapter.
    try:
        plan = effect_runtime_result("scheduler.monitor_successor.plan", {
            "schema_version": "loopx_monitor_successor_plan_request_v0",
            "todo_id": resolved_todo_id, "result_hash": safe_result_hash,
            "source_task_repository": item.get("task_repository"),
            "intent": {
                "material_change": material_change,
                "next_agent_todo": next_agent_todo, "next_action_kind": next_action_kind,
                "next_task_repository": next_task_repository,
                "next_required_capabilities": next_required_capabilities or [],
                "next_continuation_policy": next_continuation_policy,
                "next_target_key": next_target_key, "next_claimed_by": next_claimed_by,
                "next_user_todo": next_user_todo, "next_user_task_class": next_user_task_class,
            },
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != "loopx_monitor_successor_plan_result_v0":
        raise TypeError("TypeScript monitor successor plan shape mismatch")
    successor_route = plan["agent_route"]
    intent = plan["intent"]
    next_agent_todo = intent["next_agent_todo"]
    next_user_todo = intent["next_user_todo"]
    effective_next_user_task_class = intent["next_user_task_class"]
    safe_target_key = str(target_key or "").strip()
    update_result = update_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=resolved_todo_id,
        role="agent",
        reason=reason_summary,
        runtime_root_arg=str(runtime_root),
        monitor_metadata=MonitorPollObservation(
            generated_at=generated_at,
            result_hash=safe_result_hash,
            material_change=material_change,
            monitor_effect_id=monitor_effect_id,
            target_key=safe_target_key or None,
            cadence=cadence,
            next_due_at=next_due_at,
        ),
        enforce_monitor_boundedness=False,
        monitor_gate_scope_guard=gate_scope_guard,
        agent_id=agent_id,
        dry_run=not execute,
    )
    poll_transition = update_result.get("monitor_poll_transition")
    if not isinstance(poll_transition, dict):
        raise TypeError("monitor poll Todo update returned no transition receipt")
    material_change_generation = int(
        poll_transition["material_change_generation"]
    )
    consecutive_no_change = int(poll_transition["consecutive_no_change"])
    effective_next_due_at = poll_transition.get("next_due_at")
    effective_cadence = str(poll_transition.get("cadence") or "")
    safe_target_key = str(poll_transition.get("target_key") or "")
    next_results: list[dict[str, Any]] = []
    if material_change and next_agent_todo:
        next_results.append(
            add_goal_todo(
                registry_path=registry_path,
                goal_id=goal_id,
                role="agent",
                text=next_agent_todo,
                runtime_root_arg=str(runtime_root),
                task_class=TODO_TASK_CLASS_ADVANCEMENT,
                action_kind=successor_route["action_kind"],
                task_repository=successor_route["task_repository"],
                continuation_policy=successor_route["continuation_policy"],
                required_capabilities=successor_route["required_capabilities"],
                claimed_by=successor_route["claimed_by"],
                unblocks_todo_id=resolved_todo_id,
                monitor_metadata={"target_key": successor_route["target_key"]},
                dry_run=not execute,
            )
        )
    if material_change and next_user_todo:
        next_results.append(
            add_goal_todo(
                registry_path=registry_path,
                goal_id=goal_id,
                role="user",
                text=next_user_todo,
                runtime_root_arg=str(runtime_root),
                task_class=effective_next_user_task_class,
                action_kind=(
                    "gate"
                    if effective_next_user_task_class == TODO_TASK_CLASS_USER_GATE
                    else None
                ),
                agent_id=agent_id,
                unblocks_todo_id=(
                    resolved_todo_id
                    if effective_next_user_task_class == TODO_TASK_CLASS_USER_GATE
                    else None
                ),
                dry_run=not execute,
            )
        )
    successor_receipts = [
        {
            key: result.get(key)
            for key in (
                "todo_id",
                "role",
                "task_class",
                "action_kind",
                "task_repository",
                "continuation_policy",
                "required_capabilities",
                "claimed_by",
                "unblocks_todo_id",
                "target_key",
            )
            if result.get(key) not in (None, "", [])
        }
        for result in next_results
    ]
    result = {
        "schema_version": "monitor_poll_todo_writeback_v0",
        "dry_run": not execute,
        "goal_id": goal_id,
        "todo_id": resolved_todo_id,
        "target_key": safe_target_key or None,
        "result_hash": safe_result_hash,
        "material_change": material_change,
        "material_change_generation": material_change_generation,
        "consecutive_no_change": consecutive_no_change,
        "last_checked_at": generated_at,
        "next_due_at": effective_next_due_at,
        "cadence": effective_cadence or None,
        "todo_update": update_result,
        "next_todos": next_results,
        "successor_receipts": successor_receipts,
    }
    if monitor_effect_id:
        result["monitor_effect_id"] = monitor_effect_id
        result["provider_replayed"] = bool(
            poll_transition.get("provider_replayed")
        )
    return result
