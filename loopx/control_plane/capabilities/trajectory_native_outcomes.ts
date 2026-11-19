/** Source-bound native measurements, not analyzer judgments or score authority. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {AssessmentCitation, TrajectoryInput} from "./trajectory_assessment_types.ts";

export interface NativeOutcomePoint {
  submission_index: number; result_id: string; candidate_ref: string;
  submitted_at: string | null; observed_at: string; value: number; citation: AssessmentCitation;
}
export interface TrajectoryNativeOutcomes {
  method: "source_bound_native_outcomes_v1";
  scope: TrajectoryInput["scope"]; start: string; end: string; complete: boolean;
  metric: {name: string; unit: string; higher_is_better: boolean};
  sample_count: number; valid_count: number;
  initial: NativeOutcomePoint | null; best_valid: NativeOutcomePoint | null;
  retained: NativeOutcomePoint | null; final_valid: NativeOutcomePoint | null;
  retained_improvement?: {
    baseline: NativeOutcomePoint | null; retention_citation: AssessmentCitation | null;
    value: number | null; final_candidate_delta: number | null;
  };
  limitations: string[];
}

function fail(message: string): never { throw new EffectRuntimeRequestError(message); }
function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label} must be an object`);
  return value as Record<string, unknown>;
}
function identifier(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(value)) {
    fail(`${label} must be an opaque public-safe identifier`);
  }
  return value;
}
function metricLabel(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 80 || /[\u0000-\u001f]/.test(value)) {
    fail(`${label} must be a bounded display label`);
  }
  return value;
}
function timestamp(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(value)
      || !Number.isFinite(Date.parse(value))) fail(`${label} must be a UTC timestamp`);
  // Date.parse normalizes impossible calendar dates and 24:00 into another
  // instant. Compare canonical milliseconds, accepting the declared precision
  // without silently moving the provider's measurement window.
  const canonical = value.slice(0, 19) + "." + (value.slice(19, -1).slice(1) || "").padEnd(3, "0") + "Z";
  if (new Date(value).toISOString() !== canonical) fail(`${label} must be a UTC timestamp`);
  return value;
}
function boundCitation(value: unknown, input: TrajectoryInput): AssessmentCitation {
  const citation = object(value, "native citation"), source = input.sources.find(s => s.id === citation.source_id);
  const first = citation.first_line, last = citation.last_line;
  if (!source || !Number.isSafeInteger(first) || !Number.isSafeInteger(last)
      || Number(first) < source.first_line || Number(last) < Number(first)
      || Number(last) >= source.first_line + source.text.split("\n").length) fail("native citation is outside registered evidence");
  return {source_id: source.id, first_line: Number(first), last_line: Number(last)};
}

/** The provider declares validity/retention. This owner checks binding and ranks
 * valid measurements within that declared capture, never certifying a study. */
export function projectTrajectoryNativeOutcomes(value: unknown, input: TrajectoryInput): TrajectoryNativeOutcomes {
  const raw = object(value, "native outcomes"), scope = object(raw.scope, "native outcome scope");
  if (raw.schema_version !== "trajectory_native_outcomes_v1") fail("unsupported native outcomes schema");
  if (Object.keys(input.scope).some(key => scope[key] !== input.scope[key as keyof typeof input.scope])
      || Object.keys(scope).length !== Object.keys(input.scope).length) fail("native outcome scope differs from the registered pack");
  const start = timestamp(raw.start, "native start"), end = timestamp(raw.end, "native end");
  if (Date.parse(start) > Date.parse(end) || typeof raw.complete !== "boolean") fail("native capture requires an ordered window and explicit coverage");
  const metric = object(raw.metric, "native metric");
  const name = metricLabel(metric.name, "native metric name"), unit = metricLabel(metric.unit, "native metric unit");
  if (typeof metric.higher_is_better !== "boolean") fail("native metric direction must be explicit");
  if (!Array.isArray(raw.samples) || raw.samples.length > 10000) fail("native samples must be an array of at most 10000 records");
  const valid: NativeOutcomePoint[] = [], seen = new Set<string>();
  let previous = 0;
  for (const value of raw.samples) {
    const sample = object(value, "native sample"), index = sample.submission_index;
    if (!Number.isSafeInteger(index) || Number(index) <= previous
        || raw.complete && index !== previous + 1) fail("native submission indices must be distinct, ordered and complete when declared complete");
    previous = Number(index);
    const result = identifier(sample.result_id, "native result"), candidate = identifier(sample.candidate_ref, "native candidate");
    if (seen.has(result)) fail("native result identity is duplicated");
    seen.add(result);
    const submitted = sample.submitted_at === null ? null : timestamp(sample.submitted_at, "native submitted_at");
    const observed = timestamp(sample.observed_at, "native observed_at");
    if (Date.parse(observed) < Date.parse(start) || Date.parse(observed) > Date.parse(end)
        || submitted && (Date.parse(submitted) < Date.parse(start) || Date.parse(submitted) > Date.parse(observed))) fail("native result lies outside its capture or precedes submission");
    if (typeof sample.valid !== "boolean" || sample.valid && (typeof sample.value !== "number" || !Number.isFinite(sample.value))
        || !sample.valid && sample.value !== null) fail("native validity and measured value must agree; missing/invalid is not zero");
    const citation = boundCitation(sample.citation, input);
    if (sample.valid) valid.push({submission_index: Number(index), result_id: result, candidate_ref: candidate,
      submitted_at: submitted, observed_at: observed, value: sample.value as number,
      citation});
  }
  let best: NativeOutcomePoint | null = null;
  for (const point of valid) if (!best || (metric.higher_is_better ? point.value > best.value : point.value < best.value)) best = point;
  const retainedId = raw.retained_result_id;
  if (retainedId !== null) identifier(retainedId, "retained native result");
  const retained = valid.find(point => point.result_id === retainedId) ?? null;
  if (retainedId !== null && !retained) fail("retained native result must be a valid result in this capture");
  let improvement: TrajectoryNativeOutcomes["retained_improvement"];
  if (Object.hasOwn(raw, "comparison")) {
    const comparison = object(raw.comparison, "native comparison"), baselineId = comparison.baseline_result_id;
    if (baselineId !== null) identifier(baselineId, "baseline native result");
    const baseline = valid.find(point => point.result_id === baselineId) ?? null;
    if (baselineId !== null && !baseline) fail("baseline native result must be a valid result in this capture");
    if (baseline && retained && baseline.submission_index > retained.submission_index) fail("baseline cannot follow the retained result");
    const retention = comparison.retention_citation === null ? null : boundCitation(comparison.retention_citation, input);
    const delta = (point: NativeOutcomePoint | null) => {
      if (!baseline || !point) return null;
      const difference = metric.higher_is_better ? point.value - baseline.value : baseline.value - point.value;
      if (!Number.isFinite(difference)) fail("native improvement must be finite");
      return difference === 0 ? 0 : difference;
    };
    improvement = {baseline, retention_citation: retention, value: retention ? delta(retained) : null,
      final_candidate_delta: delta(valid.at(-1) ?? null)};
  }
  return {method: "source_bound_native_outcomes_v1", scope: input.scope, start, end, complete: raw.complete,
    metric: {name, unit, higher_is_better: metric.higher_is_better}, sample_count: raw.samples.length,
    valid_count: valid.length, initial: raw.complete ? valid.find(point => point.submission_index === 1) ?? null : null,
    best_valid: best, retained, final_valid: valid.at(-1) ?? null,
    ...(improvement ? {retained_improvement: improvement} : {}),
    limitations: ["Provider-recorded measurements in the declared capture; not study countability or a causal effect.",
      "Validity and retained selection belong to the native provider, not model judgments or Todo status.",
      "Initial requires a complete prefix and a valid first submission; invalid or missing results are not zero.",
      "Submission order determines initial/final; delayed feedback does not make an older candidate current.",
      "Costs retain their own measured windows; no monetary, whole-study or per-result cost is inferred."]};
}
