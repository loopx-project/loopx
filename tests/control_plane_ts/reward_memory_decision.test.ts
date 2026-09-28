import assert from "node:assert/strict";
import test from "node:test";
import {buildRewardMemorySurfaceReadCheckpoints, planRewardMemoryDecision, projectRewardMemoryDecision} from "../../loopx/control_plane/capabilities/reward_memory_decision.ts";

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
  for (const patch of [{provider_call_count: -1}, {filtered_count: 1.5}, {status: "success"},
    {boundary_reason_code: "private exception text"}]) {
    assert.throws(() => projectRewardMemoryDecision({request, observation: {...observation, ...patch}}));
  }
});

test("project only the original hook's safe typed boundary reason", () => {
  const rejected = projectRewardMemoryDecision({request, observation: {...observation,
    status: "guard_rejected", recall_status: null, provider_call_count: 0,
    result_readback_verified: false, boundary_reason_code: "exact_corpus_request_invalid"}});
  assert.equal(rejected.status, "incomplete");
  assert.equal(rejected.reason_code, "recall_boundary_rejected");
  assert.equal(rejected.boundary_reason_code, "exact_corpus_request_invalid");
  assert.equal(rejected.provider_call_count, 0);
});

test("surface checkpoint assembly preserves caller proof and exact identity without verifying it", () => {
  const input = {surface_id: "review.summary", verified: false, source_ref: "policy:original",
    corpora: [{corpus_id: "one", read_authority: "actor_scoped",
      scope: {workspace_ref: "workspace:one", project_ref: "project:one",
        user_ref: "user:one", peer_ref: "peer:one", session_ref: "session:one"}}]};
  assert.deepEqual(buildRewardMemorySurfaceReadCheckpoints(input), {checkpoints: {one: {
    verified: false, source_ref: "policy:original", surface_id: "review.summary", corpus_id: "one",
    read_authority: "actor_scoped", ...input.corpora[0].scope,
  }}});
  for (const patch of [{verified: "true"}, {source_ref: ""}, {source_ref: "private prose is not proof"},
    {corpora: [...input.corpora, ...input.corpora]}, {corpora: [{}]}]) {
    assert.throws(() => buildRewardMemorySurfaceReadCheckpoints({...input, ...patch}));
  }
});

test("safe diagnostic details never become unbound success or private exception text", () => {
  const rejected = {...observation, status: "guard_rejected", recall_status: null,
    provider_call_count: 0, result_readback_verified: false, boundary_reason_code: "exact_corpus_request_invalid"};
  for (const detail of ["freshness_age_invalid", "freshness_context_invalid",
    "read_authority_checkpoint_missing", "read_authority_checkpoint_invalid"]) {
    const result = projectRewardMemoryDecision({request, observation: {...rejected, boundary_detail_code: detail}});
    assert.equal(result.boundary_detail_code, detail);
    assert.equal(result.reason_code, "recall_boundary_rejected");
    assert.equal(result.provider_call_count, 0);
    assert.equal(result.decision_consumption_complete, false);
  }
  for (const patch of [{boundary_detail_code: "private exception body"},
    {boundary_detail_code: "freshness_age_invalid", status: "applied"},
    {boundary_detail_code: "freshness_age_invalid", boundary_reason_code: "automation_config_invalid"}]) {
    assert.throws(() => projectRewardMemoryDecision({request, observation: {...rejected, ...patch}}));
  }
});

test("semantic assessment preserves a separately bound context delivery, not utility", () => {
  const contextReceipt = {...observation.application_receipt, outcome: "applied"};
  for (const outcome of ["applied", "ignored", "refuted"]) {
    const result = projectRewardMemoryDecision({request, observation: {...observation, status: outcome,
      application_receipt: {...observation.application_receipt, outcome},
      context_delivery_receipt: contextReceipt}});
    assert.equal(result.context_delivery_verified, true);
    assert.equal(result.decision_consumption_complete, true);
    assert.equal(result.semantic_disposition, outcome);
    assert.equal(result.provider_call_count, 2);
    assert.equal(result.filtered_count, 1);
    assert.equal(result.utility_verified, false);
    assert.equal(result.grants_new_action_authority, false);
  }
  assert.equal(projectRewardMemoryDecision({request, observation}).context_delivery_verified, false);
});

test("delivery evidence cannot transfer across identity, artifact, surface or lesson", () => {
  const contextReceipt = {...observation.application_receipt, outcome: "applied"};
  for (const patch of [{application_id: "other"}, {artifact_ref: "artifact:other"}, {surface_id: "other"},
    {schema_version: "other"}, {outcome: "ignored"}, {current_artifact_verified: false},
    {result_readback_verified: false}, {memory_ref_digests: []}, {memory_ref_digests: ["b".repeat(16)]}]) {
    const result = projectRewardMemoryDecision({request, observation: {...observation,
      context_delivery_receipt: {...contextReceipt, ...patch}}});
    assert.equal(result.context_delivery_verified, false);
    // A legitimate direct semantic assessment remains valid, independent of delivery.
    assert.equal(result.decision_consumption_complete, true);
  }
  assert.throws(() => projectRewardMemoryDecision({request, observation: {...observation,
    context_delivery_receipt: "not a receipt"}}));
  const recalled = projectRewardMemoryDecision({request: {...request, mode: "recall_only"},
    observation: {...observation, context_delivery_receipt: contextReceipt}});
  assert.equal(recalled.context_delivery_verified, false);
  assert.equal(recalled.decision_consumption_complete, false);
});

test("failed assessment preserves verified delivery without completing the decision", () => {
  const result = projectRewardMemoryDecision({request, observation: {...observation, status: "failed",
    application_receipt: {...observation.application_receipt, outcome: "failed", memory_ref_digests: [],
      current_artifact_verified: false},
    context_delivery_receipt: {...observation.application_receipt, outcome: "applied"}}});
  assert.equal(result.context_delivery_verified, true);
  assert.equal(result.status, "incomplete");
  assert.equal(result.decision_consumption_complete, false);
  assert.equal(result.preserve_base_output, true);
});
