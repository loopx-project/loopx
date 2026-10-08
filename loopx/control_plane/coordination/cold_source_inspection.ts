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
  readRetainedShadowArtifacts, shadowManagementDirectory} from "./shadow_management.ts";
import {readLocalAuthorityShadow, LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA} from "./local_authority_shadow.ts";
import {localAuthorityProviderPaths} from "./local_authority_provider.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";

export const COLD_SOURCE_INSPECTION_REQUEST_SCHEMA = "loopx_cold_source_inspection_request_v0";
export const COLD_SOURCE_INSPECTION_RESULT_SCHEMA = "loopx_cold_source_inspection_result_v0";

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
        for (const path of [paths.marker, new FileAuthorityStore(paths.file, request.goal_id, {existingOnly: true}).path]) {
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
            store_kind: storeKind, read_model: "proof", scan_limit: 0,
          });
          if (result.status !== "loaded") throw new ShadowManagementError(String(result.reason_code ?? "provider_read_unavailable"));
          return result;
        }
        const runtimeReadback = await historyReadback("runtime_shadow", artifacts.runtime_store !== null);
        const legacyReadback = await historyReadback("legacy_observation", artifacts.legacy_store !== null);
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
            runtime_shadow_readback: runtimeReadback, legacy_observation_readback: legacyReadback},
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
