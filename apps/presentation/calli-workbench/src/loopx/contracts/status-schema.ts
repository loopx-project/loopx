/**
 * zod contract for `GET {statusBase}/status.json` — the subset this adapter reads.
 *
 * COPIED from loopx/apps/presentation/dashboard/src/data/status.ts (subset,
 * simplified). Sync duty: when the LoopX status contract changes
 * (`status_contract.schema_version` bump), re-diff against that file and update
 * here. Differences from the dashboard version, all made to keep the public
 * fixture (loopx/examples/status.example.json) parseable alongside live data:
 *  - every group is `.passthrough()` and almost every field optional, because
 *    the example fixture is a trimmed projection;
 *  - deep sub-contracts the adapter never reads are left as loose records.
 * The adapter's own gate on `status_contract.schema_version` lives in
 * `schema-guard.ts`, not here.
 */
import { z } from 'zod';

export const SUPPORTED_STATUS_SCHEMA_VERSION = 2;

export const statusContractSchema = z.object({
  schema_version: z.number().optional().default(0),
  minimum_dashboard_schema_version: z.number().optional().default(2),
  producer: z.string().optional().nullable(),
  reload_hint: z.string().optional().nullable(),
});

/** Loose row of a projected todo (goal_channel_projection / project_asset todo groups). */
export const projectedTodoRowSchema = z
  .object({
    todo_id: z.string().optional().nullable(),
    title: z.string().optional().nullable(),
    text: z.string().optional().nullable(),
    status: z.string().optional().nullable(),
    priority: z.string().optional().nullable(),
    role: z.string().optional().nullable(),
    claimed_by: z.string().optional().nullable(),
    action_kind: z.string().optional().nullable(),
    task_class: z.string().optional().nullable(),
    updated_at: z.string().optional().nullable(),
    completed_at: z.string().optional().nullable(),
    done: z.boolean().optional(),
    reason: z.string().optional().nullable(),
    evidence: z.string().optional().nullable(),
  })
  .passthrough();

export const goalChannelProjectionSchema = z
  .object({
    schema_version: z.string().optional().nullable(),
    goal_id: z.string().optional().nullable(),
    display_name: z.string().optional().nullable(),
    waiting_on: z.string().optional().nullable(),
    latest_status: z.string().optional().nullable(),
    next_action: z.string().optional().nullable(),
    decision_frame: z
      .object({
        user_action_required: z.boolean().optional().default(false),
        agent_action_required: z.boolean().optional().default(false),
        quiet_noop_allowed: z.boolean().optional().default(false),
      })
      .passthrough()
      .optional()
      .nullable(),
    user_todos: z.array(projectedTodoRowSchema).optional().default([]),
    agent_todos: z.array(projectedTodoRowSchema).optional().default([]),
  })
  .passthrough();

/** Loose todo group (attention item / project asset): enough to read blocked items. */
export const todoGroupLooseSchema = z
  .object({
    total_count: z.number().optional().default(0),
    open_count: z.number().optional().default(0),
    items: z.array(projectedTodoRowSchema).optional().default([]),
  })
  .passthrough();

/** Loose attention-queue item; deep sections stay records until the adapter reads them. */
export const queueItemSchema = z
  .object({
    goal_id: z.string(),
    activation_state: z.enum(['active', 'stopped']).optional().default('active'),
    status: z.string().optional().nullable(),
    waiting_on: z.string().optional().nullable(),
    severity: z.string().optional().nullable(),
    recommended_action: z.string().optional().nullable(),
    operator_question: z.string().optional().nullable(),
    agent_command: z.string().optional().nullable(),
    lifecycle_phase: z.string().optional().nullable(),
    lifecycle_flags: z.array(z.string()).optional().default([]),
    source: z.string().optional().nullable(),
    user_todos: todoGroupLooseSchema.nullable().optional(),
    agent_todos: todoGroupLooseSchema.nullable().optional(),
    goal_channel_projection: goalChannelProjectionSchema.optional().nullable(),
    handoff_readiness: z
      .object({
        ready: z.boolean().optional().default(false),
        handoff_status: z.string().optional().nullable(),
      })
      .passthrough()
      .optional()
      .nullable(),
  })
  .passthrough();

export const todoIndexItemSchema = z
  .object({
    index: z.number().optional().nullable(),
    done: z.boolean(),
    text: z.string(),
    todo_id: z.string().optional().nullable(),
    role: z.string().optional().nullable(),
    status: z.string().optional().nullable(),
    priority: z.string().optional().nullable(),
    title: z.string().optional().nullable(),
    archive_state: z.string().optional().nullable(),
    source_section: z.string().optional().nullable(),
    task_class: z.string().optional().nullable(),
    action_kind: z.string().optional().nullable(),
    claimed_by: z.string().optional().nullable(),
    updated_at: z.string().optional().nullable(),
    completed_at: z.string().optional().nullable(),
    goal_id: z.string().optional().nullable(),
  })
  .passthrough();

export const todoIndexSchema = z
  .object({
    schema_version: z.string().optional().nullable(),
    total_count: z.number().optional().default(0),
    items: z.array(todoIndexItemSchema).optional().default([]),
  })
  .passthrough();

export const runGoalSchema = z
  .object({
    id: z.string(),
    activation_state: z.enum(['active', 'stopped']).optional().default('active'),
    display_name: z.string().optional().nullable(),
    goal_objective: z.string().optional().nullable(),
    domain: z.string().optional().nullable(),
    status: z.string().optional().nullable(),
    lifecycle_phase: z.string().optional().nullable(),
    adapter_kind: z.string().optional().nullable(),
    adapter_status: z.string().optional().nullable(),
    latest_runs: z
      .array(
        z
          .object({
            generated_at: z.string(),
            classification: z.string().optional().nullable(),
            recommended_action: z.string().optional().nullable(),
            health_check: z.string().optional().nullable(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
  })
  .passthrough();

export const globalRegistryHealthSchema = z
  .object({
    available: z.boolean().optional().default(false),
    ok: z.boolean().optional().default(true),
    summary: z
      .object({
        high: z.number().optional().default(0),
        action: z.number().optional().default(0),
        info: z.number().optional().default(0),
        findings: z.number().optional().default(0),
      })
      .passthrough()
      .optional(),
    findings: z
      .array(
        z
          .object({
            kind: z.string().optional(),
            severity: z.string().optional(),
            message: z.string().optional(),
            goal_id: z.string().optional().nullable(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
  })
  .passthrough();

export const localDashboardApiSchema = z
  .object({
    source: z.string().optional(),
    status_url: z.string().optional().nullable(),
    health_url: z.string().optional().nullable(),
    ssh_hosts_url: z.string().optional().nullable(),
    runtime_identity: z
      .object({ package_version: z.string().optional().nullable() })
      .passthrough()
      .optional()
      .nullable(),
  })
  .passthrough();

export const statusPayloadSchema = z
  .object({
    ok: z.boolean(),
    registry: z.string().optional().default(''),
    runtime_root: z.string().optional().default(''),
    goal_count: z.number().optional().default(0),
    run_count: z.number().optional().default(0),
    status_contract: statusContractSchema.optional().default({
      schema_version: 0,
      minimum_dashboard_schema_version: 2,
      producer: null,
      reload_hint: null,
    }),
    contract: z
      .object({
        ok: z.boolean().optional().default(true),
        errors: z.array(z.string()).optional().default([]),
        warnings: z.array(z.string()).optional().default([]),
      })
      .passthrough()
      .optional(),
    contract_errors: z.array(z.string()).optional().default([]),
    contract_errors_total_count: z.number().optional().default(0),
    global_registry: globalRegistryHealthSchema.optional().nullable(),
    attention_queue: z
      .object({
        available: z.boolean().optional().default(false),
        item_count: z.number().optional().default(0),
        needs_user_or_controller: z.number().optional().default(0),
        needs_controller: z.number().optional().default(0),
        needs_codex: z.number().optional().default(0),
        watching_external_evidence: z.number().optional().default(0),
        watching_monitor: z.number().optional().default(0),
        items: z.array(queueItemSchema).optional().default([]),
      })
      .passthrough(),
    run_history: z
      .object({
        available: z.boolean().optional().default(false),
        goal_count: z.number().optional().default(0),
        run_count: z.number().optional().default(0),
        goals: z.array(runGoalSchema).optional().default([]),
      })
      .passthrough()
      .optional(),
    todo_index: todoIndexSchema.optional().nullable(),
    local_dashboard_api: localDashboardApiSchema.optional().nullable(),
  })
  .passthrough();

export type StatusPayload = z.infer<typeof statusPayloadSchema>;
export type QueueItem = z.infer<typeof queueItemSchema>;
export type RunGoal = z.infer<typeof runGoalSchema>;
export type TodoIndexItem = z.infer<typeof todoIndexItemSchema>;
export type ProjectedTodoRow = z.infer<typeof projectedTodoRowSchema>;
export type GoalChannelProjection = z.infer<typeof goalChannelProjectionSchema>;
