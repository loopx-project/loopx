/** Explore result measurements. Producers own scores; this owner compares only
 * explicitly matching scopes. No ranking of tasks, acceptance or launch authority.
 */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {isExploreEvidenceRef} from "./explore_evidence_ref.ts";

export interface ExploreMeasurement {
  metric: string; value: number | null; unit: string; higher_is_better: boolean;
  comparison_scope: string; input_revision: string; evaluation_ref: string;
  candidate_ref: string; evidence_ref: string;
}
export interface ExploreMeasurementPoint {
  finding_id: string; candidate_ref: string; evidence_ref: string; value: number;
}
export interface ExploreMeasurementFeedback {
  node_id: string; metric: string; unit: string; higher_is_better: boolean;
  comparison_scope: string; input_revision: string; evaluation_ref: string;
  latest: {finding_id: string; measurement: ExploreMeasurement};
  best: ExploreMeasurementPoint | null; previous_best: ExploreMeasurementPoint | null;
  improvement: number | null; measured_count: number; unknown_count: number;
}

export function normalizeExploreMeasurements(value: unknown, context?: unknown): ExploreMeasurement[] {
  if (!Array.isArray(value) || !value.length || value.length > 8) {
    throw new EffectRuntimeRequestError("measurements requires one to eight explicit measurements");
  }
  if (context !== undefined) {
    const shared = requireJsonObject(context, "measurement_context");
    const keys = ["comparison_scope", "input_revision", "evaluation_ref"];
    if (keys.some(key => !Object.hasOwn(shared, key)) || Object.keys(shared).some(key => !keys.includes(key))) {
      throw new EffectRuntimeRequestError("measurement_context requires only comparison_scope, input_revision and evaluation_ref");
    }
    return normalizeExploreMeasurements(value.map(item => {
      const observation = requireJsonObject(item, "measurement");
      if (keys.some(key => Object.hasOwn(observation, key))) {
        throw new EffectRuntimeRequestError("measurement cannot override its explicit comparison context");
      }
      // Authoring only: persist the existing complete measurement, never a
      // mutable context reference or inferred comparison membership.
      return {...shared, ...observation};
    }));
  }
  const seen = new Set<string>();
  return value.map(item => {
    const row = requireJsonObject(item, "measurement");
    const fields = ["metric", "value", "unit", "higher_is_better", "comparison_scope",
      "input_revision", "evaluation_ref", "candidate_ref", "evidence_ref"];
    if (fields.some(key => !Object.hasOwn(row, key)) || Object.keys(row).some(key => !fields.includes(key))) {
      throw new EffectRuntimeRequestError("measurement fields are missing or unsupported");
    }
    const text = (key: string, max: number): string => {
      const text = row[key];
      if (typeof text !== "string" || !text.trim() || text.length > max || /[\u0000-\u001f]/.test(text)) {
        throw new EffectRuntimeRequestError(`measurement ${key} requires bounded single-line text`);
      }
      return text.trim();
    };
    const ref = (key: string): string => {
      const result = text(key, 128);
      if (!isExploreEvidenceRef(result) || result.toLowerCase().startsWith("file:") || result.split("/").includes("..")) {
        throw new EffectRuntimeRequestError(`measurement ${key} requires an opaque reference`);
      }
      return result;
    };
    if (typeof row.higher_is_better !== "boolean" || row.value !== null
      && (typeof row.value !== "number" || !Number.isFinite(row.value))) {
      throw new EffectRuntimeRequestError("measurement requires explicit direction and finite value or null; unknown is not zero");
    }
    const result: ExploreMeasurement = {metric: text("metric", 80), unit: text("unit", 40),
      value: row.value as number | null, higher_is_better: row.higher_is_better,
      comparison_scope: ref("comparison_scope"), input_revision: text("input_revision", 160),
      evaluation_ref: ref("evaluation_ref"), candidate_ref: ref("candidate_ref"), evidence_ref: ref("evidence_ref")};
    const key = comparisonKey("", result);
    if (seen.has(key)) throw new EffectRuntimeRequestError("measurement metric/scope is duplicated");
    seen.add(key);
    return result;
  });
}

function comparisonKey(node: string, metric: ExploreMeasurement): string {
  return JSON.stringify([node, metric.metric, metric.unit, metric.higher_is_better,
    metric.comparison_scope, metric.input_revision, metric.evaluation_ref]);
}

/** Latest means latest appended finding, never the current or retained candidate.
 * Keep all matching evidence when computing best; presentation limits apply later.
 */
export function projectExploreMeasurementFeedback(findings: readonly JsonObject[]): ExploreMeasurementFeedback[] {
  const groups = new Map<string, ExploreMeasurementFeedback>();
  const ordered = findings.filter(finding => finding.measurements !== undefined);
  // Canonical readers derive this ordinal from append order; it is not stored
  // or inferred from source timestamps, which can tie or arrive out of order.
  if (ordered.some(finding => finding.recorded_order !== undefined)) {
    if (ordered.some(finding => !Number.isSafeInteger(finding.recorded_order) || Number(finding.recorded_order) < 0)
      || new Set(ordered.map(finding => finding.recorded_order)).size !== ordered.length) {
      throw new EffectRuntimeRequestError("measurement source order must be complete and unambiguous");
    }
    ordered.sort((a, b) => Number(a.recorded_order) - Number(b.recorded_order));
  } else ordered.reverse(); // Existing direct callers supply newest-first rows.
  for (const finding of ordered) {
    const node = String(finding.node_id ?? ""), id = String(finding.finding_id ?? "");
    if (!node || !id) throw new EffectRuntimeRequestError("measurements require their node and finding identities");
    for (const measurement of normalizeExploreMeasurements(finding.measurements)) {
      const key = comparisonKey(node, measurement), prior = groups.get(key);
      const previous = prior?.best ?? null;
      const point = measurement.value === null ? null : {finding_id: id,
        candidate_ref: measurement.candidate_ref, evidence_ref: measurement.evidence_ref, value: measurement.value};
      const difference = point && previous ? (measurement.higher_is_better
        ? point.value - previous.value : previous.value - point.value) : null;
      groups.set(key, {node_id: node, metric: measurement.metric, unit: measurement.unit,
        higher_is_better: measurement.higher_is_better, comparison_scope: measurement.comparison_scope,
        input_revision: measurement.input_revision, evaluation_ref: measurement.evaluation_ref,
        latest: {finding_id: id, measurement}, previous_best: previous,
        best: point && (!previous || (difference !== null && difference > 0)) ? point : previous,
        improvement: difference !== null && Number.isFinite(difference) ? (difference === 0 ? 0 : difference) : null,
        measured_count: (prior?.measured_count ?? 0) + (point ? 1 : 0),
        unknown_count: (prior?.unknown_count ?? 0) + (point ? 0 : 1)});
    }
  }
  const recency = new Map(ordered.map((finding, index) => [String(finding.finding_id), index]));
  return [...groups.values()].sort((a, b) => recency.get(b.latest.finding_id)! - recency.get(a.latest.finding_id)!);
}

export function normalizeExploreMeasurementsRequest(params: JsonObject): JsonObject {
  return {measurements: normalizeExploreMeasurements(params.measurements, params.measurement_context)};
}
