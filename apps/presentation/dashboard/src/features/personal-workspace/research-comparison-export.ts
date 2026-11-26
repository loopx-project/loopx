import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {changedResearchFields, researchComparisonLabels, researchRecordVersions} from "./research-record-versions.ts";
import {researchReportInline, researchReportLiteral} from "./research-record-report.ts";

type ComparisonExport = {
  events: readonly ExploreHistoryEvent[]; selectedId: string; comparisonId: string;
  complete: boolean; stale: boolean; exportedAt: string;
};

/** Resolve both identities from the retained canonical snapshot, never a display
 * translation or the filtered search results. No source references are fetched. */
export function researchComparisonSnapshot(input: ComparisonExport) {
  const unique = (id: string) => {
    const matches = input.events.filter(event => event.event_id === id);
    if (matches.length !== 1) throw new Error("Comparison record is unavailable or ambiguous");
    return matches[0]!;
  };
  const selected = unique(input.selectedId), comparison = unique(input.comparisonId);
  if (input.events.some(event => event.goal_id !== selected.goal_id)) throw new Error("Research export Goal mismatch");
  if (selected.event_id === comparison.event_id
    || !researchRecordVersions(input.events, selected).includes(comparison)) {
    throw new Error("Comparison records must be distinct versions of the same result");
  }
  return {
    goal_id: selected.goal_id, exported_at: input.exportedAt,
    coverage: {loaded_records: input.events.length, exported_records: 2,
      history_complete: input.complete, refresh_failed: input.stale},
    selected_record: selected, comparison_record: comparison,
    changed_fields: changedResearchFields(selected, comparison),
  };
}

export function exportResearchComparison(input: ComparisonExport, format: "json" | "md", zh: boolean): string {
  const snapshot = researchComparisonSnapshot(input);
  if (format === "json") return JSON.stringify(snapshot, null, 2) + "\n";
  const label = (en: string, cn: string) => zh ? cn : en;
  const field = (name: string, value: string | number) => `- **${name}:** ${researchReportInline(String(value))}`;
  const lines = [
    `# ${label("Research record comparison", "研究记录版本对照")}`,
    label("Compares original records, not matched evaluations or changes in research quality.", "比较原始记录，不代表同条件评测或研究质量变化。"),
    field("Goal", snapshot.goal_id), field(label("Exported at", "导出时间"), snapshot.exported_at),
    field(label("Exported / loaded records", "导出 / 已读记录"), `2 / ${snapshot.coverage.loaded_records}`),
    field(label("Selected record", "所选记录"), snapshot.selected_record.event_id),
    field(label("Comparison record", "对照记录"), snapshot.comparison_record.event_id),
    field(label("History", "历史"), input.complete ? label("Complete loaded history", "已完整载入历史") : label("Incomplete; unloaded records are not included", "不完整；不包含尚未读取的记录")),
    field(label("Refresh", "刷新"), input.stale ? label("Failed; previously read records retained", "失败；保留先前读取的记录") : label("No refresh failure reported", "未报告刷新失败")),
    label("This file contains the selected pair regardless of search filters. References are identifiers, not downloaded source files. Keep it as private as the source research.", "本文件包含当前选择的两条记录，不受搜索筛选限制。引用是标识，不包含其源文件。请按原研究的隐私范围保管本文件。"),
    `## ${label("Changed fields", "不同字段")} · ${snapshot.changed_fields.length}`,
  ];
  for (const key of snapshot.changed_fields) {
    lines.push(`### ${researchComparisonLabels[key][zh ? 1 : 0]}`);
    for (const [name, record] of [[label("Selected record", "所选记录"), snapshot.selected_record],
      [label("Comparison record", "对照记录"), snapshot.comparison_record]] as const) {
      lines.push(`#### ${name}`, record[key] === undefined ? label("Not recorded", "未记录")
        : researchReportLiteral(typeof record[key] === "string" ? record[key] : JSON.stringify(record[key], null, 2)));
    }
  }
  for (const [name, record] of [[label("Selected original record", "所选完整原始记录"), snapshot.selected_record],
    [label("Comparison original record", "对照完整原始记录"), snapshot.comparison_record]] as const) {
    lines.push(`## ${name}`, researchReportLiteral(JSON.stringify(record, null, 2)));
  }
  return lines.join("\n\n") + "\n";
}
