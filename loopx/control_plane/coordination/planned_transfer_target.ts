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
import {withVerifiedGoalTransferPayload} from "../goals/planned_transfer_payload.ts";
import {inspectTransferFile, requirePhysicalTransferPath} from "../goals/planned_transfer_container.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys} from "./authority_store_codec.ts";
import {verifyAuthorityArchive, restoreAuthorityArchive} from "./authority_archive.ts";
import {auditAuthorityArchive} from "./authority_archive_audit.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {SqliteAuthorityStore} from "./sqlite_authority_store.ts";
import {localAuthorityProviderPaths, publishLocalAuthoritySelection} from "./local_authority_provider.ts";
import {engageLegacyCoordinationWriterFenceUnderLocks, loadLegacyCoordinationWriterFence,
  PLANNED_TRANSFER_WRITER_FENCE_SCHEMA, type PlannedTransferWriterFence} from "./legacy_writer_fence.ts";
import {readShadowManagementState, shadowManagementDirectory, withShadowMaintenanceLock} from "./shadow_management.ts";
import {PREPARATION_SCHEMA} from "./planned_transfer_authority.ts";
import {legacyCoordinationTodoLockPath} from "./legacy_writer_lock_paths.ts";

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
      materials: payload.materials.map(({path: _path, ...row}) => row),
      protected_transport_verified: true, privacy_certified: false, host_drain_qualified: false,
      portable_migration_qualified: false, execution_authority_granted: false, credential_adoption_performed: false};
  });
}

export async function stageProtectedGoalTransfer(value: JsonObject): Promise<JsonObject> {
  if (!hasExactAuthorityKeys(value, ["protected_input", "target", "destination_registry", "registry_witness", "execute"]) ||
      value.execute !== true) reject("Protected staging requires an explicit destination operation");
  return withVerifiedGoalTransferPayload(requireJsonObject(value.protected_input, "protected transfer input"), async payload =>
    stageAuthorityForPlannedTransfer({source_preparation: payload.preparation, source_context: payload.context,
      archive: payload.archive, target: value.target, destination_registry: value.destination_registry,
      registry_witness: value.registry_witness, execute: true,
      protected_transport: {artifact_sha256: payload.artifactSha256, materials: payload.materials}}));
}

/** Maintenance/source/registry lock order matches the existing canonical owner.
 * The adapter later commits only the returned registry payload through its
 * codec CAS. Every effect is bound to the original operation; interrupted
 * publication leaves a stopped destination that the same operation can recover. */
export async function stageAuthorityForPlannedTransfer(value: JsonObject): Promise<JsonObject> {
  const protectedInput = value.protected_transport;
  if (!hasExactAuthorityKeys(value, ["source_preparation", "source_context", "archive", "target", "destination_registry", "registry_witness", "execute",
      ...(protectedInput === undefined ? [] : ["protected_transport"])]) ||
      value.execute !== true || typeof value.archive !== "string") reject("Explicit complete Goal transfer staging is required");
  const prepared = requireJsonObject(value.source_preparation, "prepared original authority");
  if (prepared.schema_version !== PREPARATION_SCHEMA || prepared.authority_write_fenced !== true ||
      prepared.execution_authority_granted !== false) reject("An original source authority preparation is required");
  const summary = requireJsonObject(prepared.authority_archive, "prepared authority summary");
  const plan = planPreparedGoalStage({operation_id: prepared.operation_id, source_context: value.source_context,
    archive_sha256: summary.archive_sha256, target: value.target, destination_registry: value.destination_registry,
    ...(protectedInput === undefined ? {} : {protected_transport: protectedInput})});
  if (prepared.goal_id !== plan.goalId || prepared.source_context_sha256 !== canonicalAuthoritySha256(plan.context)) reject("Prepared source and original Goal context differ");
  const target = value.target as JsonObject, root = target.runtime_root as string;
  const witness = requireJsonObject(value.registry_witness, "codec-validated destination witness");
  if (!hasExactAuthorityKeys(witness, ["path", "sha256"]) || witness.path !== target.registry_path ||
      (witness.sha256 !== null && typeof witness.sha256 !== "string")) reject("Invalid destination registry witness");
  const verifyRegistry = async () => {
    const bytes = await optionalBytes(String(witness.path));
    const digest = bytes === null ? null : createHash("sha256").update(bytes).digest("hex");
    if (digest !== witness.sha256) reject("Destination registry changed; retry staging through its original codec");
  };
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
    const directory = join(shadowManagementDirectory(root, plan.goalId), "planned-transfer-targets", canonicalAuthoritySha256(plan.operationId));
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
    // State/context are immutable original input, not authority reconstructed
    // from Markdown. No credential-bearing source config enters the live Goal.
    const originalState = await optionalBytes(plan.statePath), originalContext = await optionalBytes(plan.contextPath);
    if (!pending && (originalState || originalContext)) reject("Destination Goal material is occupied; retain its original owner");
    if ((originalState && !originalState.equals(Buffer.from(String(plan.context.state_text)))) ||
        (originalContext && !same(JSON.parse(originalContext.toString("utf8")), plan.context))) reject("Destination Goal material is occupied or changed");
    await retainProtectedMaterials(plan, pending !== null, completed !== null, false);
    await mkdir(directory, {recursive: true, mode: 0o700});
    await retainJson(pendingPath, plan.identity);
    const engaged = await engageLegacyCoordinationWriterFenceUnderLocks(root, plan.goalId, plan.statePath, fence);
    if (engaged.status !== "applied" && engaged.status !== "replayed") reject("Destination transfer fence readback failed");
    await mkdir(dirname(plan.statePath), {recursive: true, mode: 0o700});
    if (!originalState) await atomicWriteText(plan.statePath, String(plan.context.state_text));
    await retainJson(plan.contextPath, plan.context);
    await retainProtectedMaterials(plan, pending !== null, completed !== null, true);
    const Store = target.provider === "file" ? FileAuthorityStore : SqliteAuthorityStore;
    const store = new Store(paths[target.provider as "file" | "sqlite"], plan.goalId);
    const identity = await store.storeIdentity();
    if (identity.status !== "available") reject("Restored destination identity is unavailable");
    await retainJson(storeBindingPath, {store_identity: identity.store_identity});
    await restoreAuthorityArchive(value.archive as string, store, archive.archive_sha256);
    const audit = await auditAuthorityArchive(value.archive as string, store, archive.archive_sha256);
    if (audit.status !== "matched") reject("Complete original authority/receipt readback did not match");
    await publishLocalAuthoritySelection(root, plan.goalId, target.provider as "file" | "sqlite", identity.store_identity);
    const result = {schema_version: "loopx_planned_transfer_target_stage_v0", operation_id: plan.operationId, goal_id: plan.goalId,
      identity_sha256: canonicalAuthoritySha256(plan.identity), original_authority: {...archive}, authority_readback: audit,
      target_store_identity: identity.store_identity, target_provider: target.provider,
      target_is_active: false, fresh_admission_required: true, execution_authority_granted: false,
      ...(plan.protectedArtifact ? {artifact_sha256: plan.protectedArtifact.sha256,
        original_material_count: plan.protectedArtifact.materials.length, material_readback_verified: true,
        original_material_manifest: plan.protectedArtifact.manifestPath} : {})};
    await retainJson(join(directory, "receipt.json"), result);
    return {...result, destination_registry: stagedGoalRegistry(value.destination_registry as JsonObject, plan, root),
      original_context_path: plan.contextPath};
  }))));
}
