/** Optional current-work decision carried by Explore's existing result writeback.
 * The declaration records a research method, not acceptance or execution authority.
 * Stride's replan-only v2 contract remains unchanged. */
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {isExploreEvidenceRef} from "../capabilities/explore_evidence_ref.ts";

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
function currentFinding(findings: JsonObject[], binding: JsonObject): JsonObject | undefined {
  return findings.filter(row => {
    const capture = row.work_yardstick as JsonObject | undefined;
    const source = capture?.binding as JsonObject | undefined;
    return source && ["goal_id", "agent_id", "todo_id"].every(key => source[key] === binding[key]);
  }).sort((a, b) => {
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
  if (decision.prior_finding_id !== (current?.finding_id ?? null)) throw new EffectRuntimeRequestError("yardstick basis changed; read the current same-Todo decision before revising it");
  return {decision};
}
export function projectWorkYardstickCue(params: JsonObject): JsonObject | null {
  if (params.enabled !== true || !params.todo_id) return null;
  const source = currentFinding((params.findings ?? []) as JsonObject[], params);
  if (!source) return null;
  const capture = normalizeWorkYardstickCapture({capture: source.work_yardstick, evidence_refs: source.evidence_refs,
    goal_id: params.goal_id, agent_id: params.agent_id});
  const decision = capture.decision as JsonObject;
  const counter = decision.counterevidence_refs as string[];
  const previous = ((params.findings ?? []) as JsonObject[]).find(row => row.finding_id === decision.prior_finding_id);
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
  const profiles = [decision.authoritative, decision.proxy].filter(Boolean) as JsonObject[];
  const feedback = ((params.measurement_feedback ?? []) as JsonObject[]).filter(row => row.node_id === source.node_id && profiles.some(p =>
    Object.entries(p).every(([key, value]) => row[key] === value)));
  return {source_finding_id: source.finding_id, node_id: source.node_id, binding: capture.binding, ...decision,
    measurement_feedback: feedback,
    ...(followthrough ? {previous_decision_followthrough: followthrough} : {}),
    guidance: counter.length
      ? "Counterevidence is recorded for this scoped proxy. Reassess its use at the next concrete choice; a local screening heuristic cannot veto an informative authoritative check by itself. Preserve accepted correctness, authorization and budget constraints."
      : "Use this current target and scoped yardstick when choosing the next useful action. Proxy fidelity remains unqualified here; preserve accepted constraints and bound confirmation cost.",
    authority: "Recorded method and source references are Agent declarations, not independently verified acceptance or permission. Omission preserves this decision; revise it only when work or evidence changes. No extra Turn, replan or mandatory capture is required."};
}
