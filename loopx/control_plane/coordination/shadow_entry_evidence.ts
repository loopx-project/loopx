/** Exact outbox bytes and primary-lock evidence for source transaction recovery.
 * No provider opening, candidate mutation or cleanup belongs to this owner. */
import {createHash} from "node:crypto";
import {readFile, readdir, open} from "node:fs/promises";
import {join, dirname} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {authorityUnicodeCompare, canonicalAuthorityBytes, hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {readShadowBootstrapSourcePath, readShadowBootstrapSourceSnapshot, shadowEventSourcePaths, requireShadowCaptureBinding} from "./shadow_management.ts";
import {OUTBOX_ENTRY_FILE_PATTERN, ShadowLineageError} from "./local_authority_shadow_identity.ts";
import {legacyCoordinationTodoLockPath, taskLeaseLockPath} from "./legacy_writer_lock_paths.ts";
import type {CommitEntryRequest, ShadowPartition} from "./local_authority_shadow.ts";
import {LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA, LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA} from "./coordination_state_contract.generated.ts";

function requireLineage(condition: unknown, reason: string): asserts condition {
  if (!condition) throw new ShadowLineageError(reason);
}

/** Preserve complete lease records, but bind every identity before sorting. */
export function outboxPartitionProjection(value: unknown, goalId: string, partition: ShadowPartition): JsonObject {
  const projection = requireJsonObject(value, "outbox projection");
  if (partition === "todos") {
    requireLineage(hasExactAuthorityKeys(projection, ["handoff_mode", "todos"]) && Array.isArray(projection.todos), "outbox_file_invalid");
    return projection;
  }
  requireLineage(hasExactAuthorityKeys(projection, ["leases"]) && Array.isArray(projection.leases), "outbox_file_invalid");
  const seen = new Set<string>();
  const leases = projection.leases.map(raw => {
    const item = requireJsonObject(raw, "lease source"), record = requireJsonObject(item.record, "lease record");
    requireLineage(hasExactAuthorityKeys(item, ["file_stem", "record"]) && typeof item.file_stem === "string" &&
      record.goal_id === goalId && record.todo_id === item.file_stem && !seen.has(item.file_stem), "source_lease_identity_mismatch");
    seen.add(item.file_stem);
    return record;
  }).sort((a, b) => authorityUnicodeCompare(String(a.todo_id), String(b.todo_id)));
  return {leases};
}

export async function verifyPendingEntryFiles(request: CommitEntryRequest): Promise<void> {
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
    const projection = outboxPartitionProjection(prepared.projection, request.goal_id, entry.partition);
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
    requireLineage(hasExactAuthorityKeys(marker, ["schema_version", "entry_id", "capture_lineage_id", "committed_at"]),
      "outbox_committed_identity_mismatch");
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
export async function withMarkerlessSourceProof<T>(
  request: CommitEntryRequest,
  binding: Awaited<ReturnType<typeof requireShadowCaptureBinding>>,
  operation: () => Promise<T>,
  resolutionPolicy: "assert_recorded" | "derive_from_source",
): Promise<T> {
  if (request.entry.source.kind === "state_event_log") {
    const snapshot = await readShadowBootstrapSourceSnapshot(request.runtime_root, request.goal_id, binding);
    requireLineage(shadowEventSourcePaths(snapshot).includes(request.entry.source.event_log_path), "event_source_binding_invalid");
  }
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
    if (resolutionPolicy === "derive_from_source") {
      // Only the source owner under its primary lock may choose a resolution.
      // Equality with both versions cannot distinguish an interrupted no-op.
      requireLineage(entry.source.bytes_digest !== entry.source.previous_bytes_digest,
        "source_transaction_unproved");
      if (digest === entry.source.bytes_digest) entry.resolution = "committed_proven_by_readback";
      else if (digest === entry.source.previous_bytes_digest) {
        entry.resolution = "abandoned";
        request.partition_projection = null;
        request.partition_digest = null;
      } else throw new ShadowLineageError("source_transaction_unproved");
    }
    const expected = entry.resolution === "abandoned" ? entry.source.previous_bytes_digest : entry.source.bytes_digest;
    requireLineage((entry.resolution === "abandoned" || entry.resolution === "committed_proven_by_readback") &&
      digest === expected, "source_transaction_unproved");
    if (entry.source.kind === "state_event_log" && source !== null) {
      // Replace may have landed before a failed fsync. A readback is not yet
      // durable evidence: establish it before the candidate can acknowledge it.
      const file = await open(sourcePath, "r");
      try { await file.sync(); } finally { await file.close(); }
      if (process.platform !== "win32") {
        const directory = await open(dirname(sourcePath), "r");
        try { await directory.sync(); } finally { await directory.close(); }
      }
    }
    return await operation();
  };
  // Do not wait behind a primary writer while holding maintenance exclusion.
  const timeout = resolutionPolicy === "derive_from_source" ? 0 : undefined;
  try {
    if (entry.partition === "todos") {
      const statePath = await readShadowBootstrapSourcePath(request.runtime_root, request.goal_id, binding);
      const sourcePath = entry.source.kind === "state_event_log" ? entry.source.event_log_path : statePath;
      return await withFileMutationLock(legacyCoordinationTodoLockPath(request.runtime_root, request.goal_id), () =>
        withFileMutationLock(statePath, () => sourcePath === statePath ? proveAndCommit(statePath) :
          withFileMutationLock(sourcePath, () => proveAndCommit(sourcePath), timeout), timeout), timeout);
    }
    const todoId = entry.source.lease?.todo_id;
    requireLineage(typeof todoId === "string" && /^[A-Za-z0-9_.-]+$/.test(todoId) && todoId !== "." && todoId !== "..",
      "source_transaction_unproved");
    const leasePath = join(request.runtime_root, "goals", request.goal_id, "task-leases", `${todoId}.json`);
    return await withFileMutationLock(taskLeaseLockPath(request), () => proveAndCommit(leasePath), timeout);
  } catch (error) {
    if (resolutionPolicy === "derive_from_source" && error instanceof EffectRuntimeLockTimeoutError)
      throw new ShadowLineageError("primary_writer_busy");
    throw error;
  }
}
