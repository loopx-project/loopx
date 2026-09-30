/** Read-only ownership observations. These records describe claims/leases, never grant execution. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {parseIsoTimestamp} from "../runtime_timestamp.ts";
import {requireAuthorityStoreId} from "./authority_store_codec.ts";
import {CoordinationProjectionRead} from "./coordination_projection.ts";
import {leaseEpoch, leaseIsActive, TASK_LEASE_SCHEMA_VERSION} from "../work_items/task_lease_acquire.ts";

export const OWNERSHIP_OBSERVATION_SCHEMA = "loopx_ownership_observation_request_v0";
export const OWNERSHIP_OBSERVATION_RESULT = "loopx_ownership_observation_result_v0";
export const CANONICAL_OWNERSHIP_DISPLAY_LIMIT = 100;

type ObservationStatus = "soft_claim" | "hard_lease" | "hard_lease_unreadable";
function objects(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value.map(item => requireJsonObject(item, label));
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function note(todoId: string): JsonObject {
  const status: ObservationStatus = "hard_lease_unreadable";
  return {todo_id: todoId, status, reason: "corrupt_lease"};
}

function displayEntry(item: JsonObject): JsonObject {
  const entry: JsonObject = {};
  for (const key of ["todo_id", "owner_agent", "claimed_by", "lease_until", "expires_at", "status", "reason"]) {
    const value = text(item[key]);
    if (value) entry[key] = value;
  }
  for (const key of ["lease_version", "lease_epoch"]) {
    if (typeof item[key] === "number" && Number.isSafeInteger(item[key])) entry[key] = item[key];
  }
  return entry;
}

/** Both source adapters share time/generation/conflict rules; explicit [] is authoritative. */
export function projectOwnershipObservation(value: unknown): JsonObject {
  const input = requireJsonObject(value, "ownership observation");
  if (input.schema_version !== OWNERSHIP_OBSERVATION_SCHEMA) throw new Error("ownership observation schema mismatch");
  const at = typeof input.observed_at === "string" ? parseIsoTimestamp(input.observed_at) : null;
  if (at === null) throw new Error("observed_at must be a valid timestamp");
  const todos = objects(input.todos, "todos");
  const claims = new Map(todos.map(todo => [text(todo.todo_id), text(todo.claimed_by)]));
  const explicit = input.explicit_entries == null ? null : objects(input.explicit_entries, "explicit_entries");
  const entries: JsonObject[] = explicit === null ? todos.filter(todo => text(todo.claimed_by)).map(todo => ({
    todo_id: todo.todo_id ?? null, owner_agent: todo.claimed_by!, status: "soft_claim" satisfies ObservationStatus,
  })) : explicit.map(displayEntry).filter(entry => text(entry.todo_id) || text(entry.owner_agent) || text(entry.claimed_by));
  for (const row of objects(input.lease_rows, "lease_rows")) {
    const todoId = text(row.todo_id);
    if (!todoId) throw new Error("lease observation requires a Todo identity");
    if (row.unreadable === true) {entries.push(note(todoId)); continue;}
    const lease = row.lease == null ? null : requireJsonObject(row.lease, "lease");
    if (lease === null) continue;
    try {
      if (lease.schema_version !== TASK_LEASE_SCHEMA_VERSION || lease.todo_id !== todoId) throw new Error("lease identity/schema mismatch");
      if (!leaseIsActive(lease, at)) continue;
      const entry: JsonObject = {todo_id: todoId, status: "hard_lease" satisfies ObservationStatus,
        lease_epoch: leaseEpoch(lease), expires_at: lease.expires_at!};
      const owner = text(lease.owner);
      if (owner) entry.owner_agent = owner;
      if (typeof lease.version === "number" && Number.isInteger(lease.version)) entry.lease_version = lease.version;
      const claim = claims.get(todoId);
      if (owner && claim && owner !== claim) {entry.reason = "owner_conflicts_with_claim"; entry.claimed_by = claim;}
      entries.push(entry);
    } catch { entries.push(note(todoId)); }
  }
  return {schema_version: OWNERSHIP_OBSERVATION_RESULT, status: "loaded", entries,
    total_count: entries.length, observed_at: input.observed_at!};
}

/** One complete, validated revision; no display, local files, receipts or writes. */
export async function readCoordinationOwnership(store: AuthorityStore, goalId: string, observedAt: string): Promise<JsonObject> {
  requireAuthorityStoreId(goalId, "goal id");
  const loaded = await store.loadAuthority();
  if (loaded.status !== "loaded") return {schema_version: OWNERSHIP_OBSERVATION_RESULT, ...loaded};
  const read = new CoordinationProjectionRead(loaded.head, goalId);
  const index = read.coordinationIndex;
  read.validateTodoReadModel();
  const todos = [...index.todos.values()].filter(todo => todo.archive_state === "active" && todo.done !== true);
  const result = projectOwnershipObservation({schema_version: OWNERSHIP_OBSERVATION_SCHEMA,
    observed_at: observedAt, todos, explicit_entries: todos.filter(todo => todo.role === "agent" && text(todo.claimed_by))
      .map(todo => ({todo_id: todo.todo_id, owner_agent: todo.claimed_by!, status: "soft_claim"})),
    lease_rows: [...index.leases.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([todo_id, lease]) => ({todo_id, lease})),
  });
  // Evaluate the whole snapshot before bounding display; diagnostics are retained first.
  const entries = result.entries as JsonObject[];
  const ordered = [...entries.filter(row => row.reason), ...entries.filter(row => !row.reason)];
  return {...result, entries: ordered.slice(0, CANONICAL_OWNERSHIP_DISPLAY_LIMIT),
    truncated: entries.length > CANONICAL_OWNERSHIP_DISPLAY_LIMIT, display_limit: CANONICAL_OWNERSHIP_DISPLAY_LIMIT,
    todo_count: index.todos.size, lease_count: index.leases.size,
    provider_revision: loaded.provider_revision, cursor: loaded.cursor};
}
