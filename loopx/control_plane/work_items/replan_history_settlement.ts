/** Replan history IO: qualify work through the existing settlement owner. */
import type { JsonObject } from "../effect_program.ts";
import { jsonObject, requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { readQuotaSettlementSnapshot, readQuotaSettlementForAdmittedOwnerFromSnapshot,
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA } from "../quota/settlement_readback.ts";
import { parseQuotaAccountingOwner, withQuotaAccountingOwner,
  withBorrowedQuotaAccountingOwner, quotaOwnerOwnsProjection } from "../quota/source_admission.ts";
import { projectReplanHistory } from "./replan_history.ts";

export async function projectSettledReplanHistory(value: unknown): Promise<JsonObject> {
  const request = requireJsonObject(value, "replan history");
  const source = request.settlement_source;
  // Historical callers retain their units until explicitly migrated.
  if (source == null) return projectReplanHistory(request);
  const scope = requireJsonObject(source, "settlement source");
  const runtimeRoot = requireNonEmptyString(scope.runtime_root, "runtime_root");
  const goalId = requireNonEmptyString(scope.goal_id, "goal_id");
  const owner = parseQuotaAccountingOwner({runtimeRoot, goalId,
    goalRefValue: scope.goal_ref, sourceAdmissionValue: scope.source_admission});
  const withOwner = scope.borrow_source_admission === true
    ? withBorrowedQuotaAccountingOwner : withQuotaAccountingOwner;
  return await withOwner(owner, async () => {
    const snapshot = await readQuotaSettlementSnapshot(runtimeRoot, goalId);
    // Scope before ACKs too: a retired instance cannot reset the current lane.
    const runs = (Array.isArray(request.runs) ? request.runs : []).filter(
      raw => quotaOwnerOwnsProjection(owner, jsonObject(raw)?.goal_ref));
    const qualified = new Set<string>();
    const seen = new Set<string>();
    for (const raw of runs) {
      const row = jsonObject(raw);
      if (!row || typeof row.agent_id !== "string" || !row.agent_id ||
          typeof row.turn_id !== "string" || !row.turn_id) continue;
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
