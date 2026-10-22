/** Compact Explore read model. Existing Graph/Harness owners retain all gates. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireInteger, requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";
import {PLANNING_TODO_ID} from "../work_items/planning_relations.ts";

import {EXPLORE_WRITEBACK_SUMMARY_LIMIT, exploreResultWritebackAffordance} from "./explore_result_writeback.ts";
import {EXPLORE_RESULT_PAGE_MAX, normalizeExploreResultPages} from "./explore_result_page.ts";
import {projectExploreMeasurementFeedback} from "./explore_measurements.ts";

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(item => requireJsonObject(item, "Explore row")) : [];
}
function compact(row: JsonObject, fields: string[]): JsonObject {
  return Object.fromEntries(fields.filter(key => row[key] !== undefined)
    .map(key => [key, typeof row[key] === "string" ? (row[key] as string).slice(0, 240) : row[key]]));
}
function findingDigest(row: JsonObject, findingSources?: Map<string, JsonObject>): JsonObject {
  const source = findingSources?.get(String(row.finding_id));
  return source ? {source_sha256: createHash("sha256").update(JSON.stringify(source)).digest("hex")} : {};
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
function branchContext(row: JsonObject, findingSources?: Map<string, JsonObject>): JsonObject {
  const result = compact(row, ["todo_id", "text", "branch_role", "score", "confidence",
    "selection_status", "status", "actionable_open", "resume_when", "resume_ready"]);
  if (row.typed_evidence_audit == null) return result;
  const audit = requireJsonObject(row.typed_evidence_audit, "Explore evidence audit");
  const nodes = rows(audit.nodes), findings = rows(audit.findings), edges = rows(audit.relevant_edges);
  const requested = requireStringArray(audit.requested_node_refs ?? [], "requested_node_refs");
  const unknown = requireStringArray(audit.unknown_node_refs ?? [], "unknown_node_refs");
  // Preserve the existing diagnostic owner: no score change, inferred links or
  // new execution gate. Full status counts include details omitted upstream.
  result.typed_evidence_audit = {
    ...compact(audit, ["mode", "score_delta", "hazards"]),
    requested_node_refs: requested.slice(0, 8),
    unknown_node_refs: unknown.slice(0, 8),
    omitted_requested_node_refs: Math.max(0, requested.length - 8),
    omitted_unknown_node_refs: Math.max(0, unknown.length - 8),
    nodes: nodes.slice(0, 3).map(node => compact(node, ["node_id", "title", "status"])),
    findings: findings.slice(0, 3).map(finding => ({...compact(finding, ["finding_id", "node_id", "finding", "status"]),
      ...findingDigest(finding, findingSources)})),
    relevant_edges: edges.slice(0, 3).map(edge => compact(edge, ["from_node", "to_node", "edge_type"])),
    omitted_audit_nodes: Math.max(0, nodes.length - 3),
    omitted_audit_findings: Math.max(0, evidenceCount(audit, "findings", findings.length) - 3),
    omitted_audit_edges: Math.max(0, evidenceCount(audit, "edges", edges.length) - 3),
  };
  return result;
}
/** Keep the latest scoped counterexample per linked question before its other
 * findings; recency alone must not supersede evidence on different inputs. */
function prioritizeLinkedResults(results: JsonObject[], refs: Set<string>, fallbackResults = results): JsonObject[] {
  const linked = results.filter(row => refs.has(String(row.node_id ?? "")));
  const refuted = new Set<string>();
  const counterexamples = linked.filter(row => {
    const node = String(row.node_id ?? "");
    if (row.status !== "refuted" || refuted.has(node)) return false;
    refuted.add(node);
    return true;
  });
  const reserved = new Set(counterexamples);
  return [...counterexamples, ...linked.filter(row => !reserved.has(row)),
    ...fallbackResults.filter(row => !refs.has(String(row.node_id ?? "")))];
}
export function projectExploreTurnContext(params: JsonObject): JsonObject {
  const goal = requireNonEmptyString(params.goal_id, "goal_id");
  const agent = requireNonEmptyString(params.agent_id, "agent_id");
  const route = requireStringArray(params.route, "route");
  const gate = requireJsonObject(params.harness_gate, "harness_gate");
  const graph = params.graph_enabled === true;
  const harness = gate.enabled === true;
  let todoId: string | null = null;
  let focusedRefs = new Set<string>();
  if ((graph || harness) && params.todo_id != null) {
    todoId = requireNonEmptyString(params.todo_id, "Explore task scope");
    const todo = requireJsonObject(params.focus_todo, "Explore task scope requires a current canonical Todo");
    if (!PLANNING_TODO_ID.test(todoId) || todo.todo_id !== todoId || todo.role !== "agent"
      || todo.status !== "open" || todo.done === true || todo.claimed_by && todo.claimed_by !== agent) {
      throw new EffectRuntimeRequestError("Explore task scope requires an open Todo available to this agent; reread the task");
    }
    focusedRefs = new Set(requireStringArray(todo.explore_result_node_refs ?? [], "Explore task question refs"));
  }
  const projection = requireJsonObject(params.projection, "projection");
  const plan = requireJsonObject(params.plan, "plan");
  // The canonical graph retains creation order; this bounded view needs update order.
  const nodes = rows(projection.nodes).sort((a, b) =>
    String(b.last_updated_at ?? "").localeCompare(String(a.last_updated_at ?? "")));
  const findings = rows(projection.findings);
  if (params.include_finding_digests != null && typeof params.include_finding_digests !== "boolean") {
    throw new EffectRuntimeRequestError("include_finding_digests must be a boolean");
  }
  // Retain full sources, but hash only findings emitted by the bounded view.
  const findingSources = params.include_finding_digests === true ? new Map(findings.map(row =>
    [String(row.finding_id), row])) : undefined;
  const branches = rows(plan.selected_branches);
  const rejected = rows(plan.rejected_candidates);
  const frontier = rows(projection.frontier);
  const selectedRefs = new Set(harness ? branches.slice(0, 3).flatMap(branch => {
    if (branch.typed_evidence_audit == null) return [];
    const audit = requireJsonObject(branch.typed_evidence_audit, "Explore evidence audit");
    return requireStringArray(audit.requested_node_refs ?? [], "Explore node refs");
  }) : []);
  // Manual quantitative capture uses the same finding owner. Keep its sources
  // available to the existing exact-result decision path, without requiring a
  // writeback-origin tag or admitting unrelated unmeasured notes.
  const decisionResults = findings.filter(row => row.measurements !== undefined
    || Array.isArray(row.tags) && row.tags.includes("writeback-result"));
  // Keep the same detail budget, but do not let unrelated newer results evict
  // the applicability of evidence linked to the next selected work item.
  // Explicitly bound work can differ from this independent planner. Prioritize
  // its canonical links without changing candidates, scores or admission.
  const plannerResults = prioritizeLinkedResults(decisionResults, selectedRefs);
  // Shared links must not inherit another task's planner priority. Use source
  // recency within the focus and retain planner ordering only outside it.
  const writebackResults = focusedRefs.size
    ? prioritizeLinkedResults(decisionResults, focusedRefs, plannerResults) : plannerResults;
  const command = (...args: string[]) => [...route, "explore", ...args, "--goal-id", goal];
  const resultLimit = requireInteger(params.result_limit ?? 3, "result_limit");
  const resultOffset = requireInteger(params.result_offset ?? 0, "result_offset");
  if (resultLimit < 1 || resultLimit > EXPLORE_RESULT_PAGE_MAX || resultOffset < 0) {
    throw new EffectRuntimeRequestError("Use result_limit 1..20 and a nonnegative result_offset");
  }
  const resultNode = params.result_node == null ? null : requireNonEmptyString(params.result_node, "result_node");
  const measurementFeedback = projectExploreMeasurementFeedback(findings)
    .filter(item => resultNode === null || item.node_id === resultNode)
    .sort((a, b) => Number(focusedRefs.has(b.node_id)) - Number(focusedRefs.has(a.node_id))
      || (focusedRefs.has(a.node_id) ? 0
        : Number(selectedRefs.has(b.node_id)) - Number(selectedRefs.has(a.node_id))));
  const resultRows = resultNode == null ? writebackResults : writebackResults.filter(row => row.node_id === resultNode);
  const nodeById = new Map(nodes.map(node => [node.node_id, node]));
  // Bind current Todo links to the results on this page. Include the full
  // planner's nonselected branches: a heuristic/batch budget cannot prevent
  // the Agent from comparing a lower-ranked method. This grants no claim,
  // lease, quota or execution permission; their existing owners still apply.
  const comparisonCandidates = (page: JsonObject[], limit: number): JsonObject => {
    if (!harness) return {};
    const linkedCandidates = [...branches, ...rejected].flatMap(branch => {
      if (typeof branch.todo_id !== "string" || !PLANNING_TODO_ID.test(branch.todo_id)
        || branch.actionable_open !== true || branch.claimed_by && branch.claimed_by !== agent
        || branch.typed_evidence_audit == null) return [];
      const audit = requireJsonObject(branch.typed_evidence_audit, "Explore evidence audit");
      const linked = new Set(requireStringArray(audit.requested_node_refs ?? [], "Explore node refs")
        .filter(ref => nodeById.has(ref)));
      // The explicit page already bounds these results. A second three-row
      // cap would display findings that the same page cannot bind at writeback.
      const outcomes = page.filter(row => linked.has(String(row.node_id)));
      return outcomes.length ? [{todo_id: branch.todo_id, outcomes}] : [];
    });
    if (!linkedCandidates.length) return {};
    return {
      comparison_candidates: linkedCandidates.slice(0, limit).map(card => ({todo_id: card.todo_id,
        outcomes: card.outcomes.map(row => ({node_id: row.node_id, finding_id: row.finding_id,
          source_sha256: createHash("sha256").update(JSON.stringify(row)).digest("hex")}))})),
      omitted_comparison_candidates: Math.max(0, linkedCandidates.length - limit),
    };
  };
  const relationsByNode = new Map<unknown, JsonObject[]>();
  for (const edge of rows(projection.edges)) {
    for (const id of new Set([edge.from_node, edge.to_node])) {
      const incident = relationsByNode.get(id) ?? [];
      incident.push(edge);
      relationsByNode.set(id, incident);
    }
  }
  const currentQuestion = (row: JsonObject): JsonObject => {
    const node = nodeById.get(row.node_id);
    const relations = relationsByNode.get(row.node_id) ?? [];
    return {
      ...(node ? {question_status: node.status,
        ...(node.blocked_reason ? {question_blocked_reason: node.blocked_reason} : {})} : {}),
      ...(relations.length ? {
        relations: relations.slice(0, 3).map(edge => ({...compact(edge, ["from_node", "to_node", "edge_type"]),
          summary: String(edge.summary ?? "").slice(0, 320),
          ...(String(edge.summary ?? "").length > 320 ? {summary_truncated: true} : {}),
        })),
        omitted_relations: Math.max(0, relations.length - 3),
      } : {}),
    };
  };
  const resultRevision = createHash("sha256").update(JSON.stringify([
    goal, agent, resultNode, resultRows, resultRows.map(currentQuestion),
    // Manual findings participate in feedback too, even without a writeback
    // tag. Bind every compared source before paging either derived view.
    ...(measurementFeedback.length ? [findings.filter(row => row.measurements !== undefined
      && (resultNode === null || row.node_id === resultNode))] : []),
    ...(todoId === null ? [] : [[todoId, [...focusedRefs]]]),
  ])).digest("hex");
  if (params.result_revision != null && params.result_revision !== resultRevision) {
    throw new EffectRuntimeRequestError("Explore evidence changed; restart at result_offset 0 without result_revision");
  }
  if (resultOffset > 0 && params.result_revision == null) {
    throw new EffectRuntimeRequestError("Continuation requires result_revision from the previous page");
  }
  const visibleResults = resultRows.slice(resultOffset, resultOffset + resultLimit);
  // Write-time replay uses this same source snapshot and ordering. The caller
  // supplies only page coordinates, never the findings admitted by that page.
  const reviewPages = params.review_pages === undefined ? null : normalizeExploreResultPages(params.review_pages).flatMap(page => {
    if (!graph || resultNode !== null) return [];
    if ((page.todo_id ?? null) !== todoId) {
      // Replay an explicitly read task page from this same canonical snapshot,
      // without replacing the originally delivered global replan basis. Caller
      // page coordinates supply no findings, links, ownership or authority.
      const matches = rows(params.review_todos).filter(row => row.todo_id === page.todo_id);
      if (page.todo_id !== undefined && matches.length !== 1) return [];
      try {
        const replay = projectExploreTurnContext({...params, todo_id: page.todo_id,
          focus_todo: matches[0], review_pages: undefined, result_offset: page.offset,
          result_limit: page.limit, result_revision: page.revision});
        const found = (replay.graph as JsonObject).writeback_results as JsonObject[];
        const choices = replay.harness as JsonObject | null;
        return [{...page, finding_refs: found.map(row => row.finding_id),
          ...(choices?.comparison_candidates ? {comparison_candidates: choices.comparison_candidates,
            omitted_comparison_candidates: choices.omitted_comparison_candidates} : {})}];
      } catch (error) {
        if (error instanceof EffectRuntimeRequestError) return [];
        throw error;
      }
    }
    if (page.revision !== resultRevision) {
      // A stale query is not an unavailable source. Retain the current bounded
      // context/read route; the replan owner refuses any unresolved citation.
      return [];
    }
    const found = resultRows.slice(Number(page.offset), Number(page.offset) + Number(page.limit));
    return [{...page, finding_refs: found.map(row => row.finding_id),
      ...comparisonCandidates(found, Number(page.limit))}];
  });
  const visibleComparisons = comparisonCandidates(visibleResults, resultLimit);
  const remainingResults = Math.max(0, resultRows.length - resultOffset - visibleResults.length);
  const visibleMeasurements = measurementFeedback.slice(resultOffset, resultOffset + resultLimit);
  const remainingMeasurements = Math.max(0, measurementFeedback.length - resultOffset - visibleMeasurements.length);
  const resultRead = (...args: string[]) => command("turn-context", "--agent-id", agent,
    ...(todoId === null ? [] : ["--todo-id", todoId]), ...args);
  const nextRead = (remaining: number, count: number) => remaining ? resultRead(
    "--result-offset", String(resultOffset + count),
    "--result-limit", String(resultLimit), "--result-revision", resultRevision,
    ...(resultNode == null ? [] : ["--result-node", resultNode]),
  ) : null;
  const writeback = exploreResultWritebackAffordance();
  return {
    ok: true, goal_id: goal, agent_id: agent, graph_enabled: graph, harness_enabled: harness,
    ...(todoId === null ? {} : {todo_id: todoId}),
    ...(reviewPages ? {review_pages: reviewPages} : {}),
    graph: graph ? {
      counts: projection.counts ?? {},
      recent_nodes: nodes.slice(0, 3).map(row => compact(row, ["node_id", "title", "status", "blocked_reason"])),
      // The canonical evidence projection calls the finding's title `finding`.
      recent_findings: findings.slice(0, 3).map(row => ({...compact({...row, title: row.finding}, ["finding_id", "node_id", "title", "status"]),
        ...findingDigest(row, findingSources)})),
      // Full scoped summaries; progressive reads can raise the page size.
      // These retain the observation AND applicability; clipping away conditions
      // could turn a bounded refutation into a blanket route ban.
      writeback_results: visibleResults
        .map(row => ({...compact(row, ["finding_id", "node_id", "finding", "status", "evidence_refs"]),
          summary: String(row.summary ?? "").slice(0, EXPLORE_WRITEBACK_SUMMARY_LIMIT),
          ...(row.execution_observation ? {execution_observation: row.execution_observation} : {}),
          ...(row.measurements ? {measurements: row.measurements} : {}),
          ...currentQuestion(row),
          ...findingDigest(row, findingSources)})),
      result_page: {
        total: resultRows.length, offset: resultOffset, limit: resultLimit,
        ...(todoId === null ? {} : {todo_id: todoId}),
        remaining: remainingResults, revision: resultRevision,
        next_command: nextRead(remainingResults, visibleResults.length),
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
      optional_question_evolution_example: writeback.optional_question_evolution_example,
      result_writeback_guidance: writeback.guidance,
      result_attachment_template: writeback.attachment_template,
      optional_measurement_example: writeback.optional_measurement_example,
      ...(measurementFeedback.length ? {
        measurement_feedback: visibleMeasurements,
        omitted_measurement_feedback: remainingMeasurements,
        measurement_feedback_page: {
          total: measurementFeedback.length, offset: resultOffset, limit: resultLimit,
          ...(todoId === null ? {} : {todo_id: todoId}),
          remaining: remainingMeasurements, revision: resultRevision,
          next_command: nextRead(remainingMeasurements, visibleMeasurements.length),
        },
        measurement_guidance: "Compare only matching metric, unit, direction, comparison_scope, input_revision and evaluation_ref. Positive improvement means the latest recorded result beat its prior best; negative means a regression. A singleton group's null improvement is not zero gain: check whether candidate revisions or individual receipts were put in input_revision/evaluation_ref instead of candidate_ref/evidence_ref. Correct a recorded finding only with verified provenance; never merge unknown or changed conditions. Use the candidate/evidence references to investigate or return to a better candidate before repeating work. Latest recorded is not current or retained; best is not acceptance, a causal effect or authorization. Unknown values are not zero. Follow measurement_feedback_page.next_command for omitted groups, or filter by node; existing result limit/offset/revision options apply and changed evidence requires restarting the read.",
      } : {}),
      record_node_template: command("node", "--title", "<hypothesis or experiment>", "--status", "exploring"),
      record_finding_template: command("finding", "--node", "<node-id>", "--title", "<evidence-backed result>", "--status", "<tentative|confirmed|refuted>"),
      guidance: (todoId === null ? "" : "To cite this task-scoped page, retain todo_id with revision/offset/limit in explore_review.result_pages; keep the original Explore basis revision. Other tasks' evidence remains available. ") +
        "Use result_page.next_command for omitted results or node_command_template for a question; --result-limit can expand each page up to 20. Use existing evidence before repeating a route. Record meaningful hypotheses and supported or refuted results with stable node ids. Fill templates from actual evidence; do not create ceremonial nodes or infer findings from a score alone.",
    } : null,
    harness: harness ? {
      orchestration_gate: gate,
      candidate_count: plan.candidate_count ?? 0,
      selected_branches: branches.slice(0, 3).map(branch => branchContext(branch, findingSources)),
      ...visibleComparisons,
      omitted_selected_branches: Math.max(0, branches.length - 3),
      rejected_candidates: rejected.slice(0, 3).map(branch => branchContext(branch)),
      omitted_rejected_candidates: Math.max(0,
        requireInteger(plan.rejected_candidate_count ?? rejected.length, "rejected_candidate_count") - 3),
      frontier: frontier.slice(0, 3).map(node => compact(node, ["node_id", "title", "status", "summary"])),
      omitted_frontier: Math.max(0, frontier.length - 3),
      // Expand the same Todo audit shown above, not a different worker-lane plan.
      plan_command: command("todo-branch-plan", "--agent-id", agent),
      link_evidence_template: [...route, "todo", "update", "--goal-id", goal, "--agent-id", agent, "--todo-id", "<todo-id>", "--explore-result-node-ref", "<node-id>"],
      guidance: "Before choosing an experiment, compare its question with linked findings and the active frontier. Explain which evidence supports changing or retaining the route, and what the next probe can distinguish. Refutation is scoped evidence, not a permanent ban: a retry can test changed conditions or uncertainty. Link relevant existing nodes to the Todo; missing links mean unknown evidence, not a clean route. With one candidate or no active frontier, form useful alternatives only when evidence warrants them. Record results on the same hypothesis and adopt the decision through the ordinary task step or successor. These are decision guidelines, not a new writeback gate or permission to spawn. Read the full planner/summary when this bounded view is insufficient; normal quota, claim and lease admission still applies.",
    } : null,
    boundary: {read_only: true, changes_configuration: false, writes_evidence: false,
      claims_todos: false, starts_agents: false, changes_quota: false},
  };
}
