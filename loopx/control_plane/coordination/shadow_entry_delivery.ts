import {outboxPartitionProjection} from "./shadow_entry_evidence.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
/** Native delivery of an immutable, witnessed outbox entry. The host selects
 * evidence, never supplies the projection or decides whether a write committed. */
import {lstat, readFile} from "node:fs/promises";
import {join, isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {
  commitLocalAuthorityShadowEntry, commitShadowEntryTransaction, loadValidatedShadowLineage, localAuthorityShadowPartitionDigest,
  ShadowLineageError, type LocalAuthorityShadowDependencies,
} from "./local_authority_shadow.ts";
import {MAX_OUTBOX_SEQUENCE, outboxEntryFileName, sha256Digest} from "./local_authority_shadow_outbox.ts";
import {
  requireShadowBindingGoalRef,
  requireShadowCaptureBinding,
  shadowBindingGoalRef,
  withShadowMaintenanceLock,
} from "./shadow_management.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {
  LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA, LOCAL_AUTHORITY_SHADOW_EXACT_COMMIT_ENTRY_REQUEST_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA,
  LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA, LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA,
} from "./coordination_state_contract.generated.ts";

export const SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA = LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_REQUEST_SCHEMA;
export const SHADOW_EXACT_ENTRY_DELIVERY_REQUEST_SCHEMA = LOCAL_AUTHORITY_SHADOW_EXACT_COMMIT_ENTRY_REQUEST_SCHEMA;
interface Selection {
  schema_version: typeof SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA | typeof SHADOW_EXACT_ENTRY_DELIVERY_REQUEST_SCHEMA;
  runtime_root: string; goal_id: string; partition: "todos" | "leases";
  seq: number; entry_id: string; capture_lineage_id: string;
  prepared_sha256: string; committed_sha256: string | null;
  goal_ref: JsonObject | null;
}
function ensure(value: unknown, code: string): asserts value {
  if (!value) throw new ShadowLineageError(code);
}
function decode(value: unknown): Selection {
  const r = requireJsonObject(value, "shadow entry selection");
  const exactIdentity = r.schema_version === SHADOW_EXACT_ENTRY_DELIVERY_REQUEST_SCHEMA;
  const fields = ["schema_version", "runtime_root", "goal_id", "partition", "seq", "entry_id",
    "capture_lineage_id", "prepared_sha256", "committed_sha256"];
  if (exactIdentity) fields.push("goal_ref");
  ensure(hasExactAuthorityKeys(r, fields) &&
    (exactIdentity || r.schema_version === SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA) &&
    typeof r.runtime_root === "string" && isAbsolute(r.runtime_root) && !r.runtime_root.includes("\0") &&
    typeof r.goal_id === "string" && r.goal_id.trim().length > 0 && !/[\\/\0]/u.test(r.goal_id) &&
    r.goal_id !== "." && r.goal_id !== ".." &&
    (r.partition === "todos" || r.partition === "leases") &&
    typeof r.seq === "number" && Number.isSafeInteger(r.seq) && r.seq > 0 && r.seq <= MAX_OUTBOX_SEQUENCE &&
    typeof r.entry_id === "string" && /^local-shadow-tx-[0-9a-f]{64}$/u.test(r.entry_id) &&
    typeof r.capture_lineage_id === "string" && r.capture_lineage_id.trim().length > 0 &&
    typeof r.prepared_sha256 === "string" && ENVELOPED_SHA256_PATTERN.test(r.prepared_sha256) &&
    (r.committed_sha256 === null || typeof r.committed_sha256 === "string" && ENVELOPED_SHA256_PATTERN.test(r.committed_sha256)),
  "shadow_entry_selection_invalid");
  let goalRef: JsonObject | null = null;
  if (exactIdentity) {
    const parsed = parseExactGoalRef(r.goal_ref);
    ensure(parsed.kind === "parsed" && parsed.value.goalId.value === r.goal_id, "shadow_entry_selection_invalid");
    goalRef = {
      goal_id: parsed.value.goalId.value,
      goal_instance_id: parsed.value.goalInstanceId.value,
    };
  }
  return {schema_version: exactIdentity ? SHADOW_EXACT_ENTRY_DELIVERY_REQUEST_SCHEMA : SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA,
    runtime_root: r.runtime_root, goal_id: r.goal_id, partition: r.partition, seq: r.seq,
    entry_id: r.entry_id, capture_lineage_id: r.capture_lineage_id,
    prepared_sha256: r.prepared_sha256, committed_sha256: r.committed_sha256, goal_ref: goalRef};
}
async function witnessedJson(path: string, digest: string | null): Promise<JsonObject | null> {
  try {
    const stat = await lstat(path);
    ensure(stat.isFile() && !stat.isSymbolicLink(), "outbox_file_invalid");
    const bytes = await readFile(path);
    ensure(digest !== null && sha256Digest(bytes) === digest, "outbox_file_changed");
    return requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)), "outbox record");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      ensure(digest === null, "outbox_file_changed");
      return null;
    }
    throw error;
  }
}
async function recordedRequest(r: Selection): Promise<JsonObject> {
  const directory = join(r.runtime_root, "authority-shadow", "outbox", r.goal_id, r.partition);
  const prepared = await witnessedJson(join(directory, outboxEntryFileName(r.seq, r.entry_id, "prepared")), r.prepared_sha256);
  ensure(prepared !== null && hasExactAuthorityKeys(prepared, ["schema_version", "goal_id", "partition", "seq", "entry_id",
    "writer", "source", "source_root_digest", "capture_lineage_id", "projection", "partition_digest", "prepared_at"]) &&
    prepared.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_ENTRY_SCHEMA && prepared.goal_id === r.goal_id &&
    prepared.partition === r.partition && prepared.seq === r.seq && prepared.entry_id === r.entry_id &&
    prepared.capture_lineage_id === r.capture_lineage_id, "outbox_file_invalid");
  const marker = await witnessedJson(join(directory, outboxEntryFileName(r.seq, r.entry_id, "committed")), r.committed_sha256);
  if (marker !== null) ensure(hasExactAuthorityKeys(marker, ["schema_version", "entry_id", "capture_lineage_id", "committed_at"]) &&
    marker.schema_version === LOCAL_AUTHORITY_SHADOW_OUTBOX_COMMIT_SCHEMA && marker.entry_id === r.entry_id &&
    marker.capture_lineage_id === r.capture_lineage_id, "outbox_file_invalid");
  const source = {...requireJsonObject(prepared.source, "outbox source")};
  delete source.previous_lease; // Writer recovery evidence, not the committed source contract.
  const projection = outboxPartitionProjection(prepared.projection, r.goal_id, r.partition);
  if (r.partition === "todos") ensure(prepared.partition_digest === localAuthorityShadowPartitionDigest("todos", projection),
    "partition_digest_mismatch");
  return {runtime_root: r.runtime_root, goal_id: r.goal_id,
    ...(r.goal_ref === null ? {} : {goal_ref: r.goal_ref}),
    entry: {entry_id: r.entry_id, partition: r.partition, seq: r.seq,
      capture_lineage_id: r.capture_lineage_id, prepared_sha256: r.prepared_sha256, committed_sha256: r.committed_sha256,
      writer: prepared.writer, source, source_root_digest: prepared.source_root_digest,
      prepared_at: prepared.prepared_at, committed_at: marker?.committed_at ?? null,
      // This provisional value is resolved under the source lock by the transaction owner.
      resolution: marker === null ? "committed_proven_by_readback" : "committed"},
    partition_projection: projection, partition_digest: localAuthorityShadowPartitionDigest(r.partition, projection)};
}
function result(r: Selection, fields: JsonObject): JsonObject {
  return {schema_version: LOCAL_AUTHORITY_SHADOW_COMMIT_ENTRY_RESULT_SCHEMA, goal_id: r.goal_id,
    entry_id: r.entry_id, partition: r.partition, seq: r.seq, capture_lineage_id: r.capture_lineage_id,
    outcome: "failed", reason_code: null, no_op: false, store_identity: null, provider_revision: null,
    cursor: null, head_digest: null, resolution: null, partition_digest: null, ...fields};
}

/** A lost response can be replayed after local cleanup. Only a fully validated
 * lineage and the exact retained byte witnesses can replace missing files. */
export async function deliverShadowEntry(value: unknown, dependencies: LocalAuthorityShadowDependencies = {}): Promise<JsonObject> {
  let r: Selection;
  try { r = decode(value); } catch {
    throw new EffectRuntimeRequestError("shadow_entry_selection_invalid", "shadow_entry_selection_invalid");
  }
  try {
    const selected = await withShadowMaintenanceLock(r.runtime_root, r.goal_id, async () => {
      const binding = await requireShadowCaptureBinding(r.runtime_root, r.goal_id);
      ensure(binding.capture_lineage_id === r.capture_lineage_id, "stale_generation");
      if (r.goal_ref === null) {
        ensure(shadowBindingGoalRef(binding) === null, "legacy_goal_binding");
      } else {
        requireShadowBindingGoalRef(binding, r.goal_ref, r.goal_id);
      }
      const directory = join(r.runtime_root, "authority-shadow", "file-v0");
      const store = (dependencies.openStore ?? ((path, id) => new FileAuthorityStore(path, id, {existingOnly: true})))(directory, r.goal_id);
      const retained = await store.readReceipt(r.entry_id);
      if (retained.status === "found") {
        await loadValidatedShadowLineage(store, r.runtime_root, r.goal_id, binding);
        const receipt = retained.receipts[0];
        ensure(retained.receipts.length === 1 && receipt.entry_id === r.entry_id && receipt.seq === r.seq &&
          receipt.partition === r.partition && receipt.capture_lineage_id === r.capture_lineage_id &&
          receipt.source_root_digest === binding.source_root_digest && receipt.prepared_sha256 === r.prepared_sha256 &&
          receipt.committed_sha256 === r.committed_sha256, "outbox_receipt_mismatch");
        return {kind: "replay" as const, value: result(r, {outcome: "replayed", no_op: receipt.no_op,
          resolution: receipt.resolution, partition_digest: receipt.partition_digest,
          store_identity: binding.store_identity, provider_revision: retained.provider_revision, cursor: retained.cursor})};
      }
      ensure(retained.status === "missing", "shadow_receipt_unavailable");
      return {kind: "pending" as const, value: await recordedRequest(r)};
    });
    if (selected.kind === "replay") return selected.value;
    // The transaction reacquires M, validates byte witnesses and resolves source
    // state under its primary lock through commit. A changed selection rejects.
    const marked = r.committed_sha256 !== null;
    const delivered = marked
      ? await commitLocalAuthorityShadowEntry(selected.value, dependencies)
      : await commitShadowEntryTransaction(selected.value, dependencies, "derive_from_source");
    const settled = ["delivered", "replayed", "ambiguous_reconciled"].includes(delivered.outcome);
    return result(r, {...delivered, ...(marked && settled
      ? {resolution: "committed", partition_digest: selected.value.partition_digest} : {})});
  } catch (error) {
    const code = error as {reason_code?: string; code?: string};
    return result(r, {reason_code: code.reason_code ?? code.code ?? "outbox_file_invalid"});
  }
}
