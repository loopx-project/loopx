import assert from "node:assert/strict";
import test from "node:test";
import {normalizeTodoWorkRequirements} from "../../loopx/control_plane/todos/work_requirements.ts";
import {planPublicTodoUpdate, TODO_PUBLIC_UPDATE_REQUEST_SCHEMA} from "../../loopx/control_plane/todos/public_update.ts";

test("work requirements distinguish omission, blank scalar and explicit empty collections", () => {
  assert.deepEqual(normalizeTodoWorkRequirements({task_domain: "", action_kind: " \t ",
    task_repository: null, required_capabilities: null}), {});
  assert.deepEqual(normalizeTodoWorkRequirements({required_capabilities: [], target_capabilities: [],
    required_write_scopes: [], explore_result_node_refs: []}),
  {required_capabilities: [], target_capabilities: [], required_write_scopes: [], explore_result_node_refs: []});
});

for (const intent of [
  {required_capabilities: "code_review"}, {required_capabilities: ["good", 1]},
  {required_capabilities: ["good", "bad/token"]}, {target_capabilities: ["good", ""]},
  {required_write_scopes: ["src/**", "/absolute"]}, {required_write_scopes: ["src/**", "../escape"]},
  {required_write_scopes: ["src/**", "has space"]},
  {explore_result_node_refs: ["Node:ok", "bad/ref"]},
  {action_kind: "invalid action"}, {task_domain: "invalid/domain"},
  {task_repository: "https://user:password@example.com/project"},
  {task_repository: "user:password@example.com:project"},
]) {
  test(`invalid declaration is rejected in full: ${JSON.stringify(intent)}`, () => {
    assert.throws(() => normalizeTodoWorkRequirements(intent));
  });
}

test("metadata correction neither validates unrelated historic declarations nor re-arms a retained wait", () => {
  const todo = {todo_id: "todo_target", role: "agent", status: "open",
    task_class: "advancement_task", required_capabilities: ["legacy/invalid"],
    resume_when: "monitor_changed:todo_monitor", resume_monitor_generation: 7};
  const result = planPublicTodoUpdate({schema_version: TODO_PUBLIC_UPDATE_REQUEST_SCHEMA,
    todo, intent: {required_capabilities: ["Code Review"]}, updated_at: "2026-09-12T00:00:00Z",
    context: {goal_id: "goal-a", role: "agent", actor_agent_id: "agent-a", enforce_monitor_boundedness: true,
      registered_agents: ["agent-a"], items: [{todo_id: "todo_monitor", role: "agent",
        status: "open", task_class: "continuous_monitor", material_change_generation: 8}]}});
  const updates = result.metadata_updates as Record<string, unknown>;
  assert.deepEqual(updates.required_capabilities, ["code_review"]);
  assert.equal(Object.hasOwn(updates, "resume_when"), false);
  assert.equal(Object.hasOwn(updates, "resume_monitor_generation"), false);
  assert.deepEqual(todo.required_capabilities, ["legacy/invalid"]);
});

test("durable Explore links preserve identities beyond a context page", () => {
  const refs = Array.from({length: 40}, (_, i) => `Node:${i}`);
  assert.deepEqual(normalizeTodoWorkRequirements({explore_result_node_refs: [...refs, refs[0]]}),
    {explore_result_node_refs: refs});
});
