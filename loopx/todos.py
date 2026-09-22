from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from json import dumps as json_dumps
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_from_registry, require_registered_agent_id
from .history import load_registry
from .paths import resolve_runtime_root
from .rollout_event_log import load_rollout_events, rollout_event_log_path
from .state_refresh import now_local, resolve_goal_state
from .status import MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE
from .control_plane.todos.contract import (
    TODO_STATUS_DEFERRED,
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_USER_GATE,
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
from .control_plane.todos.active_state_editing import (
    TODO_SECTION_HEADINGS,
    find_todo_block,
    insert_into_existing_section,
    insert_new_section,
    replace_updated_at,
    section_bounds,
    set_todo_marker,
    todo_blocks,
)
from .control_plane.todos.addition import matching_todo_block, require_replan_successor_rebinding, require_replan_successor_scope
from .control_plane.todos.completed_archive import archive_completed_todo_lines
from .control_plane.todos.completion_policy import (
    completion_policy_from_transaction,
)
from .control_plane.todos.completion_transaction import (
    locked_todo_completion_transaction,
    materialized_todo_completion_replay,
    require_completion_successor_todo_ids,
    user_todo_completion_metadata_updates,
)
from .control_plane.todos import completion_validation as completion_validation_module
from .control_plane.todos.event_writeback import (
    complete_event_projected_goal_todo,
)
from .control_plane.todos.line_update import (
    apply_todo_update_to_lines,
    link_generated_successor_todo_ids,
    link_superseding_todo_id,
    upsert_todo_metadata,
)
from .control_plane.todos.next_action_runtime import apply_added_todo_next_action, settle_completed_todo_next_action
from .control_plane.todos.list_projection import (
    AGENT_LANE_OVERLAY_FULL_DETAIL_COLD_PATH,
    EXPLICIT_LIMIT_OVERLAY_FULL_DETAIL_COLD_PATH,
    compact_agent_lane_todo_summary,
    compact_thin_todo_list_payload,
    compact_todo_projection_overlay,
    todo_item_relations,
    todo_list_projection_contract,
)
from .control_plane.todos.goal_todo_projection import (
    exact_archived_todo_summaries,
    goal_todo_summaries,
    todo_summaries_from_fields,
)
from .control_plane.todos.active_state_todo_parser import parse_todo_source
from .control_plane.todos import monitor_metadata as todo_monitor_metadata
from .control_plane.todos.mutation_authority import authorize_todo_lifecycle_mutation, todo_update_authority_action
from .control_plane.todos.succession_warning import build_open_parent_successor_advisory
from .control_plane.todos.successor_derivation import (
    build_successor_intents,
    derive_successor_proposals,
    successor_add_kwargs,
)
from .control_plane.todos.todo_index import MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL
from .control_plane.todos.text import normalize_new_todo, plan_todo_priority
from .control_plane.todos.todo_semantics import todo_priority_label
from .control_plane.todos.unblock_resume import (
    apply_completed_user_todo_lifecycle,
    completion_decision_target,
    require_completion_decision_outcome,
)
from .control_plane.todos.write_correctness import (
    attach_todo_write_correctness_dry_run_packet as _attach_todo_write_correctness_dry_run_packet,
)
from .control_plane.todos.authoring_scope import (
    plan_todo_authoring_scope,
    require_user_todo_task_class,
)
from .control_plane.coordination.legacy_writer_fence import legacy_todo_write_transaction
from .control_plane.coordination.local_authority import (
    canonical_todo_items,
    canonical_todo_summary_fields,
    claim_canonical_todo_if_promoted,
    local_authority_is_promoted,
    read_canonical_todos_if_promoted,
)
from .control_plane.todos.provider_update import update_canonical_todo_if_promoted
from .control_plane.todos.update_intent import (
    build_canonical_update_intent,
    canonical_update_is_supported,
)
from .control_plane.todos.provider_create import create_canonical_todo_if_promoted
from .control_plane.todos.path_resolution import resolve_todo_state_path
from .control_plane.todos.provider_terminal_lifecycle import provider_first_terminal_lifecycle
from .control_plane.todos.handoff_mode import (
    goal_handoff_mode,
    enter_added_todo_ownership_handoff_gate,
    enter_todo_ownership_handoff_gate,
    resolve_todo_completion_handoff,
)
from .control_plane.coordination.local_authority_shadow_adapter import effective_runtime_root
from .control_plane.coordination.runtime_shadow_writer_adapter import (
    write_captured_todo_state,
    begin_todo_runtime_shadow_capture,
    settle_todo_runtime_shadow_capture,
)
from .control_plane.work_items.task_lease import (
    enter_terminal_todo_lease_fence,
    hold_task_lease_mutation_fence,
    release_verified_task_lease_fence,
)


ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE = max(0, MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE - 2)


def require_registered_todo_excluded_agents(
    *,
    registry_path: Path,
    goal_id: str,
    excluded_agents: Any,
    field: str = "excluded_agents",
) -> list[str]:
    return sorted(
        require_registered_agent_id(
            registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            field=field,
        )
        for agent_id in require_todo_excluded_agents(excluded_agents, field=field)
    )


def list_goal_todos(
    *,
    registry_path: Path,
    goal_id: str,
    role: str | None = None,
    status: str | None = None,
    todo_id: str | None = None,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    runtime_root_arg: str | None = None,
    limit: int | None = None,
    thin: bool = False,
) -> dict[str, Any]:
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    if todo_id and not normalized_todo_id:
        raise ValueError("todo_id must use the public token shape todo_<letters-digits-underscore-hyphen>")
    normalized_agent_id = normalize_todo_claimed_by(agent_id) if agent_id else None
    if agent_id and not normalized_agent_id:
        raise ValueError("agent_id must be a public-safe agent token such as codex-main-control")
    if limit is not None and limit < 1:
        raise ValueError("todo list --limit must be at least 1")
    registry = load_registry(registry_path)
    goal, resolved_project, resolved_state_file = resolve_goal_state(
        registry=registry,
        goal_id=goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    if goal is None:
        raise ValueError(f"goal {goal_id!r} is not present in the registry")

    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    rollout_events = load_rollout_events(
        rollout_event_log_path(runtime_root, goal_id),
        limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
    )

    roles = [role] if role else ["user", "agent"]
    canonical_read = read_canonical_todos_if_promoted(
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    if canonical_read is not None:
        projected = todo_summaries_from_fields(
            fields=canonical_todo_summary_fields(
                canonical_read["todos"],
                rollout_events=rollout_events,
                goal_acceptance_contract=canonical_read.get("goal_acceptance_contract"),
                goal_acceptance_work_guards=canonical_read.get("goal_acceptance_work_guards"),
            ),
            source="file_authority",
            projection_fields={},
            projection_overlay=None,
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
        )
    else:
        if not resolved_state_file.exists():
            raise ValueError(f"active state file does not exist: {resolved_state_file}")
        state_text = resolved_state_file.read_text(encoding="utf-8")
        projected = goal_todo_summaries(
            goal,
            state_text=state_text,
            state_path=resolved_state_file,
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
        )
    if normalized_todo_id and not projected.todos:
        if canonical_read is not None:
            archived_items = [
                item
                for item in canonical_todo_items(canonical_read["todos"])
                if item.get("archive_state") == "archive"
            ]
        else:
            _active_items, archived_items, _source_sections = parse_todo_source(
                state_text,
                goal=goal,
                state_path=resolved_state_file,
            )
        archived_projection = exact_archived_todo_summaries(
            archived_items=archived_items,
            source=projected.source,
            projection_fields=projected.projection_fields,
            projection_overlay=projected.projection_overlay,
            rollout_events=rollout_events,
            roles=roles,
            status=status,
            todo_id=normalized_todo_id,
            agent_id=normalized_agent_id,
            limit=limit,
        )
        if archived_projection is not None:
            projected = archived_projection
    source = projected.source
    projection_fields = projected.projection_fields
    projection_overlay = projected.projection_overlay
    summaries = projected.summaries
    todos = projected.todos
    unfiltered_count = projected.unfiltered_count
    uncapped_todo_count = projected.uncapped_todo_count

    matched_todo_count = len(todos)
    agent_lane_hot_path = bool(
        normalized_agent_id and limit is None
        and role is None
        and status is None
        and normalized_todo_id is None
    )
    if agent_lane_hot_path:
        summaries = {
            key: compact_agent_lane_todo_summary(
                summary,
                role=key.removesuffix("_todos"),
            )
            for key, summary in summaries.items()
        }
        todos = [
            item
            for key in ("user_todos", "agent_todos")
            for item in summaries.get(key, {}).get("items") or []
            if isinstance(item, dict)
        ]

    matched_todo = todos[0] if len(todos) == 1 else None
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "read_only": True,
        "command": "list",
        "goal_id": goal_id,
        "role": role or "all",
        "status_filter": normalize_todo_status(status) if status else None,
        "source": source,
        "todo_count": matched_todo_count,
        "todos": todos,
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
    }
    if canonical_read is not None:
        payload["authority_read"] = {
            "source_authority": canonical_read["source_authority"],
            "provider_revision": canonical_read.get("provider_revision"),
            "cursor": canonical_read.get("cursor"),
            "todo_read_model": canonical_read.get("todo_read_model"),
            "decision_read_from_provider": True,
            "legacy_fallback_used": False,
        }
    if normalized_agent_id:
        payload["agent_id_filter"] = normalized_agent_id
        payload["unfiltered_todo_count"] = unfiltered_count
        payload["filter_semantics"] = (
            "agent todos include unclaimed items plus claimed_by=<agent>; "
            "User gates use global_gate, then blocks_agent, then legacy claimed_by scope; "
            "User actions use bound_agent, then legacy claimed_by scope; unscoped items remain visible"
        )
    if agent_lane_hot_path:
        payload["returned_todo_count"] = len(todos)
        payload["todo_list_projection"] = todo_list_projection_contract(
            matched_todo_count=matched_todo_count,
            returned_todo_count=len(todos),
        )
    if limit is not None:
        payload["explicit_limit"] = limit
        payload["unfiltered_todo_count"] = unfiltered_count
        payload["returned_todo_count"] = len(todos)
        payload["todo_list_projection"] = todo_list_projection_contract(
            matched_todo_count=uncapped_todo_count,
            returned_todo_count=len(todos),
            view="explicit_limit_cold_path",
            item_limit_per_role=limit,
            full_detail_cold_paths=(
                "todo list without --limit",
                "active state",
            ),
        )
    if normalized_todo_id:
        payload["todo_id_filter"] = normalized_todo_id
        payload["matched"] = bool(todos)
        payload["todo"] = matched_todo
        payload["relations"] = todo_item_relations(matched_todo) if matched_todo else {}
        if len(todos) > 1:
            payload["ambiguous"] = True
        if not todos:
            payload["not_found"] = True
    payload.update(summaries)
    if source == "event_projection" and projection_fields.get("state_event_projection"):
        payload["state_event_projection"] = projection_fields["state_event_projection"]
    if source == "event_projection_with_markdown_overlay":
        if projection_fields.get("state_event_projection"):
            payload["state_event_projection"] = projection_fields["state_event_projection"]
        payload["projection_overlay"] = (
            compact_todo_projection_overlay(
                projection_overlay,
                full_detail_cold_path=(
                    EXPLICIT_LIMIT_OVERLAY_FULL_DETAIL_COLD_PATH
                    if limit is not None
                    else AGENT_LANE_OVERLAY_FULL_DETAIL_COLD_PATH
                ),
            )
            if agent_lane_hot_path or limit is not None
            else projection_overlay
        )
    if projection_fields.get("state_event_projection_warning"):
        payload["state_event_projection_warning"] = projection_fields["state_event_projection_warning"]
    return compact_thin_todo_list_payload(payload) if thin else payload


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
) -> dict[str, Any]:
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
) -> dict[str, Any]:
    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
    if role not in TODO_SECTION_HEADINGS:
        raise ValueError("todo role must be one of: user, agent")
    require_user_todo_task_class(
        role=role,
        task_class=task_class,
        blocks_agent=blocks_agent,
        global_gate=True if global_gate else None,
    )
    if global_gate and not (role == "user" and task_class == TODO_TASK_CLASS_USER_GATE):
        raise ValueError("global_gate is only valid for `--role user --task-class user_gate`")
    if role == "agent" and blocks_agent:
        raise ValueError(
            "blocks_agent is only valid for user gates; use --excluded-agent for "
            "agent executor constraints"
        )
    if role == "user" and claimed_by:
        raise ValueError(
            "claimed_by is execution ownership for agent todos, not a user-todo "
            "binding; use --bound-agent or --goal-bound"
        )
    if task_repository and role != "agent":
        raise ValueError("task_repository is only valid for agent todos")
    if task_domain and role != "agent":
        raise ValueError("task_domain is only valid for agent todos")
    if capability_binding_ref and role != "agent":
        raise ValueError("capability_binding_ref is only valid for agent todos")
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
    if status and not normalized_status:
        raise ValueError("todo status must be one of: open, done, blocked, deferred")
    if normalized_status == TODO_STATUS_DONE:
        raise ValueError("todo add cannot create completed work; add it open and use `loopx todo complete`")
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
    effective_claimed_by = (
        require_registered_agent_id(
            registry_path=registry_path, goal_id=goal_id, agent_id=claimed_by,
        ) if claimed_by else None
    )
    effective_agent_id = (
        require_registered_agent_id(
            registry_path=registry_path, goal_id=goal_id, agent_id=agent_id,
            field="agent_id",
        ) if agent_id else None
    )
    registered_agents = registered_agent_ids_from_registry(registry_path, goal_id)
    effective_excluded_agents = (
        require_registered_todo_excluded_agents(
            registry_path=registry_path, goal_id=goal_id,
            excluded_agents=excluded_agents,
        )
        if excluded_agents is not None
        else None
    )
    authoring_scope = plan_todo_authoring_scope(
        command="create", role=role, goal_id=goal_id, registered_agents=registered_agents,
        intent={
            "task_class": task_class, "status": status, "actor_agent_id": effective_agent_id,
            "claimed_by": effective_claimed_by, "bound_agent": bound_agent, "goal_bound": goal_bound,
            "blocks_agent": blocks_agent, "global_gate": global_gate,
            "excluded_agents": effective_excluded_agents, "resume_when": resume_when,
            "task_repository": task_repository, "task_domain": task_domain,
            "capability_binding_ref": capability_binding_ref,
        },
    )
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

    payload = {
        "ok": True,
        "dry_run": dry_run,
        "added": added,
        "already_exists": bool(add_result["already_exists"]),
        "metadata_updated": metadata_updated,
        "status_changed": bool(add_result.get("status_changed")),
        "goal_id": goal_id,
        "role": role,
        "section": add_result.get("section"),
        "todo": todo_text,
        "todo_id": add_result.get("todo_id"),
        "status": add_result.get("status"),
        "task_class": add_result.get("task_class"),
        "action_kind": add_result.get("action_kind"),
        "capability_binding_ref": add_result.get("capability_binding_ref"),
        "task_repository": add_result.get("task_repository"),
        "continuation_policy": add_result.get("continuation_policy"),
        "required_write_scopes": add_result.get("required_write_scopes"),
        "required_capabilities": add_result.get("required_capabilities"),
        "target_capabilities": add_result.get("target_capabilities"),
        "explore_result_node_refs": add_result.get("explore_result_node_refs"),
        "decision_scope": add_result.get("decision_scope"),
        "required_decision_scopes": add_result.get("required_decision_scopes"),
        "claimed_by": add_result.get("claimed_by"),
        "bound_agent": add_result.get("bound_agent"),
        "goal_bound": add_result.get("goal_bound"),
        "agent_id": effective_agent_id,
        "blocks_agent": add_result.get("blocks_agent"),
        "excluded_agents": add_result.get("excluded_agents"),
        "global_gate": add_result.get("global_gate"),
        "unblocks_todo_id": add_result.get("unblocks_todo_id"),
        "replan_obligation_id": add_result.get("replan_obligation_id"),
        "resume_when": add_result.get("resume_when"),
        "target_key": add_result.get("target_key"),
        "cadence": add_result.get("cadence"),
        "next_due_at": add_result.get("next_due_at"),
        "expires_at": add_result.get("expires_at"),
        "watch_only": add_result.get("watch_only"),
        "note": add_result.get("note"),
        "state_file": str(resolved_state_file),
        "project": str(resolved_project) if resolved_project else None,
        "updated_at": updated_at if changed else None,
        **handoff_gate,
    }
    payload = _attach_todo_write_correctness_dry_run_packet(
        payload,
        goal_id=goal_id,
        write_class="todo_add",
        state_text=original,
    )
    return settle_todo_runtime_shadow_capture(
        payload, registry_path=registry_path, runtime_root=shadow_runtime_root,
        goal_id=goal_id, write_class="todo_add", capture=shadow_capture,
    )


def resolve_todo_state(
    *,
    registry_path: Path,
    goal_id: str,
    project: Path | None = None,
    state_file: Path | None = None,
) -> tuple[Path | None, Path, str, list[str]]:
    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    original = resolved_state_file.read_text(encoding="utf-8")
    return resolved_project, resolved_state_file, original, original.splitlines()


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
    if priority is not None and clear_priority:
        raise ValueError("provide either priority or clear_priority, not both")
    shadow_runtime_root = effective_runtime_root(registry_path, runtime_root_arg)
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
            completion_validation_module.completion_validation_declaration(
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
            explore_result_node_refs, decision_scope, required_decision_scopes,
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
    if not claim_only and (validation_revision_declaration is not None or canonical_update_is_supported(
        text=text, note=note, intent=planning_intent,
        monitor_metadata=monitor_metadata,
    )):
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
            monitor_observation=monitor_metadata if monitor_intent["observation"] is not None else None,
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
        validation_failure = completion_validation_gate.get("failure")
        if validation_failure is not None:
            return validation_failure
    with legacy_todo_write_transaction(
        registry_path, goal_id, resolved_state_file, agent_id or claimed_by, "todo_update", dry_run,
        runtime_root=shadow_runtime_root,
    ), ExitStack() as handoff_gate_stack:
        original = resolved_state_file.read_text(encoding="utf-8")
        shadow_capture = begin_todo_runtime_shadow_capture(
            registry_path=registry_path, runtime_root=shadow_runtime_root,
            goal_id=goal_id, state_path=resolved_state_file,
            write_class="todo_update", original_text=original,
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
                explore_result_node_refs, decision_scope,
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
            [] if clear_excluded_agents else require_registered_todo_excluded_agents(
                registry_path=registry_path, goal_id=goal_id, excluded_agents=excluded_agents,
            ) if excluded_agents is not None else None
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
            if locked_completion["failure"] is not None:
                return locked_completion["failure"]
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
    write_class = "todo_claim" if claim_only else "todo_update"
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
        goal_id=goal_id, write_class=write_class, capture=shadow_capture,
    )


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
    validation_failure = validation_gate.get("failure")
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
        completion_match, completion_todo, event_context = (
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
                event_fields=event_context.get("fields") if event_context else None,
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
        if locked_completion["failure"] is not None:
            return locked_completion["failure"]
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
        completion_fence = completion_transaction["fence"]
        completion_state = completion_transaction.get("completion_state")
        completion_policy = completion_policy_from_transaction(completion_transaction)
        effective_claimed_by = completion_policy.effective_claimed_by
        registered_agents = completion_policy.registered_agents
        effective_next_claimed_by = completion_policy.effective_next_claimed_by
        effective_next_excluded_agents = (
            completion_policy.effective_next_excluded_agents
        )
        effective_self_merged = completion_policy.self_merged
        if not completion_match:
            if event_context:
                event_result = complete_event_projected_goal_todo(
                    goal_id=goal_id,
                    context=event_context,
                    runtime_root=shadow_runtime_root,
                    primary_lock_held=True,
                    evidence=evidence,
                    completion_turn_key=completion_turn_key,
                    completion_identity_source=completion_identity_source,
                    note=note,
                    no_followup=no_followup,
                    successor_todo_ids=normalized_successor_todo_ids,
                    claimed_by=effective_claimed_by,
                    clear_claim=clear_claim,
                    next_agent_todo=next_agent_todo,
                    next_user_todo=next_user_todo,
                    next_user_task_class=effective_next_user_task_class,
                    next_claimed_by=effective_next_claimed_by,
                    next_task_class=next_task_class,
                    next_action_kind=next_action_kind,
                    next_task_repository=next_task_repository,
                    next_required_capabilities=next_required_capabilities,
                    next_continuation_policy=next_continuation_policy,
                    self_merged=effective_self_merged,
                    next_excluded_agents=effective_next_excluded_agents,
                    registered_agents=registered_agents,
                    updated_at=updated_at,
                    dry_run=dry_run,
                    actor_agent_id=mutation_authority.get("actor_agent_id"),
                    completion_fence=completion_fence,
                    completion_state=completion_state,
                    completion_validation_source_authority=validation_gate.get("source_authority"),
                )
                event_result["linked_successor_id"] = completion_policy.linked_successor_id
                event_result["mutation_authority"] = mutation_authority
                event_result["task_lease_fence"] = task_lease_fence
                event_result.update(completion_handoff)
                release_verified_task_lease_fence(
                    task_lease_fence,
                    committed=bool(event_result.get("changed")) and not dry_run,
                )
                # This branch can append multiple state-log events inside the
                # event writer. Capturing after that call would be observation,
                # not a transaction-bound prepare/commit pair. Keep the gap
                # explicit until the event writer owns the outbox boundary.
                shadow_capture.skip("event_log_writer_not_bound")
                return settle_todo_runtime_shadow_capture(
                    event_result, registry_path=registry_path,
                    runtime_root=shadow_runtime_root, goal_id=goal_id,
                    write_class="todo_complete_event_projection", capture=shadow_capture,
                )
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
        goal_id=goal_id, write_class="todo_complete", capture=shadow_capture,
    )

@provider_first_terminal_lifecycle("supersede")
def supersede_goal_todo(
    *,
    registry_path: Path,
    goal_id: str,
    runtime_root_arg: str | None = None,
    todo_id: str,
    role: str | None = None,
    reason: str | None = None,
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
        goal_id=goal_id, write_class="todo_supersede", capture=shadow_capture,
    )


@provider_first_terminal_lifecycle("archive")
def archive_completed_todos(
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
        goal_id=goal_id, write_class="todo_archive_completed", capture=shadow_capture,
    )
