/**
 * Frozen writer for the shipped version-1 SQLite authority schema.
 *
 * The V1 provider is no longer part of the active runtime, so this fixture
 * keeps its exact on-disk contract available to the migration tests: same
 * tables, same `user_version`, same per-row commit proof. It is deliberately
 * independent of the V2 store so a migration test can compare the two.
 */
import {createHash, randomUUID} from "node:crypto";
import {existsSync, mkdirSync} from "node:fs";
import {dirname} from "node:path";
import type {DatabaseSync} from "node:sqlite";

import {canonicalAuthorityObject, canonicalAuthorityObjectList} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {commitDigest, sqliteAuthorityPath} from
  "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";

export const SQLITE_AUTHORITY_STORE_V1_FIXTURE_SCHEMA = "loopx_sqlite_authority_store_v0";

export const SQLITE_AUTHORITY_V1_FIXTURE_DDL = `
CREATE TABLE metadata (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  schema_version TEXT NOT NULL, goal_id TEXT NOT NULL, store_identity TEXT NOT NULL
);
CREATE TABLE commits (
  cursor INTEGER PRIMARY KEY CHECK(cursor > 0),
  operation_id TEXT NOT NULL UNIQUE,
  commit_digest TEXT NOT NULL CHECK(length(commit_digest) = 64),
  projection TEXT NOT NULL, events TEXT NOT NULL, receipts TEXT NOT NULL
);
CREATE TABLE head (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  cursor INTEGER NOT NULL REFERENCES commits(cursor)
);
PRAGMA user_version = 1;
`;

export interface SqliteAuthorityV1Seed {
  readonly operation_id: string;
  readonly projection: Record<string, unknown>;
  readonly events?: readonly Record<string, unknown>[];
  readonly receipts?: readonly Record<string, unknown>[];
}

export interface SqliteAuthorityV1Store {
  readonly path: string;
  readonly identity: string;
  /** One row per retained transaction, in retained order. */
  readonly rows: readonly {
    readonly cursor: string;
    readonly operation_id: string;
    readonly commit_digest: string;
    readonly operation_receipt: Record<string, unknown>;
  }[];
}

function installV1Database(path: string, goalId: string, identity: string): DatabaseSync {
  const {driver: sqlite} = sqliteAuthorityRuntime();
  const db: DatabaseSync = new sqlite.DatabaseSync(path);
  try {
    db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL;");
    db.exec("BEGIN IMMEDIATE");
    db.exec(SQLITE_AUTHORITY_V1_FIXTURE_DDL);
    db.prepare("INSERT INTO metadata VALUES (1, ?, ?, ?)")
      .run(SQLITE_AUTHORITY_STORE_V1_FIXTURE_SCHEMA, goalId, identity);
    return db;
  } catch (error) {
    try { db.exec("ROLLBACK"); } catch { /* the closing handle abandons the transaction */ }
    db.close();
    throw error;
  }
}

function v1Identity(candidate: string | undefined): string {
  const identity = candidate ?? `sqlite:${randomUUID().replaceAll("-", "")}`;
  if (!/^sqlite:[0-9a-f]{32}$/.test(identity)) throw new Error("V1 authority fixture identity is invalid");
  return identity;
}

/**
 * A version-1 database that published only its schema and metadata.
 *
 * The shipped V1 provider created the database on its first write-path open,
 * so a goal that never committed left exactly this state behind: a valid
 * database with no retained transaction and no head row.
 */
export function createEmptySqliteAuthorityStoreV1(
  directory: string,
  goalId: string,
  options: {storeIdentity?: string} = {},
): {path: string; identity: string} {
  const path = sqliteAuthorityPath(directory, goalId);
  if (existsSync(path)) throw new Error("V1 authority fixture refuses to overwrite a database");
  mkdirSync(dirname(path), {recursive: true, mode: 0o700});
  const identity = v1Identity(options.storeIdentity);
  const db = installV1Database(path, goalId, identity);
  try { db.exec("COMMIT"); } finally { db.close(); }
  return {path, identity};
}

export function createSqliteAuthorityStoreV1(
  directory: string,
  goalId: string,
  seeds: readonly SqliteAuthorityV1Seed[],
  options: {storeIdentity?: string} = {},
): SqliteAuthorityV1Store {
  if (seeds.length === 0) throw new Error("V1 authority fixture needs at least one commit");
  const path = sqliteAuthorityPath(directory, goalId);
  if (existsSync(path)) throw new Error("V1 authority fixture refuses to overwrite a database");
  mkdirSync(dirname(path), {recursive: true, mode: 0o700});
  const identity = v1Identity(options.storeIdentity);
  const db = installV1Database(path, goalId, identity);
  const rows: {cursor: string; operation_id: string; commit_digest: string;
    operation_receipt: Record<string, unknown>}[] = [];
  try {
    const insert = db.prepare("INSERT INTO commits VALUES (?, ?, ?, ?, ?, ?)");
    for (const [index, seed] of seeds.entries()) {
      const cursor = BigInt(index + 1);
      const projection = canonicalAuthorityObject(seed.projection, "V1 fixture projection");
      const events = canonicalAuthorityObjectList(seed.events ?? [], "V1 fixture events");
      const receipts = canonicalAuthorityObjectList(seed.receipts ?? [], "V1 fixture receipts");
      const digest = commitDigest(identity, cursor, seed.operation_id, projection, events, receipts);
      insert.run(cursor.toString(), seed.operation_id, digest, JSON.stringify(projection),
        JSON.stringify(events), JSON.stringify(receipts));
      rows.push({cursor: cursor.toString(), operation_id: seed.operation_id, commit_digest: digest,
        operation_receipt: {cursor: cursor.toString(), operation_id: seed.operation_id,
          projection, events, receipts}});
    }
    db.prepare("INSERT INTO head VALUES (1, ?)").run(String(seeds.length));
    db.exec("COMMIT");
  } catch (error) {
    try { db.exec("ROLLBACK"); } catch { /* the closing handle abandons the transaction */ }
    throw error;
  } finally { db.close(); }
  return {path, identity, rows};
}

/** Bytes of one V1 database, used to prove a migration left it untouched. */
export function sqliteAuthorityV1Digest(path: string, _goalId: string): string {
  const {driver: sqlite} = sqliteAuthorityRuntime();
  const db = new sqlite.DatabaseSync(path, {readOnly: true});
  try {
    const hash = createHash("sha256");
    hash.update(`user_version=${String(db.prepare("PRAGMA user_version").get()?.user_version ?? 0)}\n`);
    const names = (db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
      .all() as unknown as {name: string}[]).map(row => row.name)
      .filter(name => !name.startsWith("sqlite_")).sort();
    hash.update(`tables=${names.join(",")}\n`);
    for (const table of names) {
      const rows = db.prepare(`SELECT * FROM ${table} ORDER BY rowid`).all() as unknown as
        Record<string, unknown>[];
      for (const row of rows) {
        hash.update(`${table}:${JSON.stringify(Object.entries(row).sort(([left], [right]) =>
          left < right ? -1 : left > right ? 1 : 0))}\n`);
      }
    }
    return hash.digest("hex");
  } finally { db.close(); }
}
