import {exploreFindingCopy} from "./explore-finding-copy";
import {AlertCircle, ArrowLeft, ArrowRight, Circle, CircleHelp, FlaskConical, GitBranch, RefreshCw, Search} from "lucide-react";
import {Fragment, useEffect, useId, useLayoutEffect, useMemo, useRef, useState} from "react";
import {defaultParseSearch} from "@tanstack/react-router";
import {ResearchRecordSearch} from "./research-record-browser";
import {ResearchAnalysisButton, ResearchAnalysisPanel, ResearchAnalysisStatus, ResearchCitationPanel, ResearchProgressSummary, ResearchReportSummary, useResearchAnalysis} from "./goal-research-analysis";
import {buildExploreTimeline, parseExploreFindingSections, type TimelineFinding} from "../../../../../../loopx/control_plane/presentation/explore_timeline.js";
import {GoalResearchTimeline} from "./goal-research-timeline";
import {requestJson} from "../../data/chat";
import {sourceReadApiUrl} from "../../data/local-status-query";
import {ContextHelp} from "./context-help";
import {groupUnlinkedHistoryQuestions, layoutExploreHistory, parseExploreHistoryPage, projectExploreHistory, type ExploreHistoryEvent} from "../../../../../../loopx/control_plane/presentation/explore_history.js";
import "./goal-research-history.css";
import {ExploreMeasurements, formatExploreMeasurementValue} from "./explore-measurements";
import {projectTrajectoryDisplay, trajectoryDisplaySnapshotText} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_display.js";

type Selection = {nodes: string[]; day: string | null; finding?: string};
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
  return {nodes: day === null ? [...new Set(nodes.filter(Boolean))] : [], day, finding: query.get("research_finding") || undefined};
};

const viewFromUrl = (goal: string) => {
  const query = new URLSearchParams(window.location.search);
  const sameGoal = !query.has("research_goal") || query.get("research_goal") === goal;
  const phase = query.get("research_phase");
  return {
    lens: sameGoal && query.get("research_lens") === "graph" ? "graph" as const : "timeline" as const,
    phase: sameGoal && phase !== null && /^[0-2]$/.test(phase) ? Number(phase) : null,
  };
};

/** An effect-free history consumer. Its source is Explore, not raw sessions or
 * a judge. All recorded updates survive; unsupported analysis stays unavailable. */
export function GoalResearchHistory({goalId, active, zh, refreshRevision = 0, readSourceUrl}: {goalId: string; active: boolean; zh: boolean; refreshRevision?: number; readSourceUrl?: string}) {
  const analysis = useResearchAnalysis(goalId, active, zh, readSourceUrl);
  const [read, setRead] = useState<Read>({events: [], total: null, complete: false});
  const [originalDisplay, setOriginalDisplay] = useState(false);
  const [displayIdentity, setDisplayIdentity] = useState<{events: ExploreHistoryEvent[]; namespace: string; key: string} | null>(null);
  const displayNamespace = readSourceUrl ? new URL(readSourceUrl, window.location.href).searchParams.get("source_namespace") ?? "local" : "local";
  const display = analysis.pack?.result?.display;
  const displayHashSupported = Boolean(globalThis.crypto?.subtle);
  useEffect(() => {
    if (!read.complete || !display || !displayHashSupported) return;
    let current = true;
    const bytes = new TextEncoder().encode(trajectoryDisplaySnapshotText(read.events, goalId, displayNamespace));
    void crypto.subtle.digest("SHA-256", bytes).then(hash => {
      if (current) setDisplayIdentity({events: read.events, namespace: displayNamespace,
        key: Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, "0")).join("")});
    }).catch(() => {if (current) setDisplayIdentity({events: read.events, namespace: displayNamespace, key: ""});});
    return () => {current = false;};
  }, [read, goalId, displayNamespace, display?.snapshot_key, displayHashSupported]);
  const displayKey = displayIdentity?.events === read.events && displayIdentity.namespace === displayNamespace ? displayIdentity.key : "";
  const displayCheckFailed = !displayHashSupported || (displayIdentity?.events === read.events && displayIdentity.namespace === displayNamespace && !displayIdentity.key);
  const currentDisplay = analysis.pack?.applicability === "current" && analysis.pack.scope.goal_id === goalId
    && analysis.pack.result?.analysis_key === analysis.pack.analysis_key && analysis.pack.result.method_version === analysis.pack.method_version
    && JSON.stringify(analysis.pack.result.scope) === JSON.stringify(analysis.pack.scope)
    && display?.snapshot_key === displayKey && display.source_namespace === displayNamespace && display.language === (zh ? "zh" : "en") ? display : undefined;
  const originals = useMemo(() => new Map(read.events.map(event => [event.event_id, event])), [read.events]);
  const displayProjection = useMemo(() => projectTrajectoryDisplay(read.events, originalDisplay ? undefined : currentDisplay, displayKey, zh ? "zh" : "en"),
    [read.events, currentDisplay, displayKey, originalDisplay, zh]);
  const findingSections = (event: ExploreHistoryEvent) => currentDisplay && !originalDisplay
    ? displayProjection.sections.get(event.event_id) ?? null : parseExploreFindingSections(event.summary);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState(() => selectedFromUrl(goalId));
  // Pinning the displayed finding must not collapse a newly opened question.
  const [questionExpanded, setQuestionExpanded] = useState(() => !selectedFromUrl(goalId).finding);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const records = useRef<HTMLDetailsElement>(null);
  const [lens, setLens] = useState<Lens>(() => viewFromUrl(goalId).lens);
  const [phase, setPhase] = useState<number | null>(() => viewFromUrl(goalId).phase);
  const presentationId = useId().replace(/:/g, "");
  const detail = useRef<HTMLDivElement>(null);
  const graph = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const readerParent = useRef<{selection: Selection; nodeId: string; opener: HTMLElement | null} | null>(null);
  const focusPending = useRef(Boolean(selection.nodes.length || selection.day !== null));
  const focusReturn = useRef<{nodeId?: string; element: HTMLElement | null} | null>(null);
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
  const findingCopy = exploreFindingCopy(zh);
  const stateLabels: Record<string, string> = zh ? {open: "待探索", exploring: "探索中", blocked: "受阻", resolved: "已收束", dead_end: "已排除", ...findingCopy.labels} : {open: "Open", exploring: "Exploring", blocked: "Blocked", resolved: "Resolved", dead_end: "Ruled out", ...findingCopy.labels};
  const relationLabels: Record<string, string> = zh ? {subtopic_of: "属于", depends_on: "依赖", answers: "回答", supports: "支持", refutes: "反驳", leads_to: "引向"} : {subtopic_of: "Part of", depends_on: "Depends on", answers: "Answers", supports: "Supports", refutes: "Refutes", leads_to: "Leads to"};
  const kindLabel = (kind: string) => kindLabels[kind] ?? kind;
  const stateLabel = (state: string) => stateLabels[state] ?? state;
  const relationLabel = (type: string) => relationLabels[type] ?? type;
  const timeLabel = (value: string | null) => value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(new Date(value)) : copy.unknown;

  const sectionCopy = zh ? {inputRevision: "输入版本", applicability: "适用范围", observation: "观察", interpretation: "解读", changed: "变化", stopped: "停止", retained: "保留"}
    : {inputRevision: "Input revision", applicability: "Applicability", observation: "Observation", interpretation: "Interpretation", changed: "Changed", stopped: "Stopped", retained: "Retained"};
  const outcomeCopy: Record<string, string> = zh ? {replan: "转向", continue: "继续", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定"} : {replan: "Replan", continue: "Continue", stop: "Stop", wait: "Wait", no_change: "No change", ask_human: "Ask human"};
  const findingNarrative = (finding: ExploreHistoryEvent) => {
    const sections = findingSections(finding);
    const decision = sections?.decision;
    return sections ? <dl className="research-finding-sections">
      <dt>{sectionCopy.observation}</dt><dd>{sections.observation}</dd>
      {decision ? <>
        <dt>{zh ? "决策" : "Decision"}</dt><dd><b className={`research-outcome is-${decision.outcome}`}>{outcomeCopy[decision.outcome] ?? decision.outcome}</b></dd>
        {(["changed", "stopped", "retained"] as const).map(key => decision[key].length ? <Fragment key={key}><dt>{sectionCopy[key]}</dt><dd key={key}><ul>{decision[key].map(item => <li key={item}>{item}</li>)}</ul></dd></Fragment> : null)}
      </> : <><dt>{sectionCopy.interpretation}</dt><dd>{sections.interpretation}</dd></>}
    </dl> : <p>{finding.summary}</p>;
  };
  const findingBody = (finding: ExploreHistoryEvent) => {
    const execution = finding.execution_observation;
    const measured = (value: unknown) => value === null ? (zh ? "未测量" : "Not measured") : String(value);
    return <>{finding.measurements ? <ExploreMeasurements measurements={finding.measurements} zh={zh}/> : null}{findingNarrative(finding)}{execution ? <details><summary>{zh ? "实际执行与资源观测" : "Execution and resource observations"}</summary>
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
  const findingMeta = (finding: ExploreHistoryEvent) => <small>{timeLabel(finding.recorded_at)} · {stateLabel(finding.status)}{finding.confidence !== null ? ` · ${zh ? "来源自报置信度" : "Source-reported confidence"} ${finding.confidence}` : ""}</small>;
  const findingElement = (finding: ExploreHistoryEvent) => {
    const sections = findingSections(finding);
    const content = <>{findingBody(finding)}{sections || finding.evidence_refs.length ? <details className="research-finding-sources"><summary>{zh ? "来源与适用范围" : "Sources and applicability"}{finding.evidence_refs.length ? ` · ${finding.evidence_refs.length}` : ""}</summary>
      {sections ? <dl className="research-finding-sections"><dt>{sectionCopy.applicability}</dt><dd>{sections.applicability}</dd><dt>{sectionCopy.inputRevision}</dt><dd><code>{sections.inputRevision}</code></dd></dl> : null}
      {finding.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}
      <details><summary>{zh ? "完整原文" : "Full original text"}</summary><h4>{originals.get(finding.event_id)?.title ?? finding.title}</h4><p>{originals.get(finding.event_id)?.summary ?? finding.summary}</p></details></details> : null}</>;
    return <article className="research-recorded-finding" key={finding.event_id}>
      <h3>{finding.title}</h3>{findingMeta(finding)}{content}
    </article>;
  };
  const updateRecords = (events: readonly ExploreHistoryEvent[]) => <ol className="research-history-records">{events.map(event => <li key={event.event_id}><time>{timeLabel(event.recorded_at)}</time><div><p>{stateLabel(event.status)} · {event.title}</p>{event.summary ? findingBody(event) : null}</div>{event.parent_id ? <code>{copy.group}: {event.parent_id}</code> : null}{event.evidence_refs.map(ref => <code key={ref}>{ref}</code>)}{event.run_id ? <code>{event.run_id}</code> : null}</li>)}</ol>;

  useEffect(() => {
    const pop = () => {
      readerParent.current = null;
      focusReturn.current = null;
      focusPending.current = true;
      const next = selectedFromUrl(goalId);
      setSelection(next);
      setQuestionExpanded(!next.finding);
      const view = viewFromUrl(goalId); setLens(view.lens); setPhase(view.phase);
    };
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
    try {
      const canonical = projectExploreHistory(read.events, goalId);
      return {canonical, value: displayProjection.events === read.events ? canonical : projectExploreHistory(displayProjection.events, goalId), error: ""};
    }
    catch (failure) {return {canonical: null, value: null, error: String(failure)};}
  }, [read.events, displayProjection, goalId]);
  const value = history.value;
  const measurementFeedback = value?.measurementFeedback ?? [];
  const measurementSummary = (nodeId: string) => {
    const feedback = measurementFeedback.filter(item => item.node_id === nodeId);
    return feedback.length ? <ExploreMeasurements title={zh ? "最近记录与历史最佳" : "Latest recorded and historical best"}
      measurements={feedback.map(item => item.latest.measurement)} feedback={feedback} zh={zh}/> : null;
  };
  const grouped = useMemo(() => groupUnlinkedHistoryQuestions(value?.nodes ?? [], value?.edges ?? []), [value]);
  const layout = useMemo(() => layoutExploreHistory(grouped.nodes, value?.edges ?? []), [grouped, value]);
  const timeline = useMemo(() => {
    if (!history.canonical || !value) return null;
    const original = buildExploreTimeline(history.canonical.nodes, history.canonical.edges, history.canonical.events);
    if (!currentDisplay || originalDisplay) return original;
    const events = new Map(value.events.map(event => [event.event_id, event]));
    const nodes = new Map(value.nodes.map(node => [node.id, node]));
    const finding = (item: TimelineFinding): TimelineFinding => ({...item, event: events.get(item.event.event_id)!,
      sections: displayProjection.sections.get(item.event.event_id) ?? null});
    return {...original, nodes: new Map([...original.nodes].map(([id, item]) => [id, {...item, node: nodes.get(id)!}])),
      findings: original.findings.map(finding), decisions: original.decisions.map(finding),
      phases: original.phases.map(phase => ({...phase, lastDecision: phase.lastDecision ? finding(phase.lastDecision) : null}))};
  }, [history.canonical, value, displayProjection, currentDisplay, originalDisplay]);
  const chooseView = (next: Lens, nextPhase: number | null) => {
    setLens(next); setPhase(nextPhase);
    const url = new URL(window.location.href);
    url.searchParams.set("research_goal", goalId);
    url.searchParams.set("research_lens", next);
    if (nextPhase === null) url.searchParams.delete("research_phase");
    else url.searchParams.set("research_phase", String(nextPhase));
    if (next !== lens || nextPhase !== phase) window.history.pushState(null, "", url);
  };
  const chooseLens = (next: Lens) => chooseView(next, phase);
  const fullTime = (at: number) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(at));
  const dayTime = (at: number) => new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {month: "numeric", day: "numeric"}).format(new Date(at));
  const positions = new Map(layout.map(item => [item.node.id, item]));
  const selectedId = selection.nodes.length === 1 ? selection.nodes[0]! : "";
  const selected = value?.nodes.find(node => node.id === selectedId);
  const selectedGroup = selection.day !== null ? grouped.groups.find(group => group.day === selection.day) : undefined;
  const selectedMembers = selectedGroup?.nodes ?? (selection.nodes.length > 1 ? value?.nodes.filter(node => selection.nodes.includes(node.id)) : undefined);
  useLayoutEffect(() => {
    if (!active) return;
    if (focusReturn.current) {
      const {nodeId, element} = focusReturn.current;
      // The full-history view moves while reading. Resolve its remounted
      // control by the local return key rather than losing keyboard position.
      const returnKey = element?.dataset.researchReturn;
      const target = nodeId ? detail.current?.querySelector<HTMLButtonElement>(`[data-research-node="${CSS.escape(nodeId)}"] button`)
        : element?.isConnected ? element : returnKey ? graph.current?.querySelector<HTMLButtonElement>(`[data-research-return="${CSS.escape(returnKey)}"]`) : null;
      focusReturn.current = null;
      if (target?.isConnected) {target.focus({preventScroll: true}); target.scrollIntoView({block: "nearest"});}
      else graph.current?.focus();
      return;
    }
    if (focusPending.current && detail.current) {
      focusPending.current = false;
      detail.current.focus({preventScroll: true});
      detail.current.scrollIntoView({block: "start"});
    }
  }, [active, selected, selectedMembers]);
  const clusterY = layout.length ? Math.max(...layout.map(item => item.y + 124)) + 24 : 24;
  const width = Math.max(600, grouped.groups.length ? 944 : 0, ...layout.map(item => item.x + 230));
  const height = Math.max(230, ...layout.map(item => item.y + 124), grouped.groups.length ? clusterY + Math.ceil(grouped.groups.length / 4) * 124 : 0);
  const select = (next: Selection, focus = true) => {
    if (focus && (next.nodes.length || next.day !== null)) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      focusPending.current = true;
    }
    setSelection(next);
    setQuestionExpanded(!next.finding);
    const url = new URL(window.location.href);
    url.searchParams.delete("research_node"); url.searchParams.delete("research_group"); url.searchParams.delete("research_finding");
    if (next.nodes.length || next.day !== null) {
      if (next.day !== null) url.searchParams.set("research_group", next.day);
      else url.searchParams.set("research_node", next.nodes.length === 1 ? next.nodes[0]! : JSON.stringify(next.nodes));
      if (next.finding) url.searchParams.set("research_finding", next.finding);
      url.searchParams.set("research_goal", goalId);
    }
    else url.searchParams.set("research_goal", goalId);
    if (selection.finding !== next.finding || selection.day !== next.day || selection.nodes.length !== next.nodes.length || selection.nodes.some((id, index) => id !== next.nodes[index])) window.history.pushState(null, "", url);
  };
  const choose = (id: string, focus = true, finding?: string) => {readerParent.current = null; select({nodes: id ? [id] : [], day: null, finding}, focus);};
  const closeReader = () => {
    const parent = readerParent.current;
    readerParent.current = null;
    focusPending.current = false;
    focusReturn.current = {nodeId: parent?.nodeId, element: opener.current};
    if (parent) opener.current = parent.opener;
    select(parent?.selection ?? {nodes: [], day: null}, false);
  };
  const resultsAnchor = useRef<HTMLDivElement>(null), reportAnchor = useRef<HTMLDivElement>(null), historyAnchor = useRef<HTMLElement>(null);
  const reportEntries = useRef(new Set<string>());
  const reportScope = JSON.stringify([goalId, readSourceUrl ?? "", analysis.pack?.pack_id ?? analysis.location.packId]);
  useLayoutEffect(() => {
    if (!active || !analysis.location.open || reportEntries.current.has(reportScope)) return;
    // A record/finding link owns its focus, even while history is still loading.
    if (selection.nodes.length || selection.day !== null) {reportEntries.current.add(reportScope); return;}
    if (analysis.loading && !analysis.pack && !analysis.readError) return;
    const target = analysis.panel.current;
    if (!target) return;
    // Resolve a saved report link once per reading context. Explicit reopening
    // keeps its existing action focus; reads and tab restoration must not rearm it.
    reportEntries.current.add(reportScope);
    // A user may have selected a source while this report was loading.
    // URL restoration must not take focus from that active control.
    if (document.activeElement && document.activeElement !== document.body && document.activeElement !== target) return;
    target.focus({preventScroll: true});
    target.scrollIntoView({block: "start"});
  }, [active, reportScope, analysis.location.open, analysis.loading, analysis.pack, analysis.readError, analysis.panel, selection.nodes.length, selection.day]);
  const readingSelection = Boolean(selected || selectedMembers?.length);
  const selectedFinding = selected?.findings.find(finding => finding.result_id === selection.finding)
    ?? (!selection.finding ? selected?.findings.at(-1) : undefined);
  const selectedEdges = selected ? value?.edges.filter(edge => edge.from_node === selected.id || edge.to_node === selected.id) ?? [] : [];
  const selectedContextCitations = [...new Map((originalDisplay ? [] : currentDisplay?.fields ?? [])
    .filter(field => field.event_id === selected?.latest.event_id || field.event_id === selectedFinding?.event_id)
    .flatMap(field => field.context_citations ?? []).map(citation => [JSON.stringify(citation), citation])).values()];
  const findingExceptions = selected ? [...new Set(selected.findings.map(finding => finding.status))]
    .filter(status => status !== "confirmed").map(status => `${stateLabel(status)} ${selected.findings.filter(finding => finding.status === status).length}`).join(" · ") : "";
  // Pin the first displayed finding to its stable identity. New source records
  // must not move a reader, and reload/Back must restore that same evidence.
  useEffect(() => {
    if (!active || selection.finding || !selectedFinding || !selected) return;
    const finding = selectedFinding.result_id;
    setSelection(previous => ({...previous, finding}));
    const url = new URL(window.location.href);
    url.searchParams.set("research_finding", finding);
    window.history.replaceState(null, "", url);
  }, [active, selection.finding, selectedFinding, selected]);
  const displayControl = display ? <div className="research-display-language">
      <button type="button" disabled={!currentDisplay} aria-pressed={originalDisplay} onClick={() => setOriginalDisplay(value => !value)}>
        {originalDisplay ? (zh ? "查看译文" : "Show translation") : (zh ? "查看原文" : "Show originals")}
      </button>
      {!currentDisplay ? <span role="status">{displayCheckFailed ? (zh ? "无法核对译文，显示原文" : "Translation cannot be checked; showing originals") : !displayKey ? (zh ? "正在核对译文版本" : "Checking translation version") : (zh ? "译文版本不适用，显示原文" : "Translation is out of scope; showing originals")}</span>
        : currentDisplay.coverage.missing ? <span role="status">{zh ? "译文" : "Translated"} {currentDisplay.coverage.translated} / {currentDisplay.coverage.eligible}</span> : null}
      <ContextHelp label={zh ? "译文覆盖与来源" : "Translation coverage and source"}>
        <p>{zh ? "机器译文，未经独立校对。原始状态、数值、引用与证据保持不变，缺失或校验未通过的字段显示原文。" : "Model translation, not independently checked. Original statuses, numbers, references and evidence are preserved; missing or invalid fields retain their original text."}</p>
        <p>{zh ? "已译 / 快照字段" : "Translated / snapshot fields"} · {display.coverage.translated} / {display.coverage.eligible}
          {display.coverage.invalid ? ` · ${zh ? "校验未通过" : "Invalid"} ${display.coverage.invalid}` : ""}</p>
        <p>{zh ? "查看原文、切换语言和刷新只读；新快照须通过已有分析入口明确执行，使用同一分析任务与用量。" : "Original viewing, language switching and refresh only read. A new snapshot requires explicit execution through the existing analysis action, with the same job and usage."}</p>
      </ContextHelp>
    </div> : null;
  const historyView = !value ? null : lens === "timeline" && timeline ? <div className="research-history-canvas is-timeline" ref={graph} tabIndex={-1}>
        <GoalResearchTimeline timeline={timeline} zh={zh} selectedIds={selection.nodes} selectedDay={selection.day} dayGroups={grouped.groups} onGroupSelect={day => select({nodes: [], day})} phase={phase} onPhase={next => chooseView(lens, next)} onSelect={(id, finding) => choose(id, true, finding)} onSelectMany={nodes => select({nodes, day: null})} timeLabel={fullTime} dayLabel={dayTime}/>
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
      </div>;
  const progressive = Boolean(analysis.pack?.result?.synthesis);
  const progressiveOverview = progressive && !readingSelection;
  const jump = (target: HTMLElement | null) => {target?.focus({preventScroll: true}); target?.scrollIntoView({block: "start"});};
  const showHistory = () => {analysis.close(); requestAnimationFrame(() => jump(detail.current ?? historyAnchor.current));};
  const refresh = () => {
    setRevision(n => n + 1);
    void analysis.read().catch(() => undefined);
  };
  const focusResearchMode = () => {
    const control = document.querySelector<HTMLElement>("[data-testid='goal-research-mode']");
    control?.scrollIntoView({block: "nearest"});
    control?.querySelector<HTMLElement>("button:not(:disabled)")?.focus({preventScroll: true});
  };
  const readFailure = error || history.error;
  return <section className="research-history" aria-label={copy.title} aria-busy={busy}>
    {progressiveOverview ? <>
      <nav className="research-reading-order" aria-label={zh ? "研究阅读" : "Research reading"}>
        <button type="button" onClick={() => jump(resultsAnchor.current)}>{zh ? "指标" : "Results"}</button><span aria-hidden>→</span>
        <button type="button" onClick={() => jump(reportAnchor.current)}>{zh ? "汇报" : "Report"}</button><span aria-hidden>→</span>
        <button type="button" onClick={showHistory}>{zh ? "研究历程" : "Research history"}</button>
      </nav>
      <ResearchAnalysisStatus analysis={analysis}/>
      <div ref={resultsAnchor} tabIndex={-1} className="research-reading-layer"><ResearchProgressSummary analysis={analysis} includeReport={false}/></div>
      <div ref={reportAnchor} tabIndex={-1} className="research-reading-layer"><ResearchReportSummary analysis={analysis}/><ResearchAnalysisPanel analysis={analysis} onHistory={showHistory}/></div>
      <ResearchCitationPanel analysis={analysis}/>
    </> : null}
    <header className="research-history-heading" ref={historyAnchor} tabIndex={-1}><div><div className="research-history-title"><h2>{copy.title}</h2><ContextHelp label={zh ? "历史范围与读图说明" : "History scope and reading guide"}>
      <p>{copy.scope} · {copy.time}{zh ? "。" : ". "}{copy.coverage}{zh ? "。" : "."}</p><p>{copy.interpretation}{zh ? "。" : "."}</p>
      {lens === "graph" ? <p>{zh ? "按来源分组，实线为已记录关联，虚线仅表示分组；排列不代表时间或进展。未关联问题按首次记录 UTC 日期收纳。" : "Grouped by source. Solid lines are recorded relationships; dotted lines only group nodes. Position does not imply time or progress. Unlinked questions are collected by first-record UTC date."}</p> : null}
      <p>{zh ? "合拢仅为避免遮挡，不表示关联或因果；选择一次可读全部原始记录与依据。" : "Nearby points group to avoid overlap, without implying a relationship or causality. Select once to read every original record and source."}</p>
    </ContextHelp></div>
      </div>
      <div className={`research-history-controls${readingSelection ? " is-reading" : ""}`}>
        <button type="button" className="research-history-search" aria-label={zh ? "搜索研究记录" : "Search research records"} disabled={!value?.events.length} onClick={() => {
          setRecordsOpen(true);
          window.requestAnimationFrame(() => {
            records.current?.querySelector<HTMLInputElement>("input")?.focus({preventScroll: true});
            records.current?.scrollIntoView({block: "start"});
          });
        }}><Search size={15} aria-hidden/></button>
        {!readingSelection ? <div className="research-lens" role="group" aria-label={zh ? "视图" : "View"}>{(["timeline", "graph"] as const).map(item => <button key={item} type="button" aria-pressed={lens === item} onClick={() => chooseLens(item)}>{item === "timeline" ? (zh ? "时间线" : "Timeline") : (zh ? "关系图" : "Graph")}</button>)}</div> : null}
        <div className="research-analysis-scoped-action">{!progressiveOverview ? <ResearchAnalysisButton analysis={analysis}/> : null}
          {readingSelection && analysis.pack ? <span className="research-analysis-scope">{zh ? "分析范围" : "Analysis scope"} · {analysis.pack.title}</span> : null}
        </div>
        <button type="button" aria-label={copy.refresh} disabled={busy} onClick={refresh}><RefreshCw size={15} aria-hidden/></button>
      </div></header>
    <div className="research-history-meta">
      <p className="research-history-coverage" role="status">{copy.time}{value?.startAt ? <> · <time>{timeLabel(value.startAt)}</time> — <time>{timeLabel(value.endAt)}</time></> : null} · {busy ? `${copy.reading} ${loaded}${read.total ? ` / ${read.total}` : ""}`
        : read.total === null ? (zh ? "来源尚未读到" : "Source has not been read")
          : `${read.events.length} / ${read.total} ${copy.records}${read.complete ? "" : ` · ${copy.partial}`}`}</p>
      {!readingSelection ? displayControl : null}
    </div>
    {readFailure ? <div className="research-history-read-error" role="alert">
      <AlertCircle size={18} aria-hidden/>
      <div><strong>{read.events.length ? (zh ? "刷新失败，当前记录已保留" : "Refresh failed; current records are preserved") : copy.failure}</strong>
        <ContextHelp label={zh ? "研究记录读取详情" : "History read details"}><p>{readFailure}</p></ContextHelp>
      </div>
      <button type="button" disabled={busy} onClick={refresh}><RefreshCw size={14} aria-hidden/>{zh ? "重试" : "Retry"}</button>
    </div> : null}
    {!busy && !readFailure && read.total === 0 ? <div className="research-history-empty">
      <div role="status"><strong>{zh ? "这个 Goal 尚无研究历程" : "No research history for this Goal yet"}</strong><ContextHelp icon={FlaskConical} label={zh ? "研究历程来源" : "Research history source"}><p>{zh ? "开启研究模式后，后续研究问题、尝试与证据会在这里形成可回读的历程。" : "Enable research mode to build a readable history of questions, attempts and evidence from later work."}</p></ContextHelp></div>
      <button type="button" onClick={focusResearchMode}>{zh ? "查看研究模式" : "Review research mode"}</button>
    </div> : null}
    {!progressiveOverview ? <><ResearchAnalysisStatus analysis={analysis}/>
      {!readingSelection ? <ResearchProgressSummary analysis={analysis}/> : null}
      <ResearchAnalysisPanel analysis={analysis} onHistory={showHistory}/><ResearchCitationPanel analysis={analysis}/></> : null}
    {busy && !read.events.length ? <div className="research-history-loading" role="status" aria-label={copy.loading}>
      <span className="research-history-loading-label">{copy.loading}…</span>
      <div className="research-history-loading-phases" aria-hidden>{Array.from({length: 3}, (_, index) => <span key={index}/>)}</div>
      <div className="research-history-loading-chart" aria-hidden>{Array.from({length: 18}, (_, index) => <span key={index}/>)}</div>
    </div> : null}
    {value?.nodes.length ? <div className={`research-history-layout${selected || selectedMembers?.length ? " has-selection" : ""}`}>

      {selected ? <div className="research-history-detail" ref={detail} role="region" aria-label={selected.latest.title} tabIndex={-1} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); closeReader();}}}>
        <div className="research-reader-content">
        <div className="research-reader-location"><button type="button" className="research-reader-back" onClick={closeReader}><ArrowLeft size={14} aria-hidden/>{readerParent.current ? (zh ? "返回问题列表" : "Back to questions") : copy.all}</button>
        <div className="research-reader-states">
          <div className="research-detail-kind"><GitBranch size={14} aria-hidden/>{kindLabel(selected.latest.node_kind)} · {stateLabel(selected.latest.status)} <ContextHelp label={findingCopy.help}><p>{findingCopy.meaning}</p></ContextHelp></div>
          {selected.findings.length > 1 && findingExceptions ? <p className="research-finding-exceptions">{findingExceptions}</p> : null}
        </div></div>
        {selected.findings.length ? selectedFinding?.title !== selected.latest.title ? <details className="research-reader-question" key={selected.id} open={questionExpanded} onToggle={event => setQuestionExpanded(event.currentTarget.open)}>
          <summary><span>{selected.latest.title}</span></summary>
          {selected.latest.summary ? <p>{selected.latest.summary}</p> : null}
        </details> : null : <h3>{selected.latest.title}</h3>}{displayControl}
        {selected.latest.blocked_reason ? <p className="research-history-error">{selected.latest.blocked_reason}</p> : null}
        {selected.findings.length ? <>
          {selected.findings.length > 1 ? <nav className="research-finding-navigation" aria-label={zh ? "问题结论" : "Question findings"}>
            <label><select aria-label={zh ? "选择问题结论" : "Choose a question finding"} value={selectedFinding?.result_id ?? ""}
                onChange={event => select({...selection, finding: event.target.value}, false)}>
                {!selectedFinding ? <option value="" disabled>{zh ? "所选结论未找到" : "Selected finding not found"}</option> : null}
                {selected.findings.map((finding, index) => <option key={finding.result_id} value={finding.result_id}>
                  {index + 1}. {finding.title} · {stateLabel(finding.status)} · {timeLabel(finding.recorded_at)}
                </option>)}
              </select>
            </label>
            <div className="research-finding-steps">
              <span>{selectedFinding ? selected.findings.indexOf(selectedFinding) + 1 : "—"} / {selected.findings.length}</span>
              {([-1, 1] as const).map(step => {
                const next = selectedFinding ? selected.findings[selected.findings.indexOf(selectedFinding) + step] : undefined;
                return <button key={step} type="button" disabled={!next} aria-label={step < 0 ? (zh ? "上一条结论" : "Previous finding") : (zh ? "下一条结论" : "Next finding")}
                  onClick={() => {if (next) select({...selection, finding: next.result_id}, false);}}>
                  {step < 0 ? <ArrowLeft size={16} aria-hidden/> : <ArrowRight size={16} aria-hidden/>}
                </button>;
              })}
            </div>
          </nav> : null}
          {selectedFinding ? findingElement(selectedFinding) : <p role="status">{zh ? "此记录中找不到所选结论；可从列表选择其他结论。" : "The selected finding is absent from this snapshot. Other findings remain available."}</p>}
        </> : <>{selected.latest.summary ? <p>{selected.latest.summary}</p> : null}<ContextHelp label={zh ? "暂无附属结论" : "No attached findings"} icon={CircleHelp}><p className="research-history-note">{zh ? "此节点没有附属结论记录。" : "No finding is attached to this node."}</p></ContextHelp></>}
        <details className="research-node-provenance" key={selected.id}><summary>{zh ? "来源与历史" : "Sources and history"}</summary>
        {selectedContextCitations.length ? <><h4>{zh ? "译文依据" : "Translation context"}</h4>
          <div className="analysis-citations">{selectedContextCitations.map(citation => <button type="button" key={JSON.stringify(citation)}
            onClick={event => {void analysis.readCitation(citation, event.currentTarget);}}>
            {citation.source_id} · {zh ? "行" : "lines"} {citation.first_line}–{citation.last_line}
          </button>)}</div></> : null}
        {selected.findings.length && selectedFinding?.title === selected.latest.title && selected.latest.summary ? <p>{selected.latest.summary}</p> : null}
        {measurementSummary(selected.id)}
        <h4>{copy.versions}</h4>{selected.latest.evidence_refs.length ? selected.latest.evidence_refs.map(ref => <code key={ref}>{ref}</code>) : <p>{zh ? "产物版本与来源引用未记录。" : "Artifact version and source references were not recorded."}</p>}
        {selectedEdges.length ? <><h4>{copy.links}</h4><ul>{selectedEdges.map(edge => <li key={edge.event_id}>{positions.get(edge.from_node)?.node.latest.title ?? (zh ? "来源节点未记录" : "Source node not recorded")} → {relationLabel(edge.edge_type)} → {positions.get(edge.to_node)?.node.latest.title ?? (zh ? "目标节点未记录" : "Target node not recorded")}{edge.summary ? ` — ${edge.summary}` : ""}</li>)}</ul></> : null}
        {selected.latest.parent_id ? <p>{copy.group} · {positions.get(selected.latest.parent_id)?.node.latest.title ?? "—"}</p> : null}
        <h4>{copy.updates}</h4>{updateRecords(selected.revisions)}
        </details>
        </div>
      </div> : null}
      {selectedMembers?.length ? <div className="research-history-detail" ref={detail} role="region" aria-label={selectedGroup ? (zh ? "按日期收纳的未关联问题" : "Unlinked questions by date") : (zh ? "相邻研究项的全部记录" : "All records for nearby research items")} tabIndex={-1} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); closeReader();}}}>
        <div className="research-reader-content">
        <button type="button" className="research-reader-back" onClick={closeReader}><ArrowLeft size={14} aria-hidden/>{copy.all}</button>
        <h3>{selectedGroup ? `${selectedGroup.day || copy.unknown} · ${zh ? "未关联问题" : "Unlinked questions"}` : (zh ? `相邻研究项 · ${selectedMembers.length}` : `Nearby research items · ${selectedMembers.length}`)}</h3>
        {displayControl}
        <ol className="research-question-index">{selectedMembers.map(node => <li key={node.id} data-research-node={node.id}>
          <button type="button" onClick={() => {readerParent.current = {selection, nodeId: node.id, opener: opener.current}; select({nodes: [node.id], day: null});}}>
            <strong>{node.latest.title}</strong><span>{stateLabel(node.findings.at(-1)?.status ?? node.latest.status)}{node.latest.status === "blocked" && node.findings.length ? ` · ${stateLabel(node.latest.status)}` : ""}{node.findings.length ? ` · ${node.findings.length} ${copy.findings}` : ""}</span>
            {node.latest.blocked_reason ? <span className="research-history-error">{node.latest.blocked_reason}</span> : null}
          </button>
        </li>)}</ol>
        </div>
      </div> : null}
      <details className={`research-history-context${readingSelection ? "" : " is-overview"}`} open={!readingSelection || undefined} key={JSON.stringify([goalId, readSourceUrl])}>
        <summary hidden={!readingSelection}>{zh ? "研究全景" : "Research overview"}</summary>
        {readingSelection ? <ResearchProgressSummary analysis={analysis}/> : null}{historyView}
      </details>
    </div> : null}
    {(selection.nodes.some(id => !value?.nodes.some(node => node.id === id)) || (selection.day !== null && !selectedGroup)) && read.complete && !busy && !error && !history.error ? <p role="status">{copy.lost}</p> : null}
    {value?.events.length && !value.nodes.length ? <ContextHelp label={zh ? "节点结构未记录" : "Node structure not recorded"} icon={CircleHelp}><p className="research-history-note">{zh ? "尚未记录节点结构；可展开下面的已读记录。" : "Node structure has not been recorded; loaded records remain available below."}</p></ContextHelp> : null}
    {value && (value.missingLinks.length || value.unknownTimeCount || value.unknownKindCount) ? <ContextHelp icon={CircleHelp} label={zh ? "研究记录覆盖缺口" : "Research record coverage gaps"}>
    {value?.missingLinks.length ? <p className="research-history-note">{copy.missing} · {value.missingLinks.length}</p> : null}
    {value?.unknownTimeCount ? <p className="research-history-note">{copy.unknown} · {value.unknownTimeCount}</p> : null}
    {value?.unknownKindCount ? <p className="research-history-note">{zh ? "未识别类型保留于已读记录" : "Unrecognized types remain in loaded records"} · {value.unknownKindCount}</p> : null}
    </ContextHelp> : null}
    {value?.events.length ? <details ref={records} className="research-history-all-records" open={recordsOpen} onToggle={event => setRecordsOpen(event.currentTarget.open)}><summary>{zh ? "全部已读记录" : "All loaded records"} · {value.events.length}</summary>
      {recordsOpen ? <ResearchRecordSearch key={JSON.stringify([goalId, readSourceUrl])} goalId={goalId} events={history.canonical?.events ?? []}
        displayEvents={currentDisplay && !originalDisplay ? value.events : undefined}
        displaySections={currentDisplay && !originalDisplay ? displayProjection.sections : undefined}
        renderBody={currentDisplay && !originalDisplay ? findingBody : undefined}
        renderSummary={currentDisplay && !originalDisplay ? findingNarrative : undefined}
        complete={read.complete} stale={Boolean(error || history.error)} busy={busy} zh={zh} stateLabel={stateLabel} timeLabel={timeLabel}/> : null}
    </details> : null}
  </section>;
}
