import assert from "node:assert/strict";
import test from "node:test";
import {deflateSync} from "node:zlib";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {evaluateTurnEnvelope, quotaActionSignatureDocument} from "../../loopx/control_plane/quota/turn_envelope.ts";
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

test("readback-only validation never produces or accepts a reusable contribution", () => {
  const status = {...result, context_fields: {}, instructions: [], result_fields: {}};
  const request = {registration, result: status, readback_only: true};
  const readback = validateTurnContextContribution(request);
  assert.deepEqual(readback.readback, status.readback);
  assert.equal(Object.hasOwn(readback, "basis_digest"), false);
  assert.throws(() => projectTurnContextContribution({contribution: readback, readback: status.readback, source}));
  assert.throws(() => validateTurnContextContribution({...request, source}));
  assert.throws(() => validateTurnContextContribution({registration, result: status}));
  for (const patch of [{context_fields: result.context_fields}, {instructions: result.instructions},
    {result_fields: result.result_fields}]) {
    assert.throws(() => validateTurnContextContribution({...request, result: {...status, ...patch}}));
  }
  assert.throws(() => validateTurnContextContribution({...request, result: {...status, readback: {other_slot: {}}}}));
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

test("the shared quota source transport preserves capability gates and contribution binding", () => {
  const source = {...quotaSource, goal_boundary: {...quotaSource.goal_boundary, stop_condition: "保留原停止条件"}};
  const source_transport = {encoding: "deflate-base64-json-v0",
    data: deflateSync(Buffer.from(JSON.stringify(source))).toString("base64")};
  const operation = "capability_automation_source";
  const inline = evaluateTurnEnvelope({operation, payload: source});
  assert.deepEqual(evaluateTurnEnvelope({operation, source_transport}), inline);
  const contribution = validateTurnContextContribution({registration, result, source});
  assert.deepEqual(validateTurnContextContribution({registration, result, source_transport}), contribution);
  assert.throws(() => projectTurnContextContribution({contribution, readback: result.readback,
    source: {...source, goal_boundary: {...source.goal_boundary, stop_condition: "改动停止条件"}}}));
  assert.throws(() => evaluateTurnEnvelope({operation, payload: source, source_transport}), /invalid Turn/);
  assert.throws(() => evaluateTurnEnvelope({operation, envelope: {}, source_transport}), /invalid Turn/);
  assert.throws(() => validateTurnContextContribution({registration, result, source, source_transport}), /invalid Turn/);
  const status = {...result, context_fields: {}, instructions: [], result_fields: {}};
  assert.throws(() => validateTurnContextContribution({registration, result: status, readback_only: true,
    source_transport}), /readback-only/);
  assert.throws(() => validateTurnContextContribution({registration, result, source_transport: {
    ...source_transport, data: "AAAA"}}), /valid compressed JSON/);
});

for (const [suffixName, suffix] of [
  ["trailing bytes", Buffer.from([0, 255])],
  ["a second stream", deflateSync(Buffer.from(JSON.stringify({...quotaSource, goal_boundary: {
    ...quotaSource.goal_boundary, stop_condition: "A different stop"}})))],
] as const) {
  const data = Buffer.concat([deflateSync(Buffer.from(JSON.stringify(quotaSource))), suffix]).toString("base64");
  const source_transport = {encoding: "deflate-base64-json-v0", data};
  test(`quota capability gates reject ${suffixName} after the packed source`, () => {
    assert.throws(() => evaluateTurnEnvelope({operation: "capability_automation_source", source_transport}),
      /valid compressed JSON/);
  });
  test(`quota contribution validation rejects ${suffixName} after the packed source`, () => {
    assert.throws(() => validateTurnContextContribution({registration, result, source_transport}),
      /valid compressed JSON/);
  });
}

test("an existing quota contribution can replay with its exact legacy reader binding", () => {
  const source = {...quotaSource, replan_read_path: "fixture/decision.json",
    replan_action_packet: {obligation_id: "fixture-replan", decision: "replan_required"}};
  const current = validateTurnContextContribution({registration, source, result});
  const {basis_digest: currentBasis, ...value} = current;
  const legacyBasis = canonicalAuthoritySha256({source: {goal_id: source.goal_id, agent_id: source.agent_id,
    governing: quotaActionSignatureDocument(source)}, ...value});
  assert.notEqual(legacyBasis, currentBasis);
  const contribution = {...value, basis_digest: legacyBasis};
  assert.deepEqual(projectTurnContextContribution({contribution, source, readback: result.readback}), contribution);
  assert.throws(() => projectTurnContextContribution({contribution, readback: result.readback,
    source: {...source, replan_read_path: "fixture/other.json"}}));
});

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

for (const field of ["write_scope", "primary_action", "turn_envelope", "work_contract", "source_decision_hash",
  "result_contract", "work_evidence", "validation_evidence"]) {
  test(`capability cannot replace ${field}`, () => {
    assert.throws(() => validateTurnContextRegistration({...registration, readback_slots: [field]}));
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
