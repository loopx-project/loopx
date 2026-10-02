import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readFile, rm, stat, symlink, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {sqliteAuthorityRuntime} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {snapshotSqliteBackup, snapshotSqliteDatabase} from "../../loopx/control_plane/coordination/sqlite_backup.ts";

async function root(t: test.TestContext) {
  const path = await mkdtemp(join(tmpdir(), "sqlite-backup-"));
  t.after(() => rm(path, {recursive: true, force: true}));
  return path;
}

test("online backup preserves committed metadata, schema and blobs, excluding an uncommitted writer", async t => {
  const directory = await root(t), source = join(directory, "source"), target = join(directory, "snapshot");
  const {driver} = sqliteAuthorityRuntime();
  const writer = new driver.DatabaseSync(source);
  try {
    writer.exec("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0; PRAGMA user_version=17; PRAGMA application_id=42;");
    writer.exec("CREATE TABLE facts(id INTEGER PRIMARY KEY, metadata TEXT, raw BLOB)");
    const metadata = JSON.stringify({unknown: {nullable: null, enabled: false}, history: ["原始回执".repeat(4096)]});
    const raw = new Uint8Array([0, 1, 255]);
    writer.prepare("INSERT INTO facts VALUES(1,?,?)").run(metadata, raw);
    writer.exec("BEGIN IMMEDIATE; UPDATE facts SET metadata='not committed'");
    const info = await snapshotSqliteDatabase(source, target);
    assert.equal(info.synchronous_statement_finalization, true);
    const copy = new driver.DatabaseSync(target, {readOnly: true});
    try {
      assert.deepEqual({...copy.prepare("SELECT * FROM facts").get()}, {id: 1, metadata, raw});
      assert.equal(copy.prepare("PRAGMA user_version").get()?.user_version, 17);
      assert.equal(copy.prepare("PRAGMA application_id").get()?.application_id, 42);
      assert.equal(copy.prepare("PRAGMA journal_mode").get()?.journal_mode, "delete");
    } finally { copy.close(); }
    writer.exec("ROLLBACK");
    assert.equal(writer.prepare("SELECT metadata FROM facts").get()?.metadata, metadata);
    assert.equal((await stat(target)).mode & 0o777, 0o600);
  } finally { writer.close(); }
});

test("snapshot rejects existing destinations and source aliases without modifying their bytes", async t => {
  const directory = await root(t), source = join(directory, "source"), target = join(directory, "existing");
  const {driver} = sqliteAuthorityRuntime();
  const db = new driver.DatabaseSync(source); db.exec("CREATE TABLE facts(value TEXT)"); db.close();
  const original = await readFile(source);
  await writeFile(target, "keep existing");
  await assert.rejects(snapshotSqliteDatabase(source, target), /EEXIST/);
  assert.equal(await readFile(target, "utf8"), "keep existing");
  await assert.rejects(snapshotSqliteDatabase(source, source), /distinct absolute/);
  const alias = join(directory, "alias"); await symlink(source, alias);
  await assert.rejects(snapshotSqliteDatabase(source, alias), /EEXIST/);
  assert.deepEqual(await readFile(source), original);
});

test("invalid requests and unreadable databases cannot create a successful snapshot", async t => {
  const directory = await root(t), target = join(directory, "snapshot");
  await assert.rejects(snapshotSqliteBackup({source_path: [], destination_path: target}), /non-empty string/);
  await assert.rejects(snapshotSqliteDatabase("relative", target), /absolute/);
  const source = join(directory, "corrupt");
  await writeFile(source, Buffer.concat([Buffer.from("SQLite format 3\0"), Buffer.alloc(512, 255)]));
  await assert.rejects(snapshotSqliteDatabase(source, target));
  await assert.rejects(stat(target), /ENOENT/);
});
