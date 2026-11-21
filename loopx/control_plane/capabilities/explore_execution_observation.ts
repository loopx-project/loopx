/** Candidate execution facts carried by an existing Explore finding.
 * The producer owns measurement; this codec validates attribution, not utility.
 */
import type {JsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {isExploreEvidenceRef} from "./explore_evidence_ref.ts";
import {
  isValidContentSha256,
  requireBoundedSingleLine,
  requireExactFields,
} from "./stage_methodology_operator.ts";

export const EXPLORE_EXECUTION_OBSERVATION_SCHEMA = "explore_execution_observation_v0";

const EXECUTION_OBSERVATION_REQUIRED_FIELDS = [
  "schema_version", "candidate_sha256", "input_revision", "planned_method_id",
  "executed_method_id", "change_reason", "elapsed_seconds", "limit_seconds",
  "search_iterations", "stop_reason", "evidence_refs",
] as const;
const EXECUTION_OBSERVATION_ALLOWED_FIELDS = [
  ...EXECUTION_OBSERVATION_REQUIRED_FIELDS,
  "exit_code", "result_ref", "result_candidate_sha256",
] as const;

export function normalizeExploreExecutionObservation(params: JsonObject): JsonObject {
  const row = requireExactFields(
    params.observation,
    EXECUTION_OBSERVATION_ALLOWED_FIELDS,
    EXECUTION_OBSERVATION_REQUIRED_FIELDS,
    "execution observation",
  );
  if (row.schema_version !== EXPLORE_EXECUTION_OBSERVATION_SCHEMA) {
    throw new EffectRuntimeRequestError("execution observation fields are missing or unsupported");
  }
  const id = (value: unknown, field: string): string => {
    const result = requireBoundedSingleLine(value, field, 128);
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(result)) {
      throw new EffectRuntimeRequestError(`${field} requires an opaque identifier`);
    }
    return result;
  };
  const evidenceRef = (value: unknown, field: string): string => {
    const result = requireBoundedSingleLine(value, field, 128);
    if (!isExploreEvidenceRef(result)) {
      throw new EffectRuntimeRequestError(`${field} requires an opaque identifier`);
    }
    return result;
  };
  const sha = (value: unknown): string => {
    if (!isValidContentSha256(value)) {
      throw new EffectRuntimeRequestError("execution candidate requires its exact SHA-256");
    }
    return value;
  };
  const measured = (value: unknown, field: string, integer = false, positive = false): number | null => {
    if (value === null) return null;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || (positive && value === 0)
      || (integer && !Number.isSafeInteger(value))) {
      throw new EffectRuntimeRequestError(`${field} requires a nonnegative measurement or explicit null`);
    }
    return value;
  };
  const planned = id(row.planned_method_id, "planned_method_id");
  const executed = id(row.executed_method_id, "executed_method_id");
  const change = row.change_reason === null ? null : requireBoundedSingleLine(row.change_reason, "change_reason", 240);
  if (planned !== executed && change === null) {
    throw new EffectRuntimeRequestError("a different executed method requires its change_reason");
  }
  if (!Array.isArray(row.evidence_refs) || !row.evidence_refs.length || row.evidence_refs.length > 8) {
    throw new EffectRuntimeRequestError("execution observation requires one to eight evidence references");
  }
  const result: JsonObject = {schema_version: row.schema_version,
    candidate_sha256: sha(row.candidate_sha256),
    input_revision: requireBoundedSingleLine(row.input_revision, "input_revision", 160),
    planned_method_id: planned, executed_method_id: executed, change_reason: change,
    elapsed_seconds: measured(row.elapsed_seconds, "elapsed_seconds"),
    limit_seconds: measured(row.limit_seconds, "limit_seconds", false, true),
    search_iterations: measured(row.search_iterations, "search_iterations", true),
    stop_reason: row.stop_reason === null ? null : requireBoundedSingleLine(row.stop_reason, "stop_reason", 160),
    evidence_refs: [...new Set(row.evidence_refs.map(ref => evidenceRef(ref, "execution evidence_ref")))].sort()};
  if (Object.hasOwn(row, "exit_code")) {
    if (row.exit_code !== null && (typeof row.exit_code !== "number" || !Number.isSafeInteger(row.exit_code))) {
      throw new EffectRuntimeRequestError("exit_code requires an integer process status or null");
    }
    result.exit_code = row.exit_code;
  }
  if (Object.hasOwn(row, "result_ref") || Object.hasOwn(row, "result_candidate_sha256")) {
    result.result_ref = evidenceRef(row.result_ref, "result_ref");
    result.result_candidate_sha256 = sha(row.result_candidate_sha256);
    if (result.result_candidate_sha256 !== result.candidate_sha256
      || !(result.evidence_refs as string[]).includes(String(result.result_ref))) {
      throw new EffectRuntimeRequestError("result evidence must name this executed candidate and an included evidence reference");
    }
  }
  return result;
}
