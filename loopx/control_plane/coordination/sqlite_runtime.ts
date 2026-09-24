import { createRequire } from "node:module";
import {AuthorityStoreProtocolError} from "./authority_store_codec.ts";

const require = createRequire(import.meta.url);

export interface SqliteRuntimeInfo {
  node_version: string;
  sqlite_version: string;
  sqlite_source_id: string;
  synchronous_statement_finalization: boolean;
}

export const SQLITE_RUNTIME_IDENTITY_SCHEMA_VERSION = "loopx_sqlite_runtime_identity_v0";

/**
 * What the process that owns `node:sqlite` actually runs.
 *
 * A managed Effect runtime is reused by (user, source fingerprint), so the
 * Node/SQLite pair serving a goal is not necessarily the pair the calling
 * process would resolve from PATH. Publishing this identity lets the runtime
 * info file and `loopx doctor` show which pair is serving, and lets a
 * qualification failure name the runtime that has to be restarted.
 */
export interface SqliteRuntimeIdentity {
  schema_version: string;
  node_version: string;
  sqlite_available: boolean;
  sqlite_version: string | null;
  sqlite_source_id: string | null;
  synchronous_statement_finalization: boolean | null;
  sqlite_authority_qualified: boolean;
  unavailable_reason: string | null;
}

/** Official SQLite 3 release lines containing the WAL-reset fix. */
export function hasSqliteWalResetFix(version: string): boolean {
  const match = /^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$/.exec(version);
  if (!match) return false;
  const [major, minor, patch] = match.slice(1).map(Number);
  if (major !== 3) return false;
  return minor > 51 || (minor === 51 && patch >= 3) ||
    (minor === 50 && patch >= 7) || (minor === 44 && patch >= 6);
}

/** Non-throwing identity probe for status surfaces; never creates authority state. */
export function sqliteRuntimeIdentity(): SqliteRuntimeIdentity {
  const base = {schema_version: SQLITE_RUNTIME_IDENTITY_SCHEMA_VERSION, node_version: process.version};
  let driver: typeof import("node:sqlite");
  try { driver = require("node:sqlite") as typeof import("node:sqlite"); }
  catch {
    return {...base, sqlite_available: false, sqlite_version: null, sqlite_source_id: null,
      synchronous_statement_finalization: null, sqlite_authority_qualified: false,
      unavailable_reason: "node:sqlite is unavailable in this runtime"};
  }
  let version = "", sourceId = "";
  let finalized: boolean | null = null;
  try {
    const db = new driver.DatabaseSync(":memory:");
    let statement: ReturnType<typeof db.prepare>;
    try {
      const row = db.prepare("SELECT sqlite_version() AS version, sqlite_source_id() AS source_id").get();
      version = String(row?.version ?? ""); sourceId = String(row?.source_id ?? "");
      statement = db.prepare("SELECT 1");
    } finally { db.close(); }
    finalized = false;
    try { statement.get(); }
    catch (error) { finalized = (error as NodeJS.ErrnoException).code === "ERR_INVALID_STATE"; }
  } catch (error) {
    return {...base, sqlite_available: false, sqlite_version: null, sqlite_source_id: null,
      synchronous_statement_finalization: null, sqlite_authority_qualified: false,
      unavailable_reason: `SQLite runtime probe failed: ${error instanceof Error ? error.message : "unknown error"}`};
  }
  return {...base, sqlite_available: true, sqlite_version: version, sqlite_source_id: sourceId,
    synchronous_statement_finalization: finalized,
    sqlite_authority_qualified: finalized === true && hasSqliteWalResetFix(version),
    unavailable_reason: null};
}

let cached: {driver: typeof import("node:sqlite"); info: SqliteRuntimeInfo} | undefined;

/**
 * Whether this process is the reused managed runtime rather than a direct CLI
 * run. Only the launcher sets the runtime token, and only a process that
 * outlives the request needs an operator restart: a direct run is repaired by
 * rerunning it on a qualified PATH.
 */
function servingManagedRuntime(): boolean {
  const token = process.env.LOOPX_EFFECT_RUNTIME_TOKEN;
  return typeof token === "string" && token.length > 0;
}

/** Probe only an in-memory database before any authority path is created. */
export function sqliteAuthorityRuntime(): {driver: typeof import("node:sqlite"); info: SqliteRuntimeInfo} {
  if (cached) return cached;
  const identity = sqliteRuntimeIdentity();
  const repairGuidance = servingManagedRuntime()
    ? `This managed Effect runtime (pid ${process.pid}, Node ${identity.node_version}) is the one serving this goal; it keeps its Node until it exits. Install the qualified Node on PATH, then run \`loopx doctor --restart-runtime\` (or wait for its idle shutdown) so the next request starts a new runtime.`
    : "Rerun this command on a PATH whose Node is the qualified runtime.";
  if (!identity.sqlite_available) {
    throw new AuthorityStoreProtocolError(`SQLite authority requires a runtime with node:sqlite (${identity.unavailable_reason}); use Node 22.22.3 or newer with a qualified embedded SQLite driver. ${repairGuidance}`);
  }
  if (identity.sqlite_authority_qualified !== true) {
    throw new AuthorityStoreProtocolError(`SQLite authority runtime is not qualified (SQLite ${identity.sqlite_version}, synchronous statement finalization=${identity.synchronous_statement_finalization}); require the WAL-reset fix in SQLite 3.51.3+, 3.50.7+ or 3.44.6+ and finalized statements on close. Use Node 22.22.3 or newer with a qualified embedded SQLite driver. ${repairGuidance}`);
  }
  let driver: typeof import("node:sqlite");
  try { driver = require("node:sqlite") as typeof import("node:sqlite"); }
  catch {
    throw new AuthorityStoreProtocolError(`SQLite authority requires node:sqlite; use Node 22.22.3 or newer with a qualified embedded SQLite driver. ${repairGuidance}`);
  }
  const info = {node_version: identity.node_version, sqlite_version: String(identity.sqlite_version),
    sqlite_source_id: String(identity.sqlite_source_id),
    synchronous_statement_finalization: identity.synchronous_statement_finalization === true};
  cached = {driver, info};
  return cached;
}
