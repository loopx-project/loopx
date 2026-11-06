import assert from "node:assert/strict";
import test from "node:test";
import {normalizeWorkYardstickDecision, normalizeWorkYardstickCapture,
  validateWorkYardstickContinuation, projectWorkYardstickCue} from "../../loopx/control_plane/work_items/yardstick_decision.ts";
import {normalizeExploreResultAttachment} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";

const authoritative = {metric: "quality", unit: "points", higher_is_better: true,
  comparison_scope: "validation:fixed", input_revision: "inputs-v1", evaluation_ref: "evaluator:outcome-v1"};
const proxy = {...authoritative, metric: "screening", evaluation_ref: "evaluator:local-v1"};
const decision = {schema_version: "work_yardstick_decision_v0", target: {
  summary: "Improve the best accepted outcome", scope: "Same task and resource allowance", acceptance_ref: "acceptance:task-v1"},
  authoritative, proxy, counterevidence_refs: ["result:reversal"], prior_finding_id: null,
  next_action: {method_id: "method:direct", reason: "Use a bounded direct check after the proxy reversed the ordering", cost_bound: "One available evaluation; preserve required correctness"}};
const binding = {goal_id: "research", agent_id: "worker", todo_id: "todo_search", turn_instance_id: "turn-one"};
const finding = {finding_id: "finding-one", node_id: "question", evidence_refs: ["result:reversal"],
  last_updated_at: "2026-01-01T00:00:01Z", recorded_order: 1,
  work_yardstick: {decision, binding}};

test("ordinary decision is bounded, source-linked and distinct from replan v2", () => {
  const output = normalizeWorkYardstickDecision(decision, finding.evidence_refs);
  assert.deepEqual(output, decision);
  assert.equal(Object.hasOwn(output, "replan_obligation_id"), false);
  assert.throws(() => normalizeWorkYardstickDecision({...decision, replan_obligation_id: "fake"}), /unsupported/);
  assert.throws(() => normalizeWorkYardstickDecision(decision, ["result:unrelated"]), /counterevidence/);
  assert.throws(() => normalizeWorkYardstickDecision({...decision, proxy: authoritative}), /distinct/);
  assert.throws(() => normalizeWorkYardstickDecision({...decision, target: {...decision.target, acceptance_ref: "file:/private/source"}}), /opaque/);
  assert.throws(() => normalizeWorkYardstickCapture({capture: finding.work_yardstick, goal_id: "foreign"}), /cross Goal/);
});

test("changed decisions require their actual same-Todo predecessor, not the most recent other task", () => {
  assert.deepEqual(validateWorkYardstickContinuation({enabled: true, decision, binding, findings: []}), {decision});
  assert.throws(() => validateWorkYardstickContinuation({enabled: true, decision, binding, findings: [finding]}), /basis changed/);
  const next = {...decision, prior_finding_id: "finding-one"};
  assert.deepEqual(validateWorkYardstickContinuation({enabled: true, decision: next, binding, findings: [finding]}), {decision: next});
  assert.throws(() => validateWorkYardstickContinuation({enabled: false, decision, binding, findings: []}), /explicit/);
  assert.equal(projectWorkYardstickCue({...binding, enabled: false, findings: [finding]}), null);
  assert.equal(projectWorkYardstickCue({...binding, todo_id: "todo_other", enabled: true, findings: [finding]}), null);
});

test("a late replay cannot replace a newer committed choice; exact profiles select feedback", () => {
  const old = {...finding, finding_id: "late-old", recorded_order: 99,
    last_updated_at: "2026-01-01T00:00:00Z"};
  const feedback = [{...authoritative, node_id: "question", improvement: 2}, {...proxy, node_id: "question", improvement: -1},
    {...proxy, node_id: "question", input_revision: "different-inputs", improvement: 10},
    {...authoritative, node_id: "other-question", improvement: 100}];
  const cue = projectWorkYardstickCue({...binding, enabled: true, findings: [old, finding], measurement_feedback: feedback})!;
  assert.equal(cue.source_finding_id, "finding-one");
  assert.deepEqual(cue.measurement_feedback, feedback.slice(0, 2));
  assert.match(String(cue.guidance), /cannot veto/);
  assert.match(String(cue.guidance), /correctness, authorization and budget/);
  assert.equal(Object.hasOwn(cue, "required"), false);
  assert.equal(Object.hasOwn(cue, "fidelity_validated"), false);
});

test("one optional attachment reaches an ordinary task context without creating a planner or off-mode cue", () => {
  const result = normalizeExploreResultAttachment({attachment: {
    schema_version: "explore_result_attachment_v0", question: "Does the screen rank useful candidates?",
    applicability: "Same task only", input_revision: "inputs-v1", observation: "The paired result reversed the screen's ranking",
    interpretation: "Reassess screening before repeating it", status: "confirmed", evidence_refs: finding.evidence_refs,
    work_yardstick_decision: decision,
  }});
  assert.deepEqual(result.work_yardstick_decision, decision);
  const todo = {todo_id: binding.todo_id, role: "agent", claimed_by: "worker", status: "open", explore_result_node_refs: ["question"]};
  const params = {goal_id: "research", agent_id: "worker", graph_enabled: true, harness_gate: {enabled: false}, route: ["loopx"],
    plan: {}, projection: {nodes: [], findings: [finding]}, todo_id: binding.todo_id, focus_todo: todo};
  const off = projectExploreTurnContext(params);
  assert.equal(Object.hasOwn(off.graph, "work_yardsticks"), false);
  const on = projectExploreTurnContext({...params, yardstick_enabled: true, work_todos: [todo]});
  assert.equal(on.graph.work_yardsticks[0].source_finding_id, finding.finding_id);
  assert.equal(on.graph.work_yardsticks[0].cue, decision.next_action.reason);
  assert.equal(Object.hasOwn(on.graph.work_yardsticks[0], "authoritative"), false);
  assert.deepEqual(on.graph.work_yardsticks[0].counterevidence_refs, finding.evidence_refs);
  const detail = projectExploreTurnContext({...params, yardstick_enabled: true, work_todos: [todo], result_node: "question"});
  assert.deepEqual(detail.graph.work_yardsticks[0].authoritative, authoritative);
  assert.deepEqual(on.harness, off.harness);
  assert.equal(on.graph.work_yardstick_capture.optional, true);
  // Unknown or unowned work cannot adopt a shared question's decision.
  assert.equal(Object.hasOwn(projectExploreTurnContext({...params, yardstick_enabled: true,
    work_todos: [{...todo, claimed_by: "peer"}]}).graph, "work_yardsticks"), false);
});


test("history readback preserves scoped declarations and rejects foreign source bindings", async () => {
  const {parseExploreHistoryPage} = await import("../../loopx/control_plane/presentation/explore_history.ts");
  const row = {schema_version: "loopx_explore_result_event_v0", event_id: "capture-event", result_id: "capture-finding",
    goal_id: "research", agent_id: "worker", event_kind: "finding", recorded_at: "2026-01-01T00:00:00Z", title: "Recorded decision",
    evidence_refs: finding.evidence_refs, work_yardstick: finding.work_yardstick};
  const page = {ok: true, goal_id: "research", total: 1, next_cursor: null, items: [row]};
  assert.deepEqual(parseExploreHistoryPage(page, "research").items[0]!.work_yardstick, finding.work_yardstick);
  assert.throws(() => parseExploreHistoryPage({...page, items: [{...row, agent_id: "different"}]}, "research"), /cross Goal or Agent/);
  assert.throws(() => parseExploreHistoryPage({...page, items: [{...row, evidence_refs: []}]}, "research"), /counterevidence/);
  assert.deepEqual(normalizeWorkYardstickDecision(decision, [...finding.evidence_refs, ...Array.from({length: 12}, (_, i) => `result:extra-${i}`)]), decision,
    "The bounded counterevidence list does not shrink Explore's finding evidence allowance");
});
