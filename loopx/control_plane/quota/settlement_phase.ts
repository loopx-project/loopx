import type { SettlementIdentity } from "../effect_program.ts";
import { jsonObject } from "../runtime_decode.ts";
import { isCausalBlockedWait } from "./blocked_wait.ts";

/** A blocked Turn needs a bounded retry or a verified canonical causal wait. */
export function isBoundedBlockedRetry(value: unknown, todoId: string | null): boolean {
  if (isCausalBlockedWait(value, todoId)) return true;
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

/** A service-qualified capability duty can retire its exact admitted Turn.
 * The caller first verifies the durable writeback receipt. No Task/Goal
 * completion, progress or debit follows from this lifecycle evidence. */
export function isCapabilityRetirementWriteback(value: unknown, identity: SettlementIdentity, selectedGuard: unknown): boolean {
  const run = jsonObject(value), guard = jsonObject(selectedGuard);
  const ack = jsonObject(run?.autonomous_replan_ack), delta = jsonObject(ack?.semantic_delta);
  const retirement = jsonObject(delta?.retirement), original = jsonObject(retirement?.original_guard);
  const deltaGuard = jsonObject(delta?.capability_guard), progress = jsonObject(run?.progress_observation);
  const sameGuard = (candidate: Record<string, unknown> | null) => !!guard && !!candidate
    && candidate.schema_version === "semantic_replan_capability_guard_v0"
    && candidate.capability_id === guard.capability_id && candidate.gap_id === guard.gap_id
    && candidate.frontier_revision === guard.frontier_revision;
  return identity.binding_kind === "autonomous_replan" && identity.replan_obligation_id !== null
    && !!run && run.goal_id === identity.goal_id && run.agent_id === identity.agent_id
    && run.turn_instance_id === identity.turn_instance_id && run.replan_obligation_id === identity.replan_obligation_id
    && run.delivery_outcome === "outcome_gap" && ack?.recorded === true
    && delta?.schema_version === "replan_semantic_delta_v0" && delta.accepted === true
    && delta.obligation_id === identity.replan_obligation_id
    && JSON.stringify(delta.outcomes) === '["capability_duty_retired"]'
    && JSON.stringify(delta.satisfying_outcomes) === '["capability_duty_retired"]'
    && retirement?.schema_version === "capability_obligation_retirement_v0" && retirement.disposition === "invalidated"
    && retirement.obligation_id === identity.replan_obligation_id && retirement.capability_id === guard?.capability_id
    && ["source_disabled", "source_ineligible", "source_revision_changed"].includes(String(retirement.reason_code))
    && retirement.blocking_todo_count === 0 && Array.isArray(retirement.blocking_todo_ids) && retirement.blocking_todo_ids.length === 0
    && typeof retirement.current_revision === "string" && retirement.current_revision.length > 0
    && sameGuard(original) && sameGuard(deltaGuard)
    && progress?.schema_version === "typed_progress_observation_v0" && progress.result_class === "blocked"
    && progress.work_item_id === identity.replan_obligation_id
    && typeof progress.blocker_id === "string" && progress.blocker_id.length > 0
    && typeof progress.fingerprint === "string" && progress.fingerprint.length > 0
    && retirement.progress_fingerprint === progress.fingerprint
    && jsonObject(retirement.progress_observation)?.blocker_id === progress.blocker_id
    && jsonObject(retirement.progress_observation)?.work_item_id === identity.replan_obligation_id
    && jsonObject(retirement.progress_observation)?.result_class === "blocked"
    && jsonObject(retirement.progress_observation)?.schema_version === "typed_progress_observation_v0"
    && JSON.stringify(jsonObject(retirement.progress_observation)?.evidence_ids) === JSON.stringify(progress.evidence_ids)
    && JSON.stringify(progress.evidence_ids) === JSON.stringify([retirement.current_revision]);
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
  /** Retirement closes its original Turn, not the replacement or Goal. */
  supersede_receipt_present?: boolean;
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
    : state.completion_receipt_present ||
      (state.binding_kind === "todo" && state.supersede_receipt_present === true);
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
