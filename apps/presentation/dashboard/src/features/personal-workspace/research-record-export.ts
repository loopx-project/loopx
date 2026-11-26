import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {searchResearchRecords, type ResearchRecordDisplay, type ResearchDateRange} from "./research-record-search.ts";

/** A portable reading snapshot.
 * Export canonical records, never translated/analysed display replacements.
 * Source endpoints are deliberately absent: opening a file needs no connection.
 */
export type ResearchRecordExport = ResearchDateRange & {
  goalId: string; events: readonly ExploreHistoryEvent[]; query: string; status: string;
  complete: boolean; stale: boolean; exportedAt: string; display?: ResearchRecordDisplay;
};

export function researchRecordSnapshot({goalId, events, query, status, complete, stale, exportedAt, display, from, to}: ResearchRecordExport) {
  if (!goalId || events.some(event => event.goal_id !== goalId)) throw new Error("Research export Goal mismatch");
  const records = searchResearchRecords(events, query, status, display, {from, to});
  return {
    goal_id: goalId,
    exported_at: exportedAt,
    filters: {query, status, ...(from ? {from} : {}), ...(to ? {to} : {})},
    coverage: {
      loaded_records: events.length,
      exported_records: records.length,
      history_complete: complete,
      refresh_failed: stale,
    },
    records,
  };
}

export function exportResearchRecords(input: ResearchRecordExport): string {
  return JSON.stringify(researchRecordSnapshot(input), null, 2) + "\n";
}
