import assert from "node:assert/strict";
import test from "node:test";
import {planTodoCreationScope, TODO_CREATION_SCOPE_REQUEST_SCHEMA} from "../../loopx/control_plane/todos/creation_scope.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

function plan(overrides: JsonObject = {}): JsonObject {
  return planTodoCreationScope({schema_version: TODO_CREATION_SCOPE_REQUEST_SCHEMA,
    role: "agent", goal_id: "goal-a", registered_agents: ["agent-a", "agent-b"],
    intent: {task_class: "advancement_task", claimed_by: "agent-a"},
    unblocks_todo_id: null, unblocks_todo_id_declared: false,
    monitor_metadata: null, generated_at: "2026-10-11T02:00:00Z", ...overrides});
}

test("one draft preserves explicit gate scope without making an execution grant", () => {
  const result = plan({role: "user", intent: {task_class: "user_gate",
    actor_agent_id: "agent-a", global_gate: true}});
  assert.equal(result.global_gate, true);
  assert.equal(result.goal_bound, true);
  assert.equal(result.bound_agent, null);
  assert.equal(result.claimed_by, null);
  assert.deepEqual(result.monitor_metadata, {});
  for (const key of ["lease", "provider_revision", "receipt", "authorized"]) {
    assert.equal(Object.hasOwn(result, key), false);
  }
});

test("wait and monitor compose with the same canonical timestamp", () => {
  const result = plan({intent: {task_class: "continuous_monitor", claimed_by: "\u001cAGENT\u0085A\u001f",
    status: "deferred", resume_when: "resume_at:2026-10-11T10:00:00+08:00"},
    monitor_metadata: {cadence: "1h"}});
  assert.equal(result.claimed_by, "agent-a");
  assert.equal(result.status, "deferred");
  assert.equal(result.normalized_resume_when, "resume_at:2026-10-11T02:00:00Z");
  assert.deepEqual(result.monitor_metadata, {cadence: "1h", next_due_at: "2026-10-11T03:00:00Z"});
});

test("scope and supported wait errors precede relationship and Monitor errors", () => {
  assert.throws(() => plan({intent: {claimed_by: "unregistered"},
    unblocks_todo_id_declared: true, monitor_metadata: {cadence: "bad"}}), /not registered/);
  assert.throws(() => plan({intent: {resume_when: "bad"},
    unblocks_todo_id_declared: true, monitor_metadata: {cadence: "bad"}}), /unsupported Todo resume/);
  assert.throws(() => plan({unblocks_todo_id_declared: true,
    monitor_metadata: {cadence: "bad"}}), /unblocks_todo_id must use/);
});

test("Monitor bounds, class and claim exclusions remain obligations", () => {
  assert.throws(() => plan({intent: {task_class: "continuous_monitor"}}), /continuous_monitor requires one of/);
  assert.throws(() => plan({intent: {task_class: "user_gate"}}), /only valid for --role user/);
  assert.throws(() => plan({intent: {claimed_by: "agent-a", excluded_agents: ["agent-a"]}}), /claimed_by cannot/);
  assert.throws(() => plan({schema_version: "unknown"}), /schema mismatch/);
});
