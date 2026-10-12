/** Administrative archive transport. Large private state stays in local files;
 * the managed effect runtime returns only compact integrity/readback facts. */
import {manageLocalAuthorityMigration} from "./local_authority_migration.ts";
import {inspectAuthorityFormat} from "./authority_format_inspection.ts";
import {upgradeAuthorityFormats} from "./authority_format_upgrade.ts";
import {constants} from "node:fs";
import {mkdir, open, readFile} from "node:fs/promises";
import {isAbsolute, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {exportAuthorityArchive, restoreAuthorityArchive, verifyAuthorityArchive} from "./authority_archive.ts";
import {auditAuthorityArchive} from "./authority_archive_audit.ts";
import {AUTHORITY_ARCHIVE_SCHEMA, archiveCursor, archiveHash} from "./authority_archive_read.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {SqliteAuthorityStore} from "./sqlite_authority_store.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot,
  type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";

function path(value: unknown, name: string): string {
  if (typeof value !== "string" || !isAbsolute(value)) throw new Error(`${name} must be absolute`);
  return value;
}

function restoreBinding(goalId: string, digest: unknown, provider: unknown): JsonObject {
  if (provider !== "file" && provider !== "sqlite") throw new Error("isolated local restore requires file or sqlite");
  return {schema_version: "loopx_authority_restore_destination_v0", goal_id: goalId,
    archive_sha256: archiveHash(digest), provider};
}

/** Read only the existing compact completion facts. Never scan history, take the
 * writer lock, create a store, or infer worker liveness from a missing receipt. */
async function restoreReceipt(request: JsonObject): Promise<JsonObject> {
  const goalId = requireAuthorityStoreId(request.goal_id, "goal id");
  const binding = restoreBinding(goalId, request.archive_sha256, request.provider);
  const destination = path(request.destination, "restore destination");
  const read = async (name: string): Promise<JsonObject | null> => {
    let file;
    try { file = await open(join(destination, name), constants.O_RDONLY | constants.O_NONBLOCK); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw error; }
    try {
      // Bounded reads also catch growth after stat without buffering history.
      if (!(await file.stat()).isFile()) throw new Error("restore metadata must be a regular file");
      const buffer = Buffer.alloc(64 * 1024 + 1);
      let size = 0;
      while (size < buffer.length) {
        const {bytesRead} = await file.read(buffer, size, buffer.length - size, size);
        if (bytesRead === 0) break;
        size += bytesRead;
      }
      if (size > 64 * 1024) throw new Error("restore metadata exceeds 64 KiB");
      return requireJsonObject(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(buffer.subarray(0, size))), name);
    } finally { await file.close(); }
  };
  const actual = await read("restore-binding.json");
  if (actual !== null && canonicalAuthoritySha256(actual) !== canonicalAuthoritySha256(binding)) {
    throw new Error("restore destination belongs to a different archive or provider");
  }
  const receipt = await read("verified-restore.json");
  const observation = {verification: "historical_receipt_only", current_integrity_verified: false,
    worker_liveness: "unknown", requires_separate_authority_cutover: true};
  if (receipt === null) return {...observation, status: "receipt_missing", binding_found: actual !== null,
    reason: "No completion receipt is recorded. This does not prove failure or a running worker; preserve the destination and resume only the same reviewed restore."};
  if (actual === null || receipt.schema_version !== AUTHORITY_ARCHIVE_SCHEMA || receipt.status !== "restored" ||
      receipt.goal_id !== goalId || receipt.archive_sha256 !== binding.archive_sha256) {
    throw new Error("restore completion receipt does not match the reviewed destination");
  }
  // Project the existing receipt contract, never arbitrary fields from local JSON.
  const recorded = {schema_version: AUTHORITY_ARCHIVE_SCHEMA, status: "restored", goal_id: goalId,
    archive_sha256: archiveHash(receipt.archive_sha256), commits: archiveCursor(receipt.commits),
    projection_sha256: archiveHash(receipt.projection_sha256),
    source_provider: requireAuthorityStoreId(receipt.source_provider, "source provider"),
    source_store_identity: requireAuthorityStoreId(receipt.source_store_identity, "source store identity"),
    source_provider_revision: requireAuthorityStoreId(receipt.source_provider_revision, "source revision"),
    target_store_identity: requireAuthorityStoreId(receipt.target_store_identity, "target store identity"),
    target_provider_revision: requireAuthorityStoreId(receipt.target_provider_revision, "target revision")};
  if (recorded.source_store_identity === recorded.target_store_identity) throw new Error("restore requires independent target lineage");
  return {...observation, status: "receipt_found", binding_found: true, receipt: recorded,
    reason: "The matching historical completion receipt is recorded. Use authority-archive audit to verify current store integrity before adoption."};
}

export async function manageLocalAuthorityArchive(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const base = {schema_version: "loopx_authority_archive_admin_v0", authority_changed: false,
    legacy_fallback_used: false, execution_authority_granted: false};
  try {
    const request = requireJsonObject(value, "authority archive request");
    if (request.schema_version !== "loopx_authority_archive_admin_request_v0") throw new Error("archive request schema mismatch");
    if (request.action === "restore-receipt") return {...base, ...await restoreReceipt(request)};
    if (request.action === "inspect") return {...base, status: "inspected",
      inspection: await inspectAuthorityFormat(path(request.source, "source path"))};
    if (request.action === "upgrade") {
      if (!Array.isArray(request.runtime_roots) || request.runtime_roots.some(root => typeof root !== "string")) {
        throw new Error("Upgrade requires explicit runtime roots");
      }
      return {...base, ...await upgradeAuthorityFormats(request.runtime_roots as string[], request.execute === true)};
    }
    if (request.action === "plan-migration" || request.action === "migrate" || request.action === "migration-readback") return await manageLocalAuthorityMigration(request);
    const archive = path(request.archive, "archive path");
    if (request.action === "verify") return {...base, status: "verified", archive: await verifyAuthorityArchive(archive)};
    const goalId = requireAuthorityStoreId(request.goal_id, "goal id");
    if (request.action === "export") {
      const store = await openRuntimeAuthorityStore(requireLocalAuthorityRuntimeRoot(request.runtime_root), goalId, dependencies, {existingOnly: true});
      return {...base, status: "exported", archive: await exportAuthorityArchive(store, goalId, archive)};
    }
    if (request.action === "audit") {
      if (typeof request.archive_sha256 !== "string" ||
          (request.allow_newer_head !== undefined && typeof request.allow_newer_head !== "boolean")) {
        throw new Error("audit requires the reviewed archive digest and a boolean prefix policy");
      }
      const inspected = await verifyAuthorityArchive(archive);
      if (inspected.goal_id !== goalId) throw new Error("audit archive goal mismatch");
      let store;
      if (request.destination !== undefined) {
        if (request.runtime_root !== undefined) throw new Error("audit selects a runtime or an isolated destination, not both");
        const destination = path(request.destination, "audit destination");
        const binding = requireJsonObject(JSON.parse(await readFile(join(destination, "restore-binding.json"), "utf8")), "restore binding");
        if (binding.schema_version !== "loopx_authority_restore_destination_v0" ||
            binding.goal_id !== goalId || binding.archive_sha256 !== request.archive_sha256 ||
            (binding.provider !== "file" && binding.provider !== "sqlite")) {
          throw new Error("audit destination belongs to a different archive or provider");
        }
        store = binding.provider === "file" ? new FileAuthorityStore(join(destination, "store"), goalId, {existingOnly: true})
          : new SqliteAuthorityStore(join(destination, "store"), goalId, {existingOnly: true});
      } else {
        store = await openRuntimeAuthorityStore(requireLocalAuthorityRuntimeRoot(request.runtime_root), goalId, dependencies, {existingOnly: true});
      }
      const audit = await auditAuthorityArchive(archive, store, request.archive_sha256,
        request.allow_newer_head === true ? "retained_prefix" : "exact");
      return {...base, status: audit.status === "matched" ? "audited" : "failed", audit};
    }
    if (request.action !== "restore") throw new Error("unknown authority archive action");
    const inspected = await verifyAuthorityArchive(archive);
    if (inspected.goal_id !== goalId || inspected.archive_sha256 !== request.archive_sha256) {
      throw new Error("restore goal or reviewed archive digest mismatch");
    }
    const binding = restoreBinding(goalId, inspected.archive_sha256, request.provider);
    const destination = path(request.destination, "restore destination");
    if (request.execute !== true) return {...base, status: "planned", archive: inspected,
      provider: request.provider, requires_execute: true, destination_is_active: false};
    // A destination is a standalone recovery artifact, never a runtime root.
    // Exclusive directory creation prevents adoption of an existing runtime or store.
    let created = false;
    try { await mkdir(destination, {mode: 0o700}); created = true; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
    if (created) await durableWriteJson(join(destination, "restore-binding.json"), binding);
    const actual = JSON.parse(await readFile(join(destination, "restore-binding.json"), "utf8")) as unknown;
    if (canonicalAuthoritySha256(actual) !== canonicalAuthoritySha256(binding)) throw new Error("restore destination belongs to a different archive or provider");
    return await withFileMutationLock(join(destination, "restore"), async () => {
      const store = request.provider === "file" ? new FileAuthorityStore(join(destination, "store"), goalId)
        : new SqliteAuthorityStore(join(destination, "store"), goalId);
      const restored = await restoreAuthorityArchive(archive, store, inspected.archive_sha256);
      await durableWriteJson(join(destination, "verified-restore.json"), {...restored});
      return {...base, status: "restored", archive: restored, destination_is_active: false,
        requires_separate_authority_cutover: true};
    });
  } catch (error) {
    return {...base, status: "failed", reason_code: "authority_archive_failed",
      reason: error instanceof Error ? error.message : "authority archive unavailable"};
  }
}
