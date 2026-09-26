import assert from "node:assert/strict";
import test from "node:test";
import {planRewardMemoryDecision, projectRewardMemoryDecision} from "../../loopx/control_plane/capabilities/reward_memory_decision.ts";

const request = {mode: "execute", query_ready: true, application_kind: "semantic_application",
  has_applier: true, application_id: "application:one", artifact_ref: "artifact:current", surface_id: "review.summary"};
const observation = {status: "ignored", recall_status: "completed", provider_call_count: 2, filtered_count: 1, result_readback_verified: true,
  application_receipt: {schema_version: "reward_memory_application_receipt_v0", application_id: "application:one",
    artifact_ref: "artifact:current", surface_id: "review.summary", outcome: "ignored",
    current_artifact_verified: true, result_readback_verified: true, memory_ref_digests: ["a".repeat(16)]}};

test("execute requires explicit strategy and current artifact before recall", () => {
  assert.equal(planRewardMemoryDecision({...request, has_applier: false}).should_recall, false);
  assert.equal(planRewardMemoryDecision({...request, application_kind: null}).reason_code, "application_strategy_required");
  assert.equal(planRewardMemoryDecision({...request, artifact_ref: null}).reason_code, "current_artifact_binding_required");
  assert.equal(planRewardMemoryDecision({...request, mode: "preview"}).should_recall, false);
  assert.equal(planRewardMemoryDecision({...request, query_ready: false}).should_recall, false);
});

test("context delivery, recall-only and semantic judgment are different receipts", () => {
  const delivered = projectRewardMemoryDecision({request: {...request, application_kind: "context_delivery"},
    observation: {...observation, status: "applied", application_receipt: {...observation.application_receipt, outcome: "applied"}}});
  assert.equal(delivered.status, "context_delivered");
  assert.equal(delivered.decision_consumption_complete, false);
  assert.equal(delivered.semantic_disposition, null);
  const recalled = projectRewardMemoryDecision({request: {...request, mode: "recall_only", has_applier: false}, observation});
  assert.equal(recalled.status, "recalled");
  assert.equal(recalled.decision_consumption_complete, false);
  const ignored = projectRewardMemoryDecision({request, observation});
  assert.equal(ignored.decision_consumption_complete, true);
  assert.equal(ignored.preserve_base_output, true);
  assert.equal(ignored.utility_verified, false);
  assert.equal(ignored.provider_call_count, 2);
  assert.equal(ignored.filtered_count, 1);
});

test("stale/wrong attribution cannot complete semantic consumption", () => {
  for (const patch of [{artifact_ref: "artifact:old"}, {surface_id: "other"}, {application_id: "other"},
    {memory_ref_digests: []}, {current_artifact_verified: false}, {result_readback_verified: false}, {outcome: "applied"}]) {
    const result = projectRewardMemoryDecision({request, observation: {...observation,
      application_receipt: {...observation.application_receipt, ...patch}}});
    assert.equal(result.status, "incomplete");
    assert.equal(result.decision_consumption_complete, false);
  }
});

test("decode unknown transport values strictly", () => {
  for (const patch of [{mode: "automatic"}, {application_kind: "inject_and_adopt"}, {query_ready: "true"},
    {artifact_ref: "private content with spaces"}]) {
    assert.throws(() => planRewardMemoryDecision({...request, ...patch}));
  }
  for (const patch of [{provider_call_count: -1}, {filtered_count: 1.5}, {status: "success"}]) {
    assert.throws(() => projectRewardMemoryDecision({request, observation: {...observation, ...patch}}));
  }
});
