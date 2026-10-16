/** Bounded evidence for the existing replan boundary; no settlement authority. */
import {createHash} from "node:crypto";
import {settlementIdentityPayload, SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION, type JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {FRESH_PATH_DISPOSITIONS, requiredSemanticOutcomes} from "./replan_semantics.ts";
import {readReplanSnapshot} from "./replan_history_snapshot.ts";
import {normalizeGoalPathDelta} from "../goals/vision_checkpoint.ts";
import {normalizeRecordedStrideDecision, normalizeStrideStrategy, projectStrideExploreBasis} from "./stride_policy.ts";

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

function objectOrNull(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : null;
}

function sameSourceDigest(left: JsonObject, right: JsonObject, field: string): boolean {
  const value = objectOrNull(left._source_facts)?.[field + "_digest"];
  return typeof value === "string" && BARE_SHA256_PATTERN.test(value) &&
    value === objectOrNull(right._source_facts)?.[field + "_digest"];
}

/** Identity evidence is eligibility input only, never a new settlement authority. */
function accountingMatchKey(row: JsonObject): string | null {
  const source = objectOrNull(row._source_facts);
  const identity = objectOrNull(source?.settlement_identity);
  if (!source || identity?.schema_version !== "quota_settlement_identity_v0") return null;
  const keys = ["goal_id", "agent_id", "todo_id", "turn_instance_id", "effect_id"];
  if (keys.some(key => typeof identity[key] !== "string" || !identity[key])) return null;
  if (keys.slice(0, 4).some(key => identity[key] !== source[key]) ||
      ["goal_id", "agent_id", "todo_id"].some(key => identity[key] !== row[key])) return null;
  if (typeof source.recommended_action_digest !== "string" ||
      !BARE_SHA256_PATTERN.test(source.recommended_action_digest) || !row.recommended_action) return null;
  return JSON.stringify([...keys.map(key => identity[key]), source.recommended_action_digest]);
}

function repeatedAccounting(rows: JsonObject[]): Map<JsonObject, JsonObject> {
  const work = new Map<string, JsonObject[]>();
  for (const row of rows) {
    const key = accountingMatchKey(row);
    if (key && row.progress_observation && row.classification !== "quota_slot_spent") {
      const matches = work.get(key);
      if (matches) matches.push(row);
      else work.set(key, [row]);
    }
  }
  const repeated = new Map<JsonObject, JsonObject>();
  for (const row of rows) {
    const source = objectOrNull(row._source_facts);
    const identity = objectOrNull(source?.settlement_identity);
    const receipt = objectOrNull(source?.quota_spend_commit);
    const key = accountingMatchKey(row);
    if (row.classification !== "quota_slot_spent" || row.progress_observation !== null || !key ||
        receipt?.schema_version !== "quota_spend_commit_receipt_v0" ||
        receipt.effect_id !== String(identity?.effect_id) + "#quota_spend") continue;
    const match = work.get(key)?.find(candidate => row.recommended_action === candidate.recommended_action &&
      (!row.delivery_outcome || sameSourceDigest(row, candidate, "delivery_outcome")));
    if (match) repeated.set(row, match);
  }
  return repeated;
}

function readableEvidenceRow(row: JsonObject): JsonObject {
  const {observed_at: _time, _source_facts: _source, ...readableRow} = row;
  return readableRow;
}

function evidenceFactsKey(row: JsonObject): string {
  const {generated_at: _time, evidence_ref: _ref, ...facts} = readableEvidenceRow(row);
  return digest(facts);
}

export function projectCoreGoal(value: unknown): JsonObject {
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
    objective_missing: objective === null, acceptance_contract: acceptance,
    ...(facts.goal_read_action == null ? {} : {
      read_action: requireNonEmptyString(facts.goal_read_action, "goal_facts.goal_read_action"),
    })};
}

/** Qualify recorded bindings using the settlement owner, without settling work.
 * Keep historical Todo evidence eligible under its existing read contract.
 */
function decisionBindingMatches(row: JsonObject, source: JsonObject, delta: JsonObject | null): boolean {
  const identity = objectOrNull(source.settlement_identity);
  if (!identity || identity.turn_instance_id !== source.turn_instance_id ||
      typeof identity.turn_instance_id !== "string" || !ID.test(identity.turn_instance_id)) return false;
  if (identity.schema_version === SETTLEMENT_IDENTITY_SCHEMA_VERSION) {
    return ["goal_id", "agent_id", "todo_id"].every(key => identity[key] === row[key]);
  }
  if (identity.schema_version !== SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION ||
      typeof row.goal_id !== "string" || typeof row.agent_id !== "string" ||
      [row.todo_id, source.todo_id, identity.todo_id].some(value => value != null && value !== "") ||
      ["goal_id", "agent_id"].some(key => source[key] !== row[key]) ||
      typeof source.replan_obligation_id !== "string" || !ID.test(source.replan_obligation_id) ||
      delta?.obligation_id !== source.replan_obligation_id) return false;
  const expected = settlementIdentityPayload({goal_id: row.goal_id, agent_id: row.agent_id,
    turn_instance_id: identity.turn_instance_id, replan_obligation_id: source.replan_obligation_id});
  return Object.entries(expected).every(([key, value]) => identity[key] === value);
}

function acceptedDecisionSource(row: JsonObject): boolean {
  const source = objectOrNull(row._source_facts);
  const vision = objectOrNull(source?.agent_vision);
  const ack = objectOrNull(source?.autonomous_replan_ack);
  const delta = objectOrNull(ack?.semantic_delta);
  return !!source && decisionBindingMatches(row, source, delta) &&
    !objectOrNull(source.quota_spend_commit) &&
    vision?.schema_version === "goal_vision_replan_contract_v0" && vision.agent_id === row.agent_id &&
    (vision.goal_id == null || vision.goal_id === row.goal_id) &&
    ack?.schema_version === "autonomous_replan_ack_v0" && ack.recorded === true &&
    delta?.schema_version === "replan_semantic_delta_v0" && delta.accepted === true &&
    Array.isArray(delta.satisfying_outcomes) && delta.satisfying_outcomes.includes("fresh_vision_path_outcome");
}

function recordedDecision(row: JsonObject): JsonObject | null {
  if (!acceptedDecisionSource(row)) return null;
  const source = objectOrNull(row._source_facts)!;
  let path: JsonObject | null;
  try {
    [path] = normalizeGoalPathDelta(objectOrNull(source.agent_vision)?.path_delta);
  } catch { return null; }
  if (!path || !FRESH_PATH_DISPOSITIONS.has(String(path.outcome)) ||
      !Array.isArray(path.evidence_refs) || !path.evidence_refs.length) return null;
  const ref = objectOrNull(source.goal_ref);
  if (source.goal_ref != null && (!ref || ref.goal_id !== row.goal_id ||
      typeof ref.goal_instance_id !== "string" || !ID.test(ref.goal_instance_id))) return null;
  let stride: JsonObject | null;
  try { stride = normalizeRecordedStrideDecision(objectOrNull(source.agent_vision)?.stride_decision); }
  catch { return null; }
  const autonomous = objectOrNull(source.settlement_identity)?.schema_version === SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION;
  const decision = {history_evidence_ref: "replan-evidence-" + digest(readableEvidenceRow(row)),
    generated_at: row.generated_at, todo_id: autonomous ? null : row.todo_id, turn_instance_id: source.turn_instance_id,
    ...(autonomous ? {replan_obligation_id: source.replan_obligation_id} : {}),
    goal_ref: ref ? {goal_id: ref.goal_id, goal_instance_id: ref.goal_instance_id} : null,
    path_delta: path, ...(stride ? {stride_decision: stride} : {})};
  // Existing history refs keep their identity. This derived ref additionally
  // binds the path itself, so a changed decision cannot resolve old evidence.
  return {evidence_ref: "replan-decision-" + digest(decision), ...decision};
}

/** Return a recorded decision to the next review, without judging its utility.
 * Append order belongs to the captured history, not timestamps or prose. A
 * progress observation proves neither execution nor scientific improvement.
 */
function decisionFollowthrough(rows: JsonObject[], goalFacts: unknown, fromFullIndex: boolean): JsonObject | null {
  const currentRef = objectOrNull(objectOrNull(goalFacts)?.goal_ref);
  const scoped = rows.filter(row => {
    const source = objectOrNull(row._source_facts);
    const ref = objectOrNull(source?.goal_ref);
    return source && Number.isSafeInteger(source.history_position) &&
      Number(source.history_position) >= 0 &&
      (!currentRef || (ref?.goal_id === currentRef.goal_id &&
        ref?.goal_instance_id === currentRef.goal_instance_id));
  }).sort((a, b) => Number(objectOrNull(b._source_facts)!.history_position) -
    Number(objectOrNull(a._source_facts)!.history_position));
  const previous = scoped.find(acceptedDecisionSource);
  if (!previous) return null;
  // Do not fall back to an older decision when the latest basis is corrupt.
  const decision = recordedDecision(previous);
  if (!decision) return null;
  const source = objectOrNull(previous._source_facts)!;
  const subsequent = new Map<string, JsonObject>();
  for (const row of scoped) {
    const facts = objectOrNull(row._source_facts)!;
    if (Number(facts.history_position) <= Number(source.history_position) || !row.progress_observation) continue;
    const progress = row.progress_observation as JsonObject;
    if (!subsequent.has(String(progress.fingerprint))) subsequent.set(String(progress.fingerprint), row);
  }
  // The complete denominator is retained even when the review details are capped.
  const observations = [...subsequent.values()];
  return {
    previous_decision: decision,
    source_history_complete: fromFullIndex,
    unsequenced_progress_count: rows.filter(row => row.progress_observation &&
      !Number.isSafeInteger(objectOrNull(row._source_facts)?.history_position)).length,
    recorded_progress_count: observations.length,
    observations: observations.slice(0, 6).map(row => ({
      evidence_ref: "replan-evidence-" + digest(readableEvidenceRow(row)),
      generated_at: row.generated_at, todo_id: row.todo_id,
      coverage_ref: (row.progress_observation as JsonObject).fingerprint,
    })),
    omitted_observation_count: Math.max(0, observations.length - 6),
    execution_and_goal_gain: "unknown",
    guidance: "Compare the previous path decision with subsequent work and authoritative outcomes. " +
      "Recorded progress and changed identifiers do not prove execution, a new mechanism, or goal gain. " +
      "Resolve evidence before deciding what to retain, change, or test next.",
  };
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
  if (context.stride_strategy !== undefined) normalizeStrideStrategy(context.stride_strategy);
  if (context.decision_followthrough !== undefined) {
    const follow = requireJsonObject(context.decision_followthrough, "decision_followthrough");
    if (context.stride_strategy !== "evidence_conditioned_v1" || follow.execution_and_goal_gain !== "unknown") {
      throw new EffectRuntimeRequestError("decision followthrough requires the enabled strategy and unknown utility");
    }
    const observations = objects(follow.observations, "decision_followthrough.observations");
    if (observations.length > 6 || typeof follow.source_history_complete !== "boolean" ||
        ["recorded_progress_count", "omitted_observation_count", "unsequenced_progress_count"].some(
          key => !Number.isSafeInteger(follow[key]) || Number(follow[key]) < 0) ||
        Number(follow.recorded_progress_count) !== observations.length + Number(follow.omitted_observation_count)) {
      throw new EffectRuntimeRequestError("decision followthrough requires bounded detail and explicit coverage counts");
    }
    const previous = requireJsonObject(follow.previous_decision, "decision_followthrough.previous_decision");
    identifier(previous.evidence_ref, "previous_decision.evidence_ref");
    identifier(previous.history_evidence_ref, "previous_decision.history_evidence_ref");
    identifier(previous.turn_instance_id, "previous_decision.turn_instance_id");
    if (previous.replan_obligation_id !== undefined) {
      identifier(previous.replan_obligation_id, "previous_decision.replan_obligation_id");
      if (previous.todo_id !== null) throw new EffectRuntimeRequestError("previous decision cannot bind both Todo and autonomous replan");
    }
    normalizeGoalPathDelta(previous.path_delta);
    normalizeRecordedStrideDecision(previous.stride_decision);
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
    JSON.stringify(readableEvidenceRow(a)).localeCompare(JSON.stringify(readableEvidenceRow(b))));
  const unique = new Map<string, JsonObject>();
  for (const row of rows) {
    const readableRow = readableEvidenceRow(row);
    const ref = "replan-evidence-" + digest(readableRow);
    if (!unique.has(ref)) unique.set(ref, {...readableRow, evidence_ref: ref});
  }
  const evidence = [...unique.values()];
  if (request.operation === "resolve") {
    if (!agent) throw new EffectRuntimeRequestError("evidence resolution requires agent_id");
    const ref = identifier(request.evidence_ref, "evidence_ref");
    const match = unique.get(ref) ?? rows.map(recordedDecision).find(decision => decision?.evidence_ref === ref);
    if (!match) throw new EffectRuntimeRequestError(
      "evidence reference is unavailable in this Goal and Agent history window; refresh replan_context",
      "replan_evidence_unavailable");
    return {ok: true, goal_id: goal, agent_id: agent, evidence: match};
  }
  const repeated = repeatedAccounting(rows);
  const repeatedByGroup = new Map<string, {workKey: string; healthRepeated: boolean} | null>();
  for (const row of rows) {
    const key = evidenceFactsKey(row);
    const work = repeated.get(row);
    const proof = work ? {workKey: evidenceFactsKey(work),
      healthRepeated: !row.health_check || sameSourceDigest(row, work, "health_check")} : null;
    // A grouped span can include unpaired Turns with the same display text.
    // Reduce it only if every underlying occurrence has the same shown proof.
    if (!repeatedByGroup.has(key)) repeatedByGroup.set(key, proof);
    else if (JSON.stringify(repeatedByGroup.get(key)) !== JSON.stringify(proof)) repeatedByGroup.set(key, null);
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
    const key = evidenceFactsKey(row);
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
  const shownWorkKeys = new Set(chosen.map(({row}) => evidenceFactsKey(row)));
  const shown = chosen.flatMap(({row, count, first}) => {
    const repeat = repeatedByGroup.get(evidenceFactsKey(row));
    // Never remove an action whose matching work was omitted by the display cap.
    const represented = repeat && shownWorkKeys.has(repeat.workKey);
    if (represented && repeat.healthRepeated) return [];
    return [{
      generated_at: row.generated_at,
      evidence_ref: row.evidence_ref,
      summary: [...new Set([row.health_check, represented ? null : row.recommended_action].filter(Boolean))].join(" ") ||
        row.classification || "Recorded observation",
      ...(row.delivery_outcome ? {delivery_outcome: row.delivery_outcome} : {}),
      ...(row.progress_observation ? {coverage_ref: (row.progress_observation as JsonObject).fingerprint} : {}),
      ...(count > 1 ? {occurrences: count, first_observed_at: first} : {}),
      ...(agent ? {read_action: `${prefix} --format json history --goal-id ${goal} --agent-id ${agent} --evidence-ref ${row.evidence_ref}`} : {}),
    }];
  });
  const selectedCoverage = new Set(chosen.flatMap(({row}) => row.progress_observation
    ? [String((row.progress_observation as JsonObject).fingerprint)] : []));
  const ledger = [...coverage].filter(([key]) => selectedCoverage.has(key)).map(([, value]) => value);
  const core = projectCoreGoal(request.goal_facts);
  const strideStrategy = normalizeStrideStrategy(request.stride_strategy);
  const exploreBasis = strideStrategy !== "off" ? projectStrideExploreBasis(request.explore_context, goal, agent) : null;
  const followthrough = agent && strideStrategy !== "off"
    ? decisionFollowthrough(rows, request.goal_facts, request.from_full_index === true) : null;
  const contextId = "replan-context-" + digest({goal, agent, obligationId, core, ledger, shown, frontier,
    ...(strideStrategy !== "off" ? {stride_strategy: strideStrategy} : {}),
    ...(followthrough ? {decision_followthrough: followthrough} : {}),
    ...(exploreBasis ? {explore_basis: exploreBasis} : {})});
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
  if (strideStrategy !== "off") context.stride_strategy = strideStrategy;
  if (followthrough) context.decision_followthrough = followthrough;
  if (exploreBasis) context.explore_basis = exploreBasis;
  // Source-replayed supplemental pages qualify a proposal, not a new admission
  // context. They must not change the original context or bounded basis IDs.
  if (exploreBasis?.availability === "available" && request.explore_context != null) {
    const source = requireJsonObject(request.explore_context, "Explore stride source");
    if (source.review_pages !== undefined) context.explore_result_pages = source.review_pages;
  }
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
