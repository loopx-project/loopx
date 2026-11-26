"""Todo creation, legacy recovery, and assignment execution helpers."""

from __future__ import annotations

from collections.abc import Mapping

from ..agent_registry import registered_agent_ids_for_goal
from ..chat import apply_todo_review_preview, build_todo_review_preview
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from ..control_plane.todos.mutation_api import update_goal_todo
from .action_store_common import _opaque_id, is_str_dict, require_str_dict
from .todo_action_contracts import (
    ChatTodoActionHost,
    as_optional_str,
    raise_protected_action_gate,
    task_turn_dispatched,
    todo_checkpoints,
)
from .todo_action_tasks import (
    apply_created_todo_assignment,
    read_confirmed_task,
    start_confirmed_task,
)
from .todo_source import ChatTodoSource, ChatTodoSourceChanged, resolve_chat_todo_source


def _raise_legacy_todo_source_gate(todo_id: str, error: Exception) -> None:
    summary = f"The original confirmation recorded Todo {todo_id}. Its assignment source could not be verified."
    next_action = "Restore the original Goal source and retry, or review the assignment from the existing Todo."
    current = error.verified_todo if isinstance(error, ChatTodoSourceChanged) else None
    if current is not None:
        assignee = current.get("claimed_by")
        assignment_state = f"Its current assignee is {assignee}." if assignee else "It is currently unassigned."
        summary = (
            f"The original confirmation created Todo {todo_id}. {assignment_state} "
            "The original assignment could not be verified."
        )
        next_action = "Open the existing Todo to review its current assignment."
    raise_protected_action_gate(
        "todo.create",
        gate={"kind": "canonical_authority_required", "summary": summary, "next_action": next_action},
        cause=error,
    )


def _recover_legacy_todo_source(
    host: ChatTodoActionHost,
    proposal_id: str,
    parameters: Mapping[str, object],
    *,
    source: ChatTodoSource,
    todo_step: Mapping[str, object],
    steps: Mapping[str, object],
    todo_id: str,
    canonical_receipt: dict[str, object],
    current_fingerprint: str,
    agent_id: object,
    start_execution: bool,
    assignment_completed: bool,
) -> tuple[object, bool]:
    goal_id = str(parameters["goal_id"])
    assignment = steps.get("todo_assigned")
    if is_str_dict(assignment) and (
        not agent_id or assignment.get("todo_id") != todo_id or assignment.get("agent_id") != str(agent_id)
    ):
        raise ChatTodoSourceChanged("The saved assignment does not identify this Task and Agent.")
    authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
    if start_execution and agent_id and authority:
        # A committed claim can predate the corresponding Chat checkpoint.
        claim_completed = source.has_managed_task_claim(
            proposal_id, todo_id, str(agent_id), str(authority["source_authority"]),
        )
        if assignment_completed and not claim_completed:
            # Older releases submitted the Turn after a plain assignment.
            _, turn = read_confirmed_task(host, proposal_id, parameters, todo_id, steps)
            if turn is None or not task_turn_dispatched(turn):
                raise ChatTodoSourceChanged("The saved Task start claim is unavailable.")
        assignment_completed = assignment_completed or claim_completed
    binding = source.recover_legacy_creation(
        todo_id=todo_id,
        preview_id=current_fingerprint,
        receipt=canonical_receipt,
        authority=authority,
        assignment_pending=bool(agent_id) and not assignment_completed,
    )
    if not assignment_completed and agent_id and agent_id not in registered_agent_ids_for_goal(source.goal):
        raise ChatTodoSourceChanged("The selected Agent is no longer registered for the Goal")
    return binding, assignment_completed


def _recover_created_todo_step(
    host: ChatTodoActionHost,
    proposal_id: str,
    proposal: Mapping[str, object],
    parameters: Mapping[str, object],
    *,
    source: ChatTodoSource,
    todo_step: Mapping[str, object],
    steps: Mapping[str, object],
    agent_id: object,
    start_execution: bool,
    assignment_completed: bool,
) -> tuple[str, dict[str, object], dict[str, object], str, bool]:
    todo_id = _opaque_id(todo_step.get("todo_id"), field="todo_id")
    raw_canonical = todo_step.get("canonical_receipt")
    if not is_str_dict(raw_canonical):
        raise ValueError("Todo creation checkpoint is malformed")
    canonical_receipt = raw_canonical
    current_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
    binding_obj = todo_step.get("source")
    if "source" not in todo_step:
        try:
            binding_obj, assignment_completed = _recover_legacy_todo_source(
                host,
                proposal_id,
                parameters,
                source=source,
                todo_step=todo_step,
                steps=steps,
                todo_id=todo_id,
                canonical_receipt=canonical_receipt,
                current_fingerprint=current_fingerprint,
                agent_id=agent_id,
                start_execution=start_execution,
                assignment_completed=assignment_completed,
            )
        except (ChatTodoSourceChanged, LocalCoordinationAuthorityUnavailable) as error:
            _raise_legacy_todo_source_gate(todo_id, error)
        host.store.save_checkpoint(proposal_id, step="todo_created", receipt={**todo_step, "source": binding_obj})
    if not is_str_dict(binding_obj):
        raise ValueError("Todo creation checkpoint source is malformed")
    source.require_review(binding_obj)
    return todo_id, canonical_receipt, binding_obj, current_fingerprint, assignment_completed


def _create_new_todo_step(
    host: ChatTodoActionHost,
    proposal_id: str,
    proposal: Mapping[str, object],
    parameters: Mapping[str, object],
    *,
    source: ChatTodoSource,
    agent_id: object,
) -> tuple[str, dict[str, object], dict[str, object], str] | dict[str, object]:
    goal_id = str(parameters["goal_id"])
    if agent_id and agent_id not in registered_agent_ids_for_goal(source.goal):
        raise ValueError("the selected Agent is no longer registered for this Goal")
    authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
    if not isinstance(authority.get("store_identity"), str):
        raise ValueError("The Goal's Todo authority identity is unavailable")
    priority = as_optional_str(parameters.get("priority"))
    current = build_todo_review_preview(
        registry_path=source.registry_path,
        runtime_root_arg=str(source.runtime_root),
        goal_id=goal_id,
        text=str(parameters["text"]),
        priority=priority,
        source_identity=source.source_identity,
    )
    current_fingerprint = str(current["preview_id"])
    if current_fingerprint != proposal.get("expected_state_fingerprint"):
        stale = host.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt={})
        return {"proposal": stale, "turn": None}
    applied = apply_todo_review_preview(
        registry_path=source.registry_path,
        runtime_root_arg=str(source.runtime_root),
        goal_id=goal_id,
        text=str(parameters["text"]),
        priority=priority,
        preview_id=current_fingerprint,
        expected_registry_sha256=source.registry_sha256,
        routing_registry_witness=source.routing_registry_witness,
        source_identity=source.source_identity,
        return_authority_write=True,
    )
    canonical_receipt = require_str_dict(applied.get("receipt"), error="Todo review receipt is malformed")
    authority_write = require_str_dict(
        applied.get("authority_write"),
        error="Todo authority write receipt is malformed",
    )
    todo_id = _opaque_id(canonical_receipt["todo_id"], field="todo_id")
    binding: dict[str, object] = {
        **authority_write,
        "source_identity": source.source_identity,
        "store_identity": authority["store_identity"],
        "registry_sha256": source.registry_sha256,
    }
    host.store.save_checkpoint(
        proposal_id,
        step="todo_created",
        receipt={"todo_id": todo_id, "canonical_receipt": canonical_receipt, "source": binding},
    )
    return todo_id, canonical_receipt, binding, current_fingerprint


def _assign_created_todo(
    host: ChatTodoActionHost,
    proposal_id: str,
    *,
    goal_id: str,
    todo_id: str,
    agent_id: str,
    binding: Mapping[str, object],
    steps: Mapping[str, object],
) -> None:
    def apply_assignment(current_source: ChatTodoSource, revision: str) -> dict[str, object]:
        return update_goal_todo(
            registry_path=current_source.registry_path,
            runtime_root_arg=str(current_source.runtime_root),
            goal_id=goal_id,
            todo_id=todo_id,
            claimed_by=agent_id,
            agent_id=agent_id,
            authority_reason="owner-confirmed typed Chat action",
            update_operation_id=f"chat-assign:{proposal_id}",
            update_expected_provider_revision=revision,
            update_expected_registry_sha256=current_source.registry_sha256,
            routing_registry_witness=current_source.routing_registry_witness,
            dry_run=False,
        )

    apply_created_todo_assignment(
        host, proposal_id, goal_id=goal_id, todo_id=todo_id,
        binding=binding, steps=steps, apply_mutation=apply_assignment,
    )
    host.store.save_checkpoint(
        proposal_id, step="todo_assigned", receipt={"todo_id": todo_id, "agent_id": agent_id},
    )


def apply_todo_create(
    host: ChatTodoActionHost,
    proposal_id: str,
    proposal: Mapping[str, object],
    parameters: Mapping[str, object],
) -> dict[str, object]:
    """Create, assign, and optionally launch execution for a reviewed Todo."""
    goal_id = str(parameters["goal_id"])
    source = resolve_chat_todo_source(host, goal_id)
    agent_id = parameters.get("agent_id")
    start_execution = parameters.get("start_execution") is True
    steps = todo_checkpoints(proposal)
    assignment_completed = is_str_dict(steps.get("todo_assigned"))
    todo_step = steps.get("todo_created")
    if is_str_dict(todo_step) and todo_step.get("todo_id"):
        todo_id, canonical_receipt, binding, current_fingerprint, assignment_completed = (
            _recover_created_todo_step(
                host, proposal_id, proposal, parameters, source=source, todo_step=todo_step,
                steps=steps, agent_id=agent_id, start_execution=start_execution,
                assignment_completed=assignment_completed,
            )
        )
    else:
        created_or_stale = _create_new_todo_step(
            host, proposal_id, proposal, parameters, source=source, agent_id=agent_id,
        )
        if isinstance(created_or_stale, dict):
            return created_or_stale
        todo_id, canonical_receipt, binding, current_fingerprint = created_or_stale

    if agent_id and not start_execution and not assignment_completed:
        _assign_created_todo(
            host, proposal_id, goal_id=goal_id, todo_id=todo_id,
            agent_id=str(agent_id), binding=binding, steps=steps,
        )

    resource_ids: dict[str, object] = {
        "goal_id": str(canonical_receipt["goal_id"]),
        "todo_id": todo_id,
        **({"agent_id": str(agent_id)} if agent_id else {}),
    }
    receipt: dict[str, object] = {
        "receipt_id": str(canonical_receipt["receipt_id"]),
        "outcome": str(canonical_receipt["outcome"]),
        "projection_verified": True,
        "resource_ids": resource_ids,
        "canonical_receipt": canonical_receipt,
    }
    turn_result: dict[str, object] | None = None
    if start_execution:
        source = resolve_chat_todo_source(host, goal_id)
        source.require_review(binding)
        turn_result = start_confirmed_task(
            host, proposal_id, parameters, todo_id, steps, source=source, binding=binding,
        )
        resource_ids.update({"session_id": turn_result["session_id"], "turn_id": turn_result["turn_id"]})
        receipt["outcome"] = "task_execution_started"
    stored = host.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt)
    return {"proposal": stored, "turn": turn_result}


__all__ = ["apply_todo_create"]
