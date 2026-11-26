"""Reviewed and legacy Todo, Monitor, and Gate update execution helpers."""

from __future__ import annotations

from collections.abc import Mapping

from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ..control_plane.todos.completion_validation import COMPLETION_VALIDATION_IN_PROGRESS
from ..control_plane.todos.mutation_api import complete_goal_todo, update_goal_todo
from ..control_plane.todos.provider_projection import projection_delivery_requires_ack
from .action_store_common import ActionConflictError, _canonical_digest, _opaque_id, is_str_dict, require_str_dict
from .todo_action_contracts import (
    CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION,
    CANONICAL_UPDATE_BASIS_SCHEMA_VERSION,
    MONITOR_OUTCOME_BY_OPERATION,
    ChatTodoActionHost,
    TodoActionServiceSelf,
    TodoUpdateRunner,
    as_dict_list,
    as_optional_int,
    as_optional_str,
    as_optional_str_list,
    decision_readback,
    parse_reviewed_terminal_options,
    parse_reviewed_update_options,
)
from .todo_source import ChatTodoSource, ChatTodoSourceChanged, resolve_chat_todo_source


def _dispatch_todo_update_mutation(
    source: ChatTodoSource,
    parameters: Mapping[str, object],
    *,
    goal_id: str,
    operation: str,
    basis: Mapping[str, object] | None,
    operation_id: str | None,
    dry_run: bool,
    agent_id: str | None,
    note: str | None,
    successors: list[str] | None,
    lease_key: str | None,
    lease_version: int | None,
) -> dict[str, object]:
    status = as_optional_str(parameters.get("status"))
    if operation == "complete":
        status = "done"
    elif operation == "block":
        status = "blocked"
    elif operation == "defer":
        status = "deferred"
    update_op, update_rev, update_sha = parse_reviewed_update_options(basis, operation_id)
    reason = (
        (note or "Owner requested deferral")
        if operation in {"block", "defer"}
        else ((note or "Owner explicitly requested task resumption") if status == "open" else None)
    )
    return update_goal_todo(
        registry_path=source.registry_path,
        runtime_root_arg=str(source.runtime_root),
        routing_registry_witness=source.routing_registry_witness,
        goal_id=goal_id,
        todo_id=str(parameters["todo_id"]),
        text=as_optional_str(parameters.get("text")),
        priority=as_optional_str(parameters.get("priority")),
        clear_priority=bool(
            parameters.get("clear_priority", False) or ("priority" in parameters and parameters["priority"] is None)
        ),
        status=status,
        role="user" if operation == "complete" else None,
        no_followup=bool(parameters.get("no_followup", True)) if operation == "complete" else None,
        # The established block control uses note as its pause reason;
        # preserve the task's existing notes. Explicit ordinary note edits
        # and lifecycle effects still share the canonical transaction.
        note=(note if operation != "block" or basis is None else None),
        reason=reason,
        claimed_by=(agent_id if operation == "reassign" else None),
        resume_when=as_optional_str(parameters.get("resume_when")),
        clear_resume_when=(
            (operation == "block" or (status == "open" and not parameters.get("resume_when")))
            and basis is not None
        ),
        successor_todo_ids=successors,
        agent_id=agent_id,
        authority_reason="owner-confirmed typed Chat action",
        dry_run=dry_run,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=lease_version,
        update_operation_id=update_op,
        update_expected_provider_revision=update_rev,
        update_expected_registry_sha256=update_sha,
    )


def run_todo_update(
    host: ChatTodoActionHost,
    parameters: Mapping[str, object],
    *,
    dry_run: bool,
    basis: Mapping[str, object] | None = None,
    operation_id: str | None = None,
) -> dict[str, object]:
    """Validate and execute canonical Todo mutations (edit or terminal complete)."""
    goal_id = str(parameters["goal_id"])
    operation = str(parameters.get("operation") or "edit")
    reviewed = parameters.get("review_basis")
    if dry_run and isinstance(reviewed, Mapping) and (
        basis is None or any(basis.get(key) != value for key, value in reviewed.items())
    ):
        raise ValueError("The Task source changed. Reload its complete requirements before saving.")
    source = resolve_chat_todo_source(host, goal_id)
    if basis is not None:
        source.require_review(dict(basis))
    # Transport only caller-supplied proof. The terminal owner revalidates
    # the execution; a lease read or a Todo claim is never substitute proof.
    lease_key = (
        as_optional_str(parameters.get("task_lease_idempotency_key"))
        if operation == "complete" and "task_lease_idempotency_key" in parameters
        else None
    )
    lease_version = (
        as_optional_int(parameters.get("task_lease_expected_version"), field="task_lease_expected_version")
        if operation == "complete" and "task_lease_expected_version" in parameters
        else None
    )
    successors = as_optional_str_list(parameters.get("successor_todo_ids"), field="successor_todo_ids")
    agent_id = as_optional_str(parameters.get("agent_id"))
    note = as_optional_str(parameters.get("note"))
    if operation == "complete" and (
        basis is None or basis.get("schema_version") == CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION
    ):
        completion_turn_key, terminal_review_basis = parse_reviewed_terminal_options(basis, operation_id)
        return complete_goal_todo(
            registry_path=source.registry_path,
            runtime_root_arg=str(source.runtime_root),
            routing_registry_witness=source.routing_registry_witness,
            goal_id=goal_id,
            todo_id=str(parameters["todo_id"]),
            note=note,
            no_followup=bool(parameters.get("no_followup", True)),
            successor_todo_ids=successors,
            agent_id=agent_id,
            authority_reason="owner-confirmed typed Chat action",
            dry_run=dry_run,
            task_lease_idempotency_key=lease_key,
            task_lease_expected_version=lease_version,
            completion_turn_key=completion_turn_key,
            terminal_review_basis=terminal_review_basis,
        )
    return _dispatch_todo_update_mutation(
        source, parameters, goal_id=goal_id, operation=operation, basis=basis,
        operation_id=operation_id, dry_run=dry_run, agent_id=agent_id, note=note,
        successors=successors, lease_key=lease_key, lease_version=lease_version,
    )


def run_gate_resolve(
    host: ChatTodoActionHost,
    parameters: Mapping[str, object],
    *,
    dry_run: bool,
    basis: Mapping[str, object] | None = None,
    operation_id: str | None = None,
) -> dict[str, object]:
    """Validate and record a User gate resolution via the terminal owner."""
    goal_id = str(parameters["goal_id"])
    source = resolve_chat_todo_source(host, goal_id)
    if basis is not None:
        source.require_review(dict(basis))
    completion_turn_key, terminal_review_basis = parse_reviewed_terminal_options(basis, operation_id)
    return complete_goal_todo(
        registry_path=source.registry_path,
        runtime_root_arg=str(source.runtime_root),
        routing_registry_witness=source.routing_registry_witness,
        goal_id=goal_id,
        todo_id=str(parameters["todo_id"]),
        role="user",
        decision_outcome=str(parameters["decision"]),
        evidence=as_optional_str(parameters.get("note")),
        no_followup=True,
        agent_id=as_optional_str(parameters.get("agent_id")),
        authority_reason="owner-confirmed typed Chat decision",
        dry_run=dry_run,
        completion_turn_key=completion_turn_key,
        terminal_review_basis=terminal_review_basis,
    )


def canonical_update_basis(
    host: ChatTodoActionHost,
    goal_id: str,
    *,
    completion_todo_id: str | None = None,
    decision: bool = False,
) -> dict[str, object] | None:
    """Snapshot canonical authority identity and revision with TOCTOU verification."""
    source = resolve_chat_todo_source(host, goal_id)
    authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
    current = resolve_chat_todo_source(host, goal_id)
    if (current.source_identity, current.registry_sha256) != (source.source_identity, source.registry_sha256):
        raise ValueError("Todo authority registration changed while reading; retry")
    if authority is None:
        return None
    terminal = decision or (
        completion_todo_id is not None
        and not any(
            todo.get("todo_id") == completion_todo_id and todo.get("role") == "user"
            for todo in as_dict_list(authority.get("todos"))
        )
    )
    return {
        "schema_version": (
            CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION if terminal else CANONICAL_UPDATE_BASIS_SCHEMA_VERSION
        ),
        "provider_revision": authority["provider_revision"],
        "source_authority": authority["source_authority"],
        "registry_sha256": source.registry_sha256,
        "source_identity": source.source_identity,
    }


def _handle_reviewed_edit_error(
    host: ChatTodoActionHost,
    proposal_id: str,
    proposal: Mapping[str, object],
    operation_id: str,
    error: LocalCoordinationAuthorityUnavailable,
    *,
    terminal: bool,
) -> dict[str, object]:
    if error.code == COMPLETION_VALIDATION_IN_PROGRESS:
        raise ActionConflictError(str(error)) from error
    if (
        terminal
        and error.code == "authority_source_changed"
        and error.payload.get("completion_validation_executed") is True
    ):
        host.store.mark_failed(
            proposal_id,
            error_code="canonical_update_validation_source_changed",
            message="Todo authority registration changed during completion validation; retry",
            details={"operation_id": operation_id, "reason_code": error.code},
        )
        raise ValueError("Todo authority registration changed during completion validation; retry") from error
    if error.code in {"provider_revision_mismatch", "authority_source_changed", "provider_revision_conflict"}:
        stale = host.store.apply(
            proposal_id,
            current_state_fingerprint=_canonical_digest(
                {"expected": proposal["expected_state_fingerprint"], "conflict": error.code}
            ),
            receipt={},
        )
        return {"proposal": stale, "turn": None}
    failed = host.store.mark_failed(
        proposal_id,
        error_code="canonical_update_retry_required",
        message="The edit is not yet verified. Retry this proposal to recover the original operation.",
        details={"operation_id": operation_id, "reason_code": error.code},
    )
    return {"proposal": failed, "turn": None}


def _reviewed_edit_outcome(action_kind: str, operation: str, *, changed: bool) -> str:
    if action_kind == "gate.resolve":
        return "gate_resolved"
    if action_kind == "monitor.update":
        return MONITOR_OUTCOME_BY_OPERATION[operation]
    if operation == "complete":
        return "todo_completed"
    return "todo_updated" if changed else "todo_unchanged"


def apply_reviewed_todo_edit(
    host: TodoActionServiceSelf,
    proposal_id: str,
    proposal: Mapping[str, object],
    parameters: dict[str, object],
) -> dict[str, object]:
    """Recover or execute a canonical update bound to a reviewed basis."""
    operation_id = f"chat-update:{proposal_id}"
    raw_basis = proposal.get("canonical_update_basis")
    basis = (
        require_str_dict(raw_basis, error="canonical update review basis is malformed")
        if raw_basis is not None
        else None
    )
    action_kind = str(proposal["action_kind"])
    run: TodoUpdateRunner = (
        host._run_monitor_update
        if action_kind == "monitor.update"
        else host._run_gate_resolve
        if action_kind == "gate.resolve"
        else host._run_todo_update
    )
    terminal = action_kind == "gate.resolve" or parameters.get("operation") in {"complete", "stop"}
    try:
        result = run(parameters, dry_run=False, basis=basis, operation_id=operation_id)
    except ChatTodoSourceChanged as error:
        failed = host.store.mark_failed(
            proposal_id, error_code="canonical_update_source_changed", message=str(error),
            details={"operation_id": operation_id},
        )
        return {"proposal": failed, "turn": None}
    except LocalCoordinationAuthorityUnavailable as error:
        return _handle_reviewed_edit_error(host, proposal_id, proposal, operation_id, error, terminal=terminal)
    if result.get("ok") is not True:
        failed = host.store.mark_failed(
            proposal_id,
            error_code="canonical_update_validation_failed",
            message=(
                "Completion validation did not pass. "
                "The Todo was not completed; retry after resolving the validation failure."
            ),
            details={"operation_id": operation_id, "reason_code": result.get("reason_code")},
        )
        return {"proposal": failed, "turn": None}
    raw_original = result.get("original_receipt")
    original: Mapping[str, object] = raw_original if is_str_dict(raw_original) and raw_original else result
    todo_id = _opaque_id(result.get("todo_id"), field="todo_id")
    host.store.save_checkpoint(
        proposal_id,
        step="canonical_update",
        receipt={"operation_id": operation_id, "todo_id": todo_id, "changed": original.get("changed") is True},
    )
    if not projection_delivery_requires_ack(result.get("projection_delivery")):
        failed = host.store.mark_failed(
            proposal_id,
            error_code="canonical_update_projection_pending",
            message=(
                "The edit was committed. Display delivery is pending; "
                "retry this proposal to restore the current view."
            ),
            details={"operation_id": operation_id, "canonical_committed": True},
        )
        return {"proposal": failed, "turn": None}
    operation = str(parameters.get("operation", "edit"))
    receipt: dict[str, object] = {
        "receipt_id": _canonical_digest({"proposal_id": proposal_id, "todo_id": todo_id})[:32],
        "outcome": _reviewed_edit_outcome(action_kind, operation, changed=bool(original.get("changed"))),
        "projection_verified": True,
        "projection_delivery": result["projection_delivery"],
        "operation_id": operation_id,
        "canonical_status": result.get("provider_status", result.get("status")),
        "resource_ids": {"goal_id": str(parameters["goal_id"]), "todo_id": todo_id},
    }
    if action_kind == "gate.resolve":
        receipt.update(decision_readback(result))
    stored = host.store.apply(
        proposal_id,
        current_state_fingerprint=str(proposal["expected_state_fingerprint"]),
        receipt=receipt,
    )
    return {"proposal": stored, "turn": None}


def apply_todo_update(
    host: TodoActionServiceSelf,
    proposal_id: str,
    proposal: Mapping[str, object],
    parameters: dict[str, object],
) -> dict[str, object]:
    """Execute either a canonical-basis-reviewed update or legacy fingerprint update."""
    if proposal.get("canonical_update_basis") is not None:
        return host._apply_reviewed_todo_edit(proposal_id, proposal, parameters)
    goal_id = str(parameters["goal_id"])
    current_fingerprint = host._goal_state_fingerprint(goal_id)
    if current_fingerprint != proposal.get("expected_state_fingerprint"):
        stale = host.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt={})
        return {"proposal": stale, "turn": None}
    operation = str(parameters.get("operation") or "edit")
    decision = proposal.get("action_kind") == "gate.resolve"
    result = (host._run_gate_resolve if decision else host._run_todo_update)(parameters, dry_run=False)
    todo_id = _opaque_id(result.get("todo_id"), field="todo_id")
    receipt: dict[str, object] = {
        "receipt_id": _canonical_digest({"proposal_id": proposal_id, "todo_id": todo_id})[:32],
        "outcome": (
            "gate_resolved"
            if decision
            else "todo_completed"
            if operation == "complete"
            else ("todo_updated" if result.get("changed") else "todo_unchanged")
        ),
        "projection_verified": True,
        "resource_ids": {"goal_id": goal_id, "todo_id": todo_id},
    }
    if decision:
        receipt.update(decision_readback(result))
    stored = host.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt)
    return {"proposal": stored, "turn": None}


__all__ = [
    "apply_reviewed_todo_edit",
    "apply_todo_update",
    "canonical_update_basis",
    "run_gate_resolve",
    "run_todo_update",
]
