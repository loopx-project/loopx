import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean as requiredBoolean,
  requireInteger as requiredInteger,
  requireJsonObject as requiredObject,
  requireNonEmptyString as requiredString,
} from "../runtime_decode.ts";
import {
  normalizeSchedulerHostUpdateFailures,
  normalizeSchedulerRrule,
  retainedSchedulerHostUpdateFailures,
  rruleForMinutes,
  schedulerRruleIntervalMinutes,
  schedulerTimestampMilliseconds,
} from "./state_store.ts";

export const SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA =
  "loopx_scheduler_state_transition_request_v0";
export const SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA =
  "loopx_scheduler_state_transition_result_v0";

export const SCHEDULER_CADENCE_TRANSITIONS = [
  "initial",
  "identity_reset",
  "retry_unacknowledged_failure",
  "hold_active_initial",
  "advance_after_interval",
  "hold_until_interval",
] as const;
export type SchedulerCadenceTransition =
  (typeof SCHEDULER_CADENCE_TRANSITIONS)[number];

export const SCHEDULER_HOST_TRANSITIONS = [
  "apply_required",
  "host_match_ack_required",
  "recorded_failure_suppressed",
  "settled",
] as const;
export type SchedulerHostTransition =
  (typeof SCHEDULER_HOST_TRANSITIONS)[number];

export interface SchedulerCadenceTransitionResult extends JsonObject {
  schema_version: typeof SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA;
  operation: "cadence";
  current_index: number;
  state_status: "missing" | "reset_required" | "same_identity";
  transition: SchedulerCadenceTransition;
  current_cadence_acknowledged: boolean;
}

export interface SchedulerHostTransitionResult extends JsonObject {
  schema_version: typeof SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA;
  operation: "host";
  transition: SchedulerHostTransition;
  current_target_has_failure: boolean;
  repeated_failed_pair: boolean;
}

export interface SchedulerBackoffTransitionResult extends JsonObject {
  schema_version: typeof SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA;
  operation: "backoff";
  current_index: number;
  current_interval_minutes: number;
  current_rrule: string;
  last_applied_rrule: string;
  observed_host_rrule: string;
  effective_host_rrule: string;
  state_status: "missing" | "reset_required" | "same_identity";
  cadence_transition: SchedulerCadenceTransition;
  current_cadence_acknowledged: boolean;
  all_host_update_failures: JsonObject[];
  host_update_failures: JsonObject[];
  recorded_host_failure: JsonObject | null;
  current_rrule_already_applied: boolean;
  scheduler_state_acknowledges_current_rrule: boolean;
  host_transition: SchedulerHostTransition;
  current_target_has_failure: boolean;
  repeated_failed_pair: boolean;
}

export interface SchedulerMonitorScheduleResult extends JsonObject {
  schema_version: typeof SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA;
  operation: "monitor_schedule";
  next_due_at: string | null;
  schedule_source: "explicit" | "cadence" | "none";
  cadence_seconds: number | null;
}

export interface SchedulerSettledReplayResult extends JsonObject {
  schema_version: typeof SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA;
  operation: "settled_replay";
  hint: JsonObject & {
    action: "preserve_current_schedule";
    cadence_class: "settled_turn";
    next_trigger: "fresh_turn_identity";
    app_automation: JsonObject & {
      apply: "none";
      host_action: "none";
      ack_required: false;
      observed_host_rrule: string | null;
    };
  };
}

export type SchedulerStateTransitionResult =
  | SchedulerCadenceTransitionResult
  | SchedulerHostTransitionResult
  | SchedulerBackoffTransitionResult
  | SchedulerMonitorScheduleResult
  | SchedulerSettledReplayResult;

function projectSettledReplay(request: JsonObject): SchedulerSettledReplayResult {
  const observedRrule = optionalRequestString(request.observed_host_rrule, "observed_host_rrule");
  // A closed receipt owns this Turn only. It cannot classify the live frontier
  // as waiting, reset a scheduler identity, or authorize another host effect.
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "settled_replay",
    hint: {
      schema_version: "scheduler_hint_v0",
      source: "quota.should-run",
      action: "preserve_current_schedule",
      cadence_class: "settled_turn",
      reason_code: "interaction_heartbeat_settled_replay",
      reason: "This Turn is settled, not the Goal frontier. Preserve the existing schedule and evaluate work under a fresh Turn identity.",
      spend_policy: "no quota spend for settled Turn replay",
      next_trigger: "fresh_turn_identity",
      app_automation: {
        apply: "none",
        host_action: "none",
        ack_required: false,
        observed_host_rrule: observedRrule || null,
        no_spend_for_cadence_change: true,
      },
      unchanged_poll: {
        limits: { local_scheduler: null, codex_cli_tui: null, claude_code_loop: null },
        after_limits: { local_scheduler: "continue", codex_cli_tui: "continue", claude_code_loop: "continue" },
        final_quota_replan_check_enabled: false,
        final_quota_replan_check_action: null,
        spend_policy: "no quota spend for settled Turn replay",
      },
    },
  };
}

const MONITOR_CADENCE_PATTERN =
  /^\s*(?<count>[1-9][0-9]{0,4})\s*(?<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*$/i;

function optionalRequestString(value: unknown, label: string): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string when present`);
  }
  return value.trim();
}

function monitorCadenceSeconds(value: unknown): number | null {
  const cadence = optionalRequestString(value, "monitor cadence");
  if (!cadence) return null;
  const match = MONITOR_CADENCE_PATTERN.exec(cadence);
  if (!match?.groups) return null;
  const count = Number.parseInt(match.groups.count, 10);
  const unit = match.groups.unit.toLowerCase();
  const multiplier = unit.startsWith("s")
    ? 1
    : unit.startsWith("m")
    ? 60
    : unit.startsWith("h")
    ? 60 * 60
    : 24 * 60 * 60;
  return count * multiplier;
}

function monitorScheduleTimestamp(milliseconds: number): string {
  return new Date(milliseconds).toISOString().replace(".000Z", "Z");
}

function evaluateMonitorSchedule(
  request: JsonObject,
): SchedulerMonitorScheduleResult {
  const explicitNextDueAt = optionalRequestString(
    request.explicit_next_due_at,
    "explicit_next_due_at",
  );
  if (explicitNextDueAt) {
    if (schedulerTimestampMilliseconds(explicitNextDueAt) === null) {
      throw new EffectRuntimeRequestError("explicit_next_due_at must be an ISO timestamp");
    }
    return {
      schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
      operation: "monitor_schedule",
      next_due_at: explicitNextDueAt,
      schedule_source: "explicit",
      cadence_seconds: monitorCadenceSeconds(request.cadence),
    };
  }

  const cadenceSeconds = monitorCadenceSeconds(request.cadence);
  if (cadenceSeconds === null) {
    return {
      schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
      operation: "monitor_schedule",
      next_due_at: null,
      schedule_source: "none",
      cadence_seconds: null,
    };
  }
  const generatedAt = requiredString(request.generated_at, "generated_at");
  const generatedAtMilliseconds = schedulerTimestampMilliseconds(generatedAt);
  if (generatedAtMilliseconds === null) {
    throw new EffectRuntimeRequestError("generated_at must be an ISO timestamp");
  }
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "monitor_schedule",
    next_due_at: monitorScheduleTimestamp(
      generatedAtMilliseconds + cadenceSeconds * 1_000,
    ),
    schedule_source: "cadence",
    cadence_seconds: cadenceSeconds,
  };
}

function requiredPositiveIntegerList(value: unknown, label: string): number[] {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.some((item) =>
      typeof item !== "number" || !Number.isInteger(item) || item <= 0
    )
  ) {
    throw new EffectRuntimeRequestError(`${label} must be a non-empty array of positive integers`);
  }
  return [...value] as number[];
}

function requestObject(value: unknown): JsonObject {
  const request = requiredObject(value, "scheduler.state_transition params");
  if (request.schema_version !== SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError("Scheduler state transition request schema mismatch");
  }
  return request;
}

function evaluateCadence(request: JsonObject): SchedulerCadenceTransitionResult {
  const progressionSize = requiredInteger(
    request.progression_size,
    "progression_size",
  );
  if (progressionSize < 1) {
    throw new EffectRuntimeRequestError("scheduler cadence progression must not be empty");
  }
  if (!requiredBoolean(request.state_present, "state_present")) {
    return {
      schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
      operation: "cadence",
      current_index: 0,
      state_status: "missing",
      transition: "initial",
      current_cadence_acknowledged: false,
    };
  }
  if (!requiredBoolean(request.identity_matches, "identity_matches")) {
    return {
      schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
      operation: "cadence",
      current_index: 0,
      state_status: "reset_required",
      transition: "identity_reset",
      current_cadence_acknowledged: false,
    };
  }

  const appliedIndex = requiredInteger(request.applied_index, "applied_index");
  const acknowledged = requiredBoolean(
    request.current_cadence_acknowledged,
    "current_cadence_acknowledged",
  );
  let nextIndex: number;
  let transition: SchedulerCadenceTransition;
  if (requiredBoolean(request.has_host_update_failures, "has_host_update_failures") &&
    !acknowledged) {
    nextIndex = appliedIndex;
    transition = "retry_unacknowledged_failure";
  } else if (!requiredBoolean(request.advance_same_identity, "advance_same_identity")) {
    nextIndex = 0;
    transition = "hold_active_initial";
  } else if (requiredBoolean(request.applied_interval_elapsed, "applied_interval_elapsed")) {
    nextIndex = appliedIndex + 1;
    transition = "advance_after_interval";
  } else {
    nextIndex = appliedIndex;
    transition = "hold_until_interval";
  }
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "cadence",
    current_index: Math.min(Math.max(nextIndex, 0), progressionSize - 1),
    state_status: "same_identity",
    transition,
    current_cadence_acknowledged: acknowledged,
  };
}

function evaluateHost(request: JsonObject): SchedulerHostTransitionResult {
  const stateStatus = requiredString(request.state_status, "state_status");
  const currentTargetHasFailure = requiredBoolean(
    request.current_target_has_failure,
    "current_target_has_failure",
  );
  const repeatedFailedPair = requiredBoolean(
    request.repeated_failed_pair,
    "repeated_failed_pair",
  );
  let transition: SchedulerHostTransition;
  if (
    requiredBoolean(request.observed_host_rrule_present, "observed_host_rrule_present") &&
    requiredBoolean(request.current_rrule_already_applied, "current_rrule_already_applied") &&
    (!requiredBoolean(
      request.scheduler_state_acknowledges_current_rrule,
      "scheduler_state_acknowledges_current_rrule",
    ) || stateStatus !== "same_identity" || currentTargetHasFailure)
  ) {
    transition = "host_match_ack_required";
  } else if (
    requiredBoolean(request.current_rrule_already_applied, "current_rrule_already_applied") &&
    stateStatus === "same_identity"
  ) {
    transition = "settled";
  } else if (repeatedFailedPair) {
    transition = "recorded_failure_suppressed";
  } else {
    transition = "apply_required";
  }
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "host",
    transition,
    current_target_has_failure: currentTargetHasFailure,
    repeated_failed_pair: repeatedFailedPair,
  };
}

function schedulerAppliedIndex(
  progressionMinutes: readonly number[],
  schedulerState: JsonObject,
): number {
  const value = schedulerState.progression_index;
  return typeof value === "number" &&
      Number.isInteger(value) &&
      value >= 0 &&
      value < progressionMinutes.length
    ? value
    : -1;
}

function schedulerProgressionIntervalElapsed(
  schedulerState: JsonObject,
  currentTime: string,
): boolean {
  const updatedAt = schedulerTimestampMilliseconds(schedulerState.updated_at);
  const current = schedulerTimestampMilliseconds(currentTime);
  const appliedInterval = schedulerRruleIntervalMinutes(
    schedulerState.last_applied_rrule,
  );
  if (updatedAt === null || current === null || appliedInterval === null) {
    return true;
  }
  return current >= updatedAt + appliedInterval * 60_000;
}

function monitorRruleAppliedWithinStaleTolerance(
  cadenceClass: string,
  lastAppliedRrule: string,
  currentRrule: string,
  toleranceMinutes: number,
): boolean {
  if (cadenceClass !== "monitor_wait") return false;
  const appliedMinutes = schedulerRruleIntervalMinutes(lastAppliedRrule);
  const currentMinutes = schedulerRruleIntervalMinutes(currentRrule);
  return appliedMinutes !== null &&
    currentMinutes !== null &&
    appliedMinutes >= currentMinutes &&
    appliedMinutes - currentMinutes <= toleranceMinutes;
}

function evaluateBackoff(request: JsonObject): SchedulerBackoffTransitionResult {
  const progressionMinutes = requiredPositiveIntegerList(
    request.progression_minutes,
    "progression_minutes",
  );
  const schedulerState = requiredObject(
    request.scheduler_state,
    "scheduler_state",
  );
  const resetToken = requiredString(request.reset_token, "reset_token");
  const identitySignature = requiredString(
    request.identity_signature,
    "identity_signature",
  );
  const currentTime = requiredString(request.current_time, "current_time");
  const cadenceClass = requiredString(request.cadence_class, "cadence_class");
  const staleToleranceMinutes = requiredInteger(
    request.stale_tolerance_minutes,
    "stale_tolerance_minutes",
  );
  if (staleToleranceMinutes < 0) {
    throw new EffectRuntimeRequestError("stale_tolerance_minutes must not be negative");
  }

  const allHostUpdateFailures = retainedSchedulerHostUpdateFailures(
    normalizeSchedulerHostUpdateFailures(
      schedulerState.host_update_failures,
      schedulerState.host_update_failure,
    ),
    currentTime,
  );
  const appliedIndex = schedulerAppliedIndex(
    progressionMinutes,
    schedulerState,
  );
  const appliedTargetRrule = appliedIndex >= 0
    ? rruleForMinutes(progressionMinutes[appliedIndex])
    : "";
  const lastAppliedRrule = typeof schedulerState.last_applied_rrule === "string"
    ? schedulerState.last_applied_rrule.trim()
    : "";
  const currentCadenceAcknowledged = Boolean(appliedTargetRrule) &&
    normalizeSchedulerRrule(lastAppliedRrule) === appliedTargetRrule;
  const cadence = evaluateCadence({
    progression_size: progressionMinutes.length,
    state_present: Object.keys(schedulerState).length > 0,
    identity_matches: Object.keys(schedulerState).length > 0 &&
      schedulerState.reset_token === resetToken &&
      schedulerState.identity_signature === identitySignature,
    applied_index: appliedIndex,
    current_cadence_acknowledged: currentCadenceAcknowledged,
    advance_same_identity: requiredBoolean(
      request.advance_same_identity,
      "advance_same_identity",
    ),
    applied_interval_elapsed: schedulerProgressionIntervalElapsed(
      schedulerState,
      currentTime,
    ),
    has_host_update_failures: allHostUpdateFailures.length > 0,
  });
  const currentIndex = cadence.current_index;
  const currentInterval = progressionMinutes[currentIndex];
  const currentRrule = rruleForMinutes(currentInterval);
  const observedHostRrule = normalizeSchedulerRrule(
    request.observed_host_rrule,
  );
  const effectiveHostRrule = observedHostRrule || lastAppliedRrule;
  const hostUpdateFailures = retainedSchedulerHostUpdateFailures(
    allHostUpdateFailures,
    currentTime,
    effectiveHostRrule,
  );
  const recordedHostFailure = [...hostUpdateFailures].reverse().find((failure) =>
    normalizeSchedulerRrule(failure.target_rrule) === currentRrule
  ) ?? hostUpdateFailures.at(-1) ?? null;
  let currentRruleAlreadyApplied = effectiveHostRrule === currentRrule;
  if (
    !currentRruleAlreadyApplied &&
    !observedHostRrule &&
    cadence.state_status === "same_identity"
  ) {
    currentRruleAlreadyApplied = monitorRruleAppliedWithinStaleTolerance(
      cadenceClass,
      effectiveHostRrule,
      currentRrule,
      staleToleranceMinutes,
    );
  }
  const schedulerStateAcknowledgesCurrentRrule =
    cadence.state_status === "same_identity" &&
    normalizeSchedulerRrule(lastAppliedRrule) === currentRrule;
  const currentTargetHasFailure = allHostUpdateFailures.some((failure) =>
    normalizeSchedulerRrule(failure.target_rrule) === currentRrule
  );
  const failedTargetRrule = normalizeSchedulerRrule(
    recordedHostFailure?.target_rrule,
  );
  const failedObservedHostRrule = normalizeSchedulerRrule(
    recordedHostFailure?.observed_host_rrule,
  );
  const repeatedFailedPair = Boolean(
    failedTargetRrule === currentRrule &&
      failedObservedHostRrule === effectiveHostRrule,
  );
  const host = evaluateHost({
    state_status: cadence.state_status,
    observed_host_rrule_present: Boolean(observedHostRrule),
    current_rrule_already_applied: currentRruleAlreadyApplied,
    scheduler_state_acknowledges_current_rrule:
      schedulerStateAcknowledgesCurrentRrule,
    current_target_has_failure: currentTargetHasFailure,
    repeated_failed_pair: repeatedFailedPair,
  });
  return {
    schema_version: SCHEDULER_STATE_TRANSITION_RESULT_SCHEMA,
    operation: "backoff",
    current_index: currentIndex,
    current_interval_minutes: currentInterval,
    current_rrule: currentRrule,
    last_applied_rrule: lastAppliedRrule,
    observed_host_rrule: observedHostRrule,
    effective_host_rrule: effectiveHostRrule,
    state_status: cadence.state_status,
    cadence_transition: cadence.transition,
    current_cadence_acknowledged: currentCadenceAcknowledged,
    all_host_update_failures: allHostUpdateFailures,
    host_update_failures: hostUpdateFailures,
    recorded_host_failure: recordedHostFailure,
    current_rrule_already_applied: currentRruleAlreadyApplied,
    scheduler_state_acknowledges_current_rrule:
      schedulerStateAcknowledgesCurrentRrule,
    host_transition: host.transition,
    current_target_has_failure: currentTargetHasFailure,
    repeated_failed_pair: repeatedFailedPair,
  };
}

export function evaluateSchedulerStateTransition(
  value: unknown,
): SchedulerStateTransitionResult {
  const request = requestObject(value);
  if (request.operation === "cadence") return evaluateCadence(request);
  if (request.operation === "host") return evaluateHost(request);
  if (request.operation === "backoff") return evaluateBackoff(request);
  if (request.operation === "monitor_schedule") {
    return evaluateMonitorSchedule(request);
  }
  if (request.operation === "settled_replay") return projectSettledReplay(request);
  throw new EffectRuntimeRequestError("Scheduler state transition operation is unsupported");
}
