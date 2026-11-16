import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateHostTodoCompletion,
  HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
} from "../../loopx/control_plane/turn_driver/host_todo_completion.ts";

const todoId = "todo_abc123";

function request(
  phase: "prepare" | "finalize" | "classify_guard",
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: HOST_TODO_COMPLETION_TRANSACTION_SCHEMA_VERSION,
    phase,
    goal_id: "goal",
    agent_id: "agent",
    todo_id: todoId,
    runtime_profile: "claude_code",
    legacy_host_surface: "claude_code",
    scheduler_owner: "agent_cli_loop",
    execution_mode: "interactive",
    completion_args: [
      "todo",
      "complete",
      todoId,
      "--agent-id",
      "agent",
      "--no-follow-up",
    ],
    no_follow_up: true,
    ...overrides,
  };
}

function prepare(overrides: Record<string, unknown> = {}) {
  return evaluateHostTodoCompletion(request("prepare", overrides));
}

function identityFrom(reduced: Record<string, unknown>) {
  return reduced.identity as Record<string, unknown>;
}

function providerOutcomes(
  identity: Record<string, unknown>,
  overrides: {lifecycle_completion: Record<string, unknown>},
) {
  return Object.entries({
    guard: {ok: true, heartbeat_receipt: {settlement_identity: identity}},
    lifecycle_completion: overrides.lifecycle_completion,
  }).map(([step_kind, payload]) => ({step_kind, output: JSON.stringify(payload)}));
}

function finalize(
  outcomes: readonly Record<string, unknown>[],
  overrides: Record<string, unknown> = {},
) {
  return evaluateHostTodoCompletion(
    request("finalize", { provider_outcomes: outcomes, ...overrides }),
  );
}

test("current-Agent result rejection returns an original-Turn MCP reread and retry", () => {
  const enabled = {schema_version: "loopx_host_todo_completion_transaction_v2", first_delivery: true,
    current_agent_checkpoint: true, delivery_read_context_id: "old-result-read"};
  const identity = identityFrom(prepare(enabled));
  for (const error_code of ["checkpoint_read_context_stale", "checkpoint_read_context_unknown_or_replaced",
    "checkpoint_read_context_identity_mismatch"]) {
    const refusal = {ok: false, error_code};
    const outcomes = providerOutcomes(identity, {lifecycle_completion: refusal}).slice(0, 2);
    const reduced = finalize(outcomes, enabled);
    const result = reduced.result as Record<string, unknown>;
    assert.equal(reduced.provider_effect, null);
    assert.equal(result.ok, false);
    assert.equal(result.error_code, error_code);
    if (error_code === "checkpoint_read_context_identity_mismatch") {
      assert.deepEqual(result, refusal);
    } else {
      const recovery = result.result_read_recovery as Record<string, unknown>;
      assert.deepEqual(recovery.settlement_identity, identity);
      assert.deepEqual(recovery.read, {tool: "should_run", arguments: {revalidate_todo_id: todoId}});
      assert.deepEqual(recovery.retry, {tool: "complete_task", arguments: {delivery_read_context_id: "<read_context_id>"}});
    }
  }
});
