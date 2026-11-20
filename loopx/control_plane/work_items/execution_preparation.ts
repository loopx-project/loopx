import {HANDOFF_MODES} from "../coordination/handoff_mode_vocabulary.ts";
import {HANDOFF_MODE_RETIRED_REASON, retiredHandoffModeRejection} from "../coordination/handoff_mode_policy.ts";
import type {JsonObject} from "../effect_program.ts";
import {leaseEpoch} from "./task_lease_record.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {createHash} from "node:crypto";
import {isDeepStrictEqual} from "node:util";
import {settlementIdentity, settlementIdentityPayload} from "../effect_program.ts";
import {parseExactGoalRef} from "../goals/goal_instance_identity.ts";
import {canonicalTaskLease, canonicalLeaseTodoFact} from "../coordination/task_lease_state.ts";
import {decodeTaskLeaseProof} from "../coordination/task_lease_proof.ts";
import {heartbeatPreparedLease, heartbeatReceiptBinding, heartbeatReceiptDetails, heartbeatReceiptFactFromEvent}
  from "../quota/heartbeat_receipt_identity.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {projectProgressDelta} from "./progress_delta.ts";
import {leaseRepositoryRejection} from "./task_lease_repository.ts";
import {leaseOwnerRejection} from "./task_lease_eligibility.ts";
import {
  optionalNonEmptyString,
  requireBoolean,
  requireJsonObject,
  requireInteger,
  requireNonEmptyString,
  requireStringLiteral,
} from "../runtime_decode.ts";

export const EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION =
  "execution_preparation_facts_v0";
export const EXECUTION_PREPARATION_SCHEMA_VERSION =
  "execution_preparation_v0";
export const EXECUTION_LOSS_CLOSEOUT_SCHEMA = "quota_execution_loss_closeout_v0";
export const EXECUTION_LOSS_QUALIFICATION_SCHEMA = "execution_loss_closeout_qualification_v0";

const LEASE_STATUSES = ["active", "released", "unknown"] as const;

function rejectExecutionLoss(message: string): never {
  throw new EffectRuntimeRequestError(`execution-loss closeout ${message}`, "refresh_task_lease_unavailable");
}

function executionLossClock(value: unknown): bigint {
  if (typeof value !== "string") rejectExecutionLoss("requires an exact aware timestamp");
  const time = parseTodoTimestampMicros(value, true);
  if (time === null) rejectExecutionLoss("has an invalid or ambiguous timestamp");
  return time;
}

/** Pure qualification consumed only by the fenced administrative commit owner.
 * A current lease proves control-writer ownership, never original execution. */
export function qualifyReceiptBoundExecutionLossCloseout(value: unknown): JsonObject {
  const request = requireJsonObject(value, "execution-loss qualification");
  if (request.schema_version !== EXECUTION_LOSS_QUALIFICATION_SCHEMA)
    rejectExecutionLoss("qualification schema is unsupported");
  const binding = requireJsonObject(request.identity, "execution-loss identity");
  const identity = settlementIdentity({goal_id: requireNonEmptyString(binding.goal_id, "goal_id"),
    agent_id: requireNonEmptyString(binding.agent_id, "agent_id"), todo_id: requireNonEmptyString(binding.todo_id, "todo_id"),
    turn_instance_id: requireNonEmptyString(binding.turn_instance_id, "turn_instance_id")});
  if (identity.binding_kind !== "todo" || !identity.todo_id
      || !isDeepStrictEqual(settlementIdentityPayload(identity), binding))
    rejectExecutionLoss("requires the exact original Todo Turn identity");
  const receipt = requireJsonObject(request.receipt, "original heartbeat receipt");
  if (request.goal_ref === undefined) {
    if (Object.hasOwn(receipt, "goal_ref")) rejectExecutionLoss("cannot mix legacy and exact Goal ownership");
  } else {
    const current = parseExactGoalRef(request.goal_ref), original = parseExactGoalRef(receipt.goal_ref);
    if (current.kind !== "parsed" || original.kind !== "parsed"
        || current.value.goalId.value !== identity.goal_id
        || !isDeepStrictEqual(request.goal_ref, receipt.goal_ref))
      rejectExecutionLoss("requires the original exact Goal lifetime");
  }
  const details = heartbeatReceiptDetails(receipt), fact = heartbeatReceiptFactFromEvent(receipt);
  const originalBinding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
  const prepared = heartbeatPreparedLease(details);
  if (receipt.schema_version !== "loopx_rollout_event_v0" || receipt.event_kind !== "quota_should_run"
      || receipt.goal_id !== identity.goal_id || receipt.agent_id !== identity.agent_id
      || receipt.run_id !== identity.turn_instance_id || fact.todo_id !== identity.todo_id
      || originalBinding?.binding_kind !== "todo" || originalBinding.binding_id !== identity.todo_id
      || fact.settlement_effect_id !== identity.effect_id || fact.closeout_required !== true
      || !prepared || prepared.prepared_lease_epoch === undefined
      || (Object.hasOwn(details, "prepared_lease_required") && details.prepared_lease_required !== true)
      || (details.semantic_replan_obligation_id != null && details.semantic_replan_obligation_id !== "")
      || nativeChildReportAdmission(details, receipt.status, identity.effect_id, "open", false)
        .report_permission !== "new_operation")
    rejectExecutionLoss("requires complete original prepared work admission");
  const todo = requireJsonObject(request.current_todo, "current Todo");
  const registered = request.registered_agents;
  if (!Array.isArray(registered) || registered.some(agent => typeof agent !== "string")
      || !registered.includes(identity.agent_id) || todo.todo_id !== identity.todo_id
      || todo.role !== "agent" || todo.task_class !== "advancement_task"
      || todo.archive_state !== "active" || todo.status !== "open"
      || todo.claimed_by !== identity.agent_id || todo.done === true)
    rejectExecutionLoss("requires the current registered control writer and unfinished Todo");
  if (leaseOwnerRejection(canonicalLeaseTodoFact(todo), identity.agent_id, registered as string[]) !== null)
    rejectExecutionLoss("current control writer is not eligible for this Todo");
  const lease = canonicalTaskLease(requireJsonObject(request.current_lease, "current task lease"),
    identity.goal_id, identity.todo_id);
  const proof = decodeTaskLeaseProof(request.control_writer_proof);
  if (!proof || leaseRepositoryRejection(todo, lease) !== null
      || lease.owner !== identity.agent_id || lease.status !== "active"
      || typeof lease.version !== "number" || !Number.isSafeInteger(lease.version)
      || typeof lease.lease_epoch !== "number" || !Number.isSafeInteger(lease.lease_epoch)
      || proof.idempotency_key !== lease.idempotency_key || proof.expected_version !== lease.version)
    rejectExecutionLoss("requires the exact current control-writer proof");
  const now = executionLossClock(request.observed_at), admittedAt = executionLossClock(receipt.recorded_at);
  const acquired = executionLossClock(lease.acquired_at), updated = executionLossClock(lease.updated_at);
  const expires = executionLossClock(lease.expires_at);
  if (admittedAt > acquired || acquired > updated || updated > now || expires <= now || expires <= updated)
    rejectExecutionLoss("current control-writer clock is stale or ambiguous");
  const currentHash = createHash("sha256").update(proof.idempotency_key).digest("hex");
  if (lease.lease_epoch <= prepared.prepared_lease_epoch || lease.version <= prepared.prepared_lease_version
      || currentHash === prepared.prepared_lease_key_sha256)
    rejectExecutionLoss("cannot replace an available or ambiguous original generation");
  const progress = requireJsonObject(request.progress_observation, "blocked observation");
  if (progress.result_class !== "blocked" || progress.work_item_id !== identity.todo_id
      || typeof progress.blocker_id !== "string" || !Array.isArray(progress.evidence_ids)
      || progress.evidence_ids.length === 0
      || Object.keys(progress).some(field => !["schema_version", "result_class", "work_item_id",
        "blocker_id", "evidence_ids", "fingerprint"].includes(field)))
    rejectExecutionLoss("requires only verified blocked observation and evidence");
  projectProgressDelta({current: progress, baseline: null, window: []});
  return {schema_version: EXECUTION_LOSS_CLOSEOUT_SCHEMA, effect_id: identity.effect_id,
    todo_id: identity.todo_id, source: "turn_settlement", observed_at: request.observed_at,
    original_receipt_event_id: requireNonEmptyString(receipt.event_id, "original receipt event id"),
    original_prepared: {...prepared}, current_control_writer: {
      lease_key_sha256: currentHash, version: lease.version, lease_epoch: lease.lease_epoch,
      owner: lease.owner, store_identity: requireNonEmptyString(request.provider_identity, "provider identity"),
      provider_revision: requireNonEmptyString(request.provider_revision, "provider revision")},
    observation_fingerprint: progress.fingerprint};
}

/** Historical readback validates the frozen qualification, not today's lease. */
export function isExecutionLossCloseout(value: unknown, todoId: string | null): boolean {
  const closeout = typeof value === "object" && value !== null && !Array.isArray(value) ? value as JsonObject : null;
  if (!closeout || closeout.schema_version !== EXECUTION_LOSS_CLOSEOUT_SCHEMA || !todoId
      || closeout.todo_id !== todoId || closeout.source !== "turn_settlement"
      || Object.keys(closeout).sort().join("|") !== ["schema_version", "effect_id", "todo_id", "source",
        "observed_at", "original_receipt_event_id", "original_prepared", "current_control_writer",
        "observation_fingerprint"].sort().join("|")) return false;
  try {
    const original = requireJsonObject(closeout.original_prepared, "original preparation");
    const prepared = heartbeatPreparedLease(original);
    const writer = requireJsonObject(closeout.current_control_writer, "control-writer witness");
    executionLossClock(closeout.observed_at);
    return !!prepared && prepared.prepared_lease_epoch !== undefined
      && isDeepStrictEqual(original, prepared)
      && Object.keys(writer).sort().join("|") === ["lease_key_sha256", "version", "lease_epoch", "owner",
        "store_identity", "provider_revision"].sort().join("|")
      && typeof closeout.effect_id === "string" && !!closeout.effect_id
      && /^[a-f0-9]{64}$/u.test(String(writer.lease_key_sha256))
      && writer.lease_key_sha256 !== prepared.prepared_lease_key_sha256
      && typeof writer.version === "number" && Number.isSafeInteger(writer.version)
      && writer.version > prepared.prepared_lease_version
      && typeof writer.lease_epoch === "number" && Number.isSafeInteger(writer.lease_epoch)
      && writer.lease_epoch > prepared.prepared_lease_epoch
      && typeof closeout.original_receipt_event_id === "string" && !!closeout.original_receipt_event_id
      && typeof writer.owner === "string" && !!writer.owner
      && typeof writer.store_identity === "string" && !!writer.store_identity
      && typeof writer.provider_revision === "string" && !!writer.provider_revision
      && /^[a-f0-9]{16}$/u.test(String(closeout.observation_fingerprint));
  } catch {return false;}
}

function leaseFact(value: unknown, label: string): JsonObject {
  const lease = requireJsonObject(value, label);
  const status = requireStringLiteral(lease.status, LEASE_STATUSES, `${label}.status`);
  const active = requireBoolean(lease.active, `${label}.active`);
  const owner = optionalNonEmptyString(lease.owner, `${label}.owner`);
  if (active && status !== "active") {
    throw new EffectRuntimeRequestError(`${label}.active requires status=active`);
  }
  let proof: JsonObject | undefined;
  if (lease.lease_proof !== undefined) {
    proof = requireJsonObject(lease.lease_proof, `${label}.lease_proof`);
    const key = requireNonEmptyString(proof.idempotency_key, `${label}.lease_proof.idempotency_key`);
    const version = requireInteger(proof.expected_version, `${label}.lease_proof.expected_version`);
    if (key !== key.trim() || !Number.isSafeInteger(version) || version < 1) {
      throw new EffectRuntimeRequestError(`${label}.lease_proof requires an exact key and positive safe-integer version`);
    }
    proof = {idempotency_key: key, expected_version: version};
  }
  return {status, active, ...(owner === null ? {} : {owner}),
    ...(proof === undefined ? {} : {lease_proof: proof, lease_epoch: leaseEpoch(lease)})};
}

function claimAction(goalId: string, todoId: string, agentId: string): JsonObject {
  return {
    command: "loopx todo claim",
    cli_args: [
      "todo", "claim", "--goal-id", goalId, "--todo-id", todoId,
      "--agent-id", agentId, "--claimed-by", agentId,
    ],
    host_execution_allowed: true,
    postcondition: "rerun quota should-run and require execution_preparation.state=ready",
    safety: "ordinary managed claim atomically rechecks current ownership, write scopes, workspace isolation, and lease generation",
  };
}

/** Decide whether the selected Todo can execute under its current hard lease.
 *
 * This is a read-model decision.  It never acquires a lease.  A capable Host
 * may execute the returned ordinary claim before starting the model; every
 * authority and workspace check remains owned by that atomic claim.
 */
export function projectExecutionPreparation(value: unknown): JsonObject {
  const request = requireJsonObject(value, "execution preparation facts");
  if (request.schema_version !== EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `execution_preparation_facts.schema_version must be ${EXECUTION_PREPARATION_FACTS_SCHEMA_VERSION}`,
    );
  }
  const goalId = requireNonEmptyString(request.goal_id, "goal_id");
  const agentId = requireNonEmptyString(request.agent_id, "agent_id");
  const handoffMode = requireStringLiteral(request.handoff_mode, HANDOFF_MODES, "handoff_mode");
  const authorityAvailable = requireBoolean(request.authority_available, "authority_available");
  const selected = requireJsonObject(request.selected_todo, "selected_todo");
  const todoId = requireNonEmptyString(selected.todo_id, "selected_todo.todo_id");
  const claimedBy = optionalNonEmptyString(selected.claimed_by, "selected_todo.claimed_by");
  const status = optionalNonEmptyString(selected.status, "selected_todo.status");
  const rawLeases = request.leases;
  if (!Array.isArray(rawLeases)) {
    throw new EffectRuntimeRequestError("leases must be an array");
  }
  const leases = rawLeases.map((lease, index) => leaseFact(lease, `leases[${index}]`));
  const base = {
    schema_version: EXECUTION_PREPARATION_SCHEMA_VERSION,
    todo_id: todoId,
    agent_id: agentId,
    handoff_mode: handoffMode,
  };

  if (!authorityAvailable) {
    return {...base, state: "authority_unavailable", delivery_allowed: false,
      reason_code: "canonical_authority_unavailable",
      reason: "Canonical Todo and lease authority is unavailable; do not start delivery work."};
  }
  const retired = retiredHandoffModeRejection(handoffMode);
  if (retired) {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: retired, reason: HANDOFF_MODE_RETIRED_REASON};
  }
  if (status !== "open") {
    const retained = leases.length === 1 ? leases[0] : null;
    const retiredProof = request.include_retired_lease_for_writeback === true &&
      status !== null && handoffMode === "hard_lease" &&
      (claimedBy === null || claimedBy === agentId) &&
      retained?.status === "released" && retained.owner === agentId
      ? retained.lease_proof : undefined;
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "selected_todo_not_open",
      reason: "The selected Todo is no longer open; select current work before execution.",
      ...(retiredProof === undefined ? {} : {retired_lease_proof: retiredProof, lease_epoch: retained?.lease_epoch})};
  }
  if (claimedBy !== null && claimedBy !== agentId) {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "todo_claimed_by_other_agent", owner: claimedBy,
      reason: "The selected Todo is claimed by another agent; do not take over its execution."};
  }
  if (handoffMode === "soft_claim") {
    return {...base, state: "not_required", delivery_allowed: true,
      reason_code: "soft_claim_has_no_execution_lease"};
  }

  const active = leases.filter(lease => lease.active === true);
  if (active.length > 1) {
    return {...base, state: "blocked_by_conflict", delivery_allowed: false,
      reason_code: "multiple_active_task_leases",
      reason: "Multiple active leases were observed for one Todo; repair canonical authority before execution."};
  }
  if (active.length === 1) {
    const owner = optionalNonEmptyString(active[0].owner, "active lease owner");
    if (owner !== agentId) {
      return {...base, state: "blocked_by_conflict", delivery_allowed: false,
        reason_code: "active_lease_owned_by_other_agent", ...(owner ? {owner} : {}),
        reason: "The selected Todo has an active lease owned by another agent; do not take over its execution."};
    }
    return {...base, state: "ready", delivery_allowed: true,
      reason_code: "matching_active_task_lease",
      // Capture the admitted generation once. Recovery must not borrow a
      // replacement lease after the original Host result was produced.
      ...(active[0].lease_proof === undefined ? {} :
        {lease_proof: active[0].lease_proof, lease_epoch: active[0].lease_epoch})};
  }

  const prior = leases[0];
  const reasonCode = prior?.status === "released"
    ? "task_lease_released"
    : prior?.status === "active"
    ? "task_lease_expired"
    : "task_lease_missing";
  return {...base, state: "claim_required", delivery_allowed: false,
    reason_code: reasonCode,
    reason: "Prepare the selected Todo with one ordinary managed claim before delivery work.",
    claim: claimAction(goalId, todoId, agentId)};
}
