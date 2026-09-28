import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
  evaluateUserTodoContractDiagnostics, TODO_CONTRACT_DIAGNOSTICS_REQUEST_SCHEMA,
  userTodoScopeConflict } from "../../loopx/control_plane/todos/authoring_scope.ts";

function plan(intent: JsonObject, overrides: JsonObject = {}): JsonObject {
  return planTodoAuthoringScope({schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
    command: "create", role: "user", todo: {}, goal_id: "goal-a",
    registered_agents: ["agent-a", "agent-b"], intent, ...overrides});
}

test("global blocking is never inferred from actor, goal binding, or missing scope", () => {
  for (const actor of [undefined, "agent-a"]) for (const goal of [false, true]) {
    const intent: JsonObject = {task_class: "user_gate", goal_bound: goal};
    if (actor) intent.actor_agent_id = actor;
    if (actor && !goal) {
      const scope = plan(intent);
      assert.equal(scope.global_gate, null);
      assert.equal(scope.blocks_agent, actor);
      assert.equal(scope.bound_agent, actor);
    } else assert.throws(() => plan(intent), /scope|bind/);
  }
  const action = plan({task_class: "user_action", goal_bound: true, actor_agent_id: "agent-a"});
  assert.equal(action.global_gate, null);
  assert.equal(action.blocks_agent, null);
  const single = plan({task_class: "user_gate"}, {registered_agents: ["agent-a"]});
  assert.equal(single.global_gate, null); // Preserve legacy single-agent scope; never upgrade it.
});

test("explicit all-agent intent is accepted without turning authorship into lane binding", () => {
  for (const agents of [[], ["agent-a"], ["agent-a", "agent-b"]]) {
    const scope = plan({task_class: "user_gate", global_gate: true,
      ...(agents.length ? {actor_agent_id: "agent-a"} : {})}, {registered_agents: agents});
    assert.equal(scope.global_gate, true);
    assert.equal(scope.goal_bound, true);
    assert.equal(scope.blocks_agent, null);
    assert.equal(scope.bound_agent, null);
  }
});

test("explicit continuation and gate constraints cannot silently overwrite each other", () => {
  for (const command of ["create", "update"]) for (const intent of [
    {global_gate: true, bound_agent: "agent-a"},
    {global_gate: true, blocks_agent: "agent-a"},
    {blocks_agent: "agent-a", bound_agent: "agent-b"},
    {blocks_agent: "agent-a", goal_bound: true},
    {bound_agent: "agent-a", goal_bound: true},
    {global_gate: true, clear_global_gate: true},
    {blocks_agent: "agent-a", clear_blocks_agent: true},
  ]) assert.throws(() => plan({task_class: "user_gate", ...intent}, {command}), /gate|bind|bound|blocks/);
  const scope = plan({task_class: "user_gate", actor_agent_id: "agent-a", blocks_agent: "agent-b"});
  assert.equal(scope.bound_agent, "agent-b");
});

test("update retains omitted scope and permits an explicit global-to-lane transition", () => {
  const todo = {task_class: "user_gate", status: "open", global_gate: true, goal_bound: true};
  const before = structuredClone(todo);
  assert.equal(plan({}, {command: "update", todo}).global_gate, true);
  const scope = plan({clear_global_gate: true, blocks_agent: "agent-b"}, {command: "update", todo});
  assert.equal(scope.global_gate, null);
  assert.equal(scope.goal_bound, false);
  assert.equal(scope.bound_agent, "agent-b");
  assert.throws(() => plan({clear_global_gate: true}, {command: "update", todo}), /explicit scope/);
  assert.deepEqual(todo, before);
});

test("authoring plan cannot grant terminal, executor, or non-user gate semantics", () => {
  for (const intent of [{task_class: "user_action", global_gate: true},
    {task_class: "user_action", blocks_agent: "agent-a"},
    {task_class: "user_gate", claimed_by: "agent-a"},
    {task_class: "user_action", bound_agent: "agent-other"}]) assert.throws(() => plan(intent));
  for (const intent of [{bound_agent: "agent-a"}, {goal_bound: true}, {blocks_agent: "agent-a"},
    {global_gate: true}, {task_class: "user_action"}, {status: "done"}]) {
    assert.throws(() => plan(intent, {command: "update", role: "agent", todo: {status: "open", task_class: "advancement_task"}}));
  }
  assert.throws(() => plan({task_class: "user_action", status: "done"}), /cannot create completed/);
  // A terminal historical record can be repaired without being admitted as an active gate.
  assert.equal(plan({}, {command: "update", todo: {status: "done", task_class: "legacy"}}).status, "done");
});

test("deferred state requires a supported condition and clears remain explicit", () => {
  const todo = {task_class: "advancement_task", status: "deferred", resume_when: "capacity_available:network"};
  assert.equal(plan({}, {command: "update", role: "agent", todo}).effective_resume_when, todo.resume_when);
  assert.throws(() => plan({clear_resume_when: true}, {command: "update", role: "agent", todo}), /requires --resume-when/);
  const reopened = plan({status: "open", clear_resume_when: true}, {command: "update", role: "agent", todo});
  assert.equal(reopened.effective_resume_when, null);
  assert.throws(() => plan({resume_when: "todo_done:todo_dependency", clear_resume_when: true}, {command: "update", role: "agent", todo}), /not both/);
  assert.throws(
    () => plan({resume_when: "free text"}, {command: "update", role: "agent", todo}),
    /supported conditions are:.*todo_done:.*monitor_changed:.*pr_merged:.*capacity_available:/,
  );
});

test("resolved successor scope is checked without using draft defaults", () => {
  const unbound = {bound_agent: null, goal_bound: false, blocks_agent: null, global_gate: false};
  assert.equal(userTodoScopeConflict("user_gate", unbound, 2), "gate_scope_missing");
  assert.equal(userTodoScopeConflict("user_action", unbound, 2), "binding_missing");
  assert.equal(userTodoScopeConflict("user_gate", {...unbound, global_gate: true}, 2), "global_binding_conflict");
  assert.equal(userTodoScopeConflict("user_gate", {...unbound, global_gate: true, goal_bound: true}, 2), null);
  assert.equal(userTodoScopeConflict("user_gate", {...unbound, blocks_agent: "agent-b", bound_agent: "agent-a"}, 2), "agent_binding_conflict");
});

test("malformed intent cannot turn a truthy string or an unknown field into scope", () => {
  for (const intent of [{global_gate: "true"}, {goal_bound: 1}, {global_gat: true}]) {
    assert.throws(() => plan({task_class: "user_gate", actor_agent_id: "agent-a", ...intent}), /boolean|does not own/);
  }
});

test("canonical diagnostics keep the non-terminal user rules without repairing rows", () => {
  const evaluate = (todos: JsonObject[], agents = ["agent-a"], terminal = ["done", "deferred", "archived"]) =>
    evaluateUserTodoContractDiagnostics({schema_version: TODO_CONTRACT_DIAGNOSTICS_REQUEST_SCHEMA,
      todos, registered_agents: agents, terminal_statuses: terminal});
  const open = (extra: JsonObject): JsonObject => ({schema_version: "todo_item_v0", todo_id: "todo_x",
    role: "user", status: "open", done: false, text: "raw text that must not surface", ...extra});
  const codes = (todos: JsonObject[], agents = ["agent-a"], terminal = ["done", "deferred", "archived"]) =>
    (evaluate(todos, agents, terminal).diagnostics as JsonObject[]).map(row => row.code);
  assert.deepEqual(codes([open({})]), ["user_todo_task_class_missing"]);
  assert.deepEqual(codes([open({done: true})]), ["user_todo_task_class_missing"]);
  assert.deepEqual(codes([open({task_class: "user_action", global_gate: true})]), ["user_action_blocking_scope_invalid"]);
  assert.deepEqual(codes([open({task_class: "user_gate", global_gate: true, blocks_agent: "agent-a"})]),
    ["user_gate_scope_conflict"]);
  assert.deepEqual(codes([open({task_class: "user_gate", goal_bound: true, bound_agent: "agent-a"})]),
    ["user_todo_response_scope_conflict"]);
  assert.deepEqual(codes([open({task_class: "user_gate", global_gate: true, bound_agent: "agent-a"})]),
    ["goal_user_gate_agent_binding_invalid"]);
  assert.deepEqual(codes([open({task_class: "user_gate", blocks_agent: "agent-a", goal_bound: true})]),
    ["agent_user_gate_goal_binding_invalid"]);
  assert.deepEqual(codes([open({task_class: "user_gate", goal_bound: true})], ["agent-a", "agent-b"]),
    ["multi_agent_user_gate_missing_scope"]);
  assert.deepEqual(codes([open({task_class: "user_action"})], ["agent-a", "agent-b"]),
    ["multi_agent_user_todo_missing_response_scope"]);
  // Controls: valid open user work, completed history without a class, and agent rows stay healthy.
  assert.deepEqual(codes([open({task_class: "user_action"})]), []);
  assert.deepEqual(codes([{...open({}), status: "done", done: true}]), []);
  assert.deepEqual(codes([{schema_version: "todo_item_v0", todo_id: "todo_a", role: "agent",
    status: "open", done: false}]), []);
  const observed = evaluate([open({}), {...open({}), status: "done", done: true}]);
  assert.equal(observed.checked, 1);
  assert.equal(JSON.stringify(observed).includes("raw text"), false);
});
