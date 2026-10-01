import { z } from "zod";
import { goalAcceptanceObservationSchema, type GoalAcceptanceContract } from "./goal-acceptance-observation.js";

const refsSchema = z.record(z.string(), z.array(z.string()));
const nodeSchema = z.object({
  node_id: z.string().min(1),
  kind: z.enum(["deliverable", "gate", "gate_summary", "lease", "validation", "repair", "handoff", "evidence"]),
  title: z.string(),
  state: z.enum(["open", "ready", "blocked", "done", "waiting", "unknown"]),
  refs: refsSchema,
  owner_agent: z.string().optional(),
  actor_agent: z.string().optional(),
  from_agent: z.string().optional(),
  to_agent: z.string().optional(),
});
const edgeSchema = z.object({
  edge_id: z.string().min(1), from_node_id: z.string(), to_node_id: z.string(),
  relation: z.enum(["depends_on", "blocks", "validates", "repairs", "audits", "continues", "hands_off_to", "supersedes"]),
  reason: z.string(), refs: refsSchema.optional(),
});
const graphSchema = z.object({
  schema_version: z.literal("task_graph_projection_v0"), mode: z.literal("read_only"),
  goal_id: z.string(), generated_at: z.string().nullable(),
  truth_contract: z.object({ projection_is_writable: z.literal(false), write_api: z.literal(false) }),
  limits: z.object({
    user_gate_node_limit: z.number().int().nonnegative(),
    user_gate_open_count: z.number().int().nonnegative(),
    user_gate_truncated_count: z.number().int().nonnegative(),
    source_truncated: z.boolean().optional(), predecessor_truncated: z.boolean().optional(),
    missing_predecessor_count: z.number().int().nonnegative().optional(), topology_complete: z.boolean().optional(),
  }),
  nodes: z.array(nodeSchema), edges: z.array(edgeSchema),
}).superRefine((graph, context) => {
  const nodes = new Set(graph.nodes.map(node => node.node_id));
  const edges = new Set(graph.edges.map(edge => edge.edge_id));
  if (nodes.size !== graph.nodes.length || edges.size !== graph.edges.length
      || graph.edges.some(edge => !nodes.has(edge.from_node_id) || !nodes.has(edge.to_node_id))) {
    context.addIssue({ code: "custom", message: "Graph identities or endpoints are invalid" });
  }
});
const goalMapNodeSchema = z.object({
  node_id: z.string().min(1), kind: z.enum(["deliverable", "gate", "monitor"]), title: z.string(),
  state: nodeSchema.shape.state, depth: z.number().int().nonnegative(), refs: refsSchema,
  owner_agent: z.string().optional(), task_domain: z.string().optional(),
});
const goalMapSchema = z.object({
  schema_version: z.literal("goal_task_map_v0"), mode: z.literal("read_only"), goal_id: z.string(),
  limits: z.object({
    node_limit: z.number().int().positive(), emitted_node_count: z.number().int().nonnegative(),
    omitted_node_count: z.number().int().nonnegative(), source_truncated: z.boolean(),
    missing_endpoint_count: z.number().int().nonnegative(), cycle_edge_count: z.number().int().nonnegative(),
    topology_complete: z.boolean(),
  }),
  nodes: z.array(goalMapNodeSchema),
  edges: z.array(z.object({
    edge_id: z.string().min(1), from_node_id: z.string(), to_node_id: z.string(),
    relation: z.enum(["depends_on", "continues", "supersedes"]),
    enforcement: z.enum(["typed_lifecycle", "typed_condition", "lineage_only"]), reason: z.string(),
  })),
}).superRefine((map, context) => {
  const nodes = new Set(map.nodes.map(node => node.node_id));
  if (nodes.size !== map.nodes.length || new Set(map.edges.map(edge => edge.edge_id)).size !== map.edges.length
      || map.edges.some(edge => !nodes.has(edge.from_node_id) || !nodes.has(edge.to_node_id))) {
    context.addIssue({ code: "custom", message: "Goal map identities or endpoints are invalid" });
  }
});
const snapshotSchema = z.object({
  ok: z.literal(true), goal_id: z.string(), observed_at: z.string().datetime({ offset: true }),
  graph: graphSchema.nullable(), goal_map: goalMapSchema.nullable().optional(),
  acceptance: goalAcceptanceObservationSchema.nullable(),
});
export type DeliveryReviewSnapshot = z.infer<typeof snapshotSchema>;
export type ReviewGraph = NonNullable<DeliveryReviewSnapshot["graph"]>;
export type ReviewNode = ReviewGraph["nodes"][number];
export type ReviewRelation = ReviewGraph["edges"][number]["relation"];
export type ReviewFocus = "all" | "conditions" | "evidence" | "related";
type GoalMapNode = z.infer<typeof goalMapNodeSchema>;
type GoalMapEdge = z.infer<typeof goalMapSchema>["edges"][number];

export function parseDeliveryReview(value: unknown, goalId: string): DeliveryReviewSnapshot {
  const result = snapshotSchema.parse(value);
  if (result.goal_id !== goalId || (result.graph && result.graph.goal_id !== goalId)
      || (result.goal_map && result.goal_map.goal_id !== goalId)
      || (result.acceptance && result.acceptance.goal_id !== goalId)) {
    throw new Error("Review source does not match the selected Goal");
  }
  return result;
}

export async function fetchDeliveryReview(goalId: string, signal: AbortSignal) {
  const query = new URLSearchParams({ goal_id: goalId });
  const response = await fetch(`/api/chat/delivery-review?${query}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`Review unavailable (${response.status})`);
  return parseDeliveryReview(await response.json(), goalId);
}

/** Filtering changes visibility only. It never derives readiness or acceptance. */
export function filterReviewNodes(graph: ReviewGraph, query: string, focus: ReviewFocus, selected: string | null) {
  const related = new Set([selected]);
  for (const edge of graph.edges) {
    if (edge.from_node_id === selected) related.add(edge.to_node_id);
    if (edge.to_node_id === selected) related.add(edge.from_node_id);
  }
  const search = query.trim().toLocaleLowerCase();
  return graph.nodes.filter(node => {
    const matches = [node.title, node.owner_agent, node.actor_agent, node.from_agent, node.to_agent, ...Object.values(node.refs).flat()].some(value => value?.toLocaleLowerCase().includes(search));
    const visible = focus === "all"
      || (focus === "related" && related.has(node.node_id))
      || (focus === "conditions" && ["gate", "gate_summary", "lease"].includes(node.kind))
      || (focus === "evidence" && ["evidence", "validation", "repair", "handoff"].includes(node.kind));
    return matches && visible;
  });
}

export function reviewCoverageIncomplete(graph: ReviewGraph) {
  const limits = graph.limits;
  return limits.topology_complete !== true || limits.source_truncated === true
    || limits.predecessor_truncated === true || (limits.missing_predecessor_count ?? 0) > 0
    || limits.user_gate_truncated_count > 0;
}

export function reviewNodeColumn(node: ReviewNode) {
  return ["gate", "gate_summary", "lease"].includes(node.kind) ? 0 : node.kind === "deliverable" ? 1 : 2;
}

type EnabledContract = Extract<GoalAcceptanceContract, { enabled: true }>;
type ContractExportLabels = Record<"title" | "boundary" | "scope" | "selectedWork" | "allWork" | "source" | "revision" | "digest" | "objective" | "criteria"
  | "nonGoals" | "tasks" | "verification" | "unknown" | "noTasks" | "noCriteria" | "notApplicable"
  | "heldTasks" | "receipt" | "receiptNote" | "operation" | "verificationScope" | "allCriteria" | "passed" | "failed" | "exitCode", string> & {
  taskState: Record<EnabledContract["tasks"][number]["state"], string>;
  verificationState: Record<EnabledContract["status"], string>;
};
export type ReviewExportLabels = {
  contract: ContractExportLabels;
  title: string; scope: string; observed: string; chain: string; relations: string;
  acceptance: string; acceptanceBoundary: string; noGraph: string; incomplete: string;
  unavailable: string; refs: string; required: string; guards: string; next: string;
  owner: string; reason: string; historical: string; checks: string; missingSources: string; observedScope: string;
  kind: Record<ReviewNode["kind"], string>; state: Record<ReviewNode["state"], string>;
  relation: Record<ReviewRelation, string>;
  workMap: { title: string; boundary: string; kind: Record<GoalMapNode["kind"], string>; relation: Record<GoalMapEdge["relation"], string> } & Record<string, unknown>;
};

/** Export the entire validated snapshot, never the search-filtered screen. */
export function deliveryReviewMarkdown(snapshot: DeliveryReviewSnapshot, labels: ReviewExportLabels) {
  const line = (value: unknown) => String(value ?? labels.unavailable).replace(/[\\`*_{}[\]<>|#]/g, "\\$&").replace(/[\r\n]+/g, " ");
  const rows = [`# ${labels.title}`, "", `Goal: ${line(snapshot.goal_id)}`, `${labels.observed}: ${line(snapshot.observed_at)}`, "", labels.scope, "", labels.acceptanceBoundary, "", `## ${labels.chain}`, ""];
  const graph = snapshot.graph;
  if (!graph) rows.push(labels.noGraph);
  else {
    if (reviewCoverageIncomplete(graph)) rows.push(labels.incomplete, "");
    // Preserve exact coverage numbers/unknown flags instead of implying a full Goal graph.
    rows.push("```json", JSON.stringify(graph.limits, null, 2), "```", "");
    for (const node of graph.nodes) {
      rows.push(`- ${line(node.title)} · ${labels.kind[node.kind]} · ${labels.state[node.state]}${node.owner_agent ? ` · ${line(node.owner_agent)}` : ""}`,
        `  ${labels.refs}: ${line(node.node_id)}; ${line(JSON.stringify(node.refs))}`);
      if (node.from_agent || node.to_agent) rows.push(`  ${line(node.from_agent)} → ${line(node.to_agent)}`);
      if (node.actor_agent) rows.push(`  actor: ${line(node.actor_agent)}`);
    }
    const names = new Map(graph.nodes.map(node => [node.node_id, node.title]));
    rows.push("", `## ${labels.relations}`, "");
    for (const edge of graph.edges) rows.push(`- ${line(names.get(edge.from_node_id))} → ${labels.relation[edge.relation]} → ${line(names.get(edge.to_node_id))}: ${line(edge.reason)} (${line(edge.edge_id)})`, `  ${labels.refs}: ${line(JSON.stringify(edge.refs ?? {}))}`);
  }
  const map = snapshot.goal_map;
  if (map) {
    rows.push("", `## ${labels.workMap.title}`, "", labels.workMap.boundary, "", "```json", JSON.stringify(map.limits, null, 2), "```", "");
    const names = new Map(map.nodes.map(node => [node.node_id, node.title]));
    for (const node of map.nodes) rows.push(`- ${line(node.title)} · ${labels.workMap.kind[node.kind]} · ${labels.state[node.state]}${node.owner_agent ? ` · ${line(node.owner_agent)}` : ""} (${line(node.refs.todo_ids?.join(", "))})`);
    if (map.edges.length) rows.push("");
    for (const edge of map.edges) rows.push(`- ${line(names.get(edge.from_node_id))} → ${labels.workMap.relation[edge.relation]} → ${line(names.get(edge.to_node_id))}`);
  }
  rows.push("", `## ${labels.acceptance}`, "");
  const acceptance = snapshot.acceptance;
  if (!acceptance) rows.push(labels.unavailable);
  else {
    const available = acceptance.coverage === "partial";
    rows.push(`${labels.required}: ${available ? acceptance.acceptance_gaps.length : labels.unavailable}`, `${labels.guards}: ${available ? acceptance.guards.length : labels.unavailable}`, "");
    for (const gap of acceptance.acceptance_gaps) {
      rows.push(`### ${line(gap.evidence_required)}`, "", `${labels.owner}: ${line(gap.owner)}`,
        `${labels.reason}: ${line(gap.reason)}`, `${labels.observed}: ${line(gap.observed_at)}`,
        `${labels.refs}: ${line(gap.source)}`);
      if (gap.resolution_hint) rows.push(line(gap.resolution_hint));
      if (gap.component_checks) rows.push(`${labels.checks}:`, "```json", JSON.stringify(gap.component_checks, null, 2), "```");
      rows.push("");
    }
    rows.push(`### ${labels.guards}`, "");
    for (const guard of acceptance.guards) rows.push(`- ${line(guard.reason)}`,
      `  ${labels.owner}: ${line(guard.owner)}; ${labels.required}: ${line(guard.evidence_required)}`,
      `  ${labels.refs}: ${line(guard.todo_id)}; ${line(guard.blocks_agent)}; ${line(guard.decision_scope)}`);
    rows.push("", `### ${labels.historical}`, "");
    for (const progress of acceptance.historical_progress) rows.push(`- ${line(progress.kind)} · ${line(progress.observed_at)} · ${line(progress.source)} · ${line(progress.evidence_refs.join(", "))}`);
    rows.push("", `${labels.observedScope}: ${line(acceptance.coverage)}; truncated=${acceptance.truncated}`,
      `${labels.missingSources}: ${line(acceptance.missing_sources.join(", "))}`,
      `${labels.next}: ${line(acceptance.next_action)} (${line(acceptance.next_action_source)})`);
  }
  const contract = acceptance?.goal_acceptance_contract;
  if (contract?.enabled === true) {
    const copy = labels.contract;
    rows.push("", `## ${copy.title}`, "", copy.boundary,
      `${copy.scope}: ${contract.scope?.kind === "selected_work" ? copy.selectedWork : copy.allWork}`,
      `${copy.source}: ${line(snapshot.goal_id)}`, `${copy.revision}: ${contract.revision}`, `${copy.digest}: ${line(contract.digest)}`,
      "", `### ${copy.objective}`, line(contract.objective || copy.unknown), "", `### ${copy.criteria}`);
    if (contract.non_goals.length) rows.push(`${copy.nonGoals}: ${line(contract.non_goals.join("; "))}`);
    if (!contract.criteria.length) rows.push(copy.noCriteria);
    for (const criterion of contract.criteria) rows.push(`- ${line(criterion.id)}: ${line(criterion.description)}`);
    rows.push("", `### ${copy.tasks}`);
    if (!contract.tasks.length) rows.push(copy.noTasks);
    for (const task of contract.tasks) {
      rows.push(`- ${line(task.todo_id)}: ${copy.taskState[task.state]}`,
        `  ${copy.criteria}: ${line(task.criterion_ids.join(", ") || copy.unknown)}`);
      if (task.reason) rows.push(`  ${line(task.reason)}`);
      if (task.applicable === false) rows.push(`  ${copy.notApplicable}`);
    }
    rows.push("", `### ${copy.verification}`, copy.verificationState[contract.status]);
    if (contract.held_todo_ids.length) rows.push(`${copy.heldTasks}: ${line(contract.held_todo_ids.join(", "))}`);
    rows.push("", `### ${copy.receipt}`);
    const receipt = contract.verification;
    if (!receipt) rows.push(copy.unknown);
    else {
      rows.push(copy.receiptNote, `${copy.operation}: ${line(receipt.operation_id)}`,
        `${copy.revision}: ${receipt.contract_revision}`, `${copy.digest}: ${line(receipt.contract_digest)}`,
        `${copy.verificationScope}: ${line(receipt.todo_id ?? copy.allCriteria)}`);
      for (const result of receipt.results) rows.push(`- ${line(result.criterion_id)}: ${result.passed ? copy.passed : copy.failed}; ${copy.exitCode}: ${result.exit_code ?? copy.unknown}`);
    }
  }
  return rows.join("\n") + "\n";
}
