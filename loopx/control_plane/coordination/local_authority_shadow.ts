import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { withFileMutationLock } from "../effect_runtime_io.ts";
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
  readShadowBootstrapSourcePath,
  requireShadowCaptureBinding,
  withShadowMaintenanceLock,
} from "./shadow_management.ts";
import { outboxEntryIdentity, OUTBOX_ENTRY_FILE_PATTERN } from "./local_authority_shadow_identity.ts";
import { legacyCoordinationTodoLockPath, taskLeaseLockPath } from "./legacy_writer_lock_paths.ts";
import {
  LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA,
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
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export {
  LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA,
};

const REQUEST_FIELDS = new Set([
  "schema_version",
  "mode",
  "runtime_root",
  "goal_id",
  "observation_id",
  "observation_trigger",
  "source_digest",
  "source_projection",
]);
export type LocalAuthorityShadowOutcome =
  | "captured"
  | "replayed"
  | "ambiguous_reconciled"
  | "ambiguous_unproved"
  | "unavailable"
  | "failed"
  | "protocol_mismatch"
  | "conflict_retry_required";

export interface LocalAuthorityShadowEvidence extends JsonObject {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA;
  outcome: LocalAuthorityShadowOutcome;
  reason_code: string | null;
  goal_id: string;
  observation_id: string;
  source_digest: string;
  capture_kind: "post_commit_snapshot";
  source_transaction_correlated: false;
  durable_source_outbox: false;
  source_candidate_compared: false;
  parity_verdict: "not_evaluated";
  primary_authority: "legacy_local";
  candidate_provider: "file";
  candidate_read_for_decision: false;
  provider_to_local_writes: false;
  primary_writeback_preserved: true;
  store_identity: string | null;
  provider_revision: string | null;
  cursor: string | null;
}

interface LocalAuthorityShadowRequest {
  mode: "file_one_way";
  runtime_root: string;
  goal_id: string;
  observation_id: string;
  observation_trigger: string;
  source_digest: string;
  source_projection: JsonObject;
}

export interface LocalAuthorityShadowDependencies {
  openStore?: (directory: string, goalId: string) => AuthorityStore;
}

function decodeRequest(value: unknown): LocalAuthorityShadowRequest {
  const request = requireJsonObject(value, "local authority shadow request");
  const unexpected = Object.keys(request).filter((field) => !REQUEST_FIELDS.has(field));
  if (unexpected.length > 0) {
    const listed = [...unexpected].sort((left, right) => (left < right ? -1 : left > right ? 1 : 0));
    throw new EffectRuntimeRequestError(
      `Local authority shadow request has unsupported fields: ${listed.join(", ")}`,
    );
  }
  if (request.schema_version !== LOCAL_AUTHORITY_SHADOW_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Local authority shadow request schema mismatch");
  }
  if (request.mode !== "file_one_way") {
    throw new EffectRuntimeRequestError("Local authority shadow mode must be file_one_way");
  }
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError(
      "Local authority shadow goal id must be a single path segment",
    );
  }
  const projection = requireJsonObject(request.source_projection, "source_projection");
  if (
    projection.schema_version !== LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA ||
    projection.goal_id !== goalId
  ) {
    throw new EffectRuntimeRequestError(
      "Local authority shadow projection schema or goal identity mismatch",
    );
  }
  const sourceDigest = requireNonEmptyString(request.source_digest, "source_digest");
  if (!/^sha256:[a-f0-9]{64}$/u.test(sourceDigest)) {
    throw new EffectRuntimeRequestError("source_digest must be sha256:<64 lowercase hex>");
  }
  return {
    mode: "file_one_way",
    runtime_root: requireNonEmptyString(request.runtime_root, "runtime_root"),
    goal_id: goalId,
    observation_id: requireNonEmptyString(request.observation_id, "observation_id"),
    observation_trigger: requireNonEmptyString(
      request.observation_trigger,
      "observation_trigger",
    ),
    source_digest: sourceDigest,
    source_projection: structuredClone(projection),
  };
}

function evidence(
  request: LocalAuthorityShadowRequest,
  outcome: LocalAuthorityShadowOutcome,
  options: {
    reasonCode?: string | null;
    storeIdentity?: string | null;
    providerRevision?: string | null;
    cursor?: string | null;
  } = {},
): LocalAuthorityShadowEvidence {
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_EVIDENCE_SCHEMA,
    outcome,
    reason_code: options.reasonCode ?? null,
    goal_id: request.goal_id,
    observation_id: request.observation_id,
    source_digest: request.source_digest,
    capture_kind: "post_commit_snapshot",
    source_transaction_correlated: false,
    durable_source_outbox: false,
    source_candidate_compared: false,
    parity_verdict: "not_evaluated",
    primary_authority: "legacy_local",
    candidate_provider: "file",
    candidate_read_for_decision: false,
    provider_to_local_writes: false,
    primary_writeback_preserved: true,
    store_identity: options.storeIdentity ?? null,
    provider_revision: options.providerRevision ?? null,
    cursor: options.cursor ?? null,
  };
}

function readFailureEvidence(
  request: LocalAuthorityShadowRequest,
  result: Extract<AuthorityStoreLoadResult, { status: "unavailable" | "failed" }>,
  storeIdentity: string | null,
): LocalAuthorityShadowEvidence {
  return evidence(request, result.status, {
    reasonCode: result.reason_code,
    storeIdentity,
  });
}

function receiptMatches(
  request: LocalAuthorityShadowRequest,
  result: Extract<AuthorityStoreReceiptResult, { status: "found" }>,
): boolean {
  return result.receipts.some((raw) => {
    const receipt = raw as Record<string, unknown>;
    return receipt.schema_version === LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA &&
      receipt.observation_id === request.observation_id &&
      receipt.source_digest === request.source_digest &&
      receipt.primary_authority === "legacy_local" &&
      receipt.provider_to_local_writes === false;
  });
}

async function reconcileReceipt(
  store: AuthorityStore,
  request: LocalAuthorityShadowRequest,
  storeIdentity: string,
  reconciledOutcome: "replayed" | "ambiguous_reconciled",
): Promise<LocalAuthorityShadowEvidence> {
  const result = await store.readReceipt(request.observation_id);
  if (result.status === "found" && receiptMatches(request, result)) {
    return evidence(request, reconciledOutcome, {
      storeIdentity,
      providerRevision: result.provider_revision,
      cursor: result.cursor,
    });
  }
  if (result.status === "unavailable") {
    return evidence(request, "unavailable", {
      reasonCode: result.reason_code,
      storeIdentity,
    });
  }
  if (result.status === "failed") {
    return evidence(request, "failed", {
      reasonCode: result.reason_code,
      storeIdentity,
    });
  }
  return evidence(
    request,
    reconciledOutcome === "ambiguous_reconciled"
      ? "ambiguous_unproved"
      : "protocol_mismatch",
    {
      reasonCode: result.status === "missing"
        ? "observation_receipt_missing"
        : "observation_receipt_mismatch",
      storeIdentity,
    },
  );
}

/**
 * Record a post-commit observation in a candidate AuthorityStore.
 *
 * The legacy local writers remain the only decision authority. This function
 * receives a completed source projection and has no route back to those files.
 */
export async function recordLocalAuthorityShadow(
  value: unknown,
  dependencies: LocalAuthorityShadowDependencies = {},
): Promise<LocalAuthorityShadowEvidence> {
  const request = decodeRequest(value);
  let store: AuthorityStore;
  try {
    const providerDirectory = join(
      request.runtime_root,
      "authority-shadow",
      "file",
      request.goal_id,
    );
    store = (dependencies.openStore ?? ((directory, goalId) =>
      new FileAuthorityStore(directory, goalId)))(
        providerDirectory,
        request.goal_id,
      );
  } catch {
    return evidence(request, "unavailable", {
      reasonCode: "provider_construction_failed",
    });
  }

  try {
    const identity = await store.storeIdentity();
    if (identity.status !== "available") {
      return evidence(request, identity.status, { reasonCode: identity.reason_code });
    }
    const storeIdentity = identity.store_identity;
    const receipt = {
      schema_version: LOCAL_AUTHORITY_SHADOW_OBSERVATION_RECEIPT_SCHEMA,
      observation_id: request.observation_id,
      source_digest: request.source_digest,
      observation_trigger: request.observation_trigger,
      source_transaction_correlated: false,
      parity_verdict: "not_evaluated",
      primary_authority: "legacy_local",
      candidate_read_for_decision: false,
      provider_to_local_writes: false,
    };
    const event = {
      schema_version: "loopx_local_authority_shadow_event_v0",
      kind: "post_commit_snapshot_captured",
      observation_id: request.observation_id,
      observation_trigger: request.observation_trigger,
      source_digest: request.source_digest,
    };

    const loaded = await store.loadAuthority();
    if (loaded.status === "unavailable" || loaded.status === "failed") {
      return readFailureEvidence(request, loaded, storeIdentity);
    }
    const committed = await store.commitAuthority({
      expected_provider_revision:
        loaded.status === "loaded" ? loaded.provider_revision : null,
      operation_id: request.observation_id,
      events: [event],
      next_projection: request.source_projection,
      receipts: [receipt],
    });
    if (committed.status === "applied") {
      return evidence(request, "captured", {
        storeIdentity,
        providerRevision: committed.provider_revision,
        cursor: committed.cursor,
      });
    }
    if (committed.status === "ambiguous") {
      return await reconcileReceipt(
        store,
        request,
        storeIdentity,
        "ambiguous_reconciled",
      );
    }
    if (committed.status === "failed") {
      return evidence(request, "failed", {
        reasonCode: committed.reason_code,
        storeIdentity,
      });
    }
    if (committed.conflict_kind === "operation_id_exists") {
      return await reconcileReceipt(store, request, storeIdentity, "replayed");
    }
    return evidence(request, "conflict_retry_required", {
      reasonCode: "provider_revision_mismatch",
      storeIdentity,
      providerRevision: committed.current_provider_revision,
      cursor: committed.current_cursor,
    });
  } catch {
    return evidence(request, "unavailable", {
      reasonCode: "provider_call_failed",
    });
  }
}

// ---------------------------------------------------------------------------
// Transaction-bound entries (Stage 2C second half).
//
// A drained outbox entry becomes exactly one candidate transaction whose
// operation_id is the entry id, so a receipt names the primary transaction it
// records instead of a post-commit snapshot that may include other writers.
// ---------------------------------------------------------------------------

export const LOCAL_AUTHORITY_SHADOW_PROJECTION_SCHEMA_V1 =
  LOCAL_AUTHORITY_SHADOW_TRANSACTION_PROJECTION_SCHEMA;
export { LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA };
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
  "schema_version",
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

interface ShadowEntrySource {
  previous_partition_digest: string;
  kind: (typeof SOURCE_KINDS)[number];
  previous_bytes_digest: string | null;
  bytes_digest: string | null;
  lease: JsonObject | null;
  event_id: string | null;
}

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

interface CommitEntryRequest {
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
  rejectUnexpectedFields(source, new Set(["kind", "previous_bytes_digest", "previous_partition_digest", "bytes_digest", "lease", "event_id"]), "entry.source");
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
      kind: requireStringLiteral(source.kind, SOURCE_KINDS, "entry.source.kind"),
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
  if (request.schema_version !== LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Local authority shadow commit entry request schema mismatch",
    );
  }
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

/** Digest of the fields parity compares; must match Python `head_digest`. */
export function localAuthorityShadowHeadDigest(head: JsonObject): string {
  const view = {
    handoff_mode: head.handoff_mode ?? null,
    todos: head.todos ?? null,
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
 * Todo ids still present in the current Todo graph.
 *
 * A published Todo partition also retains archived rows for audit, so the raw
 * record list is not the graph. The source projection
 * (`build_todo_runtime_shadow_projection`) and the TypeScript source
 * verification both define the graph as `archive_state === "active"`; capture
 * and fold must apply that same typed membership rule or the candidate head
 * keeps a lease edge the source never emits.
 */
export function currentGraphTodoIds(todos: readonly JsonObject[]): Set<string> {
  return new Set(
    todos
      .filter((item) => item.archive_state === "active")
      .map((item) => String(item.todo_id)),
  );
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

export class ShadowLineageError extends Error {
  readonly reason_code: string;
  constructor(reasonCode: string) { super(reasonCode); this.reason_code = reasonCode; }
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
    requireLineage(request.partition_digest === `sha256:${canonicalAuthoritySha256(request.partition_projection)}`,
      "partition_digest_mismatch");
  }
  requireLineage(entry.source.kind !== "state_event_log", "event_log_writer_not_bound");
  requireLineage(entry.source.kind === (entry.partition === "todos" ? "markdown_active_state" : "task_lease_record"),
    "entry_source_partition_mismatch");
  requireLineage(entry.resolution !== "unproved" && entry.resolution !== "seed", "source_transaction_unproved");
}

function partitionProjection(head: JsonObject, partition: ShadowPartition): JsonObject {
  return partition === "todos" ? { handoff_mode: head.handoff_mode, todos: head.todos } : { leases: head.leases };
}

function validateSourceContinuity(request: CommitEntryRequest, previous: JsonObject): void {
  const digest = `sha256:${canonicalAuthoritySha256(partitionProjection(previous, request.entry.partition))}`;
  requireLineage(request.entry.source.previous_partition_digest === digest, "source_partition_continuity_unproved");
  if (!NO_OP_RESOLUTIONS.has(request.entry.resolution)) {
    requireLineage(request.partition_digest !== digest, "partition_unchanged");
  }
}

async function verifyPendingEntryFiles(request: CommitEntryRequest): Promise<void> {
  const entry = request.entry;
  const directory = join(request.runtime_root, "authority-shadow", "outbox", request.goal_id, entry.partition);
  const stem = `${String(entry.seq).padStart(10, "0")}-${entry.entry_id}`;
  const bytes = await readFile(join(directory, `${stem}.prepared.json`));
  requireLineage(`sha256:${createHash("sha256").update(bytes).digest("hex")}` === entry.prepared_sha256,
    "outbox_prepared_bytes_mismatch");
  const prepared = requireJsonObject(JSON.parse(bytes.toString("utf8")), "prepared entry");
  requireLineage(prepared.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA &&
    prepared.goal_id === request.goal_id && prepared.entry_id === entry.entry_id && prepared.seq === entry.seq &&
    prepared.partition === entry.partition && prepared.capture_lineage_id === entry.capture_lineage_id &&
    prepared.source_root_digest === entry.source_root_digest && prepared.prepared_at === entry.prepared_at &&
    canonicalAuthorityBytes(prepared.writer).equals(canonicalAuthorityBytes(entry.writer)), "outbox_prepared_identity_mismatch");
  const source = { ...requireJsonObject(prepared.source, "prepared source") };
  delete source.previous_lease;
  requireLineage(canonicalAuthorityBytes(source).equals(canonicalAuthorityBytes(entry.source)), "outbox_prepared_source_mismatch");
  if (request.partition_projection !== null) {
    let projection = requireJsonObject(prepared.projection, "prepared projection");
    if (entry.partition === "leases") {
      requireLineage(Array.isArray(projection.leases), "outbox_prepared_projection_mismatch");
      const leases = (projection.leases as JsonObject[]).map((value) => {
        const record = requireJsonObject(value.record, "prepared lease record");
        requireLineage(record.goal_id === request.goal_id && record.todo_id === value.file_stem, "source_lease_identity_mismatch");
        return record;
      });
      projection = { leases };
    }
    requireLineage(canonicalAuthorityBytes(projection).equals(canonicalAuthorityBytes(request.partition_projection)),
      "outbox_prepared_projection_mismatch");
  }
  let markerBytes: Buffer | null = null;
  try { markerBytes = await readFile(join(directory, `${stem}.committed.json`)); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  requireLineage((markerBytes === null ? null : `sha256:${createHash("sha256").update(markerBytes).digest("hex")}`) === entry.committed_sha256,
    "outbox_committed_bytes_mismatch");
  if (markerBytes !== null) {
    requireLineage(entry.resolution === "committed", "outbox_resolution_marker_mismatch");
    const marker = requireJsonObject(JSON.parse(markerBytes.toString("utf8")), "committed marker");
    rejectUnexpectedFields(marker, new Set(["schema_version", "entry_id", "capture_lineage_id", "committed_at"]), "committed marker");
    requireLineage(marker.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA && marker.entry_id === entry.entry_id &&
      marker.capture_lineage_id === entry.capture_lineage_id && marker.committed_at === entry.committed_at, "outbox_committed_identity_mismatch");
  } else {
    requireLineage(entry.committed_at === null && entry.resolution !== "committed", "outbox_committed_marker_missing");
  }
}

/** Resolve markerless evidence again under the actual primary lock, and keep
 * that lock through the candidate commit. A caller's earlier observation can
 * have become stale while it crossed the Python/TypeScript process boundary.
 */
async function withMarkerlessSourceProof<T>(
  request: CommitEntryRequest,
  binding: Awaited<ReturnType<typeof requireShadowCaptureBinding>>,
  operation: () => Promise<T>,
): Promise<T> {
  if (request.entry.committed_sha256 !== null) return await operation();
  const entry = request.entry;
  const proveAndCommit = async (sourcePath: string): Promise<T> => {
    await verifyPendingEntryFiles(request);
    const directory = join(request.runtime_root, "authority-shadow", "outbox", request.goal_id, entry.partition);
    for (const item of await readdir(directory, { withFileTypes: true })) {
      requireLineage(item.isFile() && !item.isSymbolicLink(), "source_transaction_unproved");
      if (item.name === "drain-cursor.json") continue;
      const match = OUTBOX_ENTRY_FILE_PATTERN.exec(item.name);
      requireLineage(match !== null && Number(match[1]) <= entry.seq &&
        (Number(match[1]) !== entry.seq || match[2] === entry.entry_id), "source_transaction_unproved");
    }
    let source: Buffer | null = null;
    try { source = await readFile(sourcePath); } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    const digest = source === null ? null : `sha256:${createHash("sha256").update(source).digest("hex")}`;
    const expected = entry.resolution === "abandoned" ? entry.source.previous_bytes_digest : entry.source.bytes_digest;
    requireLineage((entry.resolution === "abandoned" || entry.resolution === "committed_proven_by_readback") &&
      digest === expected, "source_transaction_unproved");
    return await operation();
  };
  if (entry.partition === "todos") {
    const statePath = await readShadowBootstrapSourcePath(request.runtime_root, request.goal_id, binding);
    return await withFileMutationLock(legacyCoordinationTodoLockPath(request.runtime_root, request.goal_id), () =>
      withFileMutationLock(statePath, () => proveAndCommit(statePath)));
  }
  const todoId = entry.source.lease?.todo_id;
  requireLineage(typeof todoId === "string" && /^[A-Za-z0-9_.-]+$/.test(todoId) && todoId !== "." && todoId !== "..",
    "source_transaction_unproved");
  const leasePath = join(request.runtime_root, "goals", request.goal_id, "task-leases", `${todoId}.json`);
  return await withFileMutationLock(taskLeaseLockPath(request), () => proveAndCommit(leasePath));
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
          previous_bytes_digest: receipt.source_previous_bytes_digest, event_id: receipt.source_event_id, lease: receipt.source_lease },
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
  const request = decodeCommitEntryRequest(value);
  const noOp = NO_OP_RESOLUTIONS.has(request.entry.resolution);
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
          return await reconcileTransactionReceipt(store, request, binding.store_identity, "replayed");
        }
        requireLineage(existing.status === "missing", "shadow_receipt_unavailable");
        requireLineage(lineage.transactions.length < 10000, "shadow_qualification_history_too_large");
        const attempt = await withMarkerlessSourceProof(request, active, async () => {
          await verifyPendingEntryFiles(request);
          requireLineage(request.entry.seq === lineage.last_sequences[request.entry.partition] + 1,
            "partition_sequence_mismatch");
          validateSourceContinuity(request, lineage.head.head);
          return await attemptCommitEntry(store, request, binding.store_identity, noOp);
        });
        if (attempt.kind === "final") return attempt.result;
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
): JsonObject {
  const result: JsonObject = { ...base, status: loaded.status, store_identity: storeIdentity };
  if (loaded.status === "loaded") {
    result.provider_revision = loaded.provider_revision;
    result.cursor = loaded.cursor;
    result.head = structuredClone(loaded.head);
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
    const result = loadedReadResult(base, identity.store_identity, loaded);
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
        transactions: structuredClone(lineage.transactions.filter((transaction) =>
          request.scan_after_cursor === null || Number(transaction.cursor) > Number(request.scan_after_cursor)
        ).slice(0, request.scan_limit)) as unknown as JsonObject[],
        receipt: receipt === null ? null : structuredClone(receipt) as unknown as JsonObject,
      };
    }
    return request.scan_limit > 0 ? await appendScanPage(store, request, result) : result;
  } catch (error) {
    const raw = error as { reason_code?: string; code?: string };
    return { ...base, status: "failed", reason_code: raw.reason_code ?? raw.code ?? "provider_call_failed" };
  }
}
