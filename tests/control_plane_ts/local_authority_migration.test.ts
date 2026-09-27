import assert from "node:assert/strict";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {durableWriteJson} from "../../loopx/control_plane/effect_runtime_io.ts";
import {manageLocalAuthorityArchive as manage} from "../../loopx/control_plane/coordination/local_authority_archive.ts";
import {openLocalAuthorityStoreHandle as selected, localAuthorityProviderPaths} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {legacyCoordinationWriterFencePath, LEGACY_COORDINATION_WRITER_FENCE_SCHEMA} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {withCanonicalWriter} from "../../loopx/control_plane/coordination/local_authority_write.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {inspectAuthorityFormat} from "../../loopx/control_plane/coordination/authority_format_inspection.ts";

const goal = "migration-goal";
const request = (root: string, fields: JsonObject) => ({schema_version: "loopx_authority_archive_admin_request_v0", runtime_root: root, goal_id: goal, ...fields});
async function append(root: string, i: number, leases: JsonObject[] = []) {
  return withCanonicalWriter(root, goal, false, async () => {
    const {store} = await selected(root, goal);
    const head = await store.loadAuthority();
    const projection = authorityProjectionFixture(goal, [{todo_id: "todo_a", role: "agent", status: "open", done: false,
      text: `Independent observation ${i}`, archive_state: "active"}], leases, "native", {generation: i});
    const result = await store.commitAuthority({operation_id: `op-${i}`, expected_provider_revision: head.status === "loaded" ? head.provider_revision : null,
      next_projection: projection, events: [{kind: "observation", sequence: i}], receipts: [{accepted: true, sequence: i}]});
    assert.equal(result.status, "applied", JSON.stringify(result));
    return result;
  });
}
async function fixture(t: {after: (f: () => Promise<void>) => void}) {
  const root = await mkdtemp(join(tmpdir(), "local-migration-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await append(root, 1); await append(root, 2);
  await durableWriteJson(legacyCoordinationWriterFencePath(root, goal), {
    schema_version: LEGACY_COORDINATION_WRITER_FENCE_SCHEMA, state: "engaged", goal_id: goal,
    fence_id: "fixture-fence", source_version: "fixture-source", source_projection_sha256: "a".repeat(64),
    expected_shadow_provider_revision: "fixture-revision"});
  return root;
}
async function plan(root: string, target: "file" | "sqlite", name = target) {
  const path = join(root, `${name}-plan.json`);
  const result = await manage(request(root, {action: "plan-migration", provider: target, plan: path}));
  assert.equal(result.status, "planned", JSON.stringify(result));
  return {action: "migrate", plan: path, plan_sha256: result.plan_sha256, execute: true};
}

test("real providers: File → SQLite → File preserves history, receipts and later writes", async t => {
  const root = await fixture(t);
  const original = await (await selected(root, goal)).store.scanCommitted(null, 10);
  const first = await plan(root, "sqlite");
  const preview = await manage(request(root, {...first, execute: false}));
  assert.equal(preview.status, "planned");
  assert.equal((await selected(root, goal)).provider, "file");
  assert.equal((await manage(request(root, first))).status, "migrated");
  assert.equal((await selected(root, goal)).provider, "sqlite");
  await append(root, 3);
  const resumed = await manage(request(root, first));
  assert.equal(resumed.status, "already_applied", JSON.stringify(resumed));
  assert.equal((resumed.audit as JsonObject).captured_target_cursor, "3");
  const back = await plan(root, "file");
  assert.equal((await manage(request(root, back))).status, "migrated");
  const active = await selected(root, goal);
  assert.equal(active.provider, "file");
  assert.equal((await inspectAuthorityFormat(localAuthorityProviderPaths(root, goal).marker)).provider, "file");
  const copy = await active.store.scanCommitted(null, 10);
  assert.equal(copy.status, "page");
  if (copy.status !== "page" || original.status !== "page") throw Error("missing history");
  assert.deepEqual(copy.transactions.slice(0, 2), original.transactions);
  assert.deepEqual(copy.transactions[2].receipts, [{accepted: true, sequence: 3}]);
  await append(root, 4);
  const retired = await manage(request(root, first));
  assert.equal(retired.status, "failed");
  assert.match(String(retired.reason), /superseded/);
  assert.equal((await active.store.loadAuthority()).status, "loaded");
});

test("reviewed plans reject stale source, wrong digest and another runtime without switching", async t => {
  const root = await fixture(t);
  const p = await plan(root, "sqlite");
  assert.equal((await manage(request(root, {...p, plan_sha256: "0".repeat(64)}))).status, "failed");
  await append(root, 3);
  const stale = await manage(request(root, p));
  assert.match(String(stale.reason), /source changed/);
  const other = await fixture(t);
  const wrongRoot = await manage(request(other, p));
  assert.match(String(wrongRoot.reason), /runtime or Goal mismatch/);
  assert.equal((await selected(root, goal)).provider, "file");
});

test("active leases, including expired leases, require actual settlement before planning", async t => {
  const root = await fixture(t);
  for (const [i, expiry] of [[3, 0], [4, 9999999999]]) {
    await append(root, i, [{todo_id: "todo_a", status: "active", owner: "worker", idempotency_key: "lease-key",
      version: 1, lease_epoch: 1, expires_at: expiry}]);
    const result = await manage(request(root, {action: "plan-migration", provider: "sqlite", plan: join(root, `lease-${i}.json`)}));
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /settled task leases/);
  }
  await append(root, 5, [{todo_id: "todo_a", status: "released", owner: "worker", idempotency_key: "lease-key", version: 2, lease_epoch: 1}]);
  await plan(root, "sqlite");
});

test("missing legacy fence and divergent target never publish a provider", async t => {
  const root = await fixture(t);
  const fence = legacyCoordinationWriterFencePath(root, goal);
  const saved = await readFile(fence);
  await rm(fence);
  const denied = await manage(request(root, {action: "plan-migration", provider: "sqlite", plan: join(root, "no-fence.json")}));
  assert.match(String(denied.reason), /writer fence/);
  await writeFile(fence, saved);
  const p = await plan(root, "sqlite");
  // Explicitly retain the source binding while staging an unrelated target.
  const paths = localAuthorityProviderPaths(root, goal);
  const identity = await (await selected(root, goal)).store.storeIdentity();
  assert.equal(identity.status, "available");
  if (identity.status !== "available") throw Error("identity");
  await durableWriteJson(paths.marker, {schema_version: "loopx_local_authority_provider_v0", provider: "file", goal_id: goal, store_identity: identity.store_identity});
  const target = new SqliteAuthorityStore(paths.sqlite, goal);
  await target.commitAuthority({operation_id: "unrelated", expected_provider_revision: null, events: [], receipts: [], next_projection: {goal_id: goal, other: true}});
  const failed = await manage(request(root, p));
  assert.equal(failed.status, "failed");
  assert.equal(failed.authority_changed, false);
  assert.equal((await selected(root, goal)).provider, "file");
});

test("File handles fence their store identity after opening", async t => {
  const root = await fixture(t);
  const paths = localAuthorityProviderPaths(root, goal);
  const original = await new FileAuthorityStore(paths.file, goal).storeIdentity();
  if (original.status !== "available") throw Error("identity");
  await durableWriteJson(paths.marker, {schema_version: "loopx_local_authority_provider_v0", provider: "file", goal_id: goal, store_identity: original.store_identity});
  const handle = await selected(root, goal);
  await writeFile(join(paths.file, "store-identity"), "file:" + "f".repeat(32));
  assert.notEqual((await handle.store.loadAuthority()).status, "loaded");
  await assert.rejects(selected(root, goal), /identity/);
});

// Kept separate from the production owner: fault hooks instrument real effects
// only in a subprocess that the parent actually kills.
import {fork} from "node:child_process";
import {once} from "node:events";
async function crash(root: string, p: JsonObject, boundary: "restoring" | "published", beforeKill?: () => Promise<void>) {
  const child = fork(new URL("./local_authority_migration_process.ts", import.meta.url),
    [JSON.stringify(request(root, p)), boundary], {execArgv: ["--no-warnings", "--experimental-strip-types", "--experimental-sqlite"], stdio: ["ignore", "ignore", "pipe", "ipc"]});
  let errors = "";
  child.stderr?.on("data", data => { errors += data; });
  const timeout = setTimeout(() => child.kill("SIGKILL"), 15000);
  try {
    const [message] = await Promise.race([once(child, "message"), once(child, "exit").then(() => { throw Error(errors || "worker exited before boundary"); })]);
    assert.deepEqual(message, {boundary});
    await beforeKill?.();
    const exited = once(child, "exit");
    child.kill("SIGKILL"); await exited;
  } finally { clearTimeout(timeout); child.kill("SIGKILL"); }
}
for (const boundary of ["restoring", "published"] as const) {
  test(`SIGKILL ${boundary}: retries retain receipts and selected writers reopen the right provider`, async t => {
    const root = await fixture(t);
    const p = await plan(root, "sqlite");
    await crash(root, p, boundary);
    const current = await selected(root, goal);
    assert.equal(current.provider, boundary === "published" ? "sqlite" : "file");
    if (boundary === "published") await append(root, 3);
    const retry = await manage(request(root, p));
    assert.equal(retry.status, boundary === "published" ? "already_applied" : "migrated", JSON.stringify(retry));
    const result = await (await selected(root, goal)).store.readReceipt("op-1");
    assert.equal(result.status, "found");
    assert.deepEqual(result.status === "found" ? result.receipts : [], [{accepted: true, sequence: 1}]);
  });
}
test("a queued canonical writer continues on the selected provider after publication", async t => {
  const root = await fixture(t);
  const p = await plan(root, "sqlite");
  // Kill after publication but before readback/completion; the writer must recover
  // the process-owned lock and use SQLite, not retain a pre-lock File handle.
  let pending: Promise<unknown> | undefined;
  await crash(root, p, "published", async () => {
    let finished = false;
    pending = append(root, 3).then(value => { finished = true; return value; });
    await new Promise(resolve => setTimeout(resolve, 60));
    assert.equal(finished, false, "canonical writer must wait for the live migration lock");
  });
  await pending;
  const resumed = await manage(request(root, p));
  assert.equal(resumed.status, "already_applied");
  assert.equal((resumed.audit as JsonObject).captured_target_cursor, "3");
});
test("replacement of a prepared target identity is rejected without republishing source", async t => {
  const root = await fixture(t);
  const p = await plan(root, "sqlite");
  await crash(root, p, "restoring");
  const paths = localAuthorityProviderPaths(root, goal);
  await rm(paths.sqlite, {recursive: true});
  const replaced = new SqliteAuthorityStore(paths.sqlite, goal);
  await replaced.storeIdentity();
  const retry = await manage(request(root, p));
  assert.equal(retry.status, "failed", JSON.stringify(retry));
  assert.equal((await selected(root, goal)).provider, "file");
  assert.equal((await replaced.loadAuthority()).status, "missing");
});
test("a source write after interrupted copy invalidates the old plan, a fresh plan can reuse the verified prefix", async t => {
  const root = await fixture(t);
  const p = await plan(root, "sqlite");
  await crash(root, p, "restoring");
  await append(root, 3);
  const stale = await manage(request(root, p));
  assert.match(String(stale.reason), /source changed/);
  const fresh = await plan(root, "sqlite", "fresh");
  assert.equal((await manage(request(root, fresh))).status, "migrated");
  assert.equal((await (await selected(root, goal)).store.readReceipt("op-3")).status, "found");
});

import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
test("mixed production-scale graph keeps archived Todos, decisions and lease generations through both providers", async t => {
  const root = await fixture(t);
  const mixed = productionScaleCoordinationFixture(goal, "native").projection;
  const quiescent = {...mixed, leases: (mixed.leases as JsonObject[]).map(lease => ({...lease, status: "released"}))};
  const source = (await selected(root, goal)).store;
  let head = await source.loadAuthority();
  for (let i = 0; i < 3; i++) {
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") throw Error("head");
    const result = await source.commitAuthority({operation_id: `mixed-${i}`, expected_provider_revision: head.provider_revision,
      events: [{kind: "observation", iteration: i}], receipts: [{accepted: i}], next_projection: {...quiescent, observation: i}});
    assert.equal(result.status, "applied"); head = await source.loadAuthority();
  }
  const original = await source.scanCommitted(null, 10);
  if (original.status !== "page") throw Error("history");
  for (const target of ["sqlite", "file"] as const) {
    const p = await plan(root, target);
    const result = await manage(request(root, p));
    assert.equal(result.status, "migrated", JSON.stringify(result));
    const copy = await (await selected(root, goal)).store.scanCommitted(null, 10);
    if (copy.status !== "page") throw Error("history");
    assert.deepEqual(copy.transactions.map(({provider_revision, ...row}) => row),
      original.transactions.map(({provider_revision, ...row}) => row));
  }
});


test("post-publication IO failure reports uncertainty, same-plan retry reads the durable result", async t => {
  const root = await fixture(t);
  const p = await plan(root, "sqlite");
  const child = fork(new URL("./local_authority_migration_process.ts", import.meta.url),
    [JSON.stringify(request(root, p)), "publication-error"],
    {execArgv: ["--no-warnings", "--experimental-strip-types", "--experimental-sqlite"], stdio: ["ignore", "ignore", "inherit", "ipc"]});
  const exited = once(child, "exit");
  const [message] = await once(child, "message");
  await exited;
  assert.equal(message.unexpected.status, "failed");
  assert.equal(message.unexpected.authority_changed, null);
  assert.equal(message.unexpected.reason_code, "migration_publication_uncertain");
  assert.equal((await selected(root, goal)).provider, "sqlite");
  assert.equal((await manage(request(root, p))).status, "already_applied");
});

test("Todo metadata survives every historical row, including null, false, empty arrays and removed keys", async t => {
  const root = await fixture(t);
  const source = (await selected(root, goal)).store;
  const metadata: JsonObject = {priority: "P1", task_class: "advancement_task", task_domain: "code",
    required_capabilities: ["shell", "filesystem_write"], required_write_scopes: ["src/**", "tests/**"],
    target_capabilities: ["coordination_authority"], excluded_agents: [], claimed_by: null,
    global_gate: false, completion_validation_revision: 0, completion_validation_revision_history: [],
    note: "保留多行说明\n- 原始 metadata", evidence: "validation://retained-metadata"};
  for (let i = 0; i < 2; i++) {
    const head = await source.loadAuthority();
    if (head.status !== "loaded") throw Error("head");
    const record = {...(head.head.todos as JsonObject[])[0], ...metadata};
    if (i === 1) { record.note = null; delete record.evidence; }
    const projection = authorityProjectionFixture(goal, [record], [], "native", {
      retained_annotation: {optional: null, count: 0, enabled: false, empty: [], nested: {"中文": "值"}},
    });
    const committed = await source.commitAuthority({operation_id: `metadata-${i}`, expected_provider_revision: head.provider_revision,
      events: [{kind: "metadata_update", note_present: true}], receipts: [{metadata_preserved: true}], next_projection: projection});
    assert.equal(committed.status, "applied");
  }
  const before = await source.scanCommitted(null, 10);
  if (before.status !== "page") throw Error("history");
  for (const target of ["sqlite", "file"] as const) {
    assert.equal((await manage(request(root, await plan(root, target)))).status, "migrated");
    const after = await (await selected(root, goal)).store.scanCommitted(null, 10);
    if (after.status !== "page") throw Error("history");
    assert.deepEqual(after.transactions.map(({provider_revision, ...row}) => row),
      before.transactions.map(({provider_revision, ...row}) => row));
    const old = (after.transactions[2].projection.todos as JsonObject[])[0];
    const current = (after.transactions[3].projection.todos as JsonObject[])[0];
    for (const [key, value] of Object.entries(metadata)) assert.deepEqual(old[key], value, key);
    assert.equal(current.note, null);
    assert.equal(Object.hasOwn(current, "evidence"), false);
    assert.deepEqual(current.excluded_agents, []);
    assert.equal(current.global_gate, false);
    assert.equal(current.completion_validation_revision, 0);
  }
});
