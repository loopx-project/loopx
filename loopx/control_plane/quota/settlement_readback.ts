import { isAbsolute, join } from "node:path";

import { readReceiptLogSnapshot } from "../runtime/receipt_log_snapshot.ts";
import {
  committedGoalRolloutEvents,
  heartbeatReceiptsFromEvents,
  readGoalRolloutEventSnapshot,
  strictGoalRolloutEvents,
  type GoalRolloutEventSnapshot,
} from "../rollout_receipt_log.ts";
import {
  effectIdsMatch,
  settlementBindReduce,
  settlementFailed,
  executionRequestSubject, settlementIdentity,
  settlementIdentityPayload,
  settlementIdentityFromPlan,
  settlementPure,
  settlementReceipt,
  settlementResultPayload,
  type JsonObject,
  type SettlementIdentity,
  type SettlementResult,
} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { hasMonitorPollTransaction } from "./monitor_poll_commit.ts";
import { readQuotaSpendEventFromRun } from "./accounting_artifact_transaction.ts";
import {
  jsonObject,
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";
import {
  normalizeDeliveryWorkspaceCausality,
  type DeliveryWorkspaceCausality,
} from "./settlement_workspace_causality.ts";
import {
  isBoundedBlockedRetry,
  isAcceptedBlockedReplanWriteback,
  isUnqualifiedBlockedReplanWriteback,
  qualifySettlementHistory,
  isCommittedMonitorPollEffect,
  isAcceptedProgressWriteback,
  isAcceptedReplanWriteback,
  isCapabilityRetirementWriteback,
  receiptBoundMonitorPhase,
  receiptBoundReplayPhase,
  type ReceiptBoundReplayPhase,
} from "./settlement_phase.ts";
import { isTurnScopedSettlementOutcome } from "../work_items/delivery_outcome.ts";
import {EXECUTION_LOSS_CLOSEOUT_SCHEMA, isExecutionLossCloseout} from "./execution_loss_closeout_record.ts";
import {isDeepStrictEqual} from "node:util";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {
  decodeRefreshRetry,
  isMaterialMonitorPoll,
  refreshRecovery,
  type RefreshRetryRequest,
} from "./refresh_recovery.ts";
import {
  admitsAutonomousReplan,
  heartbeatReceiptDetails as details,
  heartbeatReceiptFactFromEvent,
  normalizeHeartbeatReplanObligationId as normalizeReplanObligationId,
  normalizeHeartbeatTodoId as normalizeTodoId,
  optionalHeartbeatString as optionalString,
  selectAdmissionHeartbeatReceipt,
  selectEffectiveHeartbeatReceipt,
  heartbeatWorkRequalification,
  heartbeatPreparedLease,
  heartbeatPreparedWorkAdmission,
  heartbeatValidationEvent,
  heartbeatSemanticAdmissionEvent,
  qualifyHeartbeatPreparedGeneration,
  qualifyHeartbeatResumeLease,
  type HeartbeatReceiptFact,
} from "./heartbeat_receipt_identity.ts";

import {
  candidateReadbackBinding,
  missingCloseoutReceipts,
  selectCloseoutCandidates,
  type PriorHostTurnCloseoutCandidate,
} from "./prior_turn_closeout.ts";
import { refreshExternalDelivery } from "./refresh_external_delivery.ts";
import { BLOCKED_WAIT_REQUEST_SCHEMA, prepareBlockedWait, RECEIPT_BOUND_WAIT_REQUEST_SCHEMA, projectReceiptBoundWait, isMonitorUnavailableWait } from "./blocked_wait.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import {projectSemanticReplanGuard} from "./semantic_replan_guard.ts";
export {projectSemanticReplanGuard, SEMANTIC_REPLAN_GUARD_SCHEMA} from "./semantic_replan_guard.ts";
import {
  parseQuotaAccountingOwner,
  quotaGoalRef,
  quotaOwnerOwnsProjection,
  withBorrowedQuotaAccountingOwner,
  withQuotaAccountingOwner,
  type QuotaAccountingOwner,
} from "./source_admission.ts";

export const QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA =
  "loopx_quota_settlement_readback_request_v0";
export const QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA =
  "loopx_quota_settlement_readback_result_v0";
export const NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA = "native_task_lease_cleanup_request_v0";
export const NATIVE_TASK_LEASE_CLEANUP_SCHEMA = "native_task_lease_cleanup_v0";

interface NativeTaskLeaseCleanupRequest {
  schema_version: typeof NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA;
  recovery: boolean;
  admission_retry: boolean;
  resume_turn_key_present: boolean;
  original_journal_present: boolean;
}

function decodeNativeTaskLeaseCleanup(value: unknown): NativeTaskLeaseCleanupRequest {
  const request = requireJsonObject(value, "native task lease cleanup request");
  if (request.schema_version !== NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA ||
      Object.keys(request).some(key => !["schema_version", "recovery", "admission_retry",
        "resume_turn_key_present", "original_journal_present"].includes(key))) {
    throw new EffectRuntimeRequestError("native task lease cleanup request shape mismatch");
  }
  return {schema_version: NATIVE_TASK_LEASE_CLEANUP_REQUEST_SCHEMA,
    recovery: requireBoolean(request.recovery, "cleanup.recovery"),
    admission_retry: requireBoolean(request.admission_retry, "cleanup.admission_retry"),
    resume_turn_key_present: requireBoolean(request.resume_turn_key_present, "cleanup.resume_turn_key_present"),
    original_journal_present: requireBoolean(request.original_journal_present, "cleanup.original_journal_present")};
}

/** Receipt cleanup eligibility belongs to the settlement owner. Host/journal
 * facts never substitute for a readable admitted receipt or settle its debt. */
export function projectNativeTaskLeaseCleanup(readback: JsonObject, value: unknown): JsonObject {
  const request = decodeNativeTaskLeaseCleanup(value);
  const disposition = (action: "release" | "retain" | "unconfirmed", reason: string): JsonObject =>
    ({schema_version: NATIVE_TASK_LEASE_CLEANUP_SCHEMA, disposition: action, reason});
  const identity = jsonObject(jsonObject(readback.identity)?.result);
  const failure = jsonObject(identity?.failure);
  if (failure !== null) {
    if (failure.kind === "receipt_unbound") return disposition("release", "receipt_unbound");
    if (failure.kind === "receipt_missing" && !request.recovery && !request.admission_retry
        && !request.resume_turn_key_present && !request.original_journal_present) {
      return disposition("release", "fresh_pre_admission_missing");
    }
    return disposition("unconfirmed", "original_identity_unconfirmed");
  }
  if (identity?.value == null) return disposition("unconfirmed", "original_identity_unconfirmed");
  if (readback.replay_phase === "settled") return disposition("release", "original_turn_settled");
  const receipt = jsonObject(readback.heartbeat_receipt), receiptDetails = jsonObject(receipt?.details);
  if (receiptDetails === null) return disposition("unconfirmed", "original_receipt_unconfirmed");
  const revision = receiptDetails.settlement_receipt_revision, required = receiptDetails.closeout_required;
  if (revision === "turn_reservation" && (!Object.hasOwn(receiptDetails, "closeout_required") || required === false)) {
    return disposition("release", "original_reservation_without_closeout");
  }
  if (["work_admission", "lease_readiness", "identity_upgrade"].includes(String(revision)) && required === true) {
    return disposition("retain", "original_turn_owes_closeout");
  }
  return disposition("unconfirmed", "original_closeout_requirement_unconfirmed");
}

// Where the guard compiles the repair for an owed prior Turn. The repair lane
// depends on the bound Todo's facts, which this read model does not read, so
// the refusal names that compiled contract instead of guessing a command.
const PRIOR_TURN_RECOVERY_ACTIONS_REF = "interaction_contract.cli_channel.next_cli_actions";

interface OwedPriorTurn extends PriorHostTurnCloseoutCandidate {
  missing_receipts: string[];
}

interface ExecutionQualificationHints {
  allow_same_generation_lease_renewal?: boolean;
  validation_recovery_turn_instance_id?: string | null;
}

interface ResumeLeaseCandidate extends ExecutionQualificationHints {
  requested_proof: JsonObject | null;
  provider_context?: {turn_key: string; effect_ref: string | null};
}

interface WritebackCandidate extends ExecutionQualificationHints {
  requested_proof: JsonObject | null;
  effect_ref: string;
  completion_todo_id: string | null;
  completion_turn_key: string | null;
  run: JsonObject;
}

type OwedPriorTurnLookup = (agentId: string, turnInstanceId: string) => OwedPriorTurn | null;

const TURN_INSTANCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/;

interface ReadbackRequest {
  runtime_root: string;
  goal_id: string;
  agent_id: string | null;
  todo_id: string | null;
  turn_instance_id: string | null;
  replan_obligation_id: string | null;
  infer_turn_instance_id: boolean;
  allow_unbound_binding: boolean;
  resolve_original_binding: boolean;
  pending_closeout: boolean;
  borrow_source_admission: boolean;
  include_record_bodies: boolean;
  tolerate_malformed_history: boolean;
  refresh_retry: RefreshRetryRequest | null;
  heartbeat_reentry_guard: JsonObject | null;
  resume_lease_candidate: ResumeLeaseCandidate | null;
  writeback_candidate: WritebackCandidate | null;
  native_task_lease_cleanup: NativeTaskLeaseCleanupRequest | null;
  owner: QuotaAccountingOwner;
}

export interface QuotaSettlementReadbackSnapshot {
  readonly runtimeRoot: string;
  readonly goalId: string;
  readonly events: readonly JsonObject[];
  readonly runs: readonly JsonObject[];
  readonly eventsByTurn: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runsByTurn: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runsByEffectRef: ReadonlyMap<string, readonly JsonObject[]>;
  readonly runPositions: ReadonlyMap<JsonObject, number>;
}

interface ResultBundle extends JsonObject {
  result: SettlementResult;
  payload: JsonObject;
}

interface MatchedSpendRecords {
  run: JsonObject | null;
  receipt: JsonObject | null;
  admissionSource?: unknown;
}

interface SpendTerms {
  slots: unknown;
  source: unknown;
}

function decodeResumeLeaseCandidate(value: unknown): ResumeLeaseCandidate | null {
  if (value === undefined) return null;
  const candidate = requireJsonObject(value, "resume_lease_candidate");
  if (Object.keys(candidate).some(key => !["requested_proof", "provider_context",
      "allow_same_generation_lease_renewal", "validation_recovery_turn_instance_id"].includes(key))
      || !Object.hasOwn(candidate, "requested_proof")) {
    throw new EffectRuntimeRequestError("resume_lease_candidate requires only requested_proof and optional provider_context");
  }
  const hints: ExecutionQualificationHints = {};
  if (Object.hasOwn(candidate, "allow_same_generation_lease_renewal")) {
    hints.allow_same_generation_lease_renewal = requireBoolean(candidate.allow_same_generation_lease_renewal,
      "allow_same_generation_lease_renewal");
  }
  if (Object.hasOwn(candidate, "validation_recovery_turn_instance_id")) {
    const recovery = candidate.validation_recovery_turn_instance_id;
    if (recovery !== null && (typeof recovery !== "string" || !recovery || recovery !== recovery.trim())) {
      throw new EffectRuntimeRequestError("validation_recovery_turn_instance_id requires an exact Turn or null");
    }
    hints.validation_recovery_turn_instance_id = recovery;
  }
  let providerContext: ResumeLeaseCandidate["provider_context"];
  if (Object.hasOwn(candidate, "provider_context")) {
    const context = requireJsonObject(candidate.provider_context, "resume provider_context");
    if (Object.keys(context).length !== 2 || typeof context.turn_key !== "string"
        || !context.turn_key || context.turn_key !== context.turn_key.trim()
        || (context.effect_ref !== null && (typeof context.effect_ref !== "string"
          || !context.effect_ref || context.effect_ref !== context.effect_ref.trim()))) {
      throw new EffectRuntimeRequestError("resume provider_context requires an exact turn_key and nullable effect_ref");
    }
    providerContext = {turn_key: context.turn_key, effect_ref: context.effect_ref};
  }
  const contextFields = {...hints, ...(providerContext === undefined ? {} : {provider_context: providerContext})};
  if (candidate.requested_proof === null) return {requested_proof: null, ...contextFields};
  const proof = requireJsonObject(candidate.requested_proof, "requested resume proof");
  if (Object.keys(proof).length !== 2 || typeof proof.idempotency_key !== "string"
      || !proof.idempotency_key || proof.idempotency_key !== proof.idempotency_key.trim()
      || typeof proof.expected_version !== "number" || !Number.isSafeInteger(proof.expected_version)
      || proof.expected_version < 1) {
    throw new EffectRuntimeRequestError("requested resume proof requires an exact key and positive safe-integer version");
  }
  return {requested_proof: proof, ...contextFields};
}

function decodeWritebackCandidate(value: unknown): WritebackCandidate | null {
  if (value === undefined) return null;
  const candidate = requireJsonObject(value, "writeback_candidate");
  if (Object.keys(candidate).some(key => !["requested_proof", "effect_ref", "completion_todo_id",
      "completion_turn_key", "run", "allow_same_generation_lease_renewal",
      "validation_recovery_turn_instance_id"].includes(key))) {
    throw new EffectRuntimeRequestError("writeback_candidate contains an unsupported operation field");
  }
  const proof = decodeResumeLeaseCandidate({requested_proof: candidate.requested_proof,
    ...(Object.hasOwn(candidate, "allow_same_generation_lease_renewal")
      ? {allow_same_generation_lease_renewal: candidate.allow_same_generation_lease_renewal} : {}),
    ...(Object.hasOwn(candidate, "validation_recovery_turn_instance_id")
      ? {validation_recovery_turn_instance_id: candidate.validation_recovery_turn_instance_id} : {})})!;
  const nullableExactString = (key: string): string | null => {
    if (candidate[key] === null) return null;
    const value = requireNonEmptyString(candidate[key], `writeback_candidate.${key}`);
    if (value !== value.trim()) throw new EffectRuntimeRequestError(`writeback_candidate.${key} must be exact`);
    return value;
  };
  const run = requireJsonObject(candidate.run, "writeback_candidate.run");
  if (Object.keys(run).some(key => !["goal_id", "agent_id", "todo_id", "turn_instance_id",
      "settlement_identity", "goal_ref", "classification", "delivery_outcome", "delivery_batch_scale", "delivery_boundary",
      "progress_observation", "agent_vision", "vision_checkpoint", "autonomous_replan_ack", "refresh_recovery"].includes(key))) {
    throw new EffectRuntimeRequestError("writeback_candidate.run contains an unsupported payload field");
  }
  return {...proof, effect_ref: requireNonEmptyString(candidate.effect_ref, "writeback_candidate.effect_ref"),
    completion_todo_id: nullableExactString("completion_todo_id"),
    completion_turn_key: nullableExactString("completion_turn_key"), run};
}

const ORIGINAL_TURN_SPEND_SLOTS = 1;

type SettlementProgressState = "identity_required" | "writeback_required" |
  "writeback_receipt_required" | "spend_required" | "spend_receipt_required" | "settled";

function settlementScope(
  runtimeRootValue: unknown,
  goalIdValue: unknown,
): { runtimeRoot: string; goalId: string } {
  const runtimeRoot = requireNonEmptyString(runtimeRootValue, "runtime_root");
  if (!isAbsolute(runtimeRoot)) {
    throw new EffectRuntimeRequestError("runtime_root must be absolute");
  }
  const goalId = requireNonEmptyString(goalIdValue, "goal_id");
  if (goalId === "." || goalId === ".." || goalId.includes("/") || goalId.includes("\\")) {
    throw new EffectRuntimeRequestError("goal_id must be a single path segment");
  }
  return {runtimeRoot, goalId};
}

/** Validate the canonical scope before callers perform optional receipt IO. */
export function validateQuotaSettlementScope(
  runtimeRootValue: unknown,
  goalIdValue: unknown,
): { runtimeRoot: string; goalId: string } {
  return settlementScope(runtimeRootValue, goalIdValue);
}

/** Receipt verification owns progress; a durable debit alone is not settlement. */
function settlementProgress(
  identity: SettlementResult, writeback: SettlementResult, spend: SettlementResult,
  writebackRun: JsonObject | null, spendRun: JsonObject | null,
  spendSource: unknown = "heartbeat",
  noSpendKind: "typed_blocked_writeback_no_spend" | "capability_duty_retired_no_spend" | null = null,
): JsonObject {
  const source = spendSource ?? "heartbeat";
  if (source !== "heartbeat" && source !== "visible-goal") {
    throw new EffectRuntimeRequestError("settlement spend source is invalid", "malformed_settlement_state");
  }
  const state: SettlementProgressState = identity.failure ? "identity_required"
    : writeback.failure ? (writebackRun ? "writeback_receipt_required" : "writeback_required")
    : noSpendKind ? "settled"
    : spend.failure ? (spendRun ? "spend_receipt_required" : "spend_required")
    : "settled";
  return {
    schema_version: "quota_settlement_progress_v0", state,
    next_step: identity.failure ? "validation" : writeback.failure ? "durable_writeback"
      : noSpendKind ? null : spend.failure ? "quota_spend" : null,
    quota_spend_source: source,
    ...(noSpendKind ? {
      closeout_kind: noSpendKind,
      no_spend: true,
    } : {}),
  };
}

function optionalRequestString(value: unknown, label: string): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string or null`);
  }
  return value.trim() || null;
}

function normalizeAgentId(value: unknown): string | null {
  const candidate = String(value ?? "").trim().toLowerCase().replace(/\s+/g, "-");
  return candidate && AGENT_ID_PATTERN.test(candidate) ? candidate : null;
}

function decodeRequest(
  value: unknown,
  admittedOwner?: QuotaAccountingOwner,
): ReadbackRequest {
  const request = requireJsonObject(value, "quota settlement readback request");
  if (request.schema_version !== QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA) {
    throw new EffectRuntimeRequestError(
      "Quota settlement readback request schema mismatch",
    );
  }
  const {runtimeRoot, goalId} = settlementScope(
    request.runtime_root,
    request.goal_id,
  );
  if (typeof request.infer_turn_instance_id !== "boolean") {
    throw new EffectRuntimeRequestError("infer_turn_instance_id must be a boolean");
  }
  if (typeof request.allow_unbound_binding !== "boolean") {
    throw new EffectRuntimeRequestError("allow_unbound_binding must be a boolean");
  }
  if (request.resolve_original_binding !== undefined &&
      typeof request.resolve_original_binding !== "boolean") {
    throw new EffectRuntimeRequestError("resolve_original_binding must be a boolean");
  }
  if (request.include_record_bodies !== undefined && typeof request.include_record_bodies !== "boolean") {
    throw new EffectRuntimeRequestError("include_record_bodies must be a boolean");
  }
  if (
    request.tolerate_malformed_history !== undefined &&
    typeof request.tolerate_malformed_history !== "boolean"
  ) {
    throw new EffectRuntimeRequestError(
      "tolerate_malformed_history must be a boolean",
    );
  }
  if (request.resolve_original_binding === true && request.infer_turn_instance_id) {
    throw new EffectRuntimeRequestError("original binding requires an explicit Turn identity");
  }
  if (request.pending_closeout !== undefined && typeof request.pending_closeout !== "boolean") {
    throw new EffectRuntimeRequestError("pending_closeout must be a boolean");
  }
  if (request.pending_closeout === true && (request.infer_turn_instance_id || request.allow_unbound_binding
      || request.refresh_retry != null || request.heartbeat_reentry_guard != null || request.spend_precheck === true)) {
    throw new EffectRuntimeRequestError("pending closeout is a read-only receipt recovery, not inference or settlement admission");
  }
  if (
    request.borrow_source_admission !== undefined
    && typeof request.borrow_source_admission !== "boolean"
  ) {
    throw new EffectRuntimeRequestError(
      "borrow_source_admission must be a boolean",
    );
  }
  if (
    admittedOwner !== undefined
    && (request.goal_ref !== undefined || request.source_admission !== undefined)
  ) {
    throw new EffectRuntimeRequestError(
      "admitted settlement readback must use its parsed quota owner",
      "quota_source_admission_invalid",
    );
  }
  if (
    admittedOwner?.kind === "exact_source"
    && admittedOwner.goalRef.goalId.value !== goalId
  ) {
    throw new EffectRuntimeRequestError(
      "admitted quota owner does not match settlement goal_id",
      "quota_source_admission_invalid",
    );
  }
  const owner = admittedOwner ?? parseQuotaAccountingOwner({
    goalRefValue: request.goal_ref,
    sourceAdmissionValue: request.source_admission,
    runtimeRoot,
    goalId,
  });
  if (request.borrow_source_admission === true && owner.kind !== "exact_source") {
    throw new EffectRuntimeRequestError(
      "borrow_source_admission requires exact source admission",
      "quota_source_admission_invalid",
    );
  }
  const resumeCandidate = decodeResumeLeaseCandidate(request.resume_lease_candidate);
  if (resumeCandidate !== null && (request.infer_turn_instance_id
      || request.allow_unbound_binding || request.pending_closeout === true
      || optionalRequestString(request.agent_id, "agent_id") === null
      || optionalRequestString(request.todo_id, "todo_id") === null
      || optionalRequestString(request.turn_instance_id, "turn_instance_id") === null)) {
    throw new EffectRuntimeRequestError("resume lease qualification requires an exact Agent/Todo/Turn readback");
  }
  const writebackCandidate = decodeWritebackCandidate(request.writeback_candidate);
  if (writebackCandidate !== null && (resumeCandidate !== null || request.infer_turn_instance_id
      || request.allow_unbound_binding || request.resolve_original_binding || request.pending_closeout
      || request.refresh_retry == null || request.heartbeat_reentry_guard != null || request.spend_precheck === true
      || request.tolerate_malformed_history === true
      || optionalRequestString(request.agent_id, "agent_id") === null
      || optionalRequestString(request.todo_id, "todo_id") === null
      || optionalRequestString(request.turn_instance_id, "turn_instance_id") === null
      || optionalRequestString(request.replan_obligation_id, "replan_obligation_id") !== null)) {
    throw new EffectRuntimeRequestError("writeback qualification requires only an explicit original Todo operation and payload");
  }
  if (writebackCandidate !== null) {
    const retry = requireJsonObject(request.refresh_retry, "writeback refresh_retry");
    if (Object.keys(retry).some(key => !["checkpoint_read_context_id", "external_delivery", "vision",
        "unchanged_reason", "merge_patch", "workspace_requested", "mutation", "delivery_outcome",
        "delivery_batch_scale", "delivery_boundary", "progress_observation"].includes(key))) {
      throw new EffectRuntimeRequestError("writeback refresh_retry contains an unsupported operation field");
    }
  }
  const nativeCleanup = request.native_task_lease_cleanup === undefined ? null
    : decodeNativeTaskLeaseCleanup(request.native_task_lease_cleanup);
  if (nativeCleanup !== null && (request.infer_turn_instance_id || request.allow_unbound_binding
      || request.resolve_original_binding || request.pending_closeout || request.refresh_retry != null
      || request.heartbeat_reentry_guard != null || request.spend_precheck === true
      || request.tolerate_malformed_history === true || resumeCandidate !== null || writebackCandidate !== null
      || !optionalRequestString(request.agent_id, "agent_id")
      || !optionalRequestString(request.todo_id, "todo_id")
      || !optionalRequestString(request.turn_instance_id, "turn_instance_id")
      || optionalRequestString(request.replan_obligation_id, "replan_obligation_id") !== null)) {
    throw new EffectRuntimeRequestError("native task lease cleanup requires only an explicit original Todo Turn read");
  }
  return {
    runtime_root: runtimeRoot,
    goal_id: goalId,
    agent_id: optionalRequestString(request.agent_id, "agent_id"),
    todo_id: optionalRequestString(request.todo_id, "todo_id"),
    turn_instance_id: optionalRequestString(
      request.turn_instance_id,
      "turn_instance_id",
    ),
    replan_obligation_id: optionalRequestString(
      request.replan_obligation_id,
      "replan_obligation_id",
    ),
    infer_turn_instance_id: request.infer_turn_instance_id,
    allow_unbound_binding: request.allow_unbound_binding,
    resolve_original_binding: request.resolve_original_binding === true,
    pending_closeout: request.pending_closeout === true,
    borrow_source_admission: request.borrow_source_admission === true,
    include_record_bodies: request.include_record_bodies !== false,
    tolerate_malformed_history: request.tolerate_malformed_history === true,
    refresh_retry: decodeRefreshRetry(request.refresh_retry),
    heartbeat_reentry_guard: request.heartbeat_reentry_guard == null ? null
      : requireJsonObject(request.heartbeat_reentry_guard, "heartbeat_reentry_guard"),
    resume_lease_candidate: resumeCandidate,
    writeback_candidate: writebackCandidate,
    native_task_lease_cleanup: nativeCleanup,
    owner,
  };
}

async function readRunReceipts(
  path: string,
  tolerateMalformedHistory: boolean = false,
): Promise<readonly JsonObject[]> {
  const snapshot = await readReceiptLogSnapshot(path);
  if (!tolerateMalformedHistory && snapshot?.firstErrorLine != null) {
    throw new EffectRuntimeRequestError(
      `settlement readback line ${snapshot.firstErrorLine} is malformed`,
      "malformed_settlement_state",
    );
  }
  return snapshot?.records ?? [];
}

function turnKey(
  goalId: string | null,
  agentId: string | null,
  turnInstanceId: string | null,
): string | null {
  return goalId && agentId && turnInstanceId
    ? `${goalId}\u0000${agentId}\u0000${turnInstanceId}`
    : null;
}

function appendIndexed(
  index: Map<string, JsonObject[]>,
  key: string | null,
  record: JsonObject,
): void {
  if (key === null) return;
  const existing = index.get(key);
  if (existing) existing.push(record);
  else index.set(key, [record]);
}

function indexSettlementSnapshot(
  runtimeRoot: string,
  goalId: string,
  events: readonly JsonObject[],
  runs: readonly JsonObject[],
): QuotaSettlementReadbackSnapshot {
  const eventsByTurn = new Map<string, JsonObject[]>();
  const runsByTurn = new Map<string, JsonObject[]>();
  const runsByEffectRef = new Map<string, JsonObject[]>();
  const runPositions = new Map<JsonObject, number>();
  for (const event of events) {
    appendIndexed(
      eventsByTurn,
      turnKey(
        optionalString(event.goal_id),
        optionalString(event.agent_id),
        optionalString(event.run_id),
      ),
      event,
    );
  }
  for (const [position, run] of runs.entries()) {
    runPositions.set(run, position);
    appendIndexed(
      runsByTurn,
      turnKey(
        optionalString(run.goal_id),
        normalizeAgentId(run.agent_id),
        optionalString(run.turn_instance_id),
      ),
      run,
    );
    appendIndexed(runsByEffectRef, optionalString(run.effect_ref), run);
  }
  return {
    runtimeRoot,
    goalId,
    events,
    runs,
    eventsByTurn,
    runsByTurn,
    runsByEffectRef,
    runPositions,
  };
}

/** Load one immutable settlement snapshot for one or many readbacks. */
export async function readQuotaSettlementSnapshot(
  runtimeRoot: string,
  goalId: string,
  rolloutSnapshot?: GoalRolloutEventSnapshot | null,
  tolerateMalformedHistory: boolean = false,
): Promise<QuotaSettlementReadbackSnapshot> {
  settlementScope(runtimeRoot, goalId);
  if (
    rolloutSnapshot !== undefined &&
    rolloutSnapshot !== null &&
    (
      rolloutSnapshot.runtimeRoot !== runtimeRoot ||
      rolloutSnapshot.goalId !== goalId
    )
  ) {
    throw new EffectRuntimeRequestError(
      "rollout event snapshot does not match the settlement scope",
      "settlement_snapshot_scope_mismatch",
    );
  }
  const goalRoot = join(runtimeRoot, "goals", goalId);
  const [snapshot, runs] = await Promise.all([
    rolloutSnapshot === undefined
      ? readGoalRolloutEventSnapshot(runtimeRoot, goalId)
      : Promise.resolve(rolloutSnapshot),
    readRunReceipts(join(goalRoot, "runs", "index.jsonl"), tolerateMalformedHistory),
  ]);
  // Settlement-authority callers keep failing closed on the first damaged row.
  // The scheduler follow-up route only needs the receipts it can actually read,
  // so it opts into the same tolerant view the heartbeat reader already uses.
  // Both views still ignore an unterminated row: the append owner will drop it
  // before writing the next record.
  return indexSettlementSnapshot(
    runtimeRoot,
    goalId,
    tolerateMalformedHistory
      ? committedGoalRolloutEvents(snapshot)
      : strictGoalRolloutEvents(snapshot),
    runs,
  );
}

function indexedEvents(
  snapshot: QuotaSettlementReadbackSnapshot,
  goalId: string,
  agentId: string,
  turnInstanceId: string,
): readonly JsonObject[] {
  return snapshot.eventsByTurn.get(
    turnKey(goalId, agentId, turnInstanceId)!,
  ) ?? [];
}

function indexedRuns(
  snapshot: QuotaSettlementReadbackSnapshot,
  identity: SettlementIdentity,
): readonly JsonObject[] {
  return snapshot.runsByTurn.get(
    turnKey(identity.goal_id, identity.agent_id, identity.turn_instance_id)!,
  ) ?? [];
}

/** One committed-poll rule for settlement and prior-Turn closeout. */
export function committedMonitorPollFromSnapshot(
  snapshot: QuotaSettlementReadbackSnapshot,
  identity: Pick<SettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id" | "todo_id">,
  owner: QuotaAccountingOwner = {kind: "alias"},
): JsonObject | null {
  if (snapshot.goalId !== identity.goal_id) {
    throw new EffectRuntimeRequestError("monitor poll snapshot scope mismatch");
  }
  const runs = snapshot.runsByTurn.get(
    turnKey(identity.goal_id, identity.agent_id, identity.turn_instance_id)!,
  ) ?? [];
  return [...runs].reverse().find((run) =>
    quotaOwnerOwnsProjection(owner, run.goal_ref) &&
    run.classification === "quota_monitor_poll" &&
    optionalString(run.goal_id) === identity.goal_id &&
    optionalString(run.agent_id) === identity.agent_id &&
    optionalString(run.turn_instance_id) === identity.turn_instance_id &&
    normalizeTodoId(run.todo_id) === identity.todo_id &&
    isCommittedMonitorPollEffect(jsonObject(run.quota_monitor_poll_commit)?.effect_id, identity)
  ) ?? null;
}

function spendCandidateRuns(
  snapshot: QuotaSettlementReadbackSnapshot,
  identity: SettlementIdentity,
  turnRuns: readonly JsonObject[],
): readonly JsonObject[] {
  const effectRuns = snapshot.runsByEffectRef.get(
    `${identity.effect_id}#quota_spend`,
  ) ?? [];
  if (effectRuns.length === 0) return turnRuns;
  return [...new Set([...turnRuns, ...effectRuns])].sort(
    (left, right) =>
      (snapshot.runPositions.get(left) ?? -1) -
      (snapshot.runPositions.get(right) ?? -1),
  );
}

function persistedIdentityMatches(
  rawIdentity: unknown,
  expectedEffectId: string,
): boolean {
  if (rawIdentity === undefined) return true;
  const identity = jsonObject(rawIdentity);
  if (!identity) return false;
  return optionalString(identity.effect_id) === expectedEffectId;
}

function quotaSpendMetadataMatches(
  rawMetadata: unknown,
  effectRef: string | null,
  expectedEffectRef: string,
): boolean {
  if (rawMetadata === undefined) return true;
  const metadata = jsonObject(rawMetadata);
  const metadataEffectId = optionalString(metadata?.effect_id);
  return metadataEffectId === expectedEffectRef &&
    (effectRef === null || metadataEffectId === effectRef);
}

function runEffectMatches(
  run: JsonObject,
  identity: SettlementIdentity,
  stepKind: "durable_writeback" | "quota_spend",
): boolean {
  const expectedEffectId = identity.effect_id;
  const expectedEffectRef = `${expectedEffectId}#${stepKind}`;
  const effectRef = optionalString(run.effect_ref);
  return persistedIdentityMatches(run.settlement_identity, expectedEffectId) &&
    (!effectRef || effectRef === expectedEffectRef) &&
    (stepKind !== "quota_spend" ||
      quotaSpendMetadataMatches(run.quota_spend_commit, effectRef, expectedEffectRef));
}

/** Upgrade a legacy bound review receipt without changing its Turn identity.
 *
 * Older receipts retained the selected replan obligation but not the review
 * source that proves a regenerated frontier still represents the same duty.
 * The correction is append-only: the caller serializes the returned details
 * under the receipt-log lock.  It is admitted only while closeout has not
 * started and both the old and current guards select this exact binding.
 */
function requalifyHeartbeatReceipt(
  heartbeatReceipt: JsonObject,
  currentDetails: JsonObject,
  identity: SettlementIdentity,
  replayPhase: ReceiptBoundReplayPhase,
  closeoutStarted: boolean,
): JsonObject {
  const work = heartbeatWorkRequalification(
    heartbeatReceipt,
    currentDetails,
    identity,
    replayPhase,
    closeoutStarted,
  );
  if (work.append === true || replayPhase !== "open" || closeoutStarted ||
      identity.binding_kind !== "autonomous_replan") return work;

  const originalDetails = details(heartbeatReceipt);
  const originalGuard = projectSemanticReplanGuard(originalDetails);
  const currentGuard = projectSemanticReplanGuard(currentDetails);
  if (originalGuard.selected_obligation_id !== identity.replan_obligation_id ||
      currentGuard.selected_obligation_id !== identity.replan_obligation_id ||
      originalGuard.selected_review_source != null ||
      currentGuard.selected_review_source == null) return work;

  const upgraded = {...originalDetails};
  for (const field of ["semantic_replan_review_kind", "semantic_replan_review_stop_condition",
    "semantic_replan_review_stop_condition_sha256", "semantic_replan_review_stop_contract_id"]) {
    if (currentDetails[field] !== undefined) upgraded[field] = currentDetails[field]!;
  }
  upgraded.settlement_receipt_revision = "review_guard_upgrade";
  return {append: true, details: upgraded};
}

function runMatchesBinding(run: JsonObject, identity: SettlementIdentity): boolean {
  return normalizeTodoId(run.todo_id) === identity.todo_id &&
    normalizeReplanObligationId(run.replan_obligation_id) ===
      identity.replan_obligation_id;
}

function findWriteback(
  runs: readonly JsonObject[],
  identity: SettlementIdentity,
): JsonObject | null {
  return [...runs].reverse().find((run) =>
    optionalString(run.goal_id) === identity.goal_id &&
    optionalString(run.turn_instance_id) === identity.turn_instance_id &&
    runMatchesBinding(run, identity) &&
    normalizeAgentId(run.agent_id) === identity.agent_id &&
    runEffectMatches(run, identity, "durable_writeback") &&
    isTurnScopedSettlementOutcome(
      run.delivery_outcome,
      run.progress_observation,
      identity.todo_id ?? identity.replan_obligation_id ?? (identity.request_subject ? identity.binding_id : null),
    )
  ) ?? null;
}

function findSpend(
  runs: readonly JsonObject[],
  identity: SettlementIdentity,
): JsonObject | null {
  return [...runs].reverse().find((run) =>
    run.classification === "quota_slot_spent" &&
    optionalString(run.goal_id) === identity.goal_id &&
    normalizeAgentId(run.agent_id) === identity.agent_id &&
    (
      (optionalString(run.turn_instance_id) === identity.turn_instance_id &&
        runMatchesBinding(run, identity) &&
        runEffectMatches(run, identity, "quota_spend")) ||
      // Older quota commit rows predate persisted turn bindings. Their
      // exact effect ref is the durable identity for this replay path.
      optionalString(run.effect_ref) === `${identity.effect_id}#quota_spend` &&
        runEffectMatches(run, identity, "quota_spend")
    )
  ) ?? null;
}

function findStepEvent(
  events: readonly JsonObject[],
  identity: SettlementIdentity,
  eventKind: string,
): JsonObject | null {
  return [...events].reverse().find((event) =>
    event.event_kind === eventKind &&
    optionalString(event.goal_id) === identity.goal_id &&
    optionalString(event.agent_id) === identity.agent_id &&
    optionalString(event.run_id) === identity.turn_instance_id &&
    optionalString(details(event).settlement_effect_id) === identity.effect_id
  ) ?? null;
}

/**
 * Resolve the Turn's effective receipt, or null when it persisted no guard.
 *
 * The identity rule lives in one module, so the readback resolves the effective
 * receipt through the same reduction the closeout selector uses.
 */
function effectiveHeartbeatReceipt(
  events: readonly JsonObject[],
  identity: Pick<SettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id">,
): JsonObject | null {
  const entries: { fact: HeartbeatReceiptFact; value: JsonObject }[] = [];
  for (const event of events) {
    if (event.event_kind !== "quota_should_run") continue;
    if (optionalString(event.goal_id) !== identity.goal_id) continue;
    if (optionalString(event.agent_id) !== identity.agent_id) continue;
    if (optionalString(event.run_id) !== identity.turn_instance_id) continue;
    entries.push({ fact: heartbeatReceiptFactFromEvent(event), value: event });
  }
  return entries.length === 0
    ? null
    : selectEffectiveHeartbeatReceipt(identity.goal_id, identity.agent_id, entries);
}

/** First bound receipt in the same already filtered Turn list. */
function admissionHeartbeatReceipt(
  events: readonly JsonObject[],
  identity: Pick<SettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id">,
): JsonObject | null {
  const entries: { fact: HeartbeatReceiptFact; value: JsonObject }[] = [];
  for (const event of events) {
    if (event.event_kind !== "quota_should_run") continue;
    if (optionalString(event.goal_id) !== identity.goal_id) continue;
    if (optionalString(event.agent_id) !== identity.agent_id) continue;
    if (optionalString(event.run_id) !== identity.turn_instance_id) continue;
    entries.push({ fact: heartbeatReceiptFactFromEvent(event), value: event });
  }
  return entries.length === 0
    ? null
    : selectAdmissionHeartbeatReceipt(identity.goal_id, identity.agent_id, entries);
}

function writebackResult(
  identity: SettlementIdentity,
  run: JsonObject | null,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!run || !event) {
    return settlementFailed<JsonObject>({
      kind: "writeback_missing",
      step_kind: "durable_writeback",
      reason:
        "matching accountable refresh-state receipt is missing for the original settlement identity",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(run, [settlementReceipt(
    identity,
    "durable_writeback",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function spendResult(
  identity: SettlementIdentity,
  run: JsonObject | null,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!run || !event) {
    return settlementFailed<JsonObject>({
      kind: "receipt_missing",
      step_kind: "quota_spend",
      reason: "matching quota spend receipt is missing",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(run, [settlementReceipt(
    identity,
    "quota_spend",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function terminalResult(
  identity: SettlementIdentity,
  event: JsonObject | null,
): SettlementResult<JsonObject> {
  if (!event || details(event).no_followup !== true) {
    return settlementFailed<JsonObject>({
      kind: "receipt_missing",
      step_kind: "terminal_closeout",
      reason: "matching terminal no-follow-up closeout receipt is missing",
    });
  }
  const eventId = optionalString(event.event_id);
  return settlementPure(event, [settlementReceipt(
    identity,
    "terminal_closeout",
    eventId ? `rollout_event:${eventId}` : null,
  )]);
}

function inferPersistedIdentity(
  runs: readonly JsonObject[],
  goalId: string,
  agentId: string,
  requestedTodoId: string | null,
  allowUnboundBinding: boolean,
): SettlementResult<JsonObject> | null {
  for (const run of [...runs].reverse()) {
    const runAgentId = normalizeAgentId(run.agent_id);
    if (runAgentId && runAgentId !== agentId) continue;
    const classification = String(run.classification ?? "").trim();
    if (
      classification === "quota_slot_voided" ||
      classification === "quota_scheduler_ack" ||
      (classification === "quota_monitor_poll" && !isMaterialMonitorPoll(run)) ||
      (classification === "state_refreshed" &&
        !isTurnScopedSettlementOutcome(
          run.delivery_outcome,
          run.progress_observation,
          normalizeTodoId(run.todo_id) ??
            normalizeReplanObligationId(run.replan_obligation_id),
        ))
    ) {
      continue;
    }
    if (
      classification !== "quota_slot_spent" &&
      !isTurnScopedSettlementOutcome(
        run.delivery_outcome,
        run.progress_observation,
        normalizeTodoId(run.todo_id) ??
          normalizeReplanObligationId(run.replan_obligation_id),
      )
    ) {
      return null;
    }
    const candidateAgentId = normalizeAgentId(run.agent_id);
    if (allowUnboundBinding && candidateAgentId !== agentId) {
      return failedIdentity(
        "persisted settlement identity mismatch: accountable run is not bound to the requesting Agent",
        "identity_mismatch",
      );
    }
    const persisted = jsonObject(run.settlement_identity) ?? {};
    let todoId = normalizeTodoId(run.todo_id);
    let replanObligationId = normalizeReplanObligationId(
      run.replan_obligation_id,
    );
    if (requestedTodoId) {
      if (todoId !== requestedTodoId) return null;
      replanObligationId = null;
    } else {
      if (!allowUnboundBinding || Object.keys(persisted).length === 0) {
        return failedIdentity(
          "unbound visible-goal settlement recovery requires a fully typed persisted identity",
          "identity_mismatch",
        );
      }
      const persistedTodoId = normalizeTodoId(persisted.todo_id);
      const persistedReplanObligationId = normalizeReplanObligationId(
        persisted.replan_obligation_id,
      );
      if (Boolean(persistedTodoId) === Boolean(persistedReplanObligationId)) {
        return failedIdentity(
          "persisted settlement identity must contain exactly one Todo or autonomous replan binding",
          "identity_mismatch",
        );
      }
      if (todoId !== persistedTodoId) {
        return failedIdentity(
          "persisted settlement identity mismatch: Todo binding differs from the accountable run",
          "identity_mismatch",
        );
      }
      if (replanObligationId !== persistedReplanObligationId) {
        return failedIdentity(
          "persisted settlement identity mismatch: autonomous replan binding differs from the accountable run",
          "identity_mismatch",
        );
      }
      todoId = persistedTodoId;
      replanObligationId = persistedReplanObligationId;
    }
    const turnInstanceId = optionalString(run.turn_instance_id);
    if (!turnInstanceId) {
      return allowUnboundBinding
        ? failedIdentity(
          "persisted settlement identity mismatch: turn_instance_id differs from the accountable run",
          "identity_mismatch",
        )
        : null;
    }
    const identity = settlementIdentity({
      goal_id: goalId,
      agent_id: agentId,
      todo_id: todoId,
      turn_instance_id: turnInstanceId,
      replan_obligation_id: replanObligationId,
    });
    const expectedIdentity = settlementIdentityPayload(identity);
    if (
      allowUnboundBinding &&
      optionalString(persisted.turn_instance_id) !== turnInstanceId
    ) {
      return failedIdentity(
        "persisted settlement identity mismatch: turn_instance_id differs from the accountable run",
        "identity_mismatch",
      );
    }
    for (const [field, expected] of Object.entries(expectedIdentity)) {
      const actual = optionalString(persisted[field]);
      if (
        (allowUnboundBinding && actual !== expected) ||
        (!allowUnboundBinding && actual && actual !== expected)
      ) {
        return failedIdentity(
          `persisted settlement identity mismatch: ${field} is ${actual ?? "missing"} but expected ${expected}`,
          "identity_mismatch",
        );
      }
    }
    return settlementPure(settlementIdentityPayload(identity));
  }
  return null;
}

function failedIdentity(
  reason: string,
  kind:
    | "invalid_identity"
    | "identity_mismatch"
    | "receipt_missing"
    | "receipt_unbound",
  details?: JsonObject,
) {
  return settlementFailed<JsonObject>({
    kind,
    step_kind: "validation",
    reason,
    ...(details ? { details } : {}),
  });
}

function resolveIdentity(
  request: ReadbackRequest,
  events: readonly JsonObject[],
  runs: readonly JsonObject[],
  owedPriorTurn: OwedPriorTurnLookup | null,
): SettlementResult<JsonObject> | null {
  let agentId = normalizeAgentId(request.agent_id);
  let todoId = normalizeTodoId(request.todo_id);
  let replanObligationId = normalizeReplanObligationId(
    request.replan_obligation_id,
  );
  let turnInstanceId = request.turn_instance_id;
  let requestSubject = null;
  if (!todoId && !replanObligationId && agentId && turnInstanceId) {
    try {
      const original = effectiveHeartbeatReceipt(events, {goal_id: request.goal_id, agent_id: agentId, turn_instance_id: turnInstanceId});
      requestSubject = executionRequestSubject(original ? heartbeatReceiptFactFromEvent(original).request_subject : null);
    } catch (error) {
      return failedIdentity((error as Error).message, "identity_mismatch");
    }
  }
  if (request.infer_turn_instance_id) {
    if (
      !agentId ||
      replanObligationId ||
      (!todoId && !request.allow_unbound_binding)
    ) return null;
    const inferred = inferPersistedIdentity(
      runs,
      request.goal_id,
      agentId,
      todoId,
      request.allow_unbound_binding,
    );
    if (inferred === null) return null;
    if (inferred.failure || inferred.value === null) return inferred;
    todoId = normalizeTodoId(inferred.value.todo_id);
    replanObligationId = normalizeReplanObligationId(
      inferred.value.replan_obligation_id,
    );
    turnInstanceId = optionalString(inferred.value.turn_instance_id);
  }
  if (turnInstanceId && !TURN_INSTANCE_ID_PATTERN.test(turnInstanceId)) {
    return failedIdentity(
      "turn_instance_id must be 1-128 public-safe letters, numbers, or ._:-",
      "invalid_identity",
    );
  }
  if (request.resolve_original_binding && agentId && turnInstanceId) {
    // Resolve only this exact Turn's committed guard. This is a read, never
    // the guard's binder, a current-Todo selection or a latest-run inference.
    let guard: JsonObject | null;
    try {
      guard = effectiveHeartbeatReceipt(events, {
        goal_id: request.goal_id, agent_id: agentId, turn_instance_id: turnInstanceId,
      });
    } catch (error) {
      return failedIdentity((error as Error).message, "identity_mismatch");
    }
    if (!guard) return failedIdentity("matching Turn guard is missing", "receipt_missing");
    const fact = heartbeatReceiptFactFromEvent(guard);
    const originalTodoId = normalizeTodoId(fact.todo_id);
    const originalReplanId = normalizeReplanObligationId(fact.replan_obligation_id);
    const retainedTodoId = normalizeTodoId(
      details(guard).pending_action_selection_todo_id,
    );
    if (!originalTodoId && !originalReplanId && !requestSubject) {
      if (todoId === null && replanObligationId === null) {
        return failedIdentity("the original Turn guard has no settlement binding", "receipt_unbound");
      }
    } else {
      const requestedTodoMatchesOriginal = request.todo_id === null ||
        (todoId !== null && (
          todoId === originalTodoId ||
          (originalReplanId !== null && todoId === retainedTodoId)
        ));
      if (!requestedTodoMatchesOriginal ||
          (request.replan_obligation_id !== null &&
            (replanObligationId === null || replanObligationId !== originalReplanId))) {
        return failedIdentity(
          "settlement binding does not match: requested binding differs from the original Turn guard",
          "identity_mismatch",
        );
      }
      todoId = originalTodoId;
      replanObligationId = originalReplanId;
    }
  }
  if (!agentId || !turnInstanceId || [todoId, replanObligationId, requestSubject].filter(Boolean).length !== 1) {
    return failedIdentity(
      "turn-scoped settlement requires agent_id, turn_instance_id, and exactly one todo_id or replan_obligation_id",
      "invalid_identity",
    );
  }
  const identity = settlementIdentity({
    goal_id: request.goal_id,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: turnInstanceId,
    replan_obligation_id: replanObligationId, request_subject: requestSubject,
  });
  let receiptEvent: JsonObject | null;
  try {
    receiptEvent = effectiveHeartbeatReceipt(events, identity);
  } catch (error) {
    return failedIdentity((error as Error).message, "identity_mismatch");
  }
  if (!receiptEvent) {
    return failedIdentity(
      "matching quota should-run heartbeat receipt is missing; rerun the guard with the same turn_instance_id",
      "receipt_missing",
    );
  }
  const receiptDetails = details(receiptEvent);
  const receiptTodoId = normalizeTodoId(receiptDetails.todo_id);
  const receiptReplanObligationId = normalizeReplanObligationId(
    receiptDetails.replan_obligation_id,
  );
  if (receiptTodoId === null && receiptReplanObligationId === null && heartbeatReceiptFactFromEvent(receiptEvent).request_subject == null) {
    // A same-turn guard that ran before any work item was chosen commits a
    // receipt with no settlement binding, and the documented wake order (guard,
    // then select) produces exactly that state. This read model never binds --
    // the guard's own same-turn reconciliation owns that, so there is one
    // binder rather than two -- which means the caller has to be told the state
    // and the exact repair instead of being handed a binding mismatch it cannot
    // act on. The state also gets its own failure kind, so a consumer can branch
    // on the missing binding without reading details.binding_kind: the receipt
    // exists and is well-formed here, which is not what identity_mismatch means.
    //
    // A Turn whose explicit choice the guard already deferred is a different
    // state with a different repair: rebinding through `--todo-id` re-enters the
    // same preemption and defers again, while the guard's argument-less reentry
    // binds the preemption it is actually holding. Naming the wrong command
    // costs the caller a turn, so the repair is chosen from the retained
    // selection the guard recorded.
    //
    // Neither rebind can succeed while an earlier Turn of this Agent owes its
    // closeout: the guard preempts selection with that Turn's recovery and
    // replays it for every later Turn. Both repairs above then only replay, so
    // the owed Turn is named from the same candidate rule the guard applies.
    const deferredSelectionTodoId = normalizeTodoId(
      receiptDetails.pending_action_selection_todo_id,
    );
    const owed = owedPriorTurn?.(identity.agent_id, turnInstanceId) ?? null;
    const repair = owed !== null
      ? `an earlier Turn owes its closeout (turn_instance_id ${owed.prior_turn_instance_id}, ` +
        `${owed.binding_kind} ${owed.binding_id}, missing ${owed.missing_receipts.join(", ")}), ` +
        "so the guard replays that recovery instead of binding this turn and a " +
        "rebind of this turn cannot succeed; settle the earlier Turn first by " +
        `following the guard's ${PRIOR_TURN_RECOVERY_ACTIONS_REF} ` +
        `(quota should-run --turn-instance-id ${turnInstanceId} returns them), ` +
        "then rerun the guard for this turn and settle with the identity it returns"
      : deferredSelectionTodoId === null
      ? "rebind it through the guard's same-turn reconciliation, then settle: " +
        "quota should-run --turn-instance-id " +
        `${turnInstanceId} --todo-id ${identity.todo_id ?? "<todo_id>"}`
      : "the guard deferred this turn's explicit selection instead of binding " +
        "it, so rerun the guard for the same turn without --todo-id (which " +
        "binds the preemption it is holding), then settle with the identity it " +
        `returns: quota should-run --turn-instance-id ${turnInstanceId}`;
    return failedIdentity(
      "the quota should-run receipt for this turn carries no settlement binding " +
        `yet (turn_instance_id ${turnInstanceId}); ${repair}`,
      "receipt_unbound",
      {
        binding_kind: "unbound",
        requested_binding_kind: identity.binding_kind,
        turn_instance_id: turnInstanceId,
        ...(deferredSelectionTodoId === null
          ? {}
          : { deferred_selection_todo_id: deferredSelectionTodoId }),
        ...(owed === null ? {} : {
          owed_prior_turn: owed,
          recovery_actions_ref: PRIOR_TURN_RECOVERY_ACTIONS_REF,
        }),
      },
    );
  }
  if (
    receiptTodoId !== identity.todo_id ||
    receiptReplanObligationId !== identity.replan_obligation_id
  ) {
    return failedIdentity(
      "settlement binding does not match the original quota guard: " +
        `receipt todo=${receiptTodoId ?? "missing"} and replan_obligation_id=` +
        `${receiptReplanObligationId ?? "missing"}, requested todo=` +
        `${identity.todo_id ?? "missing"} and replan_obligation_id=` +
        `${identity.replan_obligation_id ?? "missing"}`,
      "identity_mismatch",
    );
  }
  const receiptEffectId = optionalString(receiptDetails.settlement_effect_id);
  if (!effectIdsMatch(receiptEffectId, identity.effect_id)) {
    return failedIdentity(
      "settlement effect does not match the original quota guard: " +
        `receipt effect is ${receiptEffectId} but expected ${identity.effect_id}`,
      "identity_mismatch",
    );
  }
  const validationEvent = heartbeatValidationEvent(events, receiptEvent, identity);
  if (validationEvent === null) {
    return failedIdentity("matching work admission is missing for Turn readiness", "receipt_missing");
  }
  const eventId = optionalString(validationEvent.event_id);
  return settlementPure(
    settlementIdentityPayload(identity),
    [settlementReceipt(identity, "validation", eventId ? `rollout_event:${eventId}` : null)],
  );
}

function bundle(result: SettlementResult, includeRecordBody = true): ResultBundle {
  return {result: includeRecordBody ? result : {...result, value: null},
    payload: settlementResultPayload(result)};
}

function failedAfterIdentity(
  identityResult: SettlementResult<JsonObject>,
): SettlementResult<JsonObject> {
  return identityResult.failure
    ? { value: null, receipts: [...identityResult.receipts], failure: { ...identityResult.failure } }
    : settlementFailed({
      kind: "invalid_identity",
      step_kind: "validation",
      reason: "quota settlement readback has no identity",
    });
}

function failedReadback(
  identityResult: SettlementResult<JsonObject>,
): JsonObject {
  const downstreamFailure = failedAfterIdentity(identityResult);
  const terminalFailure = settlementFailed<JsonObject>({
    kind: "receipt_missing",
    step_kind: "terminal_closeout",
    reason: "matching terminal no-follow-up closeout receipt is missing",
  });
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
    found: true,
    identity: bundle(identityResult),
    writeback: bundle(downstreamFailure),
    spend: bundle(downstreamFailure),
    delivery: bundle(downstreamFailure),
    settlement: bundle(downstreamFailure),
    terminal_closeout: bundle(terminalFailure),
    terminal_settlement: bundle(downstreamFailure),
    progress: settlementProgress(identityResult, downstreamFailure, downstreamFailure, null, null),
    workspace_causality: null,
    semantic_replan_guard: null,
    writeback_run: null,
    spend_run: null,
    heartbeat_receipt: null,
    writeback_event: null,
    spend_event: null,
    completion_event: null,
    monitor_phase: null,
    replay_phase: null,
    refresh_recovery: null,
    native_child_admission: null,
  };
}

/**
 * The newest prior Turn of this Agent whose closeout is still owed, by the
 * candidate and missing-receipt rule the guard's preflight applies to the same
 * snapshot. Each candidate is read back under its own binding, which is never
 * unbound, so the nested read never asks this question again.
 */
function owedPriorTurnFromSnapshot(
  request: ReadbackRequest,
  snapshot: QuotaSettlementReadbackSnapshot,
  agentId: string,
  turnInstanceId: string | null,
  options?: { includeUnwrittenReplans: boolean },
): OwedPriorTurn | null {
  const receipts = heartbeatReceiptsFromEvents(snapshot.events, request.goal_id, agentId)
    .filter((event) => quotaOwnerOwnsProjection(request.owner, event.goal_ref));
  const { candidates } = selectCloseoutCandidates(
    receipts,
    request.goal_id,
    agentId,
    turnInstanceId,
    options,
  );
  for (const {candidate: selected, requireWritebackForRecovery} of candidates) {
    // A committed monitor poll is its own exact no-spend closeout. This is the
    // same acceptance rule the next-Turn preflight applies; using only the
    // advancement writeback/spend chain here incorrectly made every later
    // Turn wait forever on a monitor that had already settled.
    const monitorPoll = selected.binding_kind === "todo"
      ? committedMonitorPollFromSnapshot(snapshot, {
        goal_id: request.goal_id,
        agent_id: agentId,
        turn_instance_id: selected.prior_turn_instance_id,
        todo_id: selected.binding_id,
      }, request.owner)
      : null;
    const missingReceipts = monitorPoll === null
      ? missingCloseoutReceipts(readQuotaSettlementFromRequest(
        {
          ...request,
          agent_id: agentId,
          ...candidateReadbackBinding(selected),
          infer_turn_instance_id: false,
          allow_unbound_binding: false,
          resolve_original_binding: false,
          pending_closeout: false,
          refresh_retry: null,
          heartbeat_reentry_guard: null,
        },
        snapshot,
        false,
      ), requireWritebackForRecovery)
      : null;
    if (missingReceipts != null) {
      return { ...selected, missing_receipts: missingReceipts };
    }
  }
  return null;
}

/** Resolve pending recovery without another provider read or a latest-Run heuristic. */
function readPendingCloseoutFromRequest(
  request: ReadbackRequest,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  const explicitAgentId = normalizeAgentId(request.agent_id);
  if (explicitAgentId === null) {
    throw new EffectRuntimeRequestError("pending closeout requires an explicit Agent");
  }
  const notFound = {schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA, found: false};
  const turn = request.turn_instance_id ?? owedPriorTurnFromSnapshot(
    request, snapshot, explicitAgentId, null, { includeUnwrittenReplans: true },
  )?.prior_turn_instance_id;
  if (!turn) return notFound;
  // Selection, original binding validation and receipt progress share this
  // immutable snapshot and admitted owner. Maintenance Run classifications
  // cannot conceal the guard's debt or select another Agent's work.
  const result = readQuotaSettlementFromRequest({...request, turn_instance_id: turn,
    pending_closeout: false, resolve_original_binding: true}, snapshot, request.turn_instance_id !== null);
  const identity = jsonObject(jsonObject(jsonObject(result.identity)?.result)?.value);
  const admission = jsonObject(result.native_child_admission);
  const originalReceipt = jsonObject(result.heartbeat_receipt);
  const workAdmitted = originalReceipt !== null &&
    heartbeatReceiptFactFromEvent(originalReceipt).settlement_effect_id !== null &&
    (admission?.report_permission === "new_operation" ||
    admission?.report_permission === "existing_operation_only") &&
    (!identity?.replan_obligation_id ||
      jsonObject(result.semantic_replan_guard)?.selected_obligation_id === identity.replan_obligation_id);
  const partialSettlement = [result.writeback, result.spend].some(step =>
    jsonObject(jsonObject(step)?.payload)?.ok === true);
  // Binding a candidate is not work admission. Reuse the receipt owner's
  // qualified work facts, while retaining verified partial commits even if
  // the original guard did not admit new work. Identity failures stay visible.
  if (identity !== null && !workAdmitted && !partialSettlement) return notFound;
  if (jsonObject(jsonObject(result.settlement)?.payload)?.ok === true ||
      (identity?.todo_id && committedMonitorPollFromSnapshot(snapshot, {
        goal_id: request.goal_id, agent_id: explicitAgentId, turn_instance_id: turn,
        todo_id: String(identity.todo_id),
      }, request.owner) !== null)) return notFound;
  return result;
}

function readQuotaSettlementFromRequest(
  request: ReadbackRequest,
  snapshot: QuotaSettlementReadbackSnapshot,
  nameOwedPriorTurn = true,
  matchedSpend?: MatchedSpendRecords,
): JsonObject {
  const result = readQuotaSettlementFromRequestBase(request, snapshot, nameOwedPriorTurn, matchedSpend);
  if (request.native_task_lease_cleanup === null || result.found !== true) return result;
  return {...result, native_task_lease_cleanup: projectNativeTaskLeaseCleanup(result, request.native_task_lease_cleanup)};
}

function readQuotaSettlementFromRequestBase(
  request: ReadbackRequest,
  snapshot: QuotaSettlementReadbackSnapshot,
  nameOwedPriorTurn = true,
  matchedSpend?: MatchedSpendRecords,
): JsonObject {
  if (
    snapshot.runtimeRoot !== request.runtime_root ||
    snapshot.goalId !== request.goal_id
  ) {
    throw new EffectRuntimeRequestError(
      "settlement snapshot does not match the readback request",
      "settlement_snapshot_scope_mismatch",
    );
  }
  const explicitAgentId = normalizeAgentId(request.agent_id);
  if (request.pending_closeout) return readPendingCloseoutFromRequest(request, snapshot);
  // Only latest-Turn inference needs the full run history. Explicit readback
  // must use the existing Turn indexes before applying the Goal owner fence;
  // otherwise a batch over N Turns repeatedly scans all N receipts.
  const ownerRuns = request.infer_turn_instance_id
    ? snapshot.runs.filter((run) => quotaOwnerOwnsProjection(request.owner, run.goal_ref))
    : [];
  const explicitEvents = !request.infer_turn_instance_id &&
      explicitAgentId !== null && request.turn_instance_id !== null
    ? indexedEvents(
      snapshot,
      request.goal_id,
      explicitAgentId,
      request.turn_instance_id,
    ).filter((event) =>
      quotaOwnerOwnsProjection(request.owner, event.goal_ref)
    )
    : snapshot.events.filter((event) => quotaOwnerOwnsProjection(request.owner, event.goal_ref));
  const identityResult = resolveIdentity(
    request,
    explicitEvents,
    ownerRuns,
    nameOwedPriorTurn
      ? (agentId, turnInstanceId) =>
        owedPriorTurnFromSnapshot(request, snapshot, agentId, turnInstanceId)
      : null,
  );
  if (identityResult === null) {
    return {
      schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
      found: false,
    };
  }
  if (identityResult.failure || identityResult.value === null) {
    return failedReadback(identityResult);
  }

  const identity = settlementIdentity({
    goal_id: String(identityResult.value.goal_id),
    agent_id: String(identityResult.value.agent_id),
    todo_id: optionalString(identityResult.value.todo_id),
    turn_instance_id: String(identityResult.value.turn_instance_id),
    replan_obligation_id: optionalString(identityResult.value.replan_obligation_id),
    request_subject: executionRequestSubject(identityResult.value.request_subject),
  });
  const events = indexedEvents(
    snapshot,
    identity.goal_id,
    identity.agent_id,
    identity.turn_instance_id,
  ).filter((event) =>
    quotaOwnerOwnsProjection(request.owner, event.goal_ref)
  );
  const runs = indexedRuns(snapshot, identity).filter((run) =>
    quotaOwnerOwnsProjection(request.owner, run.goal_ref)
  );
  const heartbeatReceipt = effectiveHeartbeatReceipt(events, identity);
  const admissionReceipt = admissionHeartbeatReceipt(events, identity);
  if (heartbeatReceipt === null) {
    return failedReadback(
      failedIdentity(
        "matching quota should-run heartbeat receipt disappeared during settlement readback",
        "receipt_missing",
      ),
    );
  }
  const receiptDetails = details(heartbeatReceipt);
  const allowLegacyPrepared = request.owner.kind === "alias";
  const validationReceipt = heartbeatValidationEvent(events, heartbeatReceipt, identity, allowLegacyPrepared);
  const preparedLease = validationReceipt === null ? null
    : heartbeatPreparedWorkAdmission(validationReceipt, identity, allowLegacyPrepared);
  const originalPreparedReceipt = preparedLease === null ? null : validationReceipt;
  if (Object.hasOwn(receiptDetails, "prepared_lease_required") && receiptDetails.prepared_lease_required !== true) {
    throw new EffectRuntimeRequestError("Turn readiness requirement is malformed", "malformed_settlement_state");
  }
  const writebackRun = findWriteback(runs, identity);
  const writebackEvent = findStepEvent(events, identity, "refresh_state");
  const spendRun = findSpend(
    spendCandidateRuns(snapshot, identity, runs).filter((run) =>
      quotaOwnerOwnsProjection(request.owner, run.goal_ref)
    ),
    identity,
  );
  const spendEvent = findStepEvent(events, identity, "quota_spend");
  if (matchedSpend) {
    matchedSpend.run = spendRun;
    matchedSpend.receipt = spendEvent;
    matchedSpend.admissionSource = receiptDetails.quota_spend_source;
  }
  const completionEvent = findStepEvent(events, identity, "todo_complete");
  const supersedeEvent = findStepEvent(events, identity, "todo_supersede");
  if (writebackRun !== null && (writebackRun.execution_loss_closeout !== undefined
      || writebackRun.execution_loss_commit !== undefined
      || jsonObject(writebackRun.blocked_retry)?.schema_version === EXECUTION_LOSS_CLOSEOUT_SCHEMA)) {
    const closeout = jsonObject(writebackRun.execution_loss_closeout);
    const commit = jsonObject(writebackRun.execution_loss_commit);
    const permittedRunFields = ["generated_at", "goal_id", "agent_id", "todo_id", "turn_instance_id",
      "settlement_identity", "classification", "delivery_outcome", "delivery_batch_scale", "delivery_boundary",
      "progress_observation", "blocked_retry", "execution_loss_closeout", "json_path", "markdown_path",
      "execution_loss_commit", ...(request.owner.kind === "exact_source" ? ["goal_ref"] : [])];
    if (!closeout || !isExecutionLossCloseout(closeout, identity.todo_id)
        || Object.keys(writebackRun).sort().join("|") !== permittedRunFields.sort().join("|")
        || writebackRun.classification !== "execution_loss_closeout"
        || writebackRun.delivery_outcome !== "outcome_gap" || writebackRun.delivery_boundary !== "semantic_closeout"
        || writebackRun.delivery_batch_scale !== "single_surface" || writebackRun.generated_at !== closeout.observed_at
        || closeout.effect_id !== identity.effect_id
        || originalPreparedReceipt === null
        || closeout.original_receipt_event_id !== originalPreparedReceipt.event_id
        || !isDeepStrictEqual(closeout.original_prepared, preparedLease)
        || jsonObject(closeout.current_control_writer)?.owner !== identity.agent_id
        || closeout.observation_fingerprint !== jsonObject(writebackRun.progress_observation)?.fingerprint
        || !isDeepStrictEqual(writebackRun.blocked_retry, closeout)
        || !commit || Object.keys(commit).sort().join("|") !== ["schema_version", "request_sha256",
          "original_receipt_sha256", "event"].sort().join("|")
        || commit.schema_version !== "quota_execution_loss_closeout_commit_v0"
        || commit.original_receipt_sha256 !== canonicalAuthoritySha256(originalPreparedReceipt)
        || commit.request_sha256 !== canonicalAuthoritySha256({identity: settlementIdentityPayload(identity),
          goal_ref: quotaGoalRef(request.owner), intent: {delivery_outcome: "outcome_gap", delivery_boundary: "semantic_closeout",
            progress_observation: writebackRun.progress_observation}})
        || !isDeepStrictEqual(jsonObject(commit.event)?.details,
          {settlement_effect_id: identity.effect_id, execution_loss_closeout: closeout})
        || (writebackEvent !== null && !isDeepStrictEqual(writebackEvent, commit.event))) {
      throw new EffectRuntimeRequestError("administrative execution-loss receipt differs from original admission",
        "malformed_settlement_state");
    }
  }
  const closeoutStarted = [writebackRun, writebackEvent, spendRun, spendEvent, completionEvent, supersedeEvent]
    .some((receipt) => receipt !== null);

  const writeback = writebackResult(identity, writebackRun, writebackEvent);
  const spend = spendResult(identity, spendRun, spendEvent);
  const semanticReplanGuard = projectSemanticReplanGuard(receiptDetails);
  // The exact Turn-bound blocked writeback is itself a durable no-spend
  // closeout. It cannot certify Todo completion or become delivery progress.
  // A spend already committed for this identity remains an ordinary spend
  // settlement, so readback never erases a historical debit.
  const blockedNoSpend = writeback.failure === null &&
    spendRun === null && spendEvent === null &&
    ((identity.binding_kind === "todo" &&
      writebackRun !== null && writebackRun.delivery_outcome === "outcome_gap" &&
      isBoundedBlockedRetry(writebackRun.blocked_retry, identity.todo_id) &&
      isTurnScopedSettlementOutcome(
        writebackRun.delivery_outcome,
        writebackRun.progress_observation,
        identity.todo_id,
      )) || isAcceptedBlockedReplanWriteback(
        writebackRun, identity, semanticReplanGuard.selected_obligation_id,
      ));
  const retiredNoSpend = writeback.failure === null &&
    spendRun === null && spendEvent === null &&
    isCapabilityRetirementWriteback(writebackRun, identity, semanticReplanGuard.selected_capability_guard);
  const noSpendKind = retiredNoSpend ? "capability_duty_retired_no_spend"
    : blockedNoSpend ? "typed_blocked_writeback_no_spend" : null;
  const terminalCloseout = terminalResult(identity, completionEvent);
  const withWriteback = settlementBindReduce(identityResult, writeback);
  const settled = noSpendKind ? withWriteback : settlementBindReduce(withWriteback, spend);
  const terminalSettlement = settlementBindReduce(settled, terminalCloseout);
  const monitorPoll = committedMonitorPollFromSnapshot(
    snapshot,
    identity,
    request.owner,
  );
  const nestedCausality = typeof receiptDetails.delivery_workspace_causality === "object" &&
      receiptDetails.delivery_workspace_causality !== null &&
      !Array.isArray(receiptDetails.delivery_workspace_causality)
    ? receiptDetails.delivery_workspace_causality
    : null;
  const flatCausality = {
    schema_version: receiptDetails.delivery_workspace_causality_schema_version,
    todo_id: receiptDetails.delivery_workspace_causality_todo_id,
    requirement: receiptDetails.delivery_workspace_requirement,
    source: receiptDetails.delivery_workspace_causality_source,
    reason: receiptDetails.delivery_workspace_causality_reason,
  };
  const workspaceCausality: DeliveryWorkspaceCausality | null =
    normalizeDeliveryWorkspaceCausality(nestedCausality, identity.todo_id) ??
    normalizeDeliveryWorkspaceCausality(flatCausality, identity.todo_id);
  const todoBoundReplan = identity.binding_kind === "todo" &&
    semanticReplanGuard.scope === "turn_guard" &&
    semanticReplanGuard.selected_obligation_id !== null;
  const progressWriteback = writeback.failure === null &&
    isAcceptedProgressWriteback(writebackRun, identity);
  const replanWriteback = writeback.failure === null &&
    isAcceptedReplanWriteback(writebackRun, identity);
  const monitorPhase = receiptBoundMonitorPhase({
    poll_present: monitorPoll !== null,
    material_change: isMaterialMonitorPoll(monitorPoll),
    durable_writeback_present: writeback.failure === null,
    quota_spend_present: spend.failure === null,
    unavailable_attempt_present: blockedNoSpend &&
      isMonitorUnavailableWait(writebackRun?.blocked_retry, identity.todo_id),
  });
  // A committed observation closes its exact Turn independently of whether
  // the monitor still appears in the current Todo frontier. Reuse the monitor
  // reducer so completion, retirement or archival cannot reopen that Turn.
  const replayPhase = monitorPhase === "settled" ? "settled" : receiptBoundReplayPhase({
    binding_kind: identity.binding_kind,
    writeback_completes_binding: todoBoundReplan || noSpendKind !== null || progressWriteback || replanWriteback,
    completion_receipt_present: completionEvent !== null,
    supersede_receipt_present: supersedeEvent?.status === "done" &&
      optionalString(supersedeEvent.todo_id) === identity.todo_id,
    durable_writeback_present: writeback.failure === null,
    quota_spend_present: spend.failure === null,
    no_spend_closeout_present: noSpendKind !== null,
  });

  const refreshDecision = request.refresh_retry === null ? null : refreshRecovery(
    request.refresh_retry, writebackRun, writeback.failure === null,
    workspaceCausality?.requirement,
    writebackRun !== null && snapshot.runs.slice(
      (snapshot.runPositions.get(writebackRun) ?? -1) + 1,
    ).filter((run) =>
      quotaOwnerOwnsProjection(request.owner, run.goal_ref)
    ).some((run) =>
      run.goal_id === identity.goal_id && run.agent_id === identity.agent_id &&
      (jsonObject(run.agent_vision) !== null || jsonObject(run.vision_checkpoint)?.required === true)
    ),
    writeback.failure === null && spendRun === null && spendEvent === null &&
      semanticReplanGuard.selected_obligation_id === identity.replan_obligation_id &&
      isUnqualifiedBlockedReplanWriteback(writebackRun, identity),
  );
  // A non-material poll need not appear as findWriteback's advancement run.
  // Its exact committed observation still fences an incompatible unavailable
  // claim; retain the normal workspace/vision supplement paths for real polls.
  const recovery = monitorPoll !== null && request.refresh_retry?.delivery_outcome === "outcome_gap" &&
      isTurnScopedSettlementOutcome(request.refresh_retry.delivery_outcome,
        request.refresh_retry.progress_observation, identity.todo_id)
    ? {...refreshDecision, decision: "reject", reason: "committed_writeback_payload_conflict"}
    : refreshDecision;

  const progress = settlementProgress(identityResult, writeback, spend, writebackRun, spendRun,
    receiptDetails.quota_spend_source ?? spendRun?.source, noSpendKind);
  // The combined caller consumes controls and receipts after verification.
  // Keep original bodies by default and throughout any pending recovery,
  // including the same Runs carried by the four result bundles. Identity,
  // controls and receipts remain complete; projection cannot settle a Turn.
  const includeRecords = request.include_record_bodies || progress.state !== "settled";
  return {
    schema_version: QUOTA_SETTLEMENT_READBACK_RESULT_SCHEMA,
    found: true,
    identity: bundle(identityResult),
    writeback: bundle(writeback, includeRecords),
    spend: bundle(spend, includeRecords),
    delivery: bundle(withWriteback, includeRecords),
    settlement: bundle(settled, includeRecords),
    terminal_closeout: bundle(terminalCloseout),
    terminal_settlement: bundle(terminalSettlement),
    progress,
    workspace_causality: workspaceCausality,
    semantic_replan_guard: semanticReplanGuard,
    writeback_run: includeRecords ? writebackRun : null,
    refresh_recovery: recovery,
    external_delivery: request.refresh_retry === null ? null : refreshExternalDelivery(
      request.refresh_retry.external_delivery ?? null, identity, events,
      recovery?.decision !== "reject" && recovery?.decision !== "repair_receipt",
    ),
    spend_run: includeRecords ? spendRun : null,
    heartbeat_receipt: includeRecords ? heartbeatReceipt : null,
    original_prepared_receipt: includeRecords ? originalPreparedReceipt : null,
    original_prepared_event_id: originalPreparedReceipt?.event_id ?? null,
    original_prepared_receipt_sha256: originalPreparedReceipt === null ? null
      : canonicalAuthoritySha256(originalPreparedReceipt),
    ...(preparedLease === null ? {} : {prepared_lease: preparedLease}),
    ...(receiptDetails.prepared_lease_required === true ? {prepared_lease_required: true} : {}),
    admission_recorded_at: typeof admissionReceipt?.recorded_at === "string"
      && admissionReceipt.recorded_at.trim() ? admissionReceipt.recorded_at : null,
    admission_event_id: typeof admissionReceipt?.event_id === "string"
      && admissionReceipt.event_id.trim() ? admissionReceipt.event_id : null,
    writeback_event: includeRecords ? writebackEvent : null,
    spend_event: includeRecords ? spendEvent : null,
    completion_event: includeRecords ? completionEvent : null,
    ...(includeRecords && supersedeEvent ? {supersede_event: supersedeEvent} : {}),
    monitor_phase: monitorPhase,
    replay_phase: replayPhase,
    replan_execution_pending: replayPhase === "open" &&
      admitsAutonomousReplan(heartbeatReceipt, heartbeatReceiptFactFromEvent(heartbeatReceipt)),
    successor_replan_pending: replayPhase === "open" &&
      heartbeatReceipt.status === "successor_replan_required" &&
      receiptDetails.should_run === true && receiptDetails.delivery_allowed === false,
    native_child_admission: nativeChildReportAdmission(
      receiptDetails, heartbeatReceipt.status, identity.effect_id, replayPhase,
      closeoutStarted, normalizeReplanObligationId(semanticReplanGuard.selected_obligation_id),
    ),
    ...(request.heartbeat_reentry_guard === null ? {} : {
      heartbeat_reentry_qualification: requalifyHeartbeatReceipt(heartbeatReceipt,
        request.heartbeat_reentry_guard, identity, replayPhase, closeoutStarted),
    }),
  };
}

export function readQuotaSettlementFromSnapshot(
  value: unknown,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  const request = decodeRequest(value);
  if (request.resume_lease_candidate !== null || request.writeback_candidate !== null) throw new EffectRuntimeRequestError(
    "resume lease qualification requires a live canonical authority read");
  if (request.owner.kind !== "alias") {
    throw new EffectRuntimeRequestError(
      "exact source settlement readback requires live owner admission",
      "quota_source_admission_invalid",
    );
  }
  return readQuotaSettlementFromRequest(request, snapshot);
}

/** Read under a source admission already adopted by the enclosing transaction. */
export function readAdmittedQuotaSettlementFromSnapshot(
  value: unknown,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  const request = decodeRequest(value);
  if (request.resume_lease_candidate !== null || request.writeback_candidate !== null) throw new EffectRuntimeRequestError(
    "resume lease qualification requires a live canonical authority read");
  return readQuotaSettlementFromRequest(request, snapshot);
}

/** Read under the parsed owner already admitted by the enclosing transaction. */
export function readQuotaSettlementForAdmittedOwnerFromSnapshot(
  value: unknown,
  owner: QuotaAccountingOwner,
  snapshot: QuotaSettlementReadbackSnapshot,
): JsonObject {
  const request = decodeRequest(value, owner);
  if (request.resume_lease_candidate !== null || request.writeback_candidate !== null) throw new EffectRuntimeRequestError(
    "resume lease qualification requires a live canonical authority read");
  return readQuotaSettlementFromRequest(request, snapshot);
}

async function readSettlementExecutionAuthority(request: ReadbackRequest, result: JsonObject): Promise<{
  identity: SettlementIdentity; todo: JsonObject; lease: JsonObject | undefined; handoffMode: unknown;
}> {
  function reject(message: string): never {
    throw new EffectRuntimeRequestError(message, "refresh_task_lease_unavailable");
  }
  const identityResult = jsonObject(jsonObject(result.identity)?.result);
  const identityValue = jsonObject(identityResult?.value);
  if (result.found !== true || identityResult?.failure != null || identityValue === null) {
    reject("The original Turn's settlement identity could not be verified.");
  }
  const identity = settlementIdentity({goal_id: String(identityValue.goal_id),
    agent_id: String(identityValue.agent_id), todo_id: optionalString(identityValue.todo_id),
    turn_instance_id: String(identityValue.turn_instance_id),
    replan_obligation_id: optionalString(identityValue.replan_obligation_id),
    request_subject: executionRequestSubject(identityValue.request_subject)});
  const hint = request.resume_lease_candidate ?? request.writeback_candidate;
  if (hint?.validation_recovery_turn_instance_id != null
      && hint.validation_recovery_turn_instance_id !== identity.turn_instance_id) {
    reject("Recovery Turn hint differs from the original settlement identity.");
  }
  // The ordinary reader does not load lease decision owners. Only this
  // explicitly requested ephemeral branch reads the real provider authority,
  // inside the enclosing quota/source admission and without claiming a lease.
  const [{openRuntimeAuthorityStore}, {readAuthoritySnapshot}, {canonicalTodoCollection}] = await Promise.all([
    import("../coordination/local_authority_provider.ts"),
    import("../coordination/authority_store.ts"),
    import("../coordination/local_authority_read.ts"),
  ]);
  const store = await openRuntimeAuthorityStore(request.runtime_root, request.goal_id, {}, {existingOnly: true});
  const snapshot = await readAuthoritySnapshot(store);
  if (snapshot.identity.status !== "available" || snapshot.authority.status !== "loaded") {
    reject("The current canonical execution authority is unavailable.");
  }
  const authority = snapshot.authority;
  const collection = canonicalTodoCollection(authority.head, identity.goal_id, true);
  const todo = collection.projection.todos.get(identity.todo_id!);
  const lease = collection.leaseIndex?.leases.get(identity.todo_id!);
  if (todo === undefined) reject("The original Todo's canonical authority is unavailable.");
  // Reuse the canonical read contract's within-read incarnation check, not a
  // new historical legacy incarnation/ABA gate.
  const observed = await store.storeIdentity();
  if (observed.status !== "available" || observed.store_identity !== snapshot.identity.store_identity) {
    reject("Canonical authority changed during resume qualification.");
  }
  return {identity, todo, lease, handoffMode: authority.head.handoff_mode};
}

async function readResumeLeaseQualification(request: ReadbackRequest, result: JsonObject): Promise<JsonObject> {
  function reject(message: string): never {
    throw new EffectRuntimeRequestError(message, "refresh_task_lease_unavailable");
  }
  const {identity, todo, lease, handoffMode} = await readSettlementExecutionAuthority(request, result);
  const originalReceipt = jsonObject(result.original_prepared_receipt);
  if (originalReceipt === null) {
    if (request.owner.kind === "exact_source" || handoffMode !== "soft_claim"
        || result.prepared_lease_required === true || result.prepared_lease != null) {
      reject("The original Turn's positive prepared admission is missing.");
    }
    return {state: "not_required", original_prepared: null,
      current_task_lease_proof: null, lease_epoch: null, source: null};
  }
  if (lease === undefined) reject("The original Todo's canonical lease is unavailable.");
  const completion = jsonObject(result.completion_event);
  const progress = jsonObject(result.progress);
  let terminalProviderEffect: import("../turn_driver/settlement_provider.ts").ProviderEffect | null = null;
  let terminalCompletion: JsonObject | null = null;
  const providerContext = request.resume_lease_candidate!.provider_context;
  if (providerContext !== undefined && jsonObject(jsonObject(result.settlement)?.payload)?.ok === true
      && [result.writeback_run, result.writeback_event, result.spend_run, result.spend_event]
        .every(record => record != null)) {
    const {readTurnTerminalCloseoutProvider} = await import("../turn_driver/turn_journal_query.ts");
    terminalProviderEffect = await readTurnTerminalCloseoutProvider(request.runtime_root,
      identity, request.owner, providerContext, todo);
    if (terminalProviderEffect !== null && todo.status === "done" && lease.status === "released") {
      const {projectCompletionEvidence} = await import("../todos/read_projection.ts");
      terminalCompletion = projectCompletionEvidence({operation: "terminal", todo,
        expected_todo_id: identity.todo_id, completion_turn_key: identity.turn_instance_id});
    }
  }
  return qualifyHeartbeatResumeLease({originalReceipt, identity,
    requestedProof: request.resume_lease_candidate!.requested_proof, todo, lease,
    handoffMode, completionEvent: completion,
    replayPhase: result.replay_phase as ReceiptBoundReplayPhase,
    closeoutStarted: [result.writeback_run, result.writeback_event, result.spend_run,
      result.spend_event, result.completion_event].some(record => record != null),
    writebackPending: progress?.state === "writeback_required" || progress?.state === "writeback_receipt_required",
    otherTerminalStarted: result.spend_run != null || result.spend_event != null
      || result.replay_phase === "settled",
    terminalProviderEffect, terminalCompletion,
    exactSource: request.owner.kind === "exact_source", observedAt: new Date()});
}

/** A first semantic writeback has its own operation scope. Its result is not
 * a task lease and is never consumed by host execution or Todo completion. */
async function readWritebackQualification(request: ReadbackRequest, result: JsonObject,
  snapshot: QuotaSettlementReadbackSnapshot): Promise<JsonObject> {
  function reject(message: string): never {
    throw new EffectRuntimeRequestError(message, "refresh_task_lease_unavailable");
  }
  const candidate = request.writeback_candidate!, run = candidate.run;
  const {identity, todo, lease, handoffMode} = await readSettlementExecutionAuthority(request, result);
  if (identity.binding_kind !== "todo" || candidate.effect_ref !== `${identity.effect_id}#durable_writeback`
      || candidate.completion_todo_id !== null || candidate.completion_turn_key !== null
      || run.goal_id !== identity.goal_id || run.agent_id !== identity.agent_id
      || run.todo_id !== identity.todo_id || run.turn_instance_id !== identity.turn_instance_id
      || run.delivery_boundary !== "semantic_closeout"
      || typeof run.classification !== "string" || !run.classification.trim()) {
    reject("Writeback candidate does not describe this original Todo's semantic operation.");
  }
  const parsed = settlementIdentityFromPlan({settlement_plan: {identity: run.settlement_identity}});
  if (parsed.failure !== null || !isDeepStrictEqual(settlementIdentityPayload(parsed.value), settlementIdentityPayload(identity))
      || !quotaOwnerOwnsProjection(request.owner, run.goal_ref)
      || (request.owner.kind === "alias" && Object.hasOwn(run, "goal_ref"))) {
    reject("Writeback candidate crosses its original settlement or Goal ownership.");
  }
  const retry = request.refresh_retry!;
  const progress = jsonObject(run.progress_observation);
  if (run.progress_observation != null && (progress === null || progress.work_item_id !== identity.todo_id)) {
    reject("Writeback progress must describe the original Todo, without substituting another work item.");
  }
  const recovery = jsonObject(result.refresh_recovery);
  if (retry.external_delivery != null || retry.workspace_requested || retry.checkpoint_read_context_id != null
      || retry.delivery_boundary !== "semantic_closeout" || retry.delivery_outcome !== run.delivery_outcome
      || retry.delivery_batch_scale !== (run.delivery_batch_scale ?? null)
      || !isDeepStrictEqual(retry.progress_observation, run.progress_observation ?? null)
      || recovery?.decision !== "append" || recovery.reason !== "first_writeback"
      || !isDeepStrictEqual(run.refresh_recovery, recovery)
      || Object.keys(retry.mutation).some(key => !["next_action", "next_action_basis", "autonomous_replan_recorded",
        "repair_delta_kinds", "usage_measurement", "usage_codex_session", "explore_result"].includes(key))) {
    reject("Writeback candidate differs from its actual semantic payload or carries another operation.");
  }
  if ([result.writeback_run, result.writeback_event, result.spend_run, result.spend_event].some(record => record != null)) {
    reject("Committed writeback and accounting use their existing replay owner, not a new writeback qualification.");
  }
  const originalReceipt = jsonObject(result.original_prepared_receipt);
  if (originalReceipt === null && (result.prepared_lease_required === true || result.prepared_lease != null)) {
    reject("The original Turn's required positive preparation is missing.");
  }
  const effective = jsonObject(result.heartbeat_receipt);
  if (effective === null) reject("The original semantic receipt is unavailable.");
  const events = indexedEvents(snapshot, request.goal_id, identity.agent_id, identity.turn_instance_id)
    .filter(event => quotaOwnerOwnsProjection(request.owner, event.goal_ref));
  const scope = (original: JsonObject | null, selected: string | null): JsonObject => bundle(settlementPure({
    step_kind: "durable_writeback", effect_ref: candidate.effect_ref, delivery_boundary: "semantic_closeout",
    original_prepared: original, selected_obligation_id: selected,
  }, (jsonObject(result.identity)?.result as SettlementResult).receipts));
  const closeoutStarted = [result.completion_event, result.supersede_event].some(record => record != null);
  let activePrepared: JsonObject | null = null;
  if (originalReceipt !== null && lease !== undefined && !closeoutStarted) {
    const qualified = qualifyHeartbeatResumeLease({originalReceipt, identity, requestedProof: candidate.requested_proof,
      todo, lease, handoffMode, completionEvent: null, replayPhase: result.replay_phase as ReceiptBoundReplayPhase,
      closeoutStarted: false, writebackPending: true, otherTerminalStarted: false,
      exactSource: request.owner.kind === "exact_source", observedAt: new Date()});
    activePrepared = jsonObject(qualified.original_prepared);
  }
  const [{buildVisionCheckpoint, VISION_REFRESH_REQUEST_SCHEMA, qualifiesOpenTodoSemanticCheckpoint},
    {projectExecutionPreparation, EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION}, {leaseIsActive}, {projectProgressDelta}] = await Promise.all([
    import("../goals/vision_checkpoint.ts"), import("../work_items/execution_preparation.ts"),
    import("../work_items/task_lease_record.ts"), import("../work_items/progress_delta.ts"),
  ]);
  const ownerRuns = snapshot.runs.filter(row => row.goal_id === identity.goal_id && row.agent_id === identity.agent_id
    && quotaOwnerOwnsProjection(request.owner, row.goal_ref));
  const progressSource = progress === null ? null : {current: progress, baseline: null,
    window: ownerRuns.map(row => jsonObject(row.progress_observation)).filter(value => value !== null)};
  // Reuse the typed observation codec for every semantic branch. Its outcome
  // qualification remains with the existing replan owner below.
  if (progressSource !== null) projectProgressDelta(progressSource);
  const existingVision = [...ownerRuns].reverse().map(row => jsonObject(row.agent_vision)).find(value => value !== null) ?? null;
  let vision: JsonObject | null = null;
  if (run.agent_vision != null) {
    vision = jsonObject(buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare",
      goal_id: identity.goal_id, agent_id: identity.agent_id, agent_vision_packet: run.agent_vision,
      existing_agent_vision: null, merge_patch: false, require_path_delta_for_durable_change: true,
      allow_terminal_stride_path: false}).agent_vision);
  }
  if (retry.vision === null && vision !== null) {
    reject("Writeback cannot introduce a Vision absent from its actual request.");
  }
  if (retry.vision !== null) {
    const expected = jsonObject(buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "prepare",
      goal_id: identity.goal_id, agent_id: identity.agent_id, agent_vision_packet: retry.vision,
      existing_agent_vision: existingVision, merge_patch: retry.merge_patch,
      require_path_delta_for_durable_change: true, allow_terminal_stride_path: false}).agent_vision);
    if (expected === null || vision === null || Object.keys(expected).some(key => key !== "generated_at"
        && !isDeepStrictEqual(expected[key], vision![key]))) reject("Writeback Vision differs from its actual request.");
  }
  const checkpoint = buildVisionCheckpoint({schema_version: VISION_REFRESH_REQUEST_SCHEMA, phase: "finalize",
    agent_id: identity.agent_id, agent_vision: vision, existing_agent_vision: existingVision,
    vision_unchanged_reason: retry.unchanged_reason, delivery_outcome: run.delivery_outcome,
    active_state_next_action_would_update: retry.mutation.next_action != null,
    delivery_boundary: "semantic_closeout", todo_id: identity.todo_id, completion_todo_id: null,
    autonomous_replan_recorded: retry.mutation.autonomous_replan_recorded === true, blocked_retry: null});
  const offeredCheckpoint = jsonObject(run.vision_checkpoint);
  if (checkpoint.satisfied !== true || offeredCheckpoint === null
      || ["schema_version", "agent_id", "required", "satisfied", "decision", "triggers",
      "delivery_boundary", "agent_vision_state"].some(key => !isDeepStrictEqual(offeredCheckpoint[key], checkpoint[key]))) {
    reject("Writeback checkpoint was not qualified from its actual semantic inputs.");
  }
  const qualifiedRun = {...run, settlement_identity: settlementIdentityPayload(identity), agent_vision: vision,
    vision_checkpoint: checkpoint};
  // Prepared execution authority alone cannot validate semantic writeback.
  // The same actual Vision, progress identity and checkpoint checks above
  // apply before returning this operation's scope.
  if (activePrepared !== null) return scope(activePrepared, null);
  const supersede = jsonObject(result.supersede_event);
  if (originalReceipt !== null && supersede !== null && result.completion_event == null
      && todo.status === "done" && lease?.status === "released") {
    const proof = qualifyHeartbeatPreparedGeneration({originalReceipt, identity, todo, lease, handoffMode,
      requestedProof: candidate.requested_proof, exactSource: request.owner.kind === "exact_source"});
    if (supersede.event_kind !== "todo_supersede" || supersede.status !== "done"
        || supersede.goal_id !== identity.goal_id || supersede.agent_id !== identity.agent_id
        || supersede.run_id !== identity.turn_instance_id || supersede.todo_id !== identity.todo_id
        || !effectIdsMatch(optionalString(details(supersede).settlement_effect_id), identity.effect_id)
        || !qualifiesOpenTodoSemanticCheckpoint(qualifiedRun, identity)) {
      reject("Retired writeback requires exact supersession and a fresh nonterminal semantic checkpoint.");
    }
    return scope({...proof.original}, null);
  }
  const completion = jsonObject(result.completion_event), receiptDetails = details(effective);
  if (request.owner.kind === "alias" && originalReceipt === null && result.prepared_lease_required !== true
      && !["prepared_lease_key_sha256", "prepared_lease_version", "prepared_lease_epoch", "prepared_lease_required",
        "must_attempt_work", "delivery_allowed", "quiet_noop_allowed", "settlement_receipt_revision"]
        .some(key => Object.hasOwn(receiptDetails, key))
      && todo.status === "done" && handoffMode === "hard_lease" && lease?.status === "released"
      && lease.owner === identity.agent_id && (todo.claimed_by == null || todo.claimed_by === identity.agent_id)
      && jsonObject(result.native_child_admission)?.report_permission === "existing_operation_only"
      && completion?.event_kind === "todo_complete" && completion.goal_id === identity.goal_id
      && completion.agent_id === identity.agent_id && completion.run_id === identity.turn_instance_id
      && (completion.todo_id == null || completion.todo_id === identity.todo_id)
      && effectIdsMatch(optionalString(details(completion).settlement_effect_id), identity.effect_id)
      && qualifiesOpenTodoSemanticCheckpoint(qualifiedRun, identity)) {
    const legacy = projectExecutionPreparation({schema_version: EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION,
      goal_id: identity.goal_id, agent_id: identity.agent_id, handoff_mode: handoffMode, authority_available: true,
      selected_todo: todo, include_retired_lease_for_writeback: true,
      leases: [{status: lease.status, active: false, owner: lease.owner,
        lease_proof: {idempotency_key: lease.idempotency_key, expected_version: lease.version}, lease_epoch: lease.lease_epoch}]});
    const currentProof = jsonObject(legacy.retired_lease_proof);
    if (currentProof === null || (candidate.requested_proof !== null
        && !isDeepStrictEqual(candidate.requested_proof, currentProof))) {
      reject("Legacy semantic writeback requires its exact current retired holder.");
    }
    // Historical absence proves no incarnation. Return only this writeback's
    // scope, never the observed epoch or a business execution lease.
    return scope(null, null);
  }
  const semanticReceipt = heartbeatSemanticAdmissionEvent(events, effective, identity, request.owner.kind === "alias");
  const guard = semanticReceipt === null ? null : projectSemanticReplanGuard(details(semanticReceipt));
  const selected = optionalString(guard?.selected_obligation_id);
  if (semanticReceipt === null || guard?.scope !== "turn_guard" || selected === null
      || selected !== optionalString(jsonObject(result.semantic_replan_guard)?.selected_obligation_id)
      || guard.selected_capability_guard != null || todo.status !== "open") {
    reject("Semantic writeback needs the original typed replan admission and selected obligation.");
  }
  if (originalReceipt !== null) reject("Semantic writeback cannot replace this Turn's prepared business generation.");
  const currentProof = lease === undefined ? null : {idempotency_key: lease.idempotency_key, expected_version: lease.version};
  if (candidate.requested_proof !== null && !isDeepStrictEqual(candidate.requested_proof, currentProof)) {
    reject("The supplied writeback proof does not match current canonical authority.");
  }
  const preparation = projectExecutionPreparation({schema_version: EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION,
    goal_id: identity.goal_id, agent_id: identity.agent_id, handoff_mode: handoffMode, authority_available: true,
    selected_todo: todo, leases: lease === undefined ? [] : [{status: lease.status,
      active: leaseIsActive(lease, new Date()), owner: lease.owner, lease_proof: currentProof, lease_epoch: lease.lease_epoch}]});
  if (preparation.state !== "ready" && preparation.state !== "claim_required" && preparation.state !== "not_required") {
    reject("The original semantic Todo's current authority conflicts with this actor.");
  }
  if (candidate.requested_proof !== null && preparation.state !== "ready") {
    reject("An offered semantic writeback proof requires current active authority.");
  }
  const [{projectReplanSettlementContract, REPLAN_SETTLEMENT_REQUEST_SCHEMA}, {projectReplanWriteback}] = await Promise.all([
    import("../work_items/replan_settlement.ts"), import("../work_items/replan_semantics.ts"),
  ]);
  const contract = projectReplanSettlementContract({schema_version: REPLAN_SETTLEMENT_REQUEST_SCHEMA,
    selected_todo_id: identity.todo_id, semantic_replan_obligation_id: selected});
  if (contract.settlement_binding.id !== identity.todo_id || contract.semantic_obligation.discharge !== "todo_bound_writeback") {
    reject("Semantic obligation does not belong to the original Todo settlement.");
  }
  const obligation = projectReplanWriteback({operation: "receipt_bound_obligation", guard_scoped: true,
    selected_obligation_id: selected, agent_id: identity.agent_id, goal_id: identity.goal_id,
    guard_recorded_at: semanticReceipt.recorded_at, guard_turn_instance_id: identity.turn_instance_id,
    replay_phase: "open", current_obligation: null, receipt_review: guard.selected_review_source,
    transition_candidates: []}).obligation;
  const semantic = projectReplanWriteback({operation: "qualify", obligation, agent_vision: vision ?? {},
    progress_source: progressSource});
  const ack = jsonObject(run.autonomous_replan_ack), offered = jsonObject(ack?.semantic_delta);
  if (semantic.accepted !== true || ack?.schema_version !== "autonomous_replan_ack_v0" || ack.recorded !== true
      || offered === null || ["schema_version", "accepted", "outcomes", "satisfying_outcomes", "required_any_of",
        "observation_fingerprint"].some(key => !isDeepStrictEqual(offered[key], semantic[key]))) {
    reject("Semantic writeback payload does not satisfy its original replan source.");
  }
  return scope(null, selected);
}

export async function readQuotaSettlement(value: unknown): Promise<JsonObject> {
  if (jsonObject(value)?.schema_version === "loopx_quota_history_qualification_request_v0") {
    const input = jsonObject(value)!, runs = input.runs, observedAt = input.observed_at;
    if (!Array.isArray(runs) || (observedAt != null && typeof observedAt !== "string")) {
      throw new EffectRuntimeRequestError("history qualification requires Run facts and an optional observation clock");
    }
    return qualifySettlementHistory(runs, typeof observedAt === "string" ? observedAt : null);
  }
  if (jsonObject(value)?.schema_version === RECEIPT_BOUND_WAIT_REQUEST_SCHEMA) {
    return projectReceiptBoundWait(value);
  }
  if (jsonObject(value)?.schema_version === BLOCKED_WAIT_REQUEST_SCHEMA) {
    return prepareBlockedWait(value);
  }
  const request = decodeRequest(value);
  const input = requireJsonObject(value, "quota settlement readback request");
  const spendPrecheck = input.spend_precheck;
  if (spendPrecheck !== undefined && typeof spendPrecheck !== "boolean") {
    throw new EffectRuntimeRequestError("spend_precheck must be a boolean");
  }
  const spendRequest = input.spend_request === undefined ? null
    : requireJsonObject(input.spend_request, "spend_request");
  if (spendRequest !== null && (!spendPrecheck ||
      !Number.isSafeInteger(spendRequest.slots) || typeof spendRequest.source !== "string")) {
    throw new EffectRuntimeRequestError("spend_request requires spend_precheck, integer slots and a source");
  }
  if (spendPrecheck) request.include_record_bodies = true;
  if (request.resume_lease_candidate !== null || request.writeback_candidate !== null) request.include_record_bodies = true;
  const withOwner = request.borrow_source_admission
    ? withBorrowedQuotaAccountingOwner
    : withQuotaAccountingOwner;
  return await withOwner(
    request.owner,
    async () => {
      const matchedSpend: MatchedSpendRecords = {run: null, receipt: null};
      const snapshot = await readQuotaSettlementSnapshot(
          request.runtime_root,
          request.goal_id,
          undefined,
          request.tolerate_malformed_history,
        );
      const result = readQuotaSettlementFromRequest(request, snapshot, true, matchedSpend);
      if (request.resume_lease_candidate !== null) {
        result.resume_lease_qualification = await readResumeLeaseQualification(request, result);
      }
      if (request.writeback_candidate !== null) {
        result.writeback_qualification = await readWritebackQualification(request, result, snapshot);
      }
      const identity = jsonObject(jsonObject(jsonObject(result.identity)?.result)?.value);
      const receipt = spendReceiptTerms(matchedSpend.receipt);
      // Only exact readback opens its matched debit artifact. Check its
      // identity even when the receipt is missing or omits billing terms.
      // Snapshot readers for history and guard discovery use the bounded index.
      const retained = matchedSpend.run !== null
        ? await readQuotaSpendEventFromRun(join(request.runtime_root, "goals", request.goal_id, "runs"),
          matchedSpend.run, request.goal_id)
        : null;
      const ledger = {slots: retained?.slots ?? undefined, source: retained?.source ?? undefined};
      let conflict = spendTermsConflict(ledger, receipt);
      if (retained === null && matchedSpend.run !== null && matchedSpend.receipt !== null) {
        // An index row identifies the original debit, but does not provide its
        // terms. Check explicit receipt terms against the matching admission.
        const source = matchedSpend.admissionSource ?? undefined;
        conflict = spendTermsConflict(
          {slots: ORIGINAL_TURN_SPEND_SLOTS, source}, receipt, "the original Turn admission",
        ) ?? (source === undefined && receipt.source !== undefined
          ? "the original Turn admission has no verifiable billing source for the quota spend receipt"
          : null);
      }
      if (conflict !== null && spendRequest === null) {
        throw new EffectRuntimeRequestError(conflict, "malformed_settlement_state");
      }
      if (identity && request.refresh_retry &&
          jsonObject(result.refresh_recovery)?.decision !== "reject" &&
          isTurnScopedSettlementOutcome(request.refresh_retry.delivery_outcome,
            request.refresh_retry.progress_observation, optionalString(identity.todo_id)) &&
          request.refresh_retry.delivery_outcome === "outcome_gap" &&
          await hasMonitorPollTransaction(request.runtime_root, identity as unknown as SettlementIdentity, request.owner)) {
        throw new EffectRuntimeRequestError(
          "a stored Monitor poll may already have committed its observation; recover the exact monitor-poll before an unavailable closeout",
        );
      }
      if (!spendPrecheck || result.found !== true) return result;
      // Preview still needs a fresh full read. The earlier replay check needs
      // only its receipts and whether a spend Run already needs receipt repair.
      const settlement = requireJsonObject(result.settlement, "quota settlement bundle");
      const outcome = requireJsonObject(settlement.result, "quota settlement result");
      const rejection = conflict === null
        ? spendRequest && identity ? originalSpendRejection(result, spendRequest, ledger, receipt) : null
        : rejectedSpend(conflict);
      return {
        schema_version: result.schema_version,
        found: true,
        identity: result.identity,
        settlement: {...settlement, result: {...outcome, value: null}},
        progress: result.progress,
        spend_run_present: matchedSpend.run !== null,
        ...(rejection === null ? {} : {spend_request_rejection: bundle(rejection)}),
      };
    },
  );
}

function spendReceiptTerms(event: JsonObject | null): SpendTerms {
  const {slots, source} = event === null ? {} : details(event);
  // Older rollout envelopes and repaired receipts can omit billing terms.
  return {
    slots: slots == null || slots === "" ? undefined : slots,
    source: source == null || source === "" ? undefined : source,
  };
}

function spendTermsConflict(
  recorded: SpendTerms,
  receipt: SpendTerms,
  description = "the recorded debit",
): string | null {
  for (const field of ["slots", "source"] as const) {
    if (recorded[field] !== undefined && receipt[field] !== undefined && recorded[field] !== receipt[field]) {
      return `${description} and quota spend receipt disagree on ${field}`;
    }
  }
  return null;
}

function rejectedSpend(reason: string): SettlementResult<JsonObject> {
  return settlementFailed({kind: "quota_spend_rejected", step_kind: "quota_spend", reason});
}

function originalSpendRejection(
  readback: JsonObject, proposed: JsonObject,
  ledger: SpendTerms, receipt: SpendTerms,
): SettlementResult<JsonObject> | null {
  const progress = requireJsonObject(readback.progress, "quota settlement progress");
  const guard = jsonObject(readback.heartbeat_receipt);
  const authorizedSource = guard ? details(guard).quota_spend_source : null;
  // Older ledgers can have a rollout receipt but no readable artifact. Keep
  // their receipt facts available and never repair an already mismatched debit.
  const spendEvent = jsonObject(readback.spend_event);
  const spendRun = jsonObject(readback.spend_run);
  const recordedSlots = ledger.slots ?? receipt.slots;
  const recordedSource = ledger.source ?? receipt.source;
  const fallbackSource = recordedSource === "heartbeat" || recordedSource === "visible-goal"
    ? recordedSource : progress.quota_spend_source;
  const source = authorizedSource ?? fallbackSource;
  // Every admitted CLI settlement plan authorizes one slot and the original
  // source. The same check gates initial spend, receipt repair, and replay.
  if (proposed.slots !== ORIGINAL_TURN_SPEND_SLOTS || proposed.source !== source) {
    return rejectedSpend(`original Turn settlement requires --slots ${ORIGINAL_TURN_SPEND_SLOTS} --source ${String(source)}`);
  }
  const slotsConflict = recordedSlots !== undefined && recordedSlots !== proposed.slots;
  const sourceConflict = recordedSource !== undefined && recordedSource !== source;
  // Historical complete receipts may lack the amount projection. A partial
  // debit needs the original charge facts before we can certify its receipt.
  const unverifiableRepair = spendRun !== null && spendEvent === null &&
    (recordedSlots === undefined || recordedSource === undefined);
  return slotsConflict || sourceConflict || unverifiableRepair ? rejectedSpend(unverifiableRepair
      ? "the recorded debit has no verifiable billing terms for receipt repair"
      : "the recorded debit differs from the original Turn settlement plan") : null;
}
