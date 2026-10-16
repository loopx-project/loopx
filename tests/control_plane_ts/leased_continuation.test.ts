import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm, writeFile, readFile, mkdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {openLocalAuthorityStore, selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {Pool} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {executeTodoContinuation} from "../../loopx/control_plane/coordination/todo_continuation.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {productionScaleCoordinationFixture, PRODUCTION_SCALE_VALIDATION_DECLARATION} from "./production_scale_coordination_fixture.ts";
import {resolveTestPython} from "../../scripts/test-python.mjs";

// The CLI under test is the source checkout's own interpreter, never a bare
// `python3` alias that may be an incompatible system interpreter.
const executeFile = promisify(execFile);
const PYTHON = resolveTestPython();

async function loaded(store: AuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("fixture must be loaded");
  return head;
}

async function fixture(t: test.TestContext, provider: string) {
  const root = await mkdtemp(join(tmpdir(), "leased-continuation-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  let store: AuthorityStore;
  if (provider === "postgresql") {
    const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 2});
    t.after(() => pool.end());
    const database = {connect: async () => {
      const client = await pool.connect();
      return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
        release: () => client.release()};
    }};
    await installPostgreSqlAuthorityStoreSchema(database, `postgresql:${"b".repeat(32)}`);
    store = new PostgreSqlAuthorityStore(database, {tenant_id: `continuation-${randomUUID()}`, goal_id: "goal-a"});
  } else {
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, "goal-a", true);
    store = await openLocalAuthorityStore(root, "goal-a");
  }
  const scale = productionScaleCoordinationFixture("goal-a", "native");
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: scale.projection, events: [], receipts: []})).status, "applied");
  const base = {goal_id: "goal-a", todo_id: scale.completion_todo_id, agent_id: "agent-a",
    registered_agents: scale.registered_agents, session_id: "source", workspace: root};
  const sourceProof = {idempotency_key: scale.completion_lease_idempotency_key,
    expected_version: scale.completion_lease_expected_version};
  const receiverProof = {idempotency_key: "receiver-execution", expected_version: sourceProof.expected_version + 1};
  const prepare = {...base, action: "prepare", operation_id: "prepare", lease_proof: sourceProof,
    expected_provider_revision: (await loaded(store)).provider_revision,
    context: {work_summary: "The implementation is ready for independent validation", next_steps: ["Run the declared check"]}};
  const transfer = () => executeCanonicalTaskLeaseLifecycle(store, {operation: "transfer", goal_id: base.goal_id,
    todo_id: base.todo_id, owner: base.agent_id, idempotency_key: sourceProof.idempotency_key,
    expected_version: sourceProof.expected_version, new_owner: "agent-b", new_idempotency_key: receiverProof.idempotency_key,
    ttl_seconds: 2700, transfer_claim: true, registered_agents: base.registered_agents, now: new Date()});
  const receiver = {...base, agent_id: "agent-b", session_id: "receiver", lease_proof: receiverProof};
  const inspect = () => executeTodoContinuation(store, {...receiver, action: "inspect"});
  const adopt = async () => ({...receiver, action: "adopt", operation_id: "adopt",
    expected_provider_revision: (await loaded(store)).provider_revision});
  return {root, store, base, prepare, sourceProof, receiverProof, transfer, receiver, inspect, adopt, scale};
}

async function patch(store: AuthorityStore, todoId: string, extra: JsonObject) {
  const head = await loaded(store);
  const todo = indexCoordinationProjection(head.head, "goal-a").todos.get(todoId)!;
  assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: "goal-a",
    operation_id: `change-${randomUUID()}`, expected_provider_revision: head.provider_revision,
    projection: head.head, mutations: [{kind: "todo_upsert", todo: {...todo, ...extra}}]}))).status, "applied");
}

for (const provider of ["file", "sqlite", "postgresql"]) {
  test(`${provider}: leased context crosses claim transfer, durable adoption and current execution readback`,
    {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL ? "isolated PostgreSQL URL required" : false}, async t => {
    const f = await fixture(t, provider);
    const before = await loaded(f.store);
    const prepared = await executeTodoContinuation(f.store, f.prepare);
    assert.equal(prepared.ok, true, JSON.stringify(prepared));
    assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
    const sourceNote = indexCoordinationProjection((await loaded(f.store)).head, "goal-a").todos.get(f.base.todo_id)!.note;
    assert.equal((await f.inspect()).can_adopt, false);
    const stolen = await executeTodoContinuation(f.store, await f.adopt());
    assert.equal(stolen.reason_code, "continuation_transfer_required");
    const transferred = await f.transfer();
    assert.equal(transferred.status, "applied", JSON.stringify(transferred));
    const packet = await f.inspect();
    assert.equal(packet.note_state, "current");
    assert.equal(packet.can_adopt, true, JSON.stringify(packet));
    const head = await loaded(f.store);
    const todo = indexCoordinationProjection(head.head, "goal-a").todos.get(f.base.todo_id)!;
    assert.notEqual(todo.note, sourceNote); // Only the exact owner-bound fingerprint changes.
    assert.equal(JSON.parse(String(todo.note)).work_summary, f.prepare.context.work_summary);
    const request = await f.adopt();
    const adopted = await executeTodoContinuation(f.store, request);
    assert.equal(adopted.ok, true, JSON.stringify(adopted));
    assert.equal(adopted.current_authority_verified, true);
    assert.equal((adopted.adoption as JsonObject).changed, false);
    assert.equal((await executeTodoContinuation(f.store, request)).ok, true);
    const adoptedHead = await loaded(f.store);
    assert.deepEqual(adoptedHead.head, head.head); // Receipt cannot mutate claim, lease or unrelated work.
    assert.equal((adoptedHead.head.todos as unknown[]).length, (before.head.todos as unknown[]).length);
    const update = {goal_id: "goal-a", todo_id: f.base.todo_id, expected_role: "agent",
      registered_agents: f.base.registered_agents, dry_run: false, now: new Date(), clear_fields: [], patch: {text: "Receiver continues the validated implementation"}};
    const former = await executeCoordinationTodoUpdate(f.store, {...update, actor_agent_id: "agent-a", operation_id: "old-owner",
      lease_idempotency_key: f.sourceProof.idempotency_key, lease_expected_version: f.sourceProof.expected_version});
    assert.equal(former.status, "failed");
    const continued = await executeCoordinationTodoUpdate(f.store, {...update, actor_agent_id: "agent-b", operation_id: "receiver-work",
      lease_idempotency_key: f.receiverProof.idempotency_key, lease_expected_version: f.receiverProof.expected_version});
    assert.equal(continued.status, "applied", JSON.stringify(continued));
    // A historical adoption receipt cannot certify work after facts have changed.
    assert.equal((await executeTodoContinuation(f.store, request)).ok, false);
    assert.equal((await f.store.readReceipt("adopt")).status, "found");
  });
}

for (const provider of ["file", "sqlite", "postgresql"]) test(`${provider}: leased preparation requires exact proof; context cannot inject operational proof`,
  {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL ? "isolated PostgreSQL URL required" : false}, async t => {
  const f = await fixture(t, provider);
  const before = await loaded(f.store);
  for (const proof of [null, {...f.sourceProof, expected_version: 999}, {...f.sourceProof, idempotency_key: "wrong"}]) {
    assert.equal((await executeTodoContinuation(f.store, {...f.prepare, lease_proof: proof})).ok, false);
    assert.deepEqual(await loaded(f.store), before);
    assert.equal((await f.store.readReceipt("prepare")).status, "missing");
  }
  await assert.rejects(executeTodoContinuation(f.store, {...f.prepare, lease_proof: {idempotency_key: "partial"}}), /lease_proof/);
  await assert.rejects(executeTodoContinuation(f.store, {...f.prepare,
    context: {...f.prepare.context, lease_proof: f.sourceProof}}), /unknown context field/);
  assert.equal((await f.store.readReceipt("prepare")).status, "missing");
  const released = await executeCanonicalTaskLeaseLifecycle(f.store, {operation: "release", goal_id: f.base.goal_id,
    todo_id: f.base.todo_id, owner: f.base.agent_id, idempotency_key: f.sourceProof.idempotency_key,
    expected_version: f.sourceProof.expected_version, registered_agents: f.base.registered_agents, now: new Date()});
  assert.equal(released.status, "applied", JSON.stringify(released));
  const inactive = await loaded(f.store);
  assert.equal((await executeTodoContinuation(f.store, {...f.prepare,
    expected_provider_revision: inactive.provider_revision})).ok, false);
  assert.deepEqual(await loaded(f.store), inactive);
  assert.equal((await f.store.readReceipt("prepare")).status, "missing");
});

test("claim transfer never refreshes previously stale context", async t => {
  const f = await fixture(t, "file");
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  await patch(f.store, f.base.todo_id, {text: "The requirements changed before transfer"});
  const original = indexCoordinationProjection((await loaded(f.store)).head, "goal-a").todos.get(f.base.todo_id)!.note;
  assert.equal((await f.transfer()).status, "applied");
  assert.equal(indexCoordinationProjection((await loaded(f.store)).head, "goal-a").todos.get(f.base.todo_id)!.note, original);
  assert.equal((await f.inspect()).note_state, "stale");
  assert.equal((await executeTodoContinuation(f.store, await f.adopt())).ok, false);
});

test("adoption seals intent, recovers lost acknowledgement, and refuses a released lease on replay", async t => {
  const f = await fixture(t, "sqlite");
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  assert.equal((await f.transfer()).status, "applied");
  const request = await f.adopt();
  const commit = f.store.commitAuthority.bind(f.store);
  f.store.commitAuthority = async input => { await commit(input); throw new Error("lost response after commit"); };
  const recovered = await executeTodoContinuation(f.store, request);
  assert.equal(recovered.ok, true, JSON.stringify(recovered));
  assert.equal((recovered.adoption as JsonObject).status, "recovered");
  f.store.commitAuthority = commit;
  const collision = await executeTodoContinuation(f.store, {...request, session_id: "another-receiver-session"});
  assert.equal((collision.adoption as JsonObject).reason_code, "coordination_operation_identity_mismatch");
  const released = await executeCanonicalTaskLeaseLifecycle(f.store, {operation: "release", goal_id: "goal-a",
    todo_id: f.base.todo_id, owner: "agent-b", idempotency_key: f.receiverProof.idempotency_key,
    expected_version: f.receiverProof.expected_version, registered_agents: f.base.registered_agents, now: new Date()});
  assert.equal(released.status, "applied", JSON.stringify(released));
  assert.equal((await f.inspect()).can_adopt, false);
  assert.equal((await executeTodoContinuation(f.store, request)).ok, false);
  assert.equal((await f.store.readReceipt("adopt")).status, "found");
});

test("concurrent canonical edits cannot be hidden by receipt-only adoption", async t => {
  const f = await fixture(t, "file");
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  assert.equal((await f.transfer()).status, "applied");
  const request = await f.adopt();
  const commit = f.store.commitAuthority.bind(f.store);
  f.store.commitAuthority = async input => {
    f.store.commitAuthority = commit;
    await patch(f.store, f.base.todo_id, {text: "Concurrent requirements"});
    return commit(input);
  };
  assert.equal((await executeTodoContinuation(f.store, request)).ok, false);
  assert.equal((await f.store.readReceipt("adopt")).status, "missing");
});


for (const provider of ["file", "sqlite"]) test(`${provider}: public CLI delivers prepared and adopted current context`, async t => {
  const f = await fixture(t, provider);
  const state = join(f.root, "ACTIVE_GOAL_STATE.md"), registry = join(f.root, "registry.json");
  await writeFile(state, "---\ngoal_id: goal-a\n---\n\n# Workspace\n\n## Agent Todo\n");
  await writeFile(registry, JSON.stringify({common_runtime_root: f.root, goals: [{id: "goal-a", repo: f.root,
    state_file: "ACTIVE_GOAL_STATE.md", coordination: {agent_model: "peer_v1", handoff_mode: "hard_lease",
      registered_agents: ["agent-a", "agent-b"]}}]}));
  const fence = await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: f.root, goal_id: "goal-a", state_path: state,
    fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged", goal_id: "goal-a",
      fence_id: "fixture", source_version: "fixture", source_projection_sha256: "sha256:fixture",
      expected_shadow_provider_revision: "fixture"}});
  assert.equal(fence.status, "applied", JSON.stringify(fence));
  const run = async (args: string[]) => {
    const {stdout} = await executeFile(PYTHON, ["-m", "loopx.cli", "--registry", registry,
      "--runtime-root", f.root, "--format", "json", ...args],
      {env: {...process.env, PYTHONPATH: process.cwd()}, timeout: 60000, maxBuffer: 4 * 1024 * 1024});
    return JSON.parse(stdout);
  };
  const handoff = (action: string, agent: string, session: string, extra: string[] = []) => run([
    "handoff", action, "--goal-id", "goal-a", "--todo-id", f.base.todo_id,
    "--agent-id", agent, "--session-id", session, "--workspace", f.root, ...extra]);
  const sourceArgs = ["--task-lease-idempotency-key", f.sourceProof.idempotency_key,
    "--task-lease-expected-version", String(f.sourceProof.expected_version)];
  const prepareArgs = [...sourceArgs, "--operation-id", "cli-prepare",
    "--expected-revision", String(f.prepare.expected_provider_revision), "--rationale", "Continue with the preserved decision"];
  const beforePrepare = await loaded(f.store);
  await assert.rejects(handoff("prepare", "agent-a", "source", prepareArgs.slice(sourceArgs.length)), error => {
    const failure = error as Error & {code: number; stdout: string};
    assert.equal(failure.code, 1);
    assert.equal(JSON.parse(failure.stdout).ok, false);
    return true;
  });
  assert.deepEqual(await loaded(f.store), beforePrepare);
  assert.equal((await f.store.readReceipt("cli-prepare")).status, "missing");
  const pending = await handoff("prepare", "agent-a", "source", prepareArgs);
  assert.equal(pending.ok, true); // Missing local display declaration cannot erase the canonical write.
  assert.equal(pending.projection_delivery, "pending");
  assert.equal(pending.projection_outbox.retry_business_mutation, false);
  const committed = await loaded(f.store);
  const declarations = join(f.root, "goals", "goal-a", "todo-validation-declarations");
  await mkdir(declarations, {recursive: true});
  await writeFile(join(declarations, `${f.base.todo_id}.json`), JSON.stringify({
    schema_version: "loopx_todo_completion_validation_declaration_v0", goal_id: "goal-a", todo_id: f.base.todo_id,
    declaration: PRODUCTION_SCALE_VALIDATION_DECLARATION,
    declaration_sha256: canonicalAuthoritySha256(PRODUCTION_SCALE_VALIDATION_DECLARATION)}));
  const prepared = await handoff("prepare", "agent-a", "source", prepareArgs);
  assert.equal(prepared.ok, true);
  assert.equal((await loaded(f.store)).provider_revision, committed.provider_revision);
  assert.ok(["delivered", "current"].includes(prepared.projection_delivery), JSON.stringify(prepared));
  const projected = async () => {
    const {stdout} = await executeFile(PYTHON, ["-c",
      "import json,sys; from pathlib import Path; from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source; rows=parse_todo_source(Path(sys.argv[1]).read_text())[0]['agent']; print(json.dumps(next(r for r in rows if r['todo_id']==sys.argv[2])))",
      state, f.base.todo_id], {env: {...process.env, PYTHONPATH: process.cwd()}});
    return JSON.parse(stdout);
  };
  assert.equal(JSON.parse((await projected()).note).rationale, "Continue with the preserved decision");
  const transfer = await run(["task-lease", "transfer", "--goal-id", "goal-a", "--todo-id", f.base.todo_id,
    "--owner", "agent-a", "--idempotency-key", f.sourceProof.idempotency_key,
    "--expected-version", String(f.sourceProof.expected_version), "--new-owner", "agent-b",
    "--new-idempotency-key", f.receiverProof.idempotency_key, "--ttl-seconds", "2700", "--transfer-claim"]);
  assert.equal(transfer.ok, true, JSON.stringify(transfer));
  const receiverArgs = ["--task-lease-idempotency-key", f.receiverProof.idempotency_key,
    "--task-lease-expected-version", String(f.receiverProof.expected_version)];
  const inspect = await handoff("inspect", "agent-b", "receiver", receiverArgs);
  assert.equal(inspect.can_adopt, true, JSON.stringify(inspect));
  const beforeInspect = await readFile(state, "utf8");
  await handoff("inspect", "agent-b", "receiver", receiverArgs);
  assert.equal(await readFile(state, "utf8"), beforeInspect);
  const adopted = await handoff("adopt", "agent-b", "receiver", [...receiverArgs,
    "--operation-id", "cli-adopt", "--expected-revision", inspect.provider_revision]);
  assert.equal(adopted.current_authority_verified, true, JSON.stringify(adopted));
  assert.ok(["delivered", "current"].includes(adopted.projection_delivery));
  assert.equal((await projected()).claimed_by, "agent-b");
});


for (const blocker of ["expired", "acceptance"]) test(`current ${blocker} hold defeats a historical adoption receipt`, async t => {
  const f = await fixture(t, "file");
  assert.equal((await executeTodoContinuation(f.store, f.prepare)).ok, true);
  assert.equal((await f.transfer()).status, "applied");
  const request = await f.adopt();
  assert.equal((await executeTodoContinuation(f.store, request)).ok, true);
  const head = await loaded(f.store);
  if (blocker === "acceptance") {
    const result = await configureGoalAcceptance(f.store, {goal_id: "goal-a", operation_id: "acceptance-hold",
      actor_agent_id: null, expected_provider_revision: head.provider_revision,
      document: {objective: "Validate the selected work", non_goals: [],
        scope: {kind: "selected_work", todo_ids: [f.base.todo_id]},
        criteria: [{id: "check", description: "Independent check passes", validation_argv: ["true"], validation_timeout_seconds: 10}],
        bindings: []}});
    assert.equal(result.status, "applied", JSON.stringify(result));
  } else {
    const lease = indexCoordinationProjection(head.head, "goal-a").leases.get(f.base.todo_id)!;
    assert.equal((await f.store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: "goal-a",
      operation_id: "expire", expected_provider_revision: head.provider_revision, projection: head.head,
      mutations: [{kind: "lease_upsert", lease: {...lease, expires_at: "2020-01-01T00:00:00Z"}}]}))).status, "applied");
  }
  assert.equal((await f.inspect()).can_adopt, false);
  const rejected = await executeTodoContinuation(f.store, request);
  assert.equal(rejected.ok, false);
  if (blocker === "acceptance") assert.equal(rejected.reason_code, "goal_acceptance_unbound");
  assert.equal((await f.store.readReceipt("adopt")).status, "found");
});
