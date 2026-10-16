import {compileActionReviewPlan} from "../../loopx/control_plane/presentation/action_review_plan.ts";
import assert from "node:assert/strict";
import {randomUUID} from "node:crypto";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test, {type TestContext} from "node:test";
import {Pool} from "pg";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {HandoffMode} from "../../loopx/control_plane/coordination/handoff_mode_vocabulary.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {PostgreSqlAuthorityStore, installPostgreSqlAuthorityStoreSchema} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {acceptanceWorkGuard, goalAcceptanceTodoDigest, normalizeGoalAcceptanceDocument, projectGoalAcceptance,
  projectGoalAcceptanceWorkGuards} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {executeCanonicalTaskLeaseAcquire} from "../../loopx/control_plane/coordination/task_lease_acquire.ts";
import {executeCoordinationTodoTerminalLifecycle, type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {listLocalCoordinationTodos, readLocalCoordinationTodo} from "../../loopx/control_plane/coordination/local_authority_read.ts";

const now = new Date("2026-09-17T12:00:00Z");
const todo = (extra: JsonObject = {}): JsonObject => ({schema_version: "todo_domain_record_v0",
  todo_id: "todo_work", role: "agent", status: "open", done: false, text: "Deliver the configured result",
  task_class: "advancement_task", archive_state: "active", ...extra});
function acceptance(records: JsonObject[], bound = true): JsonObject {
  const document = normalizeGoalAcceptanceDocument({objective: "Deliver a verified result", non_goals: [],
    criteria: [{id: "criterion-a", description: "The configured check succeeds", validation_argv: ["true"], validation_timeout_seconds: 10}],
    bindings: bound ? [{todo_id: "todo_work", criterion_ids: ["criterion-a"]}] : []});
  return {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
    digest: canonicalAuthoritySha256(document), document, verification: null,
    bindings: bound ? [{todo_id: "todo_work", todo_semantic_digest: goalAcceptanceTodoDigest(records[0]!),
      revision: 1, criterion_ids: ["criterion-a"], confirmed_by: "owner"}] : []};
}
function projection(records: JsonObject[], state?: JsonObject): JsonObject {
  records = [...records].sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
  return {goal_id: "goal-a", handoff_mode: "soft_claim", todos: records, leases: [],
    todo_read_model: coordinationTodoReadModel(records, "loopx_todo_domain_read_record_v0"),
    unrelated_extension: {value: "retained"}, ...(state ? {goal_acceptance: state} : {})};
}
const claim = {goal_id: "goal-a", todo_id: "todo_work", claimed_by: "agent-a", actor_agent_id: "agent-a",
  expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "claim", dry_run: false, now};
const acquire = {goal_id: "goal-a", todo_id: "todo_work", owner: "agent-a", idempotency_key: "execution-a",
  expected_version: null, ttl_seconds: 60, write_scopes: [], registered_agents: ["agent-a"], now};
const terminal: CoordinationTodoTerminalLifecycleInput = {goal_id: "goal-a", todo_id: "todo_work", expected_role: "agent",
  command: "complete", actor_agent_id: "agent-a", registered_agents: ["agent-a"], lifecycle_grants: [],
  authority_reason: null, decision_outcome: null, operation_identity: {kind: "explicit" as const, operation_id: "complete"}, lease_idempotency_key: null,
  lease_expected_version: null, allow_user_gate_auto_acquire: false, requested_no_followup: true,
  requested_completion_turn_key: null, requested_completion_identity_source: null, linked_successor_todo_ids: [],
  successor_intents: [], note: null, evidence: null, reason: null, clear_claim: false,
  validation_declaration: null, validation_receipt: null, completion_policy_request: null, dry_run: false, now};
const runnerReceipt = (label = "criterion-a", passed = true): JsonObject => ({schema_version: "issue_fix_validation_command_v0",
  command_label: label, passed, exit_code: passed ? 0 : 1, stdout_captured: false,
  stderr_captured: false, local_path_captured: false});
async function loaded(store: AuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("missing synthetic head");
  return head;
}
async function replace(store: AuthorityStore, head: JsonObject, operation_id: string) {
  const current = await loaded(store);
  assert.equal((await store.commitAuthority({operation_id, expected_provider_revision: current.provider_revision,
    next_projection: head, events: [], receipts: []})).status, "applied");
}
async function seeded(t: TestContext, provider: string, state: "bound" | "unbound" | "off", extra: JsonObject = {}, mode: HandoffMode = "soft_claim") {
  const root = await mkdtemp(join(tmpdir(), "loopx-acceptance-runtime-"));
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
    store = new PostgreSqlAuthorityStore(database, {tenant_id: `acceptance-${randomUUID()}`, goal_id: "goal-a"});
  } else store = new FileAuthorityStore(join(root, "authority", "file-v0"), "goal-a");
  const records = [todo(extra)];
  const head = {...projection(records, state === "off" ? undefined : acceptance(records, state === "bound")),
    handoff_mode: mode};
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: head, events: [], receipts: []})).status, "applied");
  return {store, root, head};
}

for (const provider of ["file", ...(process.env.LOOPX_TEST_POSTGRES_URL ? ["postgresql"] : [])]) {
  test(`${provider}: scoped probe permits an independent claim/lease but preserves ordinary completion validation`, async t => {
    const {store, head} = await seeded(t, provider, "unbound");
    const declaration = {validation_command: null, validation_command_argv: ["true"],
      validation_label: "ordinary-check", validation_timeout_seconds: 5};
    const records = [todo({completion_validation_required: true,
      completion_validation_sha256: canonicalAuthoritySha256(declaration)}), todo({todo_id: "todo_probe"})];
    await replace(store, projection(records, head.goal_acceptance as JsonObject), "add-probe");
    const basis = await loaded(store);
    const doc = {...(head.goal_acceptance as JsonObject).document as JsonObject,
      scope: {kind: "selected_work", todo_ids: ["todo_probe"]}};
    assert.equal((await configureGoalAcceptance(store, {goal_id: "goal-a", operation_id: "scope-correction",
      actor_agent_id: null, expected_provider_revision: basis.provider_revision, document: doc})).status, "applied");
    assert.equal((await executeCoordinationTodoClaim(store, claim)).status, "applied");
    const plan = await executeCoordinationTodoTerminalLifecycle(store, {...terminal, validation_declaration: declaration});
    assert.equal(plan.status, "execute_validation");
    assert.equal((plan.validation_effect as JsonObject).validation_label, "ordinary-check");
    assert.deepEqual(plan.goal_acceptance_validation_effects ?? [], []);
    // Lease admission is exercised with hard_lease; soft_claim intentionally has no lease grant.
    const current = await loaded(store);
    await replace(store, {...current.head, handoff_mode: "hard_lease"}, "require-leased-execution");
    assert.equal((await executeCanonicalTaskLeaseAcquire(store, acquire)).status, "applied");
    assert.equal(acceptanceWorkGuard((await loaded(store)).head, "goal-a", "todo_probe")?.allowed, false);
  });
  test(`${provider}: absent acceptance preserves ordinary completion, receipts, and unrelated fields`, async t => {
    const {store, head} = await seeded(t, provider, "off");
    const result = await executeCoordinationTodoTerminalLifecycle(store, terminal);
    assert.equal(result.status, "applied");
    assert.equal(Object.hasOwn(result, "goal_acceptance_completion"), false);
    const after = await loaded(store);
    assert.deepEqual(after.head.unrelated_extension, head.unrelated_extension);
    assert.equal(Object.hasOwn(after.head, "goal_acceptance"), false);
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, terminal)).status, "replayed");
  });

  test(`${provider}: unbound work rejects claim/lease/completion with actor precedence and no writes`, async t => {
    const {store} = await seeded(t, provider, "unbound");
    const before = await loaded(store);
    assert.equal((await executeCoordinationTodoClaim(store, {...claim, actor_agent_id: "intruder"})).reason_code, "actor_not_registered");
    assert.equal((await executeCoordinationTodoClaim(store, claim)).reason_code, "goal_acceptance_unbound");
    const leased = await seeded(t, provider, "unbound", {}, "hard_lease");
    const leaseBefore = await loaded(leased.store);
    assert.equal((await executeCanonicalTaskLeaseAcquire(leased.store, {...acquire, owner: "intruder"})).reason_code, "owner_not_registered");
    assert.equal((await executeCanonicalTaskLeaseAcquire(leased.store, acquire)).reason_code, "goal_acceptance_unbound");
    assert.deepEqual(await loaded(leased.store), leaseBefore);
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, terminal)).reason_code, "goal_acceptance_unbound");
    assert.deepEqual(await loaded(store), before);
    assert.equal((await store.readReceipt("complete")).status, "missing");
  });

  test(`${provider}: semantic edits stay visible, invalidate claims, and preserve head extensions`, async t => {
    const {store, head} = await seeded(t, provider, "bound");
    assert.equal((await executeCoordinationTodoClaim(store, claim)).status, "applied");
    assert.equal((await executeCoordinationTodoUpdate(store, {goal_id: "goal-a", todo_id: "todo_work", expected_role: "agent",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], operation_id: "edit", patch: {text: "Changed work"},
      clear_fields: [], dry_run: false, now})).status, "applied");
    const after = await loaded(store);
    assert.deepEqual(after.head.goal_acceptance, head.goal_acceptance);
    assert.deepEqual(after.head.unrelated_extension, head.unrelated_extension);
    assert.equal(acceptanceWorkGuard(after.head, "goal-a", "todo_work")?.reason_code, "goal_acceptance_stale");
    assert.equal((await executeCoordinationTodoClaim(store, claim)).reason_code, "goal_acceptance_stale", "old claim receipt cannot authorize stale work");
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, terminal)).reason_code, "goal_acceptance_stale");
  });

  test(`${provider}: a real validator revision leaves the bound Todo claimable`, async t => {
    const previous = {validation_command: null, validation_command_argv: ["node", "old-check.mjs"],
      validation_label: "focused check", validation_timeout_seconds: 10};
    const replacement = {...previous, validation_command_argv: ["node", "new-check.mjs"]};
    const {store} = await seeded(t, provider, "bound", {completion_validation_required: true,
      completion_validation_sha256: canonicalAuthoritySha256(previous),
      completion_validation_revision: 0, completion_validation_revision_history: []});
    const before = await loaded(store);
    const changed = await executeCoordinationTodoUpdate(store, {goal_id: "goal-a", todo_id: "todo_work",
      expected_role: "agent", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
      operation_id: "revise-validator", patch: {}, clear_fields: [], dry_run: false, now,
      expected_provider_revision: before.provider_revision,
      completion_validation_revision: {schema_version: "loopx_todo_completion_validation_revision_v0",
        expected_declaration_sha256: canonicalAuthoritySha256(previous), declaration: replacement}});
    assert.equal(changed.status, "applied");
    assert.equal(acceptanceWorkGuard((await loaded(store)).head, "goal-a", "todo_work")?.state, "ready");
    assert.equal((await executeCoordinationTodoClaim(store, claim)).status, "applied");
  });

  test(`${provider}: acquisition replay checks current acceptance after current lease proof`, async t => {
    const {store} = await seeded(t, provider, "bound", {}, "hard_lease");
    assert.equal((await executeCanonicalTaskLeaseAcquire(store, acquire)).status, "applied");
    const current = await loaded(store);
    await replace(store, {...current.head, goal_acceptance: acceptance(current.head.todos as JsonObject[], false)}, "unbind");
    assert.equal((await executeCanonicalTaskLeaseAcquire(store, acquire)).reason_code, "goal_acceptance_unbound");
    assert.equal((await executeCanonicalTaskLeaseAcquire(store, {...acquire, expected_version: 999, idempotency_key: "another"})).reason_code, "version_mismatch");
  });

  test(`${provider}: completion runs fresh effects and rejects forged, failed, stale, or incomplete receipts`, async t => {
    const {store} = await seeded(t, provider, "bound");
    const before = await loaded(store);
    const plan = await executeCoordinationTodoTerminalLifecycle(store, terminal);
    assert.equal(plan.status, "execute_validation");
    assert.equal(plan.validation_effect, null);
    assert.deepEqual(plan.goal_acceptance_validation_effects, [{criterion_id: "criterion-a", effect: {
      kind: "caller_validation", validation_command: null, validation_argv: ["true"], validation_label: "criterion-a",
      validation_timeout_seconds: 10, validation_files: [], task_repository: null}}]);
    const binding = plan.goal_acceptance_source_binding as JsonObject;
    const attempt = {...terminal, goal_acceptance_source_binding: binding};
    for (const receipts of [[{criterion_id: "criterion-a", receipt: {passed: true}}], [],
      [{criterion_id: "criterion-a", receipt: runnerReceipt("criterion-a", false)}],
      [{criterion_id: "unconfigured", receipt: runnerReceipt("unconfigured")}],
      [{criterion_id: "criterion-a", receipt: runnerReceipt()}, {criterion_id: "criterion-a", receipt: runnerReceipt()}]]) {
      assert.equal((await executeCoordinationTodoTerminalLifecycle(store, {...attempt,
        goal_acceptance_validation_receipts: receipts})).reason_code, "goal_acceptance_validation_rejected");
    }
    const dirty = await executeCoordinationTodoTerminalLifecycle(store, {...attempt,
      goal_acceptance_validation_receipts: [{criterion_id: "criterion-a", receipt: {
        ...runnerReceipt("criterion-a", false), exit_code: null, status: "workspace_dirty",
        summary: "private path /private/sensitive/worktree must never be projected",
      }}]});
    assert.equal(dirty.reason_code, "goal_acceptance_validation_rejected");
    assert.deepEqual(dirty.goal_acceptance_validation_failure, {
      schema_version: "goal_acceptance_validation_failure_v0", criterion_id: "criterion-a",
      validation_status: "workspace_dirty", exit_code: null,
      next_action: "Preserve unrelated Git-visible files in ignored private storage or outside the worktree, then retry completion from that clean worktree with the same Turn identity.",
    });
    assert.match(String(dirty.reason), /workspace_dirty.*clean worktree/);
    assert.doesNotMatch(JSON.stringify(dirty), /private\/sensitive|validation_argv/);
    const good = {...attempt, goal_acceptance_validation_receipts: [{criterion_id: "criterion-a", receipt: runnerReceipt()}]};
    for (const field of ["provider_revision", "contract_digest", "todo_semantic_digest", "operation_id"]) {
      assert.equal((await executeCoordinationTodoTerminalLifecycle(store, {...good,
        goal_acceptance_source_binding: {...binding, [field]: "stale"}})).reason_code, "goal_acceptance_validation_rejected");
    }
    assert.deepEqual(await loaded(store), before);
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, good)).status, "applied");
    assert.equal((await loaded(store)).head.todos instanceof Array, true);
    assert.equal((projectGoalAcceptance((await loaded(store)).head, "goal-a").tasks as JsonObject[])[0]!.state, "ready",
      "successful completion must not invalidate the just-checked work binding");
    const retained = await store.readReceipt("complete");
    assert.equal(retained.status, "found");
    assert.match(JSON.stringify(retained), /goal_acceptance_completion/);
    assert.doesNotMatch(JSON.stringify(retained), /validation_argv/);
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, terminal)).status, "replayed");
  });

  test(`${provider}: accepted result binds to the same Todo transaction and producer`, async t => {
    const descriptor = {provider: "local_runtime_v0", sha256: "a".repeat(64),
      size_bytes: 5, content_type: "text/plain"};
    const withoutAcceptance = await seeded(t, provider, "off", {claimed_by: "agent-a"});
    assert.equal((await executeCoordinationTodoTerminalLifecycle(withoutAcceptance.store, {...terminal,
      completion_result: descriptor})).reason_code, "completion_result_rejected");
    assert.equal(((await loaded(withoutAcceptance.store)).head.todos as JsonObject[])[0]!.done, false);
    const unclaimed = await seeded(t, provider, "bound");
    const unclaimedPlan = await executeCoordinationTodoTerminalLifecycle(unclaimed.store, {...terminal,
      completion_result: descriptor});
    assert.equal(unclaimedPlan.status, "execute_validation");
    const receipts = [{criterion_id: "criterion-a", receipt: runnerReceipt()}];
    const unclaimedAttempt = await executeCoordinationTodoTerminalLifecycle(unclaimed.store, {...terminal,
      completion_result: descriptor,
      goal_acceptance_source_binding: unclaimedPlan.goal_acceptance_source_binding as JsonObject,
      goal_acceptance_validation_receipts: receipts});
    assert.equal(unclaimedAttempt.reason_code, "completion_result_rejected");
    assert.equal(((await loaded(unclaimed.store)).head.todos as JsonObject[])[0]!.done, false);

    const {store} = await seeded(t, provider, "bound", {claimed_by: "agent-a"});
    const plan = await executeCoordinationTodoTerminalLifecycle(store, {...terminal, completion_result: descriptor});
    assert.equal(plan.status, "execute_validation");
    const attempt = {...terminal, completion_result: descriptor,
      goal_acceptance_source_binding: plan.goal_acceptance_source_binding as JsonObject,
      goal_acceptance_validation_receipts: receipts};
    const malformed = await executeCoordinationTodoTerminalLifecycle(store, {...attempt,
      completion_result: {...descriptor, sha256: "not-a-digest"}});
    assert.equal(malformed.reason_code, "completion_result_rejected");
    const committed = await executeCoordinationTodoTerminalLifecycle(store, attempt);
    assert.equal(committed.status, "applied");
    const stored = ((await loaded(store)).head.todos as JsonObject[])[0]!.completion_result as JsonObject;
    assert.equal(stored.sha256, descriptor.sha256);
    assert.equal(stored.producer_agent_id, "agent-a");
    assert.equal(stored.todo_id, "todo_work");
    assert.equal(stored.acceptance_contract_digest, (await loaded(store)).head.goal_acceptance?.digest);
    assert.equal(acceptanceWorkGuard((await loaded(store)).head, "goal-a", "todo_work")?.state, "ready");
    assert.equal((await executeCoordinationTodoTerminalLifecycle(store, attempt)).status, "replayed");
    assert.notEqual((await executeCoordinationTodoTerminalLifecycle(store, {...attempt,
      completion_result: {...descriptor, sha256: "b".repeat(64)}})).status, "replayed");
  });

  test(`${provider}: intervening head changes require fresh validation, not an old success`, async t => {
    const {store} = await seeded(t, provider, "bound");
    const plan = await executeCoordinationTodoTerminalLifecycle(store, terminal);
    const current = await loaded(store);
    await replace(store, {...current.head, unrelated_extension: {value: "new"}}, "intervening");
    const result = await executeCoordinationTodoTerminalLifecycle(store, {...terminal,
      goal_acceptance_source_binding: plan.goal_acceptance_source_binding as JsonObject,
      goal_acceptance_validation_receipts: [{criterion_id: "criterion-a", receipt: runnerReceipt()}]});
    assert.equal(result.reason_code, "goal_acceptance_validation_rejected");
    assert.equal(((await loaded(store)).head.todos as JsonObject[])[0]!.done, false);
  });
}

test("canonical list/read expose only enabled public-safe acceptance sidecars", async t => {
  const {store, root} = await seeded(t, "file", "bound");
  const records = [todo(), todo({todo_id: "todo_new", text: "New work"}),
    todo({todo_id: "todo_monitor", task_class: "continuous_monitor"}),
    todo({todo_id: "todo_gate", role: "user", task_class: "user_gate"})];
  await replace(store, projection(records, acceptance(records)), "add");
  const list = await listLocalCoordinationTodos({schema_version: "loopx_local_coordination_todo_list_request_v0",
    runtime_root: root, goal_id: "goal-a"});
  assert.equal(list.status, "loaded", JSON.stringify(list));
  assert.equal((list.goal_acceptance_contract as JsonObject).status, "held");
  assert.doesNotMatch(JSON.stringify(list), /validation_argv/);
  const guards = list.goal_acceptance_work_guards as JsonObject;
  assert.deepEqual(Object.keys(guards), ["todo_new", "todo_work"]);
  assert.equal((guards.todo_new as JsonObject).allowed, false);
  assert.equal((list.todos as JsonObject[]).some(record => "goal_acceptance_guard" in record), false);
  const read = await readLocalCoordinationTodo({schema_version: "loopx_local_coordination_todo_read_request_v0",
    runtime_root: root, goal_id: "goal-a", todo_id: "todo_new"});
  assert.equal((read.goal_acceptance_guard as JsonObject).reason_code, "goal_acceptance_unbound");
  await replace(store, projection(records), "disable");
  const disabled = await listLocalCoordinationTodos({schema_version: "loopx_local_coordination_todo_list_request_v0",
    runtime_root: root, goal_id: "goal-a"});
  assert.equal(Object.hasOwn(disabled, "goal_acceptance_contract"), false);
  assert.equal(Object.hasOwn(disabled, "goal_acceptance_work_guards"), false);
});

test("collection guard projection reuses one acceptance and Todo snapshot", () => {
  const records = Array.from({length: 150}, (_, index) => todo({
    todo_id: `todo_${String(index).padStart(3, "0")}`,
    text: `Work ${index}`,
  }));
  const head = projection(records, acceptance(records, false));
  const ids = records.map(record => String(record.todo_id));
  const startedAt = performance.now();
  const guards = projectGoalAcceptanceWorkGuards(head, "goal-a", ids);
  const elapsedMs = performance.now() - startedAt;
  assert.equal(Object.keys(guards).length, records.length);
  assert.ok(Object.values(guards).every(guard => guard.reason_code === "goal_acceptance_unbound"));
  assert.ok(elapsedMs < 1_000, `150 Todo guards should remain a bounded collection read, got ${elapsedMs}ms`);
});

test("completion plans both validators and rejects a combined budget exceeding 29 seconds", async t => {
  const declaration = {validation_command: null, validation_command_argv: ["true"],
    validation_label: "caller-check", validation_timeout_seconds: null};
  const {store} = await seeded(t, "file", "bound", {completion_validation_required: true,
    completion_validation_sha256: canonicalAuthoritySha256(declaration)});
  const before = await loaded(store);
  const rejected = await executeCoordinationTodoTerminalLifecycle(store, {...terminal, validation_declaration: declaration});
  assert.equal(rejected.reason_code, "goal_acceptance_validation_budget_exceeded");
  assert.deepEqual(await loaded(store), before);
  const boundedDeclaration = {...declaration, validation_timeout_seconds: 5};
  const bounded = await seeded(t, "file", "bound", {completion_validation_required: true,
    completion_validation_sha256: canonicalAuthoritySha256(boundedDeclaration)});
  const plan = await executeCoordinationTodoTerminalLifecycle(bounded.store, {...terminal, validation_declaration: boundedDeclaration});
  assert.equal(plan.status, "execute_validation");
  assert.equal((plan.validation_effect as JsonObject).validation_label, "caller-check");
  assert.equal((plan.goal_acceptance_validation_effects as unknown[]).length, 1);
  const result = await executeCoordinationTodoTerminalLifecycle(bounded.store, {...terminal,
    validation_declaration: boundedDeclaration, validation_receipt: runnerReceipt("caller-check"),
    goal_acceptance_source_binding: plan.goal_acceptance_source_binding as JsonObject,
    goal_acceptance_validation_receipts: [{criterion_id: "criterion-a", receipt: runnerReceipt()}]});
  assert.equal(result.status, "applied");
  assert.equal((result.validation_receipt as JsonObject).command_label, "caller-check");
  assert.ok(result.goal_acceptance_completion);
});

test("planning cannot assign stale work during a semantic edit and monitors stay outside acceptance", async t => {
  const {store} = await seeded(t, "file", "bound");
  const before = await loaded(store);
  for (const dry_run of [true, false]) {
    const update = await executeCoordinationTodoUpdate(store, {goal_id: "goal-a", todo_id: "todo_work", expected_role: "agent",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], operation_id: "claim-and-edit", patch: {text: "Different work"},
      expected_provider_revision: before.provider_revision, authority_reason: "Reviewed correction",
      planning_intent: {claimed_by: "agent-a"}, clear_fields: [], dry_run, now});
    assert.equal(update.reason_code, "goal_acceptance_stale");
    assert.deepEqual(await loaded(store), before);
    assert.equal((await store.readReceipt("claim-and-edit")).status, "missing");
  }
  const monitor = await seeded(t, "file", "unbound", {task_class: "continuous_monitor"});
  assert.equal((await executeCoordinationTodoClaim(monitor.store, claim)).status, "applied");
  assert.equal((await executeCoordinationTodoTerminalLifecycle(monitor.store, terminal)).status, "applied");
});

test("verifier file pins travel unchanged to the acceptance execution adapter", async t => {
  const {store} = await seeded(t, "file", "bound");
  const current = await loaded(store);
  const state = current.head.goal_acceptance as JsonObject;
  const document = state.document as JsonObject;
  const files = [{path: "verify.py", sha256: "a".repeat(64)}];
  const pinned = normalizeGoalAcceptanceDocument({...document,
    criteria: (document.criteria as JsonObject[]).map(criterion => ({...criterion, validation_files: files}))});
  await replace(store, {...current.head, goal_acceptance: {...state, document: pinned,
    digest: canonicalAuthoritySha256(pinned)}}, "pin-verifier");
  const plan = await executeCoordinationTodoTerminalLifecycle(store, terminal);
  assert.equal(plan.status, "execute_validation");
  assert.deepEqual(((plan.goal_acceptance_validation_effects as JsonObject[])[0]!.effect as JsonObject).validation_files, files);
});

test("a competing commit cannot separate acceptance evidence from Todo completion", async t => {
  const {store} = await seeded(t, "file", "bound");
  const plan = await executeCoordinationTodoTerminalLifecycle(store, terminal);
  let raced = false;
  const contender = new Proxy(store, {get(target, property) {
    if (property === "commitAuthority") return async (commit: Parameters<AuthorityStore["commitAuthority"]>[0]) => {
      raced = true;
      const current = await loaded(store);
      await replace(store, {...current.head, unrelated_extension: {value: "competing update"}}, "racer");
      return store.commitAuthority(commit);
    };
    const value = Reflect.get(target, property);
    return typeof value === "function" ? value.bind(target) : value;
  }});
  const result = await executeCoordinationTodoTerminalLifecycle(contender, {...terminal,
    goal_acceptance_source_binding: plan.goal_acceptance_source_binding as JsonObject,
    goal_acceptance_validation_receipts: [{criterion_id: "criterion-a", receipt: runnerReceipt()}]});
  assert.equal(raced, true);
  assert.equal(result.status, "conflict");
  assert.equal(((await loaded(store)).head.todos as JsonObject[])[0]!.done, false);
  assert.equal((await store.readReceipt("complete")).status, "missing");
});


test("prepared check review requires its exact Goal and local command effect", () => {
  const proposal = {schema_version: "loopx_chat_action_proposal_v1", action_kind: "goal.acceptance.check",
    status: "preview_ready", proposal_id: "proposal", expected_state_fingerprint: "current-basis",
    permission_classification: "local_command_execution", normalized_parameters: {goal_id: "goal", document: {}},
    context: {kind: "goal", goal_id: "goal"}, available_transitions: ["apply", "cancel"]};
  assert.equal(compileActionReviewPlan(proposal).canApply, true);
  for (const changed of [{permission_classification: "durable_write"},
      {context: {kind: "goal", goal_id: "other"}}, {normalized_parameters: {goal_id: "goal"}},
      {gate: {kind: "authority"}, status: "gated"}, {status: "stale"}]) {
    assert.equal(compileActionReviewPlan({...proposal, ...changed}).canApply, false);
  }
  assert.equal(compileActionReviewPlan({...proposal, status: "applied", receipt: {projection_verified: true}}).canApply, false);
});
