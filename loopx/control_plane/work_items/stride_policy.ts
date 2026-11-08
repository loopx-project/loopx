/** One work and research stride strategy; all execution authority retains its owners. */
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
  work_slice: "Compare continuation with a feasible alternative by complete cost and useful outcome. " +
    "Separate first deliverable quality from later gain beyond the retained best; recovering an avoidable initial quality deficit is not added capability. " +
    "Name the choice-changing observation and a complete experiment to obtain it. Test count is not decision value; retain unfinished decisive work instead of restarting setup.",
  route_distance: "Identify the authoritative goal gap, limiting component and headroom. Anchor to the retained best artifact; check whether subsequent work actually implements a mechanism addressing that gap. " +
    "Distinguish unexecuted intervention, failed transfer and insufficient route potential. If evidence weakens the route, generate a small set of genuinely different explanations or approaches (representation, decomposition, search method or information source). " +
    "Contrast their predictions and full costs, then choose a discriminating experiment; parameter variants of the same mechanism are not independent alternatives. Missing feedback is a wait or evidence gap, not route failure or exhaustion.",
  validation_scope: "Bind evidence to the limiting outcome, changed mechanism, artifact version, input conditions and original budget; separate submission from when feedback became available. " +
    "Separate decision, execution and outcome: a snapshot is not proof of restoration or continued execution. Check activation before widening a sweep; choose decisive comparisons/negative controls and what each result changes. " +
    "Before a check vetoes work, identify its source and protected goal: original constraint or frozen study criterion versus agent-proposed candidate heuristic. For a heuristic discrepancy, compare the incumbent under the same relevant control, measure or bound the effect on the goal, and use finite diagnosis that can decide grow or switch. " +
    "A prospective heuristic revision needs explicit rationale and retained counterevidence; it cannot turn an earlier failure into a pass, relax an original constraint or override a frozen study criterion. " +
    "Separate readiness for an authorized informative evaluation from evidence sufficient to replace the incumbent; count full marginal/opportunity cost within budget, feedback and permission limits and preserve the best qualified artifact. " +
    "Separate local/synthetic gains from authoritative transfer and identical outcomes from new information; retain uncertainty and negatives before broadening.",
};

export const STRIDE_DECISION_SCHEMA = decisionSchema.schema_version;
export const STRIDE_PROBE_LIMITS = decisionSchema.probe_field_limits;
export const STRIDE_AXIS_NAMES = decisionSchema.axis_names;
export const STRIDE_PLAN_LIMIT = decisionSchema.axis_plan_limit;
export const STRIDE_REASON_LIMIT = decisionSchema.axis_reason_limit;
export const STRIDE_REVIEW_LIMIT = decisionSchema.review_text_limit;
export const STRIDE_REVIEW_REFERENCE_LIMIT = decisionSchema.review_reference_limit;
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
  if (!Array.isArray(value) || value.length > STRIDE_REVIEW_REFERENCE_LIMIT || value.some(ref => typeof ref !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(ref)) || new Set(value).size !== value.length) {
    throw new EffectRuntimeRequestError(`${label} requires at most ${STRIDE_REVIEW_REFERENCE_LIMIT} distinct public-safe evidence references`);
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
  // Compact run history records absent optional decisions as JSON null.
  // Absence is not a proposal and cannot satisfy an enabled stride review.
  if (value == null) return [null, {}];
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
      work_slice: {plan: "Complete the work and checks for the adoption decision.", reason: "Resolve adoption uncertainty before expanding."},
      route_distance: {plan: "Vary only the unresolved assumption in the current method.", reason: "Evidence leaves the replacement route uncertain."},
      validation_scope: {plan: "Verify the disputed outcome and failure case under the same constraints.", reason: "Obtain new independent evidence."}},
    next_probe: {scope: "One complete decision-relevant check.", prediction: "Distinguish the remaining explanations.",
      adoption_criterion: "Applicable evidence supports the next slice.", cost_bound: "Bound all implementation and validation costs.", minimum_evidence: "Verified outcome, relevant failure evidence and attributable cost."}};
  if (previous) example.previous_review = {reference_decision_ref: previous, adoption: "unknown", information_gain: "unknown", goal_gain: "unknown",
    evidence_refs: [], lesson: "Example only: inspect subsequent work and evidence before replacing these unknowns."};
  if (basis) example.explore_review = {revision: basis.revision, disposition: basis.availability === "unavailable" ? "unavailable" : "deferred",
    considered_refs: [], reason: "Example only: inspect applicable findings, counterexamples and alternatives, then replace."};
  return {schema_version: decisionSchema.schema_version, strategy_version: context.stride_strategy,
    reference_context_id: context.context_id ?? null, reference_obligation_id: obligationId,
    axis_names: decisionSchema.axis_names, actions: decisionSchema.actions,
    basis_options: decisionSchema.basis_options,
    required_fields: ["schema_version", "strategy_version", "reference_context_id", "reference_obligation_id",
      "basis", "action", "axes", "next_probe", ...(previous ? ["previous_review"] : []), ...(basis ? ["explore_review"] : [])],
    axis_plan_limit: decisionSchema.axis_plan_limit,
    axis_reason_limit: decisionSchema.axis_reason_limit, probe_field_limits: STRIDE_PROBE_LIMITS,
    decision_value_review: {...decisionValueReview},
    review_text_limit: decisionSchema.review_text_limit,
    review_reference_limit: STRIDE_REVIEW_REFERENCE_LIMIT,
    previous_decision_ref: previous,
    ...(follow ? {decision_followthrough: follow} : {}),
    previous_review_options: {adoption: decisionSchema.adoption_options, information_gain: decisionSchema.information_options, goal_gain: decisionSchema.gain_options},
    explore_basis: context.explore_basis ?? null, explore_dispositions: decisionSchema.explore_dispositions,
    ...(basis?.harness_enabled === true ? {
      selected_candidate_option: {
        attachment: "explore_review.selected_candidate",
        fields: ["todo_id", "node_id", "finding_id", "source_sha256"],
        rule: "Optional: bind an exact comparison_candidates Todo/outcome, cite its finding as used, and retain that Todo in agent_vision.todo_delta. " +
          "Read omitted alternatives via explore_basis.rule; cards share the page limit (--result-limit up to 20). Omission is not ineligibility. Binding grants no admission or proof of novelty, execution or utility.",
      },
    } : {}),
    minimal_example: example,
    attachment: "agent_vision.stride_decision", requires_evidence_linked_path: true,
    required_on_periodic_review: true,
    rule: "Enabled periodic and observed-stall reviews require a current evidence-linked Vision path and stride decision before any settlement, including progress/successor/blocker/terminal exits. " +
      "Each axis needs plan/reason: grow useful completed scope on a supported route; switch names reference method and changed mechanism; verify resolves a grow-or-switch question. Apply these plans to the actual Todo scope and acceptance using the existing Todo authoring/update commands, then record the verified IDs in todo_delta. Retain unfinished useful work; do not create successor microtasks merely to count progress. On grow, consolidate fragmented setup, implementation, integration and decisive validation into a coherent accepted deliverable or complete interpretable experiment, including engineering/maintenance. The next admitted work must execute that scope, method and validation plan; changing review frequency or repeating the proposal is not adoption. Keep the configured review interval and preserve dependencies, acceptance, risk, authority and per-Turn settlement boundaries. " +
      "Review the exact previous decision's adoption/information/goal gain and delivered Explore sources in their review fields. Apply review_reference_limit separately to previous_review.evidence_refs and explore_review.considered_refs. Missing/omitted/unread/unprojected evidence stays unknown, not exhausted. Guidance adds no fields/gates; the example is syntax, not an action recommendation. Claims certify no authority, execution or utility. Other legal outcomes cannot bypass a due review.",
  };
}
