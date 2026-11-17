/** Qualify the previous Todo Turn before one explicit managed claim atomically
 * releases its overlapping lease and acquires the target execution. */
import {jsonObject} from "../runtime_decode.ts";
import {
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlementFromSnapshot,
  readQuotaSettlementSnapshot,
} from "../quota/settlement_readback.ts";
import {heartbeatReceiptFactFromEvent} from "../quota/heartbeat_receipt_identity.ts";
import {
  ClaimExecutionSwitchRejected,
  SETTLED_EXECUTION_SWITCH_PROOF_SCHEMA,
  type ClaimExecutionSwitchProof,
} from "./claim_execution.ts";

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** The newest Turn ever bound to the source Todo must be the settled Turn.
 * This prevents an older successful receipt from releasing work while a newer
 * source Turn is still open. Reentries of the same Turn do not change order. */
function latestBoundSourceTurn(
  events: readonly Record<string, unknown>[],
  goalId: string,
  agentId: string,
  todoId: string,
): string | null {
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const event of events) {
    if (event.event_kind !== "quota_should_run" || event.goal_id !== goalId ||
        event.agent_id !== agentId) continue;
    const fact = heartbeatReceiptFactFromEvent(event);
    const turn = nonEmpty(fact.run_id);
    if (fact.todo_id !== todoId || turn === null || seen.has(turn)) continue;
    seen.add(turn);
    ordered.push(turn);
  }
  return ordered.at(-1) ?? null;
}

export async function verifySettledExecutionSwitch(
  runtimeRoot: string,
  input: {
    readonly goal_id: string;
    readonly source_todo_id: string;
    readonly target_todo_id: string;
    readonly agent_id: string;
    readonly source_lease_idempotency_key: string;
    readonly source_lease_version: number;
    readonly source_lease_epoch: number;
  },
): Promise<ClaimExecutionSwitchProof> {
  if (input.source_todo_id === input.target_todo_id) {
    throw new ClaimExecutionSwitchRejected(
      "settled_execution_switch_same_todo",
      "Settled execution switch requires distinct source and target Todos",
    );
  }
  const snapshot = await readQuotaSettlementSnapshot(runtimeRoot, input.goal_id);
  const sourceTurn = latestBoundSourceTurn(
    snapshot.events,
    input.goal_id,
    input.agent_id,
    input.source_todo_id,
  );
  if (sourceTurn === null) {
    throw new ClaimExecutionSwitchRejected(
      "settled_execution_switch_source_turn_missing",
      "Settled execution switch requires a durable source Todo Turn binding",
    );
  }
  const readback = readQuotaSettlementFromSnapshot({
    schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: input.goal_id,
    agent_id: input.agent_id,
    todo_id: input.source_todo_id,
    turn_instance_id: sourceTurn,
    replan_obligation_id: null,
    infer_turn_instance_id: false,
    allow_unbound_binding: false,
  }, snapshot);
  const identityBundle = jsonObject(readback.identity);
  const identityResult = jsonObject(identityBundle?.result);
  const identity = jsonObject(identityResult?.value);
  const progress = jsonObject(readback.progress);
  const settlementOk = jsonObject(jsonObject(readback.settlement)?.payload)?.ok === true;
  if (readback.found !== true || !identity || !settlementOk ||
      progress?.schema_version !== "quota_settlement_progress_v0" ||
      progress.state !== "settled" || identity.goal_id !== input.goal_id ||
      identity.agent_id !== input.agent_id ||
      identity.todo_id !== input.source_todo_id ||
      identity.turn_instance_id !== sourceTurn ||
      identity.replan_obligation_id != null) {
    throw new ClaimExecutionSwitchRejected(
      "settled_execution_switch_unsettled_source",
      "Settled execution switch requires the latest source Todo Turn's complete writeback and quota receipts",
    );
  }
  return {
    schema_version: SETTLED_EXECUTION_SWITCH_PROOF_SCHEMA,
    goal_id: input.goal_id,
    source_todo_id: input.source_todo_id,
    target_todo_id: input.target_todo_id,
    agent_id: input.agent_id,
    source_lease_idempotency_key: input.source_lease_idempotency_key,
    source_lease_version: input.source_lease_version,
    source_lease_epoch: input.source_lease_epoch,
    settlement_identity: identity,
  };
}
