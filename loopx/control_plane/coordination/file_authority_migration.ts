/** Explicit physical upgrade. Legacy parsing never participates in business reads. */
import {mkdir, readFile} from "node:fs/promises";
import {join, dirname, basename} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {canonicalAuthorityBytes, canonicalAuthoritySha256, hasExactAuthorityKeys,
  isAuthorityJsonObject} from "./authority_store_codec.ts";
import {decodeRetainedAuthorityJournal} from "./authority_store_transactions.ts";
import {FILE_AUTHORITY_JOURNAL_SCHEMA, FileAuthorityJournal} from "./file_authority_journal.ts";
import {FileAuthorityStore, fileAuthorityRevision, replaceFileAuthorityDurably, syncAuthorityDirectory} from "./file_authority_store.ts";
import {createHash} from "node:crypto";

const sha256 = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
export const FILE_AUTHORITY_LEGACY_SCHEMA = "loopx_file_authority_store_v0";

/** Test-only crash seam around the real durable publication boundary. */
export interface FileAuthorityMigrationEffects {
  beforePublish?: () => Promise<void>;
  afterPublish?: () => Promise<void>;
}

export async function migrateFileAuthorityStore(directory: string, goal: string, execute = false,
  effects: FileAuthorityMigrationEffects = {}, archivedPath?: string): Promise<JsonObject> {
  const store = new FileAuthorityStore(directory, goal, {existingOnly: true});
  const sourcePath = archivedPath ?? store.path;
  if (archivedPath && (dirname(archivedPath) !== join(store.directory, "rollback") ||
    !/^authority-store-[0-9a-f]{24}\.json$/.test(basename(archivedPath)))) {
    throw new Error("Invalid File rollback document path");
  }
  // Same lock as ordinary commits: the backup and source are one exact lineage.
  return await withFileMutationLock(store.path, async () => {
    let source: Buffer;
    try { source = await readFile(sourcePath); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return {status: "missing"}; throw error; }
    const identity = await readFile(store.identityPath, "utf8");
    if (!/^file:[0-9a-f]{32}$/.test(identity)) throw new Error("Invalid file store identity");
    const revisionFor = (previous: string | null, transaction: Parameters<typeof fileAuthorityRevision>[3]) =>
      fileAuthorityRevision(goal, identity, previous, transaction);
    const value: unknown = JSON.parse(source.toString("utf8"));
    if (!isAuthorityJsonObject(value)) throw new Error("Invalid authority document");
    if (value.schema_version === FILE_AUTHORITY_JOURNAL_SCHEMA) {
      const current = FileAuthorityJournal.decode(value, goal, identity, revisionFor);
      return {status: "already_current", provider: "file", cursor: current.cursor,
        provider_revision: current.provider_revision};
    }
    if (value.schema_version !== FILE_AUTHORITY_LEGACY_SCHEMA || value.goal_id !== goal || value.store_identity !== identity ||
      !hasExactAuthorityKeys(value, ["schema_version", "goal_id", "store_identity", "provider_revision", "cursor", "head", "committed"])) {
      throw new Error("Unsupported file authority format or mismatched lineage; source was not changed");
    }
    const legacy = decodeRetainedAuthorityJournal(value, "file migration source", revisionFor);
    const compact = FileAuthorityJournal.fromTransactions(goal, identity, legacy.committed, revisionFor);
    // Compare complete logical history, not only the head or receipt count.
    const logicalDigest = canonicalAuthoritySha256(legacy.committed);
    if (canonicalAuthoritySha256(compact.scan(0, legacy.committed.length)) !== logicalDigest) {
      throw new Error("Migrated logical history differs from source");
    }
    const target = canonicalAuthorityBytes(compact.toDocument());
    const sourceDigest = sha256(source), targetDigest = sha256(target);
    const backup = join(directory, "format-backups", sourceDigest);
    const facts = {provider: "file", from_schema: FILE_AUTHORITY_LEGACY_SCHEMA, to_schema: FILE_AUTHORITY_JOURNAL_SCHEMA,
      source_sha256: sourceDigest, target_sha256: targetDigest, logical_sha256: logicalDigest,
      store_identity: identity, goal_id: goal, cursor: compact.cursor, provider_revision: compact.provider_revision,
      bytes_before: source.length, bytes_after: target.length, backup_directory: backup};
    if (!execute) return {status: "planned", ...facts};
    await mkdir(backup, {recursive: true, mode: 0o700});
    // Content-addressed backups are never overwritten. A damaged existing
    // backup stops the upgrade instead of silently replacing its evidence.
    for (const [name, bytes] of [["source.json", source], ["store-identity", Buffer.from(identity)]] as const) {
      const path = join(backup, name);
      let existing: Buffer | undefined;
      try { existing = await readFile(path); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      if (existing && !existing.equals(bytes)) throw new Error("Existing migration backup is corrupt");
      if (!existing) await replaceFileAuthorityDurably(path, bytes);
      if (!(await readFile(path)).equals(bytes)) throw new Error("Migration backup readback mismatch");
    }
    // Sync the newly created directory entries as well as their file contents.
    await syncAuthorityDirectory(join(directory, "format-backups"));
    await syncAuthorityDirectory(directory);
    const manifest = {schema_version: "loopx_authority_format_upgrade_v0", ...facts};
    await replaceFileAuthorityDurably(join(backup, "manifest.json"), canonicalAuthorityBytes(manifest));
    await effects.beforePublish?.();
    await replaceFileAuthorityDurably(sourcePath, target);
    await effects.afterPublish?.();
    if (!(await readFile(sourcePath)).equals(target)) throw new Error("Migration publication readback mismatch");
    return {status: "migrated", ...facts};
    // No mutable 'done' flag: source/target digests and the actual store are the
    // recovery proof. A crash after rename is already_current on retry.
  });
}
