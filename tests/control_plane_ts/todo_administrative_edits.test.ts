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
    write_repository: todo.task_repository,
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
  for (const state of [null, "active", "expired", "released"]) test(`${provider}: requirement revision retires old execution in the same commit (${state})`, options, async t => {
    const {store, input, lease, todo} = await fixture(t, provider, state);
    const revision = {...input, planning_intent: {required_write_scopes: ["src/**", "tests/**"],
      required_capabilities: ["filesystem_write"], reason: "Include regression coverage"}};
    const before = await head(store);
    const planned = await executeCoordinationTodoUpdate(store, {...revision, dry_run: true});
    assert.equal(planned.status, "planned", JSON.stringify(planned));
    assert.deepEqual(await head(store), before);
    const result = await executeCoordinationTodoUpdate(store, revision);
    assert.equal(result.status, "applied", JSON.stringify(result));
    const transition = result.work_requirements_transition as JsonObject;
    assert.equal(transition.execution_authority_granted, false);
    assert.equal(transition.next_execution, "acquire_fresh_lease");
    assert.equal(transition.lease_retirement, state === null ? "absent" : state === "released" ? "already_released" : "released");
    const after = await head(store);
    const updated = (after.head.todos as JsonObject[])[0]!;
    assert.deepEqual(updated.required_write_scopes, ["src/**", "tests/**"]);
    assert.deepEqual(updated.required_capabilities, ["filesystem_write"]);
    assert.equal(updated.text, todo.text);
    assert.equal(updated.claimed_by, todo.claimed_by);
    assert.equal(updated.status, "open");
    assert.equal(updated.completion_validation_required, true);
    const retained = (after.head.leases as JsonObject[])[0];
    if (state === null) assert.equal(retained, undefined);
    else {
      assert.equal(retained!.status, "released");
      for (const field of ["version", "lease_epoch", "idempotency_key", "write_scopes", "write_repository"] as const)
        assert.deepEqual(retained![field], lease[field]);
    }
    const replay = await executeCoordinationTodoUpdate(store, revision);
    assert.equal(replay.status, "replayed");
    assert.deepEqual(replay.work_requirements_transition, transition);
    assert.deepEqual(await head(store), after);
    assert.equal((await executeCoordinationTodoUpdate(store, {...revision,
      planning_intent: {required_write_scopes: ["changed/**"]}})).reason_code, "coordination_operation_identity_mismatch");
    assert.deepEqual(await head(store), after);
  });
  test(`${provider}: identical requirements preserve the current grant; explicit valid proof may revise it`, options, async t => {
    const {store, input} = await fixture(t, provider);
    const same = {...input, planning_intent: {required_write_scopes: ["src/**"]}};
    const before = await head(store);
    const result = await executeCoordinationTodoUpdate(store, same);
    assert.equal(result.status, "applied", JSON.stringify(result));
    assert.equal(result.work_requirements_transition, undefined);
    assert.deepEqual((await head(store)).head.leases, before.head.leases);
    const changed = await executeCoordinationTodoUpdate(store, {...input, operation_id: "exact-proof",
      planning_intent: {required_capabilities: ["filesystem_write"]},
      lease_idempotency_key: "execution", lease_expected_version: 7});
    assert.equal(changed.status, "applied", JSON.stringify(changed));
    assert.equal(((await head(store)).head.leases as JsonObject[])[0]!.status, "released");
  });
  test(`${provider}: repository correction preserves history and subsequent owner administration`, options, async t => {
    const {store, input, lease} = await fixture(t, provider);
    const correction = {...input, operation_id: "repository-correction", patch: {},
      planning_intent: {task_repository: "git:github.com/example/revised"}};
    assert.equal((await executeCoordinationTodoUpdate(store, correction)).status, "applied");
    const retired = (await head(store)).head.leases;
    assert.deepEqual((retired as JsonObject[])[0], {...lease, status: "released",
      released_at: NOW.toISOString().replace(".000Z", "Z"), updated_at: NOW.toISOString().replace(".000Z", "Z")});
    const steps = [
      {patch: {note: "Continue administration"}, planning_intent: {}},
      {patch: {}, planning_intent: {required_write_scopes: ["tests/**"]}},
      {patch: {}, planning_intent: {status: "blocked", reason: "Pause"}},
      {patch: {}, planning_intent: {status: "open", reason: "Resume"}},
      {patch: {}, planning_intent: {status: "deferred", reason: "Wait", resume_when: "resume_at:2099-01-01T00:00:00Z"}},
      {patch: {}, planning_intent: {status: "open", reason: "Resume deferred work"}},
      {patch: {}, planning_intent: {task_repository: "git:github.com/example/corrected"}},
    ];
    for (const [index, step] of steps.entries()) {
      const request = {...input, ...step, operation_id: `continue-${index}`};
      const before = await head(store);
      assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
      assert.deepEqual(await head(store), before);
      const result = await executeCoordinationTodoUpdate(store, request);
      assert.equal(result.status, "applied", JSON.stringify(result));
      const after = await head(store);
      assert.deepEqual(after.head.leases, retired);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
      assert.deepEqual(await head(store), after);
      for (const proof of [
        {lease_idempotency_key: "execution", lease_expected_version: 7},
        {lease_idempotency_key: "execution"},
      ]) {
        const refused = await executeCoordinationTodoUpdate(store, {...request, ...proof, operation_id: `old-proof-${index}`});
        assert.equal(refused.status, "failed", JSON.stringify(refused));
        assert.deepEqual(await head(store), after);
      }
    }
    const final = (await head(store)).head.todos as JsonObject[];
    assert.equal(final[0]!.task_repository, "git:github.com/example/corrected");
    assert.equal(final[0]!.status, "open");
    assert.equal(final[0]!.claimed_by, "agent-a");
    assert.equal(final[0]!.completion_validation_required, true);
    // Retrying the original correction reports history without undoing later work.
    const beforeReplay = await head(store);
    assert.equal((await executeCoordinationTodoUpdate(store, correction)).status, "replayed");
    assert.deepEqual(await head(store), beforeReplay);
  });
  for (const boundary of ["active", "expired", "foreign", "corrupt", "excluded", "bound"] as const) {
    test(`${provider}: released-history recovery cannot hide ${boundary} repository constraints`, options, async t => {
      const {store, input, todo} = await fixture(t, provider,
        boundary === "active" || boundary === "expired" ? boundary : "released");
      const seeded = await head(store);
      const current = {...todo, task_repository: "git:github.com/example/revised",
        ...(boundary === "excluded" ? {excluded_agents: ["agent-a"]} : {}),
        ...(boundary === "bound" ? {bound_agent: "agent-b"} : {})};
      const leases = (seeded.head.leases as JsonObject[]).map(lease => ({...lease,
        ...(boundary === "foreign" ? {owner: "agent-b"} : {}),
        ...(boundary === "corrupt" ? {write_repository: "https://github.com/example/project.git"} : {})}));
      assert.equal((await store.commitAuthority({operation_id: "retained-repository-state",
        expected_provider_revision: seeded.provider_revision, events: [], receipts: [],
        next_projection: {...seeded.head, todos: [current], leases,
          todo_read_model: coordinationTodoReadModel([current], TODO_DOMAIN_READ_RECORD_SCHEMA)}})).status, "applied");
      const before = await head(store);
      for (const [index, step] of [
        {patch: {note: "Cannot bypass holder"}, planning_intent: {}},
        {patch: {}, planning_intent: {required_write_scopes: ["tests/**"]}},
        {patch: {}, planning_intent: {status: "blocked", reason: "Pause"}},
        {patch: {}, planning_intent: {status: "deferred", reason: "Wait", resume_when: "resume_at:2099-01-01T00:00:00Z"}},
      ].entries()) {
        const request = {...input, ...step, operation_id: `refuse-${index}`};
        assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "failed");
        assert.deepEqual(await head(store), before);
        assert.equal((await store.readReceipt(request.operation_id)).status, "missing");
      }
    });
  }
  test(`${provider}: requirement edits cannot heal stale proof, combine status/ownership, or bypass CAS`, options, async t => {
    const {store, input} = await fixture(t, provider);
    const revision = {...input, planning_intent: {required_capabilities: ["filesystem_write"]}};
    const before = await head(store);
    for (const request of [
      {...revision, lease_idempotency_key: "stale", lease_expected_version: 7},
      {...revision, lease_idempotency_key: "execution", lease_expected_version: 6},
      {...revision, lease_idempotency_key: "execution"},
      {...revision, lease_expected_version: 7},
      {...revision, actor_agent_id: "agent-b"},
      {...revision, actor_agent_id: "unknown"},
      {...revision, planning_intent: {...revision.planning_intent, status: "blocked", reason: "Bundle"}},
      {...revision, planning_intent: {...revision.planning_intent, claimed_by: "agent-b"}},
      {...revision, expected_provider_revision: "stale:revision"},
    ]) {
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "failed");
      assert.deepEqual(await head(store), before);
    }
    const outcomes = await Promise.all(["one", "two"].map(operation_id => executeCoordinationTodoUpdate(store,
      {...revision, operation_id, expected_provider_revision: before.provider_revision})));
    assert.equal(outcomes.filter(result => result.status === "applied").length, 1);
  });
  for (const state of ["expired", "released"]) test(`${provider}: explicit ${state} proof is not converted into an administrative revision`, options, async t => {
    const {store, input} = await fixture(t, provider, state);
    const before = await head(store);
    const result = await executeCoordinationTodoUpdate(store, {...input,
      planning_intent: {required_capabilities: ["filesystem_write"]},
      lease_idempotency_key: "execution", lease_expected_version: 7});
    assert.equal(result.status, "failed", JSON.stringify(result));
    assert.equal((result.recovery as JsonObject).action, "inspect_current_proof");
    assert.deepEqual(await head(store), before);
  });
  test(`${provider}: registry revocation cannot leave revised work or a retired lease`, options, async t => {
    const {store, input} = await fixture(t, provider);
    const before = await head(store);
    let reads = 0;
    const result = await executeCoordinationTodoUpdate(store, {...input,
      planning_intent: {required_write_scopes: ["other/**"]}}, async () => ++reads === 1);
    assert.equal(result.reason_code, "authority_source_changed");
    assert.deepEqual(await head(store), before);
    assert.equal((await store.readReceipt(input.operation_id)).status, "missing");
  });
  for (const state of ["active", "expired", "released"]) test(`${provider}: a foreign ${state} holder cannot be retired by requirement editing`, options, async t => {
    const {store, input} = await fixture(t, provider, state);
    const seeded = await head(store);
    await store.commitAuthority({operation_id: "foreign-holder", expected_provider_revision: seeded.provider_revision,
      next_projection: {...seeded.head, leases: (seeded.head.leases as JsonObject[]).map(lease => ({...lease, owner: "agent-b"}))},
      events: [], receipts: []});
    const before = await head(store);
    const result = await executeCoordinationTodoUpdate(store, {...input,
      planning_intent: {required_write_scopes: ["other/**"]}});
    assert.equal(result.reason_code, "lease_owner_mismatch", JSON.stringify(result));
    assert.equal((result.recovery as JsonObject).owner_relation, "foreign_owner");
    assert.equal((result.recovery as JsonObject).action, "reconcile_lease_owner");
    assert.deepEqual(await head(store), before);
  });
}
