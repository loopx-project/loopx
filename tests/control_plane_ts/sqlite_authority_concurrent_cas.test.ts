import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {mkdir, mkdtemp, rm, writeFile} from "node:fs/promises";
import {spawn} from "node:child_process";
import test from "node:test";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {setTimeout as sleep} from "node:timers/promises";

import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {AuthorityStoreCommitResult, AuthorityStoreCommittedTransaction} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

/**
 * Concurrent CAS and lost-response recovery matrix for the SQLite authority
 * store, demanded by RFC 7.2 ("concurrent CAS", "lost-response") and the
 * 2026-11-10 slice order on issue #4224. The earlier
 * `SQLite real processes serialize CAS and preserve a lost-response receipt`
 * case proves one committed writer and one lost response; this file widens it
 * to a matrix of real writer processes: (a) several writers released by one
 * file barrier race a single `expected_provider_revision` for several rounds,
 * (b) a writer whose acknowledged response was lost retries the same
 * operation_id — byte-identically and rebuilt from an advanced head — while
 * another writer advances concurrently, (c) one authoritative view then
 * checks the complete ledger and `verifyAuthorityHistory`, and (d) a real
 * held write lock either delays a contending commit inside `busy_timeout` or
 * fails it cleanly with no dirty state.
 *
 * Every writer is a real subprocess (`sqlite_authority_concurrent_writer.ts`)
 * with its own connection, so write-lock serialization, busy handling and the
 * operation-id fence are exercised by the real provider, not by in-process
 * stubs. Barriers are control files owned by this test; the stores live in
 * disposable directories removed on test teardown.
 */
const GOAL_ID = "goal-concurrent-cas";
const BUSY_GOAL_ID = "goal-busy-cas";
const PLAN_SCHEMA = "loopx_sqlite_concurrent_cas_writer_plan_v0";
const WRITER_PATH = new URL("./sqlite_authority_concurrent_writer.ts", import.meta.url);
const CONTROL_DEADLINE_MS = 30000;

/** One planned commit and the proof this test expects to read back for it. */
interface LedgerEntry {
  operationId: string;
  projection: JsonObject;
  events: readonly JsonObject[];
  receipts: readonly JsonObject[];
}

// The plan protocol below mirrors `sqlite_authority_concurrent_writer.ts`.
// Like the consumer-lag test's cursor schema, the wire contract is duplicated
// on both sides by design: the helper is spawned, never imported.
type MatrixRevision = string | null | "$head";
interface MatrixCommitRequest {
  expected_provider_revision: MatrixRevision;
  operation_id: string;
  events: JsonObject[];
  next_projection: JsonObject;
  receipts: JsonObject[];
  base_projection?: "$observed-head";
}
type MatrixPlanStep =
  | {kind: "wait-barrier"; path: string}
  | {kind: "load-head"}
  | {kind: "commit"; request: MatrixCommitRequest; lost_response?: boolean}
  | {kind: "hold-lock"; ready_marker: string; release_marker: string};

interface WriterLogLine {
  done?: boolean;
  step?: number;
  kind?: string;
  phase?: string;
  operation_id?: string;
  elapsed_ms?: number;
  held_ms?: number;
  error?: string;
  result?: AuthorityStoreCommitResult;
}

interface WriterChild {
  ready: Promise<void>;
  closed: Promise<{code: number | null; signal: NodeJS.Signals | null}>;
  logs(): {output: string; stderr: string};
}

function canonicalText(value: unknown): string {
  return canonicalAuthorityBytes(value).toString("utf8");
}

function matrixEntry(kind: string, round: number | null, writer: number | null): LedgerEntry {
  const operationId = round === null ? `matrix-${kind}` : `matrix-${kind}-r${round}-w${writer}`;
  const tag = round === null ? {kind} : {kind, round, writer};
  return {
    operationId,
    projection: {schema_version: "loopx_concurrent_cas_matrix_v0", ...tag},
    events: [{schema_version: "loopx_authority_event_v0", type: "concurrent_cas_matrix_probe", ...tag}],
    receipts: [{schema_version: "loopx_authority_receipt_v0", operation_id: operationId, ...tag}],
  };
}

function commitRequestFor(entry: LedgerEntry, expected: MatrixRevision): MatrixCommitRequest {
  return {expected_provider_revision: expected, operation_id: entry.operationId,
    events: [...entry.events], next_projection: entry.projection, receipts: [...entry.receipts]};
}

async function spawnWriter(
  t: test.TestContext, plansDirectory: string, storeDirectory: string, goalId: string,
  name: string, steps: readonly MatrixPlanStep[],
): Promise<WriterChild> {
  const planPath = join(plansDirectory, `${name}.plan.json`);
  await writeFile(planPath, JSON.stringify({schema_version: PLAN_SCHEMA, steps: [...steps]}), {mode: 0o600});
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(WRITER_PATH), storeDirectory, goalId, planPath], {stdio: ["pipe", "pipe", "pipe"]});
  t.after(() => child.kill());
  let output = "", stderr = "";
  child.stdout.on("data", data => {output += String(data);});
  child.stderr.on("data", data => {stderr += String(data);});
  const logs = (): {output: string; stderr: string} => ({output, stderr});
  const closed = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve =>
    child.on("close", (code, signal) => resolve({code, signal})));
  // An exit before the ready line is the outcome, not a missed deadline; the
  // ready wait reports the drained pipes instead of spinning to the timeout.
  const ready = (async () => {
    const deadline = Date.now() + CONTROL_DEADLINE_MS;
    for (;;) {
      if (output.includes("ready\n")) return;
      const exited = await Promise.race([closed, sleep(5).then(() => null)]);
      if (exited !== null) {
        if (output.includes("ready\n")) return;
        assert.fail(`writer ${name} exited before ready (code=${String(exited.code)}, ` +
          `signal=${String(exited.signal)}, stderr=${JSON.stringify(stderr)})`);
      }
      assert.ok(Date.now() < deadline, `writer ${name} never signaled ready: stderr=${JSON.stringify(stderr)}`);
      await sleep(5);
    }
  })();
  return {ready, closed, logs};
}

/** Tolerant parse while a writer is still running: a trailing line may be
 * torn mid-write, so unparsable tails are dropped instead of failing. */
function runningWriterLines(output: string): WriterLogLine[] {
  const marker = output.indexOf("ready\n");
  if (marker === -1) return [];
  const lines = output.slice(marker + "ready\n".length).split("\n").filter(line => line.length > 0);
  const parsed: WriterLogLine[] = [];
  for (const [index, line] of lines.entries()) {
    try {
      parsed.push(JSON.parse(line) as WriterLogLine);
    } catch (error) {
      if (index === lines.length - 1) break;
      throw error;
    }
  }
  return parsed;
}

/** Strict parse after a clean exit: every stdout line must be complete JSON.
 * A lost-response run ends deliberately without its done line (exit 23). */
function parseWriterLog(name: string, child: WriterChild, requireDone = true): WriterLogLine[] {
  const {output, stderr} = child.logs();
  const marker = output.indexOf("ready\n");
  assert.notEqual(marker, -1, `writer ${name} never signaled ready; stderr=${JSON.stringify(stderr)}`);
  const lines = output.slice(marker + "ready\n".length).split("\n").filter(line => line.length > 0)
    .map(line => JSON.parse(line) as WriterLogLine);
  if (requireDone) {
    assert.notEqual(lines.find(line => line.done === true), undefined,
      `writer ${name} finished without its done line`);
  }
  return lines.filter(line => line.done !== true);
}

/** All `commit ... done` outcomes of a finished writer, with their operation ids. */
function commitOutcomes(lines: readonly WriterLogLine[]): {operationId: string; result: AuthorityStoreCommitResult; elapsedMs: number}[] {
  return lines.filter(line => line.kind === "commit" && line.phase === "done" && line.result !== undefined)
    .map(line => ({operationId: line.operation_id!, result: line.result!,
      elapsedMs: typeof line.elapsed_ms === "number" ? line.elapsed_ms : Number.NaN}));
}

async function waitForControlFile(path: string, what: string): Promise<void> {
  const deadline = Date.now() + CONTROL_DEADLINE_MS;
  while (!existsSync(path)) {
    assert.ok(Date.now() < deadline, `${what} control file never appeared: ${path}`);
    await sleep(5);
  }
}

async function waitForCommitEnter(name: string, child: WriterChild, operationId: string): Promise<void> {
  const deadline = Date.now() + CONTROL_DEADLINE_MS;
  for (;;) {
    if (runningWriterLines(child.logs().output).some(line =>
      line.kind === "commit" && line.phase === "enter" && line.operation_id === operationId)) return;
    const exited = await Promise.race([child.closed, sleep(5).then(() => null)]);
    if (exited !== null) {
      assert.fail(`writer ${name} exited before entering commit ${operationId} ` +
        `(code=${String(exited.code)}, stderr=${JSON.stringify(child.logs().stderr)})`);
    }
    assert.ok(Date.now() < deadline, `writer ${name} never entered commit ${operationId}`);
    await sleep(5);
  }
}

/** The single authoritative view of the whole ledger: head projection, a
 * paged `scanCommitted` replay (zero loss, zero duplication, strictly
 * contiguous cursors, original events/receipts/projections), and per-entry
 * receipts. `missingOperations` must have no receipt anywhere. */
async function assertLedgerView(
  store: SqliteAuthorityStore, ledger: readonly LedgerEntry[], identity: string,
  missingOperations: readonly string[] = [],
): Promise<void> {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  assert.equal(head.cursor, String(ledger.length), "head cursor equals the ledger length");
  assert.equal(head.provider_revision, `${identity}:${ledger.length}`);
  assert.equal(canonicalText(head.head), canonicalText(ledger[ledger.length - 1]!.projection),
    "head projection equals the newest committed projection");
  const seen: AuthorityStoreCommittedTransaction[] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, 3);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") break;
    if (page.transactions.length === 0) {
      assert.equal(page.has_more, false, "an empty terminal page must not claim more data");
      break;
    }
    seen.push(...page.transactions);
    after = page.transactions[page.transactions.length - 1]!.cursor;
    if (!page.has_more) break;
  }
  assert.equal(seen.length, ledger.length, "committed scan returns exactly the ledger");
  let previousCursor = 0n;
  const operationIds = new Set<string>();
  for (const [index, transaction] of seen.entries()) {
    const entry = ledger[index]!;
    const cursor = BigInt(transaction.cursor);
    assert.equal(cursor, previousCursor + 1n, `cursor must advance by exactly one at index ${index}`);
    previousCursor = cursor;
    assert.equal(transaction.operation_id, entry.operationId, `operation id at cursor ${transaction.cursor}`);
    assert.equal(operationIds.has(transaction.operation_id), false, "no duplicate delivery");
    operationIds.add(transaction.operation_id);
    assert.equal(transaction.provider_revision, `${identity}:${transaction.cursor}`);
    assert.deepEqual(transaction.events, entry.events, `events at cursor ${transaction.cursor}`);
    assert.deepEqual(transaction.receipts, entry.receipts, `receipts at cursor ${transaction.cursor}`);
    assert.equal(canonicalText(transaction.projection), canonicalText(entry.projection),
      `projection at cursor ${transaction.cursor}`);
  }
  for (const entry of ledger) {
    const receipt = await store.readReceipt(entry.operationId);
    assert.equal(receipt.status, "found", `receipt for ${entry.operationId}`);
    if (receipt.status === "found") {
      assert.deepEqual(receipt.receipts, entry.receipts, `stored receipts for ${entry.operationId}`);
    }
  }
  for (const operationId of missingOperations) {
    assert.deepEqual(await store.readReceipt(operationId), {status: "missing"},
      `no receipt may exist for losing operation ${operationId}`);
  }
}

/** One race round: `writers` real processes released by one file barrier,
 * every one committing against the same `expectedRevision`. Returns the
 * winner's entry and the losers' operation ids. */
async function runRaceRound(
  t: test.TestContext, plansDirectory: string, storeDirectory: string, control: (name: string) => string,
  round: number, writerCount: number, expectedRevision: string, expectedWinnerCursor: string, identity: string,
): Promise<{winner: LedgerEntry; losers: string[]}> {
  const barrierPath = control(`race-${round}-start`);
  const entries = Array.from({length: writerCount}, (_, writer) => matrixEntry("race", round, writer));
  const children: WriterChild[] = [];
  for (const [writer, entry] of entries.entries()) {
    children.push(await spawnWriter(t, plansDirectory, storeDirectory, GOAL_ID, `race-${round}-w${writer}`, [
      {kind: "wait-barrier", path: barrierPath},
      {kind: "commit", request: commitRequestFor(entry, expectedRevision)},
    ]));
  }
  await Promise.all(children.map(child => child.ready));
  // The barrier is one file creation observed by every waiting writer, so the
  // commits race for real inside BEGIN IMMEDIATE rather than sequentially.
  await writeFile(barrierPath, "go\n", {mode: 0o600});
  const outcomes: {operationId: string; result: AuthorityStoreCommitResult}[] = [];
  for (const [index, child] of children.entries()) {
    const closed = await child.closed;
    assert.equal(child.logs().stderr, "", `race-${round}-w${index} stderr`);
    assert.deepEqual(closed, {code: 0, signal: null}, `race-${round}-w${index} exit`);
    const commits = commitOutcomes(parseWriterLog(`race-${round}-w${index}`, child));
    assert.equal(commits.length, 1, `race-${round}-w${index} performed exactly one commit`);
    outcomes.push({operationId: commits[0]!.operationId, result: commits[0]!.result});
  }
  const applied = outcomes.filter(outcome => outcome.result.status === "applied");
  const conflicted = outcomes.filter(outcome => outcome.result.status === "conflict");
  assert.equal(applied.length, 1, `round ${round} admits exactly one writer: ${JSON.stringify(outcomes)}`);
  assert.equal(conflicted.length, writerCount - 1, `round ${round} conflicts every other writer`);
  const winner = applied[0]!;
  if (winner.result.status === "applied") {
    assert.equal(winner.result.cursor, expectedWinnerCursor, "winner takes the next cursor exactly");
    assert.equal(winner.result.provider_revision, `${identity}:${expectedWinnerCursor}`);
  }
  for (const outcome of conflicted) {
    if (outcome.result.status !== "conflict") continue;
    assert.equal(outcome.result.conflict_kind, "provider_revision_mismatch",
      `round ${round} loser ${outcome.operationId}`);
    assert.equal(outcome.result.current_cursor, expectedWinnerCursor,
      `round ${round} losers see the winner's cursor`);
    assert.equal(outcome.result.current_provider_revision, `${identity}:${expectedWinnerCursor}`);
  }
  const winnerEntry = entries.find(entry => entry.operationId === winner.operationId)!;
  return {winner: winnerEntry,
    losers: outcomes.filter(outcome => outcome.result.status !== "applied").map(outcome => outcome.operationId)};
}

test("SQLite concurrent CAS matrix: racing writers, lost-response retry and one exact final ledger", {timeout: 60000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-concurrent-cas-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const plansDirectory = join(root, "writer-plans");
  await mkdir(plansDirectory, {recursive: true});
  const control = (name: string): string => join(root, "writer-control", name);
  await mkdir(join(root, "writer-control"), {recursive: true});
  const store = new SqliteAuthorityStore(root, GOAL_ID);

  const identityResult = await store.storeIdentity();
  assert.equal(identityResult.status, "available");
  const identity = identityResult.status === "available" ? identityResult.store_identity : "";

  // Seed the lineage so the matrix races provider revisions, not schema
  // installation; every later revision is derived from this identity.
  const seed = matrixEntry("seed", null, null);
  const seeded = await store.commitAuthority(commitRequestFor(seed, null));
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  const ledger: LedgerEntry[] = [seed];
  let headRevision = seeded.status === "applied" ? seeded.provider_revision : null;
  assert.equal(headRevision, `${identity}:1`);

  // (a) Three rounds of four racing writers; each round's winner advances the
  // revision chain the next round races against.
  const missingOperations: string[] = [];
  const WRITERS = 4;
  for (const round of [1, 2, 3]) {
    const expectedWinnerCursor = String(ledger.length + 1);
    const {winner, losers} = await runRaceRound(t, plansDirectory, root, control, round, WRITERS,
      headRevision!, expectedWinnerCursor, identity);
    ledger.push(winner);
    missingOperations.push(...losers);
    headRevision = `${identity}:${expectedWinnerCursor}`;
    // After every race the store is exactly the ledger: no duplicate cursor,
    // no partial write, losers left no receipt.
    await assertLedgerView(store, ledger, identity, missingOperations);
  }

  // (b) Lost response. Writer A commits alone on the current head, applies,
  // and its response is never delivered (exit 23, no result line). The
  // recovery contract starts before any retry: the receipt is durable and
  // the head advanced exactly once.
  const lostEntry = matrixEntry("lost", null, null);
  const revisionBeforeLost = headRevision!;
  const lostBarrier = control("lost-start");
  const lostWriter = await spawnWriter(t, plansDirectory, root, GOAL_ID, "lost-writer", [
    {kind: "wait-barrier", path: lostBarrier},
    {kind: "commit", request: commitRequestFor(lostEntry, revisionBeforeLost), lost_response: true},
  ]);
  await lostWriter.ready;
  await writeFile(lostBarrier, "go\n", {mode: 0o600});
  const lostClosed = await lostWriter.closed;
  assert.equal(lostWriter.logs().stderr, "");
  assert.equal(lostClosed.code, 23, "the lost-response writer exits without its acknowledgement");
  const lostLines = parseWriterLog("lost-writer", lostWriter, false);
  const lostMarker = lostLines.find(line => line.kind === "commit" && line.phase === "lost-response");
  assert.notEqual(lostMarker, undefined, "the applied commit was never reported as a result");
  assert.equal(lostLines.some(line => line.kind === "commit" && line.phase === "done"), false,
    "no result line may exist for the lost response");
  const originalReceipt = await store.readReceipt(lostEntry.operationId);
  assert.equal(originalReceipt.status, "found", "the lost acknowledgement is recoverable from its receipt");
  if (originalReceipt.status === "found") {
    assert.equal(originalReceipt.cursor, String(ledger.length + 1));
    assert.equal(originalReceipt.provider_revision, `${identity}:${ledger.length + 1}`);
  }
  ledger.push(lostEntry);
  headRevision = `${identity}:${ledger.length}`;
  await assertLedgerView(store, ledger, identity);

  // Writer B advances the head while the response stays lost, so the retry
  // below runs against a revision chain other writers moved.
  const advanceEntry = matrixEntry("advance", null, null);
  const advanceBarrier = control("advance-start");
  const advanceWriter = await spawnWriter(t, plansDirectory, root, GOAL_ID, "advance-writer", [
    {kind: "wait-barrier", path: advanceBarrier},
    {kind: "load-head"},
    {kind: "commit", request: commitRequestFor(advanceEntry, "$head")},
  ]);
  await advanceWriter.ready;
  await writeFile(advanceBarrier, "go\n", {mode: 0o600});
  const advanceClosed = await advanceWriter.closed;
  assert.equal(advanceWriter.logs().stderr, "");
  assert.deepEqual(advanceClosed, {code: 0, signal: null});
  const advanceOutcome = commitOutcomes(parseWriterLog("advance-writer", advanceWriter));
  assert.equal(advanceOutcome.length, 1);
  assert.equal(advanceOutcome[0]!.result.status, "applied", JSON.stringify(advanceOutcome));
  if (advanceOutcome[0]!.result.status === "applied") {
    assert.equal(advanceOutcome[0]!.result.cursor, String(ledger.length + 1));
  }
  ledger.push(advanceEntry);
  await assertLedgerView(store, ledger, identity);

  // Writer C is a fresh incarnation holding the original request. Its
  // byte-identical retry must replay the original transaction (same cursor,
  // no new commit; the store proves replay from the retained row, so the
  // retried expected revision itself is irrelevant to that verdict). Its
  // rebuilt retry — same operation_id with the projection re-planned on top
  // of the head other writers advanced — must be fenced as
  // operation_id_exists, which is what stops a lost response from executing
  // a second, different operation under the same id. Writer D commits
  // against the same head at the same time, so C's retries interleave with
  // real concurrent progress.
  const interleaveEntry = matrixEntry("interleave", null, null);
  const retryBarrier = control("retry-start");
  // The rebuilt retry re-plans its projection on the observed head (the
  // merge keeps whatever the concurrent writers left there), so its content
  // is genuinely different from the retained transaction — the situation the
  // operation-id fence exists for.
  const rebuiltRequest: MatrixCommitRequest = {expected_provider_revision: "$head",
    operation_id: lostEntry.operationId, events: [...lostEntry.events],
    next_projection: {retry_marker: "lost-response-rebuild"}, receipts: [...lostEntry.receipts],
    base_projection: "$observed-head"};
  const retryWriter = await spawnWriter(t, plansDirectory, root, GOAL_ID, "retry-writer", [
    {kind: "wait-barrier", path: retryBarrier},
    {kind: "commit", request: commitRequestFor(lostEntry, revisionBeforeLost)},
    {kind: "load-head"},
    {kind: "commit", request: rebuiltRequest},
  ]);
  const interleaveWriter = await spawnWriter(t, plansDirectory, root, GOAL_ID, "interleave-writer", [
    {kind: "wait-barrier", path: retryBarrier},
    {kind: "load-head"},
    {kind: "commit", request: commitRequestFor(interleaveEntry, "$head")},
  ]);
  await Promise.all([retryWriter.ready, interleaveWriter.ready]);
  await writeFile(retryBarrier, "go\n", {mode: 0o600});
  const [retryClosed, interleaveClosed] = await Promise.all([retryWriter.closed, interleaveWriter.closed]);
  assert.equal(retryWriter.logs().stderr, "");
  assert.equal(interleaveWriter.logs().stderr, "");
  assert.deepEqual(retryClosed, {code: 0, signal: null});
  assert.deepEqual(interleaveClosed, {code: 0, signal: null});
  const retryOutcome = commitOutcomes(parseWriterLog("retry-writer", retryWriter));
  assert.equal(retryOutcome.length, 2);
  assert.deepEqual(retryOutcome.map(outcome => outcome.result.status), ["applied", "conflict"],
    JSON.stringify(retryOutcome));
  assert.equal(retryOutcome[0]!.operationId, lostEntry.operationId);
  if (retryOutcome[0]!.result.status === "applied") {
    assert.equal(retryOutcome[0]!.result.cursor, String(ledger.length - 1),
      "the identical retry replays the original transaction, never a new cursor");
    assert.equal(retryOutcome[0]!.result.provider_revision, `${identity}:${ledger.length - 1}`);
  }
  assert.equal(retryOutcome[1]!.operationId, lostEntry.operationId);
  if (retryOutcome[1]!.result.status === "conflict") {
    assert.equal(retryOutcome[1]!.result.conflict_kind, "operation_id_exists",
      "a rebuilt retry of an already-executed operation is fenced");
  }
  const interleaveOutcome = commitOutcomes(parseWriterLog("interleave-writer", interleaveWriter));
  assert.equal(interleaveOutcome.length, 1);
  assert.equal(interleaveOutcome[0]!.result.status, "applied", JSON.stringify(interleaveOutcome));
  if (interleaveOutcome[0]!.result.status === "applied") {
    assert.equal(interleaveOutcome[0]!.result.cursor, String(ledger.length + 1));
  }
  ledger.push(interleaveEntry);

  // The receipt of the lost response survived the concurrent retries byte
  // for byte, and no retry added a second transaction for its operation.
  assert.deepEqual(await store.readReceipt(lostEntry.operationId), originalReceipt,
    "the original receipt is unchanged after identical and rebuilt retries");
  await assertLedgerView(store, ledger, identity, missingOperations);

  // (c) Final consistency: one authoritative view over the complete ledger,
  // plus the full-history audit.
  const audit = await store.verifyAuthorityHistory();
  assert.equal(audit.status, "verified", JSON.stringify(audit));
  if (audit.status === "verified") assert.equal(audit.commits, ledger.length);
});

test("SQLite busy lock: a contending writer waits out the holder or fails clean at the busy timeout", {timeout: 60000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-busy-cas-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const plansDirectory = join(root, "writer-plans");
  await mkdir(plansDirectory, {recursive: true});
  const control = (name: string): string => join(root, "writer-control", name);
  await mkdir(join(root, "writer-control"), {recursive: true});
  const store = new SqliteAuthorityStore(root, BUSY_GOAL_ID);
  const identityResult = await store.storeIdentity();
  assert.equal(identityResult.status, "available");
  const identity = identityResult.status === "available" ? identityResult.store_identity : "";

  const seed = matrixEntry("seed", null, null);
  const seeded = await store.commitAuthority(commitRequestFor(seed, null));
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  const ledger: LedgerEntry[] = [seed];

  // Holder H takes the database write lock (BEGIN IMMEDIATE on a real
  // connection) and publishes its marker; writer W's commit then provably
  // starts while the lock is held. The parent releases H after a controlled
  // window, well inside the store's 5 s busy_timeout, so W must wait for the
  // lock and then apply exactly one transaction.
  const waitEntry = matrixEntry("busy-wait", null, null);
  const holdStart1 = control("hold-1-start"), holdReady1 = control("hold-1-ready"), holdRelease1 = control("hold-1-release");
  const gate1 = control("busy-1-gate");
  const holder1 = await spawnWriter(t, plansDirectory, root, BUSY_GOAL_ID, "busy-holder-1", [
    {kind: "wait-barrier", path: holdStart1},
    {kind: "hold-lock", ready_marker: holdReady1, release_marker: holdRelease1},
  ]);
  const waiter1 = await spawnWriter(t, plansDirectory, root, BUSY_GOAL_ID, "busy-waiter-1", [
    {kind: "wait-barrier", path: gate1},
    {kind: "commit", request: commitRequestFor(waitEntry, `${identity}:1`)},
  ]);
  await Promise.all([holder1.ready, waiter1.ready]);
  await writeFile(holdStart1, "go\n", {mode: 0o600});
  await waitForControlFile(holdReady1, "busy holder 1 ready");
  await writeFile(gate1, "go\n", {mode: 0o600});
  await waitForCommitEnter("busy-waiter-1", waiter1, waitEntry.operationId);
  await sleep(350);
  await writeFile(holdRelease1, "go\n", {mode: 0o600});
  const [holder1Closed, waiter1Closed] = await Promise.all([holder1.closed, waiter1.closed]);
  assert.equal(holder1.logs().stderr, "");
  assert.equal(waiter1.logs().stderr, "");
  assert.deepEqual(holder1Closed, {code: 0, signal: null});
  assert.deepEqual(waiter1Closed, {code: 0, signal: null});
  const held1 = parseWriterLog("busy-holder-1", holder1)
    .find(line => line.kind === "hold-lock" && line.phase === "done");
  assert.ok(held1?.held_ms !== undefined && held1.held_ms >= 250, "holder 1 held the lock through the contention window");
  const waited1 = commitOutcomes(parseWriterLog("busy-waiter-1", waiter1));
  assert.equal(waited1.length, 1);
  assert.equal(waited1[0]!.result.status, "applied", JSON.stringify(waited1));
  if (waited1[0]!.result.status === "applied") {
    assert.equal(waited1[0]!.result.cursor, "2", "the waited commit takes exactly the next cursor");
    assert.ok(waited1[0]!.elapsedMs >= 150, `the contending commit really waited: ${waited1[0]!.elapsedMs} ms`);
  }
  ledger.push(waitEntry);
  await assertLedgerView(store, ledger, identity);
  const audit1 = await store.verifyAuthorityHistory();
  assert.equal(audit1.status, "verified");
  if (audit1.status === "verified") assert.equal(audit1.commits, ledger.length);

  // Timeout side: this time the holder outlives the 5 s busy_timeout. The
  // contending commit must fail explicitly (a typed provider failure, not a
  // hang or a crash) and leave the store byte-identical: no partial write, no
  // cursor jump, no receipt for the refused operation.
  const timeoutEntry = matrixEntry("busy-timeout", null, null);
  const holdStart2 = control("hold-2-start"), holdReady2 = control("hold-2-ready"), holdRelease2 = control("hold-2-release");
  const gate2 = control("busy-2-gate");
  const holder2 = await spawnWriter(t, plansDirectory, root, BUSY_GOAL_ID, "busy-holder-2", [
    {kind: "wait-barrier", path: holdStart2},
    {kind: "hold-lock", ready_marker: holdReady2, release_marker: holdRelease2},
  ]);
  const waiter2 = await spawnWriter(t, plansDirectory, root, BUSY_GOAL_ID, "busy-waiter-2", [
    {kind: "wait-barrier", path: gate2},
    {kind: "commit", request: commitRequestFor(timeoutEntry, `${identity}:2`)},
  ]);
  await Promise.all([holder2.ready, waiter2.ready]);
  const beforeTimeout = await store.loadAuthority();
  assert.equal(beforeTimeout.status, "loaded");
  await writeFile(holdStart2, "go\n", {mode: 0o600});
  await waitForControlFile(holdReady2, "busy holder 2 ready");
  await writeFile(gate2, "go\n", {mode: 0o600});
  await waitForCommitEnter("busy-waiter-2", waiter2, timeoutEntry.operationId);
  const waiter2Closed = await waiter2.closed;
  assert.equal(waiter2.logs().stderr, "");
  assert.deepEqual(waiter2Closed, {code: 0, signal: null}, "a busy timeout is a typed failure, not a crash");
  const timedOut = commitOutcomes(parseWriterLog("busy-waiter-2", waiter2));
  assert.equal(timedOut.length, 1);
  assert.equal(timedOut[0]!.result.status, "failed", JSON.stringify(timedOut));
  if (timedOut[0]!.result.status === "failed") {
    assert.equal(timedOut[0]!.result.reason_code, "provider_transaction_failed");
  }
  assert.ok(timedOut[0]!.elapsedMs >= 4000, `the failure followed the full busy timeout: ${timedOut[0]!.elapsedMs} ms`);
  await writeFile(holdRelease2, "go\n", {mode: 0o600});
  const holder2Closed = await holder2.closed;
  assert.deepEqual(holder2Closed, {code: 0, signal: null});
  assert.deepEqual(await store.loadAuthority(), beforeTimeout, "the refused commit left the head untouched");
  assert.deepEqual(await store.readReceipt(timeoutEntry.operationId), {status: "missing"},
    "the refused operation left no receipt");
  await assertLedgerView(store, ledger, identity);
  const audit2 = await store.verifyAuthorityHistory();
  assert.equal(audit2.status, "verified");
  if (audit2.status === "verified") assert.equal(audit2.commits, ledger.length);
});
