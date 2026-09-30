import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

import type { JsonObject } from "../effect_program.ts";
import { durableWriteJson } from "../effect_runtime_io.ts";
import { authorityUnicodeCompare, canonicalAuthorityBytes } from "./authority_store_codec.ts";
import {
  requireShadowBindingGoalRef,
  requireShadowCaptureBinding,
  shadowBindingGoalRef,
  ShadowManagementError,
} from "./shadow_management.ts";
import { parseExactGoalRef } from "../goals/goal_instance_identity.ts";
import { outboxEntryIdentity, OUTBOX_ENTRY_FILE_PATTERN } from "./local_authority_shadow_identity.ts";
import { readProvenShadowSequence } from "./local_authority_shadow.ts";
import {
  LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
} from "./coordination_state_contract.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

/**
 * Lease-partition side of the local authority shadow outbox.
 *
 * The task-lease writers already hold the goal's lease lock when they persist
 * a record; this module lets them append a two-phase outbox entry for exactly
 * that partition inside the same lock. Missing cursor recovery reads validated
 * candidate history without taking M. Preparation failures are returned to the
 * primary owner, which enforces the active capture obligation before writing.
 */

export {
  outboxEntryIdentity,
  LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
};
export const LEASE_PARTITION = "leases";
const LEASE_FILE = /^[A-Za-z0-9_.-]+\.json$/u;
const LEASE_SOURCE_FIELDS = ["todo_id", "version", "lease_epoch", "status", "updated_at"] as const;

export interface LegacyLocalAuthorityShadowBinding {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA;
  provider: "file_v0";
}
export interface ExactLocalAuthorityShadowBinding {
  schema_version: typeof LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA;
  provider: "file_v0";
  goal_ref: JsonObject;
}
export type LocalAuthorityShadowBinding =
  | LegacyLocalAuthorityShadowBinding
  | ExactLocalAuthorityShadowBinding;

/** Decode the optional per-request binding; anything but the exact contract is "absent". */
export function decodeLocalAuthorityShadowBinding(
  value: unknown,
): LocalAuthorityShadowBinding | null {
  if (value === null || value === undefined || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  if (record.schema_version === LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA) {
    return keys.length === 2 && record.provider === "file_v0"
      ? { schema_version: LOCAL_AUTHORITY_SHADOW_BINDING_SCHEMA, provider: "file_v0" }
      : null;
  }
  const goalRef = parseExactGoalRef(record.goal_ref);
  if (
    keys.length !== 3 ||
    record.schema_version !== LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA ||
    record.provider !== "file_v0" ||
    goalRef.kind !== "parsed"
  ) {
    return null;
  }
  return {
    schema_version: LOCAL_AUTHORITY_SHADOW_EXACT_BINDING_SCHEMA,
    provider: "file_v0",
    goal_ref: {
      goal_id: goalRef.value.goalId.value,
      goal_instance_id: goalRef.value.goalInstanceId.value,
    },
  };
}

export function sha256Digest(input: Uint8Array | string): string {
  return `sha256:${createHash("sha256").update(input).digest("hex")}`;
}

/** Digest of the exact bytes `atomicWriteJson` persists for a lease record. */
export function leaseRecordDigest(record: JsonObject): string {
  return sha256Digest(`${JSON.stringify(record, null, 2)}\n`);
}

export function outboxPartitionDirectory(
  runtimeRoot: string,
  goalId: string,
  partition: string,
): string {
  return join(runtimeRoot, "authority-shadow", "outbox", goalId, partition);
}

export function outboxEntryFileName(seq: number, entryId: string, phase: "prepared" | "committed"): string {
  return `${String(seq).padStart(10, "0")}-${entryId}.${phase}.json`;
}

function isMissing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | null)?.code === "ENOENT";
}

const CURSOR_FIELDS = [
  "schema_version", "partition", "last_seq", "last_entry_id", "last_partition_digest",
  "last_cursor", "last_provider_revision", "updated_at",
] as const;
export const MAX_OUTBOX_SEQUENCE = 9_999_999_999;

export class OutboxCursorError extends Error {
  readonly code: "outbox_file_invalid" | "outbox_file_unavailable";
  constructor(code: "outbox_file_invalid" | "outbox_file_unavailable") {
    super(code === "outbox_file_invalid" ? "drain cursor binding is invalid" : "drain cursor cannot be read");
    this.code = code;
  }
}

export async function readOutboxCursor(directory: string, partition: string): Promise<JsonObject | null> {
  let bytes: Uint8Array;
  try {
    bytes = await readFile(join(directory, "drain-cursor.json"));
  } catch (error) {
    if (isMissing(error)) return null;
    throw new OutboxCursorError("outbox_file_unavailable");
  }
  try {
    return decodeOutboxCursor(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), partition);
  } catch {
    throw new OutboxCursorError("outbox_file_invalid");
  }
}

/** Shared wire validation; even a valid cursor still needs an exact receipt. */
export function decodeOutboxCursor(value: unknown, partition: string): JsonObject {
  const invalid = (): Error => new OutboxCursorError("outbox_file_invalid");
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw invalid();
  const record = value as JsonObject;
  if (Object.keys(record).length !== CURSOR_FIELDS.length ||
      CURSOR_FIELDS.some((key) => !Object.hasOwn(record, key))) throw invalid();
  if (record.schema_version !== LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA ||
      !["todos", "leases"].includes(partition) || record.partition !== partition ||
      typeof record.last_seq !== "number" || !Number.isInteger(record.last_seq) ||
      record.last_seq < 1 || record.last_seq > MAX_OUTBOX_SEQUENCE ||
      typeof record.last_entry_id !== "string" ||
      !/^local-shadow-tx-[0-9a-f]{64}$/u.test(record.last_entry_id) ||
      (record.last_partition_digest !== null &&
       (typeof record.last_partition_digest !== "string" || !ENVELOPED_SHA256_PATTERN.test(record.last_partition_digest))) ||
      [record.last_cursor, record.last_provider_revision].some((part) => typeof part !== "string" || part.trim().length === 0)) {
    throw invalid();
  }
  const timestamp = record.updated_at;
  if (typeof timestamp !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/u.test(timestamp)) throw invalid();
  const parsed = new Date(timestamp);
  if (!Number.isFinite(parsed.getTime()) || parsed.getUTCFullYear() < 1 ||
      parsed.toISOString().slice(0, 19) !== timestamp.slice(0, 19)) throw invalid();
  return record;
}

async function nextSeq(
  directory: string,
  runtimeRoot: string,
  goalId: string,
  lineageId: string,
  goalRef: unknown,
): Promise<number> {
  let highest = 0;
  try {
    for (const name of await readdir(directory)) {
      const match = OUTBOX_ENTRY_FILE_PATTERN.exec(name);
      if (match !== null) highest = Math.max(highest, Number(match[1]));
    }
  } catch (error) {
    if (!isMissing(error)) throw error;
  }
  const cursor = await readOutboxCursor(directory, LEASE_PARTITION);
  const proved = cursor === null
    ? await readProvenShadowSequence(runtimeRoot, goalId, LEASE_PARTITION, lineageId, goalRef)
    : cursor.last_seq as number;
  highest = Math.max(highest, proved);
  if (highest >= MAX_OUTBOX_SEQUENCE) throw new Error("outbox sequence exhausted");
  return highest + 1;
}

/**
 * Read the lease partition for one capture.
 *
 * The legacy lease directory is append-retained history: archiving a Todo
 * leaves its released lease file on disk. The source projection models only
 * the current Todo graph, so it drops a lease whose Todo is no longer part of
 * that graph (see `build_todo_runtime_shadow_projection`). Capture must apply
 * the same rule, or archiving a Todo after a released lease writes an orphan
 * edge into the candidate head and parity reports `shadow_projection_drift`.
 *
 * `activeTodoIds` is the current Todo graph supplied by the caller; `null`
 * means the caller could not read it, and the capture stays strict rather
 * than guessing a projection.
 */
async function readLeasePartition(
  leaseDirectory: string,
  plannedStem: string,
  plannedLease: JsonObject | null,
  activeTodoIds: ReadonlySet<string> | null,
): Promise<JsonObject[]> {
  const records = new Map<string, JsonObject>();
  let names: string[] = [];
  try {
    names = await readdir(leaseDirectory);
  } catch (error) {
    if (!isMissing(error)) throw error;
  }
  for (const name of names) {
    if (!LEASE_FILE.test(name) || name.startsWith(".")) continue;
    const stem = name.slice(0, -".json".length);
    if (stem === plannedStem) continue;
    if (activeTodoIds !== null && !activeTodoIds.has(stem)) continue;
    const raw: unknown = JSON.parse(await readFile(join(leaseDirectory, name), "utf8"));
    if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
      records.set(stem, raw as JsonObject);
    }
  }
  if (plannedLease !== null) records.set(plannedStem, plannedLease);
  const stems = [...records.keys()];
  stems.sort(authorityUnicodeCompare);
  return stems.map((stem) => ({ file_stem: stem, record: records.get(stem) as JsonObject }));
}

function leaseSourceFacts(record: JsonObject | null): JsonObject | null {
  if (record === null) return null;
  const facts: JsonObject = {};
  for (const field of LEASE_SOURCE_FIELDS) {
    const value = record[field];
    if (value === undefined) continue;
    if (typeof value === "string" || typeof value === "number" || value === null) {
      facts[field] = value;
    }
  }
  return facts;
}

export interface LeaseOutboxCaptureInput {
  runtime_root: string;
  goal_id: string;
  lease_directory: string;
  write_class: string;
  operation_id: string | null;
  previous_lease: JsonObject | null;
  planned_lease: JsonObject;
  /**
   * Current Todo graph for the goal, used to drop leases orphaned by an
   * archived Todo exactly as the source projection does. `null` keeps the
   * strict pre-existing behavior.
   */
  active_todo_ids: readonly string[] | null;
  goal_ref?: unknown;
}

export interface LeaseOutboxCapture {
  entry_id: string | null;
  seq: number | null;
  source_bytes_digest: string | null;
  failure: { reason_code: string; error_class: string } | null;
  skipped_reason?: string;
  /** Write the committed marker after the lease record landed. Never throws. */
  commit(): Promise<void>;
}

function failureOf(reasonCode: string, error: unknown): { reason_code: string; error_class: string } {
  return {
    reason_code: reasonCode,
    error_class: error instanceof Error ? error.constructor.name : typeof error,
  };
}

/**
 * Record a prepared lease-partition entry for the record about to be written.
 *
 * Call inside the lease lock, right before `atomicWriteJson(leasePath, planned)`;
 * call `commit()` right after it returns. The primary owner must reject an
 * active mutation when preparation returns a failure.
 */
export async function beginLeaseOutboxEntry(
  input: LeaseOutboxCaptureInput,
): Promise<LeaseOutboxCapture> {
  const inert: LeaseOutboxCapture = {
    entry_id: null,
    seq: null,
    source_bytes_digest: null,
    failure: null,
    async commit(): Promise<void> {},
  };
  const plannedStem = typeof input.planned_lease.todo_id === "string"
    ? input.planned_lease.todo_id
    : "";
  if (plannedStem.length === 0) {
    return { ...inert, failure: { reason_code: "outbox_prepare_failed", error_class: "MissingTodoId" } };
  }
  const directory = outboxPartitionDirectory(input.runtime_root, input.goal_id, LEASE_PARTITION);
  try {
    const binding = await requireShadowCaptureBinding(input.runtime_root, input.goal_id);
    const boundGoalRef = shadowBindingGoalRef(binding);
    if (boundGoalRef === null && input.goal_ref !== undefined && input.goal_ref !== null) {
      throw new ShadowManagementError("legacy_goal_binding");
    }
    if (boundGoalRef !== null) {
      requireShadowBindingGoalRef(binding, input.goal_ref, input.goal_id);
    }
    if (input.previous_lease !== null &&
        canonicalAuthorityBytes(input.previous_lease).equals(canonicalAuthorityBytes(input.planned_lease))) {
      return { ...inert, skipped_reason: "partition_unchanged" };
    }
    const activeTodoIds = input.active_todo_ids === null
      ? null
      : new Set(input.active_todo_ids);
    const projection = { leases: await readLeasePartition(input.lease_directory, plannedStem, input.planned_lease, activeTodoIds) };
    const previousRecords = await readLeasePartition(input.lease_directory, plannedStem, input.previous_lease, activeTodoIds);
    for (const item of [...previousRecords, ...projection.leases]) {
      const record = item.record as JsonObject;
      if (record.goal_id !== input.goal_id || record.todo_id !== item.file_stem) {
        throw new Error("lease source identity does not match its partition");
      }
    }
    const previousPartitionDigest = sha256Digest(canonicalAuthorityBytes({
      leases: previousRecords.map((item) => item.record),
    }));
    const bytesDigest = leaseRecordDigest(input.planned_lease);
    const seq = await nextSeq(
      directory,
      input.runtime_root,
      input.goal_id,
      binding.capture_lineage_id,
      input.goal_ref ?? null,
    );
    const sourceRootDigest = binding.source_root_digest;
    const entryId = outboxEntryIdentity(input.goal_id, LEASE_PARTITION, seq, bytesDigest,
      binding.capture_lineage_id, sourceRootDigest);
    const entry: JsonObject = {
      schema_version: LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA,
      goal_id: input.goal_id,
      partition: LEASE_PARTITION,
      seq,
      entry_id: entryId,
      capture_lineage_id: binding.capture_lineage_id,
      writer: {
        runtime: "typescript",
        write_class: input.write_class,
        operation_id: input.operation_id,
      },
      source: {
        kind: "task_lease_record",
        previous_partition_digest: previousPartitionDigest,
        previous_bytes_digest: input.previous_lease === null
          ? null
          : leaseRecordDigest(input.previous_lease),
        bytes_digest: bytesDigest,
        lease: leaseSourceFacts(input.planned_lease),
        previous_lease: leaseSourceFacts(input.previous_lease),
        event_id: null,
      },
      source_root_digest: sourceRootDigest,
      projection,
      partition_digest: null,
      prepared_at: new Date().toISOString(),
    };
    await durableWriteJson(join(directory, outboxEntryFileName(seq, entryId, "prepared")), entry);
    let committed = false;
    const capture: LeaseOutboxCapture = {
      entry_id: entryId,
      seq,
      source_bytes_digest: bytesDigest,
      failure: null,
      async commit(): Promise<void> {
        if (committed) return;
        committed = true;
        try {
          await durableWriteJson(
            join(directory, outboxEntryFileName(seq, entryId, "committed")),
            {
              schema_version: LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
              entry_id: entryId,
              capture_lineage_id: binding.capture_lineage_id,
              committed_at: new Date().toISOString(),
            },
          );
        } catch (error) {
          capture.failure = failureOf("outbox_commit_marker_failed", error);
        }
      },
    };
    return capture;
  } catch (error) {
    if (error instanceof ShadowManagementError && error.code === "bootstrap_required") {
      return { ...inert, skipped_reason: "bootstrap_required" };
    }
    if (error instanceof ShadowManagementError) {
      return { ...inert, failure: failureOf(error.code, error) };
    }
    return { ...inert, failure: failureOf("outbox_prepare_failed", error) };
  }
}
