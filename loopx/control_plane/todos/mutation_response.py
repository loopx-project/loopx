"""Public Todo mutation response projection from already decided facts."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .todo_semantics import todo_priority_label

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
        "priority": todo_priority_label({"text": todo_text,
            **({"priority": add_result["priority"]} if "priority" in add_result else {})}),
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
