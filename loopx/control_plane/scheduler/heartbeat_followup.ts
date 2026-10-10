import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  goalPathSegment,
  heartbeatReceiptStatus,
  type HeartbeatReceiptStatus,
} from "../rollout_receipt_log.ts";
import {
  jsonObject,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import {
  evaluateSchedulerHeartbeatHostFacts,
  SCHEDULER_HEARTBEAT_HOST_FACTS_SCHEMA,
  type SchedulerHeartbeatCommitResult,
} from "./heartbeat_commit.ts";

export const SCHEDULER_HEARTBEAT_FOLLOWUP_HINT_SCHEMA =
  "loopx_scheduler_host_followup_hint_v0";
export const SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA =
  "loopx_scheduler_host_followup_request_v0";
export const SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA =
  "loopx_scheduler_host_followup_result_v0";
export const SCHEDULER_HEARTBEAT_FOLLOWUP_ERROR_SCHEMA =
  "loopx_scheduler_host_followup_error_v0";

const ACK_CLASSIFICATION = "quota_scheduler_ack";
const FAILURE_CLASSIFICATION = "quota_scheduler_host_update_failure";

export interface SchedulerHeartbeatFollowupRequest extends JsonObject {
  schema_version: typeof SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA;
  runtime_root: string;
  turn_instance_id: string | null;
  require_heartbeat_receipt: boolean;
  before: JsonObject;
  host_facts: JsonObject;
  use_current_hint: boolean;
  reason_summary: string | null;
}

export interface SchedulerHeartbeatFollowupResult extends JsonObject {
  schema_version: string;
  ok: boolean;
}

type FollowupOperation = "ack" | "host_failure";

function optionalText(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  return requireNonEmptyString(value, "scheduler follow-up optional text").trim();
}

function followupOperation(facts: JsonObject): FollowupOperation {
  const value = facts.operation ?? facts.outcome;
  if (value === "ack") return "ack";
  if (value === "host_failure" || value === "failure") return "host_failure";
  throw new EffectRuntimeRequestError(
    "scheduler host follow-up operation is unsupported",
    "unsupported_scheduler_followup_operation",
  );
}

function requestObject(value: unknown): SchedulerHeartbeatFollowupRequest {
  const input = requireJsonObject(value, "scheduler heartbeat follow-up request");
  if (input.schema_version !== SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Scheduler heartbeat follow-up request schema mismatch",
      "scheduler_followup_schema_mismatch",
    );
  }
  const runtimeRoot = requireNonEmptyString(input.runtime_root, "runtime_root").trim();
  const hostFacts = requireJsonObject(input.host_facts, "host_facts");
  if (hostFacts.schema_version !== SCHEDULER_HEARTBEAT_HOST_FACTS_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Scheduler heartbeat host facts schema mismatch",
      "scheduler_host_facts_schema_mismatch",
    );
  }
  goalPathSegment(hostFacts.goal_id);
  requireNonEmptyString(hostFacts.agent_id, "agent_id");
  followupOperation(hostFacts);
  const turnInstanceId = optionalText(input.turn_instance_id);
  const requireReceipt = input.require_heartbeat_receipt === undefined
    ? turnInstanceId !== null
    : requireBoolean(input.require_heartbeat_receipt, "require_heartbeat_receipt");
  if (requireReceipt && turnInstanceId === null) {
    throw new EffectRuntimeRequestError(
      "receipt-bound scheduler follow-up requires turn_instance_id",
      "scheduler_followup_turn_missing",
    );
  }
  return {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    turn_instance_id: turnInstanceId,
    require_heartbeat_receipt: requireReceipt,
    before: requireJsonObject(input.before ?? {}, "before"),
    host_facts: hostFacts,
    use_current_hint: input.use_current_hint === undefined
      ? false
      : requireBoolean(input.use_current_hint, "use_current_hint"),
    reason_summary: optionalText(input.reason_summary),
  };
}

function compactBefore(value: JsonObject): JsonObject {
  const quota = jsonObject(value.quota) ?? {};
  return {
    should_run: Boolean(value.should_run),
    normal_delivery_allowed: Boolean(value.normal_delivery_allowed),
    recovery_delivery_allowed: Boolean(value.recovery_delivery_allowed),
    effective_action: value.effective_action ?? null,
    self_repair_allowed: Boolean(value.self_repair_allowed),
    capability_repair_allowed: Boolean(value.capability_repair_allowed),
    workspace_repair_allowed: Boolean(value.workspace_repair_allowed),
    state: String(value.state ?? ""),
    safe_bypass_allowed: Boolean(value.safe_bypass_allowed),
    safe_bypass_kind: value.safe_bypass_kind ?? null,
    blocked_action_scope: value.blocked_action_scope ?? null,
    compute: value.compute ?? quota.compute ?? null,
    window_hours: value.window_hours ?? quota.window_hours ?? null,
    slot_minutes: value.slot_minutes ?? quota.slot_minutes ?? null,
    spent_slots: value.spent_slots ?? quota.spent_slots ?? null,
    allowed_slots: value.allowed_slots ?? quota.allowed_slots ?? null,
  };
}

function receiptFailure(
  request: SchedulerHeartbeatFollowupRequest,
  status: Exclude<HeartbeatReceiptStatus, "fresh">,
): SchedulerHeartbeatFollowupResult {
  const facts = request.host_facts;
  const operation = followupOperation(facts);
  const missing = status === "missing";
  return {
    ok: false,
    schema_version: "quota_scheduler_followup_receipt_failure_v0",
    mode: operation === "ack" ? "scheduler-ack-current" : "scheduler-fail-current",
    goal_id: facts.goal_id,
    agent_id: facts.agent_id,
    turn_instance_id: request.turn_instance_id,
    decision: "skip",
    should_run: false,
    status: missing ? "heartbeat_receipt_missing" : "heartbeat_receipt_stale",
    state: "blocked_receipt",
    error_code: missing
      ? "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING"
      : "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
    reason: missing
      ? "scheduler follow-up requires the originating heartbeat receipt; refusing to rebuild authority from the current live frontier"
      : "scheduler follow-up Turn was superseded by a newer quota should-run receipt; refusing stale host writeback",
    retry_guidance: missing
      ? "retry with the exact Turn id from a committed quota should-run heartbeat receipt"
      : "rerun quota should-run and use the ACK/failure command emitted by the newest Turn",
    write_performed: false,
    appended: false,
    registry_mutated: false,
    scheduler_state_mutated: false,
    quota_spend_performed: false,
    delivery_outcome: "surface_only",
  };
}

function commitReceiptFailureStatus(
  commit: SchedulerHeartbeatCommitResult,
): Exclude<HeartbeatReceiptStatus, "fresh"> | null {
  if (commit.reason_code === "heartbeat_receipt_missing") return "missing";
  if (commit.reason_code === "heartbeat_receipt_stale") return "stale";
  return null;
}

function schedulerState(commit: SchedulerHeartbeatCommitResult): JsonObject | null {
  if (!commit.state) return null;
  return Object.fromEntries(
    Object.entries(commit.state).filter(([key]) => key !== "heartbeat_commit"),
  );
}

function compatibilityFailure(
  request: SchedulerHeartbeatFollowupRequest,
  commit: SchedulerHeartbeatCommitResult,
): SchedulerHeartbeatFollowupResult {
  const facts = request.host_facts;
  const operation = followupOperation(facts);
  const applied = operation === "ack"
    ? String(commit.applied_rrule ?? facts.applied_rrule ?? "")
    : String(commit.target_rrule ?? facts.expected_rrule ?? facts.target_rrule ?? "");
  const payload: SchedulerHeartbeatFollowupResult = {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA,
    ok: false,
    mode: operation === "ack" ? "scheduler-ack" : "scheduler-fail-current",
    dry_run: facts.execute === false,
    goal_id: facts.goal_id,
    agent_id: facts.agent_id,
    surface: facts.surface ?? "codex_app",
    state_key: facts.state_key ?? "scheduler_hint.app_automation.stateful_backoff",
    applied_rrule: applied,
    appended: false,
    registry_mutated: false,
    reason: commit.reason,
    reason_code: commit.reason_code,
    before: request.before,
    after: null,
    scheduler_commit: commit,
  };
  if (operation === "host_failure") {
    payload.failed_rrule = applied;
    delete payload.applied_rrule;
  }
  if (request.use_current_hint && operation === "ack") {
    payload.used_current_hint = true;
    payload.current_hint_source = "quota.should-run.scheduler_hint";
  }
  return payload;
}

function ackResult(
  request: SchedulerHeartbeatFollowupRequest,
  commit: SchedulerHeartbeatCommitResult,
): SchedulerHeartbeatFollowupResult {
  const facts = request.host_facts;
  const state = schedulerState(commit);
  const compact = compactBefore(request.before);
  const execute = facts.execute !== false;
  const appliedRrule = String(commit.applied_rrule ?? facts.applied_rrule ?? "");
  const alreadyApplied = commit.status === "skipped";
  const event: JsonObject = {
    event_type: ACK_CLASSIFICATION,
    surface: facts.surface ?? "codex_app",
    state_key: facts.state_key ?? "scheduler_hint.app_automation.stateful_backoff",
    applied_rrule: appliedRrule,
    before: compact,
    scheduler_state: state,
  };
  if (commit.stale_hint_accepted) {
    event.expected_rrule = commit.expected_rrule;
    event.stale_hint_accepted = true;
    event.stale_hint_tolerance_minutes = commit.stale_hint_tolerance_minutes;
  }
  const outputBefore = execute ? compact : request.before;
  const hostMatchAck = facts.ack_needed === true && facts.apply_needed === false;
  const payload: SchedulerHeartbeatFollowupResult = {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA,
    ok: true,
    mode: "scheduler-ack",
    dry_run: !execute,
    goal_id: facts.goal_id,
    agent_id: facts.agent_id,
    surface: facts.surface ?? "codex_app",
    state_key: facts.state_key ?? "scheduler_hint.app_automation.stateful_backoff",
    applied_rrule: appliedRrule,
    classification: ACK_CLASSIFICATION,
    generated_at: facts.generated_at,
    appended: false,
    registry_mutated: false,
    scheduler_state_mutated: execute && ["written", "replayed"].includes(commit.status),
    scheduler_commit: commit,
    already_applied: alreadyApplied,
    scheduler_ack_event: event,
    health_check: "scheduler ack state updated; no quota spend",
    delivery_outcome: "surface_only",
    scheduler_state_path: commit.path,
    before: outputBefore,
    after: alreadyApplied ? outputBefore : null,
    post_ack_contract: {
      next_action: "wait_for_next_scheduler_tick_or_material_state_transition",
      do_not_apply_successor_rrule_from_ack_response: true,
      next_rrule_source: "future_quota_should-run_only",
    },
    reason: hostMatchAck
      ? `${execute ? "updated" : "dry-run preview"} scheduler state from matching host RRULE: ${facts.goal_id}/${facts.agent_id} observed ${appliedRrule}`
      : `${execute ? "updated" : "dry-run preview"} scheduler state ack: ${facts.goal_id}/${facts.agent_id} applied ${appliedRrule}`,
  };
  if (hostMatchAck) payload.host_match_ack = true;
  if (request.use_current_hint) {
    payload.used_current_hint = true;
    payload.current_hint_source = "quota.should-run.scheduler_hint";
  }
  return payload;
}

function failureResult(
  request: SchedulerHeartbeatFollowupRequest,
  commit: SchedulerHeartbeatCommitResult,
): SchedulerHeartbeatFollowupResult {
  const facts = request.host_facts;
  const state = schedulerState(commit);
  const compact = compactBefore(request.before);
  const execute = facts.execute !== false;
  const targetRrule = String(commit.target_rrule ?? facts.expected_rrule ?? "");
  const observedRrule = String(
    commit.observed_host_rrule ?? facts.observed_host_rrule ?? "",
  );
  const failure = jsonObject(state?.host_update_failure);
  return {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_RESULT_SCHEMA,
    ok: true,
    mode: "scheduler-fail-current",
    dry_run: !execute,
    goal_id: facts.goal_id,
    agent_id: facts.agent_id,
    surface: facts.surface ?? "codex_app",
    state_key: facts.state_key ?? "scheduler_hint.app_automation.stateful_backoff",
    failed_rrule: targetRrule,
    observed_host_rrule: observedRrule,
    failure_kind: facts.failure_kind ?? "host_tool_failure",
    classification: FAILURE_CLASSIFICATION,
    generated_at: facts.generated_at,
    appended: false,
    registry_mutated: false,
    scheduler_state_mutated: execute && ["written", "replayed"].includes(commit.status),
    scheduler_commit: commit,
    scheduler_failure_event: {
      event_type: FAILURE_CLASSIFICATION,
      surface: facts.surface ?? "codex_app",
      state_key: facts.state_key ?? "scheduler_hint.app_automation.stateful_backoff",
      before: compact,
      scheduler_state: state,
      host_update_failure: failure,
    },
    scheduler_state_path: commit.path,
    health_check: "scheduler host update failure cached; repeated retained target/host pairs suppressed; no quota spend",
    delivery_outcome: "surface_only",
    before: execute ? compact : request.before,
    after: null,
    failure_count: commit.failure_count,
    reason: `${execute ? "recorded" : "dry-run preview"} scheduler host update failure for ${facts.goal_id}/${facts.agent_id}: ${targetRrule}`,
  };
}

export async function evaluateSchedulerHeartbeatFollowup(
  value: unknown,
): Promise<SchedulerHeartbeatFollowupResult> {
  const request = requestObject(value);
  const facts = request.host_facts;
  if (request.require_heartbeat_receipt) {
    const status = await heartbeatReceiptStatus({
      runtimeRoot: request.runtime_root,
      goalId: String(facts.goal_id),
      agentId: String(facts.agent_id),
      turnInstanceId: String(request.turn_instance_id),
    });
    if (status !== "fresh") return receiptFailure(request, status);
  }
  const commit = await evaluateSchedulerHeartbeatHostFacts({
    ...facts,
    runtime_root: request.runtime_root,
  }, {
    heartbeatReceiptTurnInstanceId: request.require_heartbeat_receipt
      ? request.turn_instance_id
      : null,
  });
  const receiptStatus = commitReceiptFailureStatus(commit);
  if (receiptStatus !== null) return receiptFailure(request, receiptStatus);
  if (commit.status === "conflict") return compatibilityFailure(request, commit);
  return followupOperation(facts) === "ack"
    ? ackResult(request, commit)
    : failureResult(request, commit);
}

function pythonScalar(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  return String(value);
}

export function renderSchedulerHeartbeatFollowupMarkdown(
  payload: SchedulerHeartbeatFollowupResult,
): string {
  if (payload.mode === "scheduler-fail-current") {
    const event = jsonObject(payload.scheduler_failure_event) ?? {};
    const state = jsonObject(event.scheduler_state) ?? {};
    const failure = jsonObject(state.host_update_failure) ?? {};
    const before = jsonObject(event.before) ?? {};
    const lines = [
      "# LoopX Quota Scheduler Host Update Failure",
      "",
      `- goal_id: \`${pythonScalar(payload.goal_id)}\``,
      `- classification: \`${pythonScalar(payload.classification)}\``,
      `- agent_id: \`${pythonScalar(payload.agent_id ?? state.agent_id ?? "")}\``,
      `- surface: \`${pythonScalar(payload.surface ?? event.surface)}\``,
      `- state_key: \`${pythonScalar(payload.state_key ?? event.state_key)}\``,
      `- failed_rrule: \`${pythonScalar(payload.failed_rrule ?? failure.target_rrule)}\``,
      `- observed_host_rrule: \`${pythonScalar(payload.observed_host_rrule ?? failure.observed_host_rrule ?? "")}\``,
      `- failure_kind: \`${pythonScalar(payload.failure_kind ?? failure.failure_kind)}\``,
      `- failure_count: \`${pythonScalar(failure.failure_count)}\``,
      `- scheduler_state_mutated: \`${pythonScalar(payload.scheduler_state_mutated)}\``,
      `- effective_action: \`${pythonScalar(before.effective_action)}\``,
      `- should_run: \`${pythonScalar(before.should_run)}\``,
      `- health_check: ${pythonScalar(payload.health_check ?? "scheduler host update failure recorded; no quota spend")}`,
    ];
    if (payload.scheduler_state_path) {
      lines.push(`- scheduler_state_path: \`${pythonScalar(payload.scheduler_state_path)}\``);
    }
    if (payload.reason) lines.push(`- reason: ${pythonScalar(payload.reason)}`);
    return lines.join("\n");
  }
  const event = jsonObject(payload.scheduler_ack_event) ?? {};
  const state = jsonObject(event.scheduler_state) ?? {};
  const before = jsonObject(event.before) ?? {};
  const lines = [
    "# LoopX Quota Scheduler Ack",
    "",
    `- goal_id: \`${pythonScalar(payload.goal_id)}\``,
    `- classification: \`${pythonScalar(payload.classification)}\``,
    `- agent_id: \`${pythonScalar(payload.agent_id ?? event.agent_id ?? state.agent_id ?? "")}\``,
    `- surface: \`${pythonScalar(payload.surface ?? event.surface)}\``,
    `- state_key: \`${pythonScalar(payload.state_key ?? event.state_key)}\``,
    `- applied_rrule: \`${pythonScalar(payload.applied_rrule ?? event.applied_rrule)}\``,
    `- progression_index: \`${pythonScalar(state.progression_index)}\``,
    `- reset_token: \`${pythonScalar(state.reset_token ?? "")}\``,
    `- identity_signature: \`${pythonScalar(state.identity_signature ?? "")}\``,
    `- appended: \`${pythonScalar(payload.appended)}\``,
    `- registry_mutated: \`${pythonScalar(payload.registry_mutated)}\``,
    `- effective_action: \`${pythonScalar(before.effective_action)}\``,
    `- state: \`${pythonScalar(before.state)}\``,
    `- should_run: \`${pythonScalar(before.should_run)}\``,
    `- health_check: ${pythonScalar(payload.health_check ?? "scheduler ack state updated; no quota spend")}`,
  ];
  if (payload.scheduler_state_path) {
    lines.push(`- scheduler_state_path: \`${pythonScalar(payload.scheduler_state_path)}\``);
  }
  if (payload.reason) lines.push(`- reason: ${pythonScalar(payload.reason)}`);
  return lines.join("\n");
}
