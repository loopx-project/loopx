import assert from "node:assert/strict";
import test from "node:test";
import {observeOwnerAttention, ownerAttentionHealth, ownerAttentionReferences, planOwnerAttention} from "../../loopx/control_plane/presentation/owner_attention.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const empty = () => ({schema_version: "lark_owner_attention_v0", revision: 0, enabled: true, initialized: false, subjects: [], outbox: []});
const task = {todo_id: "todo_one", role: "user", status: "open", task_class: "user_action", text: "Inspect the preview"};
const plan = (state: JsonObject, todos: JsonObject[], goalId = "alpha") =>
  planOwnerAttention({state, goals: [{goal_id: goalId, todos}], source_complete: true});
const rows = (state: JsonObject) => state.outbox as JsonObject[];

test("first enable baselines history; later discovery is complete and stable across restart", () => {
  const initial = plan(empty(), [task]);
  assert.equal(rows(initial).length, 0);
  const tasks = Array.from({length: 80}, (_, i) => ({...task, todo_id: `todo_${i}`}));
  const discovered = plan(initial, [task, ...tasks]);
  assert.equal(rows(discovered).length, 80, "no bounded prefix silently loses owner tasks");
  assert.deepEqual(plan(JSON.parse(JSON.stringify(discovered)), [task, ...tasks]), discovered);
  assert.equal(rows(plan(discovered, [{...task, updated_at: "later"}, ...tasks])).length, 80);
  assert.equal(rows(plan(discovered, [{...task, note: "New decision terms"}, ...tasks])).length, 81);
});

test("withdrawn or superseded tasks cancel unsent notices; reopening retains a new generation", () => {
  const base = plan(empty(), []);
  const newTask = plan(base, [task]);
  const withdrawn = plan(newTask, [{...task, status: "done"}]);
  assert.equal(rows(withdrawn)[0].status, "superseded");
  const reopened = plan(withdrawn, [task]);
  assert.equal(rows(reopened).length, 2);
  assert.notEqual(rows(reopened)[0].id, rows(reopened)[1].id);
  assert.equal(rows(plan(base, [{...task, superseded_by: "todo_new"}, {...task, role: "agent"}])).length, 0);
  assert.throws(() => planOwnerAttention({state: newTask, goals: [], source_complete: false}));
  assert.throws(() => plan(base, [{...task, goal_id: "foreign"}]));
  assert.throws(() => plan(base, [task, task]));
});

test("ambiguous create never returns to pending; locator recovers by independent read only", () => {
  const state = plan(plan(empty(), []), [task]);
  const id = rows(state)[0].id;
  const observe = (s: JsonObject, event: string, extra = {}) => observeOwnerAttention({state: s, id, event, now: 100, ...extra});
  const started = observe(state, "write_started", {intent: {text: "frozen", route: {source: "original"}}});
  assert.equal(rows(plan(started, [task]))[0].status, "unconfirmed", "crash before saving a locator remains unknown");
  const unknown = observe(started, "write_observed");
  assert.equal(rows(unknown)[0].message_id, null);
  assert.throws(() => observe(unknown, "write_started", {intent: {}}));
  const located = observe(started, "write_observed", {message_id: "om_original"});
  const retry = observe(located, "readback_pending");
  assert.equal(rows(retry)[0].status, "unconfirmed");
  assert.equal(rows(retry)[0].message_id, "om_original");
  assert.equal(rows(observe(retry, "readback_verified", {readback: {message_id: "om_original"}}))[0].status, "delivered");
  assert.equal(rows(observe(started, "spawn_failed"))[0].status, "pending", "only proven no-effect failure can retry create");
});

test("identical titles across Goals remain distinct; content is complete or explicitly failed", () => {
  const base = plan(empty(), []);
  const a = plan(base, [task]);
  const b = planOwnerAttention({state: a, source_complete: true,
    goals: [{goal_id: "alpha", todos: [task]}, {goal_id: "beta", todos: [task]}]});
  assert.equal(rows(b).length, 2);
  assert.notEqual(rows(b)[0].id, rows(b)[1].id);
  const long = plan(base, [{...task, text: "题".repeat(9000)}]);
  assert.equal(rows(long)[0].status, "failed");
  assert.ok(String(rows(long)[0].body).includes("题".repeat(9000)));
});

test("unknown journal states cannot present healthy or authorize delivery", () => {
  const broken = {...empty(), outbox: [{status: "new_unknown_state"}]};
  assert.throws(() => ownerAttentionHealth({state: broken}));
  assert.throws(() => plan(broken, [task]));
  assert.throws(() => ownerAttentionHealth({state: {...empty(), enabled: "true"}}));
});

test("reference discovery and planning share active-subject semantics without a display cap", () => {
  const tasks = Array.from({length: 80}, (_, i) => ({...task, todo_id: `todo_${i}`}));
  const todos = [...tasks, {...task, todo_id: "todo_done", done: true}, {...task, role: "agent"}];
  assert.deepEqual(ownerAttentionReferences({goal_id: "alpha", todos}).todo_ids, tasks.map(row => row.todo_id));
});
