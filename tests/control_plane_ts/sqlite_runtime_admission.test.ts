import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import test from "node:test";
import {spawnSync} from "node:child_process";
import {hasSqliteWalResetFix, sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import { SqliteAuthorityStore } from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";

test("SQLite WAL admission follows the fixed upstream release lines", () => {
  // Independent boundary cases from sqlite.org/wal.html#the_wal_reset_bug.
  for (const version of ["3.44.6", "3.44.7", "3.50.7", "3.50.8", "3.51.3", "3.51.4", "3.52.0"]) {
    assert.equal(hasSqliteWalResetFix(version), true, version);
  }
  for (const version of ["3.7.0", "3.44.5", "3.45.0", "3.47.2", "3.49.99", "3.50.6", "3.51.2",
    "4.0.0", "3.51.3-vendor", "3.051.3", "3.51", "3.51.3.0", "", "invalid"]) {
    assert.equal(hasSqliteWalResetFix(version), false, version);
  }
});

async function rejectVulnerableDriver(servingManagedRuntime: boolean): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "sqlite-version-admission-"));
  const source = new URL("../../loopx/control_plane/coordination/sqlite_authority_store.ts", import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import Module,{createRequire} from 'node:module';
    import {existsSync} from 'node:fs';
    const require=createRequire(import.meta.url), sqlite=require('node:sqlite'), original=Module._load;
    class VulnerableVersion extends sqlite.DatabaseSync {
      prepare(sql) {
        if(sql.includes('sqlite_version()'))return {get:()=>({version:'3.51.2',source_id:'synthetic-vulnerable-driver'})};
        return super.prepare(sql);
      }
    }
    Module._load=function(id,...args){return id==='node:sqlite'?{...sqlite,DatabaseSync:VulnerableVersion}:original.call(this,id,...args)};
    const {SqliteAuthorityStore}=await import(${JSON.stringify(source)});
    const target=process.argv[1]+'/authority', store=new SqliteAuthorityStore(target,'goal');
    const reasons=[];
    for(const result of [await store.storeIdentity(),await store.commitAuthority({operation_id:'must-reject',expected_provider_revision:null,next_projection:{},events:[],receipts:[]})]) {
      assert.equal(result.status,'failed');assert.match(result.reason,/SQLite 3\\.51\\.2/);
      reasons.push(result.reason);
    }
    assert.equal(existsSync(target),false);
    process.stdout.write(JSON.stringify(reasons));
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
    "--input-type=module", "-e", script, root], {
    encoding: "utf8", timeout: 15000,
    env: servingManagedRuntime
      ? {...process.env, LOOPX_EFFECT_RUNTIME_TOKEN: "smoke-runtime-token"}
      : process.env,
  });
  await rm(root, {recursive: true, force: true});
  assert.equal(child.status, 0, child.stderr);
  const reasons: string[] = JSON.parse(child.stdout);
  assert.equal(reasons.length, 2);
  return reasons[0];
}

test("a vulnerable SQLite version is rejected before authority paths are created", async () => {
  const reason = await rejectVulnerableDriver(true);
  // The refusal must name the repair path for a reused managed runtime,
  // because installing a qualified Node alone does not replace it.
  assert.match(reason, /managed Effect runtime \(pid \d+/);
  assert.match(reason, /doctor --restart-runtime/);
});

test("a direct CLI run is told to rerun on a qualified Node instead", async () => {
  const reason = await rejectVulnerableDriver(false);
  assert.match(reason, /Rerun this command on a PATH whose Node is the qualified runtime/);
  assert.doesNotMatch(reason, /restart-runtime/);
});

test("runtime identity publishes the serving pair and enforces required qualification", () => {
  const identity = sqliteRuntimeIdentity();
  assert.equal(identity.schema_version, "loopx_sqlite_runtime_identity_v0");
  assert.equal(identity.node_version, process.version);
  assert.equal(typeof identity.sqlite_available, "boolean");
  assert.equal(typeof identity.sqlite_authority_qualified, "boolean");
  if (process.env.LOOPX_TEST_REQUIRE_SQLITE_QUALIFIED === "1") {
    assert.equal(identity.sqlite_authority_qualified, true,
      `qualified SQLite test lane cannot skip provider cases: ${identity.node_version}/${identity.sqlite_version}`);
  }
  if (!identity.sqlite_available) {
    assert.equal(identity.sqlite_version, null);
    assert.equal(identity.sqlite_authority_qualified, false);
    assert.equal(typeof identity.unavailable_reason, "string");
    return;
  }
  assert.equal(identity.unavailable_reason, null);
  assert.equal(
    identity.sqlite_authority_qualified,
    hasSqliteWalResetFix(String(identity.sqlite_version)) &&
      identity.synchronous_statement_finalization === true,
    `identity ${identity.sqlite_version}/${identity.synchronous_statement_finalization}`,
  );
});

test("default File authority never loads the optional SQLite driver", async t => {
  const root = await mkdtemp(join(tmpdir(), "file-without-sqlite-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const source = new URL("../../loopx/control_plane/coordination/file_authority_store.ts", import.meta.url).href;
  const sqliteSource = new URL("../../loopx/control_plane/coordination/sqlite_authority_store.ts", import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';import Module from 'node:module';
    const original=Module._load;
    Module._load=function(id,...args){if(id==='node:sqlite')throw Error('optional driver unavailable');return original.call(this,id,...args)};
    await import(${JSON.stringify(sqliteSource)});
    const {FileAuthorityStore}=await import(${JSON.stringify(source)});
    const store=new FileAuthorityStore(process.argv[1],'goal');
    const result=await store.commitAuthority({operation_id:'file-default',expected_provider_revision:null,next_projection:{goal_id:'goal'},events:[],receipts:[]});
    assert.equal(result.status,'applied');assert.equal((await store.loadAuthority()).status,'loaded');
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types", "--input-type=module",
    "-e", script, root], {encoding: "utf8", timeout: 15000});
  assert.equal(child.status, 0, child.stderr);
});

test("unqualified SQLite runtime fails before creating authority files", async t => {
  const directory = await mkdtemp(join(tmpdir(), "sqlite-admission-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  let supportsFinalization = false;
  let fixedWal = false;
  try {
    const {DatabaseSync} = createRequire(import.meta.url)("node:sqlite");
    const db = new DatabaseSync(":memory:");
    fixedWal = hasSqliteWalResetFix(String(db.prepare("SELECT sqlite_version() AS version").get()?.version));
    const statement = db.prepare("SELECT 1");
    db.close();
    try { statement.get(); }
    catch (error) { supportsFinalization = (error as NodeJS.ErrnoException).code === "ERR_INVALID_STATE"; }
  } catch { /* The optional native module may be unavailable on the core runtime. */ }
  const target = join(directory, "authority");
  const store = new SqliteAuthorityStore(target, "goal");
  const identity = await store.storeIdentity();
  if (supportsFinalization && fixedWal) {
    assert.equal(identity.status, "available");
    // All statements must have been finalized at the public method boundary.
    await rm(target, {recursive: true});
  } else {
    assert.equal(identity.status, "failed");
    if (identity.status === "failed") assert.match(identity.reason, /Node 22\.22\.3/);
    assert.equal(existsSync(target), false);
    assert.equal((await store.commitAuthority({operation_id: "unsupported", expected_provider_revision: null,
      events: [], receipts: [], next_projection: {}})).status, "failed");
    assert.equal(existsSync(target), false);
  }
});
