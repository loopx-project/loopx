import {researchRecordSnapshot, type ResearchRecordExport} from "./research-record-export.ts";

// Source prose stays literal: it cannot introduce headings, remote images or
// links into the saved report. Fences grow to preserve embedded code fences.
function literal(text: string): string {
  const fence = "`".repeat(Math.max(3, ...Array.from(text.matchAll(/`+/g), match => match[0].length + 1)));
  return `${fence}text\n${text}\n${fence}`;
}
const inline = (text: string) => text.replace(/[\\`*_{}\[\]<>()#+.!|~>-]/g, "\\$&").replace(/\r?\n/g, " ");

/** Human-readable original evidence, using exactly the JSON export's selection. */
export function exportResearchReport(input: ResearchRecordExport, zh: boolean): string {
  const snapshot = researchRecordSnapshot(input);
  const label = (en: string, cn: string) => zh ? cn : en;
  const missing = label("Not recorded", "未记录");
  const field = (name: string, value: unknown) => `- **${name}:** ${inline(value === undefined || value === null || value === "" ? missing : String(value))}`;
  const lines = [
    `# ${label("Research record report", "研究记录报告")}`,
    label("Original recorded evidence; not an analysis, matched evaluation or acceptance receipt.", "原始记录证据；不是分析、同条件评测或验收回执。"),
    field("Goal", snapshot.goal_id), field(label("Exported at", "导出时间"), snapshot.exported_at),
    `## ${label("Reading scope", "阅读范围")}`,
    field(label("Exported / loaded records", "导出 / 已读记录"), `${snapshot.coverage.exported_records} / ${snapshot.coverage.loaded_records}`),
    field(label("History", "历史"), input.complete ? label("Complete loaded history", "已完整载入历史") : label("Incomplete; unloaded records are not included", "不完整；不包含尚未读取的记录")),
    field(label("Refresh", "刷新"), input.stale ? label("Failed; previously read records retained", "失败；保留先前读取的记录") : label("No refresh failure reported", "未报告刷新失败")),
    field(label("Search", "搜索"), input.query || label("All text", "全部文字")),
    field(label("Status filter", "状态筛选"), input.status || label("All statuses", "全部状态")),
    label("Filters may match saved display translations; the records below retain their original language and revision. Record time is not execution time. References are identifiers, not downloaded source files. Keep this report as private as its source research.",
      "筛选可能匹配已保存的展示翻译；以下记录保留原始语言与版本。记录时间不是执行时间。引用是标识，不包含其源文件。请按原研究的隐私范围保管本报告。"),
  ];
  snapshot.records.forEach((record, index) => {
    lines.push(`## ${index + 1}. ${inline(record.title)}`, field(label("Recorded at", "记录时间"), record.recorded_at),
      field(label("Recorded status", "记录状态"), record.status),
      `### ${label("Original text", "原文")}`, record.summary ? literal(record.summary) : missing);
    if (record.blocked_reason) lines.push(`### ${label("Blocked reason", "受阻原因")}`, literal(record.blocked_reason));
    if (record.measurements?.length) {
      lines.push(`### ${label("Native measurements", "原生测量")}`);
      for (const measurement of record.measurements) {
        lines.push(`#### ${inline(measurement.metric)}`,
          field(label("Value", "数值"), measurement.value === null ? label("Unknown", "未知") : measurement.value),
          field(label("Unit", "单位"), measurement.unit),
          field(label("Direction", "方向"), measurement.higher_is_better ? label("Higher is better", "越高越好") : label("Lower is better", "越低越好")),
          field(label("Comparison scope", "比较范围"), measurement.comparison_scope),
          field(label("Input revision", "输入版本"), measurement.input_revision),
          field(label("Evaluation reference", "评测引用"), measurement.evaluation_ref),
          field(label("Candidate reference", "候选引用"), measurement.candidate_ref),
          field(label("Evidence reference", "证据引用"), measurement.evidence_ref));
      }
    }
    lines.push(`### ${label("Sources and identity", "来源与标识")}`);
    for (const key of ["schema_version", "event_id", "event_kind", "result_id", "node_kind", "node_id", "parent_id", "from_node", "to_node", "edge_type", "agent_id", "run_id", "supersedes"] as const) {
      if (record[key]) lines.push(field(key, record[key]));
    }
    lines.push(field(label("Recorded confidence", "记录置信度"), record.confidence));
    lines.push(record.evidence_refs.length ? literal(record.evidence_refs.join("\n")) : label("No evidence references recorded.", "未记录证据引用。"));
    if (record.execution_observation) lines.push(`### ${label("Recorded execution observation", "记录的执行观察")}`, literal(JSON.stringify(record.execution_observation, null, 2)));
  });
  return lines.join("\n\n") + "\n";
}
