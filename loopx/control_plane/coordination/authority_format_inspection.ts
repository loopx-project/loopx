/** Content-based administrative recognition. Identification is not migration
 * permission and does not certify a store's complete transaction history. */
import {open, readFile, stat} from "node:fs/promises";
import {join} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {isAuthorityJsonObject, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {FILE_AUTHORITY_LEGACY_SCHEMA} from "./file_authority_migration.ts";
import {FILE_AUTHORITY_JOURNAL_SCHEMA} from "./file_authority_journal.ts";
import {sqliteAuthorityRuntime} from "./sqlite_runtime.ts";
import {SQLITE_AUTHORITY_STORE_SCHEMA, sqliteAuthorityPath} from "./sqlite_authority_store.ts";
import {SQLITE_AUTHORITY_STORE_V1_SCHEMA} from "./sqlite_authority_migration.ts";
import {verifyAuthorityArchive} from "./authority_archive.ts";

type StoreInspection = {
  artifact_kind: "authority_store"; status: "recognized"; provider: "file" | "sqlite";
  format: string; goal_id: string; store_identity: string; upgrade_required: boolean;
  verification: "metadata_only"; migration_route: "physical_upgrade" | "logical_archive";
};
export type AuthorityFormatInspection = StoreInspection | (JsonObject & {
  artifact_kind: "logical_archive" | "format_backup" | "provider_selector" | "remote_store_envelope" | "unknown";
  status: "recognized" | "unsupported";
});

function store(provider: "file" | "sqlite", format: string, goal: unknown, identity: unknown,
  old: boolean): StoreInspection {
  const storeIdentity = requireAuthorityStoreId(identity, "store identity");
  if (!new RegExp(`^${provider}:[0-9a-f]{32}$`).test(storeIdentity)) throw new Error("Invalid store identity");
  return {artifact_kind: "authority_store", status: "recognized", provider, format,
    goal_id: requireAuthorityStoreId(goal, "goal id"), store_identity: storeIdentity,
    upgrade_required: old, verification: "metadata_only",
    migration_route: old ? "physical_upgrade" : "logical_archive"};
}

export async function inspectAuthorityFormat(path: string): Promise<AuthorityFormatInspection> {
  if ((await stat(path)).isDirectory()) {
    const manifest: unknown = JSON.parse(await readFile(join(path, "manifest.json"), "utf8"));
    if (!isAuthorityJsonObject(manifest) || manifest.schema_version !== "loopx_authority_format_upgrade_v0" ||
      (manifest.provider !== "file" && manifest.provider !== "sqlite")) throw new Error("Not a recognized format backup package");
    const goal = requireAuthorityStoreId(manifest.goal_id, "backup goal id");
    const source = manifest.provider === "file" ? join(path, "source.json") : sqliteAuthorityPath(path, goal);
    const digest = createHash("sha256").update(await readFile(source)).digest("hex");
    if (digest !== manifest.source_sha256) throw new Error("Backup content digest mismatch");
    const detected = await inspectAuthorityFormat(source);
    if (detected.artifact_kind !== "authority_store" || detected.provider !== manifest.provider ||
      detected.goal_id !== goal || detected.store_identity !== manifest.store_identity) throw new Error("Backup lineage mismatch");
    if (manifest.provider === "file" && await readFile(join(path, "store-identity"), "utf8") !== manifest.store_identity) {
      throw new Error("Backup store identity mismatch");
    }
    return {artifact_kind: "format_backup", status: "recognized", format: manifest.schema_version,
      source: detected, verification: "backup_bytes_verified", migration_route: "isolated_restore_then_upgrade"};
  }
  const handle = await open(path, "r");
  const prefix = Buffer.alloc(65536);
  let length: number;
  try { length = (await handle.read(prefix, 0, prefix.length, 0)).bytesRead; } finally { await handle.close(); }
  if (prefix.subarray(0, 16).equals(Buffer.from("SQLite format 3\0"))) {
    const db = new (sqliteAuthorityRuntime().driver.DatabaseSync)(path, {readOnly: true});
    try {
      const version = Number(db.prepare("PRAGMA user_version").get()?.user_version);
      const row = db.prepare("SELECT schema_version,goal_id,store_identity FROM metadata WHERE singleton=1").get();
      if (row?.schema_version === SQLITE_AUTHORITY_STORE_V1_SCHEMA && version === 1) {
        return store("sqlite", String(row.schema_version), row.goal_id, row.store_identity, true);
      }
      if (row?.schema_version === SQLITE_AUTHORITY_STORE_SCHEMA && version === 2) {
        return store("sqlite", String(row.schema_version), row.goal_id, row.store_identity, false);
      }
      return {artifact_kind: "unknown", status: "unsupported", format: String(row?.schema_version ?? "unknown"),
        database_version: version, reason: "SQLite schema metadata and user_version are unsupported or inconsistent"};
    } finally { db.close(); }
  }
  // Recognize an archive from its first complete JSON record, never its suffix.
  // Full digest/seal validation remains streaming in the established archive owner.
  const firstLine = prefix.subarray(0, length).toString("utf8").split("\n", 1)[0]!;
  let first: unknown;
  try { first = JSON.parse(firstLine); } catch { first = null; }
  if (isAuthorityJsonObject(first) && first.kind === "header" && first.schema_version === "loopx_authority_archive_v0") {
    return {artifact_kind: "logical_archive", status: "recognized", ...await verifyAuthorityArchive(path),
      verification: "archive_verified", migration_route: "isolated_provider_restore"};
  }
  let value: unknown;
  try { value = JSON.parse(await readFile(path, "utf8")); }
  catch { return {artifact_kind: "unknown", status: "unsupported", reason: "Not a supported authority JSON, SQLite store or logical archive"}; }
  if (isAuthorityJsonObject(value)) {
    if (value.schema_version === FILE_AUTHORITY_LEGACY_SCHEMA || value.schema_version === FILE_AUTHORITY_JOURNAL_SCHEMA) {
      return store("file", value.schema_version, value.goal_id, value.store_identity,
        value.schema_version !== FILE_AUTHORITY_JOURNAL_SCHEMA);
    }
    if (value.schema_version === "loopx_local_authority_provider_v0") {
      if (value.provider !== "sqlite" && value.provider !== "postgresql") throw new Error("Unknown selector provider");
      requireAuthorityStoreId(value.store_identity, "selector store identity");
      if (value.provider === "postgresql") requireAuthorityStoreId(value.tenant_id, "selector tenant id");
      return {artifact_kind: "provider_selector", status: "recognized", format: value.schema_version,
        goal_id: requireAuthorityStoreId(value.goal_id, "goal id"), provider: value.provider ?? null,
        verification: "metadata_only", migration_route: "resolve_selected_provider_before_migration"};
    }
    if (value.schema_version === "loopx_nokv_authority_store_v0") {
      return {artifact_kind: "remote_store_envelope", status: "recognized", provider: "nokv", format: value.schema_version,
        goal_id: requireAuthorityStoreId(value.goal_id, "goal id"), verification: "metadata_only",
        migration_route: "export_through_configured_provider_service"};
    }
  }
  return {artifact_kind: "unknown", status: "unsupported", format: isAuthorityJsonObject(value) ? value.schema_version ?? null : null,
    reason: "No registered migration route for this artifact"};
}
