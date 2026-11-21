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
import {effectIdsMatch, executionRequestSubject, settlementIdentity, settlementIdentityPayload, type SettlementIdentity, type ExecutionRequestSubject} from "../effect_program.ts";
import {isDeepStrictEqual} from "node:util";
import {createHash} from "node:crypto";
import {leaseIsActive} from "../work_items/task_lease_record.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import type {ReceiptBoundReplayPhase} from "./settlement_phase.ts";
import type {ProviderEffect} from "../turn_driver/settlement_provider.ts";
import {normalizeHeartbeatReplanObligationId, projectSemanticReplanGuard} from "./semantic_replan_guard.ts";
export {normalizeHeartbeatReplanObligationId} from "./semantic_replan_guard.ts";

const TODO_ID_PATTERN = /^todo_[a-z0-9_-]{3,64}$/;
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

interface HeartbeatPreparedLease extends JsonObject {
  prepared_lease_key_sha256: string;
  prepared_lease_version: number;
  prepared_lease_epoch?: number;
}

export const HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA =
  "loopx_quota_heartbeat_prepared_publication_request_v0";

/** Publication compatibility is separate from execution readiness. A null
 * proposal preserves stored history; it cannot renew or repair a proof. */
export function qualifyHeartbeatPreparedPublication(value: unknown): JsonObject {
  const prepared = (fields: JsonObject): HeartbeatPreparedLease | null => {
    try {return heartbeatPreparedLease(fields);}
    catch (error) {
      if (error instanceof EffectRuntimeRequestError) throw receiptIdentityConflict(
        "prepared receipt publication requires a complete exact proof");
      throw error;
    }
  };
  const request = requireJsonObject(value, "prepared receipt publication");
  if (request.schema_version !== HEARTBEAT_PREPARED_PUBLICATION_REQUEST_SCHEMA
      || Object.keys(request).some(key => !["schema_version", "existing_receipt",
        "original_prepared", "proposed_prepared_fields", "receipt_history", "settlement_identity"].includes(key))) {
    throw receiptIdentityConflict("prepared receipt publication request is malformed");
  }
  let existing = request.existing_receipt === null ? null
    : requireJsonObject(request.existing_receipt, "existing heartbeat receipt");
  const originalFields = request.original_prepared === null ? null
    : requireJsonObject(request.original_prepared, "original prepared proof");
  const suppliedOriginal = originalFields === null ? null : prepared(originalFields);
  if (originalFields !== null && (suppliedOriginal === null || !isDeepStrictEqual(originalFields, suppliedOriginal))) {
    throw receiptIdentityConflict("original prepared proof is incomplete");
  }
  let original = suppliedOriginal;
  const hasHistory = Object.hasOwn(request, "receipt_history");
  if (hasHistory !== Object.hasOwn(request, "settlement_identity")) {
    throw receiptIdentityConflict("publication receipt history requires its exact settlement identity");
  }
  if (hasHistory) {
    if (!Array.isArray(request.receipt_history)) throw receiptIdentityConflict("publication receipt history is malformed");
    const binding = requireJsonObject(request.settlement_identity, "publication settlement identity");
    const identity = settlementIdentity({goal_id: requireNonEmptyString(binding.goal_id, "goal_id"),
      agent_id: requireNonEmptyString(binding.agent_id, "agent_id"),
      todo_id: optionalHeartbeatString(binding.todo_id),
      turn_instance_id: requireNonEmptyString(binding.turn_instance_id, "turn_instance_id"),
      replan_obligation_id: optionalHeartbeatString(binding.replan_obligation_id),
      request_subject: executionRequestSubject(binding.request_subject)});
    if (!isDeepStrictEqual(binding, settlementIdentityPayload(identity))) {
      throw receiptIdentityConflict("publication must preserve the exact settlement identity");
    }
    const history = request.receipt_history.map(row => requireJsonObject(row, "heartbeat history row"))
      .filter(row => row.event_kind === "quota_should_run" && row.goal_id === identity.goal_id
        && row.agent_id === identity.agent_id && row.run_id === identity.turn_instance_id);
    if (history.length && history.some(row => !isDeepStrictEqual(row.goal_ref ?? null,
        (existing ?? history[0]!).goal_ref ?? null))) {
      throw receiptIdentityConflict("publication receipt history crosses Goal instances");
    }
    const effective = history.length === 0 ? null : selectEffectiveHeartbeatReceipt(identity.goal_id,
      identity.agent_id, history.map(value => ({value, fact: heartbeatReceiptFactFromEvent(value)})));
    if (existing !== null && !isDeepStrictEqual(existing, effective)) {
      throw receiptIdentityConflict("publication effective receipt differs from its locked history");
    }
    existing = effective;
    const allowLegacyPrepared = effective !== null && !Object.hasOwn(effective, "goal_ref");
    const validation = effective === null ? null
      : heartbeatValidationEvent(history, effective, identity, allowLegacyPrepared);
    if (effective !== null && validation === null) {
      throw receiptIdentityConflict("publication prepared receipt causality is incomplete");
    }
    original = validation === null ? null
      : heartbeatPreparedWorkAdmission(validation, identity, allowLegacyPrepared);
    if (suppliedOriginal !== null && !isDeepStrictEqual(suppliedOriginal, original)) {
      throw receiptIdentityConflict("supplied original proof differs from its causal admission");
    }
  }
  const storedDetails = heartbeatReceiptDetails(existing);
  const stored = prepared(storedDetails);
  const retained: JsonObject = {...stored,
    ...(Object.hasOwn(storedDetails, "prepared_lease_required")
      ? {prepared_lease_required: storedDetails.prepared_lease_required} : {})};
  if (request.proposed_prepared_fields === null) return {ok: true, prepared_fields: retained};
  const proposal = requireJsonObject(request.proposed_prepared_fields, "proposed prepared fields");
  if (Object.keys(proposal).some(key => !["prepared_lease_required", "prepared_lease_key_sha256",
      "prepared_lease_version", "prepared_lease_epoch"].includes(key))
      || (Object.hasOwn(proposal, "prepared_lease_required")
        && typeof proposal.prepared_lease_required !== "boolean")) {
    throw receiptIdentityConflict("proposed prepared fields are malformed");
  }
  const proposed = prepared(proposal);
  if (proposed === null) throw receiptIdentityConflict("a non-null publication proposal requires a complete proof");
  if ((!hasHistory && stored !== null && original !== null && !isDeepStrictEqual(stored, original))
      || !isDeepStrictEqual(original ?? stored ?? proposed, proposed)
      || (storedDetails.prepared_lease_required === true && proposal.prepared_lease_required === false)) {
    throw receiptIdentityConflict("prepared receipt publication must preserve the exact original proof");
  }
  // A valid replay of the causal root does not rewrite a later stored
  // readiness record. The append/binder retains that record's exact fields.
  return {ok: true, prepared_fields: stored === null ? {...retained, ...proposal} : retained};
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

/** Positive prepared work admission, using the existing identity and report
 * admission owners. A pre-upgrade receipt may omit the derived effect id. */
export function heartbeatPreparedWorkAdmission(
  event: JsonObject,
  identity: SettlementIdentity,
  allowLegacyPrepared = false,
): HeartbeatPreparedLease | null {
  const fact = heartbeatReceiptFactFromEvent(event);
  const binding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
  const receiptDetails = heartbeatReceiptDetails(event);
  // The ordinary Turn guard predates work projections. This compatibility is
  // explicit at alias callers and cannot promote partial modern facts or
  // supply the strict work admission required by execution-loss closeout.
  const legacyPrepared = allowLegacyPrepared && !Object.hasOwn(event, "goal_ref")
    && event.status === "turn_run_once" && !Object.hasOwn(receiptDetails, "closeout_required")
    && receiptDetails.prepared_lease_required === true
    && ["must_attempt_work", "delivery_allowed", "quiet_noop_allowed"]
      .every(field => !Object.hasOwn(receiptDetails, field));
  if (event.schema_version !== "loopx_rollout_event_v0" || event.event_kind !== "quota_should_run"
      || event.goal_id !== identity.goal_id || event.agent_id !== identity.agent_id
      || fact.run_id !== identity.turn_instance_id || fact.todo_id !== identity.todo_id
      || binding?.binding_kind !== "todo" || binding.binding_id !== identity.todo_id
      || (fact.settlement_effect_id !== null && !effectIdsMatch(fact.settlement_effect_id, identity.effect_id))
      || (fact.closeout_required !== true && !legacyPrepared)
      || (Object.hasOwn(receiptDetails, "prepared_lease_required") && receiptDetails.prepared_lease_required !== true)
      || nativeChildReportAdmission(receiptDetails, event.status, identity.effect_id, "open", false,
        normalizeHeartbeatReplanObligationId(receiptDetails.semantic_replan_obligation_id))
        .report_permission !== "new_operation") return null;
  return heartbeatPreparedLease(receiptDetails);
}

/** One causal owner for binding validation and original positive preparation.
 * Readiness, work and identity revisions cannot replace an earlier admission. */
export function heartbeatValidationEvent(
  events: readonly JsonObject[],
  receipt: JsonObject,
  identity: SettlementIdentity,
  allowLegacyPrepared = false,
): JsonObject | null {
  return selectHeartbeatValidationEvent(events, receipt, identity, allowLegacyPrepared, false);
}

/** A semantic admission remains bound to its original Todo and obligation;
 * its report permission supplies no business execution lease. */
function heartbeatSemanticWorkAdmission(event: JsonObject, identity: SettlementIdentity): string | null {
  const fact = heartbeatReceiptFactFromEvent(event), receiptDetails = heartbeatReceiptDetails(event);
  const binding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
  const selected = normalizeHeartbeatReplanObligationId(receiptDetails.semantic_replan_obligation_id);
  if (selected === null || event.schema_version !== "loopx_rollout_event_v0"
      || event.event_kind !== "quota_should_run" || event.goal_id !== identity.goal_id
      || event.agent_id !== identity.agent_id || fact.run_id !== identity.turn_instance_id
      || fact.todo_id !== identity.todo_id || binding?.binding_kind !== "todo"
      || binding.binding_id !== identity.todo_id || fact.closeout_required !== true
      || (fact.settlement_effect_id !== null && !effectIdsMatch(fact.settlement_effect_id, identity.effect_id))
      || receiptDetails.interaction_mode !== "autonomous_replan" || receiptDetails.delivery_allowed !== false
      || receiptDetails.ok !== true || receiptDetails.should_run !== true
      || receiptDetails.must_attempt_work !== true || receiptDetails.quiet_noop_allowed !== false
      || nativeChildReportAdmission(receiptDetails, event.status, identity.effect_id, "open", false, selected)
        .report_permission !== "new_operation") return null;
  return selected;
}

export function heartbeatSemanticAdmissionEvent(events: readonly JsonObject[], receipt: JsonObject,
  identity: SettlementIdentity, allowLegacyPrepared = false): JsonObject | null {
  const selected = selectHeartbeatValidationEvent(events, receipt, identity, allowLegacyPrepared, true);
  return selected !== null && heartbeatSemanticWorkAdmission(selected, identity) !== null ? selected : null;
}

function selectHeartbeatValidationEvent(
  events: readonly JsonObject[], receipt: JsonObject, identity: SettlementIdentity,
  allowLegacyPrepared: boolean, semanticAdmission: boolean,
): JsonObject | null {
  const scoped = events.filter(event => event.event_kind === "quota_should_run"
    && event.goal_id === identity.goal_id && event.agent_id === identity.agent_id
    && event.run_id === identity.turn_instance_id
    && isDeepStrictEqual(event.goal_ref ?? null, receipt.goal_ref ?? null));
  const positions = new Map<unknown, number[]>();
  for (const [index, event] of scoped.entries()) {
    const indices = positions.get(event.event_id) ?? [];
    indices.push(index); positions.set(event.event_id, indices);
  }
  const start = positions.get(receipt.event_id);
  if (start?.length !== 1 || !isDeepStrictEqual(scoped[start[0]!], receipt)) return null;
  const initialFact = heartbeatReceiptFactFromEvent(receipt);
  const initialBinding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, initialFact);
  if (initialBinding !== null && (initialBinding.binding_kind !== identity.binding_kind
      || initialBinding.binding_id !== identity.binding_id
      || (initialFact.settlement_effect_id !== null
        && !effectIdsMatch(initialFact.settlement_effect_id, identity.effect_id)))) return null;
  const generations = new Set<string>();
  const positivePrepared = new Map<number, HeartbeatPreparedLease>();
  const semanticObligations = new Set<string>();
  for (const [index, event] of scoped.entries()) {
    const semantic = semanticAdmission ? heartbeatSemanticWorkAdmission(event, identity) : null;
    if (semantic !== null) semanticObligations.add(semantic);
    const prepared = heartbeatPreparedWorkAdmission(event, identity, allowLegacyPrepared);
    if (prepared === null) continue;
    positivePrepared.set(index, prepared);
    generations.add(JSON.stringify(prepared.prepared_lease_epoch === undefined
      ? ["legacy", prepared.prepared_lease_key_sha256, prepared.prepared_lease_version]
      : ["epoch", prepared.prepared_lease_key_sha256, prepared.prepared_lease_epoch]));
  }
  if (generations.size > 1) throw receiptIdentityConflict(
    "one Turn cannot contain positive prepared admissions from different lease generations");
  if (semanticObligations.size > 1) throw receiptIdentityConflict(
    "one Turn cannot replace its admitted semantic replan obligation");
  let position = start[0]!;
  let validation = scoped[position]!;
  let preparedAdmission: JsonObject | null = null;
  while (position >= 0) {
    const prepared = positivePrepared.get(position);
    const positive = semanticAdmission ? heartbeatSemanticWorkAdmission(validation, identity) !== null
      : prepared !== undefined;
    if (positive) preparedAdmission = validation;
    const validationDetails = heartbeatReceiptDetails(validation);
    const revision = validationDetails.settlement_receipt_revision;
    const legacyLineage = !Object.hasOwn(validationDetails, "settlement_receipt_revision")
      && positive && Object.hasOwn(validation, "causality");
    if (revision !== "lease_readiness" && revision !== "work_admission"
        && revision !== "identity_upgrade" && revision !== "review_guard_upgrade" && !legacyLineage) {
      if (!semanticAdmission && positive && !Object.hasOwn(validationDetails, "settlement_receipt_revision")
          && !Object.hasOwn(validation, "causality")) {
        let first: JsonObject | null = null;
        for (const [index, priorPrepared] of positivePrepared) {
          if (index >= position) break;
          if (!isDeepStrictEqual(priorPrepared, prepared)) throw receiptIdentityConflict(
            "an uncaused prepared receipt cannot redefine this Turn's original proof");
          first ??= scoped[index]!;
        }
        return first ?? validation;
      }
      if (semanticAdmission && positive && !Object.hasOwn(validationDetails, "settlement_receipt_revision")
          && !Object.hasOwn(validation, "causality")) {
        const originalGuard = projectSemanticReplanGuard(validationDetails);
        let first: JsonObject | null = null;
        for (const [index, prior] of scoped.entries()) {
          if (index >= position) break;
          if (heartbeatSemanticWorkAdmission(prior, identity) === null) continue;
          if (!isDeepStrictEqual(projectSemanticReplanGuard(heartbeatReceiptDetails(prior)), originalGuard)) {
            throw receiptIdentityConflict("an uncaused semantic receipt cannot redefine this Turn's original guard");
          }
          first ??= prior;
        }
        return first ?? validation;
      }
      return preparedAdmission ?? validation;
    }
    const lineage = jsonObject(validation.causality);
    const parentId = optionalHeartbeatString(lineage?.source_event_id);
    if (parentId === null || lineage?.caused_by !== parentId) return null;
    const parentPositions = positions.get(parentId);
    if (parentPositions?.length !== 1 || parentPositions[0]! >= position) return null;
    const parent = scoped[parentPositions[0]!]!;
    const fact = heartbeatReceiptFactFromEvent(parent);
    const parentDetails = heartbeatReceiptDetails(parent);
    if (legacyLineage) {
      const parentBinding = heartbeatReceiptBinding(identity.goal_id, identity.agent_id, fact);
      if ((parentBinding !== null && (parentBinding.binding_kind !== identity.binding_kind
          || parentBinding.binding_id !== identity.binding_id))
          || (fact.settlement_effect_id !== null && !effectIdsMatch(fact.settlement_effect_id, identity.effect_id))) return null;
      if (semanticAdmission ? heartbeatSemanticWorkAdmission(parent, identity) === null
        : heartbeatPreparedWorkAdmission(parent, identity, allowLegacyPrepared) === null) return preparedAdmission;
    }
    const pendingParent = revision === "identity_upgrade" && identity.binding_kind === "todo"
      && fact.todo_id === null && fact.replan_obligation_id === null && fact.request_subject == null
      && fact.settlement_effect_id === null && !fact.closeout_required
      && parentDetails.pending_action_selection_state === "deferred"
      && parentDetails.pending_action_selection_todo_id === identity.todo_id
      && normalizeHeartbeatTodoId(parentDetails.pending_action_selection_todo_id) === identity.todo_id;
    if ((!pendingParent && fact.todo_id !== identity.todo_id)
        || (fact.settlement_effect_id !== null && !effectIdsMatch(fact.settlement_effect_id, identity.effect_id))) return null;
    validation = parent;
    position = parentPositions[0]!;
  }
  return null;
}

/** Ephemeral qualification over freshly loaded canonical facts. This never
 * publishes a new prepared proof or grants a replacement generation work. */
interface HeartbeatPreparedGenerationInput {
  originalReceipt: JsonObject;
  identity: SettlementIdentity;
  requestedProof: JsonObject | null;
  todo: JsonObject;
  lease: JsonObject;
  handoffMode: unknown;
  exactSource: boolean;
}

/** Shared generation proof; phase and operation permission stay with callers. */
export function qualifyHeartbeatPreparedGeneration(input: HeartbeatPreparedGenerationInput): {
  original: HeartbeatPreparedLease; currentProof: JsonObject;
} {
  function reject(message: string): never {
    throw new EffectRuntimeRequestError(message, "refresh_task_lease_unavailable");
  }
  const original = heartbeatPreparedWorkAdmission(input.originalReceipt, input.identity, !input.exactSource);
  if (original === null || input.handoffMode !== "hard_lease") {
    reject("The original Turn's prepared work admission is unavailable.");
  }
  const {todo, lease, identity} = input;
  if (todo.todo_id !== identity.todo_id || lease.todo_id !== identity.todo_id
      || lease.owner !== identity.agent_id
      || (todo.claimed_by != null && todo.claimed_by !== identity.agent_id)
      || typeof lease.idempotency_key !== "string" || !lease.idempotency_key
      || lease.idempotency_key !== lease.idempotency_key.trim()
      || typeof lease.version !== "number" || !Number.isSafeInteger(lease.version) || lease.version < 1) {
    reject("The original Turn's current canonical lease proof is unavailable.");
  }
  const currentProof = {idempotency_key: lease.idempotency_key, expected_version: lease.version};
  if (input.requestedProof !== null && !isDeepStrictEqual(input.requestedProof, currentProof)) {
    reject("The supplied resume proof does not match the current canonical lease.");
  }
  if (createHash("sha256").update(lease.idempotency_key).digest("hex") !== original.prepared_lease_key_sha256) {
    reject("A replacement lease cannot execute this Turn's original work.");
  }
  if (original.prepared_lease_epoch === undefined) {
    if (input.exactSource || lease.version !== original.prepared_lease_version) {
      reject("This prepared receipt cannot prove a renewed lease generation.");
    }
  } else if (typeof lease.lease_epoch !== "number" || !Number.isSafeInteger(lease.lease_epoch)
      || lease.lease_epoch !== original.prepared_lease_epoch || lease.version < original.prepared_lease_version) {
    reject("The current lease does not preserve the original prepared generation.");
  }
  return {original, currentProof};
}

export function qualifyHeartbeatResumeLease(input: HeartbeatPreparedGenerationInput & {
  completionEvent: JsonObject | null;
  replayPhase: ReceiptBoundReplayPhase;
  closeoutStarted: boolean;
  writebackPending: boolean;
  otherTerminalStarted: boolean;
  terminalProviderEffect?: ProviderEffect | null;
  terminalCompletion?: JsonObject | null;
  observedAt: Date;
}): JsonObject {
  function reject(message: string): never {
    throw new EffectRuntimeRequestError(message, "refresh_task_lease_unavailable");
  }
  const {original, currentProof} = qualifyHeartbeatPreparedGeneration(input);
  const {todo, lease, identity} = input;
  const terminalPending = input.terminalProviderEffect?.step_kind === "terminal_closeout"
    && input.terminalProviderEffect.effect_ref === `${identity.effect_id}#terminal_closeout`
    && ["prepare_and_execute", "resolve_prepared", "execute_prepared"].includes(input.terminalProviderEffect.action);
  const active = todo.status === "open"
    && input.requestedProof !== null
    && ((input.replayPhase === "open" && !input.closeoutStarted) || terminalPending)
    && leaseIsActive(lease, input.observedAt);
  const completion = input.completionEvent;
  const retiredWriteback = input.writebackPending
    && !input.otherTerminalStarted && completion !== null
    && completion.event_kind === "todo_complete" && completion.goal_id === identity.goal_id
    && completion.agent_id === identity.agent_id && completion.run_id === identity.turn_instance_id
    && (completion.todo_id == null || completion.todo_id === identity.todo_id)
    && (heartbeatReceiptDetails(completion).todo_id == null
      || heartbeatReceiptDetails(completion).todo_id === identity.todo_id)
    && effectIdsMatch(optionalHeartbeatString(heartbeatReceiptDetails(completion).settlement_effect_id), identity.effect_id);
  const terminalCompletion = jsonObject(input.terminalCompletion?.completion);
  const retiredTerminal = terminalPending && input.terminalProviderEffect?.action === "resolve_prepared"
    && input.terminalCompletion?.kind === "committed" && terminalCompletion?.todo_id === identity.todo_id
    && terminalCompletion.continuation === "no_followup";
  const retired = todo.status === "done" && lease.status === "released" && (retiredWriteback || retiredTerminal);
  if (!active && !retired) {
    reject("The original Turn requires active execution or its exact retired completion proof.");
  }
  return {state: "ready", original_prepared: {...original},
    current_task_lease_proof: currentProof,
    lease_epoch: original.prepared_lease_epoch ?? null,
    source: active ? "active" : "retired"};
}

export function isHeartbeatLeaseReadiness(event: JsonObject): boolean {
  return heartbeatReceiptDetails(event).settlement_receipt_revision === "lease_readiness";
}

/** Reentry can qualify work on the original binding, never replace its authority.
 * The caller commits this append-only projection under the receipt-log lock. */
export function heartbeatWorkRequalification(
  original: JsonObject,
  currentDetails: JsonObject,
  identity: SettlementIdentity,
  phase: ReceiptBoundReplayPhase,
  closeoutStarted: boolean,
): JsonObject {
  const replay = {append: false, details: null};
  const candidate = heartbeatReceiptBinding(identity.goal_id, identity.agent_id,
    heartbeatReceiptFactFromEvent({...original, details: currentDetails}));
  if (candidate === null || candidate.binding_kind !== identity.binding_kind ||
      candidate.binding_id !== identity.binding_id ||
      candidate.settlement_effect_id !== identity.effect_id) {
    throw receiptIdentityConflict("work requalification must preserve the original settlement binding");
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
    throw receiptIdentityConflict("work requalification must preserve this Turn's original task lease generation");
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
