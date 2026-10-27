/** Ordinary task operations carry the accepted claim, not hand-copied lease
 * counters. The canonical lifecycle/fence owners still decide every effect. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256,
  requireAuthorityStoreId} from "./authority_store_codec.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLease, canonicalTaskLeaseAcquireFacts} from "./task_lease_state.ts";
import {coordinationTodoWriteScopes} from "./todo_write_scopes.ts";
import {leaseEpoch, leaseVersion, leaseIsActive, normalizeTtl, type LeaseRecord} from "../work_items/task_lease_acquire.ts";
import {sameLeaseWorkspace} from "../work_items/task_lease_workspace.ts";
import {leaseWriteRepository} from "../work_items/task_lease_repository.ts";
import {evaluateTaskLeaseAcquireDecision, materializeTaskLeaseAcquire} from "../work_items/task_lease_acquire_decision.ts";
import {decideTaskLeaseLifecycle, materializeTaskLeaseLifecycle} from "../work_items/task_lease_lifecycle_decision.ts";

export function claimExecutionReference(id: unknown, key: unknown, version: unknown): string | undefined {
  if (id == null) return undefined;
  const value = requireAuthorityStoreId(id, "claim_operation_id");
  if (key != null || version != null) throw new AuthorityStoreProtocolError(
    "Use the original claim operation or explicit task-lease proof, not both");
  return value;
}

type ExecutionInput = {goal_id: string; todo_id: string; actor_agent_id: string | null;
  registered_agents: readonly string[]; now: Date};

/** Resolve only this accepted execution. No acquisition, adoption of a newer
 * generation, or write retry is hidden behind a progress/completion request. */
export async function claimExecutionProof(store: AuthorityStore, head: JsonObject,
  input: ExecutionInput, operationId: string, allowReleasedCloseout = false): Promise<{lease_idempotency_key: string; lease_expected_version: number} | null> {
  const receipt = await store.readReceipt(operationId);
  if (receipt.status !== "found" || receipt.receipts.length !== 1) {
    throw new AuthorityStoreProtocolError("Original task claim unavailable; recover that claim before writing");
  }
  const original = receipt.receipts[0];
  const result = canonicalAuthorityObject(original.result, "original task claim");
  if (original.schema_version !== "loopx_coordination_todo_claim_receipt_v0" ||
      original.operation_id !== operationId || original.goal_id !== input.goal_id ||
      result.todo_id !== input.todo_id || result.claimed_by !== input.actor_agent_id ||
      result.execution_managed !== true) throw new AuthorityStoreProtocolError(
    "The claim reference does not name this actor's managed task execution");
  // A soft claim has no execution lease. Its normal writer still checks policy.
  if (result.handoff_mode !== "hard_lease") {
    if (result.handoff_mode === "soft_claim" && head.handoff_mode === "soft_claim") return null;
    throw new AuthorityStoreProtocolError("Task ownership policy changed; review a new task claim");
  }
  if (head.handoff_mode !== "hard_lease") throw new AuthorityStoreProtocolError("Task ownership policy changed; review the current task before continuing");
  const accepted = canonicalTaskLease(canonicalAuthorityObject(result.lease, "accepted execution"), input.goal_id, input.todo_id);
  const index = indexCoordinationProjection(head, input.goal_id);
  const raw = index.leases.get(input.todo_id);
  const current = raw && canonicalTaskLease(raw, input.goal_id, input.todo_id);
  const retainedCloseout = allowReleasedCloseout && current?.status === "released" &&
    index.todos.get(input.todo_id)?.status === "done";
  if (accepted.owner !== input.actor_agent_id || !current || !(leaseIsActive(current, input.now) || retainedCloseout) || current.owner !== input.actor_agent_id || current.idempotency_key !== accepted.idempotency_key ||
      leaseEpoch(current) !== leaseEpoch(accepted) || leaseVersion(current) < leaseVersion(accepted) ||
      !sameLeaseWorkspace(current.write_workspace, accepted.write_workspace) ||
      leaseWriteRepository(current.write_repository) !== leaseWriteRepository(accepted.write_repository)) {
    throw new AuthorityStoreProtocolError("Original task execution changed; reconcile task ownership before a new claim");
  }
  return {lease_idempotency_key: String(current.idempotency_key), lease_expected_version: leaseVersion(current)};
}

/** Maintain only a still-active grant, using the normal lifecycle decision.
 * Normal activity renews in the final third of its original TTL; an earlier
 * read/write is not a renewal, and an expired grant cannot be resurrected. */
export function maintainClaimExecution(head: JsonObject, input: ExecutionInput, lease: LeaseRecord): LeaseRecord | null {
  // Historical grants without an original TTL cannot justify a new duration.
  if (typeof lease.acquire_ttl_seconds !== "number") return null;
  const ttl = normalizeTtl(lease.acquire_ttl_seconds);
  if (!leaseIsActive(lease, input.now) || new Date(String(lease.expires_at)).valueOf() - input.now.valueOf() > ttl * 1000 / 3) return null;
  const facts = canonicalTaskLeaseAcquireFacts(indexCoordinationProjection(head, input.goal_id),
    input.goal_id, input.todo_id, input.registered_agents, input.now);
  const command = {operation: "renew" as const, owner: String(lease.owner),
    idempotency_key: String(lease.idempotency_key), expected_version: leaseVersion(lease),
    ttl_seconds: ttl, new_owner: null, new_idempotency_key: null};
  const decision = decideTaskLeaseLifecycle({handoff_mode: String(head.handoff_mode),
    registered_agents: input.registered_agents, todo: facts.todo, lease: facts.lease, command});
  if (decision.outcome !== "apply") throw new AuthorityStoreProtocolError(`Task execution maintenance rejected: ${decision.code}`);
  return materializeTaskLeaseLifecycle(lease, command, decision, input.now);
}

/** Atomic claim preparation, reusing acquire/lifecycle semantics and the
 * caller's provider CAS. A foreign retained execution needs explicit recovery. */
export function prepareClaimExecution(head: JsonObject,
  input: ExecutionInput & {operation_id: string; claimed_by: string}): {lease: LeaseRecord; changed: boolean} {
  const index = indexCoordinationProjection(head, input.goal_id);
  const facts = canonicalTaskLeaseAcquireFacts(index, input.goal_id, input.todo_id, input.registered_agents, input.now);
  const current = facts.current;
  if (current && current.owner !== input.claimed_by && current.status !== "released") {
    throw new AuthorityStoreProtocolError("Another task execution is retained; its owner must release or hand it over");
  }
  if (current && leaseIsActive(current, input.now)) {
    if (current.owner !== input.claimed_by) throw new AuthorityStoreProtocolError("Task is occupied by another actor");
    const renewed = maintainClaimExecution(head, input, current);
    return {lease: renewed ?? current, changed: renewed !== null};
  }
  const command = {owner: input.claimed_by,
    idempotency_key: `claim-${canonicalAuthoritySha256({goal_id: input.goal_id, todo_id: input.todo_id,
      owner: input.claimed_by, operation_id: input.operation_id})}`,
    expected_version: current ? leaseVersion(current) : 0, ttl_seconds: normalizeTtl(null),
    write_scopes: coordinationTodoWriteScopes(index.todos.get(input.todo_id)!)};
  const decision = evaluateTaskLeaseAcquireDecision({handoff_mode: String(head.handoff_mode),
    registered_agents: [...input.registered_agents], ...facts, command});
  if (decision.outcome !== "apply") throw new AuthorityStoreProtocolError(`Task claim execution rejected: ${decision.code}`);
  return {lease: materializeTaskLeaseAcquire(input, command, decision, input.now), changed: true};
}
