import type { SettlementIdentity } from "../effect_program.ts";

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
  completion_receipt_present: boolean;
  durable_writeback_present: boolean;
  quota_spend_present: boolean;
}

export function receiptBoundReplayPhase(
  state: ReceiptBoundReplaySettlementState,
): ReceiptBoundReplayPhase {
  const bindingComplete = state.binding_kind === "autonomous_replan"
    ? state.durable_writeback_present
    : state.completion_receipt_present;
  if (!bindingComplete) return "open";
  return state.durable_writeback_present && state.quota_spend_present
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
