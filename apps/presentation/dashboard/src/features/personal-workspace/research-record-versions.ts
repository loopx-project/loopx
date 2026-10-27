import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";

/** Recorded versions of one result, in source order. Dates and supersedes text
 * do not establish lineage across result kinds or Goals. Never use search hits
 * as the source: a filtered-out revision remains available for comparison. */
export function researchRecordVersions(events: readonly ExploreHistoryEvent[], selected: ExploreHistoryEvent) {
  return events.filter(event => event.goal_id === selected.goal_id
    && event.event_kind === selected.event_kind && event.result_id === selected.result_id);
}

// Local display fields, not a new evidence or lifecycle vocabulary.
export const researchComparisonFields = ["title", "summary", "status", "blocked_reason", "evidence_refs",
  "measurements", "confidence", "run_id", "agent_id", "node_kind", "node_id", "parent_id",
  "from_node", "to_node", "edge_type", "supersedes", "execution_observation"] as const;
export type ResearchComparisonField = typeof researchComparisonFields[number];

const normalized = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(normalized);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, normalized(item)]));
  return value;
};
export function changedResearchFields(left: ExploreHistoryEvent, right: ExploreHistoryEvent) {
  return researchComparisonFields.filter(field => JSON.stringify(normalized(left[field])) !== JSON.stringify(normalized(right[field])));
}
