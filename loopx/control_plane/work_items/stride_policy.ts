/** One opt-in work and research stride strategy; all execution authority retains its owners. */
import {createHash} from "node:crypto";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import schema from "../stride_strategy_schema.json" with {type: "json"};
import decisionSchema from "../stride_decision_schema.json" with {type: "json"};
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString, requireStringLiteral} from "../runtime_decode.ts";
import {normalizeExploreResultPages} from "../capabilities/explore_result_page.ts";

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
    "Size work for the experiment that can change that choice; a larger test count is not greater decision value.",
  route_distance: "Name the remaining authoritative goal gap, its limiting component and the plausible headroom. " +
    "Compare a different approach that can affect that component. A changed auxiliary mechanism alone does not show that it addresses the gap.",
  validation_scope: "Use evidence applicable to the limiting outcome and changed mechanism. " +
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
    exactFields(review, ["revision", "disposition", "considered_refs", "reason", ...(!historical ? ["result_pages"] : [])], "explore_review");
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
  const audits = branches.flatMap(branch => {
    const audit = branch.typed_evidence_audit as JsonObject | undefined;
    return Array.isArray(audit?.findings) ? audit.findings as JsonObject[] : [];
  });
  const byId = new Map<string, JsonObject>();
  for (const row of [...findings, ...audits, ...recent]) {
    const id = requireNonEmptyString(row.finding_id, "Explore finding id");
    if (!byId.has(id)) byId.set(id, {finding_id: id, node_id: row.node_id, status: row.status,
      source_sha256: typeof row.source_sha256 === "string" ? row.source_sha256
        : createHash("sha256").update(JSON.stringify(row)).digest("hex")});
  }
  return {availability: "available", graph_enabled: source.graph_enabled, harness_enabled: source.harness_enabled,
    // Planner scores/counts and unrelated pool churn do not alter a cited
    // finding's source. Bind the actual bounded evidence, not the whole view.
    revision: createHash("sha256").update(JSON.stringify([goal, agent, source.graph_enabled,
      source.harness_enabled, [...byId.values()].slice(0, 6)])).digest("hex"),
    finding_refs: [...byId.values()].slice(0, 6),
    branches: branches.slice(0, 3).map(branch => ({todo_id: branch.todo_id,
      selection_status: branch.selection_status, actionable_open: branch.actionable_open})),
    omitted_finding_refs: Math.max(0, byId.size - 6),
    result_page: graph?.result_page ?? null,
    rule: "Use the existing Explore view for scope/applicability, counterevidence and omitted-result reads. " +
      "For considered findings outside this list, copy revision/offset/limit from up to three global result pages you read into explore_review.result_pages; writeback replays them against the source. Keep explore_review.revision unchanged. " +
      "A planner selection or recorded finding does not certify execution, utility, refutation of other inputs, or spawn authority."};
}

/** Context binding checks shape and declared provenance, never truth of Agent claims. */
export function strideReviewMismatch(decision: JsonObject, context: JsonObject): string | null {
  const follow = context.decision_followthrough as JsonObject | undefined;
  const previous = follow?.previous_decision as JsonObject | undefined;
  const review = decision.previous_review as JsonObject | undefined;
  if (previous && review?.reference_decision_ref !== previous.evidence_ref) return "Review the delivered previous decision's adoption, information and outcome.";
  if (!previous && review) return "A previous decision review cannot claim an undelivered decision.";
  if (previous && review) {
    const rows = (value: unknown): JsonObject[] => Array.isArray(value) ? value as JsonObject[] : [];
    const allowed = new Set<unknown>([...rows(context.evidence), ...rows(follow?.observations)]
      .map(row => row.evidence_ref));
    for (const row of rows(context.coverage_ledger)) for (const ref of Array.isArray(row.evidence_ids) ? row.evidence_ids : []) allowed.add(ref);
    if ((review.evidence_refs as string[]).some(ref => !allowed.has(ref))) return "Previous-decision claims cite an undelivered evidence reference.";
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
  const deliveredPages = Array.isArray(context.explore_result_pages) ? context.explore_result_pages as JsonObject[] : [];
  for (const page of (explore.result_pages ?? []) as JsonObject[]) {
    const resolved = deliveredPages.find(row => row.revision === page.revision &&
      row.offset === page.offset && row.limit === page.limit);
    if (page.revision !== (basis.result_page as JsonObject | null)?.revision || !resolved) {
      return "Explore result pages require current source replay; reread global pages under the original Turn.";
    }
    for (const ref of resolved.finding_refs as string[]) allowed.add(ref);
  }
  if (considered.some(ref => !allowed.has(ref))) return "Explore review cites an undelivered or foreign finding.";
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
    previous_review_options: {adoption: decisionSchema.adoption_options, information_gain: decisionSchema.information_options, goal_gain: decisionSchema.gain_options},
    explore_basis: context.explore_basis ?? null, explore_dispositions: decisionSchema.explore_dispositions,
    minimal_example: example,
    attachment: "agent_vision.stride_decision", requires_evidence_linked_path: true,
    required_on_periodic_review: true,
    rule: "Every due periodic review with this strategy enabled must record a stride decision before settlement, including when a typed progress, successor, blocker or terminal outcome is also reported. " +
      "The decision requires an evidence-linked Vision path (including a terminal stop when supported by evidence), an explicit grow/switch/verify action and a concrete plan/reason for each axis. " +
      "Grow expands a supported route; switch names the reference method and changed mechanism; verify resolves a named grow-or-switch question. Size every action for a coherent accepted deliverable or complete interpretable experiment, including ordinary engineering and maintenance. Grow changes useful completed scope, not authority or settlement boundaries. " +
      "The minimal example demonstrates syntax, not the recommended action for the current evidence. " +
      "decision_value_review is Agent guidance for the existing axis plans/reasons and next_probe, not additional proposal fields or a settlement gate. " +
      "When a previous decision is delivered, assess its adoption, information and goal gain in previous_review against its exact reference. " +
      "When Explore basis is delivered, assess applicable findings/alternatives in explore_review; missing or omitted evidence stays unknown. " +
      "These are Agent claims and proposal-shape obligations, not execution authority or utility certification. Other source-specific outcomes remain legal but cannot bypass a due stride review.",
  };
}
