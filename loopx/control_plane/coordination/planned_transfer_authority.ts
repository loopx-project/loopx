/** Authority preparation for the planned-transfer composition.
 *
 * This owner closes canonical writes and pins the complete native journal. It
 * does not certify Host/effect drain, portable-artifact custody or destination
 * admission. The product coordinator must obtain those owners' decisions before
 * exposing a cutover. No public activation entry is registered here.
 */
import {mkdir, readFile} from "node:fs/promises";
import {isAbsolute, join, resolve} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {exportAuthorityArchive, verifyAuthorityArchive, type AuthorityArchiveSummary} from "./authority_archive.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {openLocalAuthorityStoreHandle, requireLocalAuthorityRuntimeRoot} from "./local_authority_provider.ts";
import {loadLegacyCoordinationWriterFence, preparePlannedTransferWriterFenceUnderLock,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA} from "./legacy_writer_fence.ts";
import {readShadowManagementState, shadowManagementDirectory, withShadowMaintenanceLock} from "./shadow_management.ts";
import {PREPARATION_SCHEMA, withVerifiedGoalTransferContext} from "../goals/planned_transfer.ts";
import {reviewProtectedGoalTransferInputs, writeProtectedGoalTransfer} from "../goals/planned_transfer_payload.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {newGoalCreationBinding} from "./new_goal_initialization.ts";
import {readPromotionReceipt} from "./promotion_receipt.ts";
import {NEW_GOAL_WRITER_FENCE_SCHEMA} from "./legacy_writer_fence.ts";
import {readGoalTurnHistoryForTransfer} from "../turn_driver/turn_journal_query.ts";
import {readGoalQuotaHistoryForTransfer} from "../quota/accounting_artifact_transaction.ts";

export {PREPARATION_SCHEMA} from "../goals/planned_transfer.ts";
const REGISTERED_SOURCE_PREVIEW_SCHEMA = "loopx_planned_transfer_source_preview_v0";

/** The codec calls while holding its original registry transaction. Ordinary
 * metadata can no longer reopen, remove or redirect a prepared Goal. Changes
 * to other Goals remain legal; the registry is not frozen as a whole. This
 * consumes codec-decoded facts and a byte witness, never another JSON parser.
 */
export async function checkPlannedTransferRegistryWrite(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["before", "after", "registry_witness"])) reject("Registry transfer guard requires its codec observation");
  const before = requireJsonObject(value.before, "current registry"), after = requireJsonObject(value.after, "proposed registry");
  const witness = requireJsonObject(value.registry_witness, "registry codec witness");
  if (!hasExactAuthorityKeys(witness, ["path", "sha256"]) || typeof witness.path !== "string" ||
      !isAbsolute(witness.path) || resolve(witness.path) !== witness.path ||
      (witness.sha256 !== null && !BARE_SHA256_PATTERN.test(String(witness.sha256)))) reject("Invalid registry codec witness");
  const verify = async () => {
    let digest: string | null;
    try { digest = createHash("sha256").update(await readFile(String(witness.path))).digest("hex"); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; digest = null; }
    if (digest !== witness.sha256) reject("Registry codec observation changed");
  };
  await verify();
  // A global-local registry is a projection, never the actual source owner.
  if (before.registry_role !== "global-local") {
    const original = Array.isArray(before.goals) ? before.goals : [];
    const proposed = Array.isArray(after.goals) ? after.goals : [];
    const roots = new Set([before.common_runtime_root, after.common_runtime_root].filter(value => typeof value === "string"));
    const ids = new Set([...original, ...proposed].map(raw => requireAuthorityStoreId(requireJsonObject(raw, "Goal").id, "Goal id")));
    for (const rawRoot of roots) for (const goalId of ids) {
      const root = requireLocalAuthorityRuntimeRoot(rawRoot);
      const fence = await loadLegacyCoordinationWriterFence(root, goalId);
      if (fence.status === "failed") reject("Goal writer fence is unreadable; preserve its original registration");
      if (fence.status !== "loaded" || fence.fence.schema_version !== PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) continue;
      const matches = proposed.filter(row => row !== null && typeof row === "object" && !Array.isArray(row) && (row as JsonObject).id === goalId);
      const prior = original.filter(row => row !== null && typeof row === "object" && !Array.isArray(row) && (row as JsonObject).id === goalId);
      if (prior.length === 0 && fence.fence.role === "target" && matches.length === 1) {
        const directory = join(shadowManagementDirectory(root, goalId), "planned-transfer-targets", canonicalAuthoritySha256(fence.fence.operation_id));
        const registration = await optionalJson(join(directory, "registration.json")), receipt = await optionalJson(join(directory, "receipt.json"));
        if (registration && receipt && registration.identity_sha256 === fence.fence.source_identity_sha256 && registration.registry_path === witness.path &&
            receipt.identity_sha256 === fence.fence.source_identity_sha256 && receipt.target_is_active === false &&
            receipt.execution_authority_granted === false && after.common_runtime_root === root && same(registration.goal, matches[0]) &&
            after.profile_id === undefined && after.registry_role !== "global-local" && after.role !== "global-local" &&
            ["registry_role", "role", "profile_id"].every(key => same(before[key] ?? null, after[key] ?? null))) continue;
      }
      if (root !== before.common_runtime_root || prior.length !== 1 || matches.length !== 1 || !same(matches[0], prior[0]) ||
          ["common_runtime_root", "registry_role", "role", "profile_id"].some(key => !same(before[key] ?? null, after[key] ?? null))) {
        reject(`Goal registration is prepared for transfer ${fence.fence.operation_id}; recover that original operation`);
      }
    }
  }
  await verify();
  return {allowed: true};
}

function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
async function optionalJson(path: string): Promise<JsonObject | null> {
  try { return requireJsonObject(JSON.parse(await readFile(path, "utf8")), "transfer preparation"); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retain(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalJson(path);
  if (prior) {
    if (!same(prior, value)) reject("Transfer preparation belongs to different source facts; recover its original operation");
    return;
  }
  await durableWriteJson(path, value);
  if (!same(await optionalJson(path), value)) reject("Transfer preparation readback failed");
}

/** First product source profile: an ordinary registered local Goal whose
 * original creation is independently readable after subsequent native writes.
 * A retained creation receipt is provenance here, never a target creation grant.
 * Imported/legacy origins need their own original-registration qualification;
 * the lower-level archive/preparation owners remain available without claiming it.
 */
async function registeredSourcePreview(context: JsonObject, originalFence: JsonObject,
  store: AuthorityStore, provider: string, head: {head: JsonObject; cursor: string; provider_revision: string},
  storeIdentity: string, goalId: string, operationId: string): Promise<JsonObject> {
  const goal = requireJsonObject(context.goal, "original registered Goal");
  if (goal.status !== "active") reject("Transfer requires the current registered Goal; recover its original lifecycle before preparing a move");
  const coordination = requireJsonObject(goal.coordination, "original Goal coordination");
  const target = requireJsonObject(coordination.storage_target, "original Goal storage target");
  if (originalFence.schema_version !== NEW_GOAL_WRITER_FENCE_SCHEMA || originalFence.creation_completed !== true ||
      typeof goal.creation_operation_id !== "string" || target.schema_version !== "loopx_new_goal_storage_target_v1") {
    reject("This transfer source requires its qualified original registration; imported origins need their owning provenance check");
  }
  const binding = newGoalCreationBinding(goalId, goal.creation_operation_id, target,
    resolve(String(goal.repo), String(goal.state_file)), context.registry_path);
  if (!same(originalFence, {...binding.fence, creation_completed: true}) ||
      !(await readPromotionReceipt(store, binding.identity)).matched) {
    reject("Transfer source differs from its original registered creation and retained receipt; repair that source without recreating it");
  }
  const index = indexCoordinationProjection(head.head, goalId);
  const retainedLeases = [...index.leases].map(([todo, lease]) => canonicalTaskLease(lease, goalId, todo))
    .filter(lease => lease.status !== "released");
  const turnHistory = await readGoalTurnHistoryForTransfer(String(context.runtime_root), goalId);
  const quotaHistory = await readGoalQuotaHistoryForTransfer(String(context.runtime_root), goalId);
  const facts = {schema_version: REGISTERED_SOURCE_PREVIEW_SCHEMA, goal_id: goalId, operation_id: operationId,
    source_context_sha256: canonicalAuthoritySha256(context), original_registration_verified: true,
    original_writer_fence_sha256: canonicalAuthoritySha256(originalFence), provider,
    provider_revision: head.provider_revision, cursor: head.cursor, source_store_identity: storeIdentity,
    retained_leases: retainedLeases, turn_history: turnHistory, quota_history: quotaHistory,
    ready_for_authority_preparation: retainedLeases.length === 0 && turnHistory.closed === true &&
      turnHistory.turn_hosts_closed === true && quotaHistory.closed === true,
    host_drain_qualified: false, portable_artifact_qualified: false, execution_authority_granted: false};
  return {...facts, preview_sha256: canonicalAuthoritySha256(facts)};
}

export async function previewRegisteredAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "source_context", "registry_witness"])) {
    reject("Registered source preview requires its complete original Goal context");
  }
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goalId = requireAuthorityStoreId(value.goal_id, "source Goal id");
  const operationId = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  return withShadowMaintenanceLock(root, goalId, () => withVerifiedGoalTransferContext(
    value.source_context, value.registry_witness, goalId, root, async context => {
      const managed = await readShadowManagementState(root, goalId);
      if (managed && managed.status !== "inactive") reject("Source capture requires disposition before transfer preview");
      const current = await loadLegacyCoordinationWriterFence(root, goalId);
      if (current.status !== "loaded") reject("Original source writer fence is unavailable");
      let original = current.fence;
      if (original.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) {
        const pending = await optionalJson(join(shadowManagementDirectory(root, goalId), "planned-transfers",
          canonicalAuthoritySha256(operationId), "preparation.json"));
        if (!pending || original.role !== "source" || original.operation_id !== operationId ||
            original.source_identity_sha256 !== canonicalAuthoritySha256(pending)) reject("Source belongs to another prepared transfer; recover its original operation");
        original = requireJsonObject(pending.original_writer_fence, "original source fence");
      }
      const opened = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
      if (opened.provider !== "file" && opened.provider !== "sqlite") reject("Transfer preview requires a qualified local File/SQLite source");
      const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
      if (head.status !== "loaded" || identity.status !== "available") reject("Original source history is unavailable; restore it before transfer");
      return registeredSourcePreview(context, original, opened.store, opened.provider, head, identity.store_identity, goalId, operationId);
    }));
}

/** The maintenance guard serializes preparation against every ordinary native
 * writer. Expiration does not settle a lease: every retained token must have
 * been released through its original owner before this operation can fence it.
 *
 * Retrying the same operation recovers the original capture after interruption.
 * It never reopens the source or rewrites a receipt following later changes.
 */
export async function prepareAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  const contextBound = value.source_context !== undefined;
  const registered = value.registered_source_preview_sha256 !== undefined;
  const protectedExport = value.protected_transport !== undefined;
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id", "expected_provider_revision",
      "expected_writer_fence_sha256", "execute", ...(contextBound ? ["source_context", "registry_witness"] : []),
      ...(registered ? ["registered_source_preview_sha256"] : []),
      ...(protectedExport ? ["protected_transport", "source_package_version"] : [])]) || value.execute !== true || (registered && !contextBound) ||
      (protectedExport && !registered) ||
      typeof value.expected_writer_fence_sha256 !== "string" || !BARE_SHA256_PATTERN.test(value.expected_writer_fence_sha256)) {
    reject("Transfer authority preparation requires an explicit execution and the complete reviewed source identity");
  }
  const root = requireLocalAuthorityRuntimeRoot(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "transfer Goal id");
  const operation = requireAuthorityStoreId(value.operation_id, "transfer operation id");
  const expectedRevision = requireAuthorityStoreId(value.expected_provider_revision, "reviewed source revision");
  const request = {goal_id: goal, operation_id: operation, expected_provider_revision: expectedRevision,
    expected_writer_fence_sha256: value.expected_writer_fence_sha256};
  const directory = join(shadowManagementDirectory(root, goal), "planned-transfers", canonicalAuthoritySha256(operation));
  return withShadowMaintenanceLock(root, goal, async () => {
    const prepare = async (context?: JsonObject) => {
    const managed = await readShadowManagementState(root, goal);
    if (managed && managed.status !== "inactive") reject("Source capture requires disposition before planned transfer");
    const current = await loadLegacyCoordinationWriterFence(root, goal);
    if (current.status !== "loaded") reject("Transfer requires the original verified canonical writer fence");
    const opened = await openLocalAuthorityStoreHandle(root, goal, {}, {existingOnly: true});
    if (opened.provider !== "file" && opened.provider !== "sqlite") reject("Planned transfer requires a qualified local File or SQLite source");
    const head = await opened.store.loadAuthority(), identity = await opened.store.storeIdentity();
    if (head.status !== "loaded" || identity.status !== "available") reject("Transfer source authority is unavailable; restore it before preparing a move");
    if (head.provider_revision !== expectedRevision) reject("Source authority changed; review a fresh transfer preview");
    const index = indexCoordinationProjection(head.head, goal);
    const leases = [...index.leases].map(([todo, lease]) => canonicalTaskLease(lease, goal, todo));
    if (leases.some(lease => lease.status !== "released")) {
      reject("Source retains an unreleased lease; settle its original operation before planned transfer");
    }
    const turnHistory = await readGoalTurnHistoryForTransfer(root, goal);
    if (turnHistory.closed !== true) reject("Source Turn effects are not closed; settle or recover their original operations before preparing transfer");
    if (turnHistory.turn_hosts_closed !== true) reject("Source Turn Hosts are not proven drained; recover their original process supervision before preparing transfer");
    const quotaHistory = await readGoalQuotaHistoryForTransfer(root, goal);
    if (quotaHistory.closed !== true) reject("Source quota effects are not closed; recover the original debit or void before preparing transfer");
    const pendingPath = join(directory, "preparation.json");
    const pending = await optionalJson(pendingPath);
    const facts = {schema_version: PREPARATION_SCHEMA, request,
      source_provider: opened.provider, source_store_identity: identity.store_identity,
      source_cursor: head.cursor, source_projection_sha256: canonicalAuthoritySha256(head.head),
      source_turn_history: turnHistory,
      source_quota_history: quotaHistory,
      ...(context ? {source_context: context, source_context_sha256: canonicalAuthoritySha256(context)} : {})};
    let priorFence: JsonObject;
    if (pending) {
      priorFence = requireJsonObject(pending.original_writer_fence, "original source writer fence");
      if (!same(pending, {...facts, original_writer_fence: priorFence})) reject("Transfer source or original operation changed");
      // A carrier is written before the fence, so an interrupted first call may
      // still find the exact original fence. Neither case permits other bytes.
      const expectedFence = {schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
        role: "source", fence_id: `planned-transfer:${operation}`, operation_id: operation, source_identity_sha256: canonicalAuthoritySha256(pending)};
      if (!same(current.fence, priorFence) && !same(current.fence, expectedFence)) reject("Source writer fence changed during transfer preparation");
    } else {
      if (current.fence.schema_version === PLANNED_TRANSFER_WRITER_FENCE_SCHEMA) reject("Source is already prepared by another transfer operation");
      priorFence = current.fence;
    }
    const preparation = {...facts, original_writer_fence: priorFence};
    if (canonicalAuthoritySha256(priorFence) !== value.expected_writer_fence_sha256) reject("Source writer fence changed; review a fresh transfer preview");
    if (registered) {
      const preview = await registeredSourcePreview(context!, priorFence, opened.store, opened.provider,
        head, identity.store_identity, goal, operation);
      if (value.registered_source_preview_sha256 !== preview.preview_sha256) reject("Reviewed transfer source preview changed; review the current source without executing it");
    }
    const protectedInputs = protectedExport ? await reviewProtectedGoalTransferInputs(value.protected_transport, context!, value.source_package_version) : undefined;
    await mkdir(directory, {recursive: true, mode: 0o700});
    await retain(pendingPath, preparation);
    await preparePlannedTransferWriterFenceUnderLock(root, goal, priorFence, {
      schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
      role: "source", fence_id: `planned-transfer:${operation}`, operation_id: operation,
      source_identity_sha256: canonicalAuthoritySha256(preparation),
    });
    // Export reuses the native retained-journal owner and its independent audit.
    // The fence stays closed on every error, including interrupted publication.
    const archive = join(directory, "authority.ndjson");
    let captured: AuthorityArchiveSummary;
    try { captured = await verifyAuthorityArchive(archive); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      captured = await exportAuthorityArchive(opened.store, goal, archive);
    }
    if (captured.goal_id !== goal || captured.source_store_identity !== identity.store_identity ||
        captured.source_provider_revision !== head.provider_revision || captured.commits !== head.cursor ||
        captured.projection_sha256 !== facts.source_projection_sha256) reject("Transfer archive differs from its prepared source");
    const result: JsonObject = {schema_version: PREPARATION_SCHEMA, operation_id: operation, goal_id: goal,
      authority_write_fenced: true, authority_archive: {...captured}, archive,
      ...(context ? {source_context_sha256: canonicalAuthoritySha256(context)} : {}),
      ...(registered ? {original_registration_verified: true} : {}),
      host_drain_qualified: false, portable_artifact_qualified: false, execution_authority_granted: false};
    await retain(join(directory, "receipt.json"), result);
    return protectedInputs ? {...result, protected_artifact: await writeProtectedGoalTransfer(protectedInputs,
      result, context!, join(directory, "encrypted-export-key.json"))} : result;
    };
    return contextBound ? withVerifiedGoalTransferContext(value.source_context, value.registry_witness, goal, root, prepare) : prepare();
  });
}
