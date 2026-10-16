/** A bounded pause is a Todo lifecycle change, not a completed delivery.
 * Retire the current holder's generation in the same provider CAS. A foreign
 * live holder still has to release; administrative authority is not takeover. */
import type {JsonObject} from "../effect_program.ts";
import type {CoordinationProjectionMutation} from "./coordination_projection.ts";
import type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {leaseEpoch, leaseIsActive, leaseVersion} from "../work_items/task_lease_acquire.ts";
import {releasedTaskLeaseRecord} from "../work_items/task_lease_lifecycle_decision.ts";
import {normalizeTodoResumeWhen, TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION} from "../todos/resume_condition.ts";

const LIFECYCLE_FIELDS = new Set(["status", "reason", "clear_resume_when", "resume_when", "evidence"]);

export function isBlockedLifecycleTransition(
  input: CoordinationTodoUpdateInput, todo: JsonObject,
): boolean {
  const intent = input.planning_intent ?? {};
  const pause = todo.status === "open" && intent.status === "blocked";
  // Explicit pause/resume clears an omitted wait in materialization. Callers
  // need not discover an otherwise hidden --clear-resume-when incantation.
  const clearWait = intent.resume_when == null && intent.clear_resume_when !== false;
  const typedWait = pause && intent.clear_resume_when !== true && typeof intent.resume_when === "string"
    && normalizeTodoResumeWhen({schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
      resume_when: intent.resume_when}) === intent.resume_when;
  return todo.role === "agent" &&
    (pause ||
      (todo.status === "blocked" && intent.status === "open")) &&
    (clearWait || typedWait) &&
    typeof intent.reason === "string" && Boolean(intent.reason.trim()) &&
    Object.keys(input.patch).every(field => field === "note") && input.clear_fields.length === 0 &&
    Object.keys(intent).every(field => LIFECYCLE_FIELDS.has(field));
}

export function blockedLifecycleRejection(input: {
  goal_id: string; todo_id: string; actor_agent_id: string | null;
  registered_agents: readonly string[]; lease: JsonObject | undefined;
  lease_idempotency_key: string | null; lease_expected_version: number | null;
  now: Date; allow_current_holder?: boolean; proof_validated?: boolean;
}): {code: string; reason: string} | null {
  if (input.actor_agent_id === null || !input.registered_agents.includes(input.actor_agent_id)) {
    return {code: "actor_not_registered", reason: "Blocked lifecycle transition requires a registered actor"};
  }
  if (!input.proof_validated && (input.lease_idempotency_key !== null || input.lease_expected_version !== null)) {
    return {code: "blocked_lifecycle_execution_proof_not_allowed",
      reason: "Blocked lifecycle transition does not consume an old execution lease"};
  }
  if (input.lease === undefined) return null;
  const lease = canonicalTaskLease(input.lease, input.goal_id, input.todo_id);
  if (leaseIsActive(lease, input.now) &&
      !(input.allow_current_holder && lease.owner === input.actor_agent_id)) {
    return {code: "blocked_lifecycle_active_lease",
      reason: "A different or divergent execution still holds this Todo; its holder must release before this lifecycle change"};
  }
  return null;
}

export function planBlockedLifecycleTransition(input: {
  goal_id: string; before: JsonObject; after: JsonObject;
  lease: JsonObject | undefined; now: Date;
}): {mutations: CoordinationProjectionMutation[]; transition: JsonObject | null} {
  const from = input.before.status;
  const to = input.after.status;
  if (input.before.role !== "agent" ||
      !((from === "open" && to === "blocked") || (from === "blocked" && to === "open"))) {
    return {mutations: [], transition: null};
  }
  const lease = input.lease === undefined ? null :
    canonicalTaskLease(input.lease, input.goal_id, String(input.before.todo_id));
  const retiring = lease !== null && lease.status !== "released";
  return {
    mutations: retiring ? [{kind: "lease_upsert", lease: releasedTaskLeaseRecord(lease, input.now)}] : [],
    transition: {kind: to === "blocked" ? "todo_blocked" : "todo_reopened",
      execution_authority_granted: false,
      lease_retirement: lease === null ? "absent" : retiring ? "released" : "already_released",
      next_execution: to === "open" ? "acquire_fresh_lease" : "wait_for_explicit_resume",
      ...(lease === null ? {} : {retired_lease_version: leaseVersion(lease),
        retired_lease_epoch: leaseEpoch(lease)}),
    },
  };
}
