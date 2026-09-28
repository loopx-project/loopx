/** Reviewed local-provider cutover. The canonical writer guard serializes the
 * copy/audit/publication boundary; the archive preserves domain history. */
import {randomUUID} from "node:crypto";
import {mkdir, open, readFile, realpath} from "node:fs/promises";
import {dirname, isAbsolute, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {canonicalAuthoritySha256 as sha256, hasExactAuthorityKeys, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {exportAuthorityArchive, restoreAuthorityArchive, verifyAuthorityArchive, type AuthorityArchiveSummary} from "./authority_archive.ts";
import {auditAuthorityArchive} from "./authority_archive_audit.ts";
import {indexCoordinationProjection} from "./coordination_projection.ts";
import {canonicalTaskLease} from "./task_lease_state.ts";
import {loadLegacyCoordinationWriterFence} from "./legacy_writer_fence.ts";
import {withCanonicalWriter} from "./local_authority_write.ts";
import {FileAuthorityStore, syncAuthorityDirectory} from "./file_authority_store.ts";
import {SqliteAuthorityStore} from "./sqlite_authority_store.ts";
import {localAuthorityProviderPaths, openLocalAuthorityStoreHandle, publishLocalAuthoritySelection,
  requireLocalAuthorityRuntimeRoot} from "./local_authority_provider.ts";

type Provider = "file" | "sqlite";
interface Source extends JsonObject {
  provider: Provider;
  store_identity: string;
  provider_revision: string;
  cursor: string;
  projection_sha256: string;
  fence_sha256: string;
}
interface Plan extends JsonObject {
  schema_version: "loopx_local_authority_migration_plan_v0";
  plan_id: string;
  runtime_root: string;
  goal_id: string;
  target_provider: Provider;
  source: Source;
}
interface Recovery extends JsonObject {
  schema_version: "loopx_local_authority_migration_recovery_v0";
  plan_sha256: string;
  phase: "prepared" | "completed";
  target_store_identity: string;
  archive_sha256: string;
}
const HEX = /^[0-9a-f]{64}$/;
function provider(value: unknown): Provider {
  if (value !== "file" && value !== "sqlite") throw new Error("Local migration requires file or sqlite");
  return value;
}
function absolute(value: unknown): string {
  if (typeof value !== "string" || !isAbsolute(value)) throw new Error("Migration plan path must be absolute");
  return value;
}
async function optionalJson(path: string): Promise<unknown | undefined> {
  try { return JSON.parse(await readFile(path, "utf8")); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; }
}
async function writePlan(path: string, plan: Plan): Promise<void> {
  // Never replace the artifact an operator already reviewed.
  const file = await open(path, "wx", 0o600);
  try { await file.writeFile(JSON.stringify(plan) + "\n"); await file.sync(); }
  finally { await file.close(); }
  await syncAuthorityDirectory(dirname(path));
}
function decodePlan(value: unknown): Plan {
  const plan = requireJsonObject(value, "migration plan");
  if (!hasExactAuthorityKeys(plan, ["schema_version", "plan_id", "runtime_root", "goal_id", "target_provider", "source"]) ||
      plan.schema_version !== "loopx_local_authority_migration_plan_v0" ||
      typeof plan.plan_id !== "string" || !/^[0-9a-f-]{36}$/.test(plan.plan_id)) throw new Error("Invalid migration plan");
  const source = requireJsonObject(plan.source, "migration source");
  const sourceProvider = provider(source.provider);
  const target = provider(plan.target_provider);
  if (!hasExactAuthorityKeys(source, ["provider", "store_identity", "provider_revision", "cursor", "projection_sha256", "fence_sha256"]) ||
      sourceProvider === target || typeof source.store_identity !== "string" ||
      !new RegExp(`^${sourceProvider}:[0-9a-f]{32}$`).test(source.store_identity) ||
      typeof source.provider_revision !== "string" || !source.provider_revision ||
      typeof source.cursor !== "string" || !/^[1-9]\d*$/.test(source.cursor) ||
      typeof source.projection_sha256 !== "string" || !HEX.test(source.projection_sha256) ||
      typeof source.fence_sha256 !== "string" || !HEX.test(source.fence_sha256)) throw new Error("Invalid migration source binding");
  absolute(plan.runtime_root); requireAuthorityStoreId(plan.goal_id, "goal id");
  return plan as Plan;
}
function decodeRecovery(value: unknown, plan: Plan, digest: string): Recovery {
  const record = requireJsonObject(value, "migration recovery");
  if (!hasExactAuthorityKeys(record, ["schema_version", "plan_sha256", "phase", "target_store_identity", "archive_sha256"]) ||
      record.schema_version !== "loopx_local_authority_migration_recovery_v0" || record.plan_sha256 !== digest ||
      (record.phase !== "prepared" && record.phase !== "completed") ||
      typeof record.target_store_identity !== "string" ||
      !new RegExp(`^${plan.target_provider}:[0-9a-f]{32}$`).test(record.target_store_identity) ||
      typeof record.archive_sha256 !== "string" || !HEX.test(record.archive_sha256)) throw new Error("Invalid migration recovery binding");
  return record as Recovery;
}
async function observe(root: string, goalId: string): Promise<{source: Source; store: AuthorityStore}> {
  const fence = await loadLegacyCoordinationWriterFence(root, goalId);
  if (fence.status !== "loaded") throw new Error("Migration requires an engaged legacy writer fence");
  const handle = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
  const selected = provider(handle.provider);
  const identity = await handle.store.storeIdentity();
  const head = await handle.store.loadAuthority();
  if (identity.status !== "available" || head.status !== "loaded") throw new Error("Migration requires an available canonical head");
  const index = indexCoordinationProjection(head.head, goalId);
  for (const [todoId, raw] of index.leases) {
    // Expiry permits another lease decision, but does not prove a Host stopped.
    if (canonicalTaskLease(raw, goalId, todoId).status === "active") {
      throw new Error("Migration requires settled task leases, including expired active leases; stop writers and settle leases first");
    }
  }
  return {store: handle.store, source: {provider: selected, store_identity: identity.store_identity,
    provider_revision: head.provider_revision, cursor: head.cursor, projection_sha256: sha256(head.head), fence_sha256: sha256(fence.fence)}};
}
function checkArchive(archive: AuthorityArchiveSummary, plan: Plan): void {
  const source = plan.source;
  if (archive.goal_id !== plan.goal_id || archive.source_provider !== source.provider ||
      archive.source_store_identity !== source.store_identity || archive.source_provider_revision !== source.provider_revision ||
      archive.commits !== source.cursor || archive.projection_sha256 !== source.projection_sha256) {
    throw new Error("Migration archive does not match the reviewed source");
  }
}
function targetStore(root: string, plan: Plan, identity?: string): AuthorityStore {
  const paths = localAuthorityProviderPaths(root, plan.goal_id);
  const options = identity === undefined ? {} : {existingOnly: true, expectedIdentity: identity};
  return plan.target_provider === "file" ? new FileAuthorityStore(paths.file, plan.goal_id, identity === undefined ? {} : {expectedIdentity: identity})
    : new SqliteAuthorityStore(paths.sqlite, plan.goal_id, options);
}

/** No provider factory injection: this administrative operation explicitly owns
 * the built-in local stores, and never silently adopts a PostgreSQL binding. */
export async function manageLocalAuthorityMigration(request: JsonObject): Promise<JsonObject> {
  const base = {schema_version: "loopx_local_authority_migration_result_v0", legacy_fallback_used: false,
    execution_authority_granted: false};
  // Publication may have succeeded even if fsync/readback throws. Report unknown,
  // not a false assertion that no authority changed; retry the same plan.
  let publicationAttempted = false;
  try {
    const root = await realpath(requireLocalAuthorityRuntimeRoot(request.runtime_root));
    const goalId = requireAuthorityStoreId(request.goal_id, "goal id");
    const planPath = absolute(request.plan);
    return await withCanonicalWriter(root, goalId, false, async () => {
      if (request.action === "plan-migration") {
        const target = provider(request.provider);
        const {source} = await observe(root, goalId);
        if (source.provider === target) throw new Error("Requested provider is already selected");
        const plan: Plan = {schema_version: "loopx_local_authority_migration_plan_v0", plan_id: randomUUID(),
          runtime_root: root, goal_id: goalId, target_provider: target, source};
        await writePlan(planPath, plan);
        return {...base, status: "planned", authority_changed: false, plan_sha256: sha256(plan), plan,
          requires_execute: true};
      }
      if (request.action !== "migrate" || typeof request.execute !== "boolean") throw new Error("Invalid migration action");
      const plan = decodePlan(await optionalJson(planPath));
      const digest = sha256(plan);
      if (request.plan_sha256 !== digest || plan.runtime_root !== root || plan.goal_id !== goalId) {
        throw new Error("Migration plan digest, runtime or Goal mismatch");
      }
      const directory = join(root, "authority-transition", "local-provider", digest);
      const recoveryPath = join(directory, "recovery.json");
      const archivePath = join(directory, "source.archive.jsonl");
      const raw = await optionalJson(recoveryPath);
      let recovery = raw === undefined ? undefined : decodeRecovery(raw, plan, digest);
      const current = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
      if (current.provider === plan.target_provider && recovery) {
        const archive = await verifyAuthorityArchive(archivePath);
        checkArchive(archive, plan);
        const proof = await auditAuthorityArchive(archivePath, current.store, recovery.archive_sha256, "retained_prefix");
        if (proof.status !== "matched" || proof.target_store_identity !== recovery.target_store_identity) {
          throw new Error("Published migration target no longer matches its retained history or identity");
        }
        if (request.execute) await durableWriteJson(recoveryPath, {...recovery, phase: "completed"});
        return {...base, status: "already_applied", authority_changed: false, plan_sha256: digest,
          selected_provider: current.provider, audit: proof};
      }
      if (recovery?.phase === "completed") throw new Error("Completed migration was superseded; create a fresh plan");
      const source = await observe(root, goalId);
      if (sha256(source.source) !== sha256(plan.source)) throw new Error("Migration source changed; create and review a fresh plan");
      if (!request.execute) return {...base, status: "planned", authority_changed: false, plan_sha256: digest,
        selected_provider: source.source.provider, target_provider: plan.target_provider, requires_execute: true};
      await mkdir(directory, {recursive: true, mode: 0o700});
      // Bind implicit File selection before preparing another provider. Otherwise
      // SQLite existence would intentionally trip the missing-selector guard.
      await publishLocalAuthoritySelection(root, goalId, source.source.provider, source.source.store_identity);
      let archive: AuthorityArchiveSummary;
      try { archive = await verifyAuthorityArchive(archivePath); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT" || recovery) throw error;
        archive = await exportAuthorityArchive(source.store, goalId, archivePath);
      }
      checkArchive(archive, plan);
      if (!recovery) {
        const identity = await targetStore(root, plan).storeIdentity();
        if (identity.status !== "available") throw new Error("Migration target identity unavailable");
        recovery = {schema_version: "loopx_local_authority_migration_recovery_v0", plan_sha256: digest,
          phase: "prepared", target_store_identity: identity.store_identity, archive_sha256: archive.archive_sha256};
        await durableWriteJson(recoveryPath, recovery);
      }
      if (archive.archive_sha256 !== recovery.archive_sha256) throw new Error("Migration backup changed");
      const target = targetStore(root, plan, recovery.target_store_identity);
      await restoreAuthorityArchive(archivePath, target, recovery.archive_sha256);
      const proof = await auditAuthorityArchive(archivePath, target, recovery.archive_sha256);
      if (proof.status !== "matched" || proof.target_store_identity !== recovery.target_store_identity) throw new Error("Migration target audit failed");
      const finalSource = await observe(root, goalId);
      if (sha256(finalSource.source) !== sha256(plan.source)) throw new Error("Migration source changed before publication");
      publicationAttempted = true;
      await publishLocalAuthoritySelection(root, goalId, plan.target_provider, recovery.target_store_identity);
      const selected = await openLocalAuthorityStoreHandle(root, goalId, {}, {existingOnly: true});
      const readback = await selected.store.loadAuthority();
      const identity = await selected.store.storeIdentity();
      // The independent full audit above is still protected by the same writer
      // guard. Publication readback binds that proof to the selected head; do
      // not re-scan the entire archive a second time for the identical revision.
      if (selected.provider !== plan.target_provider || identity.status !== "available" ||
          identity.store_identity !== recovery.target_store_identity || readback.status !== "loaded" ||
          readback.cursor !== proof.captured_target_cursor || readback.provider_revision !== proof.captured_target_provider_revision ||
          sha256(readback.head) !== plan.source.projection_sha256) throw new Error("Migration publication readback failed; retry the same plan");
      await durableWriteJson(recoveryPath, {...recovery, phase: "completed"});
      return {...base, status: "migrated", authority_changed: true, plan_sha256: digest,
        selected_provider: selected.provider, archive: {...archive}, audit: proof,
        publication_readback: {cursor: readback.cursor, provider_revision: readback.provider_revision}};
    });
  } catch (error) {
    return {...base, status: "failed", authority_changed: publicationAttempted ? null : false,
      reason_code: publicationAttempted ? "migration_publication_uncertain" : "migration_rejected",
      reason: error instanceof Error ? error.message : "Migration unavailable"};
  }
}
