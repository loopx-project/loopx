import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {normalizeResearchObservation, projectResearchFrontier, researchCompositionGaps} from "../../loopx/control_plane/capabilities/explore_research.ts";

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

function nestedResearch() {
  const nodes: JsonObject[] = [node("a", "b"), node("b"), node("c"), node("x"), node("y")];
  const edges: JsonObject[] = [];
  const add = (name: string, inputs: string[], target?: string) => {
    const raw: JsonObject = {...observation(name), input_observations: inputs.map(input => ({
      node_id: input, fingerprint: (nodes.find(row => row.node_id === input)!.research_observation as JsonObject).fingerprint,
    })), composition_candidates: target ? [{target_node_id: target, basis: "explicit",
      interaction_kind: "producer_consumer", evidence_ids: [`ev-${name}`, `ev-${target}`]}] : []};
    const result: JsonObject = {...node(name), node_kind: "experiment",
      research_observation: normalizeResearchObservation({observation: raw})};
    nodes.push(result);
    edges.push(...inputs.map(to_node => ({from_node: name, to_node, edge_type: "depends_on"})));
    return result;
  };
  const ab = add("ab", ["a", "b"], "c");
  add("abc", ["ab", "c"]);
  // An independent branch remains usable even when the first chain is stale.
  nodes.find(row => row.node_id === "x")!.research_observation = normalizeResearchObservation({observation: {
    ...observation("x"), composition_candidates: [{target_node_id: "y", basis: "explicit",
      interaction_kind: "producer_consumer", evidence_ids: ["ev-x", "ev-y"]}],
  }});
  add("xy", ["x", "y"]);
  const params = {goal_id: "fixture", nodes, edges};
  const state = (inputs: string[]) => researchCompositionGaps(params)
    .find(gap => JSON.stringify(gap.input_node_ids) === JSON.stringify(inputs))!.state;
  return {params, nodes, edges, ab, add, state};
}

test("only current declared ancestry can qualify a nested research input", () => {
  for (const damage of ["reopened", "revised", "missing", "edge_removed", "edge_added", "cycle", "lineage_removed"]) {
    const {params, nodes, edges, ab, state} = nestedResearch();
    assert.equal(state(["ab", "c"]), "observed");
    const a = nodes[0];
    if (damage === "reopened") { a.status = "open"; delete a.research_observation; }
    if (damage === "revised") a.research_observation = normalizeResearchObservation({observation: {
      ...a.research_observation as JsonObject, progress: {...(a.research_observation as JsonObject).progress as JsonObject,
        evidence_ids: ["ev-a", "ev-a-new"]},
    }});
    if (damage === "missing") nodes.splice(0, 1);
    if (damage === "edge_removed") edges.splice(edges.findIndex(edge => edge.from_node === "ab" && edge.to_node === "a"), 1);
    if (damage === "edge_added" || damage === "cycle") edges.push({
      from_node: "ab", to_node: damage === "cycle" ? "abc" : "x", edge_type: "depends_on",
    });
    if (damage === "lineage_removed") {
      const raw = {...ab.research_observation as JsonObject};
      delete raw.input_observations;
      ab.research_observation = normalizeResearchObservation({observation: raw});
    }
    const retained = structuredClone(nodes.filter(row => ["ab", "abc", "xy"].includes(String(row.node_id))));
    assert.equal(state(["ab", "c"]), "ineligible", damage);
    assert.equal(state(["x", "y"]), "observed", damage);
    assert.deepEqual(nodes.filter(row => ["ab", "abc", "xy"].includes(String(row.node_id))), retained);
    assert.equal(researchCompositionGaps({...params, nodes: [...nodes].reverse(), edges: [...edges].reverse()})
      .find(gap => JSON.stringify(gap.input_node_ids) === JSON.stringify(["ab", "c"]))!.state, "ineligible");
  }
});

test("negative results, unrelated relations and independently supported inputs retain eligibility", () => {
  const {nodes, edges, ab, state, add} = nestedResearch();
  // Record a coverage-backed negative conclusion and bind its actual fingerprint.
  ab.status = "dead_end";
  const raw = ab.research_observation as JsonObject;
  ab.research_observation = normalizeResearchObservation({observation: {...raw,
    progress: {...raw.progress as JsonObject, result_class: "no_followup"},
    closure_basis: {...raw.closure_basis as JsonObject, disposition: "no_followup"}}});
  const abc = nodes.find(row => row.node_id === "abc")!;
  abc.research_observation = normalizeResearchObservation({observation: {...abc.research_observation as JsonObject,
    input_observations: ["ab", "c"].map(id => ({node_id: id,
      fingerprint: (nodes.find(row => row.node_id === id)!.research_observation as JsonObject).fingerprint}))}});
  nodes.push({...node("unrelated"), status: "open"});
  edges.push({from_node: "ab", to_node: "unrelated", edge_type: "supports"});
  edges.push({from_node: "c", to_node: "unrelated", edge_type: "depends_on"});
  ab.parent_id = "unrelated";
  ab.tags = ["additional-context"];
  assert.equal(state(["ab", "c"]), "observed");
  // A stale sibling experiment must not veto a separate current result.
  const obsolete = add("obsolete", ["ab", "c"]);
  obsolete.research_observation = normalizeResearchObservation({observation: {
    ...obsolete.research_observation as JsonObject,
    input_observations: [{node_id: "ab", fingerprint: "old"}, {node_id: "c",
      fingerprint: (nodes[2].research_observation as JsonObject).fingerprint}],
  }});
  assert.equal(state(["ab", "c"]), "observed");
});

test("shared ancestors and deep valid chains recover from current source facts", () => {
  const {nodes, edges, add, state} = nestedResearch();
  const original = structuredClone(nodes[0]);
  // Both inputs share a, which is not a dependency cycle.
  add("ac", ["a", "c"]);
  const diamond = add("diamond", ["ab", "ac"], "b");
  add("diamond-result", ["diamond", "b"]);
  assert.equal(state(["b", "diamond"]), "observed");
  nodes[0].status = "open";
  assert.equal(state(["b", "diamond"]), "ineligible");
  // A fresh projection of an independently verified complete snapshot is not
  // a historical replay or a sticky, persisted invalidation.
  nodes[0] = original;
  assert.equal(state(["b", "diamond"]), "observed");
  let previous = String(diamond.node_id);
  for (let index = 0; index < 256; index++) {
    const name = `chain-${index}`;
    add(name, [previous, "b"], index === 255 ? "c" : undefined);
    previous = name;
  }
  add("deep-result", [previous, "c"]);
  assert.equal(state(["c", previous].sort()), "observed");
  nodes[0].status = "open";
  assert.equal(state(["c", previous].sort()), "ineligible");
  assert.equal(state(["x", "y"]), "observed");
  assert.ok(edges.length > 500);
});
