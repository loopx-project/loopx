/** Bounded native planning edits. Compose the public rule owner in-process;
 * this plan never grants authority, reads storage, or emits a lease effect. */
import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { AuthorityStoreProtocolError } from "../coordination/authority_store_codec.ts";
import { compactPythonWhitespace } from "../coordination/todo_agents.ts";
import { normalizeTodoId } from "../work_items/task_lease_acquire.ts";
import { planPublicTodoUpdate, TODO_PUBLIC_UPDATE_REQUEST_SCHEMA } from "./public_update.ts";
import { normalizeTodoWorkRequirements, TODO_WORK_REQUIREMENT_FIELDS } from "./work_requirements.ts";
import {normalizeTodoOwnershipIntent, TODO_OWNERSHIP_INTENT_FIELDS} from "./authoring_scope.ts";
import {
  normalizeTodoDecisionScope,
  normalizeTodoRequiredDecisionScopes,
  TODO_DECISION_METADATA_FIELDS,
  validateTodoDecisionMetadata,
} from "./decision_metadata.ts";

import { normalizeMonitorConfiguration, type MonitorPollObservation } from "./monitor_metadata.ts";

const STRINGS = new Set(["status", "evidence", "reason", "task_class", "continuation_policy",
  "resume_when", "unblocks_todo_id", "bound_agent", "blocks_agent"]);
const BOOLEANS = new Set(["clear_resume_when", "no_followup", "goal_bound", "clear_blocks_agent",
  "global_gate", "clear_global_gate"]);
const FIELDS = new Set([...STRINGS, ...BOOLEANS, "successor_todo_ids", "monitor_metadata",
  ...TODO_WORK_REQUIREMENT_FIELDS, ...TODO_OWNERSHIP_INTENT_FIELDS, ...TODO_DECISION_METADATA_FIELDS]);

/** A separate intent namespace preserves the shipped text/note patch and its
 * historical receipt encoding. Raw field patches do not gain new authority;
 * declarative decision metadata is admitted only through its typed codec. */
export function normalizeNativePlanningIntent(value: unknown): JsonObject {
  if (value === undefined || value === null) return {};
  const raw = requireJsonObject(value, "Todo planning intent");
  const intent: JsonObject = {};
  for (const [field, value] of Object.entries(raw)) {
    if (!FIELDS.has(field)) throw new AuthorityStoreProtocolError(`Todo planning update does not own ${field}`);
    if ((TODO_WORK_REQUIREMENT_FIELDS as readonly string[]).includes(field)) continue;
    if ((TODO_OWNERSHIP_INTENT_FIELDS as readonly string[]).includes(field)) continue;
    if (field === "monitor_metadata") {
      if (value != null) intent[field] = normalizeMonitorConfiguration(value);
      continue;
    }
    if (field === "decision_scope") {
      intent[field] = normalizeTodoDecisionScope(value, field);
      continue;
    }
    if (field === "required_decision_scopes") {
      intent[field] = normalizeTodoRequiredDecisionScopes(value, field);
      continue;
    }
    if (value === null) {
      // Null is an explicit clear for scalar planning metadata.  Ownership
      // fields use their dedicated clear switches and are normalized above.
      if (STRINGS.has(field)) intent[field] = null;
      continue;
    }
    if (STRINGS.has(field)) {
      if (typeof value !== "string") throw new AuthorityStoreProtocolError(`${field} must be a string`);
      const text = compactPythonWhitespace(value);
      intent[field] = field === "unblocks_todo_id" && text ? normalizeTodoId(text, field) : text;
    } else if (BOOLEANS.has(field)) {
      if (typeof value !== "boolean") throw new AuthorityStoreProtocolError(`${field} must be a boolean`);
      if (field !== "clear_resume_when" || value) intent[field] = value;
    } else {
      if (!Array.isArray(value)) throw new AuthorityStoreProtocolError("successor_todo_ids must be an array");
      intent[field] = [...new Set(value.map(item => normalizeTodoId(item, "successor_todo_id")))];
    }
  }
  return {...intent, ...normalizeTodoWorkRequirements(raw), ...normalizeTodoOwnershipIntent(raw)};
}

export function planNativeTodoUpdate(todo: JsonObject, intent: JsonObject,
  head: JsonObject, actor: string | null, agents: readonly string[], updatedAt: string,
  kind: "planning" | "user_completion" = "planning", observation?: MonitorPollObservation): {
    updates: JsonObject; monitorTransition?: JsonObject;
  } {
  if (kind === "user_completion" && todo.role !== "user") {
    throw new AuthorityStoreProtocolError("agent todo completion must use complete_goal_todo (loopx todo complete)");
  }
  validateTodoDecisionMetadata(todo, intent);
  const planned = planPublicTodoUpdate({schema_version: TODO_PUBLIC_UPDATE_REQUEST_SCHEMA,
    todo, intent, updated_at: updatedAt,
    context: {goal_id: head.goal_id, role: todo.role, actor_agent_id: actor,
      registered_agents: [...agents], items: head.todos,
      monitor_observation: observation ?? null, enforce_monitor_boundedness: observation === undefined}});
  if ((planned.target_status === "done" && kind === "planning") ||
      (planned.monitor_poll_transition != null && observation === undefined)) {
    throw new AuthorityStoreProtocolError("native planning update cannot complete work or commit a Monitor observation");
  }
  const updates = requireJsonObject(planned.metadata_updates, "Todo planning metadata updates");
  if (planned.monitor_poll_transition != null) {
    updates.material_change_generation = requireJsonObject(planned.monitor_poll_transition, "Monitor transition").material_change_generation;
  }
  return {updates,
    ...(planned.monitor_poll_transition == null ? {} : {
      monitorTransition: requireJsonObject(planned.monitor_poll_transition, "Monitor transition")})};
}
