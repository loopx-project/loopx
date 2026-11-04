import { createHash } from "node:crypto";
import { stripPythonWhitespace } from "../coordination/python_whitespace.ts";
import { TODO_ITEM_SCHEMA } from "../coordination/coordination_state_contract.ts";
import { projectTodoSummaryDisplay } from "../todos/context_projection.ts";
import { compactTodoReadText, object, taskClass, recordFields, handoffSource } from "../todos/read_codec.ts";
import { projectTodoHandoffContext } from "../todos/handoff_projection.ts";
import { readTodoPriority, todoPriorityRank } from "../todos/priority.ts";
import { todoPresentationMetadata } from "../coordination/todo_presentation.ts";
import { authorityUnicodeCompare } from "../coordination/authority_store_codec.ts";
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";
import {
  projectRelations,
  todoRef,
  type PlanningInventoryRelation,
  type PlanningRelationSource,
} from "./planning_relations.ts";
import { PROJECTION_ENVELOPE_SEAL_REQUEST, sealProjectionEnvelope } from "../projection_envelope.ts";

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
  const link = (edge: GraphEdge) =>
    ordering.set(edge.from_todo_id, [...ordering.get(edge.from_todo_id) ?? [], edge.to_todo_id]);
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

export const GOAL_TASK_MAP_REQUEST = "goal_task_map_request_v0";
export const GOAL_TASK_MAP_RESULT = "goal_task_map_result_v0";
const GOAL_TASK_MAP_MAX_NODES = 120;

function goalMapNode(facts: JsonObject, depth: number): JsonObject {
  const gate = requireBoolean(facts.is_user, "goal map row.is_user");
  const monitor = requireBoolean(facts.is_monitor, "goal map row.is_monitor");
  let kind = "deliverable";
  if (monitor) kind = "monitor";
  if (gate) kind = "gate";
  return {
    kind,
    depth,
    refs: { todo_ids: [facts.todo_id] },
  };
}

function materializeGoalTaskMap(request: JsonObject): JsonObject {
  const { safe, composed } = publicTextCodec(request);
  const byId = new Map<string, JsonObject>();
  const items: JsonObject[] = [];
  if (!Array.isArray(request.todos)) throw new EffectRuntimeRequestError("goal map todos must be an array");
  for (const value of request.todos) {
    const todo = requireJsonObject(value, "goal map todo");
    const tid = safe(todo.todo_id, 120);
    const title = safe(todo.title || todo.text, 160);
    if (!tid || !title || byId.has(tid)) continue;
    byId.set(tid, { ...todo, title, state: graphState(todo) });
    items.push({
      ...topologyRow(tid, todo, graphState(todo) === "done", safe),
      is_user: todo.role === "user",
      is_monitor: todo.task_class === request.monitor_task_class,
    });
  }
  const result = projectGoalTaskMap({ ...request, todos: undefined, items });
  const projection = result.projection as JsonObject;
  const identities: string[] = [];
  projection.nodes = (projection.nodes as JsonObject[]).map((node) => {
    const tid = ((node.refs as JsonObject).todo_ids as string[])[0];
    const todo = byId.get(tid)!;
    const node_id = graphIdentity(node.kind === "gate" ? "node_gate" : "node_todo", tid, safe, tid);
    composed(node_id);
    identities.push(node_id);
    const rendered: JsonObject = { node_id, ...node, title: todo.title, state: todo.state };
    for (const [key, field] of [
      ["owner_agent", "claimed_by"],
      ["task_domain", "task_domain"],
    ]) {
      const text = safe(todo[field], 80);
      if (text && node.kind !== "gate") rendered[key] = text;
    }
    return rendered;
  });
  projection.edges = (projection.edges as JsonObject[]).map((edge, index) => {
    const identity = (result.edge_identities as JsonObject[])[index];
    const from_node_id = identities[Number(identity.from_node_index)];
    const to_node_id = identities[Number(identity.to_node_index)];
    return {
      edge_id: graphIdentity(
        `edge_${identity.source_relation}`,
        composed(`${from_node_id}:${to_node_id}`, [from_node_id, to_node_id]),
        safe,
      ),
      from_node_id,
      to_node_id,
      ...edge,
    };
  });
  return { schema_version: GOAL_TASK_MAP_RESULT, projection };
}

/** Compose legacy topology facts or materialize a source/public-text Goal snapshot. */
export function projectGoalTaskMap(value: unknown): JsonObject {
  const request = requireJsonObject(value, "goal task map request");
  if (request.schema_version !== GOAL_TASK_MAP_REQUEST) {
    throw new EffectRuntimeRequestError("Goal task map request schema mismatch");
  }
  if (request.todos !== undefined) return materializeGoalTaskMap(request);
  if (typeof request.goal_id !== "string") throw new EffectRuntimeRequestError("goal_id must be a string");
  const sourceTruncated = requireBoolean(request.source_truncated, "source_truncated");
  const topology = projectGoalTaskGraphTopology({
    schema_version: TASK_GRAPH_GOAL_TOPOLOGY_REQUEST,
    items: request.items,
    node_limit: GOAL_TASK_MAP_MAX_NODES,
    source_truncated: sourceTruncated,
  });
  const rows = request.items as JsonObject[];
  const byId = new Map(rows.map((facts) => [facts.todo_id as string, facts]));
  const entries = topology.nodes as { todo_id: string; depth: number }[];
  const nodes = entries.map((entry) => goalMapNode(byId.get(entry.todo_id)!, entry.depth));
  const indices = new Map(entries.map((entry, index) => [entry.todo_id, index]));
  const edges = topology.edges as GraphEdge[];
  const facts = requireJsonObject(request.read_facts, "goal map read facts");
  const sourceCount = requireInteger(facts.source_count, "goal map source_count");
  if (sourceCount < rows.length) throw new EffectRuntimeRequestError("goal map source count is incomplete");
  const limits = topology.completeness as JsonObject;
  const omitted = [
    { reason: "unrenderable_todos", count: sourceCount - rows.length },
    { reason: "map_node_limit", count: Number(limits.omitted_node_count) },
    {
      reason: "unresolved_map_relations",
      count: Number(limits.missing_endpoint_count) + Number(limits.cycle_edge_count),
    },
  ].filter((value) => value.count > 0);
  const envelope = sealProjectionEnvelope({
    schema_version: PROJECTION_ENVELOPE_SEAL_REQUEST,
    projection: "goal_task_map",
    observed_at: facts.observed_at,
    sources: [
      {
        source_id: "goal_todos",
        read_status: facts.source_read_at == null ? "not_read" : "read",
        last_read_at: facts.source_read_at,
        item_count: sourceCount,
      },
    ],
    coverage: {
      scope: "goal_task_map",
      expected_count: sourceTruncated ? null : sourceCount,
      included_count: nodes.length,
      omitted,
      shown_count: nodes.length,
      available_count: sourceCount,
    },
  });
  return {
    schema_version: GOAL_TASK_MAP_RESULT,
    projection: {
      schema_version: "goal_task_map_v0",
      mode: "read_only",
      goal_id: request.goal_id,
      limits: topology.completeness,
      nodes,
      projection_envelope: envelope,
      edges: edges.map((edge) => ({ relation: edge.relation, enforcement: edge.enforcement, reason: edge.reason })),
    },
    node_identity_prefixes: nodes.map((node) => (node.kind === "gate" ? "node_gate" : "node_todo")),
    node_metadata_fields: nodes.map((node) => (node.kind === "gate" ? [] : ["owner_agent", "task_domain"])),
    edge_identities: edges.map((edge) => ({
      from_node_index: indices.get(edge.from_todo_id)!,
      to_node_index: indices.get(edge.to_todo_id)!,
      source_relation: edge.source_relation,
    })),
  };
}

export const STATUS_TASK_GRAPH_REQUEST = "status_task_graph_request_v0";
export const STATUS_TASK_GRAPH_RESULT = "status_task_graph_result_v0";
const STATUS_GATE_LIMIT = 2;
const STATUS_PREDECESSOR_LIMIT = 4;

type Identity = string;
type PublicText = (value: unknown, limit: number) => string | null;
const truthy = (value: unknown): boolean =>
  Array.isArray(value)
    ? value.length > 0
    : value !== null && typeof value === "object"
      ? Object.keys(value).length > 0
      : Boolean(value);
function graphIdentity(prefix: string, value: unknown, safe: PublicText, durable?: string): string {
  const raw = safe(value, 120) || prefix;
  const digest = (text: string) => createHash("sha256").update(text).digest("hex");
  let normalized = raw
    .replace(/[^A-Za-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  if (!normalized) normalized = digest(String(value ?? "None")).slice(0, 10);
  const hash = digest(durable || raw).slice(0, 6);
  if (normalized.length > 50) normalized = normalized.slice(0, 44).replace(/_+$/g, "") + "_" + hash;
  return `${prefix}_${normalized}_${hash}`;
}

function graphState(todo: JsonObject, waiting = false): string {
  const status = stripPythonWhitespace(String(todo.status || "")).toLowerCase();
  if (status === "deferred") return "waiting";
  if (truthy(todo.done) || status === "done") return "done";
  if (status === "blocked") return "blocked";
  return waiting ? "waiting" : "open";
}

function topologyRow(todo_id: string, todo: JsonObject, done: boolean, safe: PublicText): JsonObject {
  const result: JsonObject = { todo_id, done, successor_todo_ids: [] };
  for (const field of ["unblocks_todo_id", "superseded_by", "resume_when"]) {
    const text = safe(todo[field], 240);
    if (text) result[field] = text;
  }
  if (Array.isArray(todo.successor_todo_ids)) {
    result.successor_todo_ids = todo.successor_todo_ids
      .map((value) => safe(value, 120))
      .filter((value) => value !== null);
  }
  return result;
}

/** Source strings require Python facts. Only explicitly composed display strings may compact locally. */
function publicTextCodec(request: JsonObject): {
  safe: PublicText;
  composed: (value: string, parts?: unknown[]) => string;
} {
  const texts = requireJsonObject(request.public_texts, "public text codecs");
  const composedTexts = new Set<string>();
  const foldedTexts = new Set<string>();
  const composed = (value: string, parts: unknown[] = []): string => {
    for (const part of parts) {
      const text = String(part);
      const approved = composedTexts.has(text) || typeof part === "number";
      if (!approved) throw new EffectRuntimeRequestError("Unqualified composed public text part");
    }
    composedTexts.add(value);
    return value;
  };
  for (const facts of Object.values(texts)) {
    for (const text of Object.values(object(facts))) {
      if (typeof text === "string") composed(text);
    }
  }
  for (const source of Object.keys(texts)) {
    const folded = source.toLowerCase();
    if (folded !== source) foldedTexts.add(folded);
  }
  const safe: PublicText = (value, limit) => {
    if (!truthy(value)) return null;
    const text = String(value);
    const facts = object(texts[text]);
    if (Object.hasOwn(facts, String(limit))) {
      const result = facts[String(limit)];
      if (result !== null && typeof result !== "string") {
        throw new EffectRuntimeRequestError("invalid public text codec");
      }
      if (result) composed(result);
      return result as string | null;
    }
    // Agent identities are normalized case-insensitively by the existing
    // handoff owner. The lower-case form is still derived from an approved
    // source fact; accept it only without inventing or recompacting text.
    if (foldedTexts.has(text) && Array.from(text).length <= limit) {
      composed(text);
      return text;
    }
    if (!composedTexts.has(text)) {
      throw new EffectRuntimeRequestError(`Missing public text facts for display cap ${limit}`);
    }
    const result = compactTodoReadText(text, limit);
    if (result) composed(result);
    return result;
  };
  return { safe, composed };
}

class StatusGraph {
  readonly nodes: JsonObject[] = [];
  readonly edges: JsonObject[] = [];
  private readonly byId = new Map<string, JsonObject>();
  private readonly edgeIds = new Set<string>();
  private readonly safe: PublicText;
  private readonly composed: (value: string, parts?: unknown[]) => string;
  constructor(safe: PublicText, composed: (value: string, parts?: unknown[]) => string) {
    this.safe = safe;
    this.composed = composed;
  }
  addNode(prefix: string, value: unknown, node: JsonObject, durable?: string): Identity | null {
    const node_id = graphIdentity(prefix, value, this.safe, durable);
    const key = node_id;
    if (this.byId.has(key)) return node_id;
    const title = this.safe(node.title, 160);
    if (!title || !Object.keys(object(node.refs)).length) return null;
    this.composed(node_id);
    const record = { node_id, ...node, title };
    this.byId.set(key, record);
    this.nodes.push(record);
    return node_id;
  }
  refs(node: Identity | null): JsonObject | undefined {
    return node ? object(this.byId.get(node)?.refs) : undefined;
  }
  edge(
    prefix: string,
    parts: unknown[],
    from: Identity | null,
    to: Identity | null,
    relation: string,
    reason: string,
    refs?: JsonObject,
  ): void {
    if (!from || !to || from === to || !this.byId.has(from) || !this.byId.has(to)) return;
    const edge_id = graphIdentity(
      prefix,
      this.composed(parts.map((part) => String(part ?? "None")).join(":"), parts),
      this.safe,
    );
    const key = edge_id;
    const compact = this.safe(this.composed(reason), 180);
    if (!compact || this.edgeIds.has(key)) return;
    this.edgeIds.add(key);
    this.edges.push({
      edge_id,
      from_node_id: from,
      to_node_id: to,
      relation,
      reason: compact,
      ...(refs ? { refs } : {}),
    });
  }
}

function statusOpenItems(summary: JsonObject, keys: string[], limit: number): JsonObject[] {
  const rows: JsonObject[] = [];
  const seen = new Set<string>();
  const collect = (): void => {
    for (const key of keys)
      for (const value of Array.isArray(summary[key]) ? summary[key] : []) {
        const raw = object(value);
        if (!Object.keys(raw).length || truthy(raw.done)) continue;
        const text = compactTodoReadText(String(raw.text || ""), 180);
        if (!text) continue;
        const key = JSON.stringify([raw.index ?? null, text]);
        if (seen.has(key)) continue;
        seen.add(key);
        const compact: JsonObject = { index: raw.index ?? null, done: false, text };
        for (const field of recordFields)
          if (raw[field] != null && !Object.hasOwn(compact, field)) compact[field] = raw[field];
        const note = projectTodoHandoffContext([handoffSource(compact)])[0]!.note;
        if (note) compact.handoff_note = note;
        rows.push(compact);
        if (rows.length >= limit) return;
      }
  };
  collect();
  const order = (todo: JsonObject): number => {
    try {
      const legacy =
        todo.schema_version === TODO_ITEM_SCHEMA || (todo.schema_version == null && truthy(todo.source_section));
      return legacy ? (todoPresentationMetadata(todo).display_order ?? 999999) : 999999;
    } catch {
      return Number.isInteger(todo.index) && Number(todo.index) >= 0 ? Number(todo.index) : 999999;
    }
  };
  return rows.sort(
    (a, b) =>
      todoPriorityRank(readTodoPriority(a)) - todoPriorityRank(readTodoPriority(b)) ||
      order(a) - order(b) ||
      (order(a) !== 999999
        ? 0
        : authorityUnicodeCompare(
            String(a.completed_at || a.updated_at || ""),
            String(b.completed_at || b.updated_at || ""),
          ) || authorityUnicodeCompare(String(a.todo_id || ""), String(b.todo_id || ""))),
  );
}

/** One status snapshot, including source selection and the in-process topology. */
export function projectStatusTaskGraph(value: unknown): JsonObject {
  const request = requireJsonObject(value, "status task graph request");
  if (request.schema_version !== STATUS_TASK_GRAPH_REQUEST)
    throw new EffectRuntimeRequestError("Status task graph request schema mismatch");
  const goal = requireNonEmptyString(request.goal_id, "goal_id");
  const summaries = requireJsonObject(request.summaries, "status summaries");
  const agent = requireJsonObject(summaries.agent, "agent summary");
  const user = requireJsonObject(summaries.user, "user summary");
  const visibleLimit = requireInteger(request.max_status_todos_per_role, "max_status_todos_per_role");
  if (visibleLimit < 1) throw new EffectRuntimeRequestError("status Todo limit must be positive");
  const { safe, composed } = publicTextCodec(request);
  composed(goal);
  const refs = (key: string, ...values: unknown[]): JsonObject | undefined => {
    const found: string[] = [];
    for (const value of values.flat()) {
      const text = safe(value, 120);
      if (text && !found.includes(text)) found.push(text);
      if (found.length === 4) break;
    }
    return found.length ? { [key]: found } : undefined;
  };
  const builder = new StatusGraph(safe, composed);
  const deliverable = (todo: JsonObject, waiting = false): Identity | null => {
    const tid = safe(todo.todo_id, 120);
    const title = safe(todo.title || todo.text, 160);
    if (!tid || !title) return null;
    const owner = safe(todo.claimed_by, 80);
    const actor = safe(todo.last_actor_agent_id, 80);
    return builder.addNode(
      "node_todo",
      tid,
      {
        kind: "deliverable",
        title,
        state: graphState(todo, waiting),
        refs: refs("todo_ids", tid)!,
        ...(owner ? { owner_agent: owner } : {}),
        ...(actor ? { actor_agent: actor } : {}),
      },
      tid,
    );
  };
  const selected = statusOpenItems(agent, ["first_open_items", "items"], 1)[0];
  const selectedId = safe(selected?.todo_id, 120);
  const selectedNode = selected ? deliverable(selected) : null;
  if (selected && selectedNode) {
    const owner = safe(selected.claimed_by, 80);
    if (owner) {
      const lease = composed(`claim:${goal}:${selectedId || composed("selected")}:${owner}`, [
        goal,
        selectedId || composed("selected"),
        owner,
      ]);
      const leaseRefs = refs("lease_ids", lease);
      const node = builder.addNode("node_lease", lease, {
        kind: "lease",
        title: composed(`Claimed by ${owner}`, [owner]),
        state: "ready",
        refs: leaseRefs!,
        owner_agent: owner,
      });
      builder.edge(
        "edge_depends",
        [selectedNode, node],
        selectedNode,
        node,
        "depends_on",
        "Selected work depends on its active claim lease.",
        leaseRefs,
      );
    }
  }
  const planning = (source: JsonObject): JsonObject[] =>
    projectTodoSummaryDisplay({ source: { record: source }, include_terminal: true });
  let predecessorMetrics: JsonObject = {};
  if (selected && selectedId && selectedNode) {
    const agentRows = planning(agent);
    const userRows = planning(user);
    const all = new Map([...agentRows, ...userRows].map((todo) => [String(todo.todo_id), todo]));
    if (!all.has(selectedId)) all.set(selectedId, selected);
    const renderable = [...all].filter(([, todo]) => safe(todo.title || todo.text, 160));
    const topology = projectTaskGraphTopology({
      schema_version: TASK_GRAPH_TOPOLOGY_REQUEST,
      selected_todo_id: selectedId,
      predecessor_limit: STATUS_PREDECESSOR_LIMIT,
      source_truncated:
        Number(agent.total_count || 0) > agentRows.length || Number(user.total_count || 0) > userRows.length,
      items: renderable.map(([todo_id, todo]) =>
        topologyRow(todo_id, todo, graphState(todo, todo_id !== selectedId) === "done", safe),
      ),
    });
    predecessorMetrics = object(topology.completeness);
    const predecessors = topology.predecessor_todo_ids as string[];
    const edges = topology.edges as GraphEdge[];
    const ids = new Map<string, Identity | null>([[selectedId, selectedNode]]);
    for (const tid of predecessors) ids.set(tid, deliverable(all.get(tid)!, true));
    for (const edge of edges) {
      const from = ids.get(edge.from_todo_id)!;
      const to = ids.get(edge.to_todo_id)!;
      builder.edge(
        `edge_${edge.source_relation}`,
        [from, to],
        from,
        to,
        edge.relation,
        edge.reason,
        refs("todo_ids", edge.to_todo_id),
      );
    }
    for (const tid of [selectedId, ...predecessors]) {
      const todo = all.get(tid)!;
      const nid = ids.get(tid)!;
      if (graphState(todo) === "done") {
        const evidence = safe(todo.evidence, 180) || safe(todo.note, 180);
        if (evidence) {
          const node = builder.addNode("node_evidence", composed(`evidence:${tid}`, [tid]), {
            kind: "evidence",
            title: composed(Array.from(evidence).slice(0, 120).join("")),
            state: "done",
            refs: refs("todo_ids", tid)!,
          });
          builder.edge(
            "edge_evidences",
            [node, nid],
            node,
            nid,
            "validates",
            "Completion evidence supports the delivered predecessor.",
            refs("todo_ids", tid),
          );
        }
      }
      if (tid === selectedId) continue;
      const note = object(todo.handoff_note);
      const from = safe(note.from_agent, 80);
      const to = safe(note.to_agent, 80);
      if (!from || !to || from === to) continue;
      const status = safe(note.status, 80);
      const rawStatus = stripPythonWhitespace(String(todo.status || "")).toLowerCase();
      const state = ["done", "waiting", "unknown"].includes(status || "")
        ? status!
        : todo.done === true || rawStatus === "done"
          ? "done"
          : rawStatus === "open"
            ? "waiting"
            : "unknown";
      const node = builder.addNode("node_handoff", composed(`handoff:${tid}:${from}:${to}`, [tid, from, to]), {
        kind: "handoff",
        title: composed(`Handoff from ${from} to ${to}`, [from, to]),
        state,
        refs: refs("todo_ids", tid)!,
        from_agent: from,
        to_agent: to,
      });
      builder.edge(
        "edge_hands_off_to",
        [nid, node],
        nid,
        node,
        "hands_off_to",
        composed(`Agent ${from} handed off to ${to}.`, [from, to]),
        refs("todo_ids", tid),
      );
      const successor = ids.get(edges.find((edge) => edge.to_todo_id === tid)!.from_todo_id)!;
      builder.edge(
        "edge_continues_handoff",
        [node, successor],
        node,
        successor,
        "continues",
        composed(`Successor continues after handoff from ${from} to ${to}.`, [from, to]),
        refs("todo_ids", tid),
      );
    }
  }
  const visible = statusOpenItems(user, ["gate_open_items", "first_open_items", "items"], visibleLimit);
  const gates = visible.filter(
    (todo) => taskClass({ ...todo, text: [todo.title, todo.text].filter(Boolean).join(" ") }) === "user_gate",
  );
  const gateCount =
    gates.length && gates.length === visible.length
      ? Math.max(gates.length, Number(user.open_count || 0))
      : gates.length;
  const userItems = gates.slice(0, STATUS_GATE_LIMIT);
  for (const [ordinal, todo] of userItems.entries()) {
    const tid = safe(todo.todo_id, 120);
    const gate = tid || composed(`gate:${goal}:user:${ordinal + 1}`, [goal, ordinal + 1]);
    const node = builder.addNode("node_gate", gate, {
      kind: "gate",
      title: todo.title || todo.text || composed("Open user gate"),
      state: graphState(todo, true),
      refs: refs("gate_ids", gate)!,
    });
    builder.edge(
      "edge_blocks",
      [node, selectedNode, ordinal],
      node,
      selectedNode,
      "blocks",
      "Open user gate blocks the gated delivery path.",
      refs("gate_ids", gate),
    );
  }
  const truncated = Math.max(0, gateCount - userItems.length);
  if (truncated) {
    const node = builder.addNode(
      "node_gate_summary",
      composed(`${goal}:${truncated}:more_user_gates`, [goal, truncated]),
      {
        kind: "gate_summary",
        title: composed(`${truncated} more open user gates not expanded`, [truncated]),
        state: "waiting",
        refs: refs("goal_ids", goal)!,
      },
    );
    builder.edge(
      "edge_blocks",
      [node, selectedNode, composed("user_gate_summary")],
      node,
      selectedNode,
      "blocks",
      "Additional open user gates stay on the cold path instead of expanding the task graph hot path.",
      refs("goal_ids", goal),
    );
  }
  if (!Array.isArray(request.latest_runs)) throw new EffectRuntimeRequestError("latest_runs must be an array");
  const runs = request.latest_runs.map((value) => requireJsonObject(value, "latest run"));
  let runNode: Identity | null = null;
  for (const run of runs) {
    const runRef = safe(run.run_id || run.generated_at || run.classification, 120);
    const classification = safe(run.classification, 120);
    if (!runRef && !classification) continue;
    const runRefs = refs("run_ids", runRef || classification)!;
    if (selectedId) Object.assign(runRefs, refs("todo_ids", selectedId));
    const title = classification || composed("Latest compact run-history evidence");
    runNode = builder.addNode("node_run", runRef || title, {
      kind: "validation",
      title: composed(`Latest compact run: ${title}`, [title]),
      state: "ready",
      refs: runRefs,
    });
    break;
  }
  builder.edge(
    "edge_validates",
    [runNode, selectedNode],
    runNode,
    selectedNode,
    "validates",
    "Latest compact run-history evidence validates or contextualizes the selected work.",
    builder.refs(runNode),
  );
  if (runNode && selectedNode)
    for (const run of runs) {
      const text = [run.classification, run.recommended_action, run.delivery_outcome, run.delivery_batch_scale]
        .map((v) => safe(v, 160) || "")
        .join(" ")
        .toLowerCase();
      if (!text.trim()) continue;
      if (["audit", "audited"].some((marker) => text.includes(marker)))
        builder.edge(
          "edge_audits",
          [runNode, selectedNode],
          runNode,
          selectedNode,
          "audits",
          "Compact run-history evidence audits the selected work lane without replacing todo or gate state.",
          builder.refs(runNode),
        );
      if (["continuation", "continue", "continuing", "continued"].some((marker) => text.includes(marker)))
        builder.edge(
          "edge_continues",
          [runNode, selectedNode],
          runNode,
          selectedNode,
          "continues",
          "Compact run-history evidence records a continuation of the selected work lane.",
          builder.refs(runNode),
        );
      break;
    }
  if (request.replan && selectedNode) {
    const replan = requireJsonObject(request.replan, "replan facts");
    const id = safe(replan.schema_version || replan.kind || composed("autonomous_replan_obligation"), 120);
    const node = builder.addNode("node_repair", id, {
      kind: "repair",
      title: replan.recommended_action || id,
      state: "ready",
      refs: refs("todo_ids", selectedId)!,
    });
    builder.edge(
      "edge_repairs",
      [node, selectedNode],
      node,
      selectedNode,
      "repairs",
      "Autonomous repair/replan obligation should recover the selected work lane.",
      refs("todo_ids", selectedId),
    );
  }
  if (!builder.nodes.length) return { schema_version: STATUS_TASK_GRAPH_RESULT, projection: null };
  const derived: JsonObject = {
    source_of_truth: ["event_ledger", "active_goal_state", "todos", "gates", "leases", "run_history"],
    status_item_goal_id: goal,
    run_history_window: "compact_latest_runs",
  };
  const updated = safe(request.warning_updated_at, 80) || safe(request.state_updated_at, 80);
  if (updated) derived.active_state_updated_at = updated;
  const limits: JsonObject = {
    user_gate_node_limit: STATUS_GATE_LIMIT,
    user_gate_open_count: gateCount,
    user_gate_truncated_count: truncated,
  };
  if (Object.keys(predecessorMetrics).length) {
    const { predecessor_limit, ...metrics } = predecessorMetrics;
    Object.assign(limits, { predecessor_node_limit: predecessor_limit }, metrics);
  }
  return {
    schema_version: STATUS_TASK_GRAPH_RESULT,
    projection: {
      schema_version: "task_graph_projection_v0",
      mode: "read_only",
      goal_id: goal,
      generated_at:
        runs.map((run) => safe(run.generated_at, 80)).find(Boolean) ||
        safe(request.current_generated_at, 80) ||
        requireNonEmptyString(request.fallback_time, "fallback_time"),
      derived_from: derived,
      truth_contract: {
        event_ledger_is_source_of_truth: true,
        projection_is_writable: false,
        write_api: false,
        recompute_rule:
          "Recompute from status, active state, gates, leases, and run history after each lifecycle event.",
      },
      limits,
      nodes: builder.nodes,
      edges: builder.edges,
    },
  };
}
