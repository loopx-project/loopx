/** One bounded drain invocation. The host supplies configuration, never source
 * resolution, receipt proof or cleanup decisions. Candidate state is not authority. */
import {performance} from "node:perf_hooks";
import {isAbsolute, join} from "node:path";
import {existsSync} from "node:fs";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeLockTimeoutError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {readShadowManagementState, requireShadowCaptureBinding, shadowMaintenanceLockPath} from "./shadow_management.ts";
import {readShadowDrainPlan, SHADOW_DRAIN_PLAN_REQUEST_SCHEMA} from "./shadow_drain_plan.ts";
import {deliverShadowEntry, SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA} from "./shadow_entry_delivery.ts";
import {outboxPartitionDirectory, LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA} from "./local_authority_shadow_outbox.ts";
import {ShadowLineageError, type LocalAuthorityShadowDependencies} from "./local_authority_shadow.ts";
import {DrainKernelLockHost, drainInventory, reclaimDrainFiles, verifyDrainFiles, withDrainPrimary, type DrainPartition, type DrainEntry} from "./shadow_drain_files.ts";

export const SHADOW_DRAIN_SCHEMA = "loopx_shadow_drain_v0";
interface Request {runtime_root: string; goal_id: string; python_executable: string; config_enabled: boolean;
  max_entries: number; budget_seconds: number; lock_timeout_seconds: number}
interface Dependencies extends LocalAuthorityShadowDependencies {
  /** Scheduling-only fault seam; never accepted from a public request. */
  afterEffect?: (phase: "after_proof" | "before_commit" | "after_commit" | "after_cursor" | "after_unlink") => Promise<void>;
}
function decode(value: unknown): Request {
  const r = requireJsonObject(value, "drain request");
  if (!hasExactAuthorityKeys(r, ["schema_version", "runtime_root", "goal_id", "python_executable", "config_enabled",
    "max_entries", "budget_seconds", "lock_timeout_seconds"]) || r.schema_version !== SHADOW_DRAIN_SCHEMA ||
    typeof r.runtime_root !== "string" || !isAbsolute(r.runtime_root) || r.runtime_root.includes("\0") ||
    typeof r.python_executable !== "string" || !isAbsolute(r.python_executable) || r.python_executable.includes("\0") ||
    typeof r.config_enabled !== "boolean" || !Number.isSafeInteger(r.max_entries) || Number(r.max_entries) < 1 ||
    [r.budget_seconds, r.lock_timeout_seconds].some(n => typeof n !== "number" || !Number.isFinite(n) || n < 0))
    throw new ShadowLineageError("shadow_drain_request_invalid");
  requireAuthorityStoreId(r.goal_id, "goal id");
  return r as unknown as Request;
}
function errorCode(error: unknown): string {
  if (error instanceof EffectRuntimeLockTimeoutError) return "drain_lock_busy";
  const e = error as {reason_code?: string; code?: string};
  return e.reason_code ?? e.code ?? "shadow_drain_failed";
}

export async function drainShadowOutbox(value: unknown, dependencies: Dependencies = {}): Promise<JsonObject> {
  const r = decode(value), root = r.runtime_root, goal = r.goal_id;
  const result = {goal_id: goal, outcome: "nothing_pending", config_enabled: r.config_enabled,
    delivered: 0, replayed: 0, reconciled: 0, no_op: 0, reseeded: 0, reclaimed_residue: 0,
    pending_after: 0, prepared_only_after: 0, in_flight_partitions: [] as string[], budget_exhausted: false,
    stopped_at: null as JsonObject | null, reason_code: null as string | null,
    store_identity: null as unknown, provider_revision: null as unknown, last_cursor: null as unknown,
    cursor_before: null as unknown, cursor_after: null as unknown, head_digest: null as unknown,
    candidate_readback_verified: null as boolean | null, entries: [] as JsonObject[]};
  const kernel = new DrainKernelLockHost(r.python_executable);
  let consumed = 0;
  const deadline = performance.now() + r.budget_seconds * 1000;
  const timeOpen = () => performance.now() < deadline;
  const finish = (): JsonObject => ({schema_version: SHADOW_DRAIN_SCHEMA, ...result,
    ok: ["drained", "nothing_pending"].includes(result.outcome) && result.stopped_at === null,
    drained_count: result.delivered + result.replayed + result.reconciled});
  const observe = (view: JsonObject) => {
    result.candidate_readback_verified = true;
    result.cursor_before ??= view.cursor;
    result.store_identity = view.store_identity; result.provider_revision = view.provider_revision;
    result.last_cursor = view.cursor; result.cursor_after = view.cursor; result.head_digest = view.head_digest;
  };
  const locked = <T>(operation: () => Promise<T>): Promise<T> =>
    withFileMutationLock(shadowMaintenanceLockPath(root, goal), operation,
      Math.min(r.lock_timeout_seconds * 1000, Math.max(0, deadline - performance.now())));
  try {
    const initial = await readShadowManagementState(root, goal);
    if (initial?.status !== "active") {
      if (r.config_enabled || initial !== null || existsSync(join(root, "authority-shadow", "outbox", goal))) {
        result.outcome = "stopped"; result.reason_code = initial && initial.status !== "inactive" ? "shadow_management_in_progress" : "bootstrap_required";
      }
      return finish();
    }
    const lineage = (await requireShadowCaptureBinding(root, goal)).capture_lineage_id;
    const binding = async () => {
      const active = await requireShadowCaptureBinding(root, goal);
      if (active.capture_lineage_id !== lineage) throw new ShadowLineageError("stale_generation");
      return active;
    };
    const reconcile = async (partition: DrainPartition, acknowledgement: JsonObject | null = null): Promise<DrainEntry[]> => {
      const active = await binding(), before = await drainInventory(root, goal, partition);
      const plan = await readShadowDrainPlan({schema_version: SHADOW_DRAIN_PLAN_REQUEST_SCHEMA, runtime_root: root, goal_id: goal,
        partition, capture_lineage_id: lineage, store_identity: active.store_identity, source_root_digest: active.source_root_digest,
        cursor: before.cursor, entries: before.entries, remaining_entries: Math.max(0, r.max_entries - consumed),
        budget_open: timeOpen(), acknowledgement}, dependencies);
      if (plan.view !== null && typeof plan.view === "object") observe(plan.view as JsonObject);
      if (plan.status !== "planned") throw new ShadowLineageError(String(plan.reason_code ?? "shadow_drain_result_invalid"));
      await dependencies.afterEffect?.("after_proof");
      // Proof time and OS lock acquisition cannot extend the caller's effect budget.
      if (!timeOpen()) {result.budget_exhausted = true; return [];}
      result.budget_exhausted ||= plan.budget_exhausted === true;
      if (plan.history_present) await withDrainPrimary(root, goal, partition, active, kernel, async () => {
        await binding();
        const current = await drainInventory(root, goal, partition);
        if (canonicalAuthoritySha256({cursor: current.cursor, entries: current.entries}) !==
            canonicalAuthoritySha256({cursor: before.cursor, entries: before.entries})) throw new ShadowLineageError("outbox_file_changed");
        await verifyDrainFiles([...before.files.values()].flat());
        if (!timeOpen()) {result.budget_exhausted = true; return;}
        if (plan.cursor_update !== null) {
          await durableWriteJson(join(outboxPartitionDirectory(root, goal, partition), "drain-cursor.json"), {
            schema_version: LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA, partition,
            ...plan.cursor_update as JsonObject, updated_at: new Date().toISOString(),
          });
          await dependencies.afterEffect?.("after_cursor");
        }
        const files = (plan.reclaim_entry_ids as string[]).flatMap(id => before.files.get(id) ?? []);
        result.reclaimed_residue += await reclaimDrainFiles(files, () => dependencies.afterEffect?.("after_unlink") ?? Promise.resolve());
        for (const entry of plan.replay_entries as JsonObject[]) {
          const {no_op: noOp, ...summary} = entry;
          result.no_op += Number(noOp === true); result.entries.push(summary); result.replayed++; consumed++;
        }
      });
      if (!timeOpen()) {result.budget_exhausted = true; return [];}
      const pending = new Set(plan.pending_entry_ids as string[]);
      const entries = before.entries.filter(e => pending.has(e.entry_id));
      if (entries.length && entries[0].seq !== plan.next_seq) throw new ShadowLineageError("outbox_sequence_gap");
      return entries;
    };
    for (const partition of ["todos", "leases"] as const) {
      while (timeOpen() && consumed < r.max_entries && result.stopped_at === null) {
        const entry = await locked(async () => {
          const pending = await reconcile(partition);
          if (!pending.length || !timeOpen() || consumed >= r.max_entries) return null;
          const next = pending[0];
          if (!next.prepared) throw new ShadowLineageError("outbox_file_invalid");
          if (next.committed_sha256 === null)
            await withDrainPrimary(root, goal, partition, await binding(), kernel, async () => {});
          return next;
        });
        if (entry === null) break;
        await dependencies.afterEffect?.("before_commit");
        if (!timeOpen()) {result.budget_exhausted = true; break;}
        const raw = await deliverShadowEntry({schema_version: SHADOW_ENTRY_DELIVERY_REQUEST_SCHEMA, runtime_root: root, goal_id: goal,
          partition, entry_id: entry.entry_id, seq: entry.seq, capture_lineage_id: entry.capture_lineage_id,
          prepared_sha256: entry.prepared_sha256, committed_sha256: entry.committed_sha256}, dependencies);
        consumed++; await dependencies.afterEffect?.("after_commit");
        if (!["delivered", "replayed", "ambiguous_reconciled"].includes(String(raw.outcome))) {
          result.stopped_at = {partition, seq: entry.seq, entry_id: entry.entry_id, outcome: raw.outcome,
            reason_code: raw.reason_code === "source_transaction_unproved" ? "outbox_source_unproved" : raw.reason_code};
          break;
        }
        await locked(() => reconcile(partition, {entry_id: entry.entry_id, seq: entry.seq, cursor: raw.cursor,
          provider_revision: raw.provider_revision, store_identity: raw.store_identity, no_op: raw.no_op, partition_digest: raw.partition_digest}));
        result.entries.push({entry_id: entry.entry_id, partition, seq: entry.seq, resolution: raw.resolution,
          outcome: raw.outcome, reason_code: raw.reason_code, cursor: raw.cursor, provider_revision: raw.provider_revision,
          partition_digest: raw.partition_digest});
        if (raw.outcome === "delivered") result.delivered++;
        else if (raw.outcome === "replayed") result.replayed++;
        else result.reconciled++;
        if (raw.no_op === true) result.no_op++;
      }
      if (result.stopped_at !== null) break;
    }
    if (result.stopped_at !== null) {
      result.outcome = "stopped"; result.reason_code = String(result.stopped_at.reason_code ?? result.stopped_at.outcome);
    } else {
      result.budget_exhausted ||= !timeOpen();
      result.outcome = consumed || result.budget_exhausted ? "drained" : "nothing_pending";
    }
  } catch (error) {
    result.reason_code = errorCode(error);
    result.outcome = result.reason_code === "drain_lock_busy" ? "drain_deferred" : "stopped";
  } finally {await kernel.close();}
  try {
    for (const partition of ["todos", "leases"] as const) {
      const inventory = await drainInventory(root, goal, partition);
      result.pending_after += inventory.entries.filter(e => e.committed_sha256 !== null).length;
      result.prepared_only_after += inventory.entries.filter(e => e.committed_sha256 === null).length;
    }
    result.budget_exhausted ||= consumed >= r.max_entries && result.pending_after + result.prepared_only_after > 0;
  } catch (error) {result.outcome = "stopped"; result.reason_code ??= errorCode(error);}
  return finish();
}
