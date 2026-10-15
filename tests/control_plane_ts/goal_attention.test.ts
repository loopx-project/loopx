import assert from "node:assert/strict";
import {test} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {boundGoalAttention, projectGoalAttention, reconcileGoalAttention} from "../../loopx/control_plane/presentation/goal_attention.ts";
import {hasGoalFailureStatus} from "../../loopx/control_plane/presentation/goal_failure_status.ts";

test("current state repairs stale routing without rewriting historical execution", () => {
  const item = {goal_id: "alpha", source: "latest_run", status: "state_refreshed",
    waiting_on: "codex", severity: "action", recommended_action: "Obsolete step"};
  const current = {...item, source: "active_state", status: "active_state_agent_todo",
    recommended_action: "Verify the current deliverable"};
  const warning = {requires_refresh_state: true, latest_run_generated_at: "2026-01-01T00:00:00Z"};
  const result = reconcileGoalAttention({item, active_state_item: current, warning});
  assert.deepEqual(result.item, current);
  assert.equal(result.warning, null);
  assert.equal(warning.latest_run_generated_at, "2026-01-01T00:00:00Z");
  assert.equal(item.recommended_action, "Obsolete step");
  for (const protectedItem of [
    {...item, severity: "high"}, {...item, waiting_on: "controller"},
    {...item, waiting_on: "user_or_controller"}, {...item, status: "runtime_error"},
    {...item, source: "registry"},
  ]) {
    assert.deepEqual(reconcileGoalAttention({item: protectedItem, active_state_item: current, warning}),
      {item: protectedItem, warning}, "a projection repair cannot remove a failure or an owner gate");
  }
  assert.deepEqual(reconcileGoalAttention({item, active_state_item: current, warning,
    historical_run: {delivery_outcome: "outcome_gap"}}), {item, warning});
  const mixedFailure = {...item, status: "runtime_error"};
  assert.equal(hasGoalFailureStatus([mixedFailure.status]), true);
  assert.equal(hasGoalFailureStatus(["verified_delivery", null]), false);
  assert.deepEqual(reconcileGoalAttention({item: mixedFailure, active_state_item: current, warning,
    historical_run: {classification: "runtime_error", delivery_outcome: "outcome_progress"}}),
  {item: mixedFailure, warning}, "partial progress does not resolve a runtime failure");
  assert.equal(reconcileGoalAttention({item: {...item, status: "verified_delivery"},
    active_state_item: current, warning, historical_run: {delivery_outcome: "outcome_progress"}}).warning, null);
  for (const unavailable of [null, {...current, goal_id: "other"}, {...current, source: "latest_run"}]) {
    assert.deepEqual(reconcileGoalAttention({item, active_state_item: unavailable, warning}),
      {item, warning}, "missing or unrelated current facts cannot certify repair");
  }
  const currentGate = {...current, status: "active_state_user_gate", waiting_on: "controller"};
  const repairedGate = reconcileGoalAttention({item, active_state_item: currentGate, warning});
  assert.deepEqual(repairedGate.item, currentGate);
  assert.equal(repairedGate.warning, null, "a current owner decision is preserved without a spurious repair request");
});

const gate = {todo_id: "todo_gate", role: "user", task_class: "user_gate",
  status: "blocked", text: "Choose the release window", note: "Only the reviewed build",
  evidence: "https://example.org/build", updated_at: "2026-10-02T00:00:00Z"};
const blocked = {blocker_identity: "todo:todo_gate", blocker_revision: "revision-a",
  task: {todo_id: "todo_gate", text: gate.text}, cause: "Awaiting a decision",
  owner_must_know: true, owner_must_act: true, responsible_party: "owner",
  recovery_condition: {satisfied: false}, delivery: {state: "pending"}};

test("one subject retains both the decision and blocker without inventing delivery", () => {
  const result = projectGoalAttention({goal_id: "alpha", todos: [gate], blockers: [blocked]});
  assert.equal(result.items.length, 1);
  assert.deepEqual(result.items[0].request, {request_id: gate.todo_id, text: gate.text,
    reason: gate.note, evidence: gate.evidence});
  assert.deepEqual(result.items[0].blocker, blocked);
  assert.equal(result.items[0].owner_must_act, true);
  assert.deepEqual(result.coverage, {known: 1, included: 1, omitted: 0});
});

test("agent-owned blockers remain visible beside owner decisions and quiet work", () => {
  const agentBlocker = {...blocked, blocker_identity: "todo:todo_work",
    task: {todo_id: "todo_work"}, owner_must_act: false, responsible_party: "agent"};
  const result = projectGoalAttention({goal_id: "alpha", todos: [
    {...gate, status: "open"}, {...gate, todo_id: "todo_routine", role: "agent"},
    {...gate, todo_id: "todo_done", status: "done"},
    {...gate, todo_id: "todo_old", superseded_by: "todo_gate"},
    {...gate, todo_id: "todo_later", status: "deferred"},
  ], blockers: [agentBlocker]});
  assert.deepEqual(result.items.map(item => [item.todo_id, item.owner_must_act]),
    [["todo_gate", true], ["todo_work", false]]);
});

test("separate identities survive same titles; redaction and overflow stay explicit", () => {
  const result = projectGoalAttention({goal_id: "alpha", todos: [gate,
    {...gate, todo_id: "todo_second"}, {...gate, todo_id: "todo_secret", content_redacted: true},
    {...gate, todo_id: "todo_long", text: "x".repeat(901)},
  ], blockers: []});
  assert.equal(result.items.length, 4);
  assert.equal(result.items[2].request, null);
  assert.equal((result.items[2].incomplete as JsonObject[])[0].reason_code, "content_redacted");
  assert.equal((result.items[3].incomplete as JsonObject[])[0].reason_code, "content_overflow");
});

test("bounds preserve known omitted work and never let a foreign Goal supply terms", () => {
  const result = projectGoalAttention({goal_id: "alpha", limit: 1,
    todos: [gate, {...gate, todo_id: "todo_second"}, {...gate, goal_id: "foreign"}], blockers: []});
  assert.deepEqual(result.coverage, {known: 2, included: 1, omitted: 1});
  assert.throws(() => projectGoalAttention({goal_id: "alpha", limit: 0, todos: [], blockers: []}));
});

test("owner decisions cannot be hidden behind a bounded prefix of agent blockers", () => {
  const blockers = Array.from({length: 12}, (_, index) => ({...blocked,
    blocker_identity: `todo:work_${index}`, task: {todo_id: `work_${index}`},
    owner_must_act: false}));
  const result = projectGoalAttention({goal_id: "alpha", todos: [gate], blockers});
  assert.equal(result.items[0].todo_id, gate.todo_id);
  assert.deepEqual(result.coverage, {known: 13, included: 8, omitted: 5});
});

test("the whole Turn is bounded and later owner decisions outrank earlier information", () => {
  const goals = Array.from({length: 128}, (_, index) => ({goal_id: `goal_${index}`,
    attention: {status: "read", items: Array.from({length: 8}, (_, item) => ({
      todo_id: `todo_${index}_${item}`, owner_must_act: index === 127,
      request: index === 127 ? {text: gate.text, reason: gate.note} : null,
    })), coverage: {known: 10, included: 8, omitted: 2}}}));
  const result = boundGoalAttention({goals});
  const all = result.goals.flatMap(goal => (goal.attention as typeof goals[0]["attention"]).items);
  assert.equal(all.length, 12);
  assert.equal(all.filter(item => item.owner_must_act).length, 8);
  assert.deepEqual(result.goals[127].attention, goals[127].attention);
  assert.deepEqual((result.goals[1].attention as typeof goals[0]["attention"]).coverage,
    {known: 10, included: 0, omitted: 10});
  assert.throws(() => boundGoalAttention({goals, limit: 0}));
});

test("directory previews bound nested bodies while exact identities and full input survive", () => {
  const long = "📚".repeat(5000) + " Do not authorize the release before review";
  const full = {...blocked, task: {...blocked.task, text: long, note: long},
    cause: long, impact: long, recovery_condition: {satisfied: false, detail: long},
    delivery: {state: "pending", receipt: long}};
  const goal = {goal_id: "alpha", quality: "stale", agents: [{agent_id: "worker"}],
    attention: {status: "read", items: [{todo_id: "todo_gate", owner_must_act: true,
      blocker: full, request: {request_id: "todo_gate", text: gate.text, reason: long}}],
    coverage: {known: 3, included: 1, omitted: 2}}};
  const original = structuredClone(goal);
  const preview = boundGoalAttention({goals: [goal], preview: true}).goals[0];
  const attention = preview.attention as JsonObject;
  const item = (attention.items as JsonObject[])[0];
  assert.deepEqual(item.read_reference, {view: "todos", goal_id: "alpha", todo_id: "todo_gate"});
  assert.equal(item.owner_must_act, true);
  assert.equal((item.blocker as JsonObject).blocker_revision, full.blocker_revision);
  assert.equal((item.blocker as JsonObject).responsible_party, "owner");
  assert.equal(((item.blocker as JsonObject).task as JsonObject).content_truncated, true);
  assert.equal(attention.details_omitted, true);
  assert.deepEqual(attention.coverage, goal.attention.coverage);
  assert.equal(preview.quality, "stale");
  assert.deepEqual(preview.agents, goal.agents);
  assert.ok(JSON.stringify(preview).length < 1600);
  assert.ok(!JSON.stringify(preview).includes("Do not authorize"));
  assert.deepEqual(goal, original);
  assert.deepEqual(boundGoalAttention({goals: [goal]}).goals[0], original);
});

test("preview retains unknown source status and omitted work without claiming a read", () => {
  const attention = {status: "unavailable", reason: "source_missing", items: [],
    coverage: {known: 4, included: 0, omitted: 4}};
  const result = boundGoalAttention({goals: [{goal_id: "alpha", attention}], preview: true});
  assert.deepEqual(result.goals[0].attention, {...attention, details_omitted: true});
  assert.throws(() => boundGoalAttention({goals: [], preview: "true"}));
});
