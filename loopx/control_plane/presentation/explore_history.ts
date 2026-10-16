/** Read-only presentation of canonical Explore events, never an assessment.
 * Event kinds/statuses/relationships remain owned by Explore result_log.py.
 * Local display grouping must not turn parentage or time into supporting evidence.
 */
export type ExploreHistoryEvent = {
  schema_version: string; event_id: string; goal_id: string; event_kind: string;
  result_id: string; recorded_at: string; title: string; summary: string;
  status: string; node_kind: string; blocked_reason: string; parent_id: string;
  node_id: string; from_node: string; to_node: string; edge_type: string;
  agent_id: string; run_id: string; evidence_refs: string[]; supersedes: string; confidence: number | null;
};
export type ExploreHistoryPage = {goal_id: string; items: ExploreHistoryEvent[]; total: number; next_cursor: string | null};
export type ExploreHistoryNode = {id: string; latest: ExploreHistoryEvent; updates: ExploreHistoryEvent[]; findings: ExploreHistoryEvent[]; revisions: ExploreHistoryEvent[]};

/** Display-only compression for sparse capture, never a research route or a
 * causal relationship. Every member and version remains readable in one group. */
export function groupUnlinkedHistoryQuestions(nodes: readonly ExploreHistoryNode[], edges: readonly ExploreHistoryEvent[]) {
  const related = new Set(edges.flatMap(edge => [edge.from_node, edge.to_node]));
  for (const node of nodes) if (node.latest.parent_id) {related.add(node.id); related.add(node.latest.parent_id);}
  const candidates = nodes.filter(node => node.latest.node_kind === "question" && !related.has(node.id));
  if (candidates.length <= 18) return {nodes: [...nodes], groups: [] as {day: string; nodes: ExploreHistoryNode[]}[]};
  const grouped = new Set(candidates.map(node => node.id));
  const days = new Map<string, ExploreHistoryNode[]>();
  for (const node of candidates) {
    const first = Date.parse(node.updates[0]!.recorded_at);
    const day = Number.isFinite(first) ? new Date(first).toISOString().slice(0, 10) : "";
    const members = days.get(day) ?? []; members.push(node); days.set(day, members);
  }
  return {nodes: nodes.filter(node => !grouped.has(node.id)), groups: [...days].map(([day, members]) => ({day, nodes: members}))};
}

export function parseExploreHistoryPage(input: unknown, goalId: string): ExploreHistoryPage {
  if (!input || typeof input !== "object") throw new Error("Invalid Explore history page");
  const page = input as Record<string, unknown>;
  if (page.ok !== true || page.goal_id !== goalId || !Array.isArray(page.items)
      || !Number.isSafeInteger(page.total) || (page.total as number) < 0
      || !(page.next_cursor === null || typeof page.next_cursor === "string")) {
    throw new Error("Explore history scope or coverage mismatch");
  }
  const fields = ["summary", "status", "node_kind", "blocked_reason", "parent_id", "node_id",
    "from_node", "to_node", "edge_type", "agent_id", "run_id", "supersedes"];
  const items = page.items.map((row): ExploreHistoryEvent => {
    if (!row || typeof row !== "object") throw new Error("Invalid Explore history event");
    const raw = row as Record<string, unknown>;
    if (raw.schema_version !== "loopx_explore_result_event_v0" || raw.goal_id !== goalId
        || !["event_id", "result_id", "event_kind", "recorded_at", "title"].every(key => typeof raw[key] === "string" && raw[key])) {
      throw new Error("Explore history event identity mismatch");
    }
    if (fields.some(key => raw[key] !== undefined && typeof raw[key] !== "string")
        || (raw.evidence_refs !== undefined && (!Array.isArray(raw.evidence_refs) || raw.evidence_refs.some(ref => typeof ref !== "string")))
        || (raw.confidence !== undefined && (typeof raw.confidence !== "number" || !Number.isFinite(raw.confidence) || raw.confidence < 0 || raw.confidence > 1))) {
      throw new Error("Invalid Explore history evidence");
    }
    // Normalize key order too, so replay equality is independent of JSON key order.
    return {
      schema_version: String(raw.schema_version), event_id: String(raw.event_id), goal_id: goalId,
      event_kind: String(raw.event_kind), result_id: String(raw.result_id), recorded_at: String(raw.recorded_at), title: String(raw.title),
      ...Object.fromEntries(fields.map(key => [key, raw[key] ?? ""])),
      evidence_refs: [...(raw.evidence_refs as string[] | undefined ?? [])],
      confidence: typeof raw.confidence === "number" ? raw.confidence : null,
    } as ExploreHistoryEvent;
  });
  return {goal_id: goalId, items, total: page.total as number, next_cursor: page.next_cursor as string | null};
}

export function projectExploreHistory(events: readonly ExploreHistoryEvent[], goalId: string) {
  const unique = new Map<string, ExploreHistoryEvent>();
  const nodes = new Map<string, ExploreHistoryNode>();
  const edges = new Map<string, ExploreHistoryEvent>();
  const findings = new Map<string, ExploreHistoryEvent>();
  for (const event of events) {
    if (event.goal_id !== goalId) throw new Error("Explore history Goal mismatch");
    const previous = unique.get(event.event_id);
    if (previous) {
      if (JSON.stringify(previous) !== JSON.stringify(event)) throw new Error("Conflicting Explore history replay");
      continue;
    }
    unique.set(event.event_id, event);
    if (event.event_kind === "node") {
      const node = nodes.get(event.result_id);
      if (node) {node.latest = event; node.updates.push(event);}
      else nodes.set(event.result_id, {id: event.result_id, latest: event, updates: [event], findings: [], revisions: []});
    } else if (event.event_kind === "edge") edges.set(event.result_id, event);
    else if (event.event_kind === "finding") findings.set(event.result_id, event);
  }
  for (const finding of findings.values()) nodes.get(finding.node_id)?.findings.push(finding);
  // A selected node keeps superseded finding versions inline too. They must
  // not disappear behind a latest-only finding or become current verification.
  for (const event of unique.values()) {
    if (event.event_kind === "node") nodes.get(event.result_id)?.revisions.push(event);
    else if (event.event_kind === "finding" && findings.get(event.result_id)?.event_id !== event.event_id) nodes.get(event.node_id)?.revisions.push(event);
  }
  const times = [...unique.values()].map(event => Date.parse(event.recorded_at)).filter(Number.isFinite);
  const links = [...edges.values()];
  return {
    events: [...unique.values()], nodes: [...nodes.values()], edges: links, findings: [...findings.values()],
    startAt: times.length ? new Date(Math.min(...times)).toISOString() : null,
    endAt: times.length ? new Date(Math.max(...times)).toISOString() : null,
    unknownTimeCount: unique.size - times.length,
    unknownKindCount: [...unique.values()].filter(event => !["node", "edge", "finding"].includes(event.event_kind)).length,
    missingLinks: links.filter(edge => !nodes.has(edge.from_node) || !nodes.has(edge.to_node)),
  };
}

/** Stable display lanes from explicit parent/subtopic links only. Missing
 * parents stay at the root and cycles are bounded; positions imply no causality. */
export function layoutExploreHistory(nodes: readonly ExploreHistoryNode[], edges: readonly ExploreHistoryEvent[]) {
  const parents = new Map<string, string>();
  const known = new Set(nodes.map(node => node.id));
  for (const node of nodes) if (known.has(node.latest.parent_id)) parents.set(node.id, node.latest.parent_id);
  for (const edge of edges) if (edge.edge_type === "subtopic_of" && known.has(edge.from_node) && known.has(edge.to_node)) parents.set(edge.from_node, edge.to_node);
  if (!parents.size) return nodes.map((node, index) => ({node, x: (index % 4) * 230 + 24, y: Math.floor(index / 4) * 124 + 24}));
  const depth = (id: string): number => {
    const seen = new Set([id]);
    let count = 0;
    let parent = parents.get(id);
    while (parent && count < 4 && !seen.has(parent)) {
      seen.add(parent); count++; parent = parents.get(parent);
    }
    return count;
  };
  const rows = new Map<number, number>();
  return nodes.map(node => {
    const column = depth(node.id); const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    return {node, x: column * 230 + 24, y: row * 124 + 24};
  });
}
