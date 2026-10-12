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
  heartbeatReceiptFactFromEvent,
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
}

function candidate(
  binding: HeartbeatReceiptBinding,
  turnInstanceId: string,
  fact: HeartbeatReceiptFact,
): PriorHostTurnCloseoutCandidate {
  return {
    prior_turn_instance_id: turnInstanceId,
    event_id: fact.event_id,
    binding_kind: binding.binding_kind,
    binding_id: binding.binding_id,
    settlement_effect_id: binding.settlement_effect_id,
  };
}

/**
 * Select the prior must-attempt Turns that still require a host closeout.
 *
 * Every prior Turn is identity-validated, so a conflicting receipt fails the
 * read instead of being hidden behind a newer Turn. Results are newest first
 * and carry at most one effective receipt per Turn, which is the same single
 * binding the caller's recovery decision may act on.
 */
export function selectCloseoutCandidates(
  receipts: readonly JsonObject[],
  goalId: string,
  agentId: string,
  excludeTurnInstanceId: string | null,
): { candidates: PriorHostTurnCloseoutCandidate[]; turnsValidated: number } {
  const perTurn = new Map<string, HeartbeatReceiptFact[]>();
  for (const event of receipts) {
    const fact = heartbeatReceiptFactFromEvent(event);
    const turnInstanceId = fact.run_id;
    if (!turnInstanceId || turnInstanceId === excludeTurnInstanceId) {
      continue;
    }
    const existing = perTurn.get(turnInstanceId);
    if (existing) {
      existing.push(fact);
      // Map insertion order is the append-order recency index. Moving an
      // existing Turn to the tail avoids an O(T) indexOf/splice for every
      // receipt while preserving "last receipt wins recency" exactly.
      perTurn.delete(turnInstanceId);
      perTurn.set(turnInstanceId, existing);
    } else {
      perTurn.set(turnInstanceId, [fact]);
    }
  }

  const candidates: PriorHostTurnCloseoutCandidate[] = [];
  let validated = 0;
  for (const turnInstanceId of [...perTurn.keys()].reverse()) {
    const entries = (perTurn.get(turnInstanceId) ?? []).map((fact) => ({
      fact,
      value: fact,
    }));
    const effective = selectEffectiveHeartbeatReceipt(goalId, agentId, entries);
    validated += 1;
    if (effective === null) continue;
    // Closeout is opt-in on the persisted receipt, so older receipts from
    // before this contract cannot become new recovery obligations.
    if (!effective.closeout_required) continue;
    const binding = heartbeatReceiptBinding(goalId, agentId, effective);
    if (binding === null) continue;
    candidates.push(candidate(binding, turnInstanceId, effective));
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
 * null when that readback already validates the Turn's settlement.
 */
export function missingCloseoutReceipts(readback: JsonObject): string[] | null {
  if (readback.found === true && !bundleFailed(readback, "settlement")) {
    return null;
  }
  const missingReceipts: string[] = [];
  if (readback.found !== true || bundleFailed(readback, "writeback")) {
    missingReceipts.push(WRITEBACK_RECEIPT);
  }
  if (readback.found !== true || bundleFailed(readback, "spend")) {
    missingReceipts.push(SPEND_RECEIPT);
  }
  return missingReceipts;
}
