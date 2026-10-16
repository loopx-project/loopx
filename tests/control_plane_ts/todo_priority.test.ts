import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {executeCoordinationTodoCreate, COORDINATION_TODO_CREATE_RECEIPT_SCHEMA, planCoordinationTodoCreate} from "../../loopx/control_plane/coordination/todo_create.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {readFileSync} from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import {planTodoCreationPriority, planTodoPriority, readTodoPriority, todoPriorityRank} from "../../loopx/control_plane/todos/priority.ts";

test("priority is declared metadata; prose is never scheduling input", () => {
  assert.equal(readTodoPriority({text: "Investigate P0 parser behavior"}), null);
  assert.equal(readTodoPriority({priority: "P3", text: "[P0] stale display"}), "P3");
  assert.equal(readTodoPriority({priority: null, text: "[P0] stale display"}), null);
  assert.equal(readTodoPriority({text: "[P1-user] Approve access"}), "P1");
  assert.equal(todoPriorityRank("P4"), 4);
  assert.equal(todoPriorityRank(null), 50);
});

test("authoring preserves omission, explicit clear and conflict semantics", () => {
  const old = {text: "[P2] Existing task", priority: "P2"};
  assert.deepEqual(planTodoPriority(old, {text: "Renamed task"}),
    {text: "[P2] Renamed task", title: "Renamed task", priority: "P2"});
  assert.deepEqual(planTodoPriority(old, {priority: "P0"}),
    {text: "[P0] Existing task", title: "Existing task", priority: "P0"});
  assert.deepEqual(planTodoPriority(old, {clear_priority: true}),
    {text: "Existing task", title: "Existing task", priority: null});
  assert.deepEqual(planTodoPriority(old, {}), {});
  assert.equal(planTodoPriority({}, {text: "Unprioritized task"}).priority, null);
  assert.equal(planTodoPriority({}, {text: "[P1parser] Literal text"}).text, "[P1parser] Literal text");
  assert.equal(planTodoPriority(old, {text: "[P3] Legacy edit"}).priority, "P3");
  assert.throws(() => planTodoPriority(old, {priority: "P0", text: "[P1] Conflict"}), /conflict/);
  assert.throws(() => planTodoPriority(old, {priority: "P0", clear_priority: true}), /either/);
  for (const priority of ["P5", "P1junk", "P1-user", 1, false, ""]) {
    assert.throws(() => planTodoPriority({}, {text: "Task", priority}), /priority/);
  }
});


test("complex fixture priority vocabulary matches the compatibility codec", () => {
  const fixture = JSON.parse(readFileSync(new URL("../fixtures/control_plane/coordination_production_scale_v0.json", import.meta.url), "utf8"));
  for (const row of fixture.priority_cases) {
    assert.equal(readTodoPriority(row.item), row.priority);
    assert.equal(todoPriorityRank(readTodoPriority(row.item)), row.rank);
  }
});


test("canonical create defaults omission to P1, normalizes duplicates and respects explicit null", () => {
  const input = {goal_id: "priority-goal", actor_agent_id: null, registered_agents: [],
    operation_id: "create-priority", dry_run: false, now: new Date("2026-09-20T00:00:00Z"),
    todo: {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "new-todo", role: "agent",
      status: "open", done: false, archive_state: "active", text: "A task", priority: "P4"}};
  const first = planCoordinationTodoCreate(input, new Map(), TODO_DOMAIN_READ_RECORD_SCHEMA);
  assert.equal(first.status, "planned");
  const created = first.todo as JsonObject;
  assert.equal(created.priority, "P4"); assert.equal(created.text, "[P4] A task");
  const duplicate = planCoordinationTodoCreate({...input, todo: {...input.todo, todo_id: "other-todo"}},
    new Map([["new-todo", created]]), TODO_DOMAIN_READ_RECORD_SCHEMA);
  assert.equal(duplicate.changed, false);
  assert.equal(duplicate.todo_id, "new-todo");
  const unranked = planCoordinationTodoCreate({...input, todo: {...input.todo, priority: null}},
    new Map(), TODO_DOMAIN_READ_RECORD_SCHEMA).todo as JsonObject;
  assert.equal(unranked.priority, undefined); assert.equal(unranked.text, "A task");
  const {priority: _omitted, ...todo} = input.todo;
  const ordinary = planCoordinationTodoCreate({...input, todo},
    new Map(), TODO_DOMAIN_READ_RECORD_SCHEMA).todo as JsonObject;
  assert.equal(ordinary.priority, "P1");
  assert.equal(ordinary.text, "[P1] A task");
});


test("creation defaults never override a declared prefix or explicit clearing", () => {
  assert.equal(planTodoCreationPriority({text: "Normal work"}).priority, "P1");
  for (const intent of [
    {text: "Owner decision", task_class: "user_gate"},
    {text: "Route is blocked", task_class: "blocker"},
    {text: "Waiting on a prerequisite", status: "blocked"},
  ]) assert.equal(planTodoCreationPriority(intent).priority, "P0");
  assert.equal(planTodoCreationPriority({
    text: "Explicitly lower owner decision", task_class: "user_gate", priority: "P2",
  }).priority, "P2");
  for (const priority of ["P0", "P1", "P2", "P3", "P4"]) {
    assert.equal(planTodoCreationPriority({text: "Normal work", priority}).priority, priority);
    assert.equal(planTodoCreationPriority({text: `[${priority}] Legacy work`}).priority, priority);
  }
  assert.equal(planTodoCreationPriority({text: "Unranked", priority: null}).priority, null);
  assert.equal(planTodoCreationPriority({text: "Unranked", clear_priority: true}).priority, null);
  assert.equal(planTodoPriority({text: "Historical work"}, {text: "Edited historical work"}).priority, null);
  assert.throws(() => planTodoCreationPriority({text: "[P0] Conflict", priority: "P1"}), /conflict/);
});


for (const provider of ["file", "sqlite"] as const) {
  test(`${provider}: an old creation receipt keeps its unranked result on upgrade`, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-priority-replay-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const store = provider === "file" ? new FileAuthorityStore(root, "goal-a") : new SqliteAuthorityStore(root, "goal-a");
    const todo = {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: "todo-old", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Historical unranked task"};
    const request = {goal_id: "goal-a", operation_id: "old-create", actor_agent_id: null,
      registered_agents: [], dry_run: false, todo, now: new Date("2026-01-01T00:00:00Z")};
    const receipt = {schema_version: COORDINATION_TODO_CREATE_RECEIPT_SCHEMA,
      operation_id: request.operation_id, goal_id: request.goal_id, todo_id: todo.todo_id, todo,
      request_sha256: canonicalAuthoritySha256({goal_id: request.goal_id, todo,
        actor_agent_id: null, dry_run: false})};
    assert.equal((await store.commitAuthority({operation_id: "old-create", expected_provider_revision: null,
      events: [], receipts: [receipt], next_projection: {goal_id: "goal-a", handoff_mode: "soft_claim",
        todos: [todo], leases: [], todo_read_model: coordinationTodoReadModel([todo], TODO_DOMAIN_READ_RECORD_SCHEMA)}})).status, "applied");
    const before = await store.loadAuthority();
    const result = await executeCoordinationTodoCreate(store, request,
      async () => {throw new Error("receipt replay must precede current-source admission");});
    assert.equal(result.status, "replayed");
    assert.deepEqual(result.todo, todo);
    assert.deepEqual(await store.loadAuthority(), before);
    const changed = await executeCoordinationTodoCreate(store, {...request, todo: {...todo, priority: "P0"}});
    assert.equal(changed.reason_code, "coordination_operation_identity_mismatch");
  });
}
