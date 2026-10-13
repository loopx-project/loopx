import assert from "node:assert/strict";
import test from "node:test";
import {normalizeStrideDecision, strideControlContract, stridePlanningGuidance} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA} from "../../loopx/control_plane/goals/vision_checkpoint.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const contextId = "replan-context-" + "a".repeat(24);
const proposal = {schema_version: "stride_decision_v2", strategy_version: "evidence_conditioned_v1",
  reference_context_id: contextId, reference_obligation_id: "replan-" + "a".repeat(16), basis: "unexplained_gap", action: "switch", axes: {
    work_slice: {plan: "Build the complete alternate engine and compare it with the incumbent.", reason: "A partial component cannot test the end-to-end explanation."},
    route_distance: {plan: "Replace greedy selection with joint search; keep the objective fixed.", reason: "The gap may come from interactions greedy selection cannot capture."},
    validation_scope: {plan: "Run both engines on the same held-out inputs and compare full costs.", reason: "Existing independent coverage distinguishes the mechanisms."}}, next_probe: {
    scope: "Compare a bounded alternative route.", prediction: "The alternative explains the remaining gap.",
    adoption_criterion: "Retain an attributable validated gain.", cost_bound: "One complete experiment before expanding.", minimum_evidence: "An executed alternative and a controlled attributable comparison."}};
const packet = {schema_version: "goal_vision_replan_contract_v0", agent_id: "agent-a",
  vision_patch: {acceptance_summary: "Applicable evidence leaves a goal gap."}, path_delta: {
    schema_version: "goal_path_delta_v0", outcome: "replan", prior_assumption: "Local tuning could close the gap.",
    observed_reality: "Evidence leaves that explanation unresolved.", changed: ["Test an alternative mechanism."],
    evidence_refs: ["evidence:review"]}, stride_decision: proposal};
const request = {schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare", goal_id: "goal-a",
  agent_id: "agent-a", merge_patch: false, existing_agent_vision: null,
  require_path_delta_for_durable_change: false, agent_vision_packet: packet};

test("concrete independent plans retain action choice and fit the shared vision budget", () => {
  for (const basis of ["unexplained_gap", "uncertain_evidence", "supported_progress"]) {
    const decision = normalizeStrideDecision({...proposal, basis})[0]!;
    assert.deepEqual(decision.axes, proposal.axes);
    assert.equal(decision.action, "switch");
  }
  assert.equal(strideControlContract({stride_strategy: "off"}), null);
  const contract = strideControlContract({stride_strategy: "evidence_conditioned_v1"})!;
  assert.deepEqual(contract.actions, ["grow", "switch", "verify"]);
  assert.equal(Object.hasOwn(contract, "adjustments"), false);
  const prepared = buildVisionCheckpoint(request);
  const decision = (prepared.agent_vision as Record<string, unknown>).stride_decision as Record<string, unknown>;
  assert.deepEqual(decision.axes, proposal.axes);
  const budget = (prepared.agent_vision as Record<string, unknown>).vision_budget as Record<string, unknown>;
  assert.ok(Number(budget.total_usage) < 1800);
});

test("invalid, ambiguous, oversized and misplaced control proposals fail authoring", () => {
  for (const patch of [{basis: "plateau_from_elapsed_time"}, {strategy_version: "future_v2"},
    {axes: {work_slice: "shrink", route_distance: "grow", validation_scope: "grow"}},
    {action: "shrink"}, {action: "hold"}, {action: "continue"}, {action: undefined}, {axis_reasons: {}},
    {axes: {...proposal.axes, work_slice: {plan: "", reason: "Incomplete scope."}}},
    {axes: {...proposal.axes, route_distance: {plan: "x".repeat(121), reason: "Oversized."}}},
    {axes: {...proposal.axes, validation_scope: {plan: "Full comparison.", reason: "x".repeat(97)}}},
    {axes: {...proposal.axes, work_slice: {...proposal.axes.work_slice, adjustment: "shrink"}}}, {next_probe: {...proposal.next_probe, minimum_evidence: ""}},
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

test("all three actions preserve independently planned complete work and validation", () => {
  const axes = {...proposal.axes,
    work_slice: {plan: "Finish the expanded engine and paired independent repetitions.", reason: "The complete experiment is needed to resolve uncertainty."},
    validation_scope: {plan: "Add independent replicates sufficient to distinguish the uncertain effect.", reason: "A single noisy result cannot support adoption."}};
  for (const action of ["grow", "switch", "verify"]) {
    const decision = normalizeStrideDecision({...proposal, basis: "uncertain_evidence", action, axes})[0]!;
    assert.deepEqual(decision.axes, axes);
    assert.equal(decision.action, action);
  }
});

test("Explore evidence and previous adoption are bound without inferring gain or granting authority", async () => {
  const {projectStrideExploreBasis, strideReviewMismatch} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const basis = projectStrideExploreBasis({ok: true, goal_id: "goal-a", agent_id: "agent-a",
    graph_enabled: true, harness_enabled: true,
    graph: {writeback_results: [{finding_id: "finding-negative", node_id: "question-route", status: "refuted"}],
      result_page: {remaining: 4, revision: "b".repeat(64)}},
    harness: {selected_branches: [{todo_id: "todo-route", selection_status: "selected", actionable_open: true}]}}, "goal-a", "agent-a")!;
  const previous = "replan-decision-" + "b".repeat(24);
  const context = {stride_strategy: "evidence_conditioned_v1", explore_basis: basis,
    decision_followthrough: {previous_decision: {evidence_ref: previous}, observations: [{evidence_ref: "evidence:executed"}]}};
  const reviewed = {...proposal, previous_review: {reference_decision_ref: previous,
    adoption: "executed", information_gain: "new_evidence", goal_gain: "unknown", evidence_refs: ["evidence:executed"],
    lesson: "Execution gave new scoped evidence; target benefit remains unmeasured."},
    explore_review: {revision: basis.revision, disposition: "deferred", considered_refs: ["finding-negative"],
      reason: "Refutation covers different inputs; test the changed mechanism."}};
  const decision = normalizeStrideDecision(reviewed)[0]!;
  assert.equal(strideReviewMismatch(decision, context), null);
  assert.equal((decision.previous_review as Record<string, unknown>).goal_gain, "unknown");
  assert.match(String(strideReviewMismatch({...decision, previous_review: undefined}, context)), /previous/);
  assert.match(String(strideReviewMismatch({...decision, previous_review: {...reviewed.previous_review,
    reference_decision_ref: "replan-decision-" + "c".repeat(24)}}, context)), /previous/);
  assert.match(String(strideReviewMismatch({...decision, previous_review: {...reviewed.previous_review,
    evidence_refs: ["evidence:invented"]}}, context)), /undelivered/);
  assert.match(String(strideReviewMismatch({...decision, explore_review: {...reviewed.explore_review,
    revision: "c".repeat(64)}}, context)), /revision/);
  assert.match(String(strideReviewMismatch({...decision, explore_review: {...reviewed.explore_review,
    considered_refs: ["finding-foreign"]}}, context)), /foreign/);
  assert.equal(basis.result_page.remaining, 4); // Omitted evidence is not an empty graph.
  assert.throws(() => projectStrideExploreBasis({ok: true, goal_id: "foreign", agent_id: "agent-a"}, "goal-a", "agent-a"), /scope/);
  const unavailable = projectStrideExploreBasis({ok: false, goal_id: "goal-a", agent_id: "agent-a"}, "goal-a", "agent-a")!;
  const unknown = normalizeStrideDecision({...proposal, explore_review: {revision: null, disposition: "unavailable",
    considered_refs: [], reason: "Source unavailable; no applicability or utility inference."}})[0]!;
  assert.equal(strideReviewMismatch(unknown, {explore_basis: unavailable}), null);
  assert.match(String(strideReviewMismatch(decision, {explore_basis: unavailable})), /undelivered|revision/);
});

test("recorded fixed-vector decisions remain readable but cannot be newly authored", async () => {
  const {normalizeRecordedStrideDecision} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const {action, axes, ...legacy} = proposal;
  const {minimum_evidence, ...oldProbe} = legacy.next_probe;
  const recorded = {...legacy, schema_version: "stride_decision_v1", next_probe: oldProbe};
  assert.equal(normalizeRecordedStrideDecision(recorded)?.axes.work_slice, "shrink");
  assert.equal(normalizeStrideDecision(recorded)[0]?.axes.work_slice, "shrink");
  const oldVision = {...packet, stride_decision: recorded};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {obligation_id: proposal.reference_obligation_id,
    triggers: [{kind: "vision_acceptance_gap"}], replan_context: {stride_strategy: "evidence_conditioned_v1"}}, agent_vision: oldVision}).accepted, false);
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {obligation_id: proposal.reference_obligation_id,
    triggers: [{kind: "vision_acceptance_gap"}], replan_context: {stride_strategy: "off"}}, agent_vision: oldVision}).accepted, true);
  assert.throws(() => normalizeRecordedStrideDecision({...recorded, axes: {work_slice: "grow"}}), /historical/);
});

test("unrelated shared-pool churn keeps the evidence basis; changed scoped evidence does not", async () => {
  const {projectStrideExploreBasis} = await import("../../loopx/control_plane/work_items/stride_policy.ts");
  const source = {ok: true, goal_id: "goal-a", agent_id: "agent-a", graph_enabled: true, harness_enabled: true,
    graph: {writeback_results: [{finding_id: "finding-route", node_id: "route", status: "tentative", summary: "Inputs A only."}]},
    harness: {candidate_count: 1, selected_branches: []}};
  const first = projectStrideExploreBasis(source, "goal-a", "agent-a")!;
  assert.equal(projectStrideExploreBasis({...source, harness: {...source.harness, candidate_count: 100}}, "goal-a", "agent-a")!.revision, first.revision);
  assert.notEqual(projectStrideExploreBasis({...source, graph: {writeback_results: [
    {...source.graph.writeback_results[0], summary: "Different inputs and evidence."}]}}, "goal-a", "agent-a")!.revision, first.revision);
});


test("historical off-mode probes preserve normalized text and its reported budget", () => {
  const expected = {scope: "One complete experiment.", prediction: "Distinguish the remaining explanations.",
    adoption_criterion: "Retain a validated gain.", cost_bound: "One bounded comparison."};
  const historical = {schema_version: "stride_decision_v1", strategy_version: proposal.strategy_version,
    reference_context_id: proposal.reference_context_id, reference_obligation_id: proposal.reference_obligation_id,
    basis: "unexplained_gap", next_probe: Object.fromEntries(Object.entries(expected)
      .map(([key, value]) => [key, " ".repeat(4000) + value + " ".repeat(4000)]))};
  const [normalized, usage] = normalizeStrideDecision(historical);
  assert.deepEqual(normalized!.next_probe, expected);
  for (const [key, value] of Object.entries(expected)) assert.equal(usage[`stride_decision.next_probe.${key}`], value.length);
  const authored = buildVisionCheckpoint({...request, agent_vision_packet: {...packet, stride_decision: historical}});
  assert.deepEqual((authored.agent_vision as Record<string, unknown>).stride_decision, normalized);
});


test("independent v1 records preserve their values but cannot satisfy a current enabled review", () => {
  const {action, ...base} = proposal;
  const historical = {...base, schema_version: "stride_decision_v1",
    axes: {work_slice: "shrink", route_distance: "grow", validation_scope: "hold"},
    axis_reasons: {work_slice: "Old rationale.", route_distance: "Old route rationale.", validation_scope: "Old coverage rationale."}};
  assert.deepEqual(normalizeStrideDecision(historical)[0], historical);
  const obligation = {obligation_id: proposal.reference_obligation_id, triggers: [{kind: "vision_acceptance_gap"}],
    replan_context: {stride_strategy: "evidence_conditioned_v1"}};
  assert.equal(projectReplanSemantics({operation: "qualify", obligation,
    agent_vision: {...packet, stride_decision: historical}}).reason_code, "stride_decision_required");
  assert.equal(projectReplanSemantics({operation: "qualify", obligation: {...obligation,
    replan_context: {stride_strategy: "off"}}, agent_vision: {...packet, stride_decision: historical}}).accepted, true);
  assert.throws(() => normalizeStrideDecision({...historical, action: "verify"}), /unsupported/);
  const withReviews = {...proposal,
    previous_review: {reference_decision_ref: "replan-decision-" + "b".repeat(24), adoption: "executed",
      information_gain: "new_evidence", goal_gain: "flat", evidence_refs: ["evidence:executed"],
      lesson: "The completed alternative did not improve the target; retain scoped evidence."},
    explore_review: {revision: "c".repeat(64), disposition: "used", considered_refs: ["finding-route"],
      reason: "The comparison covers this assumption; do not repeat unchanged inputs."}};
  const prepared = buildVisionCheckpoint({...request, agent_vision_packet: {...packet, stride_decision: withReviews}});
  const budget = (prepared.agent_vision as Record<string, unknown>).vision_budget as Record<string, unknown>;
  assert.ok(Number(budget.total_usage) < 1800);
});

// The shipped syntax example must be authorable; off does not gain this prompt.
test("ordinary-work guidance and its example retain off isolation and bounded authoring", () => {
  assert.deepEqual(stridePlanningGuidance("off"), []);
  const guidance = stridePlanningGuidance("evidence_conditioned_v1").join(" ");
  assert.match(guidance, /coherent accepted deliverable/);
  assert.match(guidance, /Do not bundle independent commitments/);
  const contract = strideControlContract({stride_strategy: "evidence_conditioned_v1", context_id: contextId}, proposal.reference_obligation_id)!;
  const example = contract.minimal_example as Record<string, unknown>;
  assert.equal(normalizeStrideDecision(example)[0]!.action, "verify");
  const prepared = buildVisionCheckpoint({...request, agent_vision_packet: {...packet, stride_decision: example}});
  assert.ok(Number(((prepared.agent_vision as Record<string, unknown>).vision_budget as Record<string, unknown>).total_usage) < 1800);
});

test("all decision codecs reject non-string reference identities without coercion", () => {
  const {minimum_evidence: _, ...probe} = proposal.next_probe;
  const fixed = {...proposal, schema_version: "stride_decision_v1", next_probe: probe};
  delete (fixed as Record<string, unknown>).action;
  delete (fixed as Record<string, unknown>).axes;
  const independent = {...proposal, schema_version: "stride_decision_v1",
    axes: {work_slice: "grow", route_distance: "hold", validation_scope: "grow"},
    axis_reasons: {work_slice: "Complete work.", route_distance: "Keep method.", validation_scope: "Check outcomes."}};
  delete (independent as Record<string, unknown>).action;
  for (const base of [proposal, fixed, independent]) {
    for (const field of ["reference_context_id", "reference_obligation_id"] as const) {
      for (const malformed of [[base[field]], {value: base[field]}]) {
        assert.throws(() => normalizeStrideDecision({...base, [field]: malformed}), /string/);
      }
    }
  }
  for (const axes of [null, false, {work_slice: "shrink", route_distance: "grow", validation_scope: "shrink", extra: "hold"}]) {
    assert.throws(() => normalizeStrideDecision({...fixed, axes}));
  }
  const previous = {reference_decision_ref: "replan-decision-" + "c".repeat(24), adoption: "unknown",
    information_gain: "unknown", goal_gain: "unknown", evidence_refs: [], lesson: "Unknown."};
  const explore = {revision: "d".repeat(64), disposition: "deferred", considered_refs: [], reason: "Scoped later."};
  for (const wrap of [(v: string) => [v], (v: string) => ({value: v})]) {
    assert.throws(() => normalizeStrideDecision({...proposal, previous_review: {...previous, reference_decision_ref: wrap(previous.reference_decision_ref)}}), /string/);
    assert.throws(() => normalizeStrideDecision({...proposal, explore_review: {...explore, revision: wrap(explore.revision)}}), /string/);
  }
});
