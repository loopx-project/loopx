"""Historic Todo imports with lazy ownership; canonical mutations bypass legacy writers."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .control_plane.todos.mutation_api import (
    add_goal_todo as add_goal_todo,
    update_goal_todo as update_goal_todo,
    complete_goal_todo as complete_goal_todo,
    supersede_goal_todo as supersede_goal_todo,
    archive_completed_todos as archive_completed_todos,
    ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE as ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE,
)
from .control_plane.todos.list_readback import list_goal_todos as list_goal_todos
from .control_plane.todos.active_state_editing import (
    section_bounds as section_bounds,
    todo_blocks as todo_blocks,
)
from .control_plane.todos.path_resolution import (
    resolve_todo_state as resolve_todo_state,
    resolve_todo_state_path as resolve_todo_state_path,
)

# Preserve the existing import surface without loading unpromoted writer code.
_EXPORTS = {
    "add_goal_todo": "loopx.control_plane.todos.mutation_api",
    "update_goal_todo": "loopx.control_plane.todos.mutation_api",
    "complete_goal_todo": "loopx.control_plane.todos.mutation_api",
    "registered_agent_ids_from_registry": "loopx.agent_registry",
    "require_registered_agent_id": "loopx.agent_registry",
    "load_registry": "loopx.history",
    "resolve_runtime_root": "loopx.paths",
    "load_rollout_events": "loopx.rollout_event_log",
    "rollout_event_log_path": "loopx.rollout_event_log",
    "now_local": "loopx.state_refresh",
    "resolve_goal_state": "loopx.state_refresh",
    "MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE": "loopx.status",
    "TODO_STATUS_DONE": "loopx.control_plane.todos.contract",
    "TODO_STATUS_OPEN": "loopx.control_plane.todos.contract",
    "build_todo_id": "loopx.control_plane.todos.contract",
    "format_todo_metadata_line": "loopx.control_plane.todos.contract",
    "metadata_line_for_todo_block": "loopx.control_plane.todos.contract",
    "normalize_required_capabilities": "loopx.control_plane.todos.contract",
    "normalize_required_write_scopes": "loopx.control_plane.todos.contract",
    "normalize_explore_result_node_refs": "loopx.control_plane.todos.contract",
    "normalize_target_capabilities": "loopx.control_plane.todos.contract",
    "normalize_todo_blocks_agent": "loopx.control_plane.todos.contract",
    "normalize_todo_bound_agent": "loopx.control_plane.todos.contract",
    "normalize_todo_capability_binding_ref": "loopx.control_plane.todos.contract",
    "normalize_todo_claimed_by": "loopx.control_plane.todos.contract",
    "normalize_todo_continuation_policy": "loopx.control_plane.todos.contract",
    "normalize_todo_decision_scope": "loopx.control_plane.todos.contract",
    "normalize_todo_excluded_agents": "loopx.control_plane.todos.contract",
    "normalize_todo_global_gate": "loopx.control_plane.todos.contract",
    "normalize_todo_goal_bound": "loopx.control_plane.todos.contract",
    "normalize_todo_id": "loopx.control_plane.todos.contract",
    "normalize_todo_id_list": "loopx.control_plane.todos.contract",
    "normalize_todo_required_decision_scopes": "loopx.control_plane.todos.contract",
    "normalize_todo_replan_obligation_id": "loopx.control_plane.todos.contract",
    "normalize_todo_resume_when": "loopx.control_plane.todos.contract",
    "normalize_todo_status": "loopx.control_plane.todos.contract",
    "normalize_todo_task_domain": "loopx.control_plane.todos.contract",
    "normalize_todo_task_repository": "loopx.control_plane.todos.contract",
    "parse_todo_metadata_line": "loopx.control_plane.todos.contract",
    "require_todo_excluded_agents": "loopx.control_plane.todos.contract",
    "resolve_next_user_task_class": "loopx.control_plane.todos.contract",
    "require_supported_todo_resume_when": "loopx.control_plane.todos.contract",
    "todo_marker_for_status": "loopx.control_plane.todos.contract",
    "TODO_SECTION_HEADINGS": "loopx.control_plane.todos.active_state_editing",
    "find_todo_block": "loopx.control_plane.todos.active_state_editing",
    "insert_into_existing_section": "loopx.control_plane.todos.active_state_editing",
    "insert_new_section": "loopx.control_plane.todos.active_state_editing",
    "replace_updated_at": "loopx.control_plane.todos.active_state_editing",
    "section_bounds": "loopx.control_plane.todos.active_state_editing",
    "set_todo_marker": "loopx.control_plane.todos.active_state_editing",
    "todo_blocks": "loopx.control_plane.todos.active_state_editing",
    "matching_todo_block": "loopx.control_plane.todos.addition",
    "require_replan_successor_rebinding": "loopx.control_plane.todos.addition",
    "archive_completed_todo_lines": "loopx.control_plane.todos.completed_archive",
    "completion_policy_from_transaction": "loopx.control_plane.todos.completion_policy",
    "locked_todo_completion_transaction": "loopx.control_plane.todos.completion_transaction",
    "materialized_todo_completion_replay": "loopx.control_plane.todos.completion_transaction",
    "require_completion_successor_todo_ids": "loopx.control_plane.todos.completion_transaction",
    "user_todo_completion_metadata_updates": "loopx.control_plane.todos.completion_transaction",
    "completion_validation_module": "loopx.control_plane.todos",
    "serialize_added_todo_payload": "loopx.control_plane.todos.mutation_response",
    "apply_added_todo_next_action": "loopx.control_plane.todos.next_action_runtime",
    "settle_completed_todo_next_action": "loopx.control_plane.todos.next_action_runtime",
    "compact_agent_lane_todo_summary": "loopx.control_plane.todos.list_projection",
    "compact_thin_todo_list_payload": "loopx.control_plane.todos.list_projection",
    "todo_item_relations": "loopx.control_plane.todos.list_projection",
    "todo_list_projection_contract": "loopx.control_plane.todos.list_projection",
    "exact_archived_todo_summaries": "loopx.control_plane.todos.goal_todo_projection",
    "goal_todo_summaries": "loopx.control_plane.todos.goal_todo_projection",
    "todo_summaries_from_fields": "loopx.control_plane.todos.goal_todo_projection",
    "parse_todo_source": "loopx.control_plane.todos.active_state_todo_parser",
    "todo_monitor_metadata": "loopx.control_plane.todos",
    "authorize_todo_lifecycle_mutation": "loopx.control_plane.todos.mutation_authority",
    "todo_update_authority_action": "loopx.control_plane.todos.mutation_authority",
    "build_open_parent_successor_advisory": "loopx.control_plane.todos.succession_warning",
    "build_successor_intents": "loopx.control_plane.todos.successor_derivation",
    "derive_successor_proposals": "loopx.control_plane.todos.successor_derivation",
    "successor_add_kwargs": "loopx.control_plane.todos.successor_derivation",
    "MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL": "loopx.control_plane.todos.todo_index",
    "normalize_new_todo": "loopx.control_plane.todos.text",
    "list_goal_todos": "loopx.control_plane.todos.list_readback",
    "todo_priority_label": "loopx.control_plane.todos.todo_semantics",
    "apply_completed_user_todo_lifecycle": "loopx.control_plane.todos.unblock_resume",
    "completion_decision_target": "loopx.control_plane.todos.unblock_resume",
    "require_completion_decision_outcome": "loopx.control_plane.todos.unblock_resume",
    "_attach_todo_write_correctness_dry_run_packet": "loopx.control_plane.todos.write_correctness",
    "require_user_todo_task_class": "loopx.control_plane.todos.authoring_scope",
    "legacy_todo_write_transaction": "loopx.control_plane.coordination.legacy_writer_fence",
    "canonical_todo_items": "loopx.control_plane.coordination.local_authority",
    "canonical_todo_summary_fields": "loopx.control_plane.coordination.local_authority",
    "read_canonical_todos_if_promoted": "loopx.control_plane.coordination.local_authority",
    "resolve_todo_state_path": "loopx.control_plane.todos.path_resolution",
    "provider_first_terminal_lifecycle": "loopx.control_plane.todos.provider_terminal_lifecycle",
    "goal_handoff_mode": "loopx.control_plane.todos.handoff_mode",
    "enter_added_todo_ownership_handoff_gate": "loopx.control_plane.todos.handoff_mode",
    "enter_todo_ownership_handoff_gate": "loopx.control_plane.todos.handoff_mode",
    "resolve_todo_completion_handoff": "loopx.control_plane.todos.handoff_mode",
    "effective_runtime_root": "loopx.paths",
    "enter_terminal_todo_lease_fence": "loopx.control_plane.work_items.task_lease",
    "hold_task_lease_mutation_fence": "loopx.control_plane.work_items.task_lease",
    "release_verified_task_lease_fence": "loopx.control_plane.work_items.task_lease",
    "supersede_goal_todo": "loopx.control_plane.todos.mutation_api",
    "archive_completed_todos": "loopx.control_plane.todos.mutation_api",
    "ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE": "loopx.control_plane.todos.mutation_api",
    "resolve_todo_state": "loopx.control_plane.todos.path_resolution",
    "add_todo_to_lines": "loopx.control_plane.todos.legacy_mutation",
    "_add_goal_todo_legacy": "loopx.control_plane.todos.legacy_mutation",
    "_update_goal_todo_legacy": "loopx.control_plane.todos.legacy_mutation",
    "_complete_goal_todo_legacy": "loopx.control_plane.todos.legacy_mutation",
    "apply_todo_update_to_lines": "loopx.control_plane.todos.line_update",
    "link_generated_successor_todo_ids": "loopx.control_plane.todos.line_update",
    "link_superseding_todo_id": "loopx.control_plane.todos.line_update",
    "upsert_todo_metadata": "loopx.control_plane.todos.line_update",
}


_ALIASES = {
    "completion_validation_module": "completion_validation",
    "todo_monitor_metadata": "monitor_metadata",
    "_attach_todo_write_correctness_dry_run_packet": "attach_todo_write_correctness_dry_run_packet",
}


def __getattr__(name: str) -> Any:
    owner = _EXPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(owner), _ALIASES.get(name, name))


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
