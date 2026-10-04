from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .authoring_scope import todo_authoring_facts
from .external_wait_contract import TodoExternalWaitAuthoringError, build_monitor_advancement_authoring_contract
from .update_source import todo_update_snapshot
from .todo_semantics import todo_priority_label

from .active_state_editing import (
    TODO_SECTION_HEADINGS,
    find_todo_block,
    set_todo_marker,
    set_todo_text,
    todo_metadata_would_change,
)
from .contract import (
    merge_todo_id_lists,
    metadata_line_for_todo_block,
    normalize_explore_result_node_refs,
    normalize_required_capabilities,
    normalize_target_capabilities,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
    normalize_todo_decision_scope,
    normalize_todo_decision_scope_outcomes,
    normalize_todo_excluded_agents,
    normalize_todo_global_gate,
    normalize_todo_goal_bound,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_no_followup,
    normalize_todo_required_decision_scopes,
    normalize_todo_resume_when,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
    parse_todo_metadata_line,
    require_supported_todo_resume_when,
)
from .completion_state import (
    TodoCompletionContinuation,
    normalize_todo_completion_continuation,
    normalize_todo_completion_recovery,
)
from .contract import TODO_MONITOR_METADATA_FIELDS


def upsert_todo_metadata(
    lines: list[str], block: dict[str, Any], metadata_line: str | None
) -> bool:
    if not metadata_line:
        return False
    start = int(block["start"])
    end = int(block["end"])
    for index in range(start + 1, end):
        if parse_todo_metadata_line(lines[index]) is not None:
            if lines[index] == metadata_line:
                return False
            lines[index] = metadata_line
            return True
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, metadata_line)
    return True


def link_generated_successor_todo_ids(
    lines: list[str],
    *,
    update_result: dict[str, Any],
    role: str | None,
    successor_todo_ids: list[str],
) -> bool:
    merged_successor_ids = merge_todo_id_lists(
        update_result.get("successor_todo_ids"),
        successor_todo_ids,
    )
    if merged_successor_ids == normalize_todo_id_list(
        update_result.get("successor_todo_ids")
    ):
        return False
    block_match = find_todo_block(
        lines,
        todo_id=str(update_result.get("todo_id") or ""),
        role=role,
    )
    if not block_match:
        return False
    _resolved_role, _section, _start, _end, block = block_match
    metadata_updated = upsert_todo_metadata(
        lines,
        block,
        metadata_line_for_todo_block(
            block,
            {
                "successor_todo_ids": merged_successor_ids,
                "completion_continuation": (TodoCompletionContinuation.SUCCESSOR.value),
            },
        ),
    )
    update_result["successor_todo_ids"] = merged_successor_ids
    update_result["metadata_updated"] = bool(
        update_result.get("metadata_updated") or metadata_updated
    )
    update_result["changed"] = bool(update_result.get("changed") or metadata_updated)
    return metadata_updated


def link_superseding_todo_id(
    lines: list[str],
    *,
    update_result: dict[str, Any],
    role: str | None,
    successor_todo_ids: list[str],
) -> bool:
    """Persist supersede lineage after generated successors are materialized."""

    if not successor_todo_ids:
        return False
    block_match = find_todo_block(
        lines,
        todo_id=str(update_result.get("todo_id") or ""),
        role=role,
    )
    if not block_match:
        return False
    _resolved_role, _section, _start, _end, block = block_match
    merged_successor_ids = merge_todo_id_lists(
        update_result.get("successor_todo_ids"),
        successor_todo_ids,
    )
    metadata_updated = upsert_todo_metadata(
        lines,
        block,
        metadata_line_for_todo_block(
            block,
            {
                "superseded_by": successor_todo_ids[0],
                "successor_todo_ids": merged_successor_ids,
            },
        ),
    )
    update_result["metadata_updated"] = bool(
        update_result.get("metadata_updated") or metadata_updated
    )
    update_result["superseded_by"] = successor_todo_ids[0]
    update_result["successor_todo_ids"] = merged_successor_ids
    update_result["changed"] = True
    return metadata_updated


def _field_update_plan(
    block: Mapping[str, Any], intent: dict[str, Any], updated_at: str,
    monitor_context: dict[str, Any] | None = None,
    public_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt source facts only; the TS planner owns omission/clear/state rules."""
    try:
        result = effect_runtime_result(
            "todo.public_update.plan" if public_context is not None else "todo.field_update.plan",
            {
                "schema_version": "todo_public_update_request_v0" if public_context is not None else "loopx_todo_field_update_request_v0",
                "todo": {
                    **todo_authoring_facts(dict(block)),
                    "role": block.get("role"),
                    **{key: block.get(key)
                    for key in (
                        "text",
                        "todo_id",
                        "status",
                        "claimed_by",
                        "completed_at",
                        "removed_continuation_policy",
                        "no_followup",
                        "completion_continuation",
                        "successor_todo_ids",
                        "resume_monitor_generation",
                        "task_class",
                        *TODO_MONITOR_METADATA_FIELDS,
                    )},
                },
                "intent": intent,
                "updated_at": updated_at,
                "monitor_context": monitor_context,
                "context": public_context,
            },
        )
    except EffectRuntimeRejected as exc:
        if public_context is not None and exc.diagnostic_code.startswith("external_wait_"):
            condition = str(intent.get("resume_when") or block.get("resume_when") or "").strip().lower()
            kind, _, target = condition.partition(":")
            raise TodoExternalWaitAuthoringError(str(exc), code=exc.diagnostic_code,
                monitor_todo_id=target if kind == "monitor_changed" else None,
                successor_todo_ids=intent.get("successor_todo_ids")
                    if intent.get("successor_todo_ids") is not None else block.get("successor_todo_ids")) from None
        raise ValueError(str(exc)) from None
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "loopx_todo_field_update_result_v0"
        or result.get("target_status") not in {"open", "done", "blocked", "deferred"}
        or result.get("normalized_status")
        not in {None, "open", "done", "blocked", "deferred"}
        or not isinstance(result.get("metadata_updates"), dict)
    ):
        raise RuntimeError("TypeScript Todo field update result shape mismatch")
    transition = result.get("external_wait_transition")
    if isinstance(transition, dict) and transition.get("resume_kind") == "monitor_changed":
        transition["authoring_contract"] = build_monitor_advancement_authoring_contract(
            monitor_todo_id=transition["dependency_todo_id"],
            successor_todo_ids=transition["successor_todo_ids"])
    return result


def apply_todo_update_to_lines(
    lines: list[str],
    *,
    todo_id: str,
    text: str | None = None,
    priority: str | None = None,
    clear_priority: bool = False,
    status: str | None = None,
    role: str | None = None,
    note: str | None = None,
    evidence: str | None = None,
    completion_turn_key: str | None = None,
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
    decision_outcome: str | None = None,
    decision_scope_outcomes: Any = None,
    claimed_by: str | None = None,
    bound_agent: str | None = None,
    goal_bound: bool | None = None,
    clear_user_binding: bool = False,
    blocks_agent: str | None = None,
    clear_blocks_agent: bool = False,
    excluded_agents: list[str] | None = None,
    global_gate: bool | None = None,
    clear_global_gate: bool = False,
    unblocks_todo_id: str | None = None,
    successor_todo_ids: list[str] | None = None,
    completion_continuation: str | None = None,
    completion_recovery: str | None = None,
    completion_metadata_updates_override: Mapping[str, Any] | None = None,
    resume_when: str | None = None,
    resume_monitor_generation: int | None = None,
    clear_resume_when: bool = False,
    no_followup: bool | None = None,
    monitor_metadata: dict[str, Any] | None = None,
    monitor_context: dict[str, Any] | None = None,
    public_context: dict[str, Any] | None = None,
    clear_claim: bool = False,
    claim_only: bool = False,
    updated_at: str,
) -> dict[str, Any]:
    normalized_resume_when = resume_when if public_context is not None else require_supported_todo_resume_when(resume_when)
    if normalized_resume_when and clear_resume_when:
        raise ValueError(
            "todo update accepts either resume_when or clear_resume_when, not both"
        )
    normalized_todo_id = normalize_todo_id(todo_id)
    if not normalized_todo_id:
        raise ValueError(
            "todo_id must use the public token shape "
            "todo_<letters-digits-underscore-hyphen>"
        )
    if role is not None and role not in TODO_SECTION_HEADINGS:
        raise ValueError("todo role must be one of: user, agent")
    block_match = find_todo_block(lines, todo_id=normalized_todo_id, role=role)
    if not block_match:
        raise ValueError(
            f"todo_id {normalized_todo_id!r} was not found in active user or agent todos"
        )
    resolved_role, section, _start, _end, block = block_match
    if public_context is not None:
        public_context = {**public_context, "items": todo_update_snapshot(lines)
                          if resume_when or block.get("resume_when") else []}
    raw_intent = {
            "text": text,
            "priority": priority,
            "clear_priority": clear_priority,
            "status": status,
            "note": note,
            "evidence": evidence,
            "completion_turn_key": completion_turn_key,
            "reason": reason,
            "task_class": task_class,
            "action_kind": action_kind,
            "task_domain": task_domain,
            "task_repository": task_repository,
            "continuation_policy": continuation_policy,
            "required_write_scopes": required_write_scopes,
            "required_capabilities": required_capabilities,
            "target_capabilities": target_capabilities,
            "explore_result_node_refs": explore_result_node_refs,
            "decision_scope": decision_scope,
            "required_decision_scopes": required_decision_scopes,
            "decision_outcome": decision_outcome,
            "decision_scope_outcomes": decision_scope_outcomes,
            "claimed_by": claimed_by,
            "bound_agent": bound_agent,
            "goal_bound": goal_bound,
            "clear_user_binding": clear_user_binding,
            "blocks_agent": blocks_agent,
            "clear_blocks_agent": clear_blocks_agent,
            "excluded_agents": excluded_agents,
            "global_gate": global_gate,
            "clear_global_gate": clear_global_gate,
            "unblocks_todo_id": unblocks_todo_id,
            "successor_todo_ids": successor_todo_ids,
            "completion_continuation": completion_continuation,
            "completion_recovery": completion_recovery,
            "completion_metadata_updates_override": completion_metadata_updates_override,
            "resume_when": normalized_resume_when,
            "resume_monitor_generation": resume_monitor_generation,
            "clear_resume_when": clear_resume_when,
            "no_followup": no_followup,
            "monitor_metadata": monitor_metadata,
            "clear_claim": clear_claim,
            "claim_only": claim_only,
    }
    # Python's compatibility API uses None (and blank note text) for
    # omission. Strip those sentinels before crossing the typed planner; an
    # actual empty scalar such as reason="" remains an explicit clear.
    intent = {
        key: value for key, value in raw_intent.items()
        if value is not None and not (
            key == "note" and isinstance(value, str) and not value.strip()
        )
    }
    plan = _field_update_plan(
        {**block, "role": resolved_role},
        intent,
        updated_at,
        monitor_context,
        public_context,
    )
    normalized_status = plan["normalized_status"]
    target_status = plan["target_status"]
    updates = plan["metadata_updates"]
    text = updates.pop("text", text)
    updated_priority = updates.pop("priority", todo_priority_label(block))
    updates.pop("title", None)
    status_changed = (
        set_todo_marker(lines, block, normalized_status) if normalized_status else False
    )
    text_changed = (
        set_todo_text(lines, block, text, status=target_status)
        if text is not None
        else False
    )
    metadata_line = metadata_line_for_todo_block(block, updates)
    semantic_metadata_changed = todo_metadata_would_change(lines, block, metadata_line)
    if status_changed or text_changed or semantic_metadata_changed:
        updates["updated_at"] = updated_at
        metadata_line = metadata_line_for_todo_block(block, updates)
    metadata_updated = upsert_todo_metadata(lines, block, metadata_line)
    effective_metadata = parse_todo_metadata_line(metadata_line or "") or {}
    return {
        **({"monitor_poll_transition": plan["monitor_poll_transition"]}
           if "monitor_poll_transition" in plan else {}),
        **({"external_wait_transition": plan["external_wait_transition"]}
           if "external_wait_transition" in plan else {}),
        "role": resolved_role,
        "section": section,
        "todo": block.get("text"),
        "todo_id": normalized_todo_id,
        "status": target_status,
        "status_changed": status_changed,
        "text_changed": text_changed,
        "priority": updated_priority,
        "metadata_updated": metadata_updated,
        "changed": status_changed or text_changed or metadata_updated,
        "claimed_by": normalize_todo_claimed_by(effective_metadata.get("claimed_by")),
        "bound_agent": normalize_todo_bound_agent(
            effective_metadata.get("bound_agent")
        ),
        "goal_bound": normalize_todo_goal_bound(effective_metadata.get("goal_bound")),
        "task_class": effective_metadata.get("task_class"),
        "action_kind": effective_metadata.get("action_kind"),
        "task_domain": normalize_todo_task_domain(
            effective_metadata.get("task_domain")
        ),
        "capability_binding_ref": effective_metadata.get("capability_binding_ref"),
        "task_repository": normalize_todo_task_repository(
            effective_metadata.get("task_repository")
        ),
        "continuation_policy": normalize_todo_continuation_policy(
            effective_metadata.get("continuation_policy")
        ),
        "required_capabilities": normalize_required_capabilities(
            effective_metadata.get("required_capabilities")
        ),
        "target_capabilities": normalize_target_capabilities(
            effective_metadata.get("target_capabilities")
        ),
        "explore_result_node_refs": normalize_explore_result_node_refs(
            effective_metadata.get("explore_result_node_refs")
        ),
        "decision_scope": normalize_todo_decision_scope(
            effective_metadata.get("decision_scope")
        ),
        "required_decision_scopes": normalize_todo_required_decision_scopes(
            effective_metadata.get("required_decision_scopes")
        ),
        "decision_outcome": effective_metadata.get("decision_outcome"),
        "decision_scope_outcomes": normalize_todo_decision_scope_outcomes(
            effective_metadata.get("decision_scope_outcomes")
        ),
        "blocks_agent": normalize_todo_blocks_agent(
            effective_metadata.get("blocks_agent")
        ),
        "excluded_agents": normalize_todo_excluded_agents(
            effective_metadata.get("excluded_agents")
        ),
        "global_gate": normalize_todo_global_gate(
            effective_metadata.get("global_gate")
        ),
        "unblocks_todo_id": normalize_todo_id(
            effective_metadata.get("unblocks_todo_id")
        ),
        "successor_todo_ids": normalize_todo_id_list(
            effective_metadata.get("successor_todo_ids")
        ),
        "completion_continuation": normalize_todo_completion_continuation(
            effective_metadata.get("completion_continuation")
        ),
        "completion_recovery": normalize_todo_completion_recovery(
            effective_metadata.get("completion_recovery")
        ),
        "completion_receipt_id": effective_metadata.get("completion_receipt_id"),
        "resume_when": normalize_todo_resume_when(
            effective_metadata.get("resume_when")
        ),
        "resume_monitor_generation": effective_metadata.get(
            "resume_monitor_generation"
        ),
        "no_followup": normalize_todo_no_followup(
            effective_metadata.get("no_followup")
        ),
        "target_key": effective_metadata.get("target_key"),
        "cadence": effective_metadata.get("cadence"),
        "next_due_at": effective_metadata.get("next_due_at"),
        "expires_at": effective_metadata.get("expires_at"),
        "watch_only": effective_metadata.get("watch_only"),
        "material_change_generation": effective_metadata.get(
            "material_change_generation"
        ),
    }
