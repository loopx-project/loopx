import { isDeepStrictEqual } from "node:util";
import {
  EXECUTION_PREPARATION_SCHEMA_VERSION, isKnownReadyLeasePreparation,
} from "../work_items/execution_preparation.ts";
import { leaseEpoch } from "../work_items/task_lease_record.ts";
import { capturedContextSelection } from "./context_selection.ts";
import {
  contextReadSectionDigests, inlineNormalWorkOpening, initialContextReadPointers, normalWorkReadContext,
  prepareProgressiveContext, prepareProgressiveContextDelivery, progressiveContextPlan,
  type PreparationScopeDetail, type ReusableExactBody,
} from "./progressive_context.ts";
import type { LoopXTurnRouteValue } from "../turn_driver/turn_contract_generated.ts";
import {turnSessionPlan} from "../turn_driver/session_binding.ts";
import { EffectiveAction, type QuotaEffectiveActionValue } from "./effective_action.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";
import {
  omitUnboundExploreResultWriteback, referenceExploreResultDelivery,
} from "../capabilities/explore_context_delivery.ts";
import { prepareContextDelivery, restoreContextDelivery } from "./context_delivery.ts";
import { updateNativeContextLifecycle } from "./native_context_lifecycle.ts";
import {
  buildSemanticUnitFoldSwap,
  computeQuietTurnStateDigest,
  evaluateEpochPrefetchPromptGate,
  evaluateEpochPrefetchSwap,
  evaluateQuietTurn,
  planStageThenSwapPrefetch,
  recordSemanticUnitAnchor,
} from "./stage_then_swap.ts";
import {
  contextProjectionChanges, contextSourceIdentity, omitEmptyTaskTodoLanes, projectQuotaDiagnostics,
  referenceQuotaSummaryLanes, resolveQuotaSummaryLanes,
} from "./diagnostic_projection.ts";
import { completeCliSettlementChannel } from "./settlement_plan.ts";
import { compactRoutineSettlementPlan, scopeTurnGoalAcceptance } from "./turn_envelope_scoping.ts";
import {
  interpretQuotaShouldRunPacket, assertSupportedQuotaPacket,
  settlementIdentityFromPlan, settlementIdentityPayload, type JsonObject,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { projectEmptyTurnStartObservations, projectTurnStartUnavailableContext } from "../capability_hooks.ts";
import {
  jsonObject, pythonString, requireBoolean, requireJsonObject,
  requireNonEmptyString, requireStringLiteral, sourceNumberTokens,
} from "../runtime_decode.ts";
import { projectPendingCapabilityIntent } from "../work_items/pending_capability_intent.ts";
import { assertActionSelectionConsistency } from "../work_items/action_selection_admission.ts";
import {
  INTERACTION_CONTRACT_SCHEMA_VERSION, assertGoalContextDelivered, capturedTodoTextSha256,
  hasUnboundActionSelection, projectInteractionRequiredReads, selectedWorkTodo,
  selectedWorkTodoId, USER_CHANNEL_NOTIFICATION_POLICIES,
} from "../work_items/interaction_contract.ts";
import { measureTurnEnvelope, TURN_ENVELOPE_SECTION_TARGETS } from "./turn_envelope_budget.ts";
import {
  COMPLETE_TURN_DECISION_VERSION, COMPLETE_TURN_HASH_CODEC, canonicalHash, coldPath,
  compactFields, completeSourceHash, decodeTurnSource, decisionSourceHash,
  executableCliArgs, isNonEmptyFilePath, object, pickNonNull, pickOwn, scalarString,
  shellQuote, text, textList,
} from "./turn_envelope_codec.ts";
import {
  WORK_CONTEXT_GOAL_ACCEPTANCE_REF_PATTERN, WORK_CONTEXT_OUTCOME_REQUIREMENTS_REF_PATTERN,
  type ReplanReadPrecision, bindReplanSuccessorContract, deferredReplanWriteback, inlineOutcomeRequirements,
  nativeReplanPresentation, referenceCurrentTaskTitle, referenceExploreGuidance, referenceGoalAcceptance,
  referenceReplanAuthoring, referenceVisionContinuation, replanActionPacket, replanAuthoringContract,
  replanCliView, replanGoalContext, replanReadCommands, replanReadPrecision, replanView, replanWorkContext,
  requiredReads, requireCompleteDecisionSource, requireCompleteTurnDecision,
  resolveExploreGuidance, responsePlan, selectedTodo, unboundExploreReplan, userChannel,
} from "./turn_envelope_references.ts";

export { TURN_ENVELOPE_BUDGET_BYTES } from "./turn_envelope_budget.ts";
export { COMPLETE_TURN_DECISION_VERSION, decodeTurnSource } from "./turn_envelope_codec.ts";
export { requireCompleteTurnDecision } from "./turn_envelope_references.ts";

export const TURN_ENVELOPE_SCHEMA_VERSION = "loopx_turn_envelope_v0";
export const CONTRACT_CAPSULE_SCHEMA_VERSION = "loopx_contract_capsule_v0";
export const ACTION_SIGNATURE_SCHEMA_VERSION = "loopx_action_signature_v0";
export const ACTION_SIGNATURE_COVERAGE_V0 = "turn_envelope_action_dimensions_v0";
export const ACTION_SIGNATURE_COVERAGE_V1 = "turn_envelope_action_dimensions_v1";
export const ACTION_SIGNATURE_COVERAGE_V2 = "turn_envelope_action_dimensions_v2";
export const ACTION_SIGNATURE_COVERAGE_V3 = "turn_envelope_action_dimensions_v3";
export const ACTION_SIGNATURE_COVERAGE_V4 = "turn_envelope_action_dimensions_v4";
export const ACTION_SIGNATURE_COVERAGE_V5 = "turn_envelope_action_dimensions_v5";
export const ACTION_SIGNATURE_COVERAGE = ACTION_SIGNATURE_COVERAGE_V0;

// Single owner for the should-run artifact path a child brief points at, so the
// legacy field token stays inside this module's retirement budget.
export const QUOTA_GOAL_BOUNDARY_ARTIFACT = "quota_should_run.goal_boundary";
export const SCHEDULER_QUOTA_IDENTITY_KEYS = ["goal_id", "agent_identity.agent_id", "effective_action", "heartbeat_recommendation.recommended_mode", "interaction_contract.mode"];

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
  goal_route_hint: [
    "schema_version", "kind", "route_decision", "preserves_goal_next_action",
    "goal_next_action_mutation",
  ],
  handoff_readiness: [
    "ready", "codex_ready", "handoff_status", "post_handoff_run_seen",
  ],
  capability_monitor_fallback: [
    "schema_version", "capability_gate_action", "blocked_advancement_count",
    "blocked_due_monitor_count", "mode",
  ],
};

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
// The legacy adapter owns its field names. The codec receives this local binding
// from the adapter, never from request data or a second legacy-field reader.
const LEGACY_QUOTA_CONTEXT_FIELDS = Object.keys(monitorHeartbeatDecisionFields({}));

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

function checkpointedBoundaryAuthority(source: JsonObject): JsonObject | null {
  const approved = object(source.checkpointed_boundary_authority);
  const approvedScopes = Array.isArray(approved.active_write_scope)
    ? approved.active_write_scope.filter((scope): scope is string => typeof scope === "string" && scope.length > 0)
    : [];
  if (approved.schema_version !== "checkpointed_boundary_authority_v0" ||
      !Number.isInteger(approved.active_count) || Number(approved.active_count) <= 0 ||
      approvedScopes.length === 0) return null;
  return {schema_version: approved.schema_version, active_count: approved.active_count,
    active_write_scope: approvedScopes};
}

export function projectTurnBoundary(payload: JsonObject): JsonObject {
  const source = object(payload.goal_boundary);
  const result: JsonObject = {
    rule: scalarString(source.rule, "goal_boundary.rule", "stay_in_scope_or_stop"),
  };
  const adapter = object(source.adapter);
  if (Object.keys(adapter).length > 0) result.adapter = pickNonNull(adapter, ["kind", "status"]);
  // These are resolved restrictions, not display summaries. Preserve literal
  // paths, every clause and explicit empty lists; budget warnings cannot trim
  // an approval requirement or turn a path into a different glob.
  for (const field of ["write_scope", "available_capabilities", "requires_parent_approval", "guards"]) {
    if (Array.isArray(source[field])) result[field] = structuredClone(source[field]);
  }
  // Carry the already resolved approval from quota; dropping it leaves only
  // the bootstrap approval requirement and strands authorized execution.
  const approved = checkpointedBoundaryAuthority(source);
  if (approved) result.checkpointed_boundary_authority = approved;
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
    // Current gate facts use required/missing; keep historical field names
    // only when supplied by a stored source. Never rebuild the gate here.
    result.capability_gate = pickNonNull(capabilityGate, [
      "action", "reason", "required", "missing", "required_capabilities", "missing_capabilities", "owner_action",
    ]);
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
  Object.assign(result, pickNonNull(source, ["reason_code", "spend_policy"]));
  const appAutomation = object(source.app_automation);
  const legacyCodexApp = object(source.codex_app);
  const sourceApp = Object.keys(appAutomation).length > 0 ? appAutomation : legacyCodexApp;
  if (Object.keys(sourceApp).length === 0) return result;
  const sourceRef = Object.keys(appAutomation).length > 0
    ? "full_decision.scheduler_hint.app_automation"
    : "full_decision.scheduler_hint.codex_app";
  const app = pickNonNull(sourceApp, [
    "host_surface", "apply", "host_action", "recommended_rrule",
    "no_spend_for_cadence_change", "execution_interval_policy", "guarantee",
  ]);
  const state = object(sourceApp.stateful_backoff);
  if (Object.keys(state).length > 0) {
    const compactState = pickNonNull(state, ["state_key", "current_rrule", "apply_needed", "ack_needed", "state_status"]);
    const failure = object(state.host_update_failure);
    if (Object.keys(failure).length > 0) {
      compactState.host_update_failure = pickNonNull(failure, [
        "target_rrule", "observed_host_rrule", "failure_kind", "failure_count",
      ]);
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
  return pickNonNull(payload, [
    "normal_delivery_allowed", "recovery_delivery_allowed", "self_repair_allowed",
    "capability_repair_allowed", "workspace_repair_allowed", "safe_bypass_allowed",
    "safe_bypass_kind", "blocked_action_scope",
  ]);
}

function contractCapsule(payload: JsonObject): JsonObject {
  const capsule: JsonObject = {
    schema_version: CONTRACT_CAPSULE_SCHEMA_VERSION,
    source: "full_quota_decision",
  };
  for (const [sourceKey, fields] of Object.entries(CONTRACT_CAPSULE_FIELDS)) {
    const compact = compactFields(payload[sourceKey], fields, {});
    if (Object.keys(compact).length > 0) capsule[sourceKey] = compact;
  }
  // These source owners provide execution contracts, not display summaries.
  // Preserve clauses and nested/unknown data without interpreting new fields.
  // The existing signature covers the complete retained JSON; budget overflow
  // remains diagnostic rather than permission to truncate an obligation.
  for (const field of [
    "work_lane_contract", "execution_obligation", "autonomous_replan_scope",
    "agent_scope_frontier", "automation_liveness",
  ]) {
    const contract = object(payload[field]);
    if (Object.keys(contract).length > 0) capsule[field] = structuredClone(contract);
  }
  const unavailableContext = projectTurnStartUnavailableContext(payload.turn_start_capability_hook_dispatch);
  if (unavailableContext) capsule.unavailable_context = unavailableContext;
  // Work qualification is behavioral context, including its outcome and
  // degradation rules. Display-field filtering must not weaken that contract.
  const executionProfile = object(payload.execution_profile);
  if (Object.keys(executionProfile).length > 0) capsule.execution_profile = structuredClone(executionProfile);
  // These existing source fields govern evidence and continuation, rather
  // than summarize display text. Preserve every clause, order, empty value
  // and current judge without copying the detailed acceptance-gap ledger.
  const visionAudit = object(payload.vision_continuation_audit);
  const visionContract = structuredClone(pickOwn(visionAudit, [
    "schema_version", "required", "agent_id", "decision", "trigger_count", "trigger_kinds",
    "selected_todo_is_goal_completion", "closeout_allowed_without_evidence",
    "required_before_closeout", "recommended_action", "vision_gap_judge",
    "authoritative_evidence_kinds", "not_satisfied_by",
  ]));
  if (Object.keys(visionContract).length > 0) capsule.vision_continuation_audit = visionContract;
  // Preserve the bounded source diagnosis; the generic capsule list path is
  // for scalar lists and must not stringify structured component checks.
  const diagnostics = visionAudit.outcome_checkpoint_diagnostics;
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
  const warnings = ACTIONABLE_WARNING_FIELDS.filter((field) => Boolean(payload[field]));
  if (warnings.length > 0) capsule.actionable_warning_refs = warnings;
  return capsule;
}

function turnPrimaryAction(payload: JsonObject, reads: JsonObject | null) {
  const interaction = object(payload.interaction_contract);
  const agentChannel = object(interaction.agent_channel);
  const cliChannel = object(interaction.cli_channel);
  const turnBindingRequired = cliChannel.turn_binding_required === true;
  const capabilityIntent = payload.effective_action === EffectiveAction.GOVERNED_CAPABILITY_INTENT
    ? projectPendingCapabilityIntent(payload.pending_capability_intent) : null;
  // A governed capability action has already won the live decision. Stale
  // replan/host-reentry projections must not replace its exact command.
  const replanPacket = capabilityIntent || turnBindingRequired ? null : replanActionPacket(payload, reads);
  const replanSettlementOnly = object(payload.replan_action_packet).settlement_only === true;
  const primaryAction = scalarString(agentChannel.primary_action, "agent_channel.primary_action").trim();
  return {capabilityIntent, turnBindingRequired, replanPacket, replanSettlementOnly,
    primaryAction: replanPacket && !replanSettlementOnly
      ? reads?.review ?? "produce one required semantic outcome"
      : primaryAction || null};
}

function actionProjection(payload: JsonObject, deferSettlement = true, precision?: ReplanReadPrecision, verifiedSourceHash?: string): JsonObject {
  // Reader identity belongs to the original captured source. Expanding shared
  // diagnostic lanes for the historical executor must not hash that local view.
  const reads = replanReadCommands(payload, payload.replan_read_path, payload.replan_read_argv ?? ["loopx"], verifiedSourceHash);
  if (payload.schema_version === COMPLETE_TURN_DECISION_VERSION) payload = resolveQuotaSummaryLanes(payload);
  const agentIdentity = object(payload.agent_identity);
  const semanticAgentId = scalarString(payload.agent_id || agentIdentity.agent_id, "quota payload agent_id").trim() || null;
  const turn = interpretQuotaShouldRunPacket(payload, {
    goal_id: scalarString(payload.goal_id, "quota payload goal_id") || null,
    agent_id: semanticAgentId,
  });
  const interaction = object(payload.interaction_contract);
  const agentChannel = object(interaction.agent_channel);
  const cliChannel = object(interaction.cli_channel);
  const {capabilityIntent, turnBindingRequired, replanPacket, replanSettlementOnly, primaryAction} =
    turnPrimaryAction(payload, reads);
  const recommendedAction = replanPacket
    ? replanSettlementOnly
      ? scalarString(object(object(payload.replan_action_packet).writeback_contract).rule, "replan settlement rule")
      : reads ? "read replan_action_packet.review, decide from state, then follow review.next"
      : "apply replan_action_packet and emit one required semantic outcome"
    : text(turn.observation.recommended_action || payload.recommended_action, 480);
  // The signed host action is executable authority, not a display summary.
  // Budget diagnostics may warn on long commands but must not cut them.
  const action: JsonObject = {
    recommended_action: recommendedAction,
    primary_action: primaryAction,
    must_attempt: Boolean(agentChannel.must_attempt),
    delivery_allowed: Boolean(agentChannel.delivery_allowed),
    quiet_noop_allowed: Boolean(agentChannel.quiet_noop_allowed),
    selected_todo: selectedTodo(payload, recommendedAction),
  };
  if (capabilityIntent) action.capability_intent = capabilityIntent;
  if (turn.observation.action_portfolio !== null) action.action_portfolio = { ...turn.observation.action_portfolio };
  if (turn.observation.planning_horizon !== null) action.planning_horizon = {...turn.observation.planning_horizon};
  if (reads && deferSettlement) {
    const goal = replanGoalContext(payload, reads, "$.action.core_goal", precision);
    action.core_goal = goal.core;
    if (turn.observation.planning_horizon !== null) action.planning_horizon = goal.horizon;
  }
  const user = userChannel(interaction, payload);
  const schedulerValue = scheduler(payload, turn);
  let nextCliActions = capabilityIntent
    ? [scalarString(capabilityIntent.command, "pending capability intent command")]
    : [...turn.next_effect.cli_actions];
  if (nextCliActions.length === 0 && Array.isArray(cliChannel.next_cli_actions)) {
    nextCliActions = [...cliChannel.next_cli_actions].map(pythonString);
  }
  const settlementPlan = jsonObject(cliChannel.settlement_plan) ?? {};
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
    writeback.selection_command_ref = "full_decision.interaction_contract.cli_channel.selection_command";
  } else if (turnBindingRequired) {
    writeback.turn_binding_required = true;
    writeback.next_cli_actions = cliChannel.next_cli_actions;
  } else {
    // Original-Turn identities often follow an absolute runtime path. Cutting
    // a closeout command into display text can erase its binding or execute flag.
    writeback.next_cli_actions = hasSettlementPlan || !deferSettlement
      ? nextCliActions.map(command => scalarString(command, "writeback command"))
      : preserveBoundReplanCommands || receiptBoundSettlementOnly
      ? nextCliActions.slice(0, 5).map(command => scalarString(command, "bound replan closeout command"))
      : textList(nextCliActions, 5, 420);
  }
  // Transport the canonical plan intact. Reconstructing it from command previews
  // loses the effect identity, conditional closeout and host/agent boundary.
  if (hasSettlementPlan) writeback.settlement_plan = settlementPlan;
  for (const field of ["replan_settlement_contract", "delivery_workspace_causality"]) {
    const value = jsonObject(cliChannel[field]);
    if (value && Object.keys(value).length > 0) writeback[field] = value;
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
    boundary: projectTurnBoundary(payload),
    execution_policy: executionPolicy(payload),
    writeback: reads && deferSettlement ? deferredReplanWriteback(writeback) : writeback,
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
    projection.work_context = replanWorkContext(payload, workContext, reads);
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
  const summary = pickOwn(contract, [
    "schema_version", "mode", "coordinator_agent_id", "assignment_key",
    "execution_scope", "task_selection", "execution_state", "activation_required",
    "activation_allowed", "required_capability", "retry_policy", "terminal_outcome", "writeback_owner",
  ]);
  return { ...summary, delivery: "projected", read_required: true,
    eligible_peer_count: Array.isArray(contract.eligible_peer_lanes) ? contract.eligible_peer_lanes.length : 0,
    blocked_peer_count: Array.isArray(contract.blocked_peer_lanes) ? contract.blocked_peer_lanes.length : 0,
    content_hash: canonicalHash(contract), detail_ref: detailRef,
    instruction: "Read the envelope full_decision detail command before selecting or activating a peer task; counts are not executable lanes.",
  };
}

function turnActionProjection(payload: JsonObject, precision?: ReplanReadPrecision): JsonObject {
  const projection = actionProjection(payload, true, precision);
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
      ? WORK_CONTEXT_GOAL_ACCEPTANCE_REF_PATTERN.exec(core.acceptance_contract_ref) : null;
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
  if (Object.keys(acceptance).length > 0) {
    if ("outcome_requirements" in acceptance && "outcome_requirements_ref" in acceptance) {
      throw new EffectRuntimeRequestError("Outcome requirements reference conflicts with inline outcome_requirements");
    }
    if ("outcome_requirements_ref" in acceptance && !("outcome_requirements" in acceptance)) {
      const match = typeof acceptance.outcome_requirements_ref === "string"
        ? WORK_CONTEXT_OUTCOME_REQUIREMENTS_REF_PATTERN.exec(acceptance.outcome_requirements_ref) : null;
      const index = match ? Number(match[1]) : -1;
      const source = object(Array.isArray(sources) ? sources[index] : null);
      const content = object(source.content);
      const sourceContract = object(content.goal_acceptance_contract);
      if (match && source.source === "goal_acceptance"
        && (content.status === undefined || content.status === "loaded")
        && Object.keys(object(sourceContract.outcome_requirements)).length > 0
        && !("outcome_requirements_ref" in sourceContract)) {
        action.planning_horizon = {...object(action.planning_horizon), core_goal: {...core, acceptance_contract: {
          ...acceptance,
          outcome_requirements_ref: `$.work_context.sources[${index}].content.goal_acceptance_contract.outcome_requirements`,
        }}};
        projection.action = action;
        return projection;
      }
      const inlinedAcceptance = {...acceptance};
      delete inlinedAcceptance.outcome_requirements_ref;
      inlinedAcceptance.outcome_requirements = inlineOutcomeRequirements(payload, acceptance.outcome_requirements_ref);
      action.planning_horizon = {...object(action.planning_horizon), core_goal: {
        ...core, acceptance_contract: inlinedAcceptance,
      }};
      projection.action = action;
      return projection;
    }
    if (Array.isArray(sources)) {
      // The parsed values can coincide after numeric rounding. Compare the
      // original carrier identities before replacing either signed field.
      const identity = precision?.identity ?? contextSourceIdentity(payload);
      const horizonPath = ["planning_horizon", "core_goal", "acceptance_contract"];
      const contractPath = (index: number) => [
        "interaction_contract", "agent_channel", "work_context", "sources", index,
        "content", "goal_acceptance_contract",
      ];
      const horizonIdentity = identity(acceptance, horizonPath);
      const index = sources.findIndex((value, index) => {
        const source = object(value);
        const content = object(source.content);
        return source.source === "goal_acceptance"
          && (content.status === undefined || content.status === "loaded")
          && identity(object(content.goal_acceptance_contract), contractPath(index)) === horizonIdentity;
      });
      if (index >= 0) {
        const compactCore = {...core};
        delete compactCore.acceptance_contract;
        compactCore.acceptance_contract_ref = `$.work_context.sources[${index}].content.goal_acceptance_contract`;
        action.planning_horizon = {...object(action.planning_horizon), core_goal: compactCore};
        projection.action = action;
        return projection;
      }
      const requirements = object(acceptance.outcome_requirements);
      if (Object.keys(requirements).length > 0 && !("outcome_requirements_ref" in acceptance)) {
        const requirementsIdentity = identity(requirements, [...horizonPath, "outcome_requirements"]);
        const requirementsBytes = Buffer.byteLength(JSON.stringify(requirements), "utf8");
        const reqIndex = sources.findIndex((value, index) => {
          const source = object(value);
          const content = object(source.content);
          const contract = object(content.goal_acceptance_contract);
          const ref = `$.work_context.sources[${index}].content.goal_acceptance_contract.outcome_requirements`;
          return source.source === "goal_acceptance"
            && (content.status === undefined || content.status === "loaded")
            && !("outcome_requirements_ref" in contract)
            && Object.keys(object(contract.outcome_requirements)).length > 0
            && requirementsBytes > ref.length + 24
            && identity(object(contract.outcome_requirements),
              [...contractPath(index), "outcome_requirements"]) === requirementsIdentity;
        });
        if (reqIndex >= 0) {
          const compactAcceptance = {...acceptance};
          delete compactAcceptance.outcome_requirements;
          compactAcceptance.outcome_requirements_ref =
            `$.work_context.sources[${reqIndex}].content.goal_acceptance_contract.outcome_requirements`;
          action.planning_horizon = {...object(action.planning_horizon), core_goal: {
            ...core, acceptance_contract: compactAcceptance,
          }};
          projection.action = action;
        }
      }
    }
  }
  // The context owner already bounds these contributions. Replacing them with
  // a reference makes a small contribution require the entire saved decision.
  // Keep the content; the existing envelope budget still reports its cost.
  return projection;
}

function signatureCoverage(envelope: JsonObject, responsePlanValue: unknown): string {
  if (Object.keys(object(object(envelope.writeback).settlement_plan)).length > 0
    || Object.keys(object(object(envelope.writeback).settlement_identity)).length > 0) return ACTION_SIGNATURE_COVERAGE_V5;
  if (Object.keys(object(envelope.agent_context)).length > 0) return ACTION_SIGNATURE_COVERAGE_V4;
  const action = object(envelope.action);
  if (Object.keys(object(action.planning_horizon)).length > 0) return ACTION_SIGNATURE_COVERAGE_V3;
  if (Object.keys(object(action.action_portfolio)).length > 0) return ACTION_SIGNATURE_COVERAGE_V2;
  if (Object.keys(object(responsePlanValue)).length > 0) return ACTION_SIGNATURE_COVERAGE_V1;
  return ACTION_SIGNATURE_COVERAGE_V0;
}

/** Live consumers must understand the declared wire versions before dispatch.
 * Historical v0 callers may omit signature metadata; digest verification stays
 * with its existing owner. Explicit unknown/null declarations are never v0.
 * This does not make the bounded envelope a complete future work contract.
 */
export function assertTurnEnvelopeConsumerVersion(envelope: JsonObject, allowUnversionedHistory = false): void {
  assertSupportedQuotaPacket(envelope);
  if (envelope.schema_version !== TURN_ENVELOPE_SCHEMA_VERSION &&
      !(allowUnversionedHistory && !Object.hasOwn(envelope, "schema_version"))) {
    throw new EffectRuntimeRequestError("unsupported TurnEnvelope schema_version; use a compatible consumer");
  }
  const signature = object(envelope.action_signature);
  if (Object.hasOwn(signature, "schema_version") && signature.schema_version !== ACTION_SIGNATURE_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError("unsupported TurnEnvelope action signature schema_version; use a compatible consumer");
  }
  if (Object.hasOwn(signature, "coverage") && (typeof signature.coverage !== "string" || ![
    ACTION_SIGNATURE_COVERAGE_V0, ACTION_SIGNATURE_COVERAGE_V1, ACTION_SIGNATURE_COVERAGE_V2,
    ACTION_SIGNATURE_COVERAGE_V3, ACTION_SIGNATURE_COVERAGE_V4, ACTION_SIGNATURE_COVERAGE_V5,
  ].includes(signature.coverage))) {
    throw new EffectRuntimeRequestError("unsupported TurnEnvelope action signature coverage; use a compatible consumer");
  }
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
  if (Object.keys(object(envelope.agent_context)).length > 0) signature.agent_context = object(envelope.agent_context);
  if (Object.keys(object(envelope.work_context)).length > 0) signature.work_context = object(envelope.work_context);
  if (Object.keys(object(responsePlanValue)).length > 0) signature.response_plan = { ...object(responsePlanValue) };
  return signature;
}

export function quotaActionSignatureDocument(value: unknown, supplied?: unknown): JsonObject {
  const payload = requireJsonObject(value, "quota payload");
  return turnEnvelopeActionSignatureDocument(
    turnActionProjection(payload, replanReadPrecision(payload, supplied)),
  );
}

/** Complete decision composition precedes capture. Domain owners have already
 * decided admission, work and settlement; this boundary does not decide again.
 * The v0 envelope remains an internal executor adapter during reader migration.
 */
function requireCompleteHostHeader(request: JsonObject, decision: JsonObject): JsonObject {
  // The versioned model wire has one complete source. The outer executor has
  // already checked its private effect adapter; a standalone reader validates
  // the source and original capture header without recreating that adapter.
  const header = requireJsonObject(request.complete_host_request, "complete Host request");
  if (header.schema_version !== "loopx_turn_host_request_v1" ||
      Object.keys(object(request.envelope)).length !== 0 ||
      typeof header.turn_key !== "string" || !ENVELOPED_SHA256_PATTERN.test(header.turn_key)) {
    throw new EffectRuntimeRequestError("unsupported or invalid complete Host request");
  }
  if (Object.hasOwn(header, "source_decision_hash") && header.source_decision_hash !== decisionSourceHash(decision)) {
    throw new EffectRuntimeRequestError("complete Host request differs from captured source");
  }
  const detail = Object.hasOwn(header, "detail_ref") ? requireJsonObject(header.detail_ref, "complete Host detail reference") : {};
  const hasCapture = Object.hasOwn(detail, "captured_decision");
  const captured = hasCapture ? requireJsonObject(detail.captured_decision, "complete Host captured decision") : {};
  // A direct quota capture predates the Host journal and has no Turn key.
  // Preserve its source observation rather than inventing a journal binding.
  // A provided journal key keeps the existing exact-key rule.
  const captureTurnMatches = Object.hasOwn(captured, "turn_key")
    ? captured.turn_key === header.turn_key
    : captured.turn_instance_id === (object(decision.heartbeat_receipt).turn_instance_id ?? null);
  if (hasCapture && (!isNonEmptyFilePath(captured.path) || !captureTurnMatches ||
      captured.goal_id !== decision.goal_id || captured.agent_id !== object(decision.agent_identity).agent_id ||
      captured.source_hash_ref !== "$.source_decision_hash" || !Object.hasOwn(header, "source_decision_hash"))) {
    throw new EffectRuntimeRequestError("complete Host request capture binding differs");
  }
  return header;
}

function requireCompleteHostDecision(request: JsonObject): JsonObject {
  const decision = requireCompleteTurnDecision(request.payload, request.source_number_tokens);
  requireCompleteHostHeader(request, decision);
  return decision;
}

/** Check a source for consumers that still use the executor projection. An
 * explicit historical adapter cannot redirect the admitted work or identity. */
export function completeHostSource(request: JsonObject): {decision: JsonObject; projection: JsonObject} {
  if (Object.hasOwn(request, "complete_host_request")) {
    const decision = requireCompleteHostDecision(request);
    return {decision, projection: turnActionProjection(decision)};
  }
  const decision = requireCompleteTurnDecision(request.payload, request.source_number_tokens);
  const envelope = requireJsonObject(request.envelope, "complete Turn executor envelope");
  assertTurnEnvelopeConsumerVersion(envelope);
  const signature = object(envelope.action_signature);
  if (signature.source_decision_hash !== decisionSourceHash(decision)
      || signature.envelope_hash !== canonicalHash(turnEnvelopeActionSignatureDocument(envelope))
      || signature.source_hash !== signature.envelope_hash || signature.matches !== true) {
    throw new EffectRuntimeRequestError("complete Turn model input differs from captured executor source");
  }
  if (decision.goal_id !== envelope.goal_id
      || object(decision.agent_identity).agent_id !== envelope.agent_id
      || selectedWorkTodoId(decision) !== selectedWorkTodoId({
        selected_todo: object(envelope.action).selected_todo,
        task_orchestration_contract: envelope.task_orchestration_contract,
      })) {
    throw new EffectRuntimeRequestError("complete Turn session lineage differs from executor source");
  }
  // An adapter's self-consistent hash does not prove that it carries the
  // admitted decision. Reuse the producer's projection, including writeback
  // and execution conditions, rather than maintaining a second field list.
  const projection = turnActionProjection(decision);
  if (signature.source_hash !== canonicalHash(turnEnvelopeActionSignatureDocument(projection))) {
    throw new EffectRuntimeRequestError("complete Turn executor projection differs from admitted source");
  }
  return {decision, projection};
}

/** Collapse the unconfigured recall readback. Known active, failed and extended
 * records still carry their full context. This only classifies presentation. */
function disabledRewardMemoryView(value: unknown): JsonObject | null {
  const readback = jsonObject(value);
  const experiment = jsonObject(readback?.experiment);
  const route = jsonObject(experiment?.config_runtime_route);
  if (!readback || readback.status !== "disabled" || !experiment || !route ||
      typeof readback.goal_id !== "string" || typeof readback.agent_id !== "string" ||
      typeof route.registry_role !== "string" || typeof route.runtime_scope !== "string" ||
      !Array.isArray(experiment.host_coverage)) return null;
  const coverage = experiment.host_coverage;
  if (!coverage.every(value => {
    const host = jsonObject(value);
    return host && Object.keys(host).length === 3 && typeof host.host_id === "string" &&
      typeof host.automatic_recall === "string" && typeof host.automatic_ingest === "string";
  })) return null;
  const {goal_id, agent_id, status} = readback;
  const expected = {
    schema_version: "agent_turn_recall_v0", ok: true, status, goal_id, agent_id,
    surface_id: "agent_workflow.turn_admission", host_wiring: "agent_turn_admission",
    reason_code: null, context: null, automatic_recall: false, provider_call_count: 0,
    fail_open: true, provider_failure_is_user_gate: false, grants_new_action_authority: false,
    quota_spend_performed: false, external_writes_performed: false, suppress_external_sinks: true,
    experiment: {
      schema_version: "reward_memory_experiment_status_v1", ok: true, status, goal_id, agent_id,
      experimental: false, enabled: false, available: false, configured_for_agent: false,
      config_pointer_registered: false, automatic_ingest: false, automatic_recall: false,
      fail_open: true, external_writes_performed: false, host_coverage: coverage,
      config_runtime_route: {
        schema_version: "reward_memory_config_runtime_route_v0", registry_source: "invoked_registry",
        registry_role: route.registry_role, runtime_scope: route.runtime_scope, goal_source: "registry.goals",
        config_source: "goal_repo_relative_config_pointer", readback_status: "not_attempted",
        exact_readback_verified: false,
      },
    },
  };
  return isDeepStrictEqual(value, expected) ? {status, goal_id, agent_id} : null;
}

/** Task presentation is derived only after full-source validation. Keep task
 * facts (including unknown additions) and original scalar carriers intact;
 * scheduler/effect instructions remain with the outer executor. This view is
 * not a replacement source or a permission/settlement contract. */
const STANDARD_ONE_STEP_SPEND_POLICY =
  "append exactly one heartbeat spend only after scope-bounded work " +
  "is validated and written back; one_step: mode, not an operation limit";

function usesStandardOneStepWorkSegment(decision: JsonObject): boolean {
  const heartbeat = object(decision.heartbeat_recommendation);
  return decision.normal_delivery_allowed === true &&
    heartbeat.recommended_mode === "steering_audit_then_one_step" &&
    heartbeat.spend_policy === STANDARD_ONE_STEP_SPEND_POLICY;
}

function hostModelContextChanges(decision: JsonObject): JsonObject {
  const context = {...decision};
  // Maintenance warnings are actionable in the operator's quota/status view.
  for (const field of ["schema_version", "contract_binding", "projection_envelope",
    "scheduler_hint", "automation_cadence_readback", "automation_liveness",
    "scheduler_execution_context", "promotion_readiness_warning", "completed_todo_archive_warning"]) {
    delete context[field];
  }
  // Todo-authoring directions belong to the outer executor. Preserve other
  // fields because task context may depend on future additions.
  const writeHint = object(decision.todo_write_hint);
  const hintCount = Object.keys(writeHint).length;
  for (const key of ["rule", "section", "agent_todo_command_template",
    "user_action_command_template", "user_gate_command_template"]) {
    if (typeof writeHint[key] === "string") delete writeHint[key];
  }
  const retainedHints = Object.keys(writeHint).length;
  if (retainedHints !== hintCount) {
    if (retainedHints) context.todo_write_hint = writeHint;
    else delete context.todo_write_hint;
  }
  // Only the standard legacy rule has a known split: accounting stays with
  // settlement, and the built-in renderers explain the work segment. Keep
  // unfamiliar text intact; it can carry additional execution conditions.
  if (usesStandardOneStepWorkSegment(decision)) {
    const heartbeat = object(decision.heartbeat_recommendation);
    delete heartbeat.spend_policy;
    context.heartbeat_recommendation = heartbeat;
  }
  const emptyObservations = projectEmptyTurnStartObservations(decision.turn_start_capability_hook_dispatch);
  if (emptyObservations) context.turn_start_capability_hook_dispatch = emptyObservations;
  const disabledRecall = disabledRewardMemoryView(decision.reward_memory_recall);
  if (disabledRecall) context.reward_memory_recall = disabledRecall;
  const interaction = {...object(decision.interaction_contract)};
  const agent = object(interaction.agent_channel);
  // Start and settlement use the original preparation. Keep unfamiliar
  // records and disagreements visible even when the rest of the task is ready.
  const preparation = agent.execution_preparation;
  if (decision.ok === true && decision.should_run === true &&
      decision.normal_delivery_allowed === true && decision.effective_action === EffectiveAction.NORMAL_RUN &&
      agent.must_attempt === true && agent.delivery_allowed === true && object(agent.work_context).complete === true &&
      object(interaction.user_channel).action_required === false && decision.replan_action_packet == null &&
      object(decision.goal_boundary).handoff_mode === "hard_lease" &&
      isKnownReadyLeasePreparation(preparation, object(decision.agent_identity).agent_id, selectedWorkTodoId(decision)) &&
      isDeepStrictEqual(decision.execution_preparation, preparation) &&
      isDeepStrictEqual(object(interaction.cli_channel).execution_preparation, preparation)) {
    delete context.execution_preparation;
    delete agent.execution_preparation;
  }
  // The complete source may share task authoring/evidence with its effect
  // owner. Resolve those existing references before omitting that owner.
  if (Object.hasOwn(agent, "work_context")) interaction.agent_channel = {...agent,
    work_context: resolveExploreGuidance(decision, object(agent.work_context))};
  const packet = object(decision.replan_action_packet);
  if (Object.hasOwn(object(packet.writeback_contract), "vision_authoring_ref"))
    context.replan_action_packet = {...packet, writeback_contract: replanAuthoringContract(decision)};
  delete interaction.cli_channel;
  context.interaction_contract = interaction;
  // The work is already selected. Keep every candidate and unknown fact,
  // while omitting the empty diagnostic lanes of known Todo summaries.
  const ordinary = decision.effective_action === EffectiveAction.NORMAL_RUN && decision.normal_delivery_allowed === true;
  return contextProjectionChanges(decision, ordinary ? omitEmptyTaskTodoLanes(context) : context);
}

/** A current private plan stores its one admitted source and transport header.
 * The old plan codec is read only for recorded journals or explicit old input. */
function checkedTurnPlanSource(plan: JsonObject, numberTokens?: unknown): {
  work: JsonObject; decision: JsonObject | null;
} {
  if (plan.schema_version === "loopx_turn_plan_v1") {
    if (!Object.hasOwn(plan, "work_contract") || Object.hasOwn(plan, "turn_envelope")
        || !Object.hasOwn(plan, "source_decision_hash")) {
      throw new EffectRuntimeRequestError("complete Turn plan requires one source without an executor adapter");
    }
    const checked = completeHostSource({payload: plan.work_contract, source_number_tokens: numberTokens,
      complete_host_request: {schema_version: "loopx_turn_host_request_v1",
        turn_key: object(plan.transaction).turn_key, source_decision_hash: plan.source_decision_hash,
        ...(Object.hasOwn(plan, "detail_ref") ? {detail_ref: plan.detail_ref} : {})}});
    const work = {goal_id: checked.decision.goal_id ?? null, ...checked.projection};
    return {work, decision: checked.decision};
  }
  if (Object.hasOwn(plan, "schema_version") && plan.schema_version !== "loopx_turn_plan_v0") {
    throw new EffectRuntimeRequestError("unsupported Turn plan version");
  }
  const envelope = object(plan.turn_envelope);
  if (Object.hasOwn(plan, "work_contract")) {
    const checked = completeHostSource({payload: plan.work_contract, envelope,
      source_number_tokens: numberTokens});
    return {work: {...envelope, ...checked.projection}, decision: checked.decision};
  }
  // Older journals can omit the envelope header. A declared version or
  // signature format must pass the same compatibility check as Host input.
  assertTurnEnvelopeConsumerVersion(envelope, true);
  return {work: envelope, decision: null};
}

export function turnPlanWorkSource(plan: JsonObject, numberTokens?: unknown): {
  work: JsonObject; decision: JsonObject | null; scopedIdentity: JsonObject | null | undefined;
} {
  const source = checkedTurnPlanSource(plan, numberTokens);
  const scopedIdentity = plan.schema_version === "loopx_turn_plan_v1"
    ? scopedTurnSettlementIdentity(source.work, object(plan.transaction).turn_instance_id)
    : undefined;
  return {...source, scopedIdentity};
}

function admittedDriverLineage(work: JsonObject, decision: JsonObject | null,
  complete: boolean, historical?: JsonObject): JsonObject {
  const selected = selectedWorkTodo({selected_todo: object(work.action).selected_todo,
    task_orchestration_contract: work.task_orchestration_contract},
    historical && Object.hasOwn(historical, "primary_todo_id")
      ? scalarString(historical.primary_todo_id, "historical primary Todo") : undefined);
  const signature = turnEnvelopeActionSignatureDocument(work);
  signature.action = {...object(signature.action), selected_todo: selected};
  return {
    ...(decision ? {goal_id: decision.goal_id ?? null,
      agent_id: object(decision.agent_identity).agent_id ?? null,
      todo_id: selectedWorkTodoId(decision) ?? ""} : {}),
    route: complete ? admittedTurnRoute(work, () => scopedTurnSettlementIdentity(work))
      : turnEnvelopeRoute(work, historical),
    action_signature: signature,
    ...(complete ? {scoped_settlement_identity: scopedTurnSettlementIdentity(work)} : {}),
  };
}

export function completePlanningFacts(request: JsonObject): JsonObject {
  // An explicitly supplied old carrier retains its consistency proof. New
  // producers start from the complete source and never construct that view.
  const checked = Object.hasOwn(request, "envelope") ? completeHostSource(request) : null;
  const decision = checked?.decision ?? requireCompleteTurnDecision(request.payload, request.source_number_tokens);
  const work = {goal_id: decision.goal_id ?? null, ...(checked?.projection ?? turnActionProjection(decision))};
  const lineage = admittedDriverLineage(work, decision, true);
  let scopedIdentity: JsonObject | null = null, identityError: string | null = null;
  if (lineage.route === "replan_required" || lineage.route === "ready_for_host" && !lineage.todo_id) {
    try { scopedIdentity = scopedTurnSettlementIdentity(work, request.turn_instance_id); }
    catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      identityError = error.message;
    }
  }
  return {...lineage, work, source_decision_hash: decisionSourceHash(decision),
    action_source_hash: canonicalHash(turnEnvelopeActionSignatureDocument(work)),
    scoped_settlement_identity: scopedIdentity, scoped_identity_error: identityError,
    ...(request.include_selected_work_basis === true ? {selected_work_basis: selectedWorkLaunchBasis(decision)} : {}),
    ...(request.inspect_plan === true &&
      (lineage.route === "ready_for_host" || lineage.route === "replan_required")
      ? {inspection_paths: turnPlanInspectionPaths(decision, lineage.route)} : {})};
}

function completeHostDecision(request: JsonObject): JsonObject {
  if (!Object.hasOwn(request, "complete_host_request")) return completeHostSource(request).decision;
  const decision = requireCompleteHostDecision(request);
  // Keep the selection refusal previously reached through the quota interpreter.
  assertActionSelectionConsistency(decision, object(decision.interaction_contract));
  return decision;
}

function checkedContextPresentationSource(request: JsonObject): JsonObject {
  const context = requireJsonObject(request.context, "current projection");
  if (request.surface === "complete_turn") {
    const numbers = sourceNumberTokens(request, request.source_number_tokens)
      .filter(({path}) => path[0] === "context").map(({path, token}) => [path.slice(1), token]);
    requireCompleteTurnDecision(context, numbers);
  }
  return context;
}

function legacyRecommendationFallbackReferences(payload: JsonObject): JsonObject[] {
  const rootFallback = jsonObject(payload.blocked_priority_fallback);
  const recFallback = jsonObject(object(payload.heartbeat_recommendation).blocked_priority_fallback);
  if (!rootFallback || !recFallback || !isDeepStrictEqual(rootFallback, recFallback)) return [];
  return [{
    path: ["heartbeat_recommendation", "blocked_priority_fallback"],
    from: ["blocked_priority_fallback"],
    value: {context_ref: "/blocked_priority_fallback"},
  }];
}

function presentationDetailFlags(request: JsonObject): {
  includeGoalAcceptanceDetail: boolean;
  includeAgentTodosDetail: boolean;
} {
  return {
    includeGoalAcceptanceDetail: request.include_goal_acceptance_detail === undefined ? false
      : requireBoolean(request.include_goal_acceptance_detail, "include_goal_acceptance_detail"),
    includeAgentTodosDetail: request.include_agent_todos_detail === undefined ? false
      : requireBoolean(request.include_agent_todos_detail, "include_agent_todos_detail"),
  };
}

function buildContextPresentationPlan(
  payload: JsonObject,
  surface: unknown,
  flags: {includeGoalAcceptanceDetail: boolean; includeAgentTodosDetail: boolean},
): JsonObject {
  return progressiveContextPlan(
    {
      payload,
      surface,
      ...(flags.includeGoalAcceptanceDetail ? {include_goal_acceptance_detail: true} : {}),
      ...(flags.includeAgentTodosDetail ? {include_agent_todos_detail: true} : {}),
    },
    progressivePreparationScopes(payload),
    flags.includeAgentTodosDetail ? [] : legacyRecommendationFallbackReferences(payload),
  );
}

/** The unsigned root is used for path selection; edits and recovery still
 * address the unchanged, signed original capture. */
function checkedContextPresentationPlan(request: JsonObject): JsonObject {
  const flags = presentationDetailFlags(request);
  const context = checkedContextPresentationSource(request);
  if (request.surface !== "complete_turn")
    return nativeReplanPresentation(request) ?? contextPresentationPlan(request, flags);
  const source = {...context};
  delete source.schema_version;
  delete source.contract_binding;
  return nativeReplanPresentation(request) ?? buildContextPresentationPlan(source, "quota_packet", flags);
}

function completeWorkIdentity(decision: JsonObject): JsonObject {
  return {goal_id: decision.goal_id ?? null,
    agent_id: object(decision.agent_identity).agent_id ?? null,
    todo_id: selectedWorkTodoId(decision) ?? ""};
}

function completeSessionLineage(decision: JsonObject): JsonObject {
  const identity = completeWorkIdentity(decision);
  return {goal_id: scalarString(identity.goal_id, "Turn Goal identity").trim(),
    agent_id: scalarString(identity.agent_id, "Turn Agent identity").trim(),
    todo_id: scalarString(identity.todo_id, "Turn Todo identity").trim()};
}

function completeTaskTextSha256(decision: JsonObject, todoId: unknown): string | null {
  const context = object(object(object(decision.interaction_contract).agent_channel).work_context);
  const declared = {...object(decision.selected_todo),
    ...(Object.hasOwn(context, "selected_todo_text_verified")
      ? {text_source_verified: context.selected_todo_text_verified} : {})};
  return capturedTodoTextSha256(declared, context, decision.recommended_action, todoId);
}

/** Selected Todo write scopes can live in work_context.sources when page-0 selected_todo omits them. */
function selectedTodoRequiredWriteScopes(decision: JsonObject, todoId: string): unknown {
  const context = object(object(object(decision.interaction_contract).agent_channel).work_context);
  const records = (Array.isArray(context.sources) ? context.sources : [])
    .map(value => jsonObject(value))
    .filter(source => source?.source === "selected_todo")
    .map(source => jsonObject(jsonObject(source!.content)?.todo))
    .filter((record): record is JsonObject =>
      record !== null && (record.todo_id === undefined || record.todo_id === todoId));
  for (const record of records) {
    if (Object.hasOwn(record, "required_write_scopes")) {
      return record.required_write_scopes ?? [];
    }
  }
  const selected = object(decision.selected_todo);
  return selected.required_write_scopes ?? [];
}

/** Launch rechecks the admitted task, not the provider revision or the bounded
 * action signature. Progress, diagnostics and receipt changes are not edits to
 * the work. Losing the verified body on a fresh read is not a match. */
function selectedWorkLaunchBasis(decision: JsonObject, verifiedTextSha256?: string | null): string | null {
  const todoId = selectedWorkTodoId(decision);
  const selected = object(decision.selected_todo);
  if (!todoId || todoId !== selected.todo_id) return null;
  const textSha256 = verifiedTextSha256 === undefined ? completeTaskTextSha256(decision, todoId) : verifiedTextSha256;
  return textSha256 === null ? null : canonicalHash({todo_id: todoId, text_sha256: textSha256,
    required_write_scopes: selectedTodoRequiredWriteScopes(decision, todoId),
    required_capabilities: selected.required_capabilities ?? []});
}

function sealCompleteTurnDecision(
  source: JsonObject,
  schedulerExecutionArgs: string,
  identity: ReturnType<typeof contextSourceIdentity>,
): JsonObject {
  requireCompleteDecisionSource(source);
  const result: JsonObject = {...source, schema_version: COMPLETE_TURN_DECISION_VERSION,
    contract_binding: {scheduler_execution_args: schedulerExecutionArgs,
      hash_codec: COMPLETE_TURN_HASH_CODEC,
      source_decision_hash: completeSourceHash(source, identity)}};
  result.contract_binding = {...object(result.contract_binding), content_hash: completeSourceHash(result, identity)};
  return result;
}

export function composeCompleteTurnDecision(value: unknown): JsonObject {
  const request = requireJsonObject(value, "complete Turn decision request");
  const payload = requireJsonObject(request.payload, "complete Turn decision source");
  const composedSettlement = request.composed_settlement === undefined ? false
    : requireBoolean(request.composed_settlement, "composed_settlement");
  const includeGoalAcceptanceDetail = request.include_goal_acceptance_detail === undefined ? false
    : requireBoolean(request.include_goal_acceptance_detail, "include_goal_acceptance_detail");
  const includeVisionDetail = request.include_vision_detail === undefined ? false
    : requireBoolean(request.include_vision_detail, "include_vision_detail");
  if (typeof request.scheduler_execution_args !== "string") {
    throw new EffectRuntimeRequestError("Turn scheduler execution binding must be a string");
  }
  const cliArgv = request.cli_argv === undefined ? ["loopx"] : executableCliArgs(request.cli_argv);
  if (request.cli_argv !== undefined && cliArgv.length === 0) {
    throw new EffectRuntimeRequestError("Turn envelope cli_argv must be a non-empty list of strings");
  }
  const captureReferenceFile = request.capture_reference_file;
  if (captureReferenceFile !== undefined && !isNonEmptyFilePath(captureReferenceFile)) {
    throw new EffectRuntimeRequestError("Turn envelope capture_reference_file must be a non-empty file path");
  }
  if (request.cli_registry !== undefined && !isNonEmptyFilePath(request.cli_registry)) {
    throw new EffectRuntimeRequestError("Turn envelope cli_registry must be a non-empty file path");
  }
  if (request.cli_runtime_root !== undefined && !isNonEmptyFilePath(request.cli_runtime_root)) {
    throw new EffectRuntimeRequestError("Turn envelope cli_runtime_root must be a non-empty file path");
  }
  const coldReadOptions = {
    cliArgv,
    captureReferenceFile,
    registry: request.cli_registry ?? payload.registry,
    runtimeRoot: request.cli_runtime_root ?? payload.runtime_root,
  };
  if (payload.schema_version === COMPLETE_TURN_DECISION_VERSION) {
    const prior = requireCompleteTurnDecision(payload, request.source_number_tokens);
    if (object(prior.contract_binding).scheduler_execution_args !== request.scheduler_execution_args) {
      throw new EffectRuntimeRequestError("complete Turn decision cannot change its host binding");
    }
    return prior;
  }
  if (Object.hasOwn(payload, "schema_version") || Object.hasOwn(payload, "contract_binding")) {
    throw new EffectRuntimeRequestError("complete Turn decision source contains reserved version/binding fields");
  }
  requireCompleteDecisionSource(payload);
  const cliSource = object(object(payload.interaction_contract).cli_channel);
  const composedCli = composedSettlement ? completeCliSettlementChannel(payload) : cliSource;
  const prepared = composedCli === cliSource ? payload : {...payload,
    interaction_contract: {...object(payload.interaction_contract), cli_channel: composedCli}};
  const sourceIdentity = contextSourceIdentity(prepared, request.source_number_tokens ?? []);
  // A complete input must carry the original-Turn successor instructions that
  // used to exist only in the staged writer. Compose them before hashing and
  // capture; a later material read must not add a competing execution contract.
  const packet = object(prepared.replan_action_packet);
  const interaction = object(prepared.interaction_contract);
  const cli = object(interaction.cli_channel);
  const plan = object(cli.settlement_plan);
  let source = prepared;
  if (packet.obligation_id && packet.settlement_only !== true &&
      payload.effective_action !== EffectiveAction.GOVERNED_CAPABILITY_INTENT &&
      packet.obligation_id === object(object(payload.autonomous_replan_obligation).replan_context).obligation_id &&
      typeof object(plan.identity).turn_instance_id === "string") {
    const contract = bindReplanSuccessorContract(object(packet.writeback_contract), plan);
    source = {...prepared, replan_action_packet: {...packet, writeback_contract: contract},
      ...(typeof contract.successor_command === "string" ? {
        interaction_contract: {...interaction, cli_channel: {...cli,
          next_cli_actions: [contract.successor_command]}},
      } : {})};
  }
  // Retain the existing Explore owner's pure-replan applicability correction
  // before capture too. A complete contract must not advertise an attachment
  // that requires a Todo when this admission has only an obligation. Reuse the
  // exact known-guidance check; custom instructions and evidence stay intact.
  // Do not run the historical compactor: complete authoring remains in this capture.
  if (unboundExploreReplan(source, plan)) {
    const currentInteraction = object(source.interaction_contract);
    const agent = object(currentInteraction.agent_channel);
    const context = object(agent.work_context);
    if (Array.isArray(context.sources)) {
      source = {...source, interaction_contract: {...currentInteraction, agent_channel: {...agent,
        work_context: {...context, sources: context.sources.map(value => {
          const item = object(value);
          return item.source === "turn_start_capability_hook" && item.hook_id === "explore.turn_context" &&
            item.capability_id === "explore"
            ? {...item, content: omitUnboundExploreResultWriteback(object(item.content))} : value;
        })}}}};
    }
  }
  if (!includeVisionDetail) {
    source = compactRoutineSettlementPlan(source, coldReadOptions);
  }
  source = referenceGoalAcceptance(source, sourceIdentity);
  if (!includeGoalAcceptanceDetail) {
    source = scopeTurnGoalAcceptance(source, coldReadOptions);
  }
  source = referenceReplanAuthoring(source, sourceIdentity);
  source = referenceExploreGuidance(source);
  source = referenceCurrentTaskTitle(source);
  source = referenceVisionContinuation(source, sourceIdentity, true);
  source = referenceQuotaSummaryLanes(source, sourceIdentity);
  return sealCompleteTurnDecision(source, request.scheduler_execution_args, sourceIdentity);
}

const CONTEXT_ADDITION_FIELD_PATTERN = /^(?:turn_context_contributions|turn_context_hook_failures|[a-z][a-z0-9_]{2,95})$/;

export function attachCompleteTurnContext(value: unknown): JsonObject {
  const request = requireJsonObject(value, "complete Turn context request");
  const decision = requireCompleteTurnDecision(request.payload, request.source_number_tokens);
  const schedulerExecutionArgs = object(decision.contract_binding).scheduler_execution_args;
  if (typeof schedulerExecutionArgs !== "string") {
    throw new EffectRuntimeRequestError("Turn scheduler execution binding must be a string");
  }
  const additions = requireJsonObject(request.context_additions, "complete Turn context additions");
  const additionTokens = Array.isArray(request.context_addition_tokens)
    ? request.context_addition_tokens : [];
  const source: JsonObject = {...decision};
  delete source.schema_version;
  delete source.contract_binding;
  for (const [key, item] of Object.entries(additions)) {
    if (key === "schema_version" || key === "contract_binding"
        || !CONTEXT_ADDITION_FIELD_PATTERN.test(key)
        || Object.hasOwn(source, key)) {
      throw new EffectRuntimeRequestError(`complete Turn context cannot replace or add invalid field: ${key}`);
    }
    source[key] = item;
  }
  const combinedTokens = [
    ...(Array.isArray(request.source_number_tokens) ? request.source_number_tokens : []),
    ...additionTokens,
  ];
  const sourceIdentity = contextSourceIdentity(source, combinedTokens);
  return sealCompleteTurnDecision(source, schedulerExecutionArgs, sourceIdentity);
}

export function buildTurnEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "turn envelope request");
  const payload = requireJsonObject(request.payload, "turn envelope payload");
  if (typeof request.scheduler_execution_args !== "string") {
    throw new EffectRuntimeRequestError("turn envelope scheduler_execution_args must be a string");
  }
  if (payload.schema_version === COMPLETE_TURN_DECISION_VERSION) {
    requireCompleteTurnDecision(payload, request.source_number_tokens);
    if (object(payload.contract_binding).scheduler_execution_args !== request.scheduler_execution_args) {
      throw new EffectRuntimeRequestError("Turn adapter host binding differs from captured contract");
    }
  }
  const schedulerExecutionArgs = request.scheduler_execution_args;
  const capturedDecisionPath = request.captured_decision_path;
  if (capturedDecisionPath !== undefined && !isNonEmptyFilePath(capturedDecisionPath)) {
    throw new EffectRuntimeRequestError("turn envelope captured_decision_path must be a non-empty file path");
  }
  const agentId = scalarString(
    object(payload.agent_identity).agent_id,
    "quota payload agent_identity.agent_id",
  ).trim() || null;
  const actionProjectionValue = turnActionProjection(payload, replanReadPrecision(payload, request.source_number_tokens));
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
    source_decision_hash: decisionSourceHash(payload),
  };
  measureTurnEnvelope(envelope, payload);
  return envelope;
}

// The native Turn journal owns identity. A saved observation is evidence only;
// it cannot manufacture a quota receipt or authorize a later action.
function bindTurnDecisionCapture(request: JsonObject): JsonObject {
  const payload = requireJsonObject(request.payload, "captured decision");
  assertSupportedQuotaPacket(payload);
  if (payload.schema_version === COMPLETE_TURN_DECISION_VERSION) {
    try {
      requireCompleteTurnDecision(payload, request.source_number_tokens);
    } catch (error) {
      if (!(error instanceof EffectRuntimeRequestError)) throw error;
      throw new EffectRuntimeRequestError(
        `Turn decision capture does not match the original observation: ${error.message}`, error.code);
    }
  }
  const sourceHash = decisionSourceHash(payload);
  const completeHeader = Object.hasOwn(request, "complete_host_request")
    ? requireJsonObject(request.complete_host_request, "complete capture header") : null;
  const checked = completeHeader ? completeHostSource(request) : null;
  const envelope = checked ? {goal_id: checked.decision.goal_id ?? null, ...checked.projection}
    : requireJsonObject(request.envelope, "captured decision envelope");
  if (!checked) assertSupportedQuotaPacket(envelope);
  const path = scalarString(request.captured_decision_path, "capture path");
  const turnId = scalarString(request.turn_instance_id, "capture Turn identity");
  const turnKey = scalarString(request.turn_key, "capture Turn key");
  const signature = object(envelope.action_signature);
  if (!isNonEmptyFilePath(path) || !turnId.trim() ||
      !ENVELOPED_SHA256_PATTERN.test(turnKey) || payload.ok !== true ||
      payload.goal_id !== envelope.goal_id ||
      object(payload.agent_identity).agent_id !== envelope.agent_id ||
      (completeHeader ? completeHeader.source_decision_hash !== sourceHash || completeHeader.turn_key !== turnKey
        : signature.source_decision_hash !== sourceHash)) {
    throw new EffectRuntimeRequestError("Turn decision capture does not match the original observation");
  }
  const detail = object(completeHeader ? completeHeader.detail_ref : envelope.detail_ref);
  const original = object(detail.captured_decision);
  if (Object.keys(original).length && (original.path !== path ||
      original.turn_instance_id !== turnId || original.turn_key !== turnKey ||
      original.goal_id !== envelope.goal_id || original.agent_id !== envelope.agent_id)) {
    throw new EffectRuntimeRequestError("Turn decision capture identity does not match the journal");
  }
  const capture = object(coldPath(payload, String(envelope.agent_id), "", path).captured_decision);
  const readerArgv = detail.replan_read_argv ?? request.reader_argv;
  const reads = readerArgv ? replanReadCommands(payload, path, readerArgv, sourceHash) : null;
  const goal = reads ? replanGoalContext(payload, reads, undefined,
    replanReadPrecision(payload, request.source_number_tokens)) : null;
  const boundDetail = {...detail,
    full_decision: `cat -- ${shellQuote(path)}`,
    ...(reads ? {replan_action_packet: replanActionPacket(payload, reads), replan_read_argv: readerArgv,
      core_goal: goal!.core, planning_horizon: payload.planning_horizon == null ? null : goal!.horizon,
    } : {}),
    captured_decision: {...capture, turn_instance_id: turnId, turn_key: turnKey,
      ...(completeHeader ? {source_hash_ref: "$.source_decision_hash"} : {}),
      instruction: "Read this original observation for omitted context. Validate its Goal/Agent and source hash; " +
        "Turn identity comes from this journal-bound reference, not a fabricated quota receipt. " +
        "It grants no fresh authority: later actions require current admission and mutation checks. " +
        "Missing or invalid evidence requires recovery, never blind guard replay.",
    },
  };
  if (completeHeader) {
    const materialReaders = object(boundDetail.replan_action_packet);
    return {...completeHeader, detail_ref: {
      full_decision: boundDetail.full_decision, captured_decision: boundDetail.captured_decision,
      ...(materialReaders.review ? {replan_action_packet: pickOwn(materialReaders, ["review", "writeback"])} : {}),
    }};
  }
  const bound = {...envelope, detail_ref: boundDetail};
  measureTurnEnvelope(bound, payload);
  return bound;
}

// Explicit coverage of the registered decision/frontier union. New actions
// require a deliberate classification; unknown historical values retain Host routing.
export const TURN_ACTION_ROUTES = {
  [EffectiveAction.AGENT_WORKSPACE_REPAIR]: "repair_required",
  [EffectiveAction.BOUNDARY_PROJECTION_REPAIR]: "repair_required",
  [EffectiveAction.CAPABILITY_BRIDGE_REPAIR]: "repair_required",
  [EffectiveAction.CONTROL_PLANE_HEALTH_REPAIR]: "repair_required",
  [EffectiveAction.CONTROL_PLANE_PROJECTION_REPAIR]: "repair_required",
  [EffectiveAction.CONTROL_PLANE_REPAIR]: "repair_required",
  [EffectiveAction.RUNTIME_USER_GATE_PROJECTION_REPAIR]: "repair_required",
  [EffectiveAction.STATE_PROJECTION_GAP_REPAIR]: "repair_required",
  [EffectiveAction.TODO_DECISION_SCOPE_PROJECTION_REPAIR]: "repair_required",
  [EffectiveAction.AGENT_MONITOR_ONLY]: "ready_for_host",
  [EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED]: "ready_for_host",
  [EffectiveAction.BLOCKED_HEALTH]: "ready_for_host",
  [EffectiveAction.BLOCKED_WAIT]: "ready_for_host",
  [EffectiveAction.COORDINATE_TASK_BUNDLE]: "ready_for_host",
  [EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE]: "ready_for_host",
  [EffectiveAction.HEARTBEAT_RECEIPT_WRITE_FAILED]: "ready_for_host",
  [EffectiveAction.HEARTBEAT_SETTLED_SKIP]: "ready_for_host",
  [EffectiveAction.LARK_INBOX_REPLY_DUE]: "ready_for_host",
  [EffectiveAction.MONITOR_DUE]: "ready_for_host",
  [EffectiveAction.MONITOR_QUIET_SKIP]: "ready_for_host",
  [EffectiveAction.NORMAL_RUN]: "ready_for_host",
  [EffectiveAction.OPERATOR_GATE_NOTIFY]: "ready_for_host",
  [EffectiveAction.OPERATOR_INBOX_MATERIAL_REVIEW_DUE]: "ready_for_host",
  [EffectiveAction.OUTCOME_FLOOR_RECOVERY]: "ready_for_host",
  [EffectiveAction.PEER_COORDINATION_BLOCKED]: "ready_for_host",
  [EffectiveAction.QUOTA_SKIP]: "ready_for_host",
  [EffectiveAction.SCOPED_USER_GATE_FALLBACK]: "ready_for_host",
  [EffectiveAction.TERMINAL_NO_FOLLOWUP]: "ready_for_host",
  [EffectiveAction.THROTTLED_SKIP]: "ready_for_host",
  [EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY]: "ready_for_host",
  [EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED]: "replan_required",
  [EffectiveAction.GOVERNED_CAPABILITY_INTENT]: "capability_action_required",
  agent_scope_exhausted: "ready_for_host",
  agent_scope_wait: "ready_for_host",
  reassignment_required: "ready_for_host",
  autonomous_replan: "replan_required",
  successor_replan_required: "replan_required",
} satisfies Record<QuotaEffectiveActionValue | "autonomous_replan" | "successor_replan_required", LoopXTurnRouteValue>;

/** Route one admitted input, independent of controller continuation/budget policy. */
export function turnEnvelopeRoute(envelope: JsonObject, historicalText?: JsonObject): LoopXTurnRouteValue {
  const signature = object(envelope.action_signature);
  // The historical Python reader treats false/zero/null as an empty hash and
  // renders scalar booleans with Python spelling. Valid hashes remain strings.
  const sourceHash = scalarString(historicalText ? historicalText.source_hash : signature.source_hash || "", "route source hash");
  const envelopeHash = scalarString(historicalText ? historicalText.envelope_hash : signature.envelope_hash || "", "route envelope hash");
  if (signature.matches !== true || !sourceHash || sourceHash !== envelopeHash) return "contract_error";
  return admittedTurnRoute(envelope, () => turnEnvelopeReplanSettlementIdentity(envelope), historicalText);
}

/** Share action rules after the caller's versioned source check. A complete
 * source and a historical envelope retain their own proof and scalar codec. */
function admittedTurnRoute(
  work: JsonObject, replanIdentity: () => JsonObject | null, historicalText?: JsonObject,
): LoopXTurnRouteValue {
  // Size diagnostics grant no authority and do not veto a valid contract.
  const action = object(work.action);
  if (work.should_run === true) {
    // An unbound native Goal Turn can require its binding command before
    // work. That obligation cannot route a host launch, including a replan.
    if (action.must_attempt !== true || object(work.writeback).turn_binding_required === true) {
      return "blocked";
    }
    const name = scalarString(historicalText ? historicalText.effective_action : work.effective_action || "", "route effective action");
    const route = Object.hasOwn(TURN_ACTION_ROUTES, name)
      ? TURN_ACTION_ROUTES[name as keyof typeof TURN_ACTION_ROUTES] : "ready_for_host";
    if (route === "replan_required") {
      try { replanIdentity(); }
      catch (error) {
        if (!(error instanceof EffectRuntimeRequestError)) throw error;
        return "contract_error";
      }
      return "replan_required";
    }
    if (action.delivery_allowed !== true) return "blocked";
    if (route === "capability_action_required") {
      const intent = object(action.capability_intent), command = intent.command;
      // Preserve empty-container falsiness from historical Python inputs.
      const hasCommand = Array.isArray(command) ? command.length > 0
        : command !== null && typeof command === "object" ? Object.keys(command).length > 0 : Boolean(command);
      if (intent.schema_version !== "pending_capability_intent_projection_v0"
          || (intent.goal_id ?? null) !== (work.goal_id ?? null)
          || (intent.agent_id ?? null) !== (work.agent_id ?? null) || !hasCommand) return "contract_error";
      return "capability_action_required";
    }
    return route === "repair_required" ? "repair_required" : "ready_for_host";
  }
  if (object(work.user).action_required === true) return "user_action_required";
  return action.quiet_noop_allowed === true ? "wait" : "blocked";
}

// Local navigation vocabulary remains with the complete decision owner.
const INITIAL_CONTEXT_DECISION_FIELDS = ["ok", "schema_version", "goal_id", "agent_id", "should_run", "effective_action",
  "normal_delivery_allowed", "recovery_delivery_allowed", "self_repair_allowed", "capability_repair_allowed", "workspace_repair_allowed",
  "safe_bypass_allowed", "safe_bypass_kind", "blocked_action_scope", "reason",
  "execution_obligation", "workspace_guard", "goal_boundary", "heartbeat_receipt", "work_lane_contract", "selected_todo"];

/** The read-only plan needs the current decision and executable authoring
 * guidance. Evidence and diagnostics remain in its exact source capture. */
function turnPlanInspectionPaths(decision: JsonObject, route: "ready_for_host" | "replan_required"): string[][] {
  const paths = INITIAL_CONTEXT_DECISION_FIELDS
    .filter(field => field !== "schema_version" && (field !== "workspace_guard" || Object.hasOwn(decision, "workspace_guard")))
    .map(field => [field]);
  for (const field of ["agent_identity", "decision", "recommended_action", "execution_preparation", "requires_user_action",
    "action_portfolio", "delivery_workspace_isolation", "replan_action_packet"])
    paths.push([field]);
  const userTodos = object(decision.user_todo_summary);
  if (userTodos.open_count === 0 && userTodos.all_open_count === 0) {
    paths.push(["user_todo_summary", "open_count"], ["user_todo_summary", "all_open_count"]);
  } else {
    paths.push(["user_todo_summary"]);
  }
  for (const field of Object.keys(object(decision.autonomous_replan_obligation)))
    if (field !== "replan_context") paths.push(["autonomous_replan_obligation", field]);
  for (const field of Object.keys(object(decision.vision_continuation_audit)))
    if (field !== "vision_gap_judge") paths.push(["vision_continuation_audit", field]);
  const interaction = object(decision.interaction_contract);
  for (const field of ["schema_version", "mode", "user_channel"])
    paths.push(["interaction_contract", field]);
  for (const field of Object.keys(object(interaction.agent_channel)))
    if (field !== "work_context" && field !== "vision_continuation_audit")
      paths.push(["interaction_contract", "agent_channel", field]);
  paths.push(["interaction_contract", "agent_channel", "work_context", "instruction"]);
  for (const field of Object.keys(object(interaction.cli_channel)))
    if (field !== "vision_continuation_audit") paths.push(["interaction_contract", "cli_channel", field]);
  if (route === "ready_for_host") {
    for (const field of ["status_health_ok", "state", "status", "waiting_on", "quota", "scheduler_hint",
      "action_required", "action_selection_qualification", "execution_profile", "task_scope",
      "handoff_readiness", "planning_horizon"])
      paths.push([field]);
    for (const field of ["next_step", "next_action_basis"])
      paths.push(["agent_lane_next_action", field]);
    for (const field of ["selected_todo_ref", "selected_todo_authority", "user_todos", "unavailable_context", "failures"])
      paths.push(["interaction_contract", "agent_channel", "work_context", field]);
  }
  return paths;
}

function readyWorkOpening(context: JsonObject): JsonObject | undefined {
  return inlineNormalWorkOpening(context, {
    work_lane_contract: context.work_lane_contract, goal_boundary: context.goal_boundary,
    goal_acceptance: context.goal_acceptance, task_scope: context.task_scope,
    latest_run_recommended_action: context.latest_run_recommended_action,
  });
}

/** Navigation over captured facts, never a summary or a source-read receipt. */
function capturedDecisionSelection(request: JsonObject): JsonObject {
  const normalOnly = request.normal_work_initial_read === undefined ? false
    : requireBoolean(request.normal_work_initial_read, "normal_work_initial_read");
  const context = normalOnly ? requireJsonObject(request.context, "captured context") : undefined;
  const scoped = context && !normalWorkReadContext(context)
    ? {...request, include_initial_read_plan: false} : request;
  const initial = request.initial_read === true || request.list_fields === true
    && Array.isArray(request.select) && request.select.length === 1 && request.select[0] === "";
  const selection = capturedContextSelection(scoped, initial ? initialContextReadPointers(
    context ?? requireJsonObject(request.context, "captured context"), INITIAL_CONTEXT_DECISION_FIELDS) : []);
  const opening = context && selection.initial_read_plan ? readyWorkOpening(context) : undefined;
  return opening ? {...selection, inline_opening: opening} : selection;
}

/** Only the exact list can carry forward. Current boundary controls stay inline. */
function readyWriteScope(context: JsonObject): ReusableExactBody | undefined {
  const interaction = jsonObject(context.interaction_contract);
  const channel = jsonObject(interaction?.agent_channel);
  const preparation = jsonObject(jsonObject(interaction?.cli_channel)?.execution_preparation);
  const boundary = jsonObject(context.goal_boundary);
  const identityAgent = jsonObject(context.agent_identity)?.agent_id;
  const agent = context.agent_id ?? identityAgent;
  const todo = jsonObject(context.selected_todo)?.todo_id;
  const route = [context.registry, context.runtime_root, context.goal_id, agent, todo];
  if (context.ok !== true || context.should_run !== true || context.effective_action !== EffectiveAction.NORMAL_RUN ||
      context.normal_delivery_allowed !== true || channel?.delivery_allowed !== true ||
      jsonObject(channel.work_context)?.complete !== true || jsonObject(interaction?.user_channel)?.action_required !== false ||
      (identityAgent !== undefined && identityAgent !== agent) ||
      preparation?.schema_version !== EXECUTION_PREPARATION_SCHEMA_VERSION || preparation.state !== "ready" ||
      preparation.delivery_allowed !== true || preparation.agent_id !== agent || preparation.todo_id !== todo ||
      boundary?.rule !== "stay_in_scope_or_stop" || Object.hasOwn(boundary, "write_scope_pointer") ||
      !Array.isArray(boundary.write_scope) || boundary.write_scope.some(scope => typeof scope !== "string" || !scope) ||
      !route.every(part => typeof part === "string" && !!part)) return undefined;
  return {path: ["goal_boundary", "write_scope"],
    sourceIdentity: JSON.stringify([route, context.goal_ref ?? null, boundary.handoff_mode ?? null])};
}

/** Work preparation retains current controls and keeps long scope lists at their exact source addresses. */
function progressivePreparationScopes(payload: JsonObject): PreparationScopeDetail[] {
  const boundary = jsonObject(payload.goal_boundary);
  const cba = jsonObject(boundary?.checkpointed_boundary_authority);
  const hasActiveApprovalScopes = cba?.schema_version === "checkpointed_boundary_authority_v0" &&
    Array.isArray(cba.active_write_scope) && cba.active_write_scope.some(s => typeof s === "string" && s.length > 0);
  const interaction = jsonObject(payload.interaction_contract);
  const cli = jsonObject(interaction?.cli_channel);
  const preparation = jsonObject(cli?.execution_preparation);
  const obligation = jsonObject(payload.execution_obligation);
  const repair = interaction?.schema_version === INTERACTION_CONTRACT_SCHEMA_VERSION &&
    payload.should_run === true &&
    jsonObject(interaction.agent_channel)?.delivery_allowed === false &&
    cli?.spend_allowed_now === false && cli.spend_after_validation === false &&
    payload.effective_action === EffectiveAction.AGENT_WORKSPACE_REPAIR &&
    interaction.mode === EffectiveAction.AGENT_WORKSPACE_REPAIR && payload.workspace_repair_allowed === true &&
    payload.normal_delivery_allowed === false && payload.recovery_delivery_allowed === false &&
    obligation?.kind === EffectiveAction.AGENT_WORKSPACE_REPAIR && obligation.contract === "agent_workspace_guard" &&
    obligation.must_attempt_work === true && obligation.delivery_allowed === false;
  const prepEligible = interaction?.schema_version === INTERACTION_CONTRACT_SCHEMA_VERSION &&
    payload.should_run === true &&
    jsonObject(interaction.agent_channel)?.delivery_allowed === false &&
    cli?.spend_allowed_now === false && cli.spend_after_validation === false &&
    (repair || payload.effective_action === EffectiveAction.NORMAL_RUN);
  const selecting = prepEligible && hasUnboundActionSelection(cli);
  const selected = jsonObject(payload.selected_todo);
  const claiming = prepEligible && cli?.selection_required !== true && payload.normal_delivery_allowed === false &&
    preparation?.schema_version === EXECUTION_PREPARATION_SCHEMA_VERSION && preparation.state === "claim_required" &&
    preparation.delivery_allowed === false && typeof selected?.todo_id === "string" &&
    preparation.todo_id === selected.todo_id && jsonObject(preparation.claim)?.host_execution_allowed === true;
  if (!repair && !selecting && !claiming && !hasActiveApprovalScopes) return [];
  // The recovery pointer and its guidance also cost bytes. Leave short lists inline.
  const longScope = (value: unknown) => Array.isArray(value) && value.every(scope => typeof scope === "string")
    && Buffer.byteLength(JSON.stringify(value)) >= 1024;
  const scopes: PreparationScopeDetail[] = [];
  if ((repair || selecting || claiming) && boundary && !Object.hasOwn(boundary, "write_scope_pointer") && longScope(boundary.write_scope)) scopes.push({
    path: ["goal_boundary", "write_scope"],
    marker_path: ["goal_boundary", "write_scope_pointer"],
    instruction: repair
      ? "If workspace repair needs the Goal's write scopes, select /goal_boundary/write_scope from the source below. "
      : "If work preparation needs the Goal's write scopes, select the pointer in goal_boundary.write_scope_pointer from the source below. ",
  });
  if (hasActiveApprovalScopes) scopes.push({
    path: ["goal_boundary", "checkpointed_boundary_authority", "active_write_scope"],
    instruction: "If this turn depends on checkpointed approved write scopes, select /goal_boundary/checkpointed_boundary_authority/active_write_scope from the source below. ",
  });
  const finalScope = scopes.at(-1);
  if (finalScope) finalScope.instruction += repair
    ? "After relocating, rerun the guard for this Turn and use its complete current scopes before delivery. "
    : "Use the complete current scope from the ready decision before delivery. ";
  return scopes;
}

function contextPresentationPlan(
  request: JsonObject,
  flags = presentationDetailFlags(request),
): JsonObject {
  const context = requireJsonObject(request.context, "current projection");
  return buildContextPresentationPlan(context, request.surface, flags);
}

const resolveViewWriteback = (
  payload: JsonObject, precision: ReplanReadPrecision | undefined, verifiedSourceHash: string,
): JsonObject => object(actionProjection(payload, false, precision, verifiedSourceHash).writeback);

export function evaluateTurnEnvelope(value: unknown): JsonObject {
  let request = requireJsonObject(value, "turn envelope request");
  const operation = request.operation;
  if (Object.hasOwn(request, "source_transport") && (operation === "compose_complete" ||
      operation === "attach_complete_turn_context" || operation === "context_delivery")) {
    const field = operation === "context_delivery" ? "context" : "payload";
    const source = decodeTurnSource(request, field);
    // Projection edits must still address the caller's original inline shape.
    const {source_transport: _transport, ...options} = request;
    request = {...options, [field]: source};
  }
  if (operation === "session_plan") return turnSessionPlan(request);
  if (operation === "planning_facts") return completePlanningFacts(request);
  if (operation === "recorded_plan_lineage") {
    const {work, decision} = checkedTurnPlanSource(
      requireJsonObject(request.plan, "recorded Turn plan"), request.source_number_tokens);
    const identity = decision === null ? work : completeSessionLineage(decision);
    return {goal_id: identity.goal_id ?? null, agent_id: identity.agent_id ?? null};
  }
  if (operation === "plan_work") {
    const {decision, projection} = completeHostSource(request);
    return {goal_id: decision.goal_id ?? null, ...projection};
  }
  if (operation === "capability_automation_source") {
    // Optional provider participation needs checked identity and capability
    // gates, not the signed action document or its work context.
    const host = Object.hasOwn(request, "complete_host_request") || Object.hasOwn(request, "envelope");
    if (host && Object.hasOwn(request, "source_transport")) {
      throw new EffectRuntimeRequestError("invalid Turn composition source transport");
    }
    const checked = host ? completeHostSource(request) : null;
    const payload = checked?.decision ?? (Object.hasOwn(request, "source_transport")
      ? decodeTurnSource(request, "payload") : requireJsonObject(request.payload, "quota payload"));
    if (!checked && (Object.hasOwn(payload, "schema_version") || Object.hasOwn(payload, "contract_binding"))) {
      requireCompleteTurnDecision(payload, request.source_number_tokens);
    }
    const projection = checked?.projection ?? turnActionProjection(payload,
      replanReadPrecision(payload, request.source_number_tokens));
    assertSupportedQuotaPacket(projection);
    if (!checked && payload.agent_id && projection.agent_id && payload.agent_id !== projection.agent_id) {
      throw new EffectRuntimeRequestError("context source has conflicting Agent identities");
    }
    return {goal_id: payload.goal_id ?? null, agent_id: projection.agent_id ?? null,
      boundary: {capabilities: object(projection.boundary).capabilities ?? null}};
  }
  if (operation === "host_context_binding") {
    const decision = requireCompleteTurnDecision(request.payload, request.source_number_tokens);
    const header = requireCompleteHostHeader(request, decision);
    if (!Object.hasOwn(header, "source_decision_hash")) {
      throw new EffectRuntimeRequestError("complete Turn plan requires a source binding");
    }
    // Reader addresses are transport metadata. A recorded typed result may
    // still need the previous basis to recover its optional result schema.
    return {schema_version: header.schema_version, turn_key: header.turn_key,
      source_decision_hash: decisionSourceHash(decision),
      ...(request.recorded_capability_result === true ? {
        recorded_projection: {goal_id: decision.goal_id ?? null, ...turnActionProjection(decision)},
      } : {})};
  }
  if (operation === "replan_view") return replanView(request, resolveViewWriteback);
  if (operation === "context_select") return capturedDecisionSelection(request);
  if (operation === "replan_cli_view") {
    const payload = requireJsonObject(request.payload, "replan CLI payload");
    const precision = replanReadPrecision(payload, request.source_number_tokens);
    const sourceEdits = request.source_edits === true;
    const identity = precision?.identity ?? (sourceEdits ? contextSourceIdentity(payload) : undefined);
    const packet = object(payload.replan_action_packet);
    // A staged capture has already pinned its reader to a checked source.
    // An unstaged copy needs the path verified by the current capture reader.
    const view = packet.review !== undefined || packet.writeback !== undefined ? payload
      : replanCliView(payload, identity, replanReadCommands(payload,
        request.captured_decision_path ?? payload.replan_read_path), precision);
    return sourceEdits ? contextProjectionChanges(payload, view, precision?.values) : view;
  }
  if (operation === "compose_complete") {
    const result = composeCompleteTurnDecision(request);
    return request.source_edits === true
      ? contextProjectionChanges(requireJsonObject(request.payload, "complete Turn source"), result) : result;
  }
  if (operation === "attach_complete_turn_context") {
    const result = attachCompleteTurnContext(request);
    if (request.source_edits !== true) return result;
    const payload = requireJsonObject(request.payload, "complete Turn decision");
    const additions = requireJsonObject(request.context_additions, "complete Turn context additions");
    return contextProjectionChanges({...payload, ...additions}, result);
  }
  if (operation === "complete_consumer") {
    requireCompleteTurnDecision(request.payload, request.source_number_tokens);
    return {ok: true};
  }
  if (operation === "complete_host_lineage") {
    // Session-index reads need only the original checked source. A supplied
    // adapter or wire header still selects full Host validation, even if bad.
    // This read returns identity facts and grants no execution authority.
    const decision = Object.hasOwn(request, "envelope") || Object.hasOwn(request, "complete_host_request")
      ? completeHostDecision(request) : requireCompleteTurnDecision(request.payload, request.source_number_tokens);
    return completeSessionLineage(decision);
  }
  if (operation === "host_context" || operation === "host_dispatch_context") {
    if (Object.hasOwn(request, "include_session_lineage") &&
        typeof request.include_session_lineage !== "boolean") {
      throw new EffectRuntimeRequestError("Host session-lineage projection option must be a boolean");
    }
    if (Object.hasOwn(request, "include_model_context") &&
        typeof request.include_model_context !== "boolean") {
      throw new EffectRuntimeRequestError("Host model-context projection option must be a boolean");
    }
    // The current wire carries complete work facts. Only transitional requests
    // need the historical executor projection to prove agreement with them.
    const legacy = Object.hasOwn(request, "complete_host_request") ? null : completeHostSource(request);
    const decision = legacy?.decision ?? completeHostDecision(request);
    const {primaryAction, replanPacket} = legacy
      ? {primaryAction: object(legacy.projection.action).primary_action ?? null,
        replanPacket: legacy.projection.replan_action_packet ?? null}
      : turnPrimaryAction(decision, replanReadCommands(decision));
    if (operation === "host_dispatch_context" && replanPacket === null) {
      assertGoalContextDelivered(object(object(decision.interaction_contract).agent_channel));
    }
    // The caller already holds this exact checked source. Return only the
    // resolved facts it needs, not another full copy over the transport.
    return {primary_action: primaryAction, replan_admitted: replanPacket !== null,
      checkpointed_write_approval: checkpointedBoundaryAuthority(object(decision.goal_boundary)) !== null,
      execution_profile_present: Object.keys(object(decision.execution_profile)).length > 0,
      ordinary_work_segment_guidance: usesStandardOneStepWorkSegment(decision),
      ...(request.include_model_context === true
        ? {model_context_schema_version: "loopx_turn_model_context_v0",
          model_context_changes: hostModelContextChanges(decision)} : {}),
      ...(operation === "host_dispatch_context" ? {work_identity: completeWorkIdentity(decision),
        ...(request.include_session_lineage === true ? {session_lineage: completeSessionLineage(decision)} : {})} : {})};
  }
  if (operation === "selected_work_todo") {
    const source = requireJsonObject(request.payload, "Turn work selection source");
    const historical = scalarString(request.historical_primary_text, "historical primary Todo");
    return contextProjectionChanges(source, {...source, selected_todo: selectedWorkTodo(source, historical)});
  }
  if (operation === "driver_route") return {route: turnEnvelopeRoute(requireJsonObject(request.envelope, "Turn route envelope"),
    request.historical_route_text === undefined ? undefined : requireJsonObject(request.historical_route_text, "historical route text"))};
  if (operation === "driver_lineage") {
    const completeWire = Object.hasOwn(request, "complete_host_request");
    const checked = Object.hasOwn(request, "payload") || completeWire ? completeHostSource(request) : null;
    const decision = checked?.decision ?? null;
    // Recovery consumes the already checked action facts. It does not rebuild
    // a v0 adapter, cold reader commands or size diagnostics from that source.
    // Historical carriers keep their signed envelope and version-pinned proof.
    const work = completeWire && checked ? {goal_id: checked.decision.goal_id ?? null, ...checked.projection}
      : requireJsonObject(request.envelope, "Turn driver envelope");
    const historical = !decision && request.historical_route_text !== undefined
      ? requireJsonObject(request.historical_route_text, "historical route text") : undefined;
    // Both planning and recovery use these action/selection/hash facts. Python
    // retains only its persisted number codec; historical readers keep proof.
    return admittedDriverLineage(work, decision, completeWire, historical);
  }
  if (operation === "execution_context") {
    // The CLI consumes one checked source for execution, not the historical
    // adapter's layout. Reuse the projection already checked against that
    // source for effect conditions; this is not another model-facing contract.
    const {decision, projection} = completeHostSource(request);
    const work = {selected_todo: object(projection.action).selected_todo,
      task_orchestration_contract: projection.task_orchestration_contract};
    const sha256 = completeTaskTextSha256(decision, selectedWorkTodoId(work) ?? "");
    const preparation = object(decision.execution_preparation);
    return {selected_todo: selectedWorkTodo(work), sha256,
      selected_work_basis: selectedWorkLaunchBasis(decision, sha256),
      effective_action: decision.effective_action ?? null,
      writeback: object(projection.writeback),
      task_lease_proof: object(preparation.lease_proof),
      ...(preparation.lease_epoch == null ? {} : {task_lease_epoch: leaseEpoch(preparation)}),
      replan_guard_scoped: Object.hasOwn(projection, "replan_action_packet"),
      replan_action_packet: projection.replan_action_packet ?? null};
  }
  if (operation === "captured_todo_text") {
    const envelope = requireJsonObject(request.envelope, "Turn completion envelope");
    if (Object.hasOwn(request, "payload")) {
      return {sha256: completeTaskTextSha256(completeHostDecision(request), request.todo_id)};
    }
    const action = object(envelope.action);
    return {sha256: capturedTodoTextSha256(object(action.selected_todo), envelope.work_context,
      action.recommended_action, request.todo_id)};
  }
  if (operation === "consumer_version") {
    assertTurnEnvelopeConsumerVersion(requireJsonObject(request.envelope, "turn_envelope"));
    return {ok: true};
  }
  if (operation === "reference_context") {
    const source = requireJsonObject(request.payload, "quota decision");
    let payload = source;
    if (request.vision_audit === true) payload = referenceVisionContinuation(source,
      contextSourceIdentity(source, request.source_number_tokens));
    if (jsonObject(request.diagnostics)) payload = projectQuotaDiagnostics(payload, request.diagnostics as JsonObject);
    if (request.goal_acceptance === true) payload = referenceGoalAcceptance(payload,
      contextSourceIdentity(source, request.source_number_tokens));
    if (request.replan_authoring === true) {
      const shared = referenceReplanAuthoring(payload, contextSourceIdentity(source, request.source_number_tokens));
      if (shared !== payload) {
        const packet = object(shared.replan_action_packet);
        payload = {...shared, replan_action_packet: {...packet, payload_compaction: {
          schema_version: "quota_cli_replan_action_compaction_v0", mode: "compact_hot_path",
          compacted_fields: ["writeback_contract.vision_authoring"],
          projection_ref: object(packet.writeback_contract).vision_authoring_ref,
        }}};
      }
    }
    if (request.task_title === true) payload = referenceCurrentTaskTitle(payload);
    if (request.explore_guidance === true) payload = referenceExploreGuidance(payload);
    if (request.refresh_result === true) payload = referenceExploreResultDelivery(payload, request.source_number_tokens);
    // References and diagnostic caps share one edit transport. Untouched facts
    // stay in the original caller, including numbers JSON.parse cannot retain.
    return contextProjectionChanges(source, payload);
  }
  if (operation === "build") return buildTurnEnvelope(request);
  if (operation === "context_read_sections") {
    const selected = capturedContextSelection({...request, list_fields: false});
    return {sections: contextReadSectionDigests(request,
      (selected.selections as {path: (string | number)[]}[]).map(row => row.path),
      readyWriteScope(requireJsonObject(request.context, "read section source")))};
  }
  if (operation === "progressive_context_delivery")
    return prepareProgressiveContext(request, checkedContextPresentationPlan(request));
  if (operation === "context_delivery") {
    if (request.native_projection === undefined) {
      presentationDetailFlags(request);
      if (request.surface === "complete_turn") checkedContextPresentationSource(request);
      return prepareContextDelivery(request, LEGACY_QUOTA_CONTEXT_FIELDS);
    }
    const context = requireJsonObject(request.context, "current projection");
    return prepareProgressiveContextDelivery(request, LEGACY_QUOTA_CONTEXT_FIELDS, INITIAL_CONTEXT_DECISION_FIELDS,
      checkedContextPresentationPlan(request), readyWriteScope(context),
      request.include_initial_read_plan === true ? readyWorkOpening(context) : undefined);
  }
  if (operation === "native_context_lifecycle") {
    const journal = updateNativeContextLifecycle(request);
    return request.source_edits === true
      ? contextProjectionChanges(requireJsonObject(request.state, "native context journal"), journal)
      : journal;
  }
  if (operation === "stage_then_swap_plan") return planStageThenSwapPrefetch(request);
  if (operation === "stage_then_swap_evaluate") return evaluateEpochPrefetchSwap(request);
  if (operation === "stage_then_swap_prompt_gate") return evaluateEpochPrefetchPromptGate(request);
  if (operation === "semantic_unit_anchor_record") return recordSemanticUnitAnchor(request);
  if (operation === "semantic_unit_fold_swap") return buildSemanticUnitFoldSwap(request);
  if (operation === "quiet_turn_evaluate") return evaluateQuietTurn(request);
  if (operation === "quiet_turn_state_digest") return computeQuietTurnStateDigest(request);
  if (operation === "context_recovery") return restoreContextDelivery(request, LEGACY_QUOTA_CONTEXT_FIELDS);
  if (operation === "bind_capture") return bindTurnDecisionCapture(request);
  if (operation === "scoped_settlement_identity") return {identity: turnEnvelopeScopedSettlementIdentity(
    requireJsonObject(request.envelope, "envelope"), request.turn_instance_id)};
  if (operation === "replan_settlement_identity") return {identity: turnEnvelopeReplanSettlementIdentity(
    requireJsonObject(request.envelope, "turn_envelope"), request.turn_instance_id,
  )};
  if (operation === "envelope_signature") return turnEnvelopeActionSignatureDocument(request.envelope);
  if (operation === "quota_signature") return quotaActionSignatureDocument(request.payload, request.source_number_tokens);
  throw new EffectRuntimeRequestError("turn envelope operation is unsupported");
}

/** A scoped planning identity comes from the signed quota plan, never a Host
 * session or a missing Todo. Journal validation reuses this exact binding rule.
 */
function scopedTurnSettlementIdentity(work: JsonObject, turnInstanceId?: unknown): JsonObject | null {
  assertSupportedQuotaPacket(work);
  const writeback = object(work.writeback);
  const plan = Object.keys(object(writeback.settlement_plan)).length ? object(writeback.settlement_plan)
    : {identity: writeback.settlement_identity};
  const raw = object(plan.identity);
  if (raw.replan_obligation_id == null && raw.request_subject == null && raw.binding_kind !== "autonomous_replan") return null;
  const parsed = settlementIdentityFromPlan({settlement_plan: plan});
  if (parsed.failure !== null) throw new EffectRuntimeRequestError(parsed.failure.reason);
  const identity = parsed.value;
  if ((identity.binding_kind === "request" ? work.effective_action !== EffectiveAction.NORMAL_RUN
        : work.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED)
      || selectedWorkTodoId({selected_todo: object(work.action).selected_todo,
        task_orchestration_contract: work.task_orchestration_contract}) !== null
      || !["autonomous_replan", "request"].includes(identity.binding_kind)
      || identity.goal_id !== work.goal_id || identity.agent_id !== work.agent_id
      || turnInstanceId != null && identity.turn_instance_id !== turnInstanceId
      || identity.binding_kind === "autonomous_replan" && identity.replan_obligation_id !== object(work.replan_action_packet).obligation_id) {
    throw new EffectRuntimeRequestError("Turn replan settlement identity does not match its signed quota admission");
  }
  return settlementIdentityPayload(identity);
}

/** Historical envelopes keep their version/signature proof around the same
 * scoped-identity rules used after complete-source validation. */
export function turnEnvelopeScopedSettlementIdentity(envelope: JsonObject, turnInstanceId?: unknown): JsonObject | null {
  const identity = scopedTurnSettlementIdentity(envelope, turnInstanceId);
  if (identity === null) return null;
  const signature = object(envelope.action_signature);
  if (envelope.schema_version !== TURN_ENVELOPE_SCHEMA_VERSION
      || signature.matches !== true || signature.source_hash !== signature.envelope_hash
      || signature.envelope_hash !== canonicalHash(turnEnvelopeActionSignatureDocument(envelope))) {
    throw new EffectRuntimeRequestError("Turn replan settlement identity does not match its signed quota admission");
  }
  return identity;
}

/** Persisted planning callers retain their narrow compatibility entrypoint. */
export function turnEnvelopeReplanSettlementIdentity(envelope: JsonObject, turnInstanceId?: unknown): JsonObject | null {
  const identity = turnEnvelopeScopedSettlementIdentity(envelope, turnInstanceId);
  return identity?.binding_kind === "autonomous_replan" ? identity : null;
}
