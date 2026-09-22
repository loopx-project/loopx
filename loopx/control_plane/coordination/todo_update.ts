import {AUTHORITY_SOURCE_CHANGED, uncheckedAuthoritySource, type AuthoritySourceCheck} from "./authority_source.ts";
import type { JsonObject } from "../effect_program.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";
import type { AuthorityStore, AuthorityStoreCommit } from "./authority_store.ts";
import {
  AuthorityStoreProtocolError,
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
  requireAuthorityStoreId,
} from "./authority_store_codec.ts";
import {
  indexCoordinationProjection,
  prepareCoordinationProjectionCommit,
  validateCoordinationTodoReadModel,
} from "./coordination_projection.ts";

import {planMonitorCycleTransition} from "./todo_monitor_cycle.ts";
import {todoUpdateAdmissionRejection} from "./todo_update_admission.ts";
import { CoordinationCommandReceipt } from "./command_receipt.ts";
import {canonicalTodoRecord} from "./todo_presentation.ts";

export const COORDINATION_TODO_UPDATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v0";
// Older runtimes must reject planning requests rather than commit only their copy patch.
export const COORDINATION_TODO_PLANNING_UPDATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v1";
// Admission witnesses and reviewed CAS must never be silently ignored by v0/v1.
export const COORDINATION_TODO_REVIEWED_UPDATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v2";
export const COORDINATION_TODO_UPDATE_RESULT_SCHEMA =
  "loopx_coordination_todo_update_result_v0";
export const COORDINATION_TODO_UPDATE_RECEIPT_SCHEMA =
  "loopx_coordination_todo_update_receipt_v0";

export const COORDINATION_TODO_COMPLETION_UPDATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v3";
export const COORDINATION_TODO_OBSERVATION_UPDATE_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v4";
export const COORDINATION_TODO_VALIDATION_REVISION_REQUEST_SCHEMA =
  "loopx_local_coordination_todo_update_request_v5";
export type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {normalizeTodoUpdateInput, prepareUpdatedTodo, type CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {executeCoordinationTodoTerminalLifecycle} from "./todo_terminal_lifecycle.ts";
import {planCompletionValidationRevision} from "../todos/completion_validation_revision.ts";

export type CoordinationTodoUpdateResult = JsonObject & {
  readonly schema_version: typeof COORDINATION_TODO_UPDATE_RESULT_SCHEMA;
};

function failure(code: string, reason: string): CoordinationTodoUpdateResult {
  return {schema_version: COORDINATION_TODO_UPDATE_RESULT_SCHEMA, status: "failed",
    changed: false, reason_code: code, reason};
}

function isFailure(value: JsonObject): value is CoordinationTodoUpdateResult {
  return value.schema_version === COORDINATION_TODO_UPDATE_RESULT_SCHEMA &&
    value.status === "failed";
}

function updateReceipt(input: CoordinationTodoUpdateInput, requestSha: string) {
  return new CoordinationCommandReceipt({result_schema: COORDINATION_TODO_UPDATE_RESULT_SCHEMA,
    identity: {schema_version: COORDINATION_TODO_UPDATE_RECEIPT_SCHEMA,
      operation_id: input.operation_id, goal_id: input.goal_id, todo_id: input.todo_id,
      request_sha256: requestSha}, failure,
    decode(original) {
      if (typeof original.changed !== "boolean") throw new AuthorityStoreProtocolError("update receipt changed must be boolean");
      return {fields: {todo_id: input.todo_id, original_receipt: original,
        ...(input.monitor_observation === undefined ? {} : {
          monitor_poll_transition: canonicalAuthorityObject(original.monitor_poll_transition, "Monitor update receipt transition")}),
        ...(original.monitor_lifecycle_transition === undefined ? {} : {monitor_lifecycle_transition:
          canonicalAuthorityObject(original.monitor_lifecycle_transition, "Monitor lifecycle receipt transition")})}, changed: original.changed};
    }});
}

function updateRequestSha(input: CoordinationTodoUpdateInput): string {
  return canonicalAuthoritySha256({goal_id: input.goal_id,
    todo_id: input.todo_id, expected_role: input.expected_role,
    actor_agent_id: input.actor_agent_id, patch: input.patch,
    ...(input.expected_provider_revision === undefined ? {} :
      {expected_provider_revision: input.expected_provider_revision}),
    ...(input.expected_registry_sha256 === undefined ? {} :
      {expected_registry_sha256: input.expected_registry_sha256}),
    ...(input.authority_reason == null ? {} : {authority_reason: input.authority_reason}),
    clear_fields: input.clear_fields, dry_run: input.dry_run,
    ...(input.monitor_observation === undefined ? {} : {monitor_observation: input.monitor_observation}),
    ...(input.completion_validation_revision === undefined ? {} :
      {completion_validation_revision: input.completion_validation_revision}),
    ...(Object.keys(input.planning_intent ?? {}).length ? {planning_intent: input.planning_intent} : {}),
    // Preserve receipt identity for pre-proof requests already persisted in v0.
    ...(input.lease_idempotency_key != null || input.lease_expected_version != null ? {
      lease_idempotency_key: input.lease_idempotency_key,
      lease_expected_version: input.lease_expected_version,
    } : {}),
  });
}

function loadUpdateTarget(
  head: JsonObject, input: CoordinationTodoUpdateInput,
): {todo: JsonObject; leases: ReadonlyMap<string, JsonObject>} | CoordinationTodoUpdateResult {
  try {
    validateCoordinationTodoReadModel(head, input.goal_id);
    const projection = indexCoordinationProjection(head, input.goal_id);
    const found = projection.todos.get(input.todo_id);
    return found === undefined
      ? failure("todo_not_found", "canonical Todo is missing")
      : {todo: found, leases: projection.leases};
  } catch (error) {
    return failure("invalid_coordination_projection",
      error instanceof Error ? error.message : "invalid coordination projection");
  }
}

/** Update mutable Todo metadata from the canonical provider head. */
export async function executeCoordinationTodoUpdate(
  store: AuthorityStore, rawInput: CoordinationTodoUpdateInput,
  authoritySourcesCurrent: AuthoritySourceCheck = uncheckedAuthoritySource,
): Promise<CoordinationTodoUpdateResult> {
  let input: CoordinationTodoUpdateInput;
  try { input = normalizeTodoUpdateInput(rawInput); } catch (error) {
    return failure("invalid_coordination_todo_update",
      error instanceof Error ? error.message : "invalid Todo update");
  }
  if (input.completion !== undefined) {
    const completion = input.completion;
    const object = (field: string) => completion[field] == null ? null :
      canonicalAuthorityObject(completion[field], field);
    const intent = input.planning_intent!;
    const result = await executeCoordinationTodoTerminalLifecycle(store, {
      goal_id: input.goal_id, todo_id: input.todo_id, expected_role: input.expected_role as "user" | "agent" | null,
      command: "complete", actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
      lifecycle_grants: input.lifecycle_grants ?? [], authority_reason: input.authority_reason ?? null,
      decision_outcome: null, operation_id: input.operation_id,
      lease_idempotency_key: input.lease_idempotency_key ?? null, lease_expected_version: input.lease_expected_version ?? null,
      allow_user_gate_auto_acquire: input.lease_idempotency_key == null && input.lease_expected_version == null, requested_no_followup: intent.no_followup === true,
      requested_completion_turn_key: null, requested_completion_identity_source: null,
      linked_successor_todo_ids: (intent.successor_todo_ids ?? []) as string[], successor_intents: [],
      note: null, evidence: null, reason: null, clear_claim: intent.clear_claim === true,
      validation_declaration: object("validation_declaration"), validation_receipt: object("validation_receipt"),
      completion_policy_request: object("completion_policy_request"),
      goal_acceptance_source_binding: object("goal_acceptance_source_binding"),
      goal_acceptance_validation_receipts: completion.goal_acceptance_validation_receipts,
      dry_run: input.dry_run, now: input.now, user_update: {
        patch: input.patch, clear_fields: input.clear_fields, planning_intent: intent,
        ...(input.expected_provider_revision === undefined ? {} : {expected_provider_revision: input.expected_provider_revision}),
        ...(input.expected_registry_sha256 === undefined ? {} : {expected_registry_sha256: input.expected_registry_sha256}),
        ...(completion.source_provider_revision == null ? {} : {
          validation_source_provider_revision: requireAuthorityStoreId(completion.source_provider_revision, "completion source provider revision")}),
      },
    }, authoritySourcesCurrent);
    return {...result, schema_version: COORDINATION_TODO_UPDATE_RESULT_SCHEMA};
  }
  const requestSha = updateRequestSha(input);
  const receipt = updateReceipt(input, requestSha);
  const replay = await receipt.read(store);
  if (replay !== null) return replay;
  const sourceChanged = () => failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  if (!await authoritySourcesCurrent()) return sourceChanged();
  // Legacy single-agent callers historically omitted actor_agent_id for an
  // unowned Todo. Keep that narrow compatibility path, while retaining the
  // registered-actor requirement for multi-agent or explicitly-owned work.
  if (input.actor_agent_id === null) {
    if (input.registered_agents.length > 1) {
      return failure("actor_not_registered", "Todo update requires a registered actor");
    }
  } else if (input.registered_agents.length === 0 ||
      !input.registered_agents.includes(input.actor_agent_id)) {
    return failure("actor_not_registered", "Todo update requires a registered actor");
  }
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") {
    return {schema_version: COORDINATION_TODO_UPDATE_RESULT_SCHEMA, ...head, changed: false};
  }
  if (input.expected_provider_revision !== undefined &&
      input.expected_provider_revision !== head.provider_revision) {
    return failure("provider_revision_mismatch", "Current revision changed; inspect again before continuing");
  }

  const target = loadUpdateTarget(head.head, input);
  if (isFailure(target)) return target;
  const rejected = todoUpdateAdmissionRejection(head.head, target.todo, target.leases, input);
  if (rejected !== null) return failure(rejected.code, rejected.reason);
  let prepared: ReturnType<typeof prepareUpdatedTodo>;
  try { prepared = prepareUpdatedTodo(target.todo, input, head.head); }
  catch (error) { return failure("invalid_coordination_todo_update",
    error instanceof Error ? error.message : "invalid updated Todo"); }
  let {next, changed, clearFields} = prepared;
  let completionValidationRevisionReceipt: JsonObject | null = null;
  if (input.completion_validation_revision !== undefined) {
    try {
      const planned = planCompletionValidationRevision({
        todo: target.todo,
        revision: input.completion_validation_revision,
        actor_agent_id: input.actor_agent_id,
        operation_id: input.operation_id,
        revised_at: input.now.toISOString().replace(/\.\d{3}Z$/u, "Z"),
      });
      next = {
        ...next,
        ...planned.updates,
        last_actor_agent_id: input.actor_agent_id,
        updated_at: input.now.toISOString().replace(/\.\d{3}Z$/u, "Z"),
      };
      completionValidationRevisionReceipt = planned.receipt;
      changed = true;
      clearFields = clearFields.filter(
        (field) => !Object.hasOwn(planned.updates, field),
      );
      canonicalTodoRecord(next, "updated Todo");
    } catch (error) {
      return failure(
        "invalid_completion_validation_revision",
        error instanceof Error
          ? error.message
          : "invalid completion validation revision",
      );
    }
  }
  let cycle: ReturnType<typeof planMonitorCycleTransition>;
  try {
    cycle = planMonitorCycleTransition({goal_id: input.goal_id, before: target.todo, after: next,
      lease: target.leases.get(input.todo_id), handoff_mode: head.head.handoff_mode, now: input.now});
  } catch (error) {
    return failure("invalid_coordination_projection", error instanceof Error ? error.message : "invalid retained lease");
  }
  const commit: AuthorityStoreCommit = changed ? prepareCoordinationProjectionCommit({
    goal_id: input.goal_id, operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: [{kind: "todo_upsert", todo: next, clear_fields: clearFields}, ...cycle.mutations],
  }) : {operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, next_projection: head.head,
    events: [], receipts: []};
  // Planning can assign a claim too. Admit that assignment against the full
  // candidate head so a simultaneous semantic edit cannot retain stale approval.
  if (input.planning_intent?.claimed_by != null) {
    const acceptance = acceptanceWorkGuard(commit.next_projection, input.goal_id, input.todo_id);
    if (acceptance !== null && !acceptance.allowed) {
      return {...failure(String(acceptance.reason_code), `${String(acceptance.reason)} Inspect Goal acceptance and ask the owner to configure or rebind this Todo.`),
        goal_acceptance_guard: acceptance};
    }
  }
  // The provider CAS covers Todo/lease state. Registry configuration is a
  // separate source witness, not part of a distributed transaction.
  if (!await authoritySourcesCurrent()) return sourceChanged();
  if (input.dry_run) return {schema_version: COORDINATION_TODO_UPDATE_RESULT_SCHEMA,
    status: changed ? "planned" : "no_change", changed, todo_id: input.todo_id,
    provider_revision: head.provider_revision, cursor: head.cursor, dry_run: true,
    ...(prepared.monitorTransition ? {monitor_poll_transition: prepared.monitorTransition} : {}),
    ...(completionValidationRevisionReceipt === null ? {} :
      {completion_validation_revision: completionValidationRevisionReceipt}),
    ...(cycle.transition === null ? {} : {monitor_lifecycle_transition: cycle.transition})};
  commit.receipts = [{schema_version: COORDINATION_TODO_UPDATE_RECEIPT_SCHEMA,
    operation_id: input.operation_id, goal_id: input.goal_id,
    todo_id: input.todo_id, request_sha256: requestSha, changed,
    ...(prepared.monitorTransition ? {monitor_poll_transition: prepared.monitorTransition} : {}),
    ...(completionValidationRevisionReceipt === null ? {} :
      {completion_validation_revision: completionValidationRevisionReceipt}),
    ...(cycle.transition === null ? {} : {monitor_lifecycle_transition: cycle.transition})}];
  return receipt.commit(store, commit);
}
