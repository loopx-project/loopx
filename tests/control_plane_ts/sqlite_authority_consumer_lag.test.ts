import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {mkdir, mkdtemp, readFile, rename, rm, writeFile} from "node:fs/promises";
import {spawn} from "node:child_process";
import test from "node:test";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {AUTHORITY_STATE_CHECKPOINT_INTERVAL} from "../../loopx/control_plane/coordination/authority_state_log.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA} from "../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts";
import type {AuthorityStoreCommittedTransaction} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

/**
 * Post-promotion lagged projection consumer, durable-cursor evidence.
 *
 * Scope demanded by the `consumer_lag` capacity-ledger row ("24-hour logical
 * consumer backlog requires its own persisted-cursor test") and by the
 * 2026-10-03 owner diagnostic on issue #4224: the shadow-capture
 * `drain-cursor.json` is a *pre-promotion source-capture* cursor — it tracks
 * how far the capture pipeline has delivered shadow-source outbox entries
 * into the primary authority before promotion — so those tests cannot
 * certify a post-promotion lagged projection consumer. This file covers the
 * other path only: the promoted store's own durable projection outbox (the
 * `commits` table) read through `SqliteAuthorityStore.scanCommitted`, whose
 * contract says existing consumers resume by cursor. The consumer's cursor
 * lives in a file this test owns; every resume reconstructs the consumer
 * from that file, never from memory, and the 24-hour backlog is synthetic
 * (committed in batches with hour labels, with no real waiting).
 */
const GOAL_ID = "goal-lag";
const CONSUMER_CURSOR_SCHEMA = "loopx_sqlite_consumer_lag_cursor_test_v0";

const FIXED_TODOS: readonly JsonObject[] = [{
  schema_version: "todo_item_v0",
  todo_id: "todo-lag-a",
  role: "agent",
  status: "open",
  done: false,
  text: "Consume the promoted authority outbox across a long stop",
  archive_state: "active",
  source_section: "Agent Todo",
  required_write_scopes: ["loopx/control_plane/**"],
}, {
  schema_version: "todo_item_v0",
  todo_id: "todo-lag-b",
  role: "agent",
  status: "open",
  done: false,
  text: "Persist the consumption cursor before and after every page",
  archive_state: "active",
  source_section: "Agent Todo",
  required_write_scopes: ["loopx/control_plane/**"],
}, {
  schema_version: "todo_item_v0",
  todo_id: "todo-lag-c",
  role: "agent",
  status: "open",
  done: false,
  text: "Replay the original receipts after the backlog drains",
  archive_state: "active",
  source_section: "Agent Todo",
  required_write_scopes: ["loopx/control_plane/**"],
}];

interface PlannedCommit {
  operationId: string;
  projection: JsonObject;
  events: readonly JsonObject[];
  receipts: readonly JsonObject[];
  syntheticHoursAfterStop: number | null;
}

/** Fixed Todo bodies plus one moving scalar keep each delta tiny, so the
 * synthetic history measures lag and resume behavior, not payload growth. */
function plannedCommit(ordinal: number, syntheticHoursAfterStop: number | null): PlannedCommit {
  const operationId = syntheticHoursAfterStop === null
    ? `lag-steady-${String(ordinal).padStart(4, "0")}`
    : `lag-backlog-${String(syntheticHoursAfterStop).padStart(2, "0")}h-${String(ordinal).padStart(4, "0")}`;
  return {
    operationId,
    projection: authorityProjectionFixture(GOAL_ID, FIXED_TODOS, [], "native", {
      consumer_lag_ordinal: ordinal,
      ...(syntheticHoursAfterStop === null ? {} : {synthetic_hours_after_consumer_stop: syntheticHoursAfterStop}),
    }),
    events: [{
      schema_version: "loopx_authority_event_v0",
      type: "consumer_lag_probe",
      ordinal,
      ...(syntheticHoursAfterStop === null ? {} : {synthetic_hours_after_consumer_stop: syntheticHoursAfterStop}),
    }],
    receipts: [{
      schema_version: "loopx_authority_receipt_v0",
      operation_id: operationId,
      ordinal,
      ...(syntheticHoursAfterStop === null ? {} : {synthetic_hours_after_consumer_stop: syntheticHoursAfterStop}),
    }],
    syntheticHoursAfterStop,
  };
}

async function commitPlanned(store: SqliteAuthorityStore, planned: readonly PlannedCommit[],
  revision: string | null): Promise<string | null> {
  for (const commit of planned) {
    const result = await store.commitAuthority({expected_provider_revision: revision,
      operation_id: commit.operationId, next_projection: commit.projection,
      events: commit.events, receipts: commit.receipts});
    assert.equal(result.status, "applied", JSON.stringify(result));
    if (result.status !== "applied") return null;
    revision = result.provider_revision;
  }
  return revision;
}

interface PersistedCursor {
  schema_version: string;
  goal_id: string;
  last_consumed_cursor: string;
}

/** Staging file plus atomic rename: a power loss leaves the previous cursor
 * or the new one, never a torn cursor. The staging file is cleaned by the
 * next persist, and both live under the consumer's own directory. */
async function persistConsumerCursor(path: string, cursor: string): Promise<void> {
  const staging = `${path}.staging`;
  await writeFile(staging, JSON.stringify({
    schema_version: CONSUMER_CURSOR_SCHEMA, goal_id: GOAL_ID, last_consumed_cursor: cursor,
  }), {mode: 0o600});
  await rename(staging, path);
}

async function loadPersistedCursor(path: string): Promise<string | null> {
  if (!existsSync(path)) return null;
  const record = JSON.parse(await readFile(path, "utf8")) as PersistedCursor;
  assert.equal(record.schema_version, CONSUMER_CURSOR_SCHEMA);
  assert.equal(record.goal_id, GOAL_ID);
  assert.match(record.last_consumed_cursor, /^[1-9]\d*$/);
  return record.last_consumed_cursor;
}

/** Missing and raced-away files are "not yet". ENOENT between `existsSync` and
 * `readFile` is that race, not a failed read of a published marker. */
async function readTextIfPresent(path: string): Promise<string | null> {
  if (!existsSync(path)) return null;
  try {
    return await readFile(path, "utf8");
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") return null;
    throw error;
  }
}

/** A hold marker counts as published only when it contains a complete cursor.
 * An empty file is the create-before-write gap of a direct `writeFile` and
 * must not become cursor 0. The child renames a finished same-directory temp
 * file into place, so a stable read is the full cursor. */
async function readPublishedHoldCursor(path: string): Promise<string | null> {
  const body = await readTextIfPresent(path);
  if (body === null || body.length === 0) return null;
  assert.match(body, /^[1-9]\d*$/, "hold-ready must publish a complete cursor");
  return body;
}

/** `exit` can fire before the pipe delivers the last stdout. Await `close` so
 * the caller's buffers include that write, then let the caller decide whether
 * the marker it was waiting for actually arrived. */
async function exitedConsumer(
  child: {exitCode: number | null; signalCode: NodeJS.Signals | null},
  closed: Promise<{code: number | null; signal: NodeJS.Signals | null}>,
): Promise<{code: number | null; signal: NodeJS.Signals | null} | null> {
  if (child.exitCode === null && child.signalCode === null) return null;
  return closed;
}

/** An early exit is the outcome, not a missed deadline. Report the code,
 * signal, and the last line the child actually wrote. */
function failConsumerExited(
  exited: {code: number | null; signal: NodeJS.Signals | null},
  output: string,
  stderr: string,
  when: string,
): never {
  const tail = output.trim().split("\n").at(-1) ?? "";
  assert.fail(`consumer child exited ${when} (code=${String(exited.code)}, signal=${String(exited.signal)}, last_stdout=${JSON.stringify(tail)}, stderr=${JSON.stringify(stderr)})`);
}

interface RunningConsumer {
  exitCode: number | null;
  signalCode: NodeJS.Signals | null;
}

interface ConsumerLogs {
  output: string;
  stderr: string;
}

/** Ready is a stdout line. An exit before it is reported with the drained
 * pipes, not left to the test timeout. */
async function waitForConsumerReady(
  child: RunningConsumer,
  closed: Promise<{code: number | null; signal: NodeJS.Signals | null}>,
  readLogs: () => ConsumerLogs,
): Promise<void> {
  const deadline = Date.now() + 30000;
  for (;;) {
    if (readLogs().output.includes("ready\n")) return;
    const exited = await exitedConsumer(child, closed);
    if (exited !== null) {
      const logs = readLogs();
      if (logs.output.includes("ready\n")) return;
      failConsumerExited(exited, logs.output, logs.stderr, "before signaling ready");
    }
    assert.ok(Date.now() < deadline,
      `consumer child did not signal ready: stderr=${JSON.stringify(readLogs().stderr)}`);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

/** Wait until `hold-ready` is a complete cursor published by rename.
 *
 * The child writes the cursor to `hold-ready.staging` and renames it only
 * after `hold-staging-seen`. This parent reads that staging cursor while
 * `hold-ready` is still absent, then acknowledges. A missing or empty
 * `hold-ready` is not a cursor. A child that exits first fails here with its
 * code and signal instead of spinning until the deadline. */
async function waitForPublishedHold(
  holdDir: string,
  child: RunningConsumer,
  closed: Promise<{code: number | null; signal: NodeJS.Signals | null}>,
  readLogs: () => ConsumerLogs,
): Promise<string> {
  const holdReady = join(holdDir, "hold-ready");
  const holdStaging = `${holdReady}.staging`;
  const holdStagingSeen = join(holdDir, "hold-staging-seen");
  const deadline = Date.now() + 30000;
  let heldCursor: string | null = null;
  let stagedCursor: string | null = null;
  let sawEmptyMarker = false;
  while (heldCursor === null) {
    if (stagedCursor === null && !existsSync(holdReady)) {
      const staged = await readTextIfPresent(holdStaging);
      if (staged !== null && /^[1-9]\d*$/.test(staged) && !existsSync(holdReady)) {
        stagedCursor = staged;
        const ackStaging = `${holdStagingSeen}.staging`;
        await writeFile(ackStaging, "seen", {mode: 0o600});
        await rename(ackStaging, holdStagingSeen);
      }
    }
    const readyBody = await readTextIfPresent(holdReady);
    if (readyBody !== null && readyBody.length === 0) sawEmptyMarker = true;
    heldCursor = await readPublishedHoldCursor(holdReady);
    if (heldCursor !== null) break;
    const exited = await exitedConsumer(child, closed);
    if (exited !== null) {
      heldCursor = await readPublishedHoldCursor(holdReady);
      if (heldCursor !== null) break;
      const logs = readLogs();
      failConsumerExited(exited, logs.output, logs.stderr, "before publishing hold-ready");
    }
    assert.ok(Date.now() < deadline, existsSync(holdReady)
      ? "consumer child left an empty hold-ready marker; an incomplete marker is not a published cursor"
      : "consumer child did not reach its hold point");
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(sawEmptyMarker, false, "hold-ready must not be visible until its cursor bytes are complete");
  assert.equal(stagedCursor, heldCursor, "hold-ready must be the staged cursor, published by rename");
  return heldCursor;
}

/** Verify the full consumed stream against the write-time ledger: exact
 * operation set (zero loss, zero duplication), strictly contiguous cursors,
 * original events and receipts, the exact committed projection, and provider
 * revisions anchored to the store identity the consumer actually reads. */
function assertStreamMatchesLedger(
  consumed: readonly AuthorityStoreCommittedTransaction[],
  ledger: readonly PlannedCommit[],
  identity: string,
  firstCursor = 1n,
): void {
  assert.equal(consumed.length, ledger.length);
  assert.deepEqual([...new Set(consumed.map(row => row.operation_id))].sort(),
    ledger.map(commit => commit.operationId).sort(),
    "operation ids must be exactly the committed set, with no duplicate delivery");
  let previous = firstCursor - 1n;
  for (const [index, row] of consumed.entries()) {
    const cursor = BigInt(row.cursor);
    assert.equal(cursor, previous + 1n, `cursor must advance by exactly one at index ${index}`);
    previous = cursor;
    const planned = ledger[index]!;
    assert.equal(row.operation_id, planned.operationId);
    assert.deepEqual(row.events, planned.events, `original events at cursor ${row.cursor}`);
    assert.deepEqual(row.receipts, planned.receipts, `original receipts at cursor ${row.cursor}`);
    assert.equal(canonicalAuthorityBytes(row.projection).toString("utf8"),
      canonicalAuthorityBytes(planned.projection).toString("utf8"),
      `projection at cursor ${row.cursor} must equal the committed projection`);
    assert.equal(row.provider_revision, `${identity}:${row.cursor}`);
  }
}

/** Post-promotion boundary: the consumed stream is the promoted store's own
 * `scanCommitted` outbox, not the shadow capture pipeline. The pre-promotion
 * drain cursor lives under `authority-shadow/outbox/<goal>/<partition>/` with
 * the shadow schema; this consumer's cursor is a different schema in a
 * different location and both facts are asserted, not just stated. */
function assertPostPromotionBoundary(root: string, consumerDirectory: string): void {
  assert.equal(existsSync(join(root, "authority-shadow")), false,
    "no shadow capture pipeline exists for this store; the consumer reads the promoted authority itself");
  assert.equal(existsSync(join(root, "authority-shadow", "outbox")), false);
  // Anchor the contrast to the actual generated schema id, so a rename of the
  // pre-promotion cursor contract cannot silently conflate the two cursors.
  assert.equal(LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA, "loopx_local_authority_shadow_drain_cursor_v0");
  assert.notEqual(CONSUMER_CURSOR_SCHEMA, LOCAL_AUTHORITY_SHADOW_DRAIN_CURSOR_SCHEMA);
  assert.equal(existsSync(consumerDirectory), true, "consumer cursor directory is separate from the store");
}

test("SQLite lagged projection consumer resumes from a persisted cursor without loss, duplication or reordering", {timeout: 120000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-consumer-lag-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const consumerDirectory = join(root, "consumer-state");
  await mkdir(consumerDirectory, {recursive: true});
  const cursorPath = join(consumerDirectory, "consumer-cursor.json");
  const store = new SqliteAuthorityStore(root, GOAL_ID);

  // Page size deliberately shares no factor boundary with the 64-commit
  // checkpoint window, so page edges and window edges drift apart and every
  // resume crosses unaligned checkpoint boundaries.
  const PAGE = 7;
  const STEADY_COMMITS = 4 * AUTHORITY_STATE_CHECKPOINT_INTERVAL + 44; // 300
  const BACKLOG_BATCHES = 5;
  const BACKLOG_BATCH_COMMITS = 60; // 5 batches model a synthetic 24h stop
  const totalCommits = STEADY_COMMITS + BACKLOG_BATCHES * BACKLOG_BATCH_COMMITS; // 600

  const identity = await store.storeIdentity();
  assert.equal(identity.status, "available");
  const storeIdentity = identity.status === "available" ? identity.store_identity : "";

  const steady = Array.from({length: STEADY_COMMITS}, (_, index) => plannedCommit(index + 1, null));
  let revision = await commitPlanned(store, steady, null);

  // The consumer runs only while its process is up: within a run it pages
  // forward, and every page's cursor is durably persisted before the next
  // one. `consumedRows` is the verifier's ledger, not consumer state.
  const consumedRows: AuthorityStoreCommittedTransaction[] = [];
  const consumePages = async (pages: number): Promise<void> => {
    for (let page = 0; page < pages; page++) {
      const after = await loadPersistedCursor(cursorPath);
      const result = await store.scanCommitted(after, PAGE);
      assert.equal(result.status, "page", JSON.stringify(result));
      if (result.status !== "page") return;
      if (result.transactions.length === 0) {
        assert.equal(result.has_more, false);
        return;
      }
      consumedRows.push(...result.transactions);
      assert.notEqual(result.next_cursor, null);
      await persistConsumerCursor(cursorPath, result.next_cursor!);
    }
  };

  // Consume to midway through the steady history, then stop the consumer.
  const midwayPages = 20;
  await consumePages(midwayPages);
  const stoppedAt = await loadPersistedCursor(cursorPath);
  assert.equal(stoppedAt, String(midwayPages * PAGE));

  // Synthetic 24-hour backlog: the authority keeps committing in batches
  // while the consumer is stopped. Hour labels make the stop synthetic; no
  // real time is waited for anywhere in this test.
  const ledger: PlannedCommit[] = [...steady];
  for (let batch = 1; batch <= BACKLOG_BATCHES; batch++) {
    const hours = batch * (24 / BACKLOG_BATCHES);
    const planned = Array.from({length: BACKLOG_BATCH_COMMITS}, (_, index) =>
      plannedCommit(STEADY_COMMITS + (batch - 1) * BACKLOG_BATCH_COMMITS + index + 1, hours));
    revision = await commitPlanned(store, planned, revision);
    ledger.push(...planned);
  }
  assert.equal(ledger.length, totalCommits);

  // The lag is real on disk: the durable consumer cursor is hundreds of
  // commits behind the promoted head, and it still describes a valid scan
  // position rather than an out-of-range one.
  const headDuringStop = await store.loadAuthority();
  assert.equal(headDuringStop.status, "loaded");
  const headCursorDuringStop = headDuringStop.status === "loaded" ? headDuringStop.cursor : "0";
  assert.equal(Number(headCursorDuringStop) - Number(stoppedAt),
    totalCommits - midwayPages * PAGE, "consumer is behind by the whole synthetic backlog");
  const laggedPage = await store.scanCommitted(stoppedAt, PAGE);
  assert.equal(laggedPage.status, "page");

  // Deterministic torn-iteration model: the consumer fetches a page and dies
  // before persisting its cursor (power loss between processing and the
  // durable cursor update). The next incarnation re-reads from the persisted
  // cursor and must receive a byte-identical page, which is what makes
  // idempotent redelivery safe for a real kill at any point.
  {
    const after = await loadPersistedCursor(cursorPath);
    const torn = await store.scanCommitted(after, PAGE);
    assert.equal(torn.status, "page");
    // Nothing is persisted; the whole in-memory iteration is discarded.
    const redelivered = await store.scanCommitted(await loadPersistedCursor(cursorPath), PAGE);
    assert.equal(redelivered.status, "page");
    if (torn.status === "page" && redelivered.status === "page") {
      assert.equal(redelivered.transactions.length, torn.transactions.length);
      for (const [index, row] of redelivered.transactions.entries()) {
        const lost: AuthorityStoreCommittedTransaction = torn.transactions[index]!;
        assert.equal(row.cursor, lost.cursor);
        assert.equal(row.operation_id, lost.operation_id);
        assert.deepEqual(row.events, lost.events);
        assert.deepEqual(row.receipts, lost.receipts);
        assert.equal(canonicalAuthorityBytes(row.projection).toString("utf8"),
          canonicalAuthorityBytes(lost.projection).toString("utf8"));
      }
      assert.equal(redelivered.has_more, torn.has_more);
    }
  }

  // Resume across several simulated restarts: each chunk reconstructs the
  // consumer from the durable cursor file only, the way a replaced process
  // would after power loss or a deploy restart.
  for (;;) {
    const before = await loadPersistedCursor(cursorPath);
    await consumePages(9); // an odd chunk so restart edges also drift
    const after = await loadPersistedCursor(cursorPath);
    if (after === headCursorDuringStop) break;
    assert.notEqual(after, before, "each restart chunk must make progress");
  }
  // A final restart after catching up redelivers nothing.
  await consumePages(1);
  const caughtUp = await store.scanCommitted(await loadPersistedCursor(cursorPath), PAGE);
  assert.equal(caughtUp.status, "page");
  if (caughtUp.status === "page") {
    assert.deepEqual(caughtUp.transactions, []);
    assert.equal(caughtUp.has_more, false);
    assert.equal(caughtUp.next_cursor, String(totalCommits));
  }
  // A cursor ahead of the head is refused instead of silently empty.
  const ahead = await store.scanCommitted(String(totalCommits + 1), PAGE);
  assert.equal(ahead.status, "failed");
  if (ahead.status === "failed") assert.equal(ahead.reason_code, "scan_cursor_out_of_range");

  assertPostPromotionBoundary(root, consumerDirectory);
  assertStreamMatchesLedger(consumedRows, ledger, storeIdentity);
  // The cursor file that survived the whole journey is still on disk.
  assert.equal(await loadPersistedCursor(cursorPath), String(totalCommits));
  const audit = await store.verifyAuthorityHistory();
  assert.equal(audit.status, "verified");
  if (audit.status === "verified") assert.equal(audit.commits, totalCommits);
});

test("SQLite consumer restart after a real process kill resumes from the durable cursor with identical redelivery", {timeout: 120000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-consumer-lag-kill-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const consumerDirectory = join(root, "consumer-state");
  await mkdir(consumerDirectory, {recursive: true});
  const cursorPath = join(consumerDirectory, "consumer-cursor.json");
  const store = new SqliteAuthorityStore(root, GOAL_ID);
  const TOTAL = 200;
  const PAGE = 5;

  const identity = await store.storeIdentity();
  assert.equal(identity.status, "available");
  const storeIdentity = identity.status === "available" ? identity.store_identity : "";
  const ledger = Array.from({length: TOTAL}, (_, index) => plannedCommit(index + 1, 24));
  await commitPlanned(store, ledger, null);

  // Real child process, real SIGKILL: the only state that survives the kill
  // is the durable cursor file (and the stdout log collected by the parent).
  // The rendezvous directory makes the kill deterministic: the child blocks
  // at its hold point with unconsumed pages remaining, so the SIGKILL always
  // lands mid-journal regardless of process scheduling.
  const killAfterCursor = 12 * PAGE;
  const holdDir = join(root, "consumer-hold");
  await mkdir(holdDir, {recursive: true});
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(new URL("./sqlite_authority_consumer_process.ts", import.meta.url)),
    root, GOAL_ID, cursorPath, String(PAGE), holdDir, String(killAfterCursor)],
  {stdio: ["pipe", "pipe", "pipe"]});
  t.after(() => child.kill());
  let output = "", error = "";
  child.stdout.on("data", data => {output += String(data);});
  child.stderr.on("data", data => {error += String(data);});
  const closed = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve =>
    child.on("close", (code, signal) => resolve({code, signal})));
  const logs = (): ConsumerLogs => ({output, stderr: error});
  await waitForConsumerReady(child, closed, logs);
  assert.equal(error, "");
  child.stdin.end("go");

  // Kill at the child's controlled hold point. The marker name appears only
  // after the same-directory staging write is renamed into place, and only
  // once the durable cursor passed killAfterCursor and unconsumed pages
  // remain. The parent reads the staging cursor while hold-ready is absent,
  // then lets the rename happen, so an empty marker is not a cursor.
  const heldCursor = await waitForPublishedHold(holdDir, child, closed, logs);
  const heldAtCursor = Number(heldCursor);
  assert.ok(heldAtCursor >= killAfterCursor, "the child held only after the durable cursor advanced");
  assert.ok(heldAtCursor < ledger.length, "the child held with unconsumed transactions remaining");
  const exitedBeforeKill = await exitedConsumer(child, closed);
  if (exitedBeforeKill !== null) {
    failConsumerExited(exitedBeforeKill, output, error,
      `after publishing hold-ready at cursor ${heldCursor} and before SIGKILL`);
  }
  child.kill("SIGKILL");
  const killed = await closed;
  assert.notEqual(killed.code, 0);
  if (process.platform !== "win32") assert.equal(killed.signal, "SIGKILL");
  assert.equal(error, "");

  // Parse the killed child's log. A SIGKILL can tear the final stdout write;
  // only a trailing unparsable line may be dropped, never an inner one.
  const logged = output.slice(output.indexOf("ready\n") + "ready\n".length)
    .split("\n").filter(line => line.length > 0);
  const parsed: {cursor: string; operation_id: string; events: JsonObject[]; receipts: JsonObject[]}[] = [];
  for (const [index, line] of logged.entries()) {
    try {
      parsed.push(JSON.parse(line) as {cursor: string; operation_id: string;
        events: JsonObject[]; receipts: JsonObject[]});
    } catch (error) {
      if (index === logged.length - 1) break; // torn stdout write from the kill
      throw error;
    }
  }
  assert.ok(parsed.length > 0, "the killed consumer logged at least one full page");

  // Recovery in a fresh consumer: resume from exactly the durable cursor.
  const resumeFrom = await loadPersistedCursor(cursorPath);
  assert.ok(resumeFrom !== null, "the durable cursor survived the kill");
  const resumed: AuthorityStoreCommittedTransaction[] = [];
  let after = resumeFrom;
  for (;;) {
    const page = await store.scanCommitted(after, PAGE);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") break;
    if (page.transactions.length === 0) {
      assert.equal(page.has_more, false);
      break;
    }
    resumed.push(...page.transactions);
    assert.notEqual(page.next_cursor, null);
    await persistConsumerCursor(cursorPath, page.next_cursor!);
    after = page.next_cursor!;
    if (!page.has_more) break;
  }

  // Transactions the child logged beyond the durable cursor are redelivered
  // to the recovering consumer; redelivery must be identical (same cursor,
  // operation id, events, receipts) so idempotent processing stays safe.
  // Whether the kill lands in this window is timing-dependent; the
  // deterministic torn-iteration case is covered in the restart test above.
  const loggedByCursor = new Map(parsed.map(row => [row.cursor, row]));
  for (const row of resumed) {
    const seen = loggedByCursor.get(row.cursor);
    if (seen === undefined) continue;
    assert.equal(seen.operation_id, row.operation_id, `redelivered cursor ${row.cursor}`);
    assert.deepEqual(seen.events, row.events, `redelivered events at cursor ${row.cursor}`);
    assert.deepEqual(seen.receipts, row.receipts, `redelivered receipts at cursor ${row.cursor}`);
  }
  // The union of the killed child's log and the recovering consumer's reads
  // covers every committed operation exactly once. Cursors the child logged
  // beyond the durable cursor appear in both sources; those are the identical
  // redeliveries proven above, not duplicates.
  const union = new Map<string, {operation_id: string; events: JsonObject[]; receipts: JsonObject[]}>();
  for (const row of parsed) {
    assert.equal(union.has(row.cursor), false, `child logged cursor ${row.cursor} twice`);
    union.set(row.cursor, row);
  }
  for (const row of resumed) {
    if (union.has(row.cursor)) continue; // identical redelivery, proven above
    union.set(row.cursor, {operation_id: row.operation_id,
      events: [...row.events], receipts: [...row.receipts]});
  }
  assert.equal(union.size, TOTAL);
  assert.deepEqual([...union.keys()].sort((left, right) => Number(left) - Number(right)),
    ledger.map((_, index) => String(index + 1)));
  // Every delivered row carries the original proof, and the projection the
  // recovering consumer reads still equals the committed projection.
  for (const [index, commit] of ledger.entries()) {
    const row = union.get(String(index + 1))!;
    assert.equal(row.operation_id, commit.operationId);
    assert.deepEqual(row.receipts, commit.receipts, `receipts at cursor ${index + 1}`);
    assert.deepEqual(row.events, commit.events, `events at cursor ${index + 1}`);
  }
  // The recovering consumer's own reads form a contiguous verified stream
  // from the durable cursor to the head.
  assertStreamMatchesLedger(resumed, ledger.slice(Number(resumeFrom)), storeIdentity,
    BigInt(resumeFrom) + 1n);

  assertPostPromotionBoundary(root, consumerDirectory);
  assert.equal(await loadPersistedCursor(cursorPath), String(TOTAL));
  const audit = await store.verifyAuthorityHistory();
  assert.equal(audit.status, "verified");
  if (audit.status === "verified") assert.equal(audit.commits, TOTAL);
});

test("SQLite consumer hold wait reports a child that exits before the marker is published", {timeout: 30000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-consumer-lag-exit-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const cursorPath = join(root, "consumer-cursor.json");
  const holdDir = join(root, "consumer-hold");
  await mkdir(holdDir, {recursive: true});
  const store = new SqliteAuthorityStore(root, GOAL_ID);
  await commitPlanned(store, Array.from({length: 10}, (_, index) => plannedCommit(index + 1, null)), null);

  // The hold threshold is past the journal, so the child drains every page
  // and exits 0 without publishing hold-ready. The parent must say so
  // immediately instead of waiting out the deadline and reporting a miss.
  const child = spawn(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    fileURLToPath(new URL("./sqlite_authority_consumer_process.ts", import.meta.url)),
    root, GOAL_ID, cursorPath, "5", holdDir, "1000"],
  {stdio: ["pipe", "pipe", "pipe"]});
  t.after(() => child.kill());
  let output = "", error = "";
  child.stdout.on("data", data => {output += String(data);});
  child.stderr.on("data", data => {error += String(data);});
  const closed = new Promise<{code: number | null; signal: NodeJS.Signals | null}>(resolve =>
    child.on("close", (code, signal) => resolve({code, signal})));
  const logs = (): ConsumerLogs => ({output, stderr: error});
  await waitForConsumerReady(child, closed, logs);
  child.stdin.end("go");
  const started = Date.now();
  await assert.rejects(() => waitForPublishedHold(holdDir, child, closed, logs), (caught: unknown) => {
    assert.ok(caught instanceof Error);
    assert.match(caught.message, /consumer child exited before publishing hold-ready \(code=0, signal=null,/);
    return true;
  });
  assert.ok(Date.now() - started < 5000, "an early exit must not wait for the hold deadline");
  assert.equal(existsSync(join(holdDir, "hold-ready")), false);
});
