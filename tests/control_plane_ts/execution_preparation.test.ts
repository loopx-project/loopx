import assert from "node:assert/strict";
import test from "node:test";

import {
  EXECUTION_HANDOFF_MODES,
  HANDOFF_MODES,
} from "../../loopx/control_plane/coordination/handoff_mode_vocabulary.ts";
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

test("legacy facts stay readable and an old matching lease grants no new execution", () => {
  for (const mode of EXECUTION_HANDOFF_MODES) {
    assert.doesNotThrow(() => projectExecutionPreparation(facts({handoff_mode: mode})));
  }
  const historical: readonly string[] = HANDOFF_MODES;
  const execution: readonly string[] = EXECUTION_HANDOFF_MODES;
  assert.ok(historical.includes("legacy"), "legacy stays decodable for stored receipts");
  assert.ok(!execution.includes("legacy"), "legacy is not a new execution target");
  const retired = projectExecutionPreparation(facts({handoff_mode: "legacy", leases: [
    {status: "active", active: true, owner: "agent-a"},
  ]}));
  assert.equal(retired.handoff_mode, "legacy");
  assert.equal(retired.state, "blocked_by_conflict");
  assert.equal(retired.reason_code, "handoff_mode_retired");
  assert.equal(retired.delivery_allowed, false);
  assert.equal(Object.hasOwn(retired, "claim"), false);
});


test("only a matching active lease contributes its exact completion fence", () => {
  const proof = {idempotency_key: "original-attempt", expected_version: 7};
  const active = {status: "active", active: true, owner: "agent-a", lease_proof: proof};
  assert.deepEqual(projectExecutionPreparation(facts({leases: [active]})).lease_proof, proof);
  for (const lease of [{...active, owner: "agent-b"},
    {...active, active: false}, {...active, status: "released", active: false}]) {
    assert.equal(Object.hasOwn(projectExecutionPreparation(facts({leases: [lease]})), "lease_proof"), false);
  }
  for (const lease_proof of [{...proof, expected_version: true},
    {...proof, expected_version: 0}, {...proof, expected_version: Number.MAX_SAFE_INTEGER + 1},
    {...proof, idempotency_key: " original-attempt"}, {expected_version: 7}]) {
    assert.throws(() => projectExecutionPreparation(facts({leases: [{...active, lease_proof}]})),
      EffectRuntimeRequestError);
  }
});
