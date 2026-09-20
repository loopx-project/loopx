import rawStatus from "../../../../../examples/status.example.json";
import { z } from "zod";

import { goalAcceptanceObservationSchema } from "./goal-acceptance-observation";

import { goalChannelProjectionSchema } from "./goal-channel-frontstage";

export const quotaSchema = z.object({
  compute: z.number().optional().default(1),
  window_hours: z.number().optional().default(24),
  slot_minutes: z.number().optional().default(1),
  allowed_slots: z.number().optional().nullable(),
  spent_slots: z.number().optional().default(0),
  state: z.string().optional().nullable(),
  next_eligible_at: z.string().optional().nullable(),
  reason: z.string().optional().nullable(),
  blocked_action_scope: z.string().optional().nullable(),
  focus_wait: z.boolean().optional().nullable(),
  handoff_outcome_floor_block: z.boolean().optional().nullable(),
  safe_bypass_allowed: z.boolean().optional().default(false),
  safe_bypass_kind: z.string().optional().nullable(),
  safe_bypass_policy: z.string().optional().nullable(),
  post_handoff_outcome_gap_streak: z.number().optional().nullable(),
  outcome_gap_threshold: z.number().optional().nullable(),
  must_advance: z.array(z.string()).optional().default([]),
  avoid: z.array(z.string()).optional().default([]),
}).transform((quota) => {
  const slotMinutes = Math.max(1, quota.slot_minutes);
  const defaultAllowedSlots = Math.round((quota.window_hours * 60 * quota.compute) / slotMinutes);
  return {
    ...quota,
    slot_minutes: slotMinutes,
    allowed_slots: quota.allowed_slots ?? defaultAllowedSlots,
  };
});

export const controlPlaneSchema = z.object({
  self_repair: z.object({
    enabled: z.boolean().optional().default(false),
    allow_health_blocker_repair: z.boolean().optional().default(false),
    allow_waiting_projection_repair: z.boolean().optional().default(false),
  }).optional().nullable(),
}).passthrough();

export const orchestrationPolicySchema = z.object({
  model_config: z.object({ model: z.string(), reasoning_effort: z.string().optional() }).optional(),
  execution_config: z.string().optional(),

  mode: z.string().optional().default("default"),
  orchestration_mode: z.string().optional().nullable(),
  spawn_allowed: z.boolean().optional().default(false),
  allowed: z.boolean().optional().nullable(),
  max_children: z.number().optional().default(0),
  allowed_domains: z.array(z.string()).optional().default([]),
}).passthrough();

export const reviewMaterialSchema = z.object({
  label: z.string().optional().nullable(),
  path: z.string(),
  anchor: z.string().optional().nullable(),
  exists: z.boolean().optional().default(false),
  resolved_path: z.string().optional().nullable(),
});

export const todoItemSchema = z.object({
  index: z.number(),
  done: z.boolean(),
  text: z.string(),
  schema_version: z.string().optional().nullable(),
  todo_id: z.string().optional().nullable(),
  role: z.string().optional().nullable(),
  status: z.string().optional().nullable(),
  resume_when: z.string().optional().nullable(),
  resume_ready: z.boolean().optional().nullable(),
  resume_condition: z.record(z.string(), z.unknown()).optional().nullable(),
  priority: z.string().optional().nullable(),
  title: z.string().optional().nullable(),
  archive_state: z.string().optional().nullable(),
  source_section: z.string().optional().nullable(),
  task_class: z.string().optional().nullable(),
  task_domain: z.string().optional().nullable(),
  action_kind: z.string().optional().nullable(),
  claimed_by: z.string().optional().nullable(),
  required_capabilities: z.array(z.string()).optional(),
  note: z.string().optional().nullable(),
  evidence: z.string().optional().nullable(),
  updated_at: z.string().optional().nullable(),
  review_materials: z.array(reviewMaterialSchema).optional().default([]),
}).passthrough();

export const todoGroupSchema = z.object({
  source_section: z.string().optional().nullable(),
  total_count: z.number().optional().default(0),
  open_count: z.number().optional().default(0),
  done_count: z.number().optional().default(0),
  advancement_done_count: z.number().optional(),
  items: z.array(todoItemSchema).optional().default([]),
  deferred_items: z.array(todoItemSchema).optional(),
});

export const todoIndexItemSchema = todoItemSchema.extend({
  goal_id: z.string(),
  source: z.string().optional().nullable(),
  event_count: z.number().optional().default(0),
  event_kinds: z.array(z.string()).optional().default([]),
  latest_event_kind: z.string().optional().nullable(),
  latest_event_at: z.string().optional().nullable(),
  latest_event_status: z.string().optional().nullable(),
  agent_id: z.string().optional().nullable(),
}).passthrough();

export const todoIndexSchema = z.object({
  schema_version: z.string().optional().nullable(),
  source: z.string().optional().nullable(),
  total_count: z.number().optional().default(0),
  current_projected_count: z.number().optional().default(0),
  rollout_event_count: z.number().optional().default(0),
  item_limit: z.number().optional().nullable(),
  items: z.array(todoIndexItemSchema).optional().default([]),
});

export const agentManagementWorkspaceRefSchema = z.object({
  kind: z.string().optional().nullable(),
  label: z.string().optional().nullable(),
  path_safe: z.boolean().optional().default(false),
  branch: z.string().optional().nullable(),
  write_scope: z.array(z.string()).optional().default([]),
}).passthrough();

export const agentManagementStaleClaimHintSchema = z.object({
  state: z.string().optional().nullable(),
  claimed_by: z.string().optional().nullable(),
  last_activity_at: z.string().optional().nullable(),
  threshold_hours: z.number().optional().nullable(),
  reason: z.string().optional().nullable(),
  recommended_operator_action: z.string().optional().nullable(),
}).passthrough();

export const agentManagementTodoRowSchema = z.object({
  schema_version: z.string().optional().nullable(),
  todo_id: z.string().optional().nullable(),
  goal_id: z.string().optional().nullable(),
  role: z.string().optional().nullable(),
  status: z.string().optional().nullable(),
  priority: z.string().optional().nullable(),
  title: z.string().optional().nullable(),
  task_class: z.string().optional().nullable(),
  action_kind: z.string().optional().nullable(),
  claimed_by: z.string().optional().nullable(),
  required_write_scopes: z.array(z.string()).optional().default([]),
  workspace_ref: agentManagementWorkspaceRefSchema.optional().nullable(),
}).passthrough();

export const agentManagementHandoffNoteSchema = z.object({
  schema_version: z.string().optional().nullable(),
  from_agent: z.string().optional().nullable(),
  to_agent: z.string().optional().nullable(),
  intent: z.string().optional().nullable(),
  summary: z.string().optional().nullable(),
  blocker: z.string().optional().nullable(),
  suggested_next_action: z.string().optional().nullable(),
  evidence_refs: z.array(z.string()).optional().default([]),
  updated_at: z.string().optional().nullable(),
}).passthrough();

export const agentManagementRowSchema = z.object({
  agent_id: z.string(),
  role: z.string().optional().nullable(),
  state: z.string().optional().nullable(),
  current_todo: agentManagementTodoRowSchema.optional().nullable(),
  next_action: z.string().optional().nullable(),
  last_activity_at: z.string().optional().nullable(),
  evidence_refs: z.array(z.string()).optional().default([]),
  handoff_refs: z.array(z.string()).optional().default([]),
  handoff_note: agentManagementHandoffNoteSchema.optional().nullable(),
  workspace_ref: agentManagementWorkspaceRefSchema.optional().nullable(),
  stale_claim_hint: agentManagementStaleClaimHintSchema.optional().nullable(),
  blocked_on: agentManagementTodoRowSchema.optional().nullable(),
  goal_ids: z.array(z.string()).optional().default([]),
}).passthrough();

export const agentManagementProjectionSchema = z.object({
  schema_version: z.string().optional().nullable(),
  mode: z.string().optional().nullable(),
  goal_id: z.string().optional().nullable(),
  generated_at: z.string().optional().nullable(),
  style_hint: z.object({
    preferred: z.string().optional().nullable(),
    license_boundary: z.string().optional().nullable(),
  }).optional().nullable(),
  truth_contract: z.object({
    todo_is_runtime_work_item: z.boolean().optional().default(true),
    projection_is_writable: z.boolean().optional().default(false),
    introduces_task_runtime: z.boolean().optional().default(false),
    write_api: z.boolean().optional().default(false),
  }).optional().nullable(),
  source_summary: z.object({
    registered_agent_count: z.number().optional().default(0),
    projected_agent_count: z.number().optional().default(0),
    todo_source: z.string().optional().nullable(),
  }).optional().nullable(),
  agents: z.array(agentManagementRowSchema).optional().default([]),
}).passthrough();

export const goalChannelNotificationRowSchema = z.object({
  goal_id: z.string(),
  configured: z.boolean().optional().default(false),
  enabled: z.boolean().optional().default(false),
  human_gate_auto_notify_enabled: z.boolean().optional().default(false),
  target_ref: z.string().optional().nullable(),
  receipt_count: z.number().optional().default(0),
  last_notified_at: z.string().optional().nullable(),
}).passthrough();

export const goalChannelNotificationProjectionSchema = z.object({
  schema_version: z.string().optional().nullable(),
  generated_at: z.string().optional().nullable(),
  goals: z.array(goalChannelNotificationRowSchema).optional().default([]),
}).passthrough();

export const projectAssetTodoSummarySchema = z.object({
  source_section: z.string().optional().nullable(),
  open: z.number().optional().default(0),
  done: z.number().optional().default(0),
  total: z.number().optional().default(0),
  advancement_done_count: z.number().optional(),
  next: z.string().optional().nullable(),
  next_index: z.number().optional().nullable(),
  items: z.array(todoItemSchema).optional().default([]),
  recent_completed_advancement_items: z.array(todoItemSchema).optional().default([]),
});

export const dependencyBlockerSchema = z.object({
  goal_id: z.string(),
  status: z.string().optional().nullable(),
  waiting_on: z.string().optional().nullable(),
  severity: z.string().optional().nullable(),
  index: z.number().optional().nullable(),
  text: z.string(),
  source: z.string().optional().nullable(),
});

export const dependencyBlockerSummarySchema = z.object({
  source: z.string().optional().nullable(),
  open_count: z.number().optional().default(0),
  items: z.array(dependencyBlockerSchema).optional().default([]),
});

export const autonomousBacklogCandidateSchema = z.object({
  goal_id: z.string(),
  status: z.string().optional().nullable(),
  waiting_on: z.string().optional().nullable(),
  quota_state: z.string().optional().nullable(),
  priority: z.string().optional().nullable(),
  todo_index: z.number().optional().nullable(),
  text: z.string(),
  source: z.string().optional().nullable(),
});

export const autonomousBacklogCandidateSummarySchema = z.object({
  source: z.string().optional().nullable(),
  open_count: z.number().optional().default(0),
  items: z.array(autonomousBacklogCandidateSchema).optional().default([]),
});

export const projectAssetLatestValidationSchema = z.object({
  generated_at: z.string().optional().nullable(),
  classification: z.string().optional().nullable(),
  summary: z.string().optional().nullable(),
});

export const staleLatestRunWarningSchema = z.object({
  kind: z.string().optional().nullable(),
  source: z.string().optional().nullable(),
  severity: z.string().optional().nullable(),
  requires_refresh_state: z.boolean().optional().default(false),
  reason: z.string().optional().nullable(),
  active_state_updated_at: z.string().optional().nullable(),
  latest_run_generated_at: z.string().optional().nullable(),
  latest_run_state_updated_at: z.string().optional().nullable(),
  latest_run_classification: z.string().optional().nullable(),
  recommended_action: z.string().optional().nullable(),
});

export const postHandoffRunSchema = z.object({
  generated_at: z.string().optional().nullable(),
  classification: z.string().optional().nullable(),
  delivery_batch_scale: z.string().optional().nullable(),
  delivery_outcome: z.string().optional().nullable(),
  health_check: z.string().optional().nullable(),
  json_exists: z.boolean().optional().nullable(),
  markdown_exists: z.boolean().optional().nullable(),
});

export const handoffReadinessChecksSchema = z.object({
  project_asset_backed: z.boolean().optional(),
  same_source_should_run: z.boolean().optional(),
  codex_ready: z.boolean().optional(),
  handoff_has_next_action: z.boolean().optional(),
  handoff_has_stop_condition: z.boolean().optional(),
  handoff_sanitized_surface: z.boolean().optional(),
}).catchall(z.boolean());

export const projectAssetHandoffReadinessSchema = z.object({
  ready: z.boolean().optional().default(false),
  codex_ready: z.boolean().optional().default(false),
  source: z.string().optional().nullable(),
  quota_state: z.string().optional().nullable(),
  checks: handoffReadinessChecksSchema.optional().default({}),
  handoff_status: z.string().optional().nullable(),
  handoff_ready_at: z.string().optional().nullable(),
  handoff_ready_classification: z.string().optional().nullable(),
  post_handoff_run_seen: z.boolean().optional().default(false),
  post_handoff_latest_run: postHandoffRunSchema.optional().nullable(),
  post_handoff_recent_runs: z.array(postHandoffRunSchema).optional().default([]),
  post_handoff_small_scale_streak: z.number().int().nonnegative().optional().default(0),
  post_handoff_outcome_gap_streak: z.number().int().nonnegative().optional().default(0),
  next_probe: z.string().optional().nullable(),
});

export const projectAssetTodoProjectionGapSchema = z.object({
  schema_version: z.string().optional().nullable(),
  kind: z.string().optional().nullable(),
  missing_roles: z.array(z.string()).optional().default([]),
  source: z.string().optional().nullable(),
  recommended_action: z.string().optional().nullable(),
});

export const projectAssetSchema = z.object({
  owner: z.string(),
  gate: z.string(),
  next_action: z.string(),
  stop_condition: z.string(),
  user_todos: projectAssetTodoSummarySchema.optional().nullable(),
  agent_todos: projectAssetTodoSummarySchema.optional().nullable(),
  quota: quotaSchema.optional().nullable(),
  control_plane: controlPlaneSchema.optional().nullable(),
  orchestration: orchestrationPolicySchema.optional().nullable(),
  latest_validation: projectAssetLatestValidationSchema.optional().nullable(),
  stale_latest_run_warning: staleLatestRunWarningSchema.optional().nullable(),
  todo_projection_gap: projectAssetTodoProjectionGapSchema.optional().nullable(),
});

export const queueItemSchema = z.object({
  goal_id: z.string(),
  activation_state: z.enum(["active", "stopped"]).optional().default("active"),
  status: z.string(),
  waiting_on: z.string(),
  severity: z.string(),
  recommended_action: z.string(),
  project_asset: projectAssetSchema.optional().nullable(),
  handoff_readiness: projectAssetHandoffReadinessSchema.optional().nullable(),
  source: z.string().optional(),
  operator_question: z.string().optional().nullable(),
  agent_command: z.string().optional().nullable(),
  lifecycle_phase: z.string().optional().nullable(),
  lifecycle_flags: z.array(z.string()).optional().default([]),
  controller_stage: z.string().optional().nullable(),
  missing_gates: z.array(z.string()).optional().default([]),
  next_handoff_condition: z.string().optional().nullable(),
  quota: quotaSchema.optional().nullable(),
  control_plane: controlPlaneSchema.optional().nullable(),
  user_todos: todoGroupSchema.optional().nullable(),
  agent_todos: todoGroupSchema.optional().nullable(),
  stale_latest_run_warning: staleLatestRunWarningSchema.optional().nullable(),
  dependency_blockers: dependencyBlockerSummarySchema.optional().nullable(),
  todo_state_file: z.string().optional().nullable(),
  goal_channel_projection: goalChannelProjectionSchema.optional().nullable(),
});

export const humanRewardSchema = z.object({
  recorded_at: z.string().optional().nullable(),
  decision: z.string().optional().nullable(),
  reward: z.string().optional().nullable(),
  reason_summary: z.string().optional().nullable(),
  follow_up: z.string().optional().nullable(),
});

export const operatorGateSchema = z.object({
  recorded_at: z.string().optional().nullable(),
  gate: z.string().optional().nullable(),
  decision: z.string().optional().nullable(),
  operator_question: z.string().optional().nullable(),
  reason_summary: z.string().optional().nullable(),
  follow_up: z.string().optional().nullable(),
  agent_command: z.string().optional().nullable(),
});

export const operatorGateResumeContractSchema = z.object({
  version: z.string().optional().nullable(),
  goal_id: z.string().optional().nullable(),
  run_id: z.string().optional().nullable(),
  gate_id: z.string().optional().nullable(),
  created_state_ref: z.string().optional().nullable(),
  created_policy_version: z.string().optional().nullable(),
  interrupt_payload: z.object({
    question: z.string().optional().nullable(),
    choices: z.array(z.string()).optional().default([]),
  }).optional().nullable(),
  allowed_decisions: z.array(z.string()).optional().default([]),
  operator_decision: z.string().optional().nullable(),
  latest_state_ref: z.string().optional().nullable(),
  freshness_check: z.string().optional().nullable(),
  precondition_check: z.string().optional().nullable(),
  migration_or_rebase_result: z.string().optional().nullable(),
  resulting_action: z.string().optional().nullable(),
  validation_after_resume: z.string().optional().nullable(),
});

export const controllerReadinessGateSchema = z.object({
  id: z.string().optional().nullable(),
  ok: z.boolean().optional().nullable(),
  review: z.string().optional().nullable(),
});

export const controllerReadinessSchema = z.object({
  classification: z.string().optional().nullable(),
  read_only_observer_ready: z.boolean().optional().nullable(),
  decision_advisor_ready: z.boolean().optional().nullable(),
  write_controller_ready: z.boolean().optional().nullable(),
  missing_gates: z.array(z.string()).optional().default([]),
  review_judgment: z.string().optional().nullable(),
  next_handoff_condition: z.string().optional().nullable(),
  gates: z.array(controllerReadinessGateSchema).optional().default([]),
});

export const authorityRegistrySchema = z.object({
  declared: z.boolean().optional().default(false),
  required: z.boolean().optional().default(false),
  path: z.string().optional().nullable(),
  path_exists: z.boolean().optional().nullable(),
  read_status: z.string().optional().nullable(),
  default_entry_count: z.number().optional().default(0),
  default_entries_checked: z.number().optional().default(0),
  default_entries_present: z.number().optional().default(0),
  topic_authority_count: z.number().optional().default(0),
  project_material_count: z.number().optional().default(0),
  project_material_repository_count: z.number().optional().default(0),
  project_material_owner_review_required_count: z.number().optional().default(0),
  project_material_stale_count: z.number().optional().default(0),
  project_material_current_authority_count: z.number().optional().default(0),
  deprecated_source_count: z.number().optional().default(0),
  conflict_risk: z.string().optional().nullable(),
});

export const projectMapSchema = z.object({
  adapter_kind: z.string().optional().nullable(),
  adapter_status: z.string().optional().nullable(),
  authority_source_count: z.number().optional().nullable(),
  authority_registry_declared: z.boolean().optional().nullable(),
  authority_registry_path_exists: z.boolean().optional().nullable(),
  authority_registry_default_entry_count: z.number().optional().nullable(),
  authority_registry_default_entries_present: z.number().optional().nullable(),
  topic_authority_count: z.number().optional().nullable(),
  project_material_count: z.number().optional().nullable(),
  project_material_repository_count: z.number().optional().nullable(),
  project_material_owner_review_required_count: z.number().optional().nullable(),
  project_material_stale_count: z.number().optional().nullable(),
  project_material_current_authority_count: z.number().optional().nullable(),
  authority_registry_conflict_risk: z.string().optional().nullable(),
  guard_count: z.number().optional().nullable(),
  sections_found: z.number().optional().nullable(),
  sections_checked: z.number().optional().nullable(),
  files_present: z.number().optional().nullable(),
  files_checked: z.number().optional().nullable(),
});

export const runRecordSchema = z.object({
  generated_at: z.string(),
  goal_id: z.string(),
  classification: z.string().optional().nullable(),
  lifecycle_phase: z.string().optional().nullable(),
  lifecycle_flags: z.array(z.string()).optional().default([]),
  recommended_action: z.string().optional().nullable(),
  health_check: z.string().optional().nullable(),
  active_task_count: z.number().optional().nullable(),
  active_priorities: z.record(z.string(), z.unknown()).optional().nullable(),
  cache_check: z.string().optional().nullable(),
  json_exists: z.boolean().optional().default(false),
  markdown_exists: z.boolean().optional().default(false),
  human_reward: humanRewardSchema.optional().nullable(),
  operator_gate: operatorGateSchema.optional().nullable(),
  operator_gate_resume_contract: operatorGateResumeContractSchema.optional().nullable(),
  controller_readiness: controllerReadinessSchema.optional().nullable(),
  project_map: projectMapSchema.optional().nullable(),
});

export const runGoalSchema = z.object({
  acceptance_observation: goalAcceptanceObservationSchema.optional().nullable().catch(null),
  id: z.string(),
  activation_state: z.enum(["active", "stopped"]).optional().default("active"),
  display_name: z.string().optional().nullable(),
  domain: z.string().optional().nullable(),
  status: z.string().optional().nullable(),
  lifecycle_phase: z.string().optional().nullable(),
  lifecycle_flags: z.array(z.string()).optional().default([]),
  registry_member: z.boolean().optional().default(false),
  legacy_runtime_goal: z.boolean().optional().default(false),
  adapter_kind: z.string().optional().nullable(),
  adapter_status: z.string().optional().nullable(),
  authority_registry: authorityRegistrySchema.optional().nullable(),
  quota: quotaSchema.optional().nullable(),
  control_plane: controlPlaneSchema.optional().nullable(),
  spawn_policy: orchestrationPolicySchema.optional().nullable(),
  orchestration: orchestrationPolicySchema.optional().nullable(),
  coordination: z.object({
    agent_model: z.string().optional().nullable(),
    registered_agents: z.array(z.string()).optional().default([]),
  }).optional().nullable(),
  index_exists: z.boolean().optional().default(false),
  raw_index_records: z.number().optional().default(0),
  unique_runs: z.number().optional().default(0),
  latest_runs: z.array(runRecordSchema).optional().default([]),
});

export const runHistorySchema = z.object({
  available: z.boolean(),
  goal_count: z.number().optional().default(0),
  run_count: z.number().optional().default(0),
  goals: z.array(runGoalSchema).optional().default([]),
  recent_runs: z.array(runRecordSchema).optional().default([]),
});

export const globalRegistryFindingSchema = z.object({
  kind: z.string(),
  severity: z.string(),
  message: z.string(),
  recommended_action: z.string(),
  goal_id: z.string().optional().nullable(),
  path: z.string().optional().nullable(),
  goal_ids: z.array(z.string()).optional().default([]),
});

export const globalRegistryHealthSchema = z.object({
  available: z.boolean(),
  ok: z.boolean(),
  registry: z.string(),
  current_registry: z.string().optional().nullable(),
  current_registry_is_global: z.boolean().optional().default(false),
  global_goal_count: z.number().optional().default(0),
  current_goal_count: z.number().optional().default(0),
  source_registry_count: z.number().optional().default(0),
  summary: z.object({
    high: z.number().optional().default(0),
    action: z.number().optional().default(0),
    info: z.number().optional().default(0),
    checks: z.number().optional().default(0),
    findings: z.number().optional().default(0),
  }),
  findings: z.array(globalRegistryFindingSchema).optional().default([]),
  checks: z.array(z.string()).optional().default([]),
});

export const usageTotalsSchema = z.object({
  runs_24h: z.number().optional().default(0),
  runs_7d: z.number().optional().default(0),
  quota_spend_slots_24h: z.number().optional().default(0),
  quota_spend_slots_7d: z.number().optional().default(0),
  automation_run_count_24h: z.number().optional().default(0),
  automation_run_count_7d: z.number().optional().default(0),
  progress_signal_run_count_24h: z.number().optional().default(0),
  progress_signal_run_count_7d: z.number().optional().default(0),
  // Measurement fields are absent when no sampled run reported them. A zero is
  // meaningful only after a runtime has actually measured that metric.
  input_tokens_24h: z.number().optional(),
  input_tokens_7d: z.number().optional(),
  output_tokens_24h: z.number().optional(),
  output_tokens_7d: z.number().optional(),
  cache_tokens_24h: z.number().optional(),
  cache_tokens_7d: z.number().optional(),
  cost_usd_24h: z.number().optional(),
  cost_usd_7d: z.number().optional(),
  duration_ms_24h: z.number().optional(),
  duration_ms_7d: z.number().optional(),
});

export const usageGoalSchema = usageTotalsSchema.extend({
  goal_id: z.string(),
  project_share_24h: z.number().optional().default(0),
});

const defaultUsageTotals = {
  runs_24h: 0,
  runs_7d: 0,
  quota_spend_slots_24h: 0,
  quota_spend_slots_7d: 0,
  automation_run_count_24h: 0,
  automation_run_count_7d: 0,
  progress_signal_run_count_24h: 0,
  progress_signal_run_count_7d: 0,
};

export const usageSummarySchema = z.object({
  available: z.boolean().optional().default(true),
  source: z.string().optional().default("run_history"),
  generated_at: z.string().optional().nullable(),
  sample_run_count: z.number().optional().default(0),
  proxy_note: z.string().optional().nullable(),
  totals: usageTotalsSchema.optional().default(defaultUsageTotals),
  goals: z.array(usageGoalSchema).optional().default([]),
}).optional().nullable();

export const eventLedgerClassCountsSchema = z.object({
  accounting: z.number().optional().default(0),
  decision: z.number().optional().default(0),
  evidence: z.number().optional().default(0),
  state: z.number().optional().default(0),
  work: z.number().optional().default(0),
});

const defaultEventLedgerClassCounts = {
  accounting: 0,
  decision: 0,
  evidence: 0,
  state: 0,
  work: 0,
};

export const eventLedgerTotalsSchema = z.object({
  events_24h: z.number().optional().default(0),
  events_7d: z.number().optional().default(0),
  by_class_24h: eventLedgerClassCountsSchema.optional().default(defaultEventLedgerClassCounts),
  by_class_7d: eventLedgerClassCountsSchema.optional().default(defaultEventLedgerClassCounts),
});

export const eventLedgerGoalSchema = eventLedgerTotalsSchema.extend({
  goal_id: z.string(),
  latest_event_class: z.string().optional().nullable(),
  latest_event_at: z.string().optional().nullable(),
});

const defaultEventLedgerTotals = {
  events_24h: 0,
  events_7d: 0,
  by_class_24h: defaultEventLedgerClassCounts,
  by_class_7d: defaultEventLedgerClassCounts,
};

export const eventLedgerSummarySchema = z.object({
  available: z.boolean().optional().default(true),
  source: z.string().optional().default("run_history"),
  generated_at: z.string().optional().nullable(),
  sample_run_count: z.number().optional().default(0),
  proxy_note: z.string().optional().nullable(),
  event_classes: z.array(z.string()).optional().default(["accounting", "decision", "evidence", "state", "work"]),
  totals: eventLedgerTotalsSchema.optional().default(defaultEventLedgerTotals),
  goals: z.array(eventLedgerGoalSchema).optional().default([]),
}).optional().nullable();

export const promotionReadinessSummarySchema = z.object({
  available: z.boolean().optional().default(false),
  source: z.string().optional().default("run_history"),
  goal_id: z.string().optional().nullable(),
  generated_at: z.string().optional().nullable(),
  classification: z.string().optional().nullable(),
  delivery_batch_scale: z.string().optional().nullable(),
  delivery_outcome: z.string().optional().nullable(),
  recommended_action: z.string().optional().nullable(),
  json_exists: z.boolean().optional().default(false),
  markdown_exists: z.boolean().optional().default(false),
  freshness_window_hours: z.number().optional().default(24),
  freshness_status: z.string().optional().nullable(),
  is_fresh: z.boolean().optional().default(false),
  requires_readiness_run: z.boolean().optional().default(true),
  age_seconds: z.number().optional().nullable(),
  age_hours: z.number().optional().nullable(),
  freshness_reference_time: z.string().optional().nullable(),
  sample_run_count: z.number().optional().default(0),
  proxy_note: z.string().optional().nullable(),
  reason: z.string().optional().nullable(),
}).optional().nullable();

export const promotionGateSchema = z.object({
  ok: z.boolean().optional().default(true),
  registry: z.string().optional().nullable(),
  runtime_root: z.string().optional().nullable(),
  gate: z.string().optional().default("promotion_readiness"),
  gate_state: z.string().optional().default("warning"),
  can_promote: z.boolean().optional().default(false),
  should_warn: z.boolean().optional().default(true),
  non_blocking: z.boolean().optional().default(true),
  recommended_action: z.string().optional().nullable(),
  warning_message: z.string().optional().nullable(),
  readiness: promotionReadinessSummarySchema.default(null),
}).optional().nullable();

export const decisionFreshnessSummaryCountsSchema = z.object({
  decision_count: z.number().optional().default(0),
  stale_count: z.number().optional().default(0),
  rebase_required_count: z.number().optional().default(0),
  fresh_count: z.number().optional().default(0),
});

export const decisionFreshnessItemSchema = z.object({
  goal_id: z.string(),
  decision_kind: z.string().optional().nullable(),
  decision_at: z.string().optional().nullable(),
  classification: z.string().optional().nullable(),
  age_days: z.number().optional().nullable(),
  stale_by_age: z.boolean().optional().default(false),
  newer_event_count_7d: z.number().optional().default(0),
  newer_event_classes_7d: eventLedgerClassCountsSchema.optional().default(defaultEventLedgerClassCounts),
  freshness_state: z.string().optional().nullable(),
  requires_decision_point_rebase: z.boolean().optional().default(false),
  reason: z.string().optional().nullable(),
});

export const decisionFreshnessSummarySchema = z.object({
  available: z.boolean().optional().default(true),
  source: z.string().optional().default("run_history"),
  generated_at: z.string().optional().nullable(),
  sample_run_count: z.number().optional().default(0),
  window_days: z.number().optional().default(7),
  proxy_note: z.string().optional().nullable(),
  summary: decisionFreshnessSummaryCountsSchema.optional().default({
    decision_count: 0,
    stale_count: 0,
    rebase_required_count: 0,
    fresh_count: 0,
  }),
  items: z.array(decisionFreshnessItemSchema).optional().default([]),
}).optional().nullable();

export const statusContractSchema = z.object({
  schema_version: z.number().optional().default(0),
  minimum_dashboard_schema_version: z.number().optional().default(2),
  producer: z.string().optional().nullable(),
  reload_hint: z.string().optional().nullable(),
}).optional().default({
  schema_version: 0,
  minimum_dashboard_schema_version: 2,
  producer: null,
  reload_hint: "scripts/macos-dashboard-launchagent.sh restart",
});

export const goalProjectionScopeSchema = z.object({
  schema_version: z.literal("loopx_goal_projection_scope_v0"),
  scope: z.enum(["all", "active", "stopped"]),
  complete: z.boolean(),
  projected_goal_count: z.number().int().nonnegative(),
  registry_goal_count: z.number().int().nonnegative(),
  // Stable fingerprint of the registry's goal activation partition. Two
  // scoped snapshots are safe to merge only when their revisions match.
  registry_revision: z.string().optional().nullable(),
});

export const localDashboardApiSchema = z.object({
  source: z.string().optional().default("serve-status"),
  status_url: z.string().optional().nullable(),
  health_url: z.string().optional().nullable(),
  review_material_url: z.string().optional().nullable(),
  presentation_surfaces_url: z.string().optional().nullable(),
  presentation_detail_url: z.string().optional().nullable(),
  periodic_report_index_url: z.string().optional().nullable(),
  periodic_report_detail_url: z.string().optional().nullable(),
  ssh_hosts_url: z.string().optional().nullable(),
  reward_dry_run_url: z.string().optional().nullable(),
  reward_append_url: z.string().optional().nullable(),
  reward_write_enabled: z.boolean().optional().default(false),
  configure_goal_dry_run_url: z.string().optional().nullable(),
  configure_goal_apply_url: z.string().optional().nullable(),
  control_plane_write_enabled: z.boolean().optional().default(false),
}).optional().nullable();

// Core keeps the presentation status contract provider-neutral. A ready/
// review_due item carries a compact, content-addressed `detail_ref` pointing at
// the published projection rather than inlining the provider-owned view. The
// finance decision-research view schema lives with the finance extension, not
// in this generic Core dashboard contract.
const presentationDetailRefSchema = z.object({
  extension_id: z.string().min(1),
  surface_id: z.string().regex(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/),
  extension_revision: z.string().min(1),
  payload_sha256: z.string().regex(/^[0-9a-f]{64}$/),
}).strict();

const presentationSurfaceBaseSchema = z.object({
  extension_id: z.string().min(1),
  extension_revision: z.string().min(1),
  surface_id: z.string().regex(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/),
  surface_kind: z.string().regex(/^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/),
  title: z.string().min(1),
  view_schema: z.string().regex(/^[a-z][a-z0-9_]*_v\d+$/),
  visibility: z.enum(["public-safe", "owner-only"]),
  goal_id: z.string().min(1).nullable(),
  generated_at: z.string().min(1).nullable(),
  review_due_at: z.string().min(1).nullable(),
  diagnostic: z.string().min(1).nullable(),
  empty_state_title: z.string().min(1),
  empty_state_detail: z.string().min(1),
});

const readyPresentationSurfaceSchema = presentationSurfaceBaseSchema.extend({
  state: z.enum(["ready", "review_due"]),
  goal_id: z.string().min(1),
  generated_at: z.string().min(1),
  detail_ref: presentationDetailRefSchema,
}).strict();

const emptyPresentationSurfaceSchema = presentationSurfaceBaseSchema.extend({
  state: z.literal("empty"),
  detail_ref: z.never().optional(),
}).strict();

const invalidPresentationSurfaceSchema = presentationSurfaceBaseSchema.extend({
  state: z.literal("invalid"),
  diagnostic: z.string().min(1),
  detail_ref: z.never().optional(),
}).strict();

export const presentationSurfaceSchema = z.union([
  readyPresentationSurfaceSchema,
  emptyPresentationSurfaceSchema,
  invalidPresentationSurfaceSchema,
]);

export const presentationSurfaceCollectionSchema = z.object({
  schema_version: z.literal("extension_presentation_surfaces_v0"),
  count: z.number().int().nonnegative(),
  ready_count: z.number().int().nonnegative(),
  review_due_count: z.number().int().nonnegative(),
  empty_count: z.number().int().nonnegative(),
  invalid_count: z.number().int().nonnegative(),
  items: z.array(presentationSurfaceSchema),
}).strict();

const emptyPresentationSurfaceCollection = {
  schema_version: "extension_presentation_surfaces_v0" as const,
  count: 0,
  ready_count: 0,
  review_due_count: 0,
  empty_count: 0,
  invalid_count: 0,
  items: [],
};

export const presentationSurfaceCollectionResponseSchema = z.object({
  ok: z.literal(true),
  presentation_surfaces: presentationSurfaceCollectionSchema,
}).strict();

export const periodicReportDetailRefSchema = z.object({
  goal_id: z.string().min(1),
  agent_id: z.string().min(1),
  generation_id: z.string().min(1),
  content_sha256: z.string().regex(/^sha256:[0-9a-f]{64}$/),
}).strict();

export const periodicReportIndexItemSchema = z.object({
  goal_id: z.string().min(1),
  agent_id: z.string().min(1),
  generation_id: z.string().min(1),
  publication_id: z.string().min(1),
  delivered_at: z.string().min(1),
  predecessor_publication_id: z.string().min(1).nullable().optional(),
  detail_ref: periodicReportDetailRefSchema,
}).strict();

const periodicReportIndexBaseSchema = z.object({
  schema_version: z.literal("periodic_report_workspace_index_v0"),
  count: z.number().int().nonnegative(),
  items: z.array(periodicReportIndexItemSchema),
});

const periodicReportWindowedIndexSchema = periodicReportIndexBaseSchema.extend({
  returned_count: z.number().int().nonnegative(),
  total_count: z.number().int().nonnegative(),
  limit: z.number().int().nonnegative(),
  offset: z.number().int().nonnegative(),
  truncated: z.boolean(),
}).strict();

const periodicReportLegacyIndexSchema = periodicReportIndexBaseSchema.strict().transform((value) => ({
  ...value,
  returned_count: value.count,
  total_count: value.count,
  limit: value.count,
  offset: 0,
  truncated: false,
}));

export const periodicReportIndexResponseSchema = z.object({
  ok: z.literal(true),
  periodic_reports: z.union([
    periodicReportWindowedIndexSchema,
    periodicReportLegacyIndexSchema,
  ]),
}).strict();

export const periodicReportProjectionSchema = z.object({
  schema_version: z.literal("periodic_report_workspace_projection_v0"),
  goal_id: z.string().min(1),
  agent_id: z.string().min(1),
  generation_id: z.string().min(1),
  generated_at: z.string().min(1),
  title: z.string().min(1),
  summary: z.string().min(1),
  content_sha256: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  period_window: z.object({
    start_at: z.string().min(1),
    end_at: z.string().min(1),
  }).strict(),
  interaction: z.object({
    attention_kind: z.literal("progress"),
    interaction: z.literal("inform"),
    delivery: z.literal("surface"),
    form: z.literal("milestone_report"),
    writable: z.literal(false),
  }).strict(),
  delta: z.object({
    added_count: z.number().int().nonnegative(),
    changed_count: z.number().int().nonnegative(),
    item_count: z.number().int().positive(),
    items: z.array(z.object({
      fact_id: z.string().min(1),
      source_ref: z.string().min(1),
      title: z.string().min(1),
      summary: z.string().min(1),
      status: z.string().min(1),
      content_kind: z.string().min(1),
      change_kind: z.enum(["added", "changed"]),
      previous_status: z.string().min(1).optional(),
    }).strict()),
  }).strict(),
  publication: z.object({
    publication_id: z.string().min(1),
    delivered_at: z.string().min(1),
    predecessor_publication_id: z.string().min(1).nullable().optional(),
    cursor_id: z.string().min(1),
  }).strict(),
  truth_contract: z.object({
    published_cursor_is_source_of_truth: z.literal(true),
    generation_receipt_is_delivery_receipt: z.literal(false),
    projection_is_writable: z.literal(false),
    browser_write_api: z.literal(false),
  }).strict(),
}).strict();

export const periodicReportProjectionResponseSchema = z.object({
  ok: z.literal(true),
  projection: periodicReportProjectionSchema,
}).strict();

export const statusPayloadSchema = z.object({
  ok: z.boolean(),
  registry: z.string(),
  runtime_root: z.string(),
  goal_count: z.number(),
  run_count: z.number(),
  status_contract: statusContractSchema,
  goal_projection: goalProjectionScopeSchema.optional().nullable().default(null),
  local_dashboard_api: localDashboardApiSchema,
  contract: z.object({
    ok: z.boolean(),
    summary: z.object({
      errors: z.number(),
      warnings: z.number(),
      checks: z.number(),
    }),
    errors: z.array(z.string()),
    warnings: z.array(z.string()),
    checks: z.array(z.string()).optional().default([]),
  }),
  global_registry: globalRegistryHealthSchema.optional().default({
    available: false,
    ok: true,
    registry: "",
    current_registry: null,
    current_registry_is_global: false,
    global_goal_count: 0,
    current_goal_count: 0,
    source_registry_count: 0,
    summary: {
      high: 0,
      action: 0,
      info: 0,
      checks: 0,
      findings: 0,
    },
    findings: [],
    checks: [],
  }),
  attention_queue: z.object({
    available: z.boolean(),
    item_count: z.number(),
    needs_user_or_controller: z.number(),
    needs_controller: z.number().optional().default(0),
    needs_codex: z.number(),
    watching_external_evidence: z.number(),
    autonomous_backlog_candidates: autonomousBacklogCandidateSummarySchema.optional().nullable(),
    items: z.array(queueItemSchema),
  }),
  run_history: runHistorySchema.optional().default({
    available: false,
    goal_count: 0,
    run_count: 0,
    goals: [],
    recent_runs: [],
  }),
  event_ledger_summary: eventLedgerSummarySchema.default(null),
  promotion_readiness_summary: promotionReadinessSummarySchema.default(null),
  promotion_gate: promotionGateSchema.default(null),
  decision_freshness_summary: decisionFreshnessSummarySchema.default(null),
  usage_summary: usageSummarySchema.default(null),
  todo_index: todoIndexSchema.optional().nullable().default(null),
  agent_management_projection: agentManagementProjectionSchema.optional().nullable().default(null),
  goal_channel_notification_projection: goalChannelNotificationProjectionSchema.optional().nullable().default(null),
  presentation_surfaces: presentationSurfaceCollectionSchema.optional().default(
    emptyPresentationSurfaceCollection,
  ),
});

export const rewardDryRunResponseSchema = z.object({
  ok: z.boolean(),
  dry_run: z.boolean().optional().default(true),
  appended: z.boolean().optional().default(false),
  goal_id: z.string().optional().nullable(),
  raw_index_records_before: z.number().optional().nullable(),
  preview_id: z.string().optional().nullable(),
  selected_run: z.object({
    generated_at: z.string().optional().nullable(),
    classification: z.string().optional().nullable(),
    recommended_action: z.string().optional().nullable(),
    json_exists: z.boolean().optional().nullable(),
    markdown_exists: z.boolean().optional().nullable(),
  }).optional().nullable(),
  human_reward: humanRewardSchema.optional().nullable(),
  active_state_summary: z.string().optional().nullable(),
  project_agent_visibility: z.object({
    source_of_truth: z.string().optional().nullable(),
    history_command: z.string().optional().nullable(),
    active_state_role: z.string().optional().nullable(),
    review_packet_role: z.string().optional().nullable(),
  }).optional().nullable(),
  error: z.string().optional().nullable(),
});

export type StatusPayload = z.infer<typeof statusPayloadSchema>;
export type PeriodicReportDetailRef = z.infer<typeof periodicReportDetailRefSchema>;
export type PeriodicReportProjection = z.infer<typeof periodicReportProjectionSchema>;
export type GoalActivationState = "active" | "stopped";
export type StatusContract = NonNullable<z.infer<typeof statusContractSchema>>;
export type GoalProjectionScope = z.infer<typeof goalProjectionScopeSchema>;
export type QueueItem = z.infer<typeof queueItemSchema>;
export type HumanReward = z.infer<typeof humanRewardSchema>;
export type OperatorGate = z.infer<typeof operatorGateSchema>;
export type OperatorGateResumeContract = z.infer<typeof operatorGateResumeContractSchema>;
export type ControllerReadiness = z.infer<typeof controllerReadinessSchema>;
export type AuthorityRegistry = z.infer<typeof authorityRegistrySchema>;
export type ComputeQuota = z.infer<typeof quotaSchema>;
export type ControlPlanePolicy = z.infer<typeof controlPlaneSchema>;
export type OrchestrationPolicy = z.infer<typeof orchestrationPolicySchema>;
export type ProjectAsset = z.infer<typeof projectAssetSchema>;
export type ProjectAssetTodoSummary = z.infer<typeof projectAssetTodoSummarySchema>;
export type ProjectAssetLatestValidation = z.infer<typeof projectAssetLatestValidationSchema>;
export type ProjectAssetHandoffReadiness = z.infer<typeof projectAssetHandoffReadinessSchema>;

export function withGoalActivationState(
  payload: StatusPayload,
  goalId: string,
  activationState: GoalActivationState,
): StatusPayload {
  const goals = payload.run_history.goals.map((goal) =>
    goal.id === goalId && goal.activation_state !== activationState
      ? { ...goal, activation_state: activationState }
      : goal
  );
  const queueItems = payload.attention_queue.items.map((item) =>
    item.goal_id === goalId && item.activation_state !== activationState
      ? { ...item, activation_state: activationState }
      : item
  );
  const goalsChanged = goals.some((goal, index) => goal !== payload.run_history.goals[index]);
  const queueChanged = queueItems.some((item, index) => item !== payload.attention_queue.items[index]);
  if (!goalsChanged && !queueChanged) return payload;
  return {
    ...payload,
    attention_queue: queueChanged
      ? { ...payload.attention_queue, items: queueItems }
      : payload.attention_queue,
    run_history: goalsChanged
      ? { ...payload.run_history, goals }
      : payload.run_history,
  };
}
export function withoutGoal(payload: StatusPayload, goalId: string): StatusPayload {
  const goals = payload.run_history.goals.filter((goal) => goal.id !== goalId);
  const queueItems = payload.attention_queue.items.filter((item) => item.goal_id !== goalId);
  if (goals.length === payload.run_history.goals.length && queueItems.length === payload.attention_queue.items.length) {
    return payload;
  }
  return {
    ...payload,
    attention_queue: { ...payload.attention_queue, items: queueItems },
    run_history: { ...payload.run_history, goals },
  };
}

export type TodoGroup = z.infer<typeof todoGroupSchema>;
export type TodoItem = z.infer<typeof todoItemSchema>;
export type TodoIndexItem = z.infer<typeof todoIndexItemSchema>;
export type TodoIndexSummary = z.infer<typeof todoIndexSchema>;
export type AgentManagementWorkspaceRef = z.infer<typeof agentManagementWorkspaceRefSchema>;
export type AgentManagementStaleClaimHint = z.infer<typeof agentManagementStaleClaimHintSchema>;
export type AgentManagementHandoffNote = z.infer<typeof agentManagementHandoffNoteSchema>;
export type AgentManagementProjection = z.infer<typeof agentManagementProjectionSchema>;
export type GoalChannelNotificationRow = z.infer<typeof goalChannelNotificationRowSchema>;
export type GoalChannelNotificationProjection = z.infer<typeof goalChannelNotificationProjectionSchema>;
export type ReviewMaterial = z.infer<typeof reviewMaterialSchema>;
export type ProjectMap = z.infer<typeof projectMapSchema>;
export type GlobalRegistryHealth = z.infer<typeof globalRegistryHealthSchema>;
export type EventLedgerSummary = NonNullable<z.infer<typeof eventLedgerSummarySchema>>;
export type PromotionReadinessSummary = NonNullable<z.infer<typeof promotionReadinessSummarySchema>>;
export type PromotionGate = NonNullable<z.infer<typeof promotionGateSchema>>;
export type DecisionFreshnessSummary = NonNullable<z.infer<typeof decisionFreshnessSummarySchema>>;
export type DecisionFreshnessItem = z.infer<typeof decisionFreshnessItemSchema>;
export type UsageSummary = NonNullable<z.infer<typeof usageSummarySchema>>;
export type PresentationSurface = z.infer<typeof presentationSurfaceSchema>;
export type PresentationSurfaceCollection = z.infer<typeof presentationSurfaceCollectionSchema>;

export function parsePresentationSurfaceCollectionResponse(
  payload: unknown,
): PresentationSurfaceCollection {
  return presentationSurfaceCollectionResponseSchema.parse(payload).presentation_surfaces;
}
export type RunGoal = z.infer<typeof runGoalSchema>;
export type RunRecord = z.infer<typeof runRecordSchema>;
export type RewardDryRunResponse = z.infer<typeof rewardDryRunResponseSchema>;

export function parseStatusPayload(payload: unknown): StatusPayload {
  return statusPayloadSchema.parse(payload);
}

export function parseRewardDryRunResponse(payload: unknown): RewardDryRunResponse {
  return rewardDryRunResponseSchema.parse(payload);
}

export function formatStatusError(error: unknown): string {
  if (error instanceof z.ZodError) {
    return error.issues.map((issue) => `${issue.path.join(".") || "root"}: ${issue.message}`).join("; ");
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export const exampleStatusPayload = parseStatusPayload(rawStatus);
