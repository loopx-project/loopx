import {normalizeWorkYardstickCapture} from "../../../../../loopx/control_plane/work_items/yardstick_decision.js";
import {sourceReadApiUrl} from "./local-status-query";
import {oracleEvidenceSchema, type OracleEvidence} from "./goal-acceptance-observation";
import { HANDOFF_MODES, EXECUTION_HANDOFF_MODES, type ExecutionHandoffMode } from "../../../../../loopx/control_plane/coordination/handoff_mode_vocabulary.js";
import { normalizeGoalDraft } from "../../../../../loopx/control_plane/collaboration/goal_draft.js";
import { parseTurnStep, type TurnStep } from "./turn-steps.js";
import { z } from "zod";
import {normalizeExploreMeasurements} from "../../../../../loopx/control_plane/capabilities/explore_measurements.js";
import { actionSourceBasisSchema, todoUpdateSourceBasisSchema } from "./action-source-basis.js";

import {
  todoApplyResultMatchesRequest,
  todoPreviewMatchesRequest,
  type AgentResponse,
  type ChatProject,
  type CollaborationReadback,
  type LoopXModeSettings,
  type TodoApplyResult,
  type TodoPreview,
} from "./chat-model.js";
import type {DelegationPreflight} from "./delegation-preflight.js";

const configuredChatOrigin = String(import.meta.env?.VITE_LOOPX_CHAT_ORIGIN ?? "")
  .trim()
  .replace(/\/+$/, "");

export type ConfigurationBackupResult = {
  ok: boolean; status?: string; goal_count: number; machine_configuration_present: boolean;
  sha256: string; checkpoint_ref?: string;
};

export function exportConfigurationBackup(goalIds?: string[]) {
  return requestJson<ConfigurationBackupResult & {backup: Record<string, unknown>}>("/api/chat/configuration-backup/export", {
    method: "POST", body: JSON.stringify(goalIds === undefined ? {} : {goal_ids: goalIds}),
  });
}

export function restoreConfigurationCheckpoint(backup: Record<string, unknown>, execute: boolean) {
  return requestJson<ConfigurationBackupResult>("/api/chat/configuration-backup/restore", {
    method: "POST", body: JSON.stringify({backup, expected_sha256: backup.sha256, execute}),
  });
}

function chatApiUrl(path: string) {
  if (!configuredChatOrigin || /^https?:\/\//.test(path)) {
    return path;
  }
  return new URL(path, `${configuredChatOrigin}/`).toString();
}

export {
  agentBackendLabel,
  answerLocalStatusQuestion,
  buildGoalStudioNodes,
  chatFailureMessage,
  completedGoalReviews,
  isTeamPlanPreviewProposal,
  isTodoProposal,
  pendingGoalReviews,
  proposalReviewState,
  sessionInvalidatedByPayload,
  selectChatAgent,
  selectChatGoal,
  stewardPrompts,
  turnReplaySafeByPayload,
  todoNoWriteReceiptFromPayload,
  todoNoWriteReceiptLabel,
  todoApplyResultMatchesRequest,
  todoPreviewMatchesRequest,
  todoReceiptLabel,
  todoReceiptOutcomeLabel,
  todoReceiptProjected,
} from "./chat-model.js";
export type {
  LoopXModeSettings,
  AgentResponse,
  ChatCapabilities,
  CollaborationReadback,
  ChatGoal,
  ChatStatus,
  ChatTodo,
  GoalStudioNode,
  ProposalDecisionOutcome,
  ProposalReviewState,
  StewardPrompt,
  TodoNoWriteReceipt,
  TodoPreview,
  TodoProposal,
  TodoApplyResult,
  TodoWriteReceipt,
} from "./chat-model.js";

export const chatTodoSchema = z.object({
  todo_id: z.string().nullable(),
  role: z.string().nullable(),
  status: z.string(),
  priority: z.string().nullable(),
  text: z.string(),
  action_kind: z.string().nullable(),
  task_class: z.string().nullable(),
  claimed_by: z.string().nullable(),
  evidence: z.string().nullable(),
});

export const chatGoalSchema = z.object({
  goal_id: z.string(),
  title: z.string(),
  objective: z.string(),
  status: z.string(),
  waiting_on: z.string().nullable(),
  severity: z.string().nullable(),
  gate: z.string(),
  next_action: z.string(),
  top_todo: chatTodoSchema.nullable(),
  todos: z.array(chatTodoSchema),
  evidence: z.array(z.string()),
  quota: z.object({
    state: z.string().nullable(),
    spent_slots: z.number().nullable(),
    allowed_slots: z.number().nullable(),
    reason: z.string().nullable(),
  }),
});

export const chatStatusSchema = z.object({
  ok: z.boolean(),
  schema_version: z.literal("loopx_chat_status_v0"),
  selected_goal_id: z.string().nullable(),
  goal_count: z.number(),
  goals: z.array(chatGoalSchema),
});

export const managerChannelBindingSchema = z.object({
  schema_version: z.string(),
  executor_endpoint: z.string(),
  executor_endpoint_source: z.string(),
  // Why the shipped default resolved the way it did. Present so a product
  // default reads as a decision with a reason instead of an incidental
  // environment value; empty when the operator selected the endpoint explicitly.
  executor_endpoint_default_reason: z.string().optional(),
  executor_kind: z.string(),
  model: z.string(),
  model_source: z.string(),
  reasoning_effort: z.string().optional(),
  selection_policy: z.enum(["preferred", "pinned", "flexible"]).default("preferred"),
  allocation_reason: z.string().default(""),
  configured_endpoint: z.string().nullable().optional(),
  eligible_endpoints: z.array(z.string()).default([]),
  allocation_configuration_revision: z.string().default(""),
  credential_env_var: z.string(),
  operator_credential_configured: z.boolean(),
  output_token_budget: z.object({
    schema_version: z.literal("dsh_output_token_budget_v0"),
    scope: z.literal("per_model_request"),
    max_tokens: z.number().int().positive().nullable(),
    valid: z.boolean(),
    source: z.enum(["product_default", "explicit_argument"]),
    final_response_reserve_supported: z.boolean(),
    hard_tool_budget_supported: z.boolean(),
  }).nullable().optional(),
  available: z.boolean().nullable(),
  unavailable_reason: z.string().nullable(),
});

export type ManagerChannelBinding = z.infer<typeof managerChannelBindingSchema>;

export const chatCapabilitiesSchema = z.object({
  ok: z.literal(true),
  schema_version: z.enum(["loopx_chat_capabilities_v0", "loopx_chat_capabilities_v1"]),
  agent_backend: z.string(),
  sandbox: z.string(),
  approval_policy: z.string(),
  todo_write: z.string(),
  goal_subagent_configuration: z.string().optional(),
  goal_id: z.string().nullable(),
  manager: z.object({
    scope: z.literal("owner_global"),
    model: z.string(),
    reasoning_effort: z.string(),
    channel_binding: managerChannelBindingSchema.optional(),
    runtime: z.object({
      schema_version: z.literal("manager_runtime_effective_profile_v0"),
      runtime_profile: z.enum(["restricted", "trusted_owner"]),
      source: z.string(),
      configuration_revision: z.string(),
      standing_grant: z.string(),
      sandbox: z.string(),
      approval_policy: z.string(),
      tool_classes: z.array(z.string()),
      status: z.string(),
      repair: z.string().optional(),
    }),
  }).optional(),
  streaming: z.boolean().optional(),
  resume: z.boolean().optional(),
  interrupt: z.boolean().optional(),
  typed_actions: z.boolean().optional(),
  action_kinds: z.array(z.string()).optional(),
  adapters: z.array(z.object({
    agent_id: z.string(),
    display_name: z.string(),
    adapter_kind: z.string(),
    available: z.boolean(),
    streaming: z.boolean(),
    resume: z.boolean(),
    interrupt: z.boolean(),
    location: z.string().optional(),
    source: z.string().optional(),
    tool_calls: z.boolean().optional(),
    trust_scope: z.string().optional(),
    // Readiness: why an unavailable built-in runtime is unavailable and what it needs.
    unavailable_reason: z.string().nullable().optional(),
    executable: z.string().nullable().optional(),
    environment_variable: z.string().nullable().optional(),
  })).optional(),
  lark_cli: z.object({
    available: z.boolean(),
    source: z.string(),
    version: z.string().nullable(),
    error_code: z.string().nullable(),
  }).optional(),
});

export const todoProposalSchema = z.object({
  kind: z.literal("todo"),
  goal_id: z.string().min(1).optional(),
  text: z.string(),
  priority: z.enum(["P0", "P1", "P2"]),
  rationale: z.string(),
});

/**
 * The steward's admitted team plan, carried beside todo proposals.
 *
 * The plan is validated by the host before it reaches this response, and the
 * card that confirms it is the typed `team.plan` action the manager channel
 * stores. This schema exists so a Turn that carries the preview still parses
 * here; it grants nothing and reads no lane into existence.
 */
export const teamPlanPreviewProposalSchema = z.object({
  kind: z.literal("steward_team_plan_preview"),
  preview: z.record(z.string(), z.unknown()),
});

export const agentProposalSchema = z.discriminatedUnion("kind", [
  todoProposalSchema,
  teamPlanPreviewProposalSchema,
]);

export const protectedActionProposalSchema = z.object({
  operation: z.enum(["merge", "release", "deploy", "delete", "payment"]),
  target: z.string().min(1).max(160),
  summary: z.string().max(300),
});

export type ProtectedActionProposal = z.infer<typeof protectedActionProposalSchema>;

export const agentResponseSchema = z.object({
  schema_version: z.literal("loopx_chat_agent_response_v0"),
  message: z.string(),
  goal_draft: z.unknown().optional().transform(normalizeGoalDraft),
  proposals: z.array(agentProposalSchema),
  protected_action: protectedActionProposalSchema.nullable().optional().default(null),
  gate: z
    .object({
      kind: z.string(),
      summary: z.string(),
      next_action: z.string(),
    })
    .nullable(),
});

export const chatSessionCloseSchema = z.object({
  closed: z.literal(true),
  ok: z.literal(true),
  session_id: z.string().min(1),
});

export const todoPreviewSchema = z.object({
  dry_run: z.literal(true),
  ok: z.literal(true),
  preview_id: z.string().min(1),
  todo: z.object({
    goal_id: z.string().min(1),
    text: z.string(),
    todo_id: z.string().optional(),
  }),
});

export const todoWriteReceiptSchema = z.object({
  schema_version: z.literal("loopx_chat_todo_receipt_v0"),
  receipt_id: z.string().min(1),
  preview_id: z.string().min(1),
  goal_id: z.string().min(1),
  todo_id: z.string().min(1),
  status: z.literal("applied"),
  outcome: z.enum(["todo_added", "todo_already_exists"]),
  already_exists: z.boolean(),
  preview_revision: z.string().nullable(),
});

export const todoApplyResultSchema = z.object({
  applied: z.literal(true),
  ok: z.literal(true),
  receipt: todoWriteReceiptSchema,
  todo: z.object({
    text: z.string(),
    todo_id: z.string(),
  }),
});

const goalSubagentOrchestrationSchema = z.object({
  model_config: z.object({ model: z.string(), reasoning_effort: z.string().optional() }).optional(),
  execution_config: z.string().optional(),

  mode: z.string(),
  spawn_allowed: z.boolean(),
  max_children: z.number().int().nonnegative(),
  allowed_domains: z.array(z.string()).optional().default([]),
}).passthrough();

const codexHostCapacitySchema = z.object({
  alignment_requested: z.boolean(),
  configured_children: z.number().int().positive().nullable(),
  counts_main_thread: z.literal(false),
  new_session_required: z.boolean().optional().default(false),
  required_children: z.number().int().nonnegative(),
  status: z.enum([
    "already_sufficient",
    "apply_failed",
    "explicit_shortfall",
    "explicit_sufficient",
    "implicit_default_unknown",
    "not_requested",
    "not_required",
    "updated",
  ]),
  write_required: z.boolean(),
  written: z.boolean().optional().default(false),
}).passthrough();

export const goalSubagentConfigurationResultSchema = z.object({
  ok: z.literal(true),
  dry_run: z.boolean(),
  execute: z.boolean(),
  written: z.boolean(),
  changed: z.boolean(),
  goal_id: z.string().min(1),
  changed_fields: z.array(z.string()),
  before: z.object({ orchestration: goalSubagentOrchestrationSchema }).passthrough(),
  after: z.object({ orchestration: goalSubagentOrchestrationSchema }).passthrough(),
  preview_id: z.string().min(1),
  feature_summary: z.object({ multi_subagent: z.enum(["off", "enabled"]) }).passthrough(),
  goal_configuration_changed: z.boolean(),
  codex_host_capacity: codexHostCapacitySchema,
  global_sync: z.object({
    required: z.boolean(),
    executed: z.boolean(),
    readback: z.object({
      status: z.string(),
      verified: z.boolean(),
    }).passthrough(),
  }).passthrough(),
});

export type GoalSubagentConfigurationResult = z.infer<typeof goalSubagentConfigurationResultSchema>;
export type CodexHostCapacity = z.infer<typeof codexHostCapacitySchema>;

export type GoalSubagentConfigurationRequest = {
  alignCodexHostCapacity?: boolean;
  modelConfig?: { model: string; reasoning_effort?: string } | null;
  executionConfig?: string;
  allowedDomains: string[];
  enabled: boolean;
  goalId: string;
  maxChildren: number;
};

export const storedDecisionHistoryItemSchema = z
  .object({
    id: z.string().min(1),
    outcome: z.enum(["approved", "rejected", "cancelled"]),
    projectionVerified: z.boolean().nullable(),
    proposal: todoProposalSchema,
    receipt: todoWriteReceiptSchema.nullable(),
  })
  .superRefine((item, context) => {
    if (item.outcome === "approved" && !item.receipt) {
      context.addIssue({
        code: "custom",
        message: "approved decision history requires a Todo receipt",
        path: ["receipt"],
      });
    }
    if (item.outcome !== "approved" && item.receipt) {
      context.addIssue({
        code: "custom",
        message: "zero-write decision history must not include a Todo receipt",
        path: ["receipt"],
      });
    }
  });

export const storedDecisionHistorySchema = z.object({
  schema_version: z.literal("loopx_chat_decision_history_v0"),
  goal_id: z.string().min(1),
  decisions: z.array(storedDecisionHistoryItemSchema).max(24),
});

export type StoredDecisionHistoryItem = z.infer<typeof storedDecisionHistoryItemSchema>;

export class ChatApiError extends Error {
  payload: Record<string, unknown>;

  constructor(message: string, payload: Record<string, unknown>) {
    super(message);
    this.payload = payload;
  }
}

export const typedActionKindSchema = z.enum([
  "goal.acceptance.check",
  "goal.create",
  "goal.update",
  "goal.lifecycle",
  "todo.create",
  "todo.update",
  "agent.bind",
  "heartbeat.bind",
  "monitor.create",
  "monitor.update",
  "gate.resolve",
  "run.correct",
  "operation.execute",
  // The steward's team intake: one validated multi-lane preview that the owner
  // confirms. The apply re-validates the same payload before creating work.
  "team.plan",
]);

const typedOperationEnvelopeSchema = z.object({
  schema_version: z.literal("loopx_operation_envelope_v0"),
  lifecycle_state: z.enum([
    "prepared",
    "awaiting_confirmation",
    "claimed",
    "outcome_observed",
  ]),
  operation_id: z.string().min(1),
  confirmation_digest: z.string().min(1),
  payload_digest: z.string().min(1),
  projection_digest: z.string().min(1),
  expires_at: z.string().min(1),
  delivery: z.record(z.string(), z.unknown()).nullable(),
  confirmation: z.record(z.string(), z.unknown()).nullable(),
  claim: z.record(z.string(), z.unknown()).nullable(),
  outcome: z.record(z.string(), z.unknown()).nullable(),
  result_delivery: z.record(z.string(), z.unknown()).nullable().optional(),
}).passthrough();

export const typedActionProposalSchema = z.object({
  schema_version: z.literal("loopx_chat_action_proposal_v1"),
  idempotency_key: z.string().optional(),
  proposal_id: z.string().min(1),
  action_kind: typedActionKindSchema,
  summary: z.string().min(1),
  normalized_parameters: z.record(z.string(), z.unknown()),
  context: z.record(z.string(), z.unknown()),
  expected_state_fingerprint: z.string().min(1),
  permission_classification: z.string().min(1),
  // ChatActionService emits textual validation facts for every action kind.
  validation_evidence: z.array(z.string().refine((value) => value.trim().length > 0, "Validation evidence must be non-blank text")),
  available_transitions: z.array(z.enum(["apply", "cancel", "regenerate", "reject", "defer"])),
  status: z.enum(["preview_ready", "applying", "gated", "failed", "rejected", "deferred", "cancelled", "stale", "applied"]),
  receipt: z.record(z.string(), z.unknown()).nullable(),
  stale: z.record(z.string(), z.unknown()).nullable(),
  gate: z.record(z.string(), z.unknown()).nullable().optional(),
  error: z.record(z.string(), z.unknown()).nullable().optional(),
  checkpoint: z.record(z.string(), z.unknown()).nullable().optional(),
  failure: z.record(z.string(), z.unknown()).nullable().optional(),
  canonical_update_basis: actionSourceBasisSchema.optional(),
  // The workspace a Goal creation resolved to, named for the owner's review.
  workspace_binding: z.object({
    schema_version: z.literal("loopx_goal_workspace_binding_v0"),
    workspace_ref: z.string().min(1),
    label: z.string().min(1),
    source: z.enum(["goal_workspace", "configured_workspace"]),
    selection: z.enum(["default", "explicit"]),
    goal_id: z.string().min(1).optional(),
  }).optional(),
  regenerated_from: z.string().nullable().optional(),
  operation: typedOperationEnvelopeSchema.nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type TypedActionKind = z.infer<typeof typedActionKindSchema>;
export type TypedActionProposal = z.infer<typeof typedActionProposalSchema>;

export type TypedActionPreviewRequest = {
  actionKind: TypedActionKind;
  context: Record<string, unknown>;
  idempotencyKey: string;
  normalizedParameters: Record<string, unknown>;
  summary: string;
};

const typedActionEnvelopeSchema = z.object({
  ok: z.literal(true),
  proposal: typedActionProposalSchema,
});

export async function previewTypedAction(request: TypedActionPreviewRequest) {
  const payload = await requestJson<unknown>("/api/actions/preview", {
    method: "POST",
    body: JSON.stringify({
      action_kind: request.actionKind,
      context: request.context,
      idempotency_key: request.idempotencyKey,
      normalized_parameters: request.normalizedParameters,
      summary: request.summary,
    }),
  });
  return typedActionEnvelopeSchema.parse(payload).proposal;
}

export async function loadTypedAction(proposalId: string, signal?: AbortSignal) {
  const proposal = typedActionEnvelopeSchema.parse(
    await requestJson<unknown>(`/api/actions/${encodeURIComponent(proposalId)}`, { signal }),
  ).proposal;
  if (proposal.proposal_id !== proposalId) throw new Error("Proposal readback identity mismatch");
  return proposal;
}

const typedActionListEnvelopeSchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("loopx_chat_action_list_v1"),
  proposals: z.array(typedActionProposalSchema),
});

export async function listTypedActions(filters: { contextKind?: string; goalId?: string } = {}, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.contextKind) query.set("context_kind", filters.contextKind);
  if (filters.goalId) query.set("goal_id", filters.goalId);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return typedActionListEnvelopeSchema.parse(
    await requestJson<unknown>(`/api/actions${suffix}`, { signal }),
  ).proposals;
}

export async function applyTypedAction(proposalId: string) {
  const payload = await requestJson<unknown>(
    `/api/actions/${encodeURIComponent(proposalId)}/apply`,
    { method: "POST", body: "{}" },
  );
  return z.object({
    ok: z.literal(true),
    proposal: typedActionProposalSchema,
    turn: z.record(z.string(), z.unknown()).nullable().optional(),
  }).parse(payload);
}

export async function cancelTypedAction(proposalId: string) {
  return typedActionEnvelopeSchema.parse(
    await requestJson<unknown>(`/api/actions/${encodeURIComponent(proposalId)}/cancel`, {
      method: "POST",
      body: "{}",
    }),
  ).proposal;
}

export async function transitionTypedAction(
  proposalId: string,
  transition: "regenerate" | "reject" | "defer",
) {
  return typedActionEnvelopeSchema.parse(
    await requestJson<unknown>(`/api/actions/${encodeURIComponent(proposalId)}/${transition}`, {
      method: "POST",
      body: "{}",
    }),
  ).proposal;
}

export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(chatApiUrl(url), {
      cache: "no-store",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch {
    throw new ChatApiError(
      "无法连接 LoopX Chat 服务。请确认 Dashboard 与 Chat 服务已启动且来自同一版本。",
      { error_code: "chat_api_unavailable" },
    );
  }
  let responseText: string;
  try {
    responseText = await response.text();
  } catch {
    throw new ChatApiError(
      "LoopX Chat 服务响应中断。请重试当前操作。",
      {
        error_code: "chat_api_unavailable",
        http_status: response.status,
      },
    );
  }
  let parsedPayload: unknown = null;
  if (responseText.trim()) {
    try {
      parsedPayload = JSON.parse(responseText);
    } catch {
      parsedPayload = null;
    }
  }
  const payload = parsedPayload && typeof parsedPayload === "object" && !Array.isArray(parsedPayload)
    ? parsedPayload as Record<string, unknown>
    : {};
  if (!response.ok) {
    const proposal = payload.proposal && typeof payload.proposal === "object"
      ? payload.proposal as Record<string, unknown>
      : null;
    const staleMessage = proposal?.status === "stale"
      ? "来源状态已变化，请重新生成预览。"
      : null;
    const serviceMessage = response.status >= 500
      ? `LoopX Chat 服务暂时不可用（HTTP ${response.status}）。请确认 Dashboard 与 Chat 服务已启动且来自同一版本。`
      : `LoopX Chat 请求失败（HTTP ${response.status}）。`;
    throw new ChatApiError(
      staleMessage ?? String(payload.error || serviceMessage),
      Object.keys(payload).length
        ? { ...payload, http_status: response.status }
        : {
            error_code: "chat_api_unavailable",
            http_status: response.status,
          },
    );
  }
  if (parsedPayload === null) {
    throw new ChatApiError(
      `LoopX Chat 服务返回了无法识别的响应（HTTP ${response.status}）。请确认 Dashboard 与 Chat 服务来自同一版本。`,
      { error_code: "invalid_chat_api_response", http_status: response.status },
    );
  }
  return parsedPayload as T;
}

export async function readTodoRequest(goalId: string, todoId: string, signal: AbortSignal, sourceNamespace?: string, readSourceUrl?: string) {
  const query = new URLSearchParams({goal_id: goalId, todo_id: todoId});
  if (sourceNamespace) query.set("source_namespace", sourceNamespace);
  const url = sourceReadApiUrl(readSourceUrl, `/api/chat/todo/detail?${query}`);
  const expectedNamespace = readSourceUrl ? new URL(url).searchParams.get("source_namespace") : sourceNamespace;
  const result = z.object({
    ok: z.literal(true), goal_id: z.string(), todo_id: z.string(), text: z.string(),
    status: z.string(), archive_state: z.string(), updated_at: z.string().nullable(),
    evidence: z.string().nullable().optional(), note: z.string().nullable().optional(),
    review_basis: todoUpdateSourceBasisSchema.optional(),
    source_access: z.object({namespace: z.string()}).optional(),
  }).parse(await requestJson<unknown>(url, {signal}));
  if (result.goal_id !== goalId || result.todo_id !== todoId) throw new Error("Task source changed");
  if (expectedNamespace && result.source_access?.namespace !== expectedNamespace) throw new Error("Task source changed");
  return result;
}

export async function fetchChatStatus() {
  return chatStatusSchema.parse(await requestJson<unknown>("/status.json"));
}

export async function fetchChatCapabilities() {
  return chatCapabilitiesSchema.parse(await requestJson<unknown>("/api/chat/capabilities"));
}

export async function recordProjectionExchange(options: {
  answer: string;
  contextKind: "goal" | "manager";
  goalId?: string;
  question: string;
}) {
  return requestJson<{
    ok: true; schema_version: "loopx_chat_projection_exchange_v1";
    session_id: string; user_message_id: string; answer_message_id: string;
  }>(
    "/api/chat/projection-messages",
    {
      method: "POST",
      body: JSON.stringify({
        answer: options.answer,
        context_kind: options.contextKind,
        goal_id: options.goalId,
        question: options.question,
      }),
    },
  );
}

/** The App's conversation targets; each owns exactly one Core Chat channel. */
export type ConversationContext =
  | { kind: "manager" }
  | { kind: "goal"; goalId: string }
  | { kind: "project"; projectRef: string }
  | ({ kind: "managed" } & ManagedConversationTarget)
  // A UI address for an existing source-owned Session, never a new Core channel.
  | ({ kind: "bound" } & BoundConversationTarget);

export type BoundConversationTarget = { bindingId: string; sessionId: string };
export type ManagedConversationTarget = {goalId: string; agentId: string; sessionId: string};

/** Goal projections cannot answer a source-owned or ordinary workspace Chat. */
export function conversationSupportsStatusProjection(context: ConversationContext) {
  return context.kind === "manager" || context.kind === "goal";
}

// Goal ids are single path segments, so a key containing "/" never names a Goal.
const PROJECT_CONVERSATION_KEY_PREFIX = "project/";
const MANAGER_GOAL_CONVERSATION_KEY = "goal/manager";

export function conversationContextKey(context: ConversationContext): string {
  if (context.kind === "manager") return "manager";
  if (context.kind === "managed") return `managed/${encodeURIComponent(context.goalId)}/${encodeURIComponent(context.agentId)}/${encodeURIComponent(context.sessionId)}`;
  if (context.kind === "bound") return `bound/${encodeURIComponent(context.bindingId)}/${encodeURIComponent(context.sessionId)}`;
  if (context.kind === "goal") return context.goalId === "manager" ? MANAGER_GOAL_CONVERSATION_KEY : context.goalId;
  return `${PROJECT_CONVERSATION_KEY_PREFIX}${context.projectRef}`;
}

export function conversationContextOfKey(key: string): ConversationContext {
  if (key === "manager") return { kind: "manager" };
  if (key === MANAGER_GOAL_CONVERSATION_KEY) return { kind: "goal", goalId: "manager" };
  if (key.startsWith("managed/")) {
    const [, goalId = "", agentId = "", sessionId = ""] = key.split("/");
    return {kind: "managed", goalId: decodeURIComponent(goalId), agentId: decodeURIComponent(agentId), sessionId: decodeURIComponent(sessionId)};
  }
  if (key.startsWith("bound/")) {
    const [, bindingId = "", sessionId = ""] = key.split("/");
    return { kind: "bound", bindingId: decodeURIComponent(bindingId), sessionId: decodeURIComponent(sessionId) };
  }
  if (key.startsWith(PROJECT_CONVERSATION_KEY_PREFIX)) {
    return { kind: "project", projectRef: key.slice(PROJECT_CONVERSATION_KEY_PREFIX.length) };
  }
  return { kind: "goal", goalId: key };
}

export function conversationChannelId(context: ConversationContext): string {
  if (context.kind === "manager") return "manager";
  if (context.kind === "managed") return `agent.${context.agentId}`;
  if (context.kind === "bound") return conversationContextKey(context);
  return context.kind === "goal" ? `goal.${context.goalId}` : `project.${context.projectRef}`;
}

export async function createChatSession(
  context: ConversationContext,
  agentId?: string,
  mode: "resume_latest" | "new" = "resume_latest",
  signal?: AbortSignal,
  agentModelId?: string,
  agentReasoningEffort?: string,
) {
  if (context.kind === "managed") {
    if (mode !== "resume_latest" || agentModelId || agentReasoningEffort) {
      throw new Error("An original Agent Session cannot be replaced or reconfigured here.");
    }
    const session = await resolveManagedChatSession(context, signal);
    if (!session.resumable || (agentId && agentId !== session.agent_id)) throw new Error("The original managed Session is unavailable.");
    return {ok: true as const, resumed: true, session_id: session.session_id, agent_id: session.agent_id, goal_id: session.goal_id, session};
  }
  if (context.kind === "bound") {
    if (mode !== "resume_latest" || agentModelId || agentReasoningEffort) {
      throw new Error("An original conversation cannot be replaced or reconfigured here.");
    }
    const session = await resolveBoundChatSession(context, signal);
    if (!session.resumable || (agentId && agentId !== session.agent_id)) {
      throw new Error("The original conversation is unavailable on its executor.");
    }
    return { ok: true as const, resumed: true, session_id: session.session_id,
      agent_id: session.agent_id, goal_id: session.goal_id, session };
  }
  return requestJson<{
    agent_id: string;
    goal_id: string | null;
    ok: true;
    resumed: boolean;
    session_id: string;
    session: ChatSessionSummary;
  }>("/api/chat/sessions", {
    method: "POST", signal,
    // An omitted ``agent_id`` means "no explicit executor pick": the channel
    // owner resolves its own default. Sending this client's own default would
    // silently re-point the steward channel away from its configured executor.
    // A project Session carries only the host-issued reference, never a Goal.
    body: JSON.stringify(context.kind === "project"
      ? { context_kind: "project", project_ref: context.projectRef, agent_id: agentId, agent_model_id: agentModelId, agent_reasoning_effort: agentReasoningEffort, mode }
      : { goal_id: context.kind === "goal" ? context.goalId : "", agent_id: agentId, agent_model_id: agentModelId, agent_reasoning_effort: agentReasoningEffort, mode, context_kind: context.kind }),
  });
}

export type AgentModelOption = { value: string; label: string; reasoning_efforts?: string[] };
export function configureChatSessionModel(sessionId: string, model: string, effort?: string) {
  return requestJson<{ok: true; session: ChatSessionSummary}>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/model-settings`, {
    method: "POST", body: JSON.stringify({agent_model_id: model, ...(effort ? {agent_reasoning_effort: effort} : {})}),
  });
}
export type AgentModelDiscovery = {
  ok: true;
  schema_version: "loopx_chat_agent_model_options_v1";
  agent_id: string;
  supported: boolean;
  model: { id: string; label: string; selected: string; options: AgentModelOption[] } | null;
  reasoning_effort?: { id: string; label: string; selected: string; options: AgentModelOption[] } | null;
};

export function discoverAgentModelOptions(agentId: string, signal?: AbortSignal, goalId?: string, modelId?: string) {
  return requestJson<AgentModelDiscovery>("/api/chat/agent-model-options", {
    method: "POST", signal, body: JSON.stringify({agent_id: agentId, ...(goalId ? {goal_id: goalId} : {}), ...(modelId ? {agent_model_id: modelId} : {})}),
  });
}

export type ChatStreamEvent = {
  event_id: string;
  sequence: number;
  kind: string;
  created_at: string;
  payload: Record<string, unknown>;
};

export type ChatSessionSummary = {
  session_id: string;
  goal_id: string | null;
  project_ref?: string | null;
  agent_id: string;
  managed_agent_id?: string;
  goal_instance_id?: string | null;
  agent_model_id?: string | null;
  agent_reasoning_effort?: string | null;
  agent_service_tier?: string | null;
  requested_agent_model_id?: string | null;
  requested_agent_reasoning_effort?: string | null;
  model_settings_supported?: boolean;
  adapter_kind: string;
  channel_id?: string;
  status: string;
  active_turn_id: string | null;
  last_error_code: string | null;
  created_at: string;
  updated_at: string;
  /** Opaque transcript read hint, independent of execution updated_at. */
  transcript_revision?: string | null;
  last_activity_at: string;
  resumable: boolean;
  session_mode?: ChatSessionMode;
  host_surface?: string | null;
  manager_runtime?: ManagerRuntimeSessionReadback | null;
};

export type { ChatProject };

const chatProjectsSchema = z.object({
  ok: z.literal(true),
  projects: z.array(z.object({
    project_ref: z.string().min(1), title: z.string(),
    grant: z.enum(["workspace_read", "workspace_write"]),
    goal_workspace_ref: z.string().optional(),
  })),
});

export async function fetchChatProjects(signal?: AbortSignal) {
  const parsed = chatProjectsSchema.safeParse(await requestJson<unknown>("/api/chat/projects", {signal}));
  if (!parsed.success) throw new Error("The workspace list could not be read. Refresh to retry.");
  return parsed.data;
}

/** ``chat_store`` Session modes; an omitted mode is a managed runtime Session. */
export type ChatSessionMode = "managed_runtime" | "attached_host";

/**
 * Whether the Chat service queues a message sent while this Session's Turn
 * runs, per ``ChatRuntimeController.submit_turn``: an attached host Session
 * enqueues bounded follow-ups behind the host's Turn, while a managed runtime
 * Session admits one Turn at a time and answers 409 with ``active_turn_id``.
 */
export function chatSessionQueuesFollowUps(session: Pick<ChatSessionSummary, "session_mode">) {
  return session.session_mode === "attached_host";
}

/** Native steering is offered only by the managed Codex adapter; attached follow-ups keep their queue contract. */
export function chatSessionSupportsSteering(session: Pick<ChatSessionSummary, "session_mode" | "adapter_kind">) {
  return session.session_mode !== "attached_host" && session.adapter_kind === "codex_app_server";
}

export type ManagerRuntimeSessionReadback = {
  schema_version: "manager_runtime_session_readback_v0";
  runtime_profile: "restricted" | "trusted_owner";
  configuration_revision: string;
  status: string;
  sandbox: string;
  standing_grant: string;
  tool_classes: string[];
};

export type ChatVisibleMessage = {
  queue_input_revision?: number;
  queue_input_cancelled?: true;
  goal_draft?: ReturnType<typeof normalizeGoalDraft>;
  /** Client-side lineage added when messages from several Sessions are merged. */
  session_id?: string;
  collaboration?: CollaborationReadback;
  collaborations?: CollaborationReadback[];
  origin?: string;
  attachments?: ChatImageAttachment[];
  /** Read-only original-source view; text remains the canonical model input. */
  reply_context?: ChatReplyContext;
  message_id: string;
  turn_id: string | null;
  role: string;
  text: string;
  created_at: string;
  return_delivery?: {
    schema_version: "manager_return_delivery_status_v0";
    phase: "decision" | "conclusion";
    request_revision?: number | null;
    status: string;
    created_at?: string | null;
    delivered_at?: string | null;
    error?: string | null;
    verification?: "reconciled_after_restart";
  };
};

export type ChatReplyContext = {
  source_text: string;
  parent: {message_id: string; content: string; complete: boolean};
  thread?: {
    root_message_id: string; thread_id: string; before_message_id: string;
    omitted_message_count: number; complete: false;
    messages: Array<{message_id: string; content: string; content_truncated: boolean;
      position: number; sender?: {id: string; kind: string}; created_at?: string}>;
  };
};

// Existing attachment wire shape; MIME distinguishes images and UTF-8 documents.
export type ChatImageAttachment = {
  data_url: string;
  id: string;
  mime_type: string;
  name: string;
  size: number;
};

export type ChatImageAttachmentInput = {
  dataUrl: string;
  id: string;
  mimeType: string;
  name: string;
  size: number;
};

export type ChatSessionSnapshot = {
  ok: true;
  schema_version: "loopx_chat_store_v1";
  session: ChatSessionSummary;
  messages: ChatVisibleMessage[];
  active_turn: Record<string, unknown> | null;
  selected_turn?: {turn_id: string; status: string; terminal: boolean; error?: string};
  // Derived from one complete, verified read. Never persisted or an authority grant.
  verifiedHistoryBasis?: string;
  message_page?: {
    schema_version: "loopx_chat_message_page_v1";
    offset: number; total: number; snapshot_sha256: string; next_cursor: string | null;
  };
};

const chatReplyContextSchema = z.object({
  source_text: z.string(),
  parent: z.object({message_id: z.string(), content: z.string(), complete: z.boolean()}).passthrough(),
  thread: z.object({
    omitted_message_count: z.number().nullish(),
    messages: z.array(z.object({message_id: z.string(), content: z.string(), content_truncated: z.boolean(),
      sender: z.object({kind: z.string()}).passthrough().nullish(), created_at: z.string().nullish()}).passthrough()),
  }).passthrough().nullish(),
}).passthrough();

const chatCollaborationSchema = z.object({
  schema_version: z.literal("collaboration_request_readback_v0").optional(),
  request_id: z.string(), agent_id: z.string(), goal_id: z.string().nullish(),
  request_revision: z.number().nullish(), decision_reason: z.string().nullish(),
  brief: z.object({
    purpose: z.string(), context: z.string(), constraints: z.array(z.string()),
    inputs: z.array(z.object({ref: z.string(), description: z.string(), sha256: z.string().nullish()}).passthrough()),
    acceptance: z.array(z.string()), return_requirement: z.string(),
  }).passthrough(),
  returns: z.array(z.object({phase: z.string(), status: z.string()}).passthrough()),
}).passthrough();

const chatVisibleMessagesSchema = z.array(z.object({
  schema_version: z.literal("loopx_chat_message_v1").optional(),
  message_id: z.string().min(1), turn_id: z.string().nullable(), role: z.string(),
  text: z.string(), created_at: z.string(),
  attachments: z.array(z.object({id: z.string(), name: z.string(), size: z.number(),
    mime_type: z.string(), data_url: z.string()}).passthrough()).nullish(),
  reply_context: chatReplyContextSchema.nullish(),
  collaboration: chatCollaborationSchema.nullish(),
  collaborations: z.array(chatCollaborationSchema).nullish(),
  return_delivery: z.object({schema_version: z.literal("manager_return_delivery_status_v0"),
    phase: z.enum(["decision", "conclusion"]), status: z.string(), request_revision: z.number().int().nonnegative().nullish(),
    error: z.string().nullish(), verification: z.string().nullish(),
    created_at: z.string().nullish(), delivered_at: z.string().nullish()}).passthrough().nullish(),
}).passthrough());

function requireValidChatMessages(messages: unknown): void {
  const parsed = chatVisibleMessagesSchema.safeParse(messages);
  if (!parsed.success || new Set(parsed.data.map(message => message.message_id)).size !== parsed.data.length) {
    throw new Error("The original Session messages could not be verified.");
  }
}

export async function fetchChatSession(sessionId: string, signal?: AbortSignal) {
  const snapshot = await requestJson<ChatSessionSnapshot>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, { signal });
  if (snapshot?.ok !== true || snapshot.schema_version !== "loopx_chat_store_v1" || snapshot.session?.session_id !== sessionId) {
    throw new Error("The original Session scope changed during history read.");
  }
  requireValidChatMessages(snapshot.messages);
  return snapshot;
}

export const chatQueueInputRequestSchema = z.discriminatedUnion("action", [
  z.object({action: z.literal("edit"), operation_id: z.string().min(1).max(160),
    expected_revision: z.number().int().nonnegative(), message: z.string().min(1).max(24000)}).strict(),
  z.object({action: z.literal("cancel"), operation_id: z.string().min(1).max(160),
    expected_revision: z.number().int().nonnegative()}).strict(),
]);
export type ChatQueueInputRequest = z.infer<typeof chatQueueInputRequestSchema>;
const chatQueuedInputSchema = z.object({input_revision: z.number().int().nonnegative(), message: z.string(),
  attachments: z.array(z.object({id: z.string(), name: z.string(), size: z.number(),
    mime_type: z.string(), data_url: z.string()})),
});
export type ChatQueuedInput = z.infer<typeof chatQueuedInputSchema>;
export async function fetchQueuedChatInput(sessionId: string, turnId: string, signal?: AbortSignal) {
  // Keep original-source audience projection and complete paging. The visible
  // input and its revision come from one canonical user row, never raw model context.
  const history = await fetchChatSessionHistory(sessionId, signal, turnId);
  const original = history.messages.find(row => row.role === "user" && row.turn_id === turnId);
  if (!original) throw new Error("Original queued input is unavailable");
  return chatQueuedInputSchema.parse({input_revision: original.queue_input_revision ?? 0,
    message: original.reply_context?.source_text ?? original.text, attachments: original.attachments ?? []});
}
const chatQueueSchema = z.object({
  ok: z.literal(true), schema_version: z.literal("loopx_chat_store_v1"), session_id: z.string().min(1),
  active_turn_id: z.string().min(1).nullable(),
  turns: z.array(z.object({
    turn_id: z.string().min(1), created_at: z.string(), origin: z.string().nullable(),
    preview: z.string(), character_count: z.number().int().nonnegative(),
    attachment_count: z.number().int().nonnegative(),
    input_revision: z.number().int().nonnegative().default(0),
    mutation: z.object({action: z.enum(["edit", "cancel"]), operation_id: z.string().min(1),
      expected_revision: z.number().int().nonnegative(), input_revision: z.number().int().positive(),
      message: z.string().optional()}).nullable().optional().default(null),
    steering: z.object({
      schema_version: z.literal("loopx_chat_queue_steering_v1"),
      client_ingress_id: z.string().min(1), operation_id: z.string().min(1),
      target_turn_id: z.string().min(1), status: z.enum(["pending", "delivered", "failed"]),
      error_code: z.string().optional(),
    }).nullable(),
  })),
});
export type ChatQueuedTurn = z.infer<typeof chatQueueSchema>["turns"][number];

/** Bounded metadata from the existing persistent queue, never a local queue. */
export async function fetchChatQueue(sessionId: string, signal?: AbortSignal) {
  const read = () => requestJson<unknown>(`/api/chat/sessions/${encodeURIComponent(sessionId)}/queue`, {signal});
  let payload: unknown;
  try {
    payload = await read();
  } catch (error) {
    if (signal?.aborted || !(error instanceof ChatApiError)) throw error;
    const {error_code: code, http_status: status} = error.payload;
    const connectionInterrupted = code === "chat_api_unavailable" && (status === undefined || status === 200);
    if (status !== 503 && !connectionInterrupted) throw error;
    // Reuse the original Session and caller signal for one read-only retry.
    payload = await read();
  }
  const result = chatQueueSchema.safeParse(payload);
  if (!result.success || result.data.session_id !== sessionId
    || new Set(result.data.turns.map(row => row.turn_id)).size !== result.data.turns.length) {
    throw new Error("The original Session queue could not be verified.");
  }
  return result.data;
}

export async function steerQueuedChatTurn(sessionId: string, queuedId: string, targetId: string, ingressId: string) {
  const value = await requestJson<Record<string, unknown>>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/queue/${encodeURIComponent(queuedId)}/steer`,
    {method: "POST", body: JSON.stringify({target_turn_id: targetId, client_ingress_id: ingressId}),
      signal: AbortSignal.timeout(15_000)});
  if (value.ok !== true || value.session_id !== sessionId || value.queued_turn_id !== queuedId
    || value.turn_id !== targetId || value.client_ingress_id !== ingressId || value.status !== "delivered") {
    throw new Error("Queue steering acknowledgement does not match the original request");
  }
}

export async function mutateQueuedChatTurn(sessionId: string, queuedId: string, request: ChatQueueInputRequest) {
  const value = await requestJson<Record<string, unknown>>(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/queue/${encodeURIComponent(queuedId)}/input`,
    {method: "POST", body: JSON.stringify(request), signal: AbortSignal.timeout(15_000)});
  if (value.ok !== true || value.session_id !== sessionId || value.queued_turn_id !== queuedId ||
      value.operation_id !== request.operation_id || value.action !== request.action ||
      value.input_revision !== request.expected_revision + 1 || value.status !== "applied") {
    throw new Error("Queue input acknowledgement does not match the original request");
  }
}

const chatMessagePageSchema = z.object({
  schema_version: z.literal("loopx_chat_message_page_v1"),
  offset: z.number().int().nonnegative(), total: z.number().int().nonnegative(),
  snapshot_sha256: z.string().regex(/^[a-f0-9]{64}$/), next_cursor: z.string().min(1).max(1024).nullable(),
  turn_id: z.string().min(1).optional(),
  visible_page_sha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
});

// A long original transcript can outlive the interactive request budget even
// when each verified page succeeds. Apply the same bounded read deadline to
// its pages and fresh source checks; caller cancellation still takes priority.
const CHAT_HISTORY_READ_TIMEOUT_MS = 30_000;

type ChatHistoryReadProgress = {
  first: ChatSessionSnapshot;
  messages: ChatVisibleMessage[];
  cursors: Set<string>;
  cursor: string;
  total: number;
  digest: string;
  visibleProof: string | undefined;
};

function sameHistorySource(actual: ChatSessionSummary, expected: ChatSessionSummary) {
  return actual.session_id === expected.session_id &&
    (["agent_id", "goal_id", "channel_id", "managed_agent_id", "goal_instance_id", "project_ref"] as const)
      .every(key => (actual[key] ?? null) === (expected[key] ?? null)) &&
    (actual.session_mode ?? "managed_runtime") === (expected.session_mode ?? "managed_runtime");
}

/** Hydrate one complete original prefix; partial pages never become a snapshot. */
export async function fetchChatSessionHistory(session: ChatSessionSummary | string, signal?: AbortSignal, turnId?: string): Promise<ChatSessionSnapshot> {
  return readChatSessionHistory(session, signal, turnId);
}

export async function fetchConfirmedTaskTurn(
  {goalId, todoId, sessionId, turnId}: {goalId: string; todoId: string; sessionId: string; turnId: string},
  signal: AbortSignal,
) {
  const history = await readChatSessionHistory(sessionId, signal, turnId, undefined, undefined, {taskScope: {goalId, todoId}});
  const turn = history.selected_turn;
  if (history.session.goal_id !== goalId || history.session.channel_id !== `task.${todoId}`
    || turn?.turn_id !== turnId || typeof turn.status !== "string" || !turn.status
    || typeof turn.terminal !== "boolean" || (turn.error !== undefined && typeof turn.error !== "string")) {
    throw new Error("The original Task Turn could not be verified.");
  }
  return {turn, messages: history.messages};
}

async function readChatSessionHistory(session: ChatSessionSummary | string, signal?: AbortSignal, turnId?: string,
  progress?: ChatHistoryReadProgress, checkpoint?: (progress: ChatHistoryReadProgress) => void,
  {linkImages = false, taskScope}: {linkImages?: boolean; taskScope?: {goalId: string; todoId: string}} = {},
): Promise<ChatSessionSnapshot> {
  const sessionId = typeof session === "string" ? session : session.session_id;
  // A delegation has the original Session ID; pin its first verified page's
  // scope. History discovery callers keep validating against their own source.
  let expectedSession = typeof session === "string" ? undefined : session;
  // Recovery is derived browser memory, scoped again by current discovery. It
  // grants no source authority and never exposes an incomplete transcript.
  if (progress && expectedSession && !sameHistorySource(progress.first.session, expectedSession)) progress = undefined;
  let first = progress?.first, cursor: string | null = progress?.cursor ?? null;
  let total = progress?.total, digest = progress?.digest;
  let visibleProof = progress ? progress.visibleProof : "";
  const messages = [...(progress?.messages ?? [])], cursors = new Set(progress?.cursors),
    ids = new Set(messages.map(message => message.message_id));
  do {
    signal?.throwIfAborted();
    const query = new URLSearchParams({message_limit: "200"});
    if (turnId !== undefined) query.set("turn_id", turnId);
    if (taskScope) {
      query.set("task_goal_id", taskScope.goalId);
      query.set("task_todo_id", taskScope.todoId);
    }
    if (cursor !== null) query.set("message_cursor", cursor);
    const snapshot = await requestJson<ChatSessionSnapshot>(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}?${query}`,
      {signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS)]) : AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS),
        headers: historyImageHeaders(linkImages)});
    signal?.throwIfAborted();
    const expected = expectedSession;
    const sameSource = snapshot.session?.session_id === sessionId && (!expected || sameHistorySource(snapshot.session, expected));
    if (snapshot.ok !== true || snapshot.schema_version !== "loopx_chat_store_v1" || !sameSource || !Array.isArray(snapshot.messages)) {
      throw new Error("The original Session scope changed during history read.");
    }
    expectedSession ??= snapshot.session;
    // Older services return their complete v1 snapshot for this read. A later
    // page cannot silently lose the cursor contract and masquerade as complete.
    if (!Object.hasOwn(snapshot, "message_page")) {
      if (cursor !== null) throw new Error("The history page contract changed during read.");
      const legacyMessages = turnId === undefined ? snapshot.messages : snapshot.messages.filter(row => row.turn_id === turnId);
      requireValidChatMessages(legacyMessages);
      return {...snapshot, verifiedHistoryBasis: undefined,
        messages: legacyMessages};
    }
    requireValidChatMessages(snapshot.messages);
    const parsed = chatMessagePageSchema.safeParse(snapshot.message_page);
    if (!parsed.success) throw new Error("The original history page could not be verified.");
    const page = parsed.data, end = messages.length + snapshot.messages.length;
    if (page.turn_id !== turnId || (turnId !== undefined && snapshot.messages.some(row => row.turn_id !== turnId))
      || page.offset !== messages.length || snapshot.messages.length > 200 || end > page.total
      || (total !== undefined && total !== page.total) || (digest !== undefined && digest !== page.snapshot_sha256)
      || (page.next_cursor !== null) !== (end < page.total)
      || (page.next_cursor !== null && (!snapshot.messages.length || cursors.has(page.next_cursor)))) {
      throw new Error("The original history cursor did not advance consistently.");
    }
    for (const message of snapshot.messages) {
      if (typeof message.message_id !== "string" || !message.message_id || ids.has(message.message_id)) {
        throw new Error("The original history contains conflicting message identities.");
      }
      ids.add(message.message_id); messages.push(message);
    }
    first ??= snapshot;
    visibleProof = visibleProof !== undefined && page.visible_page_sha256 &&
      snapshot.messages.length === Math.min(200, page.total - page.offset)
      ? visibleProof + page.visible_page_sha256 : undefined;
    total = page.total; digest = page.snapshot_sha256; cursor = page.next_cursor;
    if (cursor !== null) {
      cursors.add(cursor);
      checkpoint?.({first, messages: [...messages], cursors: new Set(cursors), cursor, total, digest, visibleProof});
    }
  } while (cursor !== null);
  // Keep the first read's execution/revision hint. An append between pages is
  // intentionally outside this prefix and will trigger the normal next refresh.
  const basis = visibleProof === undefined ? undefined : total + ":" + visibleProof;
  return {...first!, messages, message_page: undefined,
    verifiedHistoryBasis: turnId === undefined && basis && historyBasisMatches(basis, messages.length) ? basis : undefined};
}

function historyBasisMatches(basis: string, count: number) {
  const match = /^([1-9][0-9]{0,3}):([a-f0-9]+)$/.exec(basis);
  return match !== null && count >= 1 && count <= 5000 && Number(match[1]) === count
    && match[2].length === Math.ceil(count / 200) * 64;
}

function historyImageHeaders(linkImages: boolean): HeadersInit | undefined {
  // The linked bytes are same-origin resources. A separately configured Chat
  // server still uses the compatible inline representation.
  return linkImages && !configuredChatOrigin ? {"X-LoopX-History-Images": "linked"} : undefined;
}

async function tryResumeChatSessionHistory(session: ChatSessionSummary, saved: ChatSessionSnapshot, linkImages: boolean) {
  const basis = saved.verifiedHistoryBasis;
  if (!basis || !sameHistorySource(saved.session, session) || !historyBasisMatches(basis, saved.messages.length)) return null;
  const query = new URLSearchParams({message_limit: "200", known_history: basis});
  let snapshot: ChatSessionSnapshot & {history_resume?: unknown};
  try {
    snapshot = await requestJson<typeof snapshot>(
      "/api/chat/sessions/" + encodeURIComponent(session.session_id) + "?" + query,
      {signal: AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS), headers: historyImageHeaders(linkImages)});
  } catch (error) {
    // A service rollback may no longer accept this optional argument.
    if (error instanceof ChatApiError && error.payload.http_status === 400) return null;
    throw error;
  }
  if (!snapshot || snapshot.history_resume === undefined) return null; // Restart or older service.
  const proof = z.object({schema_version: z.literal("loopx_chat_history_resume_v1"),
    base: z.literal(basis), next: z.string().max(1605)}).safeParse(snapshot.history_resume);
  if (!proof.success || snapshot.ok !== true || snapshot.schema_version !== "loopx_chat_store_v1"
    || !snapshot.session || !sameHistorySource(snapshot.session, session) || Object.hasOwn(snapshot, "message_page")
    || !Array.isArray(snapshot.messages) || snapshot.messages.length > 200
    || !historyBasisMatches(proof.data.next, saved.messages.length + snapshot.messages.length)) {
    throw new Error("The original history suffix could not be verified.");
  }
  requireValidChatMessages(snapshot.messages);
  const ids = new Set(saved.messages.map(message => message.message_id));
  for (const message of snapshot.messages) {
    if (!message || typeof message.message_id !== "string" || !message.message_id || ids.has(message.message_id)) {
      throw new Error("The original history contains conflicting message identities.");
    }
    ids.add(message.message_id);
  }
  const {history_resume: _, ...updated} = snapshot;
  return {...updated, messages: [...saved.messages, ...snapshot.messages], verifiedHistoryBasis: proof.data.next};
}

/** Read-only discovery through the existing Core Goal/Agent/Session identity. */
export async function fetchManagedAgentSessions(goalId: string, agentId: string, signal?: AbortSignal) {
  if (!/^[A-Za-z0-9._-]{1,160}$/.test(goalId) || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(agentId)) throw new Error("Invalid registered Agent address.");
  const listed = await requestJson<{ok: true; schema_version: "loopx_chat_session_list_v1"; goal_id: string; managed_agent_id: string; goal_instance_id: string | null; sessions: ChatSessionSummary[]}>(
    `/api/chat/sessions?${new URLSearchParams({goal_id: goalId, managed_agent_id: agentId})}`, {signal});
  if (listed.ok !== true || listed.schema_version !== "loopx_chat_session_list_v1" || listed.goal_id !== goalId || listed.managed_agent_id !== agentId || !Array.isArray(listed.sessions)
    || !Object.hasOwn(listed, "goal_instance_id") || listed.sessions.some(row => row.goal_id !== goalId || row.managed_agent_id !== agentId
      || row.goal_instance_id !== listed.goal_instance_id || row.channel_id !== `agent.${agentId}` || row.session_mode !== "managed_runtime")) {
    throw new Error("This service cannot verify the registered Agent's original Sessions. Update LoopX and retry.");
  }
  return listed;
}

export async function resolveManagedChatSession(target: ManagedConversationTarget, signal?: AbortSignal) {
  const listed = await fetchManagedAgentSessions(target.goalId, target.agentId, signal);
  const sessions = listed.sessions.filter(row => row.session_id === target.sessionId);
  if (sessions.length !== 1) throw new Error("The original managed Session is no longer available from this Agent.");
  return sessions[0];
}

export async function fetchChatSessions(options: {
  agentId?: string;
  channelId?: string;
  goalId?: string;
  bound?: BoundConversationTarget;
  signal?: AbortSignal;
}) {
  const query = new URLSearchParams();
  if (options.bound) {
    if (!/^[a-f0-9]{24}$/.test(options.bound.bindingId)
      || !/^[A-Za-z0-9._-]{1,160}$/.test(options.bound.sessionId)) {
      throw new Error("Invalid original conversation address.");
    }
    query.set("conversation_binding_id", options.bound.bindingId);
  } else {
    if (options.agentId) query.set("agent_id", options.agentId);
    if (options.channelId) query.set("channel_id", options.channelId);
    if (options.goalId) query.set("goal_id", options.goalId);
  }
  const listed = await requestJson<{
    ok: true;
    schema_version: "loopx_chat_session_list_v1";
    conversation_binding_id?: string;
    conversation_source?: unknown;
    sessions: ChatSessionSummary[];
  }>(`/api/chat/sessions?${query.toString()}`, { signal: options.signal });
  if (!options.bound) return listed;
  // An older service may ignore the selector and return unrelated ordinary
  // history. The owner's echo is mandatory before any transcript is read.
  if (listed.conversation_binding_id !== options.bound.bindingId) {
    throw new Error("This service cannot verify the original conversation. Update LoopX and retry.");
  }
  const sessions = listed.sessions.filter(row => row.session_id === options.bound!.sessionId);
  if (sessions.length !== 1) throw new Error("The original conversation is no longer available from this connection.");
  return { ...listed, sessions };
}

export async function resolveBoundChatSession(target: BoundConversationTarget, signal?: AbortSignal) {
  return (await fetchChatSessions({ bound: target, signal })).sessions[0];
}

/** Discovery chooses once; subsequent navigation freezes the exact Session. */
export async function discoverBoundChatSession(bindingId: string, signal?: AbortSignal) {
  if (!/^[a-f0-9]{24}$/.test(bindingId)) throw new Error("Invalid conversation connection.");
  const listed = await requestJson<{ conversation_binding_id?: string; sessions: ChatSessionSummary[] }>(
    `/api/chat/sessions?conversation_binding_id=${bindingId}`, { signal });
  if (listed.conversation_binding_id !== bindingId) throw new Error("This service cannot verify the original conversation. Update LoopX and retry.");
  const session = [...listed.sessions].sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))[0];
  if (!session || !/^[A-Za-z0-9._-]{1,160}$/.test(session.session_id)) throw new Error("No original conversation is available yet. Start it in Lark first.");
  return session;
}

export function mergeChatSessionMessages(snapshots: ChatSessionSnapshot[]) {
  const messages = new Map<string, ChatVisibleMessage>();
  for (const snapshot of snapshots) {
    for (const message of snapshot.messages) {
      messages.set(`${snapshot.session.session_id}:${message.message_id}`, { ...message, session_id: snapshot.session.session_id });
    }
  }
  // Store rows are append-ordered; opaque IDs cannot break timestamp ties.
  // Stable sort retains that order within a Session, with a deterministic tie
  // across Sessions even when their snapshots finish in a different order.
  return [...messages.values()].sort((left, right) =>
    left.created_at.localeCompare(right.created_at)
      || (left.session_id ?? "").localeCompare(right.session_id ?? "")
  );
}

function sameHistoryRevision(current: ChatSessionSummary, read: ChatSessionSummary) {
  return sameHistorySource(current, read) && Boolean(current.transcript_revision)
    && current.transcript_revision === read.transcript_revision
    && current.updated_at === read.updated_at && current.active_turn_id === read.active_turn_id
    && current.status === read.status;
}

function hasPendingReturnDelivery(snapshot: ChatSessionSnapshot) {
  // The owner also projects explicit_unverified when its receipt cannot be
  // read. That projection can recover; a recorded terminal failure does not.
  const pending = ({status, error}: {status: string; error?: string | null}) =>
    status !== "delivered" && status !== "superseded"
      && (status !== "explicit_unverified" || error === "delivery_state_unreadable");
  return snapshot.messages.some(message =>
    (message.return_delivery && pending(message.return_delivery))
    || (message.collaborations ?? (message.collaboration ? [message.collaboration] : []))
      .some(request => request.returns.some(pending))
  );
}

function historyTransportUnavailable(error: unknown, allowPlain503 = false) {
  if (!(error instanceof ChatApiError) || error.payload.session_invalidated === true) return false;
  const {error_code: code, http_status: status} = error.payload;
  // Bound private history can be confirmed separately when its store returns a plain 503.
  if (allowPlain503 && status === 503 && code === undefined) return true;
  // A response body can be interrupted after successful history headers.
  return code === "chat_api_unavailable" && (status === undefined || status === 200 || Number(status) >= 500);
}

export type ChatHistory = {
  messages: ChatVisibleMessage[];
  sessions: ChatSessionSummary[];
  snapshots: ChatSessionSnapshot[];
  unavailableSessionIds: string[];
  pendingReads?: Map<string, ChatHistoryReadProgress>;
  // Read and verified, but excluded from all displayed history until its
  // original binding is checked after the transcript was read.
  pendingBoundSnapshot?: {bindingId: string; snapshot: ChatSessionSnapshot};
};

export async function fetchChatHistory(options: {
  // An omitted ``agentId`` reads the whole channel transcript. The steward
  // channel is one conversation across whatever executor it currently
  // resolves, so the client must not filter it by its own assumed executor.
  agentId?: string;
  channelId: string;
  goalId?: string;
  bound?: BoundConversationTarget;
  managed?: ManagedConversationTarget;
}, previous?: ChatHistory): Promise<ChatHistory> {
  // Source authorization must be re-read even when recovering an older snapshot.
  const exact = Boolean(options.bound || options.managed);
  const listed = options.managed ? {sessions: [await resolveManagedChatSession(options.managed, AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS))]}
    : (!options.bound ? previous : undefined) ?? await fetchChatSessions({ ...options, signal: AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS) });
  const priorSnapshots = new Map(previous?.snapshots.map((snapshot) => [snapshot.session.session_id, snapshot]));
  const known = new Map(priorSnapshots);
  const pending = previous?.pendingBoundSnapshot;
  const current = listed.sessions[0];
  const retained = !options.managed && pending && options.bound?.bindingId === pending.bindingId && current
    && sameHistorySource(current, pending.snapshot.session) ? pending.snapshot : undefined;
  const pendingReturnBasis = retained && hasPendingReturnDelivery(retained) ? retained : undefined;
  // This discovery happened after the retained read. An unchanged exact
  // source and revision can qualify a retained read with no outstanding returns.
  if (current && retained && !pendingReturnBasis && sameHistoryRevision(current, retained.session)) {
    known.set(current.session_id, retained);
  }
  const pendingReads = new Map(previous?.pendingReads);
  for (const id of pendingReads.keys()) if (!listed.sessions.some(session => session.session_id === id)) pendingReads.delete(id);
  // A failed historical read is not an empty transcript. Retrying only the
  // missing snapshots keeps this recovery read-only and bounds repeated work.
  const missing = listed.sessions.filter((session) => {
    const saved = known.get(session.session_id);
    // Return receipts can be written after the answer text. The bound suffix
    // proof checks the displayed prefix, including delivery state.
    return !saved || (exact && !sameHistoryRevision(session, saved.session))
      || (Boolean(options.bound) && (hasPendingReturnDelivery(saved)
        || (priorSnapshots.has(session.session_id) && previous?.unavailableSessionIds.includes(session.session_id))));
  });
  if (exact) for (const session of missing) known.delete(session.session_id);
  const results = await Promise.allSettled(missing.map(async (session) => {
    const savedProgress = pendingReads.get(session.session_id);
    if (savedProgress && !sameHistorySource(savedProgress.first.session, session)) pendingReads.delete(session.session_id);
    const progress = pendingReads.get(session.session_id);
    // A retained read with pending returns is still private. Use it only as
    // the basis for a fresh proof of the visible prefix through the same source.
    const saved = options.bound && !progress && (pendingReturnBasis?.session.session_id === session.session_id
      ? pendingReturnBasis : priorSnapshots.get(session.session_id));
    let snapshot: ChatSessionSnapshot;
    try {
      const linkImages = Boolean(options.bound);
      snapshot = (saved ? await tryResumeChatSessionHistory(session, saved, linkImages) : null)
        ?? await readChatSessionHistory(session, undefined, undefined, progress,
          checkpoint => pendingReads.set(session.session_id, checkpoint), {linkImages});
    } catch (error) {
      // Reuse the hook's existing retry schedule only for a lost transport.
      // Revoked sources, invalid pages and stale cursors must start a fresh read.
      if (!historyTransportUnavailable(error, Boolean(options.bound && !options.managed))) {
        pendingReads.delete(session.session_id);
      }
      throw error;
    }
    pendingReads.delete(session.session_id);
    if (snapshot.session.session_id !== session.session_id || snapshot.session.agent_id !== session.agent_id) {
      throw new Error("The original Session identity changed during history read.");
    }
    if (options.managed && (snapshot.ok !== true || snapshot.schema_version !== "loopx_chat_store_v1" || snapshot.session.goal_id !== session.goal_id || snapshot.session.managed_agent_id !== session.managed_agent_id
      || snapshot.session.goal_instance_id !== session.goal_instance_id || snapshot.session.channel_id !== session.channel_id
      || snapshot.session.session_mode !== "managed_runtime")) throw new Error("The original managed Session scope changed during history read.");
    return snapshot;
  }));
  // A long page sequence must not publish against a connection/Goal removed
  // while it was in flight. Reuse the original source owner, never a fallback.
  if (missing.length && results.some(result => result.status === "fulfilled")) {
    if (options.bound) {
      const read = results[0];
      try {
        const confirmed = await resolveBoundChatSession(options.bound, AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS));
        if (read?.status === "fulfilled" && !sameHistorySource(confirmed, read.value.session)) {
          throw new Error("The original Session scope changed during history read.");
        }
      } catch (error) {
        if (options.managed || read?.status !== "fulfilled" || !historyTransportUnavailable(error, true)) throw error;
        return {
          messages: [],
          sessions: listed.sessions,
          snapshots: [],
          unavailableSessionIds: listed.sessions.map(session => session.session_id),
          pendingReads,
          pendingBoundSnapshot: {bindingId: options.bound.bindingId, snapshot: read.value},
        };
      }
    }
    if (options.managed) await resolveManagedChatSession(options.managed, AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS));
  }
  for (const result of results) {
    if (result.status === "fulfilled") known.set(result.value.session.session_id, result.value);
  }
  let sessions = listed.sessions;
  let usingStaleBoundHistory = false;
  const failed = results[0];
  const saved = current && priorSnapshots.get(current.session_id);
  if (options.bound && !options.managed && failed?.status === "rejected" && historyTransportUnavailable(failed.reason, true)
    && current && saved && sameHistorySource(current, saved.session)) {
    // Keep only an already proven transcript. If the binding changed while the
    // failed read was in flight, rechecking it here keeps this path fail-closed.
    const confirmed = await resolveBoundChatSession(options.bound, AbortSignal.timeout(CHAT_HISTORY_READ_TIMEOUT_MS));
    if (!sameHistorySource(confirmed, saved.session)) {
      throw new Error("The original Session scope changed during history read.");
    }
    sessions = [confirmed];
    known.set(confirmed.session_id, saved);
    usingStaleBoundHistory = true;
  }
  const snapshots = sessions.flatMap((session) => known.has(session.session_id) ? [known.get(session.session_id)!] : []);
  return {
    messages: mergeChatSessionMessages(snapshots),
    sessions,
    snapshots,
    unavailableSessionIds: sessions.filter(session => usingStaleBoundHistory || !known.has(session.session_id)).map(session => session.session_id),
    pendingReads,
  };
}

export async function acceptChatTurn(
  sessionId: string,
  message: string,
  clientTurnId: string,
  attachments: ChatImageAttachmentInput[] = [],
  signal?: AbortSignal,
  delivery?: "queue",
) {
  const body = JSON.stringify({
    message,
    client_turn_id: clientTurnId,
    ...(delivery ? { delivery } : {}),
    ...(attachments.length ? { attachments: attachments.map((attachment) => ({
      data_url: attachment.dataUrl,
      id: attachment.id,
      mime_type: attachment.mimeType,
      name: attachment.name,
      size: attachment.size,
    })) } : {}),
  });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await requestJson<{
        ok: true;
        session_id: string;
        turn_id: string;
        created: boolean;
        status: string;
        events_url: string;
      }>(`/api/chat/sessions/${sessionId}/turns`, {
        method: "POST",
        body,
        signal,
      });
    } catch (error) {
      const status = error instanceof ChatApiError
        ? Number(error.payload.http_status ?? 0)
        : 0;
      const retryable = error instanceof ChatApiError && (
        error.payload.error_code === "chat_api_unavailable"
        || status >= 500
        || (
          status === 424
          && error.payload.error_code === "resume_failed"
        )
      );
      if (attempt > 0 || signal?.aborted || !retryable) throw error;
    }
  }
  throw new Error("unreachable Chat turn acceptance retry state");
}

function parseSseBlock(block: string): ChatStreamEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as Partial<ChatStreamEvent>;
    if (!parsed.kind || !parsed.payload || typeof parsed.payload !== "object") return null;
    return {
      event_id: String(parsed.event_id ?? ""),
      sequence: Number(parsed.sequence ?? 0),
      kind: String(parsed.kind),
      created_at: String(parsed.created_at ?? ""),
      payload: parsed.payload as Record<string, unknown>,
    };
  } catch {
    return null;
  }
}

// The Chat service sends an SSE heartbeat every 15 seconds while a Turn runs.
// Three missed heartbeats mean the connection is stuck rather than slow, so the
// reader reconnects from its cursor instead of waiting on a silent socket.
export const CHAT_STREAM_STALL_TIMEOUT_MS = 45_000;

// Resolves early when the caller aborts, so the next attempt sees the abort
// instead of opening a connection the caller no longer wants.
function waitForRetry(ms: number, signal?: AbortSignal) {
  // A callback may abort while handling the reconnect phase, before this wait
  // starts listening; that abort must not sit out the backoff.
  if (signal?.aborted) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = () => {
      globalThis.clearTimeout(timer);
      signal?.removeEventListener("abort", done);
      resolve();
    };
    const timer = globalThis.setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  });
}

export async function streamChatTurn(
  eventsUrl: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  options: { stallTimeoutMs?: number } = {},
) {
  const stallTimeoutMs = options.stallTimeoutMs ?? CHAT_STREAM_STALL_TIMEOUT_MS;
  let cursor = "";
  let attempts = 0;
  let stalls = 0;
  let terminal = false;
  // An owned desktop service can need several seconds to restart. Read-only
  // reconnects cover that window (7.75s backoff), without resubmitting a Turn.
  // Keep the existing four-watchdog limit for genuinely silent connections.
  while (!terminal && attempts < 6) {
    signal?.throwIfAborted();
    const origin = typeof window === "undefined" ? "http://127.0.0.1" : window.location.origin;
    const url = new URL(chatApiUrl(eventsUrl), origin);
    if (cursor) url.searchParams.set("after", cursor);
    const attempt = new AbortController();
    const abortAttempt = () => attempt.abort();
    signal?.addEventListener("abort", abortAttempt, { once: true });
    let stalled = false;
    let stallTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
    const armStallTimer = () => {
      if (stallTimer !== undefined) globalThis.clearTimeout(stallTimer);
      stallTimer = globalThis.setTimeout(() => {
        stalled = true;
        attempt.abort();
      }, stallTimeoutMs);
    };
    try {
      armStallTimer();
      const response = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "text/event-stream" },
        signal: attempt.signal,
      });
      if (!response.ok || !response.body) {
        throw new ChatApiError(`SSE HTTP ${response.status}`, { status: response.status });
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        armStallTimer();
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseSseBlock(block);
          if (event) {
            if (event.event_id) cursor = event.event_id;
            onEvent(event);
            signal?.throwIfAborted();
            terminal = ["turn.completed", "turn.interrupted", "turn.failed"].includes(event.kind);
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done || terminal) break;
      }
      if (!terminal) throw new ChatApiError("Agent 事件流连接已断开。", {});
    } catch (error) {
      if (signal?.aborted) throw error;
      attempts += 1;
      if (stalled) stalls += 1;
      if (attempts >= 6 || stalls >= 4) {
        // Fetch refusals (including WebKit's raw "Load failed") and clean EOF
        // are transport failures too. Preserve the Session/Turn recovery route
        // through the receive wrapper instead of presenting them as an outcome.
        throw new ChatApiError("Agent 事件流连接已断开。", {
          ...(error instanceof ChatApiError ? error.payload : {}),
          reconnect_attempts: attempts,
          ...(stalled ? { stall_timeout_ms: stallTimeoutMs } : {}),
        });
      }
      // A local phase keeps the pending reply honest while the reader resumes
      // from its cursor. It carries no event id, so the cursor is unchanged.
      onEvent({
        created_at: new Date().toISOString(),
        event_id: "",
        kind: "agent.phase",
        payload: { label: "连接中断，正在重连…", method: "client/reconnect" },
        sequence: 0,
      });
      await waitForRetry(250 * 2 ** (attempts - 1), signal);
    } finally {
      if (stallTimer !== undefined) globalThis.clearTimeout(stallTimer);
      signal?.removeEventListener("abort", abortAttempt);
    }
  }
  if (!terminal) {
    throw new ChatApiError("Agent 事件流连接已断开。", { reconnect_attempts: attempts });
  }
}

export async function interruptChatTurn(sessionId: string, turnId: string) {
  const receipt = await requestJson<{ ok: true; session_id: string; turn_id: string; status: string }>(
    `/api/chat/sessions/${sessionId}/turns/${turnId}/interrupt`,
    { method: "POST", body: "{}" },
  );
  if (receipt.ok !== true || receipt.session_id !== sessionId || receipt.turn_id !== turnId) {
    throw new ChatApiError("中断回执与本次请求不一致，请刷新后查看。", { error_code: "interrupt_receipt_mismatch" });
  }
  return receipt;
}

export async function steerChatTurn(sessionId: string, turnId: string, message: string, ingressId: string) {
  const receipt = await requestJson<{ ok: boolean; session_id: string; turn_id: string; client_ingress_id: string; status: string; created: boolean }>(
    `/api/chat/sessions/${sessionId}/turns/${turnId}/steer`,
    { method: "POST", body: JSON.stringify({ message, client_ingress_id: ingressId }) },
  );
  if (receipt.ok !== true || receipt.session_id !== sessionId || receipt.turn_id !== turnId
    || receipt.client_ingress_id !== ingressId || receipt.status !== "delivered" || typeof receipt.created !== "boolean") {
    throw new ChatApiError("追加指令的回执不匹配，请保留草稿并检查当前状态。", { error_code: "steer_receipt_mismatch" });
  }
  return receipt;
}

export type LoopXModeSnapshot = {
  ok: true; session_id: string; enabled: boolean; active_turn_id: string | null; conversation_busy: boolean;
  settings: Partial<LoopXModeSettings> & { execution_config?: string };
  native: { status: string; tokenBudget?: number; tokensUsed?: number };
  registered_agents: string[]; paused: boolean; recovery_required: boolean;
  members: Array<{id: string; agent_id: string; todo_id: string | null}>;
  deliveries: Array<{operation_id: string; agent_id: string; todo_id: string | null; status: string}>;
  ingress: Array<{client_ingress_id: string; mode: string; status: string}>;
  turn_id?: string;
};
export function fetchLoopXMode(sessionId: string) {
  return requestJson<LoopXModeSnapshot>(`/api/chat/sessions/${sessionId}/loopx`);
}
export type DelegationInventory = {
  items: Array<{record_id: string; operation_id: string | null; agent_id?: string; todo_id?: string | null;
    status: string; worker_active?: boolean; recovery_required: boolean | null;
    artifacts?: Array<{ref: string; sha256: string}>; current_use?: DelegationReadback["current_use"]}>;
  has_more: boolean; next_cursor: string | null; page_readback_complete: boolean;
};
export type {DelegationPreflight} from "./delegation-preflight.js";
export type DelegationDependency = {
  operation_id: string; ref: string; sha256: string; input_ref: string;
  relation: "responds_to" | "revises" | "uses"; state: "current" | "unavailable";
};
export type DelegationAdoption = {
  requester_agent_id: string; consumer_operation_id: string; consumer_request_id: string;
  consumer_agent_id: string; consumer_todo_id: string | null; state: "current" | "unavailable";
  source_artifacts: Array<{ref: string; sha256: string}>;
  consumer_artifacts: Array<{ref: string; sha256: string}>;
};
export type DelegationReadback = {
  operation_id: string; request_id: string; agent_id: string; todo_id: string | null;
  status: string; worker_active: boolean; recovery_required: boolean;
  artifacts?: Array<{ref: string; sha256: string; text: string}>; error?: string;
  validation?: {source: "goal_acceptance" | "todo_validation" | "request_validation"; basis_sha256: string;
    check_count: number; pinned_file_count: number; checked_at?: string;
    output_versions?: Array<{ref: string; sha256: string}>; oracle_checks?: Array<OracleEvidence & {criterion_id: string}>};
  task_failure?: {stage: "task_postcondition"; status: "failed" | "inconclusive" | "unavailable";
    recovery_kind: "repair_required" | "replan_required"; summary: string; validator_kind: string;
    turn_key: string; resume_mode: "revalidate_cached_result"};
  current_use?: {state: "current" | "unavailable"; checked_operation_count: number;
    reason?: "input_unavailable" | "source_unavailable" | "source_version_changed" | "dependency_cycle" | "verification_budget_exhausted";
    blocking_operation_id?: string; blocking_input_ref?: string; path?: string[]};
  dependencies?: DelegationDependency[]; adoptions?: DelegationAdoption[];
};
const delegationArtifactVersionSchema = z.object({ref: z.string().min(1), sha256: z.string().regex(/^[a-f0-9]{64}$/)});
const delegationReadbackSchema = z.object({
  ok: z.literal(true).optional(),
  operation_id: z.string().min(1), request_id: z.string().min(1), agent_id: z.string().min(1),
  todo_id: z.string().nullable(), status: z.string().min(1), worker_active: z.boolean(), recovery_required: z.boolean(),
  artifacts: z.array(delegationArtifactVersionSchema.extend({text: z.string()})).optional(), error: z.string().optional(),
  validation: z.object({source: z.enum(["goal_acceptance", "todo_validation", "request_validation"]),
    basis_sha256: z.string(), check_count: z.number().int().nonnegative(), pinned_file_count: z.number().int().nonnegative(),
    checked_at: z.string().optional(), output_versions: z.array(delegationArtifactVersionSchema).optional(),
    oracle_checks: z.array(oracleEvidenceSchema.extend({criterion_id: z.string()})).optional()}).optional(),
  task_failure: z.object({recovery_kind: z.enum(["repair_required", "replan_required"]),
    summary: z.string(), turn_key: z.string()}).optional(),
  current_use: z.object({state: z.enum(["current", "unavailable"]),
    reason: z.string().optional(), blocking_operation_id: z.string().optional(),
    blocking_input_ref: z.string().optional(), path: z.array(z.string()).optional()}).optional(),
  dependencies: z.array(delegationArtifactVersionSchema.extend({operation_id: z.string(), input_ref: z.string(),
    relation: z.enum(["responds_to", "revises", "uses"]), state: z.enum(["current", "unavailable"])})).optional(),
  adoptions: z.array(z.object({requester_agent_id: z.string(), consumer_operation_id: z.string(),
    consumer_request_id: z.string(), consumer_agent_id: z.string(), consumer_todo_id: z.string().nullable(),
    state: z.enum(["current", "unavailable"]), source_artifacts: z.array(delegationArtifactVersionSchema),
    consumer_artifacts: z.array(delegationArtifactVersionSchema)})).optional(),
});
function requireDelegationReadback(readback: unknown, operationId: string): DelegationReadback {
  const parsed = delegationReadbackSchema.safeParse(readback);
  if (!parsed.success || parsed.data.operation_id !== operationId) {
    throw new Error("执行详情无法核验，请重新读取 / Execution details cannot be verified; retry");
  }
  return readback as DelegationReadback;
}
export function readLoopXTeamWork(sessionId: string, operationId: string) {
  return requestJson<unknown>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "read", operation_id: operationId}),
  }).then(readback => requireDelegationReadback(readback, operationId));
}
export function adoptLoopXTeamWork(sessionId: string, operationId: string, consumerOperationId: string) {
  return requestJson<unknown>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "adopt", operation_id: operationId,
      consumer_operation_id: consumerOperationId}),
  }).then(readback => requireDelegationReadback(readback, operationId));
}
export function revalidateLoopXTeamWork(sessionId: string, operationId: string) {
  return requestJson<unknown>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "revalidate", operation_id: operationId}),
  }).then(readback => requireDelegationReadback(readback, operationId));
}
const managedResultIdentitySchema = z.object({
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  content_type: z.string().min(1), producer_agent_id: z.string().min(1),
}).passthrough();
const managedGoalResultRowSchema = managedResultIdentitySchema.extend({
  todo_id: z.string().min(1), title: z.string().min(1), size_bytes: z.number().int().nonnegative(),
  completed_at: z.string().nullable().optional(),
});
const managedGoalResultPageSchema = z.object({
  ok: z.literal(true), items: z.array(managedGoalResultRowSchema), total: z.number().int().nonnegative(),
  next_cursor: z.string().min(1).nullable(), unavailable_count: z.number().int().nonnegative(),
  unavailable_todo_ids: z.array(z.string().min(1)),
}).passthrough().refine(page => page.unavailable_count === page.unavailable_todo_ids.length
  && page.items.length + page.unavailable_count <= page.total);
const managedGoalResultReadSchema = z.object({
  ok: z.literal(true), goal_id: z.string().min(1), todo_id: z.string().min(1), text: z.string(),
  result: managedResultIdentitySchema,
}).passthrough();
export type ManagedGoalResultRow = z.infer<typeof managedGoalResultRowSchema>;
export type ManagedGoalResultPage = z.infer<typeof managedGoalResultPageSchema>;
export type ManagedGoalResultRead = z.infer<typeof managedGoalResultReadSchema>;
export async function fetchManagedGoalResults(goalId: string, cursor?: string) {
  const params = new URLSearchParams({goal_id: goalId});
  if (cursor) params.set("cursor", cursor);
  const page = managedGoalResultPageSchema.safeParse(await requestJson<unknown>(`/api/chat/goal-results?${params}`));
  if (!page.success) throw new Error("报告列表响应不完整 / Report inventory response is incomplete");
  return page.data;
}
export async function readManagedGoalResult(goalId: string, todoId: string) {
  const response = managedGoalResultReadSchema.safeParse(await requestJson<unknown>(
    `/api/chat/goal-results/${encodeURIComponent(todoId)}?goal_id=${encodeURIComponent(goalId)}`,
  ));
  if (!response.success || response.data.goal_id !== goalId || response.data.todo_id !== todoId) {
    throw new Error("报告详情响应不完整或不匹配 / Report detail response is incomplete or mismatched");
  }
  return response.data;
}
export type DelegationState = "unavailable" | "accepted" | "rejected" | "recovery_required"
  | "stopped" | "executing" | "validating" | "dispatched" | "unknown";
type DelegationStateFacts = {status: string; worker_active?: boolean; recovery_required: boolean | null;
  current_use?: DelegationReadback["current_use"]};
// Keep inventory and selected-operation labels consistent; unknown states stay unknown.
export function delegationState(row: DelegationStateFacts): DelegationState {
  if (row.status === "unavailable") return "unavailable";
  if (row.status === "accepted") return row.current_use?.state === "unavailable" ? "unavailable" : "accepted";
  if (row.status === "rejected") return "rejected";
  // A recorded stop precedes recovery: only the separate stop receipt proves
  // the original execution released its Host group, so the stored status alone
  // must not be read as work still needing recovery.
  if (row.status === "stopped") return "stopped";
  if (row.recovery_required) return "recovery_required";
  if (row.status === "running" && row.worker_active) return "executing";
  if (row.status === "turn_returned" && row.worker_active) return "validating";
  if (["prepared", "running", "turn_returned"].includes(row.status)) return "dispatched";
  return "unknown";
}
const DELEGATION_STATE_LABELS: Record<DelegationState, {zh: string; en: string}> = {
  unavailable: {zh: "无法核验", en: "Unavailable"},
  accepted: {zh: "已通过当前验收", en: "Currently accepted"},
  rejected: {zh: "未通过验收", en: "Rejected"},
  stopped: {zh: "停止已登记", en: "Stop recorded"},
  recovery_required: {zh: "需要恢复原执行", en: "Original execution needs recovery"},
  executing: {zh: "执行中", en: "Executing"},
  validating: {zh: "正在验收", en: "Validating"},
  dispatched: {zh: "已派发，等待执行回读", en: "Dispatched; awaiting execution readback"},
  unknown: {zh: "状态未知", en: "Unknown state"},
};
export function delegationStateLabel(row: DelegationStateFacts, zh: boolean) {
  const label = DELEGATION_STATE_LABELS[delegationState(row)];
  return zh ? label.zh : label.en;
}
const delegationInventorySchema = z.object({
  ok: z.literal(true).optional(), schema_version: z.literal("loopx_delegation_inventory_v0").optional(),
  items: z.array(z.object({record_id: z.string().min(1), operation_id: z.string().min(1).nullable(),
    agent_id: z.string().optional(), todo_id: z.string().nullable().optional(), status: z.string().min(1),
    worker_active: z.boolean().optional(), recovery_required: z.boolean().nullable(),
    artifacts: z.array(delegationArtifactVersionSchema).optional(),
    current_use: z.object({state: z.enum(["current", "unavailable"])}).optional()})),
  has_more: z.boolean(), next_cursor: z.string().min(1).nullable(), page_readback_complete: z.boolean(),
}).refine(page => page.has_more === (page.next_cursor !== null)
  && (!page.page_readback_complete || page.items.every(row => row.status !== "unavailable")));
export async function fetchLoopXTeamWork(sessionId: string, cursor?: string) {
  const inventory = await requestJson<unknown>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "operations", limit: 10, ...(cursor ? {cursor} : {})}),
  });
  if (!delegationInventorySchema.safeParse(inventory).success) {
    throw new Error("执行列表无法核验，请重新读取 / Execution inventory cannot be verified; retry");
  }
  return inventory as DelegationInventory;
}
export function inspectLoopXMember(sessionId: string, bindingId: string) {
  return requestJson<DelegationPreflight>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "inspect", binding_id: bindingId}),
  });
}
export function updateLoopXMode(sessionId: string, operation: string, settings?: LoopXModeSettings, operationId = crypto.randomUUID()) {
  return requestJson<LoopXModeSnapshot>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation, operation_id: operationId, ...(settings ? {settings} : {})}),
  });
}
export function sendLoopXMessage(sessionId: string, message: string, deliveryMode: "queue" | "inbox" | "steer", operationId: string = crypto.randomUUID()) {
  return requestJson<{ok: true; status: string; delivery_mode: string}>(`/api/chat/sessions/${sessionId}/loopx`, {
    method: "POST", body: JSON.stringify({operation: "message", operation_id: operationId, message, delivery_mode: deliveryMode}),
  });
}

export async function sendChatTurnStreaming(
  sessionId: string,
  message: string,
  options: {
    attachments?: ChatImageAttachmentInput[];
    clientTurnId?: string;
    onDelta?: (text: string) => void;
    onActivity?: (label: string, step: TurnStep | null) => void;
    onPhase?: (phase: string, turnId: string) => void;
    signal?: AbortSignal;
  } = {},
) {
  const clientTurnId = options.clientTurnId ?? crypto.randomUUID();
  const accepted = await acceptChatTurn(
    sessionId,
    message,
    clientTurnId,
    options.attachments,
    options.signal,
  );
  options.onPhase?.(accepted.status === "queued" ? "turn.queued" : "turn.accepted", accepted.turn_id);
  return receiveChatTurnStreaming(
    sessionId,
    accepted.turn_id,
    accepted.events_url,
    options,
  );
}

async function receiveChatTurnStreaming(
  sessionId: string,
  turnId: string,
  eventsUrl: string,
  options: {
    onDelta?: (text: string) => void;
    onActivity?: (label: string, step: TurnStep | null) => void;
    onPhase?: (phase: string, turnId: string) => void;
    signal?: AbortSignal;
  } = {},
) {
  let finalResponse: unknown = null;
  let steeredIntoTurnId: string | undefined;
  const outcome: {
    failure: Record<string, unknown> | null;
    interrupted: Record<string, unknown> | null;
  } = { failure: null, interrupted: null };
  try {
    await streamChatTurn(
      eventsUrl,
      (event) => {
        options.onPhase?.(event.kind, turnId);
        if (event.kind === "answer.delta" || event.kind === "assistant.delta") {
          options.onDelta?.(String(event.payload.text ?? ""));
        }
        if (event.kind === "agent.phase") {
          const label = typeof event.payload.label === "string" ? event.payload.label.trim() : "";
          if (label) options.onActivity?.(label, parseTurnStep(event.payload.step));
        }
        if (event.kind === "turn.completed") {
          if (typeof event.payload.steered_into_turn_id === "string" && event.payload.steered_into_turn_id) {
            steeredIntoTurnId = event.payload.steered_into_turn_id;
          }
          finalResponse = event.payload.response;
        }
        if (event.kind === "turn.failed") {
          outcome.failure = event.payload;
        }
        if (event.kind === "turn.interrupted") {
          outcome.interrupted = event.payload;
        }
      },
      options.signal,
    );
  } catch (error) {
    if (error instanceof ChatApiError && !options.signal?.aborted) {
      throw new ChatApiError(error.message, {
        ...error.payload,
        events_url: eventsUrl,
        reconnectable: true,
        session_id: sessionId,
        turn_id: turnId,
      });
    }
    throw error;
  }
  if (outcome.failure) {
    throw new ChatApiError(
      String(outcome.failure.message || "Agent 回合失败。"),
      outcome.failure,
    );
  }
  if (outcome.interrupted) {
    throw new ChatApiError("Agent 回合已中断。", {
      ...outcome.interrupted,
      error_code: "turn_interrupted",
      session_id: sessionId,
      turn_id: turnId,
    });
  }
  return {
    response: agentResponseSchema.parse(finalResponse),
    sessionId,
    turnId,
    steeredIntoTurnId,
  };
}

/** Read a stored Turn's terminal outcome without submitting or resuming work.
 * Failed/interrupted Turns have no completed proposals; transport failures throw
 * so callers can retry instead of treating an unavailable response as empty.
 */
export async function readCompletedChatTurn(sessionId: string, turnId: string, signal: AbortSignal): Promise<AgentResponse | null> {
  let completed: AgentResponse | null = null;
  await streamChatTurn(`/api/chat/sessions/${sessionId}/turns/${turnId}/events`, (event) => {
    if (event.kind === "turn.completed" && !event.payload.steered_into_turn_id) completed = agentResponseSchema.parse(event.payload.response);
  }, signal);
  return completed;
}

export async function resumeChatTurnStreaming(
  sessionId: string,
  turnId: string,
  options: {
    onDelta?: (text: string) => void;
    onActivity?: (label: string, step: TurnStep | null) => void;
    onPhase?: (phase: string, turnId: string) => void;
    signal?: AbortSignal;
  } = {},
) {
  return receiveChatTurnStreaming(
    sessionId,
    turnId,
    `/api/chat/sessions/${sessionId}/turns/${turnId}/events`,
    options,
  );
}

export async function sendChatTurn(sessionId: string, message: string) {
  const payload = await requestJson<{ response: unknown }>(`/api/chat/sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
  return agentResponseSchema.parse(payload.response);
}

export async function closeChatSession(sessionId: string) {
  const result = chatSessionCloseSchema.parse(
    await requestJson<unknown>(`/api/chat/sessions/${sessionId}`, {
      keepalive: true,
      method: "DELETE",
    }),
  );
  if (result.session_id !== sessionId) {
    throw new ChatApiError("Agent 会话关闭回执与本次请求不一致。", {
      session_id: result.session_id,
    });
  }
  return result;
}

export async function resumeChatSession(sessionId: string) {
  return requestJson<{ ok: true; schema_version: "loopx_chat_session_resume_v1"; session: ChatSessionSummary }>(
    `/api/chat/sessions/${sessionId}/resume`,
    { method: "POST", body: "{}" },
  );
}

function goalSubagentConfigurationBody(request: GoalSubagentConfigurationRequest) {
  return {
    goal_id: request.goalId,
    enabled: request.enabled,
    align_codex_host_capacity: request.alignCodexHostCapacity ?? false,
    ...(request.modelConfig !== undefined ? { model_config: request.modelConfig } : {}),
    ...(request.executionConfig !== undefined ? { execution_config: request.executionConfig } : {}),
    ...(request.enabled ? {
      max_children: request.maxChildren,
      allowed_domains: request.allowedDomains,
    } : {}),
  };
}

function verifyGoalSubagentConfigurationResult(
  result: GoalSubagentConfigurationResult,
  request: GoalSubagentConfigurationRequest,
) {
  const orchestration = result.after.orchestration;
  const expectedDomains = [...new Set(request.allowedDomains)];
  const enabled = result.feature_summary.multi_subagent === "enabled";
  const matchesRequest = result.goal_id === request.goalId
    && enabled === request.enabled
    && (request.modelConfig === undefined || JSON.stringify(orchestration.model_config ?? null) === JSON.stringify(request.modelConfig))
    && (request.executionConfig === undefined || (orchestration.execution_config ?? "") === request.executionConfig)
    && (request.enabled
      ? orchestration.max_children === request.maxChildren
        && JSON.stringify(orchestration.allowed_domains) === JSON.stringify(expectedDomains)
      : orchestration.spawn_allowed === false && orchestration.max_children === 0);
  const hostCapacityMatches = !request.alignCodexHostCapacity
    || !request.enabled
    || result.codex_host_capacity.required_children === request.maxChildren;
  if (!matchesRequest || !hostCapacityMatches) {
    throw new ChatApiError("Goal 子代理配置回执与本次请求不一致，界面已停止更新。", {
      after: result.after,
      goal_id: result.goal_id,
    });
  }
  return result;
}

export async function previewGoalSubagentConfiguration(
  request: GoalSubagentConfigurationRequest,
) {
  const result = goalSubagentConfigurationResultSchema.parse(
    await requestJson<unknown>("/api/chat/goal-subagents/dry-run", {
      method: "POST",
      body: JSON.stringify(goalSubagentConfigurationBody(request)),
    }),
  );
  if (!result.dry_run || result.execute || result.written) {
    throw new ChatApiError("Goal 子代理预览返回了非预览回执，已停止进入确认状态。", {
      result,
    });
  }
  return verifyGoalSubagentConfigurationResult(result, request);
}

export async function applyGoalSubagentConfiguration(
  request: GoalSubagentConfigurationRequest,
  previewId: string,
) {
  const result = goalSubagentConfigurationResultSchema.parse(
    await requestJson<unknown>("/api/chat/goal-subagents/apply", {
      method: "POST",
      body: JSON.stringify({
        ...goalSubagentConfigurationBody(request),
        preview_id: previewId,
      }),
    }),
  );
  if (result.preview_id !== previewId || result.dry_run || !result.execute) {
    throw new ChatApiError("Goal 子代理写入回执与本次确认不一致，界面已停止更新。", {
      result,
    });
  }
  if (result.changed && (!result.written
    || (result.goal_configuration_changed
      && (!result.global_sync.executed || !result.global_sync.readback.verified))
    || (request.alignCodexHostCapacity
      && result.codex_host_capacity.write_required
      && !result.codex_host_capacity.written))) {
    throw new ChatApiError("Goal 子代理设置未通过共享状态读回验证。", { result });
  }
  return verifyGoalSubagentConfigurationResult(result, request);
}

export async function previewTodo(goalId: string, text: string) {
  const preview = todoPreviewSchema.parse(
    await requestJson<unknown>("/api/chat/todo/dry-run", {
      method: "POST",
      body: JSON.stringify({ goal_id: goalId, text }),
    }),
  );
  if (!todoPreviewMatchesRequest(preview, { goalId, text })) {
    throw new ChatApiError("Todo 写入预览与本次请求不一致，已停止进入批准状态。", {
      preview,
    });
  }
  return preview;
}

export async function applyTodo(goalId: string, text: string, previewId: string) {
  const result = todoApplyResultSchema.parse(
    await requestJson<TodoApplyResult>("/api/chat/todo/apply", {
      method: "POST",
      body: JSON.stringify({ goal_id: goalId, text, preview_id: previewId }),
    }),
  );
  if (!todoApplyResultMatchesRequest(result, { goalId, previewId, text })) {
    throw new ChatApiError("Todo 写入回执与本次批准不一致，界面已停止更新。", {
      receipt: result.receipt,
      todo: result.todo,
    });
  }
  return result;
}

export function parseCompletedDecisionHistory(raw: string | null, goalId: string) {
  if (!raw) return [];
  try {
    const parsed = storedDecisionHistorySchema.safeParse(JSON.parse(raw));
    if (!parsed.success || parsed.data.goal_id !== goalId) return [];
    return parsed.data.decisions;
  } catch {
    return [];
  }
}

export function serializeCompletedDecisionHistory(
  goalId: string,
  decisions: StoredDecisionHistoryItem[],
) {
  return JSON.stringify(
    storedDecisionHistorySchema.parse({
      schema_version: "loopx_chat_decision_history_v0",
      goal_id: goalId,
      decisions: decisions.slice(0, 24),
    }),
  );
}

export type GoalChannelTarget = {
  enabled: boolean;
  provider: string;
  target_name: string;
};

const goalChannelTargetsSchema = z.object({
  ok: z.literal(true),
  targets: z.array(
    z.object({
      enabled: z.boolean(),
      provider: z.string(),
      target_name: z.string(),
    }),
  ),
});

export async function fetchGoalChannelTargets() {
  return goalChannelTargetsSchema.parse(
    await requestJson<unknown>("/api/chat/goal-channel/targets"),
  ).targets;
}

const goalChannelOperationSchema = z.object({
  ok: z.boolean(),
  blocker: z.string().optional(),
  public_summary: z.string().optional(),
  status: z.string().optional(),
});

export type GoalChannelOperation = z.infer<typeof goalChannelOperationSchema>;

export async function setupGoalChannel(options: { execute: boolean; goalId: string; target: string }) {
  return goalChannelOperationSchema.parse(
    await requestJson<unknown>("/api/chat/goal-channel/setup", {
      method: "POST",
      body: JSON.stringify({
        execute: options.execute,
        goal_id: options.goalId,
        target: options.target,
      }),
    }),
  );
}

export async function configureGoalChannelAutoNotify(options: { autoNotify: boolean; goalId: string; kind?: "human_gate" | "blocked_notice" }) {
  return goalChannelOperationSchema.parse(
    await requestJson<unknown>("/api/chat/goal-channel/configure", {
      method: "POST",
      body: JSON.stringify({
        ...(options.kind === "blocked_notice" ? { auto_notify_blocked_notices: options.autoNotify } : { auto_notify_human_gates: options.autoNotify }),
        goal_id: options.goalId,
      }),
    }),
  );
}

export const periodicReportScheduleSchema = z.object({
  schema_version: z.literal("periodic_report_schedule_v0"),
  schedule_id: z.string(),
  rrule: z.string(),
  timezone: z.string(),
  start_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});

export const periodicReportMachineConfigurationSchema = z.object({
  schema_version: z.literal("periodic_report_machine_defaults_v0"),
  enabled: z.boolean(),
  inheritance: z.literal("live_machine_default"),
  profile_preset: z.string().optional(),
  route_ref: z.string().optional(),
  timezone: z.string(),
  schedule: periodicReportScheduleSchema.nullable().optional(),
});

export const machineConfigurationSchema = z.object({
  schema_version: z.literal("loopx_machine_configuration_v0"),
  namespaces: z.record(z.string(), z.record(z.string(), z.unknown())),
});

export const machineConfigurationNamespaceDescriptorSchema = z.object({
  namespace: z.string(),
  title: z.string(),
  description: z.string(),
  schema_versions: z.array(z.string()).min(1),
  configuration_template: z.record(z.string(), z.unknown()),
  template_status: z.enum(["ready", "schema_only"]),
});

export const machineConfigurationCatalogSchema = z.object({
  schema_version: z.literal("machine_configuration_catalog_v0"),
  namespaces: z.array(machineConfigurationNamespaceDescriptorSchema),
});

export const capabilityConfigurationFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  description: z.string(),
  input_kind: z.enum(["boolean", "number", "select", "string_list", "text", "periodic_report_schedule", "pr_review_agent_orders"]),
  agents: z.array(z.string()).optional(),
  nullable: z.boolean().optional(),
  required: z.boolean(),
  minimum: z.number().int().optional(),
  maximum: z.number().int().optional(),
  options: z.array(z.string()).optional(),
});

export const capabilityConfigurationEditorSchema = z.object({
  schema_version: z.literal("capability_configuration_editor_v0"),
  editable: z.boolean(),
  supported_scopes: z.array(z.enum(["goal", "machine"])),
  writable_scopes: z.array(z.enum(["goal", "machine"])),
  fields: z.array(capabilityConfigurationFieldSchema),
  read_only_reason: z.string().optional(),
});

export const progressReviewObservationSchema = z.object({
  mode: z.string(), read_state: z.enum(["observed", "missing", "unavailable"]), read_error: z.string().optional(),
  receipt_count: z.number(), rejected_receipts: z.number(), stale_receipts: z.number(),
  contract_revision: z.string().nullable(), authority: z.literal("none"),
  latest: z.object({status: z.string(), event_id: z.string(), evidence_id: z.string(),
    judgments: z.object({choice: z.object({relation: z.string().nullable(), increment: z.string().nullable()}).nullable(),
      noul: z.object({serves_acceptance: z.number().nullable(), evidence_increment: z.number().nullable(), behavior_change: z.number().nullable()}).nullable()}),
    criterion_current: z.boolean().optional(),
    evidence_scope: z.object({coverage: z.literal("declared_file_net_change"), files: z.array(z.string()),
      criterion_binding: z.object({origin: z.enum(["operator_study", "goal_acceptance"]), criteria_sha256: z.string(),
        todo_id: z.string().optional(), contract_digest: z.string().optional(), contract_revision: z.number().optional(),
        criterion_ids: z.array(z.string()).optional()})}).optional(),
  }).nullable(),
});
export const capabilityConfigurationCatalogSchema = z.object({
  schema_version: z.literal("capability_configuration_catalog_v0"),
  capabilities: z.array(z.object({
    capability_id: z.string(),
    display_name: z.string(),
    description: z.string(),
    available_scopes: z.array(z.enum(["goal", "machine"])),
    machine_namespace: z.string().optional(),
    goal_feature_id: z.string().optional(),
    effective_value_policy: z.enum(["goal_override_over_live_machine_default", "goal_stride_over_live_review_cadence"]).optional(),
    availability: z.string().optional(),
    default: z.record(z.string(), z.unknown()).optional(),
    current: z.record(z.string(), z.unknown()).optional(),
    machine_current: z.record(z.string(), z.unknown()).optional(),
    effective_configuration: z.object({
      schema_version: z.literal("capability_configuration_resolution_v0"),
      capability_id: z.string(),
      source: z.enum(["goal_override", "machine_default", "capability_default", "not_configured"]),
      configuration: z.record(z.string(), z.unknown()).nullable(),
      inherited: z.boolean(),
      goal_override_present: z.boolean(),
      machine_default_present: z.boolean(),
      effective_revision: z.string(),
    }).optional(),
    documentation: z.record(z.string(), z.unknown()).optional(),
    observation: progressReviewObservationSchema.optional(),
    context_contribution: z.object({
      supported_phases: z.array(z.enum(["before_plan", "before_delegate", "after_delegate_result"])),
      target: z.literal("coordinator"),
      activation: z.literal("with_capability"),
      receipt_required: z.literal(true),
    }).optional(),
    configuration_editor: capabilityConfigurationEditorSchema,
  })),
});

export const goalConfigurationInspectionSchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("goal_configuration_inspection_v0"),
  status: z.literal("configured"),
  goal_id: z.string(),
  revision: z.string(),
  available_capabilities: z.array(z.string()),
  capability_catalog: capabilityConfigurationCatalogSchema,
});

const goalConfigurationMutationBaseSchema = z.object({
  ok: z.literal(true),
  goal_id: z.string(),
  capability_id: z.string(),
  changed_fields: z.array(z.string()),
  goal_configuration: z.record(z.string(), z.unknown()).nullable(),
  capability_catalog: capabilityConfigurationCatalogSchema,
  codex_host_capacity: codexHostCapacitySchema.optional(),
});

export const goalConfigurationPreviewSchema = goalConfigurationMutationBaseSchema.extend({
  schema_version: z.literal("goal_configuration_update_plan_v0"),
  status: z.literal("preview"),
  action: z.enum(["create", "update", "delete", "unchanged"]),
  current_revision: z.string(),
  desired_revision: z.string(),
  base_revision: z.string(),
  plan_revision: z.string(),
  writes_required: z.number().int().nonnegative(),
});

export const goalConfigurationTransactionSchema = goalConfigurationMutationBaseSchema.extend({
  schema_version: z.literal("goal_configuration_transaction_v0"),
  status: z.enum(["applied", "unchanged"]),
  plan_revision: z.string(),
  applied_revision: z.string(),
  readback_verified: z.literal(true),
});

export const goalConfigurationPartialWriteSchema = z.object({
  ok: z.literal(false),
  schema_version: z.literal("goal_configuration_transaction_v0"),
  status: z.literal("partial_write"),
  goal_id: z.string(),
  capability_id: z.string(),
  plan_revision: z.string(),
  applied_revision: z.string().nullable(),
  source_written: z.literal(true),
  shared_sync_pending: z.boolean(),
  host_capacity_pending: z.boolean().optional().default(false),
  readback_verified: z.boolean(),
  changed_fields: z.array(z.string()),
  goal_configuration: z.record(z.string(), z.unknown()).nullable(),
  capability_catalog: capabilityConfigurationCatalogSchema,
  error: z.string(),
  recommended_action: z.string(),
  codex_host_capacity: codexHostCapacitySchema.optional(),
});

export const goalConfigurationApplyResultSchema = z.union([
  goalConfigurationTransactionSchema,
  goalConfigurationPartialWriteSchema,
]);

const machineConfigurationBaseSchema = z.object({
  ok: z.literal(true),
  available_namespaces: z.array(z.string()),
  namespace_catalog: machineConfigurationCatalogSchema.optional().default({
    schema_version: "machine_configuration_catalog_v0",
    namespaces: [],
  }),
  capability_catalog: capabilityConfigurationCatalogSchema,
  changed_namespaces: z.array(z.string()).optional().default([]),
  invalid_namespaces: z.array(z.string()).optional().default([]),
  machine_configuration: machineConfigurationSchema.nullable().optional(),
});

export const machineConfigurationInspectionSchema = machineConfigurationBaseSchema.extend({
  schema_version: z.literal("machine_configuration_inspection_v0"),
  status: z.enum(["configured", "absent", "invalid"]),
  revision: z.string(),
});

export const machineConfigurationPreviewSchema = machineConfigurationBaseSchema.extend({
  schema_version: z.literal("machine_configuration_update_plan_v0"),
  status: z.literal("preview"),
  action: z.enum(["create", "update", "delete", "unchanged"]),
  current_revision: z.string(),
  desired_revision: z.string(),
  plan_revision: z.string(),
  writes_required: z.number().int().nonnegative(),
  machine_configuration: machineConfigurationSchema.nullable(),
});

export const machineConfigurationTransactionSchema = machineConfigurationBaseSchema.extend({
  schema_version: z.literal("machine_configuration_transaction_v0"),
  status: z.enum(["applied", "unchanged"]),
  plan_revision: z.string(),
  transaction_id: z.string().nullable(),
  readback_verified: z.literal(true),
  rollback_available: z.boolean(),
  applied_revision: z.string().optional(),
  prior_revision: z.string().optional(),
});

export const machineConfigurationRollbackPlanSchema = machineConfigurationBaseSchema.extend({
  schema_version: z.literal("machine_configuration_rollback_plan_v0"),
  status: z.literal("preview"),
  action: z.enum(["delete", "restore", "unchanged", "blocked"]),
  reason: z.string(),
  transaction_id: z.string(),
  plan_revision: z.string(),
  rollback_allowed: z.boolean(),
  writes_required: z.number().int().nonnegative(),
});

export const machineConfigurationRollbackReceiptSchema = machineConfigurationBaseSchema.extend({
  schema_version: z.literal("machine_configuration_rollback_receipt_v0"),
  status: z.enum(["rolled_back", "unchanged"]),
  transaction_id: z.string(),
  plan_revision: z.string(),
  rollback_id: z.string().nullable(),
  readback_verified: z.literal(true),
});

export type MachineConfiguration = z.infer<typeof machineConfigurationSchema>;
export type MachineConfigurationNamespaceDescriptor = z.infer<typeof machineConfigurationNamespaceDescriptorSchema>;
export type CapabilityConfigurationCatalog = z.infer<typeof capabilityConfigurationCatalogSchema>;
export type CapabilityConfigurationEditor = z.infer<typeof capabilityConfigurationEditorSchema>;
export type GoalConfigurationInspection = z.infer<typeof goalConfigurationInspectionSchema>;
export type GoalConfigurationPreview = z.infer<typeof goalConfigurationPreviewSchema>;
export type GoalConfigurationTransaction = z.infer<typeof goalConfigurationTransactionSchema>;
export type GoalConfigurationPartialWrite = z.infer<typeof goalConfigurationPartialWriteSchema>;
export type GoalConfigurationApplyResult = z.infer<typeof goalConfigurationApplyResultSchema>;
export type MachineConfigurationInspection = z.infer<typeof machineConfigurationInspectionSchema>;
export type MachineConfigurationPreview = z.infer<typeof machineConfigurationPreviewSchema>;
export type MachineConfigurationTransaction = z.infer<typeof machineConfigurationTransactionSchema>;
export type MachineConfigurationRollbackPlan = z.infer<typeof machineConfigurationRollbackPlanSchema>;

// The operator credential readback is redacted by construction: the key field
// carries a fingerprint and never a value, so this schema has no place to put
// one even if a future server tried to send it.
export const operatorCredentialFieldSchema = z.object({
  configured: z.boolean(),
  source: z.enum(["machine_store", "service_environment", "unset"]),
  env_var: z.string().optional(),
  fingerprint: z.string().nullable().optional(),
  value: z.string().nullable().optional(),
  blocked_by: z.string().optional(),
});

export const operatorCredentialSchema = z.object({
  ok: z.literal(true),
  // The chat route returns the same versioned projection the CLI prints, so the
  // browser and the terminal cannot drift into two spellings of one readback.
  schema_version: z.literal("operator_provider_credential_projection_v0"),
  action: z.string().optional(),
  store_ref: z.string(),
  store_revision: z.string(),
  record_present: z.boolean(),
  status: z.enum(["configured", "absent", "invalid"]),
  repair: z.string(),
  provider_key: operatorCredentialFieldSchema,
  base_url: operatorCredentialFieldSchema,
});

export type OperatorCredential = z.infer<typeof operatorCredentialSchema>;

export async function fetchOperatorCredential() {
  return operatorCredentialSchema.parse(
    await requestJson<unknown>("/api/chat/operator-credential"),
  );
}

export async function writeOperatorCredential(update: {
  provider_key?: string;
  base_url?: string;
  clear_provider_key?: boolean;
  clear_base_url?: boolean;
}) {
  return operatorCredentialSchema.parse(
    await requestJson<unknown>("/api/chat/operator-credential", {
      method: "POST",
      body: JSON.stringify(update),
    }),
  );
}

export async function fetchMachineConfiguration() {
  return machineConfigurationInspectionSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration"),
  );
}

export async function fetchGoalConfiguration(goalId: string) {
  const query = new URLSearchParams({ goal_id: goalId });
  return goalConfigurationInspectionSchema.parse(
    await requestJson<unknown>(`/api/chat/goal-configuration?${query.toString()}`),
  );
}

const automationCadenceSourceSchema = z.object({
  agent_id: z.string().nullable(),
  automation_id: z.string().nullable(),
  min_interval_minutes: z.number().int().nonnegative(),
  revision: z.number().int().nonnegative(),
});

const automationCadenceEligibilitySchema = z.discriminatedUnion("state", [
  z.object({
    state: z.literal("unconfigured"),
    reason: z.literal("unconfigured"),
    eligible_now: z.null(),
    next_eligible_at_ms: z.null(),
  }),
  z.object({
    state: z.literal("eligible"),
    reason: z.literal("owner_minimum_interval"),
    eligible_now: z.literal(true),
    next_eligible_at_ms: z.number().int().nonnegative().nullable(),
  }),
  z.object({
    state: z.literal("waiting"),
    reason: z.literal("minimum_interval_wait"),
    eligible_now: z.literal(false),
    next_eligible_at_ms: z.number().int().nonnegative(),
  }),
  z.object({
    state: z.literal("unavailable"),
    reason: z.enum([
      "agent_scope_required",
      "runtime_root_unavailable",
      "owner_read_failed",
    ]),
    eligible_now: z.null(),
    next_eligible_at_ms: z.null(),
  }),
]);

export const automationCadenceSchema = z.object({
  ok: z.literal(true),
  schema_version: z.literal("chat_automation_cadence_v0"),
  goal_id: z.string(),
  agent_id: z.string().nullable(),
  automation_id: z.string().nullable(),
  configuration_revision: z.number().int().nonnegative(),
  min_interval_minutes: z.number().int().nonnegative(),
  eligibility: automationCadenceEligibilitySchema,
  enabled: z.boolean(),
  enforcement: z.string(),
  pre_model_admission: z.string(),
  sources: z.array(automationCadenceSourceSchema),
  preview_revision: z.string().optional(),
  written: z.boolean().optional(),
  readback_verified: z.boolean().optional(),
});

export type AutomationCadence = z.infer<typeof automationCadenceSchema>;
export type AutomationCadenceChange = {
  goal_id: string;
  agent_id: string | null;
  automation_id: string | null;
  min_interval_minutes: number;
  expected_revision: number;
  owner_reference: string;
  approve_reduction: boolean;
};

export async function fetchAutomationCadence(goalId: string, agentId: string | null, automationId: string | null) {
  const query = new URLSearchParams({ goal_id: goalId });
  if (agentId) query.set("agent_id", agentId);
  if (automationId) query.set("automation_id", automationId);
  return automationCadenceSchema.parse(await requestJson<unknown>(`/api/chat/automation-cadence?${query}`));
}

export async function previewAutomationCadence(change: AutomationCadenceChange) {
  return automationCadenceSchema.parse(await requestJson<unknown>("/api/chat/automation-cadence/preview", {
    method: "POST", body: JSON.stringify(change),
  }));
}

export async function applyAutomationCadence(change: AutomationCadenceChange, previewRevision: string) {
  return automationCadenceSchema.parse(await requestJson<unknown>("/api/chat/automation-cadence/apply", {
    method: "POST", body: JSON.stringify({ ...change, preview_revision: previewRevision }),
  }));
}

export async function previewGoalConfiguration(
  goalId: string,
  capabilityId: string,
  configuration: Record<string, unknown> | null,
) {
  return goalConfigurationPreviewSchema.parse(
    await requestJson<unknown>("/api/chat/goal-configuration/preview", {
      method: "POST",
      body: JSON.stringify({
        goal_id: goalId,
        capability_id: capabilityId,
        configuration,
      }),
    }),
  );
}

export async function applyGoalConfiguration(
  goalId: string,
  capabilityId: string,
  configuration: Record<string, unknown> | null,
  expectedPlanRevision: string,
) {
  return goalConfigurationApplyResultSchema.parse(
    await requestJson<unknown>("/api/chat/goal-configuration/apply", {
      method: "POST",
      body: JSON.stringify({
        goal_id: goalId,
        capability_id: capabilityId,
        configuration,
        expected_plan_revision: expectedPlanRevision,
      }),
    }),
  );
}

export async function previewMachineConfiguration(
  namespace: string,
  namespaceConfiguration: Record<string, unknown>,
) {
  return machineConfigurationPreviewSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/preview", {
      method: "POST",
      body: JSON.stringify({
        namespace,
        namespace_configuration: namespaceConfiguration,
      }),
    }),
  );
}

export async function applyMachineConfiguration(
  namespace: string,
  namespaceConfiguration: Record<string, unknown>,
  expectedPlanRevision: string,
) {
  return machineConfigurationTransactionSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/apply", {
      method: "POST",
      body: JSON.stringify({
        expected_plan_revision: expectedPlanRevision,
        namespace,
        namespace_configuration: namespaceConfiguration,
      }),
    }),
  );
}

export async function previewMachineConfigurationRemoval(namespace: string) {
  return machineConfigurationPreviewSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/preview", {
      method: "POST",
      body: JSON.stringify({ namespace, operation: "remove" }),
    }),
  );
}

export async function applyMachineConfigurationRemoval(
  namespace: string,
  expectedPlanRevision: string,
) {
  return machineConfigurationTransactionSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/apply", {
      method: "POST",
      body: JSON.stringify({
        expected_plan_revision: expectedPlanRevision,
        namespace,
        operation: "remove",
      }),
    }),
  );
}

export async function previewMachineConfigurationRollback(transactionId: string) {
  return machineConfigurationRollbackPlanSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/rollback", {
      method: "POST",
      body: JSON.stringify({ execute: false, transaction_id: transactionId }),
    }),
  );
}

export async function applyMachineConfigurationRollback(
  transactionId: string,
  expectedPlanRevision: string,
) {
  return machineConfigurationRollbackReceiptSchema.parse(
    await requestJson<unknown>("/api/chat/machine-configuration/rollback", {
      method: "POST",
      body: JSON.stringify({
        execute: true,
        expected_plan_revision: expectedPlanRevision,
        transaction_id: transactionId,
      }),
    }),
  );
}

export type GoalRepositoryContext = {
  branch: string;
  identity: string;
  label: string;
  read_only: true;
};

const goalContextsSchema = z.object({
  ok: z.literal(true),
  goals: z.array(z.object({
    goal_id: z.string(),
    repository: z.object({
      branch: z.string(),
      identity: z.string(),
      label: z.string(),
      read_only: z.literal(true),
    }),
  })),
});

export async function fetchGoalContexts() {
  return goalContextsSchema.parse(
    await requestJson<unknown>("/api/chat/goals/contexts"),
  ).goals;
}

export type LarkApp = {
  active: boolean;
  app_ref: string;
  brand: string;
  health_error_code: string | null;
  label: string;
  ready: boolean;
  reply_ready: boolean;
};

const larkAppsSchema = z.object({
  ok: z.literal(true),
  apps: z.array(z.object({
    active: z.boolean(),
    app_ref: z.string(),
    brand: z.string(),
    health_error_code: z.string().nullable().default(null),
    label: z.string(),
    ready: z.boolean(),
    reply_ready: z.boolean().default(false),
  })),
});

export async function fetchLarkApps(options?: {verify: boolean; appRef?: string; signal?: AbortSignal}) {
  const query = options ? new URLSearchParams({verify: String(options.verify), ...(options.appRef ? {app_ref: options.appRef} : {})}) : null;
  return larkAppsSchema.parse(
    await requestJson<unknown>(`/api/chat/lark/apps${query ? `?${query}` : ""}`, {signal: options?.signal}),
  ).apps;
}

export type LarkAppSetup = {
  app_ref: string;
  error: string | null;
  setup_id: string;
  status: "starting" | "waiting_for_feishu" | "ready" | "failed" | "cancelled";
  verification_url: string | null;
};

const larkAppSetupSchema = z.object({
  ok: z.literal(true),
  app_ref: z.string(),
  error: z.string().nullable(),
  setup_id: z.string(),
  status: z.enum(["starting", "waiting_for_feishu", "ready", "failed", "cancelled"]),
  verification_url: z.string().url().nullable(),
});

export async function startLarkAppSetup(options: { appRef: string; brand: "feishu" | "lark" }) {
  return larkAppSetupSchema.parse(
    await requestJson<unknown>("/api/chat/lark/app-setups", {
      method: "POST",
      body: JSON.stringify({ app_ref: options.appRef, brand: options.brand }),
    }),
  );
}

export async function fetchLarkAppSetup(setupId: string) {
  return larkAppSetupSchema.parse(
    await requestJson<unknown>(`/api/chat/lark/app-setups/${encodeURIComponent(setupId)}`),
  );
}

export async function cancelLarkAppSetup(setupId: string) {
  return larkAppSetupSchema.parse(
    await requestJson<unknown>(`/api/chat/lark/app-setups/${encodeURIComponent(setupId)}`, {
      method: "DELETE",
    }),
  );
}

export type LarkGroupChat = { chat_id: string; chat_name: string };
export type LarkCaptureScope = "addressed_only" | "configured_chat_all";
export type LarkIngressMode = "live_steering" | "session_queue" | "async_inbox" | "direct_session";
export type LarkReplyMode = "topic_reply";
const larkTopicEventRejectionReasons = [
  "invalid_event",
  "binding_unavailable",
  "chat_mismatch",
  "topic_mismatch",
  "route_ambiguous",
  "self_message",
  "invalid_routing_state",
  "not_addressed",
  "historical_context_only",
  "bot_message",
  "human_identity_unverified",
] as const;
export type LarkTopicEventRejectionReason = typeof larkTopicEventRejectionReasons[number];
export type LarkPermissionGuidance = {
  action: "enable_application_scopes_and_publish";
  api_document_url: string;
  capability: "group_history_pagination";
  identity: "bot";
  required_scopes: ["im:message.group_msg", "im:message.group_msg.include_bot:read"];
  schema_version: "lark_bot_group_history_permission_guidance_v0";
};

const larkGroupChatsSchema = z.object({
  ok: z.literal(true),
  chats: z.array(z.object({ chat_id: z.string(), chat_name: z.string(), selected: z.boolean().default(false) })),
});

export async function fetchLarkGroupChats(appRef: string, query?: string) {
  const params = new URLSearchParams({ app_ref: appRef });
  if (query) params.set("query", query);
  return larkGroupChatsSchema.parse(
    await requestJson<unknown>(`/api/chat/lark/chats?${params.toString()}`),
  ).chats;
}

export type LarkGoalConnection = {
  conversation_kind?: "goal" | "manager";
  turn_trigger?: "addressed" | "human_messages";
  read_goal_scope?: "all_registered" | "selected" | null;
  agent_id: string | null;
  connection_id: string;
  app_label: string;
  app_ref: string;
  capture_scope: LarkCaptureScope;
  chat_name: string;
  enabled: boolean;
  goal_id: string;
  goal_title: string;
  health_error_code: string | null;
  history_permission_guidance: LarkPermissionGuidance | null;
  incoming_mode: "mentions" | "all";
  ingress_mode: LarkIngressMode;
  event_count: number;
  last_event_reason: LarkTopicEventRejectionReason | null;
  last_event_status: string | null;
  listener_error_code: string | null;
  listener_status: "starting" | "listening" | "retrying" | "stopped" | null;
  replied_count: number;
  reply_ready: boolean;
  reply_mode: LarkReplyMode;
  session_bound: boolean;
  target_ref: string;
  topic_name: string;
  topic_setup_required: boolean;
};

const larkConnectionsSchema = z.object({
  ok: z.literal(true),
  connections: z.array(z.object({
    conversation_kind: z.enum(["goal", "manager"]).default("goal"),
    turn_trigger: z.enum(["addressed", "human_messages"]).default("addressed"),
    read_goal_scope: z.enum(["all_registered", "selected"]).nullable().default("selected"),
    agent_id: z.string().nullable().default(null),
    connection_id: z.string(),
    app_label: z.string(),
    app_ref: z.string(),
    capture_scope: z.enum(["addressed_only", "configured_chat_all"]).default("addressed_only"),
    chat_name: z.string(),
    enabled: z.boolean(),
    goal_id: z.string(),
    goal_title: z.string(),
    health_error_code: z.string().nullable().default(null),
    history_permission_guidance: z.object({
      action: z.literal("enable_application_scopes_and_publish"),
      api_document_url: z.string().url(),
      capability: z.literal("group_history_pagination"),
      identity: z.literal("bot"),
      required_scopes: z.tuple([
        z.literal("im:message.group_msg"),
        z.literal("im:message.group_msg.include_bot:read"),
      ]),
      schema_version: z.literal("lark_bot_group_history_permission_guidance_v0"),
    }).nullable().default(null),
    incoming_mode: z.enum(["mentions", "all"]),
    ingress_mode: z.enum(["live_steering", "session_queue", "async_inbox", "direct_session"]).default("async_inbox"),
    event_count: z.number().int().nonnegative().default(0),
    last_event_reason: z.enum(larkTopicEventRejectionReasons).nullable().default(null).catch(null),
    last_event_status: z.string().nullable().default(null),
    listener_error_code: z.string().nullable().default(null),
    listener_status: z.enum(["starting", "listening", "retrying", "stopped"]).nullable().default(null),
    replied_count: z.number().int().nonnegative().default(0),
    reply_ready: z.boolean().default(false),
    reply_mode: z.literal("topic_reply"),
    session_bound: z.boolean().default(false),
    target_ref: z.string(),
    topic_name: z.string(),
    topic_setup_required: z.boolean(),
  })),
});

export async function fetchLarkConnections(signal?: AbortSignal) {
  return larkConnectionsSchema.parse(
    await requestJson<unknown>("/api/chat/lark/connections", {signal}),
  ).connections;
}

export async function connectLarkGoalTopic(options: {
  conversationKind?: "goal" | "manager";
  turnTrigger?: "addressed" | "human_messages";
  readGoalScope?: "all_registered" | "selected";
  agentBindings?: Array<{ agentId: string; appRef: string }>;
  agentId?: string;
  appRef?: string;
  captureScope: LarkCaptureScope;
  chatId?: string;
  chatName?: string;
  connectionId?: string;
  execute: boolean;
  goalId: string;
  incomingMode: "mentions" | "all";
  ingressMode: LarkIngressMode;
  replyMode: LarkReplyMode;
}) {
  return goalChannelOperationSchema.parse(
    await requestJson<unknown>("/api/chat/lark/connections", {
      method: "POST",
      body: JSON.stringify({
        ...(options.agentBindings ? {
          agent_bindings: options.agentBindings.map((binding) => ({
            agent_id: binding.agentId,
            app_ref: binding.appRef,
          })),
        } : {}),
        ...(options.agentId ? { agent_id: options.agentId } : {}),
        ...(options.appRef ? { app_ref: options.appRef } : {}),
        ...(options.connectionId ? { connection_id: options.connectionId } : {}),
        conversation_kind: options.conversationKind ?? "goal",
        ...(options.turnTrigger ? { turn_trigger: options.turnTrigger } : {}),
        ...(options.readGoalScope ? { read_goal_scope: options.readGoalScope } : {}),
        capture_scope: options.captureScope,
        chat_id: options.chatId,
        chat_name: options.chatName,
        execute: options.execute,
        goal_id: options.goalId,
        incoming_mode: options.incomingMode,
        ingress_mode: options.ingressMode,
        reply_mode: options.replyMode,
      }),
    }),
  );
}

export async function disconnectLarkGoalTopic(goalId: string, connectionId: string) {
  const params = new URLSearchParams({ goal_id: goalId, connection_id: connectionId });
  return goalChannelOperationSchema.parse(
    await requestJson<unknown>(`/api/chat/lark/connections?${params.toString()}`, {
      method: "DELETE",
    }),
  );
}

export { CONTEXTS as usageContexts } from "../../../../../loopx/control_plane/runtime/usage_statistics_contract.js";
const usageStatisticsSchema = z.object({
  consent: z.enum(["default", "enabled", "disabled"]),
  sending: z.boolean(), blocked_by: z.string().nullable(), endpoint: z.string().nullable(),
  policy: z.string(), notice_required: z.boolean(),
  notice: z.object({ version: z.number(), endpoint: z.string(), policy: z.string() }),
  automatic_notice_required: z.boolean(),
  next_payload: z.unknown(), aggregate_preview: z.unknown(), goal_preview: z.unknown(),
  diagnostic_preview: z.unknown().optional(), diagnostic_dropped: z.number().optional(),
  stored_context: z.string().optional(), effective_context: z.string().optional(), context_source: z.string().optional(),
  installation_preview: z.unknown().optional(),
  identity_scope: z.string().optional(), delivery_history: z.array(z.object({
    day: z.string(), channel: z.enum(["heartbeat", "cli", "goal", "installation"]), rows: z.number(),
    status: z.enum(["accepted", "rejected", "unavailable"]),
  })).optional(),
});
export type UsageStatistics = z.infer<typeof usageStatisticsSchema>;
export async function setUsageContext(context: string): Promise<UsageStatistics> {
  return usageStatisticsSchema.parse(await requestJson<unknown>("/api/chat/usage-statistics",
    { method: "POST", body: JSON.stringify({ context }) }));
}
export async function usageStatistics(enabled?: boolean): Promise<UsageStatistics> {
  return usageStatisticsSchema.parse(await requestJson<unknown>("/api/chat/usage-statistics",
    enabled === undefined ? undefined : { method: "POST", body: JSON.stringify({ enabled }) }));
}

export async function acknowledgeUsageNotice(notice: UsageStatistics["notice"]): Promise<UsageStatistics> {
  return usageStatisticsSchema.parse(await requestJson<unknown>("/api/chat/usage-statistics",
    { method: "POST", body: JSON.stringify({ notice }) }));
}


export const goalOwnershipSchema = z.object({
  ok: z.literal(true), goal_id: z.string(), canonical: z.boolean(),
  current_mode: z.enum(HANDOFF_MODES).nullable(), provider_revision: z.string().nullable(),
});
export const ownershipPreviewSchema = z.object({
  ok: z.boolean(), goal_id: z.string(), status: z.string(),
  preview_id: z.string().regex(/^[a-f0-9]{32}$/),
  plan_sha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  previous_mode: z.enum(HANDOFF_MODES).optional(), handoff_mode: z.enum(EXECUTION_HANDOFF_MODES).optional(),
  preserved_claim_count: z.number().int().nonnegative().optional(),
  retained_lease_count: z.number().int().nonnegative(), conflict_count: z.number().int().nonnegative(),
  conflicts: z.array(z.object({ todo_id: z.string().optional(), reason_code: z.string().optional() })),
  backup_verified: z.boolean(), execution_authority_granted: z.literal(false),
  reason_code: z.string().optional(), current: goalOwnershipSchema.nullable().optional(),
});
export type GoalOwnership = z.infer<typeof goalOwnershipSchema>;
export type OwnershipPreview = z.infer<typeof ownershipPreviewSchema>;
export type ExecutionOwnershipMode = ExecutionHandoffMode;

export async function fetchGoalOwnership(goalId: string) {
  return goalOwnershipSchema.parse(await requestJson<unknown>(`/api/chat/goal-ownership?${new URLSearchParams({goal_id: goalId})}`));
}
export async function updateGoalOwnership(body: { goal_id: string; mode: ExecutionOwnershipMode } | {
  goal_id: string; preview_id: string; plan_sha256: string;
}) {
  const action = "mode" in body ? "preview" : "apply";
  try {
    return ownershipPreviewSchema.parse(await requestJson<unknown>(`/api/chat/goal-ownership/${action}`, {
      method: "POST", body: JSON.stringify(body),
    }));
  } catch (error) {
    if (error instanceof ChatApiError && error.payload.http_status === 409 && error.payload.status === "failed") {
      return ownershipPreviewSchema.parse(error.payload);
    }
    throw error;
  }
}


const privateAgentSessionSchema = z.object({session_id: z.string(), goal_id: z.string(), agent_id: z.string(), executor_endpoint_id: z.string()});
const privateAgentTargetSchema = privateAgentSessionSchema.extend({target_ref: z.string(), host_ref: z.string(), goal_instance_id: z.string().nullable()});
const privateConversationSchema = z.object({
  binding_id: z.string(), app_ref: z.string(), context_kind: z.enum(["project", "steward"]),
  project_ref: z.string(), project_title: z.string(), context_available: z.boolean(), executor_endpoint_id: z.string(),
  grant: z.enum(["workspace_read", "workspace_write", "portfolio_read"]), goal_count: z.number().int().default(0), listener_status: z.string(),
  goal_scope: z.enum(["all_registered", "selected"]).nullable().optional(),
  audience: z.enum(["owner", "group"]).default("owner"), group_count: z.number().int().default(0),
  pending_count: z.number().int(), recovery_count: z.number().int(),
  ingress: z.object({status: z.enum(["unobserved", "verified", "gap", "unavailable"]),
    last_checked_at: z.number().nullable(), last_complete_at: z.number().nullable(),
    source_gap_count: z.number().int().nonnegative(), without_reply_count: z.number().int().nonnegative()}).optional(),
  owner_attention: z.discriminatedUnion("available", [
    z.object({available: z.literal(true), enabled: z.boolean(), revision: z.number().int(),
      last_checked_at: z.number().nullable(), error_code: z.string().nullable(),
      source_coverage: z.object({goal_count: z.number().int().nonnegative(),
        read_goal_count: z.number().int().nonnegative(), unavailable_goal_count: z.number().int().nonnegative()}).nullable().optional(),
      source_failures: z.array(z.object({goal_id: z.string(),
        error_code: z.enum(["canonical_authority_required", "source_unavailable"])})).optional(),
      pending_count: z.number().int(), unconfirmed_count: z.number().int(), failed_count: z.number().int()}),
    z.object({available: z.literal(false), error_code: z.string()}),
  ]).optional(),
  managed_steward: z.discriminatedUnion("available", [
    z.object({available: z.literal(true), prepared: z.boolean(), scheduling_enabled: z.boolean(),
      execution_available: z.boolean().optional(), execution_error_code: z.string().optional(),
      goal_active: z.boolean().optional(), monitor: z.object({todo_id: z.string(), status: z.string(),
        cadence_minutes: z.number().int().nullable(), next_due_at: z.string().nullable(),
        last_checked_at: z.string().nullable()}).nullable().optional(),
      report: z.discriminatedUnion("available", [
        z.object({available: z.literal(true), enabled: z.boolean(), configured: z.boolean(),
          schedule: z.record(z.string(), z.unknown()).nullable().optional(), next_due_at: z.string().nullable().optional(),
          retained_window_due_at: z.string().nullable().optional(), generation_id: z.string().nullable().optional(),
          delivery_status: z.string().optional(), execution_failed: z.boolean().optional(), publication_verified: z.boolean().optional(),
          destination: z.string().optional()}),
        z.object({available: z.literal(false), error_code: z.string()}),
      ]).optional(),
      goal_id: z.string().optional(), agent_id: z.string().optional(), session_id: z.string().optional()}),
    z.object({available: z.literal(false), error_code: z.string(),
      execution_available: z.boolean().optional(), execution_error_code: z.string().optional()}),
  ]).optional(),
  agent_candidates: z.array(privateAgentSessionSchema).default([]), agent_targets: z.array(privateAgentTargetSchema).default([]),
});
const privateConversationStopRecoverySchema = z.object({binding_id: z.string(), project_ref: z.string(),
  project_title: z.string(), pending_count: z.number().int().positive()});
export type PrivateConversationStopRecovery = z.infer<typeof privateConversationStopRecoverySchema>;
const privateConversationsSchema = z.object({ok: z.literal(true), revision: z.number().int(),
  app_steward_binding_id: z.string().regex(/^[a-f0-9]{24}$/).nullable().default(null),
  connections: z.array(privateConversationSchema), stop_recoveries: z.array(privateConversationStopRecoverySchema).default([])});
export type PrivateConversation = z.infer<typeof privateConversationSchema>;
const boundConversationSourceSchema = privateConversationSchema.pick({
  binding_id: true, app_ref: true, context_kind: true, project_title: true, executor_endpoint_id: true,
}).extend({audience: z.literal("owner"), context_available: z.literal(true)});
export type BoundConversationSource = z.infer<typeof boundConversationSourceSchema>;

/** Session and display source come from one fresh Core-verified read. */
export async function resolveBoundConversation(target: BoundConversationTarget, signal?: AbortSignal) {
  const listed = await fetchChatSessions({bound: target, signal});
  const parsed = boundConversationSourceSchema.safeParse(listed.conversation_source);
  if (!parsed.success || parsed.data.binding_id !== target.bindingId) {
    throw new Error("This service cannot verify the original conversation. Update LoopX and retry.");
  }
  const session = listed.sessions[0], source = parsed.data;
  // Core qualifies this exact Session, including its original quota fallback.
  // The connection's configured preference need not be its active executor.
  return {session, source};
}

export async function fetchPrivateConversations(signal?: AbortSignal) {
  return privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/lark/private-conversations", { signal }));
}
const appStewardTargetSchema = z.union([
  z.object({ok: z.literal(true), binding_id: z.null(), session_id: z.null()}),
  z.object({ok: z.literal(true), binding_id: z.string().regex(/^[a-f0-9]{24}$/),
    session_id: z.string().regex(/^[A-Za-z0-9._-]{1,160}$/)}),
]);
export async function fetchAppStewardTarget(signal?: AbortSignal): Promise<BoundConversationTarget | null> {
  const target = appStewardTargetSchema.parse(await requestJson<unknown>("/api/chat/app-steward", {signal}));
  return target.binding_id === null ? null : {bindingId: target.binding_id, sessionId: target.session_id};
}
export async function selectAppSteward(bindingId: string | null, revision: number) {
  const result = privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/app-steward", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({binding_id: bindingId, revision}),
  }));
  if (typeof window !== "undefined") window.dispatchEvent(new Event("loopx:app-steward-changed"));
  return result;
}
export async function connectPrivateConversation(appRef: string, projectRef: string, executor: string, contextKind: "project" | "steward" = "project", projectGrant: "workspace_read" | "workspace_write" = "workspace_write", groupIds?: string[]) {
  return privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/lark/private-conversations", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({app_ref: appRef, project_ref: projectRef, executor_endpoint_id: executor, context_kind: contextKind, project_grant: projectGrant,
      ...(contextKind === "steward" ? {goal_scope: "all_registered"} : {}),
      ...(groupIds !== undefined ? {audience: "group", group_ids: groupIds} : {})}),
  }));
}
export async function disconnectPrivateConversation(bindingId: string, revision: number) {
  return privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/lark/private-conversations", {
    method: "DELETE", headers: {"Content-Type": "application/json"}, body: JSON.stringify({binding_id: bindingId, revision}),
  }));
}

const stewardPreparationSchema = z.object({ok: z.literal(true), applied: z.boolean(), plan: z.object({
  schema_version: z.literal("loopx_steward_managed_mode_plan_v0"), plan_id: z.string().regex(/^[a-f0-9]{64}$/),
  session_id: z.string(), goal_id: z.string(), already_prepared: z.boolean(),
  scheduling_enabled: z.literal(false), model_invoked: z.literal(false),
})});
export async function prepareManagedSteward(bindingId: string) {
  const path = "/api/chat/lark/private-conversations/managed-steward";
  const send = (body: unknown) => requestJson<unknown>(path, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const preview = stewardPreparationSchema.parse(await send({binding_id: bindingId, execute: false}));
  if (preview.plan.already_prepared) return preview;
  const applied = stewardPreparationSchema.parse(await send({binding_id: bindingId, execute: true,
    session_id: preview.plan.session_id, plan_id: preview.plan.plan_id}));
  if (!applied.applied) throw new Error("The original steward identity has not been prepared.");
  return applied;
}

const stewardMonitorPlanSchema = z.object({ok: z.literal(true), applied: z.boolean(), plan: z.object({
  schema_version: z.literal("loopx_steward_monitor_plan_v0"), plan_id: z.string().regex(/^[a-f0-9]{64}$/),
})});
export async function configureStewardMonitor(bindingId: string, operation: "enable" | "pause", cadenceMinutes: number) {
  const path = "/api/chat/lark/private-conversations/managed-steward/monitor";
  const send = (body: unknown) => requestJson<unknown>(path, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const request = {binding_id: bindingId, operation, cadence_minutes: cadenceMinutes};
  const preview = stewardMonitorPlanSchema.parse(await send({...request, execute: false}));
  const applied = stewardMonitorPlanSchema.parse(await send({...request, execute: true, plan_id: preview.plan.plan_id}));
  if (!applied.applied) throw new Error("The original native monitor setting was not applied.");
  return applied;
}

export async function changePrivateAgentTarget(bindingId: string, revision: number, target: {session_id: string} | {target_ref: string}) {
  return privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/lark/private-conversations/agent-targets", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({binding_id: bindingId, revision, ...target}),
  }));
}

const stewardReportPlanSchema = z.object({ok: z.literal(true), applied: z.boolean(), stop_status: z.string().optional(), plan: z.object({
  schema_version: z.literal("loopx_steward_report_plan_v0"), plan_id: z.string().regex(/^[a-f0-9]{64}$/),
})});
export async function configureStewardReport(bindingId: string, operation: "enable" | "pause" | "cancel", schedule?: Record<string, unknown>) {
  const path = "/api/chat/lark/private-conversations/managed-steward/report";
  const send = (body: unknown) => requestJson<unknown>(path, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const request = {binding_id: bindingId, operation, ...(operation === "enable" ? {schedule} : {})};
  const preview = stewardReportPlanSchema.parse(await send({...request, execute: false}));
  const applied = stewardReportPlanSchema.parse(await send({...request, execute: true, plan_id: preview.plan.plan_id}));
  if (!applied.applied) throw new Error("The original report setting was not applied.");
  if (applied.stop_status === "unconfirmed") throw new Error("汇报计划已取消；正在生成的任务是否停止尚未确认。Report cancelled; the active task's stop is unconfirmed.");
  return applied;
}

export async function changeOwnerNotifications(bindingId: string, bindingRevision: number, revision: number, enabled: boolean) {
  return privateConversationsSchema.parse(await requestJson<unknown>("/api/chat/lark/private-conversations/owner-notifications", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({binding_id: bindingId, binding_revision: bindingRevision, revision, enabled}),
  }));
}


// Display validation only; finding status semantics remain owned by Explore.
const exploreResultPageSchema = z.object({
  ok: z.literal(true), goal_id: z.string(), total: z.number().int().nonnegative(),
  next_cursor: z.string().nullable(),
  items: z.array(z.object({
    finding_id: z.string(), finding: z.string(), summary: z.string(), status: z.string(),
    node_id: z.string(), question: z.string(), scope: z.string(), agent_id: z.string(),
    evidence_refs: z.array(z.string()), last_updated_at: z.string(),
    measurements: z.unknown().transform(value => normalizeExploreMeasurements(value)).optional(),
    measurement_error: z.string().optional(),
    work_yardstick: z.unknown().transform(capture => normalizeWorkYardstickCapture({capture})).optional(),
    linked_todos_available: z.boolean().optional(),
    linked_todos: z.array(z.object({
      todo_id: z.string(), text: z.string(), status: z.string(), claimed_by: z.string(),
    })),
  })),
});
export type ExploreResultPage = z.infer<typeof exploreResultPageSchema>;
export async function fetchExploreResults(goalId: string, cursor?: string, readSourceUrl?: string, originalGoalId = goalId) {
  const query = new URLSearchParams({goal_id: goalId, view: "findings", ...(cursor ? {cursor} : {})});
  const page = exploreResultPageSchema.parse(await requestJson<unknown>(sourceReadApiUrl(readSourceUrl, `/api/chat/explore-results?${query}`)));
  if (page.goal_id !== originalGoalId) throw new Error("Explore result Goal mismatch");
  for (const item of page.items) if (item.work_yardstick) normalizeWorkYardstickCapture({
    capture: item.work_yardstick, goal_id: originalGoalId, agent_id: item.agent_id, evidence_refs: item.evidence_refs,
  });
  return page;
}

const researchHistoryTimingSchema = z.object({
  ok: z.literal(true),
  goal_id: z.string(),
  source_namespace: z.string().nullable(),
  first_recorded_at: z.iso.datetime().nullable(),
  last_recorded_at: z.iso.datetime().nullable(),
  untimed_record_count: z.number().int().nonnegative(),
  strategy_recorded_at: z.iso.datetime().nullable(),
}).refine(value => value.first_recorded_at === null
  ? value.last_recorded_at === null
  : value.last_recorded_at !== null && Date.parse(value.first_recorded_at) <= Date.parse(value.last_recorded_at));
export type ResearchHistoryTiming = z.infer<typeof researchHistoryTimingSchema>;

export async function fetchResearchHistoryTiming(
  goalId: string, originalGoalId: string, namespace: string | null, directoryUrl: string | undefined, signal: AbortSignal,
): Promise<ResearchHistoryTiming> {
  const statusUrl = namespace ? `/status.json?source_namespace=${namespace}` : directoryUrl;
  const query = new URLSearchParams({goal_id: goalId, view: "history-timing"});
  const timing = researchHistoryTimingSchema.parse(await requestJson<unknown>(
    sourceReadApiUrl(statusUrl, `/api/chat/explore-results?${query}`), {signal},
  ));
  if (timing.goal_id !== originalGoalId || timing.source_namespace !== namespace) {
    throw new Error("Research timing source mismatch");
  }
  return timing;
}
