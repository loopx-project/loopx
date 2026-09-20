/** Canonical edit decoding and materialization shared by ordinary updates and
 * User completion. Admission, validation effects and commits stay with callers. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthorityBytes,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {normalizeRegisteredTodoAgents, normalizeTodoAgent} from "./todo_agents.ts";
import {canonicalTodoRecord} from "./todo_presentation.ts";
import {TODO_OWNERSHIP_INTENT_FIELDS, planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA} from "../todos/authoring_scope.ts";
import {normalizeNativePlanningIntent, planNativeTodoUpdate} from "../todos/native_update_plan.ts";
import {decodeMonitorPollObservation, type MonitorPollObservation} from "../todos/monitor_metadata.ts";
const UPDATE_FIELDS = new Set(["text", "note"]);

export interface CoordinationTodoUpdateInput {
  readonly goal_id: string;
  readonly todo_id: string;
  readonly expected_role: string | null;
  readonly actor_agent_id: string | null;
  readonly registered_agents: readonly string[];
  readonly operation_id: string;
  readonly expected_provider_revision?: string;
  readonly expected_registry_sha256?: string;
  readonly lifecycle_grants?: readonly JsonObject[];
  readonly authority_reason?: string | null;
  readonly patch: JsonObject;
  readonly clear_fields: readonly string[];
  readonly dry_run: boolean;
  readonly now: Date;
  readonly lease_idempotency_key?: string | null;
  readonly lease_expected_version?: number | null;
  readonly planning_intent?: JsonObject;
  readonly completion?: JsonObject;
  readonly monitor_observation?: MonitorPollObservation;
}

/** Only edit intent crosses into the terminal owner; actor/lease authority is
 * carried once, in the enclosing terminal request. */
export type TodoCompletionEdit = Pick<CoordinationTodoUpdateInput,
  "patch" | "clear_fields" | "planning_intent" | "expected_provider_revision" | "expected_registry_sha256"> & {
  readonly validation_source_provider_revision?: string;
};

export function normalizeTodoUpdateInput(raw: CoordinationTodoUpdateInput): CoordinationTodoUpdateInput {
  if (raw.expected_provider_revision !== undefined) {
    requireAuthorityStoreId(raw.expected_provider_revision, "expected_provider_revision");
  }
  if (raw.expected_registry_sha256 !== undefined && !/^[a-f0-9]{64}$/u.test(raw.expected_registry_sha256)) {
    throw new AuthorityStoreProtocolError("expected_registry_sha256 must be a SHA-256 digest");
  }
  if (raw.authority_reason != null && typeof raw.authority_reason !== "string") {
    throw new AuthorityStoreProtocolError("authority_reason must be a string");
  }
  const completion = raw.completion === undefined ? undefined : canonicalAuthorityObject(raw.completion, "Todo completion update payload");
  if (completion !== undefined) {
    if (Object.keys(completion).some(field => ![
      "validation_declaration", "validation_receipt", "source_provider_revision",
      "completion_policy_request", "goal_acceptance_source_binding", "goal_acceptance_validation_receipts",
    ].includes(field))) throw new AuthorityStoreProtocolError("Unknown Todo completion update field");
    for (const field of ["validation_declaration", "validation_receipt", "completion_policy_request", "goal_acceptance_source_binding"]) {
      if (completion[field] != null) canonicalAuthorityObject(completion[field], field);
    }
    if (completion.source_provider_revision != null) {
      requireAuthorityStoreId(completion.source_provider_revision, "completion source provider revision");
    }
  }
  const planningIntent = normalizeNativePlanningIntent(raw.planning_intent);
  if (completion !== undefined && typeof planningIntent.status === "string") {
    planningIntent.status = planningIntent.status.toLowerCase();
  }
  if (raw.completion !== undefined && planningIntent.status !== "done") {
    throw new AuthorityStoreProtocolError("Completion payload requires status=done");
  }
  const key = raw.lease_idempotency_key ?? null;
  const version = raw.lease_expected_version ?? null;
  if (key !== null && (typeof key !== "string" || !key.trim() || key !== key.trim())) {
    throw new AuthorityStoreProtocolError("lease_idempotency_key must be a non-empty unpadded string");
  }
  if (version !== null && (!Number.isSafeInteger(version) || version < 0)) {
    throw new AuthorityStoreProtocolError("lease_expected_version must be a non-negative safe integer");
  }
  const patch = canonicalAuthorityObject(raw.patch, "Todo update patch");
  const clearFields = raw.clear_fields.map((field, index) =>
    requireAuthorityStoreId(field, `clear_fields[${index}]`));
  const observation = raw.monitor_observation === undefined ? undefined : decodeMonitorPollObservation(raw.monitor_observation);
  if (observation !== undefined) {
    if (completion !== undefined || Object.keys(patch).length || clearFields.length ||
        Object.keys(planningIntent).some(key => !["status", "reason", "no_followup"].includes(key)) ||
        (planningIntent.status != null && planningIntent.status !== "open") ||
        (planningIntent.no_followup != null && planningIntent.no_followup !== false)) {
      throw new AuthorityStoreProtocolError("Monitor observation accepts only reason and explicit reactivation; not copy, ownership, configuration or completion edits");
    }
  }
  if (observation === undefined && Object.keys(patch).length + clearFields.length + Object.keys(planningIntent).length === 0) {
    throw new AuthorityStoreProtocolError("Todo update requires a non-empty patch");
  }
  if (new Set(clearFields).size !== clearFields.length) {
    throw new AuthorityStoreProtocolError("clear_fields must be unique");
  }
  const unsupported = [...Object.keys(patch), ...clearFields]
    .find((field) => !UPDATE_FIELDS.has(field));
  if (unsupported !== undefined) {
    throw new AuthorityStoreProtocolError(`Todo update does not own field ${unsupported}`);
  }
  if (Object.keys(patch).some((field) => clearFields.includes(field))) {
    throw new AuthorityStoreProtocolError("Todo update cannot patch and clear the same field");
  }
  if (raw.expected_role !== null && !["agent", "user"].includes(raw.expected_role)) {
    throw new AuthorityStoreProtocolError("expected_role must be agent or user");
  }
  if (typeof raw.dry_run !== "boolean") {
    throw new AuthorityStoreProtocolError("dry_run must be a boolean");
  }
  if (!(raw.now instanceof Date) || Number.isNaN(raw.now.valueOf())) {
    throw new AuthorityStoreProtocolError("now must be a valid Date");
  }
  return {...raw, ...(completion === undefined ? {} : {completion}),
    ...(observation === undefined ? {} : {monitor_observation: observation}), planning_intent: planningIntent, lease_idempotency_key: key, lease_expected_version: version,
    goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
    todo_id: requireAuthorityStoreId(raw.todo_id, "todo id"),
    operation_id: requireAuthorityStoreId(raw.operation_id, "operation id"),
    actor_agent_id: raw.actor_agent_id === null ? null :
      normalizeTodoAgent(raw.actor_agent_id, "actor_agent_id"),
    registered_agents: normalizeRegisteredTodoAgents(raw.registered_agents),
    patch, clear_fields: clearFields};
}

export function prepareUpdatedTodo(
  todo: JsonObject, input: CoordinationTodoUpdateInput, head: JsonObject, kind: "planning" | "user_completion" = "planning",
): {next: JsonObject; changed: boolean; clearFields: string[]; monitorTransition?: JsonObject} {
  const next: JsonObject = {...todo, ...input.patch};
  for (const field of input.clear_fields) delete next[field];
  // Preserve the public planner's legacy metadata semantics. Raw copy edits
  // already carry actor attribution, while planning-only updates historically
  // leave last_actor_agent_id untouched.
  const rawCopyChanged = Object.entries(input.patch).some(([field, value]) =>
    !Object.hasOwn(todo, field) || !canonicalAuthorityBytes(todo[field]).equals(canonicalAuthorityBytes(value))) ||
    input.clear_fields.some(field => Object.hasOwn(todo, field));
  if (rawCopyChanged || input.monitor_observation !== undefined || TODO_OWNERSHIP_INTENT_FIELDS.some(field => Object.hasOwn(input.planning_intent ?? {}, field))) {
    next.last_actor_agent_id = input.actor_agent_id;
  }
  next.updated_at = input.now.toISOString().replace(/\.\d{3}Z$/u, "Z");
  const clearFields = new Set(input.clear_fields);
  let monitorTransition: JsonObject | undefined;
  if (Object.keys(input.planning_intent ?? {}).length || input.monitor_observation !== undefined) {
    const plan = planNativeTodoUpdate(todo, input.planning_intent ?? {}, head,
      input.actor_agent_id, input.registered_agents, String(next.updated_at), kind, input.monitor_observation);
    const updates = plan.updates;
    monitorTransition = plan.monitorTransition;
    for (const [field, value] of Object.entries(updates)) {
      // Markdown compatibility omits empty scalar metadata. Treat an
      // explicit empty planning scalar as a clear in the canonical record as
      // well; omission and clear are no longer conflated by the planner.
      if (value === null || value === "") { delete next[field]; clearFields.add(field); }
      else next[field] = value;
    }
    next.done = next.status === "done" || next.status === "deferred";
  }
  if (kind === "user_completion" && (todo.status !== "done" || Object.hasOwn(input.planning_intent ?? {}, "task_class"))) {
    planTodoAuthoringScope({schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA, command: "class", role: "user", todo: next,
      intent: {task_class: next.task_class, blocks_agent: next.blocks_agent ?? null, global_gate: next.global_gate ?? null}});
  }
  canonicalTodoRecord(next, "updated Todo");
  const changedBeforeAudit = {...next};
  delete changedBeforeAudit.last_actor_agent_id;
  delete changedBeforeAudit.updated_at;
  const originalBeforeAudit = {...todo};
  delete originalBeforeAudit.last_actor_agent_id;
  delete originalBeforeAudit.updated_at;
  const changed = !canonicalAuthorityBytes(changedBeforeAudit).equals(
    canonicalAuthorityBytes(originalBeforeAudit));
  if (!changed) {
    next.last_actor_agent_id = todo.last_actor_agent_id;
    next.updated_at = todo.updated_at;
  }
  return {next, changed, clearFields: [...clearFields], ...(monitorTransition ? {monitorTransition} : {})};
}
