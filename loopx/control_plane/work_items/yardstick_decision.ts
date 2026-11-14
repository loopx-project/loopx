/** Optional current-work decision carried by Explore's existing result writeback.
 * The declaration records a research method, not acceptance or execution authority.
 * Stride's replan-only v2 contract remains unchanged. */
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, requireStringArray, type JsonObject} from "../runtime_decode.ts";
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
  binding: {goal_id: string; agent_id: string; todo_id: string; turn_instance_id: string};
  source_snapshot?: {event_count: number; event_ids_sha256: string}};
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
  const raw = requireJsonObject(params.capture, "yardstick capture");
  const capture = object(raw, ["decision", "binding", ...(raw.source_snapshot === undefined ? [] : ["source_snapshot"])], "yardstick capture");
  const binding = object(capture.binding, ["goal_id", "agent_id", "todo_id", "turn_instance_id"], "work binding");
  const normalized = Object.fromEntries(Object.entries(binding).map(([key, value]) => [key, ref(value, key)]));
  if (!String(normalized.todo_id).startsWith("todo_")) throw new EffectRuntimeRequestError("yardstick capture requires a work Todo");
  for (const key of ["goal_id", "agent_id"]) {
    if (params[key] !== undefined && normalized[key] !== params[key]) throw new EffectRuntimeRequestError("yardstick capture cannot cross Goal or Agent identity");
  }
  return {decision: normalizeWorkYardstickDecision(capture.decision, params.evidence_refs), binding: normalized as WorkYardstickCapture["binding"],
    ...(capture.source_snapshot === undefined ? {} : {source_snapshot: normalizeWorkYardstickSourceSnapshot(capture.source_snapshot)})};
}
/** Presentation readers are independently deployed and retain their v0 shape.
 * The canonical capture and policy cue keep server-owned source provenance. */
export function projectWorkYardstickPresentation(params: JsonObject): Pick<WorkYardstickCapture, "decision" | "binding"> {
  const {decision, binding} = normalizeWorkYardstickCapture(params);
  return {decision, binding};
}
export function projectWorkYardstickPresentations(params: JsonObject): JsonObject {
  if (!Array.isArray(params.captures)) throw new EffectRuntimeRequestError("yardstick presentation requires capture requests");
  return {captures: params.captures.map(row => projectWorkYardstickPresentation(requireJsonObject(row, "capture request")))};
}
export function normalizeWorkYardstickSourceSnapshot(value: unknown): NonNullable<WorkYardstickCapture["source_snapshot"]> {
  const row = object(value, ["event_count", "event_ids_sha256"], "yardstick source snapshot");
  if (!Number.isSafeInteger(row.event_count) || Number(row.event_count) < 0
    || typeof row.event_ids_sha256 !== "string" || !BARE_SHA256_PATTERN.test(row.event_ids_sha256)) {
    throw new EffectRuntimeRequestError("yardstick source snapshot requires its original event count and digest");
  }
  return {event_count: Number(row.event_count), event_ids_sha256: row.event_ids_sha256};
}
/** Share current-work eligibility between the read plan and its final projection. */
export function workYardstickTodos(params: JsonObject): JsonObject[] {
  const todos = params.yardstick_enabled === true && Array.isArray(params.work_todos) ? (params.work_todos as JsonObject[])
    .filter(todo => todo.role === "agent" && !todo.done && todo.claimed_by === params.agent_id
      && ["open", "pending", "in_progress", "blocked"].includes(String(todo.status))
      && (params.todo_id == null || todo.todo_id === params.todo_id)) : [];
  const preferred = todos.findIndex(todo => todo.todo_id === params.preferred_yardstick_todo_id);
  if (preferred > 0) todos.unshift(...todos.splice(preferred, 1));
  return todos;
}
function belongsToWork(row: JsonObject, binding: JsonObject): boolean {
  const capture = row.work_yardstick as WorkYardstickCapture | undefined;
  const source = jsonObject(row.work_binding) ?? capture?.binding;
  return !!source && source.goal_id === binding.goal_id && source.agent_id === binding.agent_id
    && source.todo_id === binding.todo_id;
}

function newestFindingFirst(a: JsonObject, b: JsonObject): number {
  // Across independent histories, late delivery keeps its original commit time.
  const left = parseTodoTimestampMicros(String(a.last_updated_at ?? ""));
  const right = parseTodoTimestampMicros(String(b.last_updated_at ?? ""));
  if (left !== null && right !== null && left !== right) return left > right ? -1 : 1;
  return Number(b.recorded_order ?? 0) - Number(a.recorded_order ?? 0);
}

export function currentWorkYardstickFinding(findings: JsonObject[], binding: JsonObject): JsonObject | undefined {
  const ordered = findings.filter(row => row.work_yardstick !== undefined && belongsToWork(row, binding)).sort(newestFindingFirst);
  const successors = new Map<unknown, JsonObject>();
  for (const row of ordered) {
    const prior = (row.work_yardstick as WorkYardstickCapture).decision?.prior_finding_id;
    if (typeof prior === "string" && !successors.has(prior)) successors.set(prior, row);
  }
  // The recorded predecessor establishes the order even across clock rollback.
  const visited = new Set<JsonObject>();
  let current: JsonObject | undefined = ordered[0];
  while (current && successors.has(current.finding_id)) {
    if (visited.has(current)) throw new EffectRuntimeRequestError("cyclic work yardstick predecessors");
    visited.add(current);
    current = successors.get(current.finding_id);
  }
  return current;
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

/** Both declared pairs and first measurements support the same local choice.
 * This is advisory work context, never another review or evaluation gate. */
export function workYardstickChoiceGuidance(): string {
  return "At a meaningful evidence or probe boundary, weigh continuing, a different mechanism or a compatible combination " +
    "by expected accepted-outcome gain within accepted constraints, remaining horizon and full implementation and validation cost, " +
    "including search, confirmation and model/control overhead. Keep unknowns as unknown; continuing a supported route is valid, " +
    "and local improvement alone does not establish the best next investment. Without a material change, continue the authorized slice; " +
    "this cue requires no extra review, measurement or decision payload. Consider a bounded check when it could change the choice.";
}

/** Existing work facts, without guessing an outcome/proxy role or adding a gate. */
export function projectWorkMeasurementFacts(todoId: string, available: ExploreMeasurementFeedback[],
  detailsCommand?: string[], supplementaryReceipt = false): JsonObject | null {
  if (!available.length && !supplementaryReceipt) return null;
  const first = new Map<string, ExploreMeasurementFeedback>();
  for (const row of available) if (!first.has(row.node_id)) first.set(row.node_id, row);
  const representatives = new Set(first.values());
  // A receipt already carries its current feedback. Keep at most two other
  // groups here; the task context retains its existing three-group budget.
  const visible = [...representatives, ...available.filter(row => !representatives.has(row))].slice(0, supplementaryReceipt ? 2 : 3);
  return {todo_id: todoId, measurement_feedback: visible,
    omitted_measurement_feedback: available.length - visible.length,
    ...(detailsCommand ? {details_command: detailsCommand} : {}),
    guidance: (supplementaryReceipt ? "This write's measurement_feedback is returned separately; these groups supplement it. " : "") +
      "Recorded comparisons for questions linked by this Todo; shared findings do not prove this Todo executed them. Match each metric and recorded scope to the accepted outcome before choosing work. " +
      workYardstickChoiceGuidance() +
      " No outcome/proxy profile has been selected here. Latest recorded is not current or retained, and best is not acceptance or permission. Use the existing task reader for omitted groups; a decision attachment remains optional."};
}

function recordedBefore(row: JsonObject, sourceOrder: unknown): boolean {
  const order = row.recorded_order;
  return typeof sourceOrder === "number" && Number.isSafeInteger(sourceOrder)
    && typeof order === "number" && Number.isSafeInteger(order) && order >= 0 && order < sourceOrder;
}

function committedInWorkWindow(row: JsonObject, startOrder: number, decisionTime: bigint | null): boolean {
  const order = row.recorded_order;
  const committed = parseTodoTimestampMicros(String(row.last_updated_at ?? ""));
  // Append order says when a result became visible, not when its primary
  // committed. A delayed old delivery cannot become a new decision's evidence.
  // Tied or unknown original times do not prove a newer uncited observation.
  return Number.isSafeInteger(order) && Number(order) >= startOrder
    && committed !== null && decisionTime !== null && committed > decisionTime;
}

/** Ordinary execution and a revised choice share the existing finding history.
 * A result need not repeat a decision; its attribution uses the same original
 * commit/window rules as measurements, never merely matching method text. */
function workYardstickFollowthrough(findings: JsonObject[], source: JsonObject, binding: JsonObject): JsonObject | null {
  const current = source.work_yardstick as WorkYardstickCapture;
  const prior = findings.find(row => row.finding_id === current.decision.prior_finding_id);
  for (const choice of [source, prior]) {
    if (!choice || !belongsToWork(choice, binding)) continue;
    const capture = choice.work_yardstick as WorkYardstickCapture | undefined;
    if (!capture?.decision) continue;
    const decisionTime = parseTodoTimestampMicros(String(choice.last_updated_at ?? ""));
    const startOrder = capture.source_snapshot?.event_count ?? Number(choice.recorded_order) + 1;
    const endTime = parseTodoTimestampMicros(String(source.last_updated_at ?? ""));
    let result: JsonObject | undefined;
    for (const row of findings) {
      const execution = jsonObject(row.execution_observation);
      if (!execution || row.status !== "confirmed" || row.node_id !== choice.node_id || !belongsToWork(row, binding)
        || execution.input_revision !== capture.decision.authoritative.input_revision
        || !committedInWorkWindow(row, startOrder, decisionTime)) continue;
      // A preceding choice cannot adopt evidence first visible after its
      // successor, even if that evidence was originally committed earlier.
      if (choice !== source && (endTime === null || !Number.isSafeInteger(source.recorded_order)
        || Number(row.recorded_order) > Number(source.recorded_order)
        || row !== source && current.source_snapshot !== undefined && !recordedBefore(row, current.source_snapshot.event_count)
        || parseTodoTimestampMicros(String(row.last_updated_at))! > endTime)) continue;
      if (!result || newestFindingFirst(row, result) < 0) result = row;
    }
    if (!result) continue;
    const execution = result.execution_observation as JsonObject;
    const proposed = capture.decision.next_action.method_id;
    return {source_finding_id: choice.finding_id, result_finding_id: result.finding_id,
      proposed_method_id: proposed, executed_method_id: execution.executed_method_id,
      change_reason: execution.change_reason, candidate_sha256: execution.candidate_sha256,
      evidence_refs: execution.evidence_refs, recorded_planned_method_matches: execution.planned_method_id === proposed};
  }
  return null;
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
      (row.work_yardstick !== undefined || (cited.has(measurement.evidence_ref)
        ? recordedBefore(row, sourceOrder) : row.work_binding !== undefined
          && committedInWorkWindow(row, workObservationStart, decisionTime)))
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
    guidance: opening + workYardstickChoiceGuidance()};
}

export function projectWorkYardstickCue(params: JsonObject): JsonObject | null {
  if (params.enabled !== true || !params.todo_id) return null;
  const findings = (params.findings ?? []) as JsonObject[];
  const source = currentWorkYardstickFinding(findings, params);
  if (!source) return null;
  const capture = normalizeWorkYardstickCapture({capture: source.work_yardstick, evidence_refs: source.evidence_refs,
    goal_id: params.goal_id, agent_id: params.agent_id});
  const decision = capture.decision;
  const counter = decision.counterevidence_refs;
  const followthrough = workYardstickFollowthrough(findings, source, params);
  const profiles = decision.proxy ? [decision.authoritative, decision.proxy] : [decision.authoritative];
  const feedback = ((params.measurement_feedback ?? []) as ExploreMeasurementFeedback[]).filter(row =>
    row.node_id === source.node_id && profiles.some(p => matchesProfile(row, p)));
  // An earlier target can share the same evaluator profile and work identity.
  // Filter source findings before ranking so its best cannot displace a legal pair.
  const cited = new Set(counter);
  const sourceOrder = capture.source_snapshot?.event_count;
  // New observations belong to this current decision's work window. Earlier
  // ordinary measurements require the original explicitly cited snapshot.
  const workObservationStart = sourceOrder ?? Number(source.recorded_order) + 1;
  const decisionTime = parseTodoTimestampMicros(String(source.last_updated_at ?? ""));
  const priorSource = Array.isArray(params.cited_sources) ? (params.cited_sources as JsonObject[]).find(row =>
    row.source_finding_id === source.finding_id
      && capture.source_snapshot !== undefined
      && (row.source_snapshot as JsonObject | undefined)?.event_count === capture.source_snapshot.event_count
      && (row.source_snapshot as JsonObject | undefined)?.event_ids_sha256 === capture.source_snapshot.event_ids_sha256) : undefined;
  const plainFindings = Array.isArray(priorSource?.findings) ? priorSource.findings as JsonObject[] : [];
  const scopedFindings = [...findings.filter(row => row.work_yardstick !== undefined
      || row.work_binding !== undefined && committedInWorkWindow(row, workObservationStart, decisionTime)),
    ...plainFindings.filter(row => row.work_yardstick === undefined)].filter(row => {
    if (row.node_id !== source.node_id) return false;
    const bound = jsonObject(row.work_binding) !== null;
    if (bound && (!belongsToWork(row, params) || row.status !== "confirmed")) return false;
    // A decision can cite the agent's earlier measurements on this linked
    // question without changing their provenance or assigning earlier work.
    if (row.work_yardstick === undefined) {
      if (bound) return true;
      // A repeated reference appended later cannot change the decision's evidence.
      return cited.size > 0 && recordedBefore(row, sourceOrder)
        && row.agent_id === capture.binding.agent_id && Array.isArray(row.evidence_refs)
        && row.evidence_refs.some(ref => typeof ref === "string" && cited.has(ref));
    }
    const target = (row.work_yardstick as WorkYardstickCapture | undefined)?.decision?.target;
    return belongsToWork(row, params) && target !== undefined && target.summary === decision.target.summary
      && target.scope === decision.target.scope && target.acceptance_ref === decision.target.acceptance_ref;
  });
  const searchComparison = measuredSearchComparison(scopedFindings, decision, cited, sourceOrder, workObservationStart, decisionTime);
  return {source_finding_id: source.finding_id, node_id: source.node_id, binding: capture.binding, ...decision,
    ...(capture.source_snapshot ? {source_snapshot: capture.source_snapshot} : {}),
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
  const route = params.route === undefined ? undefined : requireStringArray(params.route, "Explore read route");
  let workFacts: JsonObject | null = null;
  if (params.enabled === true && Array.isArray(written.measurements)
      && !currentWorkYardstickFinding(findings, params)) {
    const order = new Map(findings.map(row => [row.finding_id, Number(row.recorded_order ?? 0)]));
    const workFeedback = projectExploreMeasurementFeedback(findings.filter(row => row.status === "confirmed" && belongsToWork(row, params)))
      .filter(row => row.latest.finding_id !== written.finding_id)
      .sort((a, b) => Number(b.node_id === written.node_id) - Number(a.node_id === written.node_id)
        || (order.get(b.latest.finding_id) ?? 0) - (order.get(a.latest.finding_id) ?? 0));
    workFacts = projectWorkMeasurementFacts(String(params.todo_id), workFeedback,
      route ? [...route, "explore", "turn-context", "--agent-id", String(params.agent_id),
        "--todo-id", String(params.todo_id), "--result-limit", "20", "--goal-id", String(params.goal_id)] : undefined, true);
  }
  return {
    measurement_feedback: latest,
    ...(comparison && (participates || cue?.source_finding_id === written.finding_id)
      ? {search_comparison: comparison} : {}),
    ...(workFacts ? {work_measurements: [workFacts]} : {}),
  };
}
