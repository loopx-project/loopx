import assert from "node:assert/strict";
import test from "node:test";

import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {
  canonicalAuthorityObject,
  canonicalAuthorityObjectList,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {
  type MonitorBatchInput,
  MONITOR_BATCH_PLAN_REQUEST_SCHEMA,
  MONITOR_BATCH_RECEIPT_SCHEMA,
  monitorPollRequestHash,
  planMonitorBatch,
  replayLegacyMonitorBatch,
} from "../../loopx/control_plane/scheduler/monitor_batch.ts";

function request(intent: JsonObject = {
  next_agent_todo: "Deliver observed result",
  next_action_kind: "implementation",
}): MonitorBatchInput {
  return {
    goal_id: "goal-a",
    operation_id: "effect-a",
    actor_agent_id: "agent-a",
    registered_agents: ["agent-a"],
    dry_run: false,
    gate_scope_guard: false,
    observation: {
      todo_id: "todo_watch",
      target_key: "watch-a",
      generated_at: "2026-09-01T00:00:00Z",
      result_hash: "result-a",
      material_change: true,
    },
    intent,
    todos: [{
      todo_id: "todo_watch",
      role: "agent",
      status: "open",
      done: false,
      archive_state: "active",
      text: "Observe change",
      task_class: "continuous_monitor",
      target_key: "watch-a",
      cadence: "1h",
    }],
  };
}

function historicalReceipt(input: MonitorBatchInput): JsonObject {
  const successors: JsonObject[] = [];
  const summaries: JsonObject[] = [];
  if (typeof input.intent.next_agent_todo === "string" &&
      input.intent.next_agent_todo.trim()) {
    successors.push({
      todo_id: "todo_historical_agent",
      role: "agent",
      todo: input.intent.next_agent_todo,
    });
    summaries.push({todo_id: "todo_historical_agent", role: "agent"});
  }
  if (typeof input.intent.next_user_todo === "string" &&
      input.intent.next_user_todo.trim()) {
    successors.push({
      todo_id: "todo_historical_user",
      role: "user",
      todo: input.intent.next_user_todo,
    });
    summaries.push({todo_id: "todo_historical_user", role: "user"});
  }
  return {
    schema_version: MONITOR_BATCH_RECEIPT_SCHEMA,
    goal_id: input.goal_id,
    operation_id: input.operation_id,
    request_sha256: monitorPollRequestHash(input),
    writeback: {
      schema_version: "monitor_poll_todo_writeback_v0",
      dry_run: input.dry_run,
      goal_id: input.goal_id,
      todo_id: input.observation.todo_id,
      monitor_effect_id: input.operation_id,
      result_hash: input.observation.result_hash,
      material_change: input.observation.material_change,
      last_checked_at: input.observation.generated_at,
      todo_update: {ok: true},
      next_todos: successors,
      successor_receipts: summaries,
      provider_replayed: false,
    },
  };
}

function replay(input: MonitorBatchInput, receipt = historicalReceipt(input)):
    JsonObject {
  return replayLegacyMonitorBatch({
    schema_version: MONITOR_BATCH_PLAN_REQUEST_SCHEMA,
    ...input,
    previous_receipt: receipt,
  });
}

test("historical receipt replay returns no mutations and ignores current source state", () => {
  const input = request();
  const result = replay({...input, registered_agents: [], todos: [null]});
  const writeback = canonicalAuthorityObject(
    result.writeback,
    "historical replay writeback",
  );
  const receiptWriteback = canonicalAuthorityObject(
    historicalReceipt(input).writeback,
    "historical receipt writeback",
  );
  assert.equal(result.replayed, true);
  assert.deepEqual(result.mutations, []);
  assert.equal(writeback.provider_replayed, true);
  assert.deepEqual(
    writeback.next_todos,
    receiptWriteback.next_todos,
  );
});

test("historical replay requires an immutable receipt", () => {
  const input = request();
  assert.throws(
    () => replayLegacyMonitorBatch({
      schema_version: MONITOR_BATCH_PLAN_REQUEST_SCHEMA,
      ...input,
    }),
    /requires an immutable previous receipt/,
  );
});

test("changed intent and malformed receipt fail closed", () => {
  const input = request();
  const receipt = historicalReceipt(input);
  assert.throws(
    () => replay({
      ...input,
      intent: {
        next_agent_todo: "Changed",
        next_action_kind: "implementation",
      },
    }, receipt),
    /request mismatch/,
  );
  const writeback = canonicalAuthorityObject(
    receipt.writeback,
    "historical receipt writeback",
  );
  assert.throws(
    () => replay(input, {
      ...receipt,
      writeback: {...writeback, next_todos: [{}]},
    }),
    /successor identity/,
  );
});

test("historical receipt cannot drop, duplicate, or relabel requested successors", () => {
  const input = request({
    next_agent_todo: "Deliver observed result",
    next_action_kind: "implementation",
    next_user_todo: "Approve observed result",
    next_user_task_class: "user_action",
  });
  const receipt = historicalReceipt(input);
  const writeback = canonicalAuthorityObject(
    receipt.writeback,
    "historical receipt writeback",
  );
  const next = canonicalAuthorityObjectList(
    writeback.next_todos,
    "historical successors",
  );
  const summaries = canonicalAuthorityObjectList(
    writeback.successor_receipts,
    "historical successor summaries",
  );
  const withWriteback = (changes: JsonObject): JsonObject => ({
    ...receipt,
    writeback: {...writeback, ...changes},
  });
  assert.throws(
    () => replay(input, withWriteback({next_todos: [], successor_receipts: []})),
    /malformed/,
  );
  assert.throws(
    () => replay(input, withWriteback({
      next_todos: [next[0], {...next[1], role: "agent"}],
    })),
    /successor identity/,
  );
  assert.throws(
    () => replay(input, withWriteback({
      next_todos: [next[0], {...next[1], todo_id: next[0].todo_id}],
      successor_receipts: [
        summaries[0],
        {...summaries[1], todo_id: next[0].todo_id},
      ],
    })),
    /successor identity/,
  );
});

test("receipt replay uses the retained Python whitespace semantics", () => {
  const input = request({next_agent_todo: "\u0085"});
  const receipt = historicalReceipt(input);
  const historicalWriteback = canonicalAuthorityObject(
    receipt.writeback,
    "historical receipt writeback",
  );
  const result = replay(input, {
    ...receipt,
    writeback: {
      ...historicalWriteback,
      next_todos: [],
      successor_receipts: [],
    },
  });
  const writeback = canonicalAuthorityObject(
    result.writeback,
    "historical replay writeback",
  );
  assert.deepEqual(writeback.next_todos, []);
});

test("canonical fresh planning remains actor-owned", () => {
  const input = request();
  const first = planMonitorBatch(input);
  assert.equal(first.mutations.length, 2);
  assert.equal(first.writeback.monitor_effect_id, "effect-a");
  assert.equal("receipt" in first, false);

  const peer = request({
    ...input.intent,
    next_claimed_by: "agent-b",
  });
  peer.registered_agents = ["agent-a", "agent-b"];
  assert.throws(
    () => planMonitorBatch(peer),
    /actor to match owner/,
  );
});


test("canonical monitor discoveries default independently and accept per-role priorities", () => {
  const source = request();
  source.todos = source.todos.map(todo => ({...todo, priority: "P0"}));
  for (const [taskClass, extra, expected] of [
    ["user_action", {}, ["P1", "P1"]],
    ["user_gate", {}, ["P1", "P0"]],
    ["user_gate", {next_agent_priority: "P0", next_user_priority: "P3"}, ["P0", "P3"]],
  ] as const) {
    const input = {...source, intent: {...source.intent,
      next_user_todo: "Review result", next_user_task_class: taskClass, ...extra}};
    const first = planMonitorBatch(input);
    const writeback = first.writeback;
    assert.deepEqual((writeback.next_todos as JsonObject[]).map(todo => todo.priority), expected);
    assert.deepEqual((writeback.successor_receipts as JsonObject[]).map(todo => todo.priority), expected);
  }
  for (const extra of [{next_agent_priority: "P5"}, {next_user_priority: "P0"},
    {next_agent_priority: "P0", next_agent_todo: "[P1] Conflict"}]) {
    assert.throws(() => planMonitorBatch({...source, intent: {...source.intent, ...extra}}));
  }
});

test("historical unranked monitor receipts replay without applying the new default", () => {
  const input = request();
  const receipt = historicalReceipt(input);
  const result = replay({...input, todos: []}, receipt);
  assert.equal(result.replayed, true);
  assert.deepEqual(result.mutations, []);
  const writeback = result.writeback as JsonObject;
  assert.equal((writeback.next_todos as JsonObject[])[0].priority, undefined);
  assert.equal((writeback.next_todos as JsonObject[])[0].todo, "Deliver observed result");
  for (const intent of [{...input.intent, next_agent_priority: "P0"},
    {...input.intent, next_user_priority: "P3"}]) {
    assert.throws(() => replay({...input, intent}, receipt), /request mismatch/);
  }
});
