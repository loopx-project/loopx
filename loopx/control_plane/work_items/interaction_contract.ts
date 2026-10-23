import {configuredGoalChecks} from "../goals/acceptance_contract.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {projectTurnStartUnavailableContext} from "../capability_hooks.ts";
import { createHash } from "node:crypto";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
  optionalNonEmptyString,
  jsonObject,
} from "../runtime_decode.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

import type { JsonObject } from "../effect_program.ts";
import { AgentScopeFrontierAction } from "../agents/agent_scope_frontier.generated.ts";
import { resumeConditionHasKnownPendingTarget } from "../todos/resume_condition.ts";
import { parseTodoTimestampMicros } from "../runtime_timestamp.ts";
import { monitorIsActive } from "../todos/summary_lanes.ts";

function exactBlockedSuccessorWait(request: JsonObject, monitorWait = false): JsonObject | null {
  const wait = jsonObject(request.vision_wait_state);
  const exact = (request.effective_action === AgentScopeFrontierAction.AGENT_SCOPE_WAIT ||
    monitorWait && request.effective_action === EffectiveAction.MONITOR_QUIET_SKIP) &&
    wait?.schema_version === "goal_vision_wait_state_v0" && wait.state === "waiting" &&
    wait.reason_code === "exact_blocked_successor" && wait.automatic_resume === true &&
    typeof wait.selected_todo_id === "string" && !!wait.selected_todo_id &&
    typeof wait.resume_when === "string" && !!wait.resume_when;
  return exact && wait ? wait : null;
}

function hasScheduledMonitorSource(condition: JsonObject): boolean {
  const time = (value: unknown): number | null => {
    const micros = typeof value === "string" ? parseTodoTimestampMicros(value) : null;
    return micros === null ? null : Number(micros) / 1_000_000;
  };
  const observed = time(condition.evaluated_at), due = time(condition.target_next_due_at);
  const expiry = time(condition.target_expires_at);
  if (condition.clock_provider !== "runtime_clock" || observed === null || due === null ||
      !(condition.target_archive_state === null || condition.target_archive_state === "active") ||
      condition.target_expires_at !== null && expiry === null) return false;
  // A Monitor must still be able to reach a poll. The ordinary quota owner
  // retains due-work priority; an expired or unscheduled source cannot keep
  // an unchanged external worker alive indefinitely.
  return monitorIsActive({expiresAt: expiry}, observed) && monitorIsActive({expiresAt: expiry}, due);
}

/** Share the resume owner's source proof; a Turn binding retains supervision. */
function pendingAutomaticSuccessor(request: JsonObject, wait: JsonObject): JsonObject | null {
  const receipt = jsonObject(request.heartbeat_receipt);
  const unbound = request.heartbeat_receipt == null || (!!receipt &&
    ["committed", "replayed"].includes(String(receipt.status)) &&
    !("settlement_identity" in receipt) && !("pending_action_selection" in receipt));
  const condition = jsonObject(wait.resume_condition);
  if (unbound && typeof request.agent_id === "string" && !!request.agent_id &&
      wait.agent_id === request.agent_id && wait.waiting_todo_count === 1 &&
      ["open", "deferred"].includes(String(wait.selected_todo_status)) &&
      (wait.selected_todo_claimed_by == null || wait.selected_todo_claimed_by === "" ||
        wait.selected_todo_claimed_by === request.agent_id) &&
      condition && ["resume_at", "todo_done", "monitor_changed"].includes(String(condition.kind))) {
    try {
      if (resumeConditionHasKnownPendingTarget(condition, {
        todo_id: wait.selected_todo_id, resume_when: wait.resume_when,
        resume_monitor_generation: wait.resume_monitor_generation,
      }) && (condition.kind !== "monitor_changed" || hasScheduledMonitorSource(condition))) return condition;
    } catch {
      // An incomplete or malformed source proof cannot relax the contract.
    }
  }
  return null;
}

/** A proven pending automatic successor needs only fresh source rechecks.
 * This read model never relaxes an existing Turn binding or settlement fence.
 * Unknown dependencies retain their bounded observation/replan policy. */
export function projectBlockedSuccessorWaitObservation(request: JsonObject): JsonObject {
  const wait = exactBlockedSuccessorWait(request);
  return {required: !!wait && pendingAutomaticSuccessor(request, wait) === null};
}

export const INTERACTION_CONTRACT_SCHEMA_VERSION =
  "loopx_interaction_contract_v0";
export const INTERACTION_EXECUTION_FACTS_SCHEMA_VERSION =
  "interaction_execution_facts_v0";
export const INTERACTION_EXECUTION_DECISION_SCHEMA_VERSION =
  "interaction_execution_decision_v0";

export const USER_CHANNEL_NOTIFICATION_POLICIES = ["NOTIFY", "DONT_NOTIFY"] as const;

// Interaction mode names the obligation; derive its existing compatibility
// spelling from the owner-generated effective-action value.
const EXTERNAL_OBSERVATION_MODE =
  EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE.replace("observe", "observation");

export interface UserInteractionChannel extends JsonObject {
  action_required: boolean;
  notify: (typeof USER_CHANNEL_NOTIFICATION_POLICIES)[number];
  non_blocking?: true;
  actions?: string[];
}

export type AgentInteractionChannel =
  | (JsonObject & {
      must_attempt: true;
      delivery_allowed: boolean;
      quiet_noop_allowed: false;
    })
  | (JsonObject & {
      must_attempt: false;
      delivery_allowed: false;
      quiet_noop_allowed: boolean;
    });

export interface InteractionContract extends JsonObject {
  schema_version: typeof INTERACTION_CONTRACT_SCHEMA_VERSION;
  mode: string;
  user_channel: UserInteractionChannel;
  agent_channel: AgentInteractionChannel;
  cli_channel: JsonObject;
}

const INTERACTION_CONSISTENCY_DIAGNOSTICS = {
  delivery: {code: "interaction_contract.delivery_without_attempt",
    message: "interaction_contract.agent_channel cannot allow delivery without an attempt"},
  quiet: {code: "interaction_contract.quiet_noop_conflicts_with_required_action",
    message: "interaction_contract quiet no-op conflicts with a required action"},
  terminal: {code: "interaction_contract.terminal_conflicts_with_open_action",
    message: "interaction_contract closed mode conflicts with an open action"},
  settled: {code: "interaction_contract.settled_conflicts_with_open_action",
    message: "interaction_contract settled mode conflicts with an open action"},
  spend: {code: "interaction_contract.settled_conflicts_with_spend",
    message: "interaction_contract settled mode conflicts with quota spend"},
} as const;

interface InteractionConsistencyFacts {
  mode: string;
  user_required: boolean;
  must_attempt: boolean;
  delivery_allowed: boolean;
  quiet_noop_allowed: boolean;
  spend_allowed_now: boolean;
  spend_after_validation: boolean;
}

interface PermissionFact {
  present: boolean;
  value: boolean | null;
}

export interface InteractionExecutionDecision extends JsonObject {
  schema_version: typeof INTERACTION_EXECUTION_DECISION_SCHEMA_VERSION;
  mode: string;
  must_attempt: boolean;
  delivery_allowed: boolean;
  quiet_noop_allowed: boolean;
  attempt_basis: string;
  delivery_basis: string;
  normalization_diagnostics: string[];
}

function optionalString(value: unknown, label: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value;
}

function permissionFact(
  source: JsonObject,
  field: string,
  label: string,
): PermissionFact {
  if (!Object.hasOwn(source, field)) return {present: false, value: null};
  const value = source[field];
  if (value === null) return {present: true, value: null};
  return {present: true, value: requireBoolean(value, label)};
}

function quietModeAllowsNoop(
  mode: string,
  agentScopeActions: ReadonlySet<string>,
): boolean {
  return agentScopeActions.has(mode) || [
    "monitor_quiet_skip",
    "mapped_noop_if_unchanged",
    "quota_throttled",
    "blocked_wait",
    "user_gate_cooldown_wait",
    "terminal_no_followup",
    "peer_coordination_blocked",
    "agent_monitor_only",
    "skip",
  ].includes(mode);
}

/**
 * One domain owner for the selected interaction mode and its execution facts.
 * Python transports bounded source facts and renders compatibility fields from
 * this decision; it does not infer a second attempt/delivery permission.
 */
export function projectInteractionExecution(value: unknown): InteractionExecutionDecision {
  const request = requireJsonObject(value, "interaction execution facts");
  if (request.schema_version !== INTERACTION_EXECUTION_FACTS_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `interaction_execution_facts.schema_version must be ${INTERACTION_EXECUTION_FACTS_SCHEMA_VERSION}`,
    );
  }
  const action = optionalString(request.effective_action, "effective_action") ?? "";
  const state = optionalString(request.state, "state") ?? "";
  const obligationValue = request.canonical_execution;
  if (obligationValue !== undefined && obligationValue !== null
      && (typeof obligationValue !== "object" || Array.isArray(obligationValue))) {
    throw new EffectRuntimeRequestError("canonical_execution must be an object or null");
  }
  const obligation = obligationValue == null ? {} : obligationValue as JsonObject;
  const attemptFact = permissionFact(
    obligation,
    "must_attempt_work",
    "canonical_execution.must_attempt_work",
  );
  const canonicalDelivery = permissionFact(
    obligation,
    "delivery_allowed",
    "canonical_execution.delivery_allowed",
  );
  const normalDelivery = permissionFact(
    request,
    "normal_delivery_allowed",
    "normal_delivery_allowed",
  );
  const recoveryDelivery = permissionFact(
    request,
    "recovery_delivery_allowed",
    "recovery_delivery_allowed",
  );
  const selfRepair = permissionFact(
    request,
    "self_repair_allowed",
    "self_repair_allowed",
  );
  const userRequired = requireBoolean(request.user_required, "user_required");
  const scopedFallback = requireBoolean(
    request.scoped_user_gate_fallback,
    "scoped_user_gate_fallback",
  );
  const notificationSuppressed = requireBoolean(
    request.user_gate_notification_suppressed,
    "user_gate_notification_suppressed",
  );
  const notifyUserOnGate = requireBoolean(
    request.notify_user_on_gate,
    "notify_user_on_gate",
  );
  const notifyUserOnOpenTodo = requireBoolean(
    request.notify_user_on_open_todo,
    "notify_user_on_open_todo",
  );
  const stopIfUnchanged = requireBoolean(
    request.stop_if_unchanged,
    "stop_if_unchanged",
  );
  const blockedSuccessorWaitObservation = requireBoolean(
    request.blocked_successor_wait_observation,
    "blocked_successor_wait_observation",
  );
  const observationRequired = requireBoolean(
    request.observation_required,
    "observation_required",
  );
  const replanRequired = requireBoolean(
    request.replan_required,
    "replan_required",
  );
  const agentScopeActions = new Set(
    requireStringArray(
      request.agent_scope_frontier_actions,
      "agent_scope_frontier_actions",
    ),
  );

  if (attemptFact.value === false && canonicalDelivery.value === true) {
    throw new EffectRuntimeRequestError(
      "canonical_execution.delivery_allowed cannot be true when must_attempt_work is false",
      "interaction_contract.delivery_without_attempt",
    );
  }

  let deliverySource = "none";
  let rawDelivery = false;
  if (canonicalDelivery.present) {
    rawDelivery = canonicalDelivery.value === true;
    deliverySource = canonicalDelivery.value === null ? "canonical_null" : "canonical";
  } else if (normalDelivery.value === true) {
    rawDelivery = true;
    deliverySource = "compatibility_normal";
  } else if (recoveryDelivery.value === true) {
    rawDelivery = true;
    deliverySource = "compatibility_recovery";
  } else if (selfRepair.value === true) {
    rawDelivery = true;
    deliverySource = "compatibility_self_repair";
  }
  let rawAttempt = attemptFact.value === true;
  if (action === EffectiveAction.GOVERNED_CAPABILITY_INTENT) {
    rawDelivery = rawAttempt;
    deliverySource = "canonical_attempt";
  }

  const fixedBeforeUserGate = ([
    EffectiveAction.HEARTBEAT_SETTLED_SKIP,
    EffectiveAction.GOVERNED_CAPABILITY_INTENT,
    EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY,
    EffectiveAction.AGENT_MONITOR_ONLY,
    EffectiveAction.MONITOR_DUE,
    EffectiveAction.TERMINAL_NO_FOLLOWUP,
    EffectiveAction.PEER_COORDINATION_BLOCKED,
    EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED,
  ] as readonly string[]).includes(action)
    || action === EffectiveAction.SCOPED_USER_GATE_FALLBACK || scopedFallback
    || notificationSuppressed;
  const userGateApplies = userRequired && !fixedBeforeUserGate;
  const boundedNotice = userGateApplies && rawAttempt && rawDelivery;

  let mustAttempt = rawAttempt;
  let deliveryAllowed = rawDelivery;
  if (userGateApplies && !boundedNotice) {
    mustAttempt = false;
    deliveryAllowed = false;
  }
  if (action === EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED) {
    mustAttempt = true;
  }
  if (blockedSuccessorWaitObservation) {
    mustAttempt = true;
  }

  let mode: string;
  if (action === EffectiveAction.HEARTBEAT_SETTLED_SKIP) {
    mode = "heartbeat_settled_skip";
  } else if (action === EffectiveAction.GOVERNED_CAPABILITY_INTENT) {
    mode = action;
  } else if (action === EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY) {
    mode = action;
  } else if (action === EffectiveAction.AGENT_MONITOR_ONLY) {
    mode = "agent_monitor_only";
  } else if (action === EffectiveAction.MONITOR_DUE) {
    mode = "monitor_due";
  } else if (action === EffectiveAction.TERMINAL_NO_FOLLOWUP || state === "terminal_no_followup") {
    mode = "terminal_no_followup";
  } else if (action === EffectiveAction.PEER_COORDINATION_BLOCKED) {
    mode = action;
  } else if (scopedFallback) {
    mode = "scoped_user_gate_fallback";
  } else if (notificationSuppressed) {
    mode = "user_gate_cooldown_wait";
  } else if (action === EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED) {
    mode = "automation_prompt_upgrade";
  } else if (userRequired) {
    if (boundedNotice) {
      mode = "bounded_delivery_with_user_notice";
    } else if (notifyUserOnGate || state === "operator_gate") {
      mode = "user_gate";
    } else if (notifyUserOnOpenTodo) {
      mode = "user_todo_blocker_push";
    } else {
      mode = "user_action_required";
    }
  } else if (observationRequired) {
    mode = EXTERNAL_OBSERVATION_MODE;
  } else if (replanRequired) {
    mode = "autonomous_replan";
  } else if (action === EffectiveAction.COORDINATE_TASK_BUNDLE) {
    mode = "task_orchestration";
  } else if (agentScopeActions.has(action)) {
    mode = action;
  } else if (action === EffectiveAction.MONITOR_QUIET_SKIP) {
    mode = "monitor_quiet_skip";
  } else if (recoveryDelivery.value === true || action === EffectiveAction.OUTCOME_FLOOR_RECOVERY) {
    mode = "outcome_floor_recovery";
  } else if (action === EffectiveAction.CAPABILITY_BRIDGE_REPAIR) {
    mode = "capability_bridge_repair";
  } else if (action === EffectiveAction.AGENT_WORKSPACE_REPAIR) {
    mode = action;
  } else if (action === EffectiveAction.BOUNDARY_PROJECTION_REPAIR) {
    mode = "boundary_projection_repair";
  } else if (selfRepair.value === true) {
    mode = "control_plane_self_repair";
  } else if (stopIfUnchanged) {
    mode = "mapped_noop_if_unchanged";
  } else if (deliveryAllowed) {
    mode = "bounded_delivery";
  } else if (state === "blocked_health") {
    mode = "health_blocked";
  } else if (state === "throttled") {
    mode = "quota_throttled";
  } else if (state === "waiting" || state === "focus_wait") {
    mode = "blocked_wait";
  } else {
    mode = "skip";
  }

  if (mode === "mapped_noop_if_unchanged") {
    deliveryAllowed = false;
    deliverySource = "mapped_noop";
  }
  deliveryAllowed = mustAttempt && deliveryAllowed;
  const normalizationDiagnostics: string[] = [];
  if (rawDelivery && !mustAttempt) {
    normalizationDiagnostics.push("interaction_contract.delivery_without_attempt");
  }
  const quietNoopAllowed = !userRequired && !mustAttempt && quietModeAllowsNoop(mode, agentScopeActions);
  const attemptBasis = blockedSuccessorWaitObservation
    ? "blocked_successor_observation"
    : action === EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED
      ? "automation_prompt_upgrade"
      : attemptFact.present
        ? "canonical"
        : "none";
  if (!deliveryAllowed && deliverySource !== "none") {
    deliverySource = mustAttempt ? deliverySource : "attempt_required";
  }

  return {
    schema_version: INTERACTION_EXECUTION_DECISION_SCHEMA_VERSION,
    mode,
    must_attempt: mustAttempt,
    delivery_allowed: deliveryAllowed,
    quiet_noop_allowed: quietNoopAllowed,
    attempt_basis: attemptBasis,
    delivery_basis: deliverySource,
    normalization_diagnostics: normalizationDiagnostics,
  };
}

/** The scheduler and typed consumers share these rules, not independent copies. */
function interactionConsistencyIssues(facts: InteractionConsistencyFacts) {
  const {mode, user_required, must_attempt, delivery_allowed, quiet_noop_allowed} = facts;
  const spendAllowed = facts.spend_allowed_now || facts.spend_after_validation;
  const issues: Array<(typeof INTERACTION_CONSISTENCY_DIAGNOSTICS)[keyof typeof INTERACTION_CONSISTENCY_DIAGNOSTICS]> = [];
  if (delivery_allowed && !must_attempt) issues.push(INTERACTION_CONSISTENCY_DIAGNOSTICS.delivery);
  if (quiet_noop_allowed && (must_attempt || delivery_allowed || user_required)) {
    issues.push(INTERACTION_CONSISTENCY_DIAGNOSTICS.quiet);
  }
  if ((mode === "terminal_no_followup" || mode === "peer_coordination_blocked" || mode === "agent_monitor_only")
      && (user_required || must_attempt || delivery_allowed || !quiet_noop_allowed || spendAllowed)) {
    issues.push(INTERACTION_CONSISTENCY_DIAGNOSTICS.terminal);
  }
  if (mode === "heartbeat_settled_skip") {
    if (user_required || must_attempt || delivery_allowed) issues.push(INTERACTION_CONSISTENCY_DIAGNOSTICS.settled);
    if (spendAllowed) issues.push(INTERACTION_CONSISTENCY_DIAGNOSTICS.spend);
  }
  return issues;
}

function schedulerDisposition(facts: InteractionConsistencyFacts, modes: readonly string[], noticeRetry: boolean): [string, string] {
  const {mode, user_required, must_attempt, quiet_noop_allowed} = facts;
  if (mode === "terminal_no_followup") return ["terminal_stop", mode];
  if (mode === "peer_coordination_blocked") return ["peer_coordination_wait", mode];
  if (mode === "agent_monitor_only") return ["agent_monitor_only_wait", mode];
  if (user_required && !must_attempt && noticeRetry) return ["quiet_wait", "blocked_wait"];
  if (user_required && !must_attempt) return ["human_gate", "interaction_blocking_user_gate"];
  if (mode === "monitor_quiet_skip") return ["monitor_wait", "interaction_monitor_quiet_wait"];
  if (mode === "heartbeat_settled_skip") return ["settled_replay", "interaction_heartbeat_settled_replay"];
  if (mode === "successor_replan_required" && must_attempt) return ["active_work", "interaction_successor_replan_required"];
  if (modes.includes(mode)) return ["agent_scope_wait", "interaction_agent_scope_wait"];
  if (mode === "mapped_noop_if_unchanged") return ["unchanged_wait", "interaction_unchanged_wait"];
  if (must_attempt) return ["active_work", "interaction_agent_attempt_required"];
  return ["quiet_wait", quiet_noop_allowed ? "interaction_quiet_noop_allowed" : "interaction_delivery_not_allowed"];
}

/** Preserve accumulated structural diagnostics before shared consistency rules;
 * malformed public contracts produce repair advice, never scheduler authority. */
export function projectSchedulerArbitration(value: unknown): JsonObject {
  const request = requireJsonObject(value, "scheduler arbitration facts");
  if (typeof request.mode !== "string") throw new EffectRuntimeRequestError("scheduler arbitration mode must be a string");
  const mode = request.mode;
  const user = requireJsonObject(request.user_channel, "user channel facts");
  const agent = requireJsonObject(request.agent_channel, "agent channel facts");
  const cli = requireJsonObject(request.cli_channel, "CLI channel facts");
  const userPresent = requireBoolean(user.present, "user channel presence");
  const agentPresent = requireBoolean(agent.present, "agent channel presence");
  const modes = requireStringArray(request.agent_scope_frontier_actions, "agent scope frontier actions");
  const errors: string[] = [];
  if (request.schema_version !== INTERACTION_CONTRACT_SCHEMA_VERSION) errors.push("interaction_contract.schema_version_mismatch");
  if (!mode) errors.push("interaction_contract.mode_missing");
  if (!userPresent) errors.push("interaction_contract.user_channel_missing");
  if (!agentPresent) errors.push("interaction_contract.agent_channel_missing");
  const flag = (channel: JsonObject, field: string, name: string): boolean => {
    if (typeof channel[field] === "boolean") return channel[field];
    errors.push(`interaction_contract.${name}.${field}_must_be_boolean`);
    return false;
  };
  const facts: InteractionConsistencyFacts = {
    mode, user_required: flag(user, "action_required", "user_channel"),
    must_attempt: flag(agent, "must_attempt", "agent_channel"),
    delivery_allowed: flag(agent, "delivery_allowed", "agent_channel"),
    quiet_noop_allowed: flag(agent, "quiet_noop_allowed", "agent_channel"),
    spend_allowed_now: Object.hasOwn(cli, "spend_allowed_now") ? flag(cli, "spend_allowed_now", "cli_channel") : false,
    spend_after_validation: Object.hasOwn(cli, "spend_after_validation") ? flag(cli, "spend_after_validation", "cli_channel") : false,
  };
  errors.push(...interactionConsistencyIssues(facts).map(issue => issue.code));
  const [disposition, reason] = schedulerDisposition(facts, modes,
    user.non_gating_notice === true && agent.wait_source === "turn_settlement");
  const wait = exactBlockedSuccessorWait(request, disposition === "monitor_wait");
  const waiting = disposition === "agent_scope_wait" &&
      request.effective_action === AgentScopeFrontierAction.AGENT_SCOPE_WAIT ||
    disposition === "monitor_wait" && request.effective_action === EffectiveAction.MONITOR_QUIET_SKIP;
  const condition = !errors.length && waiting &&
    !facts.must_attempt && !facts.user_required && facts.quiet_noop_allowed &&
    !facts.spend_allowed_now && !facts.spend_after_validation && wait
    ? pendingAutomaticSuccessor(request, wait) : null;
  // The resume-condition owner has verified both timestamps and a future target.
  // This private duration bounds host rechecks; it grants no execution/spend.
  const scheduled = disposition === "agent_scope_wait" && condition?.kind === "resume_at" ? {scheduled_recheck_after_seconds: Math.ceil(
    (Date.parse(String(condition.scheduled_for)) - Date.parse(String(condition.evaluated_at))) / 1000,
  )} : {};
  const dependency = condition && ["todo_done", "monitor_changed"].includes(String(condition.kind))
    ? {pending_dependency_wait: true} : {};
  return {mode, errors: [...new Set(errors)],
    disposition: errors.length ? "consistency_repair" : disposition,
    reason_code: errors.length ? "scheduler_interaction_contract_inconsistent" : reason, ...scheduled, ...dependency};
}

function decodeUserChannel(value: unknown): UserInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.user_channel");
  const actionRequired = requireBoolean(
    channel.action_required,
    "interaction_contract.user_channel.action_required",
  );
  const notify = requireStringLiteral(
    channel.notify,
    USER_CHANNEL_NOTIFICATION_POLICIES,
    "interaction_contract.user_channel.notify",
  );
  const nonBlocking = channel.non_blocking === undefined
    ? undefined
    : requireBoolean(
      channel.non_blocking,
      "interaction_contract.user_channel.non_blocking",
    );
  if (actionRequired && nonBlocking === true) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel cannot be both required and non-blocking",
    );
  }
  if (nonBlocking === false) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel.non_blocking must be true when present",
    );
  }
  const decoded: UserInteractionChannel = {
    ...channel,
    action_required: actionRequired,
    notify,
  };
  if (nonBlocking === true) decoded.non_blocking = true;
  if (channel.actions !== undefined) {
    decoded.actions = requireStringArray(
      channel.actions,
      "interaction_contract.user_channel.actions",
    );
  }
  return decoded;
}

function decodeAgentChannel(
  value: unknown,
  { userActionRequired }: { userActionRequired: boolean },
): AgentInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.agent_channel");
  const mustAttempt = requireBoolean(
    channel.must_attempt,
    "interaction_contract.agent_channel.must_attempt",
  );
  const deliveryAllowed = requireBoolean(
    channel.delivery_allowed,
    "interaction_contract.agent_channel.delivery_allowed",
  );
  const quietNoopAllowed = requireBoolean(
    channel.quiet_noop_allowed,
    "interaction_contract.agent_channel.quiet_noop_allowed",
  );
  const issues = interactionConsistencyIssues({mode: "", user_required: userActionRequired,
    must_attempt: mustAttempt, delivery_allowed: deliveryAllowed,
    quiet_noop_allowed: quietNoopAllowed, spend_allowed_now: false, spend_after_validation: false});
  if (issues.length) throw new EffectRuntimeRequestError(issues[0]!.message);
  if (mustAttempt) {
    return {
      ...channel,
      must_attempt: true,
      delivery_allowed: deliveryAllowed,
      quiet_noop_allowed: false,
    };
  }
  return {
    ...channel,
    must_attempt: false,
    delivery_allowed: false,
    quiet_noop_allowed: quietNoopAllowed,
  };
}

/** Decode the final host-facing interaction decision at the existing Effect boundary. */
export function decodeInteractionContract(value: unknown): InteractionContract {
  const contract = requireJsonObject(value, "interaction_contract");
  if (contract.schema_version !== INTERACTION_CONTRACT_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `interaction_contract.schema_version must be ${INTERACTION_CONTRACT_SCHEMA_VERSION}`,
    );
  }
  const userChannel = decodeUserChannel(contract.user_channel);
  const agentChannel = decodeAgentChannel(
    contract.agent_channel,
    { userActionRequired: userChannel.action_required },
  );
  const mode = requireNonEmptyString(contract.mode, "interaction_contract.mode");
  const cliChannel = requireJsonObject(contract.cli_channel, "interaction_contract.cli_channel");
  const issues = interactionConsistencyIssues({mode, user_required: userChannel.action_required,
    must_attempt: agentChannel.must_attempt, delivery_allowed: agentChannel.delivery_allowed,
    quiet_noop_allowed: agentChannel.quiet_noop_allowed,
    spend_allowed_now: cliChannel.spend_allowed_now === undefined ? false
      : requireBoolean(cliChannel.spend_allowed_now, "interaction_contract.cli_channel.spend_allowed_now"),
    spend_after_validation: cliChannel.spend_after_validation === undefined ? false
      : requireBoolean(cliChannel.spend_after_validation, "interaction_contract.cli_channel.spend_after_validation")});
  if (issues.length) throw new EffectRuntimeRequestError(issues[0]!.message);
  return {
    ...contract,
    schema_version: INTERACTION_CONTRACT_SCHEMA_VERSION,
    mode,
    user_channel: userChannel,
    agent_channel: agentChannel,
    cli_channel: cliChannel,
  };
}

function readArgument(value: unknown, label: string): string | null {
  const text = optionalNonEmptyString(value, label);
  if (text?.includes("\0")) throw new EffectRuntimeRequestError(`${label} contains NUL`);
  return text;
}

function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./:-]+$/.test(value) ? value : `'${value.replaceAll("'", `'"'"'`)}'`;
}

/** The adaptive primary is the work identity for both reads and settlement. */
export function selectedWorkTodoId(packet: JsonObject): string | null {
  const orchestration = jsonObject(packet.task_orchestration_contract) ?? {};
  const primaryTodoId = typeof orchestration.primary_todo_id === "string"
    ? orchestration.primary_todo_id.trim() : "";
  if (orchestration.schema_version === "task_orchestration_contract_v2"
      && orchestration.mode === "adaptive" && primaryTodoId) return primaryTodoId;
  const selected = jsonObject(packet.selected_todo) ?? {};
  return typeof selected.todo_id === "string" && selected.todo_id.trim() ? selected.todo_id : null;
}

/** Generate the shared pre-work reads after final admission. Hosts consume this
 * list; transport projections must not independently infer requirements.
 * Python supplies registered source routing, never a second admission rule.
 */
function interactionRequiredReads(request: JsonObject): JsonObject {
  if (!Array.isArray(request.required_reads)) {
    throw new EffectRuntimeRequestError("required_reads must be an array");
  }
  const reads = request.required_reads.map(value => {
    const read = requireJsonObject(value, "required read");
    readArgument(read.command, "required read command");
    return {...read};
  }).filter(read => read.command);
  const shouldRun = requireBoolean(request.should_run, "should_run");
  const deliveryAllowed = requireBoolean(request.delivery_allowed, "delivery_allowed");
  const selectionRequired = requireBoolean(request.selection_required, "selection_required");
  const hasReplan = requireBoolean(request.has_replan, "has_replan");
  const settlementOnly = requireBoolean(request.settlement_only, "settlement_only");
  const checksPresent = requireBoolean(request.goal_acceptance_checks_present, "goal_acceptance_checks_present");
  const goalId = readArgument(request.goal_id, "goal_id");
  if (!shouldRun || !deliveryAllowed || selectionRequired || settlementOnly || !goalId
      || request.effective_action === EffectiveAction.GOVERNED_CAPABILITY_INTENT) {
    return {required_reads: reads};
  }
  const prefix = requireNonEmptyString(request.command_prefix, "command_prefix");
  const goalArg = shellQuote(goalId);
  const add = (command: string, source: string, reason: string) => {
    const existing = reads.find(read => read.command === command);
    if (!existing) {
      reads.push({command, source, reason, ordering: "before_work"});
      return;
    }
    // One command is one read, but it may carry multiple independently
    // applicable obligations. Merge guidance without duplicating replay.
    const current = typeof existing.reason === "string" ? existing.reason : "";
    if (!current) existing.reason = reason;
    else if (!current.includes(reason)) existing.reason = `${current} ${reason}`;
  };
  const stateFile = readArgument(request.goal_state_file, "goal_state_file");
  if (stateFile) add(`cat -- ${shellQuote(stateFile)}`, "goal_state",
    "Read complete Goal intent, acceptance and stops. A registered source may deliver all text outside explicitly generated Todo regions inline, with full-file revision and excluded-region lineage; exact canonical Todo reads own task state/claims. Otherwise consume this full progressive read. Source failure or changed requirements require a fresh guard; summaries cannot replace either read.");
  if (request.goal_objective_present === true) add(`${prefix} --format json goal-acceptance inspect --goal-id ${goalArg}`,
    "goal_acceptance", "Use the canonical goal_objective before the older source text. Only if your adoption is missing/stale, review your own work, reconcile Todo/Vision changes, then use adopt-objective with your Agent id, exact revision/digest, own objective_work_digest and review_summary. New execution is held until your adoption. Other peers adopt independently; existing Turn settlement/release remains available. Preserve root acceptance, permissions and stops. Adopt once per objective revision, never per Turn or work/peer update. Reuse complete inline context without extra reads/writes.");
  if (checksPresent || request.goal_outcomes_present === true) add(`${prefix} --format json goal-acceptance inspect --goal-id ${goalArg}`,
    "goal_acceptance", request.goal_outcomes_present === true ? "Read the full acceptance basis and any supporting outcomes with cumulative requirements, work links and sources. Preserve the original Goal and earlier requirements when applying direct user refinements. Before switching work or declaring completion, reconcile unmet outcome requirements with subsequent work; batch changed requirements/evidence reviews through refresh-state --goal-outcome-update-json or goal-acceptance refine. Reuse complete inline context; unchanged work needs no extra inspect/write. Agent review is distinct from independent checks. Scoped acceptance never replaces Goal intent. Changed requirements need a fresh guard." : "Read full configured objective, non-goals and criteria with scope/revision. Scoped acceptance does not replace Goal intent or prove completion; Removed or changed checks require a fresh guard.");
  const todoId = hasReplan ? null : selectedWorkTodoId(request);
  if (todoId) add(`${prefix} --format json todo list --goal-id ${goalArg} --todo-id ${shellQuote(todoId)}`,
    "selected_todo", "Read full current requirements and status/claim. Require one matching active Todo; missing, ambiguous or changed work requires a fresh guard. A summary cannot replace this read.");
  return {required_reads: reads};
}

export function projectInteractionRequiredReads(request: JsonObject): JsonObject {
  return {...interactionRequiredReads(request), arbitration: projectSchedulerArbitration(request.arbitration_facts)};
}

/** Context routing reuses the admitted work identity; User scope remains owned
 * by the canonical Todo reader. No content confers execution authority. */
export function planInteractionWorkContext(request: JsonObject): JsonObject {
  const packet = requireJsonObject(request.packet, "packet");
  const interaction = jsonObject(packet.interaction_contract) ?? {};
  const cli = jsonObject(interaction.cli_channel) ?? {};
  const todoId = selectedWorkTodoId(packet);
  const selected = jsonObject(packet.selected_todo) ?? {};
  return {todo_id: todoId,
    selected_todo: selected.todo_id === todoId ? selected : {todo_id: todoId},
    read_user_todos: cli.selection_required !== true
      && packet.effective_action !== EffectiveAction.HEARTBEAT_SETTLED_SKIP};
}

/** The registered file adapter reuses the writer's strict region parser.
 * No heading/prose classification here: its exact complement is Goal context,
 * and canonical Todo readers retain task authority. Unknown/legacy input keeps
 * the complete progressive read rather than pretending to fulfill it. */
function completeGoalNarrative(content: JsonObject): boolean {
  if (!Array.isArray(content.excluded_todo_regions) || !content.excluded_todo_regions.length
      || typeof content.text !== "string" || typeof content.state_file !== "string" || !content.state_file
      || typeof content.revision !== "string" || !ENVELOPED_SHA256_PATTERN.test(content.revision)) return false;
  let end = 0;
  return content.excluded_todo_regions.every(value => {
    const region = jsonObject(value);
    if (!region || !["user", "agent", "archive"].includes(String(region.role))
        || !Number.isSafeInteger(region.start_line) || !Number.isSafeInteger(region.end_line)
        || Number(region.start_line) <= end || Number(region.end_line) < Number(region.start_line)
        || typeof region.revision !== "string" || !ENVELOPED_SHA256_PATTERN.test(region.revision)) return false;
    end = Number(region.end_line);
    return true;
  });
}

/** Deliver full registered sources once. Unowned mixed Goal documents remain
 * progressive reads; failed/changed sources forbid dependent work. */
export function projectInteractionWorkContext(request: JsonObject): JsonObject {
  if (!Array.isArray(request.required_reads) || !Array.isArray(request.source_results)) {
    throw new EffectRuntimeRequestError("context reads and source results must be arrays");
  }
  const results = request.source_results.map(value => requireJsonObject(value, "context source"));
  const selected = jsonObject(request.selected_todo) ?? {};
  const pending: JsonObject[] = [], sources: JsonObject[] = [], failures: JsonObject[] = [];
  let selectedTodoRef = false;
  let selectedTodoAuthority: string | null = null;
  let selectedTodoTextVerified = false;
  for (const value of request.required_reads) {
    const read = requireJsonObject(value, "required read");
    const matches = results.filter(result => result.command === read.command);
    if (matches.length === 0) { pending.push(read); continue; }
    const result = matches[0];
    const content = jsonObject(result.content);
    let valid = matches.length === 1 && content !== null && content.ok !== false && !result.error_code;
    let selectedTodoRecord: JsonObject | null = null;
    if (valid && read.source === "selected_todo") {
      const todo = jsonObject(content!.todo) ?? {};
      selectedTodoRecord = todo;
      const bodyMatches = typeof selected.text === "string" && todo.text === selected.text;
      const hasSnapshot = typeof selected._context_text_sha256 === "string";
      const snapshotMatches = hasSnapshot
        && typeof todo.text === "string"
        && selected._context_text_sha256 === createHash("sha256").update(todo.text, "utf8").digest("hex");
      const sourceMatchesSelection = hasSnapshot
        ? snapshotMatches
        : bodyMatches || selected._context_text_display_only === true;
      valid = content!.matched === true && content!.ambiguous !== true
        && todo.todo_id === selected.todo_id && todo.archive_state !== "archive"
        && todo.status !== "done"
        && ["status", "claimed_by"].every(field => selected[field] === undefined || selected[field] === todo[field])
        && typeof selected.content_revision === "string"
        && ENVELOPED_SHA256_PATTERN.test(selected.content_revision)
        && selected.content_revision === todo.content_revision
        && sourceMatchesSelection;
    }
    if (valid && read.source === "goal_acceptance") {
      valid = (!!configuredGoalChecks(jsonObject(content!.goal_acceptance_contract)) || !!jsonObject(content!.goal_acceptance_contract)?.outcome_requirements || !!jsonObject(content!.goal_acceptance_contract)?.goal_objective);
    }
    if (!valid) {
      pending.push(read);
      failures.push({source: read.source ?? read.kind, command: read.command,
        error_code: "context_source_unavailable", instruction: "Recover source and rerun the guard; cached context cannot authorize work."});
    } else if (content!.read_complete === false) {
      // A registered private agenda makes unfinished work visible each Turn,
      // but cannot fulfill the full source read or replace its requirements.
      sources.push({...read, content: content!});
      pending.push(read);
    } else if (read.source === "goal_state") {
      if (completeGoalNarrative(content!)) sources.push({...read, content: content!});
      else {
        pending.push(read);
        if (Object.hasOwn(content!, "excluded_todo_regions")) failures.push({
          source: read.source, command: read.command, error_code: "context_source_unavailable",
          instruction: "Recover complete Goal source and rerun the guard; malformed region lineage cannot fulfill it.",
        });
      }
    } else {
      if (read.source === "selected_todo" && typeof selectedTodoRecord?.text === "string") {
        // Bind completion to the admitted full source, before body deduplication
        // or display compaction. Provider-wide revisions also change for notes
        // and unrelated work; those do not replace this task's requirements.
        selectedTodoTextVerified = true;
      }
      if (read.source === "selected_todo" && typeof selected.text === "string"
          && selectedTodoRecord?.text === selected.text
          && selectedTodoRecord?.todo_id === selected.todo_id) {
        // Reuse only the duplicate body. The exact canonical record may carry
        // continuation, relations and authority metadata absent from the hot view.
        selectedTodoRef = true;
        const sourceTodo = Object.fromEntries(Object.entries(selectedTodoRecord)
          .filter(([key, value]) => {
            if (key === "text" || key === "todo_id" || value === null || value === undefined) return false;
            const selectedRole = selected.role;
            const roleSection = selectedRole === "agent" ? "Agent Todo"
              : selectedRole === "user" ? "User Todo" : null;
            if ((key === "archive_state" && value === "active")
                || (key === "done" && value === false)
                || (key === "source_section" && value === roleSection)) return false;
            const selectedValue = selected[key];
            return selectedValue === undefined
              || JSON.stringify(selectedValue) !== JSON.stringify(value);
          })) as JsonObject;
        const fullAuthorityRead = jsonObject(content!.authority_read);
        const authorityRead = fullAuthorityRead && typeof fullAuthorityRead.source_authority === "string"
          ? `${fullAuthorityRead.source_authority}@${String(fullAuthorityRead.provider_revision ?? "unknown")}`
          : fullAuthorityRead;
        if (Object.keys(sourceTodo).length === 0) {
          selectedTodoAuthority = typeof authorityRead === "string"
            ? authorityRead
            : JSON.stringify(authorityRead ?? {}) ?? "{}";
        } else {
          sources.push({source: read.source ?? "selected_todo", content: {
            todo: sourceTodo,
            ...(fullAuthorityRead ? {authority_read: fullAuthorityRead} : {}),
          }});
        }
      } else {
        sources.push({...read, content: content!});
      }
    }
  }
  const dispatch = jsonObject(request.hook_dispatch);
  const unavailable = projectTurnStartUnavailableContext(dispatch);
  const users = jsonObject(request.user_todos);
  if (users?.error_code) failures.push({source: "user_todos", error_code: users.error_code,
    instruction: "Recover current User obligations and rerun the guard."});
  const includeUserTodos = users && !users.error_code
    && (!Array.isArray(users.todos) || users.todos.length > 0 || users.authority_read != null);
  return {required_reads: pending, work_context: {complete: failures.length === 0,
    ...(selectedTodoTextVerified ? {selected_todo_text_verified: true} : {}),
    ...(selectedTodoRef ? {selected_todo_ref: "selected_todo"} : {}),
    ...(selectedTodoAuthority ? {selected_todo_authority: selectedTodoAuthority} : {}),
    ...(sources.length ? {sources} : {}),
    ...(unavailable ? {unavailable_context: unavailable} : {}),
    ...(includeUserTodos ? {user_todos: users} : {}), ...(failures.length ? {failures} : {}),
    instruction: "Read current sources and pending required_reads before work. Do not repeat this guard's fulfilled pre-work reads. " +
      "Use a bounded working brief of evidenced progress/change, current blockers, next work, acceptance and stops. " +
      "Missing comparison evidence is unknown, not unchanged; summaries cannot replace full requirements. " +
      "Recheck freshness before later actions; unavailable or changed sources need recovery and a fresh guard. Context grants no authority."}};
}
