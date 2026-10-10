"""Todo input transport shared by App and the compatibility facade.

Canonical transactions remain owned by the existing typed provider adapters.
Only an unpromoted operation loads its original Markdown writer.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_from_registry
from ...state_refresh import now_local
from .contract import (
    TODO_STATUS_DEFERRED,
    TODO_STATUS_OPEN,
    normalize_todo_id,
    normalize_todo_status,
    require_todo_excluded_agents,
    require_supported_todo_resume_when,
)
from .active_state_editing import TODO_SECTION_HEADINGS
from .addition import require_replan_successor_scope
from . import completion_validation as completion_validation_module
from .completion_validation_projection import completion_validation_declaration
from . import monitor_metadata as todo_monitor_metadata
from .text import plan_todo_priority
from .authoring_scope import plan_todo_authoring_scope
from ..coordination.local_authority import (
    claim_canonical_todo_if_promoted,
    local_authority_is_promoted,
)
from .provider_update import update_canonical_todo_if_promoted
from .update_intent import build_canonical_update_intent
from .provider_create import create_canonical_todo_if_promoted
from .provider_terminal_lifecycle import provider_first_terminal_lifecycle
from ...paths import effective_runtime_root


def add_goal_todo(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    role: str,
    text: str,
    priority: str | None = None,
    status: str | None = None,
    note: str | None = None,
    task_class: str | None = None,
    action_kind: str | None = None,
    task_domain: str | None = None,
    capability_binding_ref: str | None = None,
    task_repository: str | None = None,
    continuation_policy: str | None = None,
    required_write_scopes: list[str] | None = None,
    required_capabilities: list[str] | None = None,
    target_capabilities: list[str] | None = None,
    explore_result_node_refs: list[str] | None = None,
    decision_scope: Any = None,
    required_decision_scopes: Any = None,
    claimed_by: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool = False,
    blocks_agent: str | None = None,
    excluded_agents: list[str] | None = None,
    global_gate: bool = False,
    agent_id: str | None = None,
    unblocks_todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    resume_when: str | None = None,
    validation_command: str | None = None,
    validation_command_json: str | None = None,
    validation_label: str | None = None,
    validation_timeout_seconds: int | None = None,
    monitor_metadata: dict[str, Any] | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
    operation_id: str | None = None,
    expected_provider_revision: str | None = None,
) -> dict[str, Any]:
    call = dict(locals())
    call.pop("expected_provider_revision")
    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if role not in TODO_SECTION_HEADINGS:
        raise ValueError("todo role must be one of: user, agent")
    replan_obligation_id = require_replan_successor_scope(
        role=role,
        task_class=task_class,
        claimed_by=claimed_by,
        obligation_id=replan_obligation_id,
        action_kind=action_kind,
        target_key=(monitor_metadata or {}).get("target_key"),
        explore_result_node_refs=explore_result_node_refs,
    )
    normalized_status = normalize_todo_status(status) if status else TODO_STATUS_OPEN
    if not normalized_status:
        raise ValueError("todo status must be one of: open, done, blocked, deferred")
    priority_plan = plan_todo_priority({}, {"text": text, **({"priority": priority} if priority is not None else {})})
    todo_text = str(priority_plan["text"])
    if validation_command and validation_command_json:
        raise ValueError(
            "--validation-command and --validation-command-json are mutually "
            "exclusive; declare the validation command in exactly one form"
        )
    validation_argv = completion_validation_module.normalize_validation_command_json(
        validation_command_json
    )
    if validation_timeout_seconds is not None:
        if not validation_command and validation_argv is None:
            raise ValueError(
                "--validation-timeout-seconds requires --validation-command "
                "or --validation-command-json"
            )
        if not (
            1 <= validation_timeout_seconds
            <= completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS
        ):
            raise ValueError(
                "--validation-timeout-seconds must be between 1 and "
                f"{completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS}"
            )
    registered_agents = registered_agent_ids_from_registry(registry_path, goal_id)
    effective_excluded_agents = (
        require_todo_excluded_agents(excluded_agents)
        if excluded_agents is not None
        else None
    )
    authoring_scope = plan_todo_authoring_scope(
        command="create", role=role, goal_id=goal_id, registered_agents=registered_agents,
        intent={
            "task_class": task_class, "status": status, "actor_agent_id": agent_id,
            "claimed_by": claimed_by, "bound_agent": bound_agent, "goal_bound": goal_bound,
            "blocks_agent": blocks_agent, "global_gate": global_gate,
            "excluded_agents": effective_excluded_agents, "resume_when": resume_when,
            "task_repository": task_repository, "task_domain": task_domain,
            "capability_binding_ref": capability_binding_ref,
        },
    )
    # The shared plan already validates and normalizes both identities from
    # this registry snapshot; the transaction still rechecks its own source.
    effective_claimed_by = authoring_scope["claimed_by"]
    effective_agent_id = authoring_scope["actor_agent_id"]
    effective_blocks_agent = authoring_scope["blocks_agent"]
    effective_bound_agent = authoring_scope["bound_agent"]
    effective_goal_bound = authoring_scope["goal_bound"]
    normalized_unblocks_todo_id = normalize_todo_id(unblocks_todo_id) if unblocks_todo_id else None
    if unblocks_todo_id and not normalized_unblocks_todo_id:
        raise ValueError("unblocks_todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>")
    normalized_resume_when = require_supported_todo_resume_when(resume_when)
    if normalized_status == TODO_STATUS_DEFERRED and not normalized_resume_when:
        raise ValueError("deferred todo add requires --resume-when with a supported condition")
    updated_at = now_local()
    normalized_monitor_metadata = todo_monitor_metadata.require_monitor_metadata_scope(
        monitor_metadata=monitor_metadata, role=role, task_class=task_class,
        generated_at=updated_at, resume_when=normalized_resume_when,
        enforce_boundedness=True,
    )
    canonical_create = create_canonical_todo_if_promoted(
        operation_id=operation_id,
        expected_provider_revision=expected_provider_revision,
        registry_path=registry_path,
        runtime_root=shadow_runtime_root,
        goal_id=goal_id,
        role=role,
        text=todo_text,
        status=normalized_status,
        actor_agent_id=effective_agent_id or effective_claimed_by,
        claimed_by=effective_claimed_by,
        metadata={
            "priority": priority_plan["priority"],
            "title": priority_plan["title"],
            "task_class": task_class,
            "action_kind": action_kind,
            "task_domain": task_domain,
            "capability_binding_ref": capability_binding_ref,
            "task_repository": task_repository,
            "continuation_policy": continuation_policy,
            "required_write_scopes": required_write_scopes,
            "required_capabilities": required_capabilities,
            "target_capabilities": target_capabilities,
            "explore_result_node_refs": explore_result_node_refs,
            "decision_scope": decision_scope,
            "required_decision_scopes": required_decision_scopes,
            "bound_agent": effective_bound_agent,
            "goal_bound": True if role == "user" and effective_goal_bound else None,
            "blocks_agent": effective_blocks_agent,
            "excluded_agents": effective_excluded_agents,
            "global_gate": True if global_gate else None,
            "unblocks_todo_id": normalized_unblocks_todo_id,
            "replan_obligation_id": replan_obligation_id,
            "resume_when": normalized_resume_when,
            "validation_command": validation_command,
            "validation_command_argv": validation_argv,
            "validation_label": validation_label,
            "validation_timeout_seconds": validation_timeout_seconds,
            **normalized_monitor_metadata,
            "note": note,
            "updated_at": updated_at,
        },
        project=project,
        state_file=state_file,
        dry_run=dry_run,
    )
    if canonical_create is not None:
        return canonical_create
    if expected_provider_revision is not None:
        raise ValueError("reviewed provider revision requires canonical authority")
    if operation_id is not None:
        raise ValueError("todo add --operation-id requires promoted canonical authority")
    from ...todos import _add_goal_todo_legacy

    return _add_goal_todo_legacy(**call, _prepared={
        "effective_agent_id": effective_agent_id,
        "effective_blocks_agent": effective_blocks_agent,
        "effective_bound_agent": effective_bound_agent,
        "effective_claimed_by": effective_claimed_by,
        "effective_excluded_agents": effective_excluded_agents,
        "effective_goal_bound": effective_goal_bound,
        "normalized_monitor_metadata": normalized_monitor_metadata,
        "normalized_resume_when": normalized_resume_when,
        "normalized_status": normalized_status,
        "normalized_unblocks_todo_id": normalized_unblocks_todo_id,
        "replan_obligation_id": replan_obligation_id,
        "shadow_runtime_root": shadow_runtime_root,
        "todo_text": todo_text,
        "updated_at": updated_at,
    })


def _update_completion_validation_declaration(
    *, validation_command: str | None, validation_command_json: str | None,
    validation_label: str | None, validation_timeout_seconds: int | None,
    update_operation_id: str | None, update_expected_provider_revision: str | None,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Decode the optional revision declaration before either mutation route."""
    if validation_command and validation_command_json:
        raise ValueError(
            "--validation-command and --validation-command-json are mutually exclusive"
        )
    validation_argv = completion_validation_module.normalize_validation_command_json(
        validation_command_json
    )
    validation_revision_requested = any(
        value is not None
        for value in (
            validation_command,
            validation_command_json,
            validation_label,
            validation_timeout_seconds,
        )
    )
    validation_revision_declaration = None
    if validation_revision_requested:
        if bool(validation_command) == (validation_argv is not None):
            raise ValueError(
                "completion validation revision requires exactly one command form"
            )
        if validation_timeout_seconds is not None and not (
            1 <= validation_timeout_seconds
            <= completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS
        ):
            raise ValueError(
                "--validation-timeout-seconds must be between 1 and "
                f"{completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS}"
            )
        if not update_operation_id or not update_expected_provider_revision or not agent_id:
            raise ValueError(
                "completion validation revision requires update operation id, "
                "expected provider revision, and actor agent id"
            )
        validation_revision_declaration = (
            completion_validation_declaration(
                {
                    "validation_command": validation_command,
                    "validation_command_argv": validation_argv,
                    "validation_label": validation_label,
                    "validation_timeout_seconds": validation_timeout_seconds,
                }
            )
        )
        if validation_revision_declaration is None:
            raise ValueError("completion validation revision declaration is empty")
    return validation_revision_declaration


def update_goal_todo(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    todo_id: str,
    text: str | None = None,
    priority: str | None = None,
    clear_priority: bool = False,
    status: str | None = None,
    role: str | None = None,
    note: str | None = None,
    validation_command: str | None = None,
    validation_command_json: str | None = None,
    validation_label: str | None = None,
    validation_timeout_seconds: int | None = None,
    evidence: str | None = None,
    reason: str | None = None,
    task_class: str | None = None,
    action_kind: str | None = None,
    task_domain: str | None = None,
    task_repository: str | None = None,
    continuation_policy: str | None = None,
    required_write_scopes: list[str] | None = None,
    required_capabilities: list[str] | None = None,
    target_capabilities: list[str] | None = None,
    explore_result_node_refs: list[str] | None = None,
    append_explore_result_node_refs: list[str] | None = None,
    decision_scope: Any = None,
    required_decision_scopes: Any = None,
    claimed_by: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool = False,
    blocks_agent: str | None = None,
    clear_blocks_agent: bool = False,
    excluded_agents: list[str] | None = None,
    clear_excluded_agents: bool = False,
    global_gate: bool = False, clear_global_gate: bool = False,
    agent_id: str | None = None, authority_reason: str | None = None,
    unblocks_todo_id: str | None = None,
    successor_todo_ids: list[str] | None = None,
    resume_when: str | None = None,
    clear_resume_when: bool = False,
    no_followup: bool | None = None,
    monitor_metadata: todo_monitor_metadata.MonitorMetadataInput = None,
    enforce_monitor_boundedness: bool = True,
    monitor_gate_scope_guard: bool = False,
    clear_claim: bool = False,
    claim_only: bool = False,
    claim_operation_id: str | None = None,
    update_operation_id: str | None = None,
    update_expected_provider_revision: str | None = None,
    update_expected_registry_sha256: str | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    call = dict(locals())
    if priority is not None and clear_priority:
        raise ValueError("provide either priority or clear_priority, not both")
    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    validation_revision_declaration = _update_completion_validation_declaration(
        validation_command=validation_command, validation_command_json=validation_command_json,
        validation_label=validation_label, validation_timeout_seconds=validation_timeout_seconds,
        update_operation_id=update_operation_id,
        update_expected_provider_revision=update_expected_provider_revision, agent_id=agent_id,
    )
    if claim_only and any(value is not None for value in (
        update_operation_id, update_expected_provider_revision, update_expected_registry_sha256,
    )):
        raise ValueError("Todo update identity and review basis cannot be used for todo claim")
    if excluded_agents and clear_excluded_agents:
        raise ValueError(
            "todo update accepts either excluded_agents or clear_excluded_agents, not both"
        )
    if blocks_agent and clear_blocks_agent:
        raise ValueError("todo update accepts either blocks_agent or clear_blocks_agent, not both")
    if bound_agent and goal_bound:
        raise ValueError("todo update accepts either bound_agent or goal_bound, not both")
    if resume_when and clear_resume_when:
        raise ValueError(
            "todo update accepts either resume_when or clear_resume_when, not both"
        )
    promoted_claim = claim_only and local_authority_is_promoted(
        runtime_root=shadow_runtime_root, goal_id=goal_id
    )
    if claim_operation_id is not None:
        if not claim_only:
            raise ValueError("claim_operation_id is supported only by todo claim")
        if not promoted_claim:
            raise ValueError("--claim-operation-id requires promoted canonical authority; no legacy write attempted")
    if task_lease_expected_version is not None and task_lease_idempotency_key is None:
        raise ValueError(
            "--task-lease-expected-version requires --task-lease-idempotency-key"
        )
    if task_lease_idempotency_key is not None and claim_only and not promoted_claim:
        raise ValueError(
            "--task-lease-idempotency-key on todo claim requires promoted canonical authority; no legacy write attempted"
        )
    if promoted_claim:
        unsupported_claim_values = (
            text, priority, clear_priority or None, status, note,
            validation_command, validation_command_json, validation_label,
            validation_timeout_seconds, evidence, reason, task_class, action_kind,
            task_domain, task_repository, continuation_policy,
            required_write_scopes, required_capabilities, target_capabilities,
            explore_result_node_refs, append_explore_result_node_refs, decision_scope, required_decision_scopes,
            bound_agent, blocks_agent, excluded_agents, unblocks_todo_id,
            successor_todo_ids, resume_when, no_followup, monitor_metadata,
        )
        if (
            any(value is not None and value is not False for value in unsupported_claim_values)
            or goal_bound
            or clear_blocks_agent
            or clear_excluded_agents
            or global_gate
            or clear_global_gate
            or clear_resume_when
            or clear_claim
        ):
            raise ValueError(
                "todo claim only accepts todo_id, claimed_by, agent_id, optional role, "
                "project, state_file, and dry_run"
            )
        canonical_claim = claim_canonical_todo_if_promoted(
            registry_path=registry_path,
            runtime_root=shadow_runtime_root,
            goal_id=goal_id,
            todo_id=normalize_todo_id(todo_id) or todo_id,
            role=role,
            claimed_by=claimed_by or "",
            actor_agent_id=agent_id,
            dry_run=dry_run,
            operation_id=claim_operation_id,
            task_lease_idempotency_key=task_lease_idempotency_key,
            task_lease_expected_version=task_lease_expected_version,
            project=project,
            state_file=state_file,
        )
        if canonical_claim is not None:
            return canonical_claim
    # Translate the compatibility-sized CLI signature exactly once.  The
    # canonical transaction now owns ordinary role/binding/work-declaration
    # edits as well as text/note corrections. User completion is dispatched to
    # the terminal owner; Monitor observations retain their dedicated route.
    planning_intent = build_canonical_update_intent(
        status=status, evidence=evidence, reason=reason, task_class=task_class,
        action_kind=action_kind, task_domain=task_domain,
        task_repository=task_repository, continuation_policy=continuation_policy,
        required_write_scopes=required_write_scopes,
        required_capabilities=required_capabilities,
        target_capabilities=target_capabilities,
        explore_result_node_refs=explore_result_node_refs,
        append_explore_result_node_refs=append_explore_result_node_refs,
        decision_scope=decision_scope,
        required_decision_scopes=required_decision_scopes,
        claimed_by=claimed_by, bound_agent=bound_agent, goal_bound=goal_bound,
        blocks_agent=blocks_agent, clear_blocks_agent=clear_blocks_agent,
        excluded_agents=excluded_agents,
        clear_excluded_agents=clear_excluded_agents,
        global_gate=global_gate, clear_global_gate=clear_global_gate,
        unblocks_todo_id=unblocks_todo_id,
        successor_todo_ids=successor_todo_ids, resume_when=resume_when,
        clear_resume_when=clear_resume_when, no_followup=no_followup,
        clear_claim=clear_claim,
    )
    if priority is not None:
        planning_intent["priority"] = priority
    if clear_priority:
        planning_intent["clear_priority"] = True
    monitor_intent = todo_monitor_metadata.monitor_metadata_intent(monitor_metadata)
    # Promotion selects the authority, not Python's estimate of edit validity.
    # The typed decoder must also reject empty/invalid edits without importing
    # an unpromoted source writer. Unpromoted Goals retain their legacy route.
    if not claim_only:
        canonical_edit = update_canonical_todo_if_promoted(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, todo_id=normalize_todo_id(todo_id) or todo_id,
            actor_agent_id=agent_id, role=role, text=text, note=note, dry_run=dry_run,
            project=project, state_file=state_file,
            operation_id=update_operation_id,
            authority_reason=authority_reason,
            expected_provider_revision=update_expected_provider_revision,
            expected_registry_sha256=update_expected_registry_sha256,
            task_lease_idempotency_key=task_lease_idempotency_key,
            task_lease_expected_version=task_lease_expected_version,
            monitor_observation=monitor_metadata if isinstance(monitor_metadata, todo_monitor_metadata.MonitorPollObservation) else None,
            completion_validation_revision=validation_revision_declaration,
            planning_intent={**planning_intent, **(
                {"monitor_metadata": monitor_intent["metadata"]} if monitor_intent["metadata"] else {}
            )},
        )
        if canonical_edit is not None:
            return canonical_edit
    if (update_operation_id is not None or update_expected_provider_revision is not None
        or update_expected_registry_sha256 is not None) or (not claim_only and (
        task_lease_idempotency_key is not None or task_lease_expected_version is not None
    ) and not (monitor_intent["observation"] is not None and status is None)):
        raise ValueError("update operation id and lease proof require a supported promoted update; no legacy write attempted")
    from ...todos import _update_goal_todo_legacy

    return _update_goal_todo_legacy(**call, _prepared={
        "monitor_intent": monitor_intent,
        "shadow_runtime_root": shadow_runtime_root,
    })


@provider_first_terminal_lifecycle("complete")
def complete_goal_todo(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    todo_id: str,
    role: str | None = None,
    decision_outcome: str | None = None,
    evidence: str | None = None,
    completion_result_file: Path | None = None,
    completion_turn_key: str | None = None,
    completion_identity_source: str | None = None,
    terminal_review_basis: Mapping[str, Any] | None = None,
    completion_delivery_workspace: Mapping[str, Any] | None = None,
    completion_validation_workspace_path: Path | None = None,
    task_lease_idempotency_key: str | None = None,
    task_lease_expected_version: int | None = None,
    note: str | None = None,
    no_followup: bool = False,
    successor_todo_ids: list[str] | None = None,
    claimed_by: str | None = None,
    clear_claim: bool = False,
    next_agent_todo: str | None = None,
    next_user_todo: str | None = None,
    next_user_task_class: str | None = None,
    next_claimed_by: str | None = None,
    next_task_class: str | None = None,
    next_action_kind: str | None = None,
    next_task_repository: str | None = None,
    next_required_capabilities: list[str] | None = None,
    next_continuation_policy: str | None = None,
    next_excluded_agents: list[str] | None = None,
    self_merged: bool = False,
    agent_id: str | None = None, authority_reason: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    call = dict(locals())
    from ...todos import _complete_goal_todo_legacy

    return _complete_goal_todo_legacy(**call)
