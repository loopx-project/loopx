import {ResearchRecordComparison} from "./research-record-comparison";
import {CircleHelp, TriangleAlert} from "lucide-react";
import {ContextHelp} from "./context-help";
import {useMemo, useState, type ReactNode} from "react";
import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {searchResearchRecords, type ResearchRecordDisplay} from "./research-record-search";
import {exportResearchRecords} from "./research-record-export";
import {downloadReport} from "./report-download";

/** Local read controls over the existing validated history snapshot. */
export function ResearchRecordSearch({goalId, events, displayEvents, displaySections, renderBody, complete, stale, busy, zh, stateLabel, timeLabel}: {
  goalId: string;
  displayEvents?: readonly ExploreHistoryEvent[]; renderBody?: (event: ExploreHistoryEvent) => ReactNode;
  displaySections?: ResearchRecordDisplay["sections"];
  events: readonly ExploreHistoryEvent[]; complete: boolean; stale: boolean; busy: boolean; zh: boolean;
  stateLabel: (status: string) => string; timeLabel: (at: string) => string;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState(50);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const displayed = useMemo(() => new Map(displayEvents?.map(event => [event.event_id, event])), [displayEvents]);
  const display = useMemo(() => ({events: displayed, sections: displaySections}), [displayed, displaySections]);
  const matches = useMemo(() => searchResearchRecords(events, query, status, display), [events, query, status, display]);
  const statuses = [...new Set(events.map(event => event.status).filter(Boolean))].sort();
  const countLabel = zh ? `匹配 ${matches.length} / ${events.length} 条已读记录` : `${matches.length} matches in ${events.length} loaded records`;
  const readState = busy ? (zh ? "读取中" : "Reading") : stale ? (zh ? "刷新失败" : "Refresh failed") : "";
  const reset = () => {setQuery(""); setStatus(""); setLimit(50);};
  return <div className="research-record-search">
    <div className="research-record-filters">
      <input type="search" aria-label={zh ? "搜索研究记录" : "Search research records"}
        placeholder={zh ? "正文、来源引用或 ID" : "Text, source reference or ID"} value={query}
        onChange={event => {setQuery(event.target.value); setLimit(50);}}/>
      <select aria-label={zh ? "记录状态" : "Recorded status"} value={status}
        onChange={event => {setStatus(event.target.value); setLimit(50);}}>
        <option value="">{zh ? "所有状态" : "All statuses"}</option>
        {[...new Set([...statuses, ...(status ? [status] : [])])].map(item => <option key={item} value={item}>{stateLabel(item)}</option>)}
      </select>
      {query || status ? <button type="button" onClick={reset}>{zh ? "清除筛选" : "Clear filters"}</button> : null}
      <button type="button" disabled={busy || !matches.length} onClick={() => {
        setDownloadFailed(false);
        try {
          const text = exportResearchRecords({goalId, events, query, status, complete, stale, display, exportedAt: new Date().toISOString()});
          downloadReport(`research-${goalId}.json`, "", text);
        } catch {setDownloadFailed(true);}
      }}>{zh ? "下载匹配记录（JSON）" : "Download matches (JSON)"}</button>
    </div>
    {downloadFailed ? <p role="alert">{zh ? "无法开始下载，记录未改变。请重试下载。" : "Could not start the download. Records are unchanged; try downloading again."}</p> : null}
    <div className="research-record-count">
      <span role="status" aria-label={`${countLabel}${readState ? ` · ${readState}` : ""}`}>
        {matches.length} / {events.length} {zh ? "条记录" : "records"}{readState ? ` · ${readState}` : ""}
      </span>
      <ContextHelp label={!matches.length ? (zh ? "已读范围无匹配记录" : "No matches in loaded records")
        : stale ? (zh ? "刷新失败；搜索范围" : "Refresh failed; search scope")
          : !complete ? (zh ? "历史不完整；搜索范围" : "Incomplete history; search scope") : (zh ? "搜索范围" : "Search scope")}
        icon={!complete || stale ? TriangleAlert : CircleHelp}>
        <p>{countLabel}{zh ? "。" : "."}</p>
        <p>{complete ? (zh ? "已搜索完整载入的历史。" : "Search covers the complete loaded history.") : (zh ? "历史不完整；其余记录尚未核验。" : "History is incomplete; remaining records have not been verified.")}</p>
        {stale ? <p>{zh ? "刷新失败，显示上次读取的记录。" : "Refresh failed; showing previously read records."}</p> : null}
        {!matches.length ? <p>{complete && !stale && !busy
          ? (zh ? "没有匹配的记录。尝试其他文字或清除筛选。" : "No matching records. Try another term or clear the filters.")
          : (zh ? "已读部分没有匹配记录；其余历史尚未核验。" : "No matches in the loaded records; the remaining history is not verified.")}</p> : null}
      </ContextHelp>
    </div>
    <ol className="research-history-records">{matches.slice(0, limit).map(event => {
      const shown = displayed.get(event.event_id) ?? event;
      return <li key={event.event_id}>
      <details className="research-record-entry"><summary><strong>{shown.title}</strong><span>{timeLabel(event.recorded_at)} · {stateLabel(event.status)}</span></summary>
        {renderBody ? renderBody(shown) : event.summary ? <p>{event.summary}</p> : null}
        {shown.blocked_reason ? <p>{shown.blocked_reason}</p> : null}
        {renderBody ? <details><summary>{zh ? "完整原文" : "Full original text"}</summary>
          <h4>{event.title}</h4><p>{event.summary}</p>{event.blocked_reason ? <p>{event.blocked_reason}</p> : null}
        </details> : null}
        <details className="research-record-sources"><summary>{zh ? "来源与标识" : "Sources and identity"}</summary>
        <dl><dt>{zh ? "记录 ID" : "Record ID"}</dt><dd><code>{event.event_id}</code></dd>
          <dt>{zh ? "结果 ID" : "Result ID"}</dt><dd><code>{event.result_id}</code></dd>
          {event.run_id ? <><dt>{zh ? "运行 ID" : "Run ID"}</dt><dd><code>{event.run_id}</code></dd></> : null}
          {event.supersedes ? <><dt>{zh ? "取代记录" : "Supersedes"}</dt><dd><code>{event.supersedes}</code></dd></> : null}
          {event.node_id ? <><dt>{zh ? "问题 ID" : "Node ID"}</dt><dd><code>{event.node_id}</code></dd></> : null}
          {event.parent_id ? <><dt>{zh ? "来源分组" : "Source group"}</dt><dd><code>{event.parent_id}</code></dd></> : null}
          {event.from_node || event.to_node ? <><dt>{zh ? "显式关联" : "Recorded link"}</dt><dd><code>{event.from_node} → {event.to_node}</code></dd></> : null}
          {event.agent_id ? <><dt>Agent ID</dt><dd><code>{event.agent_id}</code></dd></> : null}
        </dl>
        {event.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}
        </details>
        <ResearchRecordComparison events={events} selected={event} zh={zh} stateLabel={stateLabel} timeLabel={timeLabel}/>
      </details>
    </li>;})}</ol>
    {limit < matches.length ? <button type="button" onClick={() => setLimit(value => value + 50)}>{zh ? "继续展开记录" : "Show more records"} · {Math.min(limit, matches.length)} / {matches.length}</button> : null}
  </div>;
}
