/** Inspection is observational; all three real providers share admission facts. */
import assert from "node:assert/strict";
import {createHash, randomUUID} from "node:crypto";
import {writeFileSync, unlinkSync} from "node:fs";
import {mkdtemp, mkdir, writeFile, readFile, readdir, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {Pool} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {selectLocalSqliteAuthority, type LocalAuthorityProviderDependencies} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence, legacyCoordinationWriterFencePath} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {inspectTaskLease, TASK_LEASE_INSPECT_REQUEST} from "../../loopx/control_plane/work_items/task_lease_inspection.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {productionScaleLeaseLifecycleFixture} from "./production_scale_coordination_fixture.ts";

const NOW = new Date("2026-09-13T10:05:00Z");
const sqliteQualified = sqliteRuntimeIdentity().sqlite_authority_qualified === true;
const pool = process.env.LOOPX_TEST_POSTGRES_URL ? new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL}) : null;
const database = pool ? {connect: async () => {
  const client = await pool.connect();
  return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
    release: (error?: Error) => client.release(error)};
}} : null;
const installed = database ? installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`) : null;
test.after(async () => {await pool?.end();});

type Provider = "legacy" | "file" | "sqlite" | "postgresql";
async function fixture(t: test.TestContext, provider: Provider, schema: "native" | "legacy" = "native") {
  const root = await mkdtemp(join(tmpdir(), "lease-inspect-")), goal = "inspect-goal";
  t.after(() => rm(root, {recursive: true, force: true}));
  const registry = join(root, "registry.json"), registryText = "synthetic registration source";
  await writeFile(registry, registryText);
  const fixture = productionScaleLeaseLifecycleFixture(goal, schema);
  const originalTodos = fixture.projection.todos as JsonObject[], originalLeases = fixture.projection.leases as JsonObject[];
  const todo = originalTodos.find(row => row.todo_id === fixture.target)!;
  const lease = originalLeases.find(row => row.todo_id === fixture.target)!;
  // Independent expectations: the tested implementation never generates these.
  const cases: {name: string; todo?: JsonObject | null; lease?: JsonObject | null; active: boolean; constraint?: JsonObject}[] = [
    {name: "allowed", active: true},
    {name: "absent", lease: null, active: false},
    {name: "expired", lease: {expires_at: NOW.toISOString()}, active: false},
    {name: "released", lease: {status: "released"}, active: false},
    {name: "archived", todo: {archive_state: "archive"}, active: false, constraint: {effective: false, reason: "todo_not_found"}},
    {name: "orphan", todo: null, active: false, constraint: {effective: false, reason: "todo_not_found"}},
    {name: "closed", todo: {status: "done", done: true}, active: false, constraint: {effective: false, reason: "todo_not_open", todo_status: "done"}},
    {name: "excluded", todo: {excluded_agents: ["agent-a"], claimed_by: "agent-b"}, active: false,
      constraint: {effective: false, reason: "owner_excluded_from_todo", excluded_agents: ["agent-a"]}},
    {name: "claim", todo: {claimed_by: "agent-b"}, active: false,
      constraint: {effective: false, reason: "owner_conflicts_with_claim", claimed_by: "agent-b"}},
    {name: "unregistered", lease: {owner: "agent-retired"}, active: false,
      constraint: {effective: false, reason: "owner_not_registered", registered_agents: fixture.registered_agents}},
  ];
  // Canonical heads prohibit orphan leases; legacy files may retain them.
  if (provider !== "legacy") cases.splice(cases.findIndex(item => item.name === "orphan"), 1);
  if (provider !== "legacy") cases.push(
    {name: "completion-wait", todo: {resume_when: `todo_done:${fixture.target}`, resume_ready: true},
      active: false, constraint: {effective: false, reason: "todo_dependency_pending"}},
    {name: "dated-wait", todo: {resume_when: "resume_at:2026-09-13T10:05:00.001Z", resume_ready: true},
      active: false, constraint: {effective: false, reason: "todo_dependency_pending"}},
    {name: "dated-due", todo: {resume_when: "resume_at:2026-09-13T10:05:00Z", resume_ready: false},
      active: true},
    {name: "repository-bound", todo: {task_repository: "git:github.com/team/a"},
      lease: {write_repository: "git:github.com/team/a"}, active: true},
    {name: "repository-drift", todo: {task_repository: "git:github.com/team/b"},
      lease: {write_repository: "git:github.com/team/a"}, active: false,
      constraint: {effective: false, reason: "lease_repository_divergence"}},
  );
  const todos = [...originalTodos], leases = [...originalLeases];
  for (const item of cases) {
    if (item.todo !== null) todos.push({...todo, todo_id: `todo_inspect_${item.name}`, ...item.todo});
    if (item.lease !== null) leases.push({...lease, todo_id: `todo_inspect_${item.name}`, expires_at: "2026-09-13T10:10:00Z", ...item.lease});
  }
  const projection = authorityProjectionFixture(goal, todos, leases, schema, {handoff_mode: "hard_lease"});
  const authority = {handoff_mode: "hard_lease", registered_agent_candidates: [fixture.registered_agents],
    todos: todos.filter(row => row.archive_state === "active"), todo_projection_error: null,
    source_receipts: [{source_id: "registry", path: registry, state: "file", sha256: createHash("sha256").update(registryText).digest("hex")}]};
  let store: AuthorityStore | null = null, authorityProvider: LocalAuthorityProviderDependencies = {};
  if (provider === "legacy") {
    const directory = join(root, "goals", goal, "task-leases"); await mkdir(directory, {recursive: true});
    for (const row of leases) await writeFile(join(directory, `${row.todo_id}.json`), JSON.stringify(row));
  } else {
    if (provider === "sqlite") {
      assert.equal((await selectLocalSqliteAuthority(root, goal, true)).ok, true);
      store = new SqliteAuthorityStore(join(root, "authority/sqlite-v0"), goal);
    } else if (provider === "postgresql") {
      await installed;
      const tenant = `inspection-${randomUUID()}`;
      store = new PostgreSqlAuthorityStore(database!, {tenant_id: tenant, goal_id: goal});
      t.after(async () => {
        for (const table of ["authority_receipts", "authority_events", "authority_commits", "authority_heads"]) {
          await pool!.query(`DELETE FROM loopx_control_plane.${table} WHERE tenant_id=$1`, [tenant]);
        }
      });
      const identity = await store.storeIdentity(); assert.equal(identity.status, "available");
      if (identity.status !== "available") throw new Error("fixture identity missing");
      await mkdir(join(root, "authority"), {recursive: true});
      await writeFile(join(root, "authority", `provider-${createHash("sha256").update(goal).digest("hex")}.json`), JSON.stringify({
        schema_version: "loopx_local_authority_provider_v0", provider, goal_id: goal, tenant_id: tenant, store_identity: identity.store_identity}));
      const selected = store; authorityProvider = {openPostgresqlStore: () => selected};
    } else store = new FileAuthorityStore(join(root, "authority/file-v0"), goal);
    assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const head = await store.loadAuthority(); if (head.status !== "loaded") throw new Error("fixture missing");
    await writeFile(join(root, "state.md"), "# Synthetic display\n");
    assert.equal((await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root, goal_id: goal, state_path: join(root, "state.md"), fence: {
        schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged", goal_id: goal,
        fence_id: "inspection", source_version: "state:1", source_projection_sha256: canonicalAuthoritySha256(projection),
        expected_shadow_provider_revision: head.provider_revision}})).status, "applied");
  }
  const request = {schema_version: TASK_LEASE_INSPECT_REQUEST, source: provider === "legacy" ? "legacy" : "canonical",
    runtime_root: root, goal_id: goal, todo_id: "todo_inspect_allowed", authority};
  return {root, goal, registry, store, request, authorityProvider, cases};
}

for (const provider of ["legacy", "file", "sqlite", "postgresql"] as const) {
  for (const schema of provider === "legacy" ? ["legacy"] as const : ["legacy", "native"] as const) {
    const skip = (provider === "postgresql" && !pool) || (provider === "sqlite" && !sqliteQualified);
    test(`${provider} ${schema} full mixed head reports effective ownership without writes`, {skip}, async t => {
      const {request, store, authorityProvider, cases, root, goal} = await fixture(t, provider, schema);
      const before = await store?.loadAuthority();
      const directory = join(root, "goals", goal, "task-leases");
      const legacyNames = provider === "legacy" ? (await readdir(directory)).sort() : [];
      const legacyBytes = await Promise.all(legacyNames.map(name => readFile(join(directory, name))));
      for (const item of cases) {
        const result = await inspectTaskLease({...request, todo_id: `todo_inspect_${item.name}`}, {now: () => NOW, authorityProvider});
        assert.equal(result.ok, true, JSON.stringify(result));
        assert.equal(result.active, item.active, item.name);
        assert.deepEqual(result.executor_constraint, item.constraint, item.name);
        if (item.name === "repository-bound") assert.equal((result.lease as JsonObject).write_repository, "git:github.com/team/a");
        if (store && before?.status === "loaded") {
          assert.equal(result.source_authority, `${provider}_v0`);
          assert.equal(result.provider_revision, before.provider_revision);
          assert.equal(result.legacy_fallback_used, false); assert.equal(result.lease_path, null);
        }
      }
      assert.deepEqual(await store?.loadAuthority(), before);
      if (provider === "legacy") {
        assert.deepEqual((await readdir(directory)).sort(), legacyNames);
        assert.deepEqual(await Promise.all(legacyNames.map(name => readFile(join(directory, name)))), legacyBytes);
      }
      if (store) {
        const stale = {...request, authority: {...request.authority, handoff_mode: "invalid", todos: [],
          todo_projection_error: {code: "stale_display", message: "unused"}}};
        assert.equal((await inspectTaskLease(stale, {now: () => NOW, authorityProvider})).active, true);
      }
    });
  }
}

for (const fault of ["registration", "route", "clock", "field"] as const) {
  test(`inspection rejects ${fault} change instead of publishing an effective observation`, async t => {
    const {request, store, root, goal, registry} = await fixture(t, "file");
    const before = await store!.loadAuthority();
    const result = await inspectTaskLease(fault === "field" ? {...request, lock_token: "unexpected"} : request, {now: () => {
      if (fault === "registration") writeFileSync(registry, "changed registration");
      if (fault === "route") unlinkSync(legacyCoordinationWriterFencePath(root, goal));
      return fault === "clock" ? new Date(NaN) : NOW;
    }});
    assert.equal(result.ok, false);
    assert.equal(result.error_code, fault === "clock" ? "invalid_inspection_clock" : fault === "field" ? "invalid_inspection_request" : "authority_source_changed");
    assert.equal(result.active, undefined); assert.deepEqual(await store!.loadAuthority(), before);
  });
}

test("legacy inspection preserves projection errors and rejects malformed active expiry", async t => {
  const {request, root, goal} = await fixture(t, "legacy");
  const unavailable = await inspectTaskLease({...request, authority: {...request.authority,
    todo_projection_error: {code: "todo_projection_unavailable", message: "source cannot be projected"}}}, {now: () => NOW});
  assert.equal(unavailable.active, false);
  assert.deepEqual(unavailable.executor_constraint, {effective: false, reason: "todo_projection_unavailable"});
  const lease = {schema_version: "task_lease_v0", status: "active", owner: "agent-a", expires_at: "broken"};
  await writeFile(join(root, "goals", goal, "task-leases", `${request.todo_id}.json`), JSON.stringify(lease));
  assert.equal((await inspectTaskLease(request, {now: () => NOW})).error_code, "corrupt_lease");
});
