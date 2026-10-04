/** Fresh local authority creation; existing Goals use reviewed migration.
 * The original creation receipt remains valid after later Todo writes.
 */
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeConflictError, EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {LocalAuthorityProviderOpenError, openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot, selectLocalAuthorityTarget} from "./local_authority_provider.ts";
import {engageLegacyCoordinationWriterFenceUnderLocks, completeNewGoalWriterFenceUnderLocks, loadLegacyCoordinationWriterFence, NEW_GOAL_WRITER_FENCE_SCHEMA} from "./legacy_writer_fence.ts";
import {readShadowManagementState, withShadowMaintenanceLock} from "./shadow_management.ts";
import {verifyShadowSourceSnapshot, withShadowSourceLocks, type ShadowRequest} from "./runtime_shadow.ts";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA} from "./source_projection.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA} from "./coordination_state_contract.ts";
import {commitPromotionAndReadBack, readPromotionReceipt} from "./promotion_receipt.ts";
import {requireJsonObject} from "../runtime_decode.ts";

export async function initializeNewGoalAuthority(raw: JsonObject): Promise<JsonObject> {
  const root = requireLocalAuthorityRuntimeRoot(raw.runtime_root);
  const goalId = requireAuthorityStoreId(raw.goal_id, "goal id");
  const operationId = requireAuthorityStoreId(raw.creation_operation_id, "creation operation id");
  const target = requireJsonObject(raw.target, "creation target");
  const snapshot = requireJsonObject(raw.source_snapshot, "creation source snapshot");
  const projection = projectCoordinationSource({schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA,
    kind: "snapshot", goal_id: goalId, handoff_mode: target.handoff_mode, todos: [], leases: [],
    read_model_schema: TODO_DOMAIN_READ_RECORD_SCHEMA}).projection as JsonObject;
  const capturedSource = raw.projection !== undefined;
  const request: ShadowRequest = {runtime_root: root, goal_id: goalId,
    projection: capturedSource ? requireJsonObject(raw.projection, "creation source") : projection, source_snapshot: snapshot};
  const identitySha = canonicalAuthoritySha256({goal_id: goalId, creation_operation_id: operationId,
    target, state_path: snapshot.state_path, registry_path: (snapshot.registry_source as JsonObject)?.path});
  const identity = {operation_id: operationId, projection_sha256: canonicalAuthoritySha256(projection),
    receipt: {schema_version: "loopx_new_goal_authority_creation_receipt_v0", operation_id: operationId,
      goal_id: goalId, creation_identity_sha256: identitySha}};
  const fence = {schema_version: NEW_GOAL_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goalId,
    fence_id: `new-goal:${operationId}`, creation_operation_id: operationId, creation_identity_sha256: identitySha,
    creation_completed: false};
  const validateRegistration = async () => {
    const registry = JSON.parse(await readFile(String((snapshot.registry_source as JsonObject).path), "utf8"));
    const registered = registry.goals?.find((goal: JsonObject) => goal.id === goalId);
    if (!registered || registered.creation_operation_id !== operationId ||
        !canonicalAuthorityBytes(registered.coordination?.storage_target).equals(canonicalAuthorityBytes(target)) ||
        typeof registered.repo !== "string" || typeof registered.state_file !== "string" ||
        resolve(registered.repo, registered.state_file) !== resolve(String(snapshot.state_path))) {
      throw new EffectRuntimeRequestError("Canonical creation is not bound to the registered original operation, source and target");
    }
  };
  await validateRegistration();
  const priorFence = await loadLegacyCoordinationWriterFence(root, goalId);
  if (priorFence.status === "failed") throw new EffectRuntimeConflictError(priorFence.reason);
  if (capturedSource && priorFence.status === "missing" && ((request.projection.todos as unknown[])?.length !== 0 ||
      (request.projection.leases as unknown[])?.length !== 0 || (snapshot.lease_inventory as unknown[])?.length !== 0)) {
    throw new EffectRuntimeRequestError("Canonical creation requires an empty source without lease history; use reviewed migration");
  }

  // Selection retains the established provider/lineage checks. It cannot replace
  // a selected store, and later reviewed migration wins over creation defaults.
  if (capturedSource) await selectLocalAuthorityTarget(root, goalId, target.provider as "file" | "sqlite", true, "creation_retry");
  return await withShadowMaintenanceLock(root, goalId, () => withShadowSourceLocks(request, async () => {
    await validateRegistration();
    const managed = await readShadowManagementState(root, goalId);
    if (managed?.status === "active") throw new EffectRuntimeConflictError("Existing shadow capture requires reviewed migration");
    const opened = await openLocalAuthorityStoreHandle(root, goalId).catch((error: unknown) => {
      if (error instanceof LocalAuthorityProviderOpenError) {
        throw new EffectRuntimeConflictError(`${error.message}; restore the complete authority backup, never recreate it`);
      }
      throw error;
    });
    const head = await opened.store.loadAuthority();
    const persisted = await loadLegacyCoordinationWriterFence(root, goalId);
    if (persisted.status === "failed") throw new EffectRuntimeConflictError(persisted.reason);
    if (persisted.status === "loaded" && !canonicalAuthorityBytes({...persisted.fence, creation_completed: false}).equals(canonicalAuthorityBytes(fence))) {
      throw new EffectRuntimeConflictError("Canonical creation writer fence belongs to a different operation");
    }
    let readback = await readPromotionReceipt(opened.store, identity);
    if (!readback.matched) {
      if (persisted.status === "loaded" && persisted.fence.creation_completed === true) {
        throw new EffectRuntimeConflictError("Completed canonical creation authority is unavailable; restore its complete backup, never recreate it");
      }
      if (head.status !== "missing") throw new EffectRuntimeConflictError(`Canonical creation cannot replace authority: ${readback.reason_code}`);
      if (!capturedSource) return {source_capture_required: true};
      // A creation intent is never a migration waiver. Validate the complete
      // real source and inventory under its locks; nonempty sources fail closed.
      await verifyShadowSourceSnapshot(request);
      if ((request.projection.todos as unknown[])?.length !== 0 ||
          (request.projection.leases as unknown[])?.length !== 0 ||
          (snapshot.lease_inventory as unknown[])?.length !== 0) {
        throw new EffectRuntimeRequestError("Canonical creation requires an empty source without lease history; use reviewed migration");
      }
      const fenced = await engageLegacyCoordinationWriterFenceUnderLocks(root, goalId, String(snapshot.state_path), fence);
      if (fenced.status !== "applied" && fenced.status !== "replayed") throw new EffectRuntimeConflictError("Canonical creation writer fence could not be verified");
      const committed = await commitPromotionAndReadBack(opened.store, identity, projection,
        {...identity.receipt, schema_version: "loopx_new_goal_authority_creation_event_v0"});
      readback = committed.readback;
      if (!readback.matched) throw new EffectRuntimeConflictError(`Canonical creation interrupted; retry its original operation: ${readback.reason_code}`);
    }
    if (persisted.status === "missing" && head.status === "loaded") {
      throw new EffectRuntimeConflictError("Canonical creation receipt exists but its writer fence is missing; restore the complete authority backup");
    }
    await completeNewGoalWriterFenceUnderLocks(root, goalId, fence);
    return {ok: true, provider: opened.provider, status: head.status === "missing" ? "created" : "replayed",
      authority_initialized: true, handoff_mode: target.handoff_mode, applies_to: "canonical_authority",
      promotion_performed: false, legacy_writer_fenced: true, legacy_fallback_used: false,
      provider_revision: readback.provider_revision, cursor: readback.cursor, creation_operation_id: operationId};
  }));
}
