import assert from "node:assert/strict";
import test from "node:test";
import {normalizeExploreMeasurements, projectExploreMeasurementFeedback} from "../../loopx/control_plane/capabilities/explore_measurements.ts";
import {normalizeExploreResultAttachment} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";
import {parseExploreHistoryPage, projectExploreHistory} from "../../loopx/control_plane/presentation/explore_history.ts";

const metric = {metric: "quality", value: 8, unit: "points", higher_is_better: true,
  comparison_scope: "validation:budget-v1", input_revision: "dataset-v1",
  evaluation_ref: "evaluator:v1", candidate_ref: "candidate:a", evidence_ref: "result:a"};
const finding = (id: string, value: number | null, extra = {}) => ({finding_id: id, node_id: "question-a",
  measurements: [{...metric, value, candidate_ref: `candidate:${id}`, evidence_ref: `result:${id}`, ...extra}]});

test("explicit comparison context is reused while each result retains its own candidate and receipt", () => {
  const {comparison_scope, input_revision, evaluation_ref, ...observation} = metric;
  const context = {comparison_scope, input_revision, evaluation_ref};
  const before = JSON.stringify({context, observation});
  const rows = [8, 9, 6, null].map((value, index) => ({finding_id: `result-${index}`,
    node_id: "question-a", recorded_order: index,
    measurements: normalizeExploreMeasurements([{...observation, value,
      candidate_ref: `candidate:${index}`, evidence_ref: `result:${index}`}], context)}));
  const [feedback] = projectExploreMeasurementFeedback(rows);
  assert.equal(feedback.measured_count, 3);
  assert.equal(feedback.best?.candidate_ref, "candidate:1");
  assert.equal(feedback.latest.measurement.value, null);
  assert.equal(feedback.unknown_count, 1);
  assert.equal(projectExploreMeasurementFeedback(rows.slice(0, 3))[0].improvement, -3);
  assert.deepEqual(rows[0].measurements, [metricWithIdentity(0, 8)]);
  assert.equal(JSON.stringify({context, observation}), before);
  function metricWithIdentity(index: number, value: number) {
    return {...metric, value, candidate_ref: `candidate:${index}`, evidence_ref: `result:${index}`};
  }
  const changed = {...rows[3], measurements: normalizeExploreMeasurements([
    {...observation, value: 99, evidence_ref: "result:changed"},
  ], {...context, evaluation_ref: "evaluator:changed"})};
  assert.equal(projectExploreMeasurementFeedback([...rows.slice(0, 3), changed]).length, 2);
});

test("comparison context cannot silently override result fields or accept incomplete conditions", () => {
  const {comparison_scope, input_revision, evaluation_ref, ...observation} = metric;
  const context = {comparison_scope, input_revision, evaluation_ref};
  for (const key of ["comparison_scope", "input_revision", "evaluation_ref"] as const) {
    assert.throws(() => normalizeExploreMeasurements([{...observation, [key]: context[key]}], context), /override/);
    const missing = {...context}; delete (missing as Partial<typeof context>)[key];
    assert.throws(() => normalizeExploreMeasurements([observation], missing));
  }
  for (const invalid of [null, [], {...context, candidate_ref: "candidate:a"},
    {...context, unregistered: true}, {...context, comparison_scope: ""}]) {
    assert.throws(() => normalizeExploreMeasurements([observation], invalid));
  }
  assert.throws(() => normalizeExploreMeasurements([{...observation, value: "9"}], context));
  // Existing complete observations have identical normalization without opt-in.
  for (const value of [0, -3, null]) {
    assert.deepEqual(normalizeExploreMeasurements([{...metric, value}]), [{...metric, value}]);
  }
});

test("quantitative evidence keeps zero, negatives and unknown distinct and rejects fabricated shapes", () => {
  for (const value of [0, -3, null]) assert.equal(normalizeExploreMeasurements([{...metric, value}])[0].value, value);
  for (const mutation of [{value: NaN}, {value: Infinity}, {value: "8"}, {value: false},
    {higher_is_better: "yes"}, {evidence_ref: "/local/result"}, {comparison_scope: ""},
    {candidate_ref: "file:///local/candidate"}, {metric: "quality\nscore"}, {unregistered: true}]) {
    assert.throws(() => normalizeExploreMeasurements([{...metric, ...mutation}]));
  }
  assert.throws(() => normalizeExploreMeasurements([metric, metric]), /duplicated/);
  assert.throws(() => normalizeExploreMeasurements([]));
});

test("regression returns the earlier best candidate and useful signed feedback, including lower-is-better", () => {
  const input = [finding("new", 6), finding("best", 9), finding("first", 8)];
  const before = JSON.stringify(input);
  const [feedback] = projectExploreMeasurementFeedback(input);
  assert.equal(feedback.latest.measurement.value, 6);
  assert.equal(feedback.best?.candidate_ref, "candidate:best");
  assert.equal(feedback.best?.evidence_ref, "result:best");
  assert.equal(feedback.improvement, -3);
  const [lower] = projectExploreMeasurementFeedback(input.map(row => ({...row,
    measurements: row.measurements.map(item => ({...item, higher_is_better: false}))})));
  assert.equal(lower.improvement, 2);
  assert.equal(lower.best?.value, 6);
  assert.equal(JSON.stringify(input), before, "projection cannot change the evidence");
  const [missing] = projectExploreMeasurementFeedback([finding("unknown", null), ...input]);
  assert.equal(missing.improvement, null);
  assert.equal(missing.latest.measurement.value, null);
  assert.equal(missing.best?.value, 9);
  assert.equal(missing.unknown_count, 1);
});

test("a shared label is insufficient to compare metrics across inputs, evaluators, units, direction or scope", () => {
  for (const extra of [{input_revision: "dataset-v2"}, {evaluation_ref: "evaluator:proxy"},
    {unit: "percent"}, {higher_is_better: false}, {comparison_scope: "validation:larger-budget"}]) {
    const groups = projectExploreMeasurementFeedback([finding("foreign", 99, extra), finding("first", 8)]);
    assert.equal(groups.length, 2);
    assert.ok(groups.every(group => group.previous_best === null && group.improvement === null));
  }
  assert.equal(projectExploreMeasurementFeedback([{...finding("other", 99), node_id: "other-question"}, finding("first", 8)]).length, 2);
  assert.deepEqual(projectExploreMeasurementFeedback([{finding_id: "legacy", node_id: "question-a"}]), []);
});

test("ordinary result capture binds measurements to its actual input and source, leaving status explicit", () => {
  const attachment = {schema_version: "explore_result_attachment_v0", question: "Does this method improve quality?",
    applicability: "Fixed public validation sample", input_revision: metric.input_revision,
    observation: "Quality is 8 points.", interpretation: "Compare the next candidate against this result.",
    status: "tentative", evidence_refs: [metric.evidence_ref], measurements: [metric]};
  assert.equal(normalizeExploreResultAttachment({attachment}).status, "tentative");
  for (const extra of [{input_revision: "foreign-data"}, {evidence_ref: "result:foreign"}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...attachment,
      measurements: [{...metric, ...extra}]}}), /finding's input revision and evidence/);
  }
  const path = {schema_version: "explore_result_from_path_delta_v0", question: attachment.question,
    applicability: attachment.applicability, input_revision: metric.input_revision,
    status: "tentative", measurements: [metric]};
  const result = normalizeExploreResultAttachment({attachment: path, vision_packet: {path_delta: {
    prior_assumption: "Quality is not yet measured.", observed_reality: attachment.observation, outcome: "continue", retained: ["Use the same validation protocol"],
    evidence_refs: [metric.evidence_ref]}}});
  assert.deepEqual(result.measurements, [metric]);
});

test("ordinary and path writeback expand authoring context before immutable result identity", () => {
  const {comparison_scope, input_revision, evaluation_ref, ...observation} = metric;
  const context = {comparison_scope, input_revision, evaluation_ref};
  const attachment = {schema_version: "explore_result_attachment_v0", question: "Which candidate improves quality?",
    applicability: "Fixed task, evaluator and budget", input_revision, observation: "Measured 8 points.",
    interpretation: "Keep the exact candidate and receipt.", status: "tentative", evidence_refs: [metric.evidence_ref]};
  const complete = normalizeExploreResultAttachment({attachment: {...attachment, measurements: [metric]}});
  const expanded = normalizeExploreResultAttachment({attachment: {...attachment,
    measurement_context: context, measurements: [observation]}});
  assert.deepEqual(expanded, complete, "equivalent authoring must have the same canonical identity");
  assert.equal(Object.hasOwn(expanded, "measurement_context"), false);
  const path = {schema_version: "explore_result_from_path_delta_v0", question: attachment.question,
    applicability: attachment.applicability, input_revision, status: "tentative",
    measurement_context: context, measurements: [observation]};
  const request = {attachment: path, vision_packet: {path_delta: {outcome: "continue",
    prior_assumption: "Unmeasured", observed_reality: "Measured", retained: ["Fixed conditions"],
    evidence_refs: [metric.evidence_ref]}}};
  assert.deepEqual(normalizeExploreResultAttachment(request).measurements, [metric]);
  for (const invalid of [
    {...attachment, measurement_context: context},
    {...attachment, input_revision: "different-input", measurement_context: context, measurements: [observation]},
    {...attachment, evidence_refs: ["result:other"], measurement_context: context, measurements: [observation]},
  ]) assert.throws(() => normalizeExploreResultAttachment({attachment: invalid}));
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...attachment,
    measurements: [metric]}, other_attachment: {...attachment, measurements: [observation],
    measurement_context: context}}), complete, "equivalent inline/file authoring must coalesce");
});

test("next-turn budgets retain a selected route's older best and permit a scoped read of omitted nodes", () => {
  const findings = [0, 1, 2, 3].map(index => ({...finding(`foreign-${index}`, 99), node_id: `other-${index}`}));
  findings.push(finding("latest", 6), finding("best", 9), finding("initial", 8));
  const request = {goal_id: "research", agent_id: "worker", route: ["loopx"],
    graph_enabled: true, harness_gate: {enabled: true},
    projection: {findings}, plan: {selected_branches: [{typed_evidence_audit: {requested_node_refs: ["question-a"]}}]}};
  const graph = projectExploreTurnContext(request).graph as Record<string, any>;
  assert.equal(graph.measurement_feedback.length, 3);
  assert.equal(graph.omitted_measurement_feedback, 2);
  assert.equal(graph.measurement_feedback[0].best.candidate_ref, "candidate:best");
  assert.equal(graph.measurement_feedback[0].improvement, -3);
  const scoped = projectExploreTurnContext({...request, result_node: "other-3"}).graph as Record<string, any>;
  assert.equal(scoped.measurement_feedback.length, 1);
  assert.equal(scoped.measurement_feedback[0].node_id, "other-3");
});

test("history keeps corrected measurement sources while comparisons use current findings", () => {
  const row = (id: string, result: string, value: number) => ({schema_version: "loopx_explore_result_event_v0",
    goal_id: "research", event_kind: "finding", event_id: id, result_id: result, node_id: "question-a",
    title: "Measured candidate", recorded_at: "2026-01-01T00:00:00Z", status: "tentative", measurements: [{...metric, value}]});
  const rows = [row("original", "corrected", 100), row("independent", "second", 9), row("correction", "corrected", 6)];
  const page = parseExploreHistoryPage({ok: true, goal_id: "research", items: rows, total: 3, next_cursor: null}, "research");
  const history = projectExploreHistory(page.items, "research");
  const feedback = history.measurementFeedback!;
  assert.equal(history.events[0].measurements?.[0].value, 100);
  assert.equal(feedback[0].best?.value, 9, "a superseded measurement cannot remain the best");
  assert.equal(feedback[0].latest.measurement.value, 6);
  assert.equal(feedback[0].improvement, -3);
  assert.equal(feedback[0].measured_count, 2);
  assert.deepEqual(parseExploreHistoryPage({ok: true, goal_id: "research", items: [], total: 0, next_cursor: null}, "research").items, []);
});

test("history and next-turn feedback use append order across tied and backfilled source clocks", () => {
  for (const clock of ["tied", "backfilled"]) {
    const rows = [8, 9, 6].map((value, index) => ({schema_version: "loopx_explore_result_event_v0",
      goal_id: "research", event_kind: "finding", event_id: `event-${index}`, result_id: `finding-${index}`,
      node_id: "question-a", title: "Measured candidate", status: "tentative",
      recorded_at: clock === "tied" ? "2026-01-01T00:00:00Z" : `2026-01-0${3-index}T00:00:00Z`,
      measurements: [{...metric, value}]}));
    const history = projectExploreHistory(parseExploreHistoryPage({ok: true, goal_id: "research",
      items: rows, total: 3, next_cursor: null}, "research").items, "research");
    const [feedback] = history.measurementFeedback!;
    assert.equal(feedback.latest.measurement.value, 6);
    assert.equal(feedback.best?.value, 9);
    assert.equal(feedback.improvement, -3);
    const projection = {findings: rows.map((row, recorded_order) => ({...row,
      finding_id: row.result_id, recorded_order})).reverse()};
    const context = projectExploreTurnContext({goal_id: "research", agent_id: "worker", route: ["loopx"],
      graph_enabled: true, harness_gate: {enabled: false}, projection, plan: {}});
    assert.deepEqual((context.graph as any).measurement_feedback[0], feedback);
  }
  assert.throws(() => projectExploreMeasurementFeedback([
    {...finding("first", 8), recorded_order: 0}, {...finding("next", 9), recorded_order: 0},
  ]), /unambiguous/);
});

test("measurement feedback pages retain every group and bind manual findings to the continuation", () => {
  const request = {goal_id: "research", agent_id: "worker", route: ["loopx"],
    graph_enabled: true, harness_gate: {enabled: false}, projection: {findings: [{
      ...finding("multi", 8), measurements: Array.from({length: 8}, (_, index) => ({...metric, metric: `quality-${index}`})),
    }]}, plan: {}};
  const first = projectExploreTurnContext(request).graph as any;
  assert.equal((projectExploreTurnContext({...request, result_node: "question-a", result_limit: 20}).graph as any).measurement_feedback.length, 8);
  const all = [...first.measurement_feedback];
  let page = first.measurement_feedback_page;
  while (page.next_command) {
    const next = projectExploreTurnContext({...request, result_offset: page.offset + page.limit,
      result_revision: page.revision}).graph as any;
    all.push(...next.measurement_feedback);
    page = next.measurement_feedback_page;
  }
  assert.equal(all.length, 8);
  assert.equal(new Set(all.map(row => row.metric)).size, 8);
  assert.throws(() => projectExploreTurnContext({...request, projection: {findings: [
    finding("manual", 99), ...request.projection.findings,
  ]}, result_offset: 3, result_revision: first.result_page.revision}), /changed/);
  const sources = [finding("latest", 8), finding("best", 10), {...finding("older", 1), summary: "Original applicability"}];
  const snapshot = projectExploreTurnContext({...request, projection: {findings: sources}}).graph as any;
  const corrected = {...request, projection: {findings: sources.map(row => row.finding_id === "older"
    ? {...row, summary: "Corrected applicability"} : row)}};
  assert.deepEqual((projectExploreTurnContext(corrected).graph as any).measurement_feedback, snapshot.measurement_feedback,
    "unchanged aggregates are not proof that their source evidence is unchanged");
  assert.throws(() => projectExploreTurnContext({...corrected, result_offset: 3,
    result_revision: snapshot.result_page.revision}), /changed/);
});
