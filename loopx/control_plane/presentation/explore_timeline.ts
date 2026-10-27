/** Time-anchored reading model over recorded Explore history.
 *
 * Display projection only. Bins, thirds and routes organize recorded events
 * for reading; they never become an evaluation window, a progress score or a
 * causal relation. `recorded_at` is record time, not attempt time.
 */
import type {ExploreHistoryEvent, ExploreHistoryNode} from "./explore_history.ts";

/** Sections written by Explore result writeback (explore_result_writeback.ts /
 * result_writeback.py). Reading that owner's labels is format decoding, not a
 * prose heuristic; any other summary stays raw. */
const SECTION_LABELS = ["Input revision", "Applicability", "Observation", "Interpretation"] as const;
const DECISION_LISTS = ["retained", "changed", "stopped"] as const;
export type ExploreDecision = {outcome: string; retained: string[]; changed: string[]; stopped: string[]};
export type ExploreFindingSections = {inputRevision: string; applicability: string; observation: string; interpretation: string; decision: ExploreDecision | null};

export function parseExploreFindingSections(summary: string): ExploreFindingSections | null {
  const text = summary.replace(/\s+/g, " ").trim();
  const values: string[] = [];
  let cursor = 0;
  for (const [index, label] of SECTION_LABELS.entries()) {
    const marker = `${label}: `;
    const start = index === 0 ? (text.startsWith(marker) ? 0 : -1) : text.indexOf(` ${marker}`, cursor);
    if (start < 0) return null;
    if (index) values[index - 1] = text.slice(cursor, start).trim();
    cursor = start + (index ? 1 : 0) + marker.length;
  }
  values[3] = text.slice(cursor).trim();
  return {inputRevision: values[0]!, applicability: values[1]!, observation: values[2]!, interpretation: values[3]!, decision: parseDecision(values[3]!)};
}

/** Explore path_delta writes `Outcome: <outcome>` then `retained:/changed:/stopped:` lists. */
function parseDecision(interpretation: string): ExploreDecision | null {
  const head = /^Outcome: ([a-z_]+)(?: |$)/.exec(interpretation);
  if (!head) return null;
  const decision: ExploreDecision = {outcome: head[1]!, retained: [], changed: [], stopped: []};
  const body = interpretation.slice(head[0].length);
  const marks = DECISION_LISTS.map(key => {
    const marker = `${key}: `;
    const at = body.startsWith(marker) ? 0 : body.indexOf(` ${marker}`);
    return {key, at, offset: at === 0 ? marker.length : marker.length + 1};
  }).filter(mark => mark.at >= 0).sort((a, b) => a.at - b.at);
  marks.forEach((mark, index) => {
    const end = marks[index + 1]?.at ?? body.length;
    const raw = body.slice(mark.at + mark.offset, end);
    decision[mark.key] = raw.split(/(?:^| )- /).map(item => item.trim()).filter(Boolean);
  });
  return decision;
}

export type TimelineFinding = {event: ExploreHistoryEvent; at: number | null; sections: ExploreFindingSections | null};
export type TimelineBin = {start: number; end: number; events: number; nodes: number; labels: Record<string, number>; decisions: Record<string, number>};
export type TimelinePhase = {index: number; start: number; end: number; nodes: number; findings: number; labels: Record<string, number>; decisions: Record<string, number>; lastDecision: TimelineFinding | null};
export type TimelineRoute = {id: string; nodeIds: string[]; first: number | null; last: number | null};
export type TimelineNode = {node: ExploreHistoryNode; first: number | null; last: number | null; label: string; routeId: string | null};
export type ExploreTimeline = {
  start: number | null; end: number | null;
  bins: TimelineBin[]; phases: TimelinePhase[];
  nodes: Map<string, TimelineNode>; routes: TimelineRoute[]; singles: string[]; edges: readonly ExploreHistoryEvent[];
  findings: TimelineFinding[]; decisions: TimelineFinding[]; structuredCount: number;
};

/** Combine colliding touch targets, never source records or relationships.
 * At the supplied plot width every displayed centre is at least 44px apart.
 * A group opens all members with their original timestamps and evidence. Its
 * mean record time positions the group only; it is not a new event timestamp. */
export function groupExploreTimelinePoints(items: readonly TimelineNode[], start: number, end: number, width: number): {items: TimelineNode[]; at: number}[] {
  const known = items.filter(item => item.first !== null).sort((a, b) => a.first! - b.first! || a.node.id.localeCompare(b.node.id));
  const groups: {items: TimelineNode[]; at: number}[] = [];
  const pixels = Number.isFinite(width) ? Math.max(0, width) : 0;
  const span = Math.max(1, end - start);
  for (const item of known) {
    const previous = groups.at(-1);
    if (previous && (item.first! - previous.at) / span * pixels < 44) {
      previous.at += (item.first! - previous.at) / (previous.items.length + 1);
      previous.items.push(item);
    } else groups.push({items: [item], at: item.first!});
  }
  return groups;
}

const time = (value: string) => { const at = Date.parse(value); return Number.isFinite(at) ? at : null; };
const bump = (counts: Record<string, number>, key: string) => { counts[key] = (counts[key] ?? 0) + 1; };

/** Half-open record-time groups agree with the phase counts; the last includes its end. */
export function exploreTimelinePhaseAt(phases: readonly TimelinePhase[], at: number | null): number | null {
  if (at === null) return null;
  const index = phases.findIndex((phase, index) => at >= phase.start && (at < phase.end || (index === phases.length - 1 && at <= phase.end)));
  return index < 0 ? null : index;
}

/** Keep every recorded decision, including repeated attempts and historical backfills. */
export function selectExploreTimelineDecisions(timeline: ExploreTimeline, phase: number | null): TimelineFinding[] {
  return [...timeline.decisions].reverse()
    .filter(item => phase === null || exploreTimelinePhaseAt(timeline.phases, item.at) === phase)
    .sort((a, b) => (b.at ?? -Infinity) - (a.at ?? -Infinity));
}

/** Question containment only: matching titles never establish a shared decision.
 * Filter before grouping so counts and records describe the selected time range.
 * Preserve every event, including revisions, ties and unknown record times. */
export function groupExploreTimelineDecisions(timeline: ExploreTimeline, phase: number | null) {
  const groups = new Map<string, {id: string; title: string; items: TimelineFinding[]}>();
  for (const item of selectExploreTimelineDecisions(timeline, phase)) {
    const nodeId = item.event.node_id;
    const id = nodeId ? `node:${nodeId}` : `event:${item.event.event_id}`;
    let group = groups.get(id);
    if (!group) {
      group = {id, title: timeline.nodes.get(nodeId)?.node.latest.title || item.event.title, items: []};
      groups.set(id, group);
    }
    group.items.push(item);
  }
  return [...groups.values()];
}

/** Routes are connected components of recorded typed edges and parent grouping only. */
function routesOf(nodes: readonly ExploreHistoryNode[], edges: readonly ExploreHistoryEvent[]) {
  const known = new Set(nodes.map(node => node.id));
  const parent = new Map<string, string>();
  const find = (id: string): string => { let root = id; while (parent.get(root) && parent.get(root) !== root) root = parent.get(root)!; return root; };
  const join = (a: string, b: string) => { if (known.has(a) && known.has(b)) { const ra = find(a), rb = find(b); if (ra !== rb) parent.set(ra, rb); } };
  for (const edge of edges) join(edge.from_node, edge.to_node);
  for (const node of nodes) if (node.latest.parent_id) join(node.id, node.latest.parent_id);
  const groups = new Map<string, string[]>();
  for (const node of nodes) { const root = find(node.id); groups.set(root, [...(groups.get(root) ?? []), node.id]); }
  return [...groups.values()];
}

export function buildExploreTimeline(nodes: readonly ExploreHistoryNode[], edges: readonly ExploreHistoryEvent[], events: readonly ExploreHistoryEvent[], {targetBins = 36} = {}): ExploreTimeline {
  const findings: TimelineFinding[] = events.filter(event => event.event_kind === "finding")
    .map(event => ({event, at: time(event.recorded_at), sections: parseExploreFindingSections(event.summary)}));
  const decisions = findings.filter(item => item.sections?.decision);
  const times = events.map(event => time(event.recorded_at)).filter((at): at is number => at !== null);
  const start = times.length ? Math.min(...times) : null;
  const end = times.length ? Math.max(...times) : null;
  const timelineNodes = new Map<string, TimelineNode>();
  for (const node of nodes) {
    const stamps = [...node.updates, ...node.findings].map(event => time(event.recorded_at)).filter((at): at is number => at !== null);
    const label = node.findings.at(-1)?.status ?? "";
    timelineNodes.set(node.id, {node, first: stamps.length ? Math.min(...stamps) : null, last: stamps.length ? Math.max(...stamps) : null, label, routeId: null});
  }
  const routes: TimelineRoute[] = [];
  const singles: string[] = [];
  for (const members of routesOf(nodes, edges)) {
    if (members.length < 2) { singles.push(members[0]!); continue; }
    const ordered = members.sort((a, b) => (timelineNodes.get(a)!.first ?? Infinity) - (timelineNodes.get(b)!.first ?? Infinity));
    const firsts = ordered.map(id => timelineNodes.get(id)!.first).filter((at): at is number => at !== null);
    const lasts = ordered.map(id => timelineNodes.get(id)!.last).filter((at): at is number => at !== null);
    const id = `route:${ordered[0]}`;
    for (const member of ordered) timelineNodes.get(member)!.routeId = id;
    routes.push({id, nodeIds: ordered, first: firsts.length ? Math.min(...firsts) : null, last: lasts.length ? Math.max(...lasts) : null});
  }
  routes.sort((a, b) => (a.first ?? Infinity) - (b.first ?? Infinity));
  singles.sort((a, b) => (timelineNodes.get(a)!.first ?? Infinity) - (timelineNodes.get(b)!.first ?? Infinity));
  const span = start !== null && end !== null ? Math.max(1, end - start) : 0;
  const binCount = span ? Math.max(1, Math.min(targetBins, Math.ceil(span / 3_600_000))) : 0;
  const bins: TimelineBin[] = Array.from({length: binCount}, (_, index) => ({start: start! + span * index / binCount, end: start! + span * (index + 1) / binCount, events: 0, nodes: 0, labels: {}, decisions: {}}));
  const phases: TimelinePhase[] = span ? [0, 1, 2].map(index => ({index, start: start! + span * index / 3, end: start! + span * (index + 1) / 3, nodes: 0, findings: 0, labels: {}, decisions: {}, lastDecision: null})) : [];
  const slot = <T,>(items: T[], at: number) => items[Math.min(items.length - 1, Math.floor((at - start!) / span * items.length))]!;
  for (const event of events) { const at = time(event.recorded_at); if (at !== null && bins.length) slot(bins, at).events++; }
  for (const item of timelineNodes.values()) if (item.first !== null && bins.length) { slot(bins, item.first).nodes++; slot(phases, item.first).nodes++; }
  for (const item of findings) {
    if (item.at === null || !bins.length) continue;
    const bin = slot(bins, item.at), phase = slot(phases, item.at);
    bump(bin.labels, item.event.status); bump(phase.labels, item.event.status); phase.findings++;
    const outcome = item.sections?.decision?.outcome;
    if (outcome) {
      bump(bin.decisions, outcome); bump(phase.decisions, outcome);
      if (!phase.lastDecision || item.at >= phase.lastDecision.at!) phase.lastDecision = item;
    }
  }
  return {start, end, bins, phases, nodes: timelineNodes, routes, singles, edges, findings, decisions, structuredCount: findings.filter(item => item.sections).length};
}
