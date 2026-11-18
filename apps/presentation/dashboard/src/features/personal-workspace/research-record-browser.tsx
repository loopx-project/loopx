import {ResearchRecordComparison} from "./research-record-comparison";
import {CircleHelp, TriangleAlert} from "lucide-react";
import {ContextHelp} from "./context-help";
import {useEffect, useMemo, useRef, useState, type ReactNode} from "react";
import type {ExploreHistoryEvent, ExploreHistoryNode} from "../../../../../../loopx/control_plane/presentation/explore_history.ts";
import {readResearchRecordsLocation, writeResearchRecordsLocation, researchRecordGroups, searchResearchRecords, type ResearchRecordDisplay, type ResearchRecordsLocation} from "./research-record-search";
import {exportResearchRecords} from "./research-record-export";
import {exportResearchReport} from "./research-record-report";
import {downloadReport} from "./report-download";

/** Reuse the research URL owner for reload and Back/Forward. Native App window
 * reopen remains a separate preference boundary. */
export function useResearchRecordsLocation(scope: string) {
  const read = () => readResearchRecordsLocation(new URLSearchParams(window.location.search), scope);
  const [location, setLocation] = useState(() => ({scope, value: read(), navigation: 0}));
  const value = location.scope === scope ? location.value : read();
  const current = useRef(value);
  current.current = value;
  useEffect(() => {
    const changed = () => {current.current = read(); setLocation(previous => ({scope, value: current.current, navigation: previous.navigation + 1}));};
    changed(); window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, [scope]);
  const update = (patch: Partial<ResearchRecordsLocation>, replace = true) => {
    const next = {...current.current, ...patch};
    if (JSON.stringify(next) === JSON.stringify(current.current)) return;
    current.current = next;
    const url = new URL(window.location.href);
    url.search = writeResearchRecordsLocation(url.searchParams, scope, next).toString();
    window.history[replace ? "replaceState" : "pushState"](window.history.state, "", url);
    setLocation(previous => ({scope, value: next, navigation: previous.navigation}));
  };
  return {value, update, navigation: location.navigation};
}

/** Local read controls over the existing validated history snapshot. */
export function ResearchRecordSearch({goalId, events, nodes, displayEvents, displaySections, renderBody, renderSummary, complete, stale, busy, zh, stateLabel, timeLabel, reading}: {
  goalId: string;
  displayEvents?: readonly ExploreHistoryEvent[]; renderBody?: (event: ExploreHistoryEvent) => ReactNode;
  displaySections?: ResearchRecordDisplay["sections"];
  renderSummary?: (event: ExploreHistoryEvent) => ReactNode;
  events: readonly ExploreHistoryEvent[]; nodes: readonly ExploreHistoryNode[]; complete: boolean; stale: boolean; busy: boolean; zh: boolean;
  stateLabel: (status: string) => string; timeLabel: (at: string) => string;
  reading: ReturnType<typeof useResearchRecordsLocation>;
}) {
  const {query, status, limit, expanded} = reading.value;
  const root = useRef<HTMLDivElement>(null);
  const focusNavigation = useRef(-1);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const [format, setFormat] = useState("json");
  const displayed = useMemo(() => new Map(displayEvents?.map(event => [event.event_id, event])), [displayEvents]);
  const display = useMemo(() => ({events: displayed, sections: displaySections}), [displayed, displaySections]);
  const matches = useMemo(() => searchResearchRecords(events, query, status, display), [events, query, status, display]);
  const groups = useMemo(() => researchRecordGroups(matches, complete ? nodes : []), [matches, nodes, complete]);
  const allGroups = useMemo(() => researchRecordGroups(events, complete ? nodes : []), [events, nodes, complete]);
  useEffect(() => {
    if (!complete || busy || focusNavigation.current === reading.navigation) return;
    focusNavigation.current = reading.navigation;
    const row = [...(root.current?.querySelectorAll<HTMLElement>("[data-research-record]") ?? [])]
      .find(element => expanded.includes(element.dataset.researchRecord!));
    const target = row?.querySelector<HTMLElement>("summary");
    target?.focus({preventScroll: true}); target?.scrollIntoView({block: "nearest"});
  }, [complete, busy, reading.navigation, groups, expanded]);
  const missing = complete && expanded.some(id => !allGroups.some(group => group.id === id));
  const statuses = [...new Set(events.map(event => event.status).filter(Boolean))].sort();
  const groupCount = zh ? `${groups.length} 个条目` : `${groups.length} ${groups.length === 1 ? "entry" : "entries"}`;
  const countLabel = zh ? `匹配 ${matches.length} / ${events.length} 条已读记录 · ${groupCount}` : `${matches.length} matches in ${events.length} loaded records · ${groupCount}`;
  const readState = busy ? (zh ? "读取中" : "Reading") : stale ? (zh ? "刷新失败" : "Refresh failed") : "";
  const reset = () => reading.update({query: "", status: "", limit: 50});
  const toggle = (id: string, open: boolean) => {
    if (expanded.includes(id) === open) return;
    reading.update({expanded: open ? [...expanded, id] : expanded.filter(value => value !== id)}, false);
  };
  const originalContent = (event: ExploreHistoryEvent) => {
    const shown = displayed.get(event.event_id) ?? event;
    return <>
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
      <ResearchRecordComparison events={events} selected={event} displayed={displayed} renderSummary={renderSummary}
        zh={zh} stateLabel={stateLabel} timeLabel={timeLabel}/>
    </>;
  };
  return <div className="research-record-search" ref={root}>
    <div className="research-record-filters">
      <input type="search" aria-label={zh ? "搜索研究记录" : "Search research records"}
        placeholder={zh ? "正文、来源引用或 ID" : "Text, source reference or ID"} value={query}
        onChange={event => reading.update({query: event.target.value, limit: 50})}/>
      <select aria-label={zh ? "记录状态" : "Recorded status"} value={status}
        onChange={event => reading.update({status: event.target.value, limit: 50})}>
        <option value="">{zh ? "所有状态" : "All statuses"}</option>
        {[...new Set([...statuses, ...(status ? [status] : [])])].map(item => <option key={item} value={item}>{stateLabel(item)}</option>)}
      </select>
      {query || status ? <button type="button" onClick={reset}>{zh ? "清除筛选" : "Clear filters"}</button> : null}
      <select aria-label={zh ? "下载格式" : "Download format"} value={format} onChange={event => setFormat(event.target.value)}>
        <option value="json">JSON</option>
        <option value="md">{zh ? "阅读报告（Markdown）" : "Reading report (Markdown)"}</option>
      </select>
      <button type="button" disabled={busy || !matches.length} onClick={() => {
        setDownloadFailed(false);
        try {
          const input = {goalId, events, query, status, complete, stale, display, exportedAt: new Date().toISOString()};
          const text = format === "md" ? exportResearchReport(input, zh) : exportResearchRecords(input);
          downloadReport(`research-${goalId}.${format}`, "", text);
        } catch {setDownloadFailed(true);}
      }}>{format === "md" ? (zh ? "下载阅读报告" : "Download reading report") : (zh ? "下载匹配记录（JSON）" : "Download matches (JSON)")}</button>
    </div>
    {downloadFailed ? <p role="alert">{zh ? "无法开始下载，记录未改变。请重试下载。" : "Could not start the download. Records are unchanged; try downloading again."}</p> : null}
    <div className="research-record-count">
      <span role="status" aria-label={`${countLabel}${readState ? ` · ${readState}` : ""}`}>
        {groupCount} · {matches.length} / {events.length} {zh ? "条原始记录" : "original records"}{readState ? ` · ${readState}` : ""}
      </span>
      <ContextHelp label={!matches.length ? (zh ? "已读范围无匹配记录" : "No matches in loaded records")
        : stale ? (zh ? "刷新失败；搜索范围" : "Refresh failed; search scope")
          : !complete ? (zh ? "历史不完整；搜索范围" : "Incomplete history; search scope") : (zh ? "搜索范围" : "Search scope")}
        icon={!complete || stale ? TriangleAlert : CircleHelp}>
        <p>{countLabel}{zh ? "。" : "."}</p>
        <p>{zh ? "明确关联的记录可在同一条目内展开。筛选和下载按原始记录匹配。" : "Open an entry to read its linked records. Filters and downloads match the original records."}</p>
        <p>{complete ? (zh ? "已搜索完整载入的历史。" : "Search covers the complete loaded history.") : (zh ? "历史不完整；其余记录尚未核验。" : "History is incomplete; remaining records have not been verified.")}</p>
        {stale ? <p>{zh ? "刷新失败，显示上次读取的记录。" : "Refresh failed; showing previously read records."}</p> : null}
        {!matches.length ? <p>{complete && !stale && !busy
          ? (zh ? "没有匹配的记录。尝试其他文字或清除筛选。" : "No matching records. Try another term or clear the filters.")
          : (zh ? "已读部分没有匹配记录；其余历史尚未核验。" : "No matches in the loaded records; the remaining history is not verified.")}</p> : null}
      </ContextHelp>
    </div>
    {missing ? <p role="status">{zh ? "原先展开的条目在当前分组中不可用，其他原始记录仍可查阅。" : "A previously opened entry is unavailable in the current grouping. Other original records remain readable."}</p> : null}
    <ol className="research-history-records">{groups.slice(0, limit).map(group => {
      const event = group.events[0]!;
      const shown = displayed.get(event.event_id) ?? event;
      return <li key={group.id} data-research-record={group.id}>
        {group.node && group.events.length > 1
          ? <GroupedResearchRecord title={(displayed.get(group.node.latest.event_id) ?? group.node.latest).title}
            events={group.events} displayed={displayed} renderRecord={originalContent} zh={zh} stateLabel={stateLabel} timeLabel={timeLabel}
            open={expanded.includes(group.id)} onOpen={open => toggle(group.id, open)}/>
          : <details className="research-record-entry" open={expanded.includes(group.id)} onToggle={event => toggle(group.id, event.currentTarget.open)}>
            <summary><strong>{shown.title}</strong><span>{timeLabel(event.recorded_at)} · {stateLabel(event.status)}</span></summary>
            {originalContent(event)}
          </details>}
    </li>;})}</ol>
    {limit < groups.length ? <button type="button" onClick={() => reading.update({limit: limit + 50})}>{zh ? "显示更多条目" : "Show more entries"} · {limit} / {groups.length}</button> : null}
  </div>;
}

function GroupedResearchRecord({title, events, displayed, renderRecord, zh, stateLabel, timeLabel, open, onOpen}: {
  title: string; events: readonly ExploreHistoryEvent[]; displayed: ReadonlyMap<string, ExploreHistoryEvent>;
  renderRecord: (event: ExploreHistoryEvent) => ReactNode; zh: boolean;
  stateLabel: (status: string) => string; timeLabel: (at: string) => string;
  open: boolean; onOpen: (open: boolean) => void;
}) {
  const findings = events.filter(event => event.event_kind === "finding");
  const lifecycle = events.filter(event => event.event_kind === "node").at(-1)?.status;
  const statuses = [...new Set(findings.map(event => event.status))].filter(Boolean).map(stateLabel);
  if (lifecycle) statuses.push(stateLabel(lifecycle));
  const summary = [timeLabel(events.at(-1)!.recorded_at), ...statuses, zh ? `${events.length} 条记录` : `${events.length} records`];
  return <details className="research-record-entry" open={open} onToggle={event => onOpen(event.currentTarget.open)}
    onKeyDown={event => {
      if (event.key !== "Escape" || !event.currentTarget.open || !(event.target instanceof Element)
          || event.target.closest("details") !== event.currentTarget) return;
      event.preventDefault(); event.stopPropagation();
      event.currentTarget.open = false;
      event.currentTarget.querySelector("summary")?.focus();
    }}>
    <summary><strong>{title}</strong><span>{summary.join(" · ")}</span></summary>
    {open ? <ol className="research-record-group-history">{events.map(event => {
      const eventTitle = (displayed.get(event.event_id) ?? event).title;
      const kind = event.event_kind === "finding" ? (zh ? "结论记录" : "Finding") : (zh ? "研究项记录" : "Research item");
      return <li key={event.event_id}>
        <h4>{eventTitle === title ? kind : eventTitle}</h4>
        <small>{timeLabel(event.recorded_at)} · {stateLabel(event.status)}</small>
        {renderRecord(event)}
      </li>;
    })}</ol> : null}
  </details>;
}
