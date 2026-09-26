/** Current nonterminal mutation proof. Admission stays in the lifecycle owner;
 * this boundary never acquires, renews, releases or transfers a lease. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLeaseAcquireFacts} from "./task_lease_state.ts";
import {coordinationTodoWriteScopes} from "./todo_write_scopes.ts";
import {decideTaskLeaseAcquire} from "../work_items/task_lease_acquire_decision.ts";
import {leaseOwnerRejection} from "../work_items/task_lease_eligibility.ts";
import {TODO_WORK_REQUIREMENT_FIELDS} from "../todos/work_requirements.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";
import {leaseEpoch} from "../work_items/task_lease_acquire.ts";
import {leaseRepositoryRejection} from "../work_items/task_lease_repository.ts";
import {evaluateCoordinationTerminalFence, COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA} from "./todo_lifecycle_decision.ts";

export interface TaskLeaseProof {
  idempotency_key: string;
  expected_version: number;
}

export function decodeTaskLeaseProof(value: unknown): TaskLeaseProof | null {
  if (value == null) return null;
  const proof = requireJsonObject(value, "lease_proof");
  if (Object.keys(proof).some(key => key !== "idempotency_key" && key !== "expected_version")) {
    throw new EffectRuntimeRequestError("lease_proof accepts only idempotency_key and expected_version");
  }
  if (typeof proof.idempotency_key !== "string" || !proof.idempotency_key.trim() ||
      proof.idempotency_key !== proof.idempotency_key.trim() ||
      typeof proof.expected_version !== "number" || !Number.isSafeInteger(proof.expected_version) || proof.expected_version < 1) {
    throw new EffectRuntimeRequestError("lease_proof requires an unpadded execution key and positive safe-integer version");
  }
  return {idempotency_key: proof.idempotency_key, expected_version: proof.expected_version};
}

export function evaluateCanonicalTaskLeaseProof(input: {
  todo: JsonObject; lease: JsonObject | undefined; handoff_mode: string;
  actor_agent_id: string | null; registered_agents: readonly string[];
  lease_idempotency_key: string | null; lease_expected_version: number | null; now: Date;
}) {
  const {lease} = input;
  if (!(input.now instanceof Date) || !Number.isFinite(input.now.valueOf())) {
    throw new EffectRuntimeRequestError("lease proof requires a valid transaction clock");
  }
  const expires = lease === undefined ? null :
    typeof lease.expires_at === "string" ? parseIsoTimestamp(lease.expires_at) : null;
  if (lease?.status === "active" && expires === null) {
    throw new EffectRuntimeRequestError("active lease expiry is invalid");
  }
  const decision = evaluateCoordinationTerminalFence({
    schema_version: COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA,
    todo: input.todo, registered_agents: input.registered_agents,
    actor_agent_id: input.actor_agent_id,
    // Retained execution lineage must not become an unfenced metadata edit.
    handoff_mode: lease !== undefined ? "hard_lease" : input.handoff_mode,
    lease: lease === undefined ? null : {...lease, present: true,
      active: lease.status === "active" && expires !== null && expires > input.now,
      lease_epoch: leaseEpoch(lease)},
    lease_idempotency_key: input.lease_idempotency_key,
    lease_expected_version: input.lease_expected_version,
    allow_user_gate_auto_acquire: false, delegated_authority: false,
    require_active_when_fence_supplied: true,
  });
  // Strip the terminal owner's release proposal: callers receive only a
  // decision, so an observation cannot accidentally apply terminal effects.
  return {outcome: decision.outcome, code: decision.code};
}

export type TodoUpdateLeaseRecovery = JsonObject & {
  action: "inspect_current_proof" | "reconcile_lease_owner" | "resolve_acquire_rejection" |
    "resolve_lifecycle_edit" | "acquire_fresh_lease";
  lease_state: "absent" | "active" | "released" | "expired";
  owner_relation: "none" | "same_owner" | "foreign_owner";
};

/** Metadata editing cannot replace a retained execution grant's work contract. */
export function leasedTodoEditRejection(todo: JsonObject, intent: JsonObject): {code: string; reason: string} | null {
  if (TODO_WORK_REQUIREMENT_FIELDS.some(field => Object.hasOwn(intent, field))) {
    return {code: "update_lease_requirements_transition_unsupported",
      reason: "Changing leased work requirements requires a new execution grant; metadata update leaves the lease unchanged"};
  }
  if (typeof intent.status === "string" && intent.status.toLowerCase() !== todo.status) {
    return {code: "update_lease_status_transition_unsupported",
      reason: "Changing a leased Todo status requires an atomic lifecycle operation; planning update leaves the lease unchanged"};
  }
  return null;
}

/** Diagnostic only: acquisition still rechecks the current head and its CAS.
 * Reuse admission rather than recommend a new lease solely from its expiry. */
export function todoUpdateLeaseRecovery(head: JsonObject, input: {
  goal_id: string; todo_id: string; actor_agent_id: string | null;
  registered_agents: readonly string[]; now: Date; planning_intent?: JsonObject;
}, mode: string): TodoUpdateLeaseRecovery {
  const index = indexCoordinationProjection(head, input.goal_id);
  const facts = canonicalTaskLeaseAcquireFacts(index, input.goal_id, input.todo_id,
    input.registered_agents, input.now);
  const lease = facts.lease;
  const leaseState: TodoUpdateLeaseRecovery["lease_state"] = lease === null ? "absent" : lease.active ? "active"
    : lease.status === "released" ? "released" : "expired";
  const sameOwner = lease !== null && lease.owner === input.actor_agent_id;
  const ownerRelation: TodoUpdateLeaseRecovery["owner_relation"] =
    lease === null ? "none" : sameOwner ? "same_owner" : "foreign_owner";
  const base = {
    lease_state: leaseState,
    owner_relation: ownerRelation,
    observed_version: lease?.version ?? 0,
    inspect: {command: "loopx task-lease inspect", goal_id: input.goal_id, todo_id: input.todo_id},
    execution_authority_granted: false,
  };
  const todo = index.todos.get(input.todo_id)!;
  const intent = input.planning_intent ?? {};
  const editRejection = lease === null ? null : leasedTodoEditRejection(todo, intent);
  if (editRejection !== null) {
    return {...base, action: "resolve_lifecycle_edit", reason_code: editRejection.code,
      reason: "This edit changes leased work requirements or status. Use the owning lifecycle transition; reacquiring a lease alone cannot authorize this metadata edit."};
  }
  if (todo.claimed_by !== input.actor_agent_id) {
    return {...base, action: "reconcile_lease_owner",
      reason: "A leased update requires the actor to own the Todo claim. Reconcile ownership before acquiring execution authority."};
  }
  const retry = {command: "loopx todo update",
    requires_flags: ["--task-lease-idempotency-key", "--task-lease-expected-version"],
    proof_source: "current_owner_lease_readback"};
  if (lease?.active) {
    const repositoryRejection = leaseRepositoryRejection(facts.todo, lease);
    if (repositoryRejection !== null) return {...base, action: "resolve_acquire_rejection",
      reason_code: repositoryRejection, reason: "Todo repository differs from its frozen execution grant. Reconcile the owning lifecycle; inspection cannot grant replacement authority."};
    const eligible = sameOwner && leaseOwnerRejection(facts.todo,
      input.actor_agent_id, input.registered_agents) === null;
    return {...base, action: eligible ? "inspect_current_proof" : "reconcile_lease_owner",
      reason: eligible
        ? "Inspect the active lease and retry with its current owner proof; do not acquire a competing execution."
        : "Reconcile the Todo claim and active lease holder through the lease lifecycle before retrying; do not borrow another holder's proof.",
      ...(eligible ? {retry} : {})};
  }
  const scopes = coordinationTodoWriteScopes(todo);
  // This hypothetical fresh identity is never published or returned as proof.
  const key = lease?.idempotency_key === "recovery-probe" ? "recovery-probe-next" : "recovery-probe";
  const decision = decideTaskLeaseAcquire({handoff_mode: mode, registered_agents: input.registered_agents,
    ...facts, command: {owner: input.actor_agent_id ?? "", idempotency_key: key,
      ttl_seconds: 60, write_scopes: scopes, expected_version: lease?.version ?? 0}});
  const acceptance = acceptanceWorkGuard(head, input.goal_id, input.todo_id);
  const blocked = decision.outcome !== "apply" ? decision.code
    : acceptance !== null && !acceptance.allowed ? String(acceptance.reason_code) : null;
  if (blocked !== null) {
    return {...base, action: "resolve_acquire_rejection", reason_code: blocked,
      reason: "Current mode, Todo eligibility, acceptance or write scopes reject acquisition. Resolve that condition before retrying the edit; changing handoff mode is not a recovery shortcut."};
  }
  return {...base, action: "acquire_fresh_lease",
    reason: "Inspect the current version, acquire a short lease with a fresh key, retry the edit with the returned proof, then release that lease. Acquisition revalidates current authority.",
    acquire: {command: "loopx task-lease acquire", goal_id: input.goal_id, todo_id: input.todo_id,
      owner: input.actor_agent_id, expected_version: lease?.version ?? 0,
      ttl_seconds: 60, write_scopes: scopes, fresh_idempotency_key_required: true,
      recheck_version_with_inspect: true},
    retry,
    release: {command: "loopx task-lease release",
      requires_flags: ["--owner", "--idempotency-key", "--expected-version"],
      proof_source: "current_owner_lease_readback"}};
}
