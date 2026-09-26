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

test("upgrade discovers an archived File lineage even when no active document remains", async t => {
  const runtime = await root(t), goal = "archived-format";
  const store = new FileAuthorityStore(join(runtime, "authority", "file-v0"), goal);
  const committed = await store.commitAuthority({...commit(null, "seed", 1, 1), next_projection: {goal_id: goal}});
  assert.equal(committed.status, "applied"); if (committed.status !== "applied") return;
  const logical = await store.scanCommitted(null, 1);
  assert.equal(logical.status, "page"); if (logical.status !== "page") return;
  assert.equal((await store.archiveAuthorityDocument(committed.provider_revision, "retire")).status, "applied");
  const archived = store.authorityArchivePath("retire");
  const current = JSON.parse(await readFile(archived, "utf8"));
  const old = JSON.stringify({...current, schema_version: "loopx_file_authority_store_v0", committed: logical.transactions});
  await writeFile(archived, old);
  const result = await upgradeAuthorityFormats([runtime], true);
  assert.equal(result.status, "upgraded", JSON.stringify(result));
  assert.equal((result.results as any[]).length, 1);
  assert.equal((await store.archiveAuthorityDocument(committed.provider_revision, "retire")).status, "replayed");
  assert.equal((await store.loadAuthority()).status, "missing");
});

test("content inspection separates store, archive, backup and selector regardless of extension", async t => {
  const {inspectAuthorityFormat} = await import("../../loopx/control_plane/coordination/authority_format_inspection.ts");
  const runtime = await root(t), goal = "inspect-goal";
  const file = new FileAuthorityStore(join(runtime, "authority", "file-v0"), goal);
  await file.commitAuthority({...commit(null, "seed", 1, 1), next_projection: {goal_id: goal}});
  const misleading = join(runtime, "store.sqlite");
  await writeFile(misleading, await readFile(file.path));
  const identified = await inspectAuthorityFormat(misleading);
  assert.equal(identified.artifact_kind, "authority_store");
  assert.equal(identified.provider, "file");
  assert.equal(identified.verification, "metadata_only");
  const archive = join(runtime, "archive.json");
  await exportAuthorityArchive(file, goal, archive);
  assert.equal((await inspectAuthorityFormat(archive)).artifact_kind, "logical_archive");
  const raw = JSON.parse(await readFile(file.path, "utf8"));
  const row = raw.committed[0]; row.projection = row.state.projection; delete row.state;
  raw.schema_version = "loopx_file_authority_store_v0";
  await writeFile(file.path, JSON.stringify(raw));
  const upgraded = await upgradeAuthorityFormats([runtime], true);
  const backup = (upgraded.results as any[])[0].backup_directory;
  assert.equal((await inspectAuthorityFormat(backup)).artifact_kind, "format_backup");
  await writeFile(join(backup, "source.json"), "corrupted");
  await assert.rejects(inspectAuthorityFormat(backup), /digest mismatch/);
  const selector = join(runtime, "selector.json");
  await writeFile(selector, JSON.stringify({schema_version: "loopx_local_authority_provider_v0",
    provider: "postgresql", goal_id: goal, tenant_id: "tenant-a", store_identity: "postgresql:synthetic"}));
  const selected = await inspectAuthorityFormat(selector);
  assert.equal(selected.artifact_kind, "provider_selector");
  assert.equal(selected.verification, "metadata_only");
  await writeFile(misleading, JSON.stringify({schema_version: "loopx_file_authority_store_v999"}));
  assert.equal((await inspectAuthorityFormat(misleading)).status, "unsupported");
});

test("SQLite recognition rejects conflicting version markers instead of claiming already current", async t => {
  const {inspectAuthorityFormat} = await import("../../loopx/control_plane/coordination/authority_format_inspection.ts");
  const {sqliteAuthorityRuntime} = await import("../../loopx/control_plane/coordination/sqlite_runtime.ts");
  const directory = await root(t), goal = "version-mismatch";
  const source = createSqliteAuthorityStoreV1(directory, goal, [{operation_id: "seed", projection: {goal_id: goal}}]);
  const before = await inspectAuthorityFormat(source.path);
  assert.equal(before.provider, "sqlite"); assert.equal(before.upgrade_required, true);
  const db = new (sqliteAuthorityRuntime().driver.DatabaseSync)(source.path);
  try { db.exec("PRAGMA user_version = 2"); } finally { db.close(); }
  assert.equal((await inspectAuthorityFormat(source.path)).status, "unsupported");
  const result = migrateSqliteAuthorityStoreV1ToV2(directory, goal, {execute: true});
  assert.equal(result.status, "failed"); assert.match(result.reason!, /disagree/);
});
