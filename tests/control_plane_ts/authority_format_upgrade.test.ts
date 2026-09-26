import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readFile, rm, writeFile, mkdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {upgradeAuthorityFormats} from "../../loopx/control_plane/coordination/authority_format_upgrade.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {migrateSqliteAuthorityStoreV1ToV2} from "../../loopx/control_plane/coordination/sqlite_authority_migration.ts";
import {createSqliteAuthorityStoreV1} from "./sqlite_authority_v1_fixture.ts";
import {authorityStoreCommitFixture as commit} from "./authority_store_conformance.ts";
import {exportAuthorityArchive, restoreAuthorityArchive, verifyAuthorityArchive} from
  "../../loopx/control_plane/coordination/authority_archive.ts";

async function root(t: test.TestContext) {
  const value = await mkdtemp(join(tmpdir(), "authority-upgrade-"));
  t.after(() => rm(value, {recursive: true, force: true})); return value;
}

test("runtime upgrade discovers old File and SQLite, backs up and preserves history across provider restore", async t => {
  const runtime = await root(t), goal = "upgrade-goal";
  const file = new FileAuthorityStore(join(runtime, "authority", "file-v0"), goal);
  const c = {...commit(null, "original-operation", 1, 1), next_projection: {goal_id: goal, value: 1}};
  assert.equal((await file.commitAuthority(c)).status, "applied");
  const history = await file.scanCommitted(null, 10);
  assert.equal(history.status, "page"); if (history.status !== "page") return;
  const raw = JSON.parse(await readFile(file.path, "utf8"));
  await writeFile(file.path, JSON.stringify({...raw, schema_version: "loopx_file_authority_store_v0", committed: history.transactions}));
  const source = await readFile(file.path);
  const sqliteDir = join(runtime, "authority", "sqlite-v0");
  createSqliteAuthorityStoreV1(sqliteDir, goal, [{operation_id: c.operation_id, projection: c.next_projection, receipts: c.receipts}]);
  const planned = await upgradeAuthorityFormats([runtime], false);
  assert.equal(planned.status, "planned");
  assert.deepEqual((planned.results as any[]).map(row => row.status), ["planned", "planned"]);
  assert.deepEqual(await readFile(file.path), source);
  const upgraded = await upgradeAuthorityFormats([runtime], true);
  assert.equal(upgraded.status, "upgraded", JSON.stringify(upgraded));
  const rows = upgraded.results as any[];
  assert.deepEqual(rows.map(row => row.status), ["migrated", "migrated"]);
  assert.deepEqual(await readFile(join(rows[0].backup_directory, "source.json")), source);
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(rows[1].backup_directory, goal).status, "planned");
  assert.deepEqual((await file.scanCommitted(null, 10)), history);
  const sqlite = new SqliteAuthorityStore(sqliteDir, goal);
  assert.equal((await sqlite.readReceipt(c.operation_id)).status, "found");
  const again = await upgradeAuthorityFormats([runtime], true);
  assert.deepEqual((again.results as any[]).map(row => row.status), ["already_current", "already_current"]);
  // One logical interchange protocol, not a growing set of pairwise converters.
  for (const [label, store] of [["file", file], ["sqlite", sqlite]] as const) {
    const archive = join(runtime, `${label}.ndjson`);
    await exportAuthorityArchive(store, goal, archive);
    const inspected = await verifyAuthorityArchive(archive);
    for (const provider of ["file", "sqlite"] as const) {
      const directory = join(runtime, `restore-${label}-${provider}`);
      await mkdir(directory);
      const target = provider === "file" ? new FileAuthorityStore(directory, goal) : new SqliteAuthorityStore(directory, goal);
      await restoreAuthorityArchive(archive, target, inspected.archive_sha256);
      const receipt = await target.readReceipt(c.operation_id);
      assert.equal(receipt.status, "found");
      if (receipt.status === "found") assert.deepEqual(receipt.receipts, c.receipts);
      const head = await target.loadAuthority();
      assert.equal(head.status, "loaded"); if (head.status === "loaded") assert.deepEqual(head.head, c.next_projection);
    }
  }
});

test("backup digest fence rejects changed SQLite source before adoption", async t => {
  const directory = await root(t), goal = "fenced-goal";
  createSqliteAuthorityStoreV1(directory, goal, [{operation_id: "one", projection: {value: 1}}]);
  const result = migrateSqliteAuthorityStoreV1ToV2(directory, goal,
    {execute: true, expectedSequenceDigest: "0".repeat(64)});
  assert.equal(result.status, "failed");
  assert.match(result.reason!, /changed after its verified backup/);
  assert.equal(migrateSqliteAuthorityStoreV1ToV2(directory, goal).status, "planned");
});

test("unsupported formats fail closed and do not bootstrap fallback stores", async t => {
  const runtime = await root(t), goal = "unknown-format";
  const store = new FileAuthorityStore(join(runtime, "authority", "file-v0"), goal);
  await store.commitAuthority(commit(null, "seed", 1, 1));
  const raw = JSON.parse(await readFile(store.path, "utf8")); raw.schema_version = "future_format";
  const bytes = JSON.stringify(raw); await writeFile(store.path, bytes);
  const result = await upgradeAuthorityFormats([runtime], true);
  assert.equal(result.status, "failed");
  assert.equal(await readFile(store.path, "utf8"), bytes);
});
