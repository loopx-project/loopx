import {configuredGoalChecks} from "../goals/acceptance_contract.ts";
/** Byte-bounded canonical reads. Continuations are positions in one immutable
 * provider revision, never permission to combine independently current heads. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {MAX_LOCAL_SNAPSHOT_BYTES} from "../effect_runtime_snapshot.ts";
import {decodeProjectionReadback, confirmProjectionReadback} from "../todos/projection_delivery.ts";
import {authorityStoreSourceAuthority, type AuthorityStore} from "./authority_store.ts";
import {canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {canonicalTodoCollection} from "./local_authority_read.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot, localAuthorityOpenFailure,
  type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";

import {LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_REQUEST_SCHEMA as CANONICAL_SNAPSHOT_PAGE_REQUEST,
  LOCAL_COORDINATION_TODO_SNAPSHOT_PAGE_RESULT_SCHEMA as CANONICAL_SNAPSHOT_PAGE_RESULT} from "./coordination_state_contract.generated.ts";
export {CANONICAL_SNAPSHOT_PAGE_REQUEST, CANONICAL_SNAPSHOT_PAGE_RESULT};
// Leave room for the RPC envelope below the unchanged 2 MiB transport limit.
export const CANONICAL_SNAPSHOT_PAGE_BYTES = 1792 * 1024;
export const CANONICAL_SNAPSHOT_PAGE_ITEMS = 4096;
// The private file already has a 64 MiB transport ceiling. Reserve the entire
// 2 MiB request ceiling for its response envelope (including the request id).
export const CANONICAL_SNAPSHOT_LOCAL_BYTES = MAX_LOCAL_SNAPSHOT_BYTES - 2 * 1024 * 1024;
interface Snapshot extends JsonObject {
  goal_id: string; store_identity: string; provider_revision: string; cursor: string;
  query_sha256: string; todo_count: number; lease_count: number;
}
interface Position {snapshot: Snapshot; todo_offset: number; lease_offset: number}
class SnapshotReadError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {super(message); this.code = code;}
}
function check(condition: unknown, code: string, message: string): asserts condition {
  if (!condition) throw new SnapshotReadError(code, message);
}
function count(value: unknown): number {
  check(typeof value === "number" && Number.isSafeInteger(value) && value >= 0,
    "canonical_snapshot_request_invalid", "snapshot position must be a non-negative safe integer");
  return value;
}
function decodePosition(value: unknown): Position | null {
  if (value === null) return null;
  const row = requireJsonObject(value, "snapshot continuation");
  check(hasExactAuthorityKeys(row, ["snapshot", "todo_offset", "lease_offset"]),
    "canonical_snapshot_request_invalid", "invalid continuation fields");
  const raw = requireJsonObject(row.snapshot, "snapshot identity");
  check(hasExactAuthorityKeys(raw, ["goal_id", "store_identity", "provider_revision", "cursor", "query_sha256", "todo_count", "lease_count"]),
    "canonical_snapshot_request_invalid", "invalid snapshot fields");
  const snapshot: Snapshot = {
    goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
    store_identity: requireAuthorityStoreId(raw.store_identity, "store identity"),
    provider_revision: requireAuthorityStoreId(raw.provider_revision, "provider revision"),
    cursor: requireAuthorityStoreId(raw.cursor, "cursor"),
    query_sha256: requireAuthorityStoreId(raw.query_sha256, "query digest"),
    todo_count: count(raw.todo_count), lease_count: count(raw.lease_count),
  };
  const todo = count(row.todo_offset), lease = count(row.lease_offset);
  check(todo <= snapshot.todo_count && lease <= snapshot.lease_count && todo + lease > 0 &&
    todo + lease < snapshot.todo_count + snapshot.lease_count && (lease === 0 || todo === snapshot.todo_count),
  "canonical_snapshot_request_invalid", "continuation must advance within the ordered snapshot");
  return {snapshot, todo_offset: todo, lease_offset: lease};
}

/** Exported for provider conformance, using the same store contract as the
 * shipped local entrypoint. No test-only pagination implementation is used. */
export async function readCanonicalSnapshotFromStore(value: unknown, store: AuthorityStore,
  completeBytes = 0): Promise<JsonObject> {
  const input = requireJsonObject(value, "canonical snapshot request");
  check(hasExactAuthorityKeys(input, ["schema_version", "runtime_root", "goal_id", "include_leases", "projection_readback", "after"]) &&
    input.schema_version === CANONICAL_SNAPSHOT_PAGE_REQUEST && typeof input.include_leases === "boolean",
  "canonical_snapshot_request_invalid", "invalid canonical snapshot request");
  const goal = requireAuthorityStoreId(input.goal_id, "goal id");
  const after = decodePosition(input.after);
  const readback = input.projection_readback === null ? null : decodeProjectionReadback(input.projection_readback);
  const source = authorityStoreSourceAuthority(store);
  const base = {schema_version: CANONICAL_SNAPSHOT_PAGE_RESULT, source_authority: source,
    decision_read_from_provider: true, legacy_fallback_used: false};
  const identity = await store.storeIdentity();
  if (identity.status !== "available") {
    // A promoted but absent store has the same caller contract as the old
    // single-read path: recovery must see "missing". Identity is unavailable
    // before a store exists, so distinguish absence from an identity failure
    // without opening a replacement authority.
    if (identity.status === "unavailable") {
      const absent = await store.loadAuthority();
      if (absent.status === "missing") return {...base, ...absent};
    }
    return {...base, ...identity};
  }
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return {...base, ...head};
  const query = canonicalAuthoritySha256({goal_id: goal, include_leases: input.include_leases, projection_readback: readback});
  // Fail before expensive projection work when a continuation is already stale.
  if (after !== null) check(after.snapshot.goal_id === goal && after.snapshot.store_identity === identity.store_identity &&
    after.snapshot.provider_revision === head.provider_revision && after.snapshot.cursor === head.cursor &&
    after.snapshot.query_sha256 === query,
  "canonical_snapshot_changed", "canonical snapshot changed; restart the complete read, never append a newer page");
  const {projection, todoReadModel: readModel, leaseIndex: leases, acceptance, oracleObservationBasis, projectWorkGuards} =
    canonicalTodoCollection(head.head, goal, input.include_leases);
  const snapshot: Snapshot = {goal_id: goal, store_identity: identity.store_identity,
    provider_revision: head.provider_revision, cursor: head.cursor, query_sha256: query,
    todo_count: projection.todo_ids.length, lease_count: leases?.lease_todo_ids.length ?? 0};
  if (after !== null) check(after.snapshot.todo_count === snapshot.todo_count && after.snapshot.lease_count === snapshot.lease_count,
    "canonical_snapshot_changed", "snapshot population changed");
  const observedIdentity = await store.storeIdentity();
  check(observedIdentity.status === "available" && observedIdentity.store_identity === identity.store_identity,
    "canonical_snapshot_changed", "store incarnation changed while loading the snapshot");
  const todoStart = after?.todo_offset ?? 0, leaseStart = after?.lease_offset ?? 0;
  const metadata = {todo_read_model: readModel,
    ...(!!configuredGoalChecks(acceptance) || acceptance.outcome_requirements || acceptance.goal_objective ? {goal_acceptance_contract: acceptance} : {}),
    ...(oracleObservationBasis === null ? {} : {goal_acceptance_oracle_observation_basis: oracleObservationBasis}),
    ...(leases === null ? {} : {handoff_mode: head.head.handoff_mode ?? "legacy"}),
    ...(readback === null ? {} : {projection_readback: confirmProjectionReadback(readback, head.provider_revision)})};
  function result(chosenTodos: readonly string[], chosenLeases: readonly string[], guards: JsonObject): JsonObject {
    const todoEnd = todoStart + chosenTodos.length, leaseEnd = leaseStart + chosenLeases.length;
    return {...base, status: "page", snapshot, metadata,
      todos: chosenTodos.map(id => projection.todos.get(id)!),
      ...(leases === null ? {} : {leases: chosenLeases.map(id => leases.leases.get(id)!)}),
      ...(!configuredGoalChecks(acceptance) ? {} : {goal_acceptance_work_guards: Object.fromEntries(
        chosenTodos.filter(id => Object.hasOwn(guards, id)).map(id => [id, guards[id]]))}),
      next: todoEnd === snapshot.todo_count && leaseEnd === snapshot.lease_count ? null :
        {snapshot, todo_offset: todoEnd, lease_offset: leaseEnd}};
  }
  const bytes = (result: JsonObject) => Buffer.byteLength(JSON.stringify(result), "utf8");
  // Only a new complete read may use the explicit private-file entrypoint.
  // Reuse this same admission, metadata and identity; never cache a provider
  // head between RPCs or substitute the older unguarded todo_list endpoint.
  let completeGuards: JsonObject | null = null;
  if (after === null && completeBytes > 0) {
    completeGuards = configuredGoalChecks(acceptance)
      ? projectWorkGuards(projection.todo_ids) : {};
    const complete = result(projection.todo_ids, leases?.lease_todo_ids ?? [], completeGuards);
    if (bytes(complete) <= Math.min(completeBytes, CANONICAL_SNAPSHOT_LOCAL_BYTES)) return complete;
  }
  // A snapshot beyond the private-file capacity still uses the original page
  // bounds and continuation checks. No lost oversized response or retry occurs.
  const todoIds = projection.todo_ids.slice(todoStart, todoStart + CANONICAL_SNAPSHOT_PAGE_ITEMS);
  const leaseIds = leases?.lease_todo_ids.slice(leaseStart, leaseStart + CANONICAL_SNAPSHOT_PAGE_ITEMS - todoIds.length) ?? [];
  const guards = completeGuards ?? (configuredGoalChecks(acceptance)
    ? projectWorkGuards(todoIds) : {});
  const page = (size: number) => result(todoIds.slice(0, size),
    leaseIds.slice(0, Math.max(0, size - todoIds.length)), guards);
  const available = todoIds.length + leaseIds.length;
  const complete = page(available);
  if (bytes(complete) <= CANONICAL_SNAPSHOT_PAGE_BYTES) return complete;
  check(bytes(page(0)) <= CANONICAL_SNAPSHOT_PAGE_BYTES, "canonical_snapshot_metadata_too_large", "snapshot metadata exceeds the page budget");
  let low = 0, high = available;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (bytes(page(middle)) <= CANONICAL_SNAPSHOT_PAGE_BYTES) low = middle;
    else high = middle - 1;
  }
  check(available === 0 || low > 0, "canonical_snapshot_record_too_large", "one canonical record exceeds the page budget; records are never truncated");
  return page(low);
}

/** Read-only RPC owner. A failed page never supplies a partial success or a
 * legacy fallback; the client must discard any earlier pages of that read. */
export async function readCanonicalSnapshotPage(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}, completeBytes = 0): Promise<JsonObject> {
  try {
    const input = requireJsonObject(value, "canonical snapshot request");
    const root = requireLocalAuthorityRuntimeRoot(input.runtime_root);
    const goal = requireAuthorityStoreId(input.goal_id, "goal id");
    const store = await openRuntimeAuthorityStore(root, goal, dependencies, {existingOnly: true});
    return await readCanonicalSnapshotFromStore(input, store, completeBytes);
  } catch (error) {
    return {schema_version: CANONICAL_SNAPSHOT_PAGE_RESULT, status: "failed",
      reason_code: error instanceof SnapshotReadError ? error.code : "canonical_snapshot_request_invalid",
      reason: error instanceof Error ? error.message : "canonical snapshot read failed",
      decision_read_from_provider: true, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
  }
}

/** Same canonical contract, opting into the existing same-UID file transport.
 * Too-large collections keep bounded pages; failures never supply partial rows. */
export async function readCanonicalSnapshot(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  return readCanonicalSnapshotPage(value, dependencies, CANONICAL_SNAPSHOT_LOCAL_BYTES);
}
