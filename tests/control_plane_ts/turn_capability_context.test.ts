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
