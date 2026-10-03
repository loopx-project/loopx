import assert from "node:assert/strict";
import { deliveryReviewMarkdown, filterReviewNodes, parseDeliveryReview, reviewCoverageIncomplete } from "../node_modules/.cache/delivery-review/data/delivery-review.js";
import { deliveryReviewCopy } from "../node_modules/.cache/delivery-review/features/personal-workspace/delivery-review-copy.js";

const snapshot = {
  ok: true, goal_id: "review-demo", observed_at: "2026-09-01T00:00:00Z", acceptance: null,
  graph: {
    schema_version: "task_graph_projection_v0", mode: "read_only", goal_id: "review-demo", generated_at: null,
    truth_contract: { projection_is_writable: false, write_api: false },
    limits: { user_gate_node_limit: 2, user_gate_open_count: 3, user_gate_truncated_count: 1, missing_predecessor_count: 1, topology_complete: false },
    nodes: [
      { node_id: "current", kind: "deliverable", title: "Implement package", state: "open", owner_agent: "builder", refs: { todo_ids: ["todo_current"] } },
      { node_id: "prior", kind: "deliverable", title: "Acceptance design", state: "done", refs: { todo_ids: ["todo_prior"] } },
      { node_id: "gate", kind: "gate", title: "Review publication", state: "waiting", refs: { gate_ids: ["todo_gate"] } },
      { node_id: "proof", kind: "evidence", title: "Verifier report", state: "unknown", refs: { run_ids: ["run_review"] } },
    ],
    edges: [
      { edge_id: "dependency", from_node_id: "current", to_node_id: "prior", relation: "depends_on", reason: "Requires its predecessor" },
      { edge_id: "lineage", from_node_id: "current", to_node_id: "prior", relation: "continues", reason: "Explicit successor lineage" },
      { edge_id: "blocking", from_node_id: "gate", to_node_id: "current", relation: "blocks", reason: "Pending scoped decision" },
      { edge_id: "audit", from_node_id: "proof", to_node_id: "prior", relation: "audits", reason: "Independent evidence" },
    ],
  },
};
const before = JSON.stringify(snapshot);
const parsed = parseDeliveryReview(snapshot, "review-demo");
const graph = parsed.graph;
assert.equal(graph.edges.length, 4, "Parallel lineage and dependency edges must survive");
assert.equal(reviewCoverageIncomplete(graph), true);
assert.equal(reviewCoverageIncomplete({ ...graph, limits: { ...graph.limits, missing_predecessor_count: 0, user_gate_truncated_count: 0, topology_complete: true } }), false);
assert.equal(reviewCoverageIncomplete({ ...graph, limits: { ...graph.limits, missing_predecessor_count: 0, user_gate_truncated_count: 0, topology_complete: undefined } }), true, "Unknown coverage cannot imply completeness");
assert.deepEqual(filterReviewNodes(graph, "BUILDER", "all", null).map(node => node.node_id), ["current"]);
assert.deepEqual(filterReviewNodes(graph, "todo_prior", "all", null).map(node => node.node_id), ["prior"]);
assert.deepEqual(filterReviewNodes(graph, "", "conditions", null).map(node => node.node_id), ["gate"]);
assert.deepEqual(filterReviewNodes(graph, "", "related", "current").map(node => node.node_id), ["current", "prior", "gate"]);
assert.deepEqual(filterReviewNodes(graph, "", "related", "proof").map(node => node.node_id), ["prior", "proof"], "Focus is direct adjacency, not a readiness transitive closure");
for (const copy of Object.values(deliveryReviewCopy)) {
  const markdown = deliveryReviewMarkdown(parsed, copy);
  assert.ok(markdown.includes(copy.incomplete) && markdown.includes(copy.scope) && markdown.includes(copy.acceptanceBoundary));
  assert.ok(markdown.includes("Verifier report"), "Export must retain nodes hidden by search/focus");
  assert.ok(markdown.includes(copy.relation.continues) && markdown.includes(copy.relation.depends_on));
  assert.ok(markdown.includes('"missing_predecessor_count": 1'));
}
assert.equal(JSON.stringify(snapshot), before, "Read view must not mutate source truth");
assert.throws(() => parseDeliveryReview(snapshot, "other-goal"));
for (const mutation of [
  { ...graph, goal_id: "other-goal" },
  { ...graph, nodes: [...graph.nodes, graph.nodes[0]] },
  { ...graph, edges: [...graph.edges, graph.edges[0]] },
  { ...graph, edges: [{ ...graph.edges[0], to_node_id: "missing" }] },
  { ...graph, truth_contract: { projection_is_writable: true, write_api: true } },
  { ...graph, nodes: [{ ...graph.nodes[0], state: "accepted" }] },
]) assert.throws(() => parseDeliveryReview({ ...snapshot, graph: mutation }, "review-demo"));
assert.equal(parseDeliveryReview({ ...snapshot, graph: null }, "review-demo").graph, null);
const unavailable = {
  schema_version: "goal_acceptance_observation_projection_v0", goal_id: "review-demo",
  read_only: true, acceptance_assessed: false, coverage: "unavailable", missing_sources: ["agent_vision"],
  truncated: false, historical_progress: [], acceptance_gaps: [], guards: [], next_action: null, next_action_source: null,
};
assert.throws(() => parseDeliveryReview({ ...snapshot, acceptance: { ...unavailable, goal_id: "other-goal" } }, "review-demo"));
for (const copy of Object.values(deliveryReviewCopy)) {
  const markdown = deliveryReviewMarkdown(parseDeliveryReview({ ...snapshot, acceptance: unavailable }, "review-demo"), copy);
  assert.ok(markdown.includes(`${copy.required}: ${copy.unavailable}`), "Unavailable evidence must not export as zero gaps");
  assert.ok(markdown.includes(`${copy.guards}: ${copy.unavailable}`), "Unavailable decisions must not export as zero pending");
}
console.log("delivery review: identity, scope, relationships, partial coverage, filtering, export and negative contracts passed");
await import("./goal-work-map-smoke.mjs");
await import("./goal-acceptance-contract-smoke.mjs");
