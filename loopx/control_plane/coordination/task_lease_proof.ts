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
import type {CoordinationTodoUpdateInput} from "./todo_update_intent.ts";
import {isOwnerDeferral} from "./todo_deferred_lifecycle.ts";
import {blockedLifecycleRejection, isBlockedLifecycleTransition} from "./todo_blocked_lifecycle.ts";
import {TODO_WORK_REQUIREMENT_FIELDS} from "../todos/work_requirements.ts";
import {acceptanceWorkGuard} from "../goals/acceptance_contract.ts";
import {leaseEpoch} from "../work_items/task_lease_acquire.ts";
import {leaseRepositoryRejection} from "../work_items/task_lease_repository.ts";
import {evaluateCoordinationTerminalFence, COORDINATION_TERMINAL_FENCE_REQUEST_SCHEMA} from "./todo_lifecycle_decision.ts";
import {todoExecutionDependencyRejection} from "./todo_execution_dependency.ts";

export interface TaskLeaseProof {
  idempotency_key: string;
  expected_version: number;
}

/** A reminder's bound actor owns copy edits, not an agent execution claim.
 * This classifies normalized intent only; shared actor admission and the
 * current lease/provider fences still decide whether an edit may commit. */
export function isBoundUserActionMetadataUpdate(todo: JsonObject, input: CoordinationTodoUpdateInput): boolean {
  return todo.role === "user" && todo.task_class === "user_action" &&
    todo.status === "open" && todo.claimed_by == null &&
    input.actor_agent_id !== null && todo.bound_agent === input.actor_agent_id &&
    input.registered_agents.includes(input.actor_agent_id) &&
    input.completion === undefined && input.completion_validation_revision === undefined &&
    input.monitor_observation === undefined &&
    Object.keys(input.planning_intent ?? {}).every(field => field === "evidence");
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
export function todoUpdateLeaseRecovery(head: JsonObject, input: CoordinationTodoUpdateInput, mode: string): TodoUpdateLeaseRecovery {
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
  // A blocked Todo cannot acquire execution authority. A bundled edit must
  // first use the existing administrative reopen, rather than reconcile a
  // claim/acquire a lease that the blocked status itself makes ineligible.
  // This is a diagnostic probe only; the actual retry rechecks owner admission
  // and the lifecycle fence in the ordinary provider transaction.
  const reopen = {...input, patch: {}, clear_fields: [], planning_intent: {
    status: "open", reason: "Reviewed lifecycle recovery", clear_resume_when: true,
  }};
  if (mode === "hard_lease" && intent.status === "open" &&
      isBlockedLifecycleTransition(reopen, todo) &&
      blockedLifecycleRejection({goal_id: input.goal_id, todo_id: input.todo_id,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        lease: lease === null ? undefined : index.leases.get(input.todo_id),
        lease_idempotency_key: input.lease_idempotency_key ?? null,
        lease_expected_version: input.lease_expected_version ?? null, now: input.now}) === null) {
    return {...base, action: "resolve_lifecycle_edit",
      reason: "Reopen this blocked Todo separately with only status, clear-resume-when and a reviewed reason. Do not bundle notes, evidence, work requirements or ownership. Then claim/acquire a fresh execution lease before retrying the remaining edit; reopening alone grants no execution authority.",
      retry: {command: "loopx todo update --status open --clear-resume-when --reason '<reviewed reason>'",
        goal_id: input.goal_id, todo_id: input.todo_id, agent_id: input.actor_agent_id,
        requires_flags: ["--status", "--clear-resume-when", "--reason"],
        proof_source: "fresh_lifecycle_admission"}};
  }
  const editRejection = lease === null || isOwnerDeferral(input, todo) ? null : leasedTodoEditRejection(todo, intent);
  if (editRejection !== null) {
    return {...base, action: "resolve_lifecycle_edit", reason_code: editRejection.code,
      reason: "This edit changes leased work requirements or status. Use the owning lifecycle transition; reacquiring a lease alone cannot authorize this metadata edit."};
  }
  if (todo.claimed_by !== input.actor_agent_id && !isBoundUserActionMetadataUpdate(todo, input)) {
    return {...base, action: "reconcile_lease_owner",
      reason: "A leased update requires the actor to own the Todo claim. Reconcile ownership before acquiring execution authority."};
  }
  const dependency = todoExecutionDependencyRejection(index.todos, input.todo_id);
  // Execution must continue to wait. If the owner has instead reviewed this
  // dependency as obsolete, expose the existing administrative lifecycle;
  // clearing it directly with an inactive execution proof is still rejected.
  const dependencyPause = {...input, patch: {}, clear_fields: [],
    lease_idempotency_key: null, lease_expected_version: null,
    planning_intent: {status: "blocked", clear_resume_when: true, reason: "Reviewed obsolete dependency"}};
  if (dependency !== null && mode === "hard_lease" && leaseState === "released" &&
      sameOwner && todo.claimed_by === input.actor_agent_id && todo.task_class === "advancement_task" &&
      isBlockedLifecycleTransition(dependencyPause, todo) &&
      blockedLifecycleRejection({goal_id: input.goal_id, todo_id: input.todo_id,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        lease: index.leases.get(input.todo_id), lease_idempotency_key: null,
        lease_expected_version: null, now: input.now}) === null) {
    return {...base, action: "resolve_lifecycle_edit", reason_code: dependency.code,
      reason: "Keep waiting if the dependency remains valid. For an evidence-backed owner replan, first block the unchanged Todo and clear its obsolete wait through the existing lifecycle, then reopen it. Each step requires fresh provider CAS and its own operation identity; neither consumes an old lease or grants execution. Do not bundle copy, evidence, requirements or ownership edits. Acquire a fresh execution lease afterwards.",
      lifecycle_replan: {condition: "owner_reviewed_obsolete_dependency", steps: [
        {command: "loopx todo update --status blocked --clear-resume-when --reason '<reviewed obsolete dependency>'"},
        {command: "loopx todo update --status open --clear-resume-when --reason '<reviewed new route>'"},
      ], goal_id: input.goal_id, todo_id: input.todo_id, agent_id: input.actor_agent_id,
      requires_flags: ["--update-operation-id", "--update-expected-provider-revision"],
      execution_proof: "omit", next_execution: "acquire_fresh_lease"}};
  }
  if (dependency !== null) return {...base, action: "resolve_acquire_rejection",
    reason_code: dependency.code, reason: dependency.reason};
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
