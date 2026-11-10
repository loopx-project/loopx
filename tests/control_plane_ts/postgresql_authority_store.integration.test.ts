import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { Pool, type PoolClient } from "pg";

import {
  DEFAULT_POSTGRESQL_MAX_COMMIT_BYTES,
  installPostgreSqlAuthorityStoreSchema,
  POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL,
  PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityConnection,
  type PostgreSqlAuthorityDatabase,
} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import { openLocalAuthorityStoreHandle } from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {readLocalCoordinationTodoSource} from "../../loopx/control_plane/coordination/local_authority_read.ts";
import {CANONICAL_SNAPSHOT_PAGE_REQUEST, readCanonicalSnapshotFromStore} from "../../loopx/control_plane/coordination/canonical_snapshot_page.ts";
import {projectTodoContextPage} from "../../loopx/control_plane/todos/context_projection.ts";
import {readCanonicalTodoProjection, readCanonicalQuotaProjection} from "../../loopx/control_plane/todos/read_transaction.ts";
import {canonicalReadRoot} from "./local_promotion_fixture.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {inspectGoalAcceptance, refineGoalAcceptanceOutcome} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  authorityStoreCommitFixture as commit,
  registerAuthorityStoreConformance,
} from "./authority_store_conformance.ts";

const connectionString = process.env.LOOPX_TEST_POSTGRES_URL;
const pool = connectionString ? new Pool({ connectionString, max: 12 }) : null;
const STORE_IDENTITY = `postgresql:${"b".repeat(32)}`;
const TENANT_SCOPED_TABLES = [
  "authority_heads",
  "authority_commits",
  "authority_events",
  "authority_receipts",
] as const;

function wrapClient(client: PoolClient): PostgreSqlAuthorityConnection {
  return {
    query: async (text, values) => await client.query(text, values ? [...values] : undefined),
    release: (error) => client.release(error),
  };
}

function databaseFromPool(value: Pool): PostgreSqlAuthorityDatabase {
  return { connect: async () => wrapClient(await value.connect()) };
}

function quotedRole(role: string): string {
  assert.match(role, /^[a-z][a-z0-9_]+$/);
  return `"${role}"`;
}

async function createRuntimeRole(role: string): Promise<void> {
  const identifier = quotedRole(role);
  await pool!.query(`CREATE ROLE ${identifier} NOLOGIN`);
  await pool!.query(`GRANT USAGE ON SCHEMA loopx_control_plane TO ${identifier}`);
  await pool!.query(
    `GRANT SELECT ON loopx_control_plane.authority_store_metadata TO ${identifier}`,
  );
  await pool!.query(
    `GRANT SELECT, INSERT, UPDATE ON loopx_control_plane.authority_heads TO ${identifier}`,
  );
  await pool!.query(
    `GRANT SELECT, INSERT ON loopx_control_plane.authority_commits,
       loopx_control_plane.authority_events,
       loopx_control_plane.authority_receipts TO ${identifier}`,
  );
}

function databaseForRuntimeRole(value: Pool, role: string): PostgreSqlAuthorityDatabase {
  const identifier = quotedRole(role);
  return {
    connect: async () => {
      const client = await value.connect();
      await client.query("RESET ROLE");
      await client.query(`SET ROLE ${identifier}`);
      return {
        query: async (text, values) =>
          await client.query(text, values ? [...values] : undefined),
        release: async (error) => {
          try {
            await client.query("RESET ROLE");
            client.release(error);
          } catch (resetError) {
            client.release(resetError as Error);
          }
        },
      };
    },
  };
}

async function queryAsRuntimeRole(
  value: Pool,
  role: string,
  operation: (client: PoolClient) => Promise<void>,
): Promise<void> {
  const client = await value.connect();
  try {
    await client.query(`SET ROLE ${quotedRole(role)}`);
    await operation(client);
  } finally {
    await client.query("RESET ROLE");
    client.release();
  }
}

const database = pool ? databaseFromPool(pool) : null;
const installed = database
  ? installPostgreSqlAuthorityStoreSchema(database, STORE_IDENTITY)
  : null;

async function cleanScope(tenantId: string, goalId: string): Promise<void> {
  if (!pool) return;
  await pool.query(
    `DELETE FROM loopx_control_plane.authority_heads
     WHERE tenant_id = $1 AND goal_id = $2`,
    [tenantId, goalId],
  );
}

if (database && installed) {
  test("PostgreSQL canonical quota transaction binds one provider read and complete caller differences", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const root = await canonicalReadRoot(t, options.goal_id);
    const store = new PostgreSqlAuthorityStore(database, options);
    const projection = authorityProjectionFixture(options.goal_id, [{todo_id: "todo_native_quota", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Deliver the complete required artifact", priority: "P1",
      task_class: "advancement_task", required_write_scopes: ["output.json"], note: "Retained source note", evidence: "artifact:retained"}]);
    const seeded = await store.commitAuthority({operation_id: "quota-native-seed", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []});
    assert.equal(seeded.status, "applied");
    let loads = 0;
    const observed = new Proxy(store, {get(target, property) {
      if (property === "loadAuthority") return async () => {loads++; return target.loadAuthority();};
      const value = Reflect.get(target, property);
      return typeof value === "function" ? value.bind(target) : value;
    }});
    const dependencies = {createStore: () => observed};
    const read = {schema_version: "todo_read_request_v0", kind: "canonical_fields",
      source: {runtime_root: root, goal_id: options.goal_id, include_leases: false, projection_readback: null},
      observed_at: 1790812800, evaluated_at: "2026-10-01T00:00:00Z", rollout_events: []};
    const initial = await readCanonicalTodoProjection(read, dependencies);
    const fields = initial.fields as JsonObject, lineage = initial.source_projection as JsonObject;
    const request = (field: string, changes: JsonObject[], displays: boolean): JsonObject => ({
      schema_version: "todo_quota_planning_request_v4", current_time: "2026-10-01T00:00:00Z",
      summary_request: {kind: "user", materialize_source: true, materialize_resume: true, consume_display_views: displays,
        source_reference: {read, expected_source: lineage.source, field, field_sha256: (lineage.field_digests as JsonObject)[field], changes, python_values: []},
        integer_counts: {source: null, warning: null}},
      source_contract: fields[field], handoff_items: [], route_items: [],
      resume_options: {agent_id: "agent-a", item_limit: 8, available_capabilities: []},
      selection: {observed_at: 1790812800, available: [], agent_id: "agent-a", profile: null, user_gate_scope: field === "user_todos",
        monitor_supported: false, items: [], active_items: [], active_executable_items: [], source_open_count: 1, source_complete: true,
        diagnostic_limit: 3, backlog_limit: 8, visibility_limit: 16},
    });
    const requests = [request("user_todos", [], false), request("agent_todos", [{path: ["vision_wait_states"], value: [{agent_id: "agent-a", state: "waiting"}]}], true)];
    loads = 0;
    const result = await readCanonicalQuotaProjection({requests}, dependencies);
    assert.equal(loads, 1, "both role requests share this one native provider read only");
    const wires = result.quota_wires as JsonObject[];
    assert.equal(wires[0]!.display_views, undefined, "the User caller did not request three displays");
    const views = wires[1]!.display_views as JsonObject;
    assert.equal((views.terminal as JsonObject[])[0]!.note, "Retained source note");
    assert.deepEqual((views.summary as JsonObject[])[0]!.required_write_scopes, ["output.json"]);
    const forged = structuredClone(requests);
    ((forged[1]!.summary_request as JsonObject).source_reference as JsonObject).changes = [{path: ["items", 0, "note"], value: "Forged source fact"}];
    await assert.rejects(readCanonicalQuotaProjection({requests: forged}, dependencies), /canonical Todo facts/);
    const agentField = fields.agent_todos as JsonObject;
    const sourceItem = (agentField.items as JsonObject[])[0]!;
    for (const changes of [
      [{path: ["items", 0, "required_write_scopes", 0], value: "different.json"}],
      [{path: ["open_count"], value: Number(agentField.open_count) + 1}],
      [{path: ["items"], delete: true}],
    ]) {
      const changed = structuredClone(requests);
      ((changed[1]!.summary_request as JsonObject).source_reference as JsonObject).changes = changes;
      await assert.rejects(readCanonicalQuotaProjection({requests: changed}, dependencies), /canonical Todo facts/);
    }
    // Source protection compares the final complete value. Temporary changes
    // that are restored, including a parent replacement and deletion, retain
    // the same read result and never mutate provider data.
    const restored = structuredClone(requests);
    const originalChanges = ((requests[1]!.summary_request as JsonObject).source_reference as JsonObject).changes as JsonObject[];
    ((restored[1]!.summary_request as JsonObject).source_reference as JsonObject).changes = [
      {path: ["items"], value: structuredClone(agentField.items)},
      {path: ["items", 0, "note"], delete: true},
      {path: ["items", 0, "note"], value: sourceItem.note},
      ...structuredClone(originalChanges),
    ];
    assert.deepEqual(await readCanonicalQuotaProjection({requests: restored}, dependencies), result);
    const untouched = await store.loadAuthority();
    assert.equal(untouched.status, "loaded");
    if (untouched.status !== "loaded") throw new Error("synthetic provider disappeared");
    assert.deepEqual(untouched.head, projection);
    const changed = await store.commitAuthority({operation_id: "quota-native-source-change", expected_provider_revision: untouched.provider_revision,
      next_projection: authorityProjectionFixture(options.goal_id, (projection.todos as JsonObject[]).map(row => ({...row, text: "A new requirement"}))), events: [], receipts: []});
    assert.equal(changed.status, "applied");
    await assert.rejects(readCanonicalQuotaProjection({requests}, dependencies), /canonical source changed/);
  });
  test("PostgreSQL planning reads the Agent declaration and work links from the canonical head", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const store = new PostgreSqlAuthorityStore(database, options);
    const projection = authorityProjectionFixture(options.goal_id, [{todo_id: "todo_plan", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Implement the requested outcome",
      task_class: "advancement_task", claimed_by: "author"}]);
    await store.commitAuthority({expected_provider_revision: null, operation_id: "seed-planning",
      events: [], receipts: [], next_projection: projection});
    const inspect = () => inspectGoalAcceptance(store, options.goal_id, undefined, undefined, false, "author");
    const before = await inspect();
    assert.equal((before.planning_requirements as JsonObject).declaration_current, false);
    const result = await refineGoalAcceptanceOutcome(store, {goal_id: options.goal_id,
      actor_agent_id: "author", expected_provider_revision: before.provider_revision, operation_id: "declare-planning",
      refinement: {outcome_id: "requested", owner_agent_id: "author", objective: "Deliver the requested outcome",
        requirements: [{id: "complete", description: "Complete the requested implementation"}],
        todo_ids: ["todo_plan"], source: {ref: "message:request", summary: "The owner requested this implementation"}}});
    assert.equal(result.status, "applied");
    const after = await inspect();
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
    if (loaded.status !== "loaded") return;
    assert.equal(after.provider_revision, loaded.provider_revision);
    assert.deepEqual(after.planning_requirements, {agent_id: "author", declaration_current: true,
      outcome_ids: ["requested"], linked_todo_ids: ["todo_plan"]});
    assert.deepEqual((after.goal_acceptance_contract as JsonObject).criteria ?? [], [], "Independent executable checks remain optional");
  });

  test("PostgreSQL historical Todo source retains the original frontier", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const store = new PostgreSqlAuthorityStore(database, options);
    const todos = [{schema_version: "todo_domain_record_v0", todo_id: "todo-source", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Synthetic retained work",
      title: "Synthetic retained work", priority: "P2", task_class: "advancement_task"}];
    const projection = (rows: typeof todos) => ({goal_id: options.goal_id, todos: rows, leases: [],
      todo_read_model: coordinationTodoReadModel(rows, TODO_DOMAIN_READ_RECORD_SCHEMA)});
    const first = await store.commitAuthority({expected_provider_revision: null,
      operation_id: "source-seed", events: [], receipts: [], next_projection: projection(todos)});
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    const identity = await store.storeIdentity();
    assert.equal(identity.status, "available");
    if (identity.status !== "available") return;
    const source = {source_authority: "postgresql_v0", store_identity: identity.store_identity,
      provider_revision: first.provider_revision, cursor: first.cursor};
    const later = await store.commitAuthority({expected_provider_revision: first.provider_revision,
      operation_id: "source-later", events: [], receipts: [],
      next_projection: projection(todos.map(row => ({...row, status: "done", done: true})))});
    assert.equal(later.status, "applied");
    const request = {schema_version: "loopx_local_coordination_todo_source_request_v0",
      runtime_root: tmpdir(), goal_id: options.goal_id, source};
    const historical = await readLocalCoordinationTodoSource(request, {createStore: () => store});
    assert.equal(historical.status, "loaded", JSON.stringify(historical));
    assert.equal((historical.todos as Record<string, unknown>[])[0]?.status, "open");
    assert.equal(historical.provider_revision, first.provider_revision);
    assert.equal((await readLocalCoordinationTodoSource({...request,
      source: {...source, store_identity: "foreign"}}, {createStore: () => store})).reason_code,
    "todo_source_lineage_mismatch");
  });

  test("PostgreSQL canonical snapshot feeds complete display views without becoming a fresh authority read", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const store = new PostgreSqlAuthorityStore(database, options);
    const retainedNote = "Keep every acceptance clause.\nRecover the original artifact and settlement identity.";
    const todos = [
      {todo_id: "todo_01_open", role: "agent", status: "open", done: false, archive_state: "active",
        text: "Implement the requested outcome", task_class: "advancement_task",
        required_write_scopes: ["result.json"], note: retainedNote},
      {todo_id: "todo_02_blocker", role: "agent", status: "blocked", done: false, archive_state: "active",
        text: "Wait for the required input", task_class: "blocker", claimed_by: "author"},
      {todo_id: "todo_03_completed", role: "agent", status: "done", done: true, archive_state: "active",
        text: "Preserve the verified artifact", task_class: "advancement_task",
        note: retainedNote, evidence: "artifact:verified-result", completed_at: "2026-01-01T00:00:00Z"},
    ];
    const projection = authorityProjectionFixture(options.goal_id, todos);
    const committed = await store.commitAuthority({expected_provider_revision: null,
      operation_id: "seed-display-source", events: [], receipts: [], next_projection: projection});
    assert.equal(committed.status, "applied");
    if (committed.status !== "applied") return;
    const request = {schema_version: CANONICAL_SNAPSHOT_PAGE_REQUEST, runtime_root: tmpdir(),
      goal_id: options.goal_id, include_leases: true, projection_readback: null, after: null};
    const first = await readCanonicalSnapshotFromStore(request, store);
    assert.equal(first.status, "page");
    assert.equal(first.next, null);
    const source = {source: {record: {items: first.todos}}, handoff_gates: []};
    const before = JSON.stringify(first);
    const views = projectTodoContextPage({summary_display_views: source});
    const ids = (rows: unknown) => (rows as JsonObject[]).map(row => row.todo_id);
    assert.deepEqual(ids(views.summary), ["todo_01_open", "todo_02_blocker"]);
    assert.deepEqual(ids(views.planning), ["todo_01_open", "todo_02_blocker"]);
    assert.deepEqual(ids(views.terminal), ["todo_01_open", "todo_02_blocker", "todo_03_completed"]);
    assert.deepEqual((views.planning as JsonObject[])[0].required_write_scopes, ["result.json"]);
    const terminal = (views.terminal as JsonObject[])[2];
    assert.equal(terminal.note, retainedNote);
    assert.equal(terminal.evidence, "artifact:verified-result");
    assert.equal(terminal.completed_at, "2026-01-01T00:00:00Z");
    assert.equal(JSON.stringify(first), before, "display cannot mutate the complete source snapshot");
    const unchanged = await store.loadAuthority();
    assert.equal(unchanged.status, "loaded");
    if (unchanged.status !== "loaded") return;
    assert.equal(unchanged.provider_revision, committed.provider_revision);
    assert.deepEqual(unchanged.head, projection, "display cannot write or compact provider records");

    const changed = authorityProjectionFixture(options.goal_id, todos.map(row =>
      row.todo_id === "todo_01_open" ? {...row, status: "done", done: true} : row));
    const later = await store.commitAuthority({expected_provider_revision: committed.provider_revision,
      operation_id: "advance-display-source", events: [], receipts: [], next_projection: changed});
    assert.equal(later.status, "applied");
    if (later.status !== "applied") return;
    const fresh = await readCanonicalSnapshotFromStore(request, store);
    assert.equal(fresh.status, "page");
    assert.notEqual((fresh.snapshot as JsonObject).provider_revision, (first.snapshot as JsonObject).provider_revision);
    assert.deepEqual((fresh.todos as JsonObject[]).filter(row => row.todo_id !== "todo_01_open"),
      (first.todos as JsonObject[]).filter(row => row.todo_id !== "todo_01_open"), "non-target provider rows stay unchanged");
    assert.deepEqual(ids(projectTodoContextPage({summary_display_views: {
      source: {record: {items: fresh.todos}}, handoff_gates: [],
    }}).planning), ["todo_02_blocker"]);
    assert.deepEqual(ids(projectTodoContextPage({summary_display_views: source}).planning), ["todo_01_open", "todo_02_blocker"],
      "the retained source is historical evidence, never a newly read provider head");
    await assert.rejects(readCanonicalSnapshotFromStore({...request,
      after: {snapshot: first.snapshot, todo_offset: 1, lease_offset: 0}}, store), /canonical snapshot changed/);
  });

  registerAuthorityStoreConformance("PostgreSQL provider", async (t) => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(() => cleanScope(tenantId, goalId));
    return {
      store: new PostgreSqlAuthorityStore(database, {
        tenant_id: tenantId,
        goal_id: goalId,
      }),
      contender: new PostgreSqlAuthorityStore(database, {
        tenant_id: tenantId,
        goal_id: goalId,
      }),
    };
  });

  test("PostgreSQL Todo priority edit survives reopen, replay and conflicting intent", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const store = new PostgreSqlAuthorityStore(database, options);
    const todos = [{schema_version: "todo_domain_record_v0", todo_id: "todo_priority", role: "agent",
      status: "open", done: false, archive_state: "active", text: "[P2] Existing work",
      title: "Existing work", priority: "P2", task_class: "advancement_task"}];
    await store.commitAuthority({operation_id: "seed-priority", expected_provider_revision: null,
      events: [], receipts: [], next_projection: {goal_id: options.goal_id, handoff_mode: "soft_claim", todos, leases: [],
        todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
          records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
    const request = {goal_id: options.goal_id, todo_id: "todo_priority", expected_role: "agent",
      actor_agent_id: null, registered_agents: [], operation_id: "edit-priority",
      patch: {text: "Renamed work"}, clear_fields: [], planning_intent: {priority: "P4"},
      dry_run: false, now: new Date("2030-01-01T00:00:00Z")};
    const result = await executeCoordinationTodoUpdate(store, request);
    assert.equal(result.status, "applied", JSON.stringify(result));
    const reopened = new PostgreSqlAuthorityStore(database, options);
    assert.equal((await executeCoordinationTodoUpdate(reopened, request)).status, "replayed");
    const before = await reopened.loadAuthority();
    assert.equal(before.status, "loaded");
    if (before.status !== "loaded") return;
    assert.equal((before.head.todos as {priority: string}[])[0]!.priority, "P4");
    assert.equal((await executeCoordinationTodoUpdate(reopened, {...request,
      planning_intent: {priority: "P0"}})).status, "failed");
    assert.deepEqual(await reopened.loadAuthority(), before);
    assert.equal((await executeCoordinationTodoUpdate(reopened, {...request,
      operation_id: "clear-priority", patch: {}, planning_intent: {clear_priority: true}})).status, "applied");
    const cleared = await reopened.loadAuthority();
    assert.equal(cleared.status, "loaded");
    if (cleared.status === "loaded") assert.equal((cleared.head.todos as {priority?: string}[])[0]!.priority, undefined);
  });

  test("PostgreSQL owner deferral atomically retires the lease and replays after reopen", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const store = new PostgreSqlAuthorityStore(database, options);
    const todos = [{schema_version: "todo_domain_record_v0", todo_id: "todo_wait", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Wait for dependency",
      task_class: "advancement_task", claimed_by: "agent-a"}];
    await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      events: [], receipts: [], next_projection: {goal_id: options.goal_id, handoff_mode: "hard_lease", todos,
        leases: [{schema_version: "task_lease_v0", goal_id: options.goal_id, todo_id: "todo_wait",
          owner: "agent-a", idempotency_key: "execution", version: 1, lease_epoch: 1,
          status: "active", expires_at: "2030-01-01T01:00:00Z", write_scopes: []}],
        todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 1,
          records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}});
    const request = {goal_id: options.goal_id, todo_id: "todo_wait", expected_role: "agent",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], operation_id: "defer",
      patch: {}, clear_fields: [], planning_intent: {status: "deferred",
        resume_when: "todo_done:todo_dependency", reason: "Dependency pending"},
      lease_idempotency_key: "execution", lease_expected_version: 1,
      dry_run: false, now: new Date("2030-01-01T00:00:00Z")};
    const before = await store.loadAuthority();
    assert.equal((await executeCoordinationTodoUpdate(store, {...request, lease_expected_version: 2})).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
    const reopened = new PostgreSqlAuthorityStore(database, options);
    const after = await reopened.loadAuthority();
    assert.equal(after.status, "loaded");
    if (after.status !== "loaded") return;
    assert.equal((after.head.todos as {status: string}[])[0].status, "deferred");
    assert.equal((after.head.leases as {status: string}[])[0].status, "released");
    assert.equal((await executeCoordinationTodoUpdate(reopened, request)).status, "replayed");
    assert.deepEqual(await reopened.loadAuthority(), after);
  });

  test("PostgreSQL scan binds head and rows to one snapshot during concurrent commit", async t => {
    await installed;
    const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
    t.after(() => cleanScope(options.tenant_id, options.goal_id));
    const writer = new PostgreSqlAuthorityStore(database, options);
    const first = await writer.commitAuthority(commit(null, "snapshot-first", 1, 1));
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    let interleaved = false;
    const reader = new PostgreSqlAuthorityStore({connect: async () => {
      const connection = await database.connect();
      return {...connection, query: async (sql, values) => {
        const result = await connection.query(sql, values);
        if (!interleaved && sql.includes("FROM loopx_control_plane.authority_heads")) {
          interleaved = true;
          assert.equal((await writer.commitAuthority(commit(first.provider_revision,
            "snapshot-second", 2, 2))).status, "applied");
        }
        return result;
      }};
    }}, options);
    const page = await reader.scanCommitted(null, 10);
    assert.equal(interleaved, true);
    assert.equal(page.status, "page", JSON.stringify(page));
    if (page.status !== "page") return;
    assert.deepEqual(page.transactions.map(row => row.operation_id), ["snapshot-first"]);
    assert.equal(page.has_more, false);
    const next = await reader.scanCommitted(page.next_cursor, 10);
    assert.equal(next.status, "page");
    if (next.status === "page") assert.deepEqual(next.transactions.map(row => row.operation_id), ["snapshot-second"]);
  });

  for (const removedCursor of [2, 3]) {
    test(`PostgreSQL scan rejects a missing retained row at cursor ${removedCursor}`, async t => {
      await installed;
      const options = {tenant_id: `tenant-${randomUUID()}`, goal_id: `goal-${randomUUID()}`};
      t.after(() => cleanScope(options.tenant_id, options.goal_id));
      const store = new PostgreSqlAuthorityStore(database, options);
      let revision: string | null = null;
      for (let index = 1; index <= 3; index++) {
        const result = await store.commitAuthority(commit(revision, `gap-${index}`, index, index));
        assert.equal(result.status, "applied");
        if (result.status !== "applied") return;
        revision = result.provider_revision;
      }
      await pool!.query("DELETE FROM loopx_control_plane.authority_commits WHERE tenant_id=$1 AND goal_id=$2 AND cursor=$3",
        [options.tenant_id, options.goal_id, removedCursor]);
      for (const limit of [1, 10]) {
        const result = await store.scanCommitted(removedCursor === 3 ? "1" : null, limit);
        assert.equal(result.status, "failed", JSON.stringify(result));
        if (result.status === "failed") assert.equal(result.reason_code, "provider_protocol_violation");
      }
      const head = await store.loadAuthority();
      assert.equal(head.status, "loaded");
      if (head.status === "loaded") assert.equal(head.provider_revision, revision);
    });
  }

  test("PostgreSQL provider scopes identical goals and operations by tenant", async (t) => {
    await installed;
    const goalId = `goal-${randomUUID()}`;
    const firstTenant = `tenant-${randomUUID()}`;
    const secondTenant = `tenant-${randomUUID()}`;
    t.after(async () => {
      await cleanScope(firstTenant, goalId);
      await cleanScope(secondTenant, goalId);
    });
    const first = new PostgreSqlAuthorityStore(database, {
      tenant_id: firstTenant,
      goal_id: goalId,
    });
    const second = new PostgreSqlAuthorityStore(database, {
      tenant_id: secondTenant,
      goal_id: goalId,
    });

    const results = await Promise.all([
      first.commitAuthority(commit(null, "shared-operation", 1, 1)),
      second.commitAuthority(commit(null, "shared-operation", 1, 1)),
    ]);
    assert.deepEqual(results.map((result) => result.status), ["applied", "applied"]);
    assert.equal((await first.readReceipt("shared-operation")).status, "found");
    assert.equal((await second.readReceipt("shared-operation")).status, "found");
  });

  test("local provider selector switches to a real PostgreSQL tenant", async (t) => {
    await installed;
    const root = await mkdtemp(join(tmpdir(), "loopx-local-provider-pg-"));
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(async () => {
      await cleanScope(tenantId, goalId);
      await rm(root, {recursive: true, force: true});
    });
    await mkdir(join(root, "authority"), {recursive: true});
    const marker = join(root, "authority", `provider-${createHash("sha256").update(goalId).digest("hex")}.json`);
    await writeFile(marker, JSON.stringify({
      schema_version: "loopx_local_authority_provider_v0",
      provider: "postgresql",
      goal_id: goalId,
      tenant_id: tenantId,
      store_identity: STORE_IDENTITY,
    }));
    const handle = await openLocalAuthorityStoreHandle(root, goalId, {
      openPostgresqlStore: selection => new PostgreSqlAuthorityStore(database, {
        tenant_id: selection.tenant_id,
        goal_id: selection.goal_id,
      }),
    });
    assert.equal(handle.provider, "postgresql");
    assert.equal(handle.sourceAuthority, "postgresql_v0");
    assert.equal(handle.store.providerKind, "postgresql");
    const applied = await handle.store.commitAuthority(commit(null, "selector-operation", 1, 1));
    assert.equal(applied.status, "applied");
    assert.equal((await handle.store.readReceipt("selector-operation")).status, "found");
  });

  test("PostgreSQL provider rolls back head, events, and receipts together", async (t) => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(async () => {
      await pool!.query(
        "DROP TRIGGER IF EXISTS authority_commit_test_failure ON loopx_control_plane.authority_commits",
      );
      await pool!.query(
        "DROP FUNCTION IF EXISTS loopx_control_plane.reject_test_authority_commit()",
      );
      await cleanScope(tenantId, goalId);
    });
    await pool!.query(`
      CREATE OR REPLACE FUNCTION loopx_control_plane.reject_test_authority_commit()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.operation_id = 'force-rollback' THEN
          RAISE EXCEPTION 'injected transaction failure';
        END IF;
        RETURN NEW;
      END;
      $$
    `);
    await pool!.query(`
      CREATE TRIGGER authority_commit_test_failure
      BEFORE INSERT ON loopx_control_plane.authority_commits
      FOR EACH ROW EXECUTE FUNCTION loopx_control_plane.reject_test_authority_commit()
    `);
    const store = new PostgreSqlAuthorityStore(database, {
      tenant_id: tenantId,
      goal_id: goalId,
    });
    const result = await store.commitAuthority(commit(null, "force-rollback", 1, 1));
    assert.equal(result.status, "failed");
    assert.deepEqual(await store.loadAuthority(), { status: "missing" });
    assert.deepEqual(await store.readReceipt("force-rollback"), { status: "missing" });
  });

  test("PostgreSQL COMMIT response loss is ambiguous and receipt-recoverable", async (t) => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(() => cleanScope(tenantId, goalId));
    let loseCommitResponse = true;
    const lossyDatabase: PostgreSqlAuthorityDatabase = {
      connect: async () => {
        const client = await pool!.connect();
        return {
          query: async (text, values) => {
            const result = await client.query(text, values ? [...values] : undefined);
            if (text === "COMMIT" && loseCommitResponse) {
              loseCommitResponse = false;
              throw new Error("simulated response loss after PostgreSQL commit");
            }
            return result;
          },
          release: (error) => client.release(error),
        };
      },
    };
    const store = new PostgreSqlAuthorityStore(lossyDatabase, {
      tenant_id: tenantId,
      goal_id: goalId,
    });
    const result = await store.commitAuthority(commit(null, "operation-ambiguous", 1, 4));
    assert.equal(result.status, "ambiguous");
    const receipt = await store.readReceipt("operation-ambiguous");
    assert.equal(receipt.status, "found");
    if (receipt.status === "found") assert.equal(receipt.receipts[0]?.lease_epoch, 4);
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, "loaded");
  });

  test("PostgreSQL database incarnation binds revisions and cannot be rebound", async (t) => {
    await installed;
    const tenantId = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    t.after(() => cleanScope(tenantId, goalId));
    const store = new PostgreSqlAuthorityStore(database, {
      tenant_id: tenantId,
      goal_id: goalId,
    });
    const first = await store.commitAuthority(commit(null, "operation-a", 1, 1));
    assert.equal(first.status, "applied");
    if (first.status !== "applied") return;
    const foreignRevision = first.provider_revision.replace(
      STORE_IDENTITY,
      `postgresql:${"c".repeat(32)}`,
    );
    const crossLineage = await store.commitAuthority(
      commit(foreignRevision, "operation-b", 2, 2),
    );
    assert.equal(crossLineage.status, "conflict");
    assert.equal((await store.readReceipt("operation-b")).status, "missing");

    await assert.rejects(
      installPostgreSqlAuthorityStoreSchema(
        database,
        `postgresql:${"c".repeat(32)}`,
      ),
      /database incarnation/,
    );
  });

  test("PostgreSQL runtime role is confined by transaction-local tenant RLS", async (t) => {
    await installed;
    const role = `loopx_test_runtime_${randomUUID().replaceAll("-", "")}`;
    const firstTenant = `tenant-${randomUUID()}`;
    const secondTenant = `tenant-${randomUUID()}`;
    const goalId = `goal-${randomUUID()}`;
    const runtimePool = new Pool({ connectionString, max: 1 });
    await createRuntimeRole(role);
    t.after(async () => {
      await runtimePool.end();
      await cleanScope(firstTenant, goalId);
      await cleanScope(secondTenant, goalId);
      await pool!.query(`DROP OWNED BY ${quotedRole(role)}`);
      await pool!.query(`DROP ROLE ${quotedRole(role)}`);
    });

    const runtimeDatabase = databaseForRuntimeRole(runtimePool, role);
    const first = new PostgreSqlAuthorityStore(runtimeDatabase, {
      tenant_id: firstTenant,
      goal_id: goalId,
    });
    const second = new PostgreSqlAuthorityStore(runtimeDatabase, {
      tenant_id: secondTenant,
      goal_id: goalId,
    });
    assert.equal((await first.commitAuthority(commit(null, "operation-a", 1, 1))).status, "applied");
    assert.equal((await second.commitAuthority(commit(null, "operation-b", 1, 1))).status, "applied");
    assert.equal((await first.loadAuthority()).status, "loaded");
    assert.equal((await second.readReceipt("operation-b")).status, "found");

    await queryAsRuntimeRole(runtimePool, role, async (client) => {
      for (const table of TENANT_SCOPED_TABLES) {
        const unscoped = await client.query(
          `SELECT tenant_id FROM loopx_control_plane.${table}`,
        );
        assert.equal(unscoped.rowCount, 0, `${table} must fail closed without tenant context`);
      }

      await client.query("BEGIN READ ONLY");
      await client.query(
        "SELECT set_config('loopx.tenant_id', $1, TRUE)",
        [firstTenant],
      );
      for (const table of TENANT_SCOPED_TABLES) {
        const scoped = await client.query(
          `SELECT DISTINCT tenant_id FROM loopx_control_plane.${table}`,
        );
        assert.deepEqual(scoped.rows, [{ tenant_id: firstTenant }]);
      }
      await client.query("ROLLBACK");

      await client.query("BEGIN");
      await client.query(
        "SELECT set_config('loopx.tenant_id', $1, TRUE)",
        [firstTenant],
      );
      await assert.rejects(
        client.query(
          `INSERT INTO loopx_control_plane.authority_heads
             (tenant_id, goal_id, provider_revision, cursor, head)
           VALUES ($1, $2, 0, 0, NULL)`,
          [secondTenant, `forbidden-${randomUUID()}`],
        ),
        /row-level security policy/,
      );
      await client.query("ROLLBACK");

      await assert.rejects(
        client.query(
          `UPDATE loopx_control_plane.authority_store_metadata
           SET store_identity = $1 WHERE singleton = TRUE`,
          [`postgresql:${"d".repeat(32)}`],
        ),
        /permission denied/,
      );
    });
  });
} else {
  test("PostgreSQL authority-store integration (set LOOPX_TEST_POSTGRES_URL)", { skip: true }, () => {});
}

test.after(async () => {
  await pool?.end();
});

test("PostgreSQL schema declares fail-closed tenant RLS on every scoped table", () => {
  for (const table of TENANT_SCOPED_TABLES) {
    assert.match(
      POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL,
      new RegExp(`ALTER TABLE loopx_control_plane\\.${table} FORCE ROW LEVEL SECURITY`),
    );
    assert.match(
      POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL,
      new RegExp(`CREATE POLICY ${table}_tenant_scope`),
    );
  }
  assert.match(
    POSTGRESQL_AUTHORITY_STORE_SCHEMA_SQL,
    /current_setting\('loopx\.tenant_id', TRUE\)/,
  );
});

test("PostgreSQL provider rejects an oversized commit before opening a connection", async () => {
  let connectionAttempts = 0;
  const disconnectedDatabase: PostgreSqlAuthorityDatabase = {
    connect: async () => {
      connectionAttempts += 1;
      throw new Error("capacity admission must run before connect");
    },
  };
  const store = new PostgreSqlAuthorityStore(disconnectedDatabase, {
    tenant_id: "tenant-capacity",
    goal_id: "goal-capacity",
    max_commit_bytes: 256,
  });
  const oversized = commit(null, "operation-oversized", 1, 1);
  oversized.next_projection.payload = "x".repeat(1024);

  const result = await store.commitAuthority(oversized);
  assert.equal(result.status, "failed");
  if (result.status === "failed") {
    assert.equal(result.reason_code, "store_capacity_exhausted");
    assert.match(result.reason, /configured maximum is 256/);
  }
  assert.equal(connectionAttempts, 0);
});

test("PostgreSQL provider validates its commit capacity envelope", () => {
  const disconnectedDatabase: PostgreSqlAuthorityDatabase = {
    connect: async () => {
      throw new Error("not reached");
    },
  };
  const defaultStore = new PostgreSqlAuthorityStore(disconnectedDatabase, {
    tenant_id: "tenant-capacity",
    goal_id: "goal-capacity",
  });
  assert.equal(defaultStore.maxCommitBytes, DEFAULT_POSTGRESQL_MAX_COMMIT_BYTES);
  assert.throws(
    () => new PostgreSqlAuthorityStore(disconnectedDatabase, {
      tenant_id: "tenant-capacity",
      goal_id: "goal-capacity",
      max_commit_bytes: 0,
    }),
    /max commit bytes must be a positive safe integer/,
  );
});

test("PostgreSQL provider evicts and awaits cleanup-uncertain read connections", async () => {
  const tenantId = "tenant-cleanup-fault";
  const rollbackFailure = new Error("injected rollback failure");
  const queries: string[] = [];
  let finishRelease: (() => void) | undefined;
  let resultSettled = false;
  const releaseGate = new Promise<void>((resolve) => {
    finishRelease = resolve;
  });
  let reportRelease: ((error: Error | undefined) => void) | undefined;
  const releaseStarted = new Promise<Error | undefined>((resolve) => {
    reportRelease = resolve;
  });
  const faultingDatabase: PostgreSqlAuthorityDatabase = {
    connect: async () => ({
      query: async (text) => {
        queries.push(text);
        if (text === "ROLLBACK") throw rollbackFailure;
        if (text.includes("set_config")) {
          return { rows: [{ tenant_id: tenantId }], rowCount: 1 };
        }
        if (text.includes("authority_store_metadata")) {
          return {
            rows: [{ schema_version: "loopx_postgresql_authority_store_v0", store_identity: STORE_IDENTITY }],
            rowCount: 1,
          };
        }
        if (text.includes("authority_heads")) {
          return { rows: [], rowCount: 0 };
        }
        return { rows: [], rowCount: 0 };
      },
      release: async (error) => {
        reportRelease?.(error);
        await releaseGate;
      },
    }),
  };
  const store = new PostgreSqlAuthorityStore(faultingDatabase, {
    tenant_id: tenantId,
    goal_id: "goal-cleanup-fault",
  });

  const resultPromise = store.loadAuthority().then((result) => {
    resultSettled = true;
    return result;
  });
  assert.strictEqual(await releaseStarted, rollbackFailure);
  assert.equal(resultSettled, false, "read result must wait for asynchronous connection eviction");
  assert.ok(finishRelease);
  finishRelease();

  assert.deepEqual(await resultPromise, {
    status: "unavailable",
    reason_code: "provider_read_unavailable",
    reason: "PostgreSQL authority store is unavailable",
  });
  assert.equal(queries.filter((query) => query === "ROLLBACK").length, 1);
});
