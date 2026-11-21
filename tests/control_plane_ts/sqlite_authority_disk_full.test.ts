import assert from "node:assert/strict";
import {execFileSync, spawn, spawnSync} from "node:child_process";
import {mkdtemp, rm} from "node:fs/promises";
import {createRequire} from "node:module";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

import {SqliteAuthorityStore, sqliteAuthorityPath} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

/**
 * Storage-exhaustion matrix for the SQLite authority store, demanded by RFC
 * 7.2 ("disk-full") and the 2026-11-20 slice-4 kickoff on issue #4224. The
 * assertions bind to the contract documented in
 * `docs/reference/sqlite-authority-store.md`: writes run `BEGIN IMMEDIATE`
 * with WAL and `synchronous=FULL`, "Before-COMMIT failures roll back; a
 * COMMIT error reports an ambiguous outcome for receipt reconciliation", and
 * the real-process regressions' `max_page_count` exhaustion is the
 * injected-limit path — "small disposable-database tests, not power-loss,
 * operating-system ENOSPC or large-history recovery qualification". This
 * file keeps that distinction load-bearing instead of inventing a new one:
 *
 * 1. The primary injection freezes `PRAGMA max_page_count` at the occupied
 *    page count on every write connection of a real child process, so writes
 *    that must grow the file fail through SQLite's own SQLITE_FULL path
 *    while writes fitting already-allocated pages still pass — the same
 *    shape a full disk has. Exhaustion is forced on the checkpoint-bearing
 *    commit (cursor 65 publishes `checkpoints` + `commits` + `head` in one
 *    transaction), retries fail durably while the quota stays armed, reads
 *    keep proving the intact state inside the constrained process, and a
 *    never-armed session (capacity restored) commits again without manual
 *    repair — including fresh reuse of an operation id whose commit failed.
 * 2. A contrast control repeats the identical padded workload with no quota
 *    (same checkpoint commit applies; the byte-identical retry replays
 *    `applied`) and shows the logical 16 MiB request cap failing with its
 *    own typed `store_capacity_exhausted` row, proving the injection test's
 *    `provider_transaction_failed` assertions bind to engine quota
 *    exhaustion, not to payload shape or the logical cap.
 * 3. One real-OS case fills a bounded macOS sparse volume (hdiutil) until
 *    the kernel returns ENOSPC: the failing commit is typed and never fakes
 *    success, reads fail closed, and after the volume grows the store
 *    resumes on exactly the applied prefix with the failed operation
 *    missing. This is the real-ENOSPC side the reference explicitly holds
 *    out of the injected-limit qualification; it stays a small disposable
 *    volume, not large-history qualification.
 *
 * Beyond #4328's `capacity-full` case (one failing commit on a one-commit
 * store, status-only assertion), these tests pin: the typed error
 * classification, exhaustion inside a checkpoint-publishing transaction,
 * durable zero-footprint retries under continued exhaustion, provider reads
 * under the constraint, raw-engine oracles (integrity_check, exact row
 * snapshot, frozen page count), post-restoration service without repair,
 * fresh reuse of the failed operation id, the un-injected contrast, and the
 * real-ENOSPC path.
 */
const GOAL_ID = "goal-disk-full";
const SEED_SCHEMA = "loopx_sqlite_disk_full_seed_v0";
// Duplicated from sqlite_authority_disk_full_process.ts by design: the
// helper is spawned, never imported, so the wire contract lives on both
// sides (same rule as the concurrent-writer test).
const INJECTION_SCHEMA = "loopx_sqlite_disk_full_injection_v0";
const VOLUME_SCHEMA = "loopx_sqlite_disk_full_volume_v0";
const AUDIT_VERIFIED_64 = {schema_version: "loopx_sqlite_authority_history_audit_v0",
  status: "verified", commits: 64, checkpoints: 1};
const PROCESS_PATH = new URL("./sqlite_authority_disk_full_process.ts", import.meta.url);
const CHILD_DEADLINE_MS = 30000;

const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");

/** One planned commit plus the exact proof this test expects to read back. */
interface LedgerEntry {
  operationId: string;
  projection: JsonObject;
  events: readonly JsonObject[];
  receipts: readonly JsonObject[];
}

function seedEntry(sequence: number): LedgerEntry {
  const operationId = `seed-${String(sequence).padStart(3, "0")}`;
  return {
    operationId,
    projection: {schema_version: SEED_SCHEMA, marker: "seed", sequence},
    events: [{kind: "disk-full-seed", sequence}],
    receipts: [{operation_id: operationId, sequence}],
  };
}

function injectionEntry(operationId: string, marker: string, sequence: number,
  paddingBytes: number, paddingChar: string): LedgerEntry {
  const projection: JsonObject = {schema_version: INJECTION_SCHEMA, marker, sequence};
  if (paddingBytes > 0) projection.capacity_padding = paddingChar.repeat(paddingBytes);
  return {
    operationId,
    projection,
    events: [{kind: "disk-full-probe", operation_id: operationId, sequence}],
    receipts: [{operation_id: operationId, sequence}],
  };
}

function volumeEntry(sequence: number): LedgerEntry {
  const operationId = `volume-fill-${String(sequence).padStart(2, "0")}`;
  return {
    operationId,
    projection: {schema_version: VOLUME_SCHEMA, marker: "volume-fill",
      sequence, capacity_padding: `${sequence}:`.repeat(512 * 1024)},
    events: [{kind: "disk-full-volume", sequence}],
    receipts: [{operation_id: operationId, sequence}],
  };
}

/** Commit an entry through the real in-process provider and return its
 * applied provider revision. */
async function commitEntry(store: SqliteAuthorityStore, entry: LedgerEntry,
  expected: string | null): Promise<{status: string; revision: string | null; cursor: string | null; result: unknown}> {
  const result = await store.commitAuthority({expected_provider_revision: expected,
    operation_id: entry.operationId, next_projection: entry.projection,
    events: [...entry.events], receipts: [...entry.receipts]});
  const shaped = result as {status: string; provider_revision?: string; cursor?: string};
  return {status: shaped.status, revision: shaped.provider_revision ?? null,
    cursor: shaped.cursor ?? null, result};
}

/**
 * Independent engine oracle: integrity check, table counts, head cursor,
 * file page count and the raw retained events/receipts payload — read
 * through node:sqlite directly, not through the store's proof code, so the
 * store is never used to assert on its own output.
 */
function rawSnapshot(path: string): {
  integrity: string[];
  commits: number;
  checkpoints: number;
  headCursor: string;
  pageCount: number;
  rows: {operation_id: string; events: unknown; receipts: unknown}[];
} {
  const db = new DatabaseSync(path, {readOnly: true});
  try {
    // node:sqlite returns null-prototype rows; normalize to plain strings so
    // the oracle compares values, not object shapes.
    const integrity = (db.prepare("PRAGMA integrity_check").all() as {integrity_check: string}[])
      .map(row => row.integrity_check);
    const counts = db.prepare(`SELECT
      (SELECT COUNT(*) FROM commits) AS commits,
      (SELECT COUNT(*) FROM checkpoints) AS checkpoints,
      (SELECT CAST(cursor AS TEXT) FROM head WHERE singleton = 1) AS head_cursor`).get() as {
      commits: number; checkpoints: number; head_cursor: string};
    const pageCount = Number(db.prepare("PRAGMA page_count").get()?.page_count ?? 0);
    const rows = (db.prepare("SELECT operation_id, events, receipts FROM commits ORDER BY cursor")
      .all() as {operation_id: string; events: string; receipts: string}[])
      .map(row => ({operation_id: row.operation_id,
        events: JSON.parse(row.events) as unknown, receipts: JSON.parse(row.receipts) as unknown}));
    return {integrity, commits: counts.commits, checkpoints: counts.checkpoints,
      headCursor: counts.head_cursor, pageCount, rows};
  } finally { db.close(); }
}

/** The committed projections/events/receipts read back through the provider
 * must equal the ledger byte for byte (canonical JSON comparison). */
async function assertLedger(store: SqliteAuthorityStore, ledger: readonly LedgerEntry[]): Promise<void> {
  const read: {cursor: string; operationId: string; bytes: string}[] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, 7);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page" || page.transactions.length === 0) break;
    for (const transaction of page.transactions) {
      read.push({cursor: transaction.cursor, operationId: transaction.operation_id,
        bytes: canonicalAuthorityBytes({projection: transaction.projection,
          events: transaction.events, receipts: transaction.receipts}).toString("utf8")});
    }
    after = page.transactions[page.transactions.length - 1]!.cursor;
  }
  assert.deepEqual(read.map(row => row.cursor), ledger.map((_entry, index) => String(index + 1)));
  assert.deepEqual(read.map(row => row.operationId), ledger.map(entry => entry.operationId));
  for (const [index, row] of read.entries()) {
    const entry = ledger[index]!;
    assert.equal(row.bytes, canonicalAuthorityBytes({projection: entry.projection,
      events: entry.events, receipts: entry.receipts}).toString("utf8"), `ledger row ${index}`);
  }
}

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

/** A request/response session with the instrumented child. Replies are
 * matched by seq over complete newline-delimited JSON lines; an unsolicited
 * protocol error fails the session instead of hanging the test. */
interface ChildSession {
  next(command: Omit<DiskFullCommand, "seq">): Promise<Record<string, unknown>>;
  finish(): Promise<{code: number | null; signal: NodeJS.Signals | null; stderr: string}>;
}

async function spawnInjectionChild(t: test.TestContext, directory: string): Promise<ChildSession> {
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(PROCESS_PATH), directory, GOAL_ID], {stdio: ["pipe", "pipe", "pipe"]});
  t.after(() => child.kill());
  let buffer = "", stderr = "";
  const replies = new Map<number, Record<string, unknown>>();
  const waiting: {seq: number; resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void}[] = [];
  child.stdout.on("data", data => {
    buffer += String(data);
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line !== "" && line !== "ready") {
        let parsed: Record<string, unknown>;
        try { parsed = JSON.parse(line) as Record<string, unknown>; }
        catch { assert.fail(`unparseable child line: ${line.slice(0, 200)}`); }
        if (parsed.kind === "protocol-error" || parsed.kind === "command-error") {
          const pending = waiting.shift();
          if (pending) pending.reject(new Error(JSON.stringify(parsed)));
          else replies.set(-1, parsed);
        } else {
          const waiter = waiting.find(entry => entry.seq === Number(parsed.seq));
          if (waiter) {waiting.splice(waiting.indexOf(waiter), 1); waiter.resolve(parsed);}
          else replies.set(Number(parsed.seq), parsed);
        }
      }
      newline = buffer.indexOf("\n");
    }
  });
  child.stderr.on("data", data => {stderr += String(data);});
  const closed = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve =>
    child.on("close", (code, signal) => resolve({code, signal})));
  let seq = 0;
  return {
    next: command => {
      const current = ++seq;
      const reply = replies.has(current)
        ? Promise.resolve(replies.get(current)!)
        : new Promise<Record<string, unknown>>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error(
            `child reply ${current} deadline exceeded; stderr: ${stderr}`)), CHILD_DEADLINE_MS);
          waiting.push({seq: current, resolve: value => {clearTimeout(timer); resolve(value);},
            reject: error => {clearTimeout(timer); reject(error);}});
        });
      child.stdin.write(`${JSON.stringify({...command, seq: current})}\n`);
      return reply;
    },
    finish: async () => {
      child.stdin.end();
      const done = await closed;
      return {...done, stderr};
    },
  };
}

test("SQLite injected page-count exhaustion fails closed mid-checkpoint and recovers once capacity returns",
  {timeout: 60000}, async t => {
    const directory = await mkdtemp(join(tmpdir(), "sqlite-disk-full-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const store = new SqliteAuthorityStore(directory, GOAL_ID);

    // Seed cursors 1..64 (the first checkpoint window: cursor 1 is itself
    // checkpoint-bearing), so the next commit publishes checkpoint 65 and its
    // transaction carries checkpoints + commits + head together.
    const ledger: LedgerEntry[] = [];
    let revision: string | null = null;
    for (let sequence = 1; sequence <= 64; sequence++) {
      const entry = seedEntry(sequence);
      const committed = await commitEntry(store, entry, revision);
      assert.equal(committed.status, "applied", JSON.stringify(committed.result));
      revision = committed.revision;
      ledger.push(entry);
    }
    const seededRevision = revision;
    const before = rawSnapshot(store.path);
    assert.equal(before.commits, 64);
    assert.equal(before.checkpoints, 1);
    assert.equal(before.headCursor, "64");
    assert.deepEqual(before.integrity, ["ok"]);

    // Armed session: the engine quota rejects every commit that must grow
    // the file, while the already-allocated pages keep serving reads.
    const armedSession = await spawnInjectionChild(t, directory);
    const armed = await armedSession.next({kind: "arm"});
    assert.equal(armed.kind, "armed");
    assert.equal(armed.page_count, before.pageCount);

    const exhaustA = injectionEntry("exhaust-checkpoint-65", "exhaust-checkpoint", 65, 1024 * 1024, "a");
    const exhaustB = injectionEntry("exhaust-second-65", "exhaust-second", 65, 1024 * 1024, "b");
    for (const [attempt, entry] of [exhaustA, exhaustA, exhaustB].entries()) {
      const reply = await armedSession.next({kind: "commit", operation_id: entry.operationId,
        expected_provider_revision: seededRevision,
        marker: attempt === 2 ? "exhaust-second" : "exhaust-checkpoint",
        sequence: 65, padding_bytes: 1024 * 1024, padding_char: attempt === 2 ? "b" : "a"});
      assert.equal(reply.kind, "commit-result");
      const result = reply.result as {status?: string; reason_code?: string; reason?: unknown};
      // Typed pre-COMMIT provider failure: rolled back, never applied, never
      // ambiguous, never a protocol violation and never the logical 16 MiB
      // request cap — the documented injected-limit SQLITE_FULL path.
      assert.equal(result.status, "failed", JSON.stringify(result));
      assert.equal(result.reason_code, "provider_transaction_failed", JSON.stringify(result));
      assert.ok(typeof result.reason === "string" && result.reason.length > 0);
      assert.ok(!("provider_revision" in result) && !("cursor" in result));
    }

    // Reads inside the constrained process keep proving the intact state.
    const loaded = await armedSession.next({kind: "load"});
    assert.equal((loaded.result as {status: string}).status, "loaded");
    assert.equal((loaded.result as {cursor: string}).cursor, "64");
    assert.equal((loaded.result as {provider_revision: string}).provider_revision, seededRevision);
    for (const operationId of [exhaustA.operationId, exhaustB.operationId]) {
      const receipt = await armedSession.next({kind: "receipt", operation_id: operationId});
      assert.deepEqual((receipt.result as {status: string}).status, "missing");
    }
    const audit = await armedSession.next({kind: "audit"});
    assert.deepEqual(audit.result, AUDIT_VERIFIED_64);
    const scan = await armedSession.next({kind: "scan", after: null, limit: 100});
    assert.equal((scan.result as {status: string}).status, "page");
    assert.equal((scan.result as {has_more: boolean}).has_more, false);
    assert.deepEqual((scan.result as {rows: {cursor: string; operation_id: string}[]}).rows
      .map(row => row.operation_id), ledger.map(entry => entry.operationId));
    const pages = await armedSession.next({kind: "page-count"});
    // The failed transactions allocated nothing: the database file did not
    // grow by a single page while the quota was armed.
    assert.equal(pages.page_count, before.pageCount);
    const exited = await armedSession.next({kind: "exit"});
    assert.equal(exited.kind, "exited");
    const armedClosed = await armedSession.finish();
    assert.equal(armedClosed.code, 0, armedClosed.stderr);
    assert.equal(armedClosed.stderr, "");

    // An unconstrained new process (fresh store, raw engine reads) sees the
    // exact pre-exhaustion state without any repair step.
    const verifier = new SqliteAuthorityStore(directory, GOAL_ID);
    const head = await verifier.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status === "loaded") {
      assert.equal(head.cursor, "64");
      assert.equal(head.provider_revision, seededRevision);
      assert.equal(canonicalAuthorityBytes(head.head).toString("utf8"),
        canonicalAuthorityBytes(ledger[63]!.projection).toString("utf8"));
    }
    for (const operationId of [exhaustA.operationId, exhaustB.operationId]) {
      assert.deepEqual((await verifier.readReceipt(operationId)).status, "missing");
    }
    assert.deepEqual(await verifier.verifyAuthorityHistory(), AUDIT_VERIFIED_64);
    await assertLedger(verifier, ledger);
    assert.deepEqual(rawSnapshot(store.path), before);

    // Capacity restored (a never-armed session is the raised quota: the
    // store opens a new connection per commit): service resumes with no
    // manual repair, and the operation id whose commit failed is applyable
    // fresh because nothing about it was ever retained.
    const restoredSession = await spawnInjectionChild(t, directory);
    const recoverReply = await restoredSession.next({kind: "commit", operation_id: "recover-checkpoint-65",
      expected_provider_revision: seededRevision, marker: "recover-checkpoint",
      sequence: 65, padding_bytes: 0, padding_char: ""});
    const recoverResult = recoverReply.result as {status: string; provider_revision: string; cursor: string};
    assert.equal(recoverResult.status, "applied", JSON.stringify(recoverReply));
    assert.equal(recoverResult.cursor, "65");
    const reuseReply = await restoredSession.next({kind: "commit", operation_id: "exhaust-checkpoint-65",
      expected_provider_revision: recoverResult.provider_revision, marker: "exhaust-id-reused",
      sequence: 66, padding_bytes: 0, padding_char: ""});
    const reuseResult = reuseReply.result as {status: string; provider_revision: string; cursor: string};
    assert.equal(reuseResult.status, "applied", JSON.stringify(reuseReply));
    assert.equal(reuseResult.cursor, "66");
    await restoredSession.next({kind: "exit"});
    const restoredClosed = await restoredSession.finish();
    assert.equal(restoredClosed.code, 0, restoredClosed.stderr);

    // Final state: replay/verify of the whole ledger still passes and the
    // reused operation id owns exactly its recovery receipts.
    const finalLedger = [...ledger,
      injectionEntry("recover-checkpoint-65", "recover-checkpoint", 65, 0, ""),
      injectionEntry("exhaust-checkpoint-65", "exhaust-id-reused", 66, 0, "")];
    const finalHead = await verifier.loadAuthority();
    assert.equal(finalHead.status, "loaded");
    if (finalHead.status === "loaded") {
      assert.equal(finalHead.cursor, "66");
      assert.equal(canonicalAuthorityBytes(finalHead.head).toString("utf8"),
        canonicalAuthorityBytes(finalLedger[65]!.projection).toString("utf8"));
    }
    const reusedReceipt = await verifier.readReceipt("exhaust-checkpoint-65");
    assert.equal(reusedReceipt.status, "found");
    if (reusedReceipt.status === "found") {
      assert.equal(canonicalAuthorityBytes(reusedReceipt.receipts).toString("utf8"),
        canonicalAuthorityBytes(finalLedger[65]!.receipts).toString("utf8"));
    }
    assert.deepEqual(await verifier.verifyAuthorityHistory(),
      {schema_version: "loopx_sqlite_authority_history_audit_v0", status: "verified",
        commits: 66, checkpoints: 2});
    await assertLedger(verifier, finalLedger);
    const after = rawSnapshot(store.path);
    assert.equal(after.commits, 66);
    assert.equal(after.checkpoints, 2);
    assert.equal(after.headCursor, "66");
    assert.deepEqual(after.integrity, ["ok"]);
  });

test("SQLite padded checkpoint workload completes without the quota and the logical 16 MiB cap stays separately typed",
  {timeout: 30000}, async t => {
    const directory = await mkdtemp(join(tmpdir(), "sqlite-disk-full-control-"));
    t.after(() => rm(directory, {recursive: true, force: true}));
    const store = new SqliteAuthorityStore(directory, GOAL_ID);

    let revision: string | null = null;
    for (let sequence = 1; sequence <= 64; sequence++) {
      const committed = await commitEntry(store, seedEntry(sequence), revision);
      assert.equal(committed.status, "applied");
      revision = committed.revision;
    }

    // The identical padded checkpoint-bearing commit that exhausted the
    // engine quota in the injection test applies cleanly with no quota, and
    // its byte-identical retry replays `applied` at the original cursor.
    const exhaustA = injectionEntry("exhaust-checkpoint-65", "exhaust-checkpoint", 65, 1024 * 1024, "a");
    const first = await commitEntry(store, exhaustA, revision);
    assert.equal(first.status, "applied");
    assert.equal(first.cursor, "65");
    const retry = await commitEntry(store, exhaustA, revision);
    assert.equal(retry.status, "applied", JSON.stringify(retry.result));
    assert.equal(retry.revision, first.revision);
    assert.equal(retry.cursor, "65");
    const exhaustB = injectionEntry("exhaust-second-65", "exhaust-second", 65, 1024 * 1024, "b");
    const second = await commitEntry(store, exhaustB, first.revision);
    assert.equal(second.status, "applied");
    assert.equal(second.cursor, "66");

    // The provider's own logical request cap is a different typed failure
    // that never reaches SQLite, and it leaves no trace of itself behind.
    const oversized = injectionEntry("oversized-67", "oversized", 67, 17 * 1024 * 1024, "o");
    const rejected = await commitEntry(store, oversized, second.revision);
    assert.equal(rejected.status, "failed", JSON.stringify(rejected.result));
    assert.equal((rejected.result as {reason_code: string}).reason_code, "store_capacity_exhausted");
    const afterRejected = await store.loadAuthority();
    assert.equal(afterRejected.status, "loaded");
    if (afterRejected.status === "loaded") assert.equal(afterRejected.cursor, "66");
    assert.deepEqual((await store.readReceipt("oversized-67")).status, "missing");
    assert.deepEqual(await store.verifyAuthorityHistory(),
      {schema_version: "loopx_sqlite_authority_history_audit_v0", status: "verified",
        commits: 66, checkpoints: 2});
  });

test("SQLite real ENOSPC on a bounded macOS volume fails closed and resumes after the volume grows",
  {timeout: 120000}, async t => {
    const availability = spawnSync("hdiutil", ["-version"], {encoding: "utf8"});
    if (process.platform !== "darwin" || availability.error) {
      // The real-OS side is a macOS-only small-volume probe; elsewhere the
      // injected-limit case above carries the exhaustion contract. Skipping
      // is disclosed here rather than silently narrowing the suite.
      t.skip("real-OS ENOSPC case requires macOS hdiutil sparse volumes");
      return;
    }
    const workspace = await mkdtemp(join(tmpdir(), "sqlite-disk-full-volume-"));
    const volumeName = `loopxdf${process.pid}`;
    const image = join(workspace, `${volumeName}.sparseimage`);
    let mount: string | null = null;
    const run = (args: string[]): string => execFileSync("hdiutil", args,
      {encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 30000}) as string;
    const attach = (): string => {
      const output = run(["attach", "-nobrowse", image]);
      const line = output.trim().split("\n").at(-1)!.trim();
      const parsed = line.split(/\s+/).slice(2).join(" ");
      assert.ok(parsed.startsWith("/Volumes/"), `unexpected attach output: ${output.trim()}`);
      return parsed;
    };
    const detach = (): void => {
      if (mount === null) return;
      try { run(["detach", mount, "-force"]); } catch { /* best effort; rm still removes the image */ }
      mount = null;
    };
    t.after(async () => {
      detach();
      await rm(workspace, {recursive: true, force: true});
    });
    run(["create", "-size", "24m", "-type", "SPARSE", "-fs", "APFS", "-volname", volumeName, image]);
    mount = attach();

    // Fill the volume with unique ~1 MiB projections until the kernel stops
    // accepting writes. Every acknowledged commit is recorded with its exact
    // proof so the post-restore state can be compared independently.
    const store = new SqliteAuthorityStore(mount, GOAL_ID);
    const ledger: LedgerEntry[] = [];
    let revision: string | null = null;
    let failure: {status: string; reason_code?: string} | null = null;
    for (let sequence = 1; sequence <= 40; sequence++) {
      const entry = volumeEntry(sequence);
      const result = await store.commitAuthority({expected_provider_revision: revision,
        operation_id: entry.operationId, next_projection: entry.projection,
        events: [...entry.events], receipts: [...entry.receipts]}) as
        {status: string; reason_code?: string; provider_revision?: string};
      if (result.status === "applied") {
        ledger.push(entry);
        revision = result.provider_revision ?? null;
        continue;
      }
      failure = result;
      break;
    }
    assert.ok(failure !== null, "the bounded volume never exhausted");
    assert.notEqual(failure!.status, "applied");
    // A genuinely full disk surfaces as the typed provider failure (or, if
    // the error lands on COMMIT itself, the documented ambiguous outcome for
    // receipt reconciliation) — never as success.
    assert.ok(failure!.status === "failed" || failure!.status === "ambiguous", JSON.stringify(failure));
    if (failure!.status === "failed") {
      assert.equal(failure!.reason_code, "provider_transaction_failed", JSON.stringify(failure));
    } else {
      assert.equal(failure!.reason_code, "commit_outcome_unknown", JSON.stringify(failure));
    }
    const appliedCount = ledger.length;
    assert.ok(appliedCount >= 1 && appliedCount < 40);

    // Reads under real ENOSPC fail closed: either the typed unavailable
    // shape, or — if the volume still serves the read — exactly the applied
    // prefix. No fabricated state either way.
    const loaded = await store.loadAuthority();
    if (loaded.status === "unavailable") {
      assert.equal(loaded.reason_code, "provider_read_unavailable");
    } else if (loaded.status === "loaded") {
      assert.equal(loaded.cursor, String(appliedCount));
      assert.equal(canonicalAuthorityBytes(loaded.head).toString("utf8"),
        canonicalAuthorityBytes(ledger[appliedCount - 1]!.projection).toString("utf8"));
    } else { assert.fail(`unexpected load shape under ENOSPC: ${JSON.stringify(loaded)}`); }
    const audited = await store.verifyAuthorityHistory();
    if (audited.status === "verified") {
      assert.equal(audited.commits, appliedCount);
    } else { assert.equal(audited.status, "unavailable"); }

    // Capacity restored the way an operator would: grow the volume, then
    // reopen. The store must resume on exactly the applied prefix.
    detach();
    run(["resize", "-size", "64m", image]);
    mount = attach();
    const resumed = new SqliteAuthorityStore(mount, GOAL_ID);
    const head = await resumed.loadAuthority();
    assert.equal(head.status, "loaded");
    // In the ambiguous shape the failed commit may have landed durably, in
    // which case reconciliation owns the answer: the head is one further and
    // the failed id reads back with its exact receipt.
    const ambiguousApplied = head.status === "loaded" && head.cursor === String(appliedCount + 1);
    if (head.status === "loaded" && !ambiguousApplied) {
      assert.equal(head.cursor, String(appliedCount));
      assert.equal(canonicalAuthorityBytes(head.head).toString("utf8"),
        canonicalAuthorityBytes(ledger[appliedCount - 1]!.projection).toString("utf8"));
    }
    const failedOperationId = `volume-fill-${String(appliedCount + 1).padStart(2, "0")}`;
    const reconciled = await resumed.readReceipt(failedOperationId);
    if (ambiguousApplied) {
      assert.equal(reconciled.status, "found");
    } else {
      assert.equal(reconciled.status, "missing");
    }
    const finalPrefix = ambiguousApplied ? [...ledger, volumeEntry(appliedCount + 1)] : [...ledger];
    assert.deepEqual(await resumed.verifyAuthorityHistory(),
      {schema_version: "loopx_sqlite_authority_history_audit_v0", status: "verified",
        commits: finalPrefix.length, checkpoints: Math.floor((finalPrefix.length - 1) / 64) + 1});
    const engine = new DatabaseSync(sqliteAuthorityPath(mount, GOAL_ID), {readOnly: true});
    try {
      assert.deepEqual((engine.prepare("PRAGMA integrity_check").all() as {integrity_check: string}[])
        .map(row => row.integrity_check), ["ok"]);
    }
    finally { engine.close(); }

    // A fresh commit appends right after the surviving prefix: service is
    // restored with no manual repair step.
    const recovery: LedgerEntry = {
      operationId: "volume-recovered",
      projection: {schema_version: VOLUME_SCHEMA, marker: "recovered", sequence: finalPrefix.length + 1},
      events: [{kind: "disk-full-volume", sequence: finalPrefix.length + 1, recovered: true}],
      receipts: [{operation_id: "volume-recovered", sequence: finalPrefix.length + 1}],
    };
    const headRevision = head.status === "loaded" ? head.provider_revision : revision;
    const recoveredCommit = await resumed.commitAuthority({expected_provider_revision: headRevision,
      operation_id: recovery.operationId, next_projection: recovery.projection,
      events: [...recovery.events], receipts: [...recovery.receipts]}) as
      {status: string; cursor?: string};
    assert.equal(recoveredCommit.status, "applied", JSON.stringify(recoveredCommit));
    assert.equal(recoveredCommit.cursor, String(finalPrefix.length + 1));
    await assertLedger(resumed, [...finalPrefix, recovery]);
  });
