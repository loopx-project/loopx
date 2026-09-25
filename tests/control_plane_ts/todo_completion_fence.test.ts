import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  evaluateTodoCompletionFence,
  localTodoCompletionIdentity,
  TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
} from "../../loopx/control_plane/todos/completion_fence.ts";

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "../fixtures/control_plane/todo_completion_fence_characterization_v0.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  schema_version: string;
  source_baseline: string;
  cases: Array<{
    name: string;
    request: Record<string, unknown>;
    expected_result?: Record<string, unknown>;
    expected_error?: string;
  }>;
};

test("surviving materialized completion-fence characterization remains exact", () => {
  assert.equal(
    fixture.schema_version,
    "loopx_todo_completion_fence_characterization_v0",
  );
  assert.equal(fixture.source_baseline, "58adc1783");
  assert.equal(fixture.cases.length >= 7, true);
  for (const item of fixture.cases) {
    if (item.expected_error) {
      assert.throws(
        () => evaluateTodoCompletionFence(item.request),
        (error: unknown) =>
          error instanceof Error && error.message === item.expected_error,
        item.name,
      );
      continue;
    }
    assert.deepEqual(
      evaluateTodoCompletionFence(item.request),
      item.expected_result,
      item.name,
    );
  }
});

test("terminal closeout rejects missing and contradictory durable state", () => {
  const base = {
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: "materialized",
    todo: {
      status: "done",
      no_followup: false,
      completion_continuation: null,
      completion_turn_key: "turn-a",
      successor_todo_ids: [],
    },
    requested_no_followup: true,
    requested_completion_turn_key: "turn-a",
  };
  assert.throws(
    () => evaluateTodoCompletionFence(base),
    /missing completion_continuation/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...base,
        todo: { ...base.todo, completion_continuation: "no_followup" },
      }),
    /completion_continuation=active_goal/,
  );
});

test("runtime decoder rejects malformed authority input before evaluation", () => {
  const valid = fixture.cases[0].request;
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...valid,
        schema_version: "loopx_todo_completion_fence_request_v1",
      }),
    /schema mismatch/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...valid,
        projection_source: "shadow",
      }),
    /projection_source is unsupported/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...valid,
        todo: {
          ...(valid.todo as Record<string, unknown>),
          status: "completed",
        },
      }),
    /todo.status is unsupported/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...valid,
        requested_no_followup: "true",
      }),
    /requested_no_followup must be a boolean/,
  );
});

test("evaluation is input-immutable and normalizes persisted metadata", () => {
  const request = {
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: "materialized",
    todo: {
      status: "DONE",
      no_followup: "YES",
      completion_continuation: "NO_FOLLOWUP",
      completion_turn_key: "turn-a",
      successor_todo_ids: [],
    },
    requested_no_followup: true,
    requested_completion_turn_key: "turn-a",
  };
  const before = structuredClone(request);

  const result = evaluateTodoCompletionFence(request);

  assert.equal(result.outcome, "replay");
  assert.equal(result.completion_continuation, "no_followup");
  assert.deepEqual(request, before);
});

test("unscoped completion identity is stable and repairs a legacy terminal gap", () => {
  const identity = localTodoCompletionIdentity(
    "goal-example",
    "todo_legacy001",
  );
  assert.match(identity, /^local_completion_[0-9a-f]{32}$/);
  assert.deepEqual(
    evaluateTodoCompletionFence({
      schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
      projection_source: "materialized",
      goal_id: "goal-example",
      todo_id: "todo_legacy001",
      todo: {
        status: "done",
        no_followup: false,
        completion_continuation: "active_goal",
        completion_turn_key: null,
        successor_todo_ids: [],
      },
      requested_no_followup: true,
      requested_completion_turn_key: identity,
      requested_completion_identity_source: "lifecycle_reentry",
    }),
    {
      schema_version: "loopx_todo_completion_fence_result_v0",
      outcome: "continue",
      reason: "unscoped_completion_identity_repair",
      status: "done",
      terminal_before_request: true,
      completion_continuation: "active_goal",
    },
  );
});

test("lifecycle reentry rejects invented identities and open Todos", () => {
  const base = {
    schema_version: TODO_COMPLETION_FENCE_REQUEST_SCHEMA,
    projection_source: "materialized",
    goal_id: "goal-example",
    todo_id: "todo_legacy001",
    todo: {
      status: "done",
      no_followup: false,
      completion_continuation: "active_goal",
      completion_turn_key: null,
      successor_todo_ids: [],
    },
    requested_no_followup: true,
    requested_completion_turn_key: "local_completion_00000000000000000000000000000000",
    requested_completion_identity_source: "lifecycle_reentry",
  };
  assert.throws(
    () => evaluateTodoCompletionFence(base),
    /different completion_turn_key/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionFence({
        ...base,
        todo: { ...base.todo, status: "open" },
      }),
    /requires an already completed Todo/,
  );
});
