/** Restore original authority into a stopped, fenced destination Goal.
 * No Host, credential, lease or effect admission is performed by this stage. */
import {copyFile, lstat, mkdir, open, readFile, readdir, realpath} from "node:fs/promises";
import {constants} from "node:fs";
import {dirname, join, parse} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, atomicWriteText, withFileMutationLock} from "../effect_runtime_io.ts";
import {EffectRuntimeConflictError} from "../effect_runtime_errors.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {planPreparedGoalStage, stagedGoalRegistry, type PreparedGoalStage} from "../goals/planned_transfer.ts";
import {withVerifiedGoalTransferPayload, type VerifiedGoalTransferPayload} from "../goals/planned_transfer_payload.ts";
import {readGoalTransferWorkspaceReview, retainGoalTransferWorkspace, reviewGoalTransferWorkspace,
  verifyGoalTransferWorkspaceReview} from "../goals/planned_transfer_workspace.ts";
import {inspectTransferFile, requirePhysicalTransferPath} from "../goals/planned_transfer_container.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {verifyAuthorityArchive, restoreAuthorityArchive} from "./authority_archive.ts";
import {auditAuthorityArchive} from "./authority_archive_audit.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {SqliteAuthorityStore} from "./sqlite_authority_store.ts";
import {localAuthorityProviderPaths, publishLocalAuthoritySelection} from "./local_authority_provider.ts";
import {engageLegacyCoordinationWriterFenceUnderLocks, loadLegacyCoordinationWriterFence,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, isPlannedTransferAdmissionFence, type PlannedTransferWriterFence} from "./legacy_writer_fence.ts";
import {readShadowManagementState, shadowManagementDirectory, withShadowMaintenanceLock} from "./shadow_management.ts";
import {decodeGoalTransferHistory, inspectGoalTransferHistory, restoreTransferValidationDeclarations, readGoalTransferHistoryJson, retainGoalTransferHistory, type ProtectedGoalTransferHistory} from "../goals/planned_transfer_history.ts";
import {PREPARATION_SCHEMA} from "./planned_transfer_authority.ts";
import {legacyCoordinationTodoLockPath} from "./legacy_writer_lock_paths.ts";
import {projectGoalQuotaHistoryForTransfer, readGoalQuotaHistoryForTransfer} from "../quota/accounting_artifact_transaction.ts";

function reject(message: string): never { throw new EffectRuntimeConflictError(message); }
function same(a: unknown, b: unknown): boolean { return canonicalAuthorityBytes(a).equals(canonicalAuthorityBytes(b)); }
async function optionalBytes(path: string): Promise<Buffer | null> {
  try { return await readFile(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
}
async function retainJson(path: string, value: JsonObject): Promise<void> {
  const prior = await optionalBytes(path);
  if (prior) { if (!same(JSON.parse(prior.toString("utf8")), value)) reject("Original transfer staging identity changed"); return; }
  await durableWriteJson(path, value);
  if (!same(JSON.parse((await readFile(path)).toString("utf8")), value)) reject("Transfer staging readback failed");
}
async function physicalAncestors(path: string): Promise<void> {
  let current = path;
  for (;;) {
    try { if (await realpath(current) !== current) reject("Transfer destination contains a linked ancestor"); return; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if (current === parse(current).root) reject("Transfer destination has no physical parent");
    current = dirname(current);
  }
}

async function verifyDestinationRegistry(target: JsonObject, witness: JsonObject): Promise<void> {
  if (!hasExactAuthorityKeys(witness, ["path", "sha256"]) || witness.path !== target.registry_path ||
      (witness.sha256 !== null && typeof witness.sha256 !== "string")) reject("Invalid destination registry witness");
  const bytes = await optionalBytes(String(witness.path));
  const digest = bytes === null ? null : createHash("sha256").update(bytes).digest("hex");
  if (digest !== witness.sha256) reject("Destination registry changed; retry through its original codec");
}
function targetOperationDirectory(root: string, plan: PreparedGoalStage): string {
  return join(shadowManagementDirectory(root, plan.goalId), "planned-transfer-targets", canonicalAuthoritySha256(plan.operationId));
}
function protectedStageInput(value: JsonObject, payload: VerifiedGoalTransferPayload): JsonObject {
  return {source_preparation: payload.preparation, source_context: payload.context, archive: payload.archive,
    target: value.target, destination_registry: value.destination_registry, registry_witness: value.registry_witness,
    execute: true, protected_transport: {artifact_sha256: payload.artifactSha256, materials: payload.materials},
    runtime_history: payload.runtimeHistory,
    ...(Object.hasOwn(value, "workspace_mapping") ? {workspace_mapping: value.workspace_mapping,
      expected_workspace_preview_sha256: value.expected_workspace_preview_sha256} : {})};
}

/** A live target read, not a snapshot or grant. Execution rechecks it under
 * native staging's locks; exclusive file creation also protects outside edits. */
export async function previewProtectedGoalTransferTarget(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["protected_input", "target", "destination_registry", "registry_witness", "workspace_mapping"])) reject("Target preview requires the complete reviewed destination");
  return withVerifiedGoalTransferPayload(requireJsonObject(value.protected_input, "protected input"), async payload => {
    const plan = planPreparedGoalStage({operation_id: payload.preparation.operation_id, source_context: payload.context,
      archive_sha256: requireJsonObject(payload.preparation.authority_archive, "original authority").archive_sha256,
      target: value.target, destination_registry: value.destination_registry,
      protected_transport: {artifact_sha256: payload.artifactSha256, materials: payload.materials}, workspace_mapping: value.workspace_mapping});
    const target = requireJsonObject(value.target, "destination"), witness = requireJsonObject(value.registry_witness, "destination witness");
    await physicalAncestors(String(target.runtime_root)); await physicalAncestors(String(target.project));
    await verifyDestinationRegistry(target, witness);
    const review = await reviewGoalTransferWorkspace(plan.workspaceMaterials!, payload.artifactSha256, target, witness);
    const reviewed = await readGoalTransferWorkspaceReview(targetOperationDirectory(String(target.runtime_root), plan), plan.identity);
    await verifyDestinationRegistry(target, witness);
    const {ready, ...facts} = review;
    return {...facts, ready_for_workspace_staging: ready, goal_id: plan.goalId, workspace_destinations: plan.workspaceMaterials!.destinations,
      reviewed_preview_sha256: reviewed ?? review.preview_sha256, resuming_original_operation: reviewed !== null,
      target_is_active: false, fresh_admission_required: true, execution_authority_granted: false};
  });
}

async function retainProtectedMaterials(plan: PreparedGoalStage, pending: boolean, completed: boolean, execute: boolean): Promise<void> {
  const artifact = plan.protectedArtifact;
  if (!artifact) return;
  const directory = join(dirname(plan.statePath), "transfer-materials");
  await physicalAncestors(directory); await physicalAncestors(artifact.manifestPath);
  try {
    if (!(await lstat(directory)).isDirectory() || !pending) reject("Destination material directory is occupied");
    const expected = new Set(artifact.materials.map(row => row.id));
    if ((await readdir(directory)).some(name => !expected.has(name))) reject("Destination retains unexpected material; preserve it");
  } catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
  const originalManifest = await optionalBytes(artifact.manifestPath);
  if ((!pending && originalManifest) || (originalManifest && !same(JSON.parse(originalManifest.toString("utf8")), artifact.manifest))) reject("Destination material manifest is occupied or changed");
  if (completed && !originalManifest) reject("Completed destination materials are missing; recover the original stage");
  for (const row of artifact.materials) {
    const target = join(directory, row.id);
    await requirePhysicalTransferPath(target);
    let existing: Awaited<ReturnType<typeof inspectTransferFile>> | undefined;
    try { existing = await inspectTransferFile(target); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if ((!pending && existing) || (existing && (existing.sha256 !== row.sha256 || existing.bytes !== row.bytes))) reject("Destination material is occupied or changed; preserve it");
    if (completed && !existing) reject("Completed destination material is missing; use its recovery owner");
    if (!existing) {
      const input = await inspectTransferFile(row.path);
      if (input.sha256 !== row.sha256 || input.bytes !== row.bytes) reject("Authenticated material changed before staging");
      if (!execute) continue;
      await mkdir(directory, {recursive: true, mode: 0o700});
      await copyFile(row.path, target, constants.COPYFILE_EXCL);
      const handle = await open(target, constants.O_RDONLY | constants.O_NOFOLLOW);
      try { await handle.sync(); } finally { await handle.close(); }
      const observed = await inspectTransferFile(target);
      if (observed.sha256 !== row.sha256 || observed.bytes !== row.bytes) reject("Original material readback failed");
    }
  }
  if (execute) {
    if (artifact.materials.length) {
      const handle = await open(directory, constants.O_RDONLY);
      try { await handle.sync(); } finally { await handle.close(); }
    }
    await retainJson(artifact.manifestPath, artifact.manifest);
  }
}

export async function previewProtectedGoalTransfer(value: JsonObject): Promise<JsonObject> {
  return withVerifiedGoalTransferPayload(value, async payload => {
    const goal = requireJsonObject(payload.context.goal, "original Goal");
    return {schema_version: "loopx_planned_transfer_protected_preview_v0", artifact_sha256: payload.artifactSha256,
      goal_id: goal.id, display_name: goal.display_name ?? goal.id, source_package_version: payload.sourceVersion,
      original_authority: payload.preparation.authority_archive,
      original_runtime_history: payload.runtimeHistory.manifest,
      materials: payload.materials.map(({path: _path, ...row}) => row),
      protected_transport_verified: true, privacy_certified: false, host_drain_qualified: false,
      portable_migration_qualified: false, execution_authority_granted: false, credential_adoption_performed: false};
  });
}

export async function stageProtectedGoalTransfer(value: JsonObject): Promise<JsonObject> {
  const workspace = Object.hasOwn(value, "workspace_mapping");
  if (!hasExactAuthorityKeys(value, ["protected_input", "target", "destination_registry", "registry_witness", "execute",
      ...(workspace ? ["workspace_mapping", "expected_workspace_preview_sha256"] : [])]) ||
      value.execute !== true) reject("Protected staging requires an explicit destination operation");
  return withVerifiedGoalTransferPayload(requireJsonObject(value.protected_input, "protected transfer input"), async payload =>
    stageAuthorityForPlannedTransfer(protectedStageInput(value, payload)));
}

/** Maintenance/source/registry lock order matches the existing canonical owner.
 * The adapter later commits only the returned registry payload through its
 * codec CAS. Every effect is bound to the original operation; interrupted
 * publication leaves a stopped destination that the same operation can recover. */
export async function stageAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  const protectedInput = value.protected_transport;
  const runtimeInput = value.runtime_history;
  const workspaceRequested = Object.hasOwn(value, "workspace_mapping");
  if (!hasExactAuthorityKeys(value, ["source_preparation", "source_context", "archive", "target", "destination_registry", "registry_witness", "execute",
      ...(protectedInput === undefined ? [] : ["protected_transport"]),
      ...(runtimeInput === undefined ? [] : ["runtime_history"]),
      ...(workspaceRequested ? ["workspace_mapping", "expected_workspace_preview_sha256"] : [])]) ||
      value.execute !== true || typeof value.archive !== "string") reject("Explicit complete Goal transfer staging is required");
  const prepared = requireJsonObject(value.source_preparation, "prepared original authority");
  if (prepared.schema_version !== PREPARATION_SCHEMA || prepared.authority_write_fenced !== true ||
      prepared.execution_authority_granted !== false) reject("An original source authority preparation is required");
  let history: ProtectedGoalTransferHistory | undefined;
  if (runtimeInput !== undefined) {
    const raw = requireJsonObject(runtimeInput, "authenticated runtime history");
    if (protectedInput === undefined || !hasExactAuthorityKeys(raw, ["manifest", "files"]) || !Array.isArray(raw.files) ||
        !same(raw.manifest, prepared.runtime_history)) reject("Original runtime history differs from its prepared source");
    history = {manifest: decodeGoalTransferHistory(raw.manifest), files: raw.files.map(value => {
      const file = requireJsonObject(value, "authenticated runtime file");
      if (!hasExactAuthorityKeys(file, ["path", "sha256", "bytes"]) || typeof file.path !== "string" ||
          typeof file.sha256 !== "string" || typeof file.bytes !== "number") reject("Invalid authenticated runtime content");
      return {path: file.path, sha256: file.sha256, bytes: file.bytes};
    })};
  }
  const summary = requireJsonObject(prepared.authority_archive, "prepared authority summary");
  const plan = planPreparedGoalStage({operation_id: prepared.operation_id, source_context: value.source_context,
    archive_sha256: summary.archive_sha256, target: value.target, destination_registry: value.destination_registry,
    ...(protectedInput === undefined ? {} : {protected_transport: protectedInput}),
    ...(workspaceRequested ? {workspace_mapping: value.workspace_mapping} : {})});
  if (prepared.goal_id !== plan.goalId || prepared.source_context_sha256 !== canonicalAuthoritySha256(plan.context)) reject("Prepared source and original Goal context differ");
  const target = value.target as JsonObject, root = target.runtime_root as string;
  const witness = requireJsonObject(value.registry_witness, "codec-validated destination witness");
  const verifyRegistry = () => verifyDestinationRegistry(target, witness);
  await verifyRegistry();
  await physicalAncestors(root); await physicalAncestors(String(target.project));
  const archive = await verifyAuthorityArchive(value.archive);
  if (!same({...archive}, summary) || archive.goal_id !== plan.goalId) reject("Transfer archive differs from the prepared original authority");
  return withShadowMaintenanceLock(root, plan.goalId, () =>
    withFileMutationLock(legacyCoordinationTodoLockPath(root, plan.goalId), () =>
      withFileMutationLock(plan.statePath, () => withFileMutationLock(String(witness.path), async () => {
    await verifyRegistry();
    const managed = await readShadowManagementState(root, plan.goalId);
    if (managed && managed.status !== "inactive") reject("Destination shadow capture requires its original owner");
    const directory = targetOperationDirectory(root, plan);
    await physicalAncestors(directory); await physicalAncestors(plan.statePath); await physicalAncestors(plan.contextPath);
    const pendingPath = join(directory, "preparation.json");
    const pending = await optionalBytes(pendingPath);
    if (pending && !same(JSON.parse(pending.toString("utf8")), plan.identity)) reject("Destination transfer operation belongs to different original facts");
    const paths = localAuthorityProviderPaths(root, plan.goalId);
    const fence = {schema_version: PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: plan.goalId,
      role: "target", fence_id: `planned-transfer-target:${plan.operationId}`, operation_id: plan.operationId,
      source_identity_sha256: canonicalAuthoritySha256(plan.identity)} satisfies PlannedTransferWriterFence;
    const before = await loadLegacyCoordinationWriterFence(root, plan.goalId);
    if (before.status === "failed" || (before.status === "loaded" && !same(before.fence, fence))) reject("Destination authority is occupied or its fence changed");
    const file = new FileAuthorityStore(paths.file, plan.goalId, {existingOnly: true});
    const sqlite = new SqliteAuthorityStore(paths.sqlite, plan.goalId, {existingOnly: true});
    const selected = target.provider === "file" ? file : sqlite, other = target.provider === "file" ? sqlite : file;
    const selectedHead = await selected.loadAuthority(), otherHead = await other.loadAuthority();
    const selectedIdentity = await selected.storeIdentity(), otherIdentity = await other.storeIdentity();
    if (otherHead.status !== "missing" || (!pending && (selectedHead.status !== "missing" || before.status !== "missing" || await optionalBytes(paths.marker)))) {
      reject("Destination already contains authority; staging cannot replace it");
    }
    // File's identity belongs to the shared provider directory; other Goals
    // may legitimately use it. SQLite's database/identity is per Goal.
    if (!pending && ((target.provider === "sqlite" && selectedIdentity.status === "available") ||
        (target.provider === "file" && otherIdentity.status === "available"))) reject("Destination retains a Goal store lineage; restore it rather than adopting it as empty");
    if (selectedHead.status === "failed" || selectedHead.status === "unavailable") reject("Destination authority requires recovery, never reinitialization");
    const storeBindingPath = join(directory, "target-store.json"), binding = await optionalBytes(storeBindingPath);
    if (binding) {
      const observed = await selected.storeIdentity();
      if (observed.status !== "available" || !same(JSON.parse(binding.toString("utf8")), {store_identity: observed.store_identity})) {
        reject("Destination store lineage is missing or changed; restore its complete state, never reinitialize it");
      }
    }
    const marker = await optionalBytes(paths.marker);
    if (marker) {
      const observed = await selected.storeIdentity();
      if (observed.status !== "available" || !same(JSON.parse(marker.toString("utf8")), {
          schema_version: "loopx_local_authority_provider_v0", goal_id: plan.goalId,
          provider: target.provider, store_identity: observed.store_identity})) reject("Destination provider selection changed; preserve later state");
    }
    const completed = await optionalBytes(join(directory, "receipt.json"));
    if (completed) {
      const priorAudit = await auditAuthorityArchive(value.archive as string, selected, archive.archive_sha256);
      if (priorAudit.status !== "matched") reject("Completed destination authority changed or is missing; use its recovery owner");
    }
    const liveAccounting = await readGoalQuotaHistoryForTransfer(root, plan.goalId);
    if (liveAccounting.closed !== true || (liveAccounting.files as JsonObject[]).length !== 0) {
      reject("Destination runtime accounting is occupied; preserve its original owner before staging");
    }
    // State/context are immutable original input, not authority reconstructed
    // from Markdown. No credential-bearing source config enters the live Goal.
    const originalState = await optionalBytes(plan.statePath), originalContext = await optionalBytes(plan.contextPath);
    if (!pending && (originalState || originalContext)) reject("Destination Goal material is occupied; retain its original owner");
    if ((originalState && !originalState.equals(Buffer.from(String(plan.context.state_text)))) ||
        (originalContext && !same(JSON.parse(originalContext.toString("utf8")), plan.context))) reject("Destination Goal material is occupied or changed");
    if (history) await retainGoalTransferHistory(directory, history, pending !== null, completed !== null, false);
    await retainProtectedMaterials(plan, pending !== null, completed !== null, false);
    if (plan.workspaceMaterials) {
      const review = await reviewGoalTransferWorkspace(plan.workspaceMaterials, plan.protectedArtifact!.sha256, target, witness);
      await verifyGoalTransferWorkspaceReview(plan.workspaceMaterials, review, directory, plan.identity,
        value.expected_workspace_preview_sha256, completed !== null);
    }
    await mkdir(directory, {recursive: true, mode: 0o700});
    await retainJson(pendingPath, plan.identity);
    await retainJson(join(directory, "source-preparation.json"), prepared);
    await retainJson(join(directory, "registration.json"), {identity_sha256: canonicalAuthoritySha256(plan.identity),
      registry_path: witness.path, goal: plan.goal});
    const engaged = await engageLegacyCoordinationWriterFenceUnderLocks(root, plan.goalId, plan.statePath, fence);
    if (engaged.status !== "applied" && engaged.status !== "replayed") reject("Destination transfer fence readback failed");
    await mkdir(dirname(plan.statePath), {recursive: true, mode: 0o700});
    if (!originalState) await atomicWriteText(plan.statePath, String(plan.context.state_text));
    await retainJson(plan.contextPath, plan.context);
    await retainProtectedMaterials(plan, pending !== null, completed !== null, true);
    if (plan.workspaceMaterials) await retainGoalTransferWorkspace(plan.workspaceMaterials, directory, plan.identity,
      String(value.expected_workspace_preview_sha256));
    const Store = target.provider === "file" ? FileAuthorityStore : SqliteAuthorityStore;
    const store = new Store(paths[target.provider as "file" | "sqlite"], plan.goalId);
    const identity = await store.storeIdentity();
    if (identity.status !== "available") reject("Restored destination identity is unavailable");
    await retainJson(storeBindingPath, {store_identity: identity.store_identity});
    await restoreAuthorityArchive(value.archive as string, store, archive.archive_sha256);
    const audit = await auditAuthorityArchive(value.archive as string, store, archive.archive_sha256);
    if (audit.status !== "matched") reject("Complete original authority/receipt readback did not match");
    await publishLocalAuthoritySelection(root, plan.goalId, target.provider as "file" | "sqlite", identity.store_identity);
    const historyReadback = history ? await retainGoalTransferHistory(directory, history, true, completed !== null, true) : null;
    if (history) await restoreTransferValidationDeclarations(root, plan.goalId, history.manifest.validation_declarations);
    const result = {schema_version: "loopx_planned_transfer_target_stage_v0", operation_id: plan.operationId, goal_id: plan.goalId,
      identity_sha256: canonicalAuthoritySha256(plan.identity), original_authority: {...archive}, authority_readback: audit,
      target_store_identity: identity.store_identity, target_provider: target.provider,
      ...(historyReadback ? {runtime_history_sha256: historyReadback.history_sha256,
        original_runtime_history_root: join(directory, "runtime-history"), runtime_history_readback_verified: true,
        runtime_accounting_adopted: false} : {}),
      target_is_active: false, fresh_admission_required: true, execution_authority_granted: false,
      ...(plan.protectedArtifact ? {artifact_sha256: plan.protectedArtifact.sha256,
        original_material_count: plan.protectedArtifact.materials.length, material_readback_verified: true,
        original_material_manifest: plan.protectedArtifact.manifestPath} : {}),
      ...(plan.workspaceMaterials ? {workspace_materials_readback_verified: true,
        workspace_destinations: plan.workspaceMaterials.destinations} : {})};
    await retainJson(join(directory, "receipt.json"), result);
    return {...result, destination_registry: stagedGoalRegistry(value.destination_registry as JsonObject, plan, root),
      original_context_path: plan.contextPath};
  }))));
}

/** Read back the retained original operation after restart, without reopening
 * its source or inferring an empty live target index means no prior debit. */
export async function inspectStagedGoalTransferHistory(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id"])) reject("Original staged transfer identity is required");
  const root = await requirePhysicalTransferPath(value.runtime_root);
  const operation = requireAuthorityStoreId(value.operation_id, "original transfer operation");
  const goalId = requireAuthorityStoreId(value.goal_id, "original transfer Goal");
  if (!goalId || /[\\/\0]/u.test(goalId) || goalId === "." || goalId === "..") reject("Original transfer Goal is required");
  const directory = join(shadowManagementDirectory(root, goalId), "planned-transfer-targets", canonicalAuthoritySha256(operation));
  await requirePhysicalTransferPath(directory);
  const receipt = await readGoalTransferHistoryJson(join(directory, "receipt.json"));
  if (receipt.goal_id !== goalId || receipt.operation_id !== operation || receipt.runtime_history_readback_verified !== true ||
      receipt.original_runtime_history_root !== join(directory, "runtime-history")) reject("Original staged runtime history is unavailable");
  const manifest = decodeGoalTransferHistory(await readGoalTransferHistoryJson(join(directory, "runtime-history", "manifest.json")));
  if (manifest.goal_id !== goalId || canonicalAuthoritySha256(manifest) !== receipt.runtime_history_sha256) reject("Original staged runtime history identity changed");
  return inspectGoalTransferHistory(join(directory, "runtime-history"), manifest);
}

/** Ordinary history and same-effect quota replay consume the same verified
 * original content before cutover. Never publish it into the live index or
 * interpret retained transaction receipts as new destination write authority.
 * This reader does not acquire maintenance after a caller's run-index lock;
 * exact before/after observations reject a changing staged lineage instead. */
export async function readStagedGoalTransferRuns(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["runtime_root", "goal_id", "operation_id"])) reject("Original staged accounting identity is required");
  const root = await requirePhysicalTransferPath(value.runtime_root);
  const goal = requireAuthorityStoreId(value.goal_id, "original transfer Goal");
  const operation = requireAuthorityStoreId(value.operation_id, "original transfer operation");
  if (/[\\/\0]/u.test(goal) || goal === "." || goal === "..") reject("Original transfer Goal must be one path segment");
  const fence = await loadLegacyCoordinationWriterFence(root, goal);
  const admitted = fence.status === "loaded" && isPlannedTransferAdmissionFence(fence.fence);
  if (fence.status !== "loaded" || fence.fence.operation_id !== operation ||
      (!admitted && (fence.fence.schema_version !== PLANNED_TRANSFER_WRITER_FENCE_SCHEMA || fence.fence.role !== "target"))) {
    reject("Original staged target accounting binding is unavailable");
  }
  if (admitted) {
    const {requireLocalGoalTransferAdmission} = await import("../goals/planned_transfer_cutover.ts");
    await requireLocalGoalTransferAdmission(root, goal, fence.fence);
  }
  const directory = join(shadowManagementDirectory(root, goal), "planned-transfer-targets", canonicalAuthoritySha256(operation));
  const pending = await readGoalTransferHistoryJson(join(directory, "preparation.json"));
  const receipt = await readGoalTransferHistoryJson(join(directory, "receipt.json"));
  const registration = await readGoalTransferHistoryJson(join(directory, "registration.json"));
  if (canonicalAuthoritySha256(pending) !== fence.fence.source_identity_sha256 || receipt.identity_sha256 !== fence.fence.source_identity_sha256 ||
      registration.identity_sha256 !== receipt.identity_sha256 || pending.goal_id !== goal || pending.operation_id !== operation ||
      requireJsonObject(pending.target, "staged target").runtime_root !== root ||
      receipt.target_is_active !== false || receipt.execution_authority_granted !== false || receipt.runtime_accounting_adopted !== false) {
    reject("Original staged target accounting identity changed");
  }
  await inspectStagedGoalTransferHistory(value);
  const historyRoot = join(directory, "runtime-history");
  const manifest = decodeGoalTransferHistory(await readGoalTransferHistoryJson(join(historyRoot, "manifest.json")));
  const records = await projectGoalQuotaHistoryForTransfer(manifest.source_runtime_root, goal, historyRoot, manifest.quota_history);
  const live = await readGoalQuotaHistoryForTransfer(root, goal);
  if (!admitted && (live.closed !== true || (live.receipts as JsonObject[]).length !== 0)) {
    reject("Staged target contains unadopted live accounting; preserve it for recovery");
  }
  const after = await loadLegacyCoordinationWriterFence(root, goal);
  if (!same(after, fence) || !same(receipt, await readGoalTransferHistoryJson(join(directory, "receipt.json")))) {
    reject("Staged target accounting changed during readback");
  }
  return {records, raw_count: records.length, live_index_sha256: live.index_sha256,
    provenance: {operation_id: operation, history_sha256: receipt.runtime_history_sha256,
      original_index_sha256: manifest.quota_history.index_sha256, historical_only: !admitted,
      runtime_accounting_adopted: admitted, execution_authority_granted: false}};
}
