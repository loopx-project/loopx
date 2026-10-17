/** Declared work may change; an old execution grant must not change with it.
 * Admission and Todo/lease retirement share the update owner's provider CAS. */
import type {JsonObject} from "../effect_program.ts";
import {TODO_WORK_REQUIREMENT_FIELDS} from "../todos/work_requirements.ts";
import {leaseIsActive, leaseVersion, leaseEpoch} from "../work_items/task_lease_acquire.ts";
import {releasedTaskLeaseRecord} from "../work_items/task_lease_lifecycle_decision.ts";
import {ownerAdministrativeRepositoryRejection} from "../work_items/task_lease_repository.ts";
import {canonicalAuthoritySha256} from "./authority_store_codec.ts";
import type {CoordinationProjectionMutation} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";

/** Local classification of the existing update intent, not an execution grant.
 * Status, ownership, dependencies and validation changes keep their own owners. */
export function isOwnerWorkRequirementsRevision(todo: JsonObject, input: CoordinationTodoUpdateInput): boolean {
  const intent = input.planning_intent ?? {};
  return todo.role === "agent" && ["open", "blocked", "deferred"].includes(String(todo.status)) &&
    input.actor_agent_id !== null && (todo.claimed_by == null || todo.claimed_by === input.actor_agent_id) &&
    input.completion === undefined && input.completion_validation_revision === undefined &&
    input.monitor_observation === undefined &&
    TODO_WORK_REQUIREMENT_FIELDS.some(field => Object.hasOwn(intent, field)) &&
    Object.keys(input.patch).every(field => field === "text" || field === "note") &&
    input.clear_fields.every(field => field === "note") &&
    Object.entries(intent).every(([field, value]) =>
      TODO_WORK_REQUIREMENT_FIELDS.some(requirement => requirement === field) ||
      ["evidence", "reason", "priority", "clear_priority"].includes(field) ||
      (field === "status" && value === todo.status));
}

/** Shared actor/claim/binding admission and optional explicit proof run first.
 * An inactive foreign generation is still someone else's retained authority. */
export function workRequirementsRevisionRejection(todo: JsonObject, input: CoordinationTodoUpdateInput,
  retained: JsonObject | undefined): {code: string; reason: string} | null {
  if (retained === undefined) return null;
  const lease = canonicalTaskLease(retained, input.goal_id, input.todo_id);
  leaseIsActive(lease, input.now); // Validate the stored clock even for administrative intent.
  const repository = ownerAdministrativeRepositoryRejection(todo, lease, input.actor_agent_id);
  if (repository !== null) return {code: repository, reason: "Inspect the divergent execution repository before revising work"};
  if (lease.owner !== input.actor_agent_id) return {code: "lease_owner_mismatch",
    reason: "Only the current owner may retire its retained execution while revising work requirements"};
  if (todo.claimed_by !== input.actor_agent_id) return {code: "update_owner_mismatch",
    reason: "Requirement revision must retain the execution holder's current Todo claim"};
  return null;
}

/** Called only after admission and normalization. Freeze history unchanged;
 * any later execution is separately admitted with a new acquire identity. */
export function planWorkRequirementsRevision(input: {
  goal_id: string; before: JsonObject; after: JsonObject; lease: JsonObject | undefined; now: Date;
}): {mutations: CoordinationProjectionMutation[]; transition: JsonObject | null} {
  const changed = TODO_WORK_REQUIREMENT_FIELDS.filter(field =>
    canonicalAuthoritySha256({value: input.before[field] ?? null}) !==
    canonicalAuthoritySha256({value: input.after[field] ?? null}));
  if (changed.length === 0) return {mutations: [], transition: null};
  const lease = input.lease === undefined ? null :
    canonicalTaskLease(input.lease, input.goal_id, String(input.before.todo_id));
  const retiring = lease !== null && lease.status !== "released";
  return {
    mutations: retiring ? [{kind: "lease_upsert", lease: releasedTaskLeaseRecord(lease, input.now)}] : [],
    transition: {changed_fields: changed, execution_authority_granted: false,
      lease_retirement: lease === null ? "absent" : retiring ? "released" : "already_released",
      next_execution: "acquire_fresh_lease",
      ...(lease === null ? {} : {retired_lease_version: leaseVersion(lease), retired_lease_epoch: leaseEpoch(lease)})},
  };
}
