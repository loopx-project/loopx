import assert from "node:assert/strict";
import test from "node:test";
import {projectReceiptBoundWait, RECEIPT_BOUND_WAIT_REQUEST_SCHEMA} from "../../loopx/control_plane/quota/blocked_wait.ts";
import {evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/todos/resume_condition.ts";

function request() {
  return {schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA, agent_id: "agent-a",
    todo_id: "todo_waiting", turn_instance_id: "host-turn-1", observed_at: "2026-01-01T00:00:00Z",
    todos: [{todo_id: "todo_waiting", role: "agent", task_class: "advancement_task",
      status: "open", archive_state: "active", claimed_by: "agent-a", resume_ready: false,
      resume_when: "monitor_changed:todo_monitor", resume_monitor_generation: 2,
      resume_condition: {schema_version: "todo_resume_condition_v0", kind: "monitor_changed",
        target_todo_id: "todo_monitor", target_status: "open", target_task_class: "continuous_monitor",
        resume_when: "monitor_changed:todo_monitor", baseline_generation: 2, material_change_generation: 2, satisfied: false}},
    {todo_id: "todo_monitor", role: "agent", task_class: "continuous_monitor",
      status: "open", archive_state: "active", material_change_generation: 2}]};
}

test("the current Turn retains a qualified dependency closeout and grants no work/spend", () => {
  const input = request(), before = structuredClone(input);
  const result = projectReceiptBoundWait(input);
  assert.equal(result.status, "recovery_required");
  const recovery = result.recovery as Record<string, unknown>;
  assert.equal(recovery.scope, "current_turn");
  assert.equal(recovery.turn_instance_id, input.turn_instance_id);
  assert.equal(recovery.binding_id, "todo_waiting");
  assert.equal(recovery.repair, "blocked_writeback");
  assert.equal((result.obligation as Record<string, unknown>).delivery_allowed, false);
  assert.deepEqual(input, before);
});

for (const [label, patch] of Object.entries({
  runnable: {resume_ready: true}, completed: {status: "done"}, monitor: {task_class: "continuous_monitor"},
  archived: {archive_state: "archive"}, foreign: {claimed_by: "agent-b"}, user: {role: "user"},
})) {
  test(`${label} cannot borrow the bound waiting closeout`, () => {
    const input = request();
    Object.assign(input.todos[0]!, patch);
    assert.deepEqual(projectReceiptBoundWait(input), {status: "none"});
  });
}

test("a fabricated, missing, or changed dependency cannot qualify blocked closeout", () => {
  const input = request();
  assert.throws(() => projectReceiptBoundWait({...input, todos: [input.todos[0]]}), /registered pending/);
  input.todos[1]!.material_change_generation = 3;
  assert.throws(() => projectReceiptBoundWait(input), /registered pending/);
});

for (const resume_when of ["resume_at:2026-01-01T00:10:00Z", "resume_at:2026-01-01T02:00:00Z",
  "pr_merged:example/project#1", "capacity_available:git_push"]) {
  test(`${resume_when} keeps its existing route instead of entering Todo-dependency recovery`, () => {
    const input = request();
    const waiting = {...input.todos[0]!, resume_when};
    const projection = evaluateTodoResumeConditions({
      schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [waiting], source_items: [waiting], evaluated_at: input.observed_at,
      available_capabilities: [],
    });
    const condition = (projection.conditions as {condition: Record<string, unknown>}[])[0]!.condition;
    assert.equal(condition.satisfied, false);
    assert.deepEqual(projectReceiptBoundWait({...input, todos: [
      {...waiting, resume_condition: condition, resume_ready: false},
    ]}), {status: "none"});
  });
}
