import assert from "node:assert/strict";
import test from "node:test";
import { BLOCKED_WAIT_REQUEST_SCHEMA, prepareBlockedWait, projectReceiptBoundWait, RECEIPT_BOUND_WAIT_REQUEST_SCHEMA } from "../../loopx/control_plane/quota/blocked_wait.ts";
import { isBoundedBlockedRetry } from "../../loopx/control_plane/quota/settlement_phase.ts";
import { evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION } from "../../loopx/control_plane/todos/resume_condition.ts";

function fixture(kind = "monitor_changed") {
  const target = { todo_id: "todo_dependency", status: "open", role: "agent",
    task_class: kind === "monitor_changed" ? "continuous_monitor" : "advancement_task",
    material_change_generation: 2 };
  const waiting = { todo_id: "todo_waiting", status: "open", role: "agent",
    task_class: "advancement_task", resume_when: `${kind}:${target.todo_id}`,
    resume_ready: false, resume_monitor_generation: 2 };
  const result = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [waiting], source_items: [target],
  });
  const condition = (result.conditions as { condition: Record<string, unknown> }[])[0].condition;
  return { schema_version: BLOCKED_WAIT_REQUEST_SCHEMA, todo_id: waiting.todo_id,
    observed_at: "2026-01-01T00:00:00Z", todos: [{ ...waiting, resume_condition: condition }, target] };
}

for (const kind of ["monitor_changed", "todo_done"]) {
  test(`${kind} closes only the exact pending registered dependency`, () => {
    const request = fixture(kind);
    const wait = prepareBlockedWait(request);
    assert.equal(wait.schema_version, "quota_blocked_causal_wait_v0");
    assert.equal(isBoundedBlockedRetry(wait, "todo_waiting"), true);
    assert.equal(isBoundedBlockedRetry(wait, "todo_other"), false);
    assert.equal(isBoundedBlockedRetry({ ...wait, resume_when: `${kind}:todo_other` }, "todo_waiting"), false);
    assert.equal(isBoundedBlockedRetry({ ...wait, target_todo: { ...request.todos[1], status: "done" } }, "todo_waiting"), false);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0]] }), /registered pending/);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [...request.todos, request.todos[1]] }), /registered pending/);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], status: "done" }, request.todos[1]] }), /unfinished/);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], resume_ready: true }, request.todos[1]] }), /registered pending/);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], status: "done" }] }), /registered pending/);
    assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], archive_state: "archived" }] }), /registered pending/);
  });
}

test("generation advance, missing baseline and stale projection are not pending proofs", () => {
  const request = fixture();
  assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], material_change_generation: 3 }] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], resume_monitor_generation: undefined }, request.todos[1]] }), /registered pending/);
  for (const generation of [undefined, -1, 1.5, "2"]) {
    assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], material_change_generation: generation }] }), /registered pending/);
  }
  assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], material_change_generation: 1 }] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], resume_condition: { ...request.todos[0].resume_condition, material_change_generation: 1 } }, request.todos[1]] }), /stale causal/);
  const wait = prepareBlockedWait(request);
  assert.equal(isBoundedBlockedRetry({ ...wait, target_todo: { ...request.todos[1], material_change_generation: 3 } }, "todo_waiting"), false);
  assert.equal(isBoundedBlockedRetry({ ...wait, target_todo: { ...request.todos[1], material_change_generation: 1 } }, "todo_waiting"), false);
  assert.equal(isBoundedBlockedRetry({ ...wait, observed_at: "2026-01-01T00:00:00" }, "todo_waiting"), false);
});

test("unregistered, fabricated and self-referential wait strings never suffice", () => {
  const request = fixture();
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], resume_condition: { satisfied: false } }, request.todos[1]] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], resume_when: "monitor_changed:todo_waiting" }, request.todos[1]] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [request.todos[0], { ...request.todos[1], task_class: "advancement_task" }] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, observed_at: "unknown" }), /timestamp/);
  assert.throws(() => prepareBlockedWait({ ...request, observed_at: "2026-02-30T00:00:00Z" }), /timestamp/);
  assert.equal(isBoundedBlockedRetry(prepareBlockedWait({ ...request, observed_at: "1970-01-01T00:00:00Z" }), "todo_waiting"), true);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], role: "user" }, request.todos[1]] }), /registered pending/);
  assert.throws(() => prepareBlockedWait({ ...request, todos: [{ ...request.todos[0], archive_state: "archived" }, request.todos[1]] }), /registered pending/);
});

test("deferred dependency work retains the original Turn's blocked closeout proof", () => {
  const request = fixture("todo_done");
  request.todos[0].status = "deferred";
  const wait = prepareBlockedWait(request);
  assert.equal(isBoundedBlockedRetry(wait, "todo_waiting"), true);
  assert.equal(isBoundedBlockedRetry(wait, "todo_other"), false);
  assert.throws(() => prepareBlockedWait({...request,
    todos: [request.todos[0], {...request.todos[1], status: "done"}]}), /registered pending/);
});


test("a PR wait gives a causal recovery route without accepting caller-authored PR evidence", () => {
  const request = fixture("todo_done");
  request.todos[0].resume_when = "pr_merged:example/project#1";
  assert.throws(() => prepareBlockedWait(request), /cannot qualify a PR merge wait.*registered monitor_changed or todo_done/);
});

test("an unpolled blocked Monitor offers lifecycle repair without execution authority", () => {
  const todo = {todo_id: "todo_monitor", role: "agent", task_class: "continuous_monitor",
    status: "blocked", archive_state: "active", claimed_by: "agent-a"};
  const request = {schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA,
    turn_instance_id: "turn-a", agent_id: "agent-a", todo_id: todo.todo_id,
    monitor_phase: "poll_due", todos: [todo]};
  const before = structuredClone(request);
  const result = projectReceiptBoundWait(request);
  assert.equal(result.status, "recovery_required");
  assert.deepEqual(result.recovery, {schema_version: "unsettled_host_turn_recovery_v0",
    scope: "current_turn", turn_instance_id: "turn-a", binding_kind: "todo",
    binding_id: "todo_monitor", repair: "lifecycle"});
  const obligation = result.obligation as Record<string, unknown>;
  assert.equal(obligation.delivery_allowed, false);
  assert.equal(obligation.must_attempt_work, true);
  assert.deepEqual(request, before);
  for (const patch of [{claimed_by: "agent-b"}, {archive_state: "archived"},
    {role: "user"}, {status: "open"}, {status: "done"}, {task_class: "advancement_task"}]) {
    assert.equal(projectReceiptBoundWait({...request, todos: [{...todo, ...patch}]}).status, "none");
  }
  for (const monitor_phase of [undefined, "settled", "settlement_pending", "invalid"]) {
    assert.equal(projectReceiptBoundWait({...request, monitor_phase}).status, "none");
  }
  assert.equal(projectReceiptBoundWait({...request, todos: []}).status, "none");
  assert.equal(projectReceiptBoundWait({...request, todos: [todo, todo]}).status, "none");
  assert.throws(() => projectReceiptBoundWait({...request, turn_instance_id: null}), /bound Turn/);
});
