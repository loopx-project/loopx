import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";
import {projectReplanContext} from "../../loopx/control_plane/work_items/replan_context.ts";
import {normalizeStrideDecision, strideControlContract, strideReviewMismatch} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";
import {buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA} from "../../loopx/control_plane/goals/vision_checkpoint.ts";

const goal = "research", agent = "reviewer", obligationId = "replan-" + "a".repeat(16);
const findings = Array.from({length: 7}, (_, i) => ({finding_id: `finding-${7 - i}`, node_id: "route-a",
  finding: "Scoped peer result", status: "confirmed", agent_id: "peer", tags: ["writeback-result"],
  summary: `Applies only to synthetic input ${7 - i}.`, evidence_refs: [`evidence-${7 - i}`]}));
const input = {goal_id: goal, agent_id: agent, route: ["loopx"], graph_enabled: true,
  harness_gate: {enabled: false}, plan: {}, projection: {nodes: [], findings}, include_finding_digests: true};
const first = projectExploreTurnContext(input);
const page = {revision: ((first.graph as JsonObject).result_page as JsonObject).revision, offset: 3, limit: 3};
function context(source: JsonObject): JsonObject {
  return projectReplanContext({operation: "project", goal_id: goal, agent_id: agent, rows: [], goal_facts: {},
    obligation: {obligation_id: obligationId}, stride_strategy: "evidence_conditioned_v1", explore_context: source});
}
const original = context(first);
function decision(pages: unknown = [page]): JsonObject {
  return {...strideControlContract(original, obligationId)!.minimal_example as JsonObject,
    explore_review: {revision: (original.explore_basis as JsonObject).revision, disposition: "used",
      considered_refs: ["finding-4"], reason: "The peer observation applies to the proposed comparison.",
      result_pages: pages}};
}
function qualify(ctx: JsonObject, proposal: JsonObject): JsonObject {
  const prepared = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare",
    goal_id: goal, agent_id: agent, merge_patch: false, existing_agent_vision: null,
    require_path_delta_for_durable_change: false, agent_vision_packet: {
      schema_version: "goal_vision_replan_contract_v0", agent_id: agent,
      vision_patch: {acceptance_summary: "Compare the scoped alternatives."}, stride_decision: proposal,
      path_delta: {schema_version: "goal_path_delta_v0", outcome: "replan", prior_assumption: "Route needs comparison.",
        observed_reality: "Peer result covers the same inputs.", changed: ["Compare the alternate route."], evidence_refs: ["finding-4"]}}});
  return projectReplanSemantics({operation: "qualify", obligation: {obligation_id: obligationId,
    triggers: [{kind: "periodic_review_due"}], replan_context: ctx}, agent_vision: prepared.agent_vision});
}

test("source-replayed pagination extends references without changing admission IDs or certifying utility", () => {
  const replayed = context(projectExploreTurnContext({...input, review_pages: [page]}));
  const {explore_result_pages: pages, ...unchanged} = replayed;
  assert.deepEqual(unchanged, original);
  assert.deepEqual(pages, [{...page, finding_refs: ["finding-4", "finding-3", "finding-2"]}]);
  assert.equal(qualify(original, decision()).accepted, false, "caller-supplied page coordinates are not source proof");
  assert.equal(qualify(replayed, decision()).accepted, true);
  assert.equal(qualify(replayed, {...decision(), reference_context_id: "replan-context-" + "b".repeat(24)}).accepted, false);
  assert.equal(qualify(replayed, {...decision(), reference_obligation_id: "replan-" + "b".repeat(16)}).accepted, false);
  const normalized = normalizeStrideDecision(decision())[0]!;
  const withoutPages = {...normalized, explore_review: {...normalized.explore_review as JsonObject}};
  delete (withoutPages.explore_review as JsonObject).result_pages;
  assert.match(strideReviewMismatch(withoutPages, replayed)!, /undelivered/);
  for (const ref of ["finding-1", "foreign-finding"]) {
    assert.equal(qualify(replayed, {...decision(), explore_review: {
      ...decision().explore_review as JsonObject, considered_refs: [ref]}}).accepted, false);
  }
});

test("supplemental citations keep available evidence distinct from unreadable or disabled sources", () => {
  const proposal = normalizeStrideDecision(decision())[0]!;
  assert.match(strideReviewMismatch(proposal, {} as JsonObject)!, /scoped basis/);
  assert.equal(qualify(context({ok: false, goal_id: goal, agent_id: agent}), proposal).accepted, false);
  assert.deepEqual(projectExploreTurnContext({...input, graph_enabled: false, review_pages: [page]}).review_pages, []);
  const withoutPages = {...proposal, explore_review: {...proposal.explore_review as JsonObject}};
  delete (withoutPages.explore_review as JsonObject).result_pages;
  const off = projectReplanContext({operation: "project", goal_id: goal, agent_id: agent, rows: [],
    goal_facts: {}, obligation: null, stride_strategy: "off", explore_context: first});
  assert.equal(off.explore_basis, undefined);
  assert.equal(off.explore_result_pages, undefined);
  const [, before] = normalizeStrideDecision(withoutPages);
  const [, after] = normalizeStrideDecision(proposal);
  assert.equal(Object.values(after).reduce((a,b) => a+b, 0) - Object.values(before).reduce((a,b) => a+b, 0),
    JSON.stringify([page]).length, "pages consume the existing Vision budget");
});

test("page replay detects source drift, different consumers and node-filtered snapshots", () => {
  for (const patch of [
    {goal_id: "other-goal"}, {agent_id: "other-reader"}, {result_node: "route-a"},
    {projection: {nodes: [], findings: findings.map(row => row.finding_id === "finding-4"
      ? {...row, summary: "Different scope despite unchanged ID, title and status."} : row)}},
    {projection: {nodes: [], findings: [...findings, {...findings[0], finding_id: "older-result"}]}},
  ]) assert.deepEqual(projectExploreTurnContext({...input, ...patch, review_pages: [page]}).review_pages, []);
  const stale = context(projectExploreTurnContext({...input, review_pages: [{...page, revision: "0".repeat(64)}]}));
  assert.equal((stale.explore_basis as JsonObject).availability, "available");
  assert.equal(qualify(stale, decision([{...page, revision: "0".repeat(64)}])).accepted, false);
  assert.equal(stale.context_id, original.context_id, "a bad query does not change the delivered source");
  assert.deepEqual(projectExploreTurnContext({...input, review_pages: [page]}).review_pages,
    projectExploreTurnContext({...input, review_pages: [page], projection: {...input.projection,
      nodes: [{node_id: "unrelated", title: "Unrelated node"}]}}).review_pages);
  const expanded = {...page, offset: 0, limit: 7};
  assert.equal(qualify(context(projectExploreTurnContext({...input, review_pages: [expanded]})), decision([expanded])).accepted, true);
});

test("page descriptors reject forged membership, malformed bounds and unbounded metadata", () => {
  for (const pages of [null, [], [page, page], [page, {...page, offset: 0}, {...page, offset: 6}, {...page, offset: 9}],
    [{...page, revision: [page.revision]}], [{...page, revision: "invalid"}], [{...page, offset: -1}],
    [{...page, offset: 0.5}], [{...page, offset: Number.MAX_SAFE_INTEGER + 1}],
    [{...page, offset: "3"}], [{...page, limit: true}], [{...page, limit: 0}], [{...page, limit: 21}],
    [{...page, finding_refs: ["foreign-finding"]}], [{...page, node_id: "route-a"}]]) {
    assert.throws(() => normalizeStrideDecision(decision(pages)));
    assert.throws(() => projectExploreTurnContext({...input, review_pages: pages}));
  }
  for (const patch of [{revision: null}, {disposition: "unavailable"}, {considered_refs: []}]) {
    assert.throws(() => normalizeStrideDecision({...decision(), explore_review: {
      ...decision().explore_review as JsonObject, ...patch}}));
  }
});

test("task-scoped pages replay canonical links without replacing the original global decision basis", () => {
  const task = {todo_id: "todo_chosen", role: "agent", status: "open", claimed_by: agent,
    explore_result_node_refs: ["route-a"]};
  const focused = projectExploreTurnContext({...input, todo_id: task.todo_id, focus_todo: task});
  const scopedPage = {revision: ((focused.graph as JsonObject).result_page as JsonObject).revision,
    offset: 3, limit: 3, todo_id: task.todo_id};
  const replay = projectExploreTurnContext({...input, review_pages: [scopedPage], review_todos: [task]});
  const ctx = context(replay), proposal = decision([scopedPage]);
  assert.deepEqual(ctx.explore_basis, original.explore_basis, "admitted replan evidence retains its original identity");
  assert.equal(qualify(ctx, proposal).accepted, true);
  assert.equal(qualify(context(projectExploreTurnContext({...input, review_pages: [scopedPage]})), proposal).accepted, false);
  for (const changed of [{...task, claimed_by: "peer"}, {...task, status: "done"},
    {...task, explore_result_node_refs: ["different-question"]}]) {
    const stale = context(projectExploreTurnContext({...input, review_pages: [scopedPage], review_todos: [changed]}));
    assert.deepEqual(stale.explore_result_pages, []);
    assert.equal(qualify(stale, proposal).accepted, false);
  }
  const forged = {...scopedPage, todo_id: "todo_other"};
  assert.equal(qualify(ctx, decision([forged])).accepted, false);
  for (const todo_id of [null, 3, "foreign", "todo_../outside"]) {
    assert.throws(() => normalizeStrideDecision(decision([{...scopedPage, todo_id}])));
  }
  const normalized = normalizeStrideDecision(proposal);
  const unscoped = normalizeStrideDecision(decision());
  assert.equal(Object.values(normalized[1]).reduce((a, b) => a + b, 0)
    - Object.values(unscoped[1]).reduce((a, b) => a + b, 0),
    JSON.stringify([scopedPage]).length - JSON.stringify([page]).length);
});
