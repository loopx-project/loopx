import {createHash} from "node:crypto";
import {mkdirSync, readFileSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {performance} from "node:perf_hooks";
import {renderToStaticMarkup} from "react-dom/server";
import {GoalResearchTimeline} from "../src/features/personal-workspace/goal-research-timeline";
import {groupUnlinkedHistoryQuestions, parseExploreHistoryPage, projectExploreHistory} from "../../../../loopx/control_plane/presentation/explore_history";
import {buildExploreTimeline} from "../../../../loopx/control_plane/presentation/explore_timeline";

const [fixturePath, outputDir] = process.argv.slice(2);
if (!fixturePath || !outputDir) throw new Error("Use --make-fixture <new-file> or <fixture-file> <new-output-directory>");
if (fixturePath === "--make-fixture") {
  const real = JSON.parse(readFileSync("src/data/fixtures/edgebench-graph-checkpoint-history.json", "utf8"));
  const many: Record<string, unknown>[] = [], single: Record<string, unknown>[] = [];
  const start = Date.UTC(2026, 0, 1);
  const stamp = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");
  for (let index = 0; index < 1200; index++) {
    const goal = "synthetic-timeline", parent = `route-${index}-parent`, child = `route-${index}-child`;
    const when = start + index * 120_000;
    const event = (kind: string, id: string, extra: Record<string, unknown> = {}) => ({
      schema_version: "loopx_explore_result_event_v0", event_id: `${kind}-${id}`, goal_id: goal,
      event_kind: kind, result_id: id, recorded_at: stamp(when), title: `${kind} ${id}`, ...extra,
    });
    many.push(event("node", child, {parent_id: parent, recorded_at: index % 100 === 0 ? "unknown" : stamp(when + 3_600_000), status: "open"}));
    many.push(event("node", parent, {status: "exploring"}));
    many.push(event("edge", `relation-${index}`, {from_node: parent, to_node: child,
      edge_type: index % 3 === 0 ? "refutes" : "supports", summary: "Recorded relationship"}));
    if (index % 25 === 0) many.push(event("edge", `dangling-${index}`, {from_node: parent, to_node: `missing-${index}`, edge_type: "supports"}));
    if (index % 5 === 0) many.push(event("finding", `finding-${index}`, {node_id: parent,
      status: index % 2 === 0 ? "refuted" : "confirmed", summary: "Recorded finding", evidence_refs: [`evidence:fixture-${index}`]}));
  }
  for (let index = 0; index < 800; index++) {
    const goal = "synthetic-one-route", when = stamp(start + index * 300_000);
    single.push({schema_version: "loopx_explore_result_event_v0", event_id: `node-${index}`, goal_id: goal,
      event_kind: "node", result_id: `node-${index}`, recorded_at: when, title: `Node ${index}`,
      parent_id: index ? `node-${index - 1}` : "", status: "open"});
    if (index) single.push({schema_version: "loopx_explore_result_event_v0", event_id: `edge-${index}`, goal_id: goal,
      event_kind: "edge", result_id: `edge-${index}`, recorded_at: when, title: `Edge ${index}`,
      from_node: `node-${index - 1}`, to_node: `node-${index}`, edge_type: "supports", summary: "Recorded relationship"});
  }
  const fixture = [{name: "public-checkpoint", goal_id: real.goal_id, events: real.events},
    {name: "synthetic-many-routes", goal_id: "synthetic-timeline", events: many},
    {name: "synthetic-one-route", goal_id: "synthetic-one-route", events: single}];
  const bytes = Buffer.from(JSON.stringify(fixture));
  writeFileSync(outputDir, bytes, {flag: "wx"});
  console.log(JSON.stringify({fixture_sha256: createHash("sha256").update(bytes).digest("hex"), cases: fixture.map(row => ({name: row.name, events: row.events.length}))}));
  process.exit(0);
}
const fixtureBytes = readFileSync(fixturePath);
const fixtures = JSON.parse(fixtureBytes.toString()) as Array<{name: string; goal_id: string; events: unknown[]}>;
mkdirSync(outputDir);
const cases = [];
for (const fixture of fixtures) {
  const events = parseExploreHistoryPage({ok: true, goal_id: fixture.goal_id, total: fixture.events.length,
    next_cursor: null, items: fixture.events}, fixture.goal_id).items;
  const history = projectExploreHistory(events, fixture.goal_id);
  const timeline = buildExploreTimeline(history.nodes, history.edges, history.events);
  const dayGroups = groupUnlinkedHistoryQuestions(history.nodes, history.edges).groups;
  const render = (zh: boolean, phase: number | null, complete: boolean) => renderToStaticMarkup(
    <GoalResearchTimeline timeline={timeline} complete={complete} zh={zh} selectedIds={[]} phase={phase}
      selectedDay={null} dayGroups={dayGroups} onGroupSelect={() => {}} onSelect={() => {}}
      onSelectMany={() => {}} onPhase={() => {}} timeLabel={value => new Date(value).toISOString()}
      dayLabel={value => new Date(value).toISOString().slice(0, 10)}/>,
  );
  const snapshots = [];
  for (const [name, zh, phase, complete] of [["zh-all", true, null, true], ["en-phase", false, 1, true], ["zh-partial", true, null, false]] as const) {
    const html = render(zh, phase, complete);
    writeFileSync(join(outputDir, `${fixture.name}-${name}.html`), html);
    snapshots.push({name, bytes: Buffer.byteLength(html), sha256: createHash("sha256").update(html).digest("hex")});
  }
  for (let index = 0; index < 3; index++) render(true, null, true);
  const samples = [];
  for (let index = 0; index < 12; index++) {
    const start = performance.now();
    const html = render(true, null, true);
    samples.push(performance.now() - start);
    if (createHash("sha256").update(html).digest("hex") !== snapshots[0]!.sha256) throw new Error("Repeated render changed output");
  }
  const sorted = [...samples].sort((a, b) => a - b);
  cases.push({name: fixture.name, events: events.length, nodes: timeline.nodes.size, edges: timeline.edges.length,
    routes: timeline.routes.length, snapshots, samples_ms: samples, median_ms: (sorted[5]! + sorted[6]!) / 2});
}
const report = {node: process.version, node_env: process.env.NODE_ENV, fixture_sha256: createHash("sha256").update(fixtureBytes).digest("hex"), cases};
writeFileSync(join(outputDir, "report.json"), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
