export {ShadowLineageError} from "./local_authority_shadow_identity.ts";
import {currentGraphTodoIds} from "./source_projection.ts";
import {verifyPendingEntryFiles, withMarkerlessSourceProof} from "./shadow_entry_evidence.ts";
import { createHash } from "node:crypto";
import { join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";
import type {
  AuthorityStore,
  AuthorityStoreCommitResult,
  AuthorityStoreCommittedTransaction,
  AuthorityStoreLoadResult,
  AuthorityStoreReceiptResult,
} from "./authority_store.ts";
import { authorityUnicodeCompare, canonicalAuthorityBytes, canonicalAuthoritySha256 } from "./authority_store_codec.ts";
import {
  coordinationTodoReadModel,
  validateCoordinationTodoReadModel,
} from "./coordination_projection.ts";
import { FileAuthorityStore } from "./file_authority_store.ts";
import {
  requireShadowCaptureBinding,
  withShadowMaintenanceLock,
} from "./shadow_management.ts";
import { outboxEntryIdentity, ShadowLineageError } from "./local_authority_shadow_identity.ts";
import {
  LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_TRANSACTION_RECEIPT_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export {
  LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA,
};

/** Compatibility tombstone for older clients: never open or mint a store. */
export async function recordLocalAuthorityShadow(_value: unknown): Promise<never> {
  throw new EffectRuntimeRequestError(
    "Post-commit observation is retired; configure runtime shadow and explicitly bootstrap its source lineage.",
    "local_authority_shadow_retired",
  );
}

export interface LocalAuthorityShadowDependencies {
  openStore?: (directory: string, goalId: string) => AuthorityStore;
}

// Transaction-bound entries are the only writable shadow lineage.
export const LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V1 =
  LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA;
export { LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA };
export { LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA };
export { LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA };
export const LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA_V1 = LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA;
export { LOCAL_AUTHORITY_SHADOW_TRANSACTION_RECEIPT_SCHEMA };

const SHADOW_PARTITIONS = ["todos", "leases"] as const;
const ENTRY_RESOLUTIONS = [
  "committed",
  "committed_proven_by_readback",
  "abandoned",
  "unproved",
  "seed",
] as const;
const NO_OP_RESOLUTIONS = new Set<string>(["abandoned", "unproved"]);
const SOURCE_KINDS = ["markdown_active_state", "state_event_log", "task_lease_record"] as const;
const WRITER_RUNTIMES = ["python", "typescript"] as const;
const COMMIT_ENTRY_REQUEST_FIELDS = new Set([
  "runtime_root",
  "goal_id",
  "entry",
  "partition_projection",
  "partition_digest",
]);
const ENTRY_FIELDS = new Set([
  "prepared_sha256", "committed_sha256",
  "capture_lineage_id",
  "entry_id",
  "partition",
  "seq",
  "writer",
  "source",
  "source_root_digest",
  "prepared_at",
  "committed_at",
  "resolution",
]);
const READ_REQUEST_FIELDS = new Set([
  "read_model",
  "receipt_operation_id",
  "schema_version",
  "runtime_root",
  "goal_id",
  "store_kind",
  "scan_after_cursor",
  "scan_limit",
]);
const ENTRY_ID_PATTERN = /^local-shadow-tx-[0-9a-f]{64}$/u;
const DIGEST_PATTERN = /^sha256:[a-f0-9]{64}$/u;
const MAX_SCAN_LIMIT = 10000;
const REVISION_RETRY_ATTEMPTS = 3;

export type ShadowPartition = (typeof SHADOW_PARTITIONS)[number];
export type ShadowEntryResolution = (typeof ENTRY_RESOLUTIONS)[number];
export type LocalAuthorityShadowCommitEntryOutcome =
  | "delivered"
  | "replayed"
  | "ambiguous_reconciled"
  | "ambiguous_unproved"
  | "unavailable"
  | "failed"
  | "protocol_mismatch"
  | "conflict_retry_required";

interface ShadowEntryWriter {
  runtime: (typeof WRITER_RUNTIMES)[number];
  write_class: string;
  operation_id: string | null;
}

type ShadowEntrySource = {
  previous_partition_digest: string;
  previous_bytes_digest: string | null;
  bytes_digest: string | null;
  lease: JsonObject | null;
  event_id: string | null;
} & (
  | {kind: "state_event_log"; event_log_path: string}
  | {kind: "markdown_active_state" | "task_lease_record"; event_log_path?: never}
);

interface ShadowEntry {
  prepared_sha256: string;
  committed_sha256: string | null;
  capture_lineage_id: string;
  entry_id: string;
  partition: ShadowPartition;
  seq: number;
  writer: ShadowEntryWriter;
  source: ShadowEntrySource;
  source_root_digest: string;
  prepared_at: string;
  committed_at: string | null;
  resolution: ShadowEntryResolution;
}

export interface CommitEntryRequest {
  runtime_root: string;
  goal_id: string;
  entry: ShadowEntry;
  partition_projection: JsonObject | null;
  partition_digest: string | null;
}

export interface LocalAuthorityShadowCommitEntryResult extends JsonObject {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA;
  outcome: LocalAuthorityShadowCommitEntryOutcome;
  reason_code: string | null;
  goal_id: string;
  entry_id: string;
  partition: ShadowPartition;
  seq: number;
  no_op: boolean;
  store_identity: string | null;
  provider_revision: string | null;
  cursor: string | null;
  head_digest: string | null;
}

interface ReadRequest {
  read_model: "full" | "proof";
  receipt_operation_id: string | null;
  runtime_root: string;
  goal_id: string;
  store_kind: "runtime_shadow" | "legacy_observation";
  scan_after_cursor: string | null;
  scan_limit: number;
}

function requireGoalId(value: unknown): string {
  const goalId = requireNonEmptyString(value, "goal_id");
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError(
      "Local authority shadow goal id must be a single path segment",
    );
  }
  return goalId;
}

function rejectUnexpectedFields(
  record: JsonObject,
  allowed: Set<string>,
  label: string,
): void {
  const unexpected = Object.keys(record).filter((field) => !allowed.has(field));
  if (unexpected.length > 0) {
    unexpected.sort(authorityUnicodeCompare);
    throw new EffectRuntimeRequestError(
      `${label} has unsupported fields: ${unexpected.join(", ")}`,
    );
  }
}

function optionalString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  return requireNonEmptyString(value, label);
}

function optionalDigest(value: unknown, label: string): string | null {
  const digest = optionalString(value, label);
  if (digest !== null && !DIGEST_PATTERN.test(digest)) {
    throw new EffectRuntimeRequestError(`${label} must be sha256:<64 lowercase hex>`);
  }
  return digest;
}

function decodeEntry(value: unknown): ShadowEntry {
  const raw = requireJsonObject(value, "entry");
  rejectUnexpectedFields(raw, ENTRY_FIELDS, "Local authority shadow entry");
  const entryId = requireNonEmptyString(raw.entry_id, "entry.entry_id");
  if (!ENTRY_ID_PATTERN.test(entryId)) {
    throw new EffectRuntimeRequestError("entry.entry_id must be local-shadow-tx-<64 lowercase hex>");
  }
  const seq = requireInteger(raw.seq, "entry.seq");
  if (seq < 1) {
    throw new EffectRuntimeRequestError("entry.seq must be a positive integer");
  }
  const writer = requireJsonObject(raw.writer, "entry.writer");
  const source = requireJsonObject(raw.source, "entry.source");
  rejectUnexpectedFields(writer, new Set(["runtime", "write_class", "operation_id"]), "entry.writer");
  rejectUnexpectedFields(source, new Set(["kind", "previous_bytes_digest", "previous_partition_digest", "bytes_digest", "lease", "event_id", "event_log_path"]), "entry.source");
  const kind = requireStringLiteral(source.kind, SOURCE_KINDS, "entry.source.kind");
  if (kind !== "state_event_log" && source.event_log_path !== undefined) {
    throw new EffectRuntimeRequestError("only an event source can carry event_log_path");
  }
  const sourceIdentity = kind === "state_event_log"
    ? {kind, event_log_path: requireNonEmptyString(source.event_log_path, "event_log_path")}
    : {kind};
  const lease = source.lease === null || source.lease === undefined
    ? null
    : requireJsonObject(source.lease, "entry.source.lease");
  return {
    prepared_sha256: optionalDigest(raw.prepared_sha256, "entry.prepared_sha256") ?? (() => { throw new Error("prepared_sha256 is required"); })(),
    committed_sha256: optionalDigest(raw.committed_sha256, "entry.committed_sha256"),
    capture_lineage_id: requireNonEmptyString(raw.capture_lineage_id, "entry.capture_lineage_id"),
    entry_id: entryId,
    partition: requireStringLiteral(raw.partition, SHADOW_PARTITIONS, "entry.partition"),
    seq,
    writer: {
      runtime: requireStringLiteral(writer.runtime, WRITER_RUNTIMES, "entry.writer.runtime"),
      write_class: requireNonEmptyString(writer.write_class, "entry.writer.write_class"),
      operation_id: optionalString(writer.operation_id, "entry.writer.operation_id"),
    },
    source: {
      previous_partition_digest: optionalDigest(source.previous_partition_digest, "entry.source.previous_partition_digest") ??
        (() => { throw new Error("previous_partition_digest is required"); })(),
      ...sourceIdentity,
      previous_bytes_digest: optionalDigest(
        source.previous_bytes_digest,
        "entry.source.previous_bytes_digest",
      ),
      bytes_digest: optionalDigest(source.bytes_digest, "entry.source.bytes_digest"),
      lease: lease === null ? null : structuredClone(lease),
      event_id: optionalString(source.event_id, "entry.source.event_id"),
    },
    source_root_digest: requireNonEmptyString(raw.source_root_digest, "entry.source_root_digest"),
    prepared_at: requireNonEmptyString(raw.prepared_at, "entry.prepared_at"),
    committed_at: optionalString(raw.committed_at, "entry.committed_at"),
    resolution: requireStringLiteral(raw.resolution, ENTRY_RESOLUTIONS, "entry.resolution"),
  };
}

function decodePartitionProjection(
  value: unknown,
  partition: ShadowPartition,
): JsonObject | null {
  if (value === null || value === undefined) return null;
  const projection = requireJsonObject(value, "partition_projection");
  if (partition === "todos") {
    if (
      Object.keys(projection).length !== 2 ||
      typeof projection.handoff_mode !== "string" ||
      !Array.isArray(projection.todos)
    ) {
      throw new EffectRuntimeRequestError(
        "todos partition projection must be exactly {handoff_mode, todos[]}",
      );
    }
  } else if (Object.keys(projection).length !== 1 || !Array.isArray(projection.leases)) {
    throw new EffectRuntimeRequestError("leases partition projection must be exactly {leases[]}");
  }
  return structuredClone(projection);
}

function decodeCommitEntryRequest(value: unknown): CommitEntryRequest {
  const request = requireJsonObject(value, "local authority shadow commit entry request");
  rejectUnexpectedFields(
    request,
    COMMIT_ENTRY_REQUEST_FIELDS,
    "Local authority shadow commit entry request",
  );
  const entry = decodeEntry(request.entry);
  const projection = decodePartitionProjection(request.partition_projection, entry.partition);
  const digest = optionalDigest(request.partition_digest, "partition_digest");
  const noOp = NO_OP_RESOLUTIONS.has(entry.resolution);
  if (noOp && (projection !== null || digest !== null)) {
    throw new EffectRuntimeRequestError(
      `entry resolution ${entry.resolution} must not carry a partition projection`,
    );
  }
  if (!noOp && (projection === null || digest === null)) {
    throw new EffectRuntimeRequestError(
      `entry resolution ${entry.resolution} requires partition_projection and partition_digest`,
    );
  }
  return {
    runtime_root: requireNonEmptyString(request.runtime_root, "runtime_root"),
    goal_id: requireGoalId(request.goal_id),
    entry,
    partition_projection: projection,
    partition_digest: digest,
  };
}

function decodeReadRequest(value: unknown): ReadRequest {
  const request = requireJsonObject(value, "local authority shadow read request");
  rejectUnexpectedFields(request, READ_REQUEST_FIELDS, "Local authority shadow read request");
  if (request.schema_version !== LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Local authority shadow read request schema mismatch");
  }
  const limit = request.scan_limit === undefined ? 0 : requireInteger(request.scan_limit, "scan_limit");
  if (limit < 0 || limit > MAX_SCAN_LIMIT) {
    throw new EffectRuntimeRequestError(`scan_limit must be between 0 and ${MAX_SCAN_LIMIT}`);
  }
  return {
    read_model: request.read_model === undefined || request.read_model === "full" ? "full"
      : request.read_model === "proof" ? "proof"
      : (() => {throw new EffectRuntimeRequestError("read_model must be full or proof");})(),
    receipt_operation_id: optionalString(request.receipt_operation_id, "receipt_operation_id"),
    runtime_root: requireNonEmptyString(request.runtime_root, "runtime_root"),
    goal_id: requireGoalId(request.goal_id),
    store_kind: request.store_kind === undefined || request.store_kind === "runtime_shadow"
      ? "runtime_shadow"
      : request.store_kind === "legacy_observation"
        ? "legacy_observation"
        : (() => {
            throw new EffectRuntimeRequestError(
              "Local authority shadow read store_kind must be runtime_shadow or legacy_observation",
            );
          })(),
    scan_after_cursor: optionalString(request.scan_after_cursor, "scan_after_cursor"),
    scan_limit: limit,
  };
}

/**
 * Remove query-clock observations from one Todo before authority comparison.
 *
 * `resume_condition.evaluated_at` records when a reader evaluated an otherwise
 * durable resume condition.  Re-reading unchanged source therefore changes
 * that timestamp without changing the Todo decision.  The evaluated outcome
 * and every other resume fact remain in the authority identity, so an actual
 * readiness transition still produces drift until the writer captures it.
 */
function todoAuthorityIdentityView(value: unknown): unknown {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return value;
  const todo = structuredClone(value as JsonObject);
  const condition = todo.resume_condition;
  if (condition !== null && typeof condition === "object" && !Array.isArray(condition)) {
    const stableCondition = { ...(condition as JsonObject) };
    delete stableCondition.evaluated_at;
    todo.resume_condition = stableCondition;
  }
  return todo;
}

function authorityIdentityTodos(value: unknown): unknown {
  return Array.isArray(value) ? value.map(todoAuthorityIdentityView) : value;
}

function partitionAuthorityIdentityView(partition: ShadowPartition, projection: JsonObject): JsonObject {
  if (partition !== "todos") return projection;
  return { ...projection, todos: authorityIdentityTodos(projection.todos) };
}

/**
 * Stable identity for one source partition.
 *
 * Prepared outbox bytes and the supplied projection are still compared in
 * full. This semantic digest excludes only the query-clock observation that
 * cannot prove a source mutation, so writer continuity and final parity use
 * the same identity boundary.
 */
export function localAuthorityShadowPartitionDigest(
  partition: ShadowPartition,
  projection: JsonObject,
): string {
  return `sha256:${createHash("sha256").update(
    canonicalAuthorityBytes(partitionAuthorityIdentityView(partition, projection)),
  ).digest("hex")}`;
}

/** Digest of the fields parity compares; must match Python `head_digest`. */
export function localAuthorityShadowHeadDigest(head: JsonObject): string {
  const view = {
    handoff_mode: head.handoff_mode ?? null,
    todos: authorityIdentityTodos(head.todos ?? null),
    leases: head.leases ?? null,
  };
  return `sha256:${createHash("sha256").update(canonicalAuthorityBytes(view)).digest("hex")}`;
}

function partitionsOf(head: JsonObject | null): JsonObject {
  const raw = head?.partitions;
  const partitions: JsonObject = { todos: null, leases: null };
  if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
    for (const partition of SHADOW_PARTITIONS) {
      const marker = (raw as JsonObject)[partition];
      if (marker !== null && typeof marker === "object" && !Array.isArray(marker)) {
        partitions[partition] = structuredClone(marker);
      }
    }
  }
  return partitions;
}

/**
 * Fold one partition into the candidate head. A v0 head (whole-snapshot
 * observation) is accepted as the starting point with no partition markers.
 * Markers describe the last actual mutation, not the last settled entry. Both
 * drain and qualification read this verified marker for the cursor digest;
 * bootstrap and no-op prefixes retain null, even with a nonempty baseline.
 */
export function composeLocalAuthorityShadowHead(
  current: JsonObject | null,
  goalId: string,
  entry: { partition: ShadowPartition; seq: number },
  projection: JsonObject | null,
  digest: string | null,
): JsonObject {
  const base = current ?? {};
  let handoffMode: string | null = typeof base.handoff_mode === "string" ? base.handoff_mode : null;
  let todos = Array.isArray(base.todos) ? structuredClone(base.todos) : [];
  let leases = Array.isArray(base.leases) ? structuredClone(base.leases) : [];
  const partitions = partitionsOf(current);
  if (projection !== null) {
    if (entry.partition === "todos") {
      handoffMode = String(projection.handoff_mode);
      todos = structuredClone(projection.todos as JsonObject[]);
      // The Todo partition carries the published Todo read records, including
      // archived rows retained for audit. The candidate head, like the source
      // projection, keeps live lease edges only for Todos that are still in the
      // current graph (`archive_state === "active"`); a retained archived row
      // must not re-admit the lease its archive just orphaned.
      const graphTodoIds = currentGraphTodoIds(todos);
      leases = leases.filter((lease) => graphTodoIds.has(String(lease.todo_id)));
    } else {
      leases = structuredClone(projection.leases as JsonObject[]);
    }
    partitions[entry.partition] = { seq: entry.seq, partition_digest: digest };
  }
  const next = {
    schema_version: LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V1,
    goal_id: goalId,
    source_authority: "legacy_markdown_and_task_lease",
    handoff_mode: handoffMode,
    todos,
    leases,
    todo_read_model: coordinationTodoReadModel(
      todos,
      "loopx_todo_canonical_read_record_v0",
    ),
    partitions,
    ...(base.capture_profile === undefined ? {} : {
      capture_profile: base.capture_profile,
      capture_lineage_id: base.capture_lineage_id,
      source_root_digest: base.source_root_digest,
    }),
  };
  return next;
}

function transactionReceipt(request: CommitEntryRequest, noOp: boolean): JsonObject {
  const { entry } = request;
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_TRANSACTION_RECEIPT_SCHEMA,
    prepared_sha256: entry.prepared_sha256,
    committed_sha256: entry.committed_sha256,
    capture_lineage_id: entry.capture_lineage_id,
    entry_id: entry.entry_id,
    partition: entry.partition,
    seq: entry.seq,
    write_class: entry.writer.write_class,
    writer_runtime: entry.writer.runtime,
    writer_operation_id: entry.writer.operation_id,
    source_kind: entry.source.kind,
    source_bytes_digest: entry.source.bytes_digest,
    source_previous_bytes_digest: entry.source.previous_bytes_digest,
    source_previous_partition_digest: entry.source.previous_partition_digest,
    source_event_id: entry.source.event_id,
    ...(entry.source.event_log_path === undefined ? {} : {source_event_log_path: entry.source.event_log_path}),
    source_lease: entry.source.lease,
    source_root_digest: entry.source_root_digest,
    partition_digest: request.partition_digest,
    resolution: entry.resolution,
    no_op: noOp,
    prepared_at: entry.prepared_at,
    committed_at: entry.committed_at,
    drained_at: new Date().toISOString(),
    source_transaction_correlated: true,
    durable_source_outbox: true,
    parity_verdict: "not_evaluated",
    primary_authority: "legacy_local",
    candidate_read_for_decision: false,
    provider_to_local_writes: false,
  };
}

function transactionEvent(request: CommitEntryRequest, noOp: boolean): JsonObject {
  const { entry } = request;
  let kind = "source_transaction_delivered";
  if (entry.resolution === "seed") kind = "partition_seeded";
  else if (entry.resolution === "abandoned") kind = "source_transaction_abandoned";
  else if (entry.resolution === "unproved") kind = "source_transaction_unproved";
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_EVENT_SCHEMA_V1,
    prepared_sha256: entry.prepared_sha256,
    committed_sha256: entry.committed_sha256,
    capture_lineage_id: entry.capture_lineage_id,
    kind,
    partition: entry.partition,
    seq: entry.seq,
    entry_id: entry.entry_id,
    write_class: entry.writer.write_class,
    partition_digest: request.partition_digest,
    previous_partition_digest: entry.source.previous_partition_digest,
    no_op: noOp,
  };
}

function commitEntryResult(
  request: CommitEntryRequest,
  outcome: LocalAuthorityShadowCommitEntryOutcome,
  options: {
    reasonCode?: string | null;
    storeIdentity?: string | null;
    providerRevision?: string | null;
    cursor?: string | null;
    headDigest?: string | null;
  } = {},
): LocalAuthorityShadowCommitEntryResult {
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
    outcome,
    reason_code: options.reasonCode ?? null,
    goal_id: request.goal_id,
    entry_id: request.entry.entry_id,
    capture_lineage_id: request.entry.capture_lineage_id,
    partition: request.entry.partition,
    seq: request.entry.seq,
    no_op: NO_OP_RESOLUTIONS.has(request.entry.resolution),
    store_identity: options.storeIdentity ?? null,
    provider_revision: options.providerRevision ?? null,
    cursor: options.cursor ?? null,
    head_digest: options.headDigest ?? null,
  };
}

function transactionReceiptMatches(
  request: CommitEntryRequest,
  result: Extract<AuthorityStoreReceiptResult, { status: "found" }>,
): boolean {
  if (result.receipts.length !== 1) return false;
  const actual = { ...result.receipts[0] };
  const expected = transactionReceipt(request, NO_OP_RESOLUTIONS.has(request.entry.resolution));
  if (typeof actual.drained_at !== "string") return false;
  delete actual.drained_at;
  delete expected.drained_at;
  return canonicalAuthorityBytes(actual).equals(canonicalAuthorityBytes(expected));
}

async function reconcileTransactionReceipt(
  store: AuthorityStore,
  request: CommitEntryRequest,
  storeIdentity: string,
  reconciledOutcome: "replayed" | "ambiguous_reconciled",
): Promise<LocalAuthorityShadowCommitEntryResult> {
  const result = await store.readReceipt(request.entry.entry_id);
  if (result.status === "found" && transactionReceiptMatches(request, result)) {
    return commitEntryResult(request, reconciledOutcome, {
      storeIdentity,
      providerRevision: result.provider_revision,
      cursor: result.cursor,
    });
  }
  if (result.status === "unavailable" || result.status === "failed") {
    return commitEntryResult(request, result.status, {
      reasonCode: result.reason_code,
      storeIdentity,
    });
  }
  return commitEntryResult(
    request,
    reconciledOutcome === "ambiguous_reconciled" ? "ambiguous_unproved" : "protocol_mismatch",
    {
      reasonCode: result.status === "missing"
        ? "transaction_receipt_missing"
        : "transaction_receipt_mismatch",
      storeIdentity,
    },
  );
}

function openShadowStore(
  runtimeRoot: string,
  goalId: string,
  storeKind: ReadRequest["store_kind"],
  dependencies: LocalAuthorityShadowDependencies,
): AuthorityStore {
  const providerDirectory = storeKind === "legacy_observation"
    ? join(runtimeRoot, "authority-shadow", "file", goalId)
    : join(runtimeRoot, "authority-shadow", "file-v0");
  return (dependencies.openStore ?? ((directory, id) => new FileAuthorityStore(directory, id, { existingOnly: true })))(
    providerDirectory,
    goalId,
  );
}

type CommitAttempt =
  | { kind: "final"; result: LocalAuthorityShadowCommitEntryResult }
  | { kind: "retry"; result: LocalAuthorityShadowCommitEntryResult };

export interface ShadowLineageBinding {
  capture_profile: string;
  capture_lineage_id: string;
  source_root_digest: string;
  store_identity: string;
  bootstrap_operation_id: string;
  bootstrap_provider_revision: string;
}

function requireLineage(condition: unknown, reason: string): asserts condition {
  if (!condition) throw new ShadowLineageError(reason);
}

function sourceReference(entry: ShadowEntry, digest: string | null): string {
  if (entry.source.bytes_digest !== null) return entry.source.bytes_digest;
  if (entry.source.event_id !== null) return `event:${entry.source.event_id}`;
  if (entry.resolution === "seed" && digest !== null) return `seed:${digest}`;
  throw new ShadowLineageError("entry_source_identity_missing");
}

function validateEntryIdentity(request: CommitEntryRequest, binding: ShadowLineageBinding): void {
  const { entry } = request;
  requireLineage(entry.capture_lineage_id === binding.capture_lineage_id, "stale_generation");
  // requireShadowCaptureBinding has already proved that this binding belongs
  // to the requested physical runtime root. Keep accepting the binding's
  // immutable digest so an in-flight lineage created by a pre-canonical-path
  // release can drain safely after upgrade.
  requireLineage(entry.source_root_digest === binding.source_root_digest, "source_root_mismatch");
  requireLineage(entry.entry_id === outboxEntryIdentity(request.goal_id, entry.partition, entry.seq,
    sourceReference(entry, request.partition_digest), entry.capture_lineage_id, entry.source_root_digest),
  "entry_identity_mismatch");
  if (request.partition_projection !== null) {
    requireLineage(request.partition_digest === localAuthorityShadowPartitionDigest(
      entry.partition,
      request.partition_projection,
    ),
      "partition_digest_mismatch");
  }
  requireLineage(entry.partition === "todos" ? ["markdown_active_state", "state_event_log"].includes(entry.source.kind) : entry.source.kind === "task_lease_record",
    "entry_source_partition_mismatch");
  requireLineage(entry.resolution !== "unproved" && entry.resolution !== "seed", "source_transaction_unproved");
}

function partitionProjection(head: JsonObject, partition: ShadowPartition): JsonObject {
  return partition === "todos" ? { handoff_mode: head.handoff_mode, todos: head.todos } : { leases: head.leases };
}

function validateSourceContinuity(request: CommitEntryRequest, previous: JsonObject): void {
  const digest = localAuthorityShadowPartitionDigest(
    request.entry.partition,
    partitionProjection(previous, request.entry.partition),
  );
  requireLineage(request.entry.source.previous_partition_digest === digest, "source_partition_continuity_unproved");
  if (!NO_OP_RESOLUTIONS.has(request.entry.resolution)) {
    requireLineage(request.partition_digest !== digest, "partition_unchanged");
  }
}

export interface ValidatedShadowLineage {
  head: Extract<AuthorityStoreLoadResult, { status: "loaded" }>;
  transactions: AuthorityStoreCommittedTransaction[];
  last_sequences: Record<ShadowPartition, number>;
  last_applied_sequences: Record<ShadowPartition, number>;
  write_classes: string[];
}

/** The caller holds its primary partition lock. This is existing-only and
 * never takes M or writes a cursor: management cannot complete a transition
 * while that primary lock is held, and a changed binding still fails closed.
 */
export async function readProvenShadowSequence(
  runtimeRoot: string, goalId: string, partition: ShadowPartition, expectedLineageId: string,
): Promise<number> {
  const binding = await requireShadowCaptureBinding(runtimeRoot, goalId);
  requireLineage(binding.capture_lineage_id === expectedLineageId, "stale_generation");
  const store = new FileAuthorityStore(join(runtimeRoot, "authority-shadow", "file-v0"), goalId, { existingOnly: true });
  const lineage = await loadValidatedShadowLineage(store, runtimeRoot, goalId, binding);
  const current = await requireShadowCaptureBinding(runtimeRoot, goalId);
  requireLineage(canonicalAuthorityBytes(binding).equals(canonicalAuthorityBytes(current)), "stale_generation");
  return lineage.last_sequences[partition];
}

/** Validate the exact bootstrap, every transaction, and the final readback.
 * The caller owns maintenance exclusion; this function never takes M.
 */
export async function loadValidatedShadowLineage(
  store: AuthorityStore,
  runtimeRoot: string,
  goalId: string,
  binding: ShadowLineageBinding,
): Promise<ValidatedShadowLineage> {
  const identity = await store.storeIdentity();
  requireLineage(identity.status === "available" && identity.store_identity === binding.store_identity,
    "shadow_store_identity_mismatch");
  const head = await store.loadAuthority();
  requireLineage(head.status === "loaded", "bootstrap_required");
  const transactions: AuthorityStoreCommittedTransaction[] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, 256);
    requireLineage(page.status === "page", "shadow_history_unavailable");
    transactions.push(...page.transactions);
    requireLineage(transactions.length <= 10000, "shadow_qualification_history_too_large");
    if (!page.has_more) break;
    requireLineage(page.next_cursor !== null && page.next_cursor !== after && page.transactions.length > 0,
      "shadow_qualification_cursor_stalled");
    after = page.next_cursor;
  }
  const first = transactions[0];
  requireLineage(first !== undefined && first.cursor === "1" && first.operation_id === binding.bootstrap_operation_id &&
    first.provider_revision === binding.bootstrap_provider_revision && first.receipts.length === 0 && first.events.length === 1,
  "shadow_qualification_bootstrap_identity_invalid");
  const baseline = first.projection;
  requireLineage(binding.capture_profile === "file_outbox_v1" && baseline.capture_profile === binding.capture_profile &&
    baseline.capture_lineage_id === binding.capture_lineage_id && baseline.source_root_digest === binding.source_root_digest &&
    baseline.goal_id === goalId && baseline.schema_version === LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V1 &&
    typeof baseline.handoff_mode === "string" && Array.isArray(baseline.leases), "legacy_lineage_ineligible");
  validateCoordinationTodoReadModel(baseline, goalId);
  requireLineage(canonicalAuthorityBytes(baseline.partitions).equals(canonicalAuthorityBytes({ todos: null, leases: null })),
    "shadow_bootstrap_partitions_invalid");
  const bootstrapEvent = first.events[0]!;
  requireLineage(canonicalAuthorityBytes(bootstrapEvent).equals(canonicalAuthorityBytes({
    schema_version: "loopx_coordination_runtime_shadow_bootstrap_event_v0",
    operation_id: binding.bootstrap_operation_id,
    source_version: bootstrapEvent.source_version,
    source_projection_sha256: canonicalAuthoritySha256(baseline),
    mode_declaration: "legacy_canonical_shadow",
  })) && typeof bootstrapEvent.source_version === "string", "shadow_qualification_bootstrap_shape_invalid");
  let previous = baseline;
  const settled: Record<ShadowPartition, number> = { todos: 0, leases: 0 };
  const applied: Record<ShadowPartition, number> = { todos: 0, leases: 0 };
  const writeClasses = new Set<string>();
  const operationIds = new Set<string>([first.operation_id]);
  for (const [index, transaction] of transactions.slice(1).entries()) {
    requireLineage(transaction.cursor === String(index + 2) && transaction.receipts.length === 1 && transaction.events.length === 1 &&
      !operationIds.has(transaction.operation_id), "shadow_qualification_transaction_shape_invalid");
    operationIds.add(transaction.operation_id);
    const receipt = transaction.receipts[0]!;
    const partition = requireStringLiteral(receipt.partition, SHADOW_PARTITIONS, "receipt.partition");
    const noOp = receipt.no_op === true;
    const projection: JsonObject | null = noOp ? null : partition === "todos"
      ? { handoff_mode: transaction.projection.handoff_mode, todos: transaction.projection.todos }
      : { leases: transaction.projection.leases };
    const request: CommitEntryRequest = {
      runtime_root: runtimeRoot, goal_id: goalId,
      entry: decodeEntry({
        capture_lineage_id: receipt.capture_lineage_id,
        prepared_sha256: receipt.prepared_sha256, committed_sha256: receipt.committed_sha256,
        entry_id: receipt.entry_id, partition, seq: receipt.seq,
        writer: { runtime: receipt.writer_runtime, write_class: receipt.write_class, operation_id: receipt.writer_operation_id },
        source: { kind: receipt.source_kind, bytes_digest: receipt.source_bytes_digest,
          previous_partition_digest: receipt.source_previous_partition_digest,
          previous_bytes_digest: receipt.source_previous_bytes_digest, event_id: receipt.source_event_id, lease: receipt.source_lease,
          ...(receipt.source_event_log_path === undefined ? {} : {event_log_path: receipt.source_event_log_path}) },
        source_root_digest: receipt.source_root_digest, prepared_at: receipt.prepared_at,
        committed_at: receipt.committed_at, resolution: receipt.resolution,
      }),
      partition_projection: projection,
      partition_digest: optionalDigest(receipt.partition_digest, "receipt.partition_digest"),
    };
    validateEntryIdentity(request, binding);
    validateSourceContinuity(request, previous);
    requireLineage(transaction.operation_id === request.entry.entry_id && request.entry.seq === settled[partition] + 1 &&
      noOp === NO_OP_RESOLUTIONS.has(request.entry.resolution) &&
      transactionReceiptMatches(request, { status: "found", receipts: transaction.receipts,
        cursor: transaction.cursor, provider_revision: transaction.provider_revision }), "shadow_qualification_transaction_identity_invalid");
    requireLineage(canonicalAuthorityBytes(transaction.events).equals(canonicalAuthorityBytes([transactionEvent(request, noOp)])),
      "shadow_qualification_event_identity_invalid");
    const expected = composeLocalAuthorityShadowHead(previous, goalId, request.entry, projection, request.partition_digest);
    requireLineage(canonicalAuthorityBytes(expected).equals(canonicalAuthorityBytes(transaction.projection)),
      "shadow_qualification_projection_history_invalid");
    validateCoordinationTodoReadModel(transaction.projection, goalId);
    settled[partition] = request.entry.seq;
    if (!noOp) { applied[partition] = request.entry.seq; writeClasses.add(request.entry.writer.write_class); }
    previous = transaction.projection;
  }
  const last = transactions.at(-1)!;
  const reread = await store.loadAuthority();
  requireLineage(reread.status === "loaded" && reread.provider_revision === head.provider_revision &&
    last.provider_revision === head.provider_revision && last.cursor === head.cursor &&
    canonicalAuthorityBytes(previous).equals(canonicalAuthorityBytes(head.head)) &&
    canonicalAuthorityBytes(reread.head).equals(canonicalAuthorityBytes(head.head)), "shadow_snapshot_changed_retry");
  return { head, transactions, last_sequences: settled, last_applied_sequences: applied,
    write_classes: [...writeClasses].sort(authorityUnicodeCompare) };
}

async function settleCommitOutcome(
  store: AuthorityStore,
  request: CommitEntryRequest,
  storeIdentity: string,
  committed: AuthorityStoreCommitResult,
  headDigest: string,
): Promise<CommitAttempt> {
  if (committed.status === "applied") {
    return {
      kind: "final",
      result: commitEntryResult(request, "delivered", {
        storeIdentity,
        providerRevision: committed.provider_revision,
        cursor: committed.cursor,
        headDigest,
      }),
    };
  }
  if (committed.status === "ambiguous") {
    return {
      kind: "final",
      result: await reconcileTransactionReceipt(store, request, storeIdentity, "ambiguous_reconciled"),
    };
  }
  if (committed.status === "failed") {
    return {
      kind: "final",
      result: commitEntryResult(request, "failed", {
        reasonCode: committed.reason_code,
        storeIdentity,
      }),
    };
  }
  if (committed.conflict_kind === "operation_id_exists") {
    return {
      kind: "final",
      result: await reconcileTransactionReceipt(store, request, storeIdentity, "replayed"),
    };
  }
  return {
    kind: "retry",
    result: commitEntryResult(request, "conflict_retry_required", {
      reasonCode: "provider_revision_mismatch",
      storeIdentity,
      providerRevision: committed.current_provider_revision,
      cursor: committed.current_cursor,
    }),
  };
}

/** One load-compose-commit attempt against the current provider revision. */
async function attemptCommitEntry(
  store: AuthorityStore,
  request: CommitEntryRequest,
  storeIdentity: string,
  noOp: boolean,
): Promise<CommitAttempt> {
  const loaded = await store.loadAuthority();
  if (loaded.status === "unavailable" || loaded.status === "failed") {
    return {
      kind: "final",
      result: commitEntryResult(request, loaded.status, {
        reasonCode: loaded.reason_code,
        storeIdentity,
      }),
    };
  }
  if (loaded.status === "missing") {
    return { kind: "final", result: commitEntryResult(request, "failed", { reasonCode: "bootstrap_required" }) };
  }
  const nextHead = composeLocalAuthorityShadowHead(
    loaded.status === "loaded" ? loaded.head : null,
    request.goal_id,
    request.entry,
    request.partition_projection,
    request.partition_digest,
  );
  validateCoordinationTodoReadModel(nextHead, request.goal_id);
  const committed = await store.commitAuthority({
    expected_provider_revision: loaded.status === "loaded" ? loaded.provider_revision : null,
    operation_id: request.entry.entry_id,
    events: [transactionEvent(request, noOp)],
    next_projection: nextHead,
    receipts: [transactionReceipt(request, noOp)],
  });
  return await settleCommitOutcome(
    store,
    request,
    storeIdentity,
    committed,
    localAuthorityShadowHeadDigest(nextHead),
  );
}

/**
 * Commit one drained outbox entry as exactly one candidate transaction.
 *
 * `operation_id` is the entry id, so a retry after a lost response replays
 * onto the same transaction instead of recording the source write twice.
 * Proven abandoned entries settle their sequence without changing the compared
 * head. Unproved entries remain pending and require explicit recovery.
 */
export async function commitLocalAuthorityShadowEntry(
  value: unknown,
  dependencies: LocalAuthorityShadowDependencies = {},
): Promise<LocalAuthorityShadowCommitEntryResult> {
  return await commitShadowEntryTransaction(value, dependencies, "assert_recorded");
}

/** Internal transaction runner. The delivery owner supplies only a request read
 * from witnessed outbox bytes; existing resolved callers retain assertion-only
 * semantics. No resolution policy is accepted from the RPC caller. */
export async function commitShadowEntryTransaction(
  value: unknown,
  dependencies: LocalAuthorityShadowDependencies,
  resolutionPolicy: "assert_recorded" | "derive_from_source",
): Promise<LocalAuthorityShadowCommitEntryResult> {
  const request = decodeCommitEntryRequest(value);
  const plannedProjection = request.partition_projection, plannedDigest = request.partition_digest;
  try {
    return await withShadowMaintenanceLock(request.runtime_root, request.goal_id, async () => {
      const binding = await requireShadowCaptureBinding(request.runtime_root, request.goal_id);
      validateEntryIdentity(request, binding);
      const store = openShadowStore(request.runtime_root, request.goal_id, "runtime_shadow", dependencies);
      for (let index = 0; index < REVISION_RETRY_ATTEMPTS; index += 1) {
        const active = await requireShadowCaptureBinding(request.runtime_root, request.goal_id);
        requireLineage(active.capture_lineage_id === binding.capture_lineage_id, "stale_generation");
        const lineage = await loadValidatedShadowLineage(store, request.runtime_root, request.goal_id, active);
        const existing = await store.readReceipt(request.entry.entry_id);
        if (existing.status === "found") {
          if (resolutionPolicy === "derive_from_source") {
            request.partition_projection = plannedProjection; request.partition_digest = plannedDigest;
            const receipt = existing.receipts[0];
            requireLineage(existing.receipts.length === 1 &&
              receipt.prepared_sha256 === request.entry.prepared_sha256 &&
              receipt.committed_sha256 === request.entry.committed_sha256,
              "outbox_receipt_mismatch");
            request.entry.resolution = requireStringLiteral(receipt.resolution, ENTRY_RESOLUTIONS, "receipt.resolution");
            if (NO_OP_RESOLUTIONS.has(request.entry.resolution)) {
              request.partition_projection = null; request.partition_digest = null;
            }
          }
          const replay = await reconcileTransactionReceipt(store, request, binding.store_identity, "replayed");
          return resolutionPolicy === "derive_from_source"
            ? {...replay, resolution: request.entry.resolution, partition_digest: request.partition_digest} : replay;
        }
        requireLineage(existing.status === "missing", "shadow_receipt_unavailable");
        requireLineage(lineage.transactions.length < 10000, "shadow_qualification_history_too_large");
        if (resolutionPolicy === "derive_from_source") {
          request.partition_projection = plannedProjection; request.partition_digest = plannedDigest;
        }
        const attempt = await withMarkerlessSourceProof(request, active, async () => {
          await verifyPendingEntryFiles(request);
          requireLineage(request.entry.seq === lineage.last_sequences[request.entry.partition] + 1,
            "partition_sequence_mismatch");
          validateSourceContinuity(request, lineage.head.head);
          return await attemptCommitEntry(store, request, binding.store_identity, NO_OP_RESOLUTIONS.has(request.entry.resolution));
        }, resolutionPolicy);
        if (attempt.kind === "final") {
          return resolutionPolicy === "derive_from_source"
            ? {...attempt.result, resolution: request.entry.resolution, partition_digest: request.partition_digest}
            : attempt.result;
        }
      }
      return commitEntryResult(request, "conflict_retry_required", { reasonCode: "provider_revision_mismatch" });
    });
  } catch (error) {
    const raw = error as { reason_code?: string; code?: string };
    return commitEntryResult(request, "failed", {
      reasonCode: raw.reason_code ?? raw.code ?? "provider_call_failed",
    });
  }
}

function readResultBase(goalId: string): JsonObject {
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_READ_RESULT_SCHEMA,
    goal_id: goalId,
    status: "unavailable",
    reason_code: null,
    store_identity: null,
    provider_revision: null,
    cursor: null,
    head: null,
    head_digest: null,
    partitions: null,
    scan: null,
  };
}

function loadedReadResult(
  base: JsonObject,
  storeIdentity: string,
  loaded: Extract<AuthorityStoreLoadResult, { status: "loaded" | "missing" }>,
  includeHead: boolean,
): JsonObject {
  const result: JsonObject = { ...base, status: loaded.status, store_identity: storeIdentity };
  if (loaded.status === "loaded") {
    result.provider_revision = loaded.provider_revision;
    result.cursor = loaded.cursor;
    result.head = includeHead ? structuredClone(loaded.head) : null;
    result.head_digest = localAuthorityShadowHeadDigest(loaded.head);
    result.partitions = partitionsOf(loaded.head);
  }
  return result;
}

/** One committed transaction with its projection reduced to a digest. */
function scanTransactionView(transaction: AuthorityStoreCommittedTransaction): JsonObject {
  return {
    cursor: transaction.cursor,
    provider_revision: transaction.provider_revision,
    operation_id: transaction.operation_id,
    projection_digest: localAuthorityShadowHeadDigest(transaction.projection),
    projection_partitions: partitionsOf(transaction.projection),
    events: structuredClone(transaction.events) as JsonObject[],
    receipts: structuredClone(transaction.receipts) as JsonObject[],
  };
}

async function appendScanPage(
  store: AuthorityStore,
  request: ReadRequest,
  result: JsonObject,
): Promise<JsonObject> {
  const page = await store.scanCommitted(request.scan_after_cursor, request.scan_limit);
  if (page.status !== "page") {
    return { ...result, status: page.status, reason_code: page.reason_code };
  }
  return {
    ...result,
    scan: {
      transactions: page.transactions.map(scanTransactionView),
      next_cursor: page.next_cursor,
      has_more: page.has_more,
    },
  };
}

/**
 * Read-only view of the candidate store for drain readback and parity:
 * head, its comparison digest, and a page of committed transactions with the
 * projection reduced to its digest so responses stay bounded.
 */
export async function readLocalAuthorityShadow(
  value: unknown,
  dependencies: LocalAuthorityShadowDependencies = {},
): Promise<JsonObject> {
  const request = decodeReadRequest(value);
  const base = readResultBase(request.goal_id);
  let store: AuthorityStore;
  try {
    store = openShadowStore(
      request.runtime_root,
      request.goal_id,
      request.store_kind,
      dependencies,
    );
  } catch {
    return { ...base, reason_code: "provider_construction_failed" };
  }
  try {
    const identity = await store.storeIdentity();
    if (identity.status !== "available") {
      return { ...base, status: identity.status, reason_code: identity.reason_code };
    }
    const loaded = await store.loadAuthority();
    if (loaded.status === "unavailable" || loaded.status === "failed") {
      return {
        ...base,
        status: loaded.status,
        reason_code: loaded.reason_code,
        store_identity: identity.store_identity,
      };
    }
    const result = loadedReadResult(base, identity.store_identity, loaded, request.read_model === "full");
    if (request.store_kind === "runtime_shadow" && loaded.status === "loaded" && loaded.head.capture_profile !== "file_outbox_v1") {
      result.eligible = false;
      result.reason_code = "legacy_lineage_ineligible";
    } else if (request.store_kind === "runtime_shadow" && loaded.status === "loaded") {
      const binding = await requireShadowCaptureBinding(request.runtime_root, request.goal_id);
      const lineage = await loadValidatedShadowLineage(store, request.runtime_root, request.goal_id, binding);
      const receipt = request.receipt_operation_id === null ? null :
        lineage.transactions.find((transaction) => transaction.operation_id === request.receipt_operation_id) ?? null;
      result.proof = {
        capture_lineage_id: binding.capture_lineage_id,
        bootstrap_provider_revision: binding.bootstrap_provider_revision,
        last_sequences: lineage.last_sequences,
        last_applied_sequences: lineage.last_applied_sequences,
        transactions: lineage.transactions.filter((transaction) =>
          request.scan_after_cursor === null || Number(transaction.cursor) > Number(request.scan_after_cursor)
        ).slice(0, request.scan_limit).map(transaction => request.read_model === "proof"
          ? scanTransactionView(transaction) : structuredClone(transaction) as unknown as JsonObject),
        receipt: receipt === null ? null : request.read_model === "proof"
          ? scanTransactionView(receipt) : structuredClone(receipt) as unknown as JsonObject,
      };
    }
    return request.read_model === "full" && request.scan_limit > 0
      ? await appendScanPage(store, request, result) : result;
  } catch (error) {
    const raw = error as { reason_code?: string; code?: string };
    return { ...base, status: "failed", reason_code: raw.reason_code ?? raw.code ?? "provider_call_failed" };
  }
}
