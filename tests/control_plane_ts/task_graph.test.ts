import assert from "node:assert/strict";
import test from "node:test";
import { projectGoalTaskGraphTopology, projectTaskGraphTopology, TASK_GRAPH_GOAL_TOPOLOGY_REQUEST,
  TASK_GRAPH_TOPOLOGY_REQUEST } from "../../loopx/control_plane/work_items/task_graph.ts";
import { projectRelations, todoRef } from "../../loopx/control_plane/work_items/planning_relations.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { productionScaleCoordinationFixture } from "./production_scale_coordination_fixture.ts";

const row = (todo_id: string, extra: JsonObject = {}): JsonObject =>
  ({ todo_id, done: true, successor_todo_ids: [], ...extra });
function graph(items: JsonObject[], extra: JsonObject = {}): JsonObject {
  return projectTaskGraphTopology({ schema_version: TASK_GRAPH_TOPOLOGY_REQUEST,
    selected_todo_id: "todo_root", predecessor_limit: 4, source_truncated: false, items, ...extra });
}

test("known Todo conditions only: opaque route/capability refs cannot become graph edges", () => {
  for (const value of ["route:todo_fake", "capacity_available:todo_fake", "unknown:todo_fake", "pr_merged:todo_fake"]) {
    assert.equal(todoRef(value), null);
  }
  assert.equal(todoRef("todo_real"), "todo_real");
  assert.equal(todoRef("monitor_changed:todo_real"), "todo_real");
  assert.equal(todoRef("todo_done:todo_real"), "todo_real");
});

test("shared catalog preserves lineage, lifecycle and condition as distinct knowledge", () => {
  const relations = projectRelations([{todo_id: "todo_root", successor_todo_ids: ["todo_child", "todo_child"],
    unblocks_todo_id: "todo_parent", resume_when: "monitor_changed:todo_monitor"}]);
  assert.deepEqual(relations.map(r => [r.relation, r.enforcement]), [
    ["successor", "lineage_only"], ["unblocks", "typed_lifecycle"], ["resumes_when", "typed_condition"],
  ]);
});

test("budget saturation retains every edge between admitted diamond vertices", () => {
  const rows = [row("todo_root", {done: false}),
    row("todo_a", {successor_todo_ids: ["todo_root"]}),
    row("todo_b", {successor_todo_ids: ["todo_root"]}),
    row("todo_shared", {successor_todo_ids: ["todo_a", "todo_b"]}),
    row("todo_aaa_overflow", {successor_todo_ids: ["todo_b"]})];
  const result = graph(rows, {predecessor_limit: 3});
  assert.deepEqual(result.predecessor_todo_ids, ["todo_a", "todo_b", "todo_shared"]);
  assert.deepEqual((result.edges as JsonObject[]).map(e => [e.from_todo_id, e.to_todo_id]), [
    ["todo_root", "todo_a"], ["todo_root", "todo_b"],
    ["todo_a", "todo_shared"], ["todo_b", "todo_shared"],
  ]);
  assert.equal((result.completeness as JsonObject).predecessor_truncated, true);
  assert.deepEqual(graph([...rows].reverse(), {predecessor_limit: 3}), result);
});

test("cycles and parallel semantic edges terminate without duplicating nodes", () => {
  const result = graph([row("todo_root", {resume_when: "todo_done:todo_parent", successor_todo_ids: ["todo_parent"]}),
    row("todo_parent", {successor_todo_ids: ["todo_root"]})]);
  assert.deepEqual(result.predecessor_todo_ids, ["todo_parent"]);
  assert.equal((result.edges as JsonObject[]).length, 3);
  assert.equal((result.completeness as JsonObject).topology_complete, true);
});

test("missing, source truncation and display truncation are independent", () => {
  const result = graph([row("todo_root", {resume_when: "todo_done:todo_missing"})]);
  assert.deepEqual(result.completeness, {predecessor_limit: 4, emitted_predecessor_count: 0,
    predecessor_truncated: false, source_truncated: false, missing_predecessor_count: 1, topology_complete: false});
  assert.equal((graph([row("todo_root")], {source_truncated: true}).completeness as JsonObject).topology_complete, false);
  const omitted = graph([row("todo_root"), row("todo_parent", {successor_todo_ids: ["todo_root"]})], {predecessor_limit: 0});
  assert.equal((omitted.completeness as JsonObject).predecessor_truncated, true);
  assert.equal((omitted.completeness as JsonObject).missing_predecessor_count, 0);
});

test("open predecessor remains an expansion boundary, but not a dropped node", () => {
  const result = graph([row("todo_root"), row("todo_parent", {done: false, successor_todo_ids: ["todo_root"]}),
    row("todo_ancestor", {successor_todo_ids: ["todo_parent"]})]);
  assert.deepEqual(result.predecessor_todo_ids, ["todo_parent"]);
  assert.equal((result.completeness as JsonObject).topology_complete, true);
});

test("thousands of unrelated rows and deep ancestry keep projection bounded", () => {
  const rows = [row("todo_root"), ...Array.from({length: 4096}, (_, i) => row(`todo_unrelated_${i}`)),
    ...Array.from({length: 512}, (_, i) => row(`todo_chain_${i}`, {
      successor_todo_ids: [i === 0 ? "todo_root" : `todo_chain_${i - 1}`],
    }))];
  const result = graph(rows);
  assert.equal((result.predecessor_todo_ids as string[]).length, 4);
  assert.equal((result.edges as JsonObject[]).length, 4);
  assert.equal((result.completeness as JsonObject).predecessor_truncated, true);
});

test("invalid wire input fails at the typed boundary", () => {
  assert.throws(() => graph([row("todo_root"), row("todo_root")]), /Duplicate/);
  for (const predecessor_limit of [-1, 33, 0.5]) assert.throws(() => graph([], {predecessor_limit}));
  assert.throws(() => graph([row("todo_root", {done: "false"})]), /boolean/);
  assert.throws(() => graph([], {schema_version: "unknown"}), /schema/);
});

function goalGraph(items: JsonObject[], extra: JsonObject = {}): JsonObject {
  return projectGoalTaskGraphTopology({ schema_version: TASK_GRAPH_GOAL_TOPOLOGY_REQUEST,
    node_limit: 50, source_truncated: false, items, ...extra });
}
const depths = (result: JsonObject) =>
  Object.fromEntries((result.nodes as JsonObject[]).map(n => [n.todo_id, n.depth]));
const pairs = (result: JsonObject) =>
  (result.edges as JsonObject[]).map(e => [e.from_todo_id, e.to_todo_id, e.relation]);

test("goal scope keeps every typed edge and layers by longest prerequisite path", () => {
  // scope -> (venues, rota); venues -> budget; deposit unblocked by reserve; gate unblocks reserve.
  const rows = [row("todo_scope", {successor_todo_ids: ["todo_venues", "todo_rota"]}),
    row("todo_venues", {successor_todo_ids: ["todo_budget"]}), row("todo_rota"),
    row("todo_budget", {done: false}), row("todo_reserve", {done: false, unblocks_todo_id: "todo_deposit"}),
    row("todo_deposit", {done: false}), row("todo_gate", {done: false, unblocks_todo_id: "todo_reserve"}),
    row("todo_close", {done: false, resume_when: "todo_done:todo_deposit"}), row("todo_loose", {done: false})];
  const result = goalGraph(rows);
  assert.deepEqual(depths(result), {todo_scope: 0, todo_venues: 1, todo_rota: 1, todo_budget: 2,
    todo_reserve: 1, todo_deposit: 2, todo_gate: 0, todo_close: 3, todo_loose: 0});
  assert.deepEqual(new Set(pairs(result).map(String)), new Set([
    ["todo_venues", "todo_scope", "continues"], ["todo_rota", "todo_scope", "continues"],
    ["todo_budget", "todo_venues", "continues"], ["todo_deposit", "todo_reserve", "depends_on"],
    ["todo_reserve", "todo_gate", "depends_on"], ["todo_close", "todo_deposit", "depends_on"],
  ].map(String)));
  assert.equal((result.completeness as JsonObject).topology_complete, true);
  assert.deepEqual(goalGraph([...rows].reverse()).edges, result.edges, "edge order is caller independent");
});

test("goal scope admits unfinished work, then its direct completed prerequisites, then history", () => {
  const rows = [row("todo_old_a"), row("todo_old_b"), row("todo_prereq"),
    row("todo_open", {done: false}), row("todo_next", {done: false, resume_when: "todo_done:todo_prereq"})];
  const result = goalGraph(rows, {node_limit: 3});
  assert.deepEqual((result.nodes as JsonObject[]).map(n => n.todo_id), ["todo_prereq", "todo_open", "todo_next"]);
  assert.deepEqual(result.completeness, {node_limit: 3, emitted_node_count: 3, omitted_node_count: 2,
    source_truncated: false, missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: false});
});

test("goal scope reports missing endpoints and cycles without inventing depth or edges", () => {
  const result = goalGraph([row("todo_alpha", {done: false, resume_when: "todo_done:todo_beta"}),
    row("todo_beta", {done: false, resume_when: "todo_done:todo_alpha"}),
    row("todo_gamma", {done: false, resume_when: "todo_done:todo_elsewhere"}),
    row("todo_delta", {successor_todo_ids: ["todo_gone"]}),
    row("todo_eps", {done: false, resume_when: "capacity_available:todo_alpha"})]);
  const completeness = result.completeness as JsonObject;
  assert.equal(completeness.cycle_edge_count, 1);
  assert.equal(completeness.missing_endpoint_count, 2);
  assert.equal(completeness.topology_complete, false);
  assert.deepEqual(pairs(result), [["todo_alpha", "todo_beta", "depends_on"], ["todo_beta", "todo_alpha", "depends_on"]]);
  assert.deepEqual(depths(result), {todo_alpha: 1, todo_beta: 0, todo_gamma: 0, todo_delta: 0, todo_eps: 0});
  assert.equal((goalGraph([row("todo_alpha")], {source_truncated: true}).completeness as JsonObject).topology_complete, false);
});

test("goal scope orders by dependencies and never reports opposing lineage as a cycle", () => {
  // Work spawns the decision it waits for: lineage says the gate came from the
  // work, the dependency says the work waits on the gate.
  const result = goalGraph([
    row("todo_work", {done: false, successor_todo_ids: ["todo_gate", "todo_after"]}),
    row("todo_gate", {done: false, unblocks_todo_id: "todo_work"}),
    row("todo_after", {done: false}),
    row("todo_loop_a", {successor_todo_ids: ["todo_loop_b"]}), row("todo_loop_b", {successor_todo_ids: ["todo_loop_a"]})]);
  const completeness = result.completeness as JsonObject;
  assert.equal(completeness.cycle_edge_count, 0, "Lineage carries no order, so it cannot contradict one");
  assert.equal(completeness.topology_complete, true);
  assert.equal(depths(result).todo_gate, 0);
  assert.equal(depths(result).todo_work, 1, "The dependency wins over opposing lineage");
  assert.equal(depths(result).todo_after, 2, "Agreeing lineage still adds depth");
  assert.equal(Math.abs((depths(result).todo_loop_a as number) - (depths(result).todo_loop_b as number)), 1);
  assert.equal(pairs(result).length, 5, "Every recorded relation is still drawn");
});

test("goal scope rejects invalid wire input at the typed boundary", () => {
  assert.throws(() => goalGraph([row("todo_a"), row("todo_a")]), /Duplicate/);
  for (const node_limit of [0, 201, 1.5]) assert.throws(() => goalGraph([], {node_limit}));
  assert.throws(() => goalGraph([], {schema_version: TASK_GRAPH_TOPOLOGY_REQUEST}), /schema/);
});

test("production-scale canonical fixture supports mixed ancestry without changing authority", () => {
  const fixture = productionScaleCoordinationFixture("graph-goal");
  const before = JSON.stringify(fixture.projection);
  const records = fixture.projection.todos as JsonObject[];
  assert.equal(records.length, fixture.expected_initial_todo_count);
  // Add a small, explicit relationship overlay to the existing mixed status,
  // claim, Monitor and User-gate fixture; do not replace it with a small mock.
  const rows = records.map(r => row(r.todo_id as string, {done: r.done === true}));
  const root = fixture.completion_todo_id;
  const parent = rows[0].todo_id as string;
  rows[0].successor_todo_ids = [root];
  rows[1].successor_todo_ids = [parent];
  rows.find(r => r.todo_id === root)!.resume_when = `monitor_changed:${rows[2].todo_id}`;
  const result = graph(rows, {selected_todo_id: root});
  assert.equal((result.predecessor_todo_ids as string[]).length, 3);
  assert.deepEqual(new Set((result.edges as JsonObject[]).map(e => e.enforcement)),
    new Set(["lineage_only", "typed_condition"]));
  assert.equal(JSON.stringify(fixture.projection), before);
});
