import type { DeliveryReviewSnapshot } from "./delivery-review.js";

export type GoalWorkMap = NonNullable<DeliveryReviewSnapshot["goal_map"]>;
export type GoalWorkMapNode = GoalWorkMap["nodes"][number];
export type GoalWorkMapEdge = GoalWorkMap["edges"][number];
export type GoalWorkMapFocus = "current" | "all";
export type GoalWorkMapTone = "decision" | "blocked" | "open" | "waiting" | "done" | "unknown";

/** Visual emphasis only. It never decides readiness, ownership or completion. */
export function goalWorkMapTone(node: GoalWorkMapNode): GoalWorkMapTone {
  if (node.kind === "gate" && node.state !== "done") return "decision";
  if (node.state === "ready") return "open";
  return node.state;
}

const DONE_CONTEXT = 2;
const toneRank: Record<GoalWorkMapTone, number> = { decision: 0, blocked: 1, open: 2, waiting: 3, unknown: 4, done: 5 };

export function goalWorkMapSummary(map: GoalWorkMap) {
  const work = map.nodes.filter(node => node.kind === "deliverable");
  const count = (state: GoalWorkMapNode["state"]) => work.filter(node => node.state === state).length;
  return {
    work: work.length, done: count("done"), blocked: count("blocked"), waiting: count("waiting"),
    decisions: map.nodes.filter(node => goalWorkMapTone(node) === "decision").length,
    watches: map.nodes.filter(node => node.kind === "monitor" && node.state !== "done").length,
  };
}

/**
 * Columns follow the projected prerequisite depth. "current" keeps active
 * items, their direct recorded prerequisites and the work an open decision
 * unblocks; it hides finished and deferred history, not facts.
 */
export function goalWorkMapLayout(map: GoalWorkMap, focus: GoalWorkMapFocus) {
  const active = new Set(map.nodes.filter(node => node.state !== "done" && node.state !== "waiting").map(node => node.node_id));
  const decisions = new Set(map.nodes.filter(node => goalWorkMapTone(node) === "decision").map(node => node.node_id));
  const visible = new Set(focus === "all" ? map.nodes.map(node => node.node_id) : active);
  const doneBefore = new Map<string, string[]>();
  const state = new Map(map.nodes.map(node => [node.node_id, node.state]));
  if (focus === "current") for (const edge of map.edges) {
    if (active.has(edge.from_node_id)) {
      if (state.get(edge.to_node_id) === "done") doneBefore.set(edge.from_node_id, [...new Set([...doneBefore.get(edge.from_node_id) ?? [], edge.to_node_id])]);
      else visible.add(edge.to_node_id);
    }
    if (decisions.has(edge.to_node_id)) visible.add(edge.from_node_id);
  }
  // A little finished context explains a step; a long finished history buries it.
  const collapsed = new Map<string, number>();
  for (const [id, done] of doneBefore) {
    if (done.length <= DONE_CONTEXT) done.forEach(item => visible.add(item));
    else collapsed.set(id, done.length);
  }
  const edges = map.edges.filter(edge => visible.has(edge.from_node_id) && visible.has(edge.to_node_id));
  const linked = new Set(edges.flatMap(edge => [edge.from_node_id, edge.to_node_id]));
  const order = new Map(map.nodes.map((node, index) => [node.node_id, index]));
  const byTone = (a: GoalWorkMapNode, b: GoalWorkMapNode) =>
    toneRank[goalWorkMapTone(a)] - toneRank[goalWorkMapTone(b)] || order.get(a.node_id)! - order.get(b.node_id)!;
  const shown = map.nodes.filter(node => visible.has(node.node_id));
  // Each connected chain gets its own grid so unrelated work never shares
  // columns and every line stays inside its chain.
  const parent = new Map([...linked].map(id => [id, id]));
  const root = (id: string): string => { const up = parent.get(id)!; if (up === id) return id; const top = root(up); parent.set(id, top); return top; };
  for (const edge of edges) parent.set(root(edge.from_node_id), root(edge.to_node_id));
  const members = new Map<string, GoalWorkMapNode[]>();
  for (const node of shown) if (linked.has(node.node_id)) members.set(root(node.node_id), [...members.get(root(node.node_id)) ?? [], node]);
  const urgency = (nodes: GoalWorkMapNode[]) => Math.min(...nodes.map(node => toneRank[goalWorkMapTone(node)]));
  const groups = [...members.values()]
    .sort((a, b) => urgency(a) - urgency(b) || b.length - a.length || order.get(a[0].node_id)! - order.get(b[0].node_id)!)
    .map(placed => {
      const depths = [...new Set(placed.map(node => node.depth))].sort((a, b) => a - b);
      const row = new Map<string, number>();
      return { columns: depths.map(depth => {
        // Order by the mean row of placed prerequisites to keep curves short.
        const center = (node: GoalWorkMapNode) => {
          const rows = edges.filter(edge => edge.from_node_id === node.node_id && row.has(edge.to_node_id)).map(edge => row.get(edge.to_node_id)!);
          return rows.length ? rows.reduce((sum, value) => sum + value, 0) / rows.length : Number.POSITIVE_INFINITY;
        };
        const column = placed.filter(node => node.depth === depth)
          .map(node => ({ node, center: center(node) }))
          .sort((a, b) => a.center - b.center || byTone(a.node, b.node)).map(entry => entry.node);
        column.forEach((node, index) => row.set(node.node_id, index));
        return column;
      }) };
    });
  return { groups, edges, collapsed, unlinked: shown.filter(node => !linked.has(node.node_id)).sort(byTone), hiddenCount: map.nodes.length - shown.length };
}

/** Every recorded prerequisite above and dependent below one item. */
export function goalWorkMapLineage(edges: readonly GoalWorkMapEdge[], nodeId: string | null) {
  const nodes = new Set<string>(nodeId ? [nodeId] : []);
  const related = new Set<string>();
  for (const [from, to] of [["from_node_id", "to_node_id"], ["to_node_id", "from_node_id"]] as const) {
    const queue = nodeId ? [nodeId] : [];
    const seen = new Set(queue);
    while (queue.length) {
      const current = queue.shift()!;
      for (const edge of edges) {
        if (edge[from] !== current) continue;
        related.add(edge.edge_id);
        nodes.add(edge[to]);
        if (!seen.has(edge[to])) { seen.add(edge[to]); queue.push(edge[to]); }
      }
    }
  }
  return { nodes, edges: related };
}

/**
 * Missing endpoints do not identify why an item is absent. Keep the map partial
 * until the owning projection can prove that a link is outside this Goal.
 */
export function goalWorkMapCoverage(map: GoalWorkMap): "complete" | "partial" {
  const limits = map.limits;
  if (limits.omitted_node_count > 0 || limits.source_truncated || limits.cycle_edge_count > 0) return "partial";
  if (limits.missing_endpoint_count > 0) return "partial";
  return limits.topology_complete ? "complete" : "partial";
}

/** One owner shared by every owned item is shown once instead of on each card. */
export function goalWorkMapSharedOwner(map: GoalWorkMap) {
  const owners = new Set(map.nodes.flatMap(node => node.owner_agent ? [node.owner_agent] : []));
  return owners.size === 1 && map.nodes.filter(node => node.owner_agent).length > 1 ? [...owners][0] : null;
}

/** Presentation geometry only: roomy cards and routes confined to empty gutters.
 * Keep one set of dimensions for the canvas and CSS; never shrink text to fit.
 */
export const goalWorkMapCard = { width: 236, height: 136 };
export function goalWorkMapGeometry(layout: ReturnType<typeof goalWorkMapLayout>, available: number) {
  const { width: cardWidth, height: cardHeight } = goalWorkMapCard;
  const columnStep = cardWidth + 64, rowStep = cardHeight + 36, padding = 24;
  const positions = new Map<string, { x: number; y: number }>();
  const routes = new Map<string, { x: number; y: number }[]>();
  const frames: { x: number; y: number; w: number; h: number }[] = [];
  let x = 0, y = 0, shelf = 0, right = 0;
  for (const group of layout.groups) {
    const columnIndex = new Map(group.columns.flatMap((column, index) => column.map(node => [node.node_id, index] as const)));
    const pairs = new Map<string, GoalWorkMapEdge[]>();
    for (const edge of layout.edges) {
      if (!columnIndex.has(edge.from_node_id)) continue;
      const key = `${edge.from_node_id}\0${edge.to_node_id}`;
      pairs.set(key, [...pairs.get(key) ?? [], edge]);
    }
    const detours = [...pairs.values()].filter(([edge]) => columnIndex.get(edge.from_node_id)! !== columnIndex.get(edge.to_node_id)! + 1);
    const headroom = detours.length ? 20 + detours.length * 8 : 0;
    const w = group.columns.length * columnStep - 64;
    const h = Math.max(...group.columns.map(column => column.length)) * rowStep - 36 + headroom;
    if (x > 0 && x + w > available - padding * 2) { y += shelf + 48; x = 0; shelf = 0; }
    const left = padding + x, top = padding + y;
    group.columns.forEach((column, c) => column.forEach((node, r) => positions.set(node.node_id, { x: left + c * columnStep, y: top + headroom + r * rowStep })));
    let detour = 0;
    for (const edges of pairs.values()) {
      const edge = edges[0], source = positions.get(edge.to_node_id)!, target = positions.get(edge.from_node_id)!;
      const start = { x: source.x + cardWidth, y: source.y + cardHeight / 2 };
      const end = { x: target.x, y: target.y + cardHeight / 2 };
      let points;
      if (columnIndex.get(edge.from_node_id)! === columnIndex.get(edge.to_node_id)! + 1) {
        const middle = (start.x + end.x) / 2;
        points = [start, { x: middle, y: start.y }, { x: middle, y: end.y }, end];
      } else {
        // Skip-column, reverse and same-column links travel above the group.
        // Their vertical sections stay outside all card columns, including
        // other rows in the source and destination columns.
        const track = top + 8 + detour++ * 8;
        points = [start, { x: start.x + 16, y: start.y }, { x: start.x + 16, y: track },
          { x: end.x - 16, y: track }, { x: end.x - 16, y: end.y }, end];
      }
      edges.forEach(item => routes.set(item.edge_id, points));
    }
    frames.push({ x: left - 12, y: top - 12, w: w + 24, h: h + 24 });
    right = Math.max(right, x + w);
    x += w + 48;
    shelf = Math.max(shelf, h);
  }
  return { positions, routes, frames, width: padding * 2 + right, height: padding * 2 + y + shelf };
}
