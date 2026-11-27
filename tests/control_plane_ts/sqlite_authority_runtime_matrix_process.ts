/** A real runtime-matrix worker for the SQLite authority store.
 *
 * Spawned (never imported) by `sqlite_authority_runtime_matrix.test.ts`
 * under one specific Node executable: the OS/runtime qualification row is a
 * property of the process that actually owns `node:sqlite`, so every store
 * operation and every driver observation runs inside this child. The worker
 * only reports raw results; it never asserts on its own output — the parent
 * holds the authored ledger and every contract assertion.
 *
 * The `identity` command reports both the product identity probe and an
 * independent raw driver observation (direct `node:sqlite` query of
 * `sqlite_version()`/`sqlite_source_id()` plus the statement-finalization
 * behavior) so the parent can verify the documented `runtime_identity`
 * fields against the actual embedded driver rather than against the probe
 * under test. `run-ledger` chains a deterministic commit sequence (crossing
 * the documented 64-commit checkpoint window) in one batch; the remaining
 * commands expose the store's public read/audit surface and raw engine
 * facts for the parent's independent oracles.
 */
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {createRequire} from "node:module";
import * as readline from "node:readline";
import {existsSync, readdirSync} from "node:fs";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

export const MATRIX_PROJECTION_SCHEMA = "loopx_sqlite_runtime_matrix_v0";

/** One commit request; the projection is rebuilt deterministically from the
 * parameters so the parent's authored ledger and the worker's commits are
 * byte-comparable without shipping padding over the pipe. The wire contract
 * is declared on both sides by design (same rule as the disk-full and
 * concurrent-writer workers). */
interface MatrixCommand {
  seq: number;
  kind: "identity" | "store-identity" | "run-ledger" | "commit" | "load" | "receipt" | "scan"
    | "audit" | "files" | "engine" | "sibling-goal" | "exit";
  first?: number;
  count?: number;
  padding_bytes?: number;
  padding_char?: string;
  operation_id?: string;
  marker?: string;
  sequence?: number;
  expected_provider_revision?: string | null;
  after?: string | null;
  limit?: number;
  suffix?: string;
}

const [directory, goalId] = process.argv.slice(2);
const store = new SqliteAuthorityStore(directory!, goalId!);
let headRevision: string | null = null;
let sibling: SqliteAuthorityStore | null = null;

// Raw driver oracle: observes the embedded driver directly through
// node:sqlite without touching the product probe under test.
const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");

interface RawDriverOracle {
  node_version: string;
  platform: string;
  arch: string;
  driver_available: boolean;
  sqlite_version: string | null;
  sqlite_source_id: string | null;
  synchronous_statement_finalization: boolean | null;
}

function rawDriverOracle(): RawDriverOracle {
  const base = {node_version: process.version, platform: process.platform, arch: process.arch};
  try {
    const db = new DatabaseSync(":memory:");
    let statement: {get: () => unknown};
    let version = "";
    let sourceId = "";
    try {
      const row = db.prepare("SELECT sqlite_version() AS version, sqlite_source_id() AS source_id").get() as
        {version?: string | null; source_id?: string | null} | undefined;
      version = String(row?.version ?? "");
      sourceId = String(row?.source_id ?? "");
      statement = db.prepare("SELECT 1");
    } finally { db.close(); }
    let finalized = false;
    try { statement.get(); }
    catch (error) { finalized = (error as NodeJS.ErrnoException).code === "ERR_INVALID_STATE"; }
    return {...base, driver_available: true, sqlite_version: version, sqlite_source_id: sourceId,
      synchronous_statement_finalization: finalized};
  } catch {
    return {...base, driver_available: false, sqlite_version: null, sqlite_source_id: null,
      synchronous_statement_finalization: null};
  }
}

function projectionFor(marker: string, sequence: number,
  paddingBytes: number, paddingChar: string): JsonObject {
  const projection: JsonObject = {schema_version: MATRIX_PROJECTION_SCHEMA, marker, sequence};
  if (paddingBytes > 0) projection.runtime_padding = paddingChar.repeat(paddingBytes);
  return projection;
}

function commitInput(operationId: string, marker: string, sequence: number,
  paddingBytes: number, paddingChar: string, expected: string | null) {
  return {expected_provider_revision: expected, operation_id: operationId,
    next_projection: projectionFor(marker, sequence, paddingBytes, paddingChar),
    events: [{kind: "runtime-matrix", operation_id: operationId, sequence}],
    receipts: [{operation_id: operationId, sequence}]};
}

const reply = (payload: Record<string, unknown>): void => {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
};

await new Promise<void>(resolve => process.stdout.write("ready\n", () => resolve()));
const lines = readline.createInterface({input: process.stdin});
for await (const line of lines) {
  const trimmed = line.trim();
  if (trimmed === "") continue;
  let command: MatrixCommand;
  try {
    command = JSON.parse(trimmed) as MatrixCommand;
  } catch {
    reply({kind: "protocol-error", line: trimmed.slice(0, 200)});
    process.exit(1);
  }
  try {
    if (command.kind === "exit") {
      reply({seq: command.seq, kind: "exited"});
      process.exit(0);
    }
    if (command.kind === "identity") {
      reply({seq: command.seq, kind: "identity-result", identity: sqliteRuntimeIdentity(),
        oracle: rawDriverOracle()});
      continue;
    }
    if (command.kind === "store-identity") {
      reply({seq: command.seq, kind: "store-identity-result", result: await store.storeIdentity()});
      continue;
    }
    if (command.kind === "run-ledger") {
      const first = command.first ?? 1;
      const count = command.count ?? 0;
      const results: Record<string, unknown>[] = [];
      let expected: string | null;
      if (command.expected_provider_revision !== undefined) {
        expected = command.expected_provider_revision;
      } else if (headRevision !== null) {
        expected = headRevision;
      } else {
        // A fresh session continues from the store's current head (null on an
        // empty store); explicit revisions pin the chain instead.
        const current = await store.loadAuthority();
        expected = current.status === "loaded" ? current.provider_revision : null;
      }
      for (let sequence = first; sequence < first + count; sequence++) {
        const operationId = `matrix-${String(sequence).padStart(3, "0")}`;
        const result = await store.commitAuthority(commitInput(operationId, "matrix", sequence,
          command.padding_bytes ?? 0, command.padding_char ?? "p", expected)) as
          {status: string; provider_revision?: string; cursor?: string};
        results.push({sequence, operation_id: operationId, status: result.status,
          provider_revision: result.provider_revision ?? null, cursor: result.cursor ?? null});
        if (result.status === "applied") expected = result.provider_revision ?? null;
      }
      headRevision = expected;
      reply({seq: command.seq, kind: "run-ledger-result", results});
      continue;
    }
    if (command.kind === "commit") {
      const result = await store.commitAuthority(commitInput(command.operation_id!,
        command.marker ?? "matrix", command.sequence ?? 0, command.padding_bytes ?? 0,
        command.padding_char ?? "p",
        command.expected_provider_revision !== undefined ? command.expected_provider_revision : headRevision));
      reply({seq: command.seq, kind: "commit-result", operation_id: command.operation_id, result});
      continue;
    }
    if (command.kind === "load") {
      reply({seq: command.seq, kind: "load-result", result: await store.loadAuthority()});
      continue;
    }
    if (command.kind === "receipt") {
      reply({seq: command.seq, kind: "receipt-result", result: await store.readReceipt(command.operation_id!)});
      continue;
    }
    if (command.kind === "scan") {
      const result = await store.scanCommitted(command.after ?? null, command.limit ?? 100);
      reply({seq: command.seq, kind: "scan-result", result: result.status === "page"
        ? {status: result.status, has_more: result.has_more, transactions: result.transactions}
        : result});
      continue;
    }
    if (command.kind === "audit") {
      reply({seq: command.seq, kind: "audit-result", result: await store.verifyAuthorityHistory()});
      continue;
    }
    if (command.kind === "files") {
      let entries: string[] = [];
      try { entries = readdirSync(directory!).sort(); }
      catch { /* a fresh runtime that fails admission may never create it */ }
      reply({seq: command.seq, kind: "files-result", path: store.path, path_exists: existsSync(store.path),
        directory_exists: existsSync(directory!), entries});
      continue;
    }
    if (command.kind === "engine") {
      if (!existsSync(store.path)) {
        reply({seq: command.seq, kind: "engine-result", exists: false});
        continue;
      }
      const db = new DatabaseSync(store.path, {readOnly: true});
      try {
        const userVersion = Number(db.prepare("PRAGMA user_version").get()?.user_version ?? 0);
        const journalMode = String(db.prepare("PRAGMA journal_mode").get()?.journal_mode ?? "");
        const integrity = (db.prepare("PRAGMA integrity_check").all() as {integrity_check: string}[])
          .map(row => row.integrity_check);
        reply({seq: command.seq, kind: "engine-result", exists: true, user_version: userVersion,
          journal_mode: journalMode, integrity});
      } finally { db.close(); }
      continue;
    }
    if (command.kind === "sibling-goal") {
      sibling ??= new SqliteAuthorityStore(directory!, goalId! + (command.suffix ?? "-sibling"));
      const commit = await sibling.commitAuthority({expected_provider_revision: null,
        operation_id: "sibling-001", next_projection: {schema_version: MATRIX_PROJECTION_SCHEMA,
          marker: "sibling", sequence: 1},
        events: [{kind: "runtime-matrix-sibling", sequence: 1}],
        receipts: [{operation_id: "sibling-001", sequence: 1}]});
      const load = await sibling.loadAuthority();
      reply({seq: command.seq, kind: "sibling-goal-result", main_path: store.path, sibling_path: sibling.path,
        commit, load});
      continue;
    }
    reply({kind: "protocol-error", unknown_kind: command.kind});
    process.exit(1);
  } catch (error) {
    reply({seq: command.seq, kind: "command-error",
      message: error instanceof Error ? error.message : String(error)});
    process.exit(1);
  }
}
