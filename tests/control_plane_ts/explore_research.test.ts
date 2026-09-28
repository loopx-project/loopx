import assert from "node:assert/strict";
import test from "node:test";
import {normalizeResearchObservation, projectResearchFrontier} from "../../loopx/control_plane/capabilities/explore_research.ts";

function observation(node = "a", target?: string) {
  return {
    schema_version: "typed_research_observation_v0",
    explore_node_id: node,
    progress: {schema_version: "typed_progress_observation_v0", work_item_id: `todo-${node}`,
      result_class: "exploration_exhausted", coverage_scope_id: `scope-${node}`,
      coverage_complete: true, evidence_ids: [`ev-${node}`], fingerprint: `fp-${node}`},
    closure_basis: {schema_version: "research_closure_basis_v0", disposition: "bounded",
      constraints: [{kind: "invariant", id: "boundary", role: "decisive"}], evidence_ids: [`ev-${node}`]},
    composition_candidates: target ? [{target_node_id: target, basis: "explicit",
      interaction_kind: "state_interference", evidence_ids: ["ev-a", "ev-b"]}] : [],
  };
}
function node(id: string, target?: string) {
  return {node_id: id, status: "resolved", node_kind: "hypothesis", evidence_refs: [`ev-${id}`],
    research_observation: normalizeResearchObservation({observation: observation(id, target)})};
}
test("terminal research requires coverage, attributable evidence and a decisive typed basis", () => {
  const raw = observation();
  assert.equal(normalizeResearchObservation({observation: raw}).explore_node_id, "a");
  for (const progress of [{...raw.progress, coverage_complete: false}, {...raw.progress, evidence_ids: []}]) {
    assert.throws(() => normalizeResearchObservation({observation: {...raw, progress}}));
  }
  assert.throws(() => normalizeResearchObservation({observation: {...raw,
    closure_basis: {...raw.closure_basis, constraints: [{kind: "invariant", id: "boundary", role: "supporting"}]}}}));
  assert.throws(() => normalizeResearchObservation({observation: {...raw,
    closure_basis: {...raw.closure_basis, evidence_ids: ["unrelated"]}}}));
});
test("explicit pairs are canonical, read-only and independent of node enumeration", () => {
  const nodes = [node("a", "b"), node("b", "a")];
  const first = projectResearchFrontier({goal_id: "fixture", nodes, edges: []});
  assert.deepEqual(first, projectResearchFrontier({goal_id: "fixture", nodes: [...nodes].reverse(), edges: []}));
  assert.equal(first.candidate_count, 1);
  assert.equal(first.pending_count, 1);
  assert.equal(first.mode, "read_only_shadow");
  assert.equal(projectResearchFrontier({goal_id: "fixture", nodes: [node("a"), node("b")], edges: []}).candidate_count, 0);
});
test("a status update, untyped result, unrelated experiment or ACK cannot close a candidate", () => {
  const nodes = [node("a", "b"), node("b")];
  const edges = ["a", "b"].map(to_node => ({from_node: "joint", to_node, edge_type: "depends_on"}));
  const experiment = {node_id: "joint", node_kind: "experiment", status: "resolved", evidence_refs: ["result"]};
  assert.equal(projectResearchFrontier({goal_id: "fixture", nodes: [...nodes, experiment], edges}).pending_count, 1);
  const observed = {...experiment, research_observation: normalizeResearchObservation({observation: {
    ...observation("joint"), input_observations: nodes.map(node => ({node_id: node.node_id, fingerprint: node.research_observation.fingerprint}))}})};
  assert.equal(projectResearchFrontier({goal_id: "fixture", nodes: [...nodes, observed], edges}).observed_count, 1);
  assert.equal(projectResearchFrontier({goal_id: "fixture", nodes: [...nodes, observed], edges: edges.slice(0, 1)}).pending_count, 1);
  assert.equal(projectResearchFrontier({goal_id: "fixture", nodes: [{...nodes[0], status: "open"}, nodes[1], observed], edges}).ineligible_count, 1);
});
test("unknown, private, untyped and oversized inputs fail closed", () => {
  const raw = observation("a", "b");
  for (const patch of [{explore_node_id: "/Users/example/private"}, {extra: true},
    {composition_candidates: [{...raw.composition_candidates[0], basis: "inferred"}]},
    {composition_candidates: Array(4).fill(raw.composition_candidates[0])}]) {
    assert.throws(() => normalizeResearchObservation({observation: {...raw, ...patch}}));
  }
  const unknown = projectResearchFrontier({goal_id: "fixture", nodes: [node("a", "b")], edges: []});
  assert.equal(unknown.ineligible_count, 1);
  assert.equal(unknown.pending_count, 0);
});
test("card budget reports omissions and rejects malformed projection inputs", () => {
  const nodes = [node("a", "b"), node("b"), node("c", "d"), node("d"),
    node("e", "f"), node("f"), node("g", "h"), node("h")];
  // Candidate evidence must belong to each particular pair.
  for (const row of nodes) {
    const candidate = (row.research_observation.composition_candidates as {target_node_id: string; evidence_ids: string[]}[])[0];
    if (candidate) candidate.evidence_ids = [`ev-${row.node_id}`, `ev-${candidate.target_node_id}`];
  }
  const view = projectResearchFrontier({goal_id: "fixture", nodes, edges: []});
  assert.equal(view.pending_count, 4);
  assert.equal(view.projected_count, 3);
  assert.equal(view.omitted_count, 1);
  assert.throws(() => projectResearchFrontier({goal_id: "fixture", nodes: {}, edges: []}), /nodes must be an array/);
});
