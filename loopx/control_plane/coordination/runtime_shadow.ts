import {verifyShadowRegistrySource, withShadowRegistrySource} from "./shadow_registry_source.ts";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA, currentGraphTodoIds} from "./source_projection.ts";
import { createHash } from "node:crypto";
import { readFile, readdir, lstat } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { withFileMutationLock } from "../effect_runtime_io.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import type { AuthorityStore } from "./authority_store.ts";
import { canonicalAuthorityBytes, canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId } from "./authority_store_codec.ts";
import { indexCoordinationProjectionTodos, validateCoordinationTodoReadModel } from "./coordination_projection.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import { legacyCoordinationTodoLockPath, legacyCoordinationLeaseLockPath, loadLegacyCoordinationWriterFence } from "./legacy_writer_fence.ts";
import { loadValidatedShadowLineage, localAuthorityShadowHeadDigest, ShadowLineageError } from "./local_authority_shadow.ts";
import { readOutboxCursor } from "./local_authority_shadow_outbox.ts";
import {
  bootstrapManagedShadow, rollbackManagedShadow, requireShadowCaptureBinding,
  withShadowMaintenanceLock, ShadowManagementError, requireShadowPrimaryWriteAllowed, shadowEventSourcePaths,
} from "./shadow_management.ts";
import * as schemas from "./coordination_state_contract.generated.ts";

export const COORDINATION_RUNTIME_SHADOW_REQUEST_SCHEMA = schemas.COORDINATION_RUNTIME_SHADOW_COMMIT_REQUEST_SCHEMA;
export const COORDINATION_RUNTIME_SHADOW_RESULT_SCHEMA = schemas.COORDINATION_RUNTIME_SHADOW_COMMIT_RESULT_SCHEMA;
export {
  COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA, COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA,
  COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA, COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA,
  COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA, COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA,
  COORDINATION_RUNTIME_SHADOW_RECEIPT_SCHEMA, COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA,
  COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA, COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA,
  COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export interface RuntimeShadowDependencies {
  createStore?: (directory: string, goalId: string) => AuthorityStore;
  createFileStore?: (directory: string, goalId: string) => FileAuthorityStore;
}

export interface ShadowRequest extends JsonObject {
  runtime_root: string;
  goal_id: string;
  projection: JsonObject;
  source_snapshot: JsonObject;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value || value.trim() !== value) throw new Error(`${label} must be a non-empty trimmed string`);
  return value;
}
function exact(value: JsonObject, keys: string[], label: string): void {
  if (Object.keys(value).some((key) => !keys.includes(key)) || keys.some((key) => !(key in value))) {
    throw new Error(`${label} has unsupported or missing fields`);
  }
}
function failure(schema: string, error: unknown): JsonObject {
  const typed = error as { reason_code?: string; code?: string };
  return { schema_version: schema, status: "failed", reason_code: typed.reason_code ?? typed.code ?? "invalid_shadow_request",
    reason: error instanceof Error ? error.message : "shadow operation failed", qualified: false,
    read_candidate_qualified: false, parity_matches: false, scope: "bounded", sustained_parity_verified: false,
    sustained_parity_verdict: "not_evaluated",
    primary_writeback_preserved: true, decision_read_from_shadow: false };
}
export function decodeRuntimeShadowRequest(value: unknown, schema: string, extra: string[] = []): ShadowRequest {
  const input = requireJsonObject(value, "coordination shadow request");
  const allowed = ["schema_version", "runtime_root", "goal_id", "projection", "source_snapshot", ...extra];
  if (Object.keys(input).some((key) => !allowed.includes(key)) || input.schema_version !== schema) {
    throw new Error("coordination shadow request schema or fields mismatch");
  }
  const root = text(input.runtime_root, "runtime_root");
  if (!isAbsolute(root)) throw new Error("runtime_root must be absolute");
  const goal = requireAuthorityStoreId(input.goal_id, "goal id");
  if (goal.includes("/") || goal.includes("\\") || goal === "." || goal === "..") throw new Error("goal id must be one path segment");
  return { ...input, runtime_root: resolve(root), goal_id: goal,
    projection: canonicalAuthorityObject(input.projection, "projection"),
    source_snapshot: canonicalAuthorityObject(input.source_snapshot, "source_snapshot") };
}

/** Source preconditions are ephemeral. They never become an alternative state ledger. */
function sourceSnapshot(request: ShadowRequest): JsonObject {
  const snapshot = request.source_snapshot;
  exact(snapshot, ["state_path", "registered_runtime_root", "registered_state_path", "state_bytes_sha256", "lease_inventory", "projection_sha256", "evidence_files", "registry_source", ...(snapshot.event_log_paths === undefined ? [] : ["event_log_paths"])], "source_snapshot");
  if (!isAbsolute(text(snapshot.state_path, "state_path")) ||
      !isAbsolute(text(snapshot.registered_runtime_root, "registered_runtime_root")) ||
      !isAbsolute(text(snapshot.registered_state_path, "registered_state_path")) ||
      !/^sha256:[0-9a-f]{64}$/.test(text(snapshot.state_bytes_sha256, "state_bytes_sha256")) ||
      !Array.isArray(snapshot.lease_inventory) || !Array.isArray(snapshot.evidence_files) ||
      snapshot.projection_sha256 !== canonicalAuthoritySha256(request.projection)) {
    throw new ShadowManagementError("source_snapshot_invalid");
  }
  return snapshot;
}
export async function withShadowSourceLocks<T>(request: ShadowRequest, operation: () => Promise<T>, registryMode: "current" | "retained" = "current"): Promise<T> {
  const snapshot = request.source_snapshot;
  if (!isAbsolute(text(snapshot.state_path, "state_path"))) throw new ShadowManagementError("source_snapshot_invalid");
  const root = request.runtime_root;
  const goal = request.goal_id;
  const paths = shadowEventSourcePaths(snapshot);
  const withEvents = async (index: number): Promise<T> => index === paths.length
    ? await withFileMutationLock(legacyCoordinationLeaseLockPath(root, goal), () =>
        withFileMutationLock(join(root, "goals", goal, "task-leases", ".task-leases"), () => registryMode === "current" ? withShadowRegistrySource(snapshot, operation) : operation()))
    : await withFileMutationLock(paths[index]!, () => withEvents(index + 1));
  return await withFileMutationLock(legacyCoordinationTodoLockPath(root, goal), () =>
    withFileMutationLock(String(snapshot.state_path), () =>
      withEvents(0)));
}
async function withPrePromotionSourceLocks<T>(request: ShadowRequest, operation: () => Promise<T>, registryMode: "current" | "retained" = "current"): Promise<T> {
  return await withShadowSourceLocks(request, async () => {
    const fence = await loadLegacyCoordinationWriterFence(request.runtime_root, request.goal_id);
    if (fence.status !== "missing") throw new ShadowManagementError(fence.status === "loaded" ? "legacy_authority_already_promoted" : fence.reason_code);
    return await operation();
  }, registryMode);
}
function bytesDigest(value: Uint8Array): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
async function optionalBytes(path: string): Promise<Buffer | null> {
  try { return await readFile(path); } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}
export async function verifyShadowSourceSnapshot(request: ShadowRequest): Promise<void> {
  const snapshot = sourceSnapshot(request);
  await verifyShadowRegistrySource(snapshot);
  if (resolve(String(snapshot.state_path)) !== resolve(String(snapshot.registered_state_path))) {
    throw new ShadowManagementError("shadow_source_state_path_mismatch");
  }
  const registeredRoot = resolve(String(snapshot.registered_runtime_root));
  if (registeredRoot !== request.runtime_root) {
    await requireShadowPrimaryWriteAllowed(registeredRoot, request.goal_id);
    const fence = await loadLegacyCoordinationWriterFence(registeredRoot, request.goal_id);
    if (fence.status !== "missing") throw new ShadowManagementError(fence.status === "loaded" ? "legacy_authority_already_promoted" : fence.reason_code);
    throw new ShadowManagementError("shadow_source_runtime_root_mismatch");
  }
  const rebuilt = projectCoordinationSource({
    schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA, kind: "snapshot",
    goal_id: request.goal_id, handoff_mode: request.projection.handoff_mode,
    todos: request.projection.todos, leases: request.projection.leases,
    read_model_schema: canonicalAuthorityObject(request.projection.todo_read_model, "source Todo read model").schema_version,
  }).projection as JsonObject;
  // Assembly publishes the current manifest. A persisted pre-extension
  // manifest remains valid under the existing reader compatibility rule;
  // verify it before retaining its exact bytes, never silently upgrade it.
  rebuilt.todo_read_model = validateCoordinationTodoReadModel(request.projection, request.goal_id);
  if (!canonicalAuthorityBytes(rebuilt).equals(canonicalAuthorityBytes(request.projection))) {
    throw new ShadowManagementError("source_projection_invalid");
  }
  const bytes = await optionalBytes(String(snapshot.state_path));
  if (bytes === null || bytesDigest(bytes) !== snapshot.state_bytes_sha256) throw new ShadowManagementError("source_changed_retry");
  const directory = join(request.runtime_root, "goals", request.goal_id, "task-leases");
  let names: string[];
  try { names = await readdir(directory); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    names = [];
  }
  const inventory: JsonObject[] = [];
  const leases: JsonObject[] = [];
  const currentTodoIds = currentGraphTodoIds(request.projection.todos as JsonObject[]);
  // ASCII filenames must use the same ordinal order as Python's source snapshot.
  const leaseNames = names.filter((name) => /^[A-Za-z0-9_.-]+\.json$/.test(name)).sort((left, right) => {
    if (left < right) return -1;
    if (left > right) return 1;
    return 0;
  });
  for (const name of leaseNames) {
    const data = await readFile(join(directory, name));
    const lease = canonicalAuthorityObject(JSON.parse(data.toString("utf8")), "lease");
    if (lease.goal_id !== request.goal_id || lease.todo_id !== name.slice(0, -5)) throw new ShadowManagementError("source_lease_identity_mismatch");
    inventory.push({ name, bytes_sha256: bytesDigest(data) });
    // The legacy directory is append-retained audit history. The source
    // inventory proves every file while the canonical head contains only live
    // edges to Todos that still exist in the current projection.
    if (currentTodoIds.has(String(lease.todo_id))) leases.push(lease);
  }
  if (!canonicalAuthorityBytes(inventory).equals(canonicalAuthorityBytes(snapshot.lease_inventory)) ||
      !canonicalAuthorityBytes(leases).equals(canonicalAuthorityBytes(request.projection.leases))) {
    throw new ShadowManagementError("source_changed_retry");
  }
  for (const raw of snapshot.evidence_files as JsonObject[]) {
    const evidence = requireJsonObject(raw, "evidence source");
    exact(evidence, ["path", "bytes_sha256"], "evidence source");
    const path = text(evidence.path, "evidence path");
    if (!isAbsolute(path)) throw new ShadowManagementError("source_snapshot_invalid");
    const data = await optionalBytes(path);
    if ((data === null ? null : bytesDigest(data)) !== evidence.bytes_sha256) throw new ShadowManagementError("source_changed_retry");
  }
}

export async function bootstrapCoordinationRuntimeShadow(value: unknown, _dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  const schema = schemas.COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_RESULT_SCHEMA;
  try {
    const request = decodeRuntimeShadowRequest(value, schemas.COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA, ["operation_id", "source_version"]);
    text(request.operation_id, "operation_id"); text(request.source_version, "source_version");
    const result = await bootstrapManagedShadow(request, {
      withPrimaryLocks: (operation) => withPrePromotionSourceLocks(request, operation),
      verifySourceSnapshot: () => verifyShadowSourceSnapshot(request),
    });
    return { schema_version: schema, ...result, primary_writeback_preserved: true, decision_read_from_shadow: false };
  } catch (error) { return failure(schema, error); }
}
export async function rollbackCoordinationRuntimeShadow(value: unknown, _dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  const schema = schemas.COORDINATION_RUNTIME_SHADOW_ROLLBACK_RESULT_SCHEMA;
  try {
    const request = decodeRuntimeShadowRequest(value, schemas.COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA,
      ["operation_id", "expected_provider_revision", "expected_bootstrap_operation_id"]);
    text(request.operation_id, "operation_id");
    const revision = request.expected_provider_revision;
    const bootstrap = request.expected_bootstrap_operation_id;
    if ((typeof revision === "string") === (typeof bootstrap === "string")) throw new Error("rollback requires exactly one revision or bootstrap operation selector");
    const result = await rollbackManagedShadow(request, { withPrimaryLocks: (operation) => withPrePromotionSourceLocks(request, operation, "retained") });
    return { schema_version: schema, ...result, primary_writeback_preserved: true, decision_read_from_shadow: false };
  } catch (error) { return failure(schema, error); }
}

async function pendingOutbox(root: string, goal: string,
  binding: Awaited<ReturnType<typeof requireShadowCaptureBinding>>,
  transactions: Awaited<ReturnType<typeof loadValidatedShadowLineage>>["transactions"]): Promise<boolean> {
  const outbox = join(root, "authority-shadow", "outbox", goal);
  const outboxStat = await lstat(outbox);
  if (!outboxStat.isDirectory() || outboxStat.isSymbolicLink()) throw new ShadowLineageError("outbox_file_invalid");
  const inventory = await readdir(outbox, { withFileTypes: true });
  if (inventory.some((item) => !["manifest.json", "todos", "leases"].includes(item.name) || item.isSymbolicLink() ||
      (item.name === "manifest.json" ? !item.isFile() : !item.isDirectory()))) throw new ShadowLineageError("outbox_unproved_residue");
  const manifestBytes = await optionalBytes(join(outbox, "manifest.json"));
  if (manifestBytes === null) throw new ShadowLineageError("outbox_manifest_unproved");
  let manifest: unknown;
  try { manifest = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(manifestBytes)); }
  catch { throw new ShadowLineageError("outbox_manifest_unproved"); }
  if (!canonicalAuthorityBytes(manifest).equals(canonicalAuthorityBytes({
    schema_version: schemas.SHADOW_OUTBOX_MANIFEST_SCHEMA, goal_id: goal, ...binding,
  }))) throw new ShadowLineageError("outbox_manifest_unproved");
  for (const partition of ["todos", "leases"]) {
    const directory = join(root, "authority-shadow", "outbox", goal, partition);
    let names: string[];
    try { names = await readdir(directory); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
    if (names.some((name) => name !== "drain-cursor.json")) return true;
    if (names.includes("drain-cursor.json")) {
      const stat = await lstat(join(directory, "drain-cursor.json"));
      if (!stat.isFile() || stat.isSymbolicLink()) throw new ShadowLineageError("outbox_file_invalid");
    }
    const cursor = await readOutboxCursor(directory, partition);
    const settled = transactions.slice(1).filter((transaction) => transaction.receipts[0]?.partition === partition);
    if (cursor === null) { if (settled.length) return true; continue; }
    const anchor = settled.at(-1);
    if (anchor === undefined || anchor.operation_id !== cursor.last_entry_id ||
      anchor.receipts[0]?.seq !== cursor.last_seq || anchor.cursor !== cursor.last_cursor ||
      anchor.provider_revision !== cursor.last_provider_revision) throw new ShadowLineageError("outbox_cursor_unproved");
    const marker = (anchor.projection.partitions as JsonObject)[partition];
    const digest = marker === null ? null : (marker as JsonObject).partition_digest;
    if (digest !== cursor.last_partition_digest) throw new ShadowLineageError("outbox_cursor_unproved");
  }
  return false;
}

export async function qualifyCoordinationRuntimeShadowUnderLocks(
  request: ShadowRequest,
  dependencies: RuntimeShadowDependencies,
  minimum: number,
  required: string[],
): Promise<JsonObject> {
  await verifyShadowSourceSnapshot(request);
  const result = await qualifyCoordinationShadowLineageUnderLocks(request, dependencies, minimum, required);
  await verifyShadowSourceSnapshot(request);
  return result;
}

/** Revalidate durable capture lineage under the maintenance lock. Callers must
 * either verify a fresh source snapshot or first verify the exact durable fence.
 * A fenced recovery cannot require a legacy document that is no longer authority. */
export async function qualifyCoordinationShadowLineageUnderLocks(
  request: Pick<ShadowRequest, "runtime_root" | "goal_id" | "projection">,
  dependencies: RuntimeShadowDependencies,
  minimum: number,
  required: string[],
): Promise<JsonObject> {
    const store = dependencies.createStore?.(join(request.runtime_root, "authority-shadow", "file-v0"), request.goal_id) ??
      new FileAuthorityStore(join(request.runtime_root, "authority-shadow", "file-v0"), request.goal_id, { existingOnly: true });
    const initial = await store.loadAuthority();
    if (initial.status === "loaded" && initial.head.capture_profile !== "file_outbox_v1") throw new ShadowLineageError("legacy_lineage_ineligible");
    const binding = await requireShadowCaptureBinding(request.runtime_root, request.goal_id);
    const lineage = await loadValidatedShadowLineage(store, request.runtime_root, request.goal_id, binding);
    const pending = await pendingOutbox(request.runtime_root, request.goal_id, binding, lineage.transactions);
    const matched = localAuthorityShadowHeadDigest(request.projection) === localAuthorityShadowHeadDigest(lineage.head.head);
    const missing = required.filter((kind) => !lineage.write_classes.includes(kind));
    const operations = lineage.transactions.slice(1).filter((transaction) => transaction.receipts[0]?.no_op === false).length;
    const qualified = matched && !pending && operations >= minimum && missing.length === 0;
    return {
      status: !matched ? "drifted" : pending ? "not_ready" : qualified ? "qualified" : "insufficient_evidence",
      qualified, parity_matches: matched, reason_code: pending ? "outbox_pending" : !matched ? "shadow_projection_drift" : null,
      scope: "bounded", sustained_parity_verified: false, sustained_parity_verdict: "not_evaluated", capture_profile: binding.capture_profile,
      capture_lineage_id: binding.capture_lineage_id, bootstrap_provider_revision: binding.bootstrap_provider_revision,
      provider_revision: lineage.head.provider_revision, cursor: lineage.head.cursor,
      expected_projection_sha256: localAuthorityShadowHeadDigest(request.projection),
      observed_projection_sha256: localAuthorityShadowHeadDigest(lineage.head.head),
      policy: { minimum_operations: minimum, required_event_kinds: required },
      evidence: { bootstrap_verified: true, transaction_lineage_verified: true, operation_count: operations,
        observed_event_kinds: lineage.write_classes, missing_required_event_kinds: missing,
        enough_operations: operations >= minimum, coverage_complete: missing.length === 0,
        todo_consumer_semantics_verified: true, last_sequences: lineage.last_sequences,
        last_applied_sequences: lineage.last_applied_sequences, pending_outbox: pending },
      primary_writeback_preserved: true, decision_read_from_shadow: false,
      head: lineage.head.head,
    };
}

async function qualifySnapshot(request: ShadowRequest, dependencies: RuntimeShadowDependencies, minimum: number, required: string[]): Promise<JsonObject> {
  return await withShadowMaintenanceLock(request.runtime_root, request.goal_id, () => withShadowSourceLocks(request, () =>
    qualifyCoordinationRuntimeShadowUnderLocks(request, dependencies, minimum, required)));
}
function policy(request: ShadowRequest): { minimum: number; required: string[] } {
  const minimum = request.minimum_operations ?? 3;
  const required = request.required_event_kinds ?? [];
  if (!Number.isSafeInteger(minimum) || Number(minimum) < 1 || Number(minimum) > 10000 ||
      !Array.isArray(required) || required.length > 32 || required.some((kind) => typeof kind !== "string" || !kind.trim()) ||
      new Set(required).size !== required.length) throw new Error("invalid bounded qualification policy");
  return { minimum: Number(minimum), required: required as string[] };
}
export async function qualifyCoordinationRuntimeShadow(value: unknown, dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  const schema = schemas.COORDINATION_RUNTIME_SHADOW_QUALIFY_RESULT_SCHEMA;
  try {
    const request = decodeRuntimeShadowRequest(value, schemas.COORDINATION_RUNTIME_SHADOW_QUALIFY_REQUEST_SCHEMA, ["minimum_operations", "required_event_kinds"]);
    const selected = policy(request);
    const result = await qualifySnapshot(request, dependencies, selected.minimum, selected.required);
    delete result.head;
    return { schema_version: schema, ...result };
  } catch (error) { return failure(schema, error); }
}
export async function inspectCoordinationRuntimeShadow(value: unknown, dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  const schema = schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_RESULT_SCHEMA;
  try {
    const request = decodeRuntimeShadowRequest(value, schemas.COORDINATION_RUNTIME_SHADOW_INSPECT_REQUEST_SCHEMA);
    const result = await qualifySnapshot(request, dependencies, 0, []);
    delete result.head;
    return { schema_version: schema, ...result, status: result.qualified ? "matched" : result.status,
      bootstrap_required: false };
  } catch (error) {
    const result = failure(schema, error);
    if (result.reason_code === "bootstrap_required") return { ...result, status: "missing", bootstrap_required: true };
    return result;
  }
}
export async function readCoordinationRuntimeShadowTodoCandidate(value: unknown, dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  const schema = schemas.COORDINATION_RUNTIME_SHADOW_TODO_READ_RESULT_SCHEMA;
  try {
    const request = decodeRuntimeShadowRequest(value, schemas.COORDINATION_RUNTIME_SHADOW_TODO_READ_REQUEST_SCHEMA, ["todo_id"]);
    const todoId = text(request.todo_id, "todo_id");
    const result = await qualifySnapshot(request, dependencies, 3, []);
    const head = result.head as JsonObject;
    delete result.head;
    if (!result.qualified) return { schema_version: schema, ...result, read_candidate_qualified: false };
    const index = indexCoordinationProjectionTodos(head, request.goal_id);
    const todo = index.todos.get(todoId);
    return { schema_version: schema, ...result, status: todo === undefined ? "todo_missing" : "matched",
      todo_id: todoId, todo: todo ?? null, todo_ids: index.todo_ids, read_candidate_qualified: todo !== undefined };
  } catch (error) { return failure(schema, error); }
}
/** Retired observation writes must never mix into a transaction-bound lineage. */
export async function commitCoordinationRuntimeShadow(_value: unknown, _dependencies: RuntimeShadowDependencies = {}): Promise<JsonObject> {
  return { schema_version: COORDINATION_RUNTIME_SHADOW_RESULT_SCHEMA, status: "failed",
    reason_code: "legacy_lineage_read_only", primary_writeback_preserved: true, decision_read_from_shadow: false };
}
