/** Upgrade physical formats without changing provider selection or Goal authority.
 * Provider-to-provider movement uses authority_archive's portable logical log. */
import {copyFile, mkdir, mkdtemp, open, readFile, readdir, rm, chmod} from "node:fs/promises";
import {join} from "node:path";
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {withFileMutationLock} from "../effect_runtime_io.ts";
import {inspectAuthorityFormat} from "./authority_format_inspection.ts";
import {canonicalAuthorityBytes} from "./authority_store_codec.ts";
import {migrateFileAuthorityStore} from "./file_authority_migration.ts";
import {FileAuthorityStore, replaceFileAuthorityDurably, syncAuthorityDirectory} from "./file_authority_store.ts";
import {migrateSqliteAuthorityStoreV1ToV2} from "./sqlite_authority_migration.ts";
import {sqliteAuthorityRuntime} from "./sqlite_runtime.ts";
import {sqliteAuthorityPath} from "./sqlite_authority_store.ts";
import {requireLocalAuthorityRuntimeRoot} from "./local_authority_provider.ts";

async function entries(directory: string): Promise<string[]> {
  try { return await readdir(directory); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return []; throw error; }
}

async function upgradeSqlite(directory: string, goal: string, execute: boolean): Promise<JsonObject> {
  const path = sqliteAuthorityPath(directory, goal);
  return await withFileMutationLock(`${path}.upgrade`, async () => {
    const planned = migrateSqliteAuthorityStoreV1ToV2(directory, goal);
    if (planned.status === "failed") throw new Error(planned.reason);
    if (planned.status !== "planned" || !execute) return {...planned, provider: "sqlite"};
    const backupRoot = join(directory, "format-backups");
    await mkdir(backupRoot, {recursive: true, mode: 0o700});
    const backupDirectory = await mkdtemp(join(backupRoot, "sqlite-"));
    const snapshot = sqliteAuthorityPath(backupDirectory, goal);
    const {driver} = sqliteAuthorityRuntime();
    const db = new driver.DatabaseSync(path, {readOnly: true});
    try { await driver.backup(db, snapshot); } finally { db.close(); }
    await chmod(snapshot, 0o600);
    const handle = await open(snapshot, "r");
    try { await handle.sync(); } finally { await handle.close(); }
    // The source may continue committing during online backup. Verify a separate
    // copy through the real converter; compare its logical digest inside the
    // source's BEGIN IMMEDIATE before table adoption. A race fails, never drops writes.
    const proofDirectory = await mkdtemp(join(backupDirectory, "verify-"));
    let proof;
    try {
      await copyFile(snapshot, sqliteAuthorityPath(proofDirectory, goal));
      proof = migrateSqliteAuthorityStoreV1ToV2(proofDirectory, goal,
        {execute: true, expectedIdentity: planned.identity});
      if (proof.status !== "migrated") throw new Error(proof.reason ?? "SQLite backup verification failed");
    } finally { await rm(proofDirectory, {recursive: true, force: true}); }
    const facts = {schema_version: "loopx_authority_format_upgrade_v0", provider: "sqlite",
      goal_id: goal, store_identity: planned.identity!, from_version: 1, to_version: 2,
      sequence_digest: proof.sequence_digest!, backup_directory: backupDirectory,
      source_sha256: createHash("sha256").update(await readFile(snapshot)).digest("hex")};
    await replaceFileAuthorityDurably(join(backupDirectory, "manifest.json"), canonicalAuthorityBytes(facts));
    await syncAuthorityDirectory(backupRoot);
    await syncAuthorityDirectory(directory);
    const result = migrateSqliteAuthorityStoreV1ToV2(directory, goal, {execute: true,
      expectedIdentity: planned.identity, expectedSequenceDigest: proof.sequence_digest});
    if (result.status !== "migrated" && result.status !== "already_current") {
      throw new Error(`${result.reason}; verified backup: ${backupDirectory}`);
    }
    return {...result, ...facts};
  });
}

/** Known local stores only; selectors, registries and writer fences are not mutated.
 * All stores, including unselected shadows, must be readable by the new binary. */
export async function upgradeAuthorityFormats(roots: readonly string[], execute: boolean): Promise<JsonObject> {
  const results: JsonObject[] = [];
  try {
    for (const root of [...new Set(roots.map(requireLocalAuthorityRuntimeRoot))]) {
      for (const provider of ["file", "sqlite"] as const) {
        const directory = join(root, "authority", `${provider}-v0`);
        const names = (await entries(directory)).filter(name =>
          (provider === "file" ? /^authority-store-[0-9a-f]{16}\.json$/ : /^authority-[0-9a-f]{64}\.sqlite$/).test(name));
        if (provider === "file") names.push(...(await entries(join(directory, "rollback")))
          .filter(name => /^authority-store-[0-9a-f]{24}\.json$/.test(name)).map(name => join("rollback", name)));
        for (const name of names.sort()) {
          const path = join(directory, name);
          let goal: string | null = null;
          try {
            const detected = await inspectAuthorityFormat(path);
            if (detected.artifact_kind !== "authority_store" || detected.provider !== provider) {
              throw new Error("Detected format does not match this provider directory; no migration selected");
            }
            goal = detected.goal_id;
            const expected = provider === "file" ? new FileAuthorityStore(directory, goal, {existingOnly: true}).path
              : sqliteAuthorityPath(directory, goal);
            if (!name.startsWith("rollback") && expected !== path) throw new Error("Authority filename does not match its goal");
            const result = provider === "file" ? await migrateFileAuthorityStore(directory, goal, execute, {}, name.startsWith("rollback") ? path : undefined)
              : await upgradeSqlite(directory, goal, execute);
            results.push({goal_id: goal, detected_format: detected.format, ...result});
          } catch (error) {
            // Earlier stores may already have migrated. Never report global rollback
            // or overwrite their later commits. Retry resumes per-store publication.
            return {status: "failed", results, failed_provider: provider, failed_goal_id: goal,
              reason: error instanceof Error ? error.message : "Format upgrade failed", retry_safe: true};
          }
        }
      }
    }
  } catch (error) {
    return {status: "failed", results, reason: error instanceof Error ? error.message : "Store discovery failed",
      retry_safe: true};
  }
  return {status: execute ? "upgraded" : "planned", results, authority_changed: false};
}
