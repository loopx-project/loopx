/** One public edit plan over one locked snapshot. This is not admission, a
 * provider transaction, or a receipt: callers retain permission/lock/commit. */
import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
  normalizeTodoOwnershipIntent, TODO_OWNERSHIP_INTENT_FIELDS } from "./authoring_scope.ts";
import { planTodoFieldUpdate, TODO_FIELD_UPDATE_REQUEST_SCHEMA } from "./field_update.ts";
import { planTodoExternalWaitTransition, TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION } from "./resume_condition.ts";
import { normalizeTodoWorkRequirements, TODO_WORK_REQUIREMENT_FIELDS } from "./work_requirements.ts";
import {
  normalizeTodoDecisionScope,
  normalizeTodoRequiredDecisionScopes,
  validateTodoDecisionMetadata,
} from "./decision_metadata.ts";

import { normalizeMonitorConfiguration, validateMonitorConfigurationTarget } from "./monitor_metadata.ts";
import {canonicalAuthorityBytes} from "../coordination/authority_store_codec.ts";

export const TODO_PUBLIC_UPDATE_REQUEST_SCHEMA = "todo_public_update_request_v0";

/** Shared provenance intent; callers still suppress unchanged replay and own admission. */
export function shouldRecordTodoMutationActor(
  todo: JsonObject, patch: JsonObject, clearFields: readonly string[],
  planningIntent: JsonObject, monitorObservation: unknown,
): boolean {
  const rawCopyChanged = Object.entries(patch).some(([field, value]) =>
    !Object.hasOwn(todo, field) || !canonicalAuthorityBytes(todo[field]).equals(canonicalAuthorityBytes(value))) ||
    clearFields.some(field => Object.hasOwn(todo, field));
  return rawCopyChanged || monitorObservation !== undefined ||
    TODO_OWNERSHIP_INTENT_FIELDS.some(field => Object.hasOwn(planningIntent, field));
}

const SCOPE_INTENT_FIELDS = ["task_class", "status", "claimed_by", "bound_agent", "goal_bound",
  "blocks_agent", "clear_blocks_agent", "global_gate", "clear_global_gate", "excluded_agents",
  "task_repository", "task_domain", "resume_when", "clear_resume_when", "clear_claim"] as const;

function externalWait(todo: JsonObject, intent: JsonObject, scope: JsonObject,
  context: JsonObject): JsonObject | null {
  // Copy-only edits do not re-arm a satisfied wait or demand a fresh successor.
  // Changes to its shape, however, must validate the retained condition too.
  const shapeChanged = ["status", "task_class"].some(key => scope[key] !== todo[key]) ||
    context.role !== todo.role || intent.successor_todo_ids != null;
  const resume = scope.normalized_resume_when || (shapeChanged ? scope.effective_resume_when : null);
  if (typeof resume !== "string") return null;
  const kind = resume.split(":", 1)[0];
  if (kind !== "todo_done" && kind !== "monitor_changed") return null;
  if (kind === "todo_done" && !(context.role === "agent" && scope.status === "open" &&
      scope.task_class === "advancement_task")) return null;
  if (!Array.isArray(context.items)) throw new EffectRuntimeRequestError("public update requires its locked Todo snapshot");
  const items = context.items.map(value => {
    const item = requireJsonObject(value, "public update Todo item");
    return item.todo_id === todo.todo_id ? {...item, role: context.role,
      status: scope.status, task_class: scope.task_class} : item;
  });
  return planTodoExternalWaitTransition({schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
    todo_id: todo.todo_id, resume_when: resume,
    successor_todo_ids: intent.successor_todo_ids ?? todo.successor_todo_ids ?? [], items});
}

export function planPublicTodoUpdate(value: unknown): JsonObject {
  const request = requireJsonObject(value, "public Todo update request");
  if (request.schema_version !== TODO_PUBLIC_UPDATE_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("public Todo update schema mismatch");
  }
  const todo = requireJsonObject(request.todo, "public Todo update source");
  const rawIntent = requireJsonObject(request.intent, "public Todo update intent");
  const intent: JsonObject = {...rawIntent};
  for (const field of TODO_WORK_REQUIREMENT_FIELDS) delete intent[field];
  for (const field of TODO_OWNERSHIP_INTENT_FIELDS) delete intent[field];
  Object.assign(intent, normalizeTodoWorkRequirements(rawIntent));
  Object.assign(intent, normalizeTodoOwnershipIntent(rawIntent));
  if (Object.hasOwn(intent, "decision_scope")) {
    intent.decision_scope = normalizeTodoDecisionScope(intent.decision_scope);
  }
  if (Object.hasOwn(intent, "required_decision_scopes")) {
    intent.required_decision_scopes = normalizeTodoRequiredDecisionScopes(intent.required_decision_scopes);
  }
  validateTodoDecisionMetadata(todo, intent);
  const context = requireJsonObject(request.context, "public Todo update context");
  if (context.monitor_observation == null && intent.monitor_metadata != null) {
    const metadata = normalizeMonitorConfiguration(intent.monitor_metadata);
    validateMonitorConfigurationTarget(todo, metadata);
    intent.monitor_metadata = metadata;
  }
  // Preserve omission at the authoring-scope boundary. Filling every field
  // with null made an unrelated metadata edit erase a retained gate scope.
  const scopeIntent = Object.fromEntries(SCOPE_INTENT_FIELDS.flatMap(key =>
    Object.hasOwn(intent, key) ? [[key, intent[key]]] : []));
  if (context.actor_agent_id !== undefined) scopeIntent.actor_agent_id = context.actor_agent_id;
  const scope = planTodoAuthoringScope({schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
    command: "update", role: context.role, todo,
    intent: scopeIntent,
    goal_id: context.goal_id, registered_agents: context.registered_agents});
  const transition = externalWait(todo, intent, scope, context);
  const metadata = transition ? requireJsonObject(transition.metadata_updates, "external wait updates") : null;
  const role = context.role;
  const effectiveIntent = {...intent,
    bound_agent: role === "user" ? scope.bound_agent : null,
    // Carry an explicit false when a gate changes from goal-wide to an
    // agent-bound continuation; omission would leave stale goal_bound=true in
    // the canonical record.
    goal_bound: role === "user" && scope.goal_bound !== null
      ? scope.goal_bound : null,
    clear_user_binding: scope.clear_user_binding,
    resume_when: scope.normalized_resume_when,
    resume_monitor_generation: metadata?.resume_monitor_generation ?? null};
  // A retained condition revalidated by a topology edit keeps its original
  // generation; it must never silently capture a newer Monitor observation.
  if (transition) effectiveIntent.resume_when = transition.resume_when;
  const plan = planTodoFieldUpdate({schema_version: TODO_FIELD_UPDATE_REQUEST_SCHEMA,
    todo, intent: effectiveIntent, updated_at: request.updated_at,
    monitor_context: {metadata: intent.monitor_metadata ?? null,
      observation: context.monitor_observation ?? null, role, task_class: scope.task_class,
      resume_when: scope.effective_resume_when, enforce_boundedness: context.enforce_monitor_boundedness}});
  const copyPatch = Object.fromEntries(["text", "note"].flatMap(field =>
    Object.hasOwn(intent, field) ? [[field, intent[field]]] : []));
  if (context.actor_agent_id != null && shouldRecordTodoMutationActor(
      todo, copyPatch, [], intent, context.monitor_observation ?? undefined)) {
    // The legacy writer transports this after its material-change check; actor
    // provenance alone must never turn an unchanged replay into a mutation.
    plan.metadata_updates.last_actor_agent_id = context.actor_agent_id;
  }
  return {...plan, ...(transition ? {external_wait_transition: transition} : {})};
}
