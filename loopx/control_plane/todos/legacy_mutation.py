"""Retained unpromoted Markdown writer; canonical callers never import this module."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from json import dumps as json_dumps
from pathlib import Path
from typing import Any

from ...agent_registry import (
    registered_agent_ids_from_registry,
    require_registered_agent_id,
)
from ...state_refresh import now_local
from ...status import MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE
from .contract import (
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    build_todo_id,
    format_todo_metadata_line,
    metadata_line_for_todo_block,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_explore_result_node_refs,
    normalize_target_capabilities,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_capability_binding_ref,
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
    normalize_todo_decision_scope,
    normalize_todo_excluded_agents,
    normalize_todo_global_gate,
    normalize_todo_goal_bound,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_required_decision_scopes,
    normalize_todo_replan_obligation_id,
    normalize_todo_resume_when,
    normalize_todo_status,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
    parse_todo_metadata_line,
    require_todo_excluded_agents,
    resolve_next_user_task_class,
    require_supported_todo_resume_when,
    todo_marker_for_status,
)
from .active_state_editing import (
    TODO_SECTION_HEADINGS,
    find_todo_block,
    insert_into_existing_section,
    insert_new_section,
    replace_updated_at,
    section_bounds,
    set_todo_marker,
    todo_blocks,
)
from .addition import matching_todo_block, require_replan_successor_rebinding
from .completed_archive import archive_completed_todo_lines
from .completion_policy import (
    completion_policy_from_transaction,
)
from .completion_transaction import (
    locked_todo_completion_transaction,
    materialized_todo_completion_replay,
    require_completion_successor_todo_ids,
    user_todo_completion_metadata_updates,
)
from ..todos import completion_validation as completion_validation_module
from .mutation_response import serialize_added_todo_payload
from .next_action_runtime import (
    apply_added_todo_next_action,
    settle_completed_todo_next_action,
)
from ..todos import monitor_metadata as todo_monitor_metadata
from .mutation_authority import (
    authorize_todo_lifecycle_mutation,
    todo_update_authority_action,
)
from .succession_warning import build_open_parent_successor_advisory
from .successor_derivation import (
    build_successor_intents,
    derive_successor_proposals,
    successor_add_kwargs,
)
from .text import normalize_new_todo
from .todo_semantics import todo_priority_label
from .unblock_resume import (
    apply_completed_user_todo_lifecycle,
    completion_decision_target,
    require_completion_decision_outcome,
)
from .write_correctness import (
    attach_todo_write_correctness_dry_run_packet as _attach_todo_write_correctness_dry_run_packet,
)
from .authoring_scope import require_user_todo_task_class
from ..coordination.legacy_writer_fence import legacy_todo_write_transaction
from .path_resolution import resolve_todo_state_path
from .handoff_mode import (
    goal_handoff_mode,
    enter_added_todo_ownership_handoff_gate,
    enter_todo_ownership_handoff_gate,
    resolve_todo_completion_handoff,
)
from ...paths import effective_runtime_root
from ..work_items.task_lease import (
    enter_terminal_todo_lease_fence,
    hold_task_lease_mutation_fence,
    release_verified_task_lease_fence,
)


ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE = max(
    0, MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE - 2
)


def _validate_legacy_add_declaration(
    *,
    role: str,
    task_class: str | None,
    blocks_agent: str | None,
    excluded_agents: list[str] | None,
    capability_binding_ref: str | None,
    global_gate: bool | None,
    validation_command: str | None,
    validation_command_json: str | None,
    validation_timeout_seconds: int | None,
) -> list[str] | None:
    """Validate the retained writer's declaration before any line mutation."""
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
            1
            <= validation_timeout_seconds
            <= completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS
        ):
            raise ValueError(
                "--validation-timeout-seconds must be between 1 and "
                f"{completion_validation_module.COMPLETION_VALIDATION_TIMEOUT_MAX_SECONDS} (the outer "
                "CLI/MCP subprocess budget is 30s, and a timed-out "
                "validation must still produce a typed receipt)"
            )
    if role == "agent" and blocks_agent:
        raise ValueError(
            "blocks_agent is only valid for user gates; use excluded_agents for "
            "agent executor constraints"
        )
    if role != "agent" and excluded_agents:
        raise ValueError("excluded_agents is only valid for agent todos")
    if role != "agent" and capability_binding_ref:
        raise ValueError("capability_binding_ref is only valid for agent todos")
    require_user_todo_task_class(
        role=role,
        task_class=task_class,
        blocks_agent=blocks_agent,
        global_gate=global_gate,
    )
    return validation_argv


def add_todo_to_lines(
    lines: list[str],
    *,
    role: str,
    text: str,
    status: str | None = None,
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
    goal_bound: bool | None = None,
    blocks_agent: str | None = None,
    excluded_agents: list[str] | None = None,
    global_gate: bool | None = None,
    unblocks_todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    resume_when: str | None = None,
    validation_command: str | None = None,
    validation_command_json: str | None = None,
    validation_label: str | None = None,
    validation_timeout_seconds: int | None = None,
    monitor_metadata: dict[str, Any] | None = None,
    note: str | None = None,
    evidence: str | None = None,
    updated_at: str | None = None,
    _prepared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .line_update import upsert_todo_metadata

    # Only public add supplies its same-call typed draft. Standalone line
    # codecs and successor creation still validate their own complete input.
    if _prepared is not None:
        validation_argv = _prepared["validation_argv"]
        todo_text = _prepared["todo_text"]
        normalized_status = _prepared["normalized_status"]
        normalized_resume_when = _prepared["normalized_resume_when"]
        normalized_monitor_metadata = _prepared["normalized_monitor_metadata"]
    else:
        validation_argv = _validate_legacy_add_declaration(
            role=role,
            task_class=task_class,
            blocks_agent=blocks_agent,
            excluded_agents=excluded_agents,
            capability_binding_ref=capability_binding_ref,
            global_gate=global_gate,
            validation_command=validation_command,
            validation_command_json=validation_command_json,
            validation_timeout_seconds=validation_timeout_seconds,
        )
        todo_text = normalize_new_todo(text)
        normalized_status = normalize_todo_status(status) if status else TODO_STATUS_OPEN
        if status and not normalized_status:
            raise ValueError("todo status must be one of: open, done, blocked, deferred")
        assert normalized_status is not None
        normalized_resume_when = require_supported_todo_resume_when(resume_when)
        normalized_monitor_metadata = todo_monitor_metadata.require_monitor_metadata_scope(
            monitor_metadata=monitor_metadata,
            role=role,
            task_class=task_class, generated_at=updated_at,
        )
    bounds = section_bounds(lines, role)
    section = bounds[2] if bounds else TODO_SECTION_HEADINGS[role]
    existing_blocks = (
        todo_blocks(lines, bounds[0], bounds[1], role=role, source_section=section)
        if bounds
        else []
    )
    block = matching_todo_block(
        lines,
        bounds[0],
        bounds[1],
        todo_text,
        role=role,
        source_section=section,
    ) if bounds else None
    added = block is None
    metadata_updated = False
    status_changed = False

    if block is None:
        todo_id = build_todo_id(
            role=role,
            source_section=section,
            index=len(existing_blocks) + 1,
            text=todo_text,
        )
        metadata_line = format_todo_metadata_line(
            todo_id=todo_id,
            status=normalized_status,
            task_class=task_class,
            action_kind=action_kind,
            task_domain=task_domain,
            capability_binding_ref=capability_binding_ref,
            task_repository=task_repository,
            continuation_policy=continuation_policy,
            required_write_scopes=required_write_scopes,
            required_capabilities=required_capabilities,
            target_capabilities=target_capabilities,
            explore_result_node_refs=explore_result_node_refs,
            decision_scope=decision_scope,
            required_decision_scopes=required_decision_scopes,
            claimed_by=claimed_by,
            bound_agent=bound_agent,
            goal_bound=goal_bound,
            blocks_agent=blocks_agent,
            excluded_agents=excluded_agents,
            global_gate=global_gate,
            unblocks_todo_id=unblocks_todo_id,
            replan_obligation_id=replan_obligation_id,
            resume_when=normalized_resume_when,
            validation_command=validation_command,
            validation_command_argv=(
                json_dumps(validation_argv)
                if validation_argv is not None
                else None
            ),
            validation_label=validation_label,
            validation_timeout_seconds=(
                str(validation_timeout_seconds)
                if validation_timeout_seconds is not None
                else None
            ),
            **normalized_monitor_metadata,
            note=note,
            evidence=evidence,
            updated_at=updated_at,
        )
        marker = todo_marker_for_status(normalized_status)
        todo_line = "\n".join([f"- [{marker}] {todo_text}", metadata_line] if metadata_line else [f"- [{marker}] {todo_text}"])
        if bounds:
            insert_into_existing_section(lines, bounds[0], bounds[1], todo_line)
        else:
            insert_new_section(lines, role, todo_line)
        effective_metadata = parse_todo_metadata_line(metadata_line or "") or {}
    else:
        updates: dict[str, Any] = {
            "todo_id": block.get("todo_id"),
            "status": normalized_status if status else block.get("status") or TODO_STATUS_OPEN,
        }
        if status:
            status_changed = set_todo_marker(lines, block, normalized_status)
        for metadata_field, metadata_value in (
            ("task_class", task_class),
            ("action_kind", action_kind),
            ("task_domain", task_domain),
            ("task_repository", task_repository),
            ("continuation_policy", continuation_policy),
            ("claimed_by", claimed_by),
            ("blocks_agent", blocks_agent),
            ("unblocks_todo_id", unblocks_todo_id),
            ("note", note),
            ("evidence", evidence),
        ):
            if metadata_value:
                updates[metadata_field] = metadata_value
        if capability_binding_ref:
            requested_binding_ref = normalize_todo_capability_binding_ref(
                capability_binding_ref
            )
            if not requested_binding_ref:
                raise ValueError(
                    "capability_binding_ref must be a public-safe namespaced token"
                )
            existing_binding_ref = normalize_todo_capability_binding_ref(
                block.get("capability_binding_ref")
            )
            if (
                existing_binding_ref
                and existing_binding_ref != requested_binding_ref
            ):
                raise ValueError(
                    "capability_binding_ref is immutable once set"
                )
            updates["capability_binding_ref"] = requested_binding_ref
        if required_write_scopes is not None:
            updates["required_write_scopes"] = required_write_scopes
        if required_capabilities is not None:
            updates["required_capabilities"] = required_capabilities
        if target_capabilities is not None:
            updates["target_capabilities"] = target_capabilities
        if explore_result_node_refs is not None:
            updates["explore_result_node_refs"] = explore_result_node_refs
        if decision_scope is not None:
            updates["decision_scope"] = decision_scope
        if required_decision_scopes is not None:
            updates["required_decision_scopes"] = required_decision_scopes
        if bound_agent:
            updates["bound_agent"] = bound_agent
            updates["goal_bound"] = None
        elif goal_bound is not None:
            updates["bound_agent"] = None
            updates["goal_bound"] = goal_bound
        if excluded_agents is not None:
            updates["excluded_agents"] = excluded_agents
        if global_gate is not None:
            updates["global_gate"] = global_gate
        if replan_obligation_id:
            updates["replan_obligation_id"] = require_replan_successor_rebinding(
                existing_obligation_id=block.get("replan_obligation_id"),
                requested_obligation_id=replan_obligation_id,
            )
        if normalized_resume_when:
            updates["resume_when"] = normalized_resume_when
        updates.update(normalized_monitor_metadata)
        if updated_at and not block.get("updated_at"):
            updates["updated_at"] = updated_at
        metadata_line = metadata_line_for_todo_block(block, updates)
        metadata_updated = upsert_todo_metadata(lines, block, metadata_line)
        todo_id = str(block.get("todo_id") or "")
        effective_metadata = parse_todo_metadata_line(metadata_line or "") or {}

    return {
        "added": added,
        "already_exists": not added,
        "metadata_updated": metadata_updated,
        "status_changed": status_changed,
        "changed": added or metadata_updated or status_changed,
        "role": role,
        "section": section,
        "todo": todo_text,
        "priority": todo_priority_label({"text": todo_text}),
        "todo_id": todo_id,
        "status": normalize_todo_status(effective_metadata.get("status")) or normalized_status,
        "task_class": effective_metadata.get("task_class") or task_class,
        "action_kind": effective_metadata.get("action_kind") or action_kind,
        "task_domain": normalize_todo_task_domain(
            effective_metadata.get("task_domain") or task_domain
        ),
        "capability_binding_ref": effective_metadata.get("capability_binding_ref")
        or capability_binding_ref,
        "task_repository": normalize_todo_task_repository(
            effective_metadata.get("task_repository") or task_repository
        ),
        "continuation_policy": normalize_todo_continuation_policy(
            effective_metadata.get("continuation_policy") or continuation_policy
        ),
        "required_write_scopes": normalize_required_write_scopes(
            effective_metadata.get("required_write_scopes") or required_write_scopes
        ),
        "required_capabilities": normalize_required_capabilities(
            effective_metadata.get("required_capabilities") or required_capabilities
        ),
        "target_capabilities": normalize_target_capabilities(
            effective_metadata.get("target_capabilities") or target_capabilities
        ),
        "explore_result_node_refs": normalize_explore_result_node_refs(
            effective_metadata.get("explore_result_node_refs") or explore_result_node_refs
        ),
        "decision_scope": normalize_todo_decision_scope(
            effective_metadata.get("decision_scope") or decision_scope
        ),
        "required_decision_scopes": normalize_todo_required_decision_scopes(
            effective_metadata.get("required_decision_scopes") or required_decision_scopes
        ),
        "claimed_by": normalize_todo_claimed_by(effective_metadata.get("claimed_by")),
        "bound_agent": normalize_todo_bound_agent(effective_metadata.get("bound_agent")),
        "goal_bound": normalize_todo_goal_bound(effective_metadata.get("goal_bound")),
        "blocks_agent": normalize_todo_blocks_agent(effective_metadata.get("blocks_agent")),
        "excluded_agents": normalize_todo_excluded_agents(
            effective_metadata.get("excluded_agents")
        ),
        "global_gate": normalize_todo_global_gate(effective_metadata.get("global_gate")),
        "unblocks_todo_id": normalize_todo_id(effective_metadata.get("unblocks_todo_id")),
        "replan_obligation_id": normalize_todo_replan_obligation_id(
            effective_metadata.get("replan_obligation_id")
        ),
        "resume_when": normalize_todo_resume_when(effective_metadata.get("resume_when")),
        "target_key": effective_metadata.get("target_key"),
        "cadence": effective_metadata.get("cadence"),
        "next_due_at": effective_metadata.get("next_due_at"),
        "expires_at": effective_metadata.get("expires_at"),
        "watch_only": effective_metadata.get("watch_only"),
        "note": effective_metadata.get("note") or note,
        "evidence": effective_metadata.get("evidence") or evidence,
        "updated_at": effective_metadata.get("updated_at") or updated_at,
    }


def _add_goal_todo_legacy(
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
    _prepared: dict[str, Any],
) -> dict[str, Any]:
    effective_agent_id = _prepared["effective_agent_id"]
    effective_blocks_agent = _prepared["effective_blocks_agent"]
    effective_bound_agent = _prepared["effective_bound_agent"]
    effective_claimed_by = _prepared["effective_claimed_by"]
    effective_excluded_agents = _prepared["effective_excluded_agents"]
    effective_goal_bound = _prepared["effective_goal_bound"]
    normalized_monitor_metadata = _prepared["normalized_monitor_metadata"]
    normalized_resume_when = _prepared["normalized_resume_when"]
    normalized_status = _prepared["normalized_status"]
    normalized_unblocks_todo_id = _prepared["normalized_unblocks_todo_id"]
    replan_obligation_id = _prepared["replan_obligation_id"]
    shadow_runtime_root = _prepared["shadow_runtime_root"]
    todo_text = _prepared["todo_text"]
    updated_at = _prepared["updated_at"]
    from ..coordination.runtime_shadow_writer_adapter import begin_todo_runtime_shadow_capture, write_captured_todo_state, settle_todo_runtime_shadow_capture

    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )

    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, agent_id or claimed_by, "todo_add", dry_run,
        runtime_root=shadow_runtime_root,
    ), ExitStack() as handoff_gate_stack:
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class="todo_add", original_text=original,
        )
        lines = original.splitlines()
        handoff_gate = enter_added_todo_ownership_handoff_gate(
            handoff_gate_stack,
            lines=lines,
            state_text=original,
            registry_path=registry_path,
            goal_id=goal_id,
            role=role,
            text=todo_text,
            claimed_by=effective_claimed_by,
            actor_agent_id=effective_agent_id or effective_claimed_by,
            runtime_root=shadow_runtime_root,
        )
        add_result = add_todo_to_lines(
            lines,
            role=role,
            text=todo_text,
            status=normalized_status,
            task_class=task_class,
            action_kind=action_kind,
            task_domain=task_domain,
            capability_binding_ref=capability_binding_ref,
            task_repository=task_repository,
            continuation_policy=continuation_policy,
            required_write_scopes=required_write_scopes,
            required_capabilities=required_capabilities,
            target_capabilities=target_capabilities,
            explore_result_node_refs=explore_result_node_refs,
            decision_scope=decision_scope,
            required_decision_scopes=required_decision_scopes,
            claimed_by=effective_claimed_by,
            bound_agent=effective_bound_agent,
            goal_bound=(
                True if role == "user" and effective_goal_bound else None
            ),
            blocks_agent=effective_blocks_agent,
            excluded_agents=effective_excluded_agents,
            global_gate=True if global_gate else None,
            unblocks_todo_id=normalized_unblocks_todo_id,
            replan_obligation_id=replan_obligation_id,
            resume_when=normalized_resume_when,
            validation_command=validation_command,
            validation_command_json=validation_command_json,
            validation_label=validation_label,
            validation_timeout_seconds=validation_timeout_seconds,
            monitor_metadata=normalized_monitor_metadata,
            note=note,
            updated_at=updated_at,
            _prepared=_prepared,
        )
        added = bool(add_result["added"])
        metadata_updated = bool(add_result["metadata_updated"])
        changed = apply_added_todo_next_action(lines, role=role, add_result=add_result)

        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if changed:
            new_text = replace_updated_at(new_text, updated_at)
        if changed and not dry_run:
            write_captured_todo_state(shadow_capture, runtime_root=shadow_runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=new_text)

    payload = serialize_added_todo_payload(
        add_result=add_result, goal_id=goal_id, role=role, todo_text=todo_text,
        agent_id=effective_agent_id, state_file=resolved_state_file,
        project=resolved_project, updated_at=updated_at, dry_run=dry_run,
        added=added, metadata_updated=metadata_updated, changed=changed,
        handoff_gate=handoff_gate,
    )
    payload = _attach_todo_write_correctness_dry_run_packet(
        payload,
        goal_id=goal_id,
        write_class="todo_add",
        state_text=original,
    )
    return settle_todo_runtime_shadow_capture(
        payload, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, capture=shadow_capture,
    )


def _update_goal_todo_legacy(
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
    _prepared: dict[str, Any],
) -> dict[str, Any]:
    monitor_intent = _prepared["monitor_intent"]
    shadow_runtime_root = _prepared["shadow_runtime_root"]
    from .line_update import apply_todo_update_to_lines
    from ..coordination.runtime_shadow_writer_adapter import begin_todo_runtime_shadow_capture, write_captured_todo_state, settle_todo_runtime_shadow_capture

    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    requested_successor_todo_ids = require_completion_successor_todo_ids(
        successor_todo_ids
    )
    completion_validation_gate = (
        completion_validation_module.prepare_user_todo_update_completion(
            status=status,
            state_file=resolved_state_file,
            todo_id=todo_id,
            role=role,
            registry_path=registry_path,
            goal_id=goal_id,
            dry_run=dry_run,
            no_followup=no_followup is True,
            requested_has_successor=bool(requested_successor_todo_ids),
        )
    )
    if completion_validation_gate is not None:
        validation_failure: dict[str, Any] | None = completion_validation_gate.get("failure")
        if validation_failure is not None:
            return validation_failure
    write_class = "todo_claim" if claim_only else "todo_update"
    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, agent_id or claimed_by, write_class, dry_run,
        runtime_root=shadow_runtime_root,
    ), ExitStack() as handoff_gate_stack:
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class=write_class, original_text=original,
        )
        lines = original.splitlines()
        updated_at = now_local()
        effective_claimed_by = (
            require_registered_agent_id(
                registry_path=registry_path,
                goal_id=goal_id,
                agent_id=claimed_by,
            )
            if claimed_by
            else None
        )
        effective_agent_id = (
            require_registered_agent_id(
                registry_path=registry_path,
                goal_id=goal_id,
                agent_id=agent_id,
                field="agent_id",
            )
            if agent_id
            else None
        )
        effective_blocks_agent = (
            require_registered_agent_id(
                registry_path=registry_path,
                goal_id=goal_id,
                agent_id=blocks_agent,
                field="blocks_agent",
            )
            if blocks_agent
            else None
        )
        effective_bound_agent = (
            require_registered_agent_id(
                registry_path=registry_path,
                goal_id=goal_id,
                agent_id=bound_agent,
                field="bound_agent",
            )
            if bound_agent
            else None
        )
        existing_block_match = find_todo_block(lines, todo_id=todo_id, role=role)
        if not existing_block_match:
            normalized_todo_id = normalize_todo_id(todo_id) or todo_id
            raise ValueError(f"todo_id {normalized_todo_id!r} was not found in active user or agent todos")
        existing_role, _section, _start, _end, existing_block = existing_block_match
        target_role = role or existing_role
        authority_todo = dict(existing_block)
        authority_todo["role"] = target_role
        authority_action = todo_update_authority_action(
            existing_role=existing_role,
            role=role,
            claimed_by=claimed_by,
            clear_claim=clear_claim,
            other_values=(
                text, status, note, evidence, reason, task_class, action_kind,
                task_domain,
                task_repository, continuation_policy, required_write_scopes,
                required_capabilities, target_capabilities,
                explore_result_node_refs, append_explore_result_node_refs, decision_scope,
                required_decision_scopes, blocks_agent, clear_blocks_agent,
                bound_agent, goal_bound,
                excluded_agents, clear_excluded_agents, global_gate,
                clear_global_gate, unblocks_todo_id, successor_todo_ids,
                resume_when, clear_resume_when, no_followup,
            ),
            monitor_metadata=monitor_intent["observation"] or monitor_intent["metadata"],
        )
        mutation_authority = authorize_todo_lifecycle_mutation(
            registry_path=registry_path,
            goal_id=goal_id,
            command="claim" if claim_only else "update",
            todo=authority_todo,
            actor_agent_id=effective_agent_id,
            authority_action=None if claim_only else authority_action,
            authority_reason=authority_reason,
            requested_claimed_by=effective_claimed_by,
        )
        if monitor_gate_scope_guard:
            todo_monitor_metadata.require_locked_monitor_gate_scope(state_text=original,
                todo=authority_todo, observation=monitor_intent["observation"], agent_id=effective_agent_id)
        if monitor_intent["observation"] is not None and task_lease_idempotency_key is not None:
            # Explicit observation proof uses the existing native held fence
            # under the Markdown writer lock. Closing this guard does not retire
            # execution; the acquiring caller owns release after observation.
            handoff_gate_stack.enter_context(hold_task_lease_mutation_fence(
                registry_path=registry_path, runtime_root=shadow_runtime_root,
                goal_id=goal_id, todo_id=todo_id, todo=authority_todo, actor_agent_id=effective_agent_id,
                idempotency_key=task_lease_idempotency_key,
                expected_version=task_lease_expected_version,
                require_active_when_key_supplied=True,
                handoff={"handoff_mode": goal_handoff_mode(original)},
            ))
        handoff_gate = enter_todo_ownership_handoff_gate(
            handoff_gate_stack,
            state_text=original,
            registry_path=registry_path,
            goal_id=goal_id,
            todo_id=str(authority_todo.get("todo_id") or todo_id),
            mutation_authority=mutation_authority,
            actor_agent_id=effective_agent_id or effective_claimed_by,
            ownership_mutation=(claimed_by is not None or clear_claim) and target_role == "agent",
            runtime_root=shadow_runtime_root,
        )
        effective_excluded_agents = (
            [] if clear_excluded_agents else require_todo_excluded_agents(excluded_agents)
            if excluded_agents is not None else None
        )
        completion_metadata_updates_override = None
        if completion_validation_gate is not None:
            locked_completion = locked_todo_completion_transaction(
                validation_gate=completion_validation_gate,
                todo=existing_block,
                goal_id=goal_id,
                todo_id=todo_id,
                dry_run=dry_run,
                require_source_match=True,
                missing_is_drift=False,
            )
            locked_failure: dict[str, Any] | None = locked_completion["failure"]
            if locked_failure is not None:
                return locked_failure
            completion_metadata_updates_override = (
                user_todo_completion_metadata_updates(
                    locked_completion["transaction"],
                    todo_already_done=(
                        str(existing_block.get("status") or "")
                        == TODO_STATUS_DONE
                    ),
                )
            )
        normalized_unblocks_todo_id = normalize_todo_id(unblocks_todo_id) if unblocks_todo_id else None
        if unblocks_todo_id and not normalized_unblocks_todo_id:
            raise ValueError("unblocks_todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>")
        normalized_successor_todo_ids = requested_successor_todo_ids
        update_result = apply_todo_update_to_lines(
            lines,
            todo_id=todo_id,
            text=text,
            priority=priority,
            clear_priority=clear_priority,
            status=status,
            role=role,
            note=note,
            evidence=evidence,
            reason=reason,
            task_class=task_class,
            action_kind=action_kind,
            task_domain=task_domain,
            task_repository=task_repository,
            continuation_policy=continuation_policy,
            required_write_scopes=required_write_scopes,
            required_capabilities=required_capabilities,
            target_capabilities=target_capabilities,
            explore_result_node_refs=explore_result_node_refs,
            append_explore_result_node_refs=append_explore_result_node_refs,
            decision_scope=decision_scope,
            required_decision_scopes=required_decision_scopes,
            claimed_by=effective_claimed_by,
            bound_agent=effective_bound_agent,
            goal_bound=goal_bound,
            blocks_agent=effective_blocks_agent,
            clear_blocks_agent=clear_blocks_agent,
            excluded_agents=effective_excluded_agents,
            global_gate=True if global_gate else None,
            clear_global_gate=clear_global_gate,
            unblocks_todo_id=normalized_unblocks_todo_id,
            successor_todo_ids=normalized_successor_todo_ids if successor_todo_ids is not None else None,
            resume_when=resume_when,
            clear_resume_when=clear_resume_when,
            no_followup=no_followup,
            completion_metadata_updates_override=(
                completion_metadata_updates_override
            ),
            monitor_metadata=monitor_intent["metadata"],
            public_context={
                "goal_id": goal_id, "role": target_role,
                "actor_agent_id": effective_agent_id,
                "registered_agents": registered_agent_ids_from_registry(registry_path, goal_id),
                "monitor_observation": monitor_intent["observation"],
                "enforce_monitor_boundedness": enforce_monitor_boundedness,
            },
            clear_claim=clear_claim,
            claim_only=claim_only,
            updated_at=updated_at,
        )
        changed = bool(update_result["changed"])
        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if changed:
            new_text = replace_updated_at(new_text, updated_at)
        if changed and not dry_run:
            write_captured_todo_state(shadow_capture, runtime_root=shadow_runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=new_text)
    payload = {
        "ok": True,
        "dry_run": dry_run,
        "changed": changed,
        "goal_id": goal_id,
        "agent_id": effective_agent_id,
        "mutation_authority": mutation_authority,
        **handoff_gate,
        **update_result,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
        "updated_at": updated_at if changed else None,
    }
    if successor_todo_ids is not None:
        parent_successor_advisory = build_open_parent_successor_advisory(
            todo_id=update_result.get("todo_id"),
            status=update_result.get("status"),
            successor_todo_ids=update_result.get("successor_todo_ids"),
        )
        if parent_successor_advisory:
            payload["parent_successor_advisory"] = parent_successor_advisory
    payload = _attach_todo_write_correctness_dry_run_packet(
        payload,
        goal_id=goal_id,
        write_class=write_class,
        state_text=original,
    )
    return settle_todo_runtime_shadow_capture(
        payload, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, capture=shadow_capture,
    )


def _complete_goal_todo_legacy(
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
    from .line_update import apply_todo_update_to_lines, link_generated_successor_todo_ids
    from ..coordination.runtime_shadow_writer_adapter import begin_todo_runtime_shadow_capture, write_captured_todo_state, settle_todo_runtime_shadow_capture

    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if next_task_repository and not next_agent_todo:
        raise ValueError("--next-task-repository requires --next-agent-todo")
    if next_required_capabilities and not next_agent_todo:
        raise ValueError("--next-required-capability requires --next-agent-todo")
    normalized_successor_todo_ids = require_completion_successor_todo_ids(
        successor_todo_ids
    )
    effective_next_user_task_class = resolve_next_user_task_class(
        next_user_todo,
        next_user_task_class,
    )
    completion_policy_facts = {
        "claimed_by": claimed_by, "next_claimed_by": next_claimed_by,
        "next_agent_todo": next_agent_todo, "next_action_kind": next_action_kind,
        "next_continuation_policy": next_continuation_policy,
        "next_excluded_agents": next_excluded_agents or [],
        "self_merged": self_merged, "evidence": evidence,
    }
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    # Run caller-approved validation before locking so slow commands do not
    # hold the 5s mutation lock; return typed failure payloads unchanged.
    validation_gate = completion_validation_module.run_completion_validation_gate_with_source(
        state_file=resolved_state_file,
        todo_id=todo_id,
        role=role,
        registry_path=registry_path,
        goal_id=goal_id,
        dry_run=dry_run,
        no_followup=no_followup,
        completion_turn_key=completion_turn_key,
        completion_identity_source=completion_identity_source,
        requested_has_successor=bool(
            normalized_successor_todo_ids or next_agent_todo or next_user_todo
        ),
        completion_policy_facts=completion_policy_facts,
        requested_successor_todo_ids=normalized_successor_todo_ids,
        completion_delivery_workspace=completion_delivery_workspace,
        completion_validation_workspace_path=completion_validation_workspace_path,
    )
    validation_failure: dict[str, Any] | None = validation_gate.get("failure")
    if validation_failure is not None:
        return validation_failure
    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, agent_id or claimed_by, "todo_complete", dry_run,
        runtime_root=shadow_runtime_root,
    ), ExitStack() as lease_fence_stack:
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class="todo_complete", original_text=original,
        )
        lines = original.splitlines()
        updated_at = now_local()
        completion_match, completion_todo = (
            completion_validation_module.locked_todo_completion_source(
                lines=lines,
                state_file=resolved_state_file,
                project=resolved_project,
                registry_path=registry_path,
                goal_id=goal_id,
                todo_id=todo_id,
                role=role,
            )
        )
        effective_decision_outcome = require_completion_decision_outcome(
            completion_todo,
            decision_outcome,
            materialized=bool(completion_match),
        )
        if completion_todo is None:
            normalized_todo_id = normalize_todo_id(todo_id) or todo_id
            raise ValueError(
                f"todo_id {normalized_todo_id!r} was not found in active user or agent todos"
            )
        locked_completion_policy_source = (
            completion_validation_module.completion_policy_source_from_state(
                registry_path=registry_path,
                goal_id=goal_id,
                lines=lines,
                successor_todo_ids=normalized_successor_todo_ids,
                facts=completion_policy_facts,
            )
        )
        locked_completion = locked_todo_completion_transaction(
            validation_gate=validation_gate,
            todo=completion_todo,
            goal_id=goal_id,
            todo_id=todo_id,
            dry_run=dry_run,
            require_source_match=bool(completion_match),
            missing_is_drift=True,
            current_completion_policy_source=locked_completion_policy_source,
        )
        locked_failure: dict[str, Any] | None = locked_completion["failure"]
        if locked_failure is not None:
            return locked_failure
        completion_transaction = locked_completion["transaction"]
        completion_turn_key = completion_transaction.get(
            "completion_identity_key"
        )
        completion_identity_source = completion_transaction.get(
            "completion_identity_source"
        )
        decision_target = completion_decision_target(lines, completion_todo)
        mutation_authority = authorize_todo_lifecycle_mutation(
            registry_path=registry_path,
            goal_id=goal_id,
            command="complete",
            todo=completion_todo,
            actor_agent_id=agent_id, authority_reason=authority_reason,
            requested_claimed_by=claimed_by,
            decision_outcome=effective_decision_outcome,
            decision_target=decision_target,
        )
        completion_handoff = resolve_todo_completion_handoff(state_text=original, mutation_authority=mutation_authority)
        terminal_replay = materialized_todo_completion_replay(
            transaction=completion_transaction,
            todo=completion_todo,
            dry_run=dry_run,
            goal_id=goal_id,
            todo_id=todo_id,
            handoff=completion_handoff,
            mutation_authority=mutation_authority,
            state_file=str(resolved_state_file),
            project=str(resolved_project) if resolved_project else None,
        ) if completion_match else None
        if terminal_replay is not None:
            return terminal_replay
        task_lease_fence = lease_fence_stack.enter_context(
            hold_task_lease_mutation_fence(
                registry_path=registry_path,
                goal_id=goal_id,
                todo_id=todo_id,
                todo=completion_todo,
                actor_agent_id=agent_id or claimed_by,
                idempotency_key=(task_lease_idempotency_key or completion_turn_key)
                if completion_identity_source != "unscoped_completion"
                else task_lease_idempotency_key,
                expected_version=task_lease_expected_version,
                require_active_when_key_supplied=(
                    task_lease_idempotency_key is not None
                    or task_lease_expected_version is not None
                ),
                handoff=completion_handoff,
                runtime_root=shadow_runtime_root,
            )
        )
        completion_state = completion_transaction.get("completion_state")
        completion_policy = completion_policy_from_transaction(completion_transaction)
        effective_claimed_by = completion_policy.effective_claimed_by
        registered_agents = completion_policy.registered_agents
        effective_next_claimed_by = completion_policy.effective_next_claimed_by
        effective_next_excluded_agents = (
            completion_policy.effective_next_excluded_agents
        )
        effective_self_merged = completion_policy.self_merged
        if not isinstance(completion_state, dict):
            raise RuntimeError(
                "TypeScript Todo completion transaction did not authorize a commit"
            )
        update_result = apply_todo_update_to_lines(
            lines,
            todo_id=todo_id,
            status=TODO_STATUS_DONE,
            role=role,
            decision_outcome=effective_decision_outcome,
            note=note,
            evidence=evidence,
            completion_turn_key=completion_turn_key,
            completion_continuation=str(completion_state["continuation"]),
            completion_recovery=(
                str(completion_state["recovery"])
                if completion_state.get("recovery") is not None
                else None
            ),
            completion_metadata_updates_override=dict(
                completion_transaction["metadata_updates"]
            ),
            claimed_by=effective_claimed_by,
            clear_claim=clear_claim,
            no_followup=True if no_followup else None,
            successor_todo_ids=normalized_successor_todo_ids if successor_todo_ids is not None else None,
            updated_at=updated_at,
        )
        unblock_resume, decision_scope_resolution = (
            apply_completed_user_todo_lifecycle(
                lines,
                completion_todo=completion_todo,
                update_result=update_result,
                fallback_todo_id=todo_id,
                decision_outcome=effective_decision_outcome,
                updated_at=updated_at,
                apply_update=apply_todo_update_to_lines,
            )
        )
        successor_intents = build_successor_intents(
            next_agent_todo=next_agent_todo,
            next_user_todo=next_user_todo,
            next_user_task_class=effective_next_user_task_class,
            next_claimed_by=next_claimed_by,
            next_task_class=next_task_class,
            next_action_kind=next_action_kind,
            next_task_repository=next_task_repository,
            next_required_capabilities=next_required_capabilities,
            next_continuation_policy=next_continuation_policy,
            next_excluded_agents=next_excluded_agents,
        )
        successor_proposals = derive_successor_proposals(
            command="complete",
            predecessor=completion_todo,
            registered_agents=registered_agents,
            actor_agent_id=mutation_authority.get("actor_agent_id"),
            completion_policy={
                "effective_claimed_by": effective_claimed_by,
                "effective_next_claimed_by": effective_next_claimed_by,
                "effective_next_excluded_agents": effective_next_excluded_agents,
            },
            successor_intents=successor_intents,
        )
        next_results = [
            add_todo_to_lines(
                lines,
                **successor_add_kwargs(proposal),
                updated_at=updated_at,
            )
            for proposal in successor_proposals
        ]
        generated_successor_todo_ids = [
            todo_id
            for todo_id in normalize_todo_id_list([item.get("todo_id") for item in next_results])
        ]
        successor_metadata_updated = link_generated_successor_todo_ids(
            lines,
            update_result=update_result,
            role=role,
            successor_todo_ids=generated_successor_todo_ids,
        )
        next_action_changed = update_result.get("role") == "agent" and (
            settle_completed_todo_next_action(
                lines,
                completed_todo_id=str(update_result.get("todo_id") or todo_id),
            )
        )
        next_changed = any(item.get("added") or item.get("metadata_updated") for item in next_results)
        changed = bool(
            update_result["changed"]
            or next_changed
            or successor_metadata_updated
            or next_action_changed
            or (unblock_resume or {}).get("changed")
            or (decision_scope_resolution or {}).get("changed")
        )
        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if changed:
            new_text = replace_updated_at(new_text, updated_at)
        if changed and not dry_run:
            write_captured_todo_state(shadow_capture, runtime_root=shadow_runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=new_text)
        release_verified_task_lease_fence(
            task_lease_fence,
            committed=changed and not dry_run,
        )
    result = {
        "ok": True,
        "dry_run": dry_run,
        "completed": True,
        "goal_id": goal_id,
        **update_result,
        "changed": changed,
        "next_todos": next_results,
        "linked_successor_id": completion_policy.linked_successor_id,
        "mutation_authority": mutation_authority,
        "task_lease_fence": task_lease_fence,
        **completion_handoff,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
        "updated_at": updated_at if changed else None,
    }
    if unblock_resume:
        result["unblock_resume"] = unblock_resume
    if decision_scope_resolution:
        result["decision_scope_resolution"] = decision_scope_resolution
    if effective_decision_outcome:
        result["decision_outcome"] = effective_decision_outcome
    result["self_merged"] = effective_self_merged
    return settle_todo_runtime_shadow_capture(
        result, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, capture=shadow_capture,
    )


def _supersede_goal_todo_legacy(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    todo_id: str,
    role: str | None = None,
    reason: str | None = None,
    successor_todo_ids: list[str] | None = None,
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
    agent_id: str | None = None, authority_reason: str | None = None,
    task_lease_idempotency_key: str | None = None, task_lease_expected_version: int | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    from .line_update import apply_todo_update_to_lines, link_superseding_todo_id
    from ..coordination.runtime_shadow_writer_adapter import begin_todo_runtime_shadow_capture, write_captured_todo_state, settle_todo_runtime_shadow_capture

    if successor_todo_ids:
        raise ValueError("Existing-successor supersede requires promoted canonical Todo authority; migrate the Goal before retrying")
    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if next_task_repository and not next_agent_todo:
        raise ValueError("--next-task-repository requires --next-agent-todo")
    if next_required_capabilities and not next_agent_todo:
        raise ValueError("--next-required-capability requires --next-agent-todo")
    effective_next_user_task_class = resolve_next_user_task_class(
        next_user_todo,
        next_user_task_class,
    )
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, agent_id, "todo_supersede", dry_run,
        runtime_root=shadow_runtime_root,
    ), ExitStack() as lease_fence_stack:
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class="todo_supersede", original_text=original,
        )
        lines = original.splitlines()
        updated_at = now_local()
        current_match = find_todo_block(lines, todo_id=todo_id, role=role)
        if not current_match:
            normalized_todo_id = normalize_todo_id(todo_id) or todo_id
            raise ValueError(
                f"todo_id {normalized_todo_id!r} was not found in active user or agent todos"
            )
        current_role, _section, _start, _end, current_block = current_match
        authority_todo = dict(current_block)
        authority_todo["role"] = current_role
        mutation_authority = authorize_todo_lifecycle_mutation(
            registry_path=registry_path,
            goal_id=goal_id,
            command="supersede",
            todo=authority_todo,
            actor_agent_id=agent_id, authority_reason=authority_reason,
        )
        completion_handoff, task_lease_fence = enter_terminal_todo_lease_fence(
            lease_fence_stack, registry_path=registry_path, goal_id=goal_id, todo_id=todo_id,
            todo=authority_todo, actor_agent_id=agent_id, state_text=original, mutation_authority=mutation_authority,
            idempotency_key=task_lease_idempotency_key, expected_version=task_lease_expected_version,
            runtime_root=shadow_runtime_root,
        )
        update_result = apply_todo_update_to_lines(
            lines,
            todo_id=todo_id,
            status=TODO_STATUS_DONE,
            role=role,
            reason=reason,
            note="superseded",
            updated_at=updated_at,
        )
        registered_agents = registered_agent_ids_from_registry(registry_path, goal_id)
        successor_intents = build_successor_intents(
            next_agent_todo=next_agent_todo,
            next_user_todo=next_user_todo,
            next_user_task_class=effective_next_user_task_class,
            next_claimed_by=next_claimed_by,
            next_task_class=next_task_class,
            next_action_kind=next_action_kind,
            next_task_repository=next_task_repository,
            next_required_capabilities=next_required_capabilities,
            next_continuation_policy=next_continuation_policy,
            next_excluded_agents=next_excluded_agents,
        )
        successor_proposals = derive_successor_proposals(
            command="supersede",
            predecessor=authority_todo,
            registered_agents=registered_agents,
            actor_agent_id=mutation_authority.get("actor_agent_id"),
            completion_policy=None,
            successor_intents=successor_intents,
        )
        next_results = [
            add_todo_to_lines(
                lines,
                **successor_add_kwargs(proposal),
                updated_at=updated_at,
            )
            for proposal in successor_proposals
        ]
        generated_successor_todo_ids = [
            todo_id
            for todo_id in normalize_todo_id_list([item.get("todo_id") for item in next_results])
        ]
        link_superseding_todo_id(
            lines,
            update_result=update_result,
            role=role,
            successor_todo_ids=generated_successor_todo_ids,
        )
        next_action_changed = current_role == "agent" and (
            settle_completed_todo_next_action(
                lines,
                completed_todo_id=str(update_result.get("todo_id") or todo_id),
            )
        )
        next_changed = any(item.get("added") or item.get("metadata_updated") for item in next_results)
        changed = bool(
            update_result["changed"]
            or next_changed
            or next_action_changed
        )
        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if changed:
            new_text = replace_updated_at(new_text, updated_at)
        if changed and not dry_run:
            write_captured_todo_state(shadow_capture, runtime_root=shadow_runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=new_text)
        release_verified_task_lease_fence(task_lease_fence, committed=changed and not dry_run)
    result = {
        "ok": True,
        "dry_run": dry_run,
        "superseded": True,
        "goal_id": goal_id,
        **update_result,
        "changed": changed,
        "mutation_authority": mutation_authority,
        "task_lease_fence": task_lease_fence, **completion_handoff,
        "next_todos": next_results,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
        "updated_at": updated_at if changed else None,
    }
    return settle_todo_runtime_shadow_capture(
        result, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, capture=shadow_capture,
    )


def _archive_completed_todos_legacy(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    role: str = "agent",
    max_active_done: int = ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE,
    project: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    from ..coordination.runtime_shadow_writer_adapter import begin_todo_runtime_shadow_capture, write_captured_todo_state, settle_todo_runtime_shadow_capture

    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if role not in TODO_SECTION_HEADINGS:
        raise ValueError("todo role must be one of: user, agent")
    if max_active_done < 0:
        raise ValueError("max_active_done must be non-negative")
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )

    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, None, "todo_archive_completed", dry_run,
        runtime_root=shadow_runtime_root,
    ):
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class="todo_archive_completed", original_text=original,
        )
        lines = original.splitlines()
        archive_result = archive_completed_todo_lines(
            lines,
            role=role,
            max_active_done=max_active_done,
        )
        lines = archive_result.pop("lines")

        updated_at = now_local()
        changed = bool(archive_result["changed"])
        new_text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
        if changed:
            new_text = replace_updated_at(new_text, updated_at)
        if changed and not dry_run:
            write_captured_todo_state(shadow_capture, runtime_root=shadow_runtime_root, goal_id=goal_id,
                state_path=resolved_state_file, text=new_text)

    result = {
        "ok": True,
        "dry_run": dry_run,
        "goal_id": goal_id,
        **archive_result,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
        "updated_at": updated_at if changed else None,
    }
    return settle_todo_runtime_shadow_capture(
        result, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, capture=shadow_capture,
    )
