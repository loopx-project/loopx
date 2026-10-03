import assert from "node:assert/strict";
import {test} from "node:test";
import {boundGoalAttention, projectGoalAttention} from "../../loopx/control_plane/presentation/goal_attention.ts";

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
  assert.equal(result.items[2].incomplete[0].reason_code, "content_redacted");
  assert.equal(result.items[3].incomplete[0].reason_code, "content_overflow");
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
