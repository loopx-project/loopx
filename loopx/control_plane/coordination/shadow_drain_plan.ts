/** Receipt-proven drain decisions. Python supplies locked filesystem observations;
 * this owner returns a plan, never permission to delete unchecked files. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {decodeOutboxCursor, OutboxCursorError, MAX_OUTBOX_SEQUENCE} from "./local_authority_shadow_outbox.ts";
import {readLocalAuthorityShadow, type LocalAuthorityShadowDependencies} from "./local_authority_shadow.ts";
import {LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA} from "./coordination_state_contract.generated.ts";

export const SHADOW_DRAIN_PLAN_REQUEST_SCHEMA = "loopx_shadow_drain_plan_request_v0";
export const SHADOW_DRAIN_PLAN_RESULT_SCHEMA = "loopx_shadow_drain_plan_result_v0";
type Partition = "todos" | "leases";
interface Entry {
  entry_id: string;
  seq: number;
  prepared: boolean;
  capture_lineage_id: string | null;
  prepared_sha256: string | null;
  committed_sha256: string | null;
}
interface Request {
  runtime_root: string;
  goal_id: string;
  partition: Partition;
  capture_lineage_id: string;
  store_identity: string;
  source_root_digest: string;
  cursor: JsonObject | null;
  entries: Entry[];
  remaining_entries: number;
  budget_open: boolean;
  acknowledgement: JsonObject | null;
}
class DrainPlanError extends Error {
  readonly reasonCode: string;
  constructor(code: string, message: string) {super(message); this.reasonCode = code;}
}
function ensure(condition: unknown, code: string, message: string): asserts condition {
  if (!condition) throw new DrainPlanError(code, message);
}
function text(value: unknown): string {
  ensure(typeof value === "string" && value.length > 0, "shadow_drain_request_invalid", "expected nonempty string");
  return value;
}
function nullableText(value: unknown): string | null {return value === null ? null : text(value);}
function decode(value: unknown): Request {
  const r = requireJsonObject(value, "drain plan request");
  ensure(hasExactAuthorityKeys(r, ["schema_version", "runtime_root", "goal_id", "partition", "capture_lineage_id",
    "store_identity", "source_root_digest", "cursor", "entries", "remaining_entries", "budget_open", "acknowledgement"]) &&
    r.schema_version === SHADOW_DRAIN_PLAN_REQUEST_SCHEMA && (r.partition === "todos" || r.partition === "leases") &&
    Array.isArray(r.entries) && typeof r.budget_open === "boolean" &&
    Number.isSafeInteger(r.remaining_entries) && Number(r.remaining_entries) >= 0,
  "shadow_drain_request_invalid", "invalid drain plan request");
  const partition = r.partition;
  const seen = new Set<string>();
  let previous = 0;
  const entries = r.entries.map(raw => {
    const e = requireJsonObject(raw, "drain entry observation");
    ensure(hasExactAuthorityKeys(e, ["entry_id", "seq", "prepared", "capture_lineage_id", "prepared_sha256", "committed_sha256"]) &&
      Number.isSafeInteger(e.seq) && Number(e.seq) > previous && Number(e.seq) <= MAX_OUTBOX_SEQUENCE && typeof e.prepared === "boolean",
    "shadow_drain_request_invalid", "entry observations must be ordered unique sequences");
    const id = text(e.entry_id);
    ensure(!seen.has(id), "shadow_drain_request_invalid", "duplicate entry identity");
    previous = Number(e.seq); seen.add(id);
    return {entry_id: id, seq: previous, prepared: e.prepared,
      capture_lineage_id: nullableText(e.capture_lineage_id), prepared_sha256: nullableText(e.prepared_sha256),
      committed_sha256: nullableText(e.committed_sha256)};
  });
  const acknowledgement = r.acknowledgement === null ? null : requireJsonObject(r.acknowledgement, "drain acknowledgement");
  if (acknowledgement !== null) {
    ensure(hasExactAuthorityKeys(acknowledgement, ["entry_id", "seq", "cursor", "provider_revision", "store_identity", "no_op", "partition_digest"]) &&
      Number.isSafeInteger(acknowledgement.seq) && Number(acknowledgement.seq) > 0 &&
      typeof acknowledgement.no_op === "boolean", "shadow_commit_entry_result_invalid", "invalid acknowledgement");
    for (const key of ["entry_id", "cursor", "provider_revision", "store_identity"]) text(acknowledgement[key]);
    nullableText(acknowledgement.partition_digest);
  }
  return {runtime_root: text(r.runtime_root), goal_id: text(r.goal_id), partition,
    capture_lineage_id: text(r.capture_lineage_id), store_identity: text(r.store_identity),
    source_root_digest: text(r.source_root_digest), cursor: r.cursor === null ? null : decodeOutboxCursor(r.cursor, partition),
    entries, remaining_entries: Number(r.remaining_entries), budget_open: r.budget_open,
    acknowledgement};
}
function receipt(transaction: JsonObject): JsonObject | null {
  return Array.isArray(transaction.receipts) && transaction.receipts.length === 1 &&
    transaction.receipts[0] !== null && typeof transaction.receipts[0] === "object" && !Array.isArray(transaction.receipts[0])
    ? transaction.receipts[0] as JsonObject : null;
}
function partitionDigest(transaction: JsonObject, partition: Partition): unknown {
  const partitions = requireJsonObject(transaction.projection_partitions, "proved projection partitions");
  const marker = partitions[partition];
  return marker === null ? null : requireJsonObject(marker, "proved partition marker").partition_digest;
}

function provedTransactions(r: Request, view: JsonObject): unknown[] {
  ensure(view.status === "loaded", String(view.reason_code ?? "outbox_receipt_unproved"), "candidate history is not proved");
  ensure(view.proof !== null && typeof view.proof === "object" && !Array.isArray(view.proof),
    "outbox_receipt_unproved", "candidate history is not proved");
  const proof = requireJsonObject(view.proof, "shadow lineage proof");
  const transactions = proof.transactions;
  ensure(proof.capture_lineage_id === r.capture_lineage_id && view.store_identity === r.store_identity &&
    Array.isArray(transactions) && transactions.length > 0,
  "outbox_receipt_unproved", "incomplete or foreign history proof");
  const tail = requireJsonObject(transactions.at(-1), "last proved transaction");
  ensure(tail.cursor === view.cursor && tail.provider_revision === view.provider_revision,
    "outbox_receipt_unproved", "history proof does not reach its head");
  return transactions;
}
function compactView(view: JsonObject): JsonObject {
  return {store_identity: view.store_identity, provider_revision: view.provider_revision,
    cursor: view.cursor, head_digest: view.head_digest};
}

/** Pure decision seam for independent counterexamples. Production always obtains
 * view from the existing full-lineage verifier, never from the RPC caller. */
export function planShadowDrain(value: unknown, rawView: unknown): JsonObject {
  const r = decode(value);
  const view = requireJsonObject(rawView, "shadow readback");
  const transactions = provedTransactions(r, view);
  const history = new Map<number, JsonObject>();
  for (const raw of transactions) {
    const tx = requireJsonObject(raw, "proved transaction");
    const rc = receipt(tx);
    if (rc === null || rc.partition !== r.partition) continue;
    ensure(Number.isSafeInteger(rc.seq) && rc.seq === history.size + 1 &&
      rc.capture_lineage_id === r.capture_lineage_id && rc.source_root_digest === r.source_root_digest &&
      rc.entry_id === tx.operation_id, "outbox_receipt_unproved", "partition history is not continuous");
    history.set(Number(rc.seq), tx);
  }
  if (r.cursor !== null) {
    const anchor = history.get(Number(r.cursor.last_seq));
    ensure(anchor !== undefined && anchor.operation_id === r.cursor.last_entry_id && anchor.cursor === r.cursor.last_cursor &&
      anchor.provider_revision === r.cursor.last_provider_revision &&
      partitionDigest(anchor, r.partition) === r.cursor.last_partition_digest,
    "outbox_cursor_unproved", "cursor has no exact history anchor");
  }
  const verified = new Set<string>();
  for (const entry of r.entries) {
    const tx = history.get(entry.seq);
    if (tx === undefined) {
      ensure(entry.prepared, "outbox_file_invalid", "unproved committed-only residue");
      ensure(entry.capture_lineage_id === r.capture_lineage_id, "stale_generation", "outbox entry belongs to another lineage");
      continue;
    }
    const rc = receipt(tx)!;
    ensure(rc.entry_id === entry.entry_id && rc.seq === entry.seq && rc.partition === r.partition &&
      rc.capture_lineage_id === r.capture_lineage_id, "outbox_receipt_mismatch", "entry does not match its receipt");
    for (const key of ["prepared_sha256", "committed_sha256"] as const) {
      ensure(entry[key] === null || entry[key] === rc[key], "outbox_receipt_mismatch", "outbox bytes differ from the receipt");
    }
    verified.add(entry.entry_id);
  }
  const ack = r.acknowledgement;
  if (ack !== null) {
    const tx = history.get(Number(ack.seq));
    ensure(tx !== undefined && tx.operation_id === ack.entry_id && tx.cursor === ack.cursor &&
      tx.provider_revision === ack.provider_revision && view.store_identity === ack.store_identity &&
      receipt(tx)?.no_op === ack.no_op && receipt(tx)?.partition_digest === ack.partition_digest,
    "shadow_commit_entry_result_invalid", "ACK differs from exact receipt");
  }
  // Budget affects effects only. A corrupt tail still rejects the whole plan.
  const recovered = r.entries.filter(e => history.has(e.seq) && e.entry_id !== ack?.entry_id);
  const selected = r.budget_open ? recovered.slice(0, r.remaining_entries) : [];
  const selectedIds = new Set(selected.map(e => e.entry_id));
  if (r.budget_open && ack !== null) selectedIds.add(String(ack.entry_id));
  const last = history.get(history.size);
  const update = r.budget_open && last !== undefined && (r.cursor === null || r.cursor.last_seq !== history.size)
    ? {last_seq: history.size, last_entry_id: last.operation_id, last_partition_digest: partitionDigest(last, r.partition),
      last_cursor: last.cursor, last_provider_revision: last.provider_revision} : null;
  return {schema_version: SHADOW_DRAIN_PLAN_RESULT_SCHEMA, status: "planned", goal_id: r.goal_id, partition: r.partition,
    view: compactView(view),
    next_seq: history.size + 1, history_present: history.size > 0,
    budget_exhausted: !r.budget_open || recovered.length > r.remaining_entries,
    cursor_update: update, reclaim_entry_ids: [...selectedIds].filter(id => verified.has(id)),
    pending_entry_ids: r.budget_open ? r.entries.filter(e => !history.has(e.seq)).map(e => e.entry_id) : [],
    replay_entries: selected.map(e => {
      const tx = history.get(e.seq)!, rc = receipt(tx)!;
      return {entry_id: e.entry_id, partition: r.partition, seq: e.seq, resolution: rc.resolution,
        outcome: "replayed", reason_code: "verified_receipt_recovery", cursor: tx.cursor,
        provider_revision: tx.provider_revision, partition_digest: rc.partition_digest, no_op: rc.no_op};
    })};
}

/** Native proof read for the batch owner; complete history stays in TypeScript.
 * The caller holds M; taking it again here would deadlock. */
export async function readShadowDrainPlan(value: unknown,
  dependencies: LocalAuthorityShadowDependencies = {}): Promise<JsonObject> {
  let verifiedView: JsonObject | null = null;
  try {
    const r = decode(value);
    const view = await readLocalAuthorityShadow({schema_version: LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
      runtime_root: r.runtime_root, goal_id: r.goal_id, store_kind: "runtime_shadow", scan_after_cursor: null,
      scan_limit: 10000, receipt_operation_id: null, read_model: "proof"}, dependencies);
    provedTransactions(r, view);
    verifiedView = compactView(view);
    return planShadowDrain(value, view);
  } catch (error) {
    return {schema_version: SHADOW_DRAIN_PLAN_RESULT_SCHEMA, status: "failed",
      view: verifiedView,
      reason_code: error instanceof DrainPlanError ? error.reasonCode :
        error instanceof OutboxCursorError ? error.code : "shadow_drain_request_invalid"};
  }
}
