import assert from "node:assert/strict";
import test from "node:test";

import {
  reduceTodoCompletionTransaction,
  TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA,
} from "../../loopx/control_plane/todos/completion_transaction.ts";

const baseTodo = {
  status: "open",
  no_followup: null,
  completion_continuation: null,
  completion_turn_key: null,
  successor_todo_ids: [],
  validation_command: null,
  validation_command_argv: null,
  validation_label: null,
  validation_timeout_seconds: null,
};

function request(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: TODO_COMPLETION_TRANSACTION_REQUEST_SCHEMA,
    goal_id: "goal-example",
    todo_id: "todo_example001",
    projection_source: "materialized",
    todo: { ...baseTodo },
    requested_no_followup: false,
    requested_completion_turn_key: null,
    requested_completion_identity_source: null,
    requested_has_successor: false,
    dry_run: false,
    validation_receipt: null,
    ...overrides,
  };
}

const completionPolicyRequest = {
  schema_version: "loopx_todo_completion_policy_request_v0",
  goal_id: "goal-example",
  agent_model: "peer_v1",
  claimed_by: "agent-a",
  registered_agents: ["agent-a"],
  next_claimed_by: null,
  next_agent_todo: "Continue the bounded migration.",
  next_continuation_policy: "same_agent_non_delivery",
  next_excluded_agents: [],
  self_merged: false,
  evidence: "focused validation passed",
  linked_successors: [],
};

test("open completion commits in one reduction with a stable local identity", () => {
  const result = reduceTodoCompletionTransaction(request());

  assert.equal(result.decision, "commit");
  assert.match(
    result.completion_identity_key,
    /^local_completion_[0-9a-f]{32}$/,
  );
  assert.equal(result.completion_identity_source, "unscoped_completion");
  assert.deepEqual(result.fence, {
    schema_version: "loopx_todo_completion_fence_result_v0",
    outcome: "continue",
    reason: "not_terminal",
    status: "open",
    terminal_before_request: false,
    completion_continuation: null,
  });
  assert.deepEqual(result.completion_state, {
    continuation: "active_goal",
    recovery: null,
  });
  assert.deepEqual(result.metadata_updates, {
    completion_continuation: "active_goal",
    completion_receipt_id: result.metadata_updates.completion_receipt_id,
  });
  assert.match(String(result.metadata_updates.completion_receipt_id), /^tcw_[0-9a-f]{64}$/);
});

test("committed completion receipt ids separate phases and retain stable scope", () => {
  const ordinaryRequest = request({requested_completion_turn_key: "turn-a",
    requested_completion_identity_source: "turn_settlement"});
  const ordinary = reduceTodoCompletionTransaction(ordinaryRequest);
  const repeated = reduceTodoCompletionTransaction(ordinaryRequest);
  const closeout = reduceTodoCompletionTransaction(request({
    todo: {...baseTodo, status: "done", completion_continuation: "active_goal",
      completion_turn_key: "turn-a"},
    requested_completion_turn_key: "turn-a",
    requested_completion_identity_source: "turn_settlement",
    requested_no_followup: true,
  }));
  const anotherTurn = reduceTodoCompletionTransaction({...ordinaryRequest,
    requested_completion_turn_key: "turn-b"});
  const anotherGoal = reduceTodoCompletionTransaction({...ordinaryRequest, goal_id: "goal-b"});
  for (const result of [ordinary, repeated, closeout, anotherTurn, anotherGoal]) {
    assert.equal(result.decision, "commit");
    assert.match(String(result.metadata_updates.completion_receipt_id), /^tcw_[0-9a-f]{64}$/);
  }
  assert.equal(ordinary.metadata_updates.completion_receipt_id,
    repeated.metadata_updates.completion_receipt_id);
  assert.equal(new Set([ordinary, closeout, anotherTurn, anotherGoal].map(
    result => result.metadata_updates.completion_receipt_id)).size, 4);
});

test("declared validation is one external effect between two reductions", () => {
  const pending = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        validation_command_argv: ["python", "-c", "pass"],
        validation_label: "focused smoke",
        validation_timeout_seconds: "5",
        task_repository: "git:github.com/example/repo-b",
      },
    }),
  );

  assert.deepEqual(pending, {
    schema_version: "loopx_todo_completion_transaction_result_v0",
    decision: "execute_validation",
    completion_identity_key: pending.completion_identity_key,
    completion_identity_source: "unscoped_completion",
    fence: pending.fence,
    validation_effect: {
      kind: "caller_validation",
      validation_command: null,
      validation_argv: ["python", "-c", "pass"],
      validation_label: "focused smoke",
      validation_timeout_seconds: 5,
      task_repository: "git:github.com/example/repo-b",
    },
  });

  const committed = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        validation_command_argv: ["python", "-c", "pass"],
        validation_label: "focused smoke",
        validation_timeout_seconds: "5",
      },
      validation_receipt: {
        schema_version: "issue_fix_validation_command_v0",
        command_label: "focused smoke",
        exit_code: 0,
        passed: true,
        stdout_captured: false,
        stderr_captured: false,
        local_path_captured: false,
      },
    }),
  );
  assert.equal(committed.decision, "commit");
  assert.equal(committed.validation_receipt?.passed, true);

  const rejected = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        validation_command_argv: ["python", "-c", "pass"],
      },
      validation_receipt: {
        schema_version: "issue_fix_validation_command_v0",
        command_label: "todo completion validation",
        exit_code: 1,
        passed: false,
        stdout_captured: false,
        stderr_captured: false,
        local_path_captured: false,
      },
    }),
  );
  assert.equal(rejected.decision, "reject");
  assert.equal(rejected.failure.kind, "validation_failed");
  assert.equal(rejected.failure.validation_receipt.passed, false);
});

test("revised validators reject stale or unbound validation receipts", () => {
  const todo = {
    ...baseTodo,
    validation_command_argv: ["python", "-c", "pass"],
    validation_label: "revised smoke",
    completion_validation_revision: 1,
  };
  const pending = reduceTodoCompletionTransaction(request({todo}));
  assert.equal(pending.decision, "execute_validation");
  if (pending.decision !== "execute_validation") return;
  const currentDigest = pending.validation_effect.validation_declaration_sha256;
  assert.match(String(currentDigest), /^[a-f0-9]{64}$/u);

  const receipt = {
    schema_version: "issue_fix_validation_command_v0",
    command_label: "revised smoke",
    exit_code: 0,
    passed: true,
    stdout_captured: false,
    stderr_captured: false,
    local_path_captured: false,
  };
  assert.throws(
    () => reduceTodoCompletionTransaction(request({todo, validation_receipt: receipt})),
    /does not match the current validation declaration/,
  );
  assert.throws(
    () => reduceTodoCompletionTransaction(request({
      todo,
      validation_receipt: {
        ...receipt,
        validation_declaration_sha256: "0".repeat(64),
      },
    })),
    /does not match the current validation declaration/,
  );

  const committed = reduceTodoCompletionTransaction(request({
    todo,
    validation_receipt: {
      ...receipt,
      validation_declaration_sha256: currentDigest,
    },
  }));
  assert.equal(committed.decision, "commit");
});

test("completion policy joins the coarse transaction only at commit", () => {
  const pending = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        validation_command: "true",
      },
      completion_policy_request: {
        ...completionPolicyRequest,
        claimed_by: "not registered",
      },
    }),
  );
  assert.equal(pending.decision, "execute_validation");
  assert.equal("completion_policy" in pending, false);

  const committed = reduceTodoCompletionTransaction(
    request({ completion_policy_request: completionPolicyRequest }),
  );
  assert.equal(committed.decision, "commit");
  assert.deepEqual(committed.completion_policy, {
    schema_version: "loopx_todo_completion_policy_result_v0",
    effective_claimed_by: "agent-a",
    registered_agents: ["agent-a"],
    effective_next_claimed_by: "agent-a",
    effective_next_excluded_agents: [],
    self_merged: false,
    linked_successor_id: null,
  });

  const policyRejected = reduceTodoCompletionTransaction(
    request({
      completion_policy_request: {
        ...completionPolicyRequest,
        claimed_by: "not registered",
      },
    }),
  );
  assert.equal(policyRejected.decision, "policy_reject");
  assert.deepEqual(policyRejected.completion_policy_failure, {
    schema_version: "loopx_todo_completion_policy_failure_v0",
    kind: "completion_policy_rejected",
    diagnostic_code: "invalid_request",
    summary:
      "claimed_by='not-registered' is not registered for goal " +
      "'goal-example'; registered_agents=agent-a",
  });
});

test("terminal replay bypasses a stale validation declaration", () => {
  const result = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        status: "done",
        completion_continuation: "active_goal",
        completion_turn_key: "turn-a",
        validation_command: "false",
      },
      requested_completion_turn_key: "turn-a",
      requested_completion_identity_source: "turn_settlement",
    }),
  );

  assert.equal(result.decision, "replay");
  assert.equal(result.fence.outcome, "replay");
  assert.equal(result.completion_identity_key, "turn-a");
});

test("lifecycle reentry returns the explicit terminal recovery state", () => {
  const localKey = reduceTodoCompletionTransaction(request())
    .completion_identity_key;
  const result = reduceTodoCompletionTransaction(
    request({
      todo: {
        ...baseTodo,
        status: "done",
        completion_continuation: "active_goal",
        completion_turn_key: null,
      },
      requested_no_followup: true,
      requested_completion_turn_key: localKey,
      requested_completion_identity_source: "lifecycle_reentry",
    }),
  );

  assert.equal(result.decision, "commit");
  assert.deepEqual(result.completion_state, {
    continuation: "no_followup",
    recovery: "lifecycle_reentry_terminal_closeout",
  });
  assert.deepEqual(result.metadata_updates, {
    completion_continuation: "no_followup",
    completion_recovery: "lifecycle_reentry_terminal_closeout",
    completion_receipt_id: result.metadata_updates.completion_receipt_id,
  });
});

test("runtime input and validation receipts fail closed", () => {
  assert.throws(
    () =>
      reduceTodoCompletionTransaction(
        request({ requested_no_followup: "true" }),
      ),
    /requested_no_followup must be a boolean/,
  );
  assert.throws(
    () =>
      reduceTodoCompletionTransaction(
        request({
          todo: {
            ...baseTodo,
            validation_command: "true",
          },
          validation_receipt: {
            schema_version: "issue_fix_validation_command_v0",
            command_label: "unsafe receipt",
            exit_code: 0,
            passed: true,
            stdout_captured: true,
            stderr_captured: false,
            local_path_captured: false,
          },
        }),
      ),
    /stdout_captured must be false/,
  );
  assert.throws(
    () =>
      reduceTodoCompletionTransaction(
        request({
          todo: {
            ...baseTodo,
            validation_command: "true",
            validation_label: "authorized smoke",
          },
          validation_receipt: {
            schema_version: "issue_fix_validation_command_v0",
            command_label: "different smoke",
            exit_code: 0,
            passed: true,
            stdout_captured: false,
            stderr_captured: false,
            local_path_captured: false,
          },
        }),
      ),
    /does not match the authorized effect/,
  );
  assert.throws(
    () =>
      reduceTodoCompletionTransaction(
        request({
          todo: {
            ...baseTodo,
            validation_command: "true",
          },
          validation_receipt: {
            schema_version: "issue_fix_validation_command_v0",
            command_label: "todo completion validation",
            exit_code: 1,
            passed: true,
            stdout_captured: false,
            stderr_captured: false,
            local_path_captured: false,
          },
        }),
      ),
    /exit_code and passed must describe the same outcome/,
  );
  assert.throws(
    () =>
      reduceTodoCompletionTransaction(
        request({
          requested_no_followup: true,
          requested_has_successor: true,
        }),
      ),
    /cannot record both no_followup and a successor/,
  );
});
