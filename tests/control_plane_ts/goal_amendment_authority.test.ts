import assert from "node:assert/strict";
import test from "node:test";
import {Pool} from "pg";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeGoalAmendment} from "../../loopx/control_plane/goals/goal_amendment_authority.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const goalRef = {goal_id: "amendment-goal", goal_instance_id: "ginst_" + "a".repeat(32)};
const obligation = "replan-" + "a".repeat(16);
const rootIntent = {objective: "Validate the public contract", non_goals: ["No remote changes"], acceptance: ["Independent contract check"],
  permission_scope: {write_scopes: ["tests/**"], capabilities: ["filesystem_write"]}, stop_conditions: ["Stop when Goal is inactive"], terminal_conditions: ["Accepted result returned"]};
const source = {root_commitments: {objective: rootIntent.objective, non_goals: rootIntent.non_goals, acceptance: rootIntent.acceptance, stop_condition: rootIntent.stop_conditions[0]}, goal_status: "active", profile_id: "source_session_v1", current_goal_ref: goalRef, gate: null, registered_agents: ["agent-a", "agent-b"],
  open_replan_obligations: [{obligation_id: obligation, goal_id: goalRef.goal_id, required: true, bound_agent_ids: ["agent-a"]}]};
const policy = {out_of_policy: "reject", eligible_agents: ["agent-a", "agent-b"], max_created_todos: 4, templates: [{id: "contract-check",
  todo: {text: "Verify the public contract with the independent fixture", action_kind: "validate", target_key: "contract-check",
    required_write_scopes: ["tests/**"], required_capabilities: ["filesystem_write"]}}]};
function request(operation: string, fields: JsonObject = {}): JsonObject {
  return {operation, goal_ref: goalRef, source, actor_agent_id: "agent-a", observed_at: "2026-10-12T00:00:00Z", ...fields};
}
function basis(view: JsonObject): JsonObject {
  const intent = view.intent as JsonObject;
  return {base_goal_revision: intent.goal_revision, base_intent_digest: intent.intent_digest, base_work_digest: view.work_digest};
}
for (const provider of ["file", "sqlite", "postgresql"] as const) {
  test(`${provider}: governed commit publishes one work item and basis; response loss replays original receipt`, {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL}, async t => {
    const directory = await mkdtemp(join(tmpdir(), "amendment-owner-"));t.after(() => rm(directory, {recursive: true, force: true}));
    let store: AuthorityStore;
    if (provider === "postgresql") {
      const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL});t.after(() => pool.end());
      const database = {connect: async () => {
        const client = await pool.connect();return {query: (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined), release: () => client.release()};
      }};
      await installPostgreSqlAuthorityStoreSchema(database, "postgresql:" + "b".repeat(32));
      store = new PostgreSqlAuthorityStore(database, {tenant_id: directory, goal_id: goalRef.goal_id});
    } else store = provider === "file" ? new FileAuthorityStore(directory, goalRef.goal_id) : new SqliteAuthorityStore(directory, goalRef.goal_id);
    const existing = [{schema_version: "todo_domain_record_v0", todo_id: "todo-running", role: "agent", status: "open", done: false,
      archive_state: "active", text: "Previously authorized work", task_class: "advancement_task"}];
    const seed = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed", events: [], receipts: [], next_projection: {
      goal_id: goalRef.goal_id, handoff_mode: "hard_lease", todos: existing, leases: [], todo_read_model: coordinationTodoReadModel(existing, TODO_DOMAIN_READ_RECORD_SCHEMA)}});
    assert.equal(seed.status, "applied"); if (seed.status !== "applied") throw new Error("seed failed");
    const running = await executeCoordinationTodoClaim(store, {goal_id: goalRef.goal_id, todo_id: "todo-running", operation_id: "running",
      claimed_by: "agent-b", actor_agent_id: "agent-b", expected_role: "agent", registered_agents: source.registered_agents,
      expected_provider_revision: seed.provider_revision, dry_run: false, now: new Date("2026-10-12T00:00:00Z"),
      lease_request: {idempotency_key: "running", expected_version: null, ttl_seconds: 300}});
    assert.equal(running.status, "applied");
    const initial = await store.loadAuthority();if (initial.status !== "loaded") throw new Error("missing running work");
    assert.equal((initial.head.leases as unknown[]).length, 1);
    assert.equal((await executeGoalAmendment(store, request("inspect"))).intent, null);
    const configure = request("configure", {actor_agent_id: null, operation_id: "root", root_intent: rootIntent, policy, expected_provider_revision: initial.provider_revision});
    assert.equal((await executeGoalAmendment(store, configure)).status, "applied");
    assert.equal((await executeGoalAmendment(store, configure)).status, "replayed");
    let view = await executeGoalAmendment(store, request("inspect"));
    await assert.rejects(executeGoalAmendment(store, {...configure, operation_id: "replace"}), /immutable/);
    const propose = () => request("propose", {...basis(view), operation_id: "prepare", proposal_id: "proposal-1", template_id: "contract-check",
      evidence_refs: ["evidence:contract-gap"], replan_obligation_id: obligation});
    await assert.rejects(executeGoalAmendment(store, propose()), /adopt/);
    await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt-a"}));
    const prepared = await executeGoalAmendment(store, propose());
    const proposal = prepared.proposal as JsonObject;
    assert.equal(prepared.canonical_effect, "none");
    const before = await store.loadAuthority();assert.equal(before.status, "loaded");if (before.status !== "loaded") throw new Error("missing");
    assert.deepEqual(before.head.todos, initial.head.todos);
    const commit = request("commit", {operation_id: "commit-1", proposal_id: proposal.proposal_id, proposal_digest: proposal.digest});
    const preview = await executeGoalAmendment(store, {...commit, dry_run: true});
    assert.equal(preview.status, "planned");assert.equal(preview.canonical_effect, "none");assert.equal(preview.changed, false);
    assert.deepEqual(await store.loadAuthority(), before);
    const originalCommit = store.commitAuthority.bind(store);let writes = 0;
    store.commitAuthority = async input => {writes++; const result = await originalCommit(input);assert.equal(result.status, "applied");throw new Error("lost response after commit");};
    const result = await executeGoalAmendment(store, commit);
    assert.equal(result.status, "recovered");assert.equal(result.canonical_effect, "committed");
    assert.equal(result.goal_revision, 2);assert.equal(result.replan_settlement, "pending");assert.equal(result.execution_authority_granted, false);
    assert.equal(writes, 1);
    const replay = await executeGoalAmendment(store, commit);assert.equal(replay.status, "replayed");assert.equal(writes, 1);
    assert.deepEqual(replay.original_receipt, result.original_receipt);
    const conflict = await executeGoalAmendment(store, {...commit, proposal_digest: "changed"});
    assert.equal(conflict.status, "failed"); assert.equal(conflict.reason_code, "coordination_operation_identity_mismatch");
    const after = await store.loadAuthority();assert.equal(after.status, "loaded");if (after.status !== "loaded") throw new Error("missing");
    const todos = after.head.todos as JsonObject[];assert.equal(todos.length, 2);assert.equal(todos.find(row => row.todo_id === result.todo_id)?.claimed_by, undefined);
    assert.deepEqual(todos.filter(row => row.todo_id === "todo-running"), initial.head.todos);
    assert.deepEqual(after.head.leases, initial.head.leases);assert.deepEqual((after.head.shared_goal_intent as JsonObject).root_intent, rootIntent);
    store.commitAuthority = originalCommit;
    view = await executeGoalAmendment(store, request("inspect"));
    assert.ok((view.peer_adoption as JsonObject[]).every(row => row.current === false));
    const lease_request = {idempotency_key: "new-work", expected_version: null, ttl_seconds: 300};
    await assert.rejects(executeGoalAmendment(store, request("claim", {...basis(view), operation_id: "stale-peer", todo_id: result.todo_id,
      expected_provider_revision: view.provider_revision, lease_request, actor_agent_id: "agent-b"})), /adopt/);
    assert.deepEqual(await store.loadAuthority(), after);
    await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt-b", actor_agent_id: "agent-b"}));
    view = await executeGoalAmendment(store, request("inspect"));
    assert.equal((view.peer_adoption as JsonObject[]).find(row => row.agent_id === "agent-b")?.current, true);
    assert.equal((view.peer_adoption as JsonObject[]).find(row => row.agent_id === "agent-a")?.current, false);
    const historical = await executeGoalAmendment(store, request("receipt", {receipt_operation_id: "commit-1"}));
    assert.equal(historical.status, "found");assert.equal(historical.historical_only, true);
    await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt-a-new"}));
    view = await executeGoalAmendment(store, request("inspect"));
    const claim = request("claim", {...basis(view), operation_id: "claim", todo_id: result.todo_id,
      expected_provider_revision: view.provider_revision, lease_request});
    const claimed = await executeGoalAmendment(store, claim);assert.equal(claimed.status, "applied");
    assert.equal(claimed.replan_obligation_id, obligation);
    const claimedHead = await store.loadAuthority();if (claimedHead.status !== "loaded") throw new Error("missing");
    const task = (claimedHead.head.todos as JsonObject[]).find(row => row.todo_id === result.todo_id)!;
    assert.equal(task.claimed_by, "agent-a");assert.equal(task.replan_obligation_id, obligation);
    const claimReplay = await executeGoalAmendment(store, claim);assert.equal(claimReplay.status, "replayed");
    assert.deepEqual(claimReplay.original_receipt, claimed.original_receipt);
    view = await executeGoalAmendment(store, request("inspect"));
    const continued = await executeGoalAmendment(store, request("claim", {...basis(view),
      source: {...source, open_replan_obligations: []}, operation_id: "claim-continuation", todo_id: result.todo_id,
      expected_provider_revision: view.provider_revision, observed_at: "2026-10-12T00:10:00Z",
      lease_request: {idempotency_key: "continued-work", expected_version: 1, ttl_seconds: 300}}));
    assert.equal(continued.status, "applied");assert.equal(continued.ok, true);
    assert.equal((continued.lease as JsonObject).lease_epoch, 2);
    assert.equal(continued.replan_obligation_id, obligation);

  });
}

test("source lifetime and root policy refusals leave the actual canonical head unchanged", async t => {
  const directory = await mkdtemp(join(tmpdir(), "amendment-rejections-"));t.after(() => rm(directory, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(directory, goalRef.goal_id);
  await store.commitAuthority({expected_provider_revision: null, operation_id: "seed", events: [], receipts: [], next_projection: {
    goal_id: goalRef.goal_id, todos: [], leases: [], todo_read_model: coordinationTodoReadModel([], TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  const head = await store.loadAuthority();if (head.status !== "loaded") throw new Error("seed failed");
  const configure = request("configure", {actor_agent_id: null, operation_id: "root", root_intent: rootIntent, policy, expected_provider_revision: head.provider_revision});
  for (const patch of [
    {source: {...source, profile_id: "legacy"}},
    {source: {...source, goal_status: "done"}},
    {goal_ref: {...goalRef, goal_instance_id: "ginst_" + "b".repeat(32)}},
    {source: {...source, gate: {schema_version: "loopx_source_turn_effect_gate_v1", state: "closing", retired_goal_ref: goalRef,
      new_goal_ref: {...goalRef, goal_instance_id: "ginst_" + "b".repeat(32)}, operation_id: "recreate", request_digest: "sha256:" + "a".repeat(64)}}},
  ]) {
    const rejected = await executeGoalAmendment(store, {...configure, ...patch});assert.equal(rejected.status, "failed");
    assert.deepEqual(await store.loadAuthority(), head);assert.equal((await store.readReceipt("root")).status, "missing");
  }
  for (const patch of [
    {actor_agent_id: "agent-a"}, {actor_agent_id: "outsider"}, {expected_provider_revision: "stale"},
    {root_intent: {...rootIntent, acceptance: []}},
    {policy: {...policy, out_of_policy: "allow"}},
    {policy: {...policy, templates: [{id: "bad", todo: {...policy.templates[0].todo, claimed_by: "agent-a"}}]}},
    {policy: {...policy, templates: [{id: "bad", todo: {...policy.templates[0].todo, required_capabilities: ["remote_write"]}}]}},
  ]) {
    await assert.rejects(executeGoalAmendment(store, {...configure, ...patch}));assert.deepEqual(await store.loadAuthority(), head);
  }
  const preview = await executeGoalAmendment(store, {...configure, dry_run: true});assert.equal(preview.status, "planned");assert.equal(preview.changed, false);
  assert.deepEqual(await store.loadAuthority(), head);
  await executeGoalAmendment(store, configure);
  const view = await executeGoalAmendment(store, request("inspect"));
  await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt"}));
  const baseline = await store.loadAuthority();
  const propose = request("propose", {...basis(view), operation_id: "prepare", proposal_id: "p1", template_id: "contract-check",
    evidence_refs: ["evidence:gap"], replan_obligation_id: obligation});
  for (const patch of [{source: {...source, open_replan_obligations: []}}, {replan_obligation_id: null},
    {base_intent_digest: "stale"}, {base_work_digest: "stale"}, {source_context: {request_id: "cannot-strip"}},
    {template_id: "missing"}, {evidence_refs: []}, {evidence_refs: ["https://private.example/token?secret=value"]},
    {evidence_refs: ["/home/operator/raw.log"]}]) {
    await assert.rejects(executeGoalAmendment(store, {...propose, ...patch}));assert.deepEqual(await store.loadAuthority(), baseline);
  }
  const prepared = await executeGoalAmendment(store, propose);const proposal = prepared.proposal as JsonObject;
  const current = await store.loadAuthority();if (current.status !== "loaded") throw new Error("missing");
  // A lawful independent work mutation invalidates the semantic basis even if
  // it does not change the Goal intent revision.
  const todos = [{schema_version: "todo_domain_record_v0", todo_id: "todo-independent", role: "agent", status: "open", done: false,
    archive_state: "active", text: "Independent current work"}];
  await store.commitAuthority({expected_provider_revision: current.provider_revision, operation_id: "independent", events: [], receipts: [],
    next_projection: {...current.head, todos, todo_read_model: coordinationTodoReadModel(todos, TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  const changed = await store.loadAuthority();
  await assert.rejects(executeGoalAmendment(store, request("commit", {operation_id: "commit", proposal_id: "p1", proposal_digest: proposal.digest})), /needs_rebase/);
  assert.deepEqual(await store.loadAuthority(), changed);assert.equal((await store.readReceipt("commit")).status, "missing");
});

test("semantically corrupt persisted proposals and future peer adoptions fail closed even with valid hashes", async t => {
  const directory = await mkdtemp(join(tmpdir(), "amendment-corruption-"));t.after(() => rm(directory, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(directory, goalRef.goal_id);
  const seed = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed", events: [], receipts: [], next_projection: {
    goal_id: goalRef.goal_id, todos: [], leases: [], todo_read_model: coordinationTodoReadModel([], TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  if (seed.status !== "applied") throw new Error("seed failed");
  await executeGoalAmendment(store, request("configure", {actor_agent_id: null, operation_id: "root", root_intent: rootIntent, policy, expected_provider_revision: seed.provider_revision}));
  const view = await executeGoalAmendment(store, request("inspect"));
  await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt"}));
  await executeGoalAmendment(store, request("propose", {...basis(view), operation_id: "prepare", proposal_id: "p1", template_id: "contract-check",
    evidence_refs: ["evidence:gap"], replan_obligation_id: obligation}));
  const baseline = await store.loadAuthority();if (baseline.status !== "loaded") throw new Error("missing");
  const intent = baseline.head.shared_goal_intent as JsonObject;
  const proposal = (intent.proposals as JsonObject[])[0];
  assert.deepEqual(((await executeGoalAmendment(store, request("inspect"))).intent as JsonObject).proposals, [proposal]);
  const corruptions = [
    ...[{base_goal_revision: "1"}, {base_goal_revision: 2}, {base_work_digest: "invalid"},
      ...["A".repeat(64), "a".repeat(63), "a".repeat(65), "g".repeat(64), "sha256:" + "a".repeat(64)]
        .map(base_work_digest => ({base_work_digest})),
      {template_digest: "bad"}, {evidence_refs: ["/home/operator/log"]}, {proposer_agent_id: "outsider"},
      {replan_obligation_id: null}].map(patch => {
        const merged: JsonObject = {...proposal, ...patch};
        const {digest: _digest, ...content} = merged;
        return {...intent, proposals: [{...content, digest: canonicalAuthoritySha256(content)}]};
      }),
    {...intent, adoptions: {"agent-a": {goal_revision: 2, intent_digest: intent.intent_digest}}},
    {...intent, goal_revision: 2, created_todo_ids: ["todo-without-proposal"]},
  ];
  for (const [index, invalid] of corruptions.entries()) {
    const head = await store.loadAuthority();if (head.status !== "loaded") throw new Error("missing");
    await store.commitAuthority({expected_provider_revision: head.provider_revision, operation_id: `synthetic-corruption-${index}`, events: [], receipts: [],
      next_projection: {...baseline.head, shared_goal_intent: invalid}});
    const before = await store.loadAuthority();
    await assert.rejects(executeGoalAmendment(store, request("inspect")));
    assert.deepEqual(await store.loadAuthority(), before);
  }
});

test("same-base competing commits publish once and policy capacity refuses additional work", async t => {
  const directory = await mkdtemp(join(tmpdir(), "amendment-competing-"));t.after(() => rm(directory, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(directory, goalRef.goal_id);
  const seed = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed", events: [], receipts: [], next_projection: {
    goal_id: goalRef.goal_id, todos: [], leases: [], todo_read_model: coordinationTodoReadModel([], TODO_DOMAIN_READ_RECORD_SCHEMA)}});
  if (seed.status !== "applied") throw new Error("seed failed");
  await executeGoalAmendment(store, request("configure", {actor_agent_id: null, operation_id: "root", root_intent: rootIntent,
    policy: {...policy, max_created_todos: 1}, expected_provider_revision: seed.provider_revision}));
  let view = await executeGoalAmendment(store, request("inspect"));
  await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt"}));
  const proposals = [];
  for (const key of ["first", "competing"]) {
    const prepared = await executeGoalAmendment(store, request("propose", {...basis(view), operation_id: `prepare-${key}`,
      proposal_id: key, template_id: "contract-check", evidence_refs: ["evidence:gap"], replan_obligation_id: obligation}));
    proposals.push(prepared.proposal as JsonObject);
  }
  const results = await Promise.allSettled(proposals.map(p => executeGoalAmendment(store, request("commit", {
    operation_id: `commit-${p.proposal_id}`, proposal_id: p.proposal_id, proposal_digest: p.digest}))));
  assert.equal(results.filter(r => r.status === "fulfilled" && r.value.status === "applied").length, 1);
  view = await executeGoalAmendment(store, request("inspect"));
  assert.equal((view.intent as JsonObject).goal_revision, 2);
  assert.equal((view.work_items as JsonObject[]).length, 1);
  const receipts = await Promise.all(proposals.map(p => store.readReceipt(`commit-${p.proposal_id}`)));
  assert.equal(receipts.filter(r => r.status === "found").length, 1);
  await executeGoalAmendment(store, request("adopt", {...basis(view), operation_id: "adopt-next"}));
  const prepared = await executeGoalAmendment(store, request("propose", {...basis(view), operation_id: "prepare-over-capacity",
    proposal_id: "over-capacity", template_id: "contract-check", evidence_refs: ["evidence:gap"], replan_obligation_id: obligation}));
  const before = await store.loadAuthority();
  await assert.rejects(executeGoalAmendment(store, request("commit", {operation_id: "over-capacity",
    proposal_id: "over-capacity", proposal_digest: (prepared.proposal as JsonObject).digest})), /creation capacity/);
  assert.deepEqual(await store.loadAuthority(), before);
  assert.equal((await store.readReceipt("over-capacity")).status, "missing");
});
