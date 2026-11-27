import assert from "node:assert/strict";
import {appendFileSync, writeFileSync} from "node:fs";
import fsPromises, {appendFile, mkdtemp, rm, writeFile} from "node:fs/promises";
import {syncBuiltinESMExports} from "node:module";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {gunzipSync} from "node:zlib";
import {readTodo, TODO_READ_REQUEST} from "../../loopx/control_plane/todos/read_projection.ts";
import {readTodoRuntime} from "../../loopx/control_plane/todos/read_transport.ts";
import {projectCanonicalQuotaBatch, readCanonicalTodoProjection, verifyCanonicalTodoAuthorityWitness} from "../../loopx/control_plane/todos/read_transaction.ts";
import {goalRolloutEventLogPath} from "../../loopx/control_plane/rollout_receipt_log.ts";
import {readAgent, readScope} from "../../loopx/control_plane/todos/read_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {openLocalAuthorityStore, selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_CANONICAL_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {goalAcceptanceTodoDigest, normalizeGoalAcceptanceDocument} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {refineGoalOutcome} from "../../loopx/control_plane/goals/outcome_requirements.ts";

const now = "2026-10-19T07:00:00Z";
const row = (todo_id: string, extra: JsonObject = {}): JsonObject => ({schema_version: "todo_item_v0", todo_id,
  index: 1, role: "agent", status: "open", done: false, text: "Review accepted evidence", task_class: "advancement_task",
  archive_state: "active", source_section: "Agent Todo", ...extra});
const group = (items: JsonObject[], extra: JsonObject = {}) => readTodo({schema_version: TODO_READ_REQUEST,
  kind: "group", items, lineage: [], evaluated: false, role: "agent", source_section: "Agent Todo",
  item_limit: 1, observed_at: Date.parse(now) / 1000, evaluated_at: now, ...extra});

test("optional read metadata preserves legacy booleans and validates supplied tokens", () => {
  for (const [input, expected] of [[null, null], [undefined, null], ["", null], [false, null],
    [true, "true"], [" Agent A ", "agent-a"], ["invalid/agent", null]] as const) {
    assert.equal(readAgent(input), expected);
  }
  for (const input of [null, undefined, "", false, {}, "invalid-scope"]) assert.equal(readScope(input), null);
  assert.deepEqual(readScope("direction:action:tree"), {
    schema_version: "decision_scope_v0", kind: "direction", granularity: "action", scope_key: "tree",
  });
});

test("an objective revision remains visible without acceptance checks", async () => {
  const acceptance = {goal_objective: {objective: "Preserve the current user direction"}};
  const result = await readTodo({schema_version: TODO_READ_REQUEST, kind: "fields",
    todos: [], goal_acceptance_contract: acceptance,
    observed_at: Date.parse(now) / 1000, evaluated_at: now});
  assert.deepEqual(((result.fields as JsonObject).agent_todos as JsonObject).goal_acceptance_contract, acceptance);
});

test("current acceptance checks survive full-source reads and selection without an enable flag", async () => {
  const acceptance = {criteria: [], revision: 1, digest: "sha256:" + "a".repeat(64)};
  const fields = await readTodo({schema_version: TODO_READ_REQUEST, kind: "fields",
    todos: [row("todo_guarded")], goal_acceptance_contract: acceptance,
    goal_acceptance_work_guards: {todo_guarded: {allowed: false}},
    observed_at: Date.parse(now) / 1000, evaluated_at: now});
  const summary = (fields.fields as JsonObject).agent_todos as JsonObject;
  assert.deepEqual(summary.goal_acceptance_contract, acceptance);
  assert.equal(((summary.items as JsonObject[])[0].goal_acceptance_guard as JsonObject).allowed, false);
  const selected = await readTodo({schema_version: TODO_READ_REQUEST, kind: "select",
    fields: fields.fields, field_evaluations: fields.field_evaluations, roles: ["agent"],
    source_name: "file_authority", observed_at: Date.parse(now) / 1000, evaluated_at: now});
  const summaries = (selected.projection as JsonObject).summaries as JsonObject;
  assert.deepEqual((summaries.agent_todos as JsonObject).goal_acceptance_contract, acceptance);
});

test("user summaries keep capability action counts and bounded titles beyond Todo display caps", async () => {
  const reviews = Array.from({length: 5}, (_, index) => row(`todo_report_${index}`, {
    index,
    role: "user",
    source_section: "User Todo",
    task_class: "user_action",
    action_kind: "review_periodic_report",
    capability_binding_ref: `periodic-report-review:r${String(index).repeat(64)}`,
    text: `Report edition ${index}`,
    created_by: "report-agent",
    updated_at: `2026-11-0${index + 1}T00:00:00Z`,
  }));
  const result = await group([...reviews,
    row("todo_unbound_action", {index: 6, role: "user", task_class: "user_action", action_kind: "review_periodic_report"}),
    row("todo_done_review", {index: 7, role: "user", task_class: "user_action", status: "done", done: true,
      action_kind: "review_periodic_report", capability_binding_ref: `periodic-report-review:r${"f".repeat(64)}`}),
  ], {role: "user", source_section: "User Todo", item_limit: 1});
  const summary = result.summary as JsonObject;
  assert.equal((summary.items as unknown[]).length, 1);
  const actions = summary.capability_actions as JsonObject;
  assert.equal(actions.schema_version, "todo_capability_actions_v0");
  assert.equal(actions.total_count, 5);
  const [reportGroup] = actions.groups as JsonObject[];
  assert.equal(reportGroup!.action_kind, "review_periodic_report");
  assert.equal(reportGroup!.count, 5);
  assert.deepEqual((reportGroup!.items as JsonObject[]).map(item => item.todo_id),
    ["todo_report_4", "todo_report_3", "todo_report_2"]);
  assert.ok((reportGroup!.items as JsonObject[]).every(item =>
    item.role === "user" && item.task_class === "user_action" &&
    String(item.capability_binding_ref).startsWith("periodic-report-review:r")));
});

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider} full read keeps archived dependencies and fresh provider results without Markdown`, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-todo-read-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const state = join(root, "state.md"), goal = "goal-a";
    await writeFile(state, "# Synthetic Goal\n");
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal);
    const body = "Verify the original acceptance requirement 😀. ".repeat(40);
    const todos = [row("todo_waiting", {status: "deferred", done: true, text: body,
      resume_when: "todo_done:todo_archived"}), row("todo_archived", {role: "user", status: "done", done: true,
      task_class: "user_action", archive_state: "archive", source_section: "Completed Work Archive"})]
      .sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
    const projection = {goal_id: goal, handoff_mode: "soft_claim", todos, leases: [],
      todo_read_model: coordinationTodoReadModel(todos, TODO_CANONICAL_READ_RECORD_SCHEMA)};
    const committed = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
      events: [], next_projection: projection, receipts: []});
    assert.equal(committed.status, "applied");
    if (committed.status !== "applied") return;
    const fence = await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root, goal_id: goal, state_path: state, fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0",
        state: "engaged", goal_id: goal, fence_id: "fixture", source_version: "fixture",
        source_projection_sha256: canonicalAuthoritySha256(projection), expected_shadow_provider_revision: committed.provider_revision}});
    assert.equal(fence.status, "applied");
    await writeFile(state, new Uint8Array([255]));
    const request = {schema_version: TODO_READ_REQUEST, kind: "query", source: {runtime_root: root, goal_id: goal},
      observed_at: Date.parse(now) / 1000, evaluated_at: now,
      query: {goal_id: goal, role: null, status: null, todo_id: "todo_waiting", agent_id: null, limit: null,
        read_scope: "active", json_references: true, thin: false, state_file: state, project: root}};
    const result = await readTodo(request), payload = result.payload as JsonObject;
    assert.ok(payload, JSON.stringify(result));
    assert.equal(payload.read_only, true);
    assert.equal((payload.todo as JsonObject).text, body);
    assert.equal((payload.todo as JsonObject).resume_ready, true);
    assert.equal((payload.authority_read as JsonObject).provider_revision, committed.provider_revision);
    assert.equal((await store.loadAuthority()).status, "loaded");
    const fullQuery = {...request, query: {...request.query, todo_id: null,
      json_references: false, full_text: true}};
    const full = (await readTodo(fullQuery)).payload as JsonObject;
    assert.equal(full.full_text, true);
    assert.equal((full.todos as JsonObject[])[0].text, body);
    assert.equal((full.authority_read as JsonObject).provider_revision, committed.provider_revision);
    const bounded = (await readTodo({...fullQuery, query: {...fullQuery.query, full_text: false}})).payload as JsonObject;
    assert.equal(bounded.full_text, undefined);
    assert.notEqual((bounded.todos as JsonObject[])[0].text, body);
    const {query: _query, ...summaryRequest} = {...request, kind: "canonical_fields"};
    const summary = await readTodo(summaryRequest);
    const statusRequest = {...summaryRequest, canonical_contract_fields: ["todo_id", "status", "archive_state"]};
    const status = await readTodo(statusRequest);
    const compact = status.canonical as JsonObject;
    assert.equal(compact.provider_revision, committed.provider_revision);
    assert.ok(!Object.hasOwn(compact, "todos") && !Object.hasOwn(compact, "todo_ids"));
    assert.deepEqual(compact.todo_contract_rows, [
      {todo_id: "todo_archived", status: "done", archive_state: "archive"},
      {todo_id: "todo_waiting", status: "deferred", archive_state: "active"},
    ]);
    assert.deepEqual(status.fields, summary.fields);
    assert.deepEqual(status.field_evaluations, summary.field_evaluations);
    const {replay_read: replay, ...provenance} = status.source_projection as JsonObject;
    assert.deepEqual(provenance, summary.source_projection);
    assert.deepEqual(replay, summaryRequest);
    await assert.rejects(() => readTodo({...statusRequest, source: {...request.source, include_leases: true}}),
      /read-only canonical summary/);
    await assert.rejects(() => readTodo({...statusRequest, canonical_contract_fields: ["status", "status"]}),
      /read-only canonical summary/);
    await assert.rejects(() => readTodo({...fullQuery, query: {...fullQuery.query, thin: true}}), /compact presentation/);
    await assert.rejects(() => readTodo({...fullQuery, query: {...fullQuery.query, summary: true}}), /compact presentation/);
    await assert.rejects(() => readTodo({...fullQuery, query: {...fullQuery.query, full_text: "true"}}), /full_text/);
    const laterTodos = [row("todo_waiting", {status: "blocked", text: "Changed current requirement"}), todos.find(item => item.todo_id === "todo_archived")!]
      .sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
    const changed = {...projection, todos: laterTodos,
      todo_read_model: coordinationTodoReadModel(laterTodos, TODO_CANONICAL_READ_RECORD_SCHEMA)};
    const later = await store.commitAuthority({expected_provider_revision: committed.provider_revision,
      operation_id: "later", events: [], next_projection: changed, receipts: []});
    assert.equal(later.status, "applied");
    const reread = (await readTodo(request)).payload as JsonObject;
    assert.equal((reread.todo as JsonObject).status, "blocked");
    assert.equal((reread.todo as JsonObject).text, "Changed current requirement");
    assert.notEqual((reread.authority_read as JsonObject).provider_revision, committed.provider_revision);
  });
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider} include-archived keeps current acceptance limits on active Todos`, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-todo-mixed-archive-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const state = join(root, "state.md"), goal = "goal-a";
    await writeFile(state, "# Synthetic Goal\n");
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal);
    const bound = row("todo_bound", {text: "Original bound requirement"});
    const unbound = row("todo_unbound", {index: 2, text: "Unbound active requirement"});
    const archived = row("todo_archived", {index: 3, status: "done", done: true,
      task_class: "advancement_task", archive_state: "archive", source_section: "Completed Work Archive"});
    const document = normalizeGoalAcceptanceDocument({
      objective: "Preserve current acceptance limits while reading history",
      non_goals: [],
      criteria: [{id: "criterion-a", description: "The bound work remains guarded",
        validation_argv: ["true"]}],
      bindings: [{todo_id: "todo_bound", criterion_ids: ["criterion-a"]}],
    });
    const acceptance = {
      schema_version: "loopx_goal_acceptance_v0",
      enabled: true,
      revision: 1,
      digest: canonicalAuthoritySha256(document),
      document,
      verification: null,
      bindings: [{todo_id: "todo_bound", todo_semantic_digest: goalAcceptanceTodoDigest(bound),
        revision: 1, criterion_ids: ["criterion-a"], confirmed_by: "owner"}],
    };
    const projection = authorityProjectionFixture(goal, [
      {...bound, text: "Changed bound requirement"},
      unbound,
      archived,
    ], [], "native", {goal_acceptance: acceptance});
    const committed = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "seed-mixed-archive",
      events: [],
      next_projection: projection,
      receipts: [],
    });
    assert.equal(committed.status, "applied");
    if (committed.status !== "applied") return;
    const fence = await engageLegacyCoordinationWriterFence({
      schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root,
      goal_id: goal,
      state_path: state,
      fence: {
        schema_version: "loopx_legacy_coordination_writer_fence_v0",
        state: "engaged",
        goal_id: goal,
        fence_id: "mixed-archive-fixture",
        source_version: "fixture",
        source_projection_sha256: canonicalAuthoritySha256(projection),
        expected_shadow_provider_revision: committed.provider_revision,
      },
    });
    assert.equal(fence.status, "applied");

    const query = (include_archived: boolean, status: string | null = null) => readTodo({
      schema_version: TODO_READ_REQUEST,
      kind: "query",
      source: {runtime_root: root, goal_id: goal},
      observed_at: Date.parse(now) / 1000,
      evaluated_at: now,
      query: {goal_id: goal, role: "agent", status, todo_id: null, agent_id: null, limit: null,
        read_scope: "active", include_archived, json_references: false, thin: false,
        state_file: state, project: root},
    });

    const normalResult = await query(false);
    const mixedResult = await query(true);
    const openOnlyResult = await query(true, "open");
    assert.ok(normalResult.payload, JSON.stringify(normalResult));
    assert.ok(mixedResult.payload, JSON.stringify(mixedResult));
    assert.ok(openOnlyResult.payload, JSON.stringify(openOnlyResult));
    const normal = normalResult.payload as JsonObject;
    const mixed = mixedResult.payload as JsonObject;
    const openOnly = openOnlyResult.payload as JsonObject;
    assert.deepEqual((normal.todos as JsonObject[]).map(item => item.todo_id), ["todo_bound", "todo_unbound"]);
    assert.deepEqual(new Set((mixed.todos as JsonObject[]).map(item => item.todo_id)),
      new Set(["todo_bound", "todo_unbound", "todo_archived"]));
    assert.deepEqual(new Set((openOnly.todos as JsonObject[]).map(item => item.todo_id)),
      new Set(["todo_bound", "todo_unbound"]));
    for (const payload of [normal, mixed, openOnly]) {
      const summary = payload.agent_todos as JsonObject;
      const contract = summary.goal_acceptance_contract as JsonObject;
      assert.equal(contract.status, "held");
      assert.deepEqual(contract.held_todo_ids, ["todo_bound", "todo_unbound"]);
      assert.deepEqual(summary.first_executable_items, []);
      const active = (summary.items as JsonObject[]).filter(item => item.todo_id !== "todo_archived");
      assert.ok(active.every(item => (item.goal_acceptance_guard as JsonObject).allowed === false));
    }
  });
}

test("display limits retain full-source closure and reject altered evaluation evidence", async () => {
  const items = Array.from({length: 4096}, (_, i) => row(`todo_completed_${i}`, {index: i + 1,
    status: "done", done: true, no_followup: true}));
  const result = await group(items), summary = result.summary as JsonObject;
  assert.equal(summary.total_count, 4096);
  assert.equal((summary.items as unknown[]).length, 1);
  assert.equal((summary.terminal_closure_proof as JsonObject).item_count, 4096);
  const first = await group([row("todo_owned", {status: "done", done: true, claimed_by: "agent-a"})]);
  const changed = {...(first.items as JsonObject[])[0], no_followup: true};
  await assert.rejects(() => group([changed], {evaluated: true, evaluations: first.evaluations}), /matching full-source/);
});

test("controller validation commands remain private while their declaration identity survives", async () => {
  const result = await readTodo({schema_version: TODO_READ_REQUEST, kind: "fields",
    todos: [row("todo_validation", {validation_command: "  printf synthetic  ", validation_label: "check"})],
    observed_at: Date.parse(now) / 1000, evaluated_at: now});
  const summary = (result.fields as JsonObject).agent_todos as JsonObject;
  const item = (summary.items as JsonObject[])[0]!;
  assert.equal(item.completion_validation_required, true);
  // Pinned to the independent Python declaration codec, including null fields.
  assert.equal(item.completion_validation_sha256, "31a837db75ed8cf0143228f0d3e94dae3886f0e79c496f7f4c335644f4c04020");
  assert.equal(JSON.stringify(result).includes("printf synthetic"), false);
});

test("a receiver view cannot include a peer's addressed work in any selected lane", async () => {
  const first = await group([row("todo_owned", {role: "user", task_class: "user_action", bound_agent: "agent-a"}),
    row("todo_peer", {role: "user", task_class: "user_action", bound_agent: "agent-b", index: 2})],
    {role: "user", source_section: "User Todo", item_limit: null});
  const selected = await group(first.items as JsonObject[], {evaluated: true, evaluations: first.evaluations,
    role: "user", source_section: "User Todo", item_limit: null,
    selection: {role: "user", status: null, todo_id: null, agent_id: "agent-a"}});
  assert.deepEqual((selected.items as JsonObject[]).map(item => item.todo_id), ["todo_owned"]);
  for (const value of Object.values(selected.summary as JsonObject)) {
    if (Array.isArray(value)) for (const item of value as JsonObject[]) {
      if (item.todo_id) assert.equal(item.todo_id, "todo_owned");
    }
  }
});

test("source capture selects an evaluated view without reusing unrelated lineage carriers", async () => {
  const item = row("todo_source_capture");
  const result = await readTodo({schema_version: TODO_READ_REQUEST, kind: "source_fields",
    groups: [{role: "agent", source_section: "Agent Todo", items: [item], include_empty_source: true}],
    lineage: [item], evaluated_at: now, observed_at: Date.parse(now) / 1000,
    projection_query: {roles: ["agent"], status: null, todo_id: null, agent_id: null, limit: null, source_name: "markdown_active_state"}});
  const projected = result.projection as JsonObject;
  assert.equal(projected.uncapped_todo_count, 1);
  assert.equal((projected.todos as JsonObject[])[0]!.todo_id, item.todo_id);
});

test("query display limits preserve complete source coverage and report the final view", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-todo-coverage-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const items = Array.from({length: 30}, (_, i) => row(`todo_coverage_${i}`, {
    index: i + 1, role: i < 15 ? "user" : "agent", task_class: i < 15 ? "user_action" : "advancement_task",
  })).sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
  const goal = "coverage", state = join(root, "state.md");
  await writeFile(state, "# Synthetic Goal\n");
  await selectLocalSqliteAuthority(root, goal, true);
  const store = await openLocalAuthorityStore(root, goal);
  const projection = {goal_id: goal, handoff_mode: "soft_claim", todos: items, leases: [],
    todo_read_model: coordinationTodoReadModel(items, TODO_CANONICAL_READ_RECORD_SCHEMA)};
  const committed = await store.commitAuthority({expected_provider_revision: null, operation_id: "coverage",
    events: [], next_projection: projection, receipts: []});
  assert.equal(committed.status, "applied");
  if (committed.status !== "applied") return;
  const fence = await engageLegacyCoordinationWriterFence({schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: root, goal_id: goal, state_path: state, fence: {schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged", goal_id: goal, fence_id: "coverage", source_version: "coverage",
      source_projection_sha256: canonicalAuthoritySha256(projection), expected_shadow_provider_revision: committed.provider_revision}});
  assert.equal(fence.status, "applied", JSON.stringify(fence));
  const cases = [
    {name: "ordinary", query: {}, available: 30, shown: 30},
    {name: "hot", query: {agent_id: "agent-a"}, available: 30, shown: 24},
    {name: "limited", query: {limit: 1}, available: 30, shown: 2},
    {name: "thin", query: {thin: true}, available: 30, shown: 4},
    {name: "references", query: {json_references: true, limit: 1}, available: 30, shown: 2},
    {name: "exact", query: {todo_id: items[0].todo_id}, available: 1, shown: 1},
    {name: "exact agent scope", query: {todo_id: items[0].todo_id, agent_id: "agent-a"}, available: 1, shown: 1, unfiltered: 30},
    {name: "exact limit", query: {todo_id: items[0].todo_id, limit: 1}, available: 1, shown: 1, unfiltered: 30},
    {name: "exact other role", query: {todo_id: items[0].todo_id, role: "agent"}, available: 0, shown: 0},
    {name: "missing", query: {todo_id: "todo_missing"}, available: 0, shown: 0},
  ];
  for (const scenario of cases) await t.test(scenario.name, async () => {
    const read = await readTodo({schema_version: TODO_READ_REQUEST, kind: "query",
      source: {runtime_root: root, goal_id: "coverage"},
      observed_at: Date.parse(now) / 1000, evaluated_at: now,
      query: {goal_id: "coverage", read_scope: "active", state_file: join(root, "state.md"), ...scenario.query}}, now);
    assert.ok(read.payload, JSON.stringify(read));
    const payload = read.payload as JsonObject, envelope = payload.projection_envelope as JsonObject;
    const coverage = envelope.coverage as JsonObject;
    assert.equal(envelope.alert, false);
    assert.equal(coverage.complete, true);
    assert.equal(coverage.expected_count, scenario.available);
    assert.equal(coverage.included_count, scenario.available);
    assert.deepEqual(coverage.omitted, []);
    assert.equal(coverage.shown_count, scenario.shown);
    assert.equal(coverage.available_count, scenario.available);
    assert.equal(coverage.truncated, scenario.shown < scenario.available);
    if ("unfiltered" in scenario) assert.equal(payload.unfiltered_todo_count, scenario.unfiltered);
  });
  const invalidItems = items.map((item, index) => index === 1 ? {...item, updated_at: 123} : item);
  const invalid = {...projection, todos: invalidItems,
    todo_read_model: coordinationTodoReadModel(invalidItems, TODO_CANONICAL_READ_RECORD_SCHEMA)};
  assert.equal((await store.commitAuthority({expected_provider_revision: committed.provider_revision,
    operation_id: "invalid-unselected-coordinate", events: [], next_projection: invalid, receipts: []})).status, "applied");
  await assert.rejects(() => readTodo({schema_version: TODO_READ_REQUEST, kind: "query",
    source: {runtime_root: root, goal_id: goal}, observed_at: Date.parse(now) / 1000, evaluated_at: now,
    query: {goal_id: goal, todo_id: items[0].todo_id, read_scope: "active", state_file: state}}), /invalid summary completion coordinates/);
});

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider} canonical rollout_log_stat refuses mid-read pr_merge TOCTOU and binds strictly to consumed snapshot`, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-todo-rollout-toctou-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const state = join(root, "state.md"), goal = "goal-toctou";
    await writeFile(state, "# Synthetic Goal\n");
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal);
    const waiting = row("todo_wait_pr", {
      status: "deferred", done: true, task_repository: "git:github.com/example/hotpath",
      resume_when: "pr_merged:#41",
    });
    const projection = {
      goal_id: goal, handoff_mode: "soft_claim", todos: [waiting], leases: [],
      todo_read_model: coordinationTodoReadModel([waiting], TODO_CANONICAL_READ_RECORD_SCHEMA),
    };
    const committed = await store.commitAuthority({
      expected_provider_revision: null, operation_id: "seed-toctou", events: [], next_projection: projection, receipts: [],
    });
    assert.equal(committed.status, "applied");
    if (committed.status !== "applied") return;
    const fence = await engageLegacyCoordinationWriterFence({
      schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root, goal_id: goal, state_path: state,
      fence: {
        schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged",
        goal_id: goal, fence_id: "toctou-fixture", source_version: "fixture",
        source_projection_sha256: canonicalAuthoritySha256(projection),
        expected_shadow_provider_revision: committed.provider_revision,
      },
    });
    assert.equal(fence.status, "applied");

    const logPath = goalRolloutEventLogPath(root, goal);
    const baseEvent = JSON.stringify({
      schema_version: "loopx_rollout_event_v0", event_kind: "evidence_log_read",
      goal_id: goal, event_id: "ev-base-1", recorded_at: "2026-11-24T09:00:00Z",
    }) + "\n";
    const mergeEvent = JSON.stringify({
      schema_version: "loopx_rollout_event_v0", event_kind: "pr_merge",
      goal_id: goal, event_id: "ev-merge-41", pr_ref: "example/hotpath#41",
      recorded_at: "2026-11-24T10:00:00Z",
    }) + "\n";
    const readReq: JsonObject = {
      schema_version: TODO_READ_REQUEST, kind: "canonical_fields",
      source: {runtime_root: root, goal_id: goal},
      rollout_event_source: {runtime_root: root, goal_id: goal},
      rollout_events: [], observed_at: Date.parse(now) / 1000, evaluated_at: now,
    };

    const origReadFile = fsPromises.readFile.bind(fsPromises);
    const origStat = fsPromises.stat.bind(fsPromises);

    // 1) Missing log (ENOENT) created with pr_merge before post-projection stat check -> refuse reuse.
    {
      let injected = false;
      const readHook = t.mock.method(fsPromises, "readFile", async (pathArg: unknown, ...rest: unknown[]) => {
        try {
          return await (origReadFile as (...args: unknown[]) => Promise<unknown>)(pathArg, ...rest);
        } catch (error) {
          if (!injected && pathArg === logPath && (error as NodeJS.ErrnoException)?.code === "ENOENT") {
            injected = true;
            writeFileSync(logPath, mergeEvent, "utf8");
          }
          throw error;
        }
      });
      syncBuiltinESMExports();
      try {
        const res = await readCanonicalTodoProjection(readReq);
        assert.equal(injected, true);
        assert.equal(res.rollout_log_stat, undefined);
      } finally {
        readHook.mock.restore();
        syncBuiltinESMExports();
      }
    }

    // 2) Append during readFile (preStat !== postStat) -> refuse reuse.
    await writeFile(logPath, baseEvent, "utf8");
    {
      let injected = false;
      const readHook = t.mock.method(fsPromises, "readFile", async (pathArg: unknown, ...rest: unknown[]) => {
        const bytes = await (origReadFile as (...args: unknown[]) => Promise<unknown>)(pathArg, ...rest);
        if (!injected && pathArg === logPath) {
          injected = true;
          appendFileSync(logPath, mergeEvent, "utf8");
        }
        return bytes;
      });
      syncBuiltinESMExports();
      try {
        const res = await readCanonicalTodoProjection(readReq);
        assert.equal(injected, true);
        assert.equal(res.rollout_log_stat, undefined);
      } finally {
        readHook.mock.restore();
        syncBuiltinESMExports();
      }
    }

    // 3) Append strictly after readReceiptLogSnapshotWithStat post-stat and before read_transaction post-projection stat -> refuse reuse.
    await writeFile(logPath, baseEvent, "utf8");
    {
      let sawRead = false;
      let injected = false;
      const readHook = t.mock.method(fsPromises, "readFile", async (pathArg: unknown, ...rest: unknown[]) => {
        const bytes = await (origReadFile as (...args: unknown[]) => Promise<unknown>)(pathArg, ...rest);
        if (pathArg === logPath) sawRead = true;
        return bytes;
      });
      const statHook = t.mock.method(fsPromises, "stat", async (pathArg: unknown, ...rest: unknown[]) => {
        const info = await (origStat as (...args: unknown[]) => Promise<unknown>)(pathArg, ...rest);
        if (sawRead && !injected && pathArg === logPath) {
          injected = true;
          appendFileSync(logPath, mergeEvent, "utf8");
        }
        return info;
      });
      syncBuiltinESMExports();
      try {
        const res = await readCanonicalTodoProjection(readReq);
        assert.equal(injected, true);
        assert.equal(res.rollout_log_stat, undefined);
        const sourceIdentity = (res.source_projection as JsonObject).source;
        await assert.rejects(() => projectCanonicalQuotaBatch({
          schema_version: "todo_quota_planning_batch_v0",
          authority_checks: [{read: readReq, expected_source: sourceIdentity}],
          requests: [],
        }), /quota canonical source changed|requires one or two/);
        await assert.rejects(() => projectCanonicalQuotaBatch({
          schema_version: "todo_quota_planning_batch_v0",
          authority_checks: [{read: readReq, expected_source: sourceIdentity}],
          requests: [{schema_version: "todo_quota_planning_request_v4", current_time: now}],
        }), /quota canonical source changed/);
      } finally {
        readHook.mock.restore();
        statHook.mock.restore();
        syncBuiltinESMExports();
      }
    }

    // 4) Clean read binds rollout_log_stat; subsequent pr_merge invalidates batch check; re-read observes resume_ready=true.
    await writeFile(logPath, baseEvent, "utf8");
    const before = await readCanonicalTodoProjection(readReq);
    assert.ok(before.rollout_log_stat, "clean read must bind rollout_log_stat");
    const beforeItems = ((before.fields as JsonObject).agent_todos as JsonObject).items as JsonObject[];
    assert.equal(beforeItems[0].resume_ready, false);
    const sourceIdentity = (before.source_projection as JsonObject).source;
    await appendFile(logPath, mergeEvent, "utf8");
    await assert.rejects(() => projectCanonicalQuotaBatch({
      schema_version: "todo_quota_planning_batch_v0",
      authority_checks: [{read: readReq, expected_source: sourceIdentity, rollout_log_stat: before.rollout_log_stat}],
      requests: [{schema_version: "todo_quota_planning_request_v4", current_time: now}],
    }), /quota canonical source changed/);
    const after = await readCanonicalTodoProjection(readReq);
    assert.ok(after.rollout_log_stat);
    const afterItems = ((after.fields as JsonObject).agent_todos as JsonObject).items as JsonObject[];
    assert.equal(afterItems[0].resume_ready, true);
  });
}

for (const provider of ["file", "sqlite"] as const) {
  test(`verifyCanonicalTodoAuthorityWitness detects ${provider} revision/cursor drift cleanly`, async t => {
    const root = await mkdtemp(join(tmpdir(), `witness-${provider}-`));
    t.after(() => rm(root, {recursive: true, force: true}));
    const state = join(root, "state.md");
    const goal = `witness-goal-${provider}`;
    await writeFile(state, "# Synthetic Goal\n");
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal);
    const baseTodos = [row("todo_a", {index: 1})];
    const baseProjection = {
      goal_id: goal, handoff_mode: "soft_claim", todos: baseTodos, leases: [],
      todo_read_model: coordinationTodoReadModel(baseTodos, TODO_CANONICAL_READ_RECORD_SCHEMA),
    };
    const commit1 = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "seed-witness-1",
      events: [],
      receipts: [],
      next_projection: baseProjection,
    });
    assert.equal(commit1.status, "applied");
    if (commit1.status !== "applied") return;
    const fence = await engageLegacyCoordinationWriterFence({
      schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root, goal_id: goal, state_path: state,
      fence: {
        schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged",
        goal_id: goal, fence_id: `witness-fence-${provider}`, source_version: "fixture",
        source_projection_sha256: canonicalAuthoritySha256(baseProjection),
        expected_shadow_provider_revision: commit1.provider_revision,
      },
    });
    assert.equal(fence.status, "applied");

    const sourceAuthority = `${provider}_v0`;
    const matched = await verifyCanonicalTodoAuthorityWitness({
      schema_version: "todo_authority_head_verify_v0",
      runtime_root: root,
      goal_id: goal,
      expected_witness: {
        source_authority: sourceAuthority,
        provider_revision: commit1.provider_revision,
        cursor: commit1.cursor,
      },
    });
    assert.equal(matched.fresh, true);
    assert.equal(matched.reason_code, "matched");

    const incomplete = await verifyCanonicalTodoAuthorityWitness({
      schema_version: "todo_authority_head_verify_v0",
      runtime_root: root,
      goal_id: goal,
      expected_witness: {
        source_authority: sourceAuthority,
        provider_revision: "",
        cursor: commit1.cursor,
      },
    });
    assert.equal(incomplete.fresh, false);
    assert.equal(incomplete.reason_code, "expected_witness_incomplete");

    const nextTodos = [
      row("todo_a", {index: 1}),
      row("todo_b", {index: 2, status: "deferred", resume_when: "pr_merged:example/hotpath#42"}),
    ];
    const nextProjection = {
      goal_id: goal, handoff_mode: "soft_claim", todos: nextTodos, leases: [],
      todo_read_model: coordinationTodoReadModel(nextTodos, TODO_CANONICAL_READ_RECORD_SCHEMA),
    };
    const commit2 = await store.commitAuthority({
      expected_provider_revision: commit1.provider_revision,
      operation_id: "seed-witness-2",
      events: [],
      receipts: [],
      next_projection: nextProjection,
    });
    assert.equal(commit2.status, "applied");
    if (commit2.status !== "applied") return;

    const drifted = await verifyCanonicalTodoAuthorityWitness({
      schema_version: "todo_authority_head_verify_v0",
      runtime_root: root,
      goal_id: goal,
      expected_witness: {
        source_authority: sourceAuthority,
        provider_revision: commit1.provider_revision,
        cursor: commit1.cursor,
      },
    });
    assert.equal(drifted.fresh, false);
    assert.equal(drifted.reason_code, "canonical_witness_drifted");
    assert.equal(drifted.provider_revision, commit2.provider_revision);
    assert.equal(drifted.cursor, commit2.cursor);
  });
}

for (const provider of ["file", "sqlite"] as const) {
  test(`${provider} todo.read query include_acceptance=false omits acceptance payloads with exact todo parity`, async t => {
    const root = await mkdtemp(join(tmpdir(), `loopx-todo-accept-${provider}-`));
    t.after(() => rm(root, {recursive: true, force: true}));
    const state = join(root, "state.md"), goal = `goal-accept-${provider}`;
    await writeFile(state, "# Synthetic Goal\n");
    if (provider === "sqlite") await selectLocalSqliteAuthority(root, goal, true);
    const store = await openLocalAuthorityStore(root, goal);
    const bound = row("todo_bound", {text: "Bound acceptance item"});
    const unbound = row("todo_unbound", {index: 2, text: "Unbound active item"});
    const document = normalizeGoalAcceptanceDocument({
      objective: "Keep acceptance evaluation intact without repeating acceptance payloads",
      non_goals: [],
      criteria: [{
        id: "criterion-a",
        description: "Bound item remains checked",
        validation_argv: ["python3", "check.py"],
        validation_files: [{path: "check.py", sha256: "a".repeat(64)}],
        oracle: {kind: "owner_pinned_oracle", source_id: "accept-checker", input_refs: [], output_refs: ["out.txt"]},
      }],
      bindings: [{todo_id: "todo_bound", criterion_ids: ["criterion-a"]}],
    });
    const acceptance = {
      schema_version: "loopx_goal_acceptance_v0",
      enabled: true,
      revision: 1,
      digest: canonicalAuthoritySha256(document),
      document,
      verification: null,
      bindings: [{
        todo_id: "todo_bound",
        todo_semantic_digest: goalAcceptanceTodoDigest(bound),
        revision: 1,
        criterion_ids: ["criterion-a"],
        confirmed_by: "owner",
      }],
    };
    const baseProjection = authorityProjectionFixture(goal, [bound, unbound], [], "native", {
      root_commitments: {objective: "Keep acceptance evaluation intact", acceptance: ["Bound item remains checked"]},
      goal_acceptance: acceptance,
    });
    const goalOutcomes = refineGoalOutcome(baseProjection, goal, null, "owner", {
      outcome_id: "outcome-a",
      owner_agent_id: "owner",
      objective: "Keep acceptance evaluation intact",
      requirements: [{id: "req-a", description: "Bound item remains checked"}],
      todo_ids: ["todo_bound"],
      source: {ref: "message:1", summary: "Keep acceptance evaluation intact"},
    });
    const projection = authorityProjectionFixture(goal, [bound, unbound], [], "native", {
      root_commitments: {objective: "Keep acceptance evaluation intact", acceptance: ["Bound item remains checked"]},
      goal_outcomes: goalOutcomes,
      goal_acceptance: acceptance,
    });
    const committed = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: "seed-accept-skip",
      events: [],
      next_projection: projection,
      receipts: [],
    });
    assert.equal(committed.status, "applied");
    if (committed.status !== "applied") return;
    const fence = await engageLegacyCoordinationWriterFence({
      schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
      runtime_root: root,
      goal_id: goal,
      state_path: state,
      fence: {
        schema_version: "loopx_legacy_coordination_writer_fence_v0",
        state: "engaged",
        goal_id: goal,
        fence_id: `accept-fence-${provider}`,
        source_version: "fixture",
        source_projection_sha256: canonicalAuthoritySha256(projection),
        expected_shadow_provider_revision: committed.provider_revision,
      },
    });
    assert.equal(fence.status, "applied");

    for (const role of ["agent", null] as const) {
      const baseRequest = {
        schema_version: TODO_READ_REQUEST,
        kind: "query",
        source: {runtime_root: root, goal_id: goal},
        observed_at: Date.parse(now) / 1000,
        evaluated_at: now,
        query: {
          goal_id: goal,
          role,
          status: null,
          todo_id: null,
          agent_id: null,
          limit: null,
          read_scope: "active",
          json_references: false,
          thin: false,
          state_file: state,
          project: root,
        },
      };
      const withAcceptance = await readTodo(baseRequest);
      assert.ok(withAcceptance.canonical_acceptance);
      if (role === null) assert.ok(withAcceptance.source_projection);
      else assert.equal(withAcceptance.source_projection, undefined);

      const nestedOff = await readTodo({
        ...baseRequest,
        query: {...baseRequest.query, include_acceptance: false},
      });
      const topOff = await readTodo({
        ...baseRequest,
        include_acceptance: false,
      });

      const fullPayload = withAcceptance.payload as JsonObject;
      for (const stripped of [nestedOff, topOff]) {
        assert.equal(stripped.canonical_acceptance, undefined);
        assert.equal(stripped.source_projection, undefined);
        const strippedPayload = stripped.payload as JsonObject;
        assert.deepEqual(strippedPayload.todos, fullPayload.todos);
        assert.deepEqual(strippedPayload.authority_read, fullPayload.authority_read);
        assert.equal(strippedPayload.open_count, fullPayload.open_count);
        for (const lane of ["agent_todos", "user_todos"] as const) {
          const fullSummary = fullPayload[lane] as JsonObject | undefined;
          const strippedSummary = strippedPayload[lane] as JsonObject | undefined;
          if (!fullSummary) {
            assert.equal(strippedSummary, undefined);
            continue;
          }
          assert.equal(strippedSummary!.goal_acceptance_contract, undefined);
          assert.deepEqual(strippedSummary!.items, fullSummary.items);
          assert.deepEqual(strippedSummary!.first_executable_items, fullSummary.first_executable_items);
        }
      }

      await assert.rejects(
        () => readTodo({...baseRequest, query: {...baseRequest.query, include_acceptance: "false"}}),
        /include_acceptance/,
      );
      await assert.rejects(
        () => readTodo({...baseRequest, include_acceptance: false, query: {...baseRequest.query, include_acceptance: true}}),
        /conflicts/,
      );
    }
  });
}

test("readTodoRuntime compresses >1 MiB canonical_fields responses cleanly", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-todo-large-gzip-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const state = join(root, "state.md"), goal = "goal-large-canonical";
  await writeFile(state, "# Large Synthetic Goal\n");
  const store = await openLocalAuthorityStore(root, goal);
  const longText = "Preserve exact canonical fields across >1 MiB read compression. ".repeat(30);
  const todos = Array.from({length: 600}, (_, index) =>
    row(`todo_large_${String(index).padStart(4, "0")}`, {index: index + 1, text: `${index}:${longText}`}),
  );
  const document = normalizeGoalAcceptanceDocument({
    objective: "Compress >1 MiB canonical_fields responses when acceptance is attached",
    non_goals: [],
    criteria: [{id: "criterion-a", description: "Large response still gzips", validation_argv: ["true"]}],
    bindings: [{todo_id: "todo_large_0000", criterion_ids: ["criterion-a"]}],
  });
  const acceptance = {
    schema_version: "loopx_goal_acceptance_v0",
    enabled: true,
    revision: 1,
    digest: canonicalAuthoritySha256(document),
    document,
    verification: null,
    bindings: [{
      todo_id: "todo_large_0000",
      todo_semantic_digest: goalAcceptanceTodoDigest(todos[0]!),
      revision: 1,
      criterion_ids: ["criterion-a"],
      confirmed_by: "owner",
    }],
  };
  const projection = authorityProjectionFixture(goal, todos, [], "native", {goal_acceptance: acceptance});
  const committed = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "seed-large-gzip",
    events: [],
    next_projection: projection,
    receipts: [],
  });
  assert.equal(committed.status, "applied");
  if (committed.status !== "applied") return;
  const fence = await engageLegacyCoordinationWriterFence({
    schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: root,
    goal_id: goal,
    state_path: state,
    fence: {
      schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged",
      goal_id: goal,
      fence_id: "large-gzip-fence",
      source_version: "fixture",
      source_projection_sha256: canonicalAuthoritySha256(projection),
      expected_shadow_provider_revision: committed.provider_revision,
    },
  });
  assert.equal(fence.status, "applied");

  const request = {
    schema_version: TODO_READ_REQUEST,
    kind: "canonical_fields",
    source: {runtime_root: root, goal_id: goal},
    observed_at: Date.parse(now) / 1000,
    evaluated_at: now,
  };
  const plain = await readTodo(request);
  const plainBytes = Buffer.byteLength(JSON.stringify(plain), "utf8");
  assert.ok(plainBytes > 1024 * 1024, `expected >1 MiB canonical_fields payload, got ${plainBytes}`);
  const packed = await readTodoRuntime(request);
  const piece = packed.result_piece as JsonObject;
  assert.ok(piece, JSON.stringify(packed));
  assert.equal(piece.content_encoding, "gzip");
  assert.equal(piece.decoded_byte_count, plainBytes);
  assert.ok(Number(piece.byte_count) < 256 * 1024, `expected compact gzip piece, got ${piece.byte_count}`);
  const decoded = JSON.parse(gunzipSync(Buffer.from(String(piece.data), "base64")).toString("utf8"));
  assert.deepEqual(decoded, plain);
});

test("single-role todo.read query preserves cross-role lineage, full text, empty roles, and invalid role errors", async t => {
  const todos: JsonObject[] = [
    row("todo_user_gate", {
      role: "user",
      status: "done",
      done: true,
      completed_at: now,
      no_followup: true,
      task_class: "user_gate",
      source_section: "User Todo",
      text: "[P0] Confirm user gate release window with complete unshortened details.",
    }),
    row("todo_user_open", {
      role: "user",
      status: "open",
      done: false,
      global_gate: true,
      task_class: "user_gate",
      source_section: "User Todo",
      text: "[P0] Open user review item with full body retained for single-role query parity.",
    }),
    row("todo_agent_wait", {
      role: "agent",
      status: "open",
      done: false,
      claimed_by: "antigravity",
      resume_when: "todo_done:todo_user_gate",
      unblocks_todo_id: "todo_user_open",
      text: "[P1] Agent item blocked until cross-role user gate finishes.",
    }),
  ];

  const root = await mkdtemp(join(tmpdir(), "loopx-single-role-query-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const state = join(root, "state.md"), goal = "goal-single-role";
  await writeFile(state, "# Single Role Query Goal\n");
  const store = await openLocalAuthorityStore(root, goal);
  const projection = authorityProjectionFixture(goal, todos, [], "native");
  const committed = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "seed-single-role",
    events: [],
    next_projection: projection,
    receipts: [],
  });
  assert.equal(committed.status, "applied");
  if (committed.status !== "applied") return;
  const fence = await engageLegacyCoordinationWriterFence({
    schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: root,
    goal_id: goal,
    state_path: state,
    fence: {
      schema_version: "loopx_legacy_coordination_writer_fence_v0",
      state: "engaged",
      goal_id: goal,
      fence_id: "single-role-fence",
      source_version: "fixture",
      source_projection_sha256: canonicalAuthoritySha256(projection),
      expected_shadow_provider_revision: committed.provider_revision,
    },
  });
  assert.equal(fence.status, "applied");

  const makeQuery = (query: JsonObject) => ({
    schema_version: TODO_READ_REQUEST,
    kind: "query",
    source: {runtime_root: root, goal_id: goal},
    observed_at: Date.parse(now) / 1000,
    evaluated_at: now,
    query: {goal_id: goal, state_file: state, project: root, ...query},
  });

  const userQuery = await readTodo(makeQuery({role: "user", status: "open", full_text: true}));
  const userProjection = userQuery.payload as JsonObject;
  const userList = userProjection.todos as JsonObject[];
  assert.equal(userList.length, 1);
  assert.equal(userList[0].todo_id, "todo_user_open");
  assert.match(String(userList[0].text), /single-role query parity/);
  assert.equal(userProjection.agent_todos, undefined);

  const agentQuery = await readTodo(makeQuery({role: "agent", todo_id: "todo_agent_wait"}));
  const agentProjection = agentQuery.payload as JsonObject;
  assert.equal((agentProjection.todo as JsonObject).todo_id, "todo_agent_wait");
  assert.equal((agentProjection.todo as JsonObject).resume_ready, true);
  assert.equal((agentProjection.todo as JsonObject).unblocks_todo_id, "todo_user_open");

  const emptyRoot = await mkdtemp(join(tmpdir(), "loopx-single-role-empty-"));
  t.after(() => rm(emptyRoot, {recursive: true, force: true}));
  const emptyState = join(emptyRoot, "state.md");
  await writeFile(emptyState, "# Empty Agent Role\n");
  const emptyStore = await openLocalAuthorityStore(emptyRoot, goal);
  const emptyProj = authorityProjectionFixture(goal, [todos[1]], [], "native");
  const emptyCommit = await emptyStore.commitAuthority({
    expected_provider_revision: null, operation_id: "seed-empty-agent",
    events: [], next_projection: emptyProj, receipts: [],
  });
  assert.equal(emptyCommit.status, "applied");
  if (emptyCommit.status !== "applied") return;
  await engageLegacyCoordinationWriterFence({
    schema_version: "loopx_legacy_coordination_writer_fence_engage_request_v0",
    runtime_root: emptyRoot, goal_id: goal, state_path: emptyState,
    fence: {
      schema_version: "loopx_legacy_coordination_writer_fence_v0", state: "engaged",
      goal_id: goal, fence_id: "empty-role-fence", source_version: "fixture",
      source_projection_sha256: canonicalAuthoritySha256(emptyProj),
      expected_shadow_provider_revision: emptyCommit.provider_revision,
    },
  });
  const emptyAgent = await readTodo({
    schema_version: TODO_READ_REQUEST, kind: "query", source: {runtime_root: emptyRoot, goal_id: goal},
    observed_at: Date.parse(now) / 1000, evaluated_at: now,
    query: {goal_id: goal, role: "agent", state_file: emptyState, project: emptyRoot},
  });
  assert.deepEqual((emptyAgent.payload as JsonObject).todos, []);

  await assert.rejects(
    readTodo(makeQuery({role: "bogus"})),
    /Todo read roles must be user or agent/,
  );
});
