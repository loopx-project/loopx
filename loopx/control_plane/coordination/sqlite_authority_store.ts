import {AuthorityJournalScan} from "./authority_journal_scan.ts";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, statSync } from "node:fs";
import {sqliteAuthorityRuntime} from "./sqlite_runtime.ts";
import { dirname, join } from "node:path";
import type { DatabaseSync } from "node:sqlite";

import type { JsonObject } from "../effect_program.ts";
import type { AuthorityStore, AuthorityStoreCommit, AuthorityStoreCommitResult, AuthorityStoreCommittedTransaction,
  AuthorityStoreIdentityResult, AuthorityStoreLoadResult, AuthorityStoreReadFailure, AuthorityStoreHead,
  AuthorityStoreReceiptResult, AuthorityStoreReceiptBatchResult, AuthorityStoreScanResult } from "./authority_store.ts";
import { AuthorityStoreProtocolError, canonicalAuthorityBytes, canonicalAuthorityObject,
  canonicalAuthorityObjectList, canonicalAuthoritySha256, normalizeAuthorityStoreCommit,
  requireAuthorityStoreId } from "./authority_store_codec.ts";
import {
  AUTHORITY_STATE_CHECKPOINT_INTERVAL,
  applyAuthorityStateDelta,
  authorityStateCheckpointCursor,
  authorityStateDelta,
  authorityStateDeltaReconstructs,
  authorityStateDigest,
  authorityStateReplayBudget,
  decodeAuthorityStateDelta,
  isAuthorityStateCheckpoint,
  type AuthorityStateDelta,
} from "./authority_state_log.ts";

export const SQLITE_AUTHORITY_STORE_SCHEMA = "loopx_sqlite_authority_store_v2";
const IDENTITY = /^sqlite:[0-9a-f]{32}$/;
const REVISION = /^sqlite:([0-9a-f]{32}):([1-9]\d*)$/;
const DIGEST = /^[0-9a-f]{64}$/;
const MAX_SEQUENCE = 9223372036854775807n;
const AUDIT_PAGE = 512;
const COMMIT_COLUMNS = `CAST(cursor AS TEXT) AS sequence, operation_id, commit_digest, state_digest,
  parent_state_digest, delta, events, receipts`;

/**
 * One bounded checkpoint window plus one exact delta per commit. The retained
 * transactions remain the durable projection outbox consumed by
 * `scanCommitted`; checkpoints only remove the redundant copy of the live
 * projection from every retained historical row.
 *
 * Retention is therefore shaped as three layers with one owner each:
 * `head` proves the live authority, one `checkpoints` row per bounded window
 * plus one exact `delta` per commit reconstructs retained history, and
 * `events`/`receipts` keep the original transaction proof. A live read proves
 * the head from its own evidence, a historical read rebuilds its bounded
 * window from the covering checkpoint, and `verifyAuthorityHistory` proves the
 * complete chain when a caller needs the whole archive.
 *
 * Exported so the reviewed V1 to V2 migration builds identical tables.
 */
export function sqliteAuthorityStoreSchemaDdl(tables: {
  commits: string;
  checkpoints: string;
  head: string;
}): string {
  return `
CREATE TABLE ${tables.commits} (
  cursor INTEGER PRIMARY KEY CHECK(cursor > 0),
  operation_id TEXT NOT NULL UNIQUE,
  commit_digest TEXT NOT NULL CHECK(length(commit_digest) = 64),
  state_digest TEXT NOT NULL CHECK(length(state_digest) = 64),
  parent_state_digest TEXT CHECK(parent_state_digest IS NULL OR length(parent_state_digest) = 64),
  delta TEXT NOT NULL, events TEXT NOT NULL, receipts TEXT NOT NULL
);
CREATE TABLE ${tables.checkpoints} (
  cursor INTEGER PRIMARY KEY CHECK(cursor > 0),
  projection TEXT NOT NULL,
  projection_digest TEXT NOT NULL CHECK(length(projection_digest) = 64)
);
CREATE TABLE ${tables.head} (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  cursor INTEGER NOT NULL REFERENCES ${tables.commits}(cursor),
  projection TEXT NOT NULL,
  state_digest TEXT NOT NULL CHECK(length(state_digest) = 64)
);
`;
}

const INSTALL = `
CREATE TABLE metadata (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  schema_version TEXT NOT NULL, goal_id TEXT NOT NULL, store_identity TEXT NOT NULL
);
${sqliteAuthorityStoreSchemaDdl({commits: "commits", checkpoints: "checkpoints", head: "head"})}
PRAGMA user_version = 2;
`;

interface SqliteStateCursor {
  cursor: bigint;
  projection: JsonObject;
  digest: string;
}

interface SqliteCommitRow {
  cursor: bigint;
  operation_id: string;
  commit_digest: string;
  state_digest: string;
  parent_state_digest: string | null;
  delta: AuthorityStateDelta;
  events: JsonObject[];
  receipts: JsonObject[];
}

interface SqliteVerifiedCommit {
  state: SqliteStateCursor;
  transaction: AuthorityStoreCommittedTransaction;
}

/** One bounded window of retained transactions resumed from a checkpoint. */
interface SqliteCommitWindow {
  identity: string;
  checkpoint: SqliteStateCursor;
  transactions: readonly AuthorityStoreCommittedTransaction[];
  state: SqliteStateCursor;
}

export interface SqliteAuthorityBoundedProfile {
  schema_version: "loopx_sqlite_authority_bounded_profile_v0";
  status: "available";
  cursor: string;
  commits: number;
  checkpoints: number;
  checkpoint_interval: number;
  replay_budget_commits: number;
  recovery_tail_commits: number;
  retained_projection_bytes: number;
  retained_delta_bytes: number;
  retained_payload_bytes: number;
  database_bytes: number;
  wal_bytes: number;
  shm_bytes: number;
}

export interface SqliteAuthorityHistoryAudit {
  schema_version: "loopx_sqlite_authority_history_audit_v0";
  status: "verified";
  commits: number;
  checkpoints: number;
}

export function sqliteAuthorityPath(directory: string, goalId: string): string {
  requireAuthorityStoreId(goalId, "goal id");
  return join(directory, `authority-${createHash("sha256").update(goalId).digest("hex")}.sqlite`);
}

function protocol(message: string): never { throw new AuthorityStoreProtocolError(message); }

function readFailure(error: unknown): AuthorityStoreReadFailure {
  return error instanceof AuthorityStoreProtocolError || error instanceof SyntaxError
    ? {status: "failed", reason_code: "provider_protocol_violation", reason: error.message}
    : {status: "unavailable", reason_code: "provider_read_unavailable",
      reason: "SQLite authority store could not be read; check database availability and lock contention"};
}

function fileBytes(path: string): number {
  return existsSync(path) ? statSync(path).size : 0;
}

/** One local database per goal. No network filesystem or cross-host authority. */
export class SqliteAuthorityStore implements AuthorityStore {
  readonly providerKind = "sqlite" as const;
  readonly path: string;
  readonly goalId: string;
  readonly existingOnly: boolean;
  readonly expectedIdentity: string | undefined;

  constructor(directory: string, goalId: string, options: {existingOnly?: boolean; expectedIdentity?: string} = {}) {
    this.goalId = requireAuthorityStoreId(goalId, "goal id");
    this.path = sqliteAuthorityPath(directory, this.goalId);
    this.existingOnly = options.existingOnly ?? false;
    this.expectedIdentity = options.expectedIdentity;
  }

  private open(write: boolean): DatabaseSync | null {
    const {driver: sqlite} = sqliteAuthorityRuntime();
    if (!write && !existsSync(this.path)) return null;
    if (write && this.existingOnly && !existsSync(this.path)) protocol("Selected SQLite authority database is missing");
    if (write) mkdirSync(dirname(this.path), {recursive: true, mode: 0o700});
    const db = new sqlite.DatabaseSync(this.path, {readOnly: !write});
    try {
      db.exec("PRAGMA busy_timeout = 5000; PRAGMA foreign_keys = ON;");
      if (write) {
        db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL;");
        db.exec("BEGIN IMMEDIATE");
        try {
          const version = Number(db.prepare("PRAGMA user_version").get()?.user_version ?? 0);
          if (version === 0) {
            // Never adopt an unrelated/unversioned database.
            if (db.prepare("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").get()) {
              protocol("SQLite database is not an empty LoopX authority database");
            }
            db.exec(INSTALL);
            db.prepare("INSERT INTO metadata VALUES (1, ?, ?, ?)").run(
              SQLITE_AUTHORITY_STORE_SCHEMA, this.goalId, `sqlite:${randomUUID().replaceAll("-", "")}`);
          }
          this.identity(db);
          db.exec("COMMIT");
        } catch (error) { db.exec("ROLLBACK"); throw error; }
      } else this.identity(db);
      return db;
    } catch (error) { db.close(); throw error; }
  }

  private identity(db: DatabaseSync): string {
    const version = Number(db.prepare("PRAGMA user_version").get()?.user_version ?? 0);
    if (version === 1) {
      protocol("SQLite authority schema version 1 needs the explicit V1 to V2 authority migration");
    }
    if (version !== 2) {
      protocol("Unsupported SQLite authority schema version; explicit migration is required");
    }
    const row = db.prepare("SELECT * FROM metadata WHERE singleton = 1").get();
    if (!row || row.schema_version !== SQLITE_AUTHORITY_STORE_SCHEMA || row.goal_id !== this.goalId ||
        typeof row.store_identity !== "string" || !IDENTITY.test(row.store_identity) ||
        (this.expectedIdentity !== undefined && row.store_identity !== this.expectedIdentity)) {
      protocol("SQLite authority metadata or goal identity is invalid");
    }
    return row.store_identity;
  }

  private parseJson(value: unknown, label: string): unknown {
    if (typeof value !== "string") protocol(`${label} is not retained JSON`);
    return JSON.parse(value) as unknown;
  }

  private requireDigest(value: unknown, label: string): string {
    if (typeof value !== "string" || !DIGEST.test(value)) protocol(`${label} is invalid`);
    return value;
  }

  private decodeCommitRow(row: Record<string, unknown>): SqliteCommitRow {
    const sequence = row.sequence;
    if (typeof sequence !== "string" || !/^[1-9]\d*$/.test(sequence)) protocol("Invalid SQLite transaction cursor");
    return {
      cursor: BigInt(sequence),
      operation_id: requireAuthorityStoreId(row.operation_id, "operation id"),
      commit_digest: this.requireDigest(row.commit_digest, "SQLite commit digest"),
      state_digest: this.requireDigest(row.state_digest, "SQLite state digest"),
      parent_state_digest: row.parent_state_digest === null
        ? null : this.requireDigest(row.parent_state_digest, "SQLite parent state digest"),
      delta: decodeAuthorityStateDelta(this.parseJson(row.delta, "SQLite authority state delta")),
      events: canonicalAuthorityObjectList(this.parseJson(row.events, "SQLite events"), "SQLite events"),
      receipts: canonicalAuthorityObjectList(this.parseJson(row.receipts, "SQLite receipts"), "SQLite receipts"),
    };
  }

  /**
   * Rebuild the exact projection this row published and revalidate its proof.
   *
   * `base` is either the state immediately before the row, or the checkpoint
   * that row must equal byte for byte. Both forms recompute the commit digest,
   * so a torn delta, a forged projection or edited events/receipts fail closed.
   */
  private verifyCommitRow(
    row: SqliteCommitRow,
    identity: string,
    base: {kind: "predecessor" | "sealed"; state: SqliteStateCursor},
  ): SqliteVerifiedCommit {
    let projection: JsonObject;
    if (base.kind === "sealed") {
      if (row.cursor !== base.state.cursor) protocol("SQLite authority state log window is invalid");
      if (row.state_digest !== base.state.digest) protocol("SQLite authority checkpoint state digest mismatch");
      projection = base.state.projection;
    } else {
      if (row.cursor !== base.state.cursor + 1n) protocol("SQLite authority state log cursor lineage is invalid");
      // The first retained commit is the root of the chain and carries no
      // parent digest; every later commit must name the state it extends.
      const parentMatches = row.cursor === 1n
        ? row.parent_state_digest === null
        : row.parent_state_digest === base.state.digest;
      if (!parentMatches) protocol("SQLite authority state log parent lineage is invalid");
      projection = applyAuthorityStateDelta(base.state.projection, row.delta);
      if (authorityStateDigest(projection) !== row.state_digest) {
        protocol("SQLite authority state log digest mismatch");
      }
    }
    const expected = commitDigest(identity, row.cursor, row.operation_id, projection, row.events, row.receipts);
    if (row.commit_digest !== expected) protocol("SQLite committed row digest mismatch");
    return {
      state: {cursor: row.cursor, projection, digest: row.state_digest},
      transaction: {cursor: row.cursor.toString(), provider_revision: `${identity}:${row.cursor}`,
        operation_id: row.operation_id, projection, events: row.events, receipts: row.receipts},
    };
  }

  private loadCheckpoint(db: DatabaseSync, cursor: bigint): SqliteStateCursor {
    const expected = authorityStateCheckpointCursor(cursor);
    const row = db.prepare(
      "SELECT cursor, projection, projection_digest FROM checkpoints WHERE cursor = ?",
    ).get(expected.toString());
    if (!row) protocol("SQLite authority checkpoint is missing for its window");
    const projection = canonicalAuthorityObject(this.parseJson(row.projection, "SQLite checkpoint projection"),
      "SQLite checkpoint projection");
    const digest = this.requireDigest(row.projection_digest, "SQLite checkpoint digest");
    if (authorityStateDigest(projection) !== digest) protocol("SQLite authority checkpoint digest mismatch");
    return {cursor: expected, projection, digest};
  }

  private windowRows(db: DatabaseSync, from: bigint, to: bigint): Record<string, unknown>[] {
    const rows = db.prepare(`SELECT ${COMMIT_COLUMNS} FROM commits WHERE cursor >= ? AND cursor <= ? ORDER BY cursor`)
      .all(from.toString(), to.toString()) as unknown as Record<string, unknown>[];
    if (BigInt(rows.length) !== to - from + 1n) {
      protocol("SQLite authority state log does not cover its checkpoint window");
    }
    return rows;
  }

  /**
   * Verify one contiguous span of retained transactions.
   *
   * Recovery starts at the checkpoint covering `from`, so it is bounded by the
   * checkpoint interval plus the caller's own request span. Every row from the
   * checkpoint through `to` must be present and must reconstruct the next
   * state; history outside the requested span is verified when it is read or
   * when `verifyAuthorityHistory` audits the complete archive.
   */
  private verifiedRange(db: DatabaseSync, from: bigint, to: bigint): SqliteCommitWindow {
    const identity = this.identity(db);
    const checkpoint = this.loadCheckpoint(db, from);
    const rows = this.windowRows(db, checkpoint.cursor, to);
    let state: SqliteStateCursor = checkpoint;
    const transactions: AuthorityStoreCommittedTransaction[] = [];
    for (const raw of rows) {
      const row = this.decodeCommitRow(raw);
      const verified = this.verifyCommitRow(row, identity,
        row.cursor === checkpoint.cursor ? {kind: "sealed", state} : {kind: "predecessor", state});
      state = verified.state;
      if (row.cursor >= from) transactions.push(verified.transaction);
    }
    return {identity, checkpoint, transactions, state};
  }

  /** The live head, proven without materializing retained history. */
  private current(db: DatabaseSync):
  {state: SqliteStateCursor; provider_revision: string; identity: string} | null {
    const identity = this.identity(db);
    // The live head is proven from its own evidence instead of by replaying how
    // it was reached: the head row's digest covers the live projection, the
    // retained transaction at that cursor must carry the same state digest and
    // must reproduce its exact commit proof, and the cursor bounds must stay
    // contiguous with the head. Neither cost grows with retained history.
    const bounds = db.prepare(`SELECT
      (SELECT CAST(MIN(cursor) AS TEXT) FROM commits) AS first,
      (SELECT CAST(MAX(cursor) AS TEXT) FROM commits) AS last,
      CAST((SELECT COUNT(*) FROM commits) AS TEXT) AS count`).get();
    const row = db.prepare(
      "SELECT CAST(cursor AS TEXT) AS sequence, projection, state_digest FROM head WHERE singleton = 1",
    ).get();
    if (!row) {
      if (db.prepare("SELECT 1 FROM commits LIMIT 1").get() ||
          db.prepare("SELECT 1 FROM checkpoints LIMIT 1").get()) {
        protocol("SQLite authority head is missing for retained transactions");
      }
      return null;
    }
    if (typeof row.sequence !== "string" || !/^[1-9]\d*$/.test(row.sequence)) {
      protocol("Invalid SQLite authority head cursor");
    }
    const cursor = BigInt(row.sequence);
    if (!(bounds?.count === "0" && bounds.last === null) &&
        (bounds?.first !== "1" || bounds.last !== bounds.count || bounds.last !== row.sequence)) {
      protocol("SQLite authority store is not a contiguous committed lineage");
    }
    const projection = canonicalAuthorityObject(this.parseJson(row.projection, "SQLite head projection"),
      "SQLite head projection");
    const digest = this.requireDigest(row.state_digest, "SQLite head state digest");
    if (authorityStateDigest(projection) !== digest) protocol("SQLite authority head digest mismatch");
    const retained = db.prepare(`SELECT ${COMMIT_COLUMNS} FROM commits WHERE cursor = ?`).get(row.sequence);
    if (!retained) protocol("SQLite authority head has no retained transaction");
    const newest = this.decodeCommitRow(retained as unknown as Record<string, unknown>);
    if (newest.state_digest !== digest) protocol("SQLite authority head state digest is not retained");
    const parent = cursor === 1n ? null : db.prepare("SELECT state_digest FROM commits WHERE cursor = ?")
      .get((cursor - 1n).toString());
    if (cursor === 1n
      ? newest.parent_state_digest !== null
      : newest.parent_state_digest !== parent?.state_digest) {
      protocol("SQLite authority head parent lineage is invalid");
    }
    if (newest.commit_digest !==
      commitDigest(identity, cursor, newest.operation_id, projection, newest.events, newest.receipts)) {
      protocol("SQLite committed row digest mismatch");
    }
    // Recovery of this head resumes from one retained checkpoint, so its
    // existence is part of the live contract even though its content is proven
    // when history is actually materialized.
    if (!db.prepare("SELECT 1 FROM checkpoints WHERE cursor = ?")
      .get(authorityStateCheckpointCursor(cursor).toString())) {
      protocol("SQLite authority checkpoint is missing for the live window");
    }
    return {state: {cursor, projection, digest}, provider_revision: `${identity}:${cursor}`, identity};
  }

  async storeIdentity(): Promise<AuthorityStoreIdentityResult> {
    let db: DatabaseSync | null = null;
    try {
      db = this.open(!this.existingOnly);
      if (!db) return {status: "unavailable", reason_code: "store_identity_unavailable", reason: "SQLite authority database is missing"};
      return {status: "available", store_identity: this.identity(db)};
    }
    catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }

  async loadAuthority(): Promise<AuthorityStoreLoadResult> {
    let db: DatabaseSync | null = null;
    try {
      db = this.open(false);
      if (!db) return {status: "missing"};
      db.exec("BEGIN");
      const current = this.current(db);
      if (current === null) return {status: "missing"};
      return {status: "loaded", cursor: current.state.cursor.toString(),
        provider_revision: current.provider_revision, head: current.state.projection};
    } catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }

  /** No await between BEGIN and ROLLBACK: another DatabaseSync request must not
   * block this event loop while the transaction holder awaits filesystem I/O.
   * The transaction excludes writers; it cannot roll back external run files. */
  async withCheckpointHead(save: (head: AuthorityStoreHead, identity: string) => JsonObject): Promise<JsonObject> {
    const db = this.open(true);
    if (!db) throw new Error("checkpoint authority is missing");
    let active = false;
    try {
      db.exec("BEGIN IMMEDIATE");
      active = true;
      const current = this.current(db);
      if (!current) throw new Error("checkpoint authority head is missing");
      const result = save({head: current.state.projection,
        provider_revision: current.provider_revision, cursor: current.state.cursor.toString()}, current.identity);
      if (result instanceof Promise) throw new Error("checkpoint save must be synchronous");
      return result;
    } finally {
      try { if (active) db.exec("ROLLBACK"); }
      finally { db.close(); }
    }
  }

  async commitAuthority(commit: AuthorityStoreCommit): Promise<AuthorityStoreCommitResult> {
    let normalized: AuthorityStoreCommit;
    try {
      normalized = normalizeAuthorityStoreCommit(commit);
      if (normalized.expected_provider_revision !== null && !REVISION.test(normalized.expected_provider_revision)) {
        protocol("Invalid expected SQLite provider revision");
      }
      if (canonicalAuthorityBytes(normalized).byteLength > 16 * 1024 * 1024) {
        return {status: "failed", reason_code: "store_capacity_exhausted", reason: "SQLite commit exceeds 16 MiB"};
      }
    } catch (error) { return {status: "failed", reason_code: "invalid_commit_request",
      reason: error instanceof Error ? error.message : "Invalid commit"}; }
    let db: DatabaseSync | null = null;
    let transactionOpen = false;
    let committing = false;
    try {
      db = this.open(true)!;
      db.exec("BEGIN IMMEDIATE");
      transactionOpen = true;
      const current = this.current(db);
      const cursor = current?.state.cursor ?? null;
      const revision = current?.provider_revision ?? null;
      let conflict: "provider_revision_mismatch" | "operation_id_exists" | null = null;
      if (revision !== normalized.expected_provider_revision) conflict = "provider_revision_mismatch";
      else if (db.prepare("SELECT 1 FROM commits WHERE operation_id = ?").get(normalized.operation_id)) {
        conflict = "operation_id_exists";
      }
      if (conflict) {
        db.exec("ROLLBACK"); transactionOpen = false;
        return {status: "conflict", conflict_kind: conflict, current_provider_revision: revision,
          current_cursor: cursor?.toString() ?? null};
      }
      const identity = current?.identity ?? this.identity(db);
      const next = (cursor ?? 0n) + 1n;
      if (next > MAX_SEQUENCE) protocol("SQLite authority sequence exhausted");
      const before = current?.state ?? {cursor: 0n, projection: {}, digest: ""};
      const nextState = canonicalAuthorityObject(normalized.next_projection, "SQLite authority state");
      const delta = authorityStateDelta(before.projection, nextState);
      // The encoder is proved before it is persisted: replaying the stored
      // delta must reproduce the committed projection byte for byte.
      if (!authorityStateDeltaReconstructs(before.projection, delta, nextState)) {
        protocol("SQLite authority state delta does not reconstruct its commit");
      }
      const stateDigest = authorityStateDigest(nextState);
      if (isAuthorityStateCheckpoint(next)) {
        db.prepare("INSERT INTO checkpoints VALUES (?, ?, ?)")
          .run(next.toString(), JSON.stringify(nextState), stateDigest);
      }
      db.prepare("INSERT INTO commits VALUES (?, ?, ?, ?, ?, ?, ?, ?)").run(next.toString(),
        normalized.operation_id,
        commitDigest(identity, next, normalized.operation_id, nextState, normalized.events, normalized.receipts),
        stateDigest, cursor === null ? null : before.digest,
        JSON.stringify(delta), JSON.stringify(normalized.events), JSON.stringify(normalized.receipts));
      db.prepare(`INSERT INTO head VALUES (1, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET cursor = excluded.cursor, projection = excluded.projection,
          state_digest = excluded.state_digest`).run(next.toString(), JSON.stringify(nextState), stateDigest);
      committing = true;
      db.exec("COMMIT"); transactionOpen = false;
      return {status: "applied", provider_revision: `${identity}:${next}`, cursor: next.toString()};
    } catch (error) {
      if (transactionOpen) { try { db?.exec("ROLLBACK"); } catch { /* close also abandons the transaction */ } }
      if (committing) return {status: "ambiguous", reason_code: "commit_outcome_unknown",
        reason: "SQLite COMMIT outcome is unknown; reconcile by operation receipt"};
      return {status: "failed", reason_code: error instanceof AuthorityStoreProtocolError || error instanceof SyntaxError
        ? "provider_protocol_violation" : "provider_transaction_failed",
      reason: error instanceof AuthorityStoreProtocolError || error instanceof SyntaxError ? error.message : "SQLite transaction failed before COMMIT"};
    } finally { db?.close(); }
  }

  async readReceipt(operationId: string): Promise<AuthorityStoreReceiptResult> {
    const batch = await this.readReceipts([operationId]);
    return batch.status === "receipts" ? batch.results[0]! : batch;
  }

  /** One snapshot and one proof per touched checkpoint window. The operation
   * index still resolves each requested ID; scanned data never substitutes for
   * lookup, and no verified state escapes this transaction as a cache. */
  async readReceipts(operationIds: readonly string[]): Promise<AuthorityStoreReceiptBatchResult> {
    let db: DatabaseSync | null = null;
    try {
      if (!Array.isArray(operationIds) || operationIds.length < 1 || operationIds.length > 64) {
        protocol("receipt batch requires 1..64 operations");
      }
      for (const id of operationIds) requireAuthorityStoreId(id, "operation id");
      const missing = (): AuthorityStoreReceiptBatchResult => ({status: "receipts",
        results: operationIds.map(() => ({status: "missing"}))});
      db = this.open(false);
      if (!db) return missing();
      db.exec("BEGIN");
      const head = this.current(db);
      if (head === null) return missing();
      const ranges = new Map<bigint, {from: bigint; to: bigint}>();
      const selected = operationIds.map(id => {
        const row = db!.prepare(`SELECT ${COMMIT_COLUMNS} FROM commits WHERE operation_id = ?`)
          .get(id) as unknown as Record<string, unknown> | undefined;
        if (!row) return null;
        const cursor = this.decodeCommitRow(row).cursor;
        const checkpoint = authorityStateCheckpointCursor(cursor);
        const range = ranges.get(checkpoint);
        ranges.set(checkpoint, {from: range && range.from < cursor ? range.from : cursor,
          to: range && range.to > cursor ? range.to : cursor});
        return cursor;
      });
      // Retain only requested receipts, not all reconstructed projections from
      // every window. Even sparse requests never scan the gap between windows.
      const verified = new Map<string, AuthorityStoreReceiptResult>();
      const wanted = new Set(operationIds);
      for (const {from, to} of ranges.values()) {
        const window = this.verifiedRange(db, from, to);
        for (const row of window.transactions) if (wanted.has(row.operation_id)) {
          verified.set(row.operation_id, {status: "found", cursor: row.cursor,
            provider_revision: row.provider_revision, receipts: row.receipts});
        }
      }
      const results = operationIds.map((id, index): AuthorityStoreReceiptResult => {
        const cursor = selected[index];
        if (cursor === null) return {status: "missing"};
        const result = verified.get(id);
        if (result?.status !== "found" || result.cursor !== cursor!.toString()) {
          protocol("SQLite authority receipt is not part of its retained window");
        }
        return result;
      });
      return {status: "receipts", results};
    } catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }

  async scanCommitted(afterCursor: string | null, limit: number): Promise<AuthorityStoreScanResult> {
    const scan = AuthorityJournalScan.prepare(afterCursor, limit);
    if (!(scan instanceof AuthorityJournalScan)) return scan;
    let db: DatabaseSync | null = null;
    try {
      db = this.open(false);
      if (!db) return scan.page([], null);
      db.exec("BEGIN");
      const head = this.current(db);
      const range = scan.rangeFailure(head?.state.cursor.toString() ?? null);
      if (range) return range;
      const rows = db.prepare(`SELECT ${COMMIT_COLUMNS} FROM commits WHERE cursor > ? ORDER BY cursor LIMIT ?`)
        .all(scan.offset.toString(), BigInt(limit) + 1n) as unknown as Record<string, unknown>[];
      // One bounded pass verifies the whole returned page: recovery starts at
      // the checkpoint covering the first row, and the lookahead row both
      // proves has_more and closes the verified span.
      const window = rows.length === 0
        ? null
        : this.verifiedRange(db, BigInt(String(rows[0]!.sequence)),
          BigInt(String(rows[rows.length - 1]!.sequence)));
      const verified = rows.map(raw => {
        const cursor = BigInt(String(raw.sequence)).toString();
        const transaction = window?.transactions.find(item => item.cursor === cursor);
        if (!transaction) protocol("SQLite authority scan row is not part of its retained window");
        return transaction;
      });
      return scan.page(verified, head === null ? null : {cursor: head.state.cursor.toString(),
        provider_revision: head.provider_revision, head: head.state.projection});
    } catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }

  /**
   * Linear full-history audit.
   *
   * Normal reads verify the live checkpoint window and every row they return.
   * A gap, rewritten delta or rewritten checkpoint outside that window is
   * proven here instead, so recovery and qualification can demand the complete
   * archive without making every read pay for all history.
   */
  async verifyAuthorityHistory(): Promise<SqliteAuthorityHistoryAudit | AuthorityStoreReadFailure> {
    let db: DatabaseSync | null = null;
    try {
      db = this.open(false);
      if (!db) {
        return {status: "failed", reason_code: "provider_read_unavailable", reason: "SQLite authority database is missing"};
      }
      db.exec("BEGIN");
      const identity = this.identity(db);
      const head = this.current(db);
      if (head === null) {
        return {schema_version: "loopx_sqlite_authority_history_audit_v0", status: "verified", commits: 0, checkpoints: 0};
      }
      const counted = db.prepare("SELECT COUNT(*) AS count FROM checkpoints").get();
      let state: SqliteStateCursor | null = null;
      let cursor = 0n;
      let commits = 0;
      for (;;) {
        const rows = db.prepare(`SELECT ${COMMIT_COLUMNS} FROM commits WHERE cursor > ? ORDER BY cursor LIMIT ?`)
          .all(cursor.toString(), BigInt(AUDIT_PAGE)) as unknown as Record<string, unknown>[];
        if (rows.length === 0) break;
        for (const raw of rows) {
          const row = this.decodeCommitRow(raw);
          // The exact delta chain is proved from the empty root, so a retained
          // delta can never diverge from the history it claims to extend.
          const predecessor: SqliteStateCursor = state ?? {cursor: 0n, projection: {}, digest: ""};
          const replayed: SqliteStateCursor = this.verifyCommitRow(row, identity,
            {kind: "predecessor", state: predecessor}).state;
          if (isAuthorityStateCheckpoint(row.cursor)) {
            const checkpoint = this.loadCheckpoint(db, row.cursor);
            const sealed = this.verifyCommitRow(row, identity, {kind: "sealed", state: checkpoint}).state;
            if (sealed.digest !== replayed.digest ||
                !canonicalAuthorityBytes(sealed.projection).equals(canonicalAuthorityBytes(replayed.projection))) {
              protocol("SQLite authority checkpoint does not match retained history");
            }
          }
          state = replayed;
          cursor = row.cursor;
          commits += 1;
        }
      }
      if (state === null || state.cursor !== head.state.cursor || state.digest !== head.state.digest) {
        protocol("SQLite authority history does not reach the head");
      }
      return {schema_version: "loopx_sqlite_authority_history_audit_v0", status: "verified", commits,
        checkpoints: Number(counted?.count ?? 0)};
    } catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }

  /** Retained-byte and recovery-bound evidence for the local profile. */
  async boundedProfile(): Promise<SqliteAuthorityBoundedProfile | AuthorityStoreReadFailure> {
    let db: DatabaseSync | null = null;
    try {
      db = this.open(false);
      if (!db) {
        return {status: "failed", reason_code: "provider_read_unavailable", reason: "SQLite authority database is missing"};
      }
      db.exec("BEGIN");
      const head = this.current(db);
      if (head === null) {
        return {status: "failed", reason_code: "provider_read_unavailable", reason: "SQLite authority database is empty"};
      }
      const counted = db.prepare("SELECT COUNT(*) AS count FROM commits").get();
      const retained = db.prepare(`SELECT
        (SELECT COUNT(*) FROM checkpoints) AS checkpoints,
        (SELECT COALESCE(SUM(length(projection)), 0) FROM checkpoints) AS projection_bytes,
        (SELECT COALESCE(SUM(length(delta)), 0) FROM commits) AS delta_bytes,
        (SELECT COALESCE(SUM(length(events) + length(receipts)), 0) FROM commits) AS payload_bytes`).get();
      const cursor = head.state.cursor;
      return {
        schema_version: "loopx_sqlite_authority_bounded_profile_v0",
        status: "available",
        cursor: cursor.toString(),
        commits: Number(counted?.count ?? 0),
        checkpoints: Number(retained?.checkpoints ?? 0),
        checkpoint_interval: AUTHORITY_STATE_CHECKPOINT_INTERVAL,
        replay_budget_commits: authorityStateReplayBudget(),
        recovery_tail_commits: Number(cursor - authorityStateCheckpointCursor(cursor)),
        retained_projection_bytes: Number(retained?.projection_bytes ?? 0),
        retained_delta_bytes: Number(retained?.delta_bytes ?? 0),
        retained_payload_bytes: Number(retained?.payload_bytes ?? 0),
        database_bytes: fileBytes(this.path),
        wal_bytes: fileBytes(`${this.path}-wal`),
        shm_bytes: fileBytes(`${this.path}-shm`),
      };
    } catch (error) { return readFailure(error); }
    finally { db?.close(); }
  }
}

/** The exact v0 commit proof, retained so stored digests stay comparable. */
export function commitDigest(
  identity: string,
  cursor: bigint,
  operationId: string,
  projection: JsonObject,
  events: readonly JsonObject[],
  receipts: readonly JsonObject[],
): string {
  return canonicalAuthoritySha256({
    expected_provider_revision: cursor === 1n ? null : `${identity}:${cursor - 1n}`,
    operation_id: operationId, next_projection: projection, events, receipts,
  });
}
