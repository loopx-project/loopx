/** Current use of explicit local delegation sources; never rewrites completion.
 * Host adapters collect authorized current observations. This owner alone decides
 * version agreement and transitive eligibility; absence is not a saved success. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";

export type DelegationResultUseState = "current" | "unavailable";
export type DelegationResultUseReason = "input_unavailable" | "source_unavailable"
  | "source_version_changed" | "dependency_cycle" | "verification_budget_exhausted";
export type DelegationResultUse = {state: DelegationResultUseState; checked_operation_count: number;
  reason?: DelegationResultUseReason; blocking_operation_id?: string; blocking_input_ref?: string; path?: string[]};
type Input = {operation_id: string; ref: string; sha256: string; input_ref: string; input_available: boolean};
type Node = {operation_id: string; accepted: boolean; artifacts: {ref: string; sha256: string}[]; inputs: Input[]};
export const DELEGATION_RESULT_USE_MAX_OPERATIONS = 64;
export const DELEGATION_RESULT_USE_MAX_DEPTH = 16;

export function delegationResultUseLimits(): JsonObject {
  return {max_operations: DELEGATION_RESULT_USE_MAX_OPERATIONS,
    max_depth: DELEGATION_RESULT_USE_MAX_DEPTH, max_seconds: 15};
}

function requireThat(value: unknown, message: string): asserts value {
  if (!value) throw new EffectRuntimeRequestError(message);
}
function operation(value: unknown): string {
  requireThat(typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(value),
    "valid delegation operation required");
  return value;
}
function reference(value: unknown): string {
  requireThat(typeof value === "string" && value.length > 0 && value.length <= 1024
    && !value.startsWith("/") && !value.includes("\\") && !value.split("/").includes(".."),
  "local delegation artifact reference required");
  return value;
}
function version(value: unknown): string {
  requireThat(typeof value === "string" && BARE_SHA256_PATTERN.test(value), "delegation version required");
  return value;
}
function inputs(value: unknown): Input[] {
  requireThat(Array.isArray(value) && value.length <= 32, "bounded delegation inputs required");
  return value.map(raw => {
    const row = requireJsonObject(raw, "delegation source input");
    requireThat(typeof row.input_available === "boolean", "current input observation required");
    return {operation_id: operation(row.operation_id), ref: reference(row.ref),
      sha256: version(row.sha256), input_ref: reference(row.input_ref), input_available: row.input_available};
  });
}
export function qualifyDelegationResultUse(params: JsonObject): DelegationResultUse {
  const roots = inputs(params.roots);
  requireThat(Array.isArray(params.nodes) && params.nodes.length <= DELEGATION_RESULT_USE_MAX_OPERATIONS,
    "bounded delegation source observations required");
  requireThat(params.truncated === undefined || typeof params.truncated === "boolean", "collection outcome required");
  const nodes = new Map<string, Node>();
  for (const raw of params.nodes) {
    const row = requireJsonObject(raw, "delegation source observation");
    const id = operation(row.operation_id);
    requireThat(!nodes.has(id), "duplicate delegation source observation");
    requireThat(typeof row.accepted === "boolean" && Array.isArray(row.artifacts) && row.artifacts.length <= 20,
      "current source acceptance and artifacts required");
    nodes.set(id, {operation_id: id, accepted: row.accepted,
      artifacts: row.artifacts.map(rawArtifact => {
        const artifact = requireJsonObject(rawArtifact, "delegation source artifact");
        return {ref: reference(artifact.ref), sha256: version(artifact.sha256)};
      }), inputs: inputs(row.inputs)});
  }
  const checked = new Set<string>();
  const complete = new Set<string>();
  const visiting = new Set<string>();
  const unavailable = (reason: DelegationResultUseReason, path: string[]): DelegationResultUse => ({
    state: "unavailable", reason, blocking_operation_id: path.at(-1), path,
    checked_operation_count: checked.size,
  });
  function visit(input: Input, path: string[]): DelegationResultUse | null {
    // The receiver's input is part of each edge, even for a memoized source.
    if (!input.input_available) return {...unavailable("input_unavailable", path), blocking_input_ref: input.input_ref};
    const next = [...path, input.operation_id];
    if (next.length > DELEGATION_RESULT_USE_MAX_DEPTH) return unavailable("verification_budget_exhausted", next);
    if (visiting.has(input.operation_id)) return unavailable("dependency_cycle", next);
    const source = nodes.get(input.operation_id);
    if (!source) return unavailable(params.truncated === true ? "verification_budget_exhausted" : "source_unavailable", next);
    checked.add(input.operation_id);
    if (!source.accepted) return unavailable("source_unavailable", next);
    if (!source.artifacts.some(row => row.ref === input.ref && row.sha256 === input.sha256))
      return unavailable("source_version_changed", next);
    if (complete.has(input.operation_id)) return null;
    visiting.add(input.operation_id);
    for (const ancestor of source.inputs) {
      const failure = visit(ancestor, next);
      if (failure) return failure;
    }
    visiting.delete(input.operation_id);
    complete.add(input.operation_id);
    return null;
  }
  for (const input of roots) {
    const failure = visit(input, []);
    if (failure) return failure;
  }
  if (params.truncated === true) return unavailable("verification_budget_exhausted",
    roots.length ? [roots[0].operation_id] : []);
  return {state: "current", checked_operation_count: checked.size};
}
