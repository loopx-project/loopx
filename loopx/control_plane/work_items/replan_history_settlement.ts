/** Replan history IO: qualify work through the existing settlement owner. */
import type { JsonObject } from "../effect_program.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import {
  readQuotaSettlementSnapshot,
  readQuotaSettlementForAdmittedOwnerFromSnapshot,
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  validateQuotaSettlementScope,
} from "../quota/settlement_readback.ts";
import { readGoalRolloutEventSnapshot, strictGoalRolloutEvents } from "../rollout_receipt_log.ts";
import { parseQuotaAccountingOwner, withQuotaAccountingOwner,
  withBorrowedQuotaAccountingOwner, quotaOwnerOwnsProjection } from "../quota/source_admission.ts";
import { projectReplanHistory } from "./replan_history.ts";

export async function projectSettledReplanHistory(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "replan history");
  const source = request.settlement_source;
  // Historical callers retain their units until explicitly migrated.
  if (source == null) return projectReplanHistory(request);
  const scope = requireJsonObject(source, "settlement source");
  const {runtimeRoot, goalId} = validateQuotaSettlementScope(
    scope.runtime_root,
    scope.goal_id,
  );
  const owner = parseQuotaAccountingOwner({runtimeRoot, goalId,
    goalRefValue: scope.goal_ref, sourceAdmissionValue: scope.source_admission});
  const withOwner = scope.borrow_source_admission === true
    ? withBorrowedQuotaAccountingOwner : withQuotaAccountingOwner;
  return await withOwner(owner, async () => {
    // Scope before ACKs too: a retired instance cannot reset the current lane.
    const runs = (Array.isArray(request.runs) ? request.runs : []).filter(
      raw => quotaOwnerOwnsProjection(owner, jsonObject(raw)?.goal_ref));
    const rolloutSnapshot = await readGoalRolloutEventSnapshot(runtimeRoot, goalId);
    const events = strictGoalRolloutEvents(rolloutSnapshot);
    const receiptKeys = new Set(events.filter(event =>
      event.event_kind === "quota_should_run" && event.goal_id === goalId &&
      typeof event.agent_id === "string" && typeof event.run_id === "string" &&
      quotaOwnerOwnsProjection(owner, event.goal_ref)
    ).map(event => JSON.stringify([event.agent_id, event.run_id])));
    const settlementRuns = runs.filter(raw => {
      const row = jsonObject(raw);
      return row !== null && typeof row.agent_id === "string" && row.agent_id !== "" &&
        typeof row.turn_id === "string" && row.turn_id !== "" &&
        receiptKeys.has(JSON.stringify([row.agent_id, row.turn_id]));
    });
    // History rows without a matching should-run receipt cannot be qualified
    // by settlement. Avoid parsing the strict quota run ledger unless at least
    // one exact current-owner Turn can contribute to effective-cadence counts.
    if (settlementRuns.length === 0) {
      return projectReplanHistory({...request, runs}, new Set());
    }
    const snapshot = await readQuotaSettlementSnapshot(runtimeRoot, goalId, rolloutSnapshot);
    const qualified = new Set<string>();
    const seen = new Set<string>();
    for (const raw of settlementRuns) {
      const row = jsonObject(raw);
      if (!row) continue;
      const key = JSON.stringify([row.agent_id, row.turn_id]);
      if (seen.has(key)) continue;
      seen.add(key);
      const settlement = readQuotaSettlementForAdmittedOwnerFromSnapshot({
        schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
        runtime_root: runtimeRoot, goal_id: goalId, agent_id: row.agent_id,
        turn_instance_id: row.turn_id, resolve_original_binding: true,
        infer_turn_instance_id: false, allow_unbound_binding: false,
      }, owner, snapshot);
      // A debit, a poll, or an uncommitted attempt is insufficient. Accepted
      // negative work is not excluded merely because it produced no improvement.
      if (jsonObject(settlement.progress)?.state === "settled" &&
          settlement.replay_phase === "settled" && settlement.writeback_run != null &&
          settlement.monitor_phase !== "settled") qualified.add(key);
    }
    return projectReplanHistory({...request, runs}, qualified);
  });
}
