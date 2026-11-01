import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import { projectVisionWaitCoverage, projectGoalVisionWaitState } from "../../loopx/control_plane/goals/vision_wait_coverage.ts";
import {evaluateTodoResumeConditions} from "../../loopx/control_plane/todos/resume_condition.ts";
import {projectBlockedSuccessorWaitObservation} from "../../loopx/control_plane/work_items/interaction_contract.ts";

const facts = (extra: Record<string, unknown> = {}) => ({
  causal_todo_ids: ["todo_a", "todo_b"], waiting_todo_ids: ["todo_a"],
  blocker_todo_ids: [], edges: [], ...extra,
});

test("one waiting path cannot cover another acceptance obligation", () => {
  assert.deepEqual(projectVisionWaitCoverage(facts()).uncovered_todo_ids, ["todo_b"]);
  assert.equal(projectVisionWaitCoverage(facts({ waiting_todo_ids: ["todo_a", "todo_b"] })).covered, true);
});

test("shared prerequisites do not turn sibling paths into substitutes", () => {
  assert.equal(projectVisionWaitCoverage(facts({
    causal_todo_ids: ["todo_b"], edges: [["todo_recovery", "todo_a"], ["todo_recovery", "todo_b"]],
  })).covered, false);
  assert.equal(projectVisionWaitCoverage(facts({
    causal_todo_ids: ["todo_recovery"], edges: [["todo_recovery", "todo_a"]],
  })).covered, true);
});

test("explicit successors carry lineage through replaced or completed predecessors", () => {
  assert.equal(projectVisionWaitCoverage(facts({
    causal_todo_ids: ["todo_old"], edges: [["todo_old", "todo_next"], ["todo_next", "todo_a"]],
  })).covered, true);
  assert.equal(projectVisionWaitCoverage(facts({ causal_todo_ids: ["todo_done"] })).covered, false);
});

test("every root needs coverage even when one has a concrete blocker", () => {
  assert.equal(projectVisionWaitCoverage(facts({ waiting_todo_ids: [], blocker_todo_ids: ["todo_a"] })).covered, false);
  assert.equal(projectVisionWaitCoverage(facts({ waiting_todo_ids: ["todo_b"], blocker_todo_ids: ["todo_a"] })).covered, true);
});

test("cycles terminate and unrelated edges and input order do not change coverage", () => {
  const edges = [["todo_a", "todo_b"], ["todo_b", "todo_a"], ["todo_other", "todo_remote"]];
  const a = projectVisionWaitCoverage(facts({ edges }));
  const b = projectVisionWaitCoverage(facts({ edges: [...edges].reverse(), causal_todo_ids: ["todo_b", "todo_a"] }));
  assert.deepEqual(a, b);
  assert.equal(projectVisionWaitCoverage(facts({ edges, waiting_todo_ids: [] })).covered, false);
});

test("unbound acceptance and malformed facts cannot prove a wait", () => {
  assert.equal(projectVisionWaitCoverage(facts({ causal_todo_ids: [] })).covered, false);
  for (const patch of [{ edges: [["todo_a"]] }, { edges: null }, { waiting_todo_ids: [null] }]) {
    assert.throws(() => projectVisionWaitCoverage(facts(patch)));
  }
});

function scheduledFrontier(resumeOverrides: Record<number, string> = {}): JsonObject {
  const todos = Array.from({length: 7}, (_, index) => ({todo_id: `todo_scheduled_${index}`,
    text: "Deliver scheduled public work.", role: "agent", status: "deferred",
    task_class: "advancement_task", claimed_by: "agent-a",
    resume_when: resumeOverrides[index] ?? `resume_at:2099-01-0${7 - index}T00:00:00Z`}));
  const conditions = evaluateTodoResumeConditions({schema_version: "todo_resume_evaluation_request_v0",
    items: todos, source_items: [], available_capabilities: [], evaluated_at: "2026-01-01T00:00:00Z"}).conditions as JsonObject[];
  const records = todos.map((todo, index) => ({payload: {...todo, resume_ready: false,
    resume_condition: conditions[index].condition}, id: todo.todo_id, status: todo.status,
    claim: todo.claimed_by, excluded: [], done: false, resume: todo.resume_when,
    ready: false, ready_truthy: false, priority: 1, index, target_id: null,
    target_status: null, target_class: todo.task_class}));
  const refs = records.map((_, index) => index);
  return {agent_id: "agent-a", selectable_advancement_count: 0, agent_todo_summary: {},
    acceptance_gaps: [{kind: "vision_acceptance_gap", vision_todo_ids: todos.map(todo => todo.todo_id)}],
    lineage_source_items: todos, resume_planning_request: {
      schema_version: "todo_resume_planning_request_v1", agent_id: "agent-a", item_limit: 2,
      has_deferred_count: false, has_visible_deferred_count: false, deferred_count: null,
      available_capabilities: null, records, sources: {items: refs, deferred_items: refs,
        backlog_items: [], first_open_items: [], deferred_resume_candidates: [], resume_blocked_items: [],
        monitor_open_items: [], current_agent_claimed_monitor_items: [], claimed_monitor_open_items: []}}};
}

test("all canonical waits qualify before display limits and the earliest clock drives rechecks", () => {
  const input = scheduledFrontier(), before = structuredClone(input);
  const wait = projectGoalVisionWaitState(input)!;
  assert.equal(wait.waiting_todo_count, 7);
  assert.equal((wait.waiting_todo_ids as string[]).length, 5);
  assert.equal(wait.pending_resume_count, 7);
  assert.equal(wait.selected_todo_id, "todo_scheduled_6");
  assert.equal(wait.resume_when, "resume_at:2099-01-01T00:00:00Z");
  const request = {effective_action: "agent_scope_wait", agent_id: "agent-a", vision_wait_state: wait};
  assert.deepEqual(projectBlockedSuccessorWaitObservation(request), {required: false});
  assert.deepEqual(input, before);
  for (const patch of [
    {heartbeat_receipt: {status: "committed", settlement_identity: {todo_id: "todo_scheduled_0"}}},
    {heartbeat_receipt: {status: "replayed", pending_action_selection: {}}},
    {agent_id: "agent-b"},
    ...[undefined, 0, 6, "7", 7.5].map(pending_resume_count => ({vision_wait_state: {...wait, pending_resume_count}})),
  ]) assert.deepEqual(projectBlockedSuccessorWaitObservation({...request, ...patch}), {required: true});
});

test("unsupported or missing sources cannot relax automatic-wait supervision", () => {
  for (const resume of ["capacity_available:compiler", "todo_done:todo_missing_source"]) {
    const wait = projectGoalVisionWaitState(scheduledFrontier({6: resume}))!;
    assert.equal(wait.waiting_todo_count, 7);
    assert.equal(wait.pending_resume_count, 6);
    assert.deepEqual(projectBlockedSuccessorWaitObservation({effective_action: "agent_scope_wait",
      agent_id: "agent-a", vision_wait_state: wait}), {required: true});
  }
  assert.equal(projectGoalVisionWaitState({...scheduledFrontier(), selectable_advancement_count: 1}), null);
});
