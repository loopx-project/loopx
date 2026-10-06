/** Compact Explore read model. Existing Graph/Harness owners retain all gates. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(item => requireJsonObject(item, "Explore row")) : [];
}
function compact(row: JsonObject, fields: string[]): JsonObject {
  return Object.fromEntries(fields.filter(key => row[key] !== undefined)
    .map(key => [key, typeof row[key] === "string" ? (row[key] as string).slice(0, 240) : row[key]]));
}
function branchContext(row: JsonObject): JsonObject {
  const result = compact(row, ["todo_id", "text", "branch_role", "score", "confidence"]);
  if (row.typed_evidence_audit == null) return result;
  const audit = requireJsonObject(row.typed_evidence_audit, "Explore evidence audit");
  const nodes = rows(audit.nodes), findings = rows(audit.findings), edges = rows(audit.relevant_edges);
  // Preserve the existing diagnostic owner: no score change, inferred links or
  // new execution gate. Counts describe this already bounded upstream audit.
  result.typed_evidence_audit = {
    ...compact(audit, ["mode", "score_delta", "requested_node_refs", "unknown_node_refs", "hazards"]),
    nodes: nodes.slice(0, 3).map(node => compact(node, ["node_id", "title", "status"])),
    findings: findings.slice(0, 3).map(finding => compact(finding, ["finding_id", "node_id", "finding", "status"])),
    relevant_edges: edges.slice(0, 3).map(edge => compact(edge, ["from_node", "to_node", "edge_type"])),
    omitted_audit_nodes: Math.max(0, nodes.length - 3),
    omitted_audit_findings: Math.max(0, findings.length - 3),
    omitted_audit_edges: Math.max(0, edges.length - 3),
  };
  return result;
}
export function projectExploreTurnContext(params: JsonObject): JsonObject {
  const goal = requireNonEmptyString(params.goal_id, "goal_id");
  const agent = requireNonEmptyString(params.agent_id, "agent_id");
  const route = requireStringArray(params.route, "route");
  const gate = requireJsonObject(params.harness_gate, "harness_gate");
  const graph = params.graph_enabled === true;
  const harness = gate.enabled === true;
  const projection = requireJsonObject(params.projection, "projection");
  const plan = requireJsonObject(params.plan, "plan");
  // The canonical graph retains creation order; this bounded view needs update order.
  const nodes = rows(projection.nodes).sort((a, b) =>
    String(b.last_updated_at ?? "").localeCompare(String(a.last_updated_at ?? "")));
  const findings = rows(projection.findings);
  const branches = rows(plan.selected_branches);
  const frontier = rows(projection.frontier);
  const command = (...args: string[]) => [...route, "explore", ...args, "--goal-id", goal];
  return {
    ok: true, goal_id: goal, agent_id: agent, graph_enabled: graph, harness_enabled: harness,
    graph: graph ? {
      counts: projection.counts ?? {},
      recent_nodes: nodes.slice(0, 3).map(row => compact(row, ["node_id", "title", "status", "blocked_reason"])),
      // The canonical evidence projection calls the finding's title `finding`.
      recent_findings: findings.slice(0, 3).map(row => compact({...row, title: row.finding}, ["finding_id", "node_id", "title", "status"])),
      omitted_nodes: Math.max(0, nodes.length - 3),
      summary_command: command("summary"),
      record_node_template: command("node", "--title", "<hypothesis or experiment>", "--status", "exploring"),
      record_finding_template: command("finding", "--node", "<node-id>", "--title", "<evidence-backed result>", "--status", "<tentative|confirmed|refuted>"),
      guidance: "Use existing evidence before repeating a route. Record meaningful hypotheses and supported or refuted results with stable node ids. Fill templates from actual evidence; do not create ceremonial nodes or infer findings from a score alone.",
    } : null,
    harness: harness ? {
      orchestration_gate: gate,
      candidate_count: plan.candidate_count ?? 0,
      selected_branches: branches.slice(0, 3).map(branchContext),
      omitted_selected_branches: Math.max(0, branches.length - 3),
      frontier: frontier.slice(0, 3).map(node => compact(node, ["node_id", "title", "status", "summary"])),
      omitted_frontier: Math.max(0, frontier.length - 3),
      plan_command: command("worker-branch-plan", "--agent-id", agent),
      link_evidence_template: [...route, "todo", "update", "--goal-id", goal, "--agent-id", agent, "--todo-id", "<todo-id>", "--explore-result-node-ref", "<node-id>"],
      guidance: "Before choosing an experiment, compare its question with linked findings and the active frontier. Explain which evidence supports changing or retaining the route, and what the next probe can distinguish. Refutation is scoped evidence, not a permanent ban: a retry can test changed conditions or uncertainty. Link relevant existing nodes to the Todo; missing links mean unknown evidence, not a clean route. With one candidate or no active frontier, form useful alternatives only when evidence warrants them. Record results on the same hypothesis and adopt the decision through the ordinary task step or successor. These are decision guidelines, not a new writeback gate or permission to spawn. Read the full planner/summary when this bounded view is insufficient; normal quota, claim and lease admission still applies.",
    } : null,
    boundary: {read_only: true, changes_configuration: false, writes_evidence: false,
      claims_todos: false, starts_agents: false, changes_quota: false},
  };
}
