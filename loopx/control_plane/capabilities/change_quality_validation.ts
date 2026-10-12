/** CQR qualification only; execution and causal judgments remain review evidence.
 * pre_existing_unrelated reuses PR review's disposition, without waiving a
 * required integration check or claiming that equal aggregate counts prove it.
 */
import type {JsonObject} from "../effect_program.ts";
import {ENVELOPED_SHA256_PATTERN} from "../content_digest.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireBoolean, requireInteger, requireJsonObject, requireNonEmptyString,
  requireStringArray, requireStringLiteral} from "../runtime_decode.ts";

export type ChangeQualityValidationStatus = "passed" | "failed" | "skipped";
interface Validation extends JsonObject {
  validator: string;
  status: ChangeQualityValidationStatus;
  required: boolean;
}
function fail(message: string): never { throw new EffectRuntimeRequestError(message); }
function text(value: unknown, field: string, limit: number): string {
  const result = requireNonEmptyString(value, field);
  if (result.length > limit) fail(`${field} exceeds ${limit} characters`);
  return result;
}
function fields(value: JsonObject, allowed: string[], label: string): void {
  for (const key of Object.keys(value)) if (!allowed.includes(key)) fail(`${label}.${key} is unsupported`);
}
function observation(value: unknown, label: string): JsonObject {
  const raw = requireJsonObject(value, label);
  fields(raw, ["exit_code", "failure_signature", "fixture_digest", "environment_digest", "evidence_id"], label);
  const exit = requireInteger(raw.exit_code, `${label}.exit_code`);
  if (exit < 1 || exit > 255) fail(`${label} must record a completed failed execution`);
  const result: JsonObject = {exit_code: exit, evidence_id: text(raw.evidence_id, `${label}.evidence_id`, 160)};
  for (const key of ["failure_signature", "fixture_digest", "environment_digest"]) {
    const digest = requireNonEmptyString(raw[key], `${label}.${key}`);
    if (!ENVELOPED_SHA256_PATTERN.test(digest)) fail(`${label}.${key} must be a SHA-256 digest`);
    result[key] = digest;
  }
  return result;
}

function attribution(row: Validation, rows: Validation[], scopeValue: unknown): JsonObject {
  const scope = requireJsonObject(scopeValue, "baseline attribution current scope");
  const raw = requireJsonObject(row.failure_attribution, `${row.validator}.failure_attribution`);
  fields(raw, ["schema_version", "disposition", "base_revision", "head_revision", "scope_fingerprint",
    "same_command", "baseline_observation", "head_observation", "causal_scope_analysis",
    "affected_invariant_evidence"], "failure_attribution");
  if (raw.schema_version !== "change_quality_baseline_attribution_v0" ||
      raw.disposition !== "pre_existing_unrelated") fail("unsupported CQR baseline attribution");
  if (row.status !== "failed") fail("baseline attribution requires a failed validation observation");
  const sources = requireJsonObject(scope.sources, "current scope.sources");
  if (["staged", "unstaged", "untracked"].some(key => requireStringArray(sources[key], key).length > 0)) {
    fail("baseline attribution requires an immutable clean head; commit and requalify");
  }
  for (const [key, scopeKey] of [["base_revision", "base_commit"], ["head_revision", "head_commit"],
    ["scope_fingerprint", "scope_fingerprint"]]) {
    if (typeof raw[key] !== "string" || raw[key] !== scope[scopeKey]) fail(`baseline attribution ${key} is stale`);
  }
  if (raw.base_revision === raw.head_revision) fail("baseline and head revisions must be distinct");
  const command = text(raw.same_command, "same_command", 320);
  if (command !== row.command) fail("baseline attribution must use the validation's same command");
  const base = observation(raw.baseline_observation, "baseline_observation");
  const head = observation(raw.head_observation, "head_observation");
  if (base.evidence_id === head.evidence_id) fail("base and head require independent execution evidence");
  for (const key of ["exit_code", "failure_signature", "fixture_digest", "environment_digest"]) {
    if (base[key] !== head[key]) fail(`baseline attribution ${key} changed`);
  }
  const analysis = text(raw.causal_scope_analysis, "causal_scope_analysis", 320);
  const refs = requireStringArray(raw.affected_invariant_evidence, "affected_invariant_evidence");
  if (!refs.length || refs.length > 20 || new Set(refs).size !== refs.length) fail("independent invariant evidence is required");
  const changed = requireStringArray(scope.changed_files, "scope.changed_files");
  const covered = new Set<string>();
  for (const ref of refs) {
    const evidence = rows.find(item => ref === `validator:${item.validator}`);
    if (!evidence || evidence === row || evidence.status !== "passed" || !evidence.required) {
      fail("invariant evidence must reference another passed required validator");
    }
    const paths = requireStringArray(evidence.covers_paths, `${ref}.covers_paths`);
    if (paths.length > 200 || new Set(paths).size !== paths.length || paths.some(path => !changed.includes(path))) {
      fail("invariant covers_paths must name distinct changed paths");
    }
    for (const path of paths) covered.add(path);
  }
  if (!changed.length || changed.some(path => !covered.has(path))) fail("invariant evidence does not cover the complete changed scope");
  return {schema_version: raw.schema_version, disposition: raw.disposition,
    base_revision: raw.base_revision, head_revision: raw.head_revision,
    scope_fingerprint: raw.scope_fingerprint, same_command: command,
    baseline_observation: base, head_observation: head, causal_scope_analysis: analysis,
    affected_invariant_evidence: refs};
}

/** One typed owner for failed/skipped qualification and the optional exception.
 * No commands run here; evidence shape/freshness is necessary, not execution proof.
 */
export function qualifyChangeQualityValidation(input: JsonObject): JsonObject {
  if (!Array.isArray(input.validation) || !input.validation.length || input.validation.length > 20) {
    fail("validation must contain 1–20 rows");
  }
  const rows = input.validation.map(value => {
    const row = requireJsonObject(value, "validation row");
    const validator = text(row.validator, "validator", 120);
    const status = requireStringLiteral(row.status, ["passed", "failed", "skipped"], "validation.status");
    return {...row, validator, status, required: requireBoolean(row.required, "validation.required")} as Validation;
  });
  if (new Set(rows.map(row => row.validator)).size !== rows.length) fail("validator ids must be unique");
  const blocking: string[] = [], risks: string[] = [];
  for (const row of rows) {
    const attributed = Object.hasOwn(row, "failure_attribution");
    if (attributed) row.failure_attribution = attribution(row, rows, input.scope);
    const ref = `validator:${row.validator}`;
    if ((row.status === "failed" && (row.required || !attributed)) || (row.status === "skipped" && row.required)) {
      blocking.push(ref);
    } else if (row.status !== "passed") risks.push(ref);
  }
  return {validation: rows, blocking_codes: blocking, risk_codes: risks};
}
