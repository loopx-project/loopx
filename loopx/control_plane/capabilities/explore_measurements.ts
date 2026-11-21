/** Explore result measurements and canonical metric-profile owner.
 * Producers own scores; this owner normalizes profiles, validates execution-
 * measurement consistency, orders canonical finding prefixes, and computes
 * scoped improvement without ranking tasks or granting execution authority. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireJsonObject, type JsonObject} from "../runtime_decode.ts";
import {normalizeExploreExecutionObservation} from "./explore_execution_observation.ts";
import {
  matchesExactScopeTuple,
  requireBoundedSingleLine,
  requireExactFields,
  requireOpaqueMethodologyRef,
} from "./stage_methodology_operator.ts";

export const EXPLORE_METRIC_PROFILE_KEYS = [
  "metric",
  "unit",
  "higher_is_better",
  "comparison_scope",
  "input_revision",
  "evaluation_ref",
] as const;

export interface ExploreMetricProfile {
  metric: string;
  unit: string;
  higher_is_better: boolean;
  comparison_scope: string;
  input_revision: string;
  evaluation_ref: string;
}

export interface ExploreMeasurement extends ExploreMetricProfile {
  value: number | null;
  candidate_ref: string;
  evidence_ref: string;
}

export interface ExploreMeasurementPoint {
  finding_id: string;
  candidate_ref: string;
  evidence_ref: string;
  value: number;
}

export interface ExploreMeasurementFeedback extends ExploreMetricProfile {
  node_id: string;
  latest: {finding_id: string; measurement: ExploreMeasurement};
  best: ExploreMeasurementPoint | null;
  previous_best: ExploreMeasurementPoint | null;
  improvement: number | null;
  measured_count: number;
  unknown_count: number;
}

export function normalizeExploreMetricProfile(
  value: unknown,
  label = "yardstick profile",
): ExploreMetricProfile {
  const row = requireExactFields(value, EXPLORE_METRIC_PROFILE_KEYS, EXPLORE_METRIC_PROFILE_KEYS, label);
  if (typeof row.higher_is_better !== "boolean") {
    throw new EffectRuntimeRequestError(`${label} requires explicit metric direction`);
  }
  return {
    metric: requireBoundedSingleLine(row.metric, `${label} metric`, 80),
    unit: requireBoundedSingleLine(row.unit, `${label} unit`, 40),
    higher_is_better: row.higher_is_better,
    comparison_scope: requireOpaqueMethodologyRef(row.comparison_scope, `${label} comparison_scope`),
    input_revision: requireBoundedSingleLine(row.input_revision, `${label} input_revision`, 160),
    evaluation_ref: requireOpaqueMethodologyRef(row.evaluation_ref, `${label} evaluation_ref`),
  };
}

export function matchesExploreMetricProfile(
  candidate: object,
  profile: ExploreMetricProfile,
): boolean {
  return matchesExactScopeTuple(candidate, profile, EXPLORE_METRIC_PROFILE_KEYS);
}

const CONTEXT_KEYS = ["comparison_scope", "input_revision", "evaluation_ref"] as const;
const MEASUREMENT_FIELDS = [
  ...EXPLORE_METRIC_PROFILE_KEYS,
  "value",
  "candidate_ref",
  "evidence_ref",
] as const;

export function normalizeExploreMeasurements(
  value: unknown,
  context?: unknown,
  executionObservation?: unknown,
): ExploreMeasurement[] {
  if (!Array.isArray(value) || !value.length || value.length > 8) {
    throw new EffectRuntimeRequestError("measurements requires one to eight explicit measurements");
  }
  if (context !== undefined) {
    const shared = requireExactFields(
      context,
      CONTEXT_KEYS,
      CONTEXT_KEYS,
      "measurement_context",
      "measurement_context requires only comparison_scope, input_revision and evaluation_ref",
    );
    return normalizeExploreMeasurements(
      value.map(item => {
        const observation = requireJsonObject(item, "measurement");
        if (CONTEXT_KEYS.some(key => Object.hasOwn(observation, key))) {
          throw new EffectRuntimeRequestError("measurement cannot override its explicit comparison context");
        }
        return {...shared, ...observation};
      }),
      undefined,
      executionObservation,
    );
  }
  const seen = new Set<string>();
  const measurements = value.map(item => {
    const row = requireExactFields(
      item,
      MEASUREMENT_FIELDS,
      MEASUREMENT_FIELDS,
      "measurement",
      "measurement fields are missing or unsupported",
    );
    if (
      typeof row.higher_is_better !== "boolean" ||
      (row.value !== null && (typeof row.value !== "number" || !Number.isFinite(row.value)))
    ) {
      throw new EffectRuntimeRequestError(
        "measurement requires explicit direction and finite value or null; unknown is not zero",
      );
    }
    const profile = normalizeExploreMetricProfile(
      {
        metric: row.metric,
        unit: row.unit,
        higher_is_better: row.higher_is_better,
        comparison_scope: row.comparison_scope,
        input_revision: row.input_revision,
        evaluation_ref: row.evaluation_ref,
      },
      "measurement",
    );
    const result: ExploreMeasurement = {
      ...profile,
      value: row.value as number | null,
      candidate_ref: requireOpaqueMethodologyRef(row.candidate_ref, "measurement candidate_ref"),
      evidence_ref: requireOpaqueMethodologyRef(row.evidence_ref, "measurement evidence_ref"),
    };
    const key = JSON.stringify([comparisonKey("", result), result.candidate_ref]);
    if (seen.has(key)) throw new EffectRuntimeRequestError("measurement candidate/metric/scope is duplicated");
    seen.add(key);
    return result;
  });
  const mismatch = executionMeasurementError(measurements, executionObservation);
  if (mismatch !== null) throw new EffectRuntimeRequestError(mismatch);
  return measurements;
}

export function executionMeasurementError(
  measurements: readonly ExploreMeasurement[],
  observation: unknown,
): string | null {
  if (observation == null) return null;
  const execution = normalizeExploreExecutionObservation({observation});
  if (measurements.some(item => item.input_revision !== execution.input_revision)) {
    return "measurements must retain the executed candidate's input revision";
  }
  if (execution.result_ref !== undefined && measurements.some(item => item.evidence_ref !== execution.result_ref)) {
    return "measurements must cite the executed candidate's result reference";
  }
  return null;
}

function comparisonKey(node: string, metric: ExploreMetricProfile): string {
  return JSON.stringify([
    node,
    metric.metric,
    metric.unit,
    metric.higher_is_better,
    metric.comparison_scope,
    metric.input_revision,
    metric.evaluation_ref,
  ]);
}

/** Shared canonical finding ordering: append order via `recorded_order` when
 * present, or reverse of newest-first projections for direct callers. */
export function orderCanonicalExploreFindings(
  findings: readonly JsonObject[],
  options: {strictRecordedOrder?: boolean} = {},
): JsonObject[] {
  const ordered = [...findings];
  if (ordered.some(finding => finding.recorded_order !== undefined)) {
    if (options.strictRecordedOrder) {
      if (
        ordered.some(
          finding => !Number.isSafeInteger(finding.recorded_order) || Number(finding.recorded_order) < 0,
        ) ||
        new Set(ordered.map(finding => finding.recorded_order)).size !== ordered.length
      ) {
        throw new EffectRuntimeRequestError("measurement source order must be complete and unambiguous");
      }
    }
    ordered.sort((a, b) => Number(a.recorded_order ?? 0) - Number(b.recorded_order ?? 0));
  } else {
    ordered.reverse();
  }
  return ordered;
}

/** Extracts confirmed, execution-consistent measurements and optional method id
 * from a canonical Explore finding record; skips malformed or contradicted rows. */
export function extractVerifiedFindingMeasurements(
  finding: JsonObject,
): {measurements: ExploreMeasurement[]; methodId: string | null} {
  if (finding.measurements === undefined) return {measurements: [], methodId: null};
  const status = finding.status === undefined ? "confirmed" : String(finding.status);
  if (status !== "confirmed") return {measurements: [], methodId: null};
  let measurements: ExploreMeasurement[];
  try {
    measurements = normalizeExploreMeasurements(finding.measurements);
    if (executionMeasurementError(measurements, finding.execution_observation) !== null) {
      return {measurements: [], methodId: null};
    }
  } catch {
    return {measurements: [], methodId: null};
  }
  let methodId: string | null = null;
  if (finding.execution_observation != null) {
    try {
      const exec = normalizeExploreExecutionObservation({observation: finding.execution_observation});
      methodId =
        typeof exec.executed_method_id === "string"
          ? exec.executed_method_id
          : typeof exec.planned_method_id === "string"
            ? exec.planned_method_id
            : null;
    } catch {
      return {measurements: [], methodId: null};
    }
  }
  return {measurements, methodId};
}

export function projectExploreMeasurementFeedback(
  findings: readonly JsonObject[],
  includeMeasurement?: (finding: JsonObject, measurement: ExploreMeasurement) => boolean,
): ExploreMeasurementFeedback[] {
  const groups = new Map<string, ExploreMeasurementFeedback>();
  const measuredEvidence = new Set<string>();
  const ordered = orderCanonicalExploreFindings(
    findings.filter(finding => finding.measurements !== undefined),
    {strictRecordedOrder: true},
  );
  for (const finding of ordered) {
    const node = String(finding.node_id ?? ""), id = String(finding.finding_id ?? "");
    if (!node || !id) throw new EffectRuntimeRequestError("measurements require their node and finding identities");
    const measurements = normalizeExploreMeasurements(finding.measurements);
    if (executionMeasurementError(measurements, finding.execution_observation) !== null) continue;
    for (const measurement of measurements) {
      if (includeMeasurement && !includeMeasurement(finding, measurement)) continue;
      const key = comparisonKey(node, measurement);
      const work = jsonObject(finding.work_binding);
      if (work && measurement.value !== null) {
        const evidence = JSON.stringify([
          work.goal_id, work.agent_id, work.todo_id, key, measurement.candidate_ref, measurement.evidence_ref,
        ]);
        if (measuredEvidence.has(evidence)) continue;
        measuredEvidence.add(evidence);
      }
      const prior = groups.get(key);
      const previous = prior?.best ?? null;
      const point = measurement.value === null ? null : {
        finding_id: id,
        candidate_ref: measurement.candidate_ref,
        evidence_ref: measurement.evidence_ref,
        value: measurement.value,
      };
      const difference = point && previous
        ? (measurement.higher_is_better ? point.value - previous.value : previous.value - point.value)
        : null;
      groups.set(key, {
        node_id: node,
        metric: measurement.metric,
        unit: measurement.unit,
        higher_is_better: measurement.higher_is_better,
        comparison_scope: measurement.comparison_scope,
        input_revision: measurement.input_revision,
        evaluation_ref: measurement.evaluation_ref,
        latest: {finding_id: id, measurement},
        previous_best: previous,
        best: point && (!previous || (difference !== null && difference > 0)) ? point : previous,
        improvement: difference !== null && Number.isFinite(difference) ? (difference === 0 ? 0 : difference) : null,
        measured_count: (prior?.measured_count ?? 0) + (point ? 1 : 0),
        unknown_count: (prior?.unknown_count ?? 0) + (point ? 0 : 1),
      });
    }
  }
  const recency = new Map(ordered.map((finding, index) => [String(finding.finding_id), index]));
  return [...groups.values()].sort((a, b) => recency.get(b.latest.finding_id)! - recency.get(a.latest.finding_id)!);
}

export function normalizeExploreMeasurementsRequest(params: JsonObject): JsonObject {
  return {
    measurements: normalizeExploreMeasurements(
      params.measurements,
      params.measurement_context,
      params.execution_observation,
    ),
  };
}
