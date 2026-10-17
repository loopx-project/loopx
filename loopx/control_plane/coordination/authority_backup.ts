/** Full-backup composition of the existing retained-prefix archive owner. */
import {createHash} from "node:crypto";
import {constants} from "node:fs";
import {mkdir, open, rm, writeFile} from "node:fs/promises";
import {isAbsolute, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {isRecoveryDigest, type RecoveryComponentState} from "../state_recovery_contract.ts";
import {canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {exportAuthorityArchive} from "./authority_archive.ts";
import {withVerifiedAuthorityArchive} from "./authority_archive_read.ts";
import {canonicalTodoCollection} from "./local_authority_read.ts";
import {decodeLocalAuthoritySelection, localAuthorityProviderPaths, openRuntimeAuthorityStore,
  type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";
import {decodeLegacyCoordinationWriterFence, legacyCoordinationWriterFencePath} from "./legacy_writer_fence.ts";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
export function authorityBackupDirectory(goalId: string): string {
  return `authority-captures/${hash(Buffer.from(requireAuthorityStoreId(goalId, "goal id")))}`;
}

/** Small routing dependencies, never live-store discovery or initialization. */
async function sourceBytes(path: string): Promise<Buffer | null> {
  let file;
  try { file = await open(path, constants.O_RDONLY | constants.O_NONBLOCK | (constants.O_NOFOLLOW ?? 0)); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
  try {
    if (!(await file.stat()).isFile()) throw new Error("authority dependency is not a regular file");
    const bytes = Buffer.alloc(64 * 1024 + 1);
    let size = 0;
    while (size < bytes.length) {
      const read = await file.read(bytes, size, bytes.length - size, size);
      if (read.bytesRead === 0) break;
      size += read.bytesRead;
    }
    if (size === bytes.length) throw new Error("authority dependency exceeds 64 KiB");
    return bytes.subarray(0, size);
  } finally { await file.close(); }
}
const witness = (bytes: Buffer | null) => bytes === null ? null : hash(bytes);
async function routing(root: string, goalId: string) {
  const selector = await sourceBytes(localAuthorityProviderPaths(root, goalId).marker);
  if (selector !== null) decodeLocalAuthoritySelection(JSON.parse(selector.toString("utf8")), goalId);
  const fence = await sourceBytes(legacyCoordinationWriterFencePath(root, goalId));
  if (fence !== null && decodeLegacyCoordinationWriterFence(JSON.parse(fence.toString("utf8"))).goal_id !== goalId) {
    throw new Error("authority fence Goal mismatch");
  }
  return {selector, fence, dependencies: {selector_sha256: witness(selector), writer_fence_sha256: witness(fence)}};
}

/** Retain a coherent native history prefix while ordinary appends may continue.
 * This is one owner's export, never a cross-owner epoch or admission receipt. */
export async function captureAuthorityBackup(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const request = requireJsonObject(value, "authority backup capture");
  const goalId = requireAuthorityStoreId(request.goal_id, "goal id");
  if ((request.runtime_root !== null && (typeof request.runtime_root !== "string" || !isAbsolute(request.runtime_root))) ||
      typeof request.destination !== "string" || !isAbsolute(request.destination)) {
    throw new Error("authority capture requires absolute source and destination paths");
  }
  const started_at = new Date().toISOString();
  const base = {goal_id: goalId, started_at};
  const unavailable = () => ({...base, completed_at: new Date().toISOString(), state: "unknown",
    archive_path: null, reason_code: "authority_capture_unavailable"});
  if (request.runtime_root === null) return unavailable();
  const directory = authorityBackupDirectory(goalId);
  const destination = join(request.destination, directory);
  // Only remove a directory created by this invocation. Existing destinations
  // are never adopted, overwritten, or recursively removed on a failed retry.
  let created = false;
  try {
    await mkdir(join(request.destination, "authority-captures"), {mode: 0o700, recursive: true});
    await mkdir(destination, {mode: 0o700}); created = true;
    const before = await routing(request.runtime_root, goalId);
    const selection = before.selector === null ? null
      : decodeLocalAuthoritySelection(JSON.parse(before.selector.toString("utf8")), goalId);
    if (selection?.provider === "postgresql") throw new Error("local backup requires the provider's own export");
    const store = await openRuntimeAuthorityStore(request.runtime_root, goalId, dependencies, {existingOnly: true});
    const archive = await exportAuthorityArchive(store, goalId, join(destination, "authority.jsonl"));
    const after = await routing(request.runtime_root, goalId);
    const identity = await store.storeIdentity();
    if (canonicalAuthoritySha256(before.dependencies) !== canonicalAuthoritySha256(after.dependencies) ||
        identity.status !== "available" || identity.store_identity !== archive.source_store_identity ||
        (selection === null ? archive.source_provider !== "file" :
          selection.provider !== archive.source_provider || selection.store_identity !== archive.source_store_identity)) {
      throw new Error("authority routing or lineage changed during capture");
    }
    for (const [name, bytes] of [["selector.json", before.selector], ["writer-fence.json", before.fence]] as const) {
      if (bytes !== null) await writeFile(join(destination, name), bytes, {flag: "wx", mode: 0o600});
    }
    return {...base, completed_at: new Date().toISOString(), state: "readable",
      archive_path: `${directory}/authority.jsonl`, archive: {...archive}, dependencies: before.dependencies};
  } catch (error) {
    if (!created) throw error;
    await rm(destination, {recursive: true, force: true});
    // The ordinary raw archive remains useful for an unknown/missing owner.
    // Never silently claim that it is an owner-exported canonical checkpoint.
    return unavailable();
  }
}

/** Validate the optional v0 row without trusting a manifest's observed facts. */
export function decodeAuthorityBackupCapture(value: unknown): JsonObject {
  const row = requireJsonObject(value, "authority backup capture");
  const goalId = requireAuthorityStoreId(row.goal_id, "goal id");
  const validTime = (time: unknown): time is string => typeof time === "string" && time.length <= 40 && Number.isFinite(Date.parse(time));
  if (!isRecoveryDigest(row.configuration_sha256) ||
      !validTime(row.started_at) || !validTime(row.completed_at) || Date.parse(row.started_at) > Date.parse(row.completed_at) ||
      (row.runtime_archive_path !== null && typeof row.runtime_archive_path !== "string")) {
    throw new Error("invalid authority capture interval or dependency");
  }
  // Reuse the existing recovery component classification; no new state machine.
  const state = row.state as RecoveryComponentState;
  if (state === "unknown") {
    if (row.archive_path !== null || row.reason_code !== "authority_capture_unavailable") throw new Error("invalid unavailable capture");
    return row;
  }
  if (state !== "readable" || row.runtime_archive_path === null || row.archive_path !== `${authorityBackupDirectory(goalId)}/authority.jsonl`) {
    throw new Error("invalid authority capture artifact");
  }
  const archive = requireJsonObject(row.archive, "authority archive summary");
  const binding = requireJsonObject(row.dependencies, "authority capture dependencies");
  if (archive.goal_id !== goalId || !isRecoveryDigest(archive.archive_sha256) ||
      !Object.hasOwn(binding, "selector_sha256") || !Object.hasOwn(binding, "writer_fence_sha256") ||
      Object.values(binding).some(value => value !== null && !isRecoveryDigest(value))) {
    throw new Error("invalid authority capture evidence");
  }
  return row;
}

/** Inert consumer of the actual owner export; no live source, store creation,
 * import, session binding, receipt replay or effect dispatch is involved. */
export async function inspectAuthorityBackup(dataRoot: string, row: JsonObject): Promise<JsonObject> {
  const goalId = String(row.goal_id);
  const directory = join(dataRoot, authorityBackupDirectory(goalId));
  const dependencies = requireJsonObject(row.dependencies, "authority capture dependencies");
  for (const [name, key] of [["selector.json", "selector_sha256"], ["writer-fence.json", "writer_fence_sha256"]] as const) {
    const bytes = await sourceBytes(join(directory, name));
    if (witness(bytes) !== dependencies[key]) throw new Error("captured authority dependency mismatch");
    if (bytes !== null) {
      if (key === "selector_sha256") decodeLocalAuthoritySelection(JSON.parse(bytes.toString("utf8")), goalId);
      else if (decodeLegacyCoordinationWriterFence(JSON.parse(bytes.toString("utf8"))).goal_id !== goalId) throw new Error("captured fence Goal mismatch");
    }
  }
  const expected = requireJsonObject(row.archive, "authority archive summary");
  // The archive owner verifies the seal, each ordered transition and receipt,
  // then supplies its private verified copy to the Goal read-model owner.
  return withVerifiedAuthorityArchive(join(directory, "authority.jsonl"), String(expected.archive_sha256), async archive => {
    if (canonicalAuthoritySha256({...archive.summary}) !== canonicalAuthoritySha256(expected)) throw new Error("authority capture summary mismatch");
    const selector = await sourceBytes(join(directory, "selector.json"));
    if (selector !== null) {
      const selection = decodeLocalAuthoritySelection(JSON.parse(selector.toString("utf8")), goalId);
      if (selection.provider !== archive.summary.source_provider || selection.store_identity !== archive.summary.source_store_identity) {
        throw new Error("captured selector differs from exported lineage");
      }
    } else if (archive.summary.source_provider !== "file") throw new Error("selected authority requires its selector");
    let head: JsonObject | null = null;
    for await (const row of archive.transactions()) head = row.projection;
    if (head === null) throw new Error("authority capture has no head");
    const collection = canonicalTodoCollection(head, goalId, true);
    return {provider: archive.summary.source_provider, head_digest: archive.summary.projection_sha256,
      provider_revision: archive.summary.source_provider_revision, cursor: archive.summary.commits,
      history_commits: Number(archive.summary.commits), todo_count: collection.projection.todo_ids.length,
      historical_lease_count: collection.leaseIndex?.lease_todo_ids.length ?? 0,
      capture_started_at: row.started_at, capture_completed_at: row.completed_at};
  });
}
