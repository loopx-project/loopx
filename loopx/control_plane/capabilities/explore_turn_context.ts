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
      selected_branches: branches.slice(0, 3).map(row => compact(row, ["todo_id", "text", "branch_role", "score", "confidence"])),
      omitted_selected_branches: Math.max(0, branches.length - 3),
      plan_command: command("worker-branch-plan", "--agent-id", agent),
      guidance: "Use the read-only planner when choosing among real alternatives. With one candidate, identify useful alternatives only when evidence warrants them. Planning neither launches workers nor changes spawn permission; execute through normal quota, claim and lease admission.",
    } : null,
    boundary: {read_only: true, changes_configuration: false, writes_evidence: false,
      claims_todos: false, starts_agents: false, changes_quota: false},
  };
}
