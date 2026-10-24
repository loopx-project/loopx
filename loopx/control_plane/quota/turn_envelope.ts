import { EffectiveAction } from "./effective_action.generated.ts";
import { createHash } from "node:crypto";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

import {
  interpretQuotaShouldRunPacket,
  assertSupportedQuotaPacket,
  settlementIdentityFromPlan,
  settlementIdentityPayload,
  type JsonObject,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { projectTurnStartUnavailableContext } from "../capability_hooks.ts";
import { requireBoolean, requireJsonObject, requireNonEmptyString, requireStringLiteral } from "../runtime_decode.ts";
import { projectPendingCapabilityIntent } from "../work_items/pending_capability_intent.ts";
import { projectInteractionRequiredReads, selectedWorkTodoId, USER_CHANNEL_NOTIFICATION_POLICIES } from "../work_items/interaction_contract.ts";
import { measureTurnEnvelope, TURN_ENVELOPE_SECTION_TARGETS } from "./turn_envelope_budget.ts";
export { TURN_ENVELOPE_BUDGET_BYTES } from "./turn_envelope_budget.ts";

export const TURN_ENVELOPE_SCHEMA_VERSION = "loopx_turn_envelope_v0";
export const CONTRACT_CAPSULE_SCHEMA_VERSION = "loopx_contract_capsule_v0";
export const ACTION_SIGNATURE_SCHEMA_VERSION = "loopx_action_signature_v0";
export const ACTION_SIGNATURE_COVERAGE_V0 = "turn_envelope_action_dimensions_v0";
export const ACTION_SIGNATURE_COVERAGE_V1 = "turn_envelope_action_dimensions_v1";
export const ACTION_SIGNATURE_COVERAGE_V2 = "turn_envelope_action_dimensions_v2";
export const ACTION_SIGNATURE_COVERAGE_V3 = "turn_envelope_action_dimensions_v3";

export const SCHEDULER_QUOTA_IDENTITY_KEYS = ["goal_id", "agent_identity.agent_id", "effective_action", "heartbeat_recommendation.recommended_mode", "interaction_contract.mode"];

export function schedulerQuotaContext(payload: JsonObject) {
  return {
    recommended_mode: String(object(payload.heartbeat_recommendation).recommended_mode || ""),
    spend_policy: object(payload.automation_liveness).spend_policy
      || object(payload.execution_obligation).spend_policy
      || object(payload.heartbeat_recommendation).spend_policy || null,
  };
}

export function readMonitorHeartbeatRecommendation(decision: JsonObject): JsonObject {
  return decision.heartbeat_recommendation == null ? {}
    : requireJsonObject(decision.heartbeat_recommendation, "decision.heartbeat_recommendation");
}

export function monitorHeartbeatDecisionFields(decision: JsonObject): JsonObject {
  return {heartbeat_recommendation: readMonitorHeartbeatRecommendation(decision)};
}
/** Keep legacy heartbeat fields at their existing quota projection boundary;
 * the interaction owner still decides required reads and scheduler arbitration.
 */
export function projectInteractionReadsAndGuidance(request: JsonObject): JsonObject {
  const result = projectInteractionRequiredReads(request);
  return {...result, ...interactionHeartbeatRecommendation(request, requireJsonObject(result.arbitration, "arbitration"))};
}

/** Legacy Goal commands have no Task binding. Retain their original route only
 * for admitted unscoped delivery; current selection, recovery and settlement
 * are owned by the interaction contract, including on later same-Turn reentry.
 * This projects guidance, never execution authority or a replacement command.
 */
function interactionHeartbeatRecommendation(request: JsonObject, arbitration: JsonObject): JsonObject {
  if (request.heartbeat_recommendation === undefined) return {};
  const recommendation = requireJsonObject(request.heartbeat_recommendation, "heartbeat_recommendation");
  const facts = requireJsonObject(request.arbitration_facts, "scheduler arbitration facts");
  const mode = requireNonEmptyString(facts.mode, "interaction mode");
  const agent = requireJsonObject(facts.agent_channel, "agent channel facts");
  const legacyDelivery = request.should_run === true && request.delivery_allowed === true
    && request.selection_required === false && request.has_replan === false && request.settlement_only === false
    && selectedWorkTodoId(request) === null && (mode === "bounded_delivery" || mode === "bounded_delivery_with_user_notice")
    && Array.isArray(arbitration.errors) && arbitration.errors.length === 0;
  const reconciled = recommendation.source === "interaction_contract";
  if (!reconciled && (!Object.hasOwn(recommendation, "command") || legacyDelivery)) {
    return {heartbeat_recommendation: recommendation};
  }
  const projected = {...recommendation};
  delete projected.command;
  projected.source = "interaction_contract";
  projected.recommended_mode = mode;
  projected.agent_must_attempt = requireBoolean(agent.must_attempt, "agent must_attempt");
  projected.reason = request.primary_action ?? null;
  projected.notify = requireStringLiteral(request.notify, USER_CHANNEL_NOTIFICATION_POLICIES, "heartbeat notify");
  projected.spend_policy = request.spend_policy ?? null;
  return {heartbeat_recommendation: projected};
}

export const ACTION_SIGNATURE_COVERAGE_V4 = "turn_envelope_action_dimensions_v4";
export const ACTION_SIGNATURE_COVERAGE_V5 = "turn_envelope_action_dimensions_v5";
export const ACTION_SIGNATURE_COVERAGE = ACTION_SIGNATURE_COVERAGE_V0;

const EXECUTABLE_CLI_ARGS_MAX_ITEMS = 64;
const EXECUTABLE_CLI_ARGS_MAX_ITEM_CHARS = 512;
const EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS = 2_048;
const PLANNING_HORIZON_DETAIL_REFS_REF = "$.detail_ref";

const ACTIONABLE_WARNING_FIELDS = [
  "state_projection_gap",
  "boundary_projection_gap",
  "state_action_projection_warning",
  "next_action_projection_warning",
  "stale_latest_run_warning",
  "decision_freshness_warning",
] as const;

const CONTRACT_CAPSULE_FIELDS: Readonly<Record<string, readonly string[]>> = {
  interaction_contract: ["schema_version", "mode"],
  work_lane_contract: [
    "schema_version", "lane", "monitor_kind", "next_lane", "obligation",
    "must_attempt_work", "reason_codes", "monitor_policy", "selected_todo_id",
    "selected_next_due_at", "non_runnable_non_monitor_count",
    "material_transition", "action",
  ],
  execution_obligation: [
    "kind", "contract", "contract_obligation", "must_attempt_work",
    "notify_is_execution_gate", "delivery_allowed", "reason",
  ],
  goal_route_hint: [
    "schema_version", "kind", "route_decision", "preserves_goal_next_action",
    "goal_next_action_mutation",
  ],
  autonomous_replan_scope: [
    "schema_version", "required", "applies", "scope", "owner_agent_ids",
    "selected_peer_agent",
  ],
  agent_scope_frontier: [
    "schema_version", "action", "effective_action", "blocks_delivery",
    "quiet_noop_allowed", "requires_replan", "recommended_action", "spend_policy",
  ],
  automation_liveness: [
    "schema_version", "keep_active", "pause_allowed", "pause_policy",
    "automation_action",
  ],
  handoff_readiness: [
    "ready", "codex_ready", "handoff_status", "post_handoff_run_seen",
  ],
  capability_monitor_fallback: [
    "schema_version", "capability_gate_action", "blocked_advancement_count",
    "blocked_due_monitor_count", "mode",
  ],
};

const CONTRACT_CAPSULE_TEXT_LIMITS: Readonly<Record<string, Readonly<Record<string, number>>>> = {
  work_lane_contract: { action: 300, material_transition: 240 },
  execution_obligation: { reason: 240 },
  agent_scope_frontier: { recommended_action: 320, spend_policy: 220 },
  automation_liveness: { pause_policy: 260 },
};

function object(value: unknown): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? { ...(value as JsonObject) }
    : {};
}

type PythonScalar = string | number | boolean | null | undefined;

function pythonString(value: PythonScalar): string {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  return String(value);
}

function scalarString(value: unknown, label: string, fallback = ""): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (
    typeof value !== "string" &&
    typeof value !== "number" &&
    typeof value !== "boolean"
  ) {
    throw new EffectRuntimeRequestError(`${label} must be scalar-compatible`);
  }
  return pythonString(value);
}

function integer(value: unknown, label: string): number {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "boolean") return Number(value);
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && /^[+-]?\d+$/u.test(value.trim())) {
    return Number.parseInt(value, 10);
  }
  throw new EffectRuntimeRequestError(`${label} must be integer-compatible`);
}

function text(value: unknown, limit: number): string | null {
  if (value === null || value === undefined) return null;
  const compact = scalarString(value, "text value").trim().split(/\s+/u).join(" ");
  if (!compact) return null;
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}

function textList(value: unknown, limit: number, itemLimit = 240): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value) {
    const rendered = text(item, itemLimit);
    if (!rendered || result.includes(rendered)) continue;
    result.push(rendered);
    if (result.length >= limit) break;
  }
  return result;
}

function executableCliArgs(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > EXECUTABLE_CLI_ARGS_MAX_ITEMS) {
    return [];
  }
  const result: string[] = [];
  let totalChars = 0;
  for (const item of value) {
    if (typeof item !== "string" || !item || item.length > EXECUTABLE_CLI_ARGS_MAX_ITEM_CHARS) {
      return [];
    }
    totalChars += item.length + 1;
    if (totalChars > EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS) return [];
    result.push(item);
  }
  return result;
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value !== "object" || value === null) return value;
  const source = value as JsonObject;
  return Object.fromEntries(
    Object.keys(source).sort(comparePythonUnicode).map((key) => [key, canonicalValue(source[key])]),
  );
}

function comparePythonUnicode(left: string, right: string): number {
  const leftPoints = Array.from(left, (item) => item.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (item) => item.codePointAt(0) ?? 0);
  const sharedLength = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = leftPoints[index] - rightPoints[index];
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

function canonicalHash(value: unknown): string {
  return `sha256:${createHash("sha256").update(JSON.stringify(canonicalValue(value)), "utf8").digest("hex")}`;
}

function compactFields(
  value: unknown,
  fields: readonly string[],
  limits: Readonly<Record<string, number>> = {},
): JsonObject {
  const source = object(value);
  const compact: JsonObject = {};
  for (const field of fields) {
    const raw = source[field];
    if (raw === null || raw === undefined) continue;
    if (limits[field] !== undefined) {
      const rendered = text(raw, limits[field]);
      if (rendered) compact[field] = rendered;
    } else if (Array.isArray(raw)) {
      const values = textList(raw, 6, 140);
      if (values.length > 0) compact[field] = values;
    } else {
      compact[field] = raw;
    }
  }
  return compact;
}

function selectedTodo(payload: JsonObject, recommendedAction: string | null): JsonObject | null {
  const source = object(payload.selected_todo);
  if (Object.keys(source).length === 0) return null;
  const compact: JsonObject = {};
  for (const field of [
    "todo_id", "priority", "status", "task_class", "action_kind",
    "task_repository", "continuation_policy", "claimed_by", "bound_agent",
    "goal_bound", "blocks_agent", "unblocks_todo_id", "next_due_at",
    "expires_at", "selected_by", "confidence",
    "resume_when", "resume_ready", "resume_condition",
  ]) {
    if (source[field] !== null && source[field] !== undefined) compact[field] = source[field];
  }
  const context = object(object(object(payload.interaction_contract).agent_channel).work_context);
  if (context.selected_todo_text_verified === true) compact.text_source_verified = true;
  // Work declarations can end in acceptance/stop conditions. Keep their exact
  // text; a display prefix or case-folded recommendation cannot stand in for it.
  const rendered = scalarString(source.text, "selected_todo.text");
  if (rendered && rendered === recommendedAction) {
    compact.text_ref = "action.recommended_action";
  } else if (rendered) {
    compact.text = rendered;
  }
  return Object.keys(compact).length > 0 ? compact : null;
}

/** These reads stay on one captured observation; they never re-enter the guard. */
function replanReadCommands(payload: JsonObject): JsonObject | null {
  const path = payload.replan_read_path;
  const packet = object(payload.replan_action_packet);
  if (typeof path !== "string" || !path.trim() || path.includes("\0") || payload.ok !== true ||
      !packet.obligation_id || packet.settlement_only === true) return null;
  // Retain the renderer's executable across reads. A different ambient LoopX
  // may not understand this captured contract. Legacy captures keep their route.
  const argv = payload.replan_read_argv ?? ["loopx"];
  if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== "string" || !arg || /[\x00-\x1f]/.test(arg))) {
    throw new EffectRuntimeRequestError("captured replan CLI route is malformed");
  }
  const command = `${argv.map(arg => shellQuote(arg as string)).join(" ")} --format json quota replan-view --decision-file ${shellQuote(path)}` +
    ` --source-hash ${canonicalHash(payload)}`;
  return {review: `${command} --replan-stage review`, writeback: `${command} --replan-stage writeback`,
    acceptance: `${command} --replan-stage acceptance`, explore: `${command} --replan-stage explore`};
}

function replanView(request: JsonObject): JsonObject {
  const payload = requireJsonObject(request.payload, "captured replan decision");
  if (request.source_hash !== canonicalHash(payload) || payload.ok !== true) {
    throw new EffectRuntimeRequestError("Replan read requires the original successful decision and matching source hash");
  }
  const packet = object(payload.replan_action_packet);
  const obligation = object(payload.autonomous_replan_obligation);
  const context = object(obligation.replan_context);
  if (!packet.obligation_id || packet.settlement_only === true || packet.obligation_id !== context.obligation_id) {
    throw new EffectRuntimeRequestError("Replan read requires a matching decision context");
  }
  const common = {ok: true, goal_id: payload.goal_id, agent_id: payload.agent_id ?? object(payload.agent_identity).agent_id,
    obligation_id: packet.obligation_id, context_id: context.context_id, source_hash: request.source_hash,
    stage: request.stage};
  const reads = replanReadCommands(payload);
  const sourceRead = reads ? {source_read: `cat -- ${shellQuote(payload.replan_read_path as string)}`} : {};
  const outcomes = new Map<string, JsonObject>();
  function indexedGoal(value: unknown): JsonObject {
    const core = {...object(value)};
    const acceptance = {...object(core.acceptance_contract)};
    const requirements = object(acceptance.outcome_requirements);
    if (!Array.isArray(requirements.outcomes) || !requirements.outcomes.length || !reads) return core;
    const outcomeRefs = requirements.outcomes.map(value => {
      const outcome = requireJsonObject(value, "captured acceptance outcome");
      const ref = canonicalHash(outcome);
      outcomes.set(ref, outcome);
      return ref;
    });
    const {outcomes: _outcomes, ...metadata} = requirements;
    delete acceptance.outcome_requirements;
    return {...core, acceptance_contract: acceptance,
      outcome_requirements_index: {...metadata, outcome_refs: outcomeRefs}};
  }
  const core = indexedGoal(context.core_goal);
  const horizon = {...object(payload.planning_horizon)};
  if (horizon.core_goal) {
    if (canonicalHash(horizon.core_goal) === canonicalHash(context.core_goal)) {
      delete horizon.core_goal;
      horizon.core_goal_ref = "$.core_goal";
    } else horizon.core_goal = indexedGoal(horizon.core_goal);
  }
  if (request.stage === "acceptance") {
    const outcome = outcomes.get(String(request.outcome_ref));
    if (!outcome) throw new EffectRuntimeRequestError("acceptance read requires an exact captured outcome reference");
    return {...common, outcome_ref: request.outcome_ref, outcome};
  }
  if (request.stage === "explore") return {...common, explore_basis: context.explore_basis ?? null};
  if (request.stage === "writeback") {
    // Preserve executable shape and all settlement rules; decision advice and
    // duplicate context are available in the unchanged full captured source.
    const stride = {...object(packet.stride_control)};
    for (const key of ["decision_followthrough", "explore_basis", "decision_value_review", "minimal_example"]) delete stride[key];
    const contract = {...object(packet.writeback_contract)};
    if (contract.vision_authoring) {
      const authoring = {...object(contract.vision_authoring)};
      delete authoring.minimal_example;
      contract.vision_authoring = authoring;
    }
    return {...common, ...sourceRead, writeback_contract: contract,
      required_outcome: packet.required_outcome, allowed_terminal: packet.allowed_terminal,
      ...(packet.stride_control ? {stride_control: stride} : {}),
      writeback: actionProjection(payload).writeback,
      context_read: replanReadCommands(payload)?.review ?? null};
  }
  if (request.stage !== "review") throw new EffectRuntimeRequestError("unsupported replan read stage");
  // Index every captured outcome, including differing context/horizon revisions.
  // Full-content equality alone shares a row; identical IDs do not erase edits.
  const acceptanceIndex = outcomes.size ? {
    read_action_template: `${reads!.acceptance} --outcome-ref {outcome_ref}`,
    outcomes: [...outcomes].map(([ref, outcome]) => ({outcome_ref: ref, id: outcome.id,
      objective: outcome.objective, revision: outcome.revision,
      requirement_count: Array.isArray(outcome.requirements) ? outcome.requirements.length : null})),
  } : null;
  const coverage = Array.isArray(context.coverage_ledger) ? context.coverage_ledger.map(object) : [];
  const follow = {...object(context.decision_followthrough)};
  delete follow.guidance;
  if (Object.hasOwn(follow, "recorded_progress_count")) {
    follow.observation_count = follow.recorded_progress_count;
    delete follow.recorded_progress_count;
  }
  return {...common,
    core_goal: core,
    ...(acceptanceIndex ? {acceptance_index: acceptanceIndex} : {}),
    triggers: obligation.triggers,
    decision_followthrough: Object.keys(follow).length ? follow : null,
    planning_horizon: payload.planning_horizon == null ? null : horizon,
    action_portfolio: payload.action_portfolio ?? null,
    ...(context.explore_basis ? {capabilities: {explore: {
      availability: object(context.explore_basis).availability, revision: object(context.explore_basis).revision,
      read_action: reads?.explore ?? null,
    }}} : {}),
    evidence: (Array.isArray(context.evidence) ? context.evidence : []).map(value => {
      const row = object(value);
      const observation = coverage.find(item => item.fingerprint === row.coverage_ref);
      return {...compactFields(row, ["evidence_ref", "generated_at", "delivery_outcome", "coverage_ref",
        "occurrences", "first_observed_at", "read_action"]),
        ...(observation ? {result_class: observation.result_class} : {})};
    }),
    evidence_count: context.evidence_count,
    coverage_count: context.coverage_count,
    coverage_truncated: context.coverage_truncated,
    omitted_evidence: context.omitted_evidence ?? null,
    evidence_read_action_template: context.evidence_read_action_template ?? null,
    uncovered_frontier: context.uncovered_frontier,
    // Facts and their provenance are the review input. Do not preselect a
    // diagnostic method, candidate route or judgment from observation counts.
    next: {writeback: replanReadCommands(payload)?.writeback ?? null},
  };
}

function replanActionPacket(payload: JsonObject): JsonObject | null {
  const source = object(payload.replan_action_packet);
  if (Object.keys(source).length === 0) return null;
  const reads = replanReadCommands(payload);
  if (reads) return {...compactFields(source, ["schema_version", "decision", "obligation_id", "required_outcome", "allowed_terminal"]),
    review: reads.review, writeback: reads.writeback,
    guidance: "Read review state and applicable acceptance/evidence before deciding; writeback supplies the format. Reads grant no new authority and require no acknowledgement."};
  const compact = compactFields(source, [
    "schema_version", "decision", "obligation_id", "uncovered_frontier",
    "required_outcome", "allowed_terminal", "bounded_frontier", "settlement_only", "successor_todo_id", "stride_control",
  ]);
  // The typed replan owner supplies bounded instructions; do not truncate their
  // authority/stop qualifiers through generic diagnostic compaction.
  if (Array.isArray(source.planning_guidance)) compact.planning_guidance = source.planning_guidance;
  const writeback = object(source.writeback_contract);
  if (writeback.vision_authoring) compact.writeback_contract = writeback;
  return Object.keys(compact).length > 0 ? compact : null;
}

function userChannel(interaction: JsonObject, payload: JsonObject): JsonObject {
  const source = object(interaction.user_channel);
  const channel: JsonObject = {
    action_required: Boolean(source.action_required ?? payload.action_required),
    open_count: integer(payload.open_count, "payload.open_count"),
    notify: scalarString(source.notify, "interaction.user_channel.notify", "DONT_NOTIFY"),
  };
  const actions = textList(source.actions, 3, 360);
  if (actions.length > 0) channel.actions = actions;
  const reason = text(source.reason, 360);
  if (reason) channel.reason = reason;
  return channel;
}

function responsePlan(interaction: JsonObject): JsonObject | null {
  const source = object(interaction.response_plan);
  if (Object.keys(source).length === 0) return null;
  const plan: JsonObject = {};
  for (const field of ["schema_version", "kind", "decision"]) {
    const rendered = text(source[field], 80);
    if (rendered) plan[field] = rendered;
  }
  const actionSequence = textList(source.action_sequence, 8, 80);
  if (actionSequence.length > 0) plan.action_sequence = actionSequence;
  if (typeof source.silent_wait_allowed === "boolean") {
    plan.silent_wait_allowed = source.silent_wait_allowed;
  }
  return Object.keys(plan).length > 0 ? plan : null;
}

function requiredReads(interaction: JsonObject, payload: JsonObject): JsonObject[] {
  const raw = object(interaction.agent_channel).required_reads
    ?? interaction.required_reads ?? payload.required_reads;
  const result: JsonObject[] = [];
  for (const value of Array.isArray(raw) ? raw : []) {
    const item = object(value);
    // Required reads are executable obligations, not display summaries. Keep
    // every admitted command byte-for-byte, including quoted path whitespace.
    const command = scalarString(item.command, "required read command");
    if (!command) continue;
    const compact: JsonObject = { command };
    // These are existing obligation coordinates, not provider diagnostics.
    // Keep identity and ordering intact, just like the executable command.
    for (const field of ["kind", "reason", "source", "ordering", "hook_id", "capability_id"]) {
      const value = scalarString(item[field], `required read ${field}`);
      if (value) compact[field] = value;
    }
    if (item.source === "turn_start_capability_hook" && item.prompt_budget_bytes !== undefined) {
      compact.prompt_budget_bytes = item.prompt_budget_bytes;
    }
    result.push(compact);
  }
  return result;
}

function boundary(payload: JsonObject): JsonObject {
  const source = object(payload.goal_boundary);
  const result: JsonObject = {
    rule: scalarString(source.rule, "goal_boundary.rule", "stay_in_scope_or_stop"),
  };
  const adapter = object(source.adapter);
  if (Object.keys(adapter).length > 0) {
    result.adapter = Object.fromEntries(
      ["kind", "status"].filter((field) => adapter[field] !== null && adapter[field] !== undefined)
        .map((field) => [field, adapter[field]]),
    );
  }
  // These are resolved restrictions, not display summaries. Preserve literal
  // paths, every clause and explicit empty lists; budget warnings cannot trim
  // an approval requirement or turn a path into a different glob.
  for (const field of ["write_scope", "available_capabilities", "requires_parent_approval", "guards"]) {
    if (Array.isArray(source[field])) result[field] = structuredClone(source[field]);
  }
  // Carry the already resolved approval from quota; dropping it leaves only
  // the bootstrap approval requirement and strands authorized execution.
  const approved = object(source.checkpointed_boundary_authority);
  const approvedScopes = Array.isArray(approved.active_write_scope)
    ? approved.active_write_scope.filter((scope): scope is string =>
      typeof scope === "string" && scope.length > 0)
    : [];
  if (approved.schema_version === "checkpointed_boundary_authority_v0"
      && Number.isInteger(approved.active_count) && Number(approved.active_count) > 0
      && approvedScopes.length > 0) {
    result.checkpointed_boundary_authority = {
      schema_version: approved.schema_version,
      active_count: approved.active_count,
      active_write_scope: approvedScopes,
    };
  }
  // Effective automation is already resolved for this Goal/Agent. Keep the
  // shared participation facts, not private config, diagnostics or recall.
  const memory = object(object(source.capabilities).reward_memory);
  if (memory.enabled === true && memory.configured_for_agent === true
      && memory.experiment_available === true
      && (memory.automatic_recall === true || memory.automatic_ingest === true)) {
    result.capabilities = {reward_memory: {
      automatic_recall: memory.automatic_recall === true,
      automatic_ingest: memory.automatic_ingest === true,
    }};
  }
  if (typeof source.stop_condition === "string") result.stop_condition = source.stop_condition;
  for (const field of ["execution_profile", "orchestration"]) {
    const value = object(source[field]);
    if (Object.keys(value).length > 0) result[field] = value;
  }
  const workspaceGuard = object(payload.workspace_guard);
  if (Object.keys(workspaceGuard).length > 0) result.workspace_guard = workspaceGuard;
  const capabilityGate = object(payload.capability_gate);
  if (Object.keys(capabilityGate).length > 0) {
    result.capability_gate = Object.fromEntries(
      // Current gate facts use required/missing; keep historical field names
      // only when supplied by a stored source. Never rebuild the gate here.
      ["action", "reason", "required", "missing", "required_capabilities", "missing_capabilities", "owner_action"]
        .filter((field) => capabilityGate[field] !== null && capabilityGate[field] !== undefined)
        .map((field) => [field, capabilityGate[field]]),
    );
  }
  return result;
}

function scheduler(payload: JsonObject, turn: ReturnType<typeof interpretQuotaShouldRunPacket>): JsonObject {
  const source = object(payload.scheduler_hint);
  const result: JsonObject = {};
  const action = turn.next_effect.scheduler_action || source.action;
  const cadenceClass = turn.next_effect.cadence_class || source.cadence_class;
  if (action !== null && action !== undefined) result.action = action;
  if (cadenceClass !== null && cadenceClass !== undefined) result.cadence_class = cadenceClass;
  for (const field of ["reason_code", "spend_policy"]) {
    if (source[field] !== null && source[field] !== undefined) result[field] = source[field];
  }
  const appAutomation = object(source.app_automation);
  const legacyCodexApp = object(source.codex_app);
  const sourceApp = Object.keys(appAutomation).length > 0
    ? appAutomation
    : legacyCodexApp;
  if (Object.keys(sourceApp).length === 0) return result;
  const sourceRef = Object.keys(appAutomation).length > 0
    ? "full_decision.scheduler_hint.app_automation"
    : "full_decision.scheduler_hint.codex_app";
  const app: JsonObject = {};
  for (const field of [
    "host_surface", "apply", "host_action", "recommended_rrule",
    "no_spend_for_cadence_change", "execution_interval_policy", "guarantee",
  ]) {
    if (sourceApp[field] !== null && sourceApp[field] !== undefined) app[field] = sourceApp[field];
  }
  const state = object(sourceApp.stateful_backoff);
  if (Object.keys(state).length > 0) {
    const compactState: JsonObject = {};
    for (const field of ["state_key", "current_rrule", "apply_needed", "ack_needed", "state_status"]) {
      if (state[field] !== null && state[field] !== undefined) compactState[field] = state[field];
    }
    const failure = object(state.host_update_failure);
    if (Object.keys(failure).length > 0) {
      compactState.host_update_failure = Object.fromEntries(
        ["target_rrule", "observed_host_rrule", "failure_kind", "failure_count"]
          .filter((field) => failure[field] !== null && failure[field] !== undefined)
          .map((field) => [field, failure[field]]),
      );
    }
    app.stateful_backoff = compactState;
  }
  const ack = object(sourceApp.ack_hint);
  const cliBin = ack.cli_bin ?? object(sourceApp.failure_hint).cli_bin;
  if (typeof cliBin === "string") app.cli_bin = cliBin;
  const cliArgs = executableCliArgs(ack.cli_args);
  if (cliArgs.length > 0) {
    app.ack_cli_args = cliArgs;
  } else if (ack.cli_args) {
    // Resolve omitted executable data from the same captured observation.
    // A fresh quota call can select a different Goal/Agent/Turn or create a receipt.
    app.ack_cli_args_detail_ref = {
      reason: "omitted_to_preserve_executable_argv",
      detail_ref: `${sourceRef}.ack_hint.cli_args`,
    };
  }
  if (object(sourceApp.failure_hint).cli_args) {
    app.failure_cli_args_detail_ref = {
      reason: "cold_path_until_host_update_failure",
      detail_ref: `${sourceRef}.failure_hint.cli_args`,
    };
  }
  if (Object.keys(app).length > 0) {
    if (Object.keys(appAutomation).length > 0) result.app_automation = app;
    // Preserve the historical compact field only when the full source packet
    // also supplied the Codex compatibility projection. A Trae packet never
    // acquires a Codex identity while crossing the Turn boundary.
    if (Object.keys(legacyCodexApp).length > 0) result.codex_app = app;
  }
  return result;
}

function executionPolicy(payload: JsonObject): JsonObject {
  const result: JsonObject = {};
  for (const field of [
    "normal_delivery_allowed", "recovery_delivery_allowed", "self_repair_allowed",
    "capability_repair_allowed", "workspace_repair_allowed", "safe_bypass_allowed",
    "safe_bypass_kind", "blocked_action_scope",
  ]) {
    if (payload[field] !== null && payload[field] !== undefined) result[field] = payload[field];
  }
  return result;
}

function contractCapsule(payload: JsonObject): JsonObject {
  const capsule: JsonObject = {
    schema_version: CONTRACT_CAPSULE_SCHEMA_VERSION,
    source: "full_quota_decision",
  };
  for (const [sourceKey, fields] of Object.entries(CONTRACT_CAPSULE_FIELDS)) {
    const compact = compactFields(
      payload[sourceKey], fields, CONTRACT_CAPSULE_TEXT_LIMITS[sourceKey] || {},
    );
    if (Object.keys(compact).length > 0) capsule[sourceKey] = compact;
  }
  const unavailableContext = projectTurnStartUnavailableContext(payload.turn_start_capability_hook_dispatch);
  if (unavailableContext) capsule.unavailable_context = unavailableContext;
  // Work qualification is behavioral context, including its outcome and
  // degradation rules. Display-field filtering must not weaken that contract.
  const executionProfile = object(payload.execution_profile);
  if (Object.keys(executionProfile).length > 0) {
    capsule.execution_profile = structuredClone(executionProfile);
  }
  // These existing source fields govern evidence and continuation, rather
  // than summarize display text. Preserve every clause, order, empty value
  // and current judge without copying the detailed acceptance-gap ledger.
  const visionAudit = object(payload.vision_continuation_audit);
  const visionContract: JsonObject = {};
  for (const field of [
    "schema_version", "required", "agent_id", "decision", "trigger_count", "trigger_kinds",
    "selected_todo_is_goal_completion", "closeout_allowed_without_evidence",
    "required_before_closeout", "recommended_action", "vision_gap_judge",
    "authoritative_evidence_kinds", "not_satisfied_by",
  ]) {
    if (Object.hasOwn(visionAudit, field)) visionContract[field] = structuredClone(visionAudit[field]);
  }
  if (Object.keys(visionContract).length > 0) capsule.vision_continuation_audit = visionContract;
  // Preserve the bounded source diagnosis; the generic capsule list path is
  // for scalar lists and must not stringify structured component checks.
  const diagnostics = object(payload.vision_continuation_audit).outcome_checkpoint_diagnostics;
  if (Array.isArray(diagnostics) && diagnostics.length > 0) {
    const audit = object(capsule.vision_continuation_audit);
    audit.outcome_checkpoint_diagnostics = diagnostics.slice(0, 5).map((value) => {
      const source = object(value);
      const checks = object(source.component_checks);
      return {
        reason_code: text(source.reason_code, 80),
        resolution_hint: text(source.resolution_hint, 420),
        component_checks: Object.fromEntries([
          "checkpoint_satisfied", "checkpoint_fresh", "path_outcome_valid",
          "evidence_refs_present", "final_outcome_claim_present", "no_reported_outcome_gap",
        ].filter((key) => typeof checks[key] === "boolean").map((key) => [key, checks[key]])),
      };
    });
    capsule.vision_continuation_audit = audit;
  }
  const taskScope = text(payload.task_scope, 80);
  if (taskScope) capsule.task_scope = taskScope;
  const workLane = object(payload.work_lane_contract);
  const outcome = object(workLane.outcome_followthrough);
  if (Object.keys(outcome).length > 0) {
    const compactLane = object(capsule.work_lane_contract);
    compactLane.outcome_followthrough = compactFields(
      outcome,
      ["required", "obligation", "accepted_resolution_kinds", "spend_policy"],
      { spend_policy: 220 },
    );
    capsule.work_lane_contract = compactLane;
  }
  const warnings = ACTIONABLE_WARNING_FIELDS.filter((field) => Boolean(payload[field]));
  if (warnings.length > 0) capsule.actionable_warning_refs = warnings;
  return capsule;
}

function actionProjection(payload: JsonObject): JsonObject {
  const agentIdentity = object(payload.agent_identity);
  const semanticAgentId = scalarString(
    payload.agent_id || agentIdentity.agent_id,
    "quota payload agent_id",
  ).trim() || null;
  const turn = interpretQuotaShouldRunPacket(payload, {
    goal_id: scalarString(payload.goal_id, "quota payload goal_id") || null,
    agent_id: semanticAgentId,
  });
  const interaction = object(payload.interaction_contract);
  const agentChannel = object(interaction.agent_channel);
  const cliChannel = object(interaction.cli_channel);
  const capabilityIntent = payload.effective_action === EffectiveAction.GOVERNED_CAPABILITY_INTENT
    ? projectPendingCapabilityIntent(payload.pending_capability_intent) : null;
  // A governed capability action has already won the live decision. Stale
  // replan/host-reentry projections must not replace its exact command.
  const replanPacket = capabilityIntent ? null : replanActionPacket(payload);
  const replanSettlementOnly = object(payload.replan_action_packet).settlement_only === true;
  const recommendedAction = replanPacket
    ? replanSettlementOnly
      ? scalarString(object(object(payload.replan_action_packet).writeback_contract).rule, "replan settlement rule")
      : replanReadCommands(payload) ? "read replan_action_packet.review, decide from state, then read writeback"
      : "apply replan_action_packet and emit one required semantic outcome"
    : text(turn.observation.recommended_action || payload.recommended_action, 480);
  // The signed host action is executable authority, not a display summary.
  // Budget diagnostics may warn on long commands but must not cut them.
  const primaryAction = scalarString(agentChannel.primary_action, "agent_channel.primary_action").trim();
  const action: JsonObject = {
    recommended_action: recommendedAction,
    primary_action: replanPacket && !replanSettlementOnly
      ? replanReadCommands(payload)?.review ?? "produce one required semantic outcome"
      : primaryAction || null,
    must_attempt: Boolean(agentChannel.must_attempt),
    delivery_allowed: Boolean(agentChannel.delivery_allowed),
    quiet_noop_allowed: Boolean(agentChannel.quiet_noop_allowed),
    selected_todo: selectedTodo(payload, recommendedAction),
  };
  if (capabilityIntent) action.capability_intent = capabilityIntent;
  if (turn.observation.action_portfolio !== null) {
    action.action_portfolio = { ...turn.observation.action_portfolio };
  }
  if (turn.observation.planning_horizon !== null) {
    action.planning_horizon = replanReadCommands(payload)
      ? {context_read: replanReadCommands(payload)!.review}
      : { ...turn.observation.planning_horizon };
  }
  const user = userChannel(interaction, payload);
  const schedulerValue = scheduler(payload, turn);
  let nextCliActions = capabilityIntent
    ? [scalarString(capabilityIntent.command, "pending capability intent command")]
    : [...turn.next_effect.cli_actions];
  if (nextCliActions.length === 0 && Array.isArray(cliChannel.next_cli_actions)) {
    nextCliActions = [...cliChannel.next_cli_actions].map(pythonString);
  }
  const settlementPlan = object(cliChannel.settlement_plan);
  const hasSettlementPlan = Object.keys(settlementPlan).length > 0;
  const recovery = object(payload.unsettled_host_turn_recovery);
  const receiptBoundSettlementOnly = recovery.scope === "current_turn" && recovery.repair === "quota_spend";
  let preserveBoundReplanCommands = replanSettlementOnly;
  if (replanPacket && !replanSettlementOnly) {
    const writebackContract = object(object(payload.replan_action_packet).writeback_contract);
    const successorCommand = writebackContract.successor_command;
    const refreshCommand = nextCliActions.find((item) => item.includes("refresh-state"));
    const closeoutGuard = nextCliActions.find((item) => item.includes("quota should-run"));
    nextCliActions = [];
    if (successorCommand) {
      nextCliActions.push(
        scalarString(successorCommand, "replan_action_packet.writeback_contract.successor_command"),
      );
    }
    if (refreshCommand) nextCliActions.push(refreshCommand);
    else if (closeoutGuard) nextCliActions.push(closeoutGuard);
    preserveBoundReplanCommands = Boolean(closeoutGuard);
  }
  const selectionRequired = cliChannel.selection_required === true;
  const writeback: JsonObject = {
    spend_allowed_now: Boolean(cliChannel.spend_allowed_now),
    spend_after_validation: Boolean(cliChannel.spend_after_validation),
    spend_policy: text(cliChannel.spend_policy, 280),
  };
  if (selectionRequired) {
    const suggested = object(action.action_portfolio).suggested_actions;
    const suggestedItems = Array.isArray(suggested) ? suggested : [];
    writeback.selection_required = true;
    writeback.suggested_todo_ids = suggestedItems.flatMap((item) => {
      const todoId = text(object(item).todo_id, 160);
      return todoId ? [todoId] : [];
    });
    writeback.selection_command_ref =
      "full_decision.interaction_contract.cli_channel.selection_command";
  } else {
    // Original-Turn identities often follow an absolute runtime path. Cutting
    // a closeout command into display text can erase its binding or execute flag.
    writeback.next_cli_actions = hasSettlementPlan
      ? nextCliActions.map(command => scalarString(command, "settlement command"))
      : preserveBoundReplanCommands || receiptBoundSettlementOnly
      ? nextCliActions.slice(0, 5).map(command => scalarString(command, "bound replan closeout command"))
      : textList(nextCliActions, 5, 420);
  }
  // Transport the canonical plan intact. Reconstructing it from command previews
  // loses the effect identity, conditional closeout and host/agent boundary.
  if (hasSettlementPlan) writeback.settlement_plan = settlementPlan;
  for (const field of ["replan_settlement_contract", "delivery_workspace_causality"]) {
    const value = object(cliChannel[field]);
    if (Object.keys(value).length > 0) writeback[field] = value;
  }
  const projection: JsonObject = {
    agent_id: semanticAgentId,
    decision: payload.decision ?? null,
    should_run: Boolean(payload.should_run),
    effective_action: payload.effective_action ?? null,
    state: payload.state ?? null,
    action,
    user,
    required_reads: requiredReads(interaction, payload),
    replan_action_packet: replanPacket,
    boundary: boundary(payload),
    execution_policy: executionPolicy(payload),
    writeback,
    scheduler: schedulerValue,
  };
  projection.contract_capsule = contractCapsule(payload);
  const context = object(interaction.agent_context);
  if (Object.keys(context).length > 0) projection.agent_context = structuredClone(context);
  // Source verification belongs to the selected task above. Preserve one carrier while
  // retaining the existing context elision and historical packet signatures.
  const {selected_todo_text_verified: _taskTextVerified, ...workContext} =
    object(object(interaction.agent_channel).work_context);
  const selectedTodoContextIsAlreadyInAction = workContext.complete === true
    && workContext.selected_todo_ref === "selected_todo"
    && Object.keys(workContext).every(field =>
      ["complete", "selected_todo_ref", "instruction"].includes(field))
    && (typeof object(action.selected_todo).text === "string"
      || object(action.selected_todo).text_ref === "action.recommended_action");
  if (Object.keys(workContext).length > 0 && !selectedTodoContextIsAlreadyInAction) {
    projection.work_context = workContext;
  }
  const orchestration = object(payload.task_orchestration_contract);
  if (Object.keys(orchestration).length > 0) projection.task_orchestration_contract = orchestration;
  const plan = responsePlan(interaction);
  if (plan) projection.response_plan = plan;
  return projection;
}

function compactPeerActivation(contract: JsonObject, detailRef: string): JsonObject {
  if (contract.mode !== "task_scoped_peer"
    || Buffer.byteLength(JSON.stringify(contract), "utf8") <= TURN_ENVELOPE_SECTION_TARGETS.context) return contract;
  const summary: JsonObject = {};
  for (const field of ["schema_version", "mode", "coordinator_agent_id", "assignment_key",
    "execution_scope", "task_selection", "execution_state", "activation_required",
    "activation_allowed", "required_capability", "retry_policy", "terminal_outcome", "writeback_owner"]) {
    if (field in contract) summary[field] = contract[field];
  }
  return { ...summary, delivery: "projected", read_required: true,
    eligible_peer_count: Array.isArray(contract.eligible_peer_lanes) ? contract.eligible_peer_lanes.length : 0,
    blocked_peer_count: Array.isArray(contract.blocked_peer_lanes) ? contract.blocked_peer_lanes.length : 0,
    content_hash: canonicalHash(contract), detail_ref: detailRef,
    instruction: "Read the envelope full_decision detail command before selecting or activating a peer task; counts are not executable lanes.",
  };
}

function turnActionProjection(payload: JsonObject): JsonObject {
  const projection = actionProjection(payload);
  const orchestration = object(projection.task_orchestration_contract);
  if (Object.keys(orchestration).length > 0) {
    const peer = object(orchestration.peer_activation_diagnostic);
    projection.task_orchestration_contract = orchestration.mode === "adaptive" && Object.keys(peer).length > 0
      ? { ...orchestration, peer_activation_diagnostic: compactPeerActivation(peer,
        "full_decision.task_orchestration_contract.peer_activation_diagnostic") }
      : compactPeerActivation(orchestration, "full_decision.task_orchestration_contract");
  }
  const action = object(projection.action);
  const horizon = object(action.planning_horizon);
  if (Object.keys(object(horizon.detail_refs)).length > 0) {
    const compactHorizon = { ...horizon };
    delete compactHorizon.detail_refs;
    compactHorizon.detail_refs_ref = PLANNING_HORIZON_DETAIL_REFS_REF;
    action.planning_horizon = compactHorizon;
    projection.action = action;
  }
  // Goal acceptance already travels in work context. Reuse that exact inline
  // source when present; a different scope/revision or an unavailable source
  // must retain the horizon's complete evidence instead of a dangling ref.
  const currentHorizon = object(action.planning_horizon);
  const core = object(currentHorizon.core_goal);
  // Keep the full bounded work view, reusing only identical text already
  // carried by this signed action. Short text is cheaper to leave inline.
  const selected = object(action.selected_todo);
  const selectedTextRef = typeof selected.text === "string" ? "$.action.selected_todo.text"
    : selected.text_ref === "action.recommended_action" ? "$.action.recommended_action" : null;
  const selectedText = typeof selected.text === "string" ? selected.text : action.recommended_action;
  if (Object.keys(core).length > 0 && selectedTextRef && Array.isArray(currentHorizon.work_items)) {
    action.planning_horizon = {...currentHorizon, work_items: currentHorizon.work_items.map(value => {
      const item = object(value);
      if (item.todo_id !== selected.todo_id || typeof item.text !== "string"
        || item.text !== selectedText || item.text.length <= selectedTextRef.length + 4) return value;
      const {text: _text, ...rest} = item;
      return {...rest, text_ref: selectedTextRef};
    })};
    projection.action = action;
  }
  const acceptance = object(core.acceptance_contract);
  const sources = object(projection.work_context).sources;
  // The ordinary CLI can already reference its identical work-context copy.
  // Relocate only this known carrier into the signed envelope. Never carry a
  // full-decision JSON path into an envelope where that root does not exist.
  if (!("acceptance_contract" in core) && "acceptance_contract_ref" in core) {
    const match = typeof core.acceptance_contract_ref === "string"
      ? /^\$\.interaction_contract\.agent_channel\.work_context\.sources\[(0|[1-9]\d*)\]\.content\.goal_acceptance_contract$/.exec(core.acceptance_contract_ref)
      : null;
    const index = match ? Number(match[1]) : -1;
    const source = object(Array.isArray(sources) ? sources[index] : null);
    const content = object(source.content);
    if (!match || source.source !== "goal_acceptance"
      || (content.status !== undefined && content.status !== "loaded")
      || Object.keys(object(content.goal_acceptance_contract)).length === 0) {
      throw new EffectRuntimeRequestError("Goal acceptance reference must resolve to complete inline work context");
    }
    action.planning_horizon = {...object(action.planning_horizon), core_goal: {
      ...core, acceptance_contract_ref: `$.work_context.sources[${index}].content.goal_acceptance_contract`,
    }};
    projection.action = action;
  }
  if (Object.keys(acceptance).length > 0 && Array.isArray(sources)) {
    const index = sources.findIndex((value) => {
      const source = object(value);
      return source.source === "goal_acceptance"
        && (object(source.content).status === undefined || object(source.content).status === "loaded")
        && canonicalHash(object(object(source.content).goal_acceptance_contract)) === canonicalHash(acceptance);
    });
    if (index >= 0) {
      const compactCore = {...core};
      delete compactCore.acceptance_contract;
      compactCore.acceptance_contract_ref = `$.work_context.sources[${index}].content.goal_acceptance_contract`;
      action.planning_horizon = {...object(action.planning_horizon), core_goal: compactCore};
      projection.action = action;
    }
  }
  // The context owner already bounds these contributions. Replacing them with
  // a reference makes a small contribution require the entire saved decision.
  // Keep the content; the existing envelope budget still reports its cost.
  return projection;
}

function signatureCoverage(envelope: JsonObject, responsePlanValue: unknown): string {
  if (Object.keys(object(object(envelope.writeback).settlement_plan)).length > 0) return ACTION_SIGNATURE_COVERAGE_V5;
  if (Object.keys(object(envelope.agent_context)).length > 0) return ACTION_SIGNATURE_COVERAGE_V4;
  const action = object(envelope.action);
  if (Object.keys(object(action.planning_horizon)).length > 0) return ACTION_SIGNATURE_COVERAGE_V3;
  if (Object.keys(object(action.action_portfolio)).length > 0) return ACTION_SIGNATURE_COVERAGE_V2;
  if (Object.keys(object(responsePlanValue)).length > 0) return ACTION_SIGNATURE_COVERAGE_V1;
  return ACTION_SIGNATURE_COVERAGE_V0;
}

export function turnEnvelopeActionSignatureDocument(value: unknown): JsonObject {
  const envelope = requireJsonObject(value, "turn_envelope");
  assertSupportedQuotaPacket(envelope);
  const responsePlanValue = envelope.response_plan;
  const signature: JsonObject = {
    schema_version: ACTION_SIGNATURE_SCHEMA_VERSION,
    coverage: signatureCoverage(envelope, responsePlanValue),
  };
  for (const field of [
    "agent_id", "decision", "should_run", "effective_action", "state", "action",
    "user", "required_reads", "replan_action_packet", "boundary", "execution_policy",
    "writeback", "scheduler", "contract_capsule", "task_orchestration_contract",
  ]) {
    signature[field] = envelope[field] ?? null;
  }
  if (Object.keys(object(envelope.agent_context)).length > 0) {
    signature.agent_context = object(envelope.agent_context);
  }
  if (Object.keys(object(envelope.work_context)).length > 0) {
    signature.work_context = object(envelope.work_context);
  }
  if (Object.keys(object(responsePlanValue)).length > 0) {
    signature.response_plan = { ...object(responsePlanValue) };
  }
  return signature;
}

export function quotaActionSignatureDocument(value: unknown): JsonObject {
  const payload = requireJsonObject(value, "quota payload");
  return turnEnvelopeActionSignatureDocument(
    turnActionProjection(payload),
  );
}

function shellQuote(value: string): string {
  if (/^[A-Za-z0-9_@%+=:,./-]+$/u.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function commandPrefix(runtimeRoot: unknown, registry?: unknown): string {
  const runtimeRootText = scalarString(runtimeRoot, "quota payload runtime_root").trim();
  const registryText = scalarString(registry, "quota payload registry").trim();
  return "loopx" + (registryText ? ` --registry ${shellQuote(registryText)}` : "") +
    (runtimeRootText ? ` --runtime-root ${shellQuote(runtimeRootText)}` : "");
}

function coldPath(
  payload: JsonObject,
  agentId: string | null,
  schedulerExecutionArgs: string,
  capturedDecisionPath?: string,
): JsonObject {
  const goalId = scalarString(payload.goal_id, "quota payload goal_id", "<goal-id>");
  const agentArg = agentId ? ` --agent-id ${agentId}` : "";
  const prefix = commandPrefix(payload.runtime_root, payload.registry);
  return {
    full_decision: capturedDecisionPath ? `cat -- ${shellQuote(capturedDecisionPath)}` : schedulerExecutionArgs
      ? `${prefix} --format json quota should-run --goal-id ${goalId}${agentArg}${schedulerExecutionArgs}`
      : "rerun the typed quota_guard from the current host packet",
    ...(capturedDecisionPath ? {
      captured_decision: {
        path: capturedDecisionPath,
        goal_id: payload.goal_id ?? null,
        agent_id: agentId,
        turn_instance_id: object(payload.heartbeat_receipt).turn_instance_id ?? null,
        source_hash_ref: "$.action_signature.source_decision_hash",
        instruction: "Read this saved observation for omitted context. Check ok, Goal/Agent/Turn and source hash before use. " +
          "It grants no fresh authority: selection, lease/workspace changes, cancellation or quota revalidation require " +
          "the current host guard and a new capture directory. Missing or invalid capture requires recovery, not blind guard replay.",
      },
    } : {}),
    todo_detail: `${prefix} --format json todo list --goal-id ${goalId}`,
    status_detail: `${prefix} --format json status --goal-id ${goalId}`,
  };
}

export function buildTurnEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "turn envelope request");
  const payload = requireJsonObject(request.payload, "turn envelope payload");
  if (typeof request.scheduler_execution_args !== "string") {
    throw new EffectRuntimeRequestError(
      "turn envelope scheduler_execution_args must be a string",
    );
  }
  const schedulerExecutionArgs = request.scheduler_execution_args;
  const capturedDecisionPath = request.captured_decision_path;
  if (capturedDecisionPath !== undefined && (
    typeof capturedDecisionPath !== "string" || !capturedDecisionPath.trim() || capturedDecisionPath.includes("\0")
  )) {
    throw new EffectRuntimeRequestError("turn envelope captured_decision_path must be a non-empty file path");
  }
  const agentId = scalarString(
    object(payload.agent_identity).agent_id,
    "quota payload agent_identity.agent_id",
  ).trim() || null;
  const actionProjectionValue = turnActionProjection(payload);
  const envelope: JsonObject = {
    ok: Boolean(payload.ok),
    schema_version: TURN_ENVELOPE_SCHEMA_VERSION,
    mode: "should-run",
    view: "turn_envelope",
    goal_id: payload.goal_id ?? null,
    agent_id: agentId,
    reason: text(payload.reason, 360),
    action_required: Boolean(payload.action_required),
    open_count: Number(payload.open_count || 0),
    ...actionProjectionValue,
    detail_ref: coldPath(payload, agentId, schedulerExecutionArgs, capturedDecisionPath),
  };
  const sourceSignature = turnEnvelopeActionSignatureDocument(actionProjectionValue);
  const envelopeSignature = turnEnvelopeActionSignatureDocument(envelope);
  envelope.action_signature = {
    schema_version: ACTION_SIGNATURE_SCHEMA_VERSION,
    coverage: envelopeSignature.coverage,
    source_hash: canonicalHash(sourceSignature),
    envelope_hash: canonicalHash(envelopeSignature),
    matches: JSON.stringify(sourceSignature) === JSON.stringify(envelopeSignature),
    source_decision_hash: canonicalHash(payload),
  };
  measureTurnEnvelope(envelope, payload);
  return envelope;
}

// The native Turn journal owns identity. A saved observation is evidence only;
// it cannot manufacture a quota receipt or authorize a later action.
function bindTurnDecisionCapture(request: JsonObject): JsonObject {
  const payload = requireJsonObject(request.payload, "captured decision");
  assertSupportedQuotaPacket(payload);
  const envelope = requireJsonObject(request.envelope, "captured decision envelope");
  assertSupportedQuotaPacket(envelope);
  const path = scalarString(request.captured_decision_path, "capture path");
  const turnId = scalarString(request.turn_instance_id, "capture Turn identity");
  const turnKey = scalarString(request.turn_key, "capture Turn key");
  const signature = object(envelope.action_signature);
  if (!path.trim() || path.includes("\0") || !turnId.trim() ||
      !ENVELOPED_SHA256_PATTERN.test(turnKey) || payload.ok !== true ||
      payload.goal_id !== envelope.goal_id ||
      object(payload.agent_identity).agent_id !== envelope.agent_id ||
      signature.source_decision_hash !== canonicalHash(payload)) {
    throw new EffectRuntimeRequestError("Turn decision capture does not match the original observation");
  }
  const detail = object(envelope.detail_ref);
  const original = object(detail.captured_decision);
  if (Object.keys(original).length && (original.path !== path ||
      original.turn_instance_id !== turnId || original.turn_key !== turnKey ||
      original.goal_id !== envelope.goal_id || original.agent_id !== envelope.agent_id)) {
    throw new EffectRuntimeRequestError("Turn decision capture identity does not match the journal");
  }
  const capture = object(coldPath(payload, String(envelope.agent_id), "", path).captured_decision);
  const bound = {...envelope, detail_ref: {...detail,
    full_decision: `cat -- ${shellQuote(path)}`,
    captured_decision: {...capture, turn_instance_id: turnId, turn_key: turnKey,
      instruction: "Read this original observation for omitted context. Validate its Goal/Agent and source hash; " +
        "Turn identity comes from this journal-bound reference, not a fabricated quota receipt. " +
        "It grants no fresh authority: later actions require current admission and mutation checks. " +
        "Missing or invalid evidence requires recovery, never blind guard replay.",
    },
  }};
  measureTurnEnvelope(bound, payload);
  return bound;
}

export function evaluateTurnEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "turn envelope request");
  const operation = request.operation;
  if (operation === "replan_view") return replanView(request);
  if (operation === "replan_cli_view") {
    const payload = requireJsonObject(request.payload, "replan CLI payload");
    if (!replanReadCommands(payload)) return payload;
    const obligation = {...object(payload.autonomous_replan_obligation)};
    delete obligation.replan_context;
    return {...payload,
      ...(payload.planning_horizon ? {planning_horizon: {context_read: replanReadCommands(payload)!.review}} : {}),
      autonomous_replan_obligation: {...obligation, context_read: replanReadCommands(payload)!.review},
      replan_action_packet: replanActionPacket(payload)};
  }
  if (operation === "build") return buildTurnEnvelope(request);
  if (operation === "bind_capture") return bindTurnDecisionCapture(request);
  if (operation === "replan_settlement_identity") return {identity: turnEnvelopeReplanSettlementIdentity(
    requireJsonObject(request.envelope, "turn_envelope"), request.turn_instance_id,
  )};
  if (operation === "envelope_signature") {
    return turnEnvelopeActionSignatureDocument(request.envelope);
  }
  if (operation === "quota_signature") {
    return quotaActionSignatureDocument(request.payload);
  }
  throw new EffectRuntimeRequestError("turn envelope operation is unsupported");
}

/** A scoped planning identity comes from the signed quota plan, never a Host
 * session or a missing Todo. Journal validation reuses this exact binding rule.
 */
export function turnEnvelopeReplanSettlementIdentity(envelope: JsonObject, turnInstanceId?: unknown): JsonObject | null {
  assertSupportedQuotaPacket(envelope);
  const plan = object(object(envelope.writeback).settlement_plan);
  const raw = object(plan.identity);
  if (raw.replan_obligation_id == null && raw.binding_kind !== "autonomous_replan") return null;
  const parsed = settlementIdentityFromPlan({settlement_plan: plan});
  if (parsed.failure !== null) throw new EffectRuntimeRequestError(parsed.failure.reason);
  const identity = parsed.value;
  const signature = object(envelope.action_signature);
  if (envelope.schema_version !== TURN_ENVELOPE_SCHEMA_VERSION
      || envelope.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED
      || selectedWorkTodoId({selected_todo: object(envelope.action).selected_todo,
        task_orchestration_contract: envelope.task_orchestration_contract}) !== null
      || identity.binding_kind !== "autonomous_replan"
      || identity.goal_id !== envelope.goal_id || identity.agent_id !== envelope.agent_id
      || turnInstanceId != null && identity.turn_instance_id !== turnInstanceId
      || identity.replan_obligation_id !== object(envelope.replan_action_packet).obligation_id
      || signature.matches !== true || signature.source_hash !== signature.envelope_hash
      || signature.envelope_hash !== canonicalHash(turnEnvelopeActionSignatureDocument(envelope))) {
    throw new EffectRuntimeRequestError("Turn replan settlement identity does not match its signed quota admission");
  }
  return settlementIdentityPayload(identity);
}
