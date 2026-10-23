import assert from "node:assert/strict";
import test from "node:test";
import {validateTurnContextRegistration, validateTurnContextContribution,
  projectTurnContextContribution} from "../../loopx/control_plane/turn_driver/capability_context.ts";

const registration = {hook_id: "fixture.context", capability_id: "fixture-capability",
  readback_slots: ["fixture_readback"], max_result_bytes: 4096};
const source = {goal_id: "fixture-goal", agent_id: "fixture-agent", boundary: {write_scope: []}};
const result = {readback: {fixture_readback: {value: "private advice"}},
  context_fields: {fixture_context: {value: "private advice"}}, instructions: ["Advisory only."],
  result_fields: {fixture_result: {type: "string", maxLength: 240}}};

test("registered context survives location-only capture binding", () => {
  const contribution = validateTurnContextContribution({registration, source, result});
  assert.deepEqual(projectTurnContextContribution({contribution, readback: result.readback,
    source: {...source, detail_ref: {full_decision: "fixture location"}, measurements: {bytes: 500}}}), contribution);
});

const quotaSource = {goal_id: "fixture-goal", agent_id: "fixture-agent",
  ok: true, should_run: true, mode: "should-run", effective_action: "normal_run",
  interaction_contract: {schema_version: "loopx_interaction_contract_v0", mode: "bounded_delivery",
    user_channel: {action_required: false, notify: "DONT_NOTIFY"}, agent_channel: {must_attempt: true,
      delivery_allowed: true, quiet_noop_allowed: false}, cli_channel: {}},
  agent_identity: {agent_id: "fixture-agent", registered: true},
  heartbeat_receipt: {status: "committed", turn_instance_id: "fixture-turn"},
  goal_boundary: {write_scope: ["fixture/**"], capabilities: {reward_memory: {
    enabled: true, configured_for_agent: true, experiment_available: true,
    automatic_recall: true, automatic_ingest: false}}}};

test("native quota context survives location-only capture binding", () => {
  const contribution = validateTurnContextContribution({registration, source: quotaSource, result});
  assert.deepEqual(projectTurnContextContribution({contribution, readback: result.readback,
    source: {...quotaSource, detail_ref: {full_decision: "fixture location"}, measurements: {bytes: 500}}}), contribution);
});

for (const change of ["policy", "write_scope", "agent"]) {
  test(`changed quota ${change} invalidates the optional contribution`, () => {
    const contribution = validateTurnContextContribution({registration, source: quotaSource, result});
    const changed = structuredClone(quotaSource);
    if (change === "policy") changed.goal_boundary.capabilities.reward_memory.automatic_recall = false;
    else if (change === "write_scope") changed.goal_boundary.write_scope = [];
    else changed.agent_id = "another-agent";
    assert.throws(() => projectTurnContextContribution({contribution, source: changed, readback: result.readback}));
  });
}

for (const change of ["goal", "agent", "authority", "readback", "instructions", "result_fields", "context_fields"]) {
  test(`changed ${change} invalidates the optional contribution`, () => {
    const contribution = validateTurnContextContribution({registration, source, result});
    const input = structuredClone({contribution, source, readback: result.readback});
    if (change === "goal") input.source.goal_id = "another-goal";
    else if (change === "agent") input.source.agent_id = "another-agent";
    else if (change === "authority") input.source.boundary.write_scope.push("forbidden" as never);
    else if (change === "readback") input.readback.fixture_readback.value = "changed";
    else input.contribution[change] = change === "instructions" ? ["changed"] : {};
    assert.throws(() => projectTurnContextContribution(input));
  });
}

for (const field of ["write_scope", "primary_action", "turn_envelope", "result_contract", "work_evidence", "validation_evidence"]) {
  test(`capability cannot replace ${field}`, () => {
    assert.throws(() => validateTurnContextContribution({registration, source,
      result: {...result, context_fields: {[field]: "replacement"}}}));
    assert.throws(() => validateTurnContextContribution({registration, source,
      result: {...result, result_fields: {[field]: {type: "string", maxLength: 240}}}}));
  });
}

test("registration and result budgets fail closed", () => {
  assert.throws(() => validateTurnContextRegistration({...registration, max_result_bytes: 65537}));
  assert.throws(() => validateTurnContextRegistration({...registration, readback_slots: ["fixture", "fixture"]}));
  assert.throws(() => validateTurnContextContribution({registration, source,
    result: {...result, instructions: ["x".repeat(5000)]}}));
  assert.throws(() => validateTurnContextContribution({registration, source,
    result: {...result, result_fields: {fixture_result: {type: "object"}}}}));
  assert.throws(() => validateTurnContextContribution({registration, source,
    result: {...result, readback: {another_slot: {value: "private"}}}}));
});
