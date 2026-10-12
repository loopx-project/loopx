import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {Pool} from "pg";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema,
  type PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoCreate} from "../../loopx/control_plane/coordination/todo_create.ts";
import {createLocalCoordinationTodo} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";

for (const provider of ["file", "sqlite", "postgresql"] as const) {
  test(`${provider}: reviewed create fences concurrent commits and recovers its bound identity`,
    {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL}, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-reviewed-create-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    let store: AuthorityStore;
    if (provider === "postgresql") {
      const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL});
      t.after(() => pool.end());
      const database: PostgreSqlAuthorityDatabase = {connect: async () => {
        const client = await pool.connect();
        return {query: (sql, values) => client.query(sql, values ? [...values] : undefined), release: () => client.release()};
      }};
      await installPostgreSqlAuthorityStoreSchema(database, "postgresql:" + "b".repeat(32));
      store = new PostgreSqlAuthorityStore(database, {tenant_id: root, goal_id: "goal-a"});
    } else store = provider === "file" ? new FileAuthorityStore(root, "goal-a") : new SqliteAuthorityStore(root, "goal-a");
    const seed = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      events: [], receipts: [], next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim", todos: [], leases: [],
        todo_read_model: coordinationTodoReadModel([], TODO_DOMAIN_READ_RECORD_SCHEMA)}});
    assert.equal(seed.status, "applied");
    if (seed.status !== "applied") throw new Error("fixture refused");
    const request = {goal_id: "goal-a", operation_id: "reviewed", actor_agent_id: "agent-a",
      registered_agents: ["agent-a"], dry_run: false, now: new Date("2026-01-01T00:00:00Z"),
      expected_provider_revision: seed.provider_revision,
      todo: {schema_version: "todo_domain_record_v0", todo_id: "todo-reviewed", role: "agent", status: "open",
        done: false, archive_state: "active", text: "Reviewed request"}};
    // Interleave a lawful mutation after the typed planner loaded its head but
    // before the actual provider transaction. The independent provider CAS wins.
    const commit = store.commitAuthority.bind(store);
    store.commitAuthority = async input => {
      const head = await store.loadAuthority();
      assert.equal(head.status, "loaded");
      if (head.status !== "loaded") throw new Error("missing head");
      assert.equal((await commit({operation_id: "independent", expected_provider_revision: head.provider_revision,
        next_projection: {...head.head, observation: "accepted independent change"}, events: [], receipts: []})).status, "applied");
      return commit(input);
    };
    const conflicted = await executeCoordinationTodoCreate(store, request);
    assert.equal(conflicted.status, "conflict");
    assert.equal(conflicted.conflict_kind, "provider_revision_mismatch");
    assert.equal((await store.readReceipt("reviewed")).status, "missing");
    store.commitAuthority = commit;
    const later = await store.loadAuthority();
    assert.equal(later.status, "loaded");
    if (later.status !== "loaded") throw new Error("missing later head");
    assert.deepEqual(later.head.todos, []);
    assert.equal((await executeCoordinationTodoCreate(store, request)).reason_code, "provider_revision_mismatch");
    const current = {...request, expected_provider_revision: later.provider_revision};
    const applied = await executeCoordinationTodoCreate(store, current);
    assert.equal(applied.status, "applied");
    const accepted = await store.loadAuthority();
    assert.equal(accepted.status, "loaded");
    if (accepted.status !== "loaded") throw new Error("missing accepted head");
    assert.equal((await commit({operation_id: "later-write", expected_provider_revision: accepted.provider_revision,
      next_projection: {...accepted.head, observation: "new data after acceptance"}, events: [], receipts: []})).status, "applied");
    const beforeReplay = await store.loadAuthority();
    assert.equal((await executeCoordinationTodoCreate(store, current)).status, "replayed");
    assert.deepEqual(await store.loadAuthority(), beforeReplay);
    assert.equal((await executeCoordinationTodoCreate(store, request)).reason_code, "coordination_operation_identity_mismatch");
    const {expected_provider_revision: omitted, ...unreviewed} = current;
    void omitted;
    assert.equal((await executeCoordinationTodoCreate(store, unreviewed)).reason_code, "coordination_operation_identity_mismatch");
  });
}

test("reviewed revision cannot be silently downgraded to an older create wire", async () => {
  for (const schema_version of ["loopx_local_coordination_todo_create_request_v0", "loopx_local_coordination_todo_create_request_v1"]) {
    const result = await createLocalCoordinationTodo({schema_version,
      expected_provider_revision: "reviewed", registry_source: {path: "unused", sha256: "a".repeat(64)}},
      {createStore: () => {throw new Error("must not access a provider");}});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /requires Todo create request v2/);
  }
});

test("reviewed create wire requires a revision before accessing a provider", async () => {
  for (const expected_provider_revision of [undefined, null, "", 1]) {
    const result = await createLocalCoordinationTodo({
      schema_version: "loopx_local_coordination_todo_create_request_v2",
      expected_provider_revision, registry_source: {path: join(tmpdir(), "loopx-unused-registry.json"), sha256: "a".repeat(64)}},
      {createStore: () => {throw new Error("must not access a provider");}});
    assert.equal(result.status, "failed");
    assert.match(String(result.reason), /expected provider revision/);
  }
});
