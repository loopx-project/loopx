import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawnSync} from "node:child_process";
import test, {type TestContext} from "node:test";
import {Pool} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {openLocalAuthorityStore, selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {sqliteRuntimeIdentity} from "../../loopx/control_plane/coordination/sqlite_runtime.ts";
import {loadLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {shadowManagementStatePath} from "../../loopx/control_plane/coordination/shadow_management.ts";
import {authorityProjectionFixture, todoFixtureRecord} from "./authority_projection_fixture.ts";
import {acceptanceCompletionRequirements, acceptanceWorkGuard, goalAcceptanceTodoDigest, goalAcceptanceWorkDigest,
  normalizeGoalAcceptanceDocument, projectGoalAcceptance, readGoalAcceptance, validateAcceptanceCompletion} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {commitGoalAcceptanceVerification, commitLocalGoalAcceptance, commitLocalGoalAcceptanceLifecycleTransition,
  commitLocalGoalAcceptanceVerification, configureGoalAcceptance, inspectGoalAcceptance, inspectLocalGoalAcceptance,
  transitionGoalAcceptanceLifecycle} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {decodeCompletionValidationRevision, planCompletionValidationRevision}
  from "../../loopx/control_plane/todos/completion_validation_revision.ts";

const goal = "goal-acceptance-test";
const goalA = {goal_id: goal, goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
const goalB = {goal_id: goal, goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"};
test("documented owner configuration satisfies the canonical acceptance contract", async () => {
  const reference = await readFile(new URL("../../docs/reference/goal-acceptance-observations.md", import.meta.url), "utf8");
  const example = reference.match(/```json\n([\s\S]*?)\n```/);
  assert.ok(example, "the operation guide must include a runnable configuration");
  assert.doesNotThrow(() => normalizeGoalAcceptanceDocument(JSON.parse(example[1])));
});

function todo(todo_id: string, extra: JsonObject = {}): JsonObject {
  return {todo_id, role: "agent", status: "open", done: false, archive_state: "active",
    text: `Implement ${todo_id}`, task_class: "advancement_task", action_kind: "implement", ...extra};
}
function document(): JsonObject {
  return {scope: {kind: "all_advancement"}, objective: "Deliver independently validated work", non_goals: ["Grant additional permissions"],
    criteria: [{id: "prerequisite", description: "Prerequisite passes its own check", validation_argv: [process.execPath, "-e", "process.exit(0)"]},
      {id: "outcome", description: "Final outcome passes its check", validation_argv: [process.execPath, "-e", "process.exit(0)"]}],
    bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}, {todo_id: "todo_second", criterion_ids: ["outcome"]}]};
}
function originalHead() {
  return authorityProjectionFixture(goal, [todo("todo_first"), todo("todo_second"),
    todo("todo_gate", {role: "user", task_class: "user_gate"}),
    todo("todo_monitor", {task_class: "continuous_monitor"}),
    todo("todo_completed", {status: "done", done: true})], [], "native", {other_contract: {retained: true}});
}
test("terminal continuation observations preserve work while changed requirements invalidate it", () => {
  const work = todo("todo_first");
  const completed = {...work, status: "done", done: true, no_followup: true,
    completion_continuation: "no_followup", completion_receipt_id: `tcw_${"a".repeat(64)}`,
    note: "Bounded task completed"};
  assert.equal(goalAcceptanceTodoDigest(completed), goalAcceptanceTodoDigest(work));
  assert.notEqual(goalAcceptanceTodoDigest({...completed, text: "Deliver different work"}), goalAcceptanceTodoDigest(work));
  assert.notEqual(goalAcceptanceTodoDigest({...completed, completion_validation_required: true}), goalAcceptanceTodoDigest(work));
});
test("owner bindings confirmed with a completion checkpoint retain their declared work", () => {
  const receipt = `tcw_${"a".repeat(64)}`;
  const work = todo("todo_first", {status: "done", done: true, completion_receipt_id: receipt});
  const contract = normalizeGoalAcceptanceDocument({...document(),
    bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}]});
  // This is the persisted pre-fix work shape, independently of the new digest.
  const legacyDigest = canonicalAuthoritySha256({todo_id: "todo_first", role: "agent",
    text: "Implement todo_first", task_class: "advancement_task", action_kind: "implement",
    completion_receipt_id: receipt});
  const state = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
    digest: canonicalAuthoritySha256(contract), document: contract, verification: null,
    bindings: [{todo_id: "todo_first", todo_semantic_digest: legacyDigest,
      revision: 1, criterion_ids: ["prerequisite"], confirmed_by: "owner"}]};
  const guard = (target: JsonObject) => acceptanceWorkGuard(
    authorityProjectionFixture(goal, [target], [], "native", {goal_acceptance: state}), goal, "todo_first");
  assert.equal(guard(work)?.state, "ready");
  for (const patch of [{text: "Different work"}, {required_capabilities: ["shell"]},
    {required_write_scopes: ["src"]}]) assert.equal(guard({...work, ...patch})?.state, "stale");
});
test("validator revisions and successor links preserve an existing acceptance binding", () => {
  const original = todo("todo_first", {completion_validation_required: true,
    completion_validation_sha256: "a".repeat(64), completion_validation_revision: 0,
    completion_validation_revision_history: [], successor_todo_ids: []});
  const contract = normalizeGoalAcceptanceDocument({...document(),
    bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}]});
  const state = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
    digest: canonicalAuthoritySha256(contract), document: contract, verification: null,
    bindings: [{todo_id: "todo_first", todo_semantic_digest: goalAcceptanceTodoDigest(original),
      revision: 1, criterion_ids: ["prerequisite"], confirmed_by: "owner"}]};
  const receipt = {schema_version: "loopx_todo_completion_validation_revision_receipt_v0", revision: 1,
    operation_id: "revise", previous_declaration_sha256: "a".repeat(64),
    declaration_sha256: "b".repeat(64), actor_agent_id: "agent-a", revised_at: "2026-09-23T00:00:00Z"};
  const revised = {...original, completion_validation_sha256: "b".repeat(64),
    completion_validation_revision: 1, completion_validation_revision_history: [receipt],
    successor_todo_ids: ["todo_followup"]};
  const guarded = (work: JsonObject) => acceptanceWorkGuard(authorityProjectionFixture(goal,
    [work, todo("todo_followup")], [], "native", {goal_acceptance: state}), goal, "todo_first");
  assert.equal(guarded(original)?.state, "ready");
  assert.notEqual(goalAcceptanceTodoDigest(revised), goalAcceptanceTodoDigest(original),
    "persisted v0 digests must remain compatible without rebinding every existing Todo");
  assert.equal(guarded(revised)?.state, "ready");
  assert.equal(guarded({...revised, resume_when: "monitor_changed:todo_followup"})?.state, "ready",
    "an added scheduling wait cannot alter the confirmed acceptance association");
  assert.equal(guarded({...revised, text: "Different work"})?.state, "stale");
  assert.equal(guarded({...revised, required_write_scopes: ["private"]})?.state, "stale");
  assert.equal(guarded({...revised, completion_validation_required: false})?.state, "stale");
  assert.equal(guarded({...revised, completion_validation_revision_history: [{...receipt,
    declaration_sha256: "c".repeat(64)}]})?.state, "stale");
  const boundWithWait = {...state, bindings: [{...state.bindings[0],
    todo_semantic_digest: goalAcceptanceTodoDigest({...original, resume_when: "monitor_changed:todo_first"})}]};
  const changedWait = acceptanceWorkGuard(authorityProjectionFixture(goal,
    [{...original, resume_when: "monitor_changed:todo_followup"}, todo("todo_followup")], [], "native",
    {goal_acceptance: boundWithWait}), goal, "todo_first");
  assert.equal(changedWait?.state, "stale", "the matcher cannot reconstruct a replaced prior wait condition");
});
test("first validator binding preserves only the proven original work and owner association", () => {
  for (const defaults of [{}, {completion_validation_required: false,
    completion_validation_revision: 0, completion_validation_revision_history: []}]) {
    const original = todo("todo_first", defaults);
    const contract = normalizeGoalAcceptanceDocument({...document(),
      bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}]});
    const state = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
      digest: canonicalAuthoritySha256(contract), document: contract, verification: null,
      bindings: [{todo_id: "todo_first", todo_semantic_digest: goalAcceptanceTodoDigest(original),
        revision: 1, criterion_ids: ["prerequisite"], confirmed_by: "owner"}]};
    const guarded = (work: JsonObject) => acceptanceWorkGuard(authorityProjectionFixture(goal,
      [work], [], "native", {goal_acceptance: state}), goal, "todo_first");
    const declaration = {validation_command: null, validation_command_argv: [process.execPath, "-e", "process.exit(1)"],
      validation_label: "Independent validation", validation_timeout_seconds: 5};
    const bind = planCompletionValidationRevision({todo: original, actor_agent_id: "agent-a",
      operation_id: "first-bind", revised_at: "2026-09-27T00:00:00Z",
      revision: decodeCompletionValidationRevision({schema_version: "loopx_todo_completion_validation_revision_v1",
        expected_declaration_sha256: null, declaration})});
    const bound = {...original, ...bind.updates};
    assert.equal(guarded(bound)?.state, "ready");
    assert.notEqual(goalAcceptanceTodoDigest(bound), goalAcceptanceTodoDigest(original));
    const replace = planCompletionValidationRevision({todo: bound, actor_agent_id: "agent-a",
      operation_id: "replace", revised_at: "2026-09-27T00:01:00Z",
      revision: decodeCompletionValidationRevision({schema_version: "loopx_todo_completion_validation_revision_v0",
        expected_declaration_sha256: bound.completion_validation_sha256,
        declaration: {...declaration, validation_command_argv: [process.execPath, "-e", "process.exit(0)"]}})});
    const revised = {...bound, ...replace.updates};
    assert.equal(guarded(revised)?.state, "ready");
    for (const patch of [{text: "Changed work"}, {required_write_scopes: ["private/**"]},
      {completion_validation_required: false},
      {completion_validation_revision_history: [{...bind.receipt, previous_validation_authority: {completion_validation_required: true}}]},
      {completion_validation_revision_history: [{...bind.receipt, previous_validation_authority: null}]},
      {completion_validation_revision_history: [{...bind.receipt, declaration_sha256: "a".repeat(64)}]}]) {
      assert.equal(guarded({...bound, ...patch})?.state, "stale");
    }
    assert.equal(guarded({...revised, completion_validation_revision_history: [bind.receipt,
      {...replace.receipt, previous_declaration_sha256: "b".repeat(64)}]})?.state, "stale");
    const unboundState = {...state, bindings: [], document: {...contract, bindings: []}};
    unboundState.digest = canonicalAuthoritySha256(unboundState.document);
    assert.equal(acceptanceWorkGuard(authorityProjectionFixture(goal, [bound], [], "native",
      {goal_acceptance: unboundState}), goal, "todo_first")?.state, "unbound",
    "a local validator cannot create an owner acceptance binding");
  }
});
async function seed(store: AuthorityStore) {
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: originalHead()})).status, "applied");
}
async function head(store: AuthorityStore) {
  const result = await store.loadAuthority();
  assert.equal(result.status, "loaded");
  if (result.status !== "loaded") throw new Error("test head unavailable");
  return result;
}
async function configureRequest(store: AuthorityStore, extra: JsonObject = {}): Promise<JsonObject> {
  return {goal_id: goal, actor_agent_id: null, operation_id: randomUUID(), disable: false,
    expected_provider_revision: (await head(store)).provider_revision, document: document(), ...extra};
}
async function verifyRequest(store: AuthorityStore, extra: JsonObject = {}): Promise<JsonObject> {
  const basis = await inspectGoalAcceptance(store, goal);
  return {goal_id: goal, operation_id: randomUUID(), expected_provider_revision: basis.provider_revision,
    revision: basis.revision, contract_digest: basis.contract_digest,
    results: ["outcome", "prerequisite"].map(criterion_id => ({criterion_id, passed: true, exit_code: 0})), ...extra};
}
async function lifecycleTransition(
  store: AuthorityStore,
  transition: JsonObject,
  operationId = randomUUID(),
): Promise<JsonObject> {
  return transitionGoalAcceptanceLifecycle(store, {
    goal_id: goal,
    actor_agent_id: null,
    operation_id: operationId,
    transition,
  });
}
async function update(store: AuthorityStore, todoId: string, patch: JsonObject) {
  const current = await head(store);
  const previous = (current.head.todos as JsonObject[]).find(item => item.todo_id === todoId);
  const result = await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
    operation_id: randomUUID(), expected_provider_revision: current.provider_revision, projection: current.head,
    mutations: [{kind: "todo_upsert", todo: previous ? {...previous, ...patch} : patch}]}));
  assert.equal(result.status, "applied");
}
const providers = ["file", "sqlite", "postgresql"] as const;
// Developer runtimes outside the supported Node range may lack the WAL-reset
// driver. CI asserts SQLite qualification and runs every provider row.
const sqliteQualified = sqliteRuntimeIdentity().sqlite_authority_qualified === true;
async function fixture(t: TestContext, provider: typeof providers[number]): Promise<AuthorityStore> {
  if (provider !== "postgresql") {
    const dir = await mkdtemp(join(tmpdir(), "goal-acceptance-"));
    t.after(() => rm(dir, {recursive: true, force: true}));
    return provider === "file" ? new FileAuthorityStore(dir, goal) : new SqliteAuthorityStore(dir, goal);
  }
  const pool = new Pool({connectionString: process.env.LOOPX_TEST_POSTGRES_URL, max: 4});
  const db = {connect: async () => {
    const client = await pool.connect();
    return {query: async (sql: string, values?: readonly unknown[]) => client.query(sql, values ? [...values] : undefined),
      release: () => client.release()};
  }};
  const tenant = `acceptance-${randomUUID()}`;
  t.after(async () => {
    try {
      for (const table of ["authority_commits", "authority_heads"]) {
        await pool.query(`DELETE FROM loopx_control_plane.${table} WHERE tenant_id=$1 AND goal_id=$2`, [tenant, goal]);
      }
    } finally { await pool.end(); }
  });
  await installPostgreSqlAuthorityStoreSchema(db, `postgresql:${"b".repeat(32)}`);
  return new PostgreSqlAuthorityStore(db, {tenant_id: tenant, goal_id: goal});
}

for (const provider of providers) {
  const options = {skip: (provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL) ||
    (provider === "sqlite" && !sqliteQualified)};
  test(`${provider}: first exact acceptance mutation binds the current instance atomically`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const request = await configureRequest(store, {goal_ref: goalA});
    const before = await head(store);
    assert.equal((await configureGoalAcceptance(store, {...request, dry_run: true})).status, "planned");
    assert.deepEqual(await head(store), before);
    assert.equal((await configureGoalAcceptance(store, request)).status, "applied");
    const current = await head(store);
    assert.deepEqual(current.head.goal_acceptance_lifecycle, {
      schema_version: "loopx_goal_acceptance_lifecycle_v0",
      state: "active",
      goal_ref: goalA,
    });
    assert.deepEqual((current.head.goal_acceptance as JsonObject).owner_goal_ref, goalA);
    assert.equal((await inspectGoalAcceptance(store, goal, undefined, goalA)).status, "loaded");
  });
  test(`${provider}: historical recreation migration keeps legacy acceptance with the retired instance`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    assert.equal((await configureGoalAcceptance(store, await configureRequest(store))).status, "applied");
    assert.equal((await lifecycleTransition(store, {
      kind: "reconcile_recreated",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    })).status, "applied");
    const migrated = await head(store);
    assert.deepEqual(migrated.head.goal_acceptance_lifecycle, {
      schema_version: "loopx_goal_acceptance_lifecycle_v0",
      state: "active",
      goal_ref: goalB,
    });
    assert.deepEqual((migrated.head.goal_acceptance as JsonObject).owner_goal_ref, goalA);
    assert.deepEqual(projectGoalAcceptance(migrated.head, goal), {
      enabled: false,
      lifecycle_state: "active",
      goal_ref: goalB,
    });
    assert.equal(acceptanceWorkGuard(migrated.head, goal, "todo_first"), null);
  });
  test(`${provider}: historical recreation replay does not reactivate a retiring successor`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    assert.equal((await lifecycleTransition(store, {
      kind: "reconcile_recreated",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    })).status, "applied");
    assert.equal((await lifecycleTransition(store, {
      kind: "retire",
      goal_ref: goalB,
    })).status, "applied");
    const replay = await lifecycleTransition(store, {
      kind: "reconcile_recreated",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    });
    assert.equal(replay.status, "no_change");
    assert.deepEqual(replay.goal_acceptance_lifecycle, {
      schema_version: "loopx_goal_acceptance_lifecycle_v0",
      state: "retiring",
      goal_ref: goalB,
    });
  });
  test(`${provider}: exact lifecycle migration fences retirement and same-alias replacement`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const legacyConfigure = await configureRequest(store);
    assert.equal((await configureGoalAcceptance(store, legacyConfigure)).status, "applied");
    const legacyVerification = await verifyRequest(store);
    assert.equal((await commitGoalAcceptanceVerification(store, legacyVerification)).status, "applied");

    const bindOperation = randomUUID();
    assert.equal((await lifecycleTransition(store, {kind: "bind_existing", goal_ref: goalA}, bindOperation)).status, "applied");
    const bound = await head(store);
    assert.deepEqual(bound.head.goal_acceptance_lifecycle, {
      schema_version: "loopx_goal_acceptance_lifecycle_v0",
      state: "active",
      goal_ref: goalA,
    });
    assert.deepEqual((bound.head.goal_acceptance as JsonObject).owner_goal_ref, goalA);
    assert.equal((bound.head.goal_acceptance as JsonObject).schema_version, "loopx_goal_acceptance_v1");
    assert.equal(acceptanceWorkGuard(bound.head, goal, "todo_first")?.allowed, true);
    assert.equal(projectGoalAcceptance(bound.head, goal).status, "accepted",
      "migration preserves A's verification as A history");
    await assert.rejects(inspectGoalAcceptance(store, goal), /requires goal_ref/);
    assert.equal((await inspectGoalAcceptance(store, goal, undefined, goalA)).status, "loaded");
    assert.equal((await configureGoalAcceptance(store, await configureRequest(store))).reason_code,
      "goal_acceptance_goal_instance_missing");
    const noChangeOperation = randomUUID();
    assert.equal((await lifecycleTransition(store,
      {kind: "bind_existing", goal_ref: goalA}, noChangeOperation)).status, "no_change");
    assert.equal((await store.readReceipt(noChangeOperation)).status, "found");
    assert.equal((await lifecycleTransition(store, {
      kind: "activate_successor",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    })).reason_code, "goal_acceptance_goal_instance_mismatch");

    const staleWriter = await configureRequest(store, {goal_ref: goalA});
    assert.equal((await lifecycleTransition(store, {kind: "retire", goal_ref: goalA})).status, "applied");
    const retiring = await head(store);
    assert.equal((retiring.head.goal_acceptance_lifecycle as JsonObject).state, "retiring");
    assert.equal(acceptanceWorkGuard(retiring.head, goal, "todo_first")?.allowed, false);
    assert.equal(acceptanceWorkGuard(retiring.head, goal, "todo_first")?.reason_code,
      "goal_acceptance_goal_retiring");
    assert.throws(() => acceptanceCompletionRequirements(retiring.head, goal, "todo_first"),
      /goal_acceptance_goal_retiring/);
    assert.equal((await configureGoalAcceptance(store, staleWriter)).status, "conflict");

    const retiringConfigure = await configureRequest(store, {goal_ref: goalA});
    assert.equal((await configureGoalAcceptance(store, retiringConfigure)).reason_code,
      "goal_acceptance_goal_retiring");
    const retiringVerification = {...legacyVerification, operation_id: randomUUID(), goal_ref: goalA,
      expected_provider_revision: retiringConfigure.expected_provider_revision};
    assert.equal((await commitGoalAcceptanceVerification(store, retiringVerification)).reason_code,
      "goal_acceptance_goal_retiring");
    const replayWhileRetiring = await configureGoalAcceptance(store, legacyConfigure);
    assert.equal(replayWhileRetiring.status, "replayed");
    assert.deepEqual(replayWhileRetiring.goal_acceptance_contract, {
      enabled: false,
      lifecycle_state: "retiring",
      goal_ref: goalA,
    });

    assert.equal((await lifecycleTransition(store, {
      kind: "activate_successor",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    })).status, "applied");
    const replaced = await head(store);
    assert.deepEqual(projectGoalAcceptance(replaced.head, goal), {
      enabled: false,
      lifecycle_state: "active",
      goal_ref: goalB,
    });
    assert.equal(acceptanceWorkGuard(replaced.head, goal, "todo_first"), null);
    const historicalReplay = await configureGoalAcceptance(store, legacyConfigure);
    assert.equal(historicalReplay.status, "replayed");
    assert.deepEqual(historicalReplay.goal_acceptance_contract, {
      enabled: false,
      lifecycle_state: "active",
      goal_ref: goalB,
    });
    const lifecycleReplay = await lifecycleTransition(store,
      {kind: "bind_existing", goal_ref: goalA}, bindOperation);
    assert.equal(lifecycleReplay.status, "replayed");
    assert.deepEqual(lifecycleReplay.goal_acceptance_lifecycle, {
      schema_version: "loopx_goal_acceptance_lifecycle_v0",
      state: "active",
      goal_ref: goalB,
    });
    assert.equal((await lifecycleTransition(store, {
      kind: "activate_successor",
      retired_goal_ref: goalA,
      goal_ref: goalB,
    })).reason_code, "goal_acceptance_goal_instance_mismatch",
    "only the original durable receipt may replay an already active successor");

    const staleInstance = await configureRequest(store, {goal_ref: goalA});
    assert.equal((await configureGoalAcceptance(store, staleInstance)).reason_code,
      "goal_acceptance_goal_instance_mismatch");
    const replacement = await configureRequest(store, {goal_ref: goalB});
    assert.equal((await configureGoalAcceptance(store, replacement)).status, "applied");
    const replacementHead = await head(store);
    assert.deepEqual((replacementHead.head.goal_acceptance as JsonObject).owner_goal_ref, goalB);
    assert.equal((replacementHead.head.goal_acceptance as JsonObject).revision, 1);
    assert.equal(acceptanceWorkGuard(replacementHead.head, goal, "todo_first")?.allowed, true);
  });
  test(`${provider}: selected work isolates admission and verification without weakening its own guard`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const doc = {...document(), scope: {kind: "selected_work", todo_ids: ["todo_first"]},
      bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}]};
    const request = await configureRequest(store, {document: doc});
    assert.equal((await configureGoalAcceptance(store, request)).status, "applied");
    const first = (await head(store)).head;
    assert.equal(acceptanceWorkGuard(first, goal, "todo_first")?.allowed, true);
    assert.equal(acceptanceWorkGuard(first, goal, "todo_second"), null);
    assert.equal(acceptanceCompletionRequirements(first, goal, "todo_second"), null);
    assert.deepEqual((projectGoalAcceptance(first, goal).tasks as JsonObject[]).map(row => row.todo_id), ["todo_first"]);
    assert.equal((await commitGoalAcceptanceVerification(store, await verifyRequest(store))).status, "applied");
    await update(store, "todo_second", {text: "Independent work changed"});
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "accepted");
    await update(store, "todo_new", todoFixtureRecord(todo("todo_new"), "native"));
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_new"), null);
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "accepted");
    await update(store, "todo_first", {task_class: "continuous_monitor"});
    const changed = (await head(store)).head;
    assert.equal(acceptanceWorkGuard(changed, goal, "todo_first")?.allowed, false);
    assert.throws(() => acceptanceCompletionRequirements(changed, goal, "todo_first"), /stale/);
    assert.equal(projectGoalAcceptance(changed, goal).status, "held");
    assert.equal((await configureGoalAcceptance(store, request)).status, "replayed", "old exact operation cannot rebind changed work");
  });
  test(`${provider}: explicit selection is not binding and cannot be edited away by the worker`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const doc = {...document(), scope: {kind: "selected_work", todo_ids: ["todo_first"]}, bindings: []};
    assert.equal((await configureGoalAcceptance(store, await configureRequest(store, {document: doc}))).status, "applied");
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first")?.state, "unbound");
    await update(store, "todo_first", {task_class: "continuous_monitor", status: "blocked"});
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first")?.allowed, false);
    const changed = (await head(store)).head;
    assert.throws(() => acceptanceCompletionRequirements(changed, goal, "todo_first"), /unbound/);
  });
  test(`${provider}: legacy scope-free committed configuration still recovers its exact receipt`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const raw = document(); delete raw.scope;
    const request = await configureRequest(store, {document: raw});
    const basis = await head(store);
    const doc = normalizeGoalAcceptanceDocument(raw);
    const state = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
      document: doc, digest: canonicalAuthoritySha256(doc), verification: null,
      bindings: doc.bindings.map(binding => ({...binding, revision: 1, confirmed_by: "owner",
        todo_semantic_digest: goalAcceptanceTodoDigest((basis.head.todos as JsonObject[]).find(row => row.todo_id === binding.todo_id)!)}))};
    const next = {...basis.head, goal_acceptance: state};
    const receipt = {schema_version: "loopx_goal_acceptance_operation_v0", operation_id: request.operation_id,
      goal_id: goal, request_sha256: canonicalAuthoritySha256({kind: "configure", ...request}),
      result: {goal_id: goal, operation_id: request.operation_id, goal_acceptance_contract: projectGoalAcceptance(next, goal)}};
    assert.equal((await store.commitAuthority({operation_id: String(request.operation_id),
      expected_provider_revision: basis.provider_revision, next_projection: next, events: [], receipts: [receipt]})).status, "applied");
    const committed = await head(store);
    assert.equal((await configureGoalAcceptance(store, request)).status, "replayed");
    assert.deepEqual(await head(store), committed);
  });
  test(`${provider}: owner configuration requires an explicit valid scope before any write`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const before = await head(store);
    const missing = document(); delete missing.scope;
    assert.equal((await configureGoalAcceptance(store, await configureRequest(store, {document: missing}))).reason_code,
      "goal_acceptance_scope_required");
    assert.deepEqual(await head(store), before);
    for (const doc of [
      {...document(), scope: {kind: "selected_work", todo_ids: []}},
      {...document(), scope: {kind: "selected_work", todo_ids: ["todo_first", "todo_first"]}},
      {...document(), scope: {kind: "all"}},
      {...document(), scope: {kind: "selected_work", todo_ids: ["todo_first"]}},
      {...document(), scope: {kind: "selected_work", todo_ids: ["todo_missing"]}, bindings: []}]) {
      await assert.rejects(configureGoalAcceptance(store, await configureRequest(store, {document: doc})), /scope/);
      assert.deepEqual(await head(store), before);
    }
    await assert.rejects(configureGoalAcceptance(store, await configureRequest(store, {
      document: {...document(), scope: {kind: "selected_work", todo_ids: ["todo_first", "todo_second"]}},
      actor_agent_id: "agent-a"})), /trusted owner/);
    assert.deepEqual(await head(store), before);
  });
  test(`${provider}: default off, owner configure, private inspection and public held work`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const before = await head(store);
    assert.deepEqual(projectGoalAcceptance(before.head, goal), {enabled: false});
    assert.equal(acceptanceWorkGuard(before.head, goal, "todo_first"), null);
    assert.equal(acceptanceCompletionRequirements(before.head, goal, "todo_first"), null);
    assert.deepEqual(await head(store), before);
    const doc = document(); doc.bindings = [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}];
    const request = await configureRequest(store, {document: doc});
    const result = await configureGoalAcceptance(store, request);
    assert.equal(result.status, "applied");
    assert.equal(result.source_authority, `${provider}_v0`);
    assert.equal(result.projection_delivery, "not_required");
    const after = await head(store);
    const {goal_acceptance, ...remaining} = after.head;
    assert.deepEqual(remaining, before.head, "Todo manifest, records, leases and adjacent contracts are untouched");
    assert.ok(goal_acceptance);
    const inspection = await inspectGoalAcceptance(store, goal);
    assert.equal(inspection.provider_revision, after.provider_revision);
    assert.equal(Object.hasOwn(inspection, "completion_requirements"), false, "ordinary inspection stays unchanged");
    const taskInspection = await inspectGoalAcceptance(store, goal, "todo_first");
    assert.deepEqual((taskInspection.completion_requirements as JsonObject).criterion_ids, ["prerequisite"]);
    await assert.rejects(inspectGoalAcceptance(store, goal, "todo_second"), /unbound/);
    assert.deepEqual(await head(store), after, "task-scoped planning has no provider write");
    assert.equal(((inspection.contract as JsonObject).criteria as JsonObject[])[0].validation_timeout_seconds, 5);
    assert.ok((inspection.tasks as JsonObject[]).every(task => typeof task.todo_semantic_digest === "string"));
    const projection = projectGoalAcceptance(after.head, goal);
    assert.equal(projection.status, "held");
    assert.deepEqual(projection.held_todo_ids, ["todo_second"]);
    assert.equal(acceptanceWorkGuard(after.head, goal, "todo_first")?.allowed, true);
    assert.equal(acceptanceWorkGuard(after.head, goal, "todo_second")?.allowed, false);
    for (const id of ["todo_gate", "todo_monitor", "todo_completed"]) assert.equal(acceptanceWorkGuard(after.head, goal, id), null);
    assert.equal(acceptanceWorkGuard(after.head, goal, "todo_missing")?.allowed, false);
    assert.ok(!JSON.stringify(projection).includes("validation_argv"));
    assert.ok(!JSON.stringify(result).includes("process.exit"));
    assert.throws(() => acceptanceCompletionRequirements(after.head, goal, "todo_second"), /unbound/);
  });

  test(`${provider}: missing CAS, owner violations, malformed criteria and unknown bindings have no effect`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const request = await configureRequest(store);
    const noCas = {...request}; delete noCas.expected_provider_revision;
    const noActor = {...request}; delete noActor.actor_agent_id;
    const invalidDocuments: JsonObject[] = [
      {...document(), objective: " "}, {...document(), criteria: []}, {...document(), accepted: true},
      {...document(), criteria: [{id: "a", description: "", validation_argv: ["check"]}]},
      {...document(), criteria: [{id: "a", description: "Check", validation_argv: []}]},
      {...document(), criteria: [{id: "a", description: "Check", validation_argv: [" "]}]},
      {...document(), criteria: [{id: "a", description: "Check", validation_argv: ["check"], validation_timeout_seconds: 30}]},
      {...document(), bindings: [{todo_id: "todo_first", criterion_ids: ["unknown"]}]},
      {...document(), bindings: [{todo_id: "todo_first", criterion_ids: ["outcome", "outcome"]}]},
      {...document(), bindings: [{todo_id: "todo_missing", criterion_ids: ["outcome"]}]},
      {...document(), bindings: [{todo_id: "todo_gate", criterion_ids: ["outcome"]}]},
    ];
    const before = await head(store);
    for (const invalid of [noCas, noActor, {...request, actor_agent_id: "agent"}, {...request, expected_provider_revision: null},
      {...request, operation_id: "x".repeat(257)},
      ...invalidDocuments.map(document => ({...request, document}))]) {
      await assert.rejects(() => configureGoalAcceptance(store, invalid));
      assert.deepEqual(await head(store), before);
    }
    assert.equal((await store.readReceipt(String(request.operation_id))).status, "missing");
    const stale = await configureGoalAcceptance(store, {...request, expected_provider_revision: "stale"});
    assert.equal(stale.status, "conflict");
    assert.deepEqual(await head(store), before);
  });

  test(`${provider}: CAS concurrency, exact retry, changed-content rejection, disable and versioned re-enable`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const first = await configureRequest(store), second = {...first, operation_id: randomUUID()};
    const results = await Promise.all([configureGoalAcceptance(store, first), configureGoalAcceptance(store, second)]);
    assert.deepEqual(results.map(result => result.status).sort(), ["applied", "conflict"]);
    const winner = results[0].status === "applied" ? first : second;
    const after = await head(store);
    assert.equal((await configureGoalAcceptance(store, winner)).status, "replayed");
    assert.equal((await configureGoalAcceptance(store, {...winner, document: {...document(), objective: "Changed"}})).reason_code,
      "coordination_operation_identity_mismatch");
    assert.deepEqual(await head(store), after);
    const disable = await configureRequest(store, {document: null, disable: true});
    await assert.rejects(() => configureGoalAcceptance(store, {...disable, actor_agent_id: "agent"}));
    assert.equal((await configureGoalAcceptance(store, disable)).status, "applied");
    assert.deepEqual(projectGoalAcceptance((await head(store)).head, goal), {enabled: false});
    assert.equal((await configureGoalAcceptance(store, winner)).status, "replayed");
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first"), null);
    assert.equal((await configureGoalAcceptance(store, await configureRequest(store))).status, "applied");
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).revision, 2);
    assert.equal((await store.readReceipt(String(winner.operation_id))).status, "found");
    assert.equal((await store.readReceipt(String(disable.operation_id))).status, "found");
  });

  test(`${provider}: declaration drift and new work are held; status, claims and display do not change binding`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    await update(store, "todo_first", {status: "blocked", done: false, claimed_by: "receiver", note: "Progress",
      title: "Display title", completed_at: null, completion_validation_sha256: "prior-receipt"});
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first")?.allowed, true);
    await update(store, "todo_first", {required_capabilities: ["write"]});
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first")?.state, "stale");
    await configureGoalAcceptance(store, await configureRequest(store));
    await update(store, "todo_first", {text: "Different actual work"});
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_first")?.state, "stale");
    const newTodo = (authorityProjectionFixture(goal, [todo("todo_new")]).todos as JsonObject[])[0];
    await update(store, "todo_new", newTodo);
    const projection = projectGoalAcceptance((await head(store)).head, goal);
    assert.deepEqual(projection.held_todo_ids, ["todo_first", "todo_new"]);
    assert.equal(acceptanceWorkGuard((await head(store)).head, goal, "todo_second")?.allowed, true);
  });

  test(`${provider}: verification exact coverage, failed checks, private metadata and no completion shortcut`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    const request = await verifyRequest(store);
    const before = await head(store);
    const invalid = [[], [{criterion_id: "outcome", passed: true, exit_code: 0}],
      [{criterion_id: "outcome", passed: true, exit_code: 1}, {criterion_id: "prerequisite", passed: true, exit_code: 0}],
      [{criterion_id: "outcome", passed: false, exit_code: 0}, {criterion_id: "prerequisite", passed: true, exit_code: 0}],
      [{criterion_id: "outcome", passed: false, exit_code: -9}, {criterion_id: "prerequisite", passed: true, exit_code: 0}],
      [{criterion_id: "outcome", passed: true, exit_code: 0}, {criterion_id: "outcome", passed: true, exit_code: 0}],
      [{criterion_id: "unknown", passed: true, exit_code: 0}, {criterion_id: "prerequisite", passed: true, exit_code: 0}]];
    for (const results of invalid) await assert.rejects(() => commitGoalAcceptanceVerification(store, {...request, results}));
    await assert.rejects(() => commitGoalAcceptanceVerification(store, {...request, actor_agent_id: "agent"}));
    await assert.rejects(() => commitGoalAcceptanceVerification(store, {...request, accepted: true}));
    assert.deepEqual(await head(store), before);
    const failure = await verifyRequest(store, {results: [
      {criterion_id: "outcome", passed: false, exit_code: null, status: "timeout", stdout_captured: false},
      {criterion_id: "prerequisite", passed: true, exit_code: 0}]});
    assert.equal((await commitGoalAcceptanceVerification(store, failure)).status, "applied");
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "failed");
    const success = await verifyRequest(store);
    (success.results as JsonObject[])[0].command_label = "private-execution-label";
    (success.results as JsonObject[])[0].summary = "private-runner-context";
    assert.equal((await commitGoalAcceptanceVerification(store, success)).status, "applied");
    const accepted = await head(store);
    assert.equal(projectGoalAcceptance(accepted.head, goal).status, "accepted");
    assert.deepEqual(accepted.head.todos, before.head.todos, "acceptance never marks Todos done or closes a Goal");
    assert.ok(!JSON.stringify(accepted.head).includes("private-execution-label"));
    assert.ok(!JSON.stringify(await store.readReceipt(String(success.operation_id))).includes("private-runner-context"));
    assert.equal((await commitGoalAcceptanceVerification(store, success)).status, "replayed");
    assert.equal((await commitGoalAcceptanceVerification(store, {...success, results: failure.results})).reason_code,
      "coordination_operation_identity_mismatch");
    assert.throws(() => validateAcceptanceCompletion(accepted.head, goal, "todo_first", {accepted: true}), /fields/);
    await update(store, "todo_second", {status: "blocked", done: false});
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "accepted");
    await update(store, "todo_first", {text: "New declaration"});
    assert.notEqual(projectGoalAcceptance((await head(store)).head, goal).status, "accepted");
  });

  test(`${provider}: stale verification after work/config changes and partial checks cannot accept the whole contract`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    const staleWork = await verifyRequest(store);
    await update(store, "todo_first", {text: "Changed while validators ran"});
    assert.equal((await commitGoalAcceptanceVerification(store, staleWork)).status, "conflict");
    assert.equal((await store.readReceipt(String(staleWork.operation_id))).status, "missing");
    await configureGoalAcceptance(store, await configureRequest(store));
    assert.equal((await commitGoalAcceptanceVerification(store, {...staleWork,
      expected_provider_revision: (await head(store)).provider_revision})).reason_code, "goal_acceptance_contract_stale");
    const partial = await verifyRequest(store, {todo_id: "todo_first", results: [{criterion_id: "prerequisite", passed: true, exit_code: 0}]});
    assert.equal((await commitGoalAcceptanceVerification(store, partial)).status, "applied");
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "partial");
    await commitGoalAcceptanceVerification(store, await verifyRequest(store));
    const successful = await head(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    assert.equal(projectGoalAcceptance((await head(store)).head, goal).status, "stale");
    assert.deepEqual(((await head(store)).head.goal_acceptance as JsonObject).verification,
      (successful.head.goal_acceptance as JsonObject).verification, "historical execution evidence is retained");
  });

  test(`${provider}: persisted verification validates its complete basis and returns only compact results`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    await commitGoalAcceptanceVerification(store, await verifyRequest(store));
    const accepted = (await head(store)).head;
    const state = accepted.goal_acceptance as JsonObject;
    const original = state.verification as JsonObject;
    const corrupt = (receipt: JsonObject) => ({...accepted, goal_acceptance: {...state, verification: receipt}});
    for (const field of ["operation_id", "contract_revision", "contract_digest", "work_digest", "todo_id", "results"]) {
      const missing = {...original}; delete missing[field];
      assert.throws(() => readGoalAcceptance(corrupt(missing), goal), /fields/);
    }
    for (const patch of [{operation_id: " op "}, {contract_revision: "1"}, {contract_revision: 2},
      {contract_digest: "0".repeat(64)}, {work_digest: 3}, {todo_id: ""}, {todo_id: "todo_unknown"},
      {results: [{criterion_id: "outcome", passed: false, exit_code: 1}]},
      {todo_id: "todo_first", results: [{criterion_id: "outcome", passed: true, exit_code: 0}]}]) {
      assert.throws(() => readGoalAcceptance(corrupt({...original, ...patch}), goal));
    }
    const metadata = {...original, results: (original.results as JsonObject[]).map(row =>
      ({...row, command_label: "private-receipt-label", summary: "private-receipt-context"}))};
    assert.ok(!JSON.stringify(projectGoalAcceptance(corrupt(metadata), goal)).includes("private-receipt"));
    assert.equal(projectGoalAcceptance(corrupt({...original, work_digest: "0".repeat(64)}), goal).status, "stale");
    assert.equal(projectGoalAcceptance(accepted, goal).status, "accepted");
  });

  test(`${provider}: fresh prerequisite execution is bound to atomic completion; final criterion is not inferred`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    await configureGoalAcceptance(store, await configureRequest(store));
    const basis = await head(store);
    const requirements = acceptanceCompletionRequirements(basis.head, goal, "todo_first")!;
    assert.deepEqual(requirements.criterion_ids, ["prerequisite"]);
    const results = requirements.criteria.map(criterion => {
      const execution = spawnSync(criterion.validation_argv[0], criterion.validation_argv.slice(1),
        {stdio: "ignore", timeout: criterion.validation_timeout_seconds * 1000});
      return {criterion_id: criterion.id, passed: execution.status === 0, exit_code: execution.status};
    });
    const evidence = {contract_revision: requirements.contract_revision, contract_digest: requirements.contract_digest,
      todo_id: requirements.todo_id, todo_semantic_digest: requirements.todo_semantic_digest, results};
    const receipt = validateAcceptanceCompletion(basis.head, goal, "todo_first", evidence)!;
    assert.ok(!JSON.stringify(receipt).includes("validation_argv"));
    assert.throws(() => validateAcceptanceCompletion(basis.head, goal, "todo_first", {...evidence,
      results: [{criterion_id: "prerequisite", passed: false, exit_code: 1}]}), /failed/);
    assert.throws(() => validateAcceptanceCompletion(basis.head, goal, "todo_second", evidence), /basis changed/);
    const target = (basis.head.todos as JsonObject[]).find(item => item.todo_id === "todo_first")!;
    const completion = prepareCoordinationProjectionCommit({goal_id: goal, operation_id: "complete-prerequisite",
      expected_provider_revision: basis.provider_revision, projection: basis.head,
      mutations: [{kind: "todo_upsert", todo: {...target, status: "done", done: true}}]});
    completion.receipts = [receipt];
    assert.equal((await store.commitAuthority(completion)).status, "applied");
    const completed = await head(store);
    assert.equal((completed.head.todos as JsonObject[]).find(item => item.todo_id === "todo_first")!.done, true);
    assert.deepEqual((await store.readReceipt("complete-prerequisite")).status, "found");
    assert.equal(projectGoalAcceptance(completed.head, goal).status, "unverified");
    assert.equal((await store.commitAuthority({...completion, operation_id: "stale-completion"})).status, "conflict");
    const secondBasis = acceptanceCompletionRequirements(completed.head, goal, "todo_second")!;
    await update(store, "todo_second", {required_write_scopes: ["src"]});
    assert.throws(() => validateAcceptanceCompletion((completed.head), goal, "todo_second", {...evidence,
      todo_id: "todo_second", todo_semantic_digest: secondBasis.todo_semantic_digest}), /exactly/);
    const changed = await head(store);
    assert.throws(() => validateAcceptanceCompletion(changed.head, goal, "todo_second", evidence), /stale/);
  });

  test(`${provider}: lost commit response recovers immutable operation; dry-run changes no provider facts`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const request = await configureRequest(store);
    const before = await head(store);
    assert.equal((await configureGoalAcceptance(store, {...request, dry_run: true})).status, "planned");
    assert.deepEqual(await head(store), before);
    const commit = store.commitAuthority.bind(store);
    store.commitAuthority = async input => {await commit(input); throw new Error("response lost after durable commit");};
    assert.equal((await configureGoalAcceptance(store, request)).status, "recovered");
    store.commitAuthority = commit;
    assert.equal((await configureGoalAcceptance(store, request)).status, "replayed");
  });

  test(`${provider}: file pins bind trusted criteria and contract revisions without entering public projection`, options, async t => {
    const store = await fixture(t, provider); await seed(store);
    const doc = document();
    const criterion = (doc.criteria as JsonObject[])[0];
    criterion.validation_files = [{path: "checks/verify.py", sha256: "a".repeat(64)}];
    await configureGoalAcceptance(store, await configureRequest(store, {document: doc}));
    const current = await head(store);
    const requirements = acceptanceCompletionRequirements(current.head, goal, "todo_first")!;
    assert.deepEqual(requirements.criteria[0].validation_files, criterion.validation_files);
    assert.ok(!JSON.stringify(projectGoalAcceptance(current.head, goal)).includes("checks/verify.py"));
    const tampered = structuredClone(current.head);
    const tamperedDocument = (tampered.goal_acceptance as JsonObject).document as JsonObject;
    (tamperedDocument.criteria as JsonObject[]).find(item => item.id === "prerequisite")!.validation_files =
      [{path: "checks/verify.py", sha256: "b".repeat(64)}];
    assert.throws(() => readGoalAcceptance(tampered, goal), /contract digest mismatch/);
    await commitGoalAcceptanceVerification(store, await verifyRequest(store));
    const verified = await head(store);
    criterion.validation_files = [{path: "checks/verify.py", sha256: "b".repeat(64)}];
    await configureGoalAcceptance(store, await configureRequest(store, {document: doc}));
    const changed = projectGoalAcceptance((await head(store)).head, goal);
    assert.notEqual(changed.digest, requirements.contract_digest);
    assert.equal(changed.revision, requirements.contract_revision + 1);
    assert.equal(changed.status, "stale");
    assert.deepEqual(((await head(store)).head.goal_acceptance as JsonObject).verification,
      (verified.head.goal_acceptance as JsonObject).verification);
  });

}

test("file pins are bounded, safe relative paths with exact hashes and deterministic ordering", () => {
  const normalize = (validation_files: unknown) => normalizeGoalAcceptanceDocument({...document(),
    criteria: (document().criteria as JsonObject[]).map(item => ({...item, validation_files}))});
  assert.ok(normalizeGoalAcceptanceDocument(document()).criteria.every(item => item.validation_files.length === 0));
  const files = [{path: "checks/z.py", sha256: "a".repeat(64)}, {path: ".checks/a.py", sha256: "b".repeat(64)}];
  assert.deepEqual(normalize(files), normalize([...files].reverse()));
  assert.deepEqual(normalize([{path: "checks/a.py", sha256: "A".repeat(64)}]),
    normalize([{path: "checks/a.py", sha256: "a".repeat(64)}]));
  for (const path of ["", "/verify.py", "../verify.py", "checks/../verify.py", "./verify.py", "checks/./verify.py",
    "checks//verify.py", "checks/", "C:/verify.py", "C:\\verify.py", "checks\\verify.py", "verify.py\n", "checks/a b.py", "a".repeat(1025)]) {
    assert.throws(() => normalize([{path, sha256: "a".repeat(64)}]), /path/);
  }
  for (const files of [null, {}, [{path: "verify.py", sha256: "a".repeat(63)}],
    [{path: "verify.py", sha256: "g".repeat(64)}], [{path: "verify.py", sha256: "a".repeat(64), extra: true}],
    [{path: "verify.py", sha256: "a".repeat(64)}, {path: "verify.py", sha256: "b".repeat(64)}],
    Array.from({length: 17}, (_, index) => ({path: `check-${index}.py`, sha256: "a".repeat(64)}))]) {
    assert.throws(() => normalize(files));
  }
});

test("normalization is deterministic and digest includes work declarations but excludes observations", () => {
  const first = normalizeGoalAcceptanceDocument(document());
  const second = normalizeGoalAcceptanceDocument({...document(), criteria: [...(document().criteria as JsonObject[])].reverse(),
    bindings: [...(document().bindings as JsonObject[])].reverse()});
  assert.deepEqual(first, second);
  assert.throws(() => normalizeGoalAcceptanceDocument({...document(),
    criteria: (document().criteria as JsonObject[]).map(item => ({...item, validation_timeout_seconds: 13}))}), /total at most 25 seconds/);
  const original = todo("todo_first");
  for (const patch of [{status: "done", done: true}, {claimed_by: "agent"}, {index: 3, source_section: "Done"},
    {completion_validation_sha256: "receipt", completion_turn_key: "turn"}]) {
    assert.equal(goalAcceptanceTodoDigest({...original, ...patch}), goalAcceptanceTodoDigest(original));
  }
  for (const patch of [{text: "Other work"}, {task_repository: "git:example.test/repository"},
    {required_capabilities: ["write"]}, {required_write_scopes: ["src"]}, {action_kind: "review"}, {future_work_declaration: true}]) {
    assert.notEqual(goalAcceptanceTodoDigest({...original, ...patch}), goalAcceptanceTodoDigest(original));
  }
  assert.throws(() => projectGoalAcceptance({...originalHead(), goal_acceptance: null}, goal), /object/);
  assert.deepEqual(projectGoalAcceptance({}, goal), {enabled: false}, "feature off preserves legacy callers without a read model");
});

test("work fingerprints ignore row permutations without weakening canonical read-model validation", () => {
  const original = originalHead();
  const rows = original.todos as JsonObject[];
  const permutations = [[...rows].reverse(), [...rows.slice(2), ...rows.slice(0, 2)]];
  const digest = goalAcceptanceWorkDigest(original, goal);
  for (const todos of permutations) {
    assert.equal(goalAcceptanceWorkDigest({...original, todos}, goal), digest);
    assert.equal(goalAcceptanceWorkDigest(authorityProjectionFixture(goal, todos, [], "legacy"), goal), digest);
    assert.throws(() => validateCoordinationTodoReadModel({...original, todos}, goal), /deterministic todo_id order/);
  }
  assert.throws(() => goalAcceptanceWorkDigest({...original, todos: [...rows, rows[0]]}, goal), /duplicate/);
  assert.throws(() => goalAcceptanceWorkDigest(original, "different-goal"), /goal mismatch/);
});

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: local exported effects use selected store, writer gate and private inspection`,
    {skip: provider === "sqlite" && !sqliteQualified}, async t => {
    const root = await mkdtemp(join(tmpdir(), "goal-acceptance-local-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal); await seed(store);
    const before = await inspectLocalGoalAcceptance({runtime_root: root, goal_id: goal});
    assert.equal(before.status, "loaded");
    assert.deepEqual(before.goal_acceptance_contract, {enabled: false});
    const request = {...await configureRequest(store), runtime_root: root};
    assert.equal((await commitLocalGoalAcceptance(request)).status, "applied");
    const inspect = await inspectLocalGoalAcceptance({runtime_root: root, goal_id: goal, todo_id: "todo_first"});
    assert.equal(inspect.source_authority, `${provider}_v0`);
    assert.equal((inspect.tasks as JsonObject[]).length, 1);
    assert.equal((inspect.todo as JsonObject).todo_id, "todo_first");
    const heldDoc = {...document(), bindings: [{todo_id: "todo_first", criterion_ids: ["prerequisite"]}]};
    assert.equal((await commitLocalGoalAcceptance({...await configureRequest(store, {document: heldDoc}), runtime_root: root})).status, "applied");
    assert.equal((await inspectLocalGoalAcceptance({runtime_root: root, goal_id: goal, todo_id: "todo_second"})).reason_code,
      "goal_acceptance_unbound", "canonical task diagnosis survives the private effect adapter");
    await update(store, "todo_first", {text: "Changed owner-bound work"});
    assert.equal((await inspectLocalGoalAcceptance({runtime_root: root, goal_id: goal, todo_id: "todo_first"})).reason_code,
      "goal_acceptance_stale");
    assert.equal((await commitLocalGoalAcceptance({...await configureRequest(store), runtime_root: root})).status, "applied");
    const verified = await commitLocalGoalAcceptanceVerification({...await verifyRequest(store), runtime_root: root});
    assert.equal((verified.goal_acceptance_contract as JsonObject).status, "accepted");
    assert.equal((await commitLocalGoalAcceptanceLifecycleTransition({
      runtime_root: root,
      goal_id: goal,
      actor_agent_id: null,
      operation_id: randomUUID(),
      transition: {kind: "bind_existing", goal_ref: goalA},
    })).status, "applied");
    assert.equal((await inspectLocalGoalAcceptance({
      runtime_root: root,
      goal_id: goal,
      goal_ref: goalA,
    })).status, "loaded");
    assert.equal((await loadLegacyCoordinationWriterFence(root, goal)).status, "missing", "acceptance never promotes a provider");
    // An unreadable maintenance state is not permission to bypass the writer.
    await writeFile(shadowManagementStatePath(root, goal), "invalid maintenance state");
    const blocked = await commitLocalGoalAcceptance({...await configureRequest(store), runtime_root: root});
    assert.equal(blocked.status, "failed");
    assert.equal((await inspectLocalGoalAcceptance({runtime_root: root, goal_id: goal, goal_ref: goalA})).status, "loaded");
  });
}

test("legacy omitted scope preserves document bytes, bindings and global admission", () => {
  const raw = document(); delete raw.scope;
  const doc = normalizeGoalAcceptanceDocument(raw);
  assert.equal(Object.hasOwn(doc, "scope"), false);
  const records = [todo("todo_first"), todo("todo_second"), todo("todo_new")];
  const state = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
    document: doc, digest: canonicalAuthoritySha256(doc), verification: null,
    bindings: doc.bindings.map(binding => ({...binding, revision: 1, confirmed_by: "owner",
      todo_semantic_digest: goalAcceptanceTodoDigest(records.find(row => row.todo_id === binding.todo_id)!)}))};
  const value = authorityProjectionFixture(goal, records, [], "native", {goal_acceptance: state});
  assert.deepEqual(readGoalAcceptance(value, goal), state);
  assert.equal(acceptanceWorkGuard(value, goal, "todo_first")?.allowed, true);
  assert.equal(acceptanceWorkGuard(value, goal, "todo_new")?.state, "unbound");
});

for (const provider of providers) {
  test(`${provider}: exact stale-declaration restoration preserves owner authority and lease lineage`,
    {skip: provider === "postgresql" && !process.env.LOOPX_TEST_POSTGRES_URL || provider === "sqlite" && !sqliteQualified}, async t => {
      const {executeCoordinationTodoUpdate} = await import("../../loopx/control_plane/coordination/todo_update.ts");
      const store = await fixture(t, provider);
      const wait = "resume_at:2020-01-01T00:00:00Z";
      const original = todo("todo_first", {claimed_by: "agent-a", resume_when: wait});
      const lease = {todo_id: "todo_first", goal_id: goal, owner: "agent-a", status: "released",
        idempotency_key: "previous-execution", version: 1, lease_epoch: 1,
        expires_at: "2020-01-01T00:00:00Z", write_scopes: []};
      await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, events: [], receipts: [],
        next_projection: authorityProjectionFixture(goal, [original], [lease], "native", {handoff_mode: "hard_lease"})});
      await configureGoalAcceptance(store, await configureRequest(store, {document: {...document(),
        bindings: [{todo_id: "todo_first", criterion_ids: ["outcome"]}]}}));
      const confirmed = (await head(store)).head.goal_acceptance;
      const current = await head(store);
      const changedTodo = {...(current.head.todos as JsonObject[])[0]}; delete changedTodo.resume_when;
      await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal, operation_id: "prior-unintended-edit",
        expected_provider_revision: current.provider_revision, projection: current.head,
        mutations: [{kind: "todo_upsert", todo: changedTodo, clear_fields: ["resume_when"]}]}));
      const stale = await head(store);
      assert.equal(acceptanceWorkGuard(stale.head, goal, "todo_first")?.state, "stale");
      const request = {goal_id: goal, todo_id: "todo_first", expected_role: "agent", actor_agent_id: "agent-a",
        registered_agents: ["agent-a", "agent-b"], operation_id: "restore-exact-declaration",
        expected_provider_revision: stale.provider_revision, patch: {}, clear_fields: [],
        planning_intent: {resume_when: wait}, dry_run: false, now: new Date("2030-01-01T00:00:00Z")};
      for (const change of [
        {actor_agent_id: "agent-b"}, {expected_provider_revision: undefined},
        {expected_provider_revision: "stale"}, {planning_intent: {resume_when: "resume_at:2021-01-01T00:00:00Z"}},
        {patch: {text: "Different scope"}}, {planning_intent: {resume_when: wait, status: "done"}},
        {lease_idempotency_key: "previous-execution", lease_expected_version: 1},
      ]) {
        const refused = await executeCoordinationTodoUpdate(store, {...request, ...change});
        assert.equal(refused.status, "failed", JSON.stringify(refused));
        assert.deepEqual(await head(store), stale);
      }
      // Exercise the full admission path against rejected current facts too.
      for (const [name, patch, activeLease] of [
        ["excluded", {excluded_agents: ["agent-a"]}, false],
        ["active-lease", {}, true],
      ] as const) {
        const basis = await head(store);
        await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal, operation_id: `facts-${name}`,
          expected_provider_revision: basis.provider_revision, projection: basis.head,
          mutations: [{kind: "todo_upsert", todo: {...changedTodo, ...patch}},
            {kind: "lease_upsert", lease: activeLease ? {...lease, status: "active", expires_at: "2031-01-01T00:00:00Z"} : lease}]}));
        const held = await head(store);
        const denied = await executeCoordinationTodoUpdate(store, {...request,
          expected_provider_revision: held.provider_revision});
        assert.equal(denied.status, "failed", name);
        assert.deepEqual(await head(store), held);
        await store.commitAuthority({operation_id: `reset-${name}`, expected_provider_revision: held.provider_revision,
          next_projection: stale.head, events: [], receipts: []});
      }
      request.expected_provider_revision = (await head(store)).provider_revision;
      const readyToRestore = await head(store);
      assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
      assert.deepEqual(await head(store), readyToRestore);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
      const restored = await head(store);
      assert.equal(acceptanceWorkGuard(restored.head, goal, "todo_first")?.state, "ready");
      assert.deepEqual(restored.head.goal_acceptance, confirmed);
      assert.deepEqual(restored.head.leases, stale.head.leases);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
      assert.deepEqual(await head(store), restored);
      assert.equal((await executeCoordinationTodoUpdate(store, {...request, patch: {text: "Changed replay"}})).status, "failed");
      assert.deepEqual(await head(store), restored);
    });
}
