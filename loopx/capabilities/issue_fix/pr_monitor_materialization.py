"""Execute the typed issue-fix Monitor reconciliation plan through public writers.

Each bucket is a separate recoverable mutation. The ledger is an observation,
not permission to borrow another execution's lease or bypass Todo admission.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...control_plane.todos.monitor_metadata import MonitorPollObservation
from ...control_plane.coordination.local_authority import LOCAL_AUTHORITY_SOURCES
from ...control_plane.todos.provider_projection import settle_canonical_todo_projection
from ...control_plane.work_items.task_lease import (
    acquire_task_lease, inspect_task_lease, release_task_lease,
    runtime_root_from_registry, TaskLeaseError,
)
from ...todos import add_goal_todo, complete_goal_todo, list_goal_todos, update_goal_todo

ISSUE_FIX_GROUPED_MONITOR_WRITEBACK_SCHEMA_VERSION = "issue_fix_grouped_monitor_writeback_v0"
DEFAULT_ISSUE_FIX_MONITOR_CADENCE = "30m"


def _load_lifecycle_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"issue-fix PR lifecycle ledger line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise TypeError(
                f"issue-fix PR lifecycle ledger line {line_number} must be an object"
            )
        rows.append(row)
    return rows



def _plan(*, registry_path: Path, goal_id: str, project: Path,
          rows: list[dict[str, Any]], cadence: str, generated_at: str, runtime_root: Path) -> tuple[dict[str, Any], str | None]:
    source = list_goal_todos(registry_path=registry_path, goal_id=goal_id, role="agent", project=project,
                             runtime_root_arg=str(runtime_root))
    try:
        plan = effect_runtime_result("capabilities.issue_fix.monitor_reconciliation.plan", {
            "schema_version": "loopx_issue_fix_monitor_plan_request_v0", "ledger_rows": rows,
            "todos": source["todos"], "cadence": cadence, "generated_at": generated_at,
        })
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(plan, dict) or plan.get("schema_version") != "loopx_issue_fix_monitor_plan_result_v0":
        raise TypeError("TypeScript Monitor reconciliation plan shape mismatch")
    return plan, (source.get("authority_read") or {}).get("source_authority")


def _attempt_prefix(*, goal_id: str, todo_id: str, owner: str,
                    target_key: str) -> str:
    """The stable identity of one recoverable Monitor execution attempt.

    A retry of the same bounded work is a new invocation: it observes a later
    time and may see a re-ordered ledger, so neither the observation clock nor
    the raw rows may identify the attempt. What identifies it is the
    reconciliation subject this actor executes against - the Goal, the Monitor
    Todo and the bucket it watches - which is what lets an execution that
    stopped after acquiring its own lease recognize that lease again instead of
    colliding with it. A key minted for a different subject, or by another
    actor, never matches this prefix.
    """

    identity = json.dumps(
        [goal_id, todo_id, owner, target_key],
        separators=(",", ":"),
    )
    return "issue-fix-monitor:" + hashlib.sha256(identity.encode()).hexdigest() + ":"


def _decision_signature(step: dict[str, Any]) -> tuple[Any, ...]:
    """The business decision a planned step carries, without the observation clock.

    ``generated_at`` and the schedule it derives are properties of one
    observation, not of the decision: a retry that observes the same bounded
    work later plans the same operation and must be allowed to execute it. A
    changed operation, membership digest, material-change verdict or write scope
    still stops the stale step, and the revalidated step is the one written, so
    the durable observation carries the time this execution actually observed.
    """

    observation = step.get("observation")
    if isinstance(observation, dict):
        return (step.get("operation"), step.get("todo_id"),
                tuple(step.get("write_scopes") or ()), observation.get("target_key"),
                observation.get("result_hash"), observation.get("material_change"))
    return (step.get("operation"), step.get("todo_id"),
            tuple(step.get("write_scopes") or ()), step.get("evidence"))


def _release_attempt(*, registry_path: Path, runtime_root: Path, goal_id: str,
                     todo_id: str, owner: str, prefix: str,
                     proof: dict[str, Any] | None = None) -> None:
    """Recover cleanup after a commit without renewing or borrowing execution."""
    current = inspect_task_lease(registry_path=registry_path, runtime_root=runtime_root,
                                goal_id=goal_id, todo_id=todo_id)
    if current.get("ok") is not True:
        raise TaskLeaseError("Monitor cleanup readback failed; retry after inspection",
            code=str(current.get("error_code") or "monitor_cleanup_unavailable"))
    lease = current.get("lease") or {}
    key = str(lease.get("idempotency_key") or "")
    if (lease.get("status") == "active" and lease.get("owner") == owner and key.startswith(prefix)
        and (proof is None or (key == proof["task_lease_idempotency_key"]
             and lease.get("version") == proof["task_lease_expected_version"]))):
        release_task_lease(registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, todo_id=todo_id, owner=owner, idempotency_key=key,
            expected_version=lease["version"])


def materialize_issue_fix_grouped_monitors(
    *, registry_path: Path, goal_id: str, project: Path, ledger_path: Path,
    claimed_by: str, cadence: str, generated_at: str, runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Reconcile a complete ledger; preserve creator and execution ownership."""
    runtime_root = runtime_root_from_registry(registry_path, str(runtime_root) if runtime_root is not None else None)
    rows = _load_lifecycle_rows(ledger_path)
    plan, source_authority = _plan(registry_path=registry_path, goal_id=goal_id,
        project=project, rows=rows, cadence=cadence, generated_at=generated_at, runtime_root=runtime_root)
    writes: list[dict[str, Any]] = []
    for step in plan["steps"]:
        operation, target = step["operation"], step["target_key"]
        prefix = _attempt_prefix(goal_id=goal_id, todo_id=str(step.get("todo_id") or ""),
            owner=claimed_by, target_key=target)
        if operation == "unchanged":
            _release_attempt(registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, todo_id=step["todo_id"], owner=claimed_by, prefix=prefix)
            writes.append({"operation": operation, "target_key": target, "write_performed": False})
            continue
        if operation == "add":
            result = add_goal_todo(registry_path=registry_path, goal_id=goal_id, role="agent",
                text=step["text"], priority="P2", task_class="continuous_monitor",
                action_kind=step["action_kind"], claimed_by=claimed_by, agent_id=claimed_by,
                monitor_metadata=step["metadata"], project=project, runtime_root_arg=str(runtime_root))
            writes.append({"operation": "add", "target_key": target,
                "write_performed": bool(result.get("added") or result.get("metadata_updated"))})
            continue
        todo_id = step["todo_id"]
        proof: dict[str, Any] = {}
        acquired: dict[str, Any] | None = None
        # Reactivation cannot acquire against completed work. Its existing typed
        # transaction retires old execution; a subsequent observation acquires anew.
        if operation != "reactivate":
            inspected = inspect_task_lease(registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, todo_id=todo_id)
            if inspected.get("ok") is not True:
                raise TaskLeaseError(str(inspected.get("error") or "Monitor lease inspection failed"),
                    code=str(inspected.get("error_code") or "monitor_lease_inspection_failed"))
            if inspected.get("handoff_mode") == "hard_lease" or (
                inspected.get("lease") is not None and inspected.get("handoff_mode") != "soft_claim"
            ):
                # Recover only this actor's exact observation attempt. A retired
                # attempt gets a fresh key; historical receipts grant no execution.
                prior = inspected.get("lease") or {}
                retained_key = str(prior.get("idempotency_key") or "")
                key = (retained_key if inspected.get("active") is True
                       and prior.get("owner") == claimed_by and retained_key.startswith(prefix)
                       else prefix + uuid4().hex)
                acquired = acquire_task_lease(registry_path=registry_path, runtime_root=runtime_root,
                    goal_id=goal_id, todo_id=todo_id, owner=claimed_by,
                    idempotency_key=key, ttl_seconds=60, write_scopes=step["write_scopes"])
                lease = acquired["lease"]
                proof = {"task_lease_idempotency_key": key,
                         "task_lease_expected_version": lease["version"]}
        try:
            if acquired is not None:
                # A competing observation may have committed between planning
                # and acquisition. Revalidate under our own execution, not the old
                # display snapshot. The transaction verifies the proof again.
                fresh, _ = _plan(registry_path=registry_path, goal_id=goal_id, project=project,
                    rows=rows, cadence=cadence, generated_at=generated_at, runtime_root=runtime_root)
                matching = [candidate for candidate in fresh["steps"] if candidate["target_key"] == target]
                if not matching or matching[0]["operation"] == "unchanged":
                    writes.append({"operation": "unchanged", "target_key": target, "write_performed": False})
                    continue
                if _decision_signature(matching[0]) != _decision_signature(step):
                    raise ValueError("Monitor source changed before execution; retry reconciliation")
                # Execute the decision revalidated under our own execution: the
                # recovered step carries this invocation's observation, while the
                # stable attempt prefix keeps this execution's lease its own.
                step = matching[0]
                operation = step["operation"]
            if operation in {"observe", "reactivate"}:
                result = update_goal_todo(registry_path=registry_path, goal_id=goal_id,
                    todo_id=todo_id, role="agent", agent_id=claimed_by, project=project, runtime_root_arg=str(runtime_root),
                    status="open" if operation == "reactivate" else None,
                    no_followup=False if operation == "reactivate" else None,
                    reason=step["reason"], monitor_metadata=MonitorPollObservation(**step["observation"]), **proof)
                recorded_operation = "update"
            elif operation == "complete":
                result = complete_goal_todo(registry_path=registry_path, goal_id=goal_id,
                    todo_id=todo_id, role="agent", agent_id=claimed_by, project=project, runtime_root_arg=str(runtime_root),
                    evidence=step["evidence"], no_followup=True, **proof)
                recorded_operation = "complete"
            else:
                raise TypeError(f"unsupported Monitor reconciliation operation: {operation}")
            if result.get("status") in {"failed", "validation_failed", "rejected", "ambiguous"} or result.get("ok") is False:
                raise ValueError(f"Monitor {operation} did not commit: {result.get('reason_code') or result.get('error') or result.get('status')}")
            writes.append({"operation": recorded_operation, "target_key": target,
                "write_performed": bool(result.get("changed"))})
        finally:
            if acquired is not None:
                _release_attempt(registry_path=registry_path, runtime_root=runtime_root,
                    goal_id=goal_id, todo_id=todo_id, owner=claimed_by, prefix=prefix, proof=proof)
    result = {"schema_version": ISSUE_FIX_GROUPED_MONITOR_WRITEBACK_SCHEMA_VERSION,
        "write_performed": any(item["write_performed"] for item in writes), "path_recorded": False,
        "active_bucket_count": plan["active_bucket_count"], "active_member_count": plan["active_member_count"],
        "next_due_at": plan["next_due_at"], "writes": writes}
    if source_authority in LOCAL_AUTHORITY_SOURCES:
        result = settle_canonical_todo_projection(payload={**result, "source_authority": source_authority},
            registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id, project=project)
    return result
