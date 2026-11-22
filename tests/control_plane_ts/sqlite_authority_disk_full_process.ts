/** A real instrumented writer for the disk-full injection test.
 *
 * The storage-exhaustion quota is injected only into this disposable child:
 * once armed, every subsequent write connection freezes `PRAGMA
 * max_page_count` at the page count the database already occupies, so any
 * write that must grow the file fails through the engine's own SQLITE_FULL
 * path while writes that fit already-allocated pages still pass — the same
 * shape a genuinely full disk has. Real SQL, rollback and connection
 * lifecycle stay in the production entrypoint; no fault hooks enter product
 * code and the parent process never patches anything. A fresh (never armed)
 * child models capacity restoration, because the store opens a new
 * connection per call and the next session's default quota is the raised
 * limit. Spawned only by `sqlite_authority_disk_full.test.ts`, never
 * imported. */
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {createRequire} from "node:module";
import * as readline from "node:readline";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

export const DISK_FULL_PROJECTION_SCHEMA = "loopx_sqlite_disk_full_injection_v0";

/** One commit request; the projection is rebuilt deterministically from the
 * parameters so a retry can be byte-identical without shipping padding over
 * the pipe. Mirrors the plan duplication rule of the concurrent-writer test:
 * the wire contract is declared on both sides by design. */
interface DiskFullCommand {
  seq: number;
  kind: "arm" | "commit" | "load" | "receipt" | "audit" | "scan" | "page-count" | "exit";
  operation_id?: string;
  expected_provider_revision?: string | null;
  marker?: string;
  sequence?: number;
  padding_bytes?: number;
  padding_char?: string;
  after?: string | null;
  limit?: number;
}

const [directory, goalId] = process.argv.slice(2);
const store = new SqliteAuthorityStore(directory!, goalId!);

// The quota injection: only this child's write connections are constrained.
const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
const originalExec = DatabaseSync.prototype.exec;
let armed = false;
let frozenPages = 0;
DatabaseSync.prototype.exec = function(sql: string) {
  if (armed && sql === "BEGIN IMMEDIATE") {
    this.exec(`PRAGMA max_page_count=${frozenPages}`);
  }
  return originalExec.call(this, sql);
};

function projectionFor(command: DiskFullCommand): JsonObject {
  const projection: JsonObject = {schema_version: DISK_FULL_PROJECTION_SCHEMA,
    marker: command.marker ?? "unmarked", sequence: command.sequence ?? 0};
  const bytes = command.padding_bytes ?? 0;
  if (bytes > 0) projection.capacity_padding = (command.padding_char ?? "p").repeat(bytes);
  return projection;
}

const reply = (payload: Record<string, unknown>): void => {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
};

await new Promise<void>(resolve => process.stdout.write("ready\n", () => resolve()));
const lines = readline.createInterface({input: process.stdin});
for await (const line of lines) {
  const trimmed = line.trim();
  if (trimmed === "") continue;
  let command: DiskFullCommand;
  try {
    command = JSON.parse(trimmed) as DiskFullCommand;
  } catch {
    reply({kind: "protocol-error", line: trimmed.slice(0, 200)});
    process.exit(1);
  }
  try {
    if (command.kind === "exit") {
      reply({seq: command.seq, kind: "exited"});
      process.exit(0);
    }
    if (command.kind === "arm") {
      const probe = new DatabaseSync(store.path, {readOnly: true});
      frozenPages = Number(probe.prepare("PRAGMA page_count").get()?.page_count ?? 0);
      probe.close();
      armed = frozenPages > 0;
      reply({seq: command.seq, kind: "armed", page_count: frozenPages});
      continue;
    }
    if (command.kind === "commit") {
      const events: JsonObject[] = [{kind: "disk-full-probe", operation_id: command.operation_id!,
        sequence: command.sequence ?? 0}];
      const receipts: JsonObject[] = [{operation_id: command.operation_id!,
        sequence: command.sequence ?? 0}];
      const result = await store.commitAuthority({
        expected_provider_revision: command.expected_provider_revision ?? null,
        operation_id: command.operation_id!, next_projection: projectionFor(command),
        events, receipts});
      reply({seq: command.seq, kind: "commit-result", operation_id: command.operation_id, result});
      continue;
    }
    if (command.kind === "load") {
      const result = await store.loadAuthority();
      reply({seq: command.seq, kind: "load-result", result: result.status === "loaded"
        ? {status: result.status, cursor: result.cursor, provider_revision: result.provider_revision,
          head: result.head}
        : result});
      continue;
    }
    if (command.kind === "receipt") {
      const result = await store.readReceipt(command.operation_id!);
      reply({seq: command.seq, kind: "receipt-result", result});
      continue;
    }
    if (command.kind === "audit") {
      const result = await store.verifyAuthorityHistory();
      reply({seq: command.seq, kind: "audit-result", result});
      continue;
    }
    if (command.kind === "scan") {
      const result = await store.scanCommitted(command.after ?? null, command.limit ?? 100);
      reply({seq: command.seq, kind: "scan-result", result: result.status === "page"
        ? {status: result.status, has_more: result.has_more,
          rows: result.transactions.map(row => ({cursor: row.cursor, operation_id: row.operation_id}))}
        : result});
      continue;
    }
    if (command.kind === "page-count") {
      const probe = new DatabaseSync(store.path, {readOnly: true});
      const page_count = Number(probe.prepare("PRAGMA page_count").get()?.page_count ?? 0);
      probe.close();
      reply({seq: command.seq, kind: "page-count-result", page_count});
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
