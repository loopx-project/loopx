from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ..todos.contract import (
    normalize_todo_id,
)
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


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
    legacy_batch_version: int | None = 1,
) -> dict[str, Any] | None:
    """Route one typed Monitor transaction to its owning storage adapter."""
    from .legacy_monitor_poll import apply_legacy_monitor_poll, replay_legacy_monitor_poll
    from .provider_monitor_poll import poll_canonical_monitor_if_promoted

    lease_proof = ({"idempotency_key": task_lease_idempotency_key,
                    "expected_version": task_lease_expected_version}
                   if task_lease_idempotency_key is not None or task_lease_expected_version is not None else None)
    if not todo_id and not target_key:
        if lease_proof is not None:
            raise ValueError("lease proof requires a Monitor target")
        return None
    observation = {"todo_id": todo_id, "target_key": target_key,
        "result_hash": result_hash, "material_change": material_change,
        "generated_at": generated_at, "cadence": cadence,
        "next_due_at": next_due_at, "reason_summary": reason_summary}
    intent = {"next_agent_todo": next_agent_todo, "next_action_kind": next_action_kind,
        "next_task_repository": next_task_repository,
        "next_required_capabilities": next_required_capabilities or [],
        "next_continuation_policy": next_continuation_policy,
        "next_target_key": next_target_key, "next_claimed_by": next_claimed_by,
        "next_user_todo": next_user_todo, "next_user_task_class": next_user_task_class}
    request = {"schema_version": "loopx_monitor_batch_plan_request_v0",
        "goal_id": goal_id, "operation_id": monitor_effect_id or f"monitor-poll:{goal_id}:{uuid4().hex}",
        "actor_agent_id": agent_id, "dry_run": not execute,
        "observation": observation, "intent": intent,
        "gate_scope_guard": gate_scope_guard, "legacy_batch_version": legacy_batch_version}
    if monitor_effect_id and lease_proof is None:
        # Legacy receipts are historical facts even after authority promotion.
        previous = replay_legacy_monitor_poll(registry_path=registry_path, request=request)
        if previous is not None:
            from ..coordination.local_authority import local_authority_is_promoted
            from ..coordination.local_authority_shadow_adapter import drain_local_authority_shadow_outbox
            from ..coordination.local_authority_shadow_outbox import outbox_root

            # A crash after the one Markdown install can leave its prepared
            # shadow entry without a commit marker. The existing drain proves
            # that source replacement and recovers it from durable readback.
            # Historical receipts after promotion must not drain stale legacy
            # state into the canonical authority.
            if (outbox_root(runtime_root, goal_id).exists() and
                    not local_authority_is_promoted(runtime_root=runtime_root, goal_id=goal_id)):
                drain_local_authority_shadow_outbox(
                    registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id)
            return previous
    canonical = poll_canonical_monitor_if_promoted(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        execute=execute, monitor_effect_id=monitor_effect_id, agent_id=agent_id,
        lease_proof=lease_proof, gate_scope_guard=gate_scope_guard,
        observation=observation, intent=intent,
    )
    if canonical is not None:
        return canonical
    if lease_proof is not None:
        raise ValueError("Monitor lease proof requires promoted canonical authority; no legacy write attempted")
    return apply_legacy_monitor_poll(
        registry_path=registry_path, runtime_root=runtime_root, request=request)
