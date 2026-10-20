import { createHash } from "node:crypto";
import type { JsonObject } from "../runtime_decode.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString, requireBoolean, requireStringArray } from "../runtime_decode.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { stripPythonWhitespace } from "../coordination/todo_agents.ts";
import { projectCadenceSchedule } from "../quota/automation_cadence.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { schedulerQuotaContext, SCHEDULER_QUOTA_IDENTITY_KEYS as BASE_KEYS } from "../quota/turn_envelope.ts";
import { rruleForMinutes, schedulerRruleIntervalMinutes,
  APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY } from "./state_store.ts";
import { evaluateSchedulerStateTransition, SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
  type SchedulerBackoffTransitionResult, type SchedulerSettledReplayResult } from "./state_transition_rules.ts";
import { schedulerAckHint, schedulerFailureHint, schedulerFallbackHint, legacySchedulerAckHint, legacySchedulerFailureHint } from "./hint_followup.ts";
import { applyHintExecutionContext, goalRuntimeContinuation } from "./hint_execution.ts";
import { boundedMonitorProfile, hintFrontier, hintPendingRetry, hintTimestamp, type HintProfile } from "./hint_wait.ts";

const FRONTIER_KEYS = ["selected_todo.todo_id", "selected_todo.action_kind", "selected_todo.target_key", "selected_todo.claimed_by", "selected_todo.capability_binding_ref"];
const SSH = "codex_app_ssh_goal";
const NATIVE_BLOCK = "update_goal_blocked_keep_loopx_active";
const RESUME = "explicit_codex_goal_resume";

interface CompleteHintRequest extends JsonObject {
  payload: JsonObject;
  context: JsonObject;
  identity_json: Record<string, string>;
  timestamps: Record<string, JsonObject>;
  frontier_deadlines: (JsonObject | null)[];
  arbitration: JsonObject | null;
  scheduler_state: JsonObject | null;
  include_detail: boolean;
  current_time: string;
  generated_at: string;
}

function requireHintFacts(request: JsonObject): CompleteHintRequest {
  for (const field of ["payload", "context", "identity_json", "timestamps"]) requireJsonObject(request[field], field);
  requireBoolean((request.context as JsonObject).valid, "scheduler context validity");
  requireBoolean(request.include_detail, "include_detail");
  requireNonEmptyString(request.current_time, "current_time");
  requireNonEmptyString(request.generated_at, "generated_at");
  const deadlines = request.frontier_deadlines;
  if (!Array.isArray(deadlines) || deadlines.some(deadline => deadline !== null && !jsonObject(deadline)))
    throw new EffectRuntimeRequestError("frontier_deadlines must contain objects or null");
  if (request.arbitration !== null) {
    const arbitration = requireJsonObject(request.arbitration, "scheduler arbitration");
    for (const field of ["disposition", "reason_code", "mode"]) {
      if (typeof arbitration[field] !== "string") throw new EffectRuntimeRequestError(`scheduler arbitration ${field} must be a string`);
    }
    requireStringArray(arbitration.errors, "scheduler arbitration errors");
  }
  if (request.scheduler_state !== null) requireJsonObject(request.scheduler_state, "scheduler state");
  for (const encoded of Object.values(request.identity_json as JsonObject)) {
    if (typeof encoded !== "string") throw new EffectRuntimeRequestError("scheduler identity codec must contain strings");
  }
  for (const rawTime of Object.values(request.timestamps as JsonObject)) {
    const time = requireJsonObject(rawTime, "scheduler timestamp fact");
    requireNonEmptyString(time.iso, "timestamp ISO text");
    requireNonEmptyString(time.utc, "timestamp UTC text");
    if (typeof time.after_seconds !== "number" || !Number.isFinite(time.after_seconds))
      throw new EffectRuntimeRequestError("timestamp offset must be finite seconds");
  }
  return request as CompleteHintRequest;
}

function identityKeys(cadence: string, context: JsonObject): string[] {
  const keys = [...BASE_KEYS];
  if (context.scheduler_owner === "goal_runtime") keys.push(...FRONTIER_KEYS);
  if (cadence !== "monitor_wait") keys.push("recommended_action");
  return keys;
}

function asciiJson(raw: unknown): string {
  return JSON.stringify(raw).replace(/[\u007f-\uffff]/g, char => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function profileJson(raw: unknown): string {
  if (Array.isArray(raw)) return `[${raw.map(profileJson).join(", ")}]`;
  const fields = jsonObject(raw);
  if (fields) return `{${Object.keys(fields).sort().map(key => `${asciiJson(key)}: ${profileJson(fields[key])}`).join(", ")}}`;
  return asciiJson(raw);
}

function legacyProfile(profile: JsonObject): JsonObject {
  return Object.fromEntries(Object.entries(profile).map(([key, entry]) => [
    key.startsWith("app_automation_") ? key.replace("app_automation_", "codex_app_") : key, entry]));
}

function digest(serialized: string, length: number): string {
  return createHash("sha256").update(serialized, "utf8").digest("hex").slice(0, length);
}

interface HintIdentity { reset_token: string; identity_signature: string; profile_signature: string; reset_profile_signature: string }
function hintIdentity(request: JsonObject, profile: HintProfile, snapshots: JsonObject): HintIdentity {
  const context = request.context as JsonObject;
  const keys = identityKeys(profile.cadence_class, context).sort();
  const encoded = requireJsonObject(request.identity_json, "scheduler identity codec");
  const identity = `{${keys.map(key => `${asciiJson(key)}: ${requireNonEmptyString(encoded[key], `identity ${key}`)}`).join(", ")}}`;
  const reset = profileJson(legacyProfile(snapshots.reset as JsonObject));
  return {reset_token: digest(`{"action": ${asciiJson(profile.action)}, "identity_snapshot": ${identity}, "profile_snapshot": ${reset}}`, 16),
    identity_signature: digest(identity, 12), profile_signature: digest(profileJson(legacyProfile(snapshots.profile as JsonObject)), 12),
    reset_profile_signature: digest(reset, 12)};
}

interface Cadence {local: number[]; app: number[]; app_max: number; local_max: number; floor: number; guarantee?: unknown}
function cadenceProjection(profile: HintProfile, payload: JsonObject, request: JsonObject): {cadence: Cadence; profile: HintProfile} {
  const retry = hintPendingRetry(payload);
  const due = retry && hintTimestamp(request, retry.due_at);
  let effective = profile;
  if (["human_gate", "quiet_wait", "agent_scope_wait", "monitor_wait"].includes(profile.cadence_class) && due && due.after_seconds > 0) {
    let horizon = Math.max(1, Math.ceil(due.after_seconds / 60));
    const frontier = hintFrontier(request);
    if (frontier) horizon = Math.min(horizon, Math.max(1, Math.ceil(Number(frontier.frontier_recheck_after_seconds) / 60)));
    if (horizon < profile.interval) effective = {...profile, interval: horizon, progression: [horizon], reset: undefined,
      detail: {...profile.detail, blocked_retry: retry}};
  }
  const local = effective.progression?.length ? effective.progression : [0, 1, 2].map(step => Math.min(effective.interval * 2 ** step, effective.maximum));
  const appMax = Math.min(Math.max(1, effective.maximum), 60);
  const app = local.map(minutes => Math.min(Math.max(1, Math.trunc(minutes)), appMax)).filter((minutes, index, all) => index === 0 || minutes !== all[index - 1]);
  const cadence: Cadence = {local, app, app_max: appMax, local_max: effective.maximum, floor: 0};
  const floor = jsonObject(payload.automation_cadence)?.min_interval_minutes;
  return {cadence: floor ? projectCadenceSchedule({...cadence, min_interval_minutes: floor}) as unknown as Cadence : cadence, profile: effective};
}

function profileSnapshots(profile: HintProfile, cadence: Cadence): JsonObject {
  const snapshot = {cadence_class: profile.cadence_class, app_automation_initial_interval_minutes: cadence.app[0],
    app_automation_initial_rrule: rruleForMinutes(cadence.app[0]), app_automation_max_interval_minutes: cadence.app_max,
    app_automation_progression_minutes: cadence.app, unchanged_poll_backoff_multiplier: 2,
    local_scheduler_unchanged_poll_limit: profile.cli_limit, claude_code_loop_unchanged_poll_limit: profile.claude_limit};
  return {profile: snapshot, reset: {...(profile.reset ?? snapshot), app_automation_progression_minutes: cadence.app}};
}

function finalReplan(profile: HintProfile): JsonObject {
  return {enabled: profile.cli_limit !== null || profile.claude_limit !== null, trigger: "before_unchanged_poll_after_limit",
    action: "rerun_quota_should_run_once", if_changed: "follow_new_scheduler_hint", if_run_now: "execute_new_quota_contract",
    if_unchanged: "apply_after_limit_without_spend", spend_policy: "no quota spend for final replan check or loop stop"};
}

function resetPolicy(identity: HintIdentity, cadence: Cadence): JsonObject {
  return {reset_token: identity.reset_token, host_state_key: "scheduler_hint.reset_policy.reset_token",
    app_automation_initial_interval_minutes: cadence.app[0], app_automation_initial_rrule: rruleForMinutes(cadence.app[0]),
    identity_signature: identity.identity_signature};
}

function resetDetail(profile: HintProfile, identity: HintIdentity, cadence: Cadence, keys: string[]): JsonObject {
  return {...resetPolicy(identity, cadence), schema_version: "scheduler_reset_policy_v0", source: "quota.should-run",
    reset_to: "profile_initial_interval", profile_action: profile.action, local_scheduler_initial_interval_minutes: cadence.local[0],
    clear_unchanged_poll_state: true, identity_key_count: keys.length, profile_signature: identity.profile_signature,
    reset_profile_signature: identity.reset_profile_signature,
    reset_condition_summary: "token_changed|user_feedback|new_or_reassigned_todo|gate_or_material_transition|active_work_projected",
    after_reset: "apply_initial_interval_before_backoff", app_automation_tool: "automation_update",
    app_automation_apply: "call_automation_update_to_restore_initial_rrule_on_token_change", no_spend_for_reset: true};
}

function loopDetails(profile: HintProfile, cadence: Cadence, replan: JsonObject): JsonObject {
  const codex = {unchanged_poll_limit: profile.cli_limit, after_limit: profile.cli_limit !== null ? NATIVE_BLOCK : "continue",
    final_quota_replan_check: replan, loopx_goal_state: "remains_active", resume_trigger: RESUME, no_spend_for_block: true};
  return {local_scheduler: {recommended_interval_minutes: cadence.local[0], max_interval_minutes: cadence.local_max,
    unchanged_poll_backoff_multiplier: 2, example_progression_minutes: cadence.local, unchanged_poll_limit: profile.cli_limit,
    after_limit: profile.cli_limit !== null ? "stop_tick_loop" : "continue", final_quota_replan_check: replan, no_spend_for_cadence_change: true},
    codex_cli_tui: {...codex, no_spend_for_exit: true}, [SSH]: {...codex}, claude_code_loop: {unchanged_poll_limit: profile.claude_limit,
      after_limit: profile.claude_limit !== null ? "stop_loop" : "continue", final_quota_replan_check: replan, no_spend_for_stop: true}};
}

function backoffDetail(profile: HintProfile, cadence: Cadence, decision: SchedulerBackoffTransitionResult): JsonObject {
  return {progression_minutes: cadence.app, current_interval_minutes: decision.current_interval_minutes, host_max_interval_minutes: cadence.app_max,
    coarser_wait_fallback: cadence.floor ? "hold_affected_automation" : "local_scheduler_only",
    host_update_failure: "cache_recent_failed_target_and_observed_host_pairs_then_suppress_each_exact_repeat_until_host_changes_ack_or_expiry",
    ack_required_after_apply: decision.host_transition === "apply_required", ack_required_from_host_match: decision.host_transition === "host_match_ack_required",
    persist: "reset_token|identity_signature|progression_index|last_applied_rrule|host_update_failures|host_update_failure_compat",
    same_identity_action: profile.advance !== false ? "advance_index_after_applied_interval_elapsed" : "keep_initial_interval_while_active_work",
    reset_action: "clear_progression_index_apply_initial_rrule", automation_update_scope: "rrule_only_preserve_body_name_status"};
}

function appAutomation(decision: SchedulerBackoffTransitionResult, cadence: Cadence, identity: HintIdentity, stateKey: unknown): JsonObject {
  const apply = decision.host_transition === "apply_required", match = decision.host_transition === "host_match_ack_required";
  const suppressed = decision.host_transition === "recorded_failure_suppressed";
  const backoff: JsonObject = {schema_version: "app_automation_stateful_backoff_v0", state_key: stateKey, identity_signature: identity.identity_signature,
    reset_token: identity.reset_token, progression_index: decision.current_index, current_rrule: decision.current_rrule,
    apply_needed: apply, ack_needed: apply || match, state_status: suppressed ? "host_update_failure_suppressed" : decision.state_status};
  if (decision.host_update_failures.length) backoff.host_update_failures = structuredClone(decision.host_update_failures);
  if (decision.recorded_host_failure) backoff.host_update_failure = {...decision.recorded_host_failure};
  if (decision.observed_host_rrule) backoff.host_observation = {source: "quota_should_run_host_observation", current_rrule: decision.observed_host_rrule,
    status: decision.current_rrule_already_applied ? "matches_recommended" : "drift_detected"};
  return {recommended_interval_minutes: decision.current_interval_minutes, max_interval_minutes: cadence.app_max,
    unchanged_poll_backoff_multiplier: 2, example_progression_minutes: cadence.app,
    apply: apply ? "update_automation_cadence_if_possible" : suppressed ? "none_recorded_host_failure" : "none_already_applied",
    host_tool: "automation_update", host_action: apply ? "update_current_heartbeat_rrule" : suppressed ? "none_recorded_host_failure" : match ? "ack_observed_rrule_without_update" : "none",
    host_action_contract: apply ? "automation_update_rrule_then_quota_scheduler_ack" : suppressed ? "skip_automation_update_for_recorded_host_failure" : match ? "quota_scheduler_ack_from_matching_host_observation" : "skip_automation_update_when_apply_needed_false",
    rrule_source: apply ? "scheduler_hint.app_automation.recommended_rrule" : null, stateful_backoff: backoff, no_spend_for_cadence_change: true};
}

function hostFacts(request: JsonObject, profile: HintProfile, decision: SchedulerBackoffTransitionResult, scope: JsonObject): JsonObject | null {
  const payload = request.payload as JsonObject, agent = jsonObject(payload.agent_identity)?.agent_id;
  if (!payload.goal_id || !agent) return null;
  return {schema_version: "loopx_scheduler_heartbeat_host_facts_v0", goal_id: String(payload.goal_id), agent_id: String(agent),
    ...scope, progression_index: decision.current_index, progression_minutes: scope.progression_minutes,
    expected_rrule: decision.current_rrule, cadence_class: profile.cadence_class, stale_tolerance_minutes: 2,
    generated_at: request.generated_at, ack_needed: ["apply_required", "host_match_ack_required"].includes(decision.host_transition),
    apply_needed: decision.host_transition === "apply_required"};
}

function attachAppFollowups(app: JsonObject, request: JsonObject, facts: JsonObject | null, projection: {ack: typeof schedulerAckHint; failure: typeof schedulerFailureHint}): void {
  const payload = request.payload as JsonObject, backoff = app.stateful_backoff as JsonObject;
  const agent = jsonObject(payload.agent_identity)?.agent_id;
  if (!payload.goal_id || !agent) return;
  const scope = {goal_id: payload.goal_id, agent_id: agent, surface: facts?.surface ?? "codex_app", state_key: backoff.state_key,
    available_capabilities: request.available_capabilities ?? (Array.isArray(jsonObject(payload.capability_gate)?.available) ? jsonObject(payload.capability_gate)?.available : [])};
  const observed = request.effective_host_rrule;
  if (backoff.apply_needed) {
    app.recommended_rrule = backoff.current_rrule;
    app.failure_hint = projection.failure({...scope, failed_rrule: backoff.current_rrule, observed_host_rrule: observed,
      scheduler_host_facts: facts ? {...facts, operation: "host_failure", applied_rrule: observed, observed_host_rrule: observed,
        failure_kind: "host_tool_failure", source: "quota_scheduler_host_update_failure", host_match_observed: false} : null});
    if (scope.surface === "codex_app" && !app.execution_interval_policy)
      app.fallback_hint = schedulerFallbackHint({...scope, automation_id: request.automation_id});
  }
  if (backoff.ack_needed) app.ack_hint = projection.ack({...scope, applied_rrule: backoff.current_rrule,
    reset_token: backoff.reset_token, identity_signature: backoff.identity_signature, host_match_observed: true,
    after: backoff.apply_needed ? app.execution_interval_policy ? "automation_update_then_readback_actual_rrule" : "automation_update_rrule_success" : "matching_host_rrule_observed",
    scheduler_host_facts: facts ? {...facts, operation: "ack", applied_rrule: backoff.current_rrule,
      observed_host_rrule: observed, source: "quota_scheduler_ack", host_match_observed: true} : null});
}

function notificationCooldown(profile: HintProfile, decision: SchedulerBackoffTransitionResult, request: JsonObject): JsonObject | null {
  if (profile.cadence_class !== "human_gate" || decision.host_transition !== "recorded_failure_suppressed") return null;
  const failed = hintTimestamp(request, decision.recorded_host_failure?.failed_at);
  const hostInterval = schedulerRruleIntervalMinutes(decision.effective_host_rrule);
  const interval = Math.max(1, profile.notification_interval ?? decision.current_interval_minutes);
  if (!failed || hostInterval === null || hostInterval >= interval) return null;
  const elapsed = Math.max(0, -failed.after_seconds), cycle = Math.floor(elapsed / (interval * 60));
  const due = cycle >= 1 && elapsed - cycle * interval * 60 < hostInterval * 60;
  const next = new Date(Date.parse(failed.iso) + (cycle + 1) * interval * 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return {schema_version: "user_gate_notification_cooldown_v0", active: true, notification_due: due,
    notification_suppressed: !due, policy: "failed_host_update_bounded_reminder_window", cooldown_minutes: interval,
    reminder_window_minutes: hostInterval, failed_at: failed.utc, next_reminder_at: next,
    reason: "the user gate is still pending, but the failed host cadence update left a tighter poll; suppress duplicate notices outside the bounded human-gate reminder window"};
}

function buildCadenceHint(request: CompleteHintRequest, selected: HintProfile): JsonObject {
  const payload = request.payload as JsonObject, context = request.context as JsonObject;
  const {cadence, profile} = cadenceProjection(selected, payload, request);
  const identity = hintIdentity(request, profile, profileSnapshots(profile, cadence));
  const state = jsonObject(request.scheduler_state) ?? {};
  const decision = evaluateSchedulerStateTransition({schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
    operation: "backoff", progression_minutes: cadence.app, scheduler_state: state, reset_token: identity.reset_token,
    identity_signature: identity.identity_signature, advance_same_identity: profile.advance !== false,
    current_time: request.current_time, observed_host_rrule: request.observed_host_rrule ?? null,
    cadence_class: profile.cadence_class, stale_tolerance_minutes: 2}) as SchedulerBackoffTransitionResult;
  const applicable = context.app_automation_applicability === "applicable" || context.codex_app_applicability === "applicable";
  const surface = applicable ? context.host_surface : "codex_app";
  const stateKey = state.state_key || APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY;
  const app = appAutomation(decision, cadence, identity, stateKey);
  if (cadence.floor) {
    app.execution_interval_policy = payload.automation_cadence;
    app.guarantee = cadence.guarantee;
    if (decision.host_transition === "recorded_failure_suppressed") Object.assign(app, {host_action: "pause_current_heartbeat", apply: "pause_affected_automation"});
  }
  if (decision.host_transition === "apply_required") app.recommended_rrule = decision.current_rrule;
  const facts = hostFacts(request, profile, decision, {surface, state_key: stateKey, reset_token: identity.reset_token,
    identity_signature: identity.identity_signature, progression_minutes: cadence.app});
  const withObservation = {...request, effective_host_rrule: decision.effective_host_rrule};
  attachAppFollowups(app, withObservation, facts, {ack: schedulerAckHint, failure: schedulerFailureHint});
  return completeCadenceProjection(request, profile, {cadence, identity, decision, app, facts, withObservation});
}

interface CadenceObservation {
  cadence: Cadence; identity: HintIdentity; decision: SchedulerBackoffTransitionResult;
  app: JsonObject; facts: JsonObject | null; withObservation: JsonObject;
}

function completeCadenceProjection(request: CompleteHintRequest, profile: HintProfile, observation: CadenceObservation): JsonObject {
  const {cadence, identity, decision, app, facts, withObservation} = observation;
  const payload = request.payload, context = request.context;
  const surface = context.app_automation_applicability === "applicable" || context.codex_app_applicability === "applicable" ? context.host_surface : "codex_app";
  const replan = finalReplan(profile), loops = loopDetails(profile, cadence, replan), keys = identityKeys(profile.cadence_class, context);
  const limits: JsonObject = {local_scheduler: profile.cli_limit, codex_cli_tui: profile.cli_limit, claude_code_loop: profile.claude_limit};
  const after: JsonObject = Object.fromEntries(["local_scheduler", "codex_cli_tui", "claude_code_loop"].map(key => [key, (loops[key] as JsonObject).after_limit]));
  const contains = ["local_scheduler", "codex_cli_tui", "claude_code_loop", "final_quota_replan_check", "reset_policy_detail", "stateful_backoff_detail"];
  if (profile.cli_limit !== null) { limits[SSH] = profile.cli_limit; after[SSH] = (loops[SSH] as JsonObject).after_limit; contains.splice(2, 0, SSH); }
  const arbitration = request.arbitration as JsonObject;
  const spend = schedulerQuotaContext(payload).spend_policy;
  const hint: JsonObject = {schema_version: "scheduler_hint_v0", source: "quota.should-run", action: profile.action,
    cadence_class: profile.cadence_class, reason_code: arbitration.reason_code, reason: profile.reason, spend_policy: spend,
    app_automation: app, unchanged_poll: {limits, after_limits: after, final_quota_replan_check_enabled: replan.enabled,
      final_quota_replan_check_action: replan.enabled ? replan.action : null, spend_policy: replan.spend_policy},
    unchanged_identity_keys: keys, reset_policy: resetPolicy(identity, cadence), detail_ref: {schema_version: "scheduler_hint_detail_v0",
      omitted_by_default: true, execution_required: false, request: "loopx quota should-run --include-detail scheduler",
      hot_path_runtime_fields: ["app_automation", "unchanged_poll", "reset_policy"], contains}};
  const retry = hintPendingRetry(payload);
  if (retry) hint.pending_retry = retry;
  if (surface === "codex_app") {
    const legacy = structuredClone(app), legacyBackoff = legacy.stateful_backoff as JsonObject;
    legacy.applicability = "applicable";
    legacy.rrule_source = legacy.recommended_rrule ? "scheduler_hint.codex_app.recommended_rrule" : null;
    legacyBackoff.schema_version = "codex_app_stateful_backoff_v0";
    attachAppFollowups(legacy, withObservation, facts, {ack: legacySchedulerAckHint, failure: legacySchedulerFailureHint});
    hint.codex_app = legacy;
  }
  const cooldown = notificationCooldown(profile, decision, request);
  if (cooldown) hint.user_gate_notification_cooldown = cooldown;
  const frontier = hintFrontier(request);
  if (request.include_detail) hint.cold_path_detail = {schema_version: "scheduler_hint_detail_v0", source: "quota.should-run", ...loops,
    final_quota_replan_check: replan, reset_policy_detail: resetDetail(profile, identity, cadence, keys), stateful_backoff_detail: backoffDetail(profile, cadence, decision),
    ...(profile.detail ? {cadence_context: profile.detail} : {}), ...(frontier ? {frontier_recheck: frontier} : {})};
  return applyHintExecutionContext(hint, context, frontier?.frontier_recheck_after_seconds);
}

const CADENCE_PROFILES: Record<string, HintProfile> = {
  "peer_coordination_wait": {
    "action": "backoff_until_reassigned",
    "cadence_class": "peer_coordination_wait",
    "reason": "explicit peer coordination has no executable peer lane or local fallback; keep a bounded no-spend observer because peer readiness, configuration, or the local frontier can change asynchronously",
    "interval": 10,
    "maximum": 60,
    "cli_limit": 3,
    "claude_limit": 3,
    "progression": [
      10,
      20,
      30,
      60
    ]
  },
  "agent_monitor_only_wait": {
    "action": "backoff_agent_monitor_only",
    "cadence_class": "agent_monitor_only",
    "reason": "agent monitor-only mode blocks advancement while a quiet poll keeps due monitors and verified direct replies responsive",
    "interval": 15,
    "maximum": 60,
    "cli_limit": 3,
    "claude_limit": 3
  },
  "consistency_repair": {
    "action": "repair_interaction_contract_projection",
    "cadence_class": "control_plane_repair",
    "reason": "scheduler inputs disagree with the final interaction contract; repair the projection before applying delivery or wait cadence",
    "interval": 3,
    "maximum": 10,
    "cli_limit": null,
    "claude_limit": null,
    "advance": false
  },
  "human_gate": {
    "action": "backoff_waiting_for_user",
    "cadence_class": "human_gate",
    "reason": "user/controller action is the next unlock; surface the concrete gate once, then stop repeating the same quiet poll",
    "cli_limit": 3,
    "claude_limit": 3,
    "interval": 30,
    "maximum": 120,
    "notification_interval": 30
  },
  "active_work": {
    "action": "run_now",
    "cadence_class": "active_work",
    "reason": "the interaction contract requires an agent attempt; keep the active scheduler cadence until the turn validates or blocks",
    "interval": 3,
    "maximum": 10,
    "cli_limit": null,
    "claude_limit": null
  },
  "unchanged_wait": {
    "action": "backoff_until_fresh_evidence",
    "cadence_class": "unchanged_noop",
    "reason": "the current mapped or post-handoff source is unchanged; do not keep a tight loop while waiting for fresh evidence or a concrete handoff",
    "interval": 60,
    "maximum": 240,
    "cli_limit": 3,
    "claude_limit": 3
  },
  "agent_scope_wait": {
    "action": "backoff_until_reassigned",
    "cadence_class": "agent_scope_wait",
    "reason": "this registered agent has no in-scope advancement candidate; agent-to-agent handoffs may change quickly, so stay closer to the prior scheduler cadence while waiting for handoff owner progress, reassignment, or a current-agent todo",
    "interval": 10,
    "maximum": 60,
    "progression": [10, 20, 30, 60],
    "cli_limit": 3,
    "claude_limit": 3
  },
  "monitor_wait": {
    "action": "backoff_until_material_transition",
    "cadence_class": "monitor_wait",
    "reason": "monitor-only quiet polls should remain alive but use a slower cadence until material evidence, a blocker, or replan obligation appears",
    "cli_limit": 3,
    "claude_limit": 3,
    "interval": 15,
    "maximum": 60
  },
  "quiet_wait": {
    "action": "backoff_until_state_change",
    "cadence_class": "quiet_wait",
    "cli_limit": 3,
    "claude_limit": 3,
    "interval": 30,
    "maximum": 120,
    "reason": "quota blocks delivery and no immediate user/monitor-specific path is projected; poll at a slower cadence until the status changes"
  }
};

function invalidContextHint(context: JsonObject): JsonObject {
  const spend = "no quota spend for scheduler context repair";
  return {schema_version: "scheduler_hint_v0", source: "quota.should-run", action: "repair_scheduler_execution_context",
    cadence_class: "control_plane_repair", reason_code: "invalid_scheduler_execution_context",
    reason: "scheduler ownership is missing or contradictory; repair the typed execution context before applying cadence", spend_policy: spend,
    execution_context: context, execution_phase: {schema_version: "scheduler_execution_phase_v0", disposition: "contract_error",
      completed: false, apply_needed: false, ack_needed: false, acknowledged: false},
    unchanged_poll: {local_scheduler: "stop_until_context_repaired", codex_cli_tui: "stop_until_context_repaired",
      [SSH]: "stop_until_context_repaired", claude_code_loop: "stop_until_context_repaired", final_quota_replan_check_enabled: false, spend_policy: spend},
    consistency_error: {source: "scheduler_execution_context", errors: context.errors},
    [stripPythonWhitespace(String(context.host_surface ?? "")) === "trae_app" ? "app_automation" : "codex_app"]: {
      applicability: "blocked_invalid_context", apply: "none", host_action: "none", ack_required: false}};
}

function stoppedHint(context: JsonObject, cause: JsonObject): JsonObject {
  return applyHintExecutionContext({schema_version: "scheduler_hint_v0", source: "quota.should-run", action: "stop_until_explicit_resume",
    cadence_class: cause.cadence, reason_code: cause.reason_code, reason: cause.reason, spend_policy: cause.spend,
    app_automation: {apply: "pause_or_delete_current_heartbeat_if_possible", host_tool: "automation_update",
      host_action: "pause_or_delete_current_heartbeat", host_action_required: true, attempt_limit: 1, verify_host_result: true,
      ack_required: false, resume_trigger: cause.resume, no_spend_for_host_action: true},
    unchanged_poll: {local_scheduler: "stop", codex_cli_tui: "exit", [SSH]: "complete_host_goal", claude_code_loop: "stop",
      final_quota_replan_check_enabled: false, spend_policy: cause.unchanged_spend},
    unchanged_identity_keys: identityKeys(String(cause.cadence), context)}, context);
}

function pausedHint(context: JsonObject, mode: string): JsonObject {
  const stopped = mode === "goal_stopped";
  return stoppedHint(context, {cadence: mode, reason_code: mode,
    reason: stopped ? "Goal lifecycle is stopped by owner; recurring host automation must stop until the Goal is explicitly resumed"
      : "Goal-level compute quota is paused; recurring host automation must stop until quota.compute is explicitly raised above 0",
    spend: stopped ? "no quota spend for stopped-Goal automation shutdown" : "no quota spend for paused automation shutdown",
    resume: stopped ? "explicit Goal lifecycle resume" : "explicit quota resume with quota.compute > 0",
    unchanged_spend: stopped ? "no quota spend while the Goal lifecycle is stopped" : "no quota spend while compute quota is paused"});
}

function automaticCadenceWait(payload: JsonObject, arbitration: JsonObject, context: JsonObject): JsonObject | null {
  const eligibility = jsonObject(jsonObject(payload.automation_cadence_readback)?.eligibility);
  if (payload.decision !== "wait" || payload.effective_action !== EffectiveAction.BLOCKED_WAIT || arbitration.disposition !== "quiet_wait"
    || !(context.app_automation_applicability === "applicable" || context.codex_app_applicability === "applicable")
    || !eligibility || eligibility.state !== "waiting" || eligibility.reason !== "minimum_interval_wait"
    || eligibility.eligible_now !== false || typeof eligibility.next_eligible_at_ms !== "number"
    || !Number.isInteger(eligibility.next_eligible_at_ms) || eligibility.next_eligible_at_ms < 0) return null;
  const spend = "no quota spend while automatic execution waits for the owner minimum interval";
  return applyHintExecutionContext({schema_version: "scheduler_hint_v0", source: "quota.should-run", action: "backoff_until_state_change",
    cadence_class: "quiet_wait", reason_code: arbitration.reason_code, reason: "automatic execution minimum interval has not elapsed", spend_policy: spend,
    app_automation: {apply: "none", host_action: "none", host_action_required: false, ack_required: false,
      next_eligible_at_ms: eligibility.next_eligible_at_ms, no_spend_for_cadence_change: true},
    unchanged_poll: {local_scheduler: "continue", codex_cli_tui: "continue", [SSH]: "continue", claude_code_loop: "continue",
      final_quota_replan_check_enabled: false, spend_policy: spend}, unchanged_identity_keys: identityKeys("quiet_wait", context)}, context);
}

function selectedProfile(request: JsonObject, arbitration: JsonObject): HintProfile {
  const payload = request.payload as JsonObject;
  const disposition = String(arbitration.disposition);
  let profile = CADENCE_PROFILES[disposition];
  if (!profile) throw new EffectRuntimeRequestError(`unhandled scheduler disposition: ${disposition}`);
  if (disposition === "monitor_wait" && arbitration.pending_dependency_wait === true) {
    profile = {...profile, cli_limit: null, claude_limit: null};
  }
  if (disposition === "human_gate" || disposition === "monitor_wait") profile = boundedMonitorProfile(profile, payload, request);
  if (disposition === "active_work") profile = {...profile, advance: arbitration.mode === "capability_bridge_repair"
    && jsonObject(jsonObject(payload.interaction_contract)?.agent_channel)?.delivery_allowed === false};
  if (disposition === "quiet_wait" && arbitration.reason_code === "blocked_wait") profile = {...profile,
    reason: "the receipt-owned retry is pending; recheck its original due time and current admission gates without waiting on the non-blocking user notice"};
  if (disposition === "agent_scope_wait" && arbitration.scheduled_recheck_after_seconds != null) {
    const horizon = Math.max(1, Math.ceil(Number(arbitration.scheduled_recheck_after_seconds) / 60));
    const progression = [...new Set([10, 20, 30, 60].map(bucket => Math.min(bucket, horizon)))].sort((a, b) => a - b);
    profile = {...profile, action: "backoff_until_state_change", interval: progression[0], maximum: progression[progression.length - 1],
      cli_limit: null, claude_limit: null, progression,
      reason: "an automatic clock-backed successor is pending; keep quiet rechecks until its scheduled instant, then follow fresh admission gates"};
  } else if (disposition === "agent_scope_wait" && arbitration.pending_dependency_wait === true) {
    profile = {...profile, action: "backoff_until_state_change", interval: 10, maximum: 60,
      cli_limit: null, claude_limit: null, progression: [10, 20, 30, 60],
      reason: "an exact successor has a registered pending dependency; keep quiet source rechecks and follow fresh admission gates when it changes"};
  }
  return profile;
}

function completeHint(request: CompleteHintRequest): JsonObject {
  const context = requireJsonObject(request.context, "scheduler execution context");
  const payload = requireJsonObject(request.payload, "quota hint facts");
  if (context.valid !== true) return invalidContextHint(context);
  const mode = schedulerQuotaContext(payload).recommended_mode;
  if (mode === "goal_stopped" || mode === "quota_paused") return pausedHint(context, mode);
  if (!jsonObject(request.arbitration)) throw new EffectRuntimeRequestError("scheduler arbitration must come from the interaction projection");
  const arbitration = request.arbitration as JsonObject;
  const waiting = automaticCadenceWait(payload, arbitration, context);
  if (waiting) return waiting;
  if (arbitration.disposition === "terminal_stop") return stoppedHint(context, {cadence: "terminal_no_followup", reason_code: arbitration.reason_code,
    reason: "validated closure evidence derives no-follow-up and confirms no remaining frontier; recurring polling must stop until resume",
    spend: "no quota spend for terminal automation shutdown", resume: "explicit goal resume or newly projected work",
    unchanged_spend: "no quota spend for terminal loop stop"});
  if (arbitration.disposition === "settled_replay") {
    const replay = evaluateSchedulerStateTransition({schema_version: SCHEDULER_STATE_TRANSITION_REQUEST_SCHEMA,
      operation: "settled_replay", observed_host_rrule: request.observed_host_rrule ?? null}) as SchedulerSettledReplayResult;
    return applyHintExecutionContext(replay.hint, context);
  }
  const hint = buildCadenceHint(request, selectedProfile(request, arbitration));
  if (arbitration.disposition === "consistency_repair") hint.consistency_error = Array.isArray(arbitration.errors) && arbitration.errors.length
    ? {schema_version: "scheduler_arbitration_v0", reason_code: arbitration.reason_code, mode: arbitration.mode || null,
      errors: arbitration.errors, repair_action: "rebuild interaction_contract from the current quota decision, then rerun quota before applying scheduler cadence"} : null;
  return hint;
}

export function projectSchedulerHint(raw: JsonObject): JsonObject {
  const request = requireJsonObject(raw, "scheduler hint request");
  const operation = requireNonEmptyString(request.operation, "scheduler hint operation");
  let hint: JsonObject;
  switch (operation) {
    case "hint": hint = completeHint(requireHintFacts(request)); break;
    case "ack": hint = schedulerAckHint(request); break;
    case "legacy_ack": hint = legacySchedulerAckHint(request); break;
    case "failure": hint = schedulerFailureHint(request); break;
    case "legacy_failure": hint = legacySchedulerFailureHint(request); break;
    case "fallback": hint = schedulerFallbackHint(request); break;
    case "continuation": hint = goalRuntimeContinuation(requireJsonObject(request.hint, "scheduler hint"), request.frontier_recheck_after_seconds); break;
    default: throw new EffectRuntimeRequestError(`unsupported scheduler hint operation: ${operation}`);
  }
  return {schema_version: "scheduler_hint_projection_v0", hint};
}
