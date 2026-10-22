/** One opt-in work and research stride strategy; all execution authority retains its owners. */
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import schema from "../stride_strategy_schema.json" with {type: "json"};
import decisionSchema from "../stride_decision_schema.json" with {type: "json"};
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString, requireStringLiteral} from "../runtime_decode.ts";
import {EXPLORE_RESULT_PAGE_MAX, normalizeExploreResultPages} from "../capabilities/explore_result_page.ts";
import {PLANNING_TODO_ID} from "./planning_relations.ts";

export function normalizeStrideStrategy(value: unknown): string {
  if (value === undefined) return schema.default;
  if (typeof value !== "string" || !schema.enum.includes(value)) {
    throw new EffectRuntimeRequestError("stride_strategy must be off or evidence_conditioned_v1");
  }
  return value;
}
// Read-only questions for the existing three axes; not another decision schema,
// result classifier, route ban or admission authority.
const decisionValueReview = {
  work_slice: "Compare the complete cost and useful outcome of continuation with one feasible alternative. " +
    "Assess the quality of the first deliverable and later gain beyond the retained best separately; " +
    "recovering an avoidable initial quality deficit is not evidence of additional capability. " +
    "Name the observation that would change that choice, and size a complete experiment to obtain it. " +
    "A larger test count is not greater decision value; preserve unfinished decisive work rather than restart its setup.",
  route_distance: "Name the remaining authoritative goal gap, its limiting component and the plausible headroom. " +
    "Keep the retained best artifact as the comparison anchor and check whether subsequent work actually implements the proposed mechanism change. " +
    "Compare a different approach that can affect that component. Distinguish an unexecuted intervention, failed transfer and insufficient route potential: they imply different next experiments. " +
    "When evidence weakens the current route, generate a small set of genuinely different explanations or approaches before selecting one: consider representation, decomposition, search method or information source where relevant. " +
    "Contrast their predictions and full costs, then choose a discriminating experiment; parameter variants of the same mechanism are not independent alternatives. " +
    "Missing feedback is a wait or evidence gap, not proof of route failure or exhausted alternatives. " +
    "A changed auxiliary mechanism alone does not show that it addresses the gap.",
  validation_scope: "Use evidence applicable to the limiting outcome and changed mechanism. " +
    "Bind comparisons to the artifact version, input conditions and original budget, and distinguish when feedback became available from when work was submitted. " +
    "Keep decision, execution and measured outcome separate; a saved snapshot is not proof of restoration or continued execution. " +
    "First establish that the intervention actually changes the relevant behavior; unchanged outcomes may require tracing its activation rather than a wider parameter sweep. " +
    "Choose a comparison or negative control that separates the surviving explanations, and state what each possible result would change. " +
    "Before a check vetoes further work, identify its source and protected goal: an original constraint or frozen study criterion, versus an agent-proposed candidate heuristic. " +
    "For a heuristic discrepancy, compare the incumbent under the same relevant control and measure or bound the effect on the goal; use a finite diagnosis that can decide grow or switch. " +
    "A prospective heuristic revision needs an explicit rationale and retained counterevidence; it cannot turn an earlier failure into a pass, relax an original constraint or override a frozen study criterion. " +
    "Distinguish readiness for an authorized informative evaluation from evidence sufficient to replace the incumbent. Consider its full marginal and opportunity cost under existing budget, feedback and permission limits; preserve the best qualified artifact. " +
    "Distinguish local or synthetic gains from authoritative transfer, and repeated identical outcomes from new information. " +
    "Choose decisive coverage before broadening a sweep; retain uncertainty and relevant negative evidence.",
};
export function stridePlanningGuidance(value: unknown): string[] {
  if (normalizeStrideStrategy(value) === "off") return [];
  return ["Review actual adoption of the previous decision, new information and authoritative goal outcomes separately. " +
    "Choose grow to expand useful completed scope on a supported route, switch to test a different mechanism, or verify to resolve a route decision. " +
    "Describe work size, route distance and validation breadth independently as concrete plans, with a reason for each. " +
    "Name the reference method and the assumption or mechanism that changes; more tuning alone is not a route switch. " +
    "Use the live decision_value_review questions to compare goal headroom and full cost through the three existing axes. " +
    "Work size must cover a coherent accepted deliverable or an experiment capable of an interpretable conclusion: a new route may need a larger " +
    "implementation and uncertain evidence may need broader validation. Expand useful work when evidence supports it; " +
    "do not respond to a persistent unexplained gap by repeatedly making the same work smaller. " +
    "For engineering, organizing or maintenance, grow can complete related implementation, integration, validation and readback together to reduce repeated planning and source reads. Work size is accepted scope, not Todo text, file count or tool calls. Do not bundle independent commitments or cross dependency, risk or authority boundaries to make work larger. Do not restart unfinished coherent work or repeat small probes unless changed inputs or a new uncertainty justify it. " +
    "Where relevant, compare continuation, an alternative mechanism and replication using applicable Explore findings and scoped counterevidence. " +
    "Do not force a pivot from elapsed time, a progress count or one noisy result. " +
    "Missing or refuted evidence is not a universal route ban. Preserve the best qualified artifact and original priorities. " +
    "Declare minimum evidence, a testable prediction, adoption criterion and full marginal cost including control and validation; review actual adoption, quality and cost next. Verify must resolve a named grow-or-switch question, not repeat an unchanged check. " +
    "Carry the next_probe into existing executable Todo work, reusing an unfinished experiment when it still answers the question. " +
    "Use agent_vision.todo_delta entries such as retain:todo_<id> to identify that work after its normal authoring/readback; do not invent IDs or add a planning-only Todo. " +
    "Read planned_work and subsequent observations before crediting the previous decision. A negative experiment may finish its Todo while the broader goal remains open. " +
    "Keep a candidate experiment separate from the best retained artifact: authorized evaluation may be informative before replacement is justified. " +
    "Use the live stride_control contract for agent_vision.stride_decision. This controls proposal completeness, not execution, " +
    "spawn, acceptance or a promise of utility; existing Todo/path and authority owners still govern work."];
}

export const STRIDE_DECISION_SCHEMA = decisionSchema.schema_version;
export const STRIDE_PROBE_LIMITS = decisionSchema.probe_field_limits;
export const STRIDE_AXIS_NAMES = decisionSchema.axis_names;
export const STRIDE_PLAN_LIMIT = decisionSchema.axis_plan_limit;
export const STRIDE_REASON_LIMIT = decisionSchema.axis_reason_limit;
export const STRIDE_REVIEW_LIMIT = decisionSchema.review_text_limit;
// Persisted v1 records retain their original meaning; never offered for new reviews.
export const RECORDED_STRIDE_SCHEMA = "stride_decision_v1";
const recordedAdjustments = ["grow", "hold", "shrink"] as const;
type BoundedText = (field: string, value: unknown, limit: number) => string;
function text(field: string, value: unknown, limit: number): string {
  const result = requireNonEmptyString(value, field).trim();
  if (result.length > limit) throw new EffectRuntimeRequestError(`${field} exceeds ${limit} characters`);
  return result;
}
function exactFields(value: JsonObject, fields: string[], label: string): void {
  if (Object.keys(value).some(key => !fields.includes(key))) throw new EffectRuntimeRequestError(`${label} contains unsupported fields`);
}
function refs(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 3 || value.some(ref => typeof ref !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(ref)) || new Set(value).size !== value.length) {
    throw new EffectRuntimeRequestError(`${label} requires at most three distinct public-safe evidence references`);
  }
  return value as string[];
}
function selectedCandidate(value: unknown): JsonObject {
  const input = requireJsonObject(value, "explore_review.selected_candidate");
  exactFields(input, ["todo_id", "node_id", "finding_id", "source_sha256"], "selected_candidate");
  const todo = requireNonEmptyString(input.todo_id, "selected_candidate.todo_id");
  if (!PLANNING_TODO_ID.test(todo)) throw new EffectRuntimeRequestError("selected_candidate requires a public Todo id");
  const node = refs([input.node_id], "selected_candidate.node_id")[0];
  const finding = refs([input.finding_id], "selected_candidate.finding_id")[0];
  const digest = requireNonEmptyString(input.source_sha256, "selected_candidate.source_sha256");
  if (!BARE_SHA256_PATTERN.test(digest)) throw new EffectRuntimeRequestError("selected_candidate requires the exact source digest");
  return {todo_id: todo, node_id: node, finding_id: finding, source_sha256: digest};
}
function comparisonCandidates(value: unknown): JsonObject[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 20) throw new EffectRuntimeRequestError("comparison_candidates must be a bounded source page");
  return value.map(value => {
    const card = requireJsonObject(value, "comparison candidate");
    exactFields(card, ["todo_id", "outcomes"], "comparison candidate");
    if (!Array.isArray(card.outcomes) || !card.outcomes.length || card.outcomes.length > EXPLORE_RESULT_PAGE_MAX) {
      throw new EffectRuntimeRequestError("comparison candidate requires one to twenty scoped outcomes from its result page");
    }
    const outcomes = card.outcomes.map(value => {
      const outcome = requireJsonObject(value, "comparison outcome");
      exactFields(outcome, ["node_id", "finding_id", "source_sha256"], "comparison outcome");
      const checked = selectedCandidate({todo_id: card.todo_id, ...outcome});
      return {node_id: checked.node_id, finding_id: checked.finding_id, source_sha256: checked.source_sha256};
    });
    return {todo_id: card.todo_id, outcomes};
  });
}

/** Agent interpretations are structured claims, not certified evidence or authority. */
export function normalizeStrideDecision(value: unknown, bounded: BoundedText = text):
  [JsonObject | null, Record<string, number>] {
  if (value === undefined) return [null, {}];
  const input = requireJsonObject(value, "stride_decision");
  // Off-mode transport and historical readback retain both earlier v1 shapes.
  // An enabled fresh review requires the current concrete-plan schema.
  const historical = input.schema_version === RECORDED_STRIDE_SCHEMA;
  if (historical && input.axis_reasons === undefined) return normalizeHistoricalStrideDecision(input, bounded);
  exactFields(input, ["schema_version", "strategy_version", "reference_context_id", "reference_obligation_id", "basis",
    "axes", ...(historical ? ["axis_reasons"] : ["action"]), "next_probe", "previous_review", "explore_review"], "stride_decision");
  if ((!historical && input.schema_version !== decisionSchema.schema_version) || normalizeStrideStrategy(input.strategy_version) !== "evidence_conditioned_v1") {
    throw new EffectRuntimeRequestError("stride_decision requires stride_decision_v2 and evidence_conditioned_v1; v1 is historical only");
  }
  if (!/^replan-context-[a-f0-9]{24}$/.test(requireNonEmptyString(input.reference_context_id, "stride_decision.reference_context_id")) ||
      !/^replan-[a-f0-9]{16}$/.test(requireNonEmptyString(input.reference_obligation_id, "stride_decision.reference_obligation_id"))) {
    throw new EffectRuntimeRequestError("stride_decision requires the delivered context and admitted obligation identities");
  }
  requireStringLiteral(input.basis, decisionSchema.basis_options, "stride_decision.basis");
  const axes = requireJsonObject(input.axes, "stride_decision.axes");
  exactFields(axes, decisionSchema.axis_names, "stride_decision.axes");
  const usage: Record<string, number> = {};
  const checked = (field: string, value: unknown, limit: number): string => {
    const label = `stride_decision.${field}`;
    const result = bounded(label, requireNonEmptyString(value, label), limit);
    if (!result.trim()) throw new EffectRuntimeRequestError(`${label} must be nonempty`);
    usage[label] = result.length; return result;
  };
  const axisReasons: JsonObject = {};
  const axisPlans: JsonObject = {};
  const reasons = historical ? requireJsonObject(input.axis_reasons, "stride_decision.axis_reasons") : {};
  if (historical) exactFields(reasons, decisionSchema.axis_names, "stride_decision.axis_reasons");
  else {
    requireStringLiteral(input.action, decisionSchema.actions, "stride_decision.action");
    usage["stride_decision.action"] = String(input.action).length;
  }
  for (const axis of decisionSchema.axis_names) {
    if (historical) {
      requireStringLiteral(axes[axis], recordedAdjustments, `stride_decision.axes.${axis}`);
      usage[`stride_decision.axes.${axis}`] = String(axes[axis]).length;
      axisReasons[axis] = checked(`axis_reasons.${axis}`, reasons[axis], decisionSchema.axis_reason_limit);
    } else {
      const plan = requireJsonObject(axes[axis], `stride_decision.axes.${axis}`);
      exactFields(plan, ["plan", "reason"], `stride_decision.axes.${axis}`);
      axisPlans[axis] = {
        plan: checked(`axes.${axis}.plan`, plan.plan, decisionSchema.axis_plan_limit),
        reason: checked(`axes.${axis}.reason`, plan.reason, decisionSchema.axis_reason_limit),
      };
    }
  }
  const probe = requireJsonObject(input.next_probe, "stride_decision.next_probe");
  exactFields(probe, Object.keys(STRIDE_PROBE_LIMITS), "stride_decision.next_probe");
  const normalized: JsonObject = {};
  for (const [field, limit] of Object.entries(STRIDE_PROBE_LIMITS)) normalized[field] = checked(`next_probe.${field}`, probe[field], limit);
  const output: JsonObject = {schema_version: input.schema_version, strategy_version: input.strategy_version,
    reference_context_id: input.reference_context_id, reference_obligation_id: input.reference_obligation_id,
    basis: input.basis, ...(historical ? {axes: {...axes}, axis_reasons: axisReasons}
      : {action: input.action, axes: axisPlans}), next_probe: normalized};
  for (const key of ["strategy_version", "reference_context_id", "reference_obligation_id", "basis"]) usage[`stride_decision.${key}`] = String(input[key]).length;
  if (input.previous_review != null) {
    const review = requireJsonObject(input.previous_review, "stride_decision.previous_review");
    exactFields(review, ["reference_decision_ref", "adoption", "information_gain", "goal_gain", "evidence_refs", "lesson"], "previous_review");
    if (!/^replan-decision-[a-f0-9]{24}$/.test(requireNonEmptyString(review.reference_decision_ref, "previous_review.reference_decision_ref"))) throw new EffectRuntimeRequestError("previous_review requires a recorded decision reference");
    requireStringLiteral(review.adoption, decisionSchema.adoption_options, "previous_review.adoption");
    requireStringLiteral(review.information_gain, decisionSchema.information_options, "previous_review.information_gain");
    requireStringLiteral(review.goal_gain, decisionSchema.gain_options, "previous_review.goal_gain");
    const evidence = refs(review.evidence_refs, "previous_review.evidence_refs");
    if (!evidence.length && [review.adoption, review.information_gain, review.goal_gain].some(v => v !== "unknown")) {
      throw new EffectRuntimeRequestError("previous_review claims require evidence; missing evidence remains unknown");
    }
    output.previous_review = {...review, evidence_refs: evidence, lesson: checked("previous_review.lesson", review.lesson, decisionSchema.review_text_limit)};
    for (const key of ["reference_decision_ref", "adoption", "information_gain", "goal_gain"]) usage[`stride_decision.previous_review.${key}`] = String(review[key]).length;
    usage["stride_decision.previous_review.evidence_refs"] = evidence.join("").length;
  }
  if (input.explore_review != null) {
    const review = requireJsonObject(input.explore_review, "stride_decision.explore_review");
    exactFields(review, ["revision", "disposition", "considered_refs", "reason", ...(!historical ? ["result_pages", "selected_candidate"] : [])], "explore_review");
    if (review.revision !== null && !BARE_SHA256_PATTERN.test(requireNonEmptyString(review.revision, "explore_review.revision"))) throw new EffectRuntimeRequestError("explore_review requires a delivered revision or null when unavailable");
    requireStringLiteral(review.disposition, decisionSchema.explore_dispositions, "explore_review.disposition");
    const considered = refs(review.considered_refs, "explore_review.considered_refs");
    if (review.disposition === "used" && !considered.length) throw new EffectRuntimeRequestError("used Explore evidence requires a delivered reference");
    output.explore_review = {...review, considered_refs: considered, reason: checked("explore_review.reason", review.reason, decisionSchema.review_text_limit)};
    for (const key of ["revision", "disposition"]) usage[`stride_decision.explore_review.${key}`] = String(review[key] ?? "").length;
    usage["stride_decision.explore_review.considered_refs"] = considered.join("").length;
    if (Object.hasOwn(review, "result_pages")) {
      if (review.disposition === "unavailable" || review.revision === null || !considered.length) {
        throw new EffectRuntimeRequestError("Explore result pages require available evidence and considered references");
      }
      const pages = normalizeExploreResultPages(review.result_pages);
      (output.explore_review as JsonObject).result_pages = pages;
      usage["stride_decision.explore_review.result_pages"] = JSON.stringify(pages).length;
    }
    if (Object.hasOwn(review, "selected_candidate")) {
      const candidate = selectedCandidate(review.selected_candidate);
      if (review.disposition !== "used" || review.revision === null || !considered.includes(String(candidate.finding_id))) {
        throw new EffectRuntimeRequestError("selected_candidate requires used, cited Explore evidence");
      }
      (output.explore_review as JsonObject).selected_candidate = candidate;
      usage["stride_decision.explore_review.selected_candidate"] = JSON.stringify(candidate).length;
    }
  }
  return [output, usage];
}

/** Historical read codec only, never a selectable strategy or new proposal. */
export function normalizeRecordedStrideDecision(value: unknown): JsonObject | null {
  if (value == null) return null;
  return normalizeStrideDecision(value)[0];
}
function normalizeHistoricalStrideDecision(value: unknown, bounded: BoundedText): [JsonObject, Record<string, number>] {
  const input = requireJsonObject(value, "recorded stride_decision");
  const historical: Record<string, JsonObject> = {
    supported_progress: {work_slice: "grow", route_distance: "hold", validation_scope: "hold"},
    unexplained_gap: {work_slice: "shrink", route_distance: "grow", validation_scope: "shrink"},
    uncertain_evidence: {work_slice: "shrink", route_distance: "hold", validation_scope: "grow"},
    useful_new_route: {work_slice: "grow", route_distance: "hold", validation_scope: "grow"},
    decision_failure: {work_slice: "shrink", route_distance: "hold", validation_scope: "shrink"},
  };
  exactFields(input, ["schema_version", "strategy_version", "reference_context_id", "reference_obligation_id", "basis", "axes", "next_probe"], "recorded stride_decision");
  const basis = requireStringLiteral(input.basis, decisionSchema.basis_options, "recorded stride_decision.basis");
  const vector = historical[basis];
  if (Object.hasOwn(input, "axes")) {
    const axes = requireJsonObject(input.axes, "recorded stride_decision.axes");
    exactFields(axes, decisionSchema.axis_names, "recorded stride_decision.axes");
    if (decisionSchema.axis_names.some(axis => axes[axis] !== vector[axis])) throw new EffectRuntimeRequestError("invalid historical stride vector");
  }
  const probe = requireJsonObject(input.next_probe, "recorded next_probe");
  exactFields(probe, ["scope", "prediction", "adoption_criterion", "cost_bound"], "historical next_probe");
  const [checked, usage] = normalizeStrideDecision({...input, axes: vector,
    axis_reasons: Object.fromEntries(decisionSchema.axis_names.map(axis => [axis, "Historical fixed mapping; utility remains unknown."])),
    next_probe: {...probe, minimum_evidence: "Historical declaration did not record this field."}}, bounded);
  for (const axis of decisionSchema.axis_names) delete usage[`stride_decision.axis_reasons.${axis}`];
  delete usage["stride_decision.next_probe.minimum_evidence"];
  const normalizedProbe = checked!.next_probe as JsonObject;
  return [{...input, axes: checked!.axes, next_probe: Object.fromEntries(
    Object.keys(probe).map(field => [field, normalizedProbe[field]]))}, usage];
}

/** References into the existing scoped Explore view; no second graph or planner. */
export function projectStrideExploreBasis(value: unknown, goal: string, agent: string | null): JsonObject | null {
  if (value == null) return null;
  const source = requireJsonObject(value, "Explore stride source");
  if (source.goal_id !== goal || source.agent_id !== agent) throw new EffectRuntimeRequestError("Explore stride source scope differs");
  if (source.ok !== true) return {availability: "unavailable", revision: null, finding_refs: [], branches: [],
    rule: "Explore source was unavailable; do not infer absent evidence or create execution authority."};
  if (source.graph_enabled !== true && source.harness_enabled !== true) return null;
  const graph = source.graph as JsonObject | undefined;
  const harness = source.harness as JsonObject | undefined;
  const findings = Array.isArray(graph?.writeback_results) ? graph.writeback_results as JsonObject[] : [];
  const recent = Array.isArray(graph?.recent_findings) ? graph.recent_findings as JsonObject[] : [];
  const branches = Array.isArray(harness?.selected_branches) ? harness.selected_branches as JsonObject[] : [];
  const candidates = source.harness_enabled === true ? comparisonCandidates(harness?.comparison_candidates) : [];
  const audits = branches.flatMap(branch => {
    const audit = branch.typed_evidence_audit as JsonObject | undefined;
    return Array.isArray(audit?.findings) ? audit.findings as JsonObject[] : [];
  });
  const byId = new Map<string, JsonObject>();
  for (const row of [...findings, ...audits, ...recent]) {
    const id = requireNonEmptyString(row.finding_id, "Explore finding id");
    if (!byId.has(id)) byId.set(id, {finding_id: id, node_id: row.node_id, status: row.status,
      ...(row.execution_observation ? {execution_observation: row.execution_observation} : {}),
      source_sha256: typeof row.source_sha256 === "string" ? row.source_sha256
        : createHash("sha256").update(JSON.stringify(row)).digest("hex")});
  }
  return {availability: "available", graph_enabled: source.graph_enabled, harness_enabled: source.harness_enabled,
    // Planner scores/counts and unrelated pool churn do not alter a cited
    // finding's source. Bind the actual bounded evidence, not the whole view.
    revision: createHash("sha256").update(JSON.stringify([goal, agent, source.graph_enabled,
      source.harness_enabled, [...byId.values()].slice(0, 6), ...(candidates.length ? [candidates] : [])])).digest("hex"),
    finding_refs: [...byId.values()].slice(0, 6),
    ...(candidates.length ? {comparison_candidates: candidates} : {}),
    ...(harness?.omitted_comparison_candidates !== undefined ? {
      omitted_comparison_candidates: harness.omitted_comparison_candidates} : {}),
    branches: branches.slice(0, 3).map(branch => ({todo_id: branch.todo_id,
      selection_status: branch.selection_status, actionable_open: branch.actionable_open})),
    omitted_finding_refs: Math.max(0, byId.size - 6),
    result_page: graph?.result_page ?? null,
    rule: "Use the existing Explore view for scope/applicability, counterevidence and omitted-result reads. " +
      "For considered findings outside this list, copy revision/offset/limit from up to three global result pages you read into explore_review.result_pages; writeback replays them against the source. Keep explore_review.revision unchanged. " +
      "A planner selection or recorded finding does not certify execution, utility, refutation of other inputs, or spawn authority."};
}

/** Context binding checks shape and declared provenance, never truth of Agent claims. */
export function strideReviewMismatch(decision: JsonObject, context: JsonObject, sourceReview?: unknown, todoDelta?: unknown): string | null {
  const follow = context.decision_followthrough as JsonObject | undefined;
  const previous = follow?.previous_decision as JsonObject | undefined;
  const review = decision.previous_review as JsonObject | undefined;
  if (previous && review?.reference_decision_ref !== previous.evidence_ref) return "Review the delivered previous decision's adoption, information and outcome.";
  if (!previous && review) return "A previous decision review cannot claim an undelivered decision.";
  if (previous && review) {
    const rows = (value: unknown): JsonObject[] => Array.isArray(value) ? value as JsonObject[] : [];
    const ordinary = rows((follow?.ordinary_work as JsonObject | undefined)?.evidence);
    const allowed = new Set<unknown>([...rows(context.evidence), ...rows(follow?.observations), ...ordinary]
      .map(row => row.evidence_ref));
    for (const row of rows(context.coverage_ledger)) for (const ref of Array.isArray(row.evidence_ids) ? row.evidence_ids : []) allowed.add(ref);
    const cited = review.evidence_refs as string[];
    const subsequent = new Set([...rows(follow?.observations), ...ordinary].map(row => row.evidence_ref));
    const planned = rows(follow?.planned_work);
    const linked = new Set(planned.flatMap(work => [
      ...(Array.isArray(work.observation_refs) ? work.observation_refs : []),
      ...(Array.isArray(work.ordinary_evidence_refs) ? work.ordinary_evidence_refs : []),
    ]));
    let hasDeclaredWork = planned.length > 0;
    if (sourceReview !== undefined) {
      const source = requireJsonObject(sourceReview, "stride review evidence");
      if (source.previous_decision_ref !== previous.evidence_ref || typeof source.has_declared_work !== "boolean"
        || !Array.isArray(source.citations) || source.citations.length !== cited.length) {
        return "Previous-decision evidence must be replayed against the current scoped source.";
      }
      hasDeclaredWork = source.has_declared_work;
      allowed.clear(); subsequent.clear(); linked.clear();
      for (const [index, value] of source.citations.entries()) {
        const row = requireJsonObject(value, "stride review citation");
        if (row.evidence_ref !== cited[index] || typeof row.available !== "boolean"
          || typeof row.subsequent !== "boolean" || typeof row.declared_work !== "boolean"
          || row.declared_work && !row.subsequent || row.subsequent && !row.available) {
          return "Previous-decision evidence replay is inconsistent.";
        }
        if (row.available) allowed.add(row.evidence_ref);
        if (row.subsequent) subsequent.add(row.evidence_ref);
        if (row.declared_work) linked.add(row.evidence_ref);
      }
    }
    if (cited.some(ref => !allowed.has(ref))) return "Previous-decision claims cite an undelivered or unavailable evidence reference.";
    const claimsOutcome = [review.adoption, review.information_gain, review.goal_gain].some(value => value !== "unknown");
    if (claimsOutcome && !cited.some(ref => subsequent.has(ref))) {
      return "Previous-decision claims require subsequent evidence; earlier comparisons alone cannot establish execution or outcome.";
    }
    if (hasDeclaredWork && (review.adoption === "executed" || review.adoption === "partially_executed" || review.goal_gain !== "unknown")
      && !cited.some(ref => linked.has(ref))) {
      return "Execution and goal-gain claims require subsequent evidence for the declared work; unrelated activity is insufficient.";
    }
  }
  const basis = context.explore_basis as JsonObject | undefined;
  const explore = decision.explore_review as JsonObject | undefined;
  if (!basis) return explore ? "Explore review requires a delivered scoped basis." : null;
  if (!explore || explore.revision !== basis.revision) return "Explore review requires the delivered evidence revision.";
  const considered = explore.considered_refs as string[];
  if (basis.availability === "unavailable") {
    return explore.disposition !== "unavailable" || considered.length ? "Unavailable Explore evidence must remain unknown." : null;
  }
  if (explore.disposition === "unavailable") return "Available Explore evidence cannot be declared unreadable.";
  const allowed = new Set((basis.finding_refs as JsonObject[]).map(row => row.finding_id));
  const candidates = comparisonCandidates(basis.comparison_candidates);
  for (const card of candidates) for (const outcome of card.outcomes as JsonObject[]) allowed.add(outcome.finding_id);
  const deliveredPages = Array.isArray(context.explore_result_pages) ? context.explore_result_pages as JsonObject[] : [];
  for (const page of (explore.result_pages ?? []) as JsonObject[]) {
    const resolved = deliveredPages.find(row => row.revision === page.revision &&
      row.offset === page.offset && row.limit === page.limit && row.todo_id === page.todo_id);
    if (!resolved || page.todo_id === undefined && page.revision !== (basis.result_page as JsonObject | null)?.revision) {
      return page.todo_id === undefined
        ? "Explore result pages require current source replay; reread global pages under the original Turn."
        : "Explore task result pages require current source replay; reread scoped pages under the original Turn.";
    }
    for (const ref of resolved.finding_refs as string[]) allowed.add(ref);
    candidates.push(...comparisonCandidates(resolved.comparison_candidates));
  }
  if (considered.some(ref => !allowed.has(ref))) return "Explore review cites an undelivered or foreign finding.";
  if (explore.selected_candidate != null) {
    const selected = selectedCandidate(explore.selected_candidate);
    if (!candidates.some(card => card.todo_id === selected.todo_id && (card.outcomes as JsonObject[]).some(outcome =>
      ["node_id", "finding_id", "source_sha256"].every(key => outcome[key] === selected[key])))) {
      return "Selected candidate requires the current Todo's explicit link to the exact scoped result; reread changed sources.";
    }
    // The existing Vision Todo plan and followthrough owner retain execution
    // lineage. This optional claim cannot invent work or grant its admission.
    if (!Array.isArray(todoDelta) || !todoDelta.includes(`retain:${selected.todo_id}`)) {
      return "Selected candidate must be retained as the same existing Todo in agent_vision.todo_delta.";
    }
  }
  return null;
}

export function strideControlContract(context: JsonObject, obligationId: unknown = null): JsonObject | null {
  if (normalizeStrideStrategy(context.stride_strategy) === "off") return null;
  const follow = context.decision_followthrough as JsonObject | undefined;
  const previous = (follow?.previous_decision as JsonObject | undefined)?.evidence_ref ?? null;
  const basis = context.explore_basis as JsonObject | undefined;
  const example: JsonObject = {schema_version: decisionSchema.schema_version, strategy_version: context.stride_strategy,
    reference_context_id: context.context_id ?? null, reference_obligation_id: obligationId,
    basis: "uncertain_evidence", action: "verify", axes: {
      work_slice: {plan: "Complete the work and checks needed to resolve the pending adoption decision.", reason: "Resolve decision-relevant uncertainty before expanding the route."},
      route_distance: {plan: "Keep the current method; vary the unresolved assumption only.", reason: "The existing evidence does not yet distinguish a replacement route."},
      validation_scope: {plan: "Check the disputed outcome and its relevant failure case under the same constraints.", reason: "Obtain independent evidence rather than repeat an unchanged check."}},
    next_probe: {scope: "One complete decision-relevant verification.", prediction: "Distinguish the remaining explanations.",
      adoption_criterion: "Applicable evidence supports the next slice.", cost_bound: "Bound full implementation and validation cost.", minimum_evidence: "A verified outcome, relevant failure evidence and attributable cost."}};
  if (previous) example.previous_review = {reference_decision_ref: previous, adoption: "unknown", information_gain: "unknown", goal_gain: "unknown",
    evidence_refs: [], lesson: "Example only: inspect subsequent work and evidence before replacing these unknowns."};
  if (basis) example.explore_review = {revision: basis.revision, disposition: basis.availability === "unavailable" ? "unavailable" : "deferred",
    considered_refs: [], reason: "Example only: inspect applicable findings, counterexamples and alternatives, then replace."};
  return {schema_version: decisionSchema.schema_version, strategy_version: context.stride_strategy,
    reference_context_id: context.context_id ?? null, reference_obligation_id: obligationId,
    axis_names: decisionSchema.axis_names, actions: decisionSchema.actions,
    axis_plan_limit: decisionSchema.axis_plan_limit,
    axis_reason_limit: decisionSchema.axis_reason_limit, probe_field_limits: STRIDE_PROBE_LIMITS,
    decision_value_review: {...decisionValueReview},
    review_text_limit: decisionSchema.review_text_limit,
    previous_decision_ref: previous,
    ...(follow ? {decision_followthrough: follow} : {}),
    previous_review_options: {adoption: decisionSchema.adoption_options, information_gain: decisionSchema.information_options, goal_gain: decisionSchema.gain_options},
    explore_basis: context.explore_basis ?? null, explore_dispositions: decisionSchema.explore_dispositions,
    ...(basis?.harness_enabled === true ? {
      selected_candidate_option: {
        attachment: "explore_review.selected_candidate",
        fields: ["todo_id", "node_id", "finding_id", "source_sha256"],
        rule: "Optionally bind the next probe to an exact comparison_candidates Todo/outcome, cite that finding with disposition=used, and retain the same existing Todo in agent_vision.todo_delta. " +
          "For an omitted alternative, read the existing global result pages and attach their coordinates in result_pages; current Todo links are replayed with those results. " +
          "Cards share the page limit: expand --result-limit up to 20 for multiple Todos linked to one result. omitted_comparison_candidates discloses the remaining bound; omission does not mean ineligible. " +
          "Choose continuation, a different mechanism or verification from the evidence; this source binding does not certify novelty, execution or utility and grants no admission.",
      },
    } : {}),
    minimal_example: example,
    attachment: "agent_vision.stride_decision", requires_evidence_linked_path: true,
    required_on_periodic_review: true,
    rule: "Every due periodic review and observed-stall review (typed_progress_repeat, no_progress_streak, repeated_action_loop or external_progress_review_drift) with this strategy enabled must record a stride decision before settlement, including when a typed progress, successor, blocker or terminal outcome is also reported. " +
      "The decision requires an evidence-linked Vision path (including a terminal stop when supported by evidence), an explicit grow/switch/verify action and a concrete plan/reason for each axis. " +
      "Grow expands a supported route; switch names the reference method and changed mechanism; verify resolves a named grow-or-switch question. Size every action for a coherent accepted deliverable or complete interpretable experiment, including ordinary engineering and maintenance. Grow changes useful completed scope, not authority or settlement boundaries. " +
      "The minimal example demonstrates syntax, not the recommended action for the current evidence. " +
      "decision_value_review is Agent guidance for the existing axis plans/reasons and next_probe, not additional proposal fields or a settlement gate. " +
      "When a previous decision is delivered, assess its adoption, information and goal gain in previous_review against its exact reference. " +
      "When Explore basis is delivered, assess applicable findings/alternatives in explore_review; missing or omitted evidence stays unknown. " +
      "An empty current projection does not establish that retained historical research is absent; distinguish unread or unprojected evidence from an exhausted alternative. " +
      "These are Agent claims and proposal-shape obligations, not execution authority or utility certification. Other source-specific outcomes remain legal but cannot bypass a due stride review.",
  };
}
