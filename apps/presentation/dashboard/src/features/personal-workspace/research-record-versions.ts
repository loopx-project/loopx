import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";

/** Recorded versions of one result, in source order. Dates and supersedes text
 * do not establish lineage across result kinds or Goals. Never use search hits
 * as the source: a filtered-out revision remains available for comparison. */
export function researchRecordVersions(events: readonly ExploreHistoryEvent[], selected: ExploreHistoryEvent) {
  return events.filter(event => event.goal_id === selected.goal_id
    && event.event_kind === selected.event_kind && event.result_id === selected.result_id);
}

// Local display fields.
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

export const researchComparisonLabels: Record<ResearchComparisonField, [string, string]> = {
  title: ["Title", "标题"], summary: ["Original text", "原文"], status: ["Recorded status", "记录状态"],
  blocked_reason: ["Blocked reason", "受阻原因"], evidence_refs: ["Evidence references", "证据引用"],
  measurements: ["Measurements", "测量"], confidence: ["Recorded confidence", "记录置信度"],
  run_id: ["Run ID", "运行 ID"], agent_id: ["Agent ID", "Agent ID"], node_kind: ["Node kind", "节点类型"],
  node_id: ["Node ID", "问题 ID"], parent_id: ["Source group", "来源分组"],
  from_node: ["Link source", "关联起点"], to_node: ["Link target", "关联终点"], edge_type: ["Link type", "关联类型"],
  supersedes: ["Supersedes", "取代记录"], execution_observation: ["Execution observation", "执行观察"],
};
