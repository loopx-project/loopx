import assert from "node:assert/strict";
import test from "node:test";

import {EffectRuntimeRequestError} from "../../loopx/control_plane/effect_runtime_errors.ts";
import {projectExecutionPreparation} from "../../loopx/control_plane/work_items/execution_preparation.ts";

function facts(patch: Record<string, unknown> = {}) {
  return {
    schema_version: "execution_preparation_facts_v0",
    goal_id: "goal-example",
    agent_id: "agent-a",
    handoff_mode: "hard_lease",
    authority_available: true,
    selected_todo: {todo_id: "todo_example", status: "open", claimed_by: "agent-a"},
    leases: [],
    ...patch,
  };
}

test("matching active lease is ready", () => {
  assert.deepEqual(projectExecutionPreparation(facts({leases: [
    {status: "active", active: true, owner: "agent-a"},
  ]})), {
    schema_version: "execution_preparation_v0",
    todo_id: "todo_example",
    agent_id: "agent-a",
    handoff_mode: "hard_lease",
    state: "ready",
    delivery_allowed: true,
    reason_code: "matching_active_task_lease",
  });
});

for (const [label, leases, reasonCode] of [
  ["missing", [], "task_lease_missing"],
  ["released", [{status: "released", active: false, owner: "agent-a"}], "task_lease_released"],
  ["expired", [{status: "active", active: false, owner: "agent-a"}], "task_lease_expired"],
] as const) {
  test(`${label} lease returns one atomic managed claim`, () => {
    const result = projectExecutionPreparation(facts({leases}));
    assert.equal(result.state, "claim_required");
    assert.equal(result.delivery_allowed, false);
    assert.equal(result.reason_code, reasonCode);
    assert.deepEqual((result.claim as Record<string, unknown>).cli_args, [
      "todo", "claim", "--goal-id", "goal-example", "--todo-id", "todo_example",
      "--agent-id", "agent-a", "--claimed-by", "agent-a",
    ]);
    assert.equal((result.claim as Record<string, unknown>).host_execution_allowed, true);
  });
}

test("foreign ownership remains blocked", () => {
  const lease = projectExecutionPreparation(facts({leases: [
    {status: "active", active: true, owner: "agent-b"},
  ]}));
  assert.equal(lease.state, "blocked_by_conflict");
  assert.equal(lease.reason_code, "active_lease_owned_by_other_agent");
  assert.equal(lease.delivery_allowed, false);
  assert.equal(Object.hasOwn(lease, "claim"), false);

  const todo = projectExecutionPreparation(facts({selected_todo: {
    todo_id: "todo_example", status: "open", claimed_by: "agent-b",
  }}));
  assert.equal(todo.state, "blocked_by_conflict");
  assert.equal(todo.reason_code, "todo_claimed_by_other_agent");
});

test("soft claims preserve delivery and unavailable authority fails closed", () => {
  const soft = projectExecutionPreparation(facts({handoff_mode: "soft_claim"}));
  assert.equal(soft.state, "not_required");
  assert.equal(soft.delivery_allowed, true);

  const unavailable = projectExecutionPreparation(facts({authority_available: false}));
  assert.equal(unavailable.state, "authority_unavailable");
  assert.equal(unavailable.delivery_allowed, false);
});

test("malformed facts are rejected", () => {
  assert.throws(
    () => projectExecutionPreparation(facts({handoff_mode: "future"})),
    EffectRuntimeRequestError,
  );
  assert.throws(
    () => projectExecutionPreparation(facts({leases: [{status: "released", active: true}]})),
    /active requires status=active/,
  );
});
