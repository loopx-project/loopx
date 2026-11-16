import assert from "node:assert/strict";
import test from "node:test";
import {settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {evaluateFirstDelivery} from "../../loopx/control_plane/turn_driver/first_delivery.ts";
import {settlementIdentityFromPlan} from "../../loopx/control_plane/effect_program.ts";
import {isAcceptedSettlementDirection} from "../../loopx/control_plane/quota/settlement_phase.ts";

const identity = settlementIdentityPayload({goal_id: "progress-goal", agent_id: "progress-agent",
  todo_id: "todo_progress", turn_instance_id: "progress-turn"});
function request(decision = "patched", settled = false): JsonObject {
  return {phase: "project", identity, checkpoint_continuation: true,
    completion_todo: {status: "open"}, quota_spent: settled, settlement_complete: settled,
    writeback_run: {goal_id: identity.goal_id, agent_id: identity.agent_id,
      todo_id: identity.todo_id, turn_instance_id: identity.turn_instance_id, delivery_outcome: "outcome_progress",
      vision_checkpoint: {schema_version: "vision_checkpoint_v0", agent_id: identity.agent_id,
        required: false, satisfied: true, decision, delivery_boundary: "in_flight_continuation",
        triggers: [{kind: "in_flight_continuation", todo_id: identity.todo_id}]}}};
}

for (const decision of ["not_required", "patched", "unchanged_with_reason"]) for (const settled of [false, true]) {
  test(`accepted open progress ${decision} follows settlement without inventing direction: ${settled}`, () => {
    const input = request(decision, settled), before = structuredClone(input);
    const observed = evaluateFirstDelivery(input);
    assert.equal(observed.stage, settled ? "settled" : "settlement_pending");
    assert.equal(observed.direction_required, false);
    assert.equal(observed.direction_committed, false);
    assert.equal(observed.result_committed, false, "progress is not Todo completion");
    assert.equal(observed.outcome_retained, true);
    assert.equal(observed.quota_spent, settled);
    assert.equal(observed.goal_completion_certified, false);
    assert.deepEqual(input, before, "observation has no effects");
  });
}

test("an optional patch alone cannot qualify foreign, incomplete or semantic progress", () => {
  const edits: ((input: JsonObject, run: JsonObject, checkpoint: JsonObject) => void)[] = [
    (_i, run) => {run.goal_id = "other-goal";}, (_i, run) => {run.agent_id = "other-agent";},
    (_i, run) => {run.todo_id = "other-todo";}, (_i, run) => {run.turn_instance_id = "other-turn";},
    (input) => {input.identity = {...identity, effect_id: "other-effect"};},
    (_i, _r, checkpoint) => {checkpoint.satisfied = false;},
    (_i, _r, checkpoint) => {checkpoint.required = true;},
    (_i, _r, checkpoint) => {checkpoint.delivery_boundary = "semantic_closeout";},
    (_i, _r, checkpoint) => {checkpoint.triggers = [{kind: "in_flight_continuation", todo_id: "other-todo"}];},
    (_i, _r, checkpoint) => {checkpoint.decision = "future_decision";},
  ];
  for (const edit of edits) {
    const input = request("patched", true), run = input.writeback_run as JsonObject;
    edit(input, run, run.vision_checkpoint as JsonObject);
    const observed = evaluateFirstDelivery(input);
    assert.equal(observed.direction_required, true, JSON.stringify(input));
    assert.notEqual(observed.stage, "settled", JSON.stringify(input));
  }
});

test("uncertain effects retain precedence over an accepted in-flight checkpoint", () => {
  const observed = evaluateFirstDelivery({...request("patched", true), unknown: true});
  assert.equal(observed.stage, "operation_unknown");
  assert.equal(observed.goal_completion_certified, false);
});

test("optional work-read metadata cannot certify a direction or clear an unknown attempt", () => {
  const input = request("patched", true), run = input.writeback_run as JsonObject;
  (run.vision_checkpoint as JsonObject).read_context = {
    ok: true, read_context_id: "original-work-read", purpose: "delivery_result"};
  assert.equal(evaluateFirstDelivery(input).direction_committed, false);
  assert.equal(evaluateFirstDelivery({...input, direction_receipt: {commit_attempt: {}}}).stage, "operation_unknown");
});

test("historical not-required observations keep their existing no-direction path", () => {
  const observed = evaluateFirstDelivery({phase: "project", settlement_complete: true,
    writeback_run: {vision_checkpoint: {decision: "not_required", required: false, satisfied: true}}});
  assert.equal(observed.stage, "settled");
  assert.equal(observed.direction_required, false);
  assert.equal(observed.direction_committed, false);
  assert.equal(observed.result_committed, false);
});

test("the settlement owner accepts an optional in-flight patch without waiving a replan obligation", () => {
  const decoded = settlementIdentityFromPlan({settlement_plan: {identity}});
  assert.equal(decoded.failure, null);
  if (decoded.failure !== null) throw new Error("invalid test identity");
  const run = request().writeback_run;
  const receipt = {details: {checkpoint_continuation: "current_agent_v1"}};
  assert.equal(isAcceptedSettlementDirection(run, decoded.value, receipt, null), true);
  assert.equal(isAcceptedSettlementDirection(run, decoded.value, receipt, "other-obligation"), false);
  const wrong = {...run as JsonObject, turn_instance_id: "other-turn"};
  assert.equal(isAcceptedSettlementDirection(wrong, decoded.value, receipt, null), false);
});
