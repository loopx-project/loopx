import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";
import {projectReplanContext} from "../../loopx/control_plane/work_items/replan_context.ts";
import {normalizeStrideDecision, strideControlContract, strideReviewMismatch} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";

const goal = "route-comparison", agent = "researcher", obligationId = "replan-" + "a".repeat(16);
const findings = [
  {finding_id: "finding-local", node_id: "local-method", finding: "The local method misses the interaction.",
    status: "refuted", summary: "The measured result applies only to input-v1 and the tested local search.",
    evidence_refs: ["measurement:local-v1"], tags: ["writeback-result"]},
  {finding_id: "finding-joint", node_id: "joint-method", finding: "The joint method resolves the tested interaction.",
    status: "confirmed", summary: "The measured result applies only to input-v1; independent validation remains.",
    evidence_refs: ["measurement:joint-v1"], tags: ["writeback-result"]},
];
const branches = findings.map((finding, i) => ({todo_id: `todo_method_${i}`, text: `Validate mechanism ${i}.`,
  status: "open", actionable_open: true, selection_status: "selected", score: 100 - i,
  typed_evidence_audit: {mode: "diagnostic_only", score_delta: 0,
    requested_node_refs: [finding.node_id], unknown_node_refs: [],
    nodes: [{node_id: finding.node_id, status: "exploring"}], findings: [finding], relevant_edges: []}}));
const input: JsonObject = {goal_id: goal, agent_id: agent, route: ["loopx"], graph_enabled: true,
  harness_gate: {enabled: true}, plan: {selected_branches: branches},
  projection: {nodes: findings.map(row => ({node_id: row.node_id, status: "exploring"})), findings},
  include_finding_digests: true};

function context(source = projectExploreTurnContext(input)): JsonObject {
  return projectReplanContext({operation: "project", goal_id: goal, agent_id: agent, rows: [], goal_facts: {},
    obligation: {obligation_id: obligationId}, stride_strategy: "evidence_conditioned_v1", explore_context: source});
}
function selected(ctx: JsonObject): JsonObject {
  const cards = (ctx.explore_basis as JsonObject).comparison_candidates as JsonObject[];
  const card = cards.find(row => row.todo_id === "todo_method_1")!;
  return {todo_id: card.todo_id, ...(card.outcomes as JsonObject[])[0]};
}
function decision(ctx: JsonObject): JsonObject {
  const choice = selected(ctx);
  return {...strideControlContract(ctx, obligationId)!.minimal_example as JsonObject,
    explore_review: {revision: (ctx.explore_basis as JsonObject).revision, disposition: "used",
      considered_refs: [choice.finding_id], reason: "Compare the measured mechanism under unchanged acceptance.",
      selected_candidate: choice}};
}
function qualify(ctx: JsonObject, proposal: JsonObject, todoDelta = ["retain:todo_method_1"]): JsonObject {
  return projectReplanSemantics({operation: "qualify", obligation: {obligation_id: obligationId,
    triggers: [{kind: "periodic_review_due"}], replan_context: ctx}, agent_vision: {
      stride_decision: proposal, todo_delta: todoDelta, vision_patch: {acceptance_summary: "Original acceptance remains."},
      path_delta: {outcome: "replan", evidence_refs: ["measurement:joint-v1"]}},
    observation_delta: {delta_kinds: ["new_runnable_successor"]}});
}

test("admitted stride context retains executable candidate links to exact scoped outcomes", () => {
  const source = projectExploreTurnContext(input), ctx = context(source);
  const cards = (ctx.explore_basis as JsonObject).comparison_candidates as JsonObject[];
  assert.equal(cards?.length, 2, "The executable comparison relation must survive the replan projection.");
  assert.deepEqual(cards.map(row => row.todo_id), ["todo_method_0", "todo_method_1"]);
  assert.equal((cards[0].outcomes as JsonObject[])[0].node_id, "local-method");
  assert.equal((cards[1].outcomes as JsonObject[])[0].node_id, "joint-method");
  assert.match(String(selected(ctx).source_sha256), /^[0-9a-f]{64}$/);
  assert.equal((source.harness as JsonObject).selected_branches instanceof Array, true);
  assert.deepEqual(((source.harness as JsonObject).selected_branches as JsonObject[]).map(row => row.score), [100, 99]);
  assert.equal(qualify(ctx, decision(ctx)).accepted, true);
});

test("candidate selection binds current Todo linkage, source content and retained planned work", () => {
  const ctx = context(), proposal = decision(ctx), choice = selected(ctx);
  for (const patch of [{todo_id: "todo_other"}, {node_id: "local-method"},
    {finding_id: "finding-local"}, {source_sha256: "b".repeat(64)}]) {
    const changed = {...proposal, explore_review: {...proposal.explore_review as JsonObject,
      considered_refs: [patch.finding_id ?? choice.finding_id],
      selected_candidate: {...choice, ...patch}}};
    assert.equal(qualify(ctx, changed).accepted, false, JSON.stringify(patch));
  }
  for (const todoDelta of [[], ["retain:todo_other"], ["The chosen method will run."]]) {
    assert.equal(qualify(ctx, proposal, todoDelta).accepted, false);
  }
  const changedSource = projectExploreTurnContext({...input, projection: {...input.projection as JsonObject,
    findings: findings.map(row => row.finding_id === choice.finding_id ? {...row,
      summary: "New input-v2 contradicts the prior applicability."} : row)}});
  assert.equal(qualify(context(changedSource), proposal).accepted, false);
  const unlinked = projectExploreTurnContext({...input, plan: {selected_branches: branches.map(row => ({...row,
    typed_evidence_audit: {...row.typed_evidence_audit, requested_node_refs: ["unknown"], unknown_node_refs: ["unknown"]}}))}});
  assert.equal(qualify(context(unlinked), proposal).accepted, false);
  for (const patch of [{actionable_open: false}, {claimed_by: "other-agent"}]) {
    const closed = projectExploreTurnContext({...input, plan: {selected_branches: branches.map(row => ({...row, ...patch}))}});
    assert.equal((closed.harness as JsonObject).comparison_candidates, undefined);
    assert.equal(qualify(context(closed), proposal).accepted, false);
  }
});

for (const kind of ["periodic_review_due", "typed_progress_repeat", "no_progress_streak",
  "repeated_action_loop", "external_progress_review_drift"]) {
  test(`${kind} combines current comparison evidence with its required decision`, () => {
    const ctx = context(), proposal = decision(ctx), choice = selected(ctx);
    const obligation = {obligation_id: obligationId, triggers: [{kind}], replan_context: ctx};
    const vision = {stride_decision: proposal, todo_delta: ["retain:todo_method_1"],
      vision_patch: {acceptance_summary: "Original acceptance remains."},
      path_delta: {outcome: "continue", evidence_refs: ["measurement:joint-v1"]}};
    const request = {operation: "qualify", obligation, agent_vision: vision};
    const accepted = projectReplanSemantics(request);
    assert.deepEqual(accepted.satisfying_outcomes, ["fresh_vision_path_outcome"],
      "Current evidence may justify finishing useful work without inventing a successor.");
    const successor = {delta_kinds: ["new_runnable_successor"]};
    const wrongSource = {...proposal, explore_review: {...proposal.explore_review as JsonObject,
      selected_candidate: {...choice, source_sha256: "b".repeat(64)}}};
    for (const change of [
      {stride_decision: wrongSource},
      {stride_decision: {...proposal, reference_context_id: "replan-context-" + "b".repeat(24)}},
      {stride_decision: {...proposal, reference_obligation_id: "replan-" + "b".repeat(16)}},
      {todo_delta: ["retain:todo_other"]},
      {stride_decision: undefined},
    ]) {
      assert.equal(projectReplanSemantics({...request, observation_delta: successor,
        agent_vision: {...vision, ...change}}).accepted, false,
        "A typed successor cannot bypass either the current decision or its explicit source binding.");
    }
    assert.equal(projectReplanSemantics({operation: "transition_eligibility", obligation}).allowed, false);
    assert.equal(projectReplanSemantics({...request, obligation: {...obligation,
      satisfying_semantic_outcomes: ["new_runnable_successor"]}}).accepted, false,
      "An explicit source restriction still cannot be satisfied by a fresh path alone.");
    const off = {...obligation, replan_context: {stride_strategy: "off"}};
    assert.equal(projectReplanSemantics({operation: "qualify", obligation: off,
      observation_delta: successor}).accepted, true);
    assert.equal(projectReplanSemantics({operation: "transition_eligibility", obligation: off}).allowed, true);
  });
}

test("explicit selection remains optional and cannot be turned into authority or a forced pivot", () => {
  const ctx = context(), proposal = decision(ctx);
  const ordinary = {...proposal, explore_review: {...proposal.explore_review as JsonObject}};
  delete (ordinary.explore_review as JsonObject).selected_candidate;
  assert.equal(strideReviewMismatch(normalizeStrideDecision(ordinary)[0]!, ctx), null);
  assert.equal(qualify(ctx, ordinary, []).accepted, true);
  const incumbent = (ctx.explore_basis as JsonObject).comparison_candidates as JsonObject[];
  const keep = {todo_id: incumbent[0].todo_id, ...(incumbent[0].outcomes as JsonObject[])[0]};
  assert.equal(qualify(ctx, {...proposal, action: "verify", explore_review: {
    ...proposal.explore_review as JsonObject, considered_refs: [keep.finding_id], selected_candidate: keep}},
    ["retain:todo_method_0"]).accepted, true, "Scoped negative evidence does not ban a new discriminating probe.");
  const off = context(projectExploreTurnContext({...input, graph_enabled: false, harness_gate: {enabled: false}, plan: {}}));
  assert.equal(off.explore_basis, undefined);
  assert.equal(qualify(off, proposal).accepted, false);
  const disabledStrategy = projectReplanContext({operation: "project", goal_id: goal, agent_id: agent, rows: [],
    goal_facts: {}, obligation: {obligation_id: obligationId}, stride_strategy: "off",
    explore_context: projectExploreTurnContext(input)});
  assert.equal(disabledStrategy.explore_basis, undefined);
  assert.equal(qualify(disabledStrategy, proposal).accepted, false);
  const nonperiodic = {operation: "qualify", obligation: {obligation_id: obligationId,
    triggers: [{kind: "vision_acceptance_gap"}], replan_context: ctx},
    agent_vision: {stride_decision: proposal, todo_delta: ["retain:todo_other"]},
    observation_delta: {delta_kinds: ["new_runnable_successor"]}};
  assert.equal(projectReplanSemantics(nonperiodic).accepted, false,
    "An explicit invalid binding cannot settle through an unrelated typed successor outcome.");
});

test("selection declarations reject malformed identities, unsupported authority and uncited results", () => {
  const ctx = context(), proposal = decision(ctx), choice = selected(ctx);
  for (const patch of [{todo_id: ""}, {source_sha256: "A".repeat(64)}, {source_sha256: "sha256:" + "a".repeat(64)},
    {node_id: "/private/path"}, {execute: true}]) {
    assert.throws(() => normalizeStrideDecision({...proposal, explore_review: {
      ...proposal.explore_review as JsonObject, selected_candidate: {...choice, ...patch}}}));
  }
  for (const patch of [{disposition: "deferred"}, {considered_refs: []}, {considered_refs: ["finding-local"]}]) {
    assert.throws(() => normalizeStrideDecision({...proposal, explore_review: {...proposal.explore_review as JsonObject, ...patch}}));
  }
});

test("source-replayed result pages reach a lower-ranked method without changing planner permission", () => {
  const measured = Array.from({length: 4}, (_, index) => ({...findings[index === 3 ? 1 : 0],
    finding_id: `finding-ranked-${index}`, node_id: `mechanism-${index}`}));
  const pool = measured.map((finding, index) => ({...branches[0], todo_id: `todo_ranked_${index}`,
    score: 100 - 20 * index, selection_status: index < 3 ? "selected" : "verification_budget_exhausted",
    typed_evidence_audit: {...branches[0].typed_evidence_audit, requested_node_refs: [finding.node_id],
      nodes: [{node_id: finding.node_id, status: "exploring"}], findings: [finding]}}));
  const full: JsonObject = {...input, plan: {selected_branches: pool.slice(0, 3), rejected_candidates: pool.slice(3)},
    projection: {nodes: measured.map(row => ({node_id: row.node_id, status: "exploring"})), findings: measured}};
  const first = projectExploreTurnContext(full), original = context(first);
  const page = {revision: ((first.graph as JsonObject).result_page as JsonObject).revision, offset: 3, limit: 3};
  const read = projectExploreTurnContext({...full, result_offset: 3, result_revision: page.revision});
  const card = ((read.harness as JsonObject).comparison_candidates as JsonObject[])[0];
  const choice = {todo_id: card.todo_id, ...(card.outcomes as JsonObject[])[0]};
  assert.equal(choice.todo_id, "todo_ranked_3");
  const proposal = {...strideControlContract(original, obligationId)!.minimal_example as JsonObject,
    explore_review: {revision: (original.explore_basis as JsonObject).revision, disposition: "used",
      considered_refs: [choice.finding_id], reason: "Compare the measured alternative under the original budget.",
      selected_candidate: choice, result_pages: [page]}};
  assert.equal(qualify(original, proposal, ["retain:todo_ranked_3"]).accepted, false);
  const replayed = context(projectExploreTurnContext({...full, review_pages: [page]}));
  assert.equal(replayed.context_id, original.context_id);
  assert.equal(qualify(replayed, proposal, ["retain:todo_ranked_3"]).accepted, true);
  assert.deepEqual(((first.harness as JsonObject).selected_branches as JsonObject[]).map(row => row.todo_id),
    ["todo_ranked_0", "todo_ranked_1", "todo_ranked_2"]);
  assert.equal(((read.harness as JsonObject).rejected_candidates as JsonObject[])[0].selection_status,
    "verification_budget_exhausted", "Evidence access does not rewrite planner execution permission.");
  const unlinked = {...full, plan: {...full.plan as JsonObject, rejected_candidates: [
    {...pool[3], typed_evidence_audit: {...pool[3].typed_evidence_audit, requested_node_refs: []}}]}};
  assert.equal(qualify(context(projectExploreTurnContext({...unlinked, review_pages: [page]})),
    proposal, ["retain:todo_ranked_3"]).accepted, false, "A page cannot revive a removed current Todo link.");
});

test("multiple Todos sharing one finding disclose omission and use the existing expanded page", () => {
  const pool = Array.from({length: 21}, (_, index) => ({...branches[0], todo_id: `todo_shared_${index}`,
    score: 100 - index, selection_status: index < 3 ? "selected" : "verification_budget_exhausted"}));
  const full: JsonObject = {...input, plan: {selected_branches: pool.slice(0, 3), rejected_candidates: pool.slice(3)},
    projection: {nodes: [{node_id: findings[0].node_id, status: "exploring"}], findings: [findings[0]]}};
  const first = projectExploreTurnContext(full), original = context(first);
  assert.equal((first.harness as JsonObject).omitted_comparison_candidates, 18);
  const read = projectExploreTurnContext({...full, result_limit: 20});
  const cards = (read.harness as JsonObject).comparison_candidates as JsonObject[];
  assert.equal(cards.length, 20);
  assert.equal((read.harness as JsonObject).omitted_comparison_candidates, 1);
  const card = cards[3], choice = {todo_id: card.todo_id, ...(card.outcomes as JsonObject[])[0]};
  const page = {revision: ((first.graph as JsonObject).result_page as JsonObject).revision, offset: 0, limit: 20};
  const proposal = {...strideControlContract(original, obligationId)!.minimal_example as JsonObject,
    explore_review: {revision: (original.explore_basis as JsonObject).revision, disposition: "used",
      considered_refs: [choice.finding_id], reason: "Check a different current work item against the same scoped result.",
      selected_candidate: choice, result_pages: [page]}};
  const replayed = context(projectExploreTurnContext({...full, review_pages: [page]}));
  assert.equal(qualify(replayed, proposal, ["retain:todo_shared_3"]).accepted, true);
  const omitted = {...proposal, explore_review: {...proposal.explore_review as JsonObject,
    selected_candidate: {...choice, todo_id: "todo_shared_20"}}};
  assert.equal(qualify(replayed, omitted, ["retain:todo_shared_20"]).accepted, false,
    "A full planner row alone is not a delivered comparison source binding.");
});
