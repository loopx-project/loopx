import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireBoolean, requireInteger, requireJsonObject, requireNonEmptyString,
  requireStringArray } from "../runtime_decode.ts";
import { projectRelations, todoRef, type PlanningInventoryRelation,
  type PlanningRelationSource } from "./planning_relations.ts";

export const TASK_GRAPH_TOPOLOGY_REQUEST = "task_graph_topology_request_v0";
export const TASK_GRAPH_TOPOLOGY_RESULT = "task_graph_topology_result_v0";

interface GraphRow extends PlanningRelationSource { done: boolean }
type GraphRelation = "depends_on" | "continues" | "supersedes";
interface GraphEdge extends JsonObject {
  from_todo_id: string;
  to_todo_id: string;
  relation: GraphRelation;
  source_relation: PlanningInventoryRelation["relation"];
  enforcement: PlanningInventoryRelation["enforcement"];
  reason: string;
}

function row(value: unknown): GraphRow {
  const raw = requireJsonObject(value, "graph row");
  const result: GraphRow = {
    todo_id: requireNonEmptyString(raw.todo_id, "graph row.todo_id"),
    done: requireBoolean(raw.done, "graph row.done"),
    successor_todo_ids: requireStringArray(raw.successor_todo_ids, "graph row.successor_todo_ids"),
  };
  for (const key of ["unblocks_todo_id", "superseded_by", "resume_when"] as const) {
    if (raw[key] !== undefined && raw[key] !== null && raw[key] !== "") {
      result[key] = requireNonEmptyString(raw[key], `graph row.${key}`);
    }
  }
  return result;
}

/** Display direction is work -> prerequisite/ancestor, never transition authority. */
function graphEdge(value: PlanningInventoryRelation): GraphEdge | null {
  const source = value.from_todo_id;
  // Direct persisted lineage IDs are opaque (legacy fixtures include short IDs).
  // Only a condition needs decoding; never infer a Todo from an arbitrary suffix.
  const target = value.relation === "resumes_when" ? todoRef(value.to_ref) : value.to_ref;
  if (!target || source === target) return null;
  const common = { source_relation: value.relation, enforcement: value.enforcement };
  switch (value.relation) {
    case "successor":
      return { ...common, from_todo_id: target, to_todo_id: source, relation: "continues",
        reason: "Work continues predecessor lineage; this is not a completion prerequisite." };
    case "superseded_by":
      return { ...common, from_todo_id: target, to_todo_id: source, relation: "supersedes",
        reason: "Work supersedes predecessor lineage; this does not authorize a transition." };
    case "unblocks":
      return { ...common, from_todo_id: target, to_todo_id: source, relation: "depends_on",
        reason: "Work has a typed lifecycle dependency on the linked unblocking Todo." };
    case "resumes_when":
      return { ...common, from_todo_id: source, to_todo_id: target, relation: "depends_on",
        reason: value.to_ref.trim().toLowerCase().startsWith("monitor_changed:")
          ? "Work waits for a Monitor generation change, not Monitor completion."
          : "Work has a Todo completion condition; readiness is decided by the resume evaluator." };
    case "routes_via": return null;
  }
}

function edgeKey(edge: GraphEdge): string {
  return [edge.from_todo_id, edge.to_todo_id, edge.source_relation].join("\0");
}

function decodeRows(request: JsonObject): { rows: GraphRow[]; byId: Map<string, GraphRow> } {
  if (!Array.isArray(request.items)) throw new EffectRuntimeRequestError("graph items must be an array");
  const rows = request.items.map(row);
  const byId = new Map(rows.map(item => [item.todo_id, item]));
  if (byId.size !== rows.length) throw new EffectRuntimeRequestError("Duplicate task graph Todo id");
  return { rows, byId };
}

/** Every displayable relation in caller-independent order. */
function relationEdges(rows: readonly GraphRow[]): GraphEdge[] {
  const edges = projectRelations(rows).map(graphEdge).filter((edge): edge is GraphEdge => edge !== null);
  return edges.sort((a, b) => edgeKey(a) < edgeKey(b) ? -1 : edgeKey(a) > edgeKey(b) ? 1 : 0);
}

/** Bounded read lens over one supplied snapshot. No provider reads or writes. */
export function projectTaskGraphTopology(value: unknown): JsonObject {
  const request = requireJsonObject(value, "task graph topology request");
  if (request.schema_version !== TASK_GRAPH_TOPOLOGY_REQUEST) {
    throw new EffectRuntimeRequestError("Task graph topology request schema mismatch");
  }
  const selected = requireNonEmptyString(request.selected_todo_id, "selected_todo_id");
  const limit = requireInteger(request.predecessor_limit, "predecessor_limit");
  if (limit < 0 || limit > 32) throw new EffectRuntimeRequestError("predecessor_limit must be in 0..32");
  const sourceTruncated = requireBoolean(request.source_truncated, "source_truncated");
  const { byId, rows } = decodeRows(request);
  const adjacency = new Map<string, GraphEdge[]>();
  for (const edge of relationEdges(rows)) {
    const neighbors = adjacency.get(edge.from_todo_id) ?? [];
    neighbors.push(edge);
    adjacency.set(edge.from_todo_id, neighbors);
  }
  const emitted = new Set<string>();
  const missing = new Set<string>();
  const omitted = new Set<string>();
  const candidates = new Map<string, GraphEdge>();
  const queue: string[] = [];
  if (byId.has(selected)) { emitted.add(selected); queue.push(selected); }
  else missing.add(selected);
  // Continue scanning admitted vertices after the cap: a diamond's second edge
  // must not disappear just because an earlier neighbor would exceed the cap.
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const current = queue[cursor];
    if (current !== selected && byId.get(current)?.done !== true) continue;
    for (const edge of adjacency.get(current) ?? []) {
      candidates.set(edgeKey(edge), edge);
      const target = edge.to_todo_id;
      if (!byId.has(target)) { missing.add(target); continue; }
      if (emitted.has(target)) continue;
      if (emitted.size - 1 >= limit) { omitted.add(target); continue; }
      emitted.add(target);
      queue.push(target);
    }
  }
  const edges = [...candidates.values()].filter(edge => emitted.has(edge.to_todo_id));
  const predecessors = queue.filter(id => id !== selected);
  return {
    schema_version: TASK_GRAPH_TOPOLOGY_RESULT,
    predecessor_todo_ids: predecessors,
    edges,
    completeness: {
      predecessor_limit: limit, emitted_predecessor_count: predecessors.length,
      predecessor_truncated: omitted.size > 0, source_truncated: sourceTruncated,
      missing_predecessor_count: missing.size,
      topology_complete: !sourceTruncated && missing.size === 0 && omitted.size === 0,
    },
  };
}

export const TASK_GRAPH_GOAL_TOPOLOGY_REQUEST = "task_graph_goal_topology_request_v0";
export const TASK_GRAPH_GOAL_TOPOLOGY_RESULT = "task_graph_goal_topology_result_v0";
const GOAL_NODE_LIMIT_MAX = 200;

/**
 * Whole-Goal read lens over one supplied snapshot. Unfinished work is admitted
 * first, then completed direct prerequisites of that work, then remaining
 * history, all in caller order. `depth` is the longest recorded prerequisite or
 * lineage path below a Todo: a layout fact, not an execution order or readiness.
 */
export function projectGoalTaskGraphTopology(value: unknown): JsonObject {
  const request = requireJsonObject(value, "goal task graph topology request");
  if (request.schema_version !== TASK_GRAPH_GOAL_TOPOLOGY_REQUEST) {
    throw new EffectRuntimeRequestError("Goal task graph topology request schema mismatch");
  }
  const limit = requireInteger(request.node_limit, "node_limit");
  if (limit < 1 || limit > GOAL_NODE_LIMIT_MAX) {
    throw new EffectRuntimeRequestError(`node_limit must be in 1..${GOAL_NODE_LIMIT_MAX}`);
  }
  const sourceTruncated = requireBoolean(request.source_truncated, "source_truncated");
  const { byId, rows } = decodeRows(request);
  const edges = relationEdges(rows);
  const admitted = new Set<string>();
  const admit = (id: string) => { if (admitted.size < limit) admitted.add(id); };
  for (const item of rows) if (!item.done) admit(item.todo_id);
  const unfinished = new Set(admitted);
  for (const edge of edges) {
    if (unfinished.has(edge.from_todo_id) && byId.get(edge.to_todo_id)?.done) admit(edge.to_todo_id);
  }
  for (const item of rows) admit(item.todo_id);
  const missing = new Set([...edges.flatMap(edge => [edge.from_todo_id, edge.to_todo_id])]
    .filter(id => !byId.has(id)));
  const emitted = edges.filter(edge => admitted.has(edge.from_todo_id) && admitted.has(edge.to_todo_id));
  const order = rows.map(item => item.todo_id).filter(id => admitted.has(id));
  // Dependencies order the map. Lineage carries no ordering obligation, so it
  // only adds depth where it agrees with that order; a follow-up recorded in
  // the opposite direction of a dependency is not a cycle.
  const ordering = new Map<string, string[]>();
  const link = (edge: GraphEdge) => ordering.set(edge.from_todo_id, [...ordering.get(edge.from_todo_id) ?? [], edge.to_todo_id]);
  const reaches = (from: string, to: string) => {
    const seen = new Set([from]);
    const queue = [from];
    while (queue.length) {
      const current = queue.shift()!;
      if (current === to) return true;
      for (const next of ordering.get(current) ?? []) if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
    return false;
  };
  const cycleEdges = new Set<string>();
  for (const edge of emitted.filter(edge => edge.relation === "depends_on")) {
    if (reaches(edge.to_todo_id, edge.from_todo_id)) cycleEdges.add(edgeKey(edge));
    else link(edge);
  }
  for (const edge of emitted.filter(edge => edge.relation !== "depends_on")) {
    if (!reaches(edge.to_todo_id, edge.from_todo_id)) link(edge);
  }
  const depth = new Map<string, number>();
  const visit = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    const result = Math.max(0, ...(ordering.get(id) ?? []).map(next => visit(next) + 1));
    depth.set(id, result);
    return result;
  };
  for (const id of order) visit(id);
  const omitted = rows.length - order.length;
  return {
    schema_version: TASK_GRAPH_GOAL_TOPOLOGY_RESULT,
    nodes: order.map(todo_id => ({ todo_id, depth: depth.get(todo_id)! })),
    edges: emitted,
    completeness: {
      node_limit: limit, emitted_node_count: order.length, omitted_node_count: omitted,
      source_truncated: sourceTruncated, missing_endpoint_count: missing.size,
      cycle_edge_count: cycleEdges.size,
      topology_complete: !sourceTruncated && omitted === 0 && missing.size === 0 && cycleEdges.size === 0,
    },
  };
}
