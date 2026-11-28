/**
 * One owner for the persisted heartbeat-receipt settlement identity rule.
 *
 * A Turn can persist several `quota_should_run` events. The effective receipt
 * is the one that binds a settlement identity; a Turn whose events disagree on
 * that binding is an identity conflict and must fail closed instead of letting
 * a caller infer, upgrade, or silently prefer one binding.
 *
 * Both the settlement readback and the prior-host-Turn closeout selection read
 * this rule, so it lives here rather than in either caller.
 */
import type { JsonObject } from "../effect_program.ts";
import {effectIdsMatch, executionRequestSubject, settlementIdentity, type SettlementIdentity, type ExecutionRequestSubject} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import type {ReceiptBoundReplayPhase} from "./settlement_phase.ts";
import {createHash} from "node:crypto";
import {isDeepStrictEqual} from "node:util";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {leaseRepositoryRejection} from "../work_items/task_lease_repository.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";

const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;
const REPLAN_OBLIGATION_ID_PATTERN = /^replan-[a-f0-9]{16}$/;
/** Public diagnostic preserved from the retired Python identity rule. */
const IDENTITY_CONFLICT_CODE = "heartbeat_receipt_identity_conflict";

function receiptIdentityConflict(message: string): EffectRuntimeRequestError {
  return new EffectRuntimeRequestError(message, IDENTITY_CONFLICT_CODE);
}

export function heartbeatReceiptDetails(event: JsonObject | null): JsonObject {
  return jsonObject(event?.details) ?? {};
}

export function optionalHeartbeatString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return String(value).trim() || null;
}

export function normalizeHeartbeatTodoId(value: unknown): string | null {
  const candidate = String(value ?? "").trim().toLowerCase();
  return candidate && TODO_ID_PATTERN.test(candidate) ? candidate : null;
}

export function normalizeHeartbeatReplanObligationId(
  value: unknown,
): string | null {
  const candidate = String(value ?? "").trim();
  return candidate && REPLAN_OBLIGATION_ID_PATTERN.test(candidate)
    ? candidate
    : null;
}

export interface HeartbeatReceiptBinding extends JsonObject {
  binding_kind: "todo" | "autonomous_replan" | "request";
  /** The Todo id or autonomous replan obligation id, per `binding_kind`. */
  binding_id: string;
  /**
   * The effect id the receipt declares, kept verbatim for projection.
   *
   * It is intentionally not repaired from the identity's derived effect id: a
   * receipt that names an effect the settlement authority could not derive
   * stays visible in the projected payload instead of being silently rewritten.
   */
  settlement_effect_id: string | null;
  /** The conflict-detection key: one Turn may declare only one of these. */
  identity_key: string;
}

/**
 * The only receipt fields the settlement binding rule can read.
 *
 * The persisted rollout event and the settlement readback's own projection both
 * decode into this shape, so one rule serves both readers.  The goal and Agent
 * a receipt belongs to are not receipt facts: they are the scope the reader
 * already selected.
 */
export interface HeartbeatReceiptFact extends JsonObject {
  event_id: string | null;
  run_id: string | null;
  todo_id: string | null;
  replan_obligation_id: string | null;
  request_subject?: ExecutionRequestSubject | null;
  settlement_effect_id: string | null;
  closeout_required: boolean;
}

export function heartbeatReceiptFactFromEvent(
  event: JsonObject,
): HeartbeatReceiptFact {
  const eventDetails = heartbeatReceiptDetails(event);
  return {
    event_id: optionalHeartbeatString(event.event_id),
    run_id: optionalHeartbeatString(event.run_id),
    todo_id: optionalHeartbeatString(eventDetails.todo_id),
    replan_obligation_id: optionalHeartbeatString(
      eventDetails.replan_obligation_id,
    ),
    ...(!Object.hasOwn(eventDetails, "request_id") ? {} : {request_subject: executionRequestSubject({
      request_id: eventDetails.request_id, request_revision: eventDetails.request_revision,
      request_digest: eventDetails.request_digest, execution_scope_sha256: eventDetails.execution_scope_sha256})}),
    settlement_effect_id: optionalHeartbeatString(
      eventDetails.settlement_effect_id,
    ),
    closeout_required: eventDetails.closeout_required === true,
  };
}

/** Read positive autonomous replan admission from an effective guard receipt. */
export function admitsAutonomousReplan(event: JsonObject, fact: HeartbeatReceiptFact): boolean {
  const guard = heartbeatReceiptDetails(event);
  const obligationId = normalizeHeartbeatReplanObligationId(guard.semantic_replan_obligation_id);
  return fact.settlement_effect_id !== null && obligationId !== null
    && (fact.todo_id !== null || obligationId === fact.replan_obligation_id)
    && guard.interaction_mode === "autonomous_replan" && guard.ok === true && guard.should_run === true
    && guard.must_attempt_work === true && guard.delivery_allowed === false && guard.quiet_noop_allowed === false;
}

/**
 * Resolve the settlement binding a persisted receipt declares.
 *
 * A receipt without a binding is not a settlement identity; it is reported as
 * `null` so the caller can decide whether an unbound receipt is admissible.
 */
export function heartbeatReceiptBinding(
  goalId: string,
  agentId: string,
  fact: HeartbeatReceiptFact,
): HeartbeatReceiptBinding | null {
  const declaredTodoId = optionalHeartbeatString(fact.todo_id);
  const replanObligationId = normalizeHeartbeatReplanObligationId(
    fact.replan_obligation_id,
  );
  const declaredEffectId = optionalHeartbeatString(fact.settlement_effect_id);
  const requestSubject = executionRequestSubject(fact.request_subject);
  if ([declaredTodoId, replanObligationId, requestSubject].filter(Boolean).length > 1) {
    throw receiptIdentityConflict(
      "heartbeat receipt has conflicting Todo and autonomous replan bindings",
    );
  }
  if (declaredEffectId && !declaredTodoId && !replanObligationId && !requestSubject) {
    throw receiptIdentityConflict(
      "heartbeat receipt has an effect identity without a Todo or autonomous replan binding; refuse to infer or upgrade it",
    );
  }
  if (!declaredTodoId && !replanObligationId && !requestSubject) return null;
  // A Todo-binding the settlement authority cannot address is not a binding we
  // may act on: acting on the raw string would create a recovery obligation
  // whose identity no other reader can reproduce, and dropping it would let a
  // required closeout disappear. Refuse the read instead.
  const todoId = declaredTodoId;
  if (todoId !== null && normalizeHeartbeatTodoId(todoId) !== todoId) {
    throw receiptIdentityConflict(
      "heartbeat receipt declares a Todo binding that is not a legal Todo id",
    );
  }
  const identity = settlementIdentity({
    goal_id: goalId,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: fact.run_id ?? "",
    replan_obligation_id: replanObligationId, request_subject: requestSubject,
  });
  return {
    binding_kind: identity.binding_kind as HeartbeatReceiptBinding["binding_kind"],
    binding_id: identity.binding_id,
    settlement_effect_id: declaredEffectId,
    identity_key: `${identity.binding_kind}\u0000${identity.binding_id}\u0000${
      declaredEffectId ?? identity.effect_id
    }`,
  };
}

/**
 * Reduce one Turn's receipts to its effective receipt.
 *
 * Receipts without a settlement binding are admissible only while the Turn
 * declares no binding at all; they cannot outrank a bound receipt, and they
 * cannot silently turn a conflicting Turn into a valid one.
 */
export function selectEffectiveHeartbeatReceipt<Value>(
  goalId: string,
  agentId: string,
  entries: readonly { fact: HeartbeatReceiptFact; value: Value }[],
): Value | null {
  if (entries.length === 0) return null;
  const identities = new Map<string, Value>();
  for (const entry of entries) {
    const binding = heartbeatReceiptBinding(goalId, agentId, entry.fact);
    if (binding) identities.set(binding.identity_key, entry.value);
  }
  if (identities.size > 1) {
    throw receiptIdentityConflict(
      "heartbeat receipt has conflicting settlement identities for the same goal, agent, and turn",
    );
  }
  return identities.size === 1
    ? [...identities.values()][0]!
    : entries.at(-1)!.value;
}

/** Pure transport for Python receipt callers. File/GoalRef scoping stays with
 * their locked reader; this projection grants no admission or settlement.
 * Return an index so unknown event details and numeric IO stay in that reader. */
export function projectHeartbeatReceiptSelection(value: unknown): JsonObject {
  const request = requireJsonObject(value, "heartbeat receipt selection");
  if (typeof request.goal_id !== "string" || typeof request.agent_id !== "string" ||
      !Array.isArray(request.events)) {
    throw new EffectRuntimeRequestError("heartbeat receipt selection requires scoped event facts");
  }
  const goalId = request.goal_id, agentId = request.agent_id;
  const entries = request.events.map((value, index) => ({
    fact: heartbeatReceiptFactFromEvent(requireJsonObject(value, "heartbeat receipt event")),
    value: index,
  }));
  const index = selectEffectiveHeartbeatReceipt(goalId, agentId, entries);
  if (index === null) return {index: null, binding: null};
  const fact = entries[index]!.fact;
  const binding = heartbeatReceiptBinding(goalId, agentId, fact);
  if (binding === null) return {index, binding: null};
  // The original receipt may omit its derived effect. Preserve any declared
  // effect verbatim, including a conflicting one, just as the shared rule does.
  return {index, binding: {
    binding_kind: binding.binding_kind,
    binding_id: binding.binding_id,
    effect_id: binding.settlement_effect_id ?? settlementIdentity({
      goal_id: goalId, agent_id: agentId, turn_instance_id: fact.run_id ?? "",
      todo_id: fact.todo_id,
      replan_obligation_id: normalizeHeartbeatReplanObligationId(fact.replan_obligation_id),
      request_subject: fact.request_subject,
    }).effect_id,
  }};
}

/**
 * The first bound receipt for one Turn, used only as the admission clock.
 *
 * `selectEffectiveHeartbeatReceipt` still returns the latest bound receipt so
 * review and work content stay current. A later same-Turn upgrade must not
 * move the historical-ACK admission time or log position. Callers pass the
 * GoalRef-filtered receipt list; this reduction does not widen that scope.
 * Conflicting bindings fail closed, the same way the effective reduction does.
 */
export function selectAdmissionHeartbeatReceipt<Value>(
  goalId: string,
  agentId: string,
  entries: readonly { fact: HeartbeatReceiptFact; value: Value }[],
): Value | null {
  const identities = new Map<string, Value>();
  for (const entry of entries) {
    const binding = heartbeatReceiptBinding(goalId, agentId, entry.fact);
    if (!binding || identities.has(binding.identity_key)) continue;
    identities.set(binding.identity_key, entry.value);
  }
  if (identities.size > 1) {
    throw receiptIdentityConflict(
      "heartbeat receipt has conflicting settlement identities for the same goal, agent, and turn",
    );
  }
  return identities.size === 1 ? [...identities.values()][0]! : null;
}

export interface HeartbeatPreparedLease extends JsonObject {
  prepared_lease_key_sha256: string;
  prepared_lease_version: number;
  prepared_lease_epoch?: number;
}

export function heartbeatPreparedLease(details: JsonObject): HeartbeatPreparedLease | null {
  const key = details.prepared_lease_key_sha256;
  const version = details.prepared_lease_version;
  const epoch = details.prepared_lease_epoch;
  if (key === undefined && version === undefined && epoch === undefined) return null;
  if (typeof key !== "string" || !BARE_SHA256_PATTERN.test(key) ||
      typeof version !== "number" || !Number.isSafeInteger(version) || version < 1 ||
      (epoch !== undefined && (typeof epoch !== "number" || !Number.isSafeInteger(epoch) || epoch < 1))) {
    throw new EffectRuntimeRequestError("Turn readiness has a malformed lease proof", "malformed_settlement_state");
  }
  return {prepared_lease_key_sha256: key, prepared_lease_version: version,
    ...(epoch === undefined ? {} : {prepared_lease_epoch: epoch})};
}

export function heartbeatPreparedWorkAdmission(
  event: JsonObject,
  identity: SettlementIdentity,
): HeartbeatPreparedLease | null {
  const fact = heartbeatReceiptFactFromEvent(event);
  const binding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
  const receiptDetails = heartbeatReceiptDetails(event);
  if (event.schema_version !== "loopx_rollout_event_v0" || event.event_kind !== "quota_should_run"
      || event.goal_id !== identity.goal_id || event.agent_id !== identity.agent_id
      || fact.run_id !== identity.turn_instance_id || fact.todo_id !== identity.todo_id
      || binding?.binding_kind !== "todo" || binding.binding_id !== identity.todo_id
      || (fact.settlement_effect_id !== null && !effectIdsMatch(fact.settlement_effect_id, identity.effect_id))
      || fact.closeout_required !== true
      || (Object.hasOwn(receiptDetails, "prepared_lease_required") && receiptDetails.prepared_lease_required !== true)
      || nativeChildReportAdmission(receiptDetails, event.status, identity.effect_id, "open", false,
        normalizeHeartbeatReplanObligationId(receiptDetails.semantic_replan_obligation_id))
        .report_permission !== "new_operation") return null;
  return heartbeatPreparedLease(receiptDetails);
}

export function isHeartbeatLeaseReadiness(event: JsonObject): boolean {
  return heartbeatReceiptDetails(event).settlement_receipt_revision === "lease_readiness";
}

/** A fresh guard and fenced canonical snapshot qualify a new writer, not old
 * test results. The original admission stays reachable through receipt lineage. */
function heartbeatLeaseTakeover(original: JsonObject, current: JsonObject,
  identity: SettlementIdentity, previous: HeartbeatPreparedLease,
  prepared: HeartbeatPreparedLease | null): JsonObject {
  const reject = (): never => {throw receiptIdentityConflict(
    "work requalification must preserve this Turn's original task lease generation unless a fenced current holder qualifies takeover");};
  const source = jsonObject(current.lease_takeover_source);
  const basis = jsonObject(source?.takeover_basis), todo = jsonObject(basis?.todo);
  const leases = source?.leases;
  const lease = Array.isArray(leases) && leases.length === 1 ? jsonObject(leases[0]) : null;
  const old = heartbeatReceiptDetails(original);
  if (!source || !basis || !todo || !lease || !prepared ||
      typeof original.event_id !== "string" || !original.event_id ||
      previous.prepared_lease_epoch === undefined || prepared.prepared_lease_epoch === undefined ||
      source.goal_id !== identity.goal_id || source.todo_id !== identity.todo_id ||
      source.handoff_mode !== "hard_lease" || !["file_v0", "sqlite_v0"].includes(String(source.source_authority)) ||
      source.decision_read_from_provider !== true || source.legacy_fallback_used !== false ||
      typeof source.provider_revision !== "string" || !source.provider_revision ||
      typeof basis.acceptance_sha256 !== "string" || !BARE_SHA256_PATTERN.test(basis.acceptance_sha256) ||
      todo.todo_id !== identity.todo_id || todo.role !== "agent" || todo.task_class !== "advancement_task" ||
      todo.status !== "open" || todo.done === true || todo.archive_state !== "active" ||
      todo.claimed_by !== identity.agent_id ||
      (Array.isArray(todo.excluded_agents) && todo.excluded_agents.includes(identity.agent_id)) ||
      lease.todo_id !== identity.todo_id || lease.owner !== identity.agent_id || lease.status !== "active" ||
      leaseRepositoryRejection(todo, lease) !== null ||
      typeof lease.idempotency_key !== "string" || !lease.idempotency_key ||
      lease.version !== prepared.prepared_lease_version || lease.lease_epoch !== prepared.prepared_lease_epoch ||
      prepared.prepared_lease_version <= previous.prepared_lease_version ||
      prepared.prepared_lease_epoch <= previous.prepared_lease_epoch ||
      prepared.prepared_lease_key_sha256 === previous.prepared_lease_key_sha256 ||
      createHash("sha256").update(lease.idempotency_key).digest("hex") !== prepared.prepared_lease_key_sha256 ||
      heartbeatPreparedWorkAdmission(original, identity) === null ||
      normalizeHeartbeatReplanObligationId(old.semantic_replan_obligation_id) !== null ||
      normalizeHeartbeatReplanObligationId(current.semantic_replan_obligation_id) !== null) return reject();
  const clock = (value: unknown): bigint | null => typeof value === "string" ? parseTodoTimestampMicros(value, true) : null;
  const at = clock(source.observed_at), admitted = clock(original.recorded_at),
    acquired = clock(lease.acquired_at), updated = clock(lease.updated_at), expires = clock(lease.expires_at);
  if (at === null || admitted === null || acquired === null || updated === null || expires === null ||
      admitted > acquired || acquired > updated || updated > at || expires <= at) return reject();
  return {append: true, details: {...old, ...prepared, settlement_receipt_revision: "lease_takeover",
    takeover_original_event_id: original.event_id,
    takeover_original_key_sha256: previous.prepared_lease_key_sha256,
    takeover_original_version: previous.prepared_lease_version,
    takeover_original_epoch: previous.prepared_lease_epoch,
    takeover_owner: identity.agent_id, takeover_observed_at: source.observed_at,
    takeover_expires_at: lease.expires_at, takeover_provider_revision: source.provider_revision,
    takeover_todo_sha256: canonicalAuthoritySha256(todo), takeover_acceptance_sha256: basis.acceptance_sha256}};
}

/** Bind a completed Todo's retired completing generation under the maintenance fence
 * when completion preceded takeover and no writeback or spend has started yet. */
function heartbeatCompletedLeaseTakeover(original: JsonObject, current: JsonObject,
  identity: SettlementIdentity, completionEvent: JsonObject): JsonObject {
  const replay = {append: false, details: null};
  const reject = (): never => {throw receiptIdentityConflict(
    "work requalification must preserve this Turn's original task lease generation unless a fenced completing holder qualifies takeover");};
  const source = jsonObject(current.lease_takeover_source);
  if (!source || source.handoff_mode === "soft_claim") return replay;
  const previous = heartbeatPreparedWorkAdmission(original, identity);
  const old = heartbeatReceiptDetails(original);
  const basis = jsonObject(source.takeover_basis), todo = jsonObject(basis?.todo);
  const leases = source.leases;
  const lease = Array.isArray(leases) && leases.length === 1 ? jsonObject(leases[0]) : null;
  if (previous !== null && previous.prepared_lease_epoch !== undefined && lease !== null &&
      lease.todo_id === identity.todo_id && lease.owner === identity.agent_id &&
      lease.status === "released" && typeof lease.idempotency_key === "string" && lease.idempotency_key &&
      lease.lease_epoch === previous.prepared_lease_epoch &&
      typeof lease.version === "number" && Number.isSafeInteger(lease.version) &&
      lease.version >= previous.prepared_lease_version &&
      createHash("sha256").update(lease.idempotency_key).digest("hex") === previous.prepared_lease_key_sha256) {
    return replay;
  }
  if (!basis || !todo || !lease || previous === null || previous.prepared_lease_epoch === undefined ||
      typeof original.event_id !== "string" || !original.event_id ||
      typeof completionEvent.event_id !== "string" || !completionEvent.event_id ||
      completionEvent.event_kind !== "todo_complete" ||
      completionEvent.goal_id !== identity.goal_id || completionEvent.agent_id !== identity.agent_id ||
      completionEvent.todo_id !== identity.todo_id || completionEvent.run_id !== identity.turn_instance_id ||
      !effectIdsMatch(optionalHeartbeatString(heartbeatReceiptDetails(completionEvent).settlement_effect_id), identity.effect_id) ||
      source.goal_id !== identity.goal_id || source.todo_id !== identity.todo_id ||
      source.handoff_mode !== "hard_lease" || !["file_v0", "sqlite_v0"].includes(String(source.source_authority)) ||
      source.decision_read_from_provider !== true || source.legacy_fallback_used !== false ||
      typeof source.provider_revision !== "string" || !source.provider_revision ||
      typeof basis.acceptance_sha256 !== "string" || !BARE_SHA256_PATTERN.test(basis.acceptance_sha256) ||
      todo.todo_id !== identity.todo_id || todo.role !== "agent" || todo.task_class !== "advancement_task" ||
      todo.status !== "done" || todo.done !== true ||
      (todo.archive_state !== "active" && todo.archive_state !== "archive") ||
      (todo.claimed_by != null && todo.claimed_by !== identity.agent_id) ||
      (Array.isArray(todo.excluded_agents) && todo.excluded_agents.includes(identity.agent_id)) ||
      todo.completion_turn_key !== identity.effect_id ||
      lease.todo_id !== identity.todo_id || lease.owner !== identity.agent_id || lease.status !== "released" ||
      leaseRepositoryRejection(todo, lease) !== null ||
      typeof lease.idempotency_key !== "string" || !lease.idempotency_key ||
      typeof lease.version !== "number" || !Number.isSafeInteger(lease.version) ||
      lease.version <= previous.prepared_lease_version ||
      typeof lease.lease_epoch !== "number" || !Number.isSafeInteger(lease.lease_epoch) ||
      lease.lease_epoch <= previous.prepared_lease_epoch ||
      normalizeHeartbeatReplanObligationId(old.semantic_replan_obligation_id) !== null ||
      normalizeHeartbeatReplanObligationId(current.semantic_replan_obligation_id) !== null) return reject();
  const keySha = createHash("sha256").update(lease.idempotency_key).digest("hex");
  if (keySha === previous.prepared_lease_key_sha256) return reject();
  // Terminal completion releases the lease by updating `updated_at` without
  // writing a separate `released_at` field; explicit lease releases write both.
  const retiredAtRaw = lease.released_at === undefined ? lease.updated_at : lease.released_at;
  const clock = (value: unknown): bigint | null => typeof value === "string" ? parseTodoTimestampMicros(value, true) : null;
  const at = clock(source.observed_at), admitted = clock(original.recorded_at),
    acquired = clock(lease.acquired_at), updated = clock(lease.updated_at),
    released = clock(retiredAtRaw), expires = clock(lease.expires_at);
  if (at === null || admitted === null || acquired === null || updated === null ||
      released === null || expires === null ||
      admitted > acquired || acquired > updated || released > updated || updated > at || released > expires) {
    return reject();
  }
  const prepared: HeartbeatPreparedLease = {
    prepared_lease_key_sha256: keySha,
    prepared_lease_version: lease.version,
    prepared_lease_epoch: lease.lease_epoch,
  };
  return {append: true, details: {...old, ...prepared, settlement_receipt_revision: "completed_lease_takeover",
    takeover_original_event_id: original.event_id,
    takeover_original_key_sha256: previous.prepared_lease_key_sha256,
    takeover_original_version: previous.prepared_lease_version,
    takeover_original_epoch: previous.prepared_lease_epoch,
    takeover_owner: identity.agent_id, takeover_observed_at: source.observed_at,
    takeover_released_at: retiredAtRaw as string, takeover_expires_at: lease.expires_at,
    takeover_provider_revision: source.provider_revision,
    takeover_todo_sha256: canonicalAuthoritySha256(todo), takeover_acceptance_sha256: basis.acceptance_sha256,
    takeover_completion_event_id: completionEvent.event_id}};
}

/** Reject broken or rewritten takeover lineage during cold settlement reads. */
export function validateHeartbeatTakeoverParent(event: JsonObject, parent: JsonObject,
  identity: SettlementIdentity): void {
  const details = heartbeatReceiptDetails(event);
  const revision = details.settlement_receipt_revision;
  if (revision !== "lease_takeover" && revision !== "completed_lease_takeover") return;
  const old = heartbeatPreparedWorkAdmission(parent, identity), current = heartbeatPreparedLease(details);
  const completed = revision === "completed_lease_takeover";
  const fields = {takeover_original_event_id: details.takeover_original_event_id,
    takeover_original_key_sha256: details.takeover_original_key_sha256,
    takeover_original_version: details.takeover_original_version, takeover_original_epoch: details.takeover_original_epoch,
    takeover_owner: details.takeover_owner, takeover_observed_at: details.takeover_observed_at,
    ...(completed ? {takeover_released_at: details.takeover_released_at} : {}),
    takeover_expires_at: details.takeover_expires_at, takeover_provider_revision: details.takeover_provider_revision,
    takeover_todo_sha256: details.takeover_todo_sha256, takeover_acceptance_sha256: details.takeover_acceptance_sha256,
    ...(completed ? {takeover_completion_event_id: details.takeover_completion_event_id} : {})};
  const time = (value: unknown): bigint | null => typeof value === "string" ? parseTodoTimestampMicros(value, true) : null;
  const at = time(details.takeover_observed_at), expires = time(details.takeover_expires_at),
    released = completed ? time(details.takeover_released_at) : null, recorded = time(event.recorded_at);
  const validClock = completed
    ? at !== null && released !== null && expires !== null && recorded !== null &&
      released <= expires && released <= at && recorded >= at
    : at !== null && expires !== null && recorded !== null && expires > at && recorded >= at;
  if (!old || !current || old.prepared_lease_epoch === undefined ||
      current.prepared_lease_epoch === undefined || current.prepared_lease_epoch <= old.prepared_lease_epoch ||
      current.prepared_lease_version <= old.prepared_lease_version ||
      current.prepared_lease_key_sha256 === old.prepared_lease_key_sha256 ||
      details.takeover_original_event_id !== parent.event_id || details.takeover_owner !== identity.agent_id ||
      details.takeover_original_key_sha256 !== old.prepared_lease_key_sha256 ||
      details.takeover_original_version !== old.prepared_lease_version ||
      details.takeover_original_epoch !== old.prepared_lease_epoch ||
      (completed && (typeof details.takeover_completion_event_id !== "string" || !details.takeover_completion_event_id.trim())) ||
      !isDeepStrictEqual(details, {...heartbeatReceiptDetails(parent), ...current,
        settlement_receipt_revision: revision, ...fields}) ||
      ![details.takeover_todo_sha256, details.takeover_acceptance_sha256].every(value => typeof value === "string" && BARE_SHA256_PATTERN.test(value)) ||
      typeof details.takeover_provider_revision !== "string" || !details.takeover_provider_revision ||
      !validClock) {
    throw receiptIdentityConflict("lease takeover does not preserve original admission and verified current ownership");
  }
}

/** Reentry can qualify work on the original binding, never replace its authority.
 * The caller commits this append-only projection under the receipt-log lock. */
export function heartbeatWorkRequalification(
  original: JsonObject,
  currentDetails: JsonObject,
  identity: SettlementIdentity,
  phase: ReceiptBoundReplayPhase,
  closeoutStarted: boolean,
  completedTurnEvent: JsonObject | null = null,
): JsonObject {
  const replay = {append: false, details: null};
  const candidate = heartbeatReceiptBinding(identity.goal_id, identity.agent_id,
    heartbeatReceiptFactFromEvent({...original, details: currentDetails}));
  if (candidate === null || candidate.binding_kind !== identity.binding_kind ||
      candidate.binding_id !== identity.binding_id ||
      candidate.settlement_effect_id !== identity.effect_id) {
    throw receiptIdentityConflict("work requalification must preserve the original settlement binding");
  }
  if (phase === "settlement_pending" && completedTurnEvent !== null &&
      identity.binding_kind === "todo" && identity.todo_id !== null) {
    return heartbeatCompletedLeaseTakeover(original, currentDetails, identity, completedTurnEvent);
  }
  if (phase !== "open" || closeoutStarted) return replay;
  // The retained settlement binding is not today's executable selection.
  // A waiting Todo can leave broad compatibility flags set with no selected
  // work; those flags cannot requalify a repair-only original receipt.
  if (identity.todo_id !== null && currentDetails.selected_todo_id !== identity.todo_id) return replay;
  // No legacy/status-label compatibility for new qualifications: all modern
  // work facts must be explicit. This reuses the report-admission predicate.
  const oldDetails = heartbeatReceiptDetails(original);
  const originalReplan = normalizeHeartbeatReplanObligationId(oldDetails.semantic_replan_obligation_id);
  const currentReplan = normalizeHeartbeatReplanObligationId(currentDetails.semantic_replan_obligation_id);
  if (nativeChildReportAdmission(currentDetails, null, identity.effect_id, phase, false, currentReplan)
      .report_permission !== "new_operation") return replay;
  const preparedLease = identity.todo_id === null ? null : heartbeatPreparedLease(currentDetails);
  const previous = identity.todo_id === null ? null : heartbeatPreparedLease(oldDetails);
  if (previous !== null && (
      preparedLease === null || preparedLease.prepared_lease_key_sha256 !== previous.prepared_lease_key_sha256 ||
      (previous.prepared_lease_epoch === undefined
        ? preparedLease.prepared_lease_version !== previous.prepared_lease_version
        : preparedLease.prepared_lease_epoch !== previous.prepared_lease_epoch))) {
    return heartbeatLeaseTakeover(original, currentDetails, identity, previous, preparedLease);
  }
  if (nativeChildReportAdmission(oldDetails, original.status, identity.effect_id, phase, false, originalReplan)
      .report_permission === "new_operation") {
    if (preparedLease === null || originalReplan !== currentReplan) return replay;
    const previousVersion = previous?.prepared_lease_version ?? 0;
    // A delayed readiness evaluation can never replace a newer lease. Only
    // the preparation proof changes; admission, billing and workspace stay bound.
    if (preparedLease.prepared_lease_version <= previousVersion) return replay;
    return {append: true, details: {
      ...oldDetails, ...preparedLease, settlement_receipt_revision: "lease_readiness",
    }};
  }
  if (currentReplan !== null && currentReplan !== originalReplan) {
    throw receiptIdentityConflict("work requalification cannot replace the original semantic replan guard");
  }
  const qualified = {...oldDetails, ...preparedLease};
  // The guarded route becomes durable when this binding first admits work.
  const spendSource = currentDetails.quota_spend_source;
  if (spendSource != null) {
    if (spendSource !== "heartbeat" && spendSource !== "visible-goal") {
      throw receiptIdentityConflict("work requalification has an unsupported quota spend source");
    }
    if (oldDetails.quota_spend_source != null && oldDetails.quota_spend_source !== spendSource) {
      throw receiptIdentityConflict("work requalification cannot replace the original quota spend source");
    }
    qualified.quota_spend_source = spendSource;
  }
  // The first positive work admission owns these facts. A repair-only
  // receipt's earlier selection cannot describe the newly admitted work.
  for (const field of ["ok", "should_run", "must_attempt_work", "delivery_allowed", "quiet_noop_allowed",
    "interaction_mode", "admission_resume_when", "admission_resume_ready"]) {
    if (currentDetails[field] !== undefined) qualified[field] = currentDetails[field]!;
    else delete qualified[field];
  }
  // Preserve workspace causality, semantic guards, pending selection and all
  // original identities. Work admission makes this same Todo owe closeout.
  qualified.closeout_required = identity.todo_id !== null;
  qualified.settlement_receipt_revision = "work_admission";
  return {append: true, details: qualified};
}
