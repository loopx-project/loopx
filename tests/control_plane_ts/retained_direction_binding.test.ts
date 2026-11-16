import assert from "node:assert/strict";
import test from "node:test";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {settlementIdentityPayload, type JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {evaluateCheckpointReadContext as read, evaluateResultWorkBinding as work}
  from "../../loopx/control_plane/goals/checkpoint_read_context.ts";

const identity = settlementIdentityPayload({goal_id: "result-goal", agent_id: "agent",
  todo_id: "todo_original", turn_instance_id: "original-turn"});
const facts = {todos: [{todo_id: identity.todo_id, role: "agent", status: "open", claimed_by: "agent"}],
  frontmatter: {objective: "Keep original result"}, goal_prose: "Qualify the original progress.",
  acceptance: null, agent_vision: {summary: "Verify original evidence"}, source: {}};
function direction(): JsonObject {
  const captured = read({phase: "read", purpose: "supplement_checkpoint", identity, facts,
    read_context_id: "current-direction", checkpoint_continuation: true,
    full_frontier: true, decision_scope: "agent_lane",
    prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}}});
  assert.equal(captured.ok, true);
  return {...captured.receipt as JsonObject, direction_host_attempt_count: 1};
}
const decision = {read_context_id: "current-direction", vision_unchanged_reason: "Original evidence supports this route."};

test("saved direction binds its exact retained result proof without trusting a decision-supplied digest", () => {
  const original = direction();
  const retained = {settlement_identity: identity, index_record_sha256: "original-index",
    work_receipt_sha256: "original-work-and-validation"};
  const receipt = {...original, retained_result_binding: retained};
  const returned = work({phase: "direction_return", receipt, identity,
    decision: {...decision, retained_result_binding_sha256: "host-supplied"}});
  assert.equal(returned.ok, true);
  const saved = returned.receipt as JsonObject;
  assert.equal((saved.current_agent_binding as JsonObject).retained_result_binding_sha256,
    canonicalAuthoritySha256(retained));
  const changed = work({phase: "direction_response", identity,
    receipt: {...saved, retained_result_binding: {...retained, work_receipt_sha256: "replacement"}},
    decision: saved.current_agent_decision});
  assert.notDeepEqual(changed.binding, saved.current_agent_binding);
  assert.equal(work({phase: "direction_response", identity,
    receipt: {...saved, retained_result_binding: {...retained, settlement_identity: {...identity, todo_id: "foreign"}}},
    decision}).error_code, "checkpoint_direction_binding_mismatch");
});

test("legacy direction binding stays unchanged and a fresh read never reuses the old judgment", () => {
  const original = direction();
  const legacy = work({phase: "direction_response", identity, receipt: original, decision});
  assert.deepEqual(legacy.binding, {read_context_id: original.read_context_id,
    identity_sha256: canonicalAuthoritySha256(identity), decision_sha256: canonicalAuthoritySha256(decision)});
  const returned = work({phase: "direction_return", identity, decision,
    receipt: {...original, retained_result_binding: {settlement_identity: identity, work_receipt_sha256: "original"}}});
  const renewed = read({phase: "read", purpose: "supplement_checkpoint", identity,
    facts: {...facts, goal_prose: "Changed requirement"}, read_context_id: "new-direction",
    checkpoint_continuation: true, full_frontier: true, decision_scope: "agent_lane",
    prior: {vision_checkpoint: {decision: "missing_required", satisfied: false}}, receipt: returned.receipt});
  assert.equal(renewed.ok, true);
  assert.equal((renewed.receipt as JsonObject).current_agent_decision, undefined);
  assert.equal(work({phase: "direction_dispatch", receipt: renewed.receipt}).attempt, 2);
});
