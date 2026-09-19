/** Pure field intent planning. Admission, leases, validation and commit stay
 * with the calling lifecycle transaction; this result grants no write right. */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { normalizeTodoAgent, stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { AuthorityStoreProtocolError } from "../coordination/authority_store_codec.ts";
import {
  buildTodoCompletionMetadataUpdates,
  TODO_COMPLETION_STATE_REQUEST_SCHEMA,
} from "./completion_state.ts";
import { validateLegacyContinuationPolicyRepair } from "./legacy_continuation_policy_migration.ts";
import {
  normalizeTodoResumeWhen,
  TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
  UNSUPPORTED_TODO_RESUME_CONDITION_MESSAGE,
} from "./resume_condition.ts";
import { MONITOR_METADATA_FIELDS, planMonitorMetadata, TODO_MONITOR_METADATA_REQUEST_SCHEMA } from "./monitor_metadata.ts";

export const TODO_FIELD_UPDATE_REQUEST_SCHEMA = "loopx_todo_field_update_request_v0";
export const TODO_FIELD_UPDATE_RESULT_SCHEMA = "loopx_todo_field_update_result_v0";

const STATUS = ["open", "done", "blocked", "deferred"] as const;
type Status = typeof STATUS[number];
export interface TodoFieldUpdatePlan extends JsonObject {
  schema_version: typeof TODO_FIELD_UPDATE_RESULT_SCHEMA;
  normalized_status: Status | null;
  target_status: Status;
  metadata_updates: JsonObject;
}
const STRING_FIELDS = ["note", "evidence", "completion_turn_key", "reason", "task_class",
  "action_kind", "task_domain", "task_repository", "continuation_policy"] as const;
const PRESENT_FIELDS = ["required_write_scopes", "required_capabilities", "target_capabilities",
  "explore_result_node_refs", "decision_scope", "required_decision_scopes", "decision_outcome",
  "decision_scope_outcomes"] as const;
const FLAGS = ["clear_claim", "claim_only", "clear_user_binding", "clear_blocks_agent",
  "clear_global_gate", "clear_resume_when"] as const;
const INTENT_FIELDS = new Set<string>([...STRING_FIELDS, ...PRESENT_FIELDS, ...FLAGS,
  "status", "claimed_by", "bound_agent", "goal_bound", "blocks_agent", "excluded_agents",
  "global_gate", "unblocks_todo_id", "successor_todo_ids", "completion_continuation",
  "completion_recovery", "completion_metadata_updates_override", "resume_when",
  "resume_monitor_generation", "no_followup", "monitor_metadata"]);

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  return value;
}

function present(value: unknown): boolean {
  return value !== null && value !== undefined;
}

// Imported Markdown may carry an invalid historical claim. Match the existing
// codec's nullable normalization instead of treating that token as ownership.
function existingAgent(value: unknown): string | null {
  try {
    return normalizeTodoAgent(value, "agent_id");
  } catch (error) {
    if (error instanceof AuthorityStoreProtocolError) return null;
    throw error;
  }
}

function validateIntent(value: unknown): JsonObject {
  const intent = requireJsonObject(value, "Todo field intent");
  for (const key of Object.keys(intent)) {
    if (!INTENT_FIELDS.has(key)) throw new EffectRuntimeRequestError(`Todo field plan does not own ${key}`);
  }
  for (const field of [...FLAGS, "goal_bound", "global_gate", "no_followup"]) {
    if (present(intent[field]) && typeof intent[field] !== "boolean") {
      throw new EffectRuntimeRequestError(`${field} must be a boolean`);
    }
  }
  for (const field of [...STRING_FIELDS, "status", "claimed_by", "bound_agent", "blocks_agent",
    "unblocks_todo_id", "resume_when", "completion_continuation", "completion_recovery"]) {
    optionalString(intent[field], field);
  }
  return intent;
}

function bindingUpdates(block: JsonObject, intent: JsonObject, todoId: string): JsonObject {
  const updates: JsonObject = {};
  if (intent.clear_claim) updates.claimed_by = null;
  else if (intent.claimed_by) {
    const existing = existingAgent(block.claimed_by);
    if (intent.claim_only && existing && existing !== intent.claimed_by) {
      throw new EffectRuntimeRequestError(`todo_id '${todoId}' is already claimed_by='${existing}'; ` +
        "clear or transfer the claim explicitly before claiming it");
    }
    updates.claimed_by = intent.claimed_by;
  }
  if (intent.clear_user_binding) {
    updates.bound_agent = null;
    updates.goal_bound = null;
  } else if (intent.bound_agent) {
    updates.bound_agent = intent.bound_agent;
    updates.goal_bound = null;
  } else if (present(intent.goal_bound)) {
    updates.bound_agent = null;
    updates.goal_bound = intent.goal_bound;
  }
  if (intent.blocks_agent) updates.blocks_agent = intent.blocks_agent;
  else if (intent.clear_blocks_agent) updates.blocks_agent = null;
  if (present(intent.excluded_agents)) updates.excluded_agents = intent.excluded_agents;
  if (intent.clear_global_gate) updates.global_gate = null;
  else if (Object.hasOwn(intent, "global_gate")) {
    // The public record is presence-based: false means the gate is cleared,
    // never a second persisted state that can shadow a scoped gate.
    updates.global_gate = intent.global_gate === true ? true : null;
  }
  return updates;
}

function completionUpdates(block: JsonObject, intent: JsonObject, targetStatus: Status,
  normalizedStatus: Status | null): JsonObject {
  if (present(intent.completion_metadata_updates_override)) {
    const updates = requireJsonObject(intent.completion_metadata_updates_override, "completion metadata override");
    if (Object.entries(updates).some(([key, value]) =>
      !["completion_continuation", "completion_recovery"].includes(key) || typeof value !== "string")) {
      throw new EffectRuntimeRequestError("TypeScript Todo completion metadata updates shape mismatch");
    }
    return {...updates};
  }
  const result = buildTodoCompletionMetadataUpdates({
    schema_version: TODO_COMPLETION_STATE_REQUEST_SCHEMA,
    block: {no_followup: block.no_followup ?? "", completion_continuation: block.completion_continuation ?? "",
      successor_todo_ids: block.successor_todo_ids ?? []},
    target_status: targetStatus, normalized_status: normalizedStatus,
    completion_continuation: intent.completion_continuation ?? null,
    completion_recovery: intent.completion_recovery ?? null,
    no_followup: intent.no_followup ?? null, successor_todo_ids: intent.successor_todo_ids ?? null,
  });
  return requireJsonObject(result.updates, "completion metadata updates");
}

export function planTodoFieldUpdate(value: unknown): TodoFieldUpdatePlan {
  const request = requireJsonObject(value, "Todo field update request");
  if (request.schema_version !== TODO_FIELD_UPDATE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Todo field update request schema mismatch");
  }
  const block = requireJsonObject(request.todo, "Todo field update source");
  const todoId = requireNonEmptyString(block.todo_id, "todo_id");
  const updatedAt = requireNonEmptyString(request.updated_at, "updated_at");
  const intent = validateIntent(request.intent);
  const resumeWhen = intent.resume_when ? normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION, resume_when: intent.resume_when,
  }) : null;
  if (intent.resume_when && !resumeWhen) {
    throw new EffectRuntimeRequestError(UNSUPPORTED_TODO_RESUME_CONDITION_MESSAGE);
  }
  if (resumeWhen && intent.clear_resume_when) {
    throw new EffectRuntimeRequestError("todo update accepts either resume_when or clear_resume_when, not both");
  }
  validateLegacyContinuationPolicyRepair(block, intent, todoId);
  const status = intent.status ? stripPythonWhitespace(String(intent.status)).toLowerCase() : null;
  if (status !== null && !STATUS.includes(status as Status)) {
    throw new EffectRuntimeRequestError("todo status must be one of: open, done, blocked, deferred");
  }
  const normalizedStatus = status as Status | null;
  const targetStatus = normalizedStatus ?? (block.status || "open") as Status;
  if (!STATUS.includes(targetStatus)) throw new EffectRuntimeRequestError("invalid source Todo status");
  if (targetStatus === "deferred" && intent.clear_resume_when) {
    throw new EffectRuntimeRequestError("cannot clear resume_when while todo status remains deferred");
  }
  if (intent.claim_only && targetStatus !== "open") {
    throw new EffectRuntimeRequestError(`todo claim requires status=open; todo_id '${todoId}' is status='${targetStatus}'`);
  }
  const updates: JsonObject = {todo_id: todoId, status: targetStatus};
  if (normalizedStatus === "done" && !block.completed_at) updates.completed_at = updatedAt;
  else if (normalizedStatus && normalizedStatus !== "done") updates.completed_at = null;
  Object.assign(updates, bindingUpdates(block, intent, todoId));
  if (intent.unblocks_todo_id) updates.unblocks_todo_id = intent.unblocks_todo_id;
  if (present(intent.successor_todo_ids)) updates.successor_todo_ids = intent.successor_todo_ids;
  if (intent.clear_resume_when) {
    updates.resume_when = null;
    updates.resume_monitor_generation = null;
  } else if (resumeWhen) {
    updates.resume_when = resumeWhen;
    updates.resume_monitor_generation = resumeWhen.startsWith("monitor_changed:")
      ? intent.resume_monitor_generation ?? null : null;
  }
  if (present(intent.no_followup)) updates.no_followup = intent.no_followup;
  Object.assign(updates, completionUpdates(block, intent, targetStatus, normalizedStatus));
  // Presence, rather than truthiness, is the mutation contract. An explicitly
  // empty scalar clears the compatibility field; omitted values remain
  // untouched. This fixes the old `if (intent[field])` conflation of omission
  // and an intentional clear.
  for (const field of STRING_FIELDS) {
    if (Object.hasOwn(intent, field)) {
      // `note` is a display annotation whose historical empty-input contract
      // is omission/preservation. Other scalar metadata uses empty text as an
      // explicit clear once it crosses this typed boundary.
      if (field === "note" && typeof intent[field] === "string" && !intent[field].trim()) continue;
      updates[field] = intent[field];
    }
  }
  for (const field of PRESENT_FIELDS) {
    if (Object.hasOwn(intent, field)) updates[field] = intent[field];
  }
  // Public update carries the effective scope and raw observation once. The
  // field plan composes validation and generation without another RPC.
  const monitorPlan = request.monitor_context == null ? null : planMonitorMetadata({
    ...requireJsonObject(request.monitor_context, "monitor context"),
    schema_version: TODO_MONITOR_METADATA_REQUEST_SCHEMA, existing: block, generated_at: updatedAt,
    reactivate: block.status === "done" && normalizedStatus === "open",
  });
  if (monitorPlan?.transition && block.status === "done" && normalizedStatus === "open") {
    // The new observation cycle cannot carry terminal decisions as current
    // state. Historical operation receipts retain the original completion.
    updates.no_followup = null;
    updates.completion_continuation = null;
    updates.completion_recovery = null;
    updates.completion_turn_key = null;
  }
  const monitor = monitorPlan?.metadata ?? intent.monitor_metadata;
  if (present(monitor)) {
    const metadata = requireJsonObject(monitor, "monitor metadata");
    for (const field of MONITOR_METADATA_FIELDS) if (Object.hasOwn(metadata, field)) updates[field] = metadata[field];
  }
  return {schema_version: TODO_FIELD_UPDATE_RESULT_SCHEMA, normalized_status: normalizedStatus,
    target_status: targetStatus, metadata_updates: updates,
    ...(monitorPlan?.transition ? {monitor_poll_transition: monitorPlan.transition} : {})};
}
