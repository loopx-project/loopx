import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {projectStrideExploreBasis, strideReviewMismatch} from "../../loopx/control_plane/work_items/stride_policy.ts";
import {projectExploreTurnContext} from "../../loopx/control_plane/capabilities/explore_turn_context.ts";

test("recent questions use UTC instants, preserve microseconds and retain legacy timestamps", () => {
  for (const timestamps of [
    ["2026-01-02T08:00:00+08:00", "2026-01-02T00:01:00Z", "2026-01-01T19:02:00-05:00"],
    ["2026-01-02T08:00:00.000001+08:00", "2026-01-02T00:00:00.000002Z"],
    ["unknown", "2026-01-02T00:00:00"],
  ]) {
    const nodes = timestamps.map((last_updated_at, index) => ({node_id: `node-${index}`, last_updated_at}));
    const request = {goal_id: "research", agent_id: "worker", route: ["loopx"],
      graph_enabled: true, harness_gate: {enabled: false},
      projection: {nodes, findings: [], edges: []}, plan: {}};
    const original = structuredClone(request);
    const result = projectExploreTurnContext(request).graph as JsonObject;
    assert.deepEqual(result.recent_nodes.map(row => row.node_id), nodes.map(row => row.node_id).reverse());
    assert.deepEqual(request, original);
    assert.equal(projectExploreTurnContext({...request, graph_enabled: false}).graph, null);
  }
});

test("next-turn evidence exposes current question state and relation changes invalidate old pages", () => {
  const request = {goal_id: "research", agent_id: "worker", route: ["loopx"],
    graph_enabled: true, harness_gate: {enabled: false}, result_limit: 1,
    projection: {nodes: [{node_id: "finite", status: "resolved"}],
      findings: ["result-one", "result-two"].map(finding_id => ({finding_id, node_id: "finite",
        status: "refuted", summary: "A scoped negative answer.", tags: ["writeback-result"]})),
      edges: [{from_node: "finite", to_node: "transfer", edge_type: "refutes", summary: "A counterexample."}]},
    plan: {}};
  const first = projectExploreTurnContext(request).graph as JsonObject;
  assert.equal(first.writeback_results[0].status, "refuted");
  assert.equal(first.writeback_results[0].question_status, "resolved");
  assert.equal(first.writeback_results[0].relations[0].to_node, "transfer");
  request.projection.nodes[0].status = "exploring";
  assert.throws(() => projectExploreTurnContext({...request, result_offset: 1,
    result_revision: first.result_page.revision}), /changed/);
  const reopened = projectExploreTurnContext(request).graph as JsonObject;
  assert.equal(reopened.writeback_results[0].question_status, "exploring");
  request.projection.edges[0].summary = "A revised applicability condition.";
  assert.throws(() => projectExploreTurnContext({...request, result_offset: 1,
    result_revision: reopened.result_page.revision}), /changed/);
  const off = projectExploreTurnContext({...request, graph_enabled: false});
  assert.equal(off.graph, null);
});

for (const includeDigests of [false, true]) {
  test(`paged and audited findings retain complete source digests: ${includeDigests}`, () => {
    const findings = Array.from({length: 20}, (_, index) => ({
      finding_id: `finding-${index}`, node_id: `node-${index}`,
      finding: `Scoped finding ${index}`, status: "confirmed",
      summary: "Applicable only to the tested inputs. ".repeat(20),
      evidence_refs: [`validation:case-${index}`],
      tags: index < 10 ? ["writeback-result"] : [],
    }));
    const request = {
      goal_id: "research", agent_id: "worker", route: ["loopx"],
      graph_enabled: true, harness_gate: {enabled: true},
      include_finding_digests: includeDigests, result_limit: 2,
      projection: {nodes: [], findings},
      plan: {selected_branches: [{todo_id: "audit-old-finding", typed_evidence_audit: {
        requested_node_refs: ["node-19"],
        findings: [{finding_id: "finding-19", node_id: "node-19",
          finding: "Compact audit text", status: "confirmed"}],
      }}]},
    };
    const first = projectExploreTurnContext(request);
    const page = projectExploreTurnContext({...request, result_offset: 2,
      result_revision: (first.graph as JsonObject).result_page.revision});
    const graph = page.graph as JsonObject;
    const audit = (page.harness as JsonObject).selected_branches[0].typed_evidence_audit;
    assert.deepEqual(graph.recent_findings.map((row: JsonObject) => row.finding_id),
      ["finding-0", "finding-1", "finding-2"]);
    assert.deepEqual(graph.writeback_results.map((row: JsonObject) => row.finding_id),
      ["finding-2", "finding-3"]);
    assert.equal(audit.findings[0].finding_id, "finding-19");
    for (const row of [...graph.recent_findings, ...graph.writeback_results, ...audit.findings]) {
      const source = findings.find(finding => finding.finding_id === row.finding_id)!;
      if (includeDigests) {
        assert.equal(row.source_sha256,
          createHash("sha256").update(JSON.stringify(source)).digest("hex"));
      } else {
        assert.equal(Object.hasOwn(row, "source_sha256"), false);
      }
    }
    // The audit omits this source text; a new read must still bind its revision.
    findings[19].summary += "Additional applicability constraint.";
    const updated = projectExploreTurnContext(request);
    const updatedFinding = (updated.harness as JsonObject).selected_branches[0].typed_evidence_audit.findings[0];
    if (includeDigests) {
      assert.notEqual(updatedFinding.source_sha256, audit.findings[0].source_sha256);
      assert.equal(updatedFinding.source_sha256,
        createHash("sha256").update(JSON.stringify(findings[19])).digest("hex"));
    } else {
      assert.deepEqual(updatedFinding, audit.findings[0]);
    }
  });
}

for (const tagged of [false, true]) for (const limit of [3, 4, 20]) {
  test(`every scoped measured outcome remains selectable: tagged=${tagged}, limit=${limit}`, () => {
    const findings = Array.from({length: 20}, (_, index) => ({finding_id: `scoped-${index}`,
      node_id: "question", finding: `Measured input family ${index}`, status: "confirmed",
      summary: `Applicable to input family ${index} only.`, tags: tagged ? ["writeback-result"] : [],
      evidence_refs: [`result:scoped-${index}`],
      measurements: [{metric: "quality", value: index, unit: "points", higher_is_better: true,
        comparison_scope: "validation:owned-v1", input_revision: "owned-input-v1",
        evaluation_ref: "evaluator:owned-v1", candidate_ref: `candidate:scoped-${index}`,
        evidence_ref: `result:scoped-${index}`}]}));
    const request = {goal_id: "research", agent_id: "worker", route: ["loopx"],
      graph_enabled: true, harness_gate: {enabled: true}, result_limit: limit,
      projection: {nodes: [{node_id: "question", status: "exploring"}], findings},
      plan: {selected_branches: [{todo_id: "todo_method", actionable_open: true,
        typed_evidence_audit: {requested_node_refs: ["question"]}}]}};
    const source = projectExploreTurnContext(request);
    const graph = source.graph as JsonObject, harness = source.harness as JsonObject;
    assert.ok(harness.comparison_candidates?.length, "measured findings must reach the existing decision cards without an origin tag");
    const card = harness.comparison_candidates[0];
    // Quantitative feedback still uses all sources, independently of page size.
    assert.equal(graph.measurement_feedback[0].measured_count, 20);
    assert.equal(graph.measurement_feedback[0].best.value, 19);
    assert.equal(graph.measurement_feedback[0].best.candidate_ref, "candidate:scoped-19");
    // The result page and candidate card must describe the same read scope.
    assert.equal(graph.writeback_results.length, limit);
    assert.deepEqual(card.outcomes.map((row: JsonObject) => row.finding_id),
      findings.slice(0, limit).map(row => row.finding_id));
    const last = findings[limit - 1];
    const selected = {todo_id: "todo_method", node_id: last.node_id, finding_id: last.finding_id,
      source_sha256: createHash("sha256").update(JSON.stringify(last)).digest("hex")};
    const basis = projectStrideExploreBasis(source, "research", "worker")!;
    const decision = {explore_review: {revision: basis.revision, disposition: "used",
      considered_refs: [last.finding_id], selected_candidate: selected}};
    assert.equal(strideReviewMismatch(decision, {explore_basis: basis}, undefined, ["retain:todo_method"]), null);
    assert.match(String(strideReviewMismatch({explore_review: {...decision.explore_review,
      selected_candidate: {...selected, source_sha256: "f".repeat(64)}}},
      {explore_basis: basis}, undefined, ["retain:todo_method"])), /exact scoped result/);
    assert.match(String(strideReviewMismatch(decision, {explore_basis: basis}, undefined, [])), /retained/);
    // Page replay must expose exactly the same choices at ordinary writeback.
    const page = {revision: graph.result_page.revision, offset: 0, limit};
    const replay = projectExploreTurnContext({...request, review_pages: [page]});
    assert.deepEqual(replay.review_pages[0].comparison_candidates, harness.comparison_candidates);
    const tooMany = {...source, harness: {...harness, comparison_candidates: [
      {...card, outcomes: Array.from({length: 21}, () => card.outcomes[0])}]}};
    assert.throws(() => projectStrideExploreBasis(tooMany, "research", "worker"), /scoped outcomes/);
    const evidenceOnly = projectExploreTurnContext({...request, harness_gate: {enabled: false}});
    assert.equal(evidenceOnly.harness, null);
    assert.equal(Object.hasOwn(evidenceOnly.graph, "comparison_candidates"), false);
  });
}

test("explicit task links outrank an independent planner without hiding scoped counterevidence", () => {
  const result = (id: string, node: string, status = "confirmed") => ({finding_id: id, node_id: node,
    finding: id, status, summary: `Only ${id}'s recorded inputs.`, tags: ["writeback-result"],
    measurements: [{metric: "quality", value: id === "current-negative" ? 4 : 8, unit: "points",
      higher_is_better: true, comparison_scope: `validation:${node}`, input_revision: "inputs-v1",
      evaluation_ref: "evaluator:v1", candidate_ref: `candidate:${id}`, evidence_ref: `result:${id}`}],
    evidence_refs: [`result:${id}`]});
  const findings = [result("other-new", "other"), result("current-new", "current"),
    result("other-negative", "other", "refuted"), result("current-negative", "current", "refuted")];
  const request = {goal_id: "research", agent_id: "worker", route: ["loopx"], graph_enabled: true,
    harness_gate: {enabled: true}, result_limit: 1,
    projection: {nodes: ["current", "other"].map(node_id => ({node_id, status: "exploring"})), findings},
    plan: {selected_branches: [{todo_id: "todo_planned", actionable_open: true,
      typed_evidence_audit: {requested_node_refs: ["other"]}}]}};
  const global = projectExploreTurnContext(request);
  const focus = {todo_id: "todo_chosen", focus_todo: {todo_id: "todo_chosen", role: "agent", status: "open",
    claimed_by: "worker", explore_result_node_refs: ["current"]}};
  const chosen = projectExploreTurnContext({...request, ...focus});
  assert.equal((global.graph as JsonObject).writeback_results[0].finding_id, "other-negative");
  assert.equal((chosen.graph as JsonObject).writeback_results[0].finding_id, "current-negative");
  assert.equal((chosen.graph as JsonObject).measurement_feedback[0].node_id, "current");
  assert.deepEqual((chosen.harness as JsonObject).selected_branches, (global.harness as JsonObject).selected_branches);
  assert.deepEqual(chosen.boundary, global.boundary, "read focus grants no execution or selection authority");
  const graph = chosen.graph as JsonObject;
  assert.equal(graph.result_page.total, findings.length);
  assert.ok(graph.result_page.next_command.includes("--todo-id"));
  assert.ok(graph.result_page.next_command.includes("todo_chosen"));
  const second = projectExploreTurnContext({...request, ...focus, result_offset: 1,
    result_revision: graph.result_page.revision});
  assert.equal((second.graph as JsonObject).writeback_results[0].finding_id, "current-new");
  const all = projectExploreTurnContext({...request, ...focus, result_limit: 20}).graph as JsonObject;
  assert.deepEqual(new Set(all.writeback_results.map((row: JsonObject) => row.finding_id)),
    new Set(findings.map(row => row.finding_id)), "other findings remain available");
  const source = {...chosen, graph: all};
  const basis = projectStrideExploreBasis(source, "research", "worker")!;
  const decision = {explore_review: {revision: basis.revision,
    disposition: "used", considered_refs: ["current-negative"]}};
  assert.equal(strideReviewMismatch(decision, {explore_basis: basis}), null);
  const page = {revision: graph.result_page.revision, offset: 1, limit: 1, todo_id: "todo_chosen"};
  assert.deepEqual(projectExploreTurnContext({...request, ...focus, review_pages: [page]}).review_pages[0].finding_refs,
    ["current-new"]);
  assert.deepEqual(projectExploreTurnContext({...request, review_pages: [page]}).review_pages, []);
  assert.throws(() => projectExploreTurnContext({...request, ...focus, focus_todo: {
    ...focus.focus_todo, explore_result_node_refs: ["other"]}, result_offset: 1,
    result_revision: graph.result_page.revision}), /changed/);
});

test("task focus rejects missing, foreign and terminal sources and is inert when Explore is off", () => {
  const request = {goal_id: "research", agent_id: "worker", route: ["loopx"], graph_enabled: true,
    harness_gate: {enabled: false}, projection: {}, plan: {}, todo_id: "todo_chosen"};
  const task = {todo_id: "todo_chosen", role: "agent", status: "open", claimed_by: "worker"};
  for (const focus_todo of [null, {...task, todo_id: "todo_other"}, {...task, claimed_by: "peer"},
    {...task, status: "done"}, {...task, role: "user"}]) {
    assert.throws(() => projectExploreTurnContext({...request, focus_todo}), /Todo/);
  }
  const off = {...request, graph_enabled: false};
  assert.deepEqual(projectExploreTurnContext(off), projectExploreTurnContext({...off, todo_id: null}));
});

for (const counterexample of [false, true]) {
  for (const field of ["writeback_results", "measurement_feedback"]) {
    test(`overlapping planner links cannot reorder current task ${field}, refutation=${counterexample}`, () => {
      const finding = (id: string, node: string, status = "confirmed") => ({finding_id: id,
        node_id: node, status, finding: id, tags: ["writeback-result"],
        measurements: [{metric: "quality", value: 5, unit: "points", higher_is_better: true,
          comparison_scope: `validation:${node}`, input_revision: "inputs-v1", evaluation_ref: "eval:v1",
          candidate_ref: `candidate:${id}`, evidence_ref: `result:${id}`} ]});
      const findings = [finding("current-new", "current"), finding("shared-old", "shared"),
        ...(counterexample ? [finding("current-negative", "current", "refuted")] : []),
        finding("planner-only", "other"), finding("unrelated", "unrelated")];
      const request = {goal_id: "research", agent_id: "worker", route: ["loopx"], graph_enabled: true,
        harness_gate: {enabled: true}, result_limit: 1,
        projection: {nodes: [], findings}, todo_id: "todo_current",
        focus_todo: {todo_id: "todo_current", role: "agent", status: "open", claimed_by: "worker",
          explore_result_node_refs: ["current", "shared"]}};
      const expected = field === "measurement_feedback" ? ["current", "shared", "other", "unrelated"]
        : [...(counterexample ? ["current-negative"] : []), "current-new", "shared-old", "planner-only", "unrelated"];
      for (const plannedRefs of [["other"], ["shared", "other"]]) {
        const params = {...request, plan: {selected_branches: [{todo_id: "todo_planned", actionable_open: true,
          typed_evidence_audit: {requested_node_refs: plannedRefs}}]}};
        const first = projectExploreTurnContext(params);
        const all = projectExploreTurnContext({...params, result_limit: 20});
        const values = (graph: JsonObject) => graph[field].map((row: JsonObject) =>
          field === "measurement_feedback" ? row.node_id : row.finding_id);
        assert.deepEqual(values(first.graph as JsonObject), expected.slice(0, 1));
        assert.deepEqual(values(all.graph as JsonObject), expected);
        assert.deepEqual((first.harness as JsonObject).selected_branches,
          (projectExploreTurnContext({...params, todo_id: null}).harness as JsonObject).selected_branches);
        if (field === "writeback_results") {
          const page = {todo_id: request.todo_id, revision: (first.graph as JsonObject).result_page.revision,
            offset: 1, limit: 1};
          const replay = projectExploreTurnContext({...params, review_pages: [page]});
          assert.deepEqual(replay.review_pages[0].finding_refs, expected.slice(1, 2));
        }
      }
    });
  }
}
