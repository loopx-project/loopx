import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile} from "node:fs/promises";
import test from "node:test";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {SqliteAuthorityStore, sqliteAuthorityPath} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {AUTHORITY_STATE_CHECKPOINT_INTERVAL} from "../../loopx/control_plane/coordination/authority_state_log.ts";
import {canonicalAuthorityBytes} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {snapshotSqliteDatabase} from "../../loopx/control_plane/coordination/sqlite_backup.ts";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import type {AuthorityStoreCommittedTransaction} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

/**
 * Backup/restore lineage for the promoted SQLite authority store.
 *
 * Evidence path named by the 2026-11-10 kickoff on issue #4224 (batch 3 slice
 * 2) and by the RFC 7.2 recovery qualification list: "backup/restore lineage
 * ... no lost acknowledged commit or repeated effect". #8367 covered the
 * post-promotion lagged consumer reading the store's own `scanCommitted`
 * outbox; this file covers what happens when the whole database is copied to
 * another location and reopened:
 *
 * - (a) a file-level backup taken at a transaction boundary restores the
 *   complete committed state — identity, cursor, head projection, original
 *   receipts, the full `scanCommitted` pagination and a verified history;
 * - (b) a restored copy and its origin share one store identity but diverge
 *   into isolated lineages that never observe each other's later commits;
 * - (c) a backup taken mid checkpoint window (non-aligned cursor) still
 *   replays its full retained history after restore, per #8367's window
 *   replay semantics;
 * - (d) a tampered or truncated restored database fails closed with a
 *   protocol error instead of a plausible misread.
 *
 * Backup method and WAL correctness: the store commits through short-lived
 * connections in WAL mode, so acknowledged content may live only in the
 * `-wal` file until a checkpoint runs. Copying the main database file bytes
 * is coherent only when no live connection holds un-checkpointed WAL content.
 * These tests use the SQLite online backup API through the repository's
 * qualified snapshot owner (`snapshotSqliteDatabase`, node:sqlite `backup`),
 * which reads the logical database — main file plus committed WAL frames —
 * through an open connection and materializes a standalone journal-DELETE
 * database at the destination, so the copy is coherent whether or not a
 * checkpoint has run. Test (a) proves that is not luck: an idle concurrent
 * connection stays open so the store's own connections are never the last
 * connection, commits 2..N exist only in the WAL, and a naive main-file-only
 * copy restores just cursor 1 while the API backup restores the complete head.
 */
const GOAL_ID = "goal-backup";
const PAGE = 7; // Shares no factor boundary with the 64-commit checkpoint window.

const FIXED_TODOS: readonly JsonObject[] = [{
  schema_version: "todo_item_v0",
  todo_id: "todo-backup-a",
  role: "agent",
  status: "open",
  done: false,
  text: "Survive a file-level backup and restore",
  archive_state: "active",
  source_section: "Agent Todo",
  required_write_scopes: ["loopx/control_plane/**"],
}, {
  schema_version: "todo_item_v0",
  todo_id: "todo-backup-b",
  role: "agent",
  status: "open",
  done: false,
  text: "Keep divergent restored lineages isolated",
  archive_state: "active",
  source_section: "Agent Todo",
  required_write_scopes: ["loopx/control_plane/**"],
}];

interface PlannedCommit {
  operationId: string;
  projection: JsonObject;
  events: readonly JsonObject[];
  receipts: readonly JsonObject[];
}

/** Fixed Todo bodies plus moving scalars keep each delta tiny, so the
 * history measures backup and lineage behavior, not payload growth. */
function plannedCommit(ordinal: number, prefix: string, extras: JsonObject = {}): PlannedCommit {
  const operationId = `${prefix}-${String(ordinal).padStart(4, "0")}`;
  return {
    operationId,
    projection: authorityProjectionFixture(GOAL_ID, FIXED_TODOS, [], "native", {
      backup_ordinal: ordinal, ...extras,
    }),
    events: [{
      schema_version: "loopx_authority_event_v0",
      type: "backup_lineage_probe",
      ordinal,
      ...extras,
    }],
    receipts: [{
      schema_version: "loopx_authority_receipt_v0",
      operation_id: operationId,
      ordinal,
      ...extras,
    }],
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

/** Page through the whole durable outbox, refusing torn or unordered pages. */
async function scanAll(store: SqliteAuthorityStore,
  pageSize: number): Promise<AuthorityStoreCommittedTransaction[]> {
  const rows: AuthorityStoreCommittedTransaction[] = [];
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, pageSize);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") return rows;
    if (page.transactions.length === 0) {
      assert.equal(page.has_more, false);
      return rows;
    }
    rows.push(...page.transactions);
    assert.notEqual(page.next_cursor, null);
    after = page.next_cursor!;
    if (!page.has_more) return rows;
  }
}

/** The scanned stream must be exactly the committed ledger: contiguous
 * cursors from 1, original operation ids, events and receipts, exact
 * committed projections, and provider revisions anchored to the identity the
 * store being scanned actually reports. */
function assertRowsMatchLedger(rows: readonly AuthorityStoreCommittedTransaction[],
  ledger: readonly PlannedCommit[], identity: string): void {
  assert.equal(rows.length, ledger.length);
  let previous = 0n;
  for (const [index, row] of rows.entries()) {
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

/** The restored outbox must equal the outbox captured at backup time, not
 * merely a same-length plausible stream. */
function assertRowsEqualCapture(restored: readonly AuthorityStoreCommittedTransaction[],
  captured: readonly AuthorityStoreCommittedTransaction[]): void {
  assert.equal(restored.length, captured.length);
  for (const [index, row] of restored.entries()) {
    const before = captured[index]!;
    assert.equal(row.cursor, before.cursor, `cursor at index ${index}`);
    assert.equal(row.operation_id, before.operation_id, `operation id at index ${index}`);
    assert.equal(row.provider_revision, before.provider_revision, `revision at index ${index}`);
    assert.deepEqual(row.events, before.events, `events at cursor ${row.cursor}`);
    assert.deepEqual(row.receipts, before.receipts, `receipts at cursor ${row.cursor}`);
    assert.equal(canonicalAuthorityBytes(row.projection).toString("utf8"),
      canonicalAuthorityBytes(before.projection).toString("utf8"),
      `projection at cursor ${row.cursor} must equal the backup-time projection`);
  }
}

async function backupDatabase(sourcePath: string, snapshotPath: string): Promise<void> {
  await snapshotSqliteDatabase(sourcePath, snapshotPath);
  // The snapshot is one standalone journal-DELETE database: restoring it needs
  // no -wal/-shm companion, which is what makes a plain file copy of the
  // snapshot a complete restore rather than a WAL-luck dependency.
  assert.equal(existsSync(`${snapshotPath}-wal`), false);
  assert.equal(existsSync(`${snapshotPath}-shm`), false);
}

/** Copy the standalone snapshot into a fresh directory under the store's own
 * derived file name, the way an operator restores a kept backup. */
async function restoreSnapshot(t: test.TestContext, snapshotPath: string,
  name: string): Promise<{directory: string; store: SqliteAuthorityStore}> {
  const directory = await mkdtemp(join(tmpdir(), `sqlite-backup-${name}-`));
  t.after(() => rm(directory, {recursive: true, force: true}));
  await copyFile(snapshotPath, sqliteAuthorityPath(directory, GOAL_ID));
  return {directory, store: new SqliteAuthorityStore(directory, GOAL_ID)};
}

test("SQLite transaction-boundary backup restores the complete committed lineage including WAL-resident commits",
  {timeout: 120000}, async t => {
    const root = await mkdtemp(join(tmpdir(), "sqlite-backup-restore-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const sourceDirectory = join(root, "source");
    const store = new SqliteAuthorityStore(sourceDirectory, GOAL_ID);
    // Head sits exactly on its own checkpoint (129 = window start 129), so the
    // backup point covers two full delta windows plus a zero-recovery-tail edge.
    const TOTAL = AUTHORITY_STATE_CHECKPOINT_INTERVAL * 2 + 1;
    const ledger = Array.from({length: TOTAL}, (_, index) => plannedCommit(index + 1, "restore-op"));
    const {driver} = sqliteAuthorityRuntime();

    // Commit 1 first: its connection is the last connection when it closes, so
    // the qualified runtime checkpoints it into the main database file and
    // removes the WAL (WAL-reset fix plus finalized statements on close).
    const seeded = await commitPlanned(store, ledger.slice(0, 1), null);
    assert.notEqual(seeded, null);

    // A concurrent connection now stays open across every later commit, with
    // an open read transaction pinned at the cursor-1 snapshot — the shape a
    // lagged consumer (#8367) has while the authority advances. This is what
    // makes the WAL state deterministic rather than scheduler luck:
    // - the held read-mark forbids every checkpoint (close-time, passive or
    //   automatic) from copying any later frame into the main database file,
    //   so commits 2..TOTAL exist only in the -wal file at the backup point;
    // - the registered connection also stops the store's short-lived
    //   connections from running close-time WAL cleanup (an idle connection
    //   that never read is invisible to that cleanup, which then reclaims the
    //   WAL after every commit).
    const keeper = new driver.DatabaseSync(store.path);
    keeper.exec("BEGIN");
    assert.equal(keeper.prepare("SELECT COUNT(*) AS commits FROM commits").get()?.commits, 1);
    try {
      await commitPlanned(store, ledger.slice(1), seeded);

      // The WAL state is real, not assumed: un-checkpointed committed content
      // is on disk at the backup point.
      const walPath = `${store.path}-wal`;
      assert.equal(existsSync(walPath), true, "an open concurrent connection must keep the WAL on disk");
      assert.ok((await stat(walPath)).size > 0, "the WAL must hold committed content at backup time");

      // Capture the complete committed state at the backup point.
      const identityResult = await store.storeIdentity();
      assert.equal(identityResult.status, "available");
      const identity = identityResult.status === "available" ? identityResult.store_identity : "";
      const headAtBackup = await store.loadAuthority();
      assert.equal(headAtBackup.status, "loaded", JSON.stringify(headAtBackup));
      if (headAtBackup.status === "loaded") assert.equal(headAtBackup.cursor, String(TOTAL));
      const capturedScan = await scanAll(store, PAGE);
      assertRowsMatchLedger(capturedScan, ledger, identity);
      const auditAtBackup = await store.verifyAuthorityHistory();
      assert.equal(auditAtBackup.status, "verified");
      if (auditAtBackup.status === "verified") {
        assert.equal(auditAtBackup.commits, TOTAL);
        assert.equal(auditAtBackup.checkpoints, 3); // windows 1, 65, 129
      }

      // The wrong method, kept as the contrast that proves the backup API is
      // not trivially copying an already-checkpointed file: copying only the
      // main database file while the WAL holds commits 2..TOTAL restores just
      // cursor 1, losing every acknowledged commit after the seed. The copy is
      // taken with no writer active, so the main file is stable.
      const naiveDirectory = join(root, "naive-main-file-copy");
      await mkdir(naiveDirectory, {recursive: true, mode: 0o700});
      await copyFile(store.path, sqliteAuthorityPath(naiveDirectory, GOAL_ID));
      const naive = new SqliteAuthorityStore(naiveDirectory, GOAL_ID);
      const naiveHead = await naive.loadAuthority();
      assert.equal(naiveHead.status, "loaded", JSON.stringify(naiveHead));
      if (naiveHead.status === "loaded") {
        assert.equal(naiveHead.cursor, "1", "a main-file-only copy must miss the WAL-resident commits");
      }
      assert.equal((await naive.readReceipt(ledger[1]!.operationId)).status, "missing",
        "the naive copy lost the acknowledged commit at cursor 2");

      // The qualified backup: the online backup API reads the logical database
      // including the committed WAL frames and writes a standalone copy.
      const snapshotPath = join(root, "backup.sqlite");
      await backupDatabase(store.path, snapshotPath);

      // The backup left the source untouched.
      const sourceAfter = await store.loadAuthority();
      assert.equal(sourceAfter.status, "loaded");
      if (sourceAfter.status === "loaded") assert.equal(sourceAfter.cursor, String(TOTAL));

      // Restore into a fresh directory and prove complete equality with the
      // backup point: identity, cursor, head projection, original receipts and
      // the exact durable outbox.
      const {store: restored} = await restoreSnapshot(t, snapshotPath, "restore");
      const restoredIdentity = await restored.storeIdentity();
      assert.equal(restoredIdentity.status, "available");
      if (restoredIdentity.status === "available") {
        assert.equal(restoredIdentity.store_identity, identity,
          "restore preserves the store identity");
      }
      const restoredHead = await restored.loadAuthority();
      assert.equal(restoredHead.status, "loaded", JSON.stringify(restoredHead));
      if (restoredHead.status === "loaded" && headAtBackup.status === "loaded") {
        assert.equal(restoredHead.cursor, headAtBackup.cursor);
        assert.equal(restoredHead.provider_revision, headAtBackup.provider_revision);
        assert.equal(canonicalAuthorityBytes(restoredHead.head).toString("utf8"),
          canonicalAuthorityBytes(headAtBackup.head).toString("utf8"),
          "restored head projection must equal the backup-time projection");
      }
      // Original receipts survive verbatim: spot-check the first commit, the
      // middle window-start checkpoint commit and the head commit.
      for (const ordinal of [1, AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1, TOTAL]) {
        const planned = ledger[ordinal - 1]!;
        const receipt = await restored.readReceipt(planned.operationId);
        assert.equal(receipt.status, "found", `receipt for cursor ${ordinal}`);
        if (receipt.status === "found") {
          assert.equal(receipt.cursor, String(ordinal));
          assert.equal(receipt.provider_revision, `${identity}:${ordinal}`);
          assert.deepEqual(receipt.receipts, planned.receipts);
        }
      }
      const restoredScan = await scanAll(restored, PAGE);
      assertRowsEqualCapture(restoredScan, capturedScan);
      assertRowsMatchLedger(restoredScan, ledger, identity);
      const restoredAudit = await restored.verifyAuthorityHistory();
      assert.equal(restoredAudit.status, "verified");
      if (restoredAudit.status === "verified") {
        assert.equal(restoredAudit.commits, TOTAL);
        assert.equal(restoredAudit.checkpoints, 3);
      }
    } finally {
      keeper.close();
    }
  });

test("SQLite restored copy diverges from its origin with the same identity but isolated lineages",
  {timeout: 60000}, async t => {
    const root = await mkdtemp(join(tmpdir(), "sqlite-backup-diverge-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const sourceDirectory = join(root, "source");
    const original = new SqliteAuthorityStore(sourceDirectory, GOAL_ID);
    const SHARED = AUTHORITY_STATE_CHECKPOINT_INTERVAL - 24; // 40, one open window
    const DIVERGENT = 6;
    const shared = Array.from({length: SHARED}, (_, index) => plannedCommit(index + 1, "shared-op"));
    const backupRevision = await commitPlanned(original, shared, null);
    assert.notEqual(backupRevision, null);

    // With every connection closed the runtime already checkpointed the WAL,
    // and the backup API is coherent in either WAL state (see the suite note).
    const identity = await original.storeIdentity();
    assert.equal(identity.status, "available");
    const storeIdentity = identity.status === "available" ? identity.store_identity : "";
    const snapshotPath = join(root, "backup.sqlite");
    await backupDatabase(original.path, snapshotPath);

    const {store: replica} = await restoreSnapshot(t, snapshotPath, "replica");
    const replicaIdentity = await replica.storeIdentity();
    assert.equal(replicaIdentity.status, "available");
    if (replicaIdentity.status === "available") {
      assert.equal(replicaIdentity.store_identity, storeIdentity,
        "a restored copy keeps the origin's store identity");
    }

    // Both lineages continue from the same backup head, each acknowledging its
    // own commits against the same expected revision.
    const divergence = (lineage: "origin" | "replica") => Array.from({length: DIVERGENT},
      (_, index) => plannedCommit(SHARED + index + 1, `${lineage}-line`, {lineage}));
    await commitPlanned(original, divergence("origin"), backupRevision);
    await commitPlanned(replica, divergence("replica"), backupRevision);

    const originalHead = await original.loadAuthority();
    const replicaHead = await replica.loadAuthority();
    assert.equal(originalHead.status, "loaded");
    assert.equal(replicaHead.status, "loaded");
    if (originalHead.status === "loaded" && replicaHead.status === "loaded") {
      // provider_revision is identity plus cursor, not a content address:
      // after a restore-based split the same revision string names different
      // content, which is why the isolation assertions below are the proof.
      assert.equal(originalHead.cursor, String(SHARED + DIVERGENT));
      assert.equal(replicaHead.cursor, String(SHARED + DIVERGENT));
      assert.equal(originalHead.provider_revision, replicaHead.provider_revision);
      assert.notEqual(canonicalAuthorityBytes(originalHead.head).toString("utf8"),
        canonicalAuthorityBytes(replicaHead.head).toString("utf8"),
        "diverged lineages must not converge to one projection");
    }

    // Each lineage scans exactly its own history up to its own cursor; neither
    // sees the other's post-backup commits.
    const expectedShared = shared.map(commit => commit.operationId);
    const originalIds = (await scanAll(original, PAGE)).map(row => row.operation_id);
    const replicaIds = (await scanAll(replica, PAGE)).map(row => row.operation_id);
    assert.deepEqual(originalIds, [...expectedShared, ...divergence("origin").map(c => c.operationId)]);
    assert.deepEqual(replicaIds, [...expectedShared, ...divergence("replica").map(c => c.operationId)]);
    assert.equal(originalIds.some(id => id.startsWith("replica-")), false,
      "the origin must not see the replica's post-backup commits");
    assert.equal(replicaIds.some(id => id.startsWith("origin-")), false,
      "the replica must not see the origin's post-backup commits");

    // Receipt lookups do not cross lineages; the shared prefix stays findable
    // from both with identical original receipts. Divergence operation ids
    // carry the global ordinal (43 = SHARED + 3), not the per-side index.
    const thirdDivergenceId = (lineage: "origin" | "replica") =>
      `${lineage}-line-${String(SHARED + 3).padStart(4, "0")}`;
    assert.equal((await original.readReceipt(thirdDivergenceId("replica"))).status, "missing");
    assert.equal((await replica.readReceipt(thirdDivergenceId("origin"))).status, "missing");
    for (const [store, lineage] of [[original, "origin"], [replica, "replica"]] as const) {
      const own = await store.readReceipt(thirdDivergenceId(lineage));
      assert.equal(own.status, "found");
      const sharedReceipt = await store.readReceipt(shared[SHARED - 1]!.operationId);
      assert.equal(sharedReceipt.status, "found");
      if (sharedReceipt.status === "found") {
        assert.deepEqual(sharedReceipt.receipts, shared[SHARED - 1]!.receipts);
        assert.equal(sharedReceipt.cursor, String(SHARED));
      }
      const audit = await store.verifyAuthorityHistory();
      assert.equal(audit.status, "verified");
      if (audit.status === "verified") assert.equal(audit.commits, SHARED + DIVERGENT);
    }
  });

test("SQLite backup taken mid checkpoint window replays its full unaligned history after restore",
  {timeout: 60000}, async t => {
    const root = await mkdtemp(join(tmpdir(), "sqlite-backup-unaligned-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const sourceDirectory = join(root, "source");
    const store = new SqliteAuthorityStore(sourceDirectory, GOAL_ID);
    // Cursor 70 sits inside the window starting at checkpoint 65: the backup
    // point keeps a five-delta recovery tail instead of a head checkpoint.
    const TOTAL = AUTHORITY_STATE_CHECKPOINT_INTERVAL + 6; // 70
    const ledger = Array.from({length: TOTAL}, (_, index) => plannedCommit(index + 1, "unaligned-op"));
    const identityResult = await store.storeIdentity();
    assert.equal(identityResult.status, "available");
    const identity = identityResult.status === "available" ? identityResult.store_identity : "";
    await commitPlanned(store, ledger, null);

    const snapshotPath = join(root, "backup.sqlite");
    await backupDatabase(store.path, snapshotPath);
    const {store: restored} = await restoreSnapshot(t, snapshotPath, "unaligned");

    // The full retained history still replays from the empty root across both
    // checkpoint windows: complete archive verification, not just the head.
    const audit = await restored.verifyAuthorityHistory();
    assert.equal(audit.status, "verified");
    if (audit.status === "verified") {
      assert.equal(audit.commits, TOTAL);
      assert.equal(audit.checkpoints, 2); // windows 1 and 65
    }
    const profile = await restored.boundedProfile();
    assert.equal(profile.status, "available");
    if (profile.status === "available") {
      assert.equal(profile.cursor, String(TOTAL));
      assert.equal(profile.commits, TOTAL);
      assert.equal(profile.checkpoints, 2);
      assert.equal(profile.checkpoint_interval, AUTHORITY_STATE_CHECKPOINT_INTERVAL);
      assert.equal(profile.recovery_tail_commits, TOTAL - (AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1),
        "the restored head is a mid-window backup point with a five-delta tail");
    }
    const head = await restored.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status === "loaded") {
      assert.equal(head.cursor, String(TOTAL));
      assert.equal(canonicalAuthorityBytes(head.head).toString("utf8"),
        canonicalAuthorityBytes(ledger[TOTAL - 1]!.projection).toString("utf8"));
    }

    // The durable outbox pages across the unaligned backup point exactly as it
    // did before the backup; page edges drift against the window edges.
    assertRowsMatchLedger(await scanAll(restored, PAGE), ledger, identity);
    // A page straddling the second window's checkpoint reconstructs rows 63-64
    // from window 1 deltas and rows 65-67 from checkpoint 65 plus deltas — the
    // same window replay semantics #8367 certified for the live store.
    const boundary = await restored.scanCommitted("62", 5);
    assert.equal(boundary.status, "page", JSON.stringify(boundary));
    if (boundary.status === "page") {
      assert.deepEqual(boundary.transactions.map(row => row.cursor), ["63", "64", "65", "66", "67"]);
      for (const [index, row] of boundary.transactions.entries()) {
        const planned = ledger[62 + index]!;
        assert.equal(row.operation_id, planned.operationId);
        assert.deepEqual(row.receipts, planned.receipts);
        assert.equal(canonicalAuthorityBytes(row.projection).toString("utf8"),
          canonicalAuthorityBytes(planned.projection).toString("utf8"),
          `projection at cursor ${row.cursor} must replay from its covering checkpoint`);
      }
    }
    // Window-edge receipts stay findable from the restored mid-window copy.
    for (const ordinal of [AUTHORITY_STATE_CHECKPOINT_INTERVAL, AUTHORITY_STATE_CHECKPOINT_INTERVAL + 1]) {
      const receipt = await restored.readReceipt(ledger[ordinal - 1]!.operationId);
      assert.equal(receipt.status, "found", `receipt at cursor ${ordinal}`);
      if (receipt.status === "found") assert.deepEqual(receipt.receipts, ledger[ordinal - 1]!.receipts);
    }
  });

test("SQLite restore fails closed on a tampered or truncated backup file", {timeout: 60000}, async t => {
  const root = await mkdtemp(join(tmpdir(), "sqlite-backup-tamper-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const sourceDirectory = join(root, "source");
  const store = new SqliteAuthorityStore(sourceDirectory, GOAL_ID);
  const TOTAL = AUTHORITY_STATE_CHECKPOINT_INTERVAL + 32; // 96, mid second window
  // A byte-unique marker inside the head commit's original receipts, so the
  // one flipped byte lands inside retained payload where the store's own
  // digest proof must catch it deterministically.
  const TAMPER_MARKER = `backup-tamper-probe-${"z".repeat(64)}`;
  const headOperationId = `tamper-op-${String(TOTAL).padStart(4, "0")}`;
  const headCommit: PlannedCommit = {
    operationId: headOperationId,
    projection: authorityProjectionFixture(GOAL_ID, FIXED_TODOS, [], "native", {backup_ordinal: TOTAL}),
    events: [{schema_version: "loopx_authority_event_v0", type: "backup_lineage_probe", ordinal: TOTAL}],
    receipts: [{schema_version: "loopx_authority_receipt_v0", operation_id: headOperationId,
      ordinal: TOTAL, backup_marker: TAMPER_MARKER}],
  };
  const ledger = Array.from({length: TOTAL - 1}, (_, index) => plannedCommit(index + 1, "tamper-op"));
  const revision = await commitPlanned(store, ledger, null);
  assert.notEqual(revision, null);
  const headSeeded = await store.commitAuthority({expected_provider_revision: revision,
    operation_id: headCommit.operationId, next_projection: headCommit.projection,
    events: headCommit.events, receipts: headCommit.receipts});
  assert.equal(headSeeded.status, "applied", JSON.stringify(headSeeded));
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");

  const snapshotPath = join(root, "backup.sqlite");
  await backupDatabase(store.path, snapshotPath);

  // A control restore of the untouched snapshot still verifies, so any failure
  // below is caused by the damage, not by the backup itself.
  const {store: control} = await restoreSnapshot(t, snapshotPath, "control");
  assert.equal((await control.verifyAuthorityHistory()).status, "verified");

  // Flipped byte: one byte inside the head commit's retained receipts.
  const {directory: flipDirectory, store: flipped} = await restoreSnapshot(t, snapshotPath, "flip");
  const flipPath = sqliteAuthorityPath(flipDirectory, GOAL_ID);
  const flippedBytes = await readFile(flipPath);
  const markerAt = flippedBytes.indexOf(TAMPER_MARKER);
  assert.ok(markerAt >= 0, "the tamper marker must be retained in the restored receipts");
  assert.equal(flippedBytes.lastIndexOf(TAMPER_MARKER), markerAt, "the marker must be byte-unique");
  assert.equal(flippedBytes[markerAt], TAMPER_MARKER.charCodeAt(0));
  flippedBytes[markerAt] = TAMPER_MARKER.charCodeAt(0) + 1; // stays valid JSON, breaks the digest
  await writeFile(flipPath, flippedBytes);
  const bytesAfterFlip = await readFile(flipPath);

  const {driver} = sqliteAuthorityRuntime();
  const sqlSnapshot = (): {head: unknown[]; commits: unknown[]} => {
    const reader = new driver.DatabaseSync(flipPath, {readOnly: true});
    try {
      return {head: reader.prepare("SELECT * FROM head ORDER BY singleton").all(),
        commits: reader.prepare("SELECT * FROM commits ORDER BY cursor").all()};
    } finally { reader.close(); }
  };
  const beforeDamage = sqlSnapshot();
  for (const result of [await flipped.loadAuthority(), await flipped.readReceipt(headOperationId),
    await flipped.scanCommitted(null, PAGE), await flipped.verifyAuthorityHistory()]) {
    assert.equal(result.status, "failed", JSON.stringify(result));
    if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation",
      "a flipped payload byte must fail the store's own proof, not parse luck");
  }
  // Read-only attempts never rewrote the damaged file.
  assert.deepEqual(await readFile(flipPath), bytesAfterFlip);
  // A write attempt against the true head revision is refused on the same
  // proof, and leaves the stored rows exactly as they were.
  const refused = await flipped.commitAuthority({
    expected_provider_revision: headSeeded.status === "applied" ? headSeeded.provider_revision : null,
    operation_id: "after-damage",
    next_projection: authorityProjectionFixture(GOAL_ID, FIXED_TODOS, [], "native", {backup_ordinal: TOTAL + 1}),
    events: [], receipts: []});
  assert.equal(refused.status, "failed", JSON.stringify(refused));
  assert.deepEqual(sqlSnapshot(), beforeDamage,
    "a refused commit must not repair or further damage the restored database");

  // Truncated file: cut to half its size, keeping the header page. SQLite's
  // own malformed detection must surface as an error rather than a partial or
  // plausible readback, and the reads must not modify the damaged bytes.
  const {directory: truncateDirectory, store: truncated} = await restoreSnapshot(t, snapshotPath, "truncate");
  const truncatePath = sqliteAuthorityPath(truncateDirectory, GOAL_ID);
  const whole = await readFile(truncatePath);
  assert.ok(whole.byteLength >= 16384, "the restored database has several pages to lose");
  const truncatedBytes = whole.subarray(0, Math.floor(whole.byteLength / 2));
  await writeFile(truncatePath, truncatedBytes);
  for (const result of [await truncated.loadAuthority(), await truncated.readReceipt(ledger[0]!.operationId),
    await truncated.scanCommitted(null, PAGE), await truncated.verifyAuthorityHistory()]) {
    assert.notEqual(result.status, "loaded", "a truncated restore must never load a head");
    assert.notEqual(result.status, "missing", "a truncated restore is corruption, not an empty store");
    assert.notEqual(result.status, "page", "a truncated restore must never page a plausible outbox");
    assert.notEqual(result.status, "found", "a truncated restore must never return a receipt");
    assert.notEqual(result.status, "verified", "a truncated restore must never verify a partial history");
    assert.ok(result.status === "failed" || result.status === "unavailable",
      `the damaged restore must surface an error, not a misread: ${JSON.stringify(result)}`);
  }
  assert.deepEqual(await readFile(truncatePath), truncatedBytes,
    "read attempts must leave the truncated bytes exactly as damaged");
});
