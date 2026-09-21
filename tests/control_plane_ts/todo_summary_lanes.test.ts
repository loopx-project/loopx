import assert from "node:assert/strict";
import test from "node:test";
import {projectTodoSummaryLanes, projectLegacyTodoWorkCounts, countTodoWork} from "../../loopx/control_plane/todos/summary_lanes.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

const row = (fields: JsonObject = {}): JsonObject => ({status: "open", done: false,
  task_class: "advancement_task", has_resume: false, resume_ready: null, resume_evaluated: false,
  acceptance_blocked: false, claimed: false, preferred: false, watch_only: false,
  due_at: null, expires_at: null, sort: [1, 1, "", ""], ...fields});
const project = (rows: JsonObject[]) => projectTodoSummaryLanes({schema_version: "todo_summary_lanes_request_v0", rows, observed_at: 100});

test("lane algebra distinguishes open, blocked, deferred, completed and acceptance denial", () => {
  const rows = [row(), row({status: "blocked", task_class: "blocker"}), row({done: true, status: "deferred"}),
    row({done: true, status: "done"}), row({acceptance_blocked: true}),
    row({has_resume: true, resume_ready: false, resume_evaluated: true}),
    row({has_resume: true, resume_ready: true, resume_evaluated: true, claimed: true, preferred: true})];
  const before = structuredClone(rows), result = project(rows), lanes = result.lanes as JsonObject;
  assert.deepEqual(lanes.open_items, [0, 1, 4, 5, 6]);
  assert.deepEqual(lanes.done_items, [3]);
  assert.deepEqual(lanes.deferred_items, [2]);
  assert.deepEqual(lanes.executable_items, [0, 6]);
  assert.deepEqual(lanes.blocker_items, [1]);
  assert.deepEqual(lanes.resume_blocked_items, [5]);
  assert.deepEqual(lanes.active_next_action_executable_items, [6]);
  assert.deepEqual(lanes.budgeted_items, [0, 1, 4, 5, 6, 2, 3]);
  assert.equal((result.work_counts as JsonObject).advancement, 2);
  assert.deepEqual(rows, before);
});

test("one observation time fences due, expiry, missing schedule and watch-only monitors", () => {
  const monitor = (fields: JsonObject) => row({task_class: "continuous_monitor", ...fields});
  const lanes = project([monitor({due_at: 100}), monitor({due_at: 101}),
    monitor({due_at: 10, expires_at: 100}), monitor({}), monitor({watch_only: true}),
    monitor({due_at: 90, acceptance_blocked: true})]).lanes as JsonObject;
  assert.deepEqual(lanes.monitor_due_items, [0]);
  assert.deepEqual(lanes.watch_only_monitor_items, [4]);
  assert.deepEqual(lanes.watch_only_monitor_due_items, []);
  assert.deepEqual(lanes.non_watch_only_monitor_due_items, [0]);
  assert.deepEqual(lanes.convergent_open_items, [0, 1, 2, 3, 5]);
  assert.deepEqual(lanes.monitor_schedule_gap_items, [3]);
  assert.deepEqual(lanes.monitor_items, [0, 1, 2, 3, 4]);
});

test("watch-only due monitors remain schedulable but are partitioned from ordinary due work", () => {
  const monitor = (fields: JsonObject) => row({task_class: "continuous_monitor", ...fields});
  const lanes = project([
    monitor({due_at: 90, watch_only: true}),
    monitor({due_at: 80}),
    row(),
  ]).lanes as JsonObject;
  assert.deepEqual(lanes.monitor_due_items, [0, 1]);
  assert.deepEqual(lanes.watch_only_monitor_due_items, [0]);
  assert.deepEqual(lanes.non_watch_only_monitor_due_items, [1]);
  assert.deepEqual(lanes.convergent_open_items, [1, 2]);
});

test("display ordering preserves stable legacy ties and Python Unicode ordering", () => {
  const lanes = project([row({sort: [1, 2, "", ""]}), row({sort: [0, 9, "", ""]}),
    row({sort: [1, 2, "", ""]}), row({sort: [1, 999999, "", "\u{10000}"]}),
    row({sort: [1, 999999, "", "\ue000"]})]).lanes as JsonObject;
  assert.deepEqual(lanes.projected_open_items, [1, 0, 2, 4, 3]);
});

test("invalid evaluated source fails before any lane can escape", () => {
  for (const fields of [{done: true}, {status: "unknown"}, {task_class: "unknown"},
    {has_resume: true}, {sort: [true, 1, "", ""]}, {due_at: Infinity}]) {
    assert.throws(() => project([row(fields)]));
  }
});

test("legacy fragments deduplicate identity and cannot invent unseen advancement", () => {
  const item = {identity: "todo_blocker", task_class: "blocker", actionable: false};
  const result = projectLegacyTodoWorkCounts({schema_version: "todo_work_counts_request_v0",
    rows: [item, item], source_open_count: 12});
  assert.equal(result.advancement, 0); assert.equal(result.hidden, 11); assert.equal(result.complete, false);
  assert.throws(() => countTodoWork([{taskClass: "blocker", actionable: false}], 0, true));
  assert.throws(() => countTodoWork([], Number.MAX_SAFE_INTEGER + 1, true));
});

test("production corpus classification uses every row, independent of display budget", () => {
  for (const native of [false, true]) {
    const fixture = productionScaleCoordinationFixture("goal-summary", native ? "native" : "legacy");
    const todos = fixture.projection.todos as JsonObject[];
    const result = project(todos.map((todo, ordinal) => row({status: todo.status, done: todo.done,
      task_class: todo.task_class, claimed: !!todo.claimed_by, sort: [1, ordinal, "", ""]})));
    assert.equal((result.lanes as JsonObject).budgeted_items instanceof Array, true);
    assert.equal(((result.lanes as JsonObject).budgeted_items as number[]).length, todos.length);
    assert.equal((result.work_counts as JsonObject).complete, true);
  }
});

test("conflicting display fragments cannot certify a complete source", () => {
  const result = projectLegacyTodoWorkCounts({schema_version: "todo_work_counts_request_v0", source_open_count: 1,
    rows: [{identity: "todo_same", task_class: "advancement_task", actionable: false},
      {identity: "todo_same", task_class: "advancement_task", actionable: true}]});
  assert.equal(result.complete, false);
});
