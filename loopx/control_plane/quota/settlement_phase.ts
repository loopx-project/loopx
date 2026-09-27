import type { SettlementIdentity } from "../effect_program.ts";
import { jsonObject } from "../runtime_decode.ts";

/** A typed blocked Turn may close without spend only with a bounded retry. */
export function isBoundedBlockedRetry(value: unknown, todoId: string | null): boolean {
  const retry = jsonObject(value);
  if (!retry || retry.schema_version !== "quota_blocked_retry_v0" ||
      (retry.source !== "todo" && retry.source !== "turn_settlement") ||
      typeof todoId !== "string" || retry.todo_id !== todoId ||
      typeof retry.observed_at !== "string" || typeof retry.due_at !== "string" ||
      retry.resume_when !== `resume_at:${retry.due_at}`) return false;
  const observed = Date.parse(retry.observed_at);
  const due = Date.parse(retry.due_at);
  const delay = (due - observed) / 1000;
  return Number.isFinite(delay) && delay >= 60 && delay <= 30 * 60;
}

/** The committed checkpoint accepts progress for a Turn, not Todo completion.
 * The caller must first verify this writeback's exact durable receipt. */
export function isAcceptedInFlightWriteback(
  value: unknown,
  identity: SettlementIdentity,
): boolean {
  const run = jsonObject(value);
  const checkpoint = jsonObject(run?.vision_checkpoint);
  if (identity.binding_kind !== "todo" || identity.todo_id === null ||
      !run || run.goal_id !== identity.goal_id ||
      run.agent_id !== identity.agent_id || run.todo_id !== identity.todo_id ||
      run.turn_instance_id !== identity.turn_instance_id ||
      run.delivery_outcome !== "outcome_progress" ||
      !checkpoint || checkpoint.schema_version !== "vision_checkpoint_v0" ||
      checkpoint.agent_id !== identity.agent_id ||
      checkpoint.delivery_boundary !== "in_flight_continuation" ||
      checkpoint.satisfied !== true || !Array.isArray(checkpoint.triggers)) {
    return false;
  }
  return checkpoint.triggers.some((value) => {
    const trigger = jsonObject(value);
    return trigger?.kind === "in_flight_continuation" &&
      trigger.todo_id === identity.todo_id;
  });
}

/** Both same-Turn readback and prior-Turn recovery accept the shipped effect identities. */
export function isCommittedMonitorPollEffect(
  effectId: unknown,
  identity: Pick<SettlementIdentity, "goal_id" | "agent_id" | "turn_instance_id" | "todo_id">,
): boolean {
  if (!identity.todo_id) return false;
  const base = `quota-monitor-poll:${identity.goal_id}:${identity.agent_id}:${identity.turn_instance_id}`;
  return effectId === base || effectId === `${base}:todo:${identity.todo_id}`;
}

export const RECEIPT_BOUND_MONITOR_PHASES = [
  "poll_due",
  "settlement_pending",
  "settled",
] as const;
export type ReceiptBoundMonitorPhase =
  (typeof RECEIPT_BOUND_MONITOR_PHASES)[number];

export interface ReceiptBoundMonitorSettlementState {
  poll_present: boolean;
  material_change: boolean;
  durable_writeback_present: boolean;
  quota_spend_present: boolean;
}

export function receiptBoundMonitorPhase(
  state: ReceiptBoundMonitorSettlementState,
): ReceiptBoundMonitorPhase {
  if (!state.poll_present) return "poll_due";
  // The committed monitor-poll is the durable no-spend closeout for this
  // monitor Turn. A material observation may atomically release an independent
  // successor, but it never upgrades the observe-only monitor into an
  // accountable delivery or quota-spend identity. Keep the extra inputs in the
  // cross-runtime request for compatibility with older callers; they no longer
  // decide this phase.
  return "settled";
}

export const RECEIPT_BOUND_REPLAY_PHASES = [
  "open",
  "settlement_pending",
  "settled",
] as const;
export type ReceiptBoundReplayPhase =
  (typeof RECEIPT_BOUND_REPLAY_PHASES)[number];

export interface ReceiptBoundReplaySettlementState {
  binding_kind?: "todo" | "autonomous_replan" | "unbound";
  /** A validated writeback discharges the binding without Todo completion. */
  writeback_completes_binding?: boolean;
  completion_receipt_present: boolean;
  durable_writeback_present: boolean;
  quota_spend_present: boolean;
  /** Exact typed blocked writeback closes a Turn without a quota debit. */
  no_spend_closeout_present?: boolean;
}

export function receiptBoundReplayPhase(
  state: ReceiptBoundReplaySettlementState,
): ReceiptBoundReplayPhase {
  const bindingComplete = state.binding_kind === "autonomous_replan" ||
      state.writeback_completes_binding === true
    ? state.durable_writeback_present
    : state.completion_receipt_present;
  if (!bindingComplete) return "open";
  return state.durable_writeback_present &&
      (state.quota_spend_present || state.no_spend_closeout_present === true)
    ? "settled"
    : "settlement_pending";
}

// Compatibility aliases for callers that predate ordinary-completion replay.
// New quota code must use the replay contract so a Todo successor cannot make
// the original turn look open after its settlement chain has committed.
export const RECEIPT_BOUND_TERMINAL_PHASES = RECEIPT_BOUND_REPLAY_PHASES;
export type ReceiptBoundTerminalPhase = ReceiptBoundReplayPhase;

export interface ReceiptBoundTerminalSettlementState {
  terminal_closeout_present: boolean;
  durable_writeback_present: boolean;
  quota_spend_present: boolean;
}

export function receiptBoundTerminalPhase(
  state: ReceiptBoundTerminalSettlementState,
): ReceiptBoundTerminalPhase {
  return receiptBoundReplayPhase({
    completion_receipt_present: state.terminal_closeout_present,
    durable_writeback_present: state.durable_writeback_present,
    quota_spend_present: state.quota_spend_present,
  });
}
