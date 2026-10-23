import assert from "node:assert/strict";
import {mkdtemp, writeFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {readTodo, TODO_READ_REQUEST} from "../../loopx/control_plane/todos/read_projection.ts";
import {readAgent, readScope} from "../../loopx/control_plane/todos/read_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {openLocalAuthorityStore, selectLocalSqliteAuthority} from "../../loopx/control_plane/coordination/local_authority_provider.ts";
import {engageLegacyCoordinationWriterFence} from "../../loopx/control_plane/coordination/legacy_writer_fence.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {TODO_CANONICAL_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";

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
