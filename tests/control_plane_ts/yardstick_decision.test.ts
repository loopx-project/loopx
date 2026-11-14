import assert from "node:assert/strict";
import test from "node:test";
import {normalizeWorkYardstickDecision, normalizeWorkYardstickCapture, projectWorkYardstickPresentation,
  projectWorkYardstickCue} from "../../loopx/control_plane/work_items/yardstick_decision.ts";
import {validateWorkYardstickContinuation} from "../../loopx/control_plane/work_items/yardstick_source.ts";
import {normalizeExploreResultAttachment} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";
import {projectExploreMeasurementFeedback} from "../../loopx/control_plane/capabilities/explore_measurements.ts";

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

test("ordinary execution follows its current or preceding decision without a repeated declaration", () => {
  const observation = {schema_version: "explore_execution_observation_v0", candidate_sha256: "a".repeat(64),
    input_revision: "inputs-v1", planned_method_id: "method:direct", executed_method_id: "method:direct",
    change_reason: null, elapsed_seconds: null, limit_seconds: null, search_iterations: 1,
    stop_reason: "verified", evidence_refs: ["result:direct"]};
  const result = {finding_id: "executed", node_id: "question", status: "confirmed", agent_id: "worker",
    last_updated_at: "2026-01-01T00:00:02Z", recorded_order: 2,
    work_binding: {...binding, turn_instance_id: "turn-two"}, execution_observation: observation};
  const project = (rows: unknown[]) => projectWorkYardstickCue({...binding, enabled: true, findings: rows})!;
  const before = project([finding, result]);
  const expected = {source_finding_id: "finding-one", result_finding_id: "executed", proposed_method_id: "method:direct",
    executed_method_id: "method:direct", change_reason: null, candidate_sha256: "a".repeat(64),
    evidence_refs: ["result:direct"], recorded_planned_method_matches: true};
  assert.deepEqual(before.previous_decision_followthrough, expected);
  assert.deepEqual(before.next_action, decision.next_action);
  const next = {...finding, finding_id: "next-choice", recorded_order: 3, last_updated_at: "2026-01-01T00:00:03Z",
    work_yardstick: {binding: {...binding, turn_instance_id: "turn-three"},
      decision: {...decision, prior_finding_id: "finding-one", next_action: {...decision.next_action, method_id: "method:confirm"}}}};
  assert.deepEqual(project([finding, result, next]).previous_decision_followthrough, expected);
  const confirmed = {...result, finding_id: "confirmed", recorded_order: 4, last_updated_at: "2026-01-01T00:00:04Z",
    execution_observation: {...observation, planned_method_id: "method:confirm", executed_method_id: "method:confirm"}};
  const after = project([finding, result, next, confirmed]);
  assert.deepEqual(after.previous_decision_followthrough, {...expected, source_finding_id: "next-choice",
    result_finding_id: "confirmed", proposed_method_id: "method:confirm", executed_method_id: "method:confirm"});
});

test("method feedback excludes foreign, unconfirmed, stale and late-visible execution records", () => {
  const result = {finding_id: "executed", node_id: "question", status: "confirmed", agent_id: "worker",
    last_updated_at: "2026-01-01T00:00:02Z", recorded_order: 2, work_binding: binding,
    execution_observation: {candidate_sha256: "a".repeat(64), input_revision: "inputs-v1",
      planned_method_id: "method:direct", executed_method_id: "method:alternative", change_reason: "The check refuted the original route",
      evidence_refs: ["result:alternative"]}};
  const project = (rows: unknown[]) => projectWorkYardstickCue({...binding, enabled: true, findings: rows})!;
  const expected = project([finding, result]).previous_decision_followthrough;
  assert.ok(expected);
  for (const invalid of [
    {work_binding: {...binding, goal_id: "foreign"}}, {work_binding: {...binding, agent_id: "foreign"}},
    {work_binding: {...binding, todo_id: "todo_foreign"}}, {node_id: "other-question"}, {status: "tentative"},
    {last_updated_at: "2026-01-01T00:00:00Z"}, {last_updated_at: ""},
    {execution_observation: {...result.execution_observation, input_revision: "other-inputs"}},
  ]) {
    const row = {...result, finding_id: "unrelated", recorded_order: 9, last_updated_at: "2026-01-01T00:00:09Z", ...invalid};
    assert.deepEqual(project([finding, result, row]).previous_decision_followthrough, expected);
  }
  const next = {...finding, finding_id: "next-choice", recorded_order: 3, last_updated_at: "2026-01-01T00:00:03Z",
    work_yardstick: {binding, decision: {...decision, prior_finding_id: "finding-one"}}};
  const delayed = {...result, finding_id: "late-visible", recorded_order: 9, last_updated_at: "2026-01-01T00:00:02.5Z"};
  assert.deepEqual(project([finding, result, next, delayed]).previous_decision_followthrough, expected);
  const captured = {...next, recorded_order: 5, work_yardstick: {...next.work_yardstick,
    source_snapshot: {event_count: 3, event_ids_sha256: "b".repeat(64)}}};
  const afterRead = {...delayed, recorded_order: 4};
  assert.deepEqual(project([finding, result, captured, afterRead]).previous_decision_followthrough, expected);
  assert.equal(projectWorkYardstickCue({...binding, enabled: false, findings: [finding, result]}), null);
});

test("goal and proxy join the same two candidates regardless of declared order and metric direction", () => {
  const timeProxy = {...proxy, higher_is_better: false, unit: "seconds"};
  const measured = {...finding, work_yardstick: {binding, decision: {...decision, proxy: timeProxy}}, measurements: [
    {...authoritative, value: 0, candidate_ref: "candidate:a", evidence_ref: "result:goal-a"},
    {...authoritative, value: 3, candidate_ref: "candidate:b", evidence_ref: "result:goal-b"},
    {...timeProxy, value: 4, candidate_ref: "candidate:b", evidence_ref: "result:screen-b"},
    {...timeProxy, value: 2, candidate_ref: "candidate:a", evidence_ref: "result:screen-a"},
  ]};
  const project = (row = measured) => projectWorkYardstickCue({...binding, enabled: true, findings: [row],
    measurement_feedback: projectExploreMeasurementFeedback([row])})!.search_comparison;
  const result = project();
  assert.equal(result.goal.preferred_candidate_ref, "candidate:b");
  assert.equal(result.goal.previous_best.value, 0);
  assert.equal(result.proxy.preferred_candidate_ref, "candidate:a");
  assert.equal(result.proxy.latest.evidence_ref, "result:screen-a");
  assert.equal(result.proxy_order_reversed, true);
  assert.match(result.guidance, /comparison base when available/);
  assert.match(result.guidance, /continuing, a different mechanism or a compatible combination/);

  const observations = (changeIndex: number, changed: object) => ({...measured,
    measurements: measured.measurements.map((row, index) => index === changeIndex ? {...row, ...changed} : row)});
  assert.equal(project(observations(1, {value: null})), undefined);
  assert.equal(project(observations(1, {evaluation_ref: "evaluator:other"})), undefined);
  assert.equal(project(observations(2, {candidate_ref: "candidate:unrelated"})).proxy, undefined);
  assert.equal(project(observations(2, {comparison_scope: "validation:other"})).proxy, undefined);
  assert.equal(project(observations(2, {input_revision: "other-inputs"})).proxy, undefined);

  const cap = project({...measured, measurements: measured.measurements.map((row, index) =>
    index < 2 ? {...row, value: 10} : row)});
  assert.equal(cap.goal.preferred_candidate_ref, null);
  assert.equal(cap.proxy_order_reversed, false);
  assert.match(cap.guidance, /goal results tie under this scope/);
  const repeat = {...measured, finding_id: "later-retest", recorded_order: 2,
    measurements: [{...measured.measurements[1]!, value: 3}]};
  const sameCandidate = projectWorkYardstickCue({...binding, enabled: true, findings: [repeat, measured],
    measurement_feedback: projectExploreMeasurementFeedback([repeat, measured])})!;
  assert.equal(sameCandidate.search_comparison, undefined);

  for (const other of [{...binding, goal_id: "other"}, {...binding, agent_id: "peer"}, {...binding, todo_id: "todo_other"}]) {
    const foreign = {...repeat, work_yardstick: {...repeat.work_yardstick, binding: other},
      measurements: [{...repeat.measurements[0]!, candidate_ref: "candidate:foreign", value: 99}]};
    const shared = projectWorkYardstickCue({...binding, enabled: true, findings: [foreign, measured],
      measurement_feedback: projectExploreMeasurementFeedback([foreign, measured])})!;
    assert.equal(shared.source_finding_id, measured.finding_id);
    assert.deepEqual(shared.search_comparison, result);
  }

  // Same-parent sibling reversal: a rejected sibling (candidate:a) with a
  // stronger proxy score or a later proxy-only screen (candidate:d) must not
  // hide the paired proxy reversal between incumbent (candidate:b) and a
  // second sibling (candidate:c).
  const siblingSecond = {...measured, finding_id: "finding-sibling-c", recorded_order: 2,
    work_yardstick: {binding, decision: {...decision, proxy: timeProxy, prior_finding_id: measured.finding_id}},
    measurements: [
      {...authoritative, value: 1, candidate_ref: "candidate:c", evidence_ref: "result:goal-c"},
      {...timeProxy, value: 3, candidate_ref: "candidate:c", evidence_ref: "result:screen-c"},
    ]};
  const screenOnly = {...siblingSecond, finding_id: "finding-screen-d", recorded_order: 3,
    work_yardstick: {binding, decision: {...decision, proxy: timeProxy, prior_finding_id: siblingSecond.finding_id}},
    measurements: [
      {...timeProxy, value: 1, candidate_ref: "candidate:d", evidence_ref: "result:screen-d"},
    ]};
  for (const rows of [[siblingSecond, measured], [screenOnly, siblingSecond, measured]]) {
    const feedback = projectExploreMeasurementFeedback(rows);
    const cue = projectWorkYardstickCue({...binding, enabled: true, findings: rows, measurement_feedback: feedback})!;
    const comparison = cue.search_comparison as Record<string, any>;
    assert.equal(comparison.goal.previous_best.candidate_ref, "candidate:b");
    assert.equal(comparison.goal.latest.candidate_ref, "candidate:c");
    assert.equal(comparison.goal.preferred_candidate_ref, "candidate:b");
    assert.equal(comparison.proxy.previous_best.candidate_ref, "candidate:b");
    assert.equal(comparison.proxy.latest.candidate_ref, "candidate:c");
    assert.equal(comparison.proxy.preferred_candidate_ref, "candidate:c");
    assert.equal(comparison.proxy_order_reversed, true);
    assert.match(comparison.guidance, /proxy reverses the observed goal order/);
  }
});

test("a current decision can reuse explicitly cited measurements written before its separate outcome", () => {
  const preview = {...proxy, input_revision: "preview-inputs-v2"};
  const cited = ["result:preview-a", "result:preview-b", "result:preview-sibling"];
  const declared = {...decision, proxy: preview, counterevidence_refs: cited};
  const source = {...finding, recorded_order: 2, work_yardstick: {decision: declared, binding,
    source_snapshot: {event_count: 2, event_ids_sha256: "a".repeat(64)}},
    evidence_refs: [...cited, "result:goal-a", "result:goal-b"], measurements: [
      {...authoritative, value: 20, candidate_ref: "candidate:a", evidence_ref: "result:goal-a"},
      {...authoritative, value: 14, candidate_ref: "candidate:b", evidence_ref: "result:goal-b"},
    ]};
  const recorded = {finding_id: "recorded-preview", node_id: source.node_id, agent_id: binding.agent_id,
    recorded_order: 1, last_updated_at: "2026-01-01T00:00:00Z",
    evidence_refs: [...cited, "result:preview-uncited"], measurements: [
      {...preview, value: 6, candidate_ref: "candidate:a", evidence_ref: cited[0]!},
      {...preview, value: 8, candidate_ref: "candidate:b", evidence_ref: cited[1]!},
      {...preview, value: 98, candidate_ref: "candidate:sibling", evidence_ref: cited[2]!},
      {...preview, value: 99, candidate_ref: "candidate:uncited", evidence_ref: "result:preview-uncited"},
    ]};
  const project = (row = recorded, current = source) => projectWorkYardstickCue({...binding, enabled: true,
    findings: [current, row], cited_sources: [{source_finding_id: current.finding_id,
      source_snapshot: current.work_yardstick.source_snapshot, findings: [row]}],
    measurement_feedback: projectExploreMeasurementFeedback([current, row])})!.search_comparison;
  const comparison = project();
  assert.equal(comparison.goal.preferred_candidate_ref, "candidate:a");
  assert.equal(comparison.proxy?.preferred_candidate_ref, "candidate:b");
  assert.equal(comparison.proxy_order_reversed, true);

  assert.equal(project({...recorded, agent_id: "peer"}).proxy, undefined);
  assert.equal(project({...recorded, node_id: "different-question"}).proxy, undefined);
  const differentInputs = recorded.measurements.map(row => ({...row, input_revision: "different-preview-inputs"}));
  assert.equal(project({...recorded, measurements: differentInputs}).proxy, undefined);
  const partial = {...source, work_yardstick: {binding, decision: {...declared, counterevidence_refs: [cited[0]!]}}};
  assert.equal(project(recorded, partial).proxy, undefined);
  const differentTarget = {...recorded, work_yardstick: {binding, decision: {...declared,
    target: {...declared.target, acceptance_ref: "acceptance:another-target"}}}};
  assert.equal(project(differentTarget).proxy, undefined);

  const later = {...recorded, finding_id: "later-preview", recorded_order: 3,
    // Claimed observation time is older. Only recorded order binds the citation.
    last_updated_at: "2025-12-31T23:59:59Z", evidence_refs: [cited[1]!],
    measurements: [{...recorded.measurements[1]!, value: 1}]};
  const citedSource = {source_finding_id: source.finding_id, source_snapshot: source.work_yardstick.source_snapshot, findings: [recorded]};
  const after = projectWorkYardstickCue({...binding, enabled: true, findings: [source, later, recorded], cited_sources: [citedSource]})!;
  assert.deepEqual(after.search_comparison, comparison);
  const unknown = projectWorkYardstickCue({...binding, enabled: true,
    findings: [source, recorded], cited_sources: [{...citedSource, findings: [{...recorded, recorded_order: undefined}]}]})!;
  assert.equal(unknown.search_comparison.proxy, undefined);
  const historical = {...source, work_yardstick: {decision: declared, binding}};
  assert.equal(project(recorded, historical).proxy, undefined,
    "Historical captures without a frozen source must not acquire later citations");
  const recovered = {...source, recorded_order: 8};
  assert.deepEqual(project(recorded, recovered), comparison);
  const duringOutage = {...later, recorded_order: 3};
  assert.deepEqual(projectWorkYardstickCue({...binding, enabled: true,
    findings: [recovered, duringOutage, recorded], cited_sources: [citedSource]})!.search_comparison, comparison);
});

test("the settlement freezes and verifies the original canonical event prefix", () => {
  const events = [{goal_id: binding.goal_id, event_id: "original-a"}, {goal_id: binding.goal_id, event_id: "original-b"}];
  const params = {enabled: true, decision, binding, findings: [], source_events: events};
  const first = validateWorkYardstickContinuation(params);
  assert.equal(first.source_snapshot.event_count, 2);
  assert.equal(first.source_snapshot.event_ids_sha256,
    "9c39bca70a9d3a82756b561afab02853c5bfdc2f2a2e404d1a9c045093817a0d");
  const appended = {...params, source_events: [...events, {goal_id: binding.goal_id, event_id: "late"}], source_snapshot: first.source_snapshot};
  assert.deepEqual(validateWorkYardstickContinuation(appended), first);
  assert.throws(() => validateWorkYardstickContinuation({...appended,
    source_events: [{...events[0], event_id: "changed"}, events[1]]}), /snapshot changed/);
  assert.throws(() => validateWorkYardstickContinuation({...appended, source_events: events.slice(0, 1)}), /snapshot changed/);
  assert.throws(() => validateWorkYardstickContinuation({...params,
    source_events: [{goal_id: "another-goal", event_id: "foreign"}]}), /canonical Goal/);
  assert.deepEqual(validateWorkYardstickContinuation({...appended, source_snapshot: null}), {decision});
  assert.throws(() => normalizeWorkYardstickDecision({...decision, source_snapshot: first.source_snapshot}), /unsupported/,
    "The author cannot supply evidence provenance in an attachment");
});

test("canonical prefix hashing preserves empty and UTF-8 stored digest vectors", () => {
  const vectors = [
    {ids: [], digest: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
    {ids: ["事件-🙂", 'quoted"\\id'], digest: "7510a7f2e168cfaff2b09b4c62b3607f15e8811ee9e0f58fd51537820da8507f"},
  ];
  for (const {ids, digest} of vectors) {
    const result = validateWorkYardstickContinuation({enabled: true, decision, binding, findings: [],
      source_events: ids.map(event_id => ({goal_id: binding.goal_id, event_id}))});
    assert.deepEqual(result.source_snapshot, {event_count: ids.length, event_ids_sha256: digest});
  }
});

test("only strictly older committed replay preserves a stale predecessor as history", () => {
  const params = {enabled: true, decision, binding, findings: [finding]};
  assert.deepEqual(validateWorkYardstickContinuation({...params,
    replay_committed_at: "2026-01-01T00:00:00.999999Z"}), {decision});
  for (const time of [undefined, "invalid", finding.last_updated_at, "2026-01-01T00:00:01.000001Z"]) {
    assert.throws(() => validateWorkYardstickContinuation({...params, replay_committed_at: time}), /basis changed/);
  }
  assert.throws(() => normalizeWorkYardstickDecision({...decision,
    replay_committed_at: "2026-01-01T00:00:00Z"}), /unsupported/,
    "The attachment cannot supply replay provenance");
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
  const measured = {...finding, measurements: [
    {...authoritative, value: 10, candidate_ref: "candidate:a", evidence_ref: "result:goal-a"},
    {...authoritative, value: 20, candidate_ref: "candidate:b", evidence_ref: "result:goal-b"},
    {...proxy, value: 8, candidate_ref: "candidate:b", evidence_ref: "result:screen-b"},
    {...proxy, value: 9, candidate_ref: "candidate:a", evidence_ref: "result:screen-a"},
  ]};
  const reversedCompact = projectExploreTurnContext({...params, projection: {nodes: [], findings: [measured]},
    yardstick_enabled: true, work_todos: [todo]}).graph.work_yardsticks[0];
  assert.equal(reversedCompact.comparison_candidate_ref, "candidate:b");
  assert.equal(reversedCompact.proxy_order_reversed, true);
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
  const internal = {...finding.work_yardstick,
    source_snapshot: {event_count: 3, event_ids_sha256: "a".repeat(64)}};
  assert.deepEqual(normalizeWorkYardstickCapture({capture: internal}).source_snapshot, internal.source_snapshot);
  assert.deepEqual(projectWorkYardstickPresentation({capture: internal}), finding.work_yardstick,
    "Independently installed presentation readers retain the original v0 capture fields");
  assert.throws(() => projectWorkYardstickPresentation({capture: internal, goal_id: "foreign"}), /cross Goal/);
});


test("different proxy workloads retain their revisions and compare the same declared subjects", () => {
  const outcome = {...authoritative, input_revision: "task:full-v2"};
  const screening = {...proxy, input_revision: "dataset:small-proxy-v3"};
  const choice = {...decision, authoritative: outcome, proxy: screening};
  const measured = {...finding, work_yardstick: {binding, decision: choice}, measurements: [
    {...outcome, value: 7, candidate_ref: "archive:a", evidence_ref: "result:formal-a"},
    {...outcome, value: 4, candidate_ref: "archive:b", evidence_ref: "result:formal-b"},
    {...screening, value: 9, candidate_ref: "archive:a", evidence_ref: "result:proxy-a"},
    {...screening, value: 12, candidate_ref: "archive:b", evidence_ref: "result:proxy-b"},
  ]};
  const project = (row = measured) => projectWorkYardstickCue({...binding, enabled: true, findings: [row],
    measurement_feedback: projectExploreMeasurementFeedback([row])})!;
  const cue = project();
  assert.equal(cue.search_comparison.goal.preferred_candidate_ref, "archive:a");
  assert.equal(cue.search_comparison.proxy.preferred_candidate_ref, "archive:b");
  assert.equal(cue.search_comparison.proxy_order_reversed, true);
  assert.equal(cue.authoritative.input_revision, "task:full-v2");
  assert.equal(cue.proxy.input_revision, "dataset:small-proxy-v3");
  // Different granularity is retained as evidence and is never automatically aliased.
  const unpaired = {...measured, measurements: measured.measurements.map((row, index) =>
    index > 1 ? {...row, candidate_ref: row.candidate_ref.replace("archive:", "candidate:")} : row)};
  assert.equal(project(unpaired).search_comparison.proxy, undefined);
  assert.equal(projectExploreMeasurementFeedback([unpaired]).length, 2);
  assert.equal(projectWorkYardstickCue({...binding, enabled: false, findings: [measured]}), null);
});


test("late graph delivery cannot turn an old primary into a new decision's uncited evidence", () => {
  const snapshot = {event_count: 2, event_ids_sha256: "a".repeat(64)};
  const current = {...finding, recorded_order: 3, status: "confirmed",
    work_yardstick: {binding, decision: {...decision, counterevidence_refs: []}, source_snapshot: snapshot},
    measurements: [
      {...authoritative, value: 7, candidate_ref: "candidate:a", evidence_ref: "result:a"},
      {...authoritative, value: 4, candidate_ref: "candidate:b", evidence_ref: "result:b"},
    ]};
  const plain = {finding_id: "late-primary", node_id: "question", status: "confirmed", recorded_order: 4,
    work_binding: {...binding, turn_instance_id: "old-turn"}, last_updated_at: "2026-01-01T00:00:00.999999Z",
    evidence_refs: ["result:late"], measurements: [
      {...authoritative, value: 100, candidate_ref: "candidate:late", evidence_ref: "result:late"},
    ]};
  const project = (row = plain, source = current, cited_sources: unknown[] = []) => projectWorkYardstickCue({
    ...binding, enabled: true, findings: [source, row], cited_sources,
    measurement_feedback: projectExploreMeasurementFeedback([source, row]),
  })!.search_comparison;
  const expected = project().goal;
  assert.equal(expected.preferred_candidate_ref, "candidate:a");
  for (const timestamp of ["2026-01-01T00:00:00Z", finding.last_updated_at, "invalid", ""]) {
    assert.deepEqual(project({...plain, last_updated_at: timestamp}).goal, expected);
  }
  assert.equal(project({...plain, last_updated_at: "2026-01-01T00:00:01.000001Z"}).goal.preferred_candidate_ref,
    "candidate:late", "A genuinely later committed ordinary observation still qualifies");
  assert.deepEqual(project({...plain, recorded_order: 1,
    last_updated_at: "2026-01-01T00:00:01.000001Z"}).goal, expected,
    "Original visible-prefix eligibility is also required");
  assert.deepEqual(project(plain, {...current, last_updated_at: ""}).goal, expected);

  const historical = {...plain, recorded_order: 1};
  const explicitlyCited = {...current, evidence_refs: [...current.evidence_refs, "result:late"],
    work_yardstick: {...current.work_yardstick,
    decision: {...current.work_yardstick.decision, counterevidence_refs: ["result:late"]}}};
  const cited = project(historical, explicitlyCited, [{source_finding_id: current.finding_id,
    source_snapshot: snapshot, findings: [historical]}]);
  assert.equal(cited.goal.preferred_candidate_ref, "candidate:late",
    "An explicitly cited historical 100 remains applicable to the newer a/b comparison");
  assert.equal(cited.goal.previous_best.candidate_ref, "candidate:late",
    "Verified cited history is retained even though it predates the current decision");
  assert.equal(projectExploreMeasurementFeedback([current, plain])[0]!.latest.measurement.candidate_ref,
    "candidate:late", "The raw result and profile-wide history are retained");
});
