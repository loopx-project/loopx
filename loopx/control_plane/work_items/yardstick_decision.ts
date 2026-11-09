/** Optional current-work decision carried by Explore's existing result writeback.
 * The declaration records a research method, not acceptance or execution authority.
 * Stride's replan-only v2 contract remains unchanged. */
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {isExploreEvidenceRef} from "../capabilities/explore_evidence_ref.ts";
import {projectExploreMeasurementFeedback, type ExploreMeasurementFeedback,
  type ExploreMeasurementPoint} from "../capabilities/explore_measurements.ts";

export const WORK_YARDSTICK_DECISION_SCHEMA = "work_yardstick_decision_v0";
export type WorkYardstickProfile = {metric: string; unit: string; higher_is_better: boolean;
  comparison_scope: string; input_revision: string; evaluation_ref: string};
export type WorkYardstickDecision = {schema_version: typeof WORK_YARDSTICK_DECISION_SCHEMA;
  target: {summary: string; scope: string; acceptance_ref: string};
  authoritative: WorkYardstickProfile; proxy: WorkYardstickProfile | null;
  counterevidence_refs: string[]; prior_finding_id: string | null;
  next_action: {method_id: string; reason: string; cost_bound: string}};
export type WorkYardstickCapture = {decision: WorkYardstickDecision;
  binding: {goal_id: string; agent_id: string; todo_id: string; turn_instance_id: string}};
function object(value: unknown, fields: string[], label: string): JsonObject {
  const row = requireJsonObject(value, label);
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !Object.hasOwn(row, key))) {
    throw new EffectRuntimeRequestError(`${label} fields are missing or unsupported`);
  }
  return row;
}
function text(value: unknown, label: string, limit = 240): string {
  if (typeof value !== "string" || !value.trim() || value.length > limit || /[\u0000-\u001f]/.test(value)) {
    throw new EffectRuntimeRequestError(`${label} requires bounded single-line text`);
  }
  return value.trim();
}
function ref(value: unknown, label: string): string {
  const result = text(value, label, 128);
  if (!isExploreEvidenceRef(result) || result.startsWith("file:") || result.split("/").includes("..")) {
    throw new EffectRuntimeRequestError(`${label} requires an opaque reference`);
  }
  return result;
}
function refs(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 8) throw new EffectRuntimeRequestError(`${label} requires at most eight references`);
  return [...new Set(value.map(item => ref(item, label)))].sort();
}
/** The scope keys deliberately reuse Explore measurement comparison identity. */
function profile(value: unknown): WorkYardstickProfile {
  const row = object(value, ["metric", "unit", "higher_is_better", "comparison_scope", "input_revision", "evaluation_ref"], "yardstick profile");
  if (typeof row.higher_is_better !== "boolean") throw new EffectRuntimeRequestError("yardstick requires explicit metric direction");
  return {metric: text(row.metric, "metric", 80), unit: text(row.unit, "unit", 40),
    higher_is_better: row.higher_is_better, comparison_scope: ref(row.comparison_scope, "comparison_scope"),
    input_revision: text(row.input_revision, "input_revision", 160), evaluation_ref: ref(row.evaluation_ref, "evaluation_ref")};
}
export function normalizeWorkYardstickDecision(value: unknown, evidenceRefs?: unknown): WorkYardstickDecision {
  const row = object(value, ["schema_version", "target", "authoritative", "proxy", "counterevidence_refs", "next_action", "prior_finding_id"], "work yardstick decision");
  if (row.schema_version !== WORK_YARDSTICK_DECISION_SCHEMA) throw new EffectRuntimeRequestError("unsupported work yardstick decision schema");
  const target = object(row.target, ["summary", "scope", "acceptance_ref"], "research target");
  const next = object(row.next_action, ["method_id", "reason", "cost_bound"], "next research action");
  const counter = refs(row.counterevidence_refs, "counterevidence_refs");
  if (evidenceRefs !== undefined && (!Array.isArray(evidenceRefs) || counter.some(item => !evidenceRefs.includes(item)))) {
    throw new EffectRuntimeRequestError("counterevidence must reference evidence captured by this finding");
  }
  const authoritative = profile(row.authoritative), proxy = row.proxy === null ? null : profile(row.proxy);
  if (proxy && JSON.stringify(proxy) === JSON.stringify(authoritative)) throw new EffectRuntimeRequestError("proxy and authoritative profiles must be distinct");
  if (!proxy && counter.length) throw new EffectRuntimeRequestError("proxy counterevidence requires the affected proxy profile");
  return {schema_version: WORK_YARDSTICK_DECISION_SCHEMA,
    target: {summary: text(target.summary, "target summary"), scope: text(target.scope, "target scope"), acceptance_ref: ref(target.acceptance_ref, "acceptance_ref")},
    authoritative, proxy, counterevidence_refs: counter,
    next_action: {method_id: ref(next.method_id, "method_id"), reason: text(next.reason, "next action reason", 320), cost_bound: text(next.cost_bound, "cost_bound", 160)},
    prior_finding_id: row.prior_finding_id === null ? null : ref(row.prior_finding_id, "prior_finding_id")};
}
/** Binding is supplied by the committed settlement, never by an attachment. */
export function normalizeWorkYardstickCapture(params: JsonObject): WorkYardstickCapture {
  const capture = object(params.capture, ["decision", "binding"], "yardstick capture");
  const binding = object(capture.binding, ["goal_id", "agent_id", "todo_id", "turn_instance_id"], "work binding");
  const normalized = Object.fromEntries(Object.entries(binding).map(([key, value]) => [key, ref(value, key)]));
  if (!String(normalized.todo_id).startsWith("todo_")) throw new EffectRuntimeRequestError("yardstick capture requires a work Todo");
  for (const key of ["goal_id", "agent_id"]) {
    if (params[key] !== undefined && normalized[key] !== params[key]) throw new EffectRuntimeRequestError("yardstick capture cannot cross Goal or Agent identity");
  }
  return {decision: normalizeWorkYardstickDecision(capture.decision, params.evidence_refs), binding: normalized as WorkYardstickCapture["binding"]};
}
function belongsToWork(row: JsonObject, binding: JsonObject): boolean {
  const capture = row.work_yardstick as WorkYardstickCapture | undefined;
  const source = jsonObject(row.work_binding) ?? capture?.binding;
  return !!source && source.goal_id === binding.goal_id && source.agent_id === binding.agent_id
    && source.todo_id === binding.todo_id;
}

function currentFinding(findings: JsonObject[], binding: JsonObject): JsonObject | undefined {
  return findings.filter(row => row.work_yardstick !== undefined && belongsToWork(row, binding)).sort((a, b) => {
    // Late delivery reuses the original committed time, not append order.
    const left = parseTodoTimestampMicros(String(a.last_updated_at ?? ""));
    const right = parseTodoTimestampMicros(String(b.last_updated_at ?? ""));
    if (left !== null && right !== null && left !== right) return left > right ? -1 : 1;
    return Number(b.recorded_order ?? 0) - Number(a.recorded_order ?? 0);
  })[0];
}
export function validateWorkYardstickContinuation(params: JsonObject): JsonObject {
  if (params.enabled !== true) throw new EffectRuntimeRequestError("work yardstick capture requires explicit yardstick_enabled configuration");
  const binding = requireJsonObject(params.binding, "work binding");
  const decision = normalizeWorkYardstickDecision(params.decision, params.evidence_refs);
  const current = currentFinding((params.findings ?? []) as JsonObject[], binding);
  // Only the settlement adapter supplies the original timestamp of an exact
  // committed replay. Preserve a strictly older missing capture as history;
  // it cannot displace the current choice or admit a newer stale predecessor.
  const replayTime = typeof params.replay_committed_at === "string"
    ? parseTodoTimestampMicros(params.replay_committed_at) : null;
  const currentTime = current ? parseTodoTimestampMicros(String(current.last_updated_at ?? "")) : null;
  const historicalReplay = replayTime !== null && currentTime !== null && replayTime < currentTime;
  if (decision.prior_finding_id !== (current?.finding_id ?? null) && !historicalReplay) {
    throw new EffectRuntimeRequestError("yardstick basis changed; read the current same-Todo decision before revising it");
  }
  return {decision};
}

function measuredPair(row: ExploreMeasurementFeedback | undefined) {
  const previous = row?.previous_best, latest = row?.latest;
  if (!previous || !latest || !Number.isFinite(previous.value)) return null;
  const {value, candidate_ref, evidence_ref} = latest.measurement;
  if (value === null || !Number.isFinite(value) || candidate_ref === previous.candidate_ref) return null;
  const recent: ExploreMeasurementPoint = {finding_id: latest.finding_id, candidate_ref, evidence_ref, value};
  const preferred = value === previous.value ? null : (value > previous.value) === row.higher_is_better
    ? candidate_ref : previous.candidate_ref;
  return {previous_best: previous, latest: recent, preferred_candidate_ref: preferred};
}

function matchesProfile(row: ExploreMeasurementFeedback, profile: WorkYardstickProfile): boolean {
  return Object.entries(profile).every(([key, value]) => row[key as keyof WorkYardstickProfile] === value);
}

function measuredSearchComparison(
  scopedFindings: readonly JsonObject[],
  decision: WorkYardstickDecision,
  cited: ReadonlySet<string>,
) {
  const pairFor = (profile: WorkYardstickProfile, candidates?: ReadonlySet<string>) => {
    const feedback = projectExploreMeasurementFeedback(scopedFindings, (row, measurement) =>
      (row.work_binding !== undefined || row.work_yardstick !== undefined || cited.has(measurement.evidence_ref))
      && (!candidates || candidates.has(measurement.candidate_ref)));
    return measuredPair(feedback.find(row => matchesProfile(row, profile)));
  };
  const goal = pairFor(decision.authoritative);
  if (!goal) return null;
  // Both comparisons have two distinct candidates. Scope the proxy lookup to
  // the two goal candidates so a rejected sibling proxy false-positive or a
  // later proxy-only screening row cannot displace their paired comparison.
  const goalCandidates = new Set([goal.previous_best.candidate_ref, goal.latest.candidate_ref]);
  const proxy = decision.proxy && pairFor(decision.proxy, goalCandidates);
  const reversed = !!(goal.preferred_candidate_ref && proxy?.preferred_candidate_ref
    && goal.preferred_candidate_ref !== proxy.preferred_candidate_ref);
  const opening = goal.preferred_candidate_ref === null
    ? "Observed goal results tie under this scope. Check remaining headroom before another evaluation. "
    : (reversed ? "The proxy reverses the observed goal order. " : "") +
      "Use the better-scoring candidate under this scope as a comparison base when available. ";
  return {goal, ...(proxy ? {proxy, proxy_order_reversed: reversed} : {}),
    guidance: opening + "Weigh continuing, a different mechanism or a compatible combination by expected end-to-end gain and full implementation and validation cost. Prefer a bounded check that can change the choice."};
}

export function projectWorkYardstickCue(params: JsonObject): JsonObject | null {
  if (params.enabled !== true || !params.todo_id) return null;
  const findings = (params.findings ?? []) as JsonObject[];
  const source = currentFinding(findings, params);
  if (!source) return null;
  const capture = normalizeWorkYardstickCapture({capture: source.work_yardstick, evidence_refs: source.evidence_refs,
    goal_id: params.goal_id, agent_id: params.agent_id});
  const decision = capture.decision;
  const counter = decision.counterevidence_refs;
  const previous = findings.find(row => row.finding_id === decision.prior_finding_id);
  const priorCapture = previous?.work_yardstick as JsonObject | undefined;
  const priorBinding = priorCapture?.binding as JsonObject | undefined;
  const priorDecision = priorCapture?.decision as JsonObject | undefined;
  const execution = source.execution_observation as JsonObject | undefined;
  const followthrough = priorBinding && execution && ["goal_id", "agent_id", "todo_id"].every(key => priorBinding[key] === params[key])
    ? {source_finding_id: previous!.finding_id, result_finding_id: source.finding_id,
      proposed_method_id: (priorDecision!.next_action as JsonObject).method_id,
      executed_method_id: execution.executed_method_id, change_reason: execution.change_reason,
      candidate_sha256: execution.candidate_sha256, evidence_refs: execution.evidence_refs,
      recorded_planned_method_matches: execution.planned_method_id === (priorDecision!.next_action as JsonObject).method_id}
    : null;
  const profiles = decision.proxy ? [decision.authoritative, decision.proxy] : [decision.authoritative];
  const feedback = ((params.measurement_feedback ?? []) as ExploreMeasurementFeedback[]).filter(row =>
    row.node_id === source.node_id && profiles.some(p => matchesProfile(row, p)));
  // An earlier target can share the same evaluator profile and work identity.
  // Filter source findings before ranking so its best cannot displace a legal pair.
  const cited = new Set(counter);
  const sourceOrder = source.recorded_order;
  const scopedFindings = findings.filter(row => {
    if (row.node_id !== source.node_id) return false;
    const bound = jsonObject(row.work_binding) !== null;
    if (bound && (!belongsToWork(row, params) || row.status !== "confirmed")) return false;
    // A decision can cite the agent's earlier measurements on this linked
    // question without changing their provenance or assigning earlier work.
    if (row.work_yardstick === undefined) {
      if (bound) return true;
      const order = row.recorded_order;
      // A repeated reference appended later cannot change the decision's evidence.
      return cited.size > 0 && typeof sourceOrder === "number" && Number.isSafeInteger(sourceOrder)
        && typeof order === "number" && Number.isSafeInteger(order) && order >= 0 && order < sourceOrder
        && row.agent_id === capture.binding.agent_id && Array.isArray(row.evidence_refs)
        && row.evidence_refs.some(ref => typeof ref === "string" && cited.has(ref));
    }
    const target = (row.work_yardstick as WorkYardstickCapture | undefined)?.decision?.target;
    return belongsToWork(row, params) && target !== undefined && target.summary === decision.target.summary
      && target.scope === decision.target.scope && target.acceptance_ref === decision.target.acceptance_ref;
  });
  const searchComparison = measuredSearchComparison(scopedFindings, decision, cited);
  return {source_finding_id: source.finding_id, node_id: source.node_id, binding: capture.binding, ...decision,
    measurement_feedback: feedback,
    ...(searchComparison ? {search_comparison: searchComparison} : {}),
    ...(followthrough ? {previous_decision_followthrough: followthrough} : {}),
    guidance: counter.length
      ? "Counterevidence is recorded for this scoped proxy. Reassess its use at the next concrete choice; a local screening heuristic cannot veto an informative authoritative check by itself. Preserve accepted correctness, authorization and budget constraints."
      : "Use this current target and scoped yardstick when choosing the next useful action. Proxy fidelity remains unqualified here; preserve accepted constraints and bound confirmation cost.",
    authority: "Recorded method and source references are Agent declarations, not independently verified acceptance or permission. Omission preserves this decision; revise it only when work or evidence changes. No extra Turn, replan or mandatory capture is required."};
}

/** Project the receipt from the committed evidence prefix, before another Turn. */
export function projectExploreFindingReceipt(params: JsonObject): JsonObject {
  const findings = (params.findings ?? []) as JsonObject[];
  const written = findings.find(row => row.finding_id === params.finding_id && belongsToWork(row, params));
  if (!written) throw new EffectRuntimeRequestError("Explore receipt requires its committed work finding");
  const question = findings.filter(row => row.node_id === written.node_id);
  const feedback = projectExploreMeasurementFeedback(question.filter(row =>
    row.status === "confirmed" && belongsToWork(row, params)));
  const latest = feedback.filter(group => group.latest.finding_id === written.finding_id);
  const cue = projectWorkYardstickCue({...params, findings: question, measurement_feedback: feedback});
  const comparison = cue?.search_comparison as ReturnType<typeof measuredSearchComparison> | undefined;
  const participates = comparison && [comparison.goal, comparison.proxy].some(pair =>
    pair?.latest.finding_id === written.finding_id || pair?.previous_best.finding_id === written.finding_id);
  return {
    measurement_feedback: latest,
    ...(comparison && (participates || cue?.source_finding_id === written.finding_id)
      ? {search_comparison: comparison} : {}),
  };
}
