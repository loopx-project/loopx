import {compactTodoReadText} from "../../loopx/control_plane/todos/read_codec.ts";
import assert from "node:assert/strict";
import test from "node:test";
import { projectStatusTaskGraph, STATUS_TASK_GRAPH_REQUEST, GOAL_TASK_MAP_REQUEST, projectGoalTaskMap, projectGoalTaskGraphTopology, projectTaskGraphTopology, TASK_GRAPH_GOAL_TOPOLOGY_REQUEST,
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
  assert.equal(todoRef("monitor_changed:todo_real@case-a/run-1"), "todo_real");
  assert.equal(todoRef("monitor_changed:todo_real@case-a:run-1"), "todo_real");
  assert.equal(todoRef("monitor_changed:todo_real@one@two"), null);
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

const mapRow = (todo_id: string, extra: JsonObject = {}): JsonObject => ({
  ...row(todo_id, {done: false}), is_user: false, is_monitor: false, ...extra,
});
function goalMap(items: JsonObject[], extra: JsonObject = {}): JsonObject {
  return projectGoalTaskMap({schema_version: GOAL_TASK_MAP_REQUEST, goal_id: "goal_test",
    source_truncated: false, items,
    read_facts: {observed_at: "2026-10-01T00:00:00Z", source_read_at: "2026-10-01T00:00:00Z",
      source_count: items.length}, ...extra});
}
const mapProjection = (response: JsonObject) => response.projection as JsonObject;

test("goal map composes topology and materialization instructions without emitting source-only facts", () => {
  const response = goalMap([
    mapRow("todo_gate", {is_user: true, is_monitor: true, title: "\uFEFF", state: null, owner_agent: [],
      task_domain: {}, unblocks_todo_id: "todo_work"}),
    mapRow("todo_work", {title: {}, state: "invented", owner_agent: {},
      task_domain: [], private_notes: "must not escape"}),
    mapRow("todo_monitor", {is_monitor: true}),
  ], {goal_id: " \uFEFFgoal_test\uFEFF "});
  const envelope = mapProjection(response).projection_envelope as JsonObject;
  assert.equal(envelope.fresh, true);
  assert.equal(envelope.complete, true);
  const legacy = structuredClone(response);
  delete (legacy.projection as JsonObject).projection_envelope;
  assert.deepEqual(legacy, {
    schema_version: "goal_task_map_result_v0",
    projection: {
      schema_version: "goal_task_map_v0", mode: "read_only", goal_id: " \uFEFFgoal_test\uFEFF ",
      limits: {node_limit: 120, emitted_node_count: 3, omitted_node_count: 0, source_truncated: false,
        missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true},
      nodes: [
        {kind: "gate", depth: 0, refs: {todo_ids: ["todo_gate"]}},
        {kind: "deliverable", depth: 1, refs: {todo_ids: ["todo_work"]}},
        {kind: "monitor", depth: 0, refs: {todo_ids: ["todo_monitor"]}},
      ],
      edges: [{relation: "depends_on", enforcement: "typed_lifecycle",
        reason: "Work has a typed lifecycle dependency on the linked unblocking Todo."}],
    },
    node_identity_prefixes: ["node_gate", "node_todo", "node_todo"],
    node_metadata_fields: [[], ["owner_agent", "task_domain"], ["owner_agent", "task_domain"]],
    edge_identities: [{from_node_index: 1, to_node_index: 0, source_relation: "unblocks"}],
  });
});

test("goal map refuses duplicate canonical render-fact IDs", () => {
  assert.throws(() => goalMap([
    mapRow("todo_same", {is_user: true}), mapRow("todo_same"),
  ]), /Duplicate/);
});

test("goal map returns a complete empty map and preserves an empty Goal ID", () => {
  const response = goalMap([], {goal_id: ""});
  assert.equal((mapProjection(response).projection_envelope as JsonObject).complete, true);
  delete (response.projection as JsonObject).projection_envelope;
  assert.deepEqual(response, {
    schema_version: "goal_task_map_result_v0",
    projection: {schema_version: "goal_task_map_v0", mode: "read_only", goal_id: "",
      limits: {node_limit: 120, emitted_node_count: 0, omitted_node_count: 0, source_truncated: false,
        missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true}, nodes: [], edges: []},
    node_identity_prefixes: [], node_metadata_fields: [], edge_identities: [],
  });
});

test("goal map seals source age, unknown reads and unrenderable source coverage in the existing owner", () => {
  const facts = {observed_at: "2026-10-01T00:06:00Z", source_read_at: "2026-10-01T00:00:00Z", source_count: 2};
  const envelope = mapProjection(goalMap([mapRow("todo_visible")], {read_facts: facts})).projection_envelope as JsonObject;
  assert.equal(envelope.fresh, false);
  assert.equal(envelope.complete, false);
  assert.deepEqual((envelope.coverage as JsonObject).omitted,
    [{reason: "unrenderable_todos", count: 1, refs: []}]);
  const unknown = mapProjection(goalMap([], {read_facts: {...facts, source_read_at: null, source_count: 0}})).projection_envelope as JsonObject;
  assert.equal(unknown.fresh, false);
  assert.equal((unknown.sources as JsonObject[])[0]!.read_status, "not_read");
  assert.throws(() => goalMap([mapRow("todo_visible")], {read_facts: {...facts, source_count: 0}}), /source count/);
});

test("goal map carries missing endpoints, source truncation and dependency cycles into limits", () => {
  const response = goalMap([
    mapRow("todo_alpha", {resume_when: "todo_done:todo_beta"}),
    mapRow("todo_beta", {resume_when: "todo_done:todo_alpha"}),
    mapRow("todo_gamma", {resume_when: "todo_done:todo_filtered"}),
  ], {source_truncated: true});
  const projection = mapProjection(response);
  assert.deepEqual(projection.limits, {node_limit: 120, emitted_node_count: 3, omitted_node_count: 0,
    source_truncated: true, missing_endpoint_count: 1, cycle_edge_count: 1, topology_complete: false});
  assert.deepEqual((projection.nodes as JsonObject[]).map(node => node.depth), [1, 0, 0]);
  assert.deepEqual(response.edge_identities, [
    {from_node_index: 0, to_node_index: 1, source_relation: "resumes_when"},
    {from_node_index: 1, to_node_index: 0, source_relation: "resumes_when"},
  ]);
});

test("goal map admits active work and direct prerequisites before filling the 120-node cap", () => {
  const response = goalMap([
    ...Array.from({length: 121}, (_, index) => mapRow(`todo_old_${index}`, {done: true})),
    mapRow("todo_open", {resume_when: "todo_done:todo_old_120"}), mapRow("todo_other"),
  ]);
  const projection = mapProjection(response);
  const ids = (projection.nodes as JsonObject[]).map(node => (node.refs as JsonObject).todo_ids);
  assert.equal(ids.length, 120);
  assert.deepEqual(ids.slice(116), [["todo_old_116"], ["todo_old_120"], ["todo_open"], ["todo_other"]]);
  assert.deepEqual(projection.limits, {node_limit: 120, emitted_node_count: 120, omitted_node_count: 3,
    source_truncated: false, missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: false});
  assert.deepEqual(response.edge_identities,
    [{from_node_index: 118, to_node_index: 117, source_relation: "resumes_when"}]);
});

test("goal map decodes node-kind facts after admission", () => {
  const response = goalMap([
    ...Array.from({length: 120}, (_, index) => mapRow(`todo_active_${index}`)),
    mapRow("todo_omitted", {done: true, is_user: "invalid", is_monitor: []}),
  ]);
  assert.equal((mapProjection(response).limits as JsonObject).omitted_node_count, 1);
});

test("goal map rejects malformed wire types at each consumed boundary", () => {
  for (const extra of [{schema_version: "unknown"}, {goal_id: null}, {source_truncated: "false"}, {items: {}}]) {
    assert.throws(() => goalMap([], extra));
  }
  assert.throws(() => projectGoalTaskMap({schema_version: GOAL_TASK_MAP_REQUEST,
    goal_id: "goal_test", source_truncated: false, items: [null]}), /object/);
  for (const extra of [{todo_id: 1}, {todo_id: null}, {todo_id: ""}, {todo_id: undefined},
    {done: "false"}, {successor_todo_ids: [1]}, {unblocks_todo_id: true}, {is_user: "false"}, {is_monitor: 0}]) {
    assert.throws(() => goalMap([mapRow("todo_invalid", extra)]));
  }
});

function statusGraph(extra: JsonObject = {}): JsonObject {
  const publicTexts: JsonObject = {};
  const collect = (value: unknown): void => {
    if (typeof value === "string") {
      publicTexts[value] = Object.fromEntries([80, 120, 160, 180, 240].map(cap =>
        [String(cap), compactTodoReadText(value, cap)]));
    } else if (value && typeof value === "object") {
      for (const child of Object.values(value)) collect(child);
    }
  };
  collect(extra);
  const overrides = extra.public_texts as JsonObject | undefined;
  extra = {...extra, public_texts: Array.isArray(overrides) ? overrides : {...publicTexts, ...overrides}};
  return projectStatusTaskGraph({schema_version: STATUS_TASK_GRAPH_REQUEST, goal_id: "synthetic-goal",
    summaries: {agent: {items: [], total_count: 0}, user: {items: [], total_count: 0}},
    max_status_todos_per_role: 8, latest_runs: [], public_texts: {},
    fallback_time: "2026-11-02T12:00:00Z", ...extra});
}
const statusTodo = (todo_id: string, extra: JsonObject = {}): JsonObject =>
  ({todo_id, text: `Review ${todo_id}`, status: "open", ...extra});
const statusNodes = (result: JsonObject): JsonObject[] => (result.projection as JsonObject).nodes as JsonObject[];
const statusEdges = (result: JsonObject): JsonObject[] => (result.projection as JsonObject).edges as JsonObject[];

test("status empty snapshot has no graph, while gate and run evidence survive without a selected Todo", () => {
  assert.deepEqual(statusGraph(), {schema_version: "status_task_graph_result_v0", projection: null});
  const result = statusGraph({summaries: {agent: {}, user: {items: [statusTodo("todo_gate", {task_class: "user_gate"})]}},
    latest_runs: [{run_id: "run_checked", classification: "checked"}]});
  assert.deepEqual(statusNodes(result).map(node => [node.kind, node.state]), [["gate", "waiting"], ["validation", "ready"]]);
  assert.deepEqual(statusEdges(result), []);
  assert.deepEqual((result.projection as JsonObject).limits, {user_gate_node_limit: 2, user_gate_open_count: 1, user_gate_truncated_count: 0});
});

test("status owns selection before sorting and caps gates independently of predecessor expansion", () => {
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_first", {priority: "P4", claimed_by: "builder"}),
    statusTodo("todo_later", {priority: "P0"})]}, user: {open_count: 9,
      gate_open_items: Array.from({length: 5}, (_, i) => statusTodo(`todo_gate_${i}`, {task_class: "user_gate"}))}}});
  assert.deepEqual(statusNodes(result).map(node => node.kind), ["deliverable", "lease", "gate", "gate", "gate_summary"]);
  assert.deepEqual(statusNodes(result)[0].refs, {todo_ids: ["todo_first"]});
  assert.deepEqual(statusEdges(result).map(edge => edge.relation), ["depends_on", "blocks", "blocks", "blocks"]);
  assert.equal(statusNodes(result)[4].title, "7 more open user gates not expanded");
});

test("status retains parallel lineage and condition edges, done evidence and predecessor handoff", () => {
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_root", {resume_when: "todo_done:todo_parent"}),
    statusTodo("todo_parent", {status: "done", done: true, successor_todo_ids: ["todo_root"], evidence: "Checked result",
      handoff_note: {from_agent: "builder", to_agent: "reviewer"}})]}, user: {}}});
  assert.deepEqual(statusNodes(result).map(node => [node.kind, node.state]), [
    ["deliverable", "open"], ["deliverable", "done"], ["evidence", "done"], ["handoff", "done"]]);
  assert.deepEqual(statusEdges(result).map(edge => edge.relation), ["depends_on", "continues", "validates", "hands_off_to", "continues"]);
  assert.deepEqual(statusNodes(result)[2].refs, {todo_ids: ["todo_parent"]});
});

test("status limits expansion at open predecessors, reports missing targets and source truncation", () => {
  const result = statusGraph({summaries: {agent: {total_count: 8, items: [statusTodo("todo_root", {resume_when: "todo_done:todo_missing"}),
    statusTodo("todo_parent", {successor_todo_ids: ["todo_root"], evidence: "Not delivered"}),
    statusTodo("todo_ancestor", {status: "done", successor_todo_ids: ["todo_parent"]})]}, user: {}}});
  assert.deepEqual(statusNodes(result).map(node => node.refs), [{todo_ids: ["todo_root"]}, {todo_ids: ["todo_parent"]}]);
  assert.deepEqual((result.projection as JsonObject).limits, {user_gate_node_limit: 2, user_gate_open_count: 0, user_gate_truncated_count: 0,
    predecessor_node_limit: 4, emitted_predecessor_count: 1, predecessor_truncated: false, source_truncated: true,
    missing_predecessor_count: 1, topology_complete: false});
});

test("status run node and lineage independently select first useful facts, repair remains display only", () => {
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_root")]}, user: {}},
    latest_runs: [{run_id: "run_identity_only"}, {classification: "audit continued", generated_at: "2026-11-01T00:00:00Z"}],
    warning_updated_at: "2026-11-02T00:00:00Z", state_updated_at: "2026-10-31T00:00:00Z",
    replan: {kind: "synthetic_repair", recommended_action: "Recover selected work"}});
  assert.deepEqual(statusEdges(result).map(edge => edge.relation), ["validates", "audits", "continues", "repairs"]);
  const projection = result.projection as JsonObject;
  assert.equal(projection.generated_at, "2026-11-01T00:00:00Z");
  assert.equal((projection.derived_from as JsonObject).active_state_updated_at, "2026-11-02T00:00:00Z");
  assert.equal((projection.truth_contract as JsonObject).write_api, false);
});

test("status deduplicates display identities and honors codec rejection without selecting a replacement", () => {
  const gate = statusTodo("todo_gate", {task_class: "user_gate"});
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_root"), statusTodo("todo_other")]},
    user: {items: [{...gate, index: 1}, {...gate, index: 2}]}},
    public_texts: {"Review todo_root": {"160": null}}});
  assert.deepEqual(statusNodes(result).map(node => node.kind), ["gate"]);
  assert.deepEqual(statusEdges(result), []);
});

test("status refuses malformed consumed facts and has no native fallback", () => {
  for (const extra of [{schema_version: "unknown"}, {goal_id: ""}, {summaries: []},
    {latest_runs: {}}, {max_status_todos_per_role: 0}, {public_texts: []}]) assert.throws(() => statusGraph(extra));
});

test("status gate order preserves typed legacy coordinates and Python whitespace status", () => {
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_root", {status: "\ufeffdone"})]},
    user: {items: [statusTodo("todo_gate_b", {schema_version: "todo_item_v0", index: 2, task_class: "user_gate"}),
      statusTodo("todo_gate_a", {schema_version: "todo_item_v0", index: 1, task_class: "user_gate"})]}}});
  assert.equal(statusNodes(result)[0].state, "open");
  assert.deepEqual(statusNodes(result).slice(1).map(node => node.refs), [{gate_ids: ["todo_gate_a"]}, {gate_ids: ["todo_gate_b"]}]);
});

test("retired topology RPC cannot recreate a Python-led status composition", async () => {
  const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import("../../loopx/control_plane/effect_runtime_handlers.ts");
  const handlers = createEffectRuntimeHandlers({fingerprint: "status-graph-test", requestShutdown: () => undefined});
  await assert.rejects(dispatchEffectRuntimeMethod(handlers, "work_item.task_graph.topology", {}), {code: "unsupported_method"});
  const result = await dispatchEffectRuntimeMethod(handlers, "work_item.task_graph.status", {
    schema_version: STATUS_TASK_GRAPH_REQUEST, goal_id: "synthetic-goal", summaries: {agent: {}, user: {}},
    max_status_todos_per_role: 8, public_texts: {}, latest_runs: [], fallback_time: "2026-11-02T12:00:00Z"});
  assert.deepEqual(result, {schema_version: "status_task_graph_result_v0", projection: null});
});

test("status deduplicates actual legacy hashes, including evidence identities truncated before hashing", () => {
  const long = "todo_" + "a".repeat(130);
  const result = statusGraph({summaries: {agent: {items: [statusTodo("todo_root"),
    statusTodo(long + "x", {status: "done", successor_todo_ids: ["todo_root"], evidence: "First evidence"}),
    statusTodo(long + "y", {status: "done", successor_todo_ids: ["todo_root"], evidence: "Later evidence"})]}, user: {}}});
  const nodes = statusNodes(result), edges = statusEdges(result);
  assert.deepEqual(nodes.map(node => node.kind), ["deliverable", "deliverable", "evidence"]);
  assert.equal(nodes[2].title, "First evidence");
  assert.equal(new Set(nodes.map(node => node.node_id)).size, nodes.length);
  assert.equal(new Set(edges.map(edge => edge.edge_id)).size, edges.length);
});


test("status compaction fallback refuses unqualified source text", () => {
  assert.throws(() => projectStatusTaskGraph({schema_version: STATUS_TASK_GRAPH_REQUEST,
    goal_id: "synthetic-goal", summaries: {agent: {items: [statusTodo("todo_unqualified")]}, user: {}},
    max_status_todos_per_role: 8, latest_runs: [], public_texts: {},
    fallback_time: "2026-11-02T12:00:00Z"}), /Missing public text facts/);
});

test("status compaction fallback enumerates composed titles, identities, references and reasons", () => {
  const result = statusGraph({summaries: {
    agent: {items: [statusTodo("todo_root", {claimed_by: "builder"}),
      statusTodo("todo_parent", {status: "done", successor_todo_ids: ["todo_root"],
        evidence: "Checked public evidence", handoff_note: {from_agent: "builder", to_agent: "reviewer"}})]},
    user: {open_count: 6, items: [{text: "Review gate", task_class: "user_gate"}]},
  }, latest_runs: [{run_id: "run_checked", classification: "audit continued"}], replan: {}});
  assert.deepEqual(statusNodes(result).map(node => [node.kind, node.title]), [
    ["deliverable", "Review todo_root"], ["lease", "Claimed by builder"],
    ["deliverable", "Review todo_parent"], ["evidence", "Checked public evidence"],
    ["handoff", "Handoff from builder to reviewer"], ["gate", "Review gate"],
    ["gate_summary", "5 more open user gates not expanded"],
    ["validation", "Latest compact run: audit continued"], ["repair", "autonomous_replan_obligation"],
  ]);
  assert.deepEqual(statusEdges(result).map(edge => edge.relation), [
    "depends_on", "continues", "validates", "hands_off_to", "continues", "blocks", "blocks",
    "validates", "audits", "continues", "repairs",
  ]);
  assert.deepEqual(statusNodes(result)[5].refs, {gate_ids: ["gate:synthetic-goal:user:1"]});
  for (const edge of statusEdges(result)) {
    assert.equal(typeof edge.reason, "string");
    assert.match(String(edge.edge_id), /^edge_[a-z0-9_]+$/);
  }
});

for (const [fromSource, toSource, expectedTitle] of [
  ["Builder", "Reviewer", "Handoff from builder to reviewer"],
  [" Builder ", " Reviewer ", "Handoff from builder to reviewer"],
  [" Builder\tTeam ", " Reviewer\u00a0Team ", "Handoff from builder-team to reviewer-team"],
  ["\u001cBuilder\u0085", "\u001fReviewer\u001f", "Handoff from builder to reviewer"],
]) {
  test(`status normalizes approved handoff identities: ${JSON.stringify(fromSource)}`, () => {
    const values = {
      "synthetic-goal": {"120": "synthetic-goal", "80": "synthetic-goal"},
      "todo_root": {"120": "todo_root"}, "Review todo_root": {"160": "Review todo_root"},
      "open": {"80": "open"}, "todo_done:todo_parent": {"240": "todo_done:todo_parent"},
      "todo_parent": {"120": "todo_parent"}, "Review todo_parent": {"160": "Review todo_parent"},
      "done": {"80": "done"},
      [fromSource]: {"80": compactTodoReadText(fromSource, 80)},
      [toSource]: {"80": compactTodoReadText(toSource, 80)},
    };
    const result = projectStatusTaskGraph({
      schema_version: STATUS_TASK_GRAPH_REQUEST, goal_id: "synthetic-goal",
      summaries: {agent: {items: [
        statusTodo("todo_root", {resume_when: "todo_done:todo_parent"}),
        statusTodo("todo_parent", {status: "done", done: true, successor_todo_ids: ["todo_root"],
          handoff_note: {from_agent: fromSource, to_agent: toSource, status: "done"}}),
      ]}, user: {}},
      max_status_todos_per_role: 8, latest_runs: [], public_texts: values,
      fallback_time: "2026-11-02T12:00:00Z",
    });
    assert.equal(statusNodes(result).find(node => node.kind === "handoff")?.title,
      expectedTitle);
  });
}

for (const rejected of [null, ""]) {
  test(`status refuses normalized handoff identities with rejected source text: ${rejected}`, () => {
    assert.throws(() => statusGraph({summaries: {agent: {items: [
      statusTodo("todo_root", {resume_when: "todo_done:todo_parent"}),
      statusTodo("todo_parent", {status: "done", done: true, successor_todo_ids: ["todo_root"],
        handoff_note: {from_agent: "Builder", to_agent: "Reviewer", status: "done"}}),
    ]}, user: {}}, public_texts: {"Builder": {"80": rejected}}}), /Missing public text facts/);
  });
}
