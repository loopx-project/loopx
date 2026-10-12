"""Public Todo mutation response projection from already decided facts."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import (
    normalize_explore_result_node_refs, normalize_required_capabilities,
    normalize_target_capabilities, normalize_todo_blocks_agent,
    normalize_todo_bound_agent, normalize_todo_claimed_by,
    normalize_todo_continuation_policy, normalize_todo_decision_scope,
    normalize_todo_decision_scope_outcomes, normalize_todo_excluded_agents,
    normalize_todo_global_gate, normalize_todo_goal_bound, normalize_todo_id,
    normalize_todo_id_list, normalize_todo_no_followup,
    normalize_todo_required_decision_scopes, normalize_todo_resume_when,
    normalize_todo_task_domain, normalize_todo_task_repository,
)
from .completion_state import (
    normalize_todo_completion_continuation, normalize_todo_completion_recovery,
)

def serialize_added_todo_payload(
    *, add_result: Mapping[str, Any], goal_id: str, role: str, todo_text: str,
    agent_id: str | None, state_file: Path, project: Path | None,
    updated_at: str | None, dry_run: bool, added: bool,
    metadata_updated: bool, changed: bool, handoff_gate: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the public add result from already decided Todo facts."""
    effective_agent_id = agent_id
    resolved_state_file = state_file
    resolved_project = project
    return {
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

def serialize_todo_update_result(
    *, role: str, section: str, todo: str | None, todo_id: str,
    status: str, priority: str | None, status_changed: bool,
    text_changed: bool, metadata_updated: bool, metadata: Mapping[str, Any],
    monitor_poll_transition: Mapping[str, Any] | None = None,
    external_wait_transition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the public update result from already decided Todo facts."""
    effective_metadata = metadata
    return {
        **({"monitor_poll_transition": dict(monitor_poll_transition)}
           if monitor_poll_transition is not None else {}),
        **({"external_wait_transition": dict(external_wait_transition)}
           if external_wait_transition is not None else {}),
        "role": role,
        "section": section,
        "todo": todo,
        "todo_id": todo_id,
        "status": status,
        "status_changed": status_changed,
        "text_changed": text_changed,
        "priority": priority,
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
