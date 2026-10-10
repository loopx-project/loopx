import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";
import {planLegacyMonitorBatch, planMonitorBatch, MONITOR_BATCH_PLAN_REQUEST_SCHEMA} from "../../loopx/control_plane/scheduler/monitor_batch.ts";

function request(): JsonObject {
  return {schema_version: MONITOR_BATCH_PLAN_REQUEST_SCHEMA, legacy_batch_version: 1,
    goal_id: "goal-a", operation_id: "effect-a", actor_agent_id: "agent-a",
    registered_agents: ["agent-a"], dry_run: false, gate_scope_guard: false,
    observation: {todo_id: "todo_watch", target_key: "watch-a", generated_at: "2026-09-01T00:00:00Z",
      result_hash: "result-a", material_change: true},
    intent: {next_agent_todo: "Deliver observed result", next_action_kind: "implementation"},
    todos: [{todo_id: "todo_watch", role: "agent", status: "open", done: false,
      archive_state: "active", text: "Observe change", task_class: "continuous_monitor",
      target_key: "watch-a", cadence: "1h"}]};
}

test("legacy batch plans Monitor and successors together with durable identities", () => {
  const plan = planLegacyMonitorBatch(request());
  assert.equal(plan.replayed, false);
  const mutations = plan.mutations as {kind: string; todo: JsonObject}[];
  assert.equal(mutations.length, 2);
  assert.equal(mutations[0]?.todo.monitor_effect_id, "effect-a");
  const writeback = plan.writeback as JsonObject;
  assert.deepEqual((writeback.next_todos as JsonObject[]).map(todo => todo.todo_id),
    [mutations[1]?.todo.todo_id]);
  assert.equal((plan.receipt as JsonObject).writeback, writeback);
});

test("legacy Monitor routes an explicit successor claim to a registered peer; canonical remains actor-owned", () => {
  const source = {...request(), registered_agents: ["agent-a", "agent-b"],
    intent: {...(request().intent as JsonObject), next_claimed_by: "agent-b"}};
  const planned = planLegacyMonitorBatch(source);
  const successor = (planned.mutations as {todo: JsonObject}[])[1]!.todo;
  assert.equal(successor.claimed_by, "agent-b");
  assert.equal(successor.created_by, "agent-a");
  assert.equal(successor.last_actor_agent_id, "agent-a");
  assert.throws(() => planMonitorBatch(source as never), /actor to match owner/);
  const unknown = {...source, intent: {...source.intent, next_claimed_by: "unregistered"}};
  assert.throws(() => planLegacyMonitorBatch(unknown),
    (error: unknown) => error instanceof EffectRuntimeRequestError &&
      error.kind === "request_rejected" && /claim owner is not registered/.test(error.message));
  const internal = new Error("synthetic internal failure");
  const broken = {...source};
  Object.defineProperty(broken, "todos", {get() {throw internal;}});
  assert.throws(() => planLegacyMonitorBatch(broken), error => error === internal);
});

test("receipt replays before current target and gate checks", () => {
  const first = planLegacyMonitorBatch(request());
  const replay = {...request(), previous_receipt: first.receipt, legacy_batch_version: null,
    todos: [{todo_id: "todo_watch", role: "agent", status: "done", archive_state: "archive",
      task_class: "continuous_monitor", monitor_effect_id: "effect-a", target_key: "renamed"}],
    gate_scope_guard: true};
  const result = planLegacyMonitorBatch(replay);
  assert.equal(result.replayed, true);
  assert.deepEqual(result.mutations, []);
  assert.equal((result.writeback as JsonObject).provider_replayed, true);
  assert.deepEqual((result.writeback as JsonObject).next_todos, (first.writeback as JsonObject).next_todos);
  assert.equal(planLegacyMonitorBatch({...replay, todos: [null]}).replayed, true);
});

test("receipt replay uses the same Python whitespace semantics as successor planning", () => {
  const source = {...request(), intent: {next_agent_todo: "\u0085"}};
  const first = planLegacyMonitorBatch(source);
  assert.deepEqual((first.writeback as JsonObject).next_todos, []);
  const retry = planLegacyMonitorBatch({...source, previous_receipt: first.receipt});
  assert.equal(retry.replayed, true);
  assert.deepEqual((retry.writeback as JsonObject).next_todos, []);
});

test("changed intent, malformed receipt, and missing receipt fail closed", () => {
  const first = planLegacyMonitorBatch(request());
  assert.throws(() => planLegacyMonitorBatch({...request(), previous_receipt: first.receipt,
    intent: {next_agent_todo: "Changed", next_action_kind: "implementation"}}), /request mismatch/);
  assert.throws(() => planLegacyMonitorBatch({...request(), previous_receipt: {
    ...(first.receipt as JsonObject), writeback: {...(first.writeback as JsonObject),
      next_todos: [{}]}}}),
  /successor identity/);
  assert.throws(() => planLegacyMonitorBatch({...request(), todos: [{
    ...(request().todos as JsonObject[])[0], monitor_effect_id: "effect-a"}]}), /without its batch receipt/);
  assert.throws(() => planLegacyMonitorBatch({...request(), legacy_batch_version: null}), /needs reconciliation/);
});

test("legacy receipt cannot drop, duplicate, or relabel requested successors", () => {
  const both = {...request(), intent: {...(request().intent as JsonObject),
    next_user_todo: "Approve observed result", next_user_task_class: "user_action"}};
  const first = planLegacyMonitorBatch(both);
  const writeback = first.writeback as JsonObject;
  const next = writeback.next_todos as JsonObject[];
  const summaries = writeback.successor_receipts as JsonObject[];
  const replay = (changes: JsonObject) => planLegacyMonitorBatch({...both,
    previous_receipt: {...(first.receipt as JsonObject), writeback: {...writeback, ...changes}}});
  assert.throws(() => replay({next_todos: [], successor_receipts: []}), /malformed/);
  assert.throws(() => replay({next_todos: [next[0], {...next[1], role: "agent"}]}), /successor identity/);
  assert.throws(() => replay({next_todos: [next[0], {...next[1], todo_id: next[0].todo_id}],
    successor_receipts: [summaries[0], {...summaries[1], todo_id: next[0].todo_id}]}), /successor identity/);
});

test("shared fresh planner does not apply legacy receipt policy to canonical state", () => {
  const source = request();
  const unchanged = {...source, intent: {}, observation: {
    ...(source.observation as JsonObject), material_change: false}};
  const first = planMonitorBatch(unchanged);
  const planned = planMonitorBatch({...unchanged, todos: [first.mutations[0].todo]});
  assert.equal(planned.writeback.monitor_effect_id, "effect-a");
  assert.equal((planned.mutations as JsonObject[]).length, 1);
  assert.equal("receipt" in planned, false);
});

test("legacy successor reuses a semantic duplicate with omitted empty capabilities", () => {
  const source = request();
  const fresh = planLegacyMonitorBatch(source);
  const candidate = (fresh.mutations as {todo: JsonObject}[])[1]?.todo;
  assert.ok(candidate);
  const {required_capabilities: _omitted, ...existing} = candidate;
  const duplicate = {...existing, todo_id: "todo_existing_successor"};
  const todos = [...(source.todos as JsonObject[]), duplicate];
  const replay = planLegacyMonitorBatch({...source, todos});
  assert.equal((replay.mutations as JsonObject[]).length, 1);
  assert.equal(((replay.writeback as JsonObject).next_todos as JsonObject[])[0]?.todo_id,
    "todo_existing_successor");

  assert.throws(() => planLegacyMonitorBatch({...source, todos: [
    (source.todos as JsonObject[])[0], {...duplicate, action_kind: "different"}]}),
  /different action_kind/);
  const {unblocks_todo_id: _missing, ...missingLink} = duplicate;
  assert.throws(() => planLegacyMonitorBatch({...source, todos: [
    (source.todos as JsonObject[])[0], missingLink]}),
  /different unblocks_todo_id/);
});
