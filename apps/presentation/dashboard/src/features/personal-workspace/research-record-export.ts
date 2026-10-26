import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {searchResearchRecords, type ResearchRecordDisplay} from "./research-record-search.ts";

/** A portable reading snapshot, not an import format or an acceptance receipt.
 * Export canonical records, never translated/analysed display replacements.
 * Source endpoints are deliberately absent: opening a file needs no connection.
 */
export function exportResearchRecords({goalId, events, query, status, complete, stale, exportedAt, display}: {
  goalId: string; events: readonly ExploreHistoryEvent[]; query: string; status: string;
  complete: boolean; stale: boolean; exportedAt: string; display?: ResearchRecordDisplay;
}): string {
  if (!goalId || events.some(event => event.goal_id !== goalId)) throw new Error("Research export Goal mismatch");
  const records = searchResearchRecords(events, query, status, display);
  return JSON.stringify({
    goal_id: goalId,
    exported_at: exportedAt,
    filters: {query, status},
    coverage: {
      loaded_records: events.length,
      exported_records: records.length,
      history_complete: complete,
      refresh_failed: stale,
    },
    records,
  }, null, 2) + "\n";
}
