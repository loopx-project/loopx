/** Local transport binds the existing provider and writer fence to the mode transaction. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireStringArray} from "../runtime_decode.ts";
import {requireAuthorityStoreId} from "./authority_store_codec.ts";
import {openLocalAuthorityStore, localAuthorityOpenFailure} from "./local_authority_provider.ts";
import {requireLocalAuthorityRuntimeRoot as runtimeRoot} from "./local_authority_provider.ts";
import {authorityStoreSourceAuthority as sourceAuthorityFor} from "./authority_store.ts";
import {withCanonicalWriter} from "./local_authority_write.ts";
import {ShadowManagementError} from "./shadow_management.ts";
import {executeHandoffModeSet, HANDOFF_MODE_SET_SCHEMA} from "./handoff_mode_transaction.ts";
import {planHandoffModeMigration, executeHandoffModeMigration} from "./handoff_mode_migration.ts";
import {requirePromotionRegisteredAgents, withShadowRegistrySource} from "./shadow_registry_source.ts";

export async function setLocalHandoffMode(value: unknown): Promise<JsonObject> {
  const evidence = {source_authority: "file_v0", decision_read_from_provider: true, legacy_fallback_used: false};
  try {
    const input = requireJsonObject(value, "handoff mode set request");
    if (input.schema_version !== HANDOFF_MODE_SET_SCHEMA) throw new Error("handoff mode request schema mismatch");
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    return await withCanonicalWriter(root, goalId, input.dry_run === true, async () => {
      const store = await openLocalAuthorityStore(root, goalId);
      evidence.source_authority = sourceAuthorityFor(store);
      return {...await executeHandoffModeSet(store, {goal_id: goalId,
        operation_id: input.operation_id as string, requested_mode: input.requested_mode as string,
        observed_at: input.observed_at as string, dry_run: input.dry_run as boolean}), ...evidence};
    });
  } catch (error) {
    return {schema_version: "loopx_coordination_handoff_mode_set_result_v0", status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "handoff_mode_unavailable",
      reason: error instanceof Error ? error.message : String(error), ...evidence, ...localAuthorityOpenFailure(error)};
  }
}

/** Administrative IO uses the same selected provider and maintenance guard. */
export async function migrateLocalHandoffMode(value: unknown): Promise<JsonObject> {
  let sourceAuthority = "canonical_unavailable";
  try {
    const input = requireJsonObject(value, "handoff mode migration request");
    if (input.schema_version !== "loopx_handoff_mode_migration_request_v0") throw new Error("migration request schema mismatch");
    const root = runtimeRoot(input.runtime_root);
    const goalId = requireAuthorityStoreId(input.goal_id, "goal id");
    const common = {goal_id: goalId, runtime_root: root, plan: input.plan as string,
      registered_agents: requireStringArray(input.registered_agents, "registered_agents"), observed_at: input.observed_at as string};
    const snapshot = {registry_source: input.registry_source};
    requirePromotionRegisteredAgents(snapshot, common.registered_agents);
    return await withCanonicalWriter(root, goalId, input.execute !== true, async () => withShadowRegistrySource(snapshot, async () => {
      const store = await openLocalAuthorityStore(root, goalId);
      sourceAuthority = sourceAuthorityFor(store);
      const result = input.action === "plan-migration"
        ? await planHandoffModeMigration(store, {...common, requested_mode: input.requested_mode as string})
        : input.action === "migrate"
          ? await executeHandoffModeMigration(store, {...common, execute: input.execute as boolean, plan_sha256: input.plan_sha256 as string})
          : (() => { throw new Error("unknown migration action"); })();
      return {...result, source_authority: sourceAuthority, decision_read_from_provider: true, legacy_fallback_used: false};
    }));
  } catch (error) {
    return {schema_version: "loopx_handoff_mode_migration_result_v0", status: "failed", changed: false,
      reason_code: error instanceof ShadowManagementError ? error.reason_code : "handoff_mode_migration_unavailable",
      reason: error instanceof Error ? error.message : String(error), source_authority: sourceAuthority,
      execution_authority_granted: false, legacy_fallback_used: false, ...localAuthorityOpenFailure(error)};
  }
}
