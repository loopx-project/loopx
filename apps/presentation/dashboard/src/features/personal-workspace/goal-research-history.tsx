import {useEffect, useId, useMemo, useRef, useState} from "react";
import {defaultParseSearch} from "@tanstack/react-router";
import {ArrowLeft, Circle, FlaskConical, GitBranch, RefreshCw} from "lucide-react";
import {ResearchAnalysisButton, ResearchAnalysisPanel, ResearchAnalysisStatus, ResearchProgressSummary, useResearchAnalysis} from "./goal-research-analysis";
import {buildExploreTimeline, parseExploreFindingSections} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import {GoalResearchTimeline} from "./goal-research-timeline";
import {requestJson} from "../../data/chat";
import {sourceReadApiUrl} from "../../data/local-status-query";
import {ContextHelp} from "./context-help";
import {groupUnlinkedHistoryQuestions, layoutExploreHistory, parseExploreHistoryPage, projectExploreHistory, type ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.js";
import "./goal-research-history.css";
import {ExploreMeasurements, formatExploreMeasurementValue} from "./explore-measurements";

type Selection = {nodes: string[]; day: string | null};
type Lens = "timeline" | "graph";
type Read = {events: ExploreHistoryEvent[]; total: number | null; complete: boolean};
const selectedFromUrl = (goal: string): Selection => {
  const query = new URLSearchParams(window.location.search);
  if (query.get("research_goal") !== goal) return {nodes: [], day: null};
  const day = query.get("research_group");
  // Reuse the router's array encoding so a grouped selection also survives
  // canonicalization on reload. Existing scalar node links remain valid.
  const search = defaultParseSearch(window.location.search);
  const decoded = "research_node" in search ? search.research_node : undefined;
  const nodes = Array.isArray(decoded) && decoded.every(id => typeof id === "string") ? decoded : query.getAll("research_node");
  return {nodes: day === null ? [...new Set(nodes.filter(Boolean))] : [], day};
};

/** An effect-free history consumer. Its source is Explore, not raw sessions or
 * a judge. All recorded updates survive; unsupported analysis stays unavailable. */
export function GoalResearchHistory({goalId, active, zh, refreshRevision = 0, readSourceUrl}: {goalId: string; active: boolean; zh: boolean; refreshRevision?: number; readSourceUrl?: string}) {
  const analysis = useResearchAnalysis(goalId, active, zh, readSourceUrl);
  const [read, setRead] = useState<Read>({events: [], total: null, complete: false});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState(() => selectedFromUrl(goalId));
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [recordLimit, setRecordLimit] = useState(50);
  const [lens, setLens] = useState<Lens>(() => new URLSearchParams(window.location.search).get("research_lens") === "graph" ? "graph" : "timeline");
  const [phase, setPhase] = useState<number | null>(null);
  useEffect(() => {setPhase(null);}, [goalId]);
  const presentationId = useId().replace(/:/g, "");
  const detail = useRef<HTMLDivElement>(null);
  const graph = useRef<HTMLDivElement>(null);
  const copy = zh ? {
    title: "研究历程", loading: "正在读取完整记录", reading: "读取中", refresh: "刷新记录",
    all: "回到全图", scope: "Explore 已记录历史", coverage: "其他来源覆盖、尝试发生时序尚未核验",
    partial: "历史尚未完整载入", failure: "无法继续读取历史。已读记录保留；请重试。",
    time: "记录时间（本地）", unknown: "时间未记录", records: "条记录", evidence: "已记录依据",
    interpretation: "独立检查与实际采用尚未核验", versions: "来源与版本引用", updates: "历史记录",
    changes: "次记录", findings: "条结论", links: "显式关联", missing: "部分关联的来源节点尚未记录",
    group: "来源分组", lost: "当前记录中找不到所选节点，其他历史仍可浏览。",
  } : {
    title: "Research history", loading: "Reading the full recorded history", reading: "Reading", refresh: "Refresh history",
    all: "Whole graph", scope: "Recorded Explore history", coverage: "Other-source coverage and attempt-time coverage are unverified",
    partial: "History is not fully loaded", failure: "Cannot continue reading history. Loaded records remain; retry.",
    time: "Recorded time (local)", unknown: "Time not recorded", records: "records", evidence: "Recorded evidence",
    interpretation: "Independent checking and actual adoption are unverified", versions: "Source and version references", updates: "History",
    changes: "records", findings: "findings", links: "Explicit relationships", missing: "Some linked source nodes are not recorded",
    group: "Source grouping", lost: "The selected node is absent from this snapshot. Other history remains readable.",
  };
  // Labels reuse Explore's existing vocabulary; they confer no verification state.
  const kindLabels: Record<string, string> = zh ? {question: "问题", hypothesis: "假设", experiment: "尝试", artifact: "产物", area: "路线"} : {question: "Question", hypothesis: "Hypothesis", experiment: "Attempt", artifact: "Artifact", area: "Route"};
  const stateLabels: Record<string, string> = zh ? {open: "待探索", exploring: "探索中", blocked: "受阻", resolved: "已收束", dead_end: "已排除", tentative: "待检查", confirmed: "来源记为确认", refuted: "已反驳"} : {open: "Open", exploring: "Exploring", blocked: "Blocked", resolved: "Resolved", dead_end: "Ruled out", tentative: "Tentative", confirmed: "Recorded confirmation", refuted: "Refuted"};
  const relationLabels: Record<string, string> = zh ? {subtopic_of: "属于", depends_on: "依赖", answers: "回答", supports: "支持", refutes: "反驳", leads_to: "引向"} : {subtopic_of: "Part of", depends_on: "Depends on", answers: "Answers", supports: "Supports", refutes: "Refutes", leads_to: "Leads to"};
  const kindLabel = (kind: string) => kindLabels[kind] ?? kind;
  const stateLabel = (state: string) => stateLabels[state] ?? state;
  const relationLabel = (type: string) => relationLabels[type] ?? type;
  const timeLabel = (value: string | null) => value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)) : copy.unknown;

  const sectionCopy = zh ? {inputRevision: "输入版本", applicability: "适用范围", observation: "观察", interpretation: "解读", changed: "变化", stopped: "停止", retained: "保留"}
    : {inputRevision: "Input revision", applicability: "Applicability", observation: "Observation", interpretation: "Interpretation", changed: "Changed", stopped: "Stopped", retained: "Retained"};
  const outcomeCopy: Record<string, string> = zh ? {replan: "转向", continue: "继续", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定"} : {replan: "Replan", continue: "Continue", stop: "Stop", wait: "Wait", no_change: "No change", ask_human: "Ask human"};
  const findingBody = (finding: ExploreHistoryEvent) => {
    const sections = parseExploreFindingSections(finding.summary);
    const decision = sections?.decision;
    const execution = finding.execution_observation;
    const measured = (value: unknown) => value === null ? (zh ? "未测量" : "Not measured") : String(value);
    return <>{finding.measurements ? <ExploreMeasurements measurements={finding.measurements} zh={zh}/> : null}{sections ? <dl className="research-finding-sections">
      <dt>{sectionCopy.observation}</dt><dd>{sections.observation}</dd>
      {decision ? <>
        <dt>{zh ? "决策" : "Decision"}</dt><dd><b className={`research-outcome is-${decision.outcome}`}>{outcomeCopy[decision.outcome] ?? decision.outcome}</b></dd>
        {(["changed", "stopped", "retained"] as const).map(key => decision[key].length ? <><dt key={`${key}-t`}>{sectionCopy[key]}</dt><dd key={key}><ul>{decision[key].map(item => <li key={item}>{item}</li>)}</ul></dd></> : null)}
      </> : <><dt>{sectionCopy.interpretation}</dt><dd>{sections.interpretation}</dd></>}
      <dt>{sectionCopy.applicability}</dt><dd className="is-muted">{sections.applicability}</dd>
      <dt>{sectionCopy.inputRevision}</dt><dd><code>{sections.inputRevision}</code></dd>
    </dl> : <p>{finding.summary}</p>}{execution ? <details><summary>{zh ? "实际执行与资源观测" : "Execution and resource observations"}</summary>
      <dl className="research-finding-sections">
        <dt>{zh ? "计划方法 → 实际方法" : "Planned → executed method"}</dt><dd>{String(execution.planned_method_id)} → {String(execution.executed_method_id)}</dd>
        {execution.change_reason ? <><dt>{zh ? "改向原因" : "Reason for change"}</dt><dd>{String(execution.change_reason)}</dd></> : null}
        <dt>{zh ? "运行秒数 / 上限秒数" : "Elapsed / limit seconds"}</dt><dd>{measured(execution.elapsed_seconds)} / {measured(execution.limit_seconds)}</dd>
        <dt>{zh ? "搜索次数" : "Search iterations"}</dt><dd>{measured(execution.search_iterations)}</dd>
        <dt>{zh ? "结束原因" : "Termination reason"}</dt><dd>{measured(execution.stop_reason)}</dd>
        <dt>{zh ? "执行候选 SHA-256" : "Executed candidate SHA-256"}</dt><dd><code>{String(execution.candidate_sha256)}</code></dd>
        {execution.result_ref ? <><dt>{zh ? "该候选的结果引用" : "Result for this candidate"}</dt><dd><code>{String(execution.result_ref)}</code></dd></> : null}
      </dl><p>{zh ? "来源记录的执行观测；不等于方法有效或当前候选已通过验收。" : "Source-recorded execution observations; not proof of method benefit or current-candidate acceptance."}</p>
    </details> : null}</>;
  };
  const findingElement = (finding: ExploreHistoryEvent) => <div className="research-recorded-finding" key={finding.event_id}><strong>{finding.title}</strong><small>{timeLabel(finding.recorded_at)} · {zh ? "来源标记" : "Source label"} · {stateLabel(finding.status)}{finding.confidence !== null ? ` · ${zh ? "来源自报置信度" : "Source-reported confidence"} ${finding.confidence}` : ""}</small>{findingBody(finding)}{finding.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}</div>;
  const updateRecords = (events: readonly ExploreHistoryEvent[]) => <ol className="research-history-records">{events.map(event => <li key={event.event_id}><time>{timeLabel(event.recorded_at)}</time><span>{stateLabel(event.status)} · {event.title}{event.summary ? ` — ${event.summary}` : ""}</span>{event.measurements ? <ExploreMeasurements measurements={event.measurements} zh={zh}/> : null}{event.parent_id ? <code>{copy.group}: {event.parent_id}</code> : null}{event.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}{event.run_id ? <code>{event.run_id}</code> : null}</li>)}</ol>;

  useEffect(() => {
    const pop = () => setSelection(selectedFromUrl(goalId));
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, [goalId]);
  useEffect(() => {
    if (!active) return;
    let current = true;
    const controller = new AbortController();
    setBusy(true); setError(""); setLoaded(0);
    void (async () => {
      const events: ExploreHistoryEvent[] = [];
      const cursors = new Set<string>();
      let total = -1;
      try {
        let cursor = "";
        do {
          const query = new URLSearchParams({goal_id: goalId, view: "history", ...(cursor ? {cursor} : {})});
          const page = parseExploreHistoryPage(await requestJson<unknown>(sourceReadApiUrl(readSourceUrl, `/api/chat/explore-results?${query}`), {signal: controller.signal}), goalId);
          if (total !== -1 && total !== page.total) throw new Error("Explore history snapshot changed");
          total = page.total; events.push(...page.items);
          if (events.length > total || (page.next_cursor && (!page.items.length || cursors.has(page.next_cursor)))) throw new Error("Invalid Explore history pagination");
          if (current) setLoaded(events.length);
          cursor = page.next_cursor ?? "";
          if (cursor) cursors.add(cursor);
          // The owning HTTP snapshot bounds size; never silently cap history here.
        } while (cursor && current);
        if (current) {
          if (cursor || events.length !== total) throw new Error("Explore history coverage mismatch");
          projectExploreHistory(events, goalId); // Verify replay identities before replacing a valid snapshot.
          setRead({events, total, complete: true});
        }
      } catch (failure) {
        if (current) {
          if (events.length) setRead(previous => previous.complete ? previous : {events, total: Math.max(total, events.length), complete: false});
          setError(failure instanceof Error ? failure.message : String(failure));
        }
      } finally {if (current) setBusy(false);}
    })();
    return () => {current = false; controller.abort();};
  }, [goalId, active, revision, refreshRevision, readSourceUrl]);
  const history = useMemo(() => {
    try {return {value: projectExploreHistory(read.events, goalId), error: ""};}
    catch (failure) {return {value: null, error: String(failure)};}
  }, [read.events, goalId]);
  const value = history.value;
  const measurementFeedback = value?.measurementFeedback ?? [];
  const measurementSummary = (nodeId: string) => {
    const feedback = measurementFeedback.filter(item => item.node_id === nodeId);
    return feedback.length ? <ExploreMeasurements title={zh ? "最近记录与历史最佳" : "Latest recorded and historical best"}
      measurements={feedback.map(item => item.latest.measurement)} feedback={feedback} zh={zh}/> : null;
  };
  const grouped = useMemo(() => groupUnlinkedHistoryQuestions(value?.nodes ?? [], value?.edges ?? []), [value]);
  const layout = useMemo(() => layoutExploreHistory(grouped.nodes, value?.edges ?? []), [grouped, value]);
  const timeline = useMemo(() => value ? buildExploreTimeline(value.nodes, value.edges, value.events) : null, [value]);
  const chooseLens = (next: Lens) => { setLens(next); const url = new URL(window.location.href); if (next === "graph") url.searchParams.set("research_lens", "graph"); else url.searchParams.delete("research_lens"); window.history.replaceState(window.history.state, "", url); };
  const fullTime = (at: number) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(at));
  const dayTime = (at: number) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "numeric", day: "numeric"}).format(new Date(at));
  const positions = new Map(layout.map(item => [item.node.id, item]));
  const selectedId = selection.nodes.length === 1 ? selection.nodes[0]! : "";
  const selected = value?.nodes.find(node => node.id === selectedId);
  const selectedGroup = selection.day !== null ? grouped.groups.find(group => group.day === selection.day) : undefined;
  const selectedMembers = selectedGroup?.nodes ?? (selection.nodes.length > 1 ? value?.nodes.filter(node => selection.nodes.includes(node.id)) : undefined);
  const clusterY = layout.length ? Math.max(...layout.map(item => item.y + 124)) + 24 : 24;
  const width = Math.max(600, grouped.groups.length ? 944 : 0, ...layout.map(item => item.x + 230));
  const height = Math.max(230, ...layout.map(item => item.y + 124), grouped.groups.length ? clusterY + Math.ceil(grouped.groups.length / 4) * 124 : 0);
  const select = (next: Selection, focus = true) => {
    setSelection(next);
    const url = new URL(window.location.href);
    url.searchParams.delete("research_node"); url.searchParams.delete("research_group");
    if (next.nodes.length || next.day !== null) {
      if (next.day !== null) url.searchParams.set("research_group", next.day);
      else url.searchParams.set("research_node", next.nodes.length === 1 ? next.nodes[0]! : JSON.stringify(next.nodes));
      url.searchParams.set("research_goal", goalId);
    }
    else url.searchParams.delete("research_goal");
    if (selection.day !== next.day || selection.nodes.length !== next.nodes.length || selection.nodes.some((id, index) => id !== next.nodes[index])) window.history.pushState(null, "", url);
    if (focus && (next.nodes.length || next.day !== null)) window.requestAnimationFrame(() => {
      const panel = detail.current;
      if (panel) {panel.scrollTop = 0; panel.focus({preventScroll: true}); panel.scrollIntoView({block: "nearest"});}
    });
  };
  const choose = (id: string, focus = true) => select({nodes: id ? [id] : [], day: null}, focus);
  if (!busy && !error && !read.events.length && !analysis.pack && !analysis.snapshot?.catalog.packs.length) return null;
  return <section className="research-history" aria-label={copy.title} aria-busy={busy}>
    <header className="research-history-heading"><div><div className="research-history-title"><h2>{copy.title}</h2><ContextHelp label={zh ? "历史范围与读图说明" : "History scope and reading guide"}>
      <p>{copy.scope} · {copy.time}{zh ? "。" : ". "}{copy.coverage}{zh ? "。" : "."}</p><p>{copy.interpretation}{zh ? "。" : "."}</p>
      {lens === "graph" ? <p>{zh ? "按来源分组，实线为已记录关联，虚线仅表示分组；排列不代表时间或进展。未关联问题按首次记录 UTC 日期收纳。" : "Grouped by source. Solid lines are recorded relationships; dotted lines only group nodes. Position does not imply time or progress. Unlinked questions are collected by first-record UTC date."}</p> : null}
      <p>{zh ? "合拢仅为避免遮挡，不表示关联或因果；选择一次可读全部原始记录与依据。" : "Nearby points group to avoid overlap, without implying a relationship or causality. Select once to read every original record and source."}</p>
    </ContextHelp></div>
      <p className="research-history-coverage" role="status">{copy.time}{value?.startAt ? <> · <time>{timeLabel(value.startAt)}</time> — <time>{timeLabel(value.endAt)}</time></> : null} · {busy ? `${copy.reading} ${loaded}${read.total ? ` / ${read.total}` : ""}`
        : read.total === null ? (zh ? "来源尚未读到" : "Source has not been read")
          : `${read.events.length} / ${read.total} ${copy.records}${read.complete ? "" : ` · ${copy.partial}`}`}</p></div>
      <div className="research-history-controls">
        {selection.nodes.length || selection.day !== null ? <button type="button" onClick={() => {choose("", false); graph.current?.focus();}}><ArrowLeft size={14} aria-hidden/>{copy.all}</button> : null}
        <div className="research-lens" role="group" aria-label={zh ? "视图" : "View"}>{(["timeline", "graph"] as const).map(item => <button key={item} type="button" aria-pressed={lens === item} onClick={() => chooseLens(item)}>{item === "timeline" ? (zh ? "时间线" : "Timeline") : (zh ? "关系图" : "Graph")}</button>)}</div>
        <ResearchAnalysisButton analysis={analysis}/>
        <button type="button" aria-label={copy.refresh} disabled={busy} onClick={() => setRevision(n => n + 1)}><RefreshCw size={15} aria-hidden/></button>
      </div></header>
    {error || history.error ? <p role="alert" className="research-history-error">{copy.failure} <small>{error || history.error}</small></p> : null}
    <ResearchAnalysisStatus analysis={analysis}/>
    <ResearchProgressSummary analysis={analysis}/>
    <ResearchAnalysisPanel analysis={analysis}/>
    {busy && !read.events.length ? <div className="research-history-loading" role="status">{copy.loading}…</div> : null}
    {value?.nodes.length ? <div className={`research-history-layout${selected || selectedMembers?.length ? " has-selection" : ""}`}>
      {lens === "timeline" && timeline ? <div className="research-history-canvas is-timeline" ref={graph} tabIndex={-1}>
        <GoalResearchTimeline timeline={timeline} zh={zh} selectedIds={selection.nodes} selectedDay={selection.day} dayGroups={grouped.groups} onGroupSelect={day => select({nodes: [], day})} phase={phase} onPhase={setPhase} onSelect={id => choose(id)} onSelectMany={nodes => select({nodes, day: null})} timeLabel={fullTime} dayLabel={dayTime}/>
      </div> : <div className="research-history-canvas" ref={graph} tabIndex={0} aria-label={zh ? "完整已载入研究关系图；可滚动" : "Full loaded research graph; scrollable"}>
        <div className="research-history-stage" style={{width, height}}>
          <svg width={width} height={height} aria-hidden="true" className="research-history-edges">
            <defs><marker id={`${presentationId}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M1,1 L7,4 L1,7" className="research-edge-arrow"/></marker></defs>
            {value.edges.map(edge => {
              const from = positions.get(edge.from_node); const to = positions.get(edge.to_node);
              if (!from || !to) return null;
              const forward = to.x > from.x;
              const x = from.x + (forward ? 206 : 0); const y = from.y + 52;
              const targetX = to.x + (forward ? 0 : 206); const targetY = to.y + 52;
              const bend = forward ? 24 : -24;
              return <g key={edge.event_id}><path className={edge.edge_type === "refutes" ? "is-refutation" : ""} markerEnd={`url(#${presentationId}-arrow)`}
                d={`M${x},${y} C${x + bend},${y} ${targetX - bend},${targetY} ${targetX},${targetY}`}><title>{relationLabel(edge.edge_type)}: {edge.summary}</title></path>
                <text x={(x + targetX) / 2} y={(y + targetY) / 2 - 6} textAnchor="middle">{relationLabel(edge.edge_type)}</text></g>;
            })}
            {layout.map(item => {
              const parent = positions.get(item.node.latest.parent_id);
              if (!parent || value.edges.some(edge => (edge.from_node === parent.node.id && edge.to_node === item.node.id) || (edge.to_node === parent.node.id && edge.from_node === item.node.id))) return null;
              return <path key={`parent:${item.node.id}`} className="is-grouping" d={`M${parent.x + 206},${parent.y + 47} C${parent.x + 226},${parent.y + 47} ${item.x - 20},${item.y + 47} ${item.x},${item.y + 47}`} />;
            })}
          </svg>
          {layout.map(({node, x, y}) => {
            const measured = measurementFeedback.find(item => item.node_id === node.id);
            return <button key={node.id} type="button" className={`research-history-node is-${node.latest.status}${selection.nodes.includes(node.id) ? " is-selected" : ""}`}
            style={{left: x, top: y}} aria-pressed={selection.nodes.includes(node.id)} aria-label={`${node.latest.title} · ${stateLabel(node.latest.status)}`} onClick={() => choose(node.id)}>
            <span className="research-node-meta">{node.latest.node_kind === "experiment" ? <FlaskConical size={13} aria-hidden/> : <Circle size={12} aria-hidden/>}{kindLabel(node.latest.node_kind)}<small>{stateLabel(node.latest.status)}</small></span>
            <strong>{node.latest.title}</strong>{measured ? <span className="research-node-count" title={`${measured.metric} (${measured.unit}) · ${measured.comparison_scope} · ${measured.evaluation_ref}`}>
              {measured.metric} {formatExploreMeasurementValue(measured.latest.measurement.value, zh)} · best {formatExploreMeasurementValue(measured.best?.value ?? null, zh)} {measured.higher_is_better ? "↑" : "↓"}
            </span> : <span className="research-node-count"><time>{timeLabel(node.updates[0]?.recorded_at)}</time>{node.updates.length > 1 ? ` · ${node.updates.length} ${copy.changes}` : ""}{node.findings.length ? ` · ${node.findings.length} ${copy.findings}` : ""}</span>}
          </button>})}
          {grouped.groups.map((group, index) => <button type="button" key={`day:${group.day}`} className={`research-history-node research-history-group${selectedGroup === group ? " is-selected" : ""}`} style={{left: (index % 4) * 230 + 24, top: clusterY + Math.floor(index / 4) * 124}}
            aria-pressed={selectedGroup === group} onClick={() => select({nodes: [], day: group.day})}>
            <span className="research-node-meta"><Circle size={12} aria-hidden/>{zh ? "未关联问题" : "Unlinked questions"}</span>
            <strong>{group.day ? `${group.day} UTC` : copy.unknown}</strong><span className="research-node-count">{group.nodes.length} {zh ? "个问题 · 展开全部" : "questions · read all"}</span>
          </button>)}
        </div>
      </div>}
      {selected ? <div className="research-history-detail" ref={detail} role="region" aria-label={selected.latest.title} tabIndex={-1} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); choose("", false); graph.current?.focus();}}}>
        <p className="research-detail-kind"><GitBranch size={14} aria-hidden/>{kindLabel(selected.latest.node_kind)} · {stateLabel(selected.latest.status)}</p>
        <h3>{selected.latest.title}</h3><p>{selected.latest.summary || (zh ? "此节点尚无详细说明。" : "No detail was recorded for this node.")}</p>
        {selected.latest.blocked_reason ? <p className="research-history-error">{selected.latest.blocked_reason}</p> : null}
        {measurementSummary(selected.id)}
        <h4>{copy.evidence}</h4>
        {selected.findings.length ? selected.findings.map(findingElement) : <p>{zh ? "此节点没有附属结论记录。" : "No finding is attached to this node."}</p>}
        <h4>{copy.versions}</h4>{selected.latest.evidence_refs.length ? selected.latest.evidence_refs.map(ref => <code key={ref}>{ref}</code>) : <p>{zh ? "产物版本与来源引用未记录。" : "Artifact version and source references were not recorded."}</p>}
        <h4>{copy.links}</h4><ul>{value.edges.filter(edge => edge.from_node === selected.id || edge.to_node === selected.id).map(edge => <li key={edge.event_id}>{positions.get(edge.from_node)?.node.latest.title ?? (zh ? "来源节点未记录" : "Source node not recorded")} → {relationLabel(edge.edge_type)} → {positions.get(edge.to_node)?.node.latest.title ?? (zh ? "目标节点未记录" : "Target node not recorded")}{edge.summary ? ` — ${edge.summary}` : ""}</li>)}</ul>
        {selected.latest.parent_id ? <p>{copy.group} · {positions.get(selected.latest.parent_id)?.node.latest.title ?? "—"}</p> : null}
        <h4>{copy.updates}</h4>{updateRecords(selected.revisions)}
      </div> : null}
      {selectedMembers?.length ? <div className="research-history-detail" ref={detail} role="region" aria-label={selectedGroup ? (zh ? "按日期收纳的未关联问题" : "Unlinked questions by date") : (zh ? "相邻研究项的全部记录" : "All records for nearby research items")} tabIndex={-1} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); choose("", false); graph.current?.focus();}}}>
        <h3>{selectedGroup ? `${selectedGroup.day || copy.unknown} · ${zh ? "未关联问题" : "Unlinked questions"}` : (zh ? `相邻研究项 · ${selectedMembers.length}` : `Nearby research items · ${selectedMembers.length}`)}</h3>
        {selectedMembers.map(node => <article className="research-history-group-member" key={node.id} data-research-node={node.id}><h4>{node.latest.title}</h4><p>{stateLabel(node.latest.status)} · {node.latest.summary}</p>{node.latest.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}
          {measurementSummary(node.id)}
          {node.findings.map(findingElement)}
          {updateRecords(node.revisions)}
        </article>)}
      </div> : null}
    </div> : null}
    {(selection.nodes.some(id => !value?.nodes.some(node => node.id === id)) || (selection.day !== null && !selectedGroup)) && read.complete && !busy && !error && !history.error ? <p role="status">{copy.lost}</p> : null}
    {value?.events.length && !value.nodes.length ? <p className="research-history-note">{zh ? "尚未记录节点结构；可展开下面的已读记录。" : "Node structure has not been recorded; loaded records remain available below."}</p> : null}
    {value?.missingLinks.length ? <p className="research-history-note">{copy.missing} · {value.missingLinks.length}</p> : null}
    {value?.unknownTimeCount ? <p className="research-history-note">{copy.unknown} · {value.unknownTimeCount}</p> : null}
    {value?.unknownKindCount ? <p className="research-history-note">{zh ? "未识别类型保留于已读记录" : "Unrecognized types remain in loaded records"} · {value.unknownKindCount}</p> : null}
    {value?.events.length ? <details className="research-history-all-records" open={recordsOpen} onToggle={event => setRecordsOpen(event.currentTarget.open)}><summary>{zh ? "全部已读记录" : "All loaded records"} · {value.events.length}</summary>
      {recordsOpen ? <><ol className="research-history-records">{value.events.slice(0, recordLimit).map(event => <li key={event.event_id}><time>{timeLabel(event.recorded_at)}</time><span>{event.title} · {stateLabel(event.status)}{event.summary ? ` — ${event.summary}` : ""}</span>{event.parent_id ? <code>{copy.group}: {event.parent_id}</code> : null}{event.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}{event.run_id ? <code>{event.run_id}</code> : null}</li>)}</ol>
        {recordLimit < value.events.length ? <button type="button" onClick={() => setRecordLimit(n => n + 50)}>{zh ? "继续展开记录" : "Show more records"} · {Math.min(recordLimit, value.events.length)} / {value.events.length}</button> : null}</> : null}
    </details> : null}
  </section>;
}
