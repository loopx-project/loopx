import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { projectTodoResumePlanning, RESUME_PLANNING_REQUEST } from "../../loopx/control_plane/todos/resume_planning.ts";
import {evaluateTodoResumeConditions} from "../../loopx/control_plane/todos/resume_condition.ts";

function fact(id: string, overrides: JsonObject = {}): JsonObject {
  return {
    payload: { todo_id: id, index: 1, text: "Wait for work", role: "agent",
      task_class: "advancement_task", status: "deferred", resume_when: "capacity_available:compiler",
      resume_ready: false },
    id, status: "deferred", claim: null, excluded: [], done: false,
    resume: "capacity_available:compiler", ready: false, ready_truthy: false,
    priority: 1, index: 1, target_id: null, target_status: null,
    target_class: "advancement_task", ...overrides,
  };
}

function request(overrides: JsonObject = {}): JsonObject {
  return {
    schema_version: RESUME_PLANNING_REQUEST, agent_id: "agent-a", item_limit: 2,
    has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null,
    available_capabilities: null,
    sources: { items: [], backlog_items: [], first_open_items: [], deferred_items: [],
      deferred_resume_candidates: [], resume_blocked_items: [], monitor_open_items: [],
      current_agent_claimed_monitor_items: [], claimed_monitor_open_items: [] },
    ...overrides,
  };
}

test("only the actual pending Monitor generation can cover an in-scope successor", () => {
  const waiter = {todo_id: "todo_waiting", role: "agent", status: "open", task_class: "advancement_task",
    text: "Validate after the watched artifact changes.", resume_when: "monitor_changed:todo_source",
    resume_monitor_generation: 0};
  const source = {todo_id: "todo_source", role: "agent", status: "open", task_class: "continuous_monitor"};
  const evaluate = (items: JsonObject[]) => (evaluateTodoResumeConditions({
    schema_version: "todo_resume_evaluation_request_v0", items: [waiter], source_items: items,
  }).conditions as JsonObject[])[0].condition as JsonObject;
  const condition = evaluate([source]);
  const waiting = fact(waiter.todo_id, {status: "open", claim: "agent-a", resume: waiter.resume_when,
    target_id: source.todo_id, target_status: "open", target_class: "continuous_monitor",
    payload: {...waiter, resume_ready: false, resume_condition: condition}});
  const project = (row: JsonObject) => {
    const input = request();
    (input.sources as JsonObject).resume_blocked_items = [row];
    return projectTodoResumePlanning(input).blocked_successor_items as JsonObject[];
  };
  assert.equal(project(waiting)[0].todo_id, waiter.todo_id);
  for (const changed of [evaluate([]), evaluate([{...source, material_change_generation: 1}]),
    {...condition, baseline_generation: undefined}, {...condition, target_status: undefined}]) {
    assert.equal(project({...waiting, payload: {...waiter, resume_ready: false, resume_condition: changed}}).length, 0);
  }
  assert.equal(project({...waiting, claim: "another-agent"}).length, 0);
  assert.equal(project({...waiting, excluded: ["agent-a"]}).length, 0);
});

test("capacity evaluation and claim lanes use one snapshot without mutating it", () => {
  const input = request({ available_capabilities: ["compiler"] });
  (input.sources as JsonObject).items = [fact("todo_capacity", { claim: "agent-a" }),
    fact("todo_excluded", { excluded: ["agent-a"] })];
  const before = structuredClone(input);
  const result = projectTodoResumePlanning(input);
  const lanes = result.deferred_lanes as JsonObject;
  assert.equal(lanes.current_agent_deferred_resume_count, 1);
  assert.equal(lanes.executor_excluded_self_deferred_resume_count, 1);
  assert.equal(lanes.unclaimed_deferred_resume_count, 0);
  assert.equal((result.blocked_successor_items as unknown[]).length, 0);
  assert.deepEqual(input, before);
  input.available_capabilities = [];
  const unavailable = projectTodoResumePlanning(input).deferred_lanes as JsonObject;
  assert.equal(unavailable.current_agent_deferred_resume_count, 0);
});

test("readiness counts retain unseen work and revalidate capacity instead of reusing stale totals", () => {
  const input = request({has_deferred_count: true, deferred_count: 12,
    deferred_work_counts: {ready: 12, blocked: 0, unknown: 0, complete: true,
      ready_by_agent: {"agent-a": 12}}});
  (input.sources as JsonObject).items = [fact("todo_capacity", {claim: "agent-a"})];
  const counts = () => (projectTodoResumePlanning(input).deferred_lanes as JsonObject).deferred_work_counts;
  assert.deepEqual(counts(), input.deferred_work_counts);
  input.available_capabilities = [];
  assert.deepEqual(counts(), {ready: 0, blocked: 1, unknown: 11, complete: false, ready_by_agent: {}});
  input.available_capabilities = ["compiler"];
  assert.deepEqual(counts(), {ready: 1, blocked: 0, unknown: 11, complete: false, ready_by_agent: {"agent-a": 1}});
});

test("malformed source counts cannot fabricate complete readiness", () => {
  for (const fields of [{ready: -1}, {blocked: 0.5}, {unknown: null},
    {ready_by_agent: {"agent-a": 2}}, {complete: "true"},
    {ready: 2**53, ready_by_agent: {"agent-a": 2**53}}, {blocked: 2**53},
    {unknown: 2**53, complete: false}]) {
    const input = request({deferred_work_counts: {ready: 1, blocked: 0, unknown: 0,
      complete: true, ready_by_agent: {"agent-a": 1}, ...fields}});
    assert.throws(() => projectTodoResumePlanning(input), /counts/);
  }
});

test("historical duplicate readiness or claim conflicts stay unknown across capacity reads", () => {
  for (const conflicting of [{ready: false}, {claim: "agent-b"}, {status: "open"}]) {
    for (const capabilities of [null, ["compiler"]]) {
      const ready = fact("todo_conflict", {ready: true, ready_truthy: true, claim: "agent-a"});
      const input = request({has_deferred_count: true, deferred_count: 1, available_capabilities: capabilities});
      (input.sources as JsonObject).deferred_resume_candidates = [ready];
      (input.sources as JsonObject).items = [{...ready, ...conflicting}];
      const counts = (projectTodoResumePlanning(input).deferred_lanes as JsonObject).deferred_work_counts;
      assert.deepEqual(counts, {ready: 0, blocked: 0, unknown: 1, complete: false, ready_by_agent: {}});
    }
  }
});

test("capability reevaluation retains ready candidates outside the deferred display bound", () => {
  const visible = fact("todo_visible", { claim: "agent-a" });
  const ready = fact("todo_ready", {
    claim: "agent-a", ready: true, ready_truthy: true,
  });
  (ready.payload as JsonObject).resume_ready = true;
  const input = request({ available_capabilities: ["compiler"] });
  (input.sources as JsonObject).deferred_items = [visible];
  (input.sources as JsonObject).deferred_resume_candidates = [ready];

  const result = projectTodoResumePlanning(input);
  const lanes = result.deferred_lanes as JsonObject;

  assert.deepEqual(
    (lanes.current_agent_deferred_resume_candidates as JsonObject[]).map(
      (row) => row.todo_id,
    ),
    ["todo_ready", "todo_visible"],
  );
  assert.equal(lanes.current_agent_deferred_resume_count, 2);
  assert.deepEqual(
    ((result.capacity_fields as JsonObject).deferred_items as JsonObject[]).map(
      (row) => row.todo_id,
    ),
    ["todo_visible"],
  );
});

test("explicit candidate payload wins when the display lane has the same Todo id", () => {
  const display = fact("todo_shared", { claim: "agent-a" });
  (display.payload as JsonObject).text = "Compact display copy";
  const candidate = fact("todo_shared", {
    claim: "agent-a", ready: true, ready_truthy: true,
  });
  (candidate.payload as JsonObject).text = "Lossless candidate copy";
  (candidate.payload as JsonObject).resume_ready = true;
  const input = request({ available_capabilities: ["compiler"] });
  (input.sources as JsonObject).deferred_items = [display];
  (input.sources as JsonObject).deferred_resume_candidates = [candidate];

  const result = projectTodoResumePlanning(input);
  const lanes = result.deferred_lanes as JsonObject;
  const candidates = lanes.current_agent_deferred_resume_candidates as JsonObject[];

  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].todo_id, "todo_shared");
  assert.equal(candidates[0].text, "Lossless candidate copy");
});

test("a large wait source retains total counts independently from the display bound", () => {
  const input = request();
  const rows = Array.from({ length: 257 }, (_, i) => fact(`todo_wait_${i}`));
  (input.sources as JsonObject).deferred_items = rows;
  const result = projectTodoResumePlanning(input);
  const lanes = result.deferred_lanes as JsonObject;
  assert.equal(lanes.deferred_count, 257);
  assert.equal((lanes.deferred_items as unknown[]).length, 2);
  assert.equal((result.deferred_items as unknown[]).length, 257);
  assert.deepEqual(result.resume_blocked_lanes, {});
});

test("equal priority/index preserves source order, and zero display keeps counts", () => {
  const input = request({ item_limit: 0, has_deferred_count: true, deferred_count: 99 });
  (input.sources as JsonObject).items = [fact("todo_zed"), fact("todo_alpha")];
  const result = projectTodoResumePlanning(input);
  assert.deepEqual((result.deferred_items as JsonObject[]).map((row) => row.todo_id),
    ["todo_zed", "todo_alpha"]);
  assert.equal((result.deferred_lanes as JsonObject).deferred_count, 99);
  assert.deepEqual((result.deferred_lanes as JsonObject).deferred_items, []);
});

test("wire faults fail closed before selection instead of inventing readiness", () => {
  for (const override of [{ schema_version: "future" }, { item_limit: "2" },
    { has_deferred_count: null }, { available_capabilities: true }, { sources: {} }]) {
    assert.throws(() => projectTodoResumePlanning(request(override)));
  }
  for (const override of [{ ready: "false" }, { done: 0 }, { excluded: "agent-a" },
    { priority: null }, { payload: {} }]) {
    const input = request();
    (input.sources as JsonObject).items = [fact("todo_invalid", override)];
    assert.throws(() => projectTodoResumePlanning(input));
  }
});
