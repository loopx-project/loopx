// Generated from coordination_state_contract_v0.json; do not edit.

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

export const LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA = "loopx_local_coordination_todo_read_request_v0";
export const LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA = "loopx_local_coordination_todo_read_result_v0";
export const LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA = "loopx_local_coordination_todo_list_request_v0";
export const LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA = "loopx_local_coordination_todo_list_result_v0";
export const LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA = "loopx_local_coordination_promotion_request_v0";
export const LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA = "loopx_local_coordination_promotion_result_v0";
export const LOCAL_COORDINATION_PROMOTION_RECEIPT_SCHEMA = "loopx_local_coordination_promotion_receipt_v0";
export const LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA = "loopx_local_coordination_promotion_review_request_v0";
export const LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA = "loopx_local_coordination_promotion_review_result_v0";

export const COORDINATION_RUNTIME_SHADOW_COMMIT_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_commit_v0";
export const COORDINATION_RUNTIME_SHADOW_COMMIT_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_result_v0";
export const COORDINATION_RUNTIME_SHADOW_RECEIPT_SCHEMA = "loopx_coordination_runtime_shadow_receipt_v0";
export const COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_inspect_v0";
export const COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_inspection_v0";
export const COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_bootstrap_v0";
export const COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_bootstrap_result_v0";
export const COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_rollback_v0";
export const COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_rollback_result_v0";
export const COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_qualify_v0";
export const COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_qualification_v0";
export const COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_todo_read_v0";
export const COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_todo_read_result_v0";

export const LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA = "loopx_coordination_runtime_shadow_binding_v0";
export const LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA = "loopx_local_authority_shadow_config_v0";
export const LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA = "loopx_local_authority_shadow_request_v0";
export const LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA = "loopx_local_authority_shadow_projection_v0";
export const LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA = "loopx_local_authority_shadow_evidence_v0";
export const LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA = "loopx_local_authority_shadow_observation_receipt_v0";
export const LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA = "loopx_local_authority_shadow_outbox_entry_v1";
export const LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA = "loopx_local_authority_shadow_outbox_commit_v1";
export const LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA = "loopx_local_authority_shadow_drain_cursor_v0";
export const LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA = "loopx_coordination_runtime_shadow_projection_v0";
export const LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_commit_entry_request_v1";
export const LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_commit_entry_result_v0";
export const LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA = "loopx_coordination_runtime_shadow_outbox_read_v0";
export const LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA = "loopx_coordination_runtime_shadow_outbox_read_result_v0";
export const LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA = "loopx_coordination_runtime_shadow_outbox_event_v1";
export const LOCAL_AUTHORITY_SHADOW_TRANSACTION_RECEIPT_SCHEMA = "loopx_coordination_runtime_shadow_outbox_receipt_v1";
export const LOCAL_AUTHORITY_SHADOW_TRANSACTION_EVIDENCE_SCHEMA = "loopx_local_authority_shadow_evidence_v1";

export const SHADOW_MANAGEMENT_STATE_SCHEMA = "loopx_shadow_management_state_v1";
export const SHADOW_MANAGEMENT_MANIFEST_SCHEMA = "loopx_shadow_management_manifest_v1";
export const SHADOW_OUTBOX_MANIFEST_SCHEMA = "loopx_shadow_outbox_manifest_v1";

export const LEGACY_COORDINATION_WRITER_FENCE_SCHEMA = "loopx_legacy_coordination_writer_fence_v0";
export const LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA = "loopx_legacy_coordination_writer_fence_engage_request_v0";
export const LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA = "loopx_legacy_coordination_writer_fence_result_v0";
export const LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA = "loopx_legacy_coordination_write_check_request_v0";
export const LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA = "loopx_legacy_coordination_write_check_result_v0";

export const DELIVERY_CONTINUITY_RESULT_SCHEMA = "loopx_delivery_continuity_result_v0";
export const DELIVERY_BOUNDARY_RESULT_SCHEMA = "loopx_delivery_boundary_result_v0";
export const DELIVERY_ROUTING_REQUEST_SCHEMA = "loopx_delivery_routing_request_v0";
export const DELIVERY_ROUTING_RESULT_SCHEMA = "loopx_delivery_routing_result_v0";

export const DELIVERY_WORKSPACE_CAUSALITY_SCHEMA = "delivery_workspace_causality_v0";
export const DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA = "loopx_delivery_workspace_causality_request_v0";
export const DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA = "loopx_delivery_workspace_causality_result_v0";
export const DELIVERY_WORKSPACE_RESOLUTION_SCHEMA = "delivery_workspace_resolution_v0";
export const DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA = "settlement_workspace_requirement_v0";
export const DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA = "legacy_settlement_receipt_evidence_v0";

export const DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA = "delivery_workspace_v1";
export const DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA = "delivery_workspace_v0";
export const DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA = "loopx_delivery_workspace_request_v0";
export const DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA = "loopx_delivery_workspace_result_v0";

export const TASK_LEASE_ACQUIRE_REQUEST_SCHEMA = "loopx_task_lease_acquire_native_v0";
export const TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA = "loopx_canonical_task_lease_acquire_request_v0";
export const TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA = "loopx_task_lease_lifecycle_native_v0";
export const TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA = "loopx_canonical_task_lease_renew_request_v0";
export const TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA = "loopx_canonical_task_lease_lifecycle_request_v0";
export const TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA = "loopx_canonical_task_lease_claim_transfer_request_v0";

export const CAPABILITY_HOOK_REGISTRATION_SCHEMA = "loopx_capability_hook_registration_v0";
export const CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA = "loopx_interaction_projection_hook_result_v0";
export const CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA = "loopx_turn_start_capability_hook_registration_v1";
export const CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA = "loopx_turn_start_capability_hook_result_v0";
export const CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA = "loopx_post_writeback_capability_hook_registration_v0";
export const CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA = "loopx_post_writeback_capability_hook_input_v0";
export const CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA = "loopx_post_writeback_capability_hook_result_v0";
export const CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA = "loopx_post_writeback_capability_hook_receipt_v0";
export const CAPABILITY_HOOK_INTENT_SCHEMA = "loopx_capability_intent_v0";

export const ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA = "action_selection_qualification_request_v0";
export const ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA = "action_selection_qualification_v0";
export const ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA = "quota_planning_packet_request_v0";
export const ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA = "quota_planning_packet_v0";

export const TODO_RESUME_NORMALIZE_REQUEST_SCHEMA = "todo_resume_normalize_request_v0";
export const TODO_RESUME_EVALUATION_REQUEST_SCHEMA = "todo_resume_evaluation_request_v0";
export const TODO_RESUME_EVALUATION_RESULT_SCHEMA = "todo_resume_evaluation_v0";
export const TODO_RESUME_EXTERNAL_WAIT_REQUEST_SCHEMA = "todo_external_wait_request_v0";
export const TODO_RESUME_EXTERNAL_WAIT_RESULT_SCHEMA = "todo_external_wait_transition_v0";

export const REPLAN_SETTLEMENT_REQUEST_SCHEMA = "loopx_replan_settlement_request_v0";
export const REPLAN_SETTLEMENT_RESULT_SCHEMA = "replan_settlement_contract_v0";
export const REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA = "loopx_todo_lifecycle_reentry_request_v0";
export const REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA = "todo_lifecycle_settlement_reentry_v0";

export const COORDINATION_STATE_CONTRACT = deepFreeze({
  "schema_version": "loopx_coordination_state_contract_v0",
  "todo_read_record": {
    "schema_version": "loopx_todo_canonical_read_record_v0",
    "item_schema_version": "todo_item_v0",
    "fields": [
      "index",
      "done",
      "text",
      "schema_version",
      "todo_id",
      "role",
      "status",
      "priority",
      "title",
      "archive_state",
      "source_section",
      "task_class",
      "action_kind",
      "task_domain",
      "capability_binding_ref",
      "task_repository",
      "continuation_policy",
      "removed_continuation_policy",
      "required_write_scopes",
      "required_capabilities",
      "target_capabilities",
      "explore_result_node_refs",
      "decision_scope",
      "required_decision_scopes",
      "decision_outcome",
      "decision_scope_outcomes",
      "claimed_by",
      "created_by",
      "last_actor_agent_id",
      "bound_agent",
      "goal_bound",
      "blocks_agent",
      "excluded_agents",
      "global_gate",
      "unblocks_todo_id",
      "resume_when",
      "resume_monitor_generation",
      "resume_condition",
      "resume_ready",
      "no_followup",
      "successor_todo_ids",
      "completion_continuation",
      "completion_recovery",
      "replan_obligation_id",
      "target_key",
      "cadence",
      "next_due_at",
      "expires_at",
      "watch_only",
      "last_checked_at",
      "result_hash",
      "consecutive_no_change",
      "material_change",
      "material_change_generation",
      "max_no_change_before_replan",
      "monitor_effect_id",
      "note",
      "evidence",
      "reason",
      "completed_at",
      "completion_turn_key",
      "updated_at",
      "superseded_by",
      "completion_validation_required",
      "completion_validation_sha256",
      "handoff_note"
    ],
    "required_fields": [
      "schema_version",
      "todo_id",
      "role",
      "status",
      "done",
      "text",
      "archive_state",
      "source_section"
    ]
  },
  "todo_domain_record": {
    "schema_version": "loopx_todo_domain_read_record_v0",
    "item_schema_version": "todo_domain_record_v0",
    "fields_from": "todo_read_record",
    "exclude_fields_from": "todo_projection_metadata",
    "required_fields": [
      "schema_version",
      "todo_id",
      "role",
      "status",
      "done",
      "text",
      "archive_state"
    ]
  },
  "todo_priority": {
    "values": [
      "P0",
      "P1",
      "P2",
      "P3",
      "P4"
    ],
    "legacy_prefix_pattern": "^\\s*\\[(P[0-4](?:[-\\s][^\\]]*)?)\\]\\s*(.+)$",
    "legacy_label_pattern": "^(P[0-4])(?:$|[-\\s])",
    "missing_rank": 50
  },
  "todo_projection_metadata": {
    "fields": [
      "source_section",
      "index"
    ],
    "required_fields": [
      "source_section"
    ]
  },
  "local_authority_protocol": {
    "todo_read_request_schema": LOCAL_COORDINATION_TODO_READ_REQUEST_SCHEMA,
    "todo_read_result_schema": LOCAL_COORDINATION_TODO_READ_RESULT_SCHEMA,
    "todo_list_request_schema": LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
    "todo_list_result_schema": LOCAL_COORDINATION_TODO_LIST_RESULT_SCHEMA,
    "promotion_request_schema": LOCAL_COORDINATION_PROMOTION_REQUEST_SCHEMA,
    "promotion_result_schema": LOCAL_COORDINATION_PROMOTION_RESULT_SCHEMA,
    "promotion_receipt_schema": LOCAL_COORDINATION_PROMOTION_RECEIPT_SCHEMA,
    "promotion_review_request_schema": LOCAL_COORDINATION_PROMOTION_REVIEW_REQUEST_SCHEMA,
    "promotion_review_result_schema": LOCAL_COORDINATION_PROMOTION_REVIEW_RESULT_SCHEMA
  },
  "runtime_shadow_protocol": {
    "commit_request_schema": COORDINATION_RUNTIME_SHADOW_COMMIT_REQUEST_SCHEMA,
    "commit_result_schema": COORDINATION_RUNTIME_SHADOW_COMMIT_RESULT_SCHEMA,
    "receipt_schema": COORDINATION_RUNTIME_SHADOW_RECEIPT_SCHEMA,
    "inspect_request_schema": COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA,
    "inspect_result_schema": COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
    "bootstrap_request_schema": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    "bootstrap_result_schema": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
    "rollback_request_schema": COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA,
    "rollback_result_schema": COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA,
    "qualify_request_schema": COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA,
    "qualify_result_schema": COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
    "todo_read_request_schema": COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA,
    "todo_read_result_schema": COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA
  },
  "local_authority_shadow_protocol": {
    "binding_schema": LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
    "config_schema": LOCAL_AUTHORITY_SHADOW_CONFIG_SCHEMA,
    "request_schema": LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA,
    "projection_schema": LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA,
    "evidence_schema": LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
    "observation_receipt_schema": LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA,
    "outbox_entry_schema": LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
    "outbox_commit_schema": LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
    "drain_cursor_schema": LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
    "transaction_projection_schema": LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA,
    "commit_entry_request_schema": LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA,
    "commit_entry_result_schema": LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
    "read_request_schema": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
    "read_result_schema": LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
    "event_schema": LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA,
    "transaction_receipt_schema": LOCAL_AUTHORITY_SHADOW_TRANSACTION_RECEIPT_SCHEMA,
    "transaction_evidence_schema": LOCAL_AUTHORITY_SHADOW_TRANSACTION_EVIDENCE_SCHEMA
  },
  "shadow_management_protocol": {
    "state_schema": SHADOW_MANAGEMENT_STATE_SCHEMA,
    "manifest_schema": SHADOW_MANAGEMENT_MANIFEST_SCHEMA,
    "outbox_manifest_schema": SHADOW_OUTBOX_MANIFEST_SCHEMA
  },
  "legacy_writer_fence_protocol": {
    "fence_schema": LEGACY_COORDINATION_WRITER_FENCE_SCHEMA,
    "engage_request_schema": LEGACY_COORDINATION_WRITER_FENCE_ENGAGE_REQUEST_SCHEMA,
    "result_schema": LEGACY_COORDINATION_WRITER_FENCE_RESULT_SCHEMA,
    "write_check_request_schema": LEGACY_COORDINATION_WRITE_CHECK_REQUEST_SCHEMA,
    "write_check_result_schema": LEGACY_COORDINATION_WRITE_CHECK_RESULT_SCHEMA
  },
  "delivery_continuity_protocol": {
    "continuity_result_schema": DELIVERY_CONTINUITY_RESULT_SCHEMA,
    "boundary_result_schema": DELIVERY_BOUNDARY_RESULT_SCHEMA,
    "routing_request_schema": DELIVERY_ROUTING_REQUEST_SCHEMA,
    "routing_result_schema": DELIVERY_ROUTING_RESULT_SCHEMA
  },
  "delivery_workspace_protocol": {
    "causality_schema": DELIVERY_WORKSPACE_CAUSALITY_SCHEMA,
    "causality_request_schema": DELIVERY_WORKSPACE_CAUSALITY_REQUEST_SCHEMA,
    "causality_result_schema": DELIVERY_WORKSPACE_CAUSALITY_RESULT_SCHEMA,
    "resolution_schema": DELIVERY_WORKSPACE_RESOLUTION_SCHEMA,
    "settlement_requirement_schema": DELIVERY_WORKSPACE_SETTLEMENT_REQUIREMENT_SCHEMA,
    "legacy_receipt_evidence_schema": DELIVERY_WORKSPACE_LEGACY_RECEIPT_EVIDENCE_SCHEMA
  },
  "delivery_workspace_snapshot_protocol": {
    "snapshot_schema": DELIVERY_WORKSPACE_SNAPSHOT_SNAPSHOT_SCHEMA,
    "legacy_snapshot_schema": DELIVERY_WORKSPACE_SNAPSHOT_LEGACY_SNAPSHOT_SCHEMA,
    "request_schema": DELIVERY_WORKSPACE_SNAPSHOT_REQUEST_SCHEMA,
    "result_schema": DELIVERY_WORKSPACE_SNAPSHOT_RESULT_SCHEMA
  },
  "task_lease_protocol": {
    "acquire_request_schema": TASK_LEASE_ACQUIRE_REQUEST_SCHEMA,
    "canonical_acquire_request_schema": TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA,
    "lifecycle_request_schema": TASK_LEASE_LIFECYCLE_REQUEST_SCHEMA,
    "canonical_renew_request_schema": TASK_LEASE_CANONICAL_RENEW_REQUEST_SCHEMA,
    "canonical_lifecycle_request_schema": TASK_LEASE_CANONICAL_LIFECYCLE_REQUEST_SCHEMA,
    "canonical_claim_transfer_request_schema": TASK_LEASE_CANONICAL_CLAIM_TRANSFER_REQUEST_SCHEMA
  },
  "capability_hook_protocol": {
    "registration_schema": CAPABILITY_HOOK_REGISTRATION_SCHEMA,
    "interaction_result_schema": CAPABILITY_HOOK_INTERACTION_RESULT_SCHEMA,
    "turn_start_registration_schema": CAPABILITY_HOOK_TURN_START_REGISTRATION_SCHEMA,
    "turn_start_result_schema": CAPABILITY_HOOK_TURN_START_RESULT_SCHEMA,
    "post_writeback_registration_schema": CAPABILITY_HOOK_POST_WRITEBACK_REGISTRATION_SCHEMA,
    "post_writeback_input_schema": CAPABILITY_HOOK_POST_WRITEBACK_INPUT_SCHEMA,
    "post_writeback_result_schema": CAPABILITY_HOOK_POST_WRITEBACK_RESULT_SCHEMA,
    "post_writeback_receipt_schema": CAPABILITY_HOOK_POST_WRITEBACK_RECEIPT_SCHEMA,
    "intent_schema": CAPABILITY_HOOK_INTENT_SCHEMA
  },
  "action_portfolio_protocol": {
    "selection_request_schema": ACTION_PORTFOLIO_SELECTION_REQUEST_SCHEMA,
    "selection_result_schema": ACTION_PORTFOLIO_SELECTION_RESULT_SCHEMA,
    "planning_packet_request_schema": ACTION_PORTFOLIO_PLANNING_PACKET_REQUEST_SCHEMA,
    "planning_packet_result_schema": ACTION_PORTFOLIO_PLANNING_PACKET_RESULT_SCHEMA
  },
  "todo_resume_protocol": {
    "normalize_request_schema": TODO_RESUME_NORMALIZE_REQUEST_SCHEMA,
    "evaluation_request_schema": TODO_RESUME_EVALUATION_REQUEST_SCHEMA,
    "evaluation_result_schema": TODO_RESUME_EVALUATION_RESULT_SCHEMA,
    "external_wait_request_schema": TODO_RESUME_EXTERNAL_WAIT_REQUEST_SCHEMA,
    "external_wait_result_schema": TODO_RESUME_EXTERNAL_WAIT_RESULT_SCHEMA
  },
  "replan_settlement_protocol": {
    "request_schema": REPLAN_SETTLEMENT_REQUEST_SCHEMA,
    "result_schema": REPLAN_SETTLEMENT_RESULT_SCHEMA,
    "lifecycle_reentry_request_schema": REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
    "lifecycle_reentry_result_schema": REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA
  },
  "compatibility": {
    "unknown_field_policy": "reject",
    "field_removal_policy": "maintainer_approval_required",
    "markdown_role": "human_workbench_and_compatibility_projection"
  }
} as const);
