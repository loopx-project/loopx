/** Read-only complete old-source inventory. This is neither a reviewed import
 * plan nor evidence of stopped Hosts, settled effects or qualified shadow. */
import {readFile, lstat} from "node:fs/promises";
import {dirname, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityObject, canonicalAuthoritySha256} from "./authority_store_codec.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {decodeRuntimeShadowRequest, verifyShadowSourceSnapshot, withShadowSourceLocks} from "./runtime_shadow.ts";
import {loadLegacyCoordinationWriterFence} from "./legacy_writer_fence.ts";
import {withShadowMaintenanceLock, ShadowManagementError, readShadowManagementState,
  readRetainedShadowArtifacts, shadowManagementDirectory, requireShadowCaptureBinding} from "./shadow_management.ts";
import {readLocalAuthorityShadow, LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA} from "./local_authority_shadow.ts";
import {drainInventory} from "./shadow_drain_files.ts";
import {planShadowDrain, SHADOW_DRAIN_PLAN_REQUEST_SCHEMA} from "./shadow_drain_plan.ts";
import {decodeLocalAuthoritySelection, localAuthorityProviderPaths, openLocalAuthorityStoreHandle} from "./local_authority_provider.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";

export const COLD_SOURCE_INSPECTION_REQUEST_SCHEMA = "loopx_cold_source_inspection_request_v0";
export const COLD_SOURCE_INSPECTION_RESULT_SCHEMA = "loopx_cold_source_inspection_result_v0";

/** Observe the existing drain owner's decisions without publishing a cursor,
 * replaying an entry or reclaiming bytes. Failure keeps the raw inventory;
 * an unknown original cannot be silently classified as settled. M is held. */
async function reviewRetainedOutbox(root: string, goal: string, view: JsonObject): Promise<JsonObject> {
  const boundary = {executed: false, execution_authority_granted: false};
  try {
    const binding = await requireShadowCaptureBinding(root, goal);
    const partitions: JsonObject[] = [];
    for (const partition of ["todos", "leases"] as const) {
      const inventory = await drainInventory(root, goal, partition);
      partitions.push(planShadowDrain({schema_version: SHADOW_DRAIN_PLAN_REQUEST_SCHEMA,
        runtime_root: root, goal_id: goal, partition,
        capture_lineage_id: binding.capture_lineage_id, store_identity: binding.store_identity,
        source_root_digest: binding.source_root_digest, cursor: inventory.cursor, entries: inventory.entries,
        remaining_entries: inventory.entries.length, budget_open: true, acknowledgement: null}, view));
    }
    return {status: "planned", ...boundary, partitions};
  } catch (error) {
    const failure = error as {reasonCode?: string; reason_code?: string; code?: string};
    return {status: "failed", ...boundary,
      reason_code: failure.reasonCode ?? failure.reason_code ?? failure.code ?? "shadow_drain_request_invalid"};
  }
}

export async function inspectColdCoordinationSource(value: unknown): Promise<JsonObject> {
  try {
    const request = decodeRuntimeShadowRequest(value, COLD_SOURCE_INSPECTION_REQUEST_SCHEMA);
    const management = shadowManagementDirectory(request.runtime_root, request.goal_id);
    // The registered runtime may have a supported path alias. Its artifact
    // subdirectories must not redirect a read or a maintenance lock elsewhere.
    for (const path of [join(request.runtime_root, "authority-shadow"),
      join(request.runtime_root, "authority-shadow", "file"),
      join(request.runtime_root, "authority-shadow", "outbox"),
      join(request.runtime_root, "authority-transition"), dirname(management), management]) {
      try {
        if (!(await lstat(path)).isDirectory()) throw new ShadowManagementError("shadow_outbox_layout_invalid");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
    }
    return await withShadowMaintenanceLock(request.runtime_root, request.goal_id, () =>
      withShadowSourceLocks(request, async () => {
        const fence = await loadLegacyCoordinationWriterFence(request.runtime_root, request.goal_id);
        if (fence.status !== "missing") throw new ShadowManagementError(
          fence.status === "loaded" ? "legacy_authority_already_promoted" : fence.reason_code);
        const paths = localAuthorityProviderPaths(request.runtime_root, request.goal_id);
        // A reviewed preview may bind an empty SQLite target before cutover.
        // A selector alone is not a canonical head. Reuse the provider owner
        // to verify its existing identity; never create or fall back on error.
        let selected: string | null = null;
        try { selected = await readFile(paths.marker, "utf8"); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
        if (selected !== null) {
          let selection: ReturnType<typeof decodeLocalAuthoritySelection>;
          try { selection = decodeLocalAuthoritySelection(JSON.parse(selected), request.goal_id); }
          catch { throw new ShadowManagementError("cold_source_canonical_authority_present"); }
          if (selection.provider !== "sqlite") throw new ShadowManagementError("cold_source_canonical_authority_present");
          const opened = await openLocalAuthorityStoreHandle(request.runtime_root, request.goal_id, {}, {existingOnly: true});
          if ((await opened.store.loadAuthority()).status !== "missing") {
            throw new ShadowManagementError("cold_source_canonical_authority_present");
          }
        }
        for (const path of [new FileAuthorityStore(paths.file, request.goal_id, {existingOnly: true}).path]) {
          try { await lstat(path); }
          catch (error) {
            if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
            throw error;
          }
          throw new ShadowManagementError("cold_source_canonical_authority_present",
            "Use the canonical provider's storage/recovery path; Markdown is no longer an import source");
        }
        await verifyShadowSourceSnapshot(request);
        const artifacts = await readRetainedShadowArtifacts(request.runtime_root, request.goal_id);
        const managementState = await readShadowManagementState(request.runtime_root, request.goal_id);
        if (managementState?.status === "active" && artifacts.runtime_store === null) {
          throw new ShadowManagementError("provider_read_unavailable");
        }
        async function historyReadback(storeKind: "runtime_shadow" | "legacy_observation", present: boolean): Promise<JsonObject | null> {
          if (!present) return null;
          const result = await readLocalAuthorityShadow({
            schema_version: LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
            runtime_root: request.runtime_root, goal_id: request.goal_id,
            store_kind: storeKind, read_model: "proof",
            scan_limit: storeKind === "runtime_shadow" && managementState?.status === "active" ? 10000 : 0,
          });
          if (result.status !== "loaded") throw new ShadowManagementError(String(result.reason_code ?? "provider_read_unavailable"));
          return result;
        }
        const runtimeReadback = await historyReadback("runtime_shadow", artifacts.runtime_store !== null);
        const legacyReadback = await historyReadback("legacy_observation", artifacts.legacy_store !== null);
        // Inactive/interrupted capture must recover its own management operation
        // first. A directory alone cannot supply the missing lineage authority.
        const outboxReview = managementState?.status === "active" && runtimeReadback !== null
          ? await reviewRetainedOutbox(request.runtime_root, request.goal_id, runtimeReadback) : null;
        // Full proof is an internal input to both partition plans, not extra
        // response history. Preserve the existing compact readback boundary.
        if (runtimeReadback !== null) (runtimeReadback.proof as JsonObject).transactions = [];
        const retainedLeases: JsonObject[] = [];
        for (const entry of request.source_snapshot.lease_inventory as JsonObject[]) {
          const name = String(entry.name);
          // Source verification binds every file to its Goal and filename. Keep
          // historical bytes separate from the graph's current execution edges.
          const lease = canonicalAuthorityObject(JSON.parse(await readFile(
            join(request.runtime_root, "goals", request.goal_id, "task-leases", name), "utf8")), "retained lease");
          canonicalTaskLease(lease, request.goal_id, name.slice(0, -5));
          retainedLeases.push(lease);
        }
        await verifyShadowSourceSnapshot(request);
        if (canonicalAuthoritySha256(artifacts) !== canonicalAuthoritySha256(
          await readRetainedShadowArtifacts(request.runtime_root, request.goal_id))) {
          throw new ShadowManagementError("source_changed_retry");
        }
        const todos = request.projection.todos as JsonObject[];
        return {
          schema_version: COLD_SOURCE_INSPECTION_RESULT_SCHEMA, status: "inspected",
          goal_id: request.goal_id, executed: false, import_ready: false,
          writer_stop_verified: false, outbox_reconciliation_verified: false,
          active_todo_count: todos.filter(todo => todo.archive_state === "active").length,
          archived_todo_count: todos.filter(todo => todo.archive_state === "archive").length,
          lease_file_count: retainedLeases.length,
          // Expiry revokes execution; it never proves the writer process stopped.
          leases_requiring_settlement: retainedLeases.filter(lease => lease.status === "active")
            .map(lease => lease.todo_id),
          retained_leases: retainedLeases,
          capture: {management_state: managementState, artifacts,
            runtime_shadow_readback: runtimeReadback, legacy_observation_readback: legacyReadback,
            outbox_review: outboxReview},
          projection: request.projection, source_snapshot: request.source_snapshot,
          decision_read_from_shadow: false,
        };
      }));
  } catch (error) {
    return {schema_version: COLD_SOURCE_INSPECTION_RESULT_SCHEMA, status: "failed",
      executed: false, import_ready: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "cold_source_inspection_invalid",
      reason: error instanceof Error ? error.message : "cold source inspection failed"};
  }
}

/** App read model of the same verified observation. No source bytes, paths,
 * execution identities or original receipts cross the HTTP boundary. */
export async function inspectColdCoordinationStorage(value: unknown): Promise<JsonObject> {
  const observed = await inspectColdCoordinationSource(value);
  const base = {ok: observed.status === "inspected", status: observed.status,
    authority_changed: false, execution_authority_granted: false};
  if (observed.status !== "inspected") return {...base, current: null, reason_code: observed.reason_code};
  const capture = observed.capture as JsonObject;
  const artifacts = capture.artifacts as JsonObject;
  const outbox = artifacts.outbox as JsonObject | null;
  const outboxInventory = outbox?.inventory as JsonObject | undefined;
  return {...base, current: {goal_id: observed.goal_id, canonical: false, provider: null},
    cold_source: {
      active_todo_count: observed.active_todo_count, archived_todo_count: observed.archived_todo_count,
      lease_file_count: observed.lease_file_count,
      unsettled_lease_count: (observed.leases_requiring_settlement as string[]).length,
      capture_artifacts_present: artifacts.management_state !== null || artifacts.runtime_store !== null ||
        artifacts.legacy_store !== null || (artifacts.rollback_archives as JsonObject[]).length > 0,
      outbox_files_present: ((outboxInventory?.entries ?? []) as JsonObject[]).some(entry => entry.kind === "file"),
      import_ready: false, writer_stop_verified: false, outbox_reconciliation_verified: false,
    }};
}
