/**
 * zod contracts for the LoopX chat surface ({chatBase}/api/chat/*) and the
 * status service's /ssh-hosts catalog.
 *
 * No zod contract for these exists in the LoopX repo (dashboard/src/data/chat.ts
 * only types them). Shapes below were verified against the live local services
 * (127.0.0.1:8766, :8767) on 2026-11-14 and cross-checked with dashboard
 * `ChatSessionSummary`. Everything is `.passthrough()` so additive server fields
 * do not break the adapter; the literals that DO matter for compatibility
 * (schema_version strings) are pinned.
 */
import { z } from 'zod';

export const chatSessionSummarySchema = z
  .object({
    session_id: z.string(),
    goal_id: z.string().nullable(),
    agent_id: z.string(),
    adapter_kind: z.string().optional().default(''),
    status: z.string().optional().default('unknown'),
    agent_model_id: z.string().nullable().optional(),
    agent_reasoning_effort: z.string().nullable().optional(),
    active_turn_id: z.string().nullable().optional(),
    last_error_code: z.string().nullable().optional(),
    created_at: z.string().optional().default(''),
    updated_at: z.string().optional().default(''),
    last_activity_at: z.string().optional().default(''),
    transcript_revision: z.string().nullable().optional(),
    resumable: z.boolean().optional().default(false),
    session_mode: z.string().optional().nullable(),
    host_surface: z.string().optional().nullable(),
    executor_endpoint_id: z.string().optional().nullable(),
  })
  .passthrough();

export const chatSessionListSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('loopx_chat_session_list_v1'),
    sessions: z.array(chatSessionSummarySchema),
  })
  .passthrough();

/** One visible message of a session store snapshot. */
export const chatVisibleMessageSchema = z
  .object({
    message_id: z.string(),
    turn_id: z.string().nullable().optional(),
    role: z.string(),
    text: z.string().optional().default(''),
    created_at: z.string().optional().default(''),
  })
  .passthrough();

export const chatSessionSnapshotSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('loopx_chat_store_v1'),
    session: chatSessionSummarySchema,
    messages: z.array(chatVisibleMessageSchema).optional().default([]),
    active_turn: z.record(z.string(), z.unknown()).nullable().optional(),
  })
  .passthrough();

export const chatAdapterSchema = z
  .object({
    agent_id: z.string(),
    display_name: z.string().optional().default(''),
    adapter_kind: z.string().optional().default(''),
    available: z.boolean().optional().default(false),
    executable: z.string().optional().nullable(),
    environment_variable: z.string().optional().nullable(),
    unavailable_reason: z.string().optional().nullable(),
    detail: z.string().optional().nullable(),
    streaming: z.boolean().optional().default(false),
    resume: z.boolean().optional().default(false),
    interrupt: z.boolean().optional().default(false),
    tool_calls: z.boolean().optional().default(false),
    trust_scope: z.string().optional().nullable(),
    source: z.string().optional().nullable(),
  })
  .passthrough();

export const chatEndpointListSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('loopx_chat_endpoint_list_v1'),
    endpoints: z.array(chatAdapterSchema),
  })
  .passthrough();

export const chatCapabilitiesSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('loopx_chat_capabilities_v1'),
    agent_backend: z.string().optional().default(''),
    sandbox: z.string().optional().default(''),
    todo_write: z.string().optional().default(''),
    streaming: z.boolean().optional().default(false),
    resume: z.boolean().optional().default(false),
    interrupt: z.boolean().optional().default(false),
    manager: z
      .object({
        scope: z.string().optional().default(''),
        model: z.string().optional().nullable(),
        reasoning_effort: z.string().optional().nullable(),
      })
      .passthrough()
      .optional(),
    runtime_identity: z
      .object({ package_version: z.string().optional().nullable() })
      .passthrough()
      .optional()
      .nullable(),
    adapters: z.array(chatAdapterSchema).optional().default([]),
  })
  .passthrough();

export const chatGoalContextsSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('loopx_chat_goal_contexts_v0'),
    goals: z
      .array(
        z
          .object({
            goal_id: z.string(),
            repository: z
              .object({
                branch: z.string().optional().nullable(),
                identity: z.string().optional().nullable(),
                label: z.string().optional().nullable(),
                read_only: z.boolean().optional().nullable(),
              })
              .passthrough()
              .optional()
              .nullable(),
          })
          .passthrough(),
      )
      .optional()
      .default([]),
  })
  .passthrough();

export const chatTodoDetailSchema = z
  .object({
    ok: z.literal(true),
    goal_id: z.string(),
    todo_id: z.string(),
    text: z.string().optional().default(''),
    status: z.string().optional().nullable(),
    updated_at: z.string().optional().nullable(),
  })
  .passthrough();

export const sshHostCatalogSchema = z
  .object({
    ok: z.literal(true),
    schema_version: z.literal('ssh_host_catalog_v0'),
    hosts: z.array(z.object({ alias: z.string() }).passthrough()),
  })
  .passthrough();

export type ChatSessionSummary = z.infer<typeof chatSessionSummarySchema>;
export type ChatVisibleMessage = z.infer<typeof chatVisibleMessageSchema>;
export type ChatAdapter = z.infer<typeof chatAdapterSchema>;
export type ChatCapabilities = z.infer<typeof chatCapabilitiesSchema>;
export type ChatGoalContext = z.infer<typeof chatGoalContextsSchema>['goals'][number];
export type SshHostCatalog = z.infer<typeof sshHostCatalogSchema>;
