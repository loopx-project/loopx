import {useMemo, useState, type ReactNode} from "react";
import type {ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {searchResearchRecords} from "./research-record-search";

/** Local read controls over the existing validated history snapshot. */
export function ResearchRecordSearch({events, displayEvents, renderBody, complete, stale, busy, zh, stateLabel, timeLabel}: {
  displayEvents?: readonly ExploreHistoryEvent[]; renderBody?: (event: ExploreHistoryEvent) => ReactNode;
  events: readonly ExploreHistoryEvent[]; complete: boolean; stale: boolean; busy: boolean; zh: boolean;
  stateLabel: (status: string) => string; timeLabel: (at: string) => string;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState(50);
  const matches = useMemo(() => searchResearchRecords(events, query, status), [events, query, status]);
  const displayed = useMemo(() => new Map(displayEvents?.map(event => [event.event_id, event])), [displayEvents]);
  const statuses = [...new Set(events.map(event => event.status).filter(Boolean))].sort();
  const reset = () => {setQuery(""); setStatus(""); setLimit(50);};
  return <div className="research-record-search">
    <div className="research-record-filters">
      <input type="search" aria-label={zh ? "搜索研究记录" : "Search research records"}
        placeholder={zh ? "原文、来源引用或 ID" : "Original text, source reference or ID"} value={query}
        onChange={event => {setQuery(event.target.value); setLimit(50);}}/>
      <select aria-label={zh ? "记录状态" : "Recorded status"} value={status}
        onChange={event => {setStatus(event.target.value); setLimit(50);}}>
        <option value="">{zh ? "所有状态" : "All statuses"}</option>
        {[...new Set([...statuses, ...(status ? [status] : [])])].map(item => <option key={item} value={item}>{stateLabel(item)}</option>)}
      </select>
      {query || status ? <button type="button" onClick={reset}>{zh ? "清除筛选" : "Clear filters"}</button> : null}
    </div>
    <p role="status">{zh ? `匹配 ${matches.length} / ${events.length} 条已读记录` : `${matches.length} matches in ${events.length} loaded records`}
      {!complete ? (zh ? " · 历史不完整" : " · History incomplete") : ""}
      {busy ? (zh ? " · 正在读取历史" : " · Reading history") : stale ? (zh ? " · 刷新失败，显示上次读取的记录" : " · Refresh failed; showing previously read records")
        : ""}</p>
    {!matches.length ? <p>{complete && !stale && !busy
      ? (zh ? "没有匹配的记录。尝试其他文字或清除筛选。" : "No matching records. Try another term or clear the filters.")
      : (zh ? "已读部分没有匹配记录；其余历史尚未核验。" : "No matches in the loaded records; the remaining history is not verified.")}</p> : null}
    <ol className="research-history-records">{matches.slice(0, limit).map(event => {
      const shown = displayed.get(event.event_id) ?? event;
      return <li key={event.event_id}>
      <details><summary><strong>{shown.title}</strong><span>{timeLabel(event.recorded_at)} · {stateLabel(event.status)}</span></summary>
        {renderBody ? renderBody(shown) : event.summary ? <p>{event.summary}</p> : null}
        {shown.blocked_reason ? <p>{shown.blocked_reason}</p> : null}
        {renderBody ? <details><summary>{zh ? "完整原文" : "Full original text"}</summary>
          <h4>{event.title}</h4><p>{event.summary}</p>{event.blocked_reason ? <p>{event.blocked_reason}</p> : null}
        </details> : null}
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
    </li>;})}</ol>
    {limit < matches.length ? <button type="button" onClick={() => setLimit(value => value + 50)}>{zh ? "继续展开记录" : "Show more records"} · {Math.min(limit, matches.length)} / {matches.length}</button> : null}
  </div>;
}
