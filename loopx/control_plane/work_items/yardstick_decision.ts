/** Optional current-work decision carried by Explore's existing result writeback.
 * The declaration records a research method, not acceptance or execution authority.
 * Stride's replan-only v2 contract remains unchanged. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireStringArray, type JsonObject} from "../runtime_decode.ts";
import {
  matchesExploreMetricProfile,
  normalizeExploreMetricProfile,
  projectExploreMeasurementFeedback,
  type ExploreMeasurementFeedback,
  type ExploreMeasurementPoint,
  type ExploreMetricProfile,
} from "../capabilities/explore_measurements.ts";
import {
  committedInWorkWindow,
  evaluateMethodologyActivation,
  latestCommittedScopedRecord,
  matchesWorkIdentity,
  normalizeCanonicalSourceSnapshot,
  projectBoundedMethodologyItems,
  recordedBeforePrefix,
  requireBoundedSingleLine,
  requireExactFields,
  requireOpaqueMethodologyRef,
  requireUniqueOpaqueRefs,
  selectCanonicalEvidenceWindow,
} from "../capabilities/stage_methodology_operator.ts";
import {evaluateScopedYardstickFidelity} from "../capabilities/yardstick_fidelity.ts";

export const WORK_YARDSTICK_DECISION_SCHEMA = "work_yardstick_decision_v0";
export type WorkYardstickProfile = ExploreMetricProfile;
export type WorkYardstickDecision = {
  schema_version: typeof WORK_YARDSTICK_DECISION_SCHEMA;
  target: {summary: string; scope: string; acceptance_ref: string};
  authoritative: WorkYardstickProfile;
  proxy: WorkYardstickProfile | null;
  counterevidence_refs: string[];
  prior_finding_id: string | null;
  next_action: {method_id: string; reason: string; cost_bound: string};
};
export type WorkYardstickCapture = {
  decision: WorkYardstickDecision;
  binding: {goal_id: string; agent_id: string; todo_id: string; turn_instance_id: string};
  source_snapshot?: {event_count: number; event_ids_sha256: string};
};

const DECISION_FIELDS = [
  "schema_version", "target", "authoritative", "proxy", "counterevidence_refs", "next_action", "prior_finding_id",
] as const;
const TARGET_FIELDS = ["summary", "scope", "acceptance_ref"] as const;
const ACTION_FIELDS = ["method_id", "reason", "cost_bound"] as const;
const BINDING_FIELDS = ["goal_id", "agent_id", "todo_id", "turn_instance_id"] as const;

function strictOpaqueRef(value: unknown, label: string): string {
  const raw = requireBoundedSingleLine(value, label, 128);
  if (raw.startsWith("file:")) {
    throw new EffectRuntimeRequestError(`${label} requires an opaque reference`);
  }
  return requireOpaqueMethodologyRef(raw, label, 128);
}

export function normalizeWorkYardstickDecision(value: unknown, evidenceRefs?: unknown): WorkYardstickDecision {
  const row = requireExactFields(value, DECISION_FIELDS, DECISION_FIELDS, "work yardstick decision");
  if (row.schema_version !== WORK_YARDSTICK_DECISION_SCHEMA) {
    throw new EffectRuntimeRequestError("unsupported work yardstick decision schema");
  }
  const target = requireExactFields(row.target, TARGET_FIELDS, TARGET_FIELDS, "research target");
  const next = requireExactFields(row.next_action, ACTION_FIELDS, ACTION_FIELDS, "next research action");
  const counter = requireUniqueOpaqueRefs(row.counterevidence_refs, "counterevidence_refs", 8);
  if (evidenceRefs !== undefined && (!Array.isArray(evidenceRefs) || counter.some(item => !evidenceRefs.includes(item)))) {
    throw new EffectRuntimeRequestError("counterevidence must reference evidence captured by this finding");
  }
  const authoritative = normalizeExploreMetricProfile(row.authoritative, "yardstick");
  const proxy = row.proxy === null ? null : normalizeExploreMetricProfile(row.proxy, "yardstick");
  if (proxy && JSON.stringify(proxy) === JSON.stringify(authoritative)) {
    throw new EffectRuntimeRequestError("proxy and authoritative profiles must be distinct");
  }
  if (!proxy && counter.length) {
    throw new EffectRuntimeRequestError("proxy counterevidence requires the affected proxy profile");
  }
  return {
    schema_version: WORK_YARDSTICK_DECISION_SCHEMA,
    target: {
      summary: requireBoundedSingleLine(target.summary, "target summary", 240),
      scope: requireBoundedSingleLine(target.scope, "target scope", 240),
      acceptance_ref: strictOpaqueRef(target.acceptance_ref, "acceptance_ref"),
    },
    authoritative,
    proxy,
    counterevidence_refs: counter,
    next_action: {
      method_id: strictOpaqueRef(next.method_id, "method_id"),
      reason: requireBoundedSingleLine(next.reason, "next action reason", 320),
      cost_bound: requireBoundedSingleLine(next.cost_bound, "cost_bound", 160),
    },
    prior_finding_id: row.prior_finding_id === null ? null : strictOpaqueRef(row.prior_finding_id, "prior_finding_id"),
  };
}

/** Binding is supplied by the committed settlement, never by an attachment. */
export function normalizeWorkYardstickCapture(params: JsonObject): WorkYardstickCapture {
  const raw = requireJsonObject(params.capture, "yardstick capture");
  const captureFields = ["decision", "binding", ...(raw.source_snapshot === undefined ? [] : ["source_snapshot"])];
  const capture = requireExactFields(raw, captureFields, captureFields, "yardstick capture");
  const binding = requireExactFields(capture.binding, BINDING_FIELDS, BINDING_FIELDS, "work binding");
  const normalized = Object.fromEntries(
    Object.entries(binding).map(([key, val]) => [key, strictOpaqueRef(val, key)]),
  );
  if (!String(normalized.todo_id).startsWith("todo_")) {
    throw new EffectRuntimeRequestError("yardstick capture requires a work Todo");
  }
  for (const key of ["goal_id", "agent_id"]) {
    if (params[key] !== undefined && normalized[key] !== params[key]) {
      throw new EffectRuntimeRequestError("yardstick capture cannot cross Goal or Agent identity");
    }
  }
  return {
    decision: normalizeWorkYardstickDecision(capture.decision, params.evidence_refs),
    binding: normalized as WorkYardstickCapture["binding"],
    ...(capture.source_snapshot === undefined
      ? {}
      : {source_snapshot: normalizeWorkYardstickSourceSnapshot(capture.source_snapshot)}),
  };
}

/** Presentation readers retain their v0 capture shape; when canonical findings
 * are supplied, scoped multi-point fidelity is computed in the same single batch. */
export function projectWorkYardstickPresentation(
  params: JsonObject,
): Pick<WorkYardstickCapture, "decision" | "binding"> {
  const {decision, binding} = normalizeWorkYardstickCapture(params);
  return {decision, binding};
}

export function projectWorkYardstickPresentations(params: JsonObject): JsonObject {
  if (!Array.isArray(params.captures)) {
    throw new EffectRuntimeRequestError("yardstick presentation requires capture requests");
  }
  const captures = params.captures.map(row =>
    projectWorkYardstickPresentation(requireJsonObject(row, "capture request")),
  );
  if (!Array.isArray(params.findings)) {
    return {captures};
  }
  const byNode = new Map<string, JsonObject[]>();
  for (const item of params.findings) {
    const row = requireJsonObject(item, "Explore finding");
    const node = String(row.node_id ?? "");
    const list = byNode.get(node) ?? [];
    list.push(row);
    byNode.set(node, list);
  }
  const fidelityCache = new Map<string, JsonObject>();
  const yardstickFidelities = params.captures.map((rawReq, idx) => {
    const req = requireJsonObject(rawReq, "capture request");
    const nodeId = typeof req.node_id === "string" ? req.node_id : "";
    const {decision} = captures[idx]!;
    if (!nodeId || decision.proxy === null) return null;
    const key = JSON.stringify([nodeId, decision.authoritative, decision.proxy]);
    let cached = fidelityCache.get(key);
    if (!cached) {
      cached = evaluateScopedYardstickFidelity({
        node_id: nodeId,
        authoritative: decision.authoritative as unknown as JsonObject,
        proxy: decision.proxy as unknown as JsonObject,
        findings: byNode.get(nodeId) ?? [],
      }) as unknown as JsonObject;
      fidelityCache.set(key, cached);
    }
    return cached;
  });
  return {captures, yardstick_fidelities: yardstickFidelities};
}

export function normalizeWorkYardstickSourceSnapshot(
  value: unknown,
): NonNullable<WorkYardstickCapture["source_snapshot"]> {
  return normalizeCanonicalSourceSnapshot(value, "yardstick source snapshot");
}

/** Share current-work eligibility between the read plan and its final projection. */
export function workYardstickTodos(params: JsonObject): JsonObject[] {
  return evaluateMethodologyActivation({enabled: params.yardstick_enabled}).active && Array.isArray(params.work_todos)
    ? (params.work_todos as JsonObject[]).filter(
        todo =>
          todo.role === "agent" &&
          !todo.done &&
          todo.claimed_by === params.agent_id &&
          ["open", "pending", "in_progress", "blocked"].includes(String(todo.status)) &&
          (params.todo_id == null || todo.todo_id === params.todo_id),
      )
    : [];
}

function belongsToWork(row: JsonObject, binding: JsonObject): boolean {
  const capture = row.work_yardstick as WorkYardstickCapture | undefined;
  return matchesWorkIdentity(row, binding, capture?.binding as unknown as JsonObject | undefined);
}

export function currentWorkYardstickFinding(findings: JsonObject[], binding: JsonObject): JsonObject | undefined {
  return latestCommittedScopedRecord(findings, row => row.work_yardstick !== undefined && belongsToWork(row, binding));
}

function measuredPair(row: ExploreMeasurementFeedback | undefined) {
  const previous = row?.previous_best, latest = row?.latest;
  if (!previous || !latest || !Number.isFinite(previous.value)) return null;
  const {value, candidate_ref, evidence_ref} = latest.measurement;
  if (value === null || !Number.isFinite(value) || candidate_ref === previous.candidate_ref) return null;
  const recent: ExploreMeasurementPoint = {finding_id: latest.finding_id, candidate_ref, evidence_ref, value};
  const preferred =
    value === previous.value
      ? null
      : (value > previous.value) === row.higher_is_better
        ? candidate_ref
        : previous.candidate_ref;
  return {previous_best: previous, latest: recent, preferred_candidate_ref: preferred};
}

export function workYardstickChoiceGuidance(): string {
  return (
    "At a meaningful evidence or probe boundary, weigh continuing, a different mechanism or a compatible combination " +
    "by expected accepted-outcome gain within accepted constraints, remaining horizon and full implementation and validation cost, " +
    "including search, confirmation and model/control overhead. Keep unknowns as unknown; continuing a supported route is valid, " +
    "and local improvement alone does not establish the best next investment. Without a material change, continue the authorized slice; " +
    "this cue requires no extra review, measurement or decision payload. Consider a bounded check when it could change the choice."
  );
}

/** Existing work facts, without guessing an outcome/proxy role or adding a gate. */
export function projectWorkMeasurementFacts(
  todoId: string,
  available: ExploreMeasurementFeedback[],
  detailsCommand?: string[],
  supplementaryReceipt = false,
): JsonObject | null {
  if (!available.length && !supplementaryReceipt) return null;
  const first = new Map<string, ExploreMeasurementFeedback>();
  for (const row of available) if (!first.has(row.node_id)) first.set(row.node_id, row);
  const representatives = new Set(first.values());
  const prioritized = [...representatives, ...available.filter(row => !representatives.has(row))];
  const bounded = projectBoundedMethodologyItems(prioritized, supplementaryReceipt ? 2 : 3, detailsCommand);
  return {
    todo_id: todoId,
    measurement_feedback: bounded.visible,
    omitted_measurement_feedback: bounded.omitted_count,
    ...(bounded.details_command ? {details_command: bounded.details_command} : {}),
    guidance:
      (supplementaryReceipt ? "This write's measurement_feedback is returned separately; these groups supplement it. " : "") +
      "Recorded comparisons for questions linked by this Todo; shared findings do not prove this Todo executed them. Match each metric and recorded scope to the accepted outcome before choosing work. " +
      workYardstickChoiceGuidance() +
      " No outcome/proxy profile has been selected here. Latest recorded is not current or retained, and best is not acceptance or permission. Use the existing task reader for omitted groups; a decision attachment remains optional.",
  };
}

function measuredSearchComparison(
  scopedFindings: readonly JsonObject[],
  decision: WorkYardstickDecision,
  cited: ReadonlySet<string>,
  sourceOrder: unknown,
  workObservationStart: number,
  decisionTime: bigint | null,
) {
  const pairFor = (profile: WorkYardstickProfile, candidates?: ReadonlySet<string>) => {
    const feedback = projectExploreMeasurementFeedback(scopedFindings, (row, measurement) =>
      (row.work_yardstick !== undefined ||
        (cited.has(measurement.evidence_ref)
          ? recordedBeforePrefix(row, sourceOrder)
          : row.work_binding !== undefined && committedInWorkWindow(row, workObservationStart, decisionTime))) &&
      (!candidates || candidates.has(measurement.candidate_ref)),
    );
    return measuredPair(feedback.find(row => matchesExploreMetricProfile(row, profile)));
  };
  const goal = pairFor(decision.authoritative);
  if (!goal) return null;
  const goalCandidates = new Set([goal.previous_best.candidate_ref, goal.latest.candidate_ref]);
  const proxy = decision.proxy && pairFor(decision.proxy, goalCandidates);
  const reversed = !!(
    goal.preferred_candidate_ref &&
    proxy?.preferred_candidate_ref &&
    goal.preferred_candidate_ref !== proxy.preferred_candidate_ref
  );
  const opening =
    goal.preferred_candidate_ref === null
      ? "Observed goal results tie under this scope. Check remaining headroom before another evaluation. "
      : (reversed ? "The proxy reverses the observed goal order. " : "") +
        "Use the better-scoring candidate under this scope as a comparison base when available. ";
  return {
    goal,
    ...(proxy ? {proxy, proxy_order_reversed: reversed} : {}),
    guidance: opening + workYardstickChoiceGuidance(),
  };
}

export function projectWorkYardstickCue(params: JsonObject): JsonObject | null {
  if (!evaluateMethodologyActivation({enabled: params.enabled, required_scope: {todo_id: params.todo_id}}).active) {
    return null;
  }
  const findings = (params.findings ?? []) as JsonObject[];
  const source = currentWorkYardstickFinding(findings, params);
  if (!source) return null;
  const capture = normalizeWorkYardstickCapture({
    capture: source.work_yardstick,
    evidence_refs: source.evidence_refs,
    goal_id: params.goal_id,
    agent_id: params.agent_id,
  });
  const decision = capture.decision;
  const counter = decision.counterevidence_refs;
  const previous = findings.find(row => row.finding_id === decision.prior_finding_id);
  const priorCapture = previous?.work_yardstick as JsonObject | undefined;
  const priorBinding = priorCapture?.binding as JsonObject | undefined;
  const priorDecision = priorCapture?.decision as JsonObject | undefined;
  const execution = source.execution_observation as JsonObject | undefined;
  const followthrough =
    priorBinding && execution && ["goal_id", "agent_id", "todo_id"].every(key => priorBinding[key] === params[key])
      ? {
          source_finding_id: previous!.finding_id,
          result_finding_id: source.finding_id,
          proposed_method_id: (priorDecision!.next_action as JsonObject).method_id,
          executed_method_id: execution.executed_method_id,
          change_reason: execution.change_reason,
          candidate_sha256: execution.candidate_sha256,
          evidence_refs: execution.evidence_refs,
          recorded_planned_method_matches:
            execution.planned_method_id === (priorDecision!.next_action as JsonObject).method_id,
        }
      : null;
  const profiles = decision.proxy ? [decision.authoritative, decision.proxy] : [decision.authoritative];
  const feedback = ((params.measurement_feedback ?? []) as ExploreMeasurementFeedback[]).filter(
    row => row.node_id === source.node_id && profiles.some(p => matchesExploreMetricProfile(row, p)),
  );
  const cited = new Set(counter);
  const evidenceWindow = selectCanonicalEvidenceWindow({
    findings,
    cited_sources: params.cited_sources,
    source_finding: source,
    binding: capture.binding,
    source_snapshot: capture.source_snapshot,
    cited_refs: cited,
    has_decision_attachment: row => row.work_yardstick !== undefined,
    matches_decision_target: row => {
      const target = (row.work_yardstick as WorkYardstickCapture | undefined)?.decision?.target;
      return (
        belongsToWork(row, params) &&
        target !== undefined &&
        target.summary === decision.target.summary &&
        target.scope === decision.target.scope &&
        target.acceptance_ref === decision.target.acceptance_ref
      );
    },
  });
  const scopedFindings = evidenceWindow.scoped_findings;
  const searchComparison = measuredSearchComparison(
    scopedFindings,
    decision,
    cited,
    evidenceWindow.source_order,
    evidenceWindow.work_observation_start,
    evidenceWindow.decision_time_micros,
  );
  const multiPointFidelity = decision.proxy
    ? evaluateScopedYardstickFidelity({
        node_id: source.node_id,
        findings: scopedFindings,
        authoritative: decision.authoritative as unknown as JsonObject,
        proxy: decision.proxy as unknown as JsonObject,
      })
    : null;
  return {
    source_finding_id: source.finding_id,
    node_id: source.node_id,
    binding: capture.binding,
    ...decision,
    ...(capture.source_snapshot ? {source_snapshot: capture.source_snapshot} : {}),
    measurement_feedback: feedback,
    ...(searchComparison ? {search_comparison: searchComparison} : {}),
    ...(multiPointFidelity && multiPointFidelity.calibration_tier === "multi_point"
      ? {yardstick_fidelity: multiPointFidelity as unknown as JsonObject}
      : {}),
    ...(followthrough ? {previous_decision_followthrough: followthrough} : {}),
    guidance: counter.length
      ? "Counterevidence is recorded for this scoped proxy. Reassess its use at the next concrete choice; a local screening heuristic cannot veto an informative authoritative check by itself. Preserve accepted correctness, authorization and budget constraints."
      : "Use this current target and scoped yardstick when choosing the next useful action. Proxy fidelity remains unqualified here; preserve accepted constraints and bound confirmation cost.",
    authority:
      "Recorded method and source references are Agent declarations, not independently verified acceptance or permission. Omission preserves this decision; revise it only when work or evidence changes. No extra Turn, replan or mandatory capture is required.",
  };
}

/** Project the receipt from the committed evidence prefix, before another Turn. */
export function projectExploreFindingReceipt(params: JsonObject): JsonObject {
  const findings = (params.findings ?? []) as JsonObject[];
  const written = findings.find(row => row.finding_id === params.finding_id && belongsToWork(row, params));
  if (!written) throw new EffectRuntimeRequestError("Explore receipt requires its committed work finding");
  const question = findings.filter(row => row.node_id === written.node_id);
  const feedback = projectExploreMeasurementFeedback(
    question.filter(row => row.status === "confirmed" && belongsToWork(row, params)),
  );
  const latest = feedback.filter(group => group.latest.finding_id === written.finding_id);
  const cue = projectWorkYardstickCue({...params, findings: question, measurement_feedback: feedback});
  const comparison = cue?.search_comparison as ReturnType<typeof measuredSearchComparison> | undefined;
  const participates =
    comparison &&
    [comparison.goal, comparison.proxy].some(
      pair => pair?.latest.finding_id === written.finding_id || pair?.previous_best.finding_id === written.finding_id,
    );
  const route = params.route === undefined ? undefined : requireStringArray(params.route, "Explore read route");
  let workFacts: JsonObject | null = null;
  if (params.enabled === true && Array.isArray(written.measurements) && !currentWorkYardstickFinding(findings, params)) {
    const order = new Map(findings.map(row => [row.finding_id, Number(row.recorded_order ?? 0)]));
    const workFeedback = projectExploreMeasurementFeedback(
      findings.filter(row => row.status === "confirmed" && belongsToWork(row, params)),
    )
      .filter(row => row.latest.finding_id !== written.finding_id)
      .sort(
        (a, b) =>
          Number(b.node_id === written.node_id) - Number(a.node_id === written.node_id) ||
          (order.get(b.latest.finding_id) ?? 0) - (order.get(a.latest.finding_id) ?? 0),
      );
    workFacts = projectWorkMeasurementFacts(
      String(params.todo_id),
      workFeedback,
      route
        ? [
            ...route,
            "explore",
            "turn-context",
            "--agent-id",
            String(params.agent_id),
            "--todo-id",
            String(params.todo_id),
            "--result-limit",
            "20",
            "--goal-id",
            String(params.goal_id),
          ]
        : undefined,
      true,
    );
  }
  return {
    measurement_feedback: latest,
    ...(comparison && (participates || cue?.source_finding_id === written.finding_id)
      ? {search_comparison: comparison}
      : {}),
    ...(workFacts ? {work_measurements: [workFacts]} : {}),
  };
}
