from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...history import load_registry
from ...paths import resolve_runtime_root
from ...control_plane.todos.contract import (
    normalize_todo_decision_scope,
)
from ...control_plane.todos.todo_semantics import todo_item_task_class
from ...control_plane.todos.mutation_api import complete_goal_todo
from ...control_plane.todos.list_readback import list_goal_todos
from .pr_lifecycle import (
    TERMINAL_PR_STATES,
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from .pr_lifecycle_rollout import append_pr_merge_rollout_event
from .pr_review_ack import (
    load_issue_fix_pr_review_ack_receipts,
    resolve_issue_fix_pr_review_context,
)


PR_GATE_RECONCILIATION_SCHEMA_VERSION = "issue_fix_pr_gate_reconciliation_v0"
PR_REVIEW_RECONCILIATION_SCHEMA_VERSION = "issue_fix_pr_review_reconciliation_v0"
PR_REVIEW_ACKED_RECONCILIATION_SCHEMA_VERSION = (
    "issue_fix_pr_review_acked_reconciliation_v0"
)


def _validate_merge_gate(todo: Mapping[str, Any], *, pr_number: int) -> None:
    if str(todo.get("role") or "") != "user":
        raise ValueError("PR gate reconciliation requires a user todo")
    if todo_item_task_class(dict(todo)) != "user_gate":
        raise ValueError("PR gate reconciliation requires task_class=user_gate")
    decision_scope = normalize_todo_decision_scope(todo.get("decision_scope"))
    expected_scope_key = f"merge_pr_{pr_number}"
    if not decision_scope or decision_scope.get("scope_key") != expected_scope_key:
        raise ValueError(
            "PR gate decision_scope must match "
            f"direction:action:{expected_scope_key}"
        )


def _pr_lifecycle_todo(
    *,
    registry_path: Path,
    goal_id: str,
    todo_id: str,
    project: Path | None,
) -> dict[str, Any]:
    todo_payload = list_goal_todos(
        registry_path=registry_path,
        goal_id=goal_id,
        role="user",
        todo_id=todo_id,
        project=project,
    )
    todo = todo_payload.get("todo")
    if not isinstance(todo, dict):
        raise ValueError(f"user todo {todo_id!r} was not found")
    return todo


def reconcile_issue_fix_pr_gate(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    agent_id: str | None = None,
    project: Path | None,
    url: str,
    provider_payload: Mapping[str, Any] | None = None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    execute: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    lifecycle = build_issue_fix_pr_lifecycle_monitor_packet(
        url=url,
        provider_payload=provider_payload,
        fetch_metadata=fetch_metadata,
        fetch_timeout_seconds=fetch_timeout_seconds,
        generated_at=generated_at,
    )
    observation = lifecycle.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("PR lifecycle observation is missing")
    number = observation.get("number")
    if not isinstance(number, int) or number <= 0:
        raise ValueError("PR lifecycle observation requires a numeric PR")

    todo = _pr_lifecycle_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        project=project,
    )
    _validate_merge_gate(todo, pr_number=number)

    state = str(observation.get("state") or "UNKNOWN").upper()
    terminal = state in TERMINAL_PR_STATES
    already_reconciled = bool(todo.get("done")) or str(todo.get("status") or "") == "done"
    receipt: dict[str, Any] = {
        "ok": True,
        "schema_version": PR_GATE_RECONCILIATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "pr": {
            "repo": observation.get("repo"),
            "number": number,
            "url": observation.get("permalink"),
            "state": state,
            "merged_at": observation.get("merged_at"),
            "closed_at": observation.get("closed_at"),
        },
        "terminal": terminal,
        "execute": execute,
        "would_reconcile": terminal and not already_reconciled,
        "write_performed": False,
        "already_reconciled": already_reconciled,
        "external_read_performed": lifecycle.get("external_reads_performed") is True,
        "external_write_performed": False,
        "private_material_read": False,
        "raw_provider_payload_recorded": False,
    }
    if not terminal:
        receipt["skip_reason"] = "pr_not_terminal"
        return receipt
    if not execute:
        receipt["skip_reason"] = "execute_required"
        return receipt

    if not already_reconciled:
        permalink = str(observation.get("permalink") or url)
        terminal_at = str(
            observation.get("merged_at")
            or observation.get("closed_at")
            or generated_at
            or ""
        ).strip()
        completion = complete_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            role="user",
            agent_id=agent_id,
            decision_outcome="approve" if state == "MERGED" else "cancel",
            note=(
                f"Public PR lifecycle reached terminal state {state}; "
                "the merge approval gate is obsolete."
            ),
            evidence=(
                f"{permalink} state={state}"
                + (f" terminal_at={terminal_at}" if terminal_at else "")
            ),
            no_followup=True,
            project=project,
        )
        receipt["write_performed"] = bool(completion.get("changed"))
        receipt["todo_completion"] = {
            "completed": completion.get("completed"),
            "status": completion.get("status"),
            "status_changed": completion.get("status_changed"),
            "metadata_updated": completion.get("metadata_updated"),
            "mutation_authority": completion.get("mutation_authority"),
        }
    if state == "MERGED":
        receipt["rollout_event"] = append_pr_merge_rollout_event(
            payload=lifecycle,
            goal_id=goal_id,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
        )
    receipt["reconciled"] = True
    return receipt


def reconcile_issue_fix_pr_review(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    project: Path | None,
    url: str,
    ack_receipt: Mapping[str, Any] | None = None,
    provider_payload: Mapping[str, Any] | None = None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    execute: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    context = resolve_issue_fix_pr_review_context(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        agent_id=agent_id,
        project=project,
        url=url,
        ack_receipt=ack_receipt,
    )
    context_status = str(context.get("status") or "ack_receipt_invalid")
    if context_status == "current" and ack_receipt is None:
        context_status = "ack_receipt_missing"
    binding = context.get("binding")
    if context_status != "matched":
        return {
            "ok": True,
            "schema_version": PR_REVIEW_RECONCILIATION_SCHEMA_VERSION,
            "goal_id": goal_id,
            "todo_id": todo_id,
            "pr": None,
            "binding": binding,
            "ack_receipt_id": (
                ack_receipt.get("receipt_id")
                if isinstance(ack_receipt, Mapping)
                else None
            ),
            "ack_receipt_status": context_status,
            "terminal": False,
            "owner_acknowledged": False,
            "execute": execute,
            "would_reconcile": False,
            "write_performed": False,
            "already_reconciled": context_status == "todo_not_open",
            "external_read_performed": False,
            "external_write_performed": False,
            "private_material_read": False,
            "raw_provider_payload_recorded": False,
            "skip_reason": context_status,
        }

    lifecycle = build_issue_fix_pr_lifecycle_monitor_packet(
        url=url,
        provider_payload=provider_payload,
        fetch_metadata=fetch_metadata,
        fetch_timeout_seconds=fetch_timeout_seconds,
        generated_at=generated_at,
    )
    observation = lifecycle.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("PR lifecycle observation is missing")
    number = observation.get("number")
    if not isinstance(number, int) or number <= 0:
        raise ValueError("PR lifecycle observation requires a numeric PR")

    current = resolve_issue_fix_pr_review_context(
        registry_path=registry_path,
        goal_id=goal_id,
        todo_id=todo_id,
        agent_id=agent_id,
        project=project,
        url=url,
        ack_receipt=ack_receipt,
    )
    ack_status = str(current.get("status") or "ack_receipt_invalid")
    ack_valid = ack_status == "matched"
    todo = current.get("todo")
    if not isinstance(todo, Mapping):
        ack_valid = False
    binding = current.get("binding") or binding
    state = str(observation.get("state") or "UNKNOWN").upper()
    terminal = state in TERMINAL_PR_STATES
    already_reconciled = ack_status == "todo_not_open"
    receipt: dict[str, Any] = {
        "ok": True,
        "schema_version": PR_REVIEW_RECONCILIATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "todo_id": todo_id,
        "pr": {
            "repo": observation.get("repo"),
            "number": number,
            "url": observation.get("permalink"),
            "state": state,
            "merged_at": observation.get("merged_at"),
            "closed_at": observation.get("closed_at"),
        },
        "binding": binding,
        "ack_receipt_id": (
            ack_receipt.get("receipt_id")
            if isinstance(ack_receipt, Mapping)
            else None
        ),
        "ack_receipt_status": ack_status,
        "terminal": terminal,
        "owner_acknowledged": ack_valid,
        "execute": execute,
        "would_reconcile": (
            terminal and ack_valid and not already_reconciled
        ),
        "write_performed": False,
        "already_reconciled": already_reconciled,
        "external_read_performed": lifecycle.get("external_reads_performed") is True,
        "external_write_performed": False,
        "private_material_read": False,
        "raw_provider_payload_recorded": False,
    }
    if not ack_valid:
        receipt["skip_reason"] = ack_status
        return receipt
    if not terminal:
        receipt["skip_reason"] = "pr_not_terminal"
        return receipt
    if not execute:
        receipt["skip_reason"] = "execute_required"
        return receipt

    if not already_reconciled:
        permalink = str(observation.get("permalink") or url)
        terminal_at = str(
            observation.get("merged_at")
            or observation.get("closed_at")
            or generated_at
            or ""
        ).strip()
        completion = complete_goal_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=todo_id,
            role="user",
            agent_id=agent_id,
            note=(
                f"Owner acknowledged the review and public PR lifecycle reached "
                f"terminal state {state}."
            ),
            evidence=(
                f"{permalink} state={state}"
                + (f" terminal_at={terminal_at}" if terminal_at else "")
            ),
            no_followup=True,
            project=project,
        )
        receipt["write_performed"] = bool(completion.get("changed"))
        receipt["todo_completion"] = {
            "completed": completion.get("completed"),
            "status": completion.get("status"),
            "status_changed": completion.get("status_changed"),
            "metadata_updated": completion.get("metadata_updated"),
            "mutation_authority": completion.get("mutation_authority"),
        }
    if state == "MERGED":
        receipt["rollout_event"] = append_pr_merge_rollout_event(
            payload=lifecycle,
            goal_id=goal_id,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
        )
    receipt["reconciled"] = True
    return receipt


def reconcile_acknowledged_issue_fix_pr_reviews(
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str,
    project: Path | None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    execute: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Reconcile current review todos from their persisted acknowledgement bindings."""

    runtime_root = (
        Path(runtime_root_arg).expanduser()
        if runtime_root_arg
        else resolve_runtime_root(load_registry(registry_path.expanduser()), None)
    )
    receipts = load_issue_fix_pr_review_ack_receipts(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    results: list[dict[str, Any]] = []
    for ack_receipt in receipts:
        binding = ack_receipt.get("binding")
        if not isinstance(binding, Mapping):
            results.append(
                {
                    "ok": False,
                    "ack_receipt_id": ack_receipt.get("receipt_id"),
                    "skip_reason": "ack_receipt_binding_missing",
                    "external_read_performed": False,
                    "write_performed": False,
                }
            )
            continue
        todo_id = str(binding.get("todo_id") or "").strip()
        url = str(binding.get("permalink") or "").strip()
        if not todo_id or not url:
            results.append(
                {
                    "ok": False,
                    "ack_receipt_id": ack_receipt.get("receipt_id"),
                    "todo_id": todo_id or None,
                    "skip_reason": "ack_receipt_binding_incomplete",
                    "external_read_performed": False,
                    "write_performed": False,
                }
            )
            continue
        try:
            result = reconcile_issue_fix_pr_review(
                registry_path=registry_path,
                runtime_root_arg=str(runtime_root),
                goal_id=goal_id,
                todo_id=todo_id,
                agent_id=agent_id,
                project=project,
                url=url,
                ack_receipt=ack_receipt,
                fetch_metadata=fetch_metadata,
                fetch_timeout_seconds=fetch_timeout_seconds,
                execute=execute,
                generated_at=generated_at,
            )
        except ValueError:
            result = {
                "ok": False,
                "todo_id": todo_id,
                "ack_receipt_id": ack_receipt.get("receipt_id"),
                "skip_reason": "provider_observation_failed",
                "error_category": "provider_observation_failed",
                "external_read_performed": fetch_metadata,
                "write_performed": False,
            }
        results.append(result)

    failure_count = sum(result.get("ok") is False for result in results)
    return {
        "ok": True,
        "schema_version": PR_REVIEW_ACKED_RECONCILIATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "execute": execute,
        "degraded": failure_count > 0,
        "ack_receipt_count": len(receipts),
        "result_count": len(results),
        "terminal_count": sum(result.get("terminal") is True for result in results),
        "reconciled_count": sum(
            result.get("write_performed") is True for result in results
        ),
        "already_reconciled_count": sum(
            result.get("already_reconciled") is True for result in results
        ),
        "external_read_count": sum(
            result.get("external_read_performed") is True for result in results
        ),
        "failure_count": failure_count,
        "private_material_read": False,
        "external_write_performed": False,
        "quota_spend_required": False,
        "results": results,
    }


def render_issue_fix_pr_gate_reconciliation_markdown(payload: dict[str, Any]) -> str:
    pr = payload.get("pr") if isinstance(payload.get("pr"), dict) else {}
    return "\n".join(
        [
            "# LoopX Issue Fix PR Gate Reconciliation",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- todo_id: `{payload.get('todo_id')}`",
            f"- pr: `{pr.get('repo')}#{pr.get('number')}`",
            f"- state: `{pr.get('state')}`",
            f"- terminal: `{payload.get('terminal')}`",
            f"- write_performed: `{payload.get('write_performed')}`",
            f"- already_reconciled: `{payload.get('already_reconciled')}`",
            f"- skip_reason: `{payload.get('skip_reason')}`",
        ]
    )


def render_issue_fix_pr_review_reconciliation_markdown(
    payload: dict[str, Any],
) -> str:
    pr = payload.get("pr") if isinstance(payload.get("pr"), dict) else {}
    return "\n".join(
        [
            "# LoopX Issue Fix PR Review Reconciliation",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- todo_id: `{payload.get('todo_id')}`",
            f"- pr: `{pr.get('repo')}#{pr.get('number')}`",
            f"- state: `{pr.get('state')}`",
            f"- terminal: `{payload.get('terminal')}`",
            f"- owner_acknowledged: `{payload.get('owner_acknowledged')}`",
            f"- write_performed: `{payload.get('write_performed')}`",
            f"- already_reconciled: `{payload.get('already_reconciled')}`",
            f"- skip_reason: `{payload.get('skip_reason')}`",
        ]
    )
