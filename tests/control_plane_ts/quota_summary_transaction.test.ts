import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {composeQuotaSummary, prepareQuotaSummary} from "../../loopx/control_plane/todos/quota_selection.ts";

function planning(): JsonObject {
  return {lanes: {
    all_open_items: [], display_open_items: [], executable_items: [], gate_items: [], monitor_items: [],
    monitor_due_items: [], watch_only_monitor_items: [], watch_only_monitor_due_items: [],
    non_watch_only_monitor_due_items: [], monitor_capability_blocked_due_items: [], monitor_schedule_gap_items: [],
    active_next_action_items: [], active_next_action_executable_items: [], claimed_open_items: [],
    other_agent_scoped_items: [], user_action_open_items: [], other_agent_bound_user_action_items: [], open_count: 0,
  }, resume_planning: {capacity_fields: null, deferred_lanes: {}, resume_blocked_lanes: {resume_blocked_count: 2}},
  work_counts: {open: 0}, source_completeness: {complete: true}, claim_visibility: {}, handoff_lanes: {}, route_lanes: {},
  closure_intent: null};
}
function request(source: JsonObject, fields: JsonObject = {}): JsonObject {
  return {summary_request: {source: {record: source}, source_rows: {}, kind: "user", integer_counts: {source: source.completed_without_successor_count == null ? null : String(source.completed_without_successor_count), warning: null}, ...fields},
    selection: {agent_id: "agent-a", user_gate_scope: false}, handoff_items: []};
}
function restored(wire: JsonObject, source: JsonObject): JsonObject {
  const result = structuredClone(wire.summary) as JsonObject;
  for (const copy of wire.source_copies as {source: (string | number)[]; target: (string | number)[]}[]) {
    let value: any = source, parent: any = result;
    for (const key of copy.source) value = value[key];
    for (const key of copy.target.slice(0, -1)) parent = parent[key];
    parent[copy.target.at(-1)!] = value;
  }
  return result;
}

test("source metadata and selected Vision proofs use references; planning counts remain normalized", () => {
  const source = {total_count: 17, done_count: 2, goal_acceptance_contract: {ratio: 0},
    vision_wait_states: [{agent_id: "agent-b"}, {agent_id: "agent-a", evidence: {revision: 3}}]};
  const wire = composeQuotaSummary(request(source), planning());
  assert.deepEqual((wire.source_copies as JsonObject[]).map(row => row.source),
    [["total_count"], ["done_count"], ["goal_acceptance_contract"], ["vision_wait_states", 1]]);
  const result = restored(wire, source);
  assert.equal(result.goal_acceptance_contract, source.goal_acceptance_contract);
  assert.deepEqual(result.vision_wait_states, [source.vision_wait_states[1]]);
  assert.equal(result.open_count, 0);
});

test("legacy assets omit user-only closure, warning and blocked-resume lanes", () => {
  const source = {total: "4", done: "3", completed_without_successor_count: 2};
  const p = planning(); p.closure_intent = {kind: "no_followup"};
  const result = restored(composeQuotaSummary(request(source, {legacy: true}), p), source);
  assert.equal(result.source_section, "project_asset");
  assert.equal(result.total_count, "4");
  assert.equal(result.done_count, "3");
  for (const key of ["closure_intent", "todo_succession_warning", "resume_blocked_count", "gate_open_items", "source_completeness"])
    assert.equal(Object.hasOwn(result, key), false, key);
});

test("summary admits one monitor while retaining the complete due count and bounded backlog", () => {
  const p = planning(), lanes = p.lanes as JsonObject;
  const items = Array.from({length: 20}, (_, index) => ({index, text: "Observe synthetic state"}));
  Object.assign(lanes, {monitor_due_items: items, watch_only_monitor_due_items: items,
    watch_only_monitor_items: items, display_open_items: items, executable_items: items});
  const source = {convergence_open_count: 3};
  const result = restored(composeQuotaSummary(request(source), p), source);
  assert.equal(result.monitor_due_count, 20);
  assert.equal((result.monitor_due_items as unknown[]).length, 1);
  assert.equal((result.backlog_items as unknown[]).length, 8);
  assert.equal((result.first_open_items as unknown[]).length, 3);
});

test("warning projection preserves source-only values while normalizing task class and scopes", () => {
  const source = {completed_without_successor_count: "2", completed_without_successor_items: [
    {index: 7, text: "Completed work", priority: "P1", status: "done", task_class: "advancement_task", completion_recovery: {revision: 9}},
  ]};
  const result = restored(composeQuotaSummary(request(source), planning()), source);
  assert.equal(result.completed_without_successor_count, 2);
  const items = result.completed_without_successor_items as JsonObject[];
  assert.equal(items[0]!.completion_recovery, source.completed_without_successor_items[0]!.completion_recovery);
  assert.equal(items[0]!.task_class, "advancement_task");
  assert.deepEqual((result.todo_succession_warning as JsonObject).items, items);
});

test("source provenance follows lane precedence even when equal identities carry conflicting facts", () => {
  const first = {todo_id: "todo_same", index: 1, text: "Preferred source", task_class: "user_gate"};
  const second = {...first, text: "Discarded source", task_class: "advancement_task"};
  const input = request({items: [second], first_open_items: [first]});
  const fact = (payload: JsonObject, gate: boolean) => ({payload, gate, presentation: [1, 999999, "", "todo_same"]});
  (input.summary_request as JsonObject).source_rows = {items: [fact(second, false)], first_open_items: [fact(first, true)]};
  const selected = (prepareQuotaSummary(input).selection as JsonObject).items as JsonObject[];
  assert.equal(selected.length, 1);
  assert.equal(selected[0]!.gate, true);
  assert.equal((selected[0]!.payload as JsonObject).text, "Preferred source");
});
