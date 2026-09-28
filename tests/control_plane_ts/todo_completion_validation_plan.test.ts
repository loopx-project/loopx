import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateTodoCompletionValidationPlan,
  TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA,
} from "../../loopx/control_plane/todos/completion_validation_plan.ts";

const baseTodo = {
  status: "open",
  no_followup: null,
  completion_continuation: null,
  completion_turn_key: null,
  successor_todo_ids: [],
};

function request(todo: Record<string, unknown>) {
  return {
    schema_version: TODO_COMPLETION_VALIDATION_PLAN_REQUEST_SCHEMA,
    projection_source: "materialized",
    todo: { ...baseTodo, ...todo },
    requested_no_followup: false,
    requested_completion_turn_key: null,
    dry_run: false,
  };
}

test("retired event source cannot admit a validation effect", () => {
  assert.throws(() => evaluateTodoCompletionValidationPlan({
    ...request({status: "deferred", validation_command: "false"}), projection_source: "event_log",
  }), /projection_source is unsupported/);
});

test("valid command and argv declarations produce run effects", () => {
  assert.deepEqual(
    evaluateTodoCompletionValidationPlan(
      request({
        validation_command: "python -m pytest",
        validation_label: "focused smoke",
        validation_timeout_seconds: "5",
      }),
    ),
    {
      schema_version: "loopx_todo_completion_validation_plan_result_v0",
      effect: "run",
      reason: "declared_validation",
      validation_command: "python -m pytest",
      validation_argv: null,
      validation_label: "focused smoke",
      validation_timeout_seconds: 5,
    },
  );
  assert.deepEqual(
    evaluateTodoCompletionValidationPlan(
      request({
        validation_command_argv: ["python", "-c", "pass"],
      }),
    ),
    {
      schema_version: "loopx_todo_completion_validation_plan_result_v0",
      effect: "run",
      reason: "declared_validation",
      validation_command: null,
      validation_argv: ["python", "-c", "pass"],
      validation_label: null,
      validation_timeout_seconds: null,
    },
  );
});

test("invalid persisted declaration shapes reject before execution", () => {
  for (const invalidTimeout of [0, 30, "not-int", {}]) {
    const result = evaluateTodoCompletionValidationPlan(
      request({
        validation_command: "python -m pytest",
        validation_timeout_seconds: invalidTimeout,
      }),
    );
    assert.equal(result.effect, "reject");
    assert.equal(result.reason, "invalid_declaration");
    assert.equal(result.status, "declaration_invalid");
    assert.match(result.summary, /validation_timeout_seconds/);
  }
  for (const invalidArgv of [[], ["python", 3], {}, "not-json"]) {
    const result = evaluateTodoCompletionValidationPlan(
      request({
        validation_command_argv: invalidArgv,
      }),
    );
    assert.equal(result.effect, "reject");
    assert.equal(result.status, "declaration_invalid");
    assert.match(result.summary, /validation_command_argv/);
  }
});

test("runtime decoder rejects malformed request authority", () => {
  assert.throws(
    () =>
      evaluateTodoCompletionValidationPlan({
        ...request({ validation_command: "true" }),
        schema_version: "loopx_todo_completion_validation_plan_request_v1",
      }),
    /schema mismatch/,
  );
  assert.throws(
    () =>
      evaluateTodoCompletionValidationPlan({
        ...request({ validation_command: "true" }),
        dry_run: "false",
      }),
    /dry_run must be a boolean/,
  );
});
