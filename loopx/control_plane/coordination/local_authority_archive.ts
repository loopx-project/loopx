/** Administrative archive transport. Large private state stays in local files;
 * the managed effect runtime returns only compact integrity/readback facts. */
import {mkdir, readFile} from "node:fs/promises";
import {isAbsolute, join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {exportAuthorityArchive, restoreAuthorityArchive, verifyAuthorityArchive} from "./authority_archive.ts";
import {FileAuthorityStore} from "./file_authority_store.ts";
import {SqliteAuthorityStore} from "./sqlite_authority_store.ts";
import {openRuntimeAuthorityStore, requireLocalAuthorityRuntimeRoot,
  type LocalAuthorityProviderDependencies} from "./local_authority_provider.ts";

function path(value: unknown, name: string): string {
  if (typeof value !== "string" || !isAbsolute(value)) throw new Error(`${name} must be absolute`);
  return value;
}

export async function manageLocalAuthorityArchive(value: unknown,
  dependencies: LocalAuthorityProviderDependencies = {}): Promise<JsonObject> {
  const base = {schema_version: "loopx_authority_archive_admin_v0", authority_changed: false,
    legacy_fallback_used: false, execution_authority_granted: false};
  try {
    const request = requireJsonObject(value, "authority archive request");
    if (request.schema_version !== "loopx_authority_archive_admin_request_v0") throw new Error("archive request schema mismatch");
    const archive = path(request.archive, "archive path");
    if (request.action === "verify") return {...base, status: "verified", archive: await verifyAuthorityArchive(archive)};
    const goalId = requireAuthorityStoreId(request.goal_id, "goal id");
    if (request.action === "export") {
      const store = await openRuntimeAuthorityStore(requireLocalAuthorityRuntimeRoot(request.runtime_root), goalId, dependencies);
      return {...base, status: "exported", archive: await exportAuthorityArchive(store, goalId, archive)};
    }
    if (request.action !== "restore") throw new Error("unknown authority archive action");
    const inspected = await verifyAuthorityArchive(archive);
    if (inspected.goal_id !== goalId || inspected.archive_sha256 !== request.archive_sha256) {
      throw new Error("restore goal or reviewed archive digest mismatch");
    }
    if (request.provider !== "file" && request.provider !== "sqlite") throw new Error("isolated local restore requires file or sqlite");
    const destination = path(request.destination, "restore destination");
    if (request.execute !== true) return {...base, status: "planned", archive: inspected,
      provider: request.provider, requires_execute: true, destination_is_active: false};
    // A destination is a standalone recovery artifact, never a runtime root.
    // Exclusive directory creation prevents adoption of an existing runtime or store.
    const binding = {schema_version: "loopx_authority_restore_destination_v0", goal_id: goalId,
      archive_sha256: inspected.archive_sha256, provider: request.provider};
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
