import assert from "node:assert/strict";
import test from "node:test";
import {normalizeStrideDecision, strideControlContract} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const contextId = "replan-context-" + "a".repeat(24);
const proposal = {schema_version: "stride_decision_v1", strategy_version: "evidence_conditioned_v1",
  reference_context_id: contextId, reference_obligation_id: "replan-" + "a".repeat(16), basis: "unexplained_gap", next_probe: {
    scope: "Compare a bounded alternative route.", prediction: "The alternative explains the remaining gap.",
    adoption_criterion: "Retain an attributable validated gain.", cost_bound: "One small probe before expanding."}};
const packet = {schema_version: "goal_vision_replan_contract_v0", agent_id: "agent-a",
  vision_patch: {acceptance_summary: "Applicable evidence leaves a goal gap."}, path_delta: {
    schema_version: "goal_path_delta_v0", outcome: "replan", prior_assumption: "Local tuning could close the gap.",
    observed_reality: "Evidence leaves that explanation unresolved.", changed: ["Test an alternative mechanism."],
    evidence_refs: ["evidence:review"]}, stride_decision: proposal};
const request = {schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare", goal_id: "goal-a",
  agent_id: "agent-a", merge_patch: false, existing_agent_vision: null,
  require_path_delta_for_durable_change: false, agent_vision_packet: packet};

test("v1 separates route departure from work size and uncertainty from route novelty", () => {
  assert.deepEqual(normalizeStrideDecision(proposal)[0]?.axes,
    {work_slice: "shrink", route_distance: "grow", validation_scope: "shrink"});
  assert.deepEqual(normalizeStrideDecision({...proposal, basis: "uncertain_evidence"})[0]?.axes,
    {work_slice: "shrink", route_distance: "hold", validation_scope: "grow"});
  assert.deepEqual(normalizeStrideDecision({...proposal, basis: "supported_progress"})[0]?.axes,
    {work_slice: "grow", route_distance: "hold", validation_scope: "hold"});
  assert.equal(strideControlContract({stride_strategy: "off"}), null);
  const prepared = buildVisionCheckpoint(request);
  const decision = (prepared.agent_vision as Record<string, unknown>).stride_decision as Record<string, unknown>;
  assert.deepEqual(decision.axes, {work_slice: "shrink", route_distance: "grow", validation_scope: "shrink"});
  const budget = (prepared.agent_vision as Record<string, unknown>).vision_budget as Record<string, unknown>;
  assert.ok(Number(budget.total_usage) < 1800);
});

test("invalid, ambiguous, oversized and misplaced control proposals fail authoring", () => {
  for (const patch of [{basis: "plateau_from_elapsed_time"}, {strategy_version: "future_v2"},
    {axes: {work_slice: "grow", route_distance: "grow", validation_scope: "grow"}},
    {next_probe: {...proposal.next_probe, scope: "x".repeat(121)}}, {reference_context_id: "missing"},
    {permission: "execute_anything"}, {next_probe: {...proposal.next_probe, cost_bound: ""}}]) {
    assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
      ...packet, stride_decision: {...proposal, ...patch}}}));
  }
  assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
    ...packet, vision_patch: {...packet.vision_patch, stride_decision: proposal}, stride_decision: undefined}}), /agent_vision.stride_decision/);
  assert.throws(() => buildVisionCheckpoint({...request, agent_vision_packet: {
    ...packet, stride_decision: {...proposal, next_probe: {...proposal.next_probe, scope: "/Users/private-owner/file"}}}}), /private/);
});

test("an accepted schema and vector do not authorize work or certify gain; stale references cannot discharge a path review", () => {
  const vision = buildVisionCheckpoint(request).agent_vision;
  const obligation = {obligation_id: "replan-" + "a".repeat(16), triggers: [{kind: "vision_acceptance_gap"}],
    replan_context: {stride_strategy: "evidence_conditioned_v1", context_id: contextId}};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation, agent_vision: vision}).accepted, true);
  const stale = projectReplanSemantics({operation: "qualify", obligation: {...obligation, obligation_id: "replan-" + "b".repeat(16)}, agent_vision: vision});
  // A changed live context from shared-pool churn cannot steal the admitted obligation.
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {...obligation,
    replan_context: {...obligation.replan_context, context_id: "replan-context-" + "b".repeat(24)}}, agent_vision: vision}).accepted, true);
  const legacyVision = {...packet, stride_decision: undefined};
  const missing = projectReplanSemantics({operation: "qualify", obligation, agent_vision: legacyVision});
  assert.equal(missing.accepted, false);
  assert.equal(missing.reason_code, "stride_decision_required");
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {...obligation,
    replan_context: {stride_strategy: "off"}}, agent_vision: legacyVision}).accepted, true);
  assert.equal(stale.accepted, false);
  assert.equal(stale.reason_code, "stride_reference_obligation_mismatch");
  assert.equal(projectReplanSemantics({operation: "qualify", obligation, stride_decision: proposal}).accepted, false);
});
