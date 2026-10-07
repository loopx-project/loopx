/** Compact Explore read model. Existing Graph/Harness owners retain all gates. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";

import {EXPLORE_WRITEBACK_SUMMARY_LIMIT, exploreResultWritebackAffordance} from "./explore_result_writeback.ts";

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(item => requireJsonObject(item, "Explore row")) : [];
}
function compact(row: JsonObject, fields: string[]): JsonObject {
  return Object.fromEntries(fields.filter(key => row[key] !== undefined)
    .map(key => [key, typeof row[key] === "string" ? (row[key] as string).slice(0, 240) : row[key]]));
}
function evidenceCount(audit: JsonObject, kind: string, detailCount: number): number {
  if (audit.status_counts == null) return detailCount;
  const counts = requireJsonObject(audit.status_counts, "Explore evidence status counts");
  if (counts[kind] == null) return detailCount;
  const statuses = requireJsonObject(counts[kind], `Explore ${kind} status counts`);
  let total = 0;
  for (const value of Object.values(statuses)) {
    const count = requireInteger(value, `Explore ${kind} count`);
    if (count < 0) throw new EffectRuntimeRequestError(`Explore ${kind} count must be nonnegative`);
    total += count;
  }
  return Math.max(detailCount, total);
}
function branchContext(row: JsonObject): JsonObject {
  const result = compact(row, ["todo_id", "text", "branch_role", "score", "confidence"]);
  if (row.typed_evidence_audit == null) return result;
  const audit = requireJsonObject(row.typed_evidence_audit, "Explore evidence audit");
  const nodes = rows(audit.nodes), findings = rows(audit.findings), edges = rows(audit.relevant_edges);
  // Preserve the existing diagnostic owner: no score change, inferred links or
  // new execution gate. Full status counts include details omitted upstream.
  result.typed_evidence_audit = {
    ...compact(audit, ["mode", "score_delta", "requested_node_refs", "unknown_node_refs", "hazards"]),
    nodes: nodes.slice(0, 3).map(node => compact(node, ["node_id", "title", "status"])),
    findings: findings.slice(0, 3).map(finding => compact(finding, ["finding_id", "node_id", "finding", "status"])),
    relevant_edges: edges.slice(0, 3).map(edge => compact(edge, ["from_node", "to_node", "edge_type"])),
    omitted_audit_nodes: Math.max(0, nodes.length - 3),
    omitted_audit_findings: Math.max(0, evidenceCount(audit, "findings", findings.length) - 3),
    omitted_audit_edges: Math.max(0, evidenceCount(audit, "edges", edges.length) - 3),
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
  const selectedRefs = new Set(harness ? branches.slice(0, 3).flatMap(branch => {
    if (branch.typed_evidence_audit == null) return [];
    const audit = requireJsonObject(branch.typed_evidence_audit, "Explore evidence audit");
    return requireStringArray(audit.requested_node_refs ?? [], "Explore node refs");
  }) : []);
  const attachedResults = findings.filter(row => Array.isArray(row.tags) && row.tags.includes("writeback-result"));
  // Keep the same detail budget, but do not let unrelated newer results evict
  // the applicability of evidence linked to the next selected work item.
  const linkedResults = attachedResults.filter(row => selectedRefs.has(String(row.node_id ?? "")));
  // Reserve detail slots for the latest refutation of each linked question.
  // Repeated findings on one question must not evict another question's scope.
  // A newer positive observation may apply to different inputs; recency alone
  // cannot supersede that counterexample. Fill remaining slots by recency.
  // Projection already resolves revisions of the same finding; no history or
  // scheduler state is changed here.
  const refutedQuestions = new Set<string>();
  const counterexamples = linkedResults.filter(row => {
    const node = String(row.node_id ?? "");
    if (row.status !== "refuted" || refutedQuestions.has(node)) return false;
    refutedQuestions.add(node);
    return true;
  });
  const reserved = new Set(counterexamples);
  const writebackResults = [
    ...counterexamples,
    ...linkedResults.filter(row => !reserved.has(row)),
    ...attachedResults.filter(row => !selectedRefs.has(String(row.node_id ?? ""))),
  ];
  const command = (...args: string[]) => [...route, "explore", ...args, "--goal-id", goal];
  const resultLimit = requireInteger(params.result_limit ?? 3, "result_limit");
  const resultOffset = requireInteger(params.result_offset ?? 0, "result_offset");
  if (resultLimit < 1 || resultLimit > 20 || resultOffset < 0) {
    throw new EffectRuntimeRequestError("Use result_limit 1..20 and a nonnegative result_offset");
  }
  const resultNode = params.result_node == null ? null : requireNonEmptyString(params.result_node, "result_node");
  const resultRows = resultNode == null ? writebackResults : writebackResults.filter(row => row.node_id === resultNode);
  const resultRevision = createHash("sha256").update(JSON.stringify([goal, agent, resultNode, resultRows])).digest("hex");
  if (params.result_revision != null && params.result_revision !== resultRevision) {
    throw new EffectRuntimeRequestError("Explore evidence changed; restart at result_offset 0 without result_revision");
  }
  if (resultOffset > 0 && params.result_revision == null) {
    throw new EffectRuntimeRequestError("Continuation requires result_revision from the previous page");
  }
  const visibleResults = resultRows.slice(resultOffset, resultOffset + resultLimit);
  const remainingResults = Math.max(0, resultRows.length - resultOffset - visibleResults.length);
  const resultRead = (...args: string[]) => command("turn-context", "--agent-id", agent, ...args);
  const writeback = exploreResultWritebackAffordance();
  return {
    ok: true, goal_id: goal, agent_id: agent, graph_enabled: graph, harness_enabled: harness,
    graph: graph ? {
      counts: projection.counts ?? {},
      recent_nodes: nodes.slice(0, 3).map(row => compact(row, ["node_id", "title", "status", "blocked_reason"])),
      // The canonical evidence projection calls the finding's title `finding`.
      recent_findings: findings.slice(0, 3).map(row => compact({...row, title: row.finding}, ["finding_id", "node_id", "title", "status"])),
      // Full scoped summaries; progressive reads can raise the page size.
      // These retain the observation AND applicability; clipping away conditions
      // could turn a bounded refutation into a blanket route ban.
      writeback_results: visibleResults
        .map(row => ({...compact(row, ["finding_id", "node_id", "finding", "status", "evidence_refs"]),
          summary: String(row.summary ?? "").slice(0, EXPLORE_WRITEBACK_SUMMARY_LIMIT)})),
      result_page: {
        total: resultRows.length, offset: resultOffset, limit: resultLimit,
        remaining: remainingResults, revision: resultRevision,
        next_command: remainingResults ? resultRead(
          "--result-offset", String(resultOffset + visibleResults.length),
          "--result-limit", String(resultLimit), "--result-revision", resultRevision,
          ...(resultNode == null ? [] : ["--result-node", resultNode]),
        ) : null,
        node_command_template: resultRead("--result-node", "<node-id>", "--result-limit", "10"),
      },
      omitted_nodes: Math.max(0, nodes.length - 3),
      summary_command: command("summary"),
      result_writeback_option: writeback.option,
      result_writeback_inline_option: writeback.inline_option,
      result_writeback_inline_field: writeback.inline_field,
      result_attachment_schema: writeback.attachment_schema,
      path_delta_attachment_schema: writeback.path_delta_attachment_schema,
      path_delta_attachment_template: writeback.path_delta_attachment_template,
      linked_question_attachment_template: writeback.linked_question_attachment_template,
      result_writeback_guidance: writeback.guidance,
      result_attachment_template: writeback.attachment_template,
      record_node_template: command("node", "--title", "<hypothesis or experiment>", "--status", "exploring"),
      record_finding_template: command("finding", "--node", "<node-id>", "--title", "<evidence-backed result>", "--status", "<tentative|confirmed|refuted>"),
      guidance: "Use result_page.next_command for omitted results or node_command_template for a question; --result-limit can expand each page up to 20. Use existing evidence before repeating a route. Record meaningful hypotheses and supported or refuted results with stable node ids. Fill templates from actual evidence; do not create ceremonial nodes or infer findings from a score alone.",
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
