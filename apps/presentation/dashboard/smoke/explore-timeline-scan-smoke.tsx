import assert from "node:assert/strict";
import {renderToStaticMarkup} from "react-dom/server";
import {GoalResearchTimeline} from "../src/features/personal-workspace/goal-research-timeline";
import {parseExploreHistoryPage, projectExploreHistory} from "../../../../loopx/control_plane/presentation/explore_history";
import {buildExploreTimeline} from "../../../../loopx/control_plane/presentation/explore_timeline";

const goalId = "timeline-scan-fixture";
const events: Record<string, unknown>[] = [];
for (let index = 0; index < 40; index++) {
  const parent = `parent-${index}`, child = `child-${index}`;
  const event = (kind: string, id: string, extra: Record<string, unknown>) => ({
    schema_version: "loopx_explore_result_event_v0", goal_id: goalId, event_kind: kind,
    event_id: `${kind}-${id}`, result_id: id, title: id, recorded_at: "2026-01-01T00:00:00Z", ...extra,
  });
  if (index === 0) {
    events.push(event("node", "child-later", {parent_id: parent, recorded_at: "2026-01-01T02:00:00Z"}));
    events.push(event("node", "child-unknown", {parent_id: parent, recorded_at: "unknown"}));
  }
  events.push(event("node", child, {parent_id: parent, recorded_at: "2026-01-01T01:00:00Z"}));
  events.push(event("node", parent, {}));
  events.push(event("edge", `edge-${index}`, {from_node: parent, to_node: child, edge_type: "supports", summary: `route-edge-${index}`}));
  if (index === 0) {
    events.push(event("edge", "back", {from_node: child, to_node: parent, edge_type: "refutes", summary: "route-back"}));
    events.push(event("edge", "unknown", {from_node: parent, to_node: "child-unknown", edge_type: "supports", summary: "unknown-time-edge"}));
    events.push(event("edge", "missing", {from_node: parent, to_node: "missing-node", edge_type: "supports", summary: "missing-node-edge"}));
  }
}
const parsed = parseExploreHistoryPage({ok: true, goal_id: goalId, total: events.length, next_cursor: null, items: events}, goalId).items;
const history = projectExploreHistory(parsed, goalId);
const timeline = buildExploreTimeline(history.nodes, history.edges, history.events);
// Count source accesses instead of depending on a machine-specific time limit.
let routeReads = 0, edgeReads = 0;
const nodes = new Map([...timeline.nodes].map(([id, item]) => [id, {
  ...item, get routeId() {routeReads++; return item.routeId;},
}]));
const edges = timeline.edges.map(edge => ({...edge, get from_node() {edgeReads++; return edge.from_node;}}));
const html = renderToStaticMarkup(<GoalResearchTimeline timeline={{...timeline, nodes, edges}} complete zh selectedIds={[]}
  phase={null} selectedDay={null} dayGroups={[]} onGroupSelect={() => {}} onSelect={() => {}} onSelectMany={() => {}}
  onPhase={() => {}} timeLabel={value => new Date(value).toISOString()} dayLabel={value => new Date(value).toISOString().slice(0, 10)}/>);
const routes = [...html.matchAll(/<svg[^>]*class="research-history-edges"[^>]*>(.*?)<\/svg>/gs)].map(match => match[1]!);
assert.equal(routes.length, 40);
for (const [index, route] of routes.entries()) {
  const titles = [...route.matchAll(/<title>(.*?)<\/title>/g)].map(match => match[1]);
  assert.deepEqual(titles, index ? [`supports: route-edge-${index}`] : ["supports: route-edge-0", "refutes: route-back"]);
}
const firstParents = [...routes[0]!.matchAll(/class="is-grouping" d="([^"]+)"/g)].map(match => match[1]!);
assert.equal(firstParents.length, 2);
const endpoint = (path: string) => Number(path.match(/ ([\d.]+),26$/)?.[1]);
assert.ok(endpoint(firstParents[0]!) > endpoint(firstParents[1]!), "parent arcs keep source order, not timestamp order");
assert.doesNotMatch(html, /unknown-time-edge|missing-node-edge/);
assert.ok(routeReads <= 4 * (nodes.size + edges.length), `nodes were repeatedly scanned: ${routeReads}`);
assert.ok(edgeReads <= 4 * edges.length, `edges were repeatedly scanned: ${edgeReads}`);
console.log(`explore timeline: 40 routes retained; ${routeReads} node and ${edgeReads} edge identity reads`);
