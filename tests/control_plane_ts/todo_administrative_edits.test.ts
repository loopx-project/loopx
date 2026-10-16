import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {Pool} from "pg";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationTodoUpdate, type CoordinationTodoUpdateInput} from "../../loopx/control_plane/coordination/todo_update.ts";

const GOAL = "administrative-edits";
const NOW = new Date("2026-09-25T06:00:00Z");
const pool = process.env.LOOPX_TEST_POSTGRES_URL ? new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL}) : null;
const database = pool && {connect: async () => {
  const client = await pool.connect();
  return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
    release: (error?: Error) => client.release(error)};
}};
const installed = database && installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`);
test.after(async () => { await pool?.end(); });

async function fixture(t: test.TestContext, provider: "file" | "sqlite" | "postgresql", leaseState: string | null = "active") {
  const root = await mkdtemp(join(tmpdir(), "loopx-administrative-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await installed;
  const store: AuthorityStore = provider === "postgresql"
    ? new PostgreSqlAuthorityStore(database!, {tenant_id: `test-${randomUUID()}`, goal_id: GOAL})
    : provider === "file" ? new FileAuthorityStore(root, GOAL) : new SqliteAuthorityStore(root, GOAL);
  const todo = {schema_version: "todo_item_v0", todo_id: "todo_work", source_section: "Agent Todo", role: "agent", status: "open",
    done: false, archive_state: "active", claimed_by: "agent-a", text: "Original task",
    task_class: "advancement_task", task_repository: "git:github.com/example/project", required_write_scopes: ["src/**"], completion_validation_required: true};
  const lease = {schema_version: "task_lease_v0", goal_id: GOAL, todo_id: "todo_work", owner: "agent-a",
    idempotency_key: "execution", version: 7, lease_epoch: 3, write_scopes: ["src/**"],
    status: leaseState === "released" ? "released" : "active",
    expires_at: leaseState === "active" ? "2026-09-25T07:00:00Z" : "2026-09-25T05:00:00Z"};
  const seeded = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: GOAL, handoff_mode: "hard_lease", todos: [todo],
      leases: leaseState === null ? [] : [lease], todo_read_model: coordinationTodoReadModel([todo], TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  assert.equal(seeded.status, "applied", JSON.stringify(seeded));
  const input: CoordinationTodoUpdateInput = {goal_id: GOAL, todo_id: "todo_work", expected_role: "agent",
    actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], operation_id: "edit",
    patch: {note: "Reviewed note"}, clear_fields: [], planning_intent: {evidence: "evidence:reviewed"},
    dry_run: false, now: NOW};
  return {store, input, lease, todo};
}

async function head(store: AuthorityStore) {
  const read = await store.loadAuthority();
  assert.equal(read.status, "loaded");
  if (read.status !== "loaded") throw new Error("missing synthetic authority");
  return read;
}

for (const provider of ["file", "sqlite", "postgresql"] as const) {
  const options = {skip: provider === "postgresql" && !pool ? "requires isolated real PostgreSQL" : false};
  for (const state of [null, "active", "expired", "released"]) test(`${provider}: owner metadata needs no execution grant (${state})`, options, async t => {
    const {store, input} = await fixture(t, provider, state);
    const before = await head(store);
    const planned = await executeCoordinationTodoUpdate(store, {...input, dry_run: true});
    assert.equal(planned.status, "planned", JSON.stringify(planned));
    assert.deepEqual(await head(store), before);
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "applied");
    const after = await head(store);
    assert.deepEqual(after.head.leases, before.head.leases);
    const todo = (after.head.todos as JsonObject[])[0]!;
    assert.equal(todo.note, "Reviewed note");
    assert.equal(todo.status, "open");
    assert.equal(todo.completion_validation_required, true);
    assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "replayed");
    assert.deepEqual(await head(store), after);
  });
  test(`${provider}: pause, note and evidence retire own active lease atomically; resume grants no execution`, options, async t => {
    const {store, input} = await fixture(t, provider);
    const pause = {...input, planning_intent: {...input.planning_intent, status: "blocked", reason: "Owner paused"}};
    const applied = await executeCoordinationTodoUpdate(store, pause);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const paused = await head(store);
    const todo = (paused.head.todos as JsonObject[])[0]!;
    assert.equal(todo.status, "blocked");
    assert.equal(todo.done, false);
    assert.equal(todo.note, input.patch.note);
    assert.equal(todo.evidence, input.planning_intent!.evidence);
    assert.equal((paused.head.leases as JsonObject[])[0]!.status, "released");
    assert.equal((await executeCoordinationTodoUpdate(store, pause)).status, "replayed");
    assert.deepEqual(await head(store), paused);
    const resumed = await executeCoordinationTodoUpdate(store, {...input, operation_id: "resume",
      planning_intent: {status: "open", reason: "Owner resumed"}});
    assert.equal(resumed.status, "applied");
    assert.equal((resumed.blocked_lifecycle_transition as JsonObject).execution_authority_granted, false);
    assert.deepEqual((await head(store)).head.leases, paused.head.leases);
  });
  test(`${provider}: explicit stale proof, foreign holder and changed provider revision never auto-heal`, options, async t => {
    const {store, input} = await fixture(t, provider);
    const before = await head(store);
    for (const request of [
      {...input, lease_idempotency_key: "stale", lease_expected_version: 6},
      {...input, lease_idempotency_key: "execution"},
      {...input, expected_provider_revision: "stale:revision"},
      {...input, actor_agent_id: "agent-b"},
      {...input, actor_agent_id: "unknown"},
      {...input, planning_intent: {required_write_scopes: ["other/**"]}},
    ]) {
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "failed");
      assert.deepEqual(await head(store), before);
    }
    const competing = {...input, patch: {note: "Foreign execution"}, operation_id: "competing",
      actor_agent_id: "agent-b", lifecycle_grants: [{agent_id: "agent-b", actions: ["update"], requires_reason: true}],
      authority_reason: "Administrative grant is not a holder grant"};
    const refused = await executeCoordinationTodoUpdate(store, competing);
    assert.equal(refused.status, "failed");
    assert.equal((refused.recovery as JsonObject)?.owner_relation, "foreign_owner", JSON.stringify(refused));
    assert.deepEqual(await head(store), before);
    // Two authorized edits pinning one head cannot silently overwrite each other.
    const outcomes = await Promise.all(["one", "two"].map(operation_id => executeCoordinationTodoUpdate(store,
      {...input, operation_id, patch: {note: operation_id}, expected_provider_revision: before.provider_revision})));
    assert.equal(outcomes.filter(result => result.status === "applied").length, 1);
  });
}
