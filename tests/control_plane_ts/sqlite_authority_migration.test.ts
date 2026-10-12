import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {createRequire} from "node:module";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";

import {canonicalAuthorityBytes} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {migrateSqliteAuthorityStoreV1ToV2} from
  "../../loopx/control_plane/coordination/sqlite_authority_migration.ts";
import {SqliteAuthorityStore} from
  "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {createEmptySqliteAuthorityStoreV1, createSqliteAuthorityStoreV1, sqliteAuthorityV1Digest,
  type SqliteAuthorityV1Seed} from "./sqlite_authority_v1_fixture.ts";

const GOAL_ID = "migration-goal";
const HISTORY_LENGTH = 70;

async function directory(t: test.TestContext): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "sqlite-migration-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  return root;
}

function seeds(): SqliteAuthorityV1Seed[] {
  return Array.from({length: HISTORY_LENGTH}, (_unused, index) => ({
    operation_id: `migration-op-${String(index).padStart(3, "0")}`,
    projection: {
      authority_revision: index + 1,
      todos: Array.from({length: 4}, (_todo, position) => ({
        todo_id: `todo-${position}`, index: position, revision: index + 1,
      })),
      leases: index % 7 === 0 ? [] : [{todo_id: "todo-0", version: index + 1, status: "active"}],
      history_marker: `marker-${index % 5}`,
    },
    receipts: [{operation_id: `migration-op-${String(index).padStart(3, "0")}`,
      recorded_at: `2025-01-01T00:00:${String(index % 60).padStart(2, "0")}Z`}],
  }));
}

/** The logical history every provider must expose, read from frozen V1 rows. */
async function logicalHistory(store: SqliteAuthorityStore): Promise<string[]> {
  const trace: string[] = [];
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return trace;
  trace.push(`head:${head.cursor}:${head.provider_revision}`);
  trace.push(canonicalAuthorityBytes(head.head).toString("utf8"));
  let after: string | null = null;
  for (;;) {
    const page = await store.scanCommitted(after, 6);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") return trace;
    if (page.transactions.length === 0) break;
    for (const transaction of page.transactions) {
      const receipt = await store.readReceipt(transaction.operation_id);
      assert.equal(receipt.status, "found", JSON.stringify(receipt));
      trace.push(JSON.stringify({cursor: transaction.cursor,
        provider_revision: transaction.provider_revision,
        operation_id: transaction.operation_id,
        projection: canonicalAuthorityBytes(transaction.projection).toString("utf8"),
        events: transaction.events, receipts: transaction.receipts,
        receipt_readback: receipt.status === "found" ? receipt.receipts : null}));
    }
    after = page.transactions[page.transactions.length - 1]!.cursor;
  }
  return trace;
}

test("SQLite V1 migration plans without writing and reports a missing database", async t => {
  const root = await directory(t);
  const store = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const before = sqliteAuthorityV1Digest(store.path, GOAL_ID);
  const planned = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID);
  assert.equal(planned.status, "planned", JSON.stringify(planned));
  assert.equal(planned.commits, HISTORY_LENGTH);
  assert.equal(planned.identity, store.identity);
  assert.equal(sqliteAuthorityV1Digest(store.path, GOAL_ID), before);
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(root, "other-goal").status, "missing");
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {expectedIdentity: `sqlite:${"a".repeat(32)}`})
    .reason_code, "migration_protocol_violation");
  assert.equal(sqliteAuthorityV1Digest(store.path, GOAL_ID), before);
});

test("SQLite V1 migration preserves the exact retained history", {timeout: 60000}, async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const before = sqliteAuthorityV1Digest(v1.path, GOAL_ID);
  const migrated = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(migrated.status, "migrated", JSON.stringify(migrated));
  assert.equal(migrated.commits, HISTORY_LENGTH);
  assert.equal(migrated.checkpoints, Math.ceil(HISTORY_LENGTH / 64));
  assert.equal(migrated.identity, v1.identity);
  assert.notEqual(sqliteAuthorityV1Digest(v1.path, GOAL_ID), before);
  const store = new SqliteAuthorityStore(root, GOAL_ID);
  const history = await logicalHistory(store);
  // Cursor, operation identity, provider revision, projection, events and
  // receipts are byte-identical to the frozen V1 rows.
  for (const [index, row] of v1.rows.entries()) {
    const entry = history[2 + index]!;
    assert.ok(entry !== undefined, `missing migrated history entry ${index}`);
    const parsed = JSON.parse(entry) as Record<string, unknown>;
    assert.equal(parsed.cursor, row.cursor);
    assert.equal(parsed.operation_id, row.operation_id);
    assert.equal(parsed.provider_revision, `${v1.identity}:${row.cursor}`);
    assert.deepEqual(parsed.projection, JSON.stringify(row.operation_receipt.projection));
    assert.deepEqual(parsed.receipts, row.operation_receipt.receipts);
    assert.deepEqual(parsed.receipt_readback, row.operation_receipt.receipts);
  }
  const stored = new SqliteAuthorityStore(root, GOAL_ID);
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(stored.path, {readOnly: true});
  try {
    // Order by the integer column: the CAST alias would sort cursors as text.
    const digests = (db.prepare("SELECT commit_digest FROM commits ORDER BY commits.cursor")
      .all() as unknown as {cursor: string; commit_digest: string}[]);
    assert.deepEqual(digests.map(row => row.commit_digest), v1.rows.map(row => row.commit_digest));
    assert.equal(String(db.prepare("PRAGMA user_version").get()?.user_version), "2");
    assert.equal(db.prepare("SELECT COUNT(*) AS count FROM checkpoints").get()?.count, migrated.checkpoints);
  } finally { db.close(); }
  const audit = await store.verifyAuthorityHistory();
  assert.equal(audit.status, "verified", JSON.stringify(audit));
  const again = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(again.status, "already_current", JSON.stringify(again));
  assert.deepEqual(await logicalHistory(store), history);
});

function assertFrozenV1(path: string, expectedDigest: string): void {
  const reader = new (createRequire(import.meta.url)("node:sqlite").DatabaseSync)(path, {readOnly: true});
  try {
    assert.equal(String(reader.prepare("PRAGMA user_version").get()?.user_version), "1");
    assert.equal(reader.prepare("SELECT schema_version FROM metadata WHERE singleton = 1")
      .get()?.schema_version, "loopx_sqlite_authority_store_v0");
    assert.equal(reader.prepare("SELECT COUNT(*) AS count FROM commits").get()?.count, HISTORY_LENGTH);
    assert.equal(reader.prepare("SELECT name FROM sqlite_master WHERE name = 'checkpoints'").get(), undefined);
    assert.equal(reader.prepare("SELECT COUNT(*) AS count FROM head").get()?.count, 1);
  } finally { reader.close(); }
  assert.equal(sqliteAuthorityV1Digest(path, GOAL_ID), expectedDigest);
}

test("SQLite V1 migration refuses a busy writer without changing retained history and retries", {timeout: 30000}, async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const before = sqliteAuthorityV1Digest(v1.path, GOAL_ID);
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const writer = new DatabaseSync(v1.path);
  writer.exec("BEGIN IMMEDIATE");
  try {
    const blocked = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
    assert.equal(blocked.status, "failed", JSON.stringify(blocked));
    assert.equal(blocked.reason_code, "migration_transaction_failed");
    assertFrozenV1(v1.path, before);
  } finally {
    writer.exec("ROLLBACK");
    writer.close();
  }
  const migrated = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(migrated.status, "migrated", JSON.stringify(migrated));
  assert.equal(migrated.identity, v1.identity);
  const store = new SqliteAuthorityStore(root, GOAL_ID);
  const history = await logicalHistory(store);
  for (const [index, row] of v1.rows.entries()) {
    const retained = JSON.parse(history[2 + index]!);
    assert.equal(retained.operation_id, row.operation_id);
    assert.equal(retained.projection, JSON.stringify(row.operation_receipt.projection));
    assert.deepEqual(retained.receipts, row.operation_receipt.receipts);
  }
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true}).status, "already_current");
  assert.deepEqual(await logicalHistory(store), history);
});

test("SQLite V1 migration fails closed when its swap target already exists", async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const db = new DatabaseSync(v1.path);
  db.exec("CREATE TABLE commits_v2 (cursor INTEGER PRIMARY KEY)");
  db.close();
  const before = sqliteAuthorityV1Digest(v1.path, GOAL_ID);
  const blocked = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(blocked.status, "failed", JSON.stringify(blocked));
  assert.equal(blocked.reason_code, "migration_transaction_failed");
  const reader = new DatabaseSync(v1.path, {readOnly: true});
  try {
    assert.equal(String(reader.prepare("PRAGMA user_version").get()?.user_version), "1");
    assert.equal(reader.prepare("SELECT COUNT(*) AS count FROM commits").get()?.count, HISTORY_LENGTH);
    assert.equal(reader.prepare("SELECT name FROM sqlite_master WHERE name = 'checkpoints'").get(), undefined);
    assert.equal(reader.prepare("SELECT name FROM sqlite_master WHERE name = 'head_v2'").get(), undefined);
    assert.equal(reader.prepare("SELECT COUNT(*) AS count FROM commits_v2").get()?.count, 0);
  } finally { reader.close(); }
  assert.equal(sqliteAuthorityV1Digest(v1.path, GOAL_ID), before);
});

test("SQLite V1 migration runs from its operator entry point", {timeout: 60000}, async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const planned = runMigrationCli(["--directory", root, "--goal-id", GOAL_ID]);
  assert.equal(planned.status, 0, planned.stderr);
  const plan = JSON.parse(planned.stdout) as Record<string, unknown>;
  assert.equal(plan.status, "planned");
  assert.equal(plan.commits, HISTORY_LENGTH);
  assert.equal(plan.identity, v1.identity);
  const executed = runMigrationCli(["--directory", root, "--goal-id", GOAL_ID, "--execute",
    "--expected-identity", v1.identity, "--format", "markdown"]);
  assert.equal(executed.status, 0, executed.stderr);
  assert.match(executed.stdout, /status: migrated/u);
  assert.match(executed.stdout, new RegExp(`commits: ${HISTORY_LENGTH}`, "u"));
  const reopened = new SqliteAuthorityStore(root, GOAL_ID, {existingOnly: true, expectedIdentity: v1.identity});
  const head = await reopened.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") assert.equal(head.cursor, String(HISTORY_LENGTH));
  assert.equal((await reopened.verifyAuthorityHistory()).status, "verified");
});

test("SQLite V1 migration CLI refuses an unnamed incarnation", async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const before = sqliteAuthorityV1Digest(v1.path, GOAL_ID);
  const refused = runMigrationCli(["--directory", root, "--goal-id", GOAL_ID, "--execute",
    "--expected-identity", `sqlite:${"b".repeat(32)}`]);
  assert.equal(refused.status, 1, refused.stdout);
  assert.equal((JSON.parse(refused.stdout) as Record<string, unknown>).reason_code,
    "migration_protocol_violation");
  assertFrozenV1(v1.path, before);
});

test("SQLite V1 migration carries an empty database forward without inventing a head", async t => {
  const root = await directory(t);
  const v1 = createEmptySqliteAuthorityStoreV1(root, GOAL_ID);
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID).status, "planned");
  const migrated = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(migrated.status, "migrated", JSON.stringify(migrated));
  assert.equal(migrated.commits, 0);
  assert.equal(migrated.checkpoints, 0);
  assert.equal(migrated.identity, v1.identity);
  const store = new SqliteAuthorityStore(root, GOAL_ID, {existingOnly: true, expectedIdentity: v1.identity});
  assert.deepEqual(await store.loadAuthority(), {status: "missing"});
  const emptyPage = await store.scanCommitted(null, 4);
  assert.equal(emptyPage.status, "page");
  if (emptyPage.status === "page") {
    assert.deepEqual(emptyPage.transactions, []);
    assert.equal(emptyPage.has_more, false);
    assert.equal(emptyPage.next_cursor, null);
  }
  // An empty authority database still accepts its first commit.
  const receipt = await store.commitAuthority({expected_provider_revision: null, operation_id: "empty-op-001",
    next_projection: {authority_revision: 1, todos: []}, events: [], receipts: []});
  assert.equal(receipt.status, "applied", JSON.stringify(receipt));
  assert.equal(receipt.status === "applied" ? receipt.cursor : null, "1");
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");
  assert.deepEqual(await store.loadAuthority(), {status: "loaded",
    cursor: "1", provider_revision: `${v1.identity}:1`, head: {authority_revision: 1, todos: []}});
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true}).status, "already_current");
});

function runMigrationCli(args: readonly string[]): {status: number | null; stdout: string; stderr: string} {
  const script = fileURLToPath(new URL("../../examples/coordination/sqlite-authority-migration.ts",
    import.meta.url));
  return spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite",
    "--experimental-strip-types", script, ...args], {encoding: "utf8"});
}

/**
 * Retained V1 history that used only JSON object keys V1 never restricted.
 *
 * V1 stored a whole projection per commit, so `""` was an ordinary key and
 * `__proto__` was data. The migration has to carry both into the new format and
 * prove they still read back before it commits.
 */
function legacyKeySeeds(): SqliteAuthorityV1Seed[] {
  return [
    {operation_id: "legacy-op-001",
      projection: {authority_revision: 1, "": {empty: true}, ["__proto__"]: {own: 1}, todos: []}},
    {operation_id: "legacy-op-002",
      projection: {authority_revision: 2, "": {empty: true}, ["__proto__"]: {own: 1},
        nested: {["__proto__"]: {deep: true}}, todos: [{todo_id: "todo-0", status: "open"}]}},
    {operation_id: "legacy-op-003",
      projection: {authority_revision: 3, "": {empty: false}, nested: {},
        todos: [{todo_id: "todo-0", status: "done"}]}},
  ];
}

test("SQLite V1 migration keeps every legacy JSON key readable", async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, legacyKeySeeds());
  const {DatabaseSync: Reader} = createRequire(import.meta.url)("node:sqlite");
  const legacy = new Reader(v1.path, {readOnly: true});
  try {
    // The frozen V1 rows really carry the keys this test is about.
    const text = String(legacy.prepare("SELECT projection FROM commits WHERE cursor = 2").get()?.projection);
    assert.match(text, /"__proto__"/u);
    assert.match(text, /""\s*:/u);
  } finally { legacy.close(); }
  const migrated = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(migrated.status, "migrated", JSON.stringify(migrated));
  assert.equal(migrated.commits, 3);
  const store = new SqliteAuthorityStore(root, GOAL_ID, {existingOnly: true, expectedIdentity: v1.identity});
  // The live head reads back as the exact projection the last V1 commit published.
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded", JSON.stringify(head));
  if (head.status === "loaded") {
    assert.equal(canonicalAuthorityBytes(head.head).toString("utf8"),
      JSON.stringify(v1.rows[2]!.operation_receipt.projection));
    // The empty key survives the migration, and the last commit really did
    // drop `__proto__` rather than the codec silently dropping it for us.
    assert.equal(Object.hasOwn(head.head, ""), true);
    assert.equal(Object.hasOwn(head.head, "__proto__"), false);
    assert.equal(Object.getPrototypeOf(head.head), Object.prototype);
  }
  // A retained projection keeps the legacy `__proto__` key as stored data.
  const page = await store.scanCommitted(null, 6);
  assert.equal(page.status, "page", JSON.stringify(page));
  if (page.status === "page") {
    const retained = page.transactions[1]!.projection;
    assert.equal(Object.hasOwn(retained, "__proto__"), true);
    assert.equal(Object.hasOwn(retained, ""), true);
    assert.equal(Object.getPrototypeOf(retained), Object.prototype);
    assert.deepEqual(retained["__proto__"], {own: 1});
  }
  // Every retained projection reads back byte-identically, not only the head.
  const history = await logicalHistory(store);
  for (const [index, row] of v1.rows.entries()) {
    const parsed = JSON.parse(history[2 + index]!) as Record<string, unknown>;
    assert.equal(parsed.cursor, row.cursor);
    assert.equal(parsed.operation_id, row.operation_id);
    assert.deepEqual(parsed.projection, JSON.stringify(row.operation_receipt.projection));
  }
  assert.equal((await store.verifyAuthorityHistory()).status, "verified");
});

test("SQLite V1 migration refuses a rewritten proof and leaves the database intact", async t => {
  const root = await directory(t);
  const v1 = createSqliteAuthorityStoreV1(root, GOAL_ID, seeds());
  const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
  const forged = new DatabaseSync(v1.path);
  forged.prepare("UPDATE commits SET commit_digest=? WHERE cursor=?").run("f".repeat(64), HISTORY_LENGTH);
  forged.close();
  const before = sqliteAuthorityV1Digest(v1.path, GOAL_ID);
  const rejected = migrateSqliteAuthorityStoreV1ToV2(root, GOAL_ID, {execute: true});
  assert.equal(rejected.status, "failed", JSON.stringify(rejected));
  assert.equal(rejected.reason_code, "migration_protocol_violation");
  assert.equal(rejected.commits, undefined);
  assertFrozenV1(v1.path, before);
  const finalReader = new DatabaseSync(v1.path, {readOnly: true});
  try {
    assert.equal(finalReader.prepare("SELECT commit_digest FROM commits WHERE cursor=?")
      .get(HISTORY_LENGTH)?.commit_digest, "f".repeat(64));
  } finally { finalReader.close(); }
});
