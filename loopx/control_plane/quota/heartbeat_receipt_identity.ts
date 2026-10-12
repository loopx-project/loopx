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
import {settlementIdentity, type SettlementIdentity} from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject } from "../runtime_decode.ts";
import {nativeChildReportAdmission} from "../capabilities/native_child_admission.ts";
import type {ReceiptBoundReplayPhase} from "./settlement_phase.ts";

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
  binding_kind: "todo" | "autonomous_replan";
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
    settlement_effect_id: optionalHeartbeatString(
      eventDetails.settlement_effect_id,
    ),
    closeout_required: eventDetails.closeout_required === true,
  };
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
  if (declaredTodoId && replanObligationId) {
    throw receiptIdentityConflict(
      "heartbeat receipt has conflicting Todo and autonomous replan bindings",
    );
  }
  if (declaredEffectId && !declaredTodoId && !replanObligationId) {
    throw receiptIdentityConflict(
      "heartbeat receipt has an effect identity without a Todo or autonomous replan binding; refuse to infer or upgrade it",
    );
  }
  if (!declaredTodoId && !replanObligationId) return null;
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
    replan_obligation_id: replanObligationId,
  });
  return {
    binding_kind: identity.binding_kind === "todo"
      ? "todo"
      : "autonomous_replan",
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
  // No legacy/status-label compatibility for new qualifications: all modern
  // work facts must be explicit. This reuses the report-admission predicate.
  if (nativeChildReportAdmission(currentDetails, null, identity.effect_id, phase, false)
      .report_permission !== "new_operation") return replay;
  const oldDetails = heartbeatReceiptDetails(original);
  if (nativeChildReportAdmission(oldDetails, original.status, identity.effect_id, phase, false)
      .report_permission === "new_operation") return replay;
  const originalReplan = normalizeHeartbeatReplanObligationId(oldDetails.semantic_replan_obligation_id);
  const currentReplan = normalizeHeartbeatReplanObligationId(currentDetails.semantic_replan_obligation_id);
  if (currentReplan !== null && currentReplan !== originalReplan) {
    throw receiptIdentityConflict("work requalification cannot replace the original semantic replan guard");
  }
  const qualified = {...oldDetails};
  for (const field of ["ok", "should_run", "must_attempt_work", "delivery_allowed", "quiet_noop_allowed"]) {
    qualified[field] = currentDetails[field]!;
  }
  // Preserve workspace causality, semantic guards, pending selection and all
  // original identities. Work admission makes this same Todo owe closeout.
  qualified.closeout_required = identity.todo_id !== null;
  qualified.settlement_receipt_revision = "work_admission";
  return {append: true, details: qualified};
}
