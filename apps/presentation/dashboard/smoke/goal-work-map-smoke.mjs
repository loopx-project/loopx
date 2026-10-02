import assert from "node:assert/strict";
import { deliveryReviewMarkdown, parseDeliveryReview } from "../node_modules/.cache/delivery-review/data/delivery-review.js";
import { goalWorkMapCoverage, goalWorkMapLayout, goalWorkMapLineage, goalWorkMapSharedOwner, goalWorkMapSummary, goalWorkMapTone } from "../node_modules/.cache/delivery-review/data/goal-work-map.js";
import { deliveryReviewCopy } from "../node_modules/.cache/delivery-review/features/personal-workspace/delivery-review-copy.js";

const node = (id, kind, state, depth) => ({ node_id: id, kind, title: `Title ${id}`, state, depth, refs: { todo_ids: [`todo_${id}`] } });
const edge = (from, to, relation = "depends_on", enforcement = "typed_lifecycle") =>
  ({ edge_id: `${from}_${to}_${relation}`, from_node_id: from, to_node_id: to, relation, enforcement, reason: "Recorded relation" });
// A decision gates two blocked tasks; finished history feeds open work through
// two relations between one pair; a watch has no recorded link.
const map = {
  schema_version: "goal_task_map_v0", mode: "read_only", goal_id: "map-demo",
  limits: { node_limit: 120, emitted_node_count: 7, omitted_node_count: 0, source_truncated: false, missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true },
  nodes: [node("gate", "gate", "open", 0), node("reserve", "deliverable", "blocked", 1), node("deposit", "deliverable", "blocked", 2),
    node("scope", "deliverable", "done", 0), node("venues", "deliverable", "done", 1), node("budget", "deliverable", "open", 2),
    node("watch", "monitor", "open", 0)],
  edges: [edge("reserve", "gate"), edge("deposit", "reserve"), edge("venues", "scope", "continues", "lineage_only"),
    edge("budget", "venues", "continues", "lineage_only"), edge("budget", "venues", "depends_on", "typed_condition")],
};
const snapshot = { ok: true, goal_id: "map-demo", observed_at: "2026-09-01T00:00:00Z", graph: null, goal_map: map, acceptance: null };
const parsed = parseDeliveryReview(snapshot, "map-demo").goal_map;
const ids = nodes => nodes.map(item => item.node_id);
const grids = layout => layout.groups.map(group => group.columns.map(ids));

assert.deepEqual(goalWorkMapSummary(parsed), { work: 5, done: 2, blocked: 2, waiting: 0, decisions: 1, watches: 1 });
assert.equal(goalWorkMapTone(node("x", "gate", "done", 0)), "done", "A decided gate no longer asks for a decision");
assert.equal(goalWorkMapTone(node("x", "deliverable", "ready", 0)), "open");

const current = goalWorkMapLayout(parsed, "current");
assert.deepEqual(grids(current), [[["gate"], ["reserve"], ["deposit"]], [["venues"], ["budget"]]],
  "Each chain gets its own grid, the one needing a decision first, with hidden depths compacted");
assert.equal(current.hiddenCount, 1, "Older history beyond direct prerequisites is hidden, not dropped");
assert.deepEqual(ids(current.unlinked), ["watch"]);
assert.equal(current.edges.length, 4, "Parallel relations between one pair survive layout");
const all = goalWorkMapLayout(parsed, "all");
assert.deepEqual(grids(all), [[["gate"], ["reserve"], ["deposit"]], [["scope"], ["venues"], ["budget"]]]);
assert.equal(all.hiddenCount, 0);
const gap = goalWorkMapLayout({ ...parsed, nodes: [node("a", "deliverable", "done", 0), node("b", "deliverable", "done", 1), node("c", "deliverable", "open", 2)],
  edges: [edge("b", "a"), edge("c", "b")] }, "current");
assert.deepEqual(grids(gap), [[["b"], ["c"]]], "Hidden depths leave no empty columns");
const joined = goalWorkMapLayout({ ...parsed, nodes: [node("p", "deliverable", "open", 0), node("q", "deliverable", "open", 0), node("r", "deliverable", "open", 1)],
  edges: [edge("r", "p"), edge("r", "q")] }, "all");
assert.deepEqual(grids(joined), [[["p", "q"], ["r"]]], "A shared dependent keeps its prerequisites in one chain");

const lineage = goalWorkMapLineage(parsed.edges, "reserve");
assert.deepEqual([...lineage.nodes].sort(), ["deposit", "gate", "reserve"], "Lineage follows only recorded links");
assert.equal(lineage.edges.size, 2);
assert.equal(goalWorkMapLineage(parsed.edges, "budget").edges.size, 3, "Transitive prerequisites are traced");
assert.equal(goalWorkMapLineage(parsed.edges, null).nodes.size, 0);

// Deferred work leaves the current view unless active work needs it or an open decision unblocks it.
const parked = goalWorkMapLayout({ ...parsed, nodes: [node("ask", "gate", "open", 0), node("later", "deliverable", "waiting", 1),
  node("run", "deliverable", "open", 1), node("input", "deliverable", "waiting", 0), node("shelf", "deliverable", "waiting", 0)],
  edges: [edge("later", "ask", "depends_on", "typed_condition"), edge("run", "input", "depends_on", "typed_condition")] }, "current");
assert.deepEqual(new Set(parked.groups.flatMap(group => group.columns.flat()).map(item => item.node_id)), new Set(["ask", "later", "run", "input"]));
assert.equal(parked.hiddenCount, 1, "Unrelated deferred work is hidden, not dropped");

// A long finished history behind one step collapses into a count instead of a column.
const history = (count) => goalWorkMapLayout({ ...parsed, nodes: [node("step", "deliverable", "open", 1), node("next", "deliverable", "open", 2),
  ...Array.from({ length: count }, (_, index) => node(`done${index}`, "deliverable", "done", 0))],
  edges: [edge("next", "step", "depends_on", "typed_condition"), ...Array.from({ length: count }, (_, index) => edge("step", `done${index}`))] }, "current");
assert.deepEqual(grids(history(2)), [[["done0", "done1"], ["step"], ["next"]]], "Two finished prerequisites stay as context");
assert.deepEqual(grids(history(3)), [[["step"], ["next"]]]);
assert.equal(history(3).collapsed.get("step"), 3);
assert.equal(history(3).hiddenCount, 3, "Collapsed history is counted, not dropped");
assert.equal(goalWorkMapLayout({ ...parsed, nodes: history(3).groups[0].columns.flat() }, "all").collapsed.size, 0, "The full map collapses nothing");

const limits = parsed.limits;
assert.equal(goalWorkMapCoverage(parsed), "complete");
assert.equal(goalWorkMapCoverage({ ...parsed, limits: { ...limits, missing_endpoint_count: 2, topology_complete: false } }), "partial",
  "Missing endpoints do not prove the target was archived or belongs to another Goal");
for (const change of [{ omitted_node_count: 1 }, { source_truncated: true }, { cycle_edge_count: 1 }]) {
  assert.equal(goalWorkMapCoverage({ ...parsed, limits: { ...limits, missing_endpoint_count: 2, topology_complete: false, ...change } }), "partial");
}
assert.equal(goalWorkMapCoverage({ ...parsed, limits: { ...limits, topology_complete: false } }), "partial", "Unexplained incompleteness stays partial");
const owned = (...owners) => ({ ...parsed, nodes: owners.map((owner, index) => ({ ...node(`n${index}`, "deliverable", "open", 0), ...(owner ? { owner_agent: owner } : {}) })) });
assert.equal(goalWorkMapSharedOwner(owned("solo", "solo", null)), "solo");
assert.equal(goalWorkMapSharedOwner(owned("solo", "pair")), null);
assert.equal(goalWorkMapSharedOwner(owned("solo")), null, "A single owned item keeps its owner on the card");

for (const mutation of [
  { ...map, goal_id: "other-goal" },
  { ...map, nodes: [...map.nodes, map.nodes[0]] },
  { ...map, edges: [...map.edges, map.edges[0]] },
  { ...map, edges: [{ ...map.edges[0], to_node_id: "missing" }] },
  { ...map, edges: [{ ...map.edges[0], relation: "blocks" }] },
  { ...map, nodes: [{ ...map.nodes[0], kind: "evidence" }] },
  { ...map, mode: "writable" },
]) assert.throws(() => parseDeliveryReview({ ...snapshot, goal_map: mutation }, "map-demo"));

for (const copy of Object.values(deliveryReviewCopy)) {
  const markdown = deliveryReviewMarkdown(parseDeliveryReview(snapshot, "map-demo"), copy);
  assert.ok(markdown.includes(`## ${copy.workMap.title}`) && markdown.includes(copy.workMap.boundary));
  assert.ok(markdown.includes('"topology_complete": true'), "Exports keep coverage limits next to the map");
  assert.ok(markdown.includes(`Title budget → ${copy.workMap.relation.depends_on} → Title venues`)
    && markdown.includes(`Title budget → ${copy.workMap.relation.continues} → Title venues`));
  assert.ok(!deliveryReviewMarkdown(parseDeliveryReview({ ...snapshot, goal_map: null }, "map-demo"), copy).includes(`## ${copy.workMap.title}`));
}
console.log("goal work map: summary, focus layout, lineage, coverage, export and negative contracts passed");
