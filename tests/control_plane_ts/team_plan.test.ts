import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test, {type TestContext} from "node:test";
import {Pool} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {commitTeamPlan} from "../../loopx/control_plane/work_items/team_plan_authority.ts";
import {planTeamTransaction, previewTeamPlan, teamTransactionIdentity} from "../../loopx/control_plane/work_items/team_plan.ts";

const goal = "team-plan-test";
function request(): JsonObject {
  return {goal_id: goal, actor_agent_id: null, registered_agents: ["alpha", "beta"],
    supported_action_kinds: ["implement"], observed_at: "2026-09-17T00:00:00Z",
    expected_state_fingerprint: "confirmed", current_state_fingerprint: "confirmed",
    plan: {schema_version: "steward_team_plan_preview_v0", kind: "steward_team_plan_preview",
      goal_id: goal, proposal_id: "same-operation", objective: "Independent acceptance of each lane",
      quota_envelope: {slots: 2}, stop_condition: "Owner closes the request",
      lanes: ["alpha", "beta"].map(agent => ({lane_id: `lane-${agent}`, agent_id: agent,
        acceptance: `Evidence returned by ${agent}`, first_todo: {text: "Same bounded work", priority: "P1",
          task_class: "advancement_task", action_kind: "implement"}}))}};
}
async function seed(store: AuthorityStore) {
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: {goal_id: goal, todos: [], leases: [],
      todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: 0,
        records_sha256: canonicalAuthoritySha256([]), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}})).status, "applied");
}
const providers = ["file", "postgresql"] as const;
async function fixture(t: TestContext, provider: typeof providers[number]): Promise<AuthorityStore> {
  if (provider === "file") {
    const dir = await mkdtemp(join(tmpdir(), "team-plan-store-"));
    t.after(() => rm(dir, {recursive: true, force: true}));
    return new FileAuthorityStore(dir, goal);
  }
  const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 4});
  t.after(() => pool.end());
  const db = {connect: async () => {
    const client = await pool.connect();
    return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
      release: () => client.release()};
  }};
  await installPostgreSqlAuthorityStoreSchema(db, `postgresql:${"b".repeat(32)}`);
  return new PostgreSqlAuthorityStore(db, {tenant_id: `team-${randomUUID()}`, goal_id: goal});
}
for (const provider of providers) {
  const options = {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL};
  test(`${provider}: equal-text lane assignments commit once; concurrent replay preserves receiver identity`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const input = request();
    const results = await Promise.all([commitTeamPlan(store, input), commitTeamPlan(store, input)]);
    for (const result of results) assert.ok(["applied", "recovered", "replayed"].includes(String(result.status)), JSON.stringify(result));
    const after = await store.loadAuthority(); assert.equal(after.status, "loaded");
    if (after.status !== "loaded") return;
    const todos = after.head.todos as JsonObject[];
    assert.equal(todos.length, 2);
    assert.equal(new Set(todos.map(todo => todo.todo_id)).size, 2);
    assert.deepEqual(todos.map(todo => todo.claimed_by).sort(), ["alpha", "beta"]);
    assert.ok(todos.every(todo => todo.created_by == null && todo.last_actor_agent_id == null));
    assert.deepEqual(after.head.leases, []);
    const changed = structuredClone(input); (changed.plan as JsonObject).objective = "Different commitment";
    assert.equal((await commitTeamPlan(store, changed)).reason_code, "coordination_operation_identity_mismatch");
    assert.deepEqual(await store.loadAuthority(), after);
  });
  test(`${provider}: invalid last lane, changed state and unauthorized sender write no prefix`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const before = await store.loadAuthority();
    const invalid = request(); ((invalid.plan as JsonObject).lanes as JsonObject[])[1]!.first_todo = {text: "Invalid"};
    for (const input of [invalid, {...request(), current_state_fingerprint: "changed"}, {...request(), actor_agent_id: "alpha"},
      {...request(), current_state_fingerprint: null}, {...request(), registered_agents: ["gamma"]}]) {
      await assert.rejects(() => commitTeamPlan(store, input));
      assert.deepEqual(await store.loadAuthority(), before);
      assert.equal((await store.readReceipt(String(teamTransactionIdentity(input).operation_id))).status, "missing");
    }
    assert.equal((await commitTeamPlan(store, {...request(), expected_provider_revision: "stale"})).reason_code, "team_plan_preview_stale");
    assert.deepEqual(await store.loadAuthority(), before);
  });
  test(`${provider}: lost response and later completion/deletion recover historical result without replacement`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const original = store.commitAuthority.bind(store);
    store.commitAuthority = async commit => {await original(commit); throw new Error("response lost");};
    assert.equal((await commitTeamPlan(store, request())).status, "recovered");
    store.commitAuthority = original;
    const head = await store.loadAuthority(); assert.equal(head.status, "loaded");
    if (head.status !== "loaded") return;
    const todos = head.head.todos as JsonObject[];
    const commit = prepareCoordinationProjectionCommit({goal_id: goal, operation_id: "receiver-progress",
      expected_provider_revision: head.provider_revision, projection: head.head,
      mutations: [{kind: "todo_upsert", todo: {...todos[0]!, text: "Revised by receiver", status: "done", done: true}},
        {kind: "todo_remove", todo_id: String(todos[1]!.todo_id)}]});
    assert.equal((await original(commit)).status, "applied");
    const progressed = await store.loadAuthority();
    assert.equal((await commitTeamPlan(store, {...request(), current_state_fingerprint: "now changed"})).status, "replayed");
    assert.deepEqual(await store.loadAuthority(), progressed);
  });
  test(`${provider}: declared capability and audience gaps survive commit and recovery`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    for (const reason of ["agent_not_registered", "capability_not_granted", "audience_not_authorized"]) {
      const input = request();
      const plan = input.plan as JsonObject;
      plan.proposal_id = `declared-${reason}`;
      const lanes = plan.lanes as JsonObject[];
      delete lanes[1]!.first_todo;
      lanes[1]!.staffing_gap = {reason_code: reason, note: "Required admission is unavailable"};
      const result = await commitTeamPlan(store, input);
      assert.equal(result.status, "applied");
      const expected = [{lane_id: "lane-beta", agent_id: "beta", reason_code: reason}];
      assert.deepEqual((result.result as JsonObject).gap_lanes, expected);
      const replay = await commitTeamPlan(store, input);
      assert.equal(replay.status, "replayed");
      assert.deepEqual((replay.result as JsonObject).gap_lanes, expected);
    }
  });
  test(`${provider}: commit outage cannot publish a successful partial batch`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const before = await store.loadAuthority();
    store.commitAuthority = async () => {throw new Error("storage unavailable");};
    const result = await commitTeamPlan(store, request());
    assert.equal(result.status, "ambiguous");
    assert.deepEqual(await store.loadAuthority(), before);
  });
}

test("preview stays inert and rejects unenforced policy claims", () => {
  const input = request();
  assert.equal(previewTeamPlan(input).applies, false);
  for (const enforcement of [{quota_envelope: "enforced"}, {stop_condition: "enforced"}, {other: "advisory"}]) {
    assert.throws(() => previewTeamPlan({...input, plan: {...input.plan as JsonObject, enforcement}}), /cannot enforce/u);
  }
});


test("team preview accepts P4 and rejects conflicting legacy priority before confirmation", () => {
  const input = request();
  const first = ((input.plan as JsonObject).lanes as JsonObject[])[0]!.first_todo as JsonObject;
  first.priority = "P4";
  assert.doesNotThrow(() => previewTeamPlan(input));
  first.text = "[P0] Conflicting declaration";
  assert.throws(() => previewTeamPlan(input), /conflict/);
});

function rejectsWith(code: string) {
  return (error: unknown) => (error as {code?: string}).code === code;
}

test("a first commit needs the reviewed basis and its readback; replay stays historical", () => {
  const complete = request();
  for (const missing of [{expected_state_fingerprint: null}, {current_state_fingerprint: null},
    {expected_state_fingerprint: ""}, {current_state_fingerprint: 7},
    {expected_state_fingerprint: undefined, current_state_fingerprint: undefined}]) {
    assert.throws(() => planTeamTransaction({...complete, ...missing}), rejectsWith("team_plan_basis_missing"));
  }
  assert.throws(() => planTeamTransaction({...complete, current_state_fingerprint: "moved"}), rejectsWith("team_plan_preview_stale"));
  const planned = planTeamTransaction(complete);
  assert.equal(planned.replayed, false);
  assert.equal((planned.todos as JsonObject[]).length, 2);
  // The receipt is read back against the basis the commit bound, however the
  // state has moved since; a readback is not a second admission.
  const replay = planTeamTransaction({...complete, current_state_fingerprint: null, previous_receipt: planned.receipt});
  assert.equal(replay.replayed, true);
  assert.equal((replay.result as JsonObject).action, "reused");
  assert.deepEqual((replay.result as JsonObject).lane_todo_ids, (planned.result as JsonObject).lane_todo_ids);
  // A different bound basis is a different operation, not a replay of this one.
  assert.throws(() => planTeamTransaction({...complete, expected_state_fingerprint: "other", previous_receipt: planned.receipt}),
    /operation identity mismatch/u);
});

test("a plan whose every lane is a gap is refused before any lane is planned", () => {
  const nobodyRegistered = {...request(), registered_agents: ["gamma"]};
  assert.throws(() => planTeamTransaction(nobodyRegistered), rejectsWith("team_plan_no_staffable_lane"));
  const declared = request();
  for (const lane of (declared.plan as JsonObject).lanes as JsonObject[]) {
    delete lane.first_todo;
    lane.staffing_gap = {reason_code: "capability_not_granted", note: "Required admission is unavailable"};
  }
  assert.throws(() => planTeamTransaction(declared), rejectsWith("team_plan_no_staffable_lane"));
  // The preview itself still reports the gaps; only committing them is refused.
  assert.equal((previewTeamPlan(declared).gaps as JsonObject[]).length, 2);
  // One ready lane is enough to commit, and the receipt keeps the other as a gap.
  const partial = {...request(), registered_agents: ["alpha"]};
  const planned = planTeamTransaction(partial);
  assert.equal((planned.result as JsonObject).action, "created");
  assert.deepEqual((planned.result as JsonObject).gap_lanes, [{lane_id: "lane-beta", agent_id: "beta", reason_code: "agent_not_registered"}]);
});
