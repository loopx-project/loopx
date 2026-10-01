import {leaseWorkspace} from "../work_items/task_lease_workspace.ts";
/** Full canonical facts shared by lease acquisition, maintenance and Todo claim. */
import type {JsonObject} from "../effect_program.ts";
import {AuthorityStoreProtocolError} from "./authority_store_codec.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {normalizeTodoAgent} from "./todo_agents.ts";
import {leaseOwnerRejection} from "../work_items/task_lease_eligibility.ts";
import {leaseVersion, leaseEpoch, leaseInteger, leaseIsActive, normalizeOwner,
  normalizeIdempotencyKey, TASK_LEASE_SCHEMA_VERSION, type LeaseRecord, type TodoFact} from "../work_items/task_lease_acquire.ts";
import type {AcquireDecisionInput} from "../work_items/task_lease_acquire_decision.ts";
import {normalizeTodoRepository} from "../todos/work_requirements.ts";
import {leaseWriteRepository} from "../work_items/task_lease_repository.ts";

export function canonicalTaskLease(value: JsonObject, goalId: string, todoId: string): LeaseRecord {
  if ((value.schema_version !== undefined && value.schema_version !== TASK_LEASE_SCHEMA_VERSION) ||
      (value.goal_id !== undefined && value.goal_id !== goalId) || value.todo_id !== todoId ||
      (value.status !== "active" && value.status !== "released")) {
    throw new AuthorityStoreProtocolError("canonical lease identity or schema is invalid");
  }
  if (typeof value.owner !== "string" || typeof value.idempotency_key !== "string" ||
      normalizeOwner(value.owner) !== value.owner || normalizeIdempotencyKey(value.idempotency_key) !== value.idempotency_key) {
    throw new AuthorityStoreProtocolError("canonical lease owner and execution key must be normalized strings");
  }
  leaseVersion(value); leaseEpoch(value); leaseWorkspace(value.write_workspace);
  try { leaseWriteRepository(value.write_repository); }
  catch { throw new AuthorityStoreProtocolError("canonical lease write_repository must be a canonical repository identity or null"); }
  if (value.write_scopes !== undefined && (!Array.isArray(value.write_scopes) ||
      value.write_scopes.some(scope => typeof scope !== "string"))) {
    throw new AuthorityStoreProtocolError("canonical lease write_scopes must be strings");
  }
  // Older canonical records may omit the wire tag. Normalize once before
  // shared lease rules inspect it, without rewriting the persisted record.
  return {...value, schema_version: TASK_LEASE_SCHEMA_VERSION};
}

export function canonicalLeaseTodoFact(todo: JsonObject | undefined): TodoFact | null {
  if (!todo || todo.archive_state !== "active") return null;
  const excluded = todo.excluded_agents ?? [];
  if (!Array.isArray(excluded)) throw new AuthorityStoreProtocolError("Todo exclusions must be an array");
  return {todo_id: String(todo.todo_id), status: String(todo.status),
    task_repository: normalizeTodoRepository(todo.task_repository),
    claimed_by: todo.claimed_by == null ? null : normalizeTodoAgent(todo.claimed_by, "todo.claimed_by"),
    excluded_agents: excluded.map(value => normalizeTodoAgent(value, "todo.excluded_agents"))};
}

/** Every retained lease is paired with its current Todo, never a display page. */
export function canonicalTaskLeaseAcquireFacts(index: ReturnType<typeof indexCoordinationProjection>,
  goalId: string, todoId: string, registered: readonly string[], now: Date):
  Pick<AcquireDecisionInput, "todo" | "lease" | "other_leases"> & {current: LeaseRecord | null} {
  const raw = index.leases.get(todoId);
  const current = raw ? canonicalTaskLease(raw, goalId, todoId) : null;
  const todo = canonicalLeaseTodoFact(index.todos.get(todoId));
  const lease = current === null ? null : {present: true, active: leaseIsActive(current, now),
    status: String(current.status), owner: String(current.owner), idempotency_key: String(current.idempotency_key),
    version: leaseVersion(current), lease_epoch: leaseEpoch(current),
    write_scopes: (current.write_scopes ?? []) as string[], write_repository: leaseWriteRepository(current.write_repository), write_workspace: leaseWorkspace(current.write_workspace),
    acquire_ttl_seconds: leaseInteger(current, "acquire_ttl_seconds")};
  const other_leases = [...index.leases].flatMap(([id, rawLease]) => {
    if (id === todoId) return [];
    const candidate = canonicalTaskLease(rawLease, goalId, id);
    const active = leaseIsActive(candidate, now);
    return [{todo_id: id, owner: String(candidate.owner), active,
      effective: active && leaseOwnerRejection(canonicalLeaseTodoFact(index.todos.get(id)), String(candidate.owner), registered) === null,
      write_scopes: (candidate.write_scopes ?? []) as string[], write_repository: leaseWriteRepository(candidate.write_repository), write_workspace: leaseWorkspace(candidate.write_workspace)}];
  });
  return {todo, lease, other_leases, current};
}
