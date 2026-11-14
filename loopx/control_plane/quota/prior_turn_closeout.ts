/**
 * Which prior host Turn still owes its closeout.
 *
 * Two readers ask that question over one settlement snapshot: the guard's
 * prior-Turn closeout preflight, which turns the answer into a recovery
 * obligation, and the settlement readback, which has to name the owed Turn when
 * the current Turn's guard could not bind because of it. Candidate selection
 * and the missing-receipt rule live here so both readers name the same Turn.
 */
import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { jsonObject } from "../runtime_decode.ts";
import {
  heartbeatReceiptBinding,
  heartbeatReceiptDetails,
  heartbeatReceiptFactFromEvent,
  normalizeHeartbeatReplanObligationId,
  selectEffectiveHeartbeatReceipt,
  type HeartbeatReceiptBinding,
  type HeartbeatReceiptFact,
} from "./heartbeat_receipt_identity.ts";

export const WRITEBACK_RECEIPT = "durable_writeback_receipt";
export const SPEND_RECEIPT = "quota_spend_receipt";
export const MISSING_RECEIPT_NAMES = [WRITEBACK_RECEIPT, SPEND_RECEIPT] as const;

export interface PriorHostTurnCloseoutCandidate extends JsonObject {
  prior_turn_instance_id: string;
  event_id: string | null;
  binding_kind: HeartbeatReceiptBinding["binding_kind"];
  /** The Todo id or autonomous replan obligation id, per `binding_kind`. */
  binding_id: string;
  settlement_effect_id: string | null;
  /** The effective guard admitted a replan under this Todo's settlement identity. */
  admitted_todo_replan?: true;
}

interface CloseoutCandidateCheck {
  candidate: PriorHostTurnCloseoutCandidate;
  requireWritebackForRecovery: boolean;
}

function candidate(
  binding: HeartbeatReceiptBinding,
  turnInstanceId: string,
  fact: HeartbeatReceiptFact,
  admittedTodoReplan: boolean,
): PriorHostTurnCloseoutCandidate {
  return {
    prior_turn_instance_id: turnInstanceId,
    event_id: fact.event_id,
    binding_kind: binding.binding_kind,
    binding_id: binding.binding_id,
    settlement_effect_id: binding.settlement_effect_id,
    ...(admittedTodoReplan ? {admitted_todo_replan: true as const} : {}),
  };
}

function admitsAutonomousReplan(event: JsonObject, fact: HeartbeatReceiptFact): boolean {
  const guard = heartbeatReceiptDetails(event);
  const obligationId = normalizeHeartbeatReplanObligationId(guard.semantic_replan_obligation_id);
  return fact.settlement_effect_id !== null && obligationId !== null
    && (fact.todo_id !== null || obligationId === fact.replan_obligation_id)
    && guard.interaction_mode === "autonomous_replan" && guard.ok === true && guard.should_run === true
    && guard.must_attempt_work === true && guard.delivery_allowed === false && guard.quiet_noop_allowed === false;
}

/**
 * Select prior Turns eligible for closeout readback. A Todo-bound replan
 * carries the Todo settlement identity and a separate semantic obligation.
 * It did not deliver a Todo, so its closeout marker can be false. Fresh
 * admission requires a verified outstanding writeback for this binding.
 * Explicit pending-closeout discovery can also find admitted replans before
 * writeback, whether their settlement identity is a Todo or an obligation.
 *
 * Every prior Turn is identity-validated, so a conflicting receipt fails the
 * read instead of being hidden behind a newer Turn. Results are newest first
 * and carry at most one effective receipt per Turn, which is the same single
 * binding the caller's recovery decision may act on.
 *
 * On reentry, only Turns first recorded before the current Turn may be its
 * prerequisites. Replays can change recency and upgrade an unbound receipt,
 * but cannot make a later Turn a prerequisite of an earlier one. Turn ids and
 * wall clocks are not an ordering authority.
 */
export function selectCloseoutCandidates(
  receipts: readonly JsonObject[],
  goalId: string,
  agentId: string,
  excludeTurnInstanceId: string | null,
  { includeUnwrittenReplans } = { includeUnwrittenReplans: false },
): { candidates: CloseoutCandidateCheck[]; turnsValidated: number } {
  const perTurn = new Map<string, { fact: HeartbeatReceiptFact; value: JsonObject }[]>();
  const firstRecorded = new Map<string, number>();
  for (const event of receipts) {
    const fact = heartbeatReceiptFactFromEvent(event);
    const turnInstanceId = fact.run_id;
    if (!turnInstanceId) continue;
    if (!firstRecorded.has(turnInstanceId)) {
      firstRecorded.set(turnInstanceId, firstRecorded.size);
    }
    if (turnInstanceId === excludeTurnInstanceId) continue;
    const entry = { fact, value: event };
    const existing = perTurn.get(turnInstanceId);
    if (existing) {
      existing.push(entry);
      // Map insertion order is the append-order recency index. Moving an
      // existing Turn to the tail avoids an O(T) indexOf/splice for every
      // receipt while preserving "last receipt wins recency" exactly.
      perTurn.delete(turnInstanceId);
      perTurn.set(turnInstanceId, existing);
    } else {
      perTurn.set(turnInstanceId, [entry]);
    }
  }

  const candidates: CloseoutCandidateCheck[] = [];
  const currentPosition = excludeTurnInstanceId === null
    ? undefined : firstRecorded.get(excludeTurnInstanceId);
  let validated = 0;
  for (const turnInstanceId of [...perTurn.keys()].reverse()) {
    const effective = selectEffectiveHeartbeatReceipt(goalId, agentId, perTurn.get(turnInstanceId)!);
    validated += 1;
    if (effective === null) continue;
    // Validate identities even outside the causal candidate set: filtering
    // must not conceal a conflicting persisted settlement binding.
    if (currentPosition !== undefined && firstRecorded.get(turnInstanceId)! >= currentPosition) continue;
    const fact = heartbeatReceiptFactFromEvent(effective);
    const binding = heartbeatReceiptBinding(goalId, agentId, fact);
    if (binding === null) continue;
    const admittedReplan = admitsAutonomousReplan(effective, fact);
    const admittedTodoReplan = admittedReplan && binding.binding_kind === "todo";
    if (!fact.closeout_required && !admittedTodoReplan &&
        !(includeUnwrittenReplans && admittedReplan)) continue;
    candidates.push({candidate: candidate(binding, turnInstanceId, fact, admittedTodoReplan),
      requireWritebackForRecovery: !fact.closeout_required && !includeUnwrittenReplans});
  }
  return {candidates, turnsValidated: validated};
}

/** The exact settlement readback request for one candidate's own binding. */
export function candidateReadbackBinding(
  selected: PriorHostTurnCloseoutCandidate,
): { todo_id: string | null; replan_obligation_id: string | null; turn_instance_id: string } {
  return {
    todo_id: selected.binding_kind === "todo" ? selected.binding_id : null,
    turn_instance_id: selected.prior_turn_instance_id,
    replan_obligation_id: selected.binding_kind === "autonomous_replan"
      ? selected.binding_id
      : null,
  };
}

function bundleFailed(readback: JsonObject, step: string): boolean {
  const bundle = jsonObject(readback[step]);
  const payload = bundle === null ? null : jsonObject(bundle.payload);
  if (payload === null || typeof payload.ok !== "boolean") {
    throw new EffectRuntimeRequestError(
      `quota settlement readback has no ${step} result`,
      "malformed_settlement_readback",
    );
  }
  return payload.ok !== true;
}

/**
 * The closeout receipts a candidate's settlement readback still lacks, or
 * null when the Turn's settlement validates, or undefined when a candidate
 * without a closeout marker has no verified writeback.
 */
export function missingCloseoutReceipts(
  readback: JsonObject, requireWriteback: boolean,
): string[] | null | undefined {
  if (readback.found === true && !bundleFailed(readback, "settlement")) {
    return null;
  }
  const missingReceipts: string[] = [];
  if (readback.found !== true || bundleFailed(readback, "writeback")) {
    if (requireWriteback) return undefined;
    missingReceipts.push(WRITEBACK_RECEIPT);
  }
  if (readback.found !== true || bundleFailed(readback, "spend")) {
    missingReceipts.push(SPEND_RECEIPT);
  }
  return missingReceipts;
}

/** Only an exact writeback can leave the original debit as the sole debt. */
export function awaitingQuotaSpend(missing: readonly string[]): boolean {
  return missing.length === 1 && missing[0] === SPEND_RECEIPT;
}
