/** One qualified physical SQLite snapshot owner for archive and format-upgrade IO. */
import {open, unlink} from "node:fs/promises";
import {isAbsolute, resolve} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {sqliteAuthorityRuntime, type SqliteRuntimeInfo} from "./sqlite_runtime.ts";

export async function snapshotSqliteDatabase(source: string, destination: string): Promise<SqliteRuntimeInfo> {
  if (!isAbsolute(source) || !isAbsolute(destination) || resolve(source) === resolve(destination)) {
    throw new Error("SQLite backup requires distinct absolute source and destination paths");
  }
  const {driver, info} = sqliteAuthorityRuntime();
  const db = new driver.DatabaseSync(source, {readOnly: true});
  let created = false;
  try {
    // Never replace a caller's existing file, including a symlink or source alias.
    const reserved = await open(destination, "wx", 0o600);
    created = true;
    await reserved.close();
    db.exec("PRAGMA busy_timeout=5000");
    await driver.backup(db, destination);
    const copy = new driver.DatabaseSync(destination);
    try {
      // The archive contains one standalone database, never a live WAL pair.
      copy.exec("PRAGMA journal_mode=DELETE");
      const rows = copy.prepare("PRAGMA integrity_check").all();
      if (rows.length !== 1 || rows[0]?.integrity_check !== "ok") {
        throw new Error("SQLite backup integrity check failed");
      }
    } finally { copy.close(); }
    const handle = await open(destination, "r");
    try { await handle.sync(); } finally { await handle.close(); }
    return info;
  } catch (error) {
    if (created) await unlink(destination);
    throw error;
  } finally { db.close(); }
}

export async function snapshotSqliteBackup(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "SQLite backup request");
  return {...await snapshotSqliteDatabase(
    requireNonEmptyString(request.source_path, "SQLite backup source"),
    requireNonEmptyString(request.destination_path, "SQLite backup destination"),
  )};
}
