import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {parseExploreHistoryPage, projectExploreHistory, layoutExploreHistory, groupUnlinkedHistoryQuestions} from "../../loopx/control_plane/presentation/explore_history.ts";

const goal = "history-fixture";
const event = (extra: Record<string, unknown> = {}) => parseExploreHistoryPage({ok: true, goal_id: goal,
  total: 1, next_cursor: null, items: [{schema_version: "loopx_explore_result_event_v0", event_id: "e1",
    goal_id: goal, event_kind: "node", result_id: "route", title: "Question", recorded_at: "2026-01-01T00:00:00Z",
    node_kind: "question", status: "open", ...extra}]}, goal).items[0]!;

test("whole history retains old failures, later work and append-order corrections across eighteen hours", () => {
  const start = event();
  const negative = event({event_id: "e2", status: "dead_end", summary: "Input v1 refutes the method", recorded_at: "2026-01-01T04:00:00Z"});
  const later = event({event_id: "e3", result_id: "later", title: "Later attempt", node_kind: "experiment", status: "blocked", recorded_at: "2026-01-01T18:00:00Z"});
  const correction = event({event_id: "e4", status: "exploring", recorded_at: "2026-01-01T03:00:00Z"});
  const view = projectExploreHistory([start, negative, later, correction], goal);
  assert.deepEqual(view.events.map(row => row.event_id), ["e1", "e2", "e3", "e4"]);
  assert.equal(view.nodes[0]!.latest.status, "exploring", "the append owner, not timestamp sorting, selects current state");
  assert.equal(view.nodes[0]!.updates[1]!.summary, "Input v1 refutes the method");
  assert.equal(view.nodes.length, 2);
  assert.deepEqual([view.startAt, view.endAt], ["2026-01-01T00:00:00.000Z", "2026-01-01T18:00:00.000Z"]);
  assert.equal(view.edges.length, 0, "time adjacency must not create an edge");
  const firstFinding = event({event_id: "f1", event_kind: "finding", result_id: "f", node_id: "route", status: "refuted", evidence_refs: ["artifact:input-v1"]});
  const revisedFinding = {...firstFinding, event_id: "f2", status: "tentative", evidence_refs: ["artifact:input-v2"]};
  const revised = projectExploreHistory([start, firstFinding, revisedFinding], goal).nodes[0]!;
  assert.equal(revised.findings.length, 1);
  assert.equal(revised.findings[0]!.status, "tentative");
  assert.equal(revised.revisions[1]!.status, "refuted", "an older negative remains inline after a finding revision");
  assert.deepEqual(revised.revisions[1]!.evidence_refs, ["artifact:input-v1"]);
});

test("parent grouping and 200 nodes do not manufacture supporting evidence or drop the tail", () => {
  const rows = Array.from({length: 200}, (_, index) => event({event_id: `event-${index}`, result_id: `node-${index}`,
    parent_id: index ? `node-${index - 1}` : "", title: `Recorded attempt ${index}`}));
  const view = projectExploreHistory(rows, goal);
  const layout = layoutExploreHistory(view.nodes, view.edges);
  assert.equal(view.nodes.length, 200); assert.equal(layout.length, 200);
  assert.equal(view.edges.length, 0); assert.equal(layout.at(-1)!.node.id, "node-199");
  assert.ok(layout.every(row => Number.isFinite(row.x) && Number.isFinite(row.y)));
  const cyclic = [event({parent_id: "other"}), event({event_id: "e2", result_id: "other", parent_id: "route"})];
  assert.equal(layoutExploreHistory(projectExploreHistory(cyclic, goal).nodes, []).length, 2);
});

test("explicit links and unattached findings remain source records; missing anchors and times stay unknown", () => {
  const rows = [event({recorded_at: "unknown"}),
    event({event_id: "e2", event_kind: "edge", result_id: "relation", from_node: "route", to_node: "unloaded", edge_type: "refutes"}),
    event({event_id: "e3", event_kind: "finding", result_id: "negative", node_id: "unloaded", status: "refuted"})];
  const view = projectExploreHistory(rows, goal);
  assert.equal(view.missingLinks.length, 1); assert.equal(view.unknownTimeCount, 1);
  assert.equal(view.findings[0]!.status, "refuted"); assert.equal(view.events.length, 3);
  assert.equal(view.edges[0]!.edge_type, "refutes");
});

test("replays deduplicate only identical events and reject conflicting identities or wrong-Goal pages", () => {
  const row = event();
  assert.equal(projectExploreHistory([row, {...row}], goal).events.length, 1);
  assert.throws(() => projectExploreHistory([row, {...row, summary: "changed"}], goal), /Conflicting/);
  assert.throws(() => projectExploreHistory([{...row, goal_id: "other"}], goal), /Goal mismatch/);
  assert.throws(() => parseExploreHistoryPage({ok: true, goal_id: "other", items: [], total: 0, next_cursor: null}, goal), /scope/);
  assert.throws(() => parseExploreHistoryPage({ok: true, goal_id: goal, items: [row], total: -1, next_cursor: null}, goal), /coverage/);
  assert.throws(() => event({evidence_refs: [12]}), /evidence/);
  assert.equal(event({confidence: 0}).confidence, 0, "a source's stated zero is retained");
  assert.equal(event().confidence, null, "unrecorded confidence is unknown, not zero");
  assert.throws(() => event({confidence: 2}), /evidence/);
});

test("sparse question capture is grouped without inventing routes or hiding versions and negative evidence", () => {
  const rows = Array.from({length: 120}, (_, index) => event({event_id: `question-${index}`, result_id: `q-${index}`,
    recorded_at: `2026-01-${String(index % 10 + 1).padStart(2, "0")}T12:00:00Z`}));
  rows.push(event({event_id: "negative", event_kind: "finding", result_id: "finding", node_id: "q-119", status: "refuted"}));
  rows.push(event({event_id: "earlier-version", result_id: "q-119", summary: "A later append correcting an earlier version"}));
  rows.push(event({event_id: "source-edge", event_kind: "edge", result_id: "edge", from_node: "q-0", to_node: "q-1", edge_type: "refutes"}));
  const view = projectExploreHistory(rows, goal);
  const display = groupUnlinkedHistoryQuestions(view.nodes, view.edges);
  assert.deepEqual(display.nodes.map(node => node.id), ["q-0", "q-1"], "explicit relationship endpoints stay individual");
  const members = display.groups.flatMap(group => group.nodes);
  assert.equal(members.length, 118); assert.equal(display.groups.length, 10);
  assert.equal(members.find(node => node.id === "q-119")!.updates.length, 2);
  assert.equal(members.find(node => node.id === "q-119")!.findings[0]!.status, "refuted");
  assert.equal(view.edges.length, 1, "date buckets create no source edges or scientific route");
  assert.equal(new Set([...members, ...display.nodes].map(node => node.id)).size, 120);
});

test("time-anchored reading keeps record-time thirds, routes and explicit decisions without causality", async () => {
  const {buildExploreTimeline, parseExploreFindingSections} = await import("../../loopx/control_plane/presentation/explore_timeline.ts");
  const rows = [
    event({event_id: "n1", result_id: "q1", title: "Early question", recorded_at: "2026-01-01T00:00:00Z", status: "resolved"}),
    event({event_id: "n2", result_id: "q2", title: "Mid question", recorded_at: "2026-01-02T00:00:00Z", status: "resolved", parent_id: "q1"}),
    event({event_id: "n3", result_id: "q3", title: "Late question", recorded_at: "2026-01-03T00:00:00Z", status: "open"}),
    event({event_id: "f1", event_kind: "finding", result_id: "finding1", node_id: "q1", status: "confirmed", evidence_refs: ["artifact:v1"]}),
    event({event_id: "f3", event_kind: "finding", result_id: "finding3", node_id: "q3", status: "tentative", recorded_at: "2026-01-03T00:00:00Z", evidence_refs: ["artifact:v3"],
      summary: "Input revision: plan:abc Applicability: exact input Observation: child resource finished Interpretation: Outcome: replan\nretained:\n- original owner\nchanged:\n- child DONE\nstopped:\n- do not rerun the old probe"}),
  ];
  const view = projectExploreHistory(rows, goal);
  const timeline = buildExploreTimeline(view.nodes, view.edges, view.events);
  assert.equal(timeline.bins.length > 0, true);
  assert.deepEqual(timeline.phases.map(phase => phase.nodes), [1, 1, 1]);
  assert.equal(timeline.routes.length, 1, "recorded parent grouping joins a reading route");
  assert.equal(timeline.routes[0]!.nodeIds[0], "q1");
  assert.equal(timeline.singles.length, 1, "time adjacency never joins q3 to the route");
  assert.equal(timeline.decisions.length, 1);
  const decision = timeline.decisions[0]!.sections!.decision!;
  assert.equal(decision.outcome, "replan");
  assert.deepEqual(decision.changed, ["child DONE"]);
  assert.deepEqual(decision.stopped, ["do not rerun the old probe"]);
  const sections = parseExploreFindingSections(rows[4]!.summary)!;
  assert.equal(sections.observation, "child resource finished");
  assert.equal(parseExploreFindingSections("free-form finding text"), null, "unstructured findings stay raw prose");
});

test("phase reading retains successive decisions for one question, including a late backfill", async () => {
  const {buildExploreTimeline, exploreTimelinePhaseAt, selectExploreTimelineDecisions} = await import("../../loopx/control_plane/presentation/explore_timeline.ts");
  const finding = (id: string, at: string, outcome: string) => event({event_id: id, event_kind: "finding", result_id: id, node_id: "route", recorded_at: at, status: "tentative",
    summary: `Input revision: v1 Applicability: this input Observation: measured Interpretation: Outcome: ${outcome} changed: - ${id}`});
  const rows = [event(), finding("early", "2026-01-01T01:00:00Z", "continue"),
    finding("early-stop", "2026-01-01T02:00:00Z", "stop"), finding("late", "2026-01-03T00:00:00Z", "wait"),
    finding("backfill", "2026-01-01T01:30:00Z", "no_change")];
  const view = projectExploreHistory(rows, goal), timeline = buildExploreTimeline(view.nodes, view.edges, view.events);
  assert.deepEqual(selectExploreTimelineDecisions(timeline, 0).map(item => item.event.event_id), ["early-stop", "backfill", "early"]);
  assert.deepEqual(selectExploreTimelineDecisions(timeline, null).map(item => item.sections!.decision!.outcome), ["wait", "stop", "no_change", "continue"]);
  assert.equal(timeline.phases[0]!.lastDecision!.event.event_id, "early-stop");
  assert.equal(exploreTimelinePhaseAt(timeline.phases, timeline.phases[1]!.start), 1, "a boundary belongs to one reading group");
  assert.equal(exploreTimelinePhaseAt(timeline.phases, timeline.end), 2);
  assert.equal(exploreTimelinePhaseAt(timeline.phases, null), null, "missing record time remains unknown");
  assert.equal(view.nodes[0]!.latest.status, "open", "display-time sorting cannot rewrite canonical node state");
});

test("coincident and nearby records remain accessible without overlapping 44px targets", async () => {
  const {buildExploreTimeline, groupExploreTimelinePoints} = await import("../../loopx/control_plane/presentation/explore_timeline.ts");
  const rows = Array.from({length: 25}, (_, index) => event({event_id: `point-${index}`, result_id: `q-${index}`,
    recorded_at: index < 9 ? "2026-01-01T00:00:00Z" : `2026-01-01T${String(index - 8).padStart(2, "0")}:00:00Z`}));
  rows.push(event({event_id: "unknown", result_id: "unknown", recorded_at: "unknown"}));
  const view = projectExploreHistory(rows, goal), timeline = buildExploreTimeline(view.nodes, view.edges, view.events);
  const items = [...timeline.nodes.values()].reverse(), before = JSON.stringify(items);
  for (const width of [0, 1, 100, 300, 720, 1200]) {
    const groups = groupExploreTimelinePoints(items, timeline.start!, timeline.end!, width);
    assert.deepEqual(groups.flatMap(group => group.items.map(item => item.node.id)).sort(), rows.slice(0, 25).map(row => row.result_id).sort());
    assert(groups.every(group => group.items.every(item => item.first !== null)));
    assert(groups[0]!.items.filter(item => item.first === timeline.start).length === 9, "all exactly simultaneous records stay in the selectable group");
    for (let index = 1; index < groups.length; index++) assert((groups[index]!.at - groups[index - 1]!.at) / (timeline.end! - timeline.start!) * width >= 44 - 1e-9);
  }
  assert.equal(JSON.stringify(items), before, "grouping never changes any timestamp, finding or source object");
  assert.equal(view.edges.length, 0, "display proximity creates no source relationship");
  assert.equal(view.unknownTimeCount, 1, "the unknown time remains available outside the axis");
});

test("three unchanged research checkpoints retain their actual findings and missing relationships", async () => {
  const {buildExploreTimeline, groupExploreTimelinePoints} = await import("../../loopx/control_plane/presentation/explore_timeline.ts");
  for (const [name, digest, eventCount, nodeCount, findingCount, refutedCount] of [
    ["edgebench-research-history.json", "b876dd7660e0aab19db717f60e86865494a74b8fa27b42a5a67ee295ef524397", 76, 38, 38, 32],
    ["edgebench-graph-checkpoint-history.json", "b105de360ed7d015ae5d7bf96b545879e12507b1fc97eaeb5fb6fa2c0bbc00ab", 134, 66, 68, 34],
    ["edgebench-ad-checkpoint-history.json", "de04b679e9f382b650e891e5c7dd7146bc690500435c511f107c9d24da4f4efa", 105, 48, 57, 0],
  ] as const) {
    const capture = JSON.parse(readFileSync(new URL(`../../apps/presentation/dashboard/src/data/fixtures/${name}`, import.meta.url), "utf8"));
    const rows = parseExploreHistoryPage({ok: true, goal_id: capture.goal_id, total: capture.events.length, next_cursor: null, items: capture.events}, capture.goal_id).items;
    const view = projectExploreHistory(rows, capture.goal_id), timeline = buildExploreTimeline(view.nodes, view.edges, view.events);
    assert.equal(capture.source_sha256, digest);
    assert.equal(rows.length, eventCount);
    assert.equal(view.nodes.length, nodeCount);
    assert.equal(timeline.findings.length, findingCount);
    assert.equal(timeline.findings.filter(item => item.event.status === "refuted").length, refutedCount);
    assert.deepEqual(timeline.routes, []);
    assert.equal(timeline.singles.length, nodeCount);
    for (const width of [90, 300, 700, 1200]) {
      const members = [...timeline.nodes.values()];
      const groups = groupExploreTimelinePoints(members, timeline.start!, timeline.end!, width);
      assert.deepEqual(groups.flatMap(group => group.items.map(item => item.node.id)).sort(), members.map(item => item.node.id).sort());
      for (let index = 1; index < groups.length; index++) assert((groups[index]!.at - groups[index - 1]!.at) / (timeline.end! - timeline.start!) * width >= 44 - 1e-9);
    }
    if (name === "edgebench-research-history.json") {
      const negative = timeline.nodes.get("question_97e36b6161239743d643c33214e574370f75f5e9b470341790869ed4c4800a83")!;
      assert.equal(negative.label, "refuted");
      assert.match(negative.node.findings[0]!.summary, /daily trials1\.53-2\.99\/199-719%/);
      assert.deepEqual(negative.node.findings[0]!.evidence_refs, ["evidence:opportunity-cost-gates"]);
    }
    assert.equal(view.edges.length, 0, "the real solver recorded no typed relationships; chronology cannot supply them");
  }
});
