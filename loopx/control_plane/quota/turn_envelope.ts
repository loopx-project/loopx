import { EffectiveAction } from "./effective_action.generated.ts";
import { createHash } from "node:crypto";

import {
  interpretQuotaShouldRunPacket,
  type JsonObject,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { turnStartPromptBudgetBytes } from "../capability_hooks.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { projectPendingCapabilityIntent } from "../work_items/pending_capability_intent.ts";
import { measureTurnEnvelope, turnEnvelopeBudgetBytes } from "./turn_envelope_budget.ts";
export { TURN_ENVELOPE_BUDGET_BYTES } from "./turn_envelope_budget.ts";

export const TURN_ENVELOPE_SCHEMA_VERSION = "loopx_turn_envelope_v0";
export const CONTRACT_CAPSULE_SCHEMA_VERSION = "loopx_contract_capsule_v0";
export const ACTION_SIGNATURE_SCHEMA_VERSION = "loopx_action_signature_v0";
export const ACTION_SIGNATURE_COVERAGE_V0 = "turn_envelope_action_dimensions_v0";
export const ACTION_SIGNATURE_COVERAGE_V1 = "turn_envelope_action_dimensions_v1";
export const ACTION_SIGNATURE_COVERAGE_V2 = "turn_envelope_action_dimensions_v2";
export const ACTION_SIGNATURE_COVERAGE_V3 = "turn_envelope_action_dimensions_v3";
export const ACTION_SIGNATURE_COVERAGE_V4 = "turn_envelope_action_dimensions_v4";
export const ACTION_SIGNATURE_COVERAGE = ACTION_SIGNATURE_COVERAGE_V0;

const EXECUTABLE_CLI_ARGS_MAX_ITEMS = 64;
const EXECUTABLE_CLI_ARGS_MAX_ITEM_CHARS = 512;
const EXECUTABLE_CLI_ARGS_MAX_TOTAL_CHARS = 2_048;
const SCHEDULER_DETAIL_REQUEST = "loopx quota should-run --include-detail scheduler";
const PROTOCOL_ACTION_PACKET_LLM_POLICY = "no_api";
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
  execution_profile: ["cadence", "minimum_scale", "spend_rule", "must_include"],
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
  vision_continuation_audit: [
    "schema_version", "required", "decision", "selected_todo_is_goal_completion",
    "closeout_allowed_without_evidence", "trigger_kinds", "recommended_action",
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
  vision_continuation_audit: { recommended_action: 48 },
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

function sameActionText(left: unknown, right: unknown): boolean {
  const leftText = text(left, 2_000);
  const rightText = text(right, 2_000);
  if (!leftText || !rightText) return false;
  // JS has no native casefold. Upper-then-lower preserves the Python v0
  // behavior for multi-character folds such as German sharp-s and ligatures.
  const leftFolded = leftText.toUpperCase().toLowerCase();
  const rightFolded = rightText.toUpperCase().toLowerCase();
  if (leftFolded === rightFolded) return true;
  if (leftFolded.endsWith("...")) {
    const prefix = leftFolded.slice(0, -3).trimEnd();
    return prefix.length >= 80 && rightFolded.startsWith(prefix);
  }
  if (rightFolded.endsWith("...")) {
    const prefix = rightFolded.slice(0, -3).trimEnd();
    return prefix.length >= 80 && leftFolded.startsWith(prefix);
  }
  return false;
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
  ]) {
    if (source[field] !== null && source[field] !== undefined) compact[field] = source[field];
  }
  const rendered = text(source.text, 360);
  if (rendered && sameActionText(source.text, recommendedAction)) {
    compact.text_ref = "action.recommended_action";
  } else if (rendered) {
    compact.text = rendered;
  }
  return Object.keys(compact).length > 0 ? compact : null;
}

function replanActionPacket(payload: JsonObject): JsonObject | null {
  const source = object(payload.replan_action_packet);
  if (Object.keys(source).length === 0) return null;
  const compact = compactFields(source, [
    "schema_version", "decision", "obligation_id", "uncovered_frontier",
    "required_outcome", "allowed_terminal", "bounded_frontier",
  ]);
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
  const raw = interaction.required_reads || payload.required_reads;
  if (!Array.isArray(raw)) return [];
  const result: JsonObject[] = [];
  for (const value of raw.slice(0, 5)) {
    const item = object(value);
    const promptBudget = item.source === "turn_start_capability_hook"
      ? turnStartPromptBudgetBytes(item.prompt_budget_bytes) : 0;
    const command = text(item.command, promptBudget || 360);
    if (!command) continue;
    const compact: JsonObject = { command };
    if (promptBudget) compact.prompt_budget_bytes = promptBudget;
    for (const field of ["kind", "reason", "source"]) {
      const rendered = text(item[field], 240);
      if (rendered) compact[field] = rendered;
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
  for (const field of ["write_scope", "available_capabilities", "requires_parent_approval"]) {
    const values = textList(source[field], 16, 180);
    if (values.length > 0) result[field] = values;
  }
  // Carry the already resolved approval from quota; dropping it leaves only
  // the bootstrap approval requirement and strands authorized execution.
  const approved = object(source.checkpointed_boundary_authority);
  const approvedScopes = Array.isArray(approved.active_write_scope)
    ? approved.active_write_scope.filter((scope): scope is string =>
      typeof scope === "string" && scope.length > 0 && scope.length <= 180).slice(0, 16)
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
  const guards = textList(source.guards, 8, 280);
  if (guards.length > 0) result.guards = guards;
  const stopCondition = text(source.stop_condition, 320);
  if (stopCondition) result.stop_condition = stopCondition;
  for (const field of ["execution_profile", "orchestration"]) {
    const value = object(source[field]);
    if (Object.keys(value).length > 0) result[field] = value;
  }
  const workspaceGuard = object(payload.workspace_guard);
  if (Object.keys(workspaceGuard).length > 0) result.workspace_guard = workspaceGuard;
  const capabilityGate = object(payload.capability_gate);
  if (Object.keys(capabilityGate).length > 0) {
    result.capability_gate = Object.fromEntries(
      ["action", "reason", "required_capabilities", "missing_capabilities", "owner_action"]
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
  const app: JsonObject = {};
  for (const field of [
    "host_surface", "apply", "host_action", "recommended_rrule",
    "no_spend_for_cadence_change",
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
  const cliArgs = executableCliArgs(ack.cli_args);
  if (cliArgs.length > 0) {
    app.ack_cli_args = cliArgs;
  } else if (ack.cli_args) {
    app.ack_cli_args_detail_ref = {
      reason: "omitted_to_preserve_executable_argv",
      request: SCHEDULER_DETAIL_REQUEST,
    };
  }
  if (object(sourceApp.failure_hint).cli_args) {
    app.failure_cli_args_detail_ref = {
      reason: "cold_path_until_host_update_failure",
      request: SCHEDULER_DETAIL_REQUEST,
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

function renderProtocolActionPacketSummary(fields: JsonObject): string {
  return Object.entries(fields).map(([key, value]) => {
    const rendered = typeof value === "boolean"
      ? String(value)
      : scalarString(value, `protocol_action_fields.${key}`);
    return `${key}=${rendered}`;
  }).join(" ");
}

function derivedProtocolActionPacketFields(
  action: JsonObject,
  user: JsonObject,
  capsule: JsonObject,
  schedulerValue: JsonObject,
): JsonObject {
  const interaction = object(capsule.interaction_contract);
  const workLane = object(capsule.work_lane_contract);
  const automation = object(capsule.automation_liveness);
  const mode = scalarString(interaction.mode, "interaction_contract.mode");
  const userRequired = Boolean(user.action_required);
  const agentRequired = Boolean(action.must_attempt);
  const actor = userRequired && ["scoped_user_gate_fallback", "bounded_delivery_with_user_notice"].includes(mode)
    ? "agent_with_user_gate"
    : userRequired ? "user" : "agent";
  const fields: JsonObject = {
    actor,
    user_action_required: userRequired,
    agent_action_required: agentRequired,
    quiet_noop_allowed: Boolean(action.quiet_noop_allowed),
  };
  if (workLane.lane) fields.lane = workLane.lane;
  if (automation.automation_action) fields.automation = automation.automation_action;
  if (schedulerValue.action) fields.scheduler = schedulerValue.action;
  if (automation.pause_allowed === false) fields.pause_allowed = false;
  fields.llm = PROTOCOL_ACTION_PACKET_LLM_POLICY;
  const userActions = Array.isArray(user.actions) ? user.actions : [];
  const actionKey = agentRequired || !userRequired ? "agent_action" : "user_action";
  if (userActions.length > 0 && (!userRequired || actionKey !== "user_action")) {
    fields.user_action_pending = true;
    const userAction = text(userActions[0], 80);
    if (userAction) fields.user_action = userAction;
  }
  const actionValue = agentRequired
    ? action.primary_action
    : userRequired && userActions.length > 0
    ? userActions[0]
    : workLane.obligation === "quiet_until_material_monitor_transition"
    ? "quiet until a material monitor transition, regression, or concrete blocker appears"
    : action.primary_action || "quiet no-op; no material transition";
  const rendered = text(actionValue, 80);
  if (rendered) fields[actionKey] = rendered;
  return fields;
}

function contractCapsule(
  payload: JsonObject,
  action: JsonObject,
  user: JsonObject,
  schedulerValue: JsonObject,
  protocolActionFields: JsonObject,
): JsonObject {
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
  const packet = object(payload.protocol_action_packet);
  if (Object.keys(packet).length > 0 && Object.keys(object(payload.replan_action_packet)).length === 0) {
    const summary = text(packet.summary, 2_000);
    const derived = derivedProtocolActionPacketFields(action, user, capsule, schedulerValue);
    const residue: JsonObject = {};
    for (const [key, value] of Object.entries(protocolActionFields)) {
      if (JSON.stringify(derived[key]) !== JSON.stringify(value)) residue[key] = value;
    }
    const reconstructed: JsonObject = {};
    for (const key of Object.keys(protocolActionFields)) {
      reconstructed[key] = key in residue ? residue[key] : derived[key];
    }
    const verified = Boolean(summary) && renderProtocolActionPacketSummary(reconstructed) === summary;
    const projection: JsonObject = {
      schema_version: packet.schema_version,
      present: true,
      summary_hash: canonicalHash(summary || ""),
      derivation_status: verified
        ? Object.keys(residue).length > 0 ? "verified_with_residue" : "verified"
        : "unverified_retain_summary",
      reconstruction_verified: verified,
      llm_policy: PROTOCOL_ACTION_PACKET_LLM_POLICY,
      candidate_derivation_inputs: [
        "action", "user", "work_lane_contract", "automation_liveness", "scheduler",
      ],
    };
    if (Object.keys(residue).length > 0) projection.residue = residue;
    if (!verified) projection.summary = summary;
    capsule.protocol_action_packet = projection;
  }
  const warnings = ACTIONABLE_WARNING_FIELDS.filter((field) => Boolean(payload[field]));
  if (warnings.length > 0) capsule.actionable_warning_refs = warnings;
  return capsule;
}

function actionProjection(payload: JsonObject, protocolActionFields: JsonObject): JsonObject {
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
  const recommendedAction = replanPacket
    ? "apply replan_action_packet and emit one required semantic outcome"
    : text(turn.observation.recommended_action || payload.recommended_action, 480);
  const action: JsonObject = {
    recommended_action: recommendedAction,
    primary_action: replanPacket
      ? "produce one required semantic outcome"
      : text(agentChannel.primary_action, 480),
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
    action.planning_horizon = { ...turn.observation.planning_horizon };
  }
  const user = userChannel(interaction, payload);
  const schedulerValue = scheduler(payload, turn);
  let nextCliActions = capabilityIntent
    ? [scalarString(capabilityIntent.command, "pending capability intent command")]
    : [...turn.next_effect.cli_actions];
  if (nextCliActions.length === 0 && Array.isArray(cliChannel.next_cli_actions)) {
    nextCliActions = [...cliChannel.next_cli_actions].map(pythonString);
  }
  if (replanPacket) {
    const writebackContract = object(object(payload.replan_action_packet).writeback_contract);
    const successorCommand = writebackContract.successor_command;
    const refreshCommand = nextCliActions.find((item) => item.includes("refresh-state"));
    nextCliActions = [];
    if (successorCommand) {
      nextCliActions.push(
        scalarString(successorCommand, "replan_action_packet.writeback_contract.successor_command"),
      );
    }
    if (refreshCommand) nextCliActions.push(refreshCommand);
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
    writeback.next_cli_actions = textList(nextCliActions, 5, 420);
  }
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
  projection.contract_capsule = contractCapsule(
    payload, action, user, schedulerValue, protocolActionFields,
  );
  const context = object(interaction.agent_context);
  if (Object.keys(context).length > 0) projection.agent_context = context;
  const orchestration = object(payload.task_orchestration_contract);
  if (Object.keys(orchestration).length > 0) projection.task_orchestration_contract = orchestration;
  const plan = responsePlan(interaction);
  if (plan) projection.response_plan = plan;
  return projection;
}

function turnActionProjection(payload: JsonObject, protocolActionFields: JsonObject): JsonObject {
  const projection = actionProjection(payload, protocolActionFields);
  const action = object(projection.action);
  const horizon = object(action.planning_horizon);
  if (Object.keys(object(horizon.detail_refs)).length > 0) {
    const compactHorizon = { ...horizon };
    delete compactHorizon.detail_refs;
    compactHorizon.detail_refs_ref = PLANNING_HORIZON_DETAIL_REFS_REF;
    action.planning_horizon = compactHorizon;
    projection.action = action;
  }
  const context = object(projection.agent_context);
  // Guidance must not crowd out the actionable contract. Preserve a signed
  // content reference to the existing full-decision route under budget pressure.
  if (Object.keys(context).length > 0
    && Buffer.byteLength(JSON.stringify(projection), "utf8") > turnEnvelopeBudgetBytes(projection) - 1_400) {
    projection.agent_context = {
      schema_version: context.schema_version, phase: context.phase, scope: context.scope,
      target: "coordinator", authority: "guidance_only", delivery: "projected",
      content_hash: canonicalHash(context),
      detail_ref: "full_decision.interaction_contract.agent_context",
      instruction: "Use the envelope detail_ref command to read capability context before planning.",
    };
  }
  return projection;
}

function signatureCoverage(envelope: JsonObject, responsePlanValue: unknown): string {
  if (Object.keys(object(envelope.agent_context)).length > 0) return ACTION_SIGNATURE_COVERAGE_V4;
  const action = object(envelope.action);
  if (Object.keys(object(action.planning_horizon)).length > 0) return ACTION_SIGNATURE_COVERAGE_V3;
  if (Object.keys(object(action.action_portfolio)).length > 0) return ACTION_SIGNATURE_COVERAGE_V2;
  if (Object.keys(object(responsePlanValue)).length > 0) return ACTION_SIGNATURE_COVERAGE_V1;
  return ACTION_SIGNATURE_COVERAGE_V0;
}

export function turnEnvelopeActionSignatureDocument(value: unknown): JsonObject {
  const envelope = requireJsonObject(value, "turn_envelope");
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
  if (Object.keys(object(responsePlanValue)).length > 0) {
    signature.response_plan = { ...object(responsePlanValue) };
  }
  return signature;
}

export function quotaActionSignatureDocument(
  value: unknown,
  protocolActionFields: unknown = {},
): JsonObject {
  const payload = requireJsonObject(value, "quota payload");
  return turnEnvelopeActionSignatureDocument(
    turnActionProjection(payload, object(protocolActionFields)),
  );
}

function shellQuote(value: string): string {
  if (/^[A-Za-z0-9_@%+=:,./-]+$/u.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function commandPrefix(runtimeRoot: unknown): string {
  const runtimeRootText = scalarString(runtimeRoot, "quota payload runtime_root").trim();
  return runtimeRootText ? `loopx --runtime-root ${shellQuote(runtimeRootText)}` : "loopx";
}

function coldPath(
  payload: JsonObject,
  agentId: string | null,
  schedulerExecutionArgs: string,
): JsonObject {
  const goalId = scalarString(payload.goal_id, "quota payload goal_id", "<goal-id>");
  const agentArg = agentId ? ` --agent-id ${agentId}` : "";
  const prefix = commandPrefix(payload.runtime_root);
  return {
    full_decision: schedulerExecutionArgs
      ? `${prefix} --format json quota should-run --goal-id ${goalId}${agentArg}${schedulerExecutionArgs}`
      : "rerun the typed quota_guard from the current host packet",
    todo_detail: `${prefix} --format json todo list --goal-id ${goalId}`,
    status_detail: `${prefix} --format json status --goal-id ${goalId}`,
  };
}

export function buildTurnEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "turn envelope request");
  const payload = requireJsonObject(request.payload, "turn envelope payload");
  const protocolActionFields = requireJsonObject(
    request.protocol_action_fields,
    "turn envelope protocol_action_fields",
  );
  if (typeof request.scheduler_execution_args !== "string") {
    throw new EffectRuntimeRequestError(
      "turn envelope scheduler_execution_args must be a string",
    );
  }
  const schedulerExecutionArgs = request.scheduler_execution_args;
  const agentId = scalarString(
    object(payload.agent_identity).agent_id,
    "quota payload agent_identity.agent_id",
  ).trim() || null;
  const actionProjectionValue = turnActionProjection(payload, protocolActionFields);
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
    detail_ref: coldPath(payload, agentId, schedulerExecutionArgs),
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

export function evaluateTurnEnvelope(value: unknown): JsonObject {
  const request = requireJsonObject(value, "turn envelope request");
  const operation = request.operation;
  if (operation === "build") return buildTurnEnvelope(request);
  if (operation === "envelope_signature") {
    return turnEnvelopeActionSignatureDocument(request.envelope);
  }
  if (operation === "quota_signature") {
    return quotaActionSignatureDocument(
      request.payload,
      requireJsonObject(
        request.protocol_action_fields,
        "turn envelope protocol_action_fields",
      ),
    );
  }
  throw new EffectRuntimeRequestError("turn envelope operation is unsupported");
}
