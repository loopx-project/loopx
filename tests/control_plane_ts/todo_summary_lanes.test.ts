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

import {projectTodoContextPage, projectTodoSummaryDisplay} from "../../loopx/control_plane/todos/context_projection.ts";

test("display source lanes preserve first identity, legacy coordinates and explicit readiness", () => {
  const record = {active_next_action_items: [{todo_id: "todo_first", text: " First ", status: "open", resume_when: "todo_done:todo_gate"}],
    items: [{todo_id: "todo_first", text: "Duplicate identity"}, {index: 7, text: " Legacy "},
      {index: 7, text: "Legacy"}, {text: "Completed", done: true}, {text: "  "},
      {todo_id: "todo_explicit", text: "Explicit wait", resume_when: "todo_done:todo_gate", resume_ready: false}],
    blocker_items: [{todo_id: "todo_blocker", text: "Blocked", status: "blocked"}]};
  const before = structuredClone(record);
  const rows = projectTodoSummaryDisplay({source: {record}, handoff_gates: [{gate_state: "cleared_with_successor",
    successor_todo_ids: ["todo_first", "todo_explicit"]}]});
  assert.deepEqual(rows.map(row => row.text), ["First", "Blocked", "Legacy", "Explicit wait"]);
  assert.equal(rows[0]!.resume_ready, true);
  assert.deepEqual(rows[0]!.resume_condition, {schema_version: "todo_resume_condition_v0", resume_when: "todo_done:todo_gate",
    satisfied: true, source: "handoff_gate_cleared_with_successor"});
  assert.equal(rows[3]!.resume_ready, false);
  assert.deepEqual(record, before);
});

test("planning keeps distinct domain states and terminal frontier evidence, without presentation-lane expansion", () => {
  const record = {items: [{todo_id: "todo_open", text: "Open", depends_on_todo_ids: ["todo_dependency"]},
      {todo_id: "todo_done", text: "Complete", status: "done", no_followup: true, note: "Evidence tail"},
      {todo_id: "todo_x", text: "Legacy short id", status: "done"}],
    deferred_items: [{todo_id: "todo_wait", text: "Wait", status: "deferred"}],
    blocker_items: [{todo_id: "todo_open", text: "Duplicate"}],
    monitor_open_items: [{todo_id: "todo_monitor", text: "Monitor", task_class: "continuous_monitor"}],
    first_open_items: [{todo_id: "todo_display", text: "Only a display lane"}]};
  const plain = projectTodoSummaryDisplay({source: {record}, include_terminal: false});
  assert.deepEqual(plain.map(row => row.todo_id), ["todo_open", "todo_wait", "todo_monitor"]);
  assert.equal(plain[0]!.depends_on_todo_ids, undefined);
  const terminal = projectTodoSummaryDisplay({source: {record}, include_terminal: true});
  assert.deepEqual(terminal.map(row => row.todo_id), ["todo_open", "todo_done", "todo_x", "todo_wait", "todo_monitor"]);
  assert.deepEqual(terminal[0]!.depends_on_todo_ids, ["todo_dependency"]);
  assert.equal(terminal[1]!.note, "Evidence tail");
});

test("summary source identity keeps raw IDs and strict legacy coordinates", () => {
  const cases: {name: string; items: JsonObject[]; selected: number[]}[] = [
    {name: "raw identities and anonymous collisions", items: [
      {todo_id: "todo_same", index: 1, text: " First "},
      {todo_id: "todo_same", index: 2, text: "Duplicate id"},
      {todo_id: " todo_same ", index: 1, text: "Second spelling"},
      {todo_id: "todo_a", index: 7, text: " Same "},
      {todo_id: "todo_b", index: 7, text: "Same"},
      {index: 7, text: "Same"}, {index: 8, text: " Legacy "},
      {todo_id: "todo_c", index: 8, text: "Legacy"},
      {index: 9, text: "Legacy"}], selected: [0, 2, 3, 4, 6, 8]},
    {name: "strict coordinates", items: [
      {index: 2, text: "Coordinate"}, {index: "2", text: "Coordinate"},
      {text: "Missing"}, {index: null, text: "Missing"},
      {index: NaN, text: "Nonfinite"}, {index: NaN, text: "Nonfinite"},
      {index: 0, text: "Zero"}, {index: -0, text: "Zero"},
      {index: 2, text: "Coordinate"}], selected: [0, 1, 2, 3, 4, 5, 6]},
    {name: "truthy and falsy raw identities", items: [
      {todo_id: 1, index: 1, text: "Number id"},
      {todo_id: true, index: 2, text: "Boolean id"},
      {todo_id: 1, index: 3, text: "Same number id"},
      {todo_id: 0, index: 4, text: "Zero id"},
      {todo_id: 0, index: 5, text: "Zero id"},
      {todo_id: "todo_falsy", index: 6, text: "False id collision"},
      {todo_id: false, index: 6, text: "False id collision"},
      {todo_id: "", index: 7, text: "Empty id"},
      {todo_id: null, index: 7, text: "Empty id"}], selected: [0, 1, 3, 4, 5, 7]},
  ];
  for (const {name, items, selected} of cases) {
    const before = structuredClone(items), visited: number[] = [];
    const source = {record: {items}};
    const rows = projectTodoSummaryDisplay({source}, (_key, index) => visited.push(index));
    assert.deepEqual(visited, selected, name);
    assert.equal(rows.length, selected.length, name);
    assert.deepEqual(projectTodoContextPage({summary_display: {source}}).items, rows, name);
    assert.deepEqual(items, before, name);
  }
});

test("flat summary items keep duplicate rows without source selection", () => {
  const record = {todo_id: "todo_same", text: "Same row"};
  const result = projectTodoContextPage({summary_display: {items: [{record}, {record}]}});
  assert.equal((result.items as JsonObject[]).length, 2);
});
