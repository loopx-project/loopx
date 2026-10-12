import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {evaluateTodoResumeConditions, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION} from "../../loopx/control_plane/todos/resume_condition.ts";
import {todoExecutionDependencyRejection} from "../../loopx/control_plane/coordination/todo_execution_dependency.ts";
import {prepareBlockedWait, isCausalBlockedWait, projectReceiptBoundWait,
  BLOCKED_WAIT_REQUEST_SCHEMA, RECEIPT_BOUND_WAIT_REQUEST_SCHEMA} from "../../loopx/control_plane/quota/blocked_wait.ts";
import {projectTodoResumePlanning, RESUME_PLANNING_REQUEST} from "../../loopx/control_plane/todos/resume_planning.ts";
import {projectDeliveryResponse} from "../../loopx/control_plane/work_items/delivery_history.ts";

const now = "2026-01-01T00:00:00Z";
function todo(id: string, target?: string, patch: JsonObject = {}): JsonObject {
  return {todo_id: `todo_${id}`, role: "agent", status: "open", archive_state: "active",
    task_class: "advancement_task", claimed_by: `agent-${id}`, text: `Work ${id}`,
    ...(target ? {resume_when: `todo_done:todo_${target}`, resume_ready: false} : {}), ...patch};
}
function evaluate(rows: JsonObject[]): JsonObject[] {
  const result = evaluateTodoResumeConditions({schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: rows, source_items: rows});
  return rows.map(row => {
    const condition = (result.conditions as JsonObject[]).find(entry => entry.todo_id === row.todo_id)?.condition as JsonObject;
    return condition ? {...row, resume_condition: condition, resume_ready: condition.satisfied} : row;
  });
}
function cycle(): JsonObject[] {
  return [todo("alpha", "bravo"), todo("bravo", "charlie"), todo("charlie", "alpha")];
}
function waitRequest(rows: JsonObject[]): JsonObject {
  return {schema_version: BLOCKED_WAIT_REQUEST_SCHEMA, todo_id: "todo_alpha", todos: rows, observed_at: now};
}

test("completion cycles and their dependents share the execution diagnosis across owners", () => {
  const rows = [todo("entry", "alpha"), ...cycle()];
  const before = structuredClone(rows);
  for (const row of evaluate(rows)) {
    const condition = row.resume_condition as JsonObject;
    assert.equal(condition.invalid_state, "dependency_cycle");
    assert.equal(condition.availability_reason, "resume_condition_invalid");
    assert.equal(row.resume_ready, false);
    assert.deepEqual(condition.dependency_cycle_todo_ids, ["todo_alpha", "todo_bravo", "todo_charlie"]);
    const rejection = todoExecutionDependencyRejection(new Map(rows.map(row => [String(row.todo_id), row])), String(row.todo_id), new Date(now));
    assert.equal(rejection?.code, "todo_dependency_invalid");
    assert.deepEqual(rejection?.condition, condition);
  }
  assert.deepEqual(rows, before);
});

test("completion facts cut historical edges; partial sources and other wait kinds cannot invent cycles", () => {
  for (const rows of [
    [todo("alpha", "bravo"), todo("bravo", "alpha", {status: "done", archive_state: "archive"})],
    [todo("alpha", "bravo"), todo("bravo", "charlie"), todo("charlie", "alpha", {status: "done"})],
    [todo("alpha", "bravo"), todo("bravo", "missing")],
    [todo("alpha", "bravo"), todo("bravo", undefined, {resume_when: "monitor_changed:todo_alpha"})],
    [todo("alpha", "bravo"), todo("bravo"), todo("other", "third"), todo("third", "other")],
  ]) {
    const condition = evaluate(rows)[0].resume_condition as JsonObject;
    assert.equal(condition.invalid_state, undefined);
    assert.equal(condition.satisfied, rows[1].status === "done");
  }
  const self = evaluate([todo("alpha", "alpha")])[0].resume_condition as JsonObject;
  assert.equal(self.invalid_state, "dependency_self_reference");
});

test("deferred waits, normalized tokens and bounded cycle evidence retain all affected diagnoses", () => {
  const rows = Array.from({length: 2000}, (_, i) => todo(`node_${i}`, `node_${(i + 1) % 2000}`, {status: "deferred"}));
  rows[0].resume_when = " TODO_DONE:TODO_NODE_1 ";
  const projected = evaluate(rows.reverse());
  assert.ok(projected.every(row => (row.resume_condition as JsonObject).invalid_state === "dependency_cycle"));
  const condition = projected[0].resume_condition as JsonObject;
  assert.equal(condition.dependency_cycle_size, 2000);
  assert.ok((condition.dependency_cycle_todo_ids as string[]).length <= 8);
});

test("fresh closeout rejects a cycle even when its projected direct condition is stale", () => {
  const old = evaluate([todo("alpha", "bravo"), todo("bravo")])[0];
  assert.throws(() => prepareBlockedWait(waitRequest([old, ...cycle().slice(1)])), /cycle/i);
  assert.throws(() => prepareBlockedWait(waitRequest(evaluate(cycle()))), /cycle/i);
});

test("historical pair-only causal receipts retain their original readback semantics", () => {
  for (const target of ["alpha", "charlie"]) {
    const frozen = {schema_version: "quota_blocked_causal_wait_v0", source: "todo",
      todo_id: "todo_alpha", resume_when: "todo_done:todo_bravo", observed_at: now,
      waiting_todo: todo("alpha", "bravo"), target_todo: todo("bravo", target)};
    assert.equal(isCausalBlockedWait(frozen, "todo_alpha"), true);
    assert.equal(isCausalBlockedWait(frozen, "todo_other"), false);
    assert.equal(isCausalBlockedWait({...frozen, target_todo: {...frozen.target_todo, status: "done"}}, "todo_alpha"), false);
  }
});

test("a bound Turn exposes dependency repair without changing its binding or qualifying a wait", () => {
  const request = {...waitRequest(evaluate(cycle())), schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA,
    agent_id: "agent-alpha", turn_instance_id: "turn-cycle"};
  const before = structuredClone(request);
  const result = projectReceiptBoundWait(request);
  assert.equal(result.status, "recovery_required");
  assert.deepEqual(result.recovery, {schema_version: "unsettled_host_turn_recovery_v0", scope: "current_turn",
    turn_instance_id: "turn-cycle", binding_kind: "todo", binding_id: "todo_alpha", repair: "dependency"});
  assert.equal((result.obligation as JsonObject).delivery_allowed, false);
  assert.equal((result.obligation as JsonObject).next_lane, "advancement_task");
  assert.deepEqual(request, before);
});

for (const state of ["spend_required", "spend_receipt_required"]) {
  test(`a later cycle preserves the original Turn's verified ${state} settlement`, () => {
    const input = {...waitRequest(evaluate(cycle())), schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA,
      agent_id: "agent-alpha", turn_instance_id: "turn-cycle",
      settlement_progress: {schema_version: "quota_settlement_progress_v0", state, next_step: "quota_spend"}};
    const before = structuredClone(input);
    const result = projectReceiptBoundWait(input);
    assert.deepEqual(result.recovery, {schema_version: "unsettled_host_turn_recovery_v0", scope: "current_turn",
      turn_instance_id: "turn-cycle", binding_kind: "todo", binding_id: "todo_alpha", repair: "quota_spend"});
    assert.equal((result.obligation as JsonObject).spend_allowed_now, true);
    assert.equal((result.obligation as JsonObject).delivery_allowed, false);
    // Settling verified past work does not qualify the current cycle as a wait.
    assert.throws(() => prepareBlockedWait({...input, schema_version: BLOCKED_WAIT_REQUEST_SCHEMA}), /cycle/i);
    assert.deepEqual(input, before);
  });
}

test("a cycle still requires dependency repair without applicable verified spend progress", () => {
  for (const progress of [null, {},
    {schema_version: "quota_settlement_progress_v0", state: "writeback_required", next_step: "durable_writeback"},
    {schema_version: "quota_settlement_progress_v0", state: "writeback_receipt_required", next_step: "durable_writeback"},
    {schema_version: "quota_settlement_progress_v0", state: "spend_required", next_step: "durable_writeback"},
    {schema_version: "quota_settlement_progress_v0", state: "settled", next_step: null},
    {schema_version: "other", state: "spend_required", next_step: "quota_spend"},
  ]) {
    const result = projectReceiptBoundWait({...waitRequest(evaluate(cycle())),
      schema_version: RECEIPT_BOUND_WAIT_REQUEST_SCHEMA, agent_id: "agent-alpha",
      turn_instance_id: "turn-cycle", settlement_progress: progress});
    assert.equal((result.recovery as JsonObject).repair, "dependency");
    assert.equal((result.obligation as JsonObject).spend_allowed_now, undefined);
    assert.equal((result.obligation as JsonObject).delivery_allowed, false);
  }
});

test("cycle planning requests scoped replan instead of quiet successor waiting", () => {
  const rows = evaluate(cycle());
  const items = rows.map((row, index) => ({payload: row, id: row.todo_id, status: row.status,
    claim: row.claimed_by, excluded: [] as string[], done: false, resume: row.resume_when,
    ready: false, ready_truthy: false, priority: 1, index,
    target_id: (row.resume_condition as JsonObject).target_todo_id, target_status: "open", target_class: "advancement_task"}));
  const request = {schema_version: RESUME_PLANNING_REQUEST, agent_id: "agent-alpha", item_limit: 1,
    has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null, available_capabilities: null,
    sources: {items, backlog_items: [], first_open_items: [], deferred_items: [], deferred_resume_candidates: [],
      resume_blocked_items: [], monitor_open_items: [], current_agent_claimed_monitor_items: [], claimed_monitor_open_items: []}};
  const result = projectTodoResumePlanning(request);
  assert.deepEqual(result.blocked_successor_items, []);
  const replan = result.dependency_cycle_replan as JsonObject;
  assert.equal(replan.count, 1);
  assert.deepEqual((replan.candidates as JsonObject[]).map(row => row.todo_id), ["todo_alpha"]);
  assert.equal(projectTodoResumePlanning({...request, agent_id: "agent-other"}).dependency_cycle_replan, null);
  items[0].excluded = ["agent-alpha"];
  assert.equal(projectTodoResumePlanning(request).dependency_cycle_replan, null);
});

test("a cyclic current wait cannot exempt a bound blocker from delivery supervision", () => {
  const row = evaluate(cycle())[0];
  const result = projectDeliveryResponse({todo: row, agent_id: "agent-alpha", run_agent_id: "agent-alpha",
    run: {todo_id: row.todo_id, replan_obligation_id: "", delivery_outcome: "outcome_gap",
      delivery_batch_scale: "implementation", delivery_turn_kind: "", outcome_followthrough_required: true,
      progress_observation: {schema_version: "typed_progress_observation_v0", result_class: "blocked",
        work_item_id: row.todo_id, blocker_id: "blocker_dependency", evidence_ids: ["evidence_dependency"]}}});
  assert.equal(result.outcome_floor_applicable, true);
  assert.equal(result.reason, "history_supervision");
});
