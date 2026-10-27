import {useState, type ReactNode} from "react";
import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {changedResearchFields, researchComparisonFields, researchRecordVersions, type ResearchComparisonField} from "./research-record-versions.ts";
import {ExploreMeasurements} from "./explore-measurements";
import {ContextHelp} from "./context-help";

const labels: Record<ResearchComparisonField, [string, string]> = {
  title: ["Title", "标题"], summary: ["Original text", "原文"], status: ["Recorded status", "记录状态"],
  blocked_reason: ["Blocked reason", "受阻原因"], evidence_refs: ["Evidence references", "证据引用"],
  measurements: ["Measurements", "测量"], confidence: ["Recorded confidence", "记录置信度"],
  run_id: ["Run ID", "运行 ID"], agent_id: ["Agent ID", "Agent ID"], node_kind: ["Node kind", "节点类型"],
  node_id: ["Node ID", "问题 ID"], parent_id: ["Source group", "来源分组"],
  from_node: ["Link source", "关联起点"], to_node: ["Link target", "关联终点"], edge_type: ["Link type", "关联类型"],
  supersedes: ["Supersedes", "取代记录"], execution_observation: ["Execution observation", "执行观察"],
};

/** Requested original-source detail; no analysis, acceptance or write path. */
export function ResearchRecordComparison({events, selected, displayed, renderSummary, zh, stateLabel, timeLabel}: {
  events: readonly ExploreHistoryEvent[]; selected: ExploreHistoryEvent; zh: boolean;
  displayed?: ReadonlyMap<string, ExploreHistoryEvent>; renderSummary?: (event: ExploreHistoryEvent) => ReactNode;
  stateLabel: (status: string) => string; timeLabel: (at: string) => string;
}) {
  const versions = researchRecordVersions(events, selected);
  const index = versions.findIndex(event => event.event_id === selected.event_id);
  const alternatives = versions.filter(event => event.event_id !== selected.event_id);
  const [chosen, setChosen] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState(false);
  const comparison = alternatives.find(event => event.event_id === chosen)
    ?? versions[index - 1] ?? alternatives[0];
  if (!comparison) return null;
  const changed = changedResearchFields(selected, comparison);
  const value = (event: ExploreHistoryEvent, field: ResearchComparisonField) => {
    const raw = event[field];
    const shown = displayed?.get(event.event_id) ?? event;
    if (raw === undefined || raw === null || raw === "" || (Array.isArray(raw) && !raw.length)) return <span className="research-comparison-missing">{zh ? "未记录" : "Not recorded"}</span>;
    if (field === "measurements") return <ExploreMeasurements measurements={event.measurements ?? []} zh={zh}/>;
    if (field === "status") return stateLabel(event.status);
    if (Array.isArray(raw)) return <ul>{raw.map((item, i) => <li key={i}><code>{String(item)}</code></li>)}</ul>;
    const narrative = field === "summary" && renderSummary;
    const content = narrative ? renderSummary(shown)
      : typeof raw === "object" ? <pre>{JSON.stringify(shown[field], null, 2)}</pre> : String(shown[field]);
    return <>{content}{narrative || JSON.stringify(shown[field]) !== JSON.stringify(raw) ? <details>
      <summary>{zh ? "完整原文" : "Full original text"}</summary>
      {typeof raw === "object" ? <pre>{JSON.stringify(raw, null, 2)}</pre> : <p>{String(raw)}</p>}
    </details> : null}</>;
  };
  return <details className="research-record-comparison" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{zh ? "比较记录版本" : "Compare recorded versions"} · {versions.length}</summary>
    {open ? <>
      <label>{zh ? "比较对象" : "Compare with"}<select value={comparison.event_id} onChange={event => setChosen(event.target.value)}>
        {alternatives.map(event => <option key={event.event_id} value={event.event_id}>{timeLabel(event.recorded_at)} · {(displayed?.get(event.event_id) ?? event).title} · {event.event_id}</option>)}
      </select></label>
      <div className="research-comparison-controls">
        <span role="status">{zh ? `${changed.length} 个字段不同` : `${changed.length} fields differ`}</span>
        <ContextHelp label={zh ? "比较范围与记录标识" : "Comparison scope and record identities"}>
          <p>{zh ? "比较原始记录，不代表同条件评测或研究质量变化。" : "Compares original records, not matched evaluations or changes in research quality."}</p>
          <p>{zh ? "所选记录" : "Selected record"}<code>{selected.event_id}</code></p>
          <p>{zh ? "对照记录" : "Comparison record"}<code>{comparison.event_id}</code></p>
        </ContextHelp>
        <label><input type="checkbox" checked={showAll} onChange={event => setShowAll(event.target.checked)}/>{zh ? "包括相同字段" : "Include unchanged fields"}</label>
      </div>
      <div className="research-comparison-pair research-comparison-identities">
        {[selected, comparison].map((event, i) => <div key={event.event_id}><strong>{i === 0 ? (zh ? "所选记录" : "Selected record") : (zh ? "对照记录" : "Comparison record")}</strong><p>{timeLabel(event.recorded_at)}</p></div>)}
      </div>
      {(showAll ? researchComparisonFields : changed).map(field => <section key={field} className="research-comparison-field">
        <h4>{field === "summary" && renderSummary ? (zh ? "记录正文" : "Recorded text") : labels[field][zh ? 1 : 0]}</h4>
        <div className="research-comparison-pair">{[selected, comparison].map((event, i) => <div key={event.event_id}>
          <span className="research-comparison-side">{i === 0 ? (zh ? "所选记录" : "Selected record") : (zh ? "对照记录" : "Comparison record")}</span>
          <div>{value(event, field)}</div>
        </div>)}</div>
      </section>)}
    </> : null}
  </details>;
}
