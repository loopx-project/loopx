from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..todos.contract import (
    normalize_todo_id,
)
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    read_canonical_todos_if_promoted,
    require_canonical_authority,
)
from ...paths import effective_runtime_root


def resolve_monitor_todo_item(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root: Path | None = None,
    todo_id: str | None = None,
    target_key: str | None = None,
) -> dict[str, Any]:
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    safe_target_key = str(target_key or "").strip()
    if not normalized_todo_id and not safe_target_key:
        raise ValueError("monitor todo writeback requires --todo-id or --target-key")
    authority = read_canonical_todos_if_promoted(
        runtime_root=effective_runtime_root(
            registry_path,
            str(runtime_root) if runtime_root is not None else None,
        ),
        goal_id=goal_id,
    )
    if authority is None:
        require_canonical_authority(goal_id=goal_id, operation="monitor_poll")
    items = [
        item
        for item in authority["todos"]
        if item.get("role") == "agent" and item.get("archive_state") != "archive"
    ]
    # DTO only: selectMonitorTodo remains the rule owner. Preserve the complete
    # candidate set, but keep prose/evidence and schedule fields in the source
    # snapshot. The selected fact is mapped back to its complete original row.
    fields = ("todo_id", "target_key", "status", "archive_state", "role", "task_class")
    facts = [{key: item[key] for key in fields if key in item} for item in items]
    try:
        result = effect_runtime_result("scheduler.monitor_target.select", {
            "schema_version": "loopx_monitor_target_request_v0", "items": facts,
            "todo_id": normalized_todo_id, "target_key": safe_target_key or None,
        })
    except EffectRuntimeRejected as exc:
        # A source/transport outage is not a definitive target rejection.
        # Due lookup catches ValueError for semantic misses only.
        if exc.diagnostic_code != "invalid_request":
            raise
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict) or result.get("schema_version") != "loopx_monitor_target_result_v0":
        raise TypeError("TypeScript Monitor target selection shape mismatch")
    for item, fact in zip(items, facts, strict=True):
        if fact == result.get("todo"):
            return deepcopy(item)
    raise TypeError("TypeScript Monitor target selection does not match its source snapshot")


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
    next_agent_priority: str | None = None,
    next_user_priority: str | None = None,
    agent_id: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    gate_scope_guard: bool = False,
    legacy_batch_version: int | None = None,
) -> dict[str, Any] | None:
    """Route one typed Monitor transaction to its owning storage adapter."""
    from .monitor_batch_receipts import replay_legacy_monitor_poll

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
    # Omitted additive fields must retain old request hashes during replay.
    if next_agent_priority is not None:
        intent["next_agent_priority"] = next_agent_priority
    if next_user_priority is not None:
        intent["next_user_priority"] = next_user_priority
    request = {"schema_version": "loopx_monitor_batch_plan_request_v0",
        "goal_id": goal_id, "operation_id": monitor_effect_id or f"monitor-poll:{goal_id}:{uuid4().hex}",
        "actor_agent_id": agent_id, "dry_run": not execute,
        "observation": observation, "intent": intent,
        "gate_scope_guard": gate_scope_guard, "legacy_batch_version": legacy_batch_version}
    from .provider_monitor_poll import (
        poll_canonical_monitor_if_promoted,
    )

    # A selected provider must be readable before any historical fallback.
    # An unmigrated source may still replay its exact business receipt; that
    # typed readback does not admit a fresh Monitor write or drain old captures.
    authority = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    source_error: OSError | ValueError | None = None
    if monitor_effect_id and lease_proof is None:
        # Legacy receipts are historical facts even after authority promotion.
        try:
            previous = replay_legacy_monitor_poll(registry_path=registry_path, request=request)
        except (OSError, ValueError) as error:
            if authority is None:
                raise
            # Keep the typed native receipt available when its display is
            # damaged. v4 forbids planning or committing if that receipt is
            # absent; a malformed historical source never permits fresh work.
            previous = None
            source_error = error
        if previous is not None:
            return previous
    if authority is None:
        require_canonical_authority(goal_id=goal_id, operation="monitor_poll")
    try:
        canonical = poll_canonical_monitor_if_promoted(
            registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
            execute=execute, monitor_effect_id=monitor_effect_id, agent_id=agent_id,
            lease_proof=lease_proof, gate_scope_guard=gate_scope_guard,
            receipt_only=source_error is not None,
            observation=observation, intent=intent,
        )
    except LocalCoordinationAuthorityRejection as error:
        if source_error is not None and error.code == "monitor_poll_original_receipt_required":
            raise source_error from error
        raise
    return canonical
