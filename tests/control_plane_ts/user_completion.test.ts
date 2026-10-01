import assert from "node:assert/strict";
import test from "node:test";
import {evaluateUserCompletion, planUserCompletion, requireCompletionDecisionOutcome} from "../../loopx/control_plane/todos/user_completion.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const scope = {schema_version: "decision_scope_v0", kind: "direction", granularity: "action", scope_key: "publish"};
const other = {...scope, scope_key: "announce"};
const target: JsonObject = {todo_id: "todo_target", role: "agent", task_class: "advancement_task",
  status: "blocked", done: false, claimed_by: "agent-a", required_decision_scopes: [scope]};
const gate: JsonObject = {todo_id: "todo_gate", role: "user", task_class: "user_gate", status: "open",
  decision_scope: scope, unblocks_todo_id: "todo_target"};

test("approval consumes only covered requirements and preserves independent rejection", () => {
  const rejection = {schema_version: "todo_decision_scope_outcome_v0", decision_scope: other,
    outcome: "reject", source_todo_id: "todo_other_gate"};
  const current = {...target, required_decision_scopes: [scope, other], decision_scope_outcomes: [rejection]};
  const plan = planUserCompletion(gate, [current, gate], "approve");
  assert.deepEqual(plan.updates.required_decision_scopes, [other]);
  assert.deepEqual(plan.updates.decision_scope_outcomes, [rejection]);
  assert.equal(plan.updates.status, undefined);
  assert.equal(plan.unblock_resume?.state, "decision_requirements_remaining");
  assert.deepEqual(current.required_decision_scopes, [scope, other], "planning must not mutate its snapshot");
});

test("another exact User blocker prevents resume, unrelated work does not", () => {
  const remaining = {...gate, todo_id: "todo_other"};
  assert.equal(planUserCompletion(gate, [target, gate, remaining], "approve").unblock_resume?.state, "other_user_blockers_active");
  assert.equal(planUserCompletion(gate, [target, gate, {...remaining, unblocks_todo_id: "todo_elsewhere"}], "approve").unblock_resume?.state, "resumed");
  assert.equal(planUserCompletion(gate, [target, gate, {...remaining, status: "done", done: true}], "approve").unblock_resume?.state, "resumed");
});

test("an admitted deferred User gate still carries its explicit decision effects", () => {
  const deferred = {...gate, status: "deferred", done: true, resume_when: "owner_decision_ready"};
  const plan = planUserCompletion(deferred, [target, deferred], "approve");
  assert.equal(plan.updates.status, "open");
  assert.deepEqual(plan.updates.required_decision_scopes, []);
  assert.equal(plan.unblock_resume?.state, "resumed");
  assert.equal(plan.decision_scope_resolution?.state, "resolved");
});

for (const outcome of ["approve", "reject", "cancel"] as const) {
  test(`${outcome} cannot revive terminal, deferred or archived work`, () => {
    for (const change of [{status: "done", done: true}, {status: "deferred", done: true}, {archive_state: "archive"}]) {
      const plan = planUserCompletion(gate, [{...target, ...change}, gate], outcome);
      assert.deepEqual(plan.updates, {});
    }
    assert.deepEqual(planUserCompletion({...gate, status: "done", done: true}, [target], outcome).updates, {},
      "a second completion is not fresh approval authority");
  });
}

test("User action completion and explicit blocker repair remain distinct", () => {
  const action = {...gate, task_class: "user_action"};
  assert.equal(planUserCompletion(action, [target], null).unblock_resume?.state, "decision_requirements_remaining");
  const ready = {...target, required_decision_scopes: []};
  assert.equal(planUserCompletion(action, [ready], null).unblock_resume?.state, "resumed");
  assert.equal(planUserCompletion(action, [{...ready, task_class: "blocker"}], null).unblock_resume?.state, "explicit_blocker_repair_required");
  assert.deepEqual(planUserCompletion({...gate, role: "agent"}, [ready], "approve").updates, {});
});


test("the newest exact-scope rejection replaces its prior outcome in place", () => {
  const first = {decision_scope: scope, outcome: "reject", source_todo_id: "todo_prior"};
  const independent = {decision_scope: other, outcome: "reject", source_todo_id: "todo_independent"};
  const plan = planUserCompletion(gate, [{...target, decision_scope_outcomes: [first, independent]}], "cancel");
  const outcomes = plan.updates.decision_scope_outcomes as JsonObject[];
  assert.equal(outcomes.length, 2);
  assert.equal(outcomes[0].outcome, "cancel");
  assert.equal(outcomes[0].source_todo_id, gate.todo_id);
  assert.deepEqual(outcomes[1], independent);
  const approved = {...target, required_decision_scopes: [], decision_scope_outcomes: [{...first, outcome: "approve"}]};
  assert.equal(planUserCompletion({...gate, task_class: "user_action"}, [approved], null).unblock_resume?.state, "resumed");
});

test("malformed decision history cannot be treated as an empty approval history", () => {
  assert.throws(() => planUserCompletion(gate, [{...target, decision_scope_outcomes: "invalid"}], "approve"),
    /decision_scope_outcomes must be an array/);
});

test("ordinary User cancellation leaves the dependent and its authority untouched", () => {
  const action = {...gate, task_class: "user_action", bound_agent: "agent-a"};
  for (const current of [target, {...target, required_decision_scopes: []}]) {
    const before = structuredClone(current);
    const plan = planUserCompletion(action, [current], "cancel");
    assert.deepEqual(plan.updates, {});
    assert.equal(plan.decision_scope_resolution, null);
    assert.equal(plan.unblock_resume?.state, "decision_cancelled");
    assert.equal(plan.unblock_resume?.changed, false);
    assert.deepEqual(current, before);
  }
});

test("shared completion bridge permits action cancellation but not invented approval", () => {
  const request = {schema_version: "todo_user_completion_request_v0", todos: [],
    source: {...gate, task_class: "user_action"}, decision_outcome: "cancel"};
  assert.doesNotThrow(() => evaluateUserCompletion(request));
  for (const outcome of ["approve", "reject"]) {
    assert.throws(() => evaluateUserCompletion({...request, decision_outcome: outcome}),
      /user_gate or cancelling a user_action/);
  }
  assert.throws(() => evaluateUserCompletion({...request, source: gate, decision_outcome: null}),
    /user_gate completion requires/);
  assert.throws(() => evaluateUserCompletion({...request, source: gate, materialized: false}),
    /must first materialize/);
});

test("native Gate closure without a decision preserves requirements and grants no approval", () => {
  const before = structuredClone(target);
  assert.equal(requireCompletionDecisionOutcome(gate, null), null);
  assert.deepEqual(planUserCompletion(gate, [target, gate], null), {
    updates: {}, unblock_resume: null, decision_scope_resolution: null,
  });
  assert.deepEqual(target, before);
});
