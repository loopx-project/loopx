/** Bounded evidence for the existing replan boundary; no settlement authority. */
import {createHash} from "node:crypto";
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {requiredSemanticOutcomes} from "./replan_semantics.ts";
import {readReplanSnapshot} from "./replan_history_snapshot.ts";

const CONTEXT_SCHEMA = "replan_context_v0";
// Replan is an infrequent decision, not the ordinary guard's display window.
// Bound distinct observations after repetition reduction, not raw newest runs.
const MAX_EVIDENCE_ROWS = 24;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function identifier(value: unknown, label: string): string {
  const result = requireNonEmptyString(value, label);
  if (!ID.test(result)) throw new EffectRuntimeRequestError(`${label} must be a public-safe identifier`);
  return result;
}

function objects(value: unknown, label: string): JsonObject[] {
  if (!Array.isArray(value)) throw new EffectRuntimeRequestError(`${label} must be an array`);
  return value.map(item => requireJsonObject(item, label));
}

function digest(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex").slice(0, 24);
}

function coreGoal(value: unknown): JsonObject {
  const facts = value == null ? {} : requireJsonObject(value, "goal_facts");
  const active = facts.active_state_objective;
  const registered = facts.registry_objective;
  for (const objective of [active, registered]) {
    if (objective != null && typeof objective !== "string") throw new EffectRuntimeRequestError("Goal objective must be text");
  }
  const objective = active || registered || null;
  const acceptance = facts.acceptance_contract == null ? null
    : requireJsonObject(facts.acceptance_contract, "goal_acceptance_contract");
  return {objective, objective_source: active ? "active_state" : registered ? "registry" : null,
    objective_missing: objective === null, acceptance_contract: acceptance};
}

export function validateReplanContext(value: unknown): JsonObject {
  const context = requireJsonObject(value, "replan_context");
  if (context.schema_version !== CONTEXT_SCHEMA) {
    throw new EffectRuntimeRequestError("unsupported replan_context schema_version");
  }
  identifier(context.goal_id, "replan_context.goal_id");
  if (context.agent_id !== null) identifier(context.agent_id, "replan_context.agent_id");
  identifier(context.context_id, "replan_context.context_id");
  const core = requireJsonObject(context.core_goal, "replan_context.core_goal");
  if ((core.objective !== null && typeof core.objective !== "string") ||
      core.objective_missing !== (core.objective === null)) {
    throw new EffectRuntimeRequestError("replan_context core_goal requires objective and explicit missing state");
  }
  if (core.acceptance_contract !== null) requireJsonObject(core.acceptance_contract, "core_goal.acceptance_contract");
  objects(context.coverage_ledger, "replan_context.coverage_ledger");
  for (const row of objects(context.evidence, "replan_context.evidence")) {
    identifier(row.evidence_ref, "evidence.evidence_ref");
    requireNonEmptyString(row.generated_at, "evidence.generated_at");
  }
  const frontier = requireJsonObject(context.uncovered_frontier, "replan_context.uncovered_frontier");
  if (!Array.isArray(frontier.required_any_of)) {
    throw new EffectRuntimeRequestError("replan_context uncovered_frontier requires required_any_of");
  }
  return context;
}

export async function projectReplanContextSnapshot(value: unknown): Promise<JsonObject> {
  return projectReplanContext(await readReplanSnapshot(value));
}

export function projectReplanContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "replan context request");
  if (request.operation === "validate") return validateReplanContext(request.context);
  if (request.operation !== "project" && request.operation !== "resolve") {
    throw new EffectRuntimeRequestError("unsupported replan context operation");
  }
  const goal = identifier(request.goal_id, "goal_id");
  const agent = request.agent_id === null ? null : identifier(request.agent_id, "agent_id");
  const obligation = request.obligation === null ? null : requireJsonObject(request.obligation, "obligation");
  const obligationId = obligation ? identifier(obligation.obligation_id, "obligation_id") : null;
  const input = objects(request.rows, "rows");
  for (const row of input) {
    identifier(row.goal_id, "evidence.goal_id");
    if (row.agent_id !== null) identifier(row.agent_id, "evidence.agent_id");
  }
  const rows = input.filter(row => row.goal_id === goal && (agent === null || row.agent_id === agent));
  for (const row of rows) {
    requireNonEmptyString(row.generated_at, "evidence.generated_at");
    if (typeof row.observed_at !== "number" || !Number.isFinite(row.observed_at)) {
      throw new EffectRuntimeRequestError("evidence.observed_at must be a valid timestamp");
    }
    if (row.progress_observation !== null) {
      const progress = requireJsonObject(row.progress_observation, "progress_observation");
      if (progress.schema_version !== "typed_progress_observation_v0") {
        throw new EffectRuntimeRequestError("unsupported progress_observation schema_version");
      }
      identifier(progress.fingerprint, "progress_observation.fingerprint");
    }
  }
  rows.sort((a, b) => Number(b.observed_at) - Number(a.observed_at) ||
    JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const unique = new Map<string, JsonObject>();
  for (const row of rows) {
    const {observed_at: _time, ...publicRow} = row;
    const ref = "replan-evidence-" + digest(publicRow);
    if (!unique.has(ref)) unique.set(ref, {...publicRow, evidence_ref: ref});
  }
  const evidence = [...unique.values()];
  if (request.operation === "resolve") {
    if (!agent) throw new EffectRuntimeRequestError("evidence resolution requires agent_id");
    const ref = identifier(request.evidence_ref, "evidence_ref");
    const match = unique.get(ref);
    if (!match) throw new EffectRuntimeRequestError(
      "evidence reference is unavailable in this Goal and Agent history window; refresh replan_context",
      "replan_evidence_unavailable");
    return {ok: true, goal_id: goal, agent_id: agent, evidence: match};
  }
  const coverage = new Map<string, JsonObject>();
  for (const row of evidence) {
    if (row.progress_observation === null) continue;
    const progress = requireJsonObject(row.progress_observation, "progress_observation");
    const fingerprint = String(progress.fingerprint);
    if (!coverage.has(fingerprint)) coverage.set(fingerprint, {...progress, generated_at: row.generated_at});
  }
  const baseline = obligation?.progress_baseline ?? (
    Array.isArray(obligation?.triggers)
      ? (obligation.triggers as JsonObject[]).find(trigger => trigger.progress_baseline)?.progress_baseline
      : null
  ) ?? null;
  const frontier = {baseline, required_any_of: obligation ? requiredSemanticOutcomes(obligation) : []};
  const prefix = request.read_prefix === undefined ? "loopx" : requireNonEmptyString(request.read_prefix, "read_prefix");
  const groups = new Map<string, {row: JsonObject; count: number; first: unknown}>();
  for (const row of evidence) {
    const {generated_at: _time, evidence_ref: _ref, ...facts} = row;
    const key = digest(facts);
    const group = groups.get(key);
    if (group) { group.count++; group.first = row.generated_at; }
    else groups.set(key, {row, count: 1, first: row.generated_at});
  }
  const candidates = [...groups.values()];
  const selected = new Set<typeof candidates[number]>();
  // Preserve observed result diversity, then distinct routes, then recency.
  // No prose classifier infers that an unchanged score proves a hypothesis false.
  for (const dimensions of [["result_class"], ["surface_id", "hypothesis_id", "probe_kind"]]) {
    const seen = new Set<string>();
    for (const candidate of candidates) {
      const progress = candidate.row.progress_observation as JsonObject | null;
      if (!progress) continue;
      const key = JSON.stringify(dimensions.map(field => progress[field] ?? null));
      if (!seen.has(key) && selected.size < MAX_EVIDENCE_ROWS) selected.add(candidate);
      seen.add(key);
    }
  }
  for (const candidate of candidates) {
    if (selected.size < MAX_EVIDENCE_ROWS) selected.add(candidate);
  }
  const chosen = candidates.filter(candidate => selected.has(candidate));
  const omitted = candidates.filter(candidate => !selected.has(candidate));
  const shown = chosen.map(({row, count, first}) => ({
    generated_at: row.generated_at,
    evidence_ref: row.evidence_ref,
    summary: [...new Set([row.health_check, row.recommended_action].filter(Boolean))].join(" ") ||
      row.classification || "Recorded observation",
    ...(row.delivery_outcome ? {delivery_outcome: row.delivery_outcome} : {}),
    ...(row.progress_observation ? {coverage_ref: (row.progress_observation as JsonObject).fingerprint} : {}),
    ...(count > 1 ? {occurrences: count, first_observed_at: first} : {}),
    ...(agent ? {read_action: `${prefix} --format json history --goal-id ${goal} --agent-id ${agent} --evidence-ref ${row.evidence_ref}`} : {}),
  }));
  const selectedCoverage = new Set(chosen.flatMap(({row}) => row.progress_observation
    ? [String((row.progress_observation as JsonObject).fingerprint)] : []));
  const ledger = [...coverage].filter(([key]) => selectedCoverage.has(key)).map(([, value]) => value);
  const core = coreGoal(request.goal_facts);
  const contextId = "replan-context-" + digest({goal, agent, obligationId, core, ledger, shown, frontier});
  const context: JsonObject = {
    schema_version: CONTEXT_SCHEMA, context_id: contextId, goal_id: goal, agent_id: agent,
    obligation_id: obligationId, evidence_source: "compact_run_history", delivery: "host_projected",
    from_full_index: request.from_full_index === true,
    core_goal: core,
    evidence: shown, evidence_count: evidence.length, distinct_observation_count: candidates.length,
    evidence_truncated: omitted.length > 0,
    coverage_ledger: ledger, coverage_count: coverage.size, coverage_truncated: coverage.size > ledger.length,
    uncovered_frontier: frontier,
  };
  if (omitted.length) context.omitted_evidence = {
    distinct_observation_count: omitted.length,
    newest_at: omitted[0].row.generated_at, oldest_at: omitted.at(-1)!.first,
    ...(agent ? {read_action: `${prefix} --format json history --goal-id ${goal} --agent-id ${agent} --limit ${input.length}`} : {}),
  };
  if (obligation) context.delivery_receipt = {schema_version: "replan_context_delivery_receipt_v0",
      context_id: contextId, obligation_id: obligationId, status: "delivered",
      delivered_by: "quota_host_projection"};
  return context;
}
