import {useCallback, useEffect, useRef, useState} from "react";
import {ArrowUpRight, ChevronDown, LoaderCircle, RefreshCw, Sparkles, X} from "lucide-react";
import type {AssessmentCitation, TrajectoryEpisode, TrajectoryOutputLanguage, TrajectoryContinuation} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import {ResearchProgress, metricCopy, verdictLabel, type ResearchProgressPack} from "./goal-research-progress";
import {requestJson} from "../../data/chat";
import {sourceReadApiUrl} from "../../data/local-status-query";
import {ContextHelp} from "./context-help";
import "./goal-research-analysis.css";

// Transport views of the existing toolkit producer, not an assessment, score,
// source selection or job-lifecycle owner. Runtime paths never reach this view.
type Job = {status: string; attempt?: number; analysis_wall_seconds?: number | null; error?: string; cancel_requested?: boolean;
  reused_from_analysis_key?: string;
  config?: {model: string; effort: string; service_tier: string}; served_service_tier?: string | null;
  usage?: {input_tokens?: number; output_tokens?: number; reasoning_output_tokens?: number} | null};
type Pack = ResearchProgressPack & {source_count: number; analysis_key: string; method_version: string;
  config: {model: string; effort: string; service_tier: string; output_language?: TrajectoryOutputLanguage}; availability: string; reason_code: string | null;
  objective?: string; episodes?: TrajectoryEpisode[]; job?: Job | null;
  continuation_unavailable?: {base_pack_id: string};
  continuation?: TrajectoryContinuation & {base_pack_id: string; base_title: string; base_available: boolean; base_episode_count: number}};
type Snapshot = {ok: true; execution_enabled?: boolean; catalog: {availability: string; reason_code: string | null; default_pack_id: string | null; packs: Pack[]; unreadable_pack_ids?: string[]}; pack: Pack | null};
type Citation = AssessmentCitation & {artifact: string; text: string; applicability: string};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const locationFor = (goal: string) => {
  const query = new URLSearchParams(window.location.search);
  const owner = query.get("research_analysis") ?? query.get("research_analysis_goal");
  return {open: query.get("research_analysis") === goal, packId: owner === goal ? query.get("research_pack") : null};
};

export function useResearchAnalysis(goalId: string, active: boolean, zh: boolean, readSourceUrl?: string) {
  const endpoint = "/api/chat/research-analysis";
  const outputLanguage = zh ? "zh" : "en";
  const [citation, setCitation] = useState<Citation | null>(null);
  const [citationError, setCitationError] = useState("");
  const [citationBusy, setCitationBusy] = useState(false);
  const citeGeneration = useRef(0);
  const sourcePanel = useRef<HTMLElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement | null>(null);
  const [location, setLocation] = useState(() => locationFor(goalId));
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const generation = useRef(0);
  const readSequence = useRef(0);
  const submitting = useRef(false);
  const scope = useRef({goalId, packId: location.packId, outputLanguage});
  scope.current = {goalId, packId: location.packId, outputLanguage};
  const read = useCallback(async (packId: string | null = location.packId, signal?: AbortSignal) => {
    const current = generation.current, sequence = ++readSequence.current;
    const query = new URLSearchParams({goal_id: goalId, output_language: outputLanguage, ...(packId ? {pack_id: packId} : {})});
    let value: Snapshot;
    try {value = await requestJson<Snapshot>(sourceReadApiUrl(readSourceUrl, `${endpoint}?${query}`), {signal});}
    catch (e) {if (current === generation.current && sequence === readSequence.current && !signal?.aborted) setReadError(errorText(e)); throw e;}
    if (!value?.ok || !Array.isArray(value.catalog?.packs) || (value.pack && (value.pack.pack_id !== (packId ?? value.catalog.default_pack_id)
        || value.pack.config.output_language !== outputLanguage || (value.pack.result && value.pack.result.output_language !== outputLanguage)))) {
      const failure = new Error(zh ? "分析来源读回不一致。" : "Analysis source readback does not match.");
      if (current === generation.current && sequence === readSequence.current && !signal?.aborted) setReadError(failure.message);
      throw failure;
    }
    if (current === generation.current && sequence === readSequence.current && scope.current.goalId === goalId && scope.current.packId === packId && scope.current.outputLanguage === outputLanguage && !signal?.aborted) {setSnapshot(value); setReadError(""); if (value.pack?.job?.status === "succeeded" || value.pack?.job?.status === "cancelled") setError("");}
    return value;
  }, [goalId, location.packId, zh, outputLanguage, readSourceUrl]);
  const applyLocation = useCallback((next: typeof location) => {
    // Selection and browser history share one source transition. Invalidate
    // pending reads/actions before a new scope can display the old snapshot.
    if (scope.current.packId !== next.packId) {
      generation.current++; readSequence.current++; submitting.current = false;
      citeGeneration.current++; setCitation(null); setCitationError(""); setCitationBusy(false);
      setPending(false); setCancelPending(false); setError(""); setReadError("");
      setSnapshot(previous => previous?.pack?.pack_id === next.packId ? previous : previous ? {...previous, pack: null} : null);
    }
    scope.current = {goalId, packId: next.packId, outputLanguage};
    setLocation(next);
  }, [goalId, outputLanguage]);
  useEffect(() => {
    generation.current++; submitting.current = false;
    applyLocation(locationFor(goalId)); setSnapshot(null); setError(""); setReadError(""); setPending(false); setCancelPending(false);
    const pop = () => applyLocation(locationFor(goalId));
    window.addEventListener("popstate", pop);
    return () => {generation.current++; citeGeneration.current++; window.removeEventListener("popstate", pop);};
  }, [goalId, applyLocation, readSourceUrl]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); setLoading(true);
    void read(location.packId, controller.signal).catch(() => undefined).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [active, read, location.packId]);
  // A language change is an analysis-scope change, not a model invocation.
  // Hide the prior language synchronously, before the next effect/read finishes.
  const currentSnapshot = snapshot?.pack && snapshot.pack.config.output_language !== outputLanguage ? null : snapshot;
  const pack = currentSnapshot?.pack ?? null;
  useEffect(() => {citeGeneration.current++; setCitation(null); setCitationError(""); setCitationBusy(false);}, [goalId, readSourceUrl, snapshot?.pack?.pack_id, snapshot?.pack?.analysis_key]);
  useEffect(() => {
    if ((!citation && !citationBusy && !citationError) || !location.open || !active) return;
    // A response can arrive before React mounts the source panel. Move only
    // after that commit, without competing with analysis-opening navigation.
    sourcePanel.current?.focus({preventScroll: true});
    sourcePanel.current?.scrollIntoView({block: "nearest"});
  }, [citation, citationBusy, citationError, location.open, active]);

  // A pending request is observed here. A persisted running receipt alone is
  // not proof that the model is currently executing; old receipts stop polling.
  const recordedRunning = pack?.job?.status === "running";
  useEffect(() => {
    if (!active || !location.open || (!pending && !recordedRunning)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const until = Date.now() + 15 * 60_000;
    const poll = async () => {
      try {await read();} catch { /* readback error is scoped inside read() */ }
      if (!stopped && Date.now() < until) timer = setTimeout(poll, 3000);
    };
    timer = setTimeout(poll, 1000);
    return () => {stopped = true; clearTimeout(timer);};
  }, [active, location.open, pending, recordedRunning, read]);
  const navigate = (open: boolean, packId = location.packId, push = false) => {
    const url = new URL(window.location.href);
    if (packId) {url.searchParams.set("research_pack", packId); url.searchParams.set("research_analysis_goal", goalId);}
    else {url.searchParams.delete("research_pack"); url.searchParams.delete("research_analysis_goal");}
    if (open) url.searchParams.set("research_analysis", goalId);
    else url.searchParams.delete("research_analysis");
    window.history[push ? "pushState" : "replaceState"](window.history.state, "", url);
    applyLocation({open, packId});
  };
  const show = (focusAnalysis = true) => {
    navigate(true, pack?.pack_id ?? location.packId, !location.open);
    if (focusAnalysis) requestAnimationFrame(() => {panel.current?.focus({preventScroll: true}); panel.current?.scrollIntoView({block: "start"});});
  };
  const execute = async (retry = false) => {
    if (readSourceUrl && snapshot?.execution_enabled !== true) return;
    if (!pack || pack.availability !== "available" || submitting.current) return;
    show(); submitting.current = true; setPending(true); setError("");
    const current = generation.current;
    try {
      await requestJson(sourceReadApiUrl(readSourceUrl, endpoint), {method: "POST", body: JSON.stringify({goal_id: goalId, pack_id: pack.pack_id, analysis_key: pack.analysis_key, output_language: outputLanguage, retry})});
      if (current === generation.current) await read(pack.pack_id);
    } catch (e) {if (current === generation.current) {setError(errorText(e)); void read(pack.pack_id).catch(() => undefined);}}
    finally {if (current === generation.current) {submitting.current = false; setPending(false);}}
  };
  const cancel = async () => {
    if (readSourceUrl && snapshot?.execution_enabled !== true) return;
    if (!pack || cancelPending) return;
    const current = generation.current;
    setCancelPending(true);
    try {
      await requestJson(sourceReadApiUrl(readSourceUrl, `${endpoint}/cancel`), {method: "POST", body: JSON.stringify({goal_id: goalId, pack_id: pack.pack_id, analysis_key: pack.analysis_key, output_language: outputLanguage})});
      if (current === generation.current) await read(pack.pack_id);
    } catch (e) {if (current === generation.current) setError(errorText(e));}
    finally {if (current === generation.current) setCancelPending(false);}
  };
  const readCitation = async (c: AssessmentCitation, origin?: HTMLButtonElement) => {
    const p = pack;
    if (!p) return;
    if (origin) sourceTrigger.current = origin;
    show(false);
    const current = ++citeGeneration.current; setCitation(null); setCitationBusy(true); setCitationError("");
    const query = new URLSearchParams({goal_id: goalId, pack_id: p.pack_id, analysis_key: p.analysis_key, output_language: outputLanguage, source_id: c.source_id, first_line: String(c.first_line), last_line: String(c.last_line)});
    try {const value = await requestJson<Citation>(sourceReadApiUrl(readSourceUrl, `${endpoint}/citation?${query}`)); if (current === citeGeneration.current) setCitation(value);}
    catch (e) {if (current === citeGeneration.current) setCitationError(errorText(e));}
    finally {if (current === citeGeneration.current) setCitationBusy(false);}
  };
  const closeCitation = () => {citeGeneration.current++; setCitation(null); setCitationError(""); setCitationBusy(false); requestAnimationFrame(() => sourceTrigger.current?.focus());};
  return {readOnly: Boolean(readSourceUrl) && currentSnapshot?.execution_enabled !== true, citation, citationBusy, citationError, sourcePanel, readCitation, closeCitation, goalId, zh, location, snapshot: currentSnapshot, pack, readError, error: error || readError, loading, pending, cancelPending, recordedRunning, panel, trigger, read, show, execute, cancel,
    close: () => {citeGeneration.current++; setCitation(null); setCitationError(""); setCitationBusy(false); navigate(false); requestAnimationFrame(() => trigger.current?.focus());}, select: (id: string) => navigate(location.open, id, true)};
}
type Controller = ReturnType<typeof useResearchAnalysis>;

const reasonCopy = (reason: string | null | undefined, zh: boolean) => {
  const copy: Record<string, [string, string]> = {
    no_available_pack: ["尚无可分析的授权轨迹包。", "No authorized trajectory pack is available."],
    pack_not_registered: ["此 Goal 尚未接入可分析的轨迹。", "No trajectory is connected for analysis in this Goal."],
    source_changed: ["原始来源已变化；保留历史分析，重新核对后才能再次分析。", "The original source changed. Historical analysis is retained; recheck sources before another analysis."],
    source_unavailable: ["原始来源暂不可读；历史记录仍可浏览。", "The original source cannot be read. History remains available."],
    method_changed: ["分析方法已更新；旧结果仅适用于原方法。", "The method changed. Earlier results apply only to their original method."],
  };
  return (reason && copy[reason]?.[zh ? 0 : 1]) || (zh ? "分析暂不可用，请刷新重试。" : "Analysis is unavailable; refresh to retry.");
};

export function ResearchAnalysisButton({analysis: a}: {analysis: Controller}) {
  const unavailable = a.readOnly || a.pack?.availability !== "available";
  // The action keeps its separate-usage consequence visible. Further method
  // explanation is optional; cached results and unavailable states are reads.
  const readOnly = unavailable || a.pack?.result || a.pending || a.recordedRunning;
  return <div className="research-analysis-entry"><div className="research-analysis-entry-actions"><button ref={a.trigger} type="button" disabled={a.loading && !a.pack} onClick={() => readOnly ? a.show() : void a.execute(a.pack?.job?.status === "failed" || a.pack?.job?.status === "cancelled")} aria-expanded={a.location.open} title={unavailable ? reasonCopy(a.pack?.reason_code ?? a.snapshot?.catalog.reason_code, a.zh) : undefined}>
    <Sparkles size={14} aria-hidden/>{a.pack?.result ? (a.zh ? "查看分析" : "View analysis") : analysisActionLabel(a.pack, a.zh)}
  </button><ContextHelp label={a.zh ? "分析说明" : "About deep analysis"}>
    {a.pack ? <p>{a.zh ? "分析请求设置（固定）：" : "Fixed analysis request: "}{a.pack.config.model} / {a.pack.config.effort} / {a.pack.config.service_tier}</p> : null}
    <p>{a.zh ? "问题解读、理由与限制说明按界面语言生成；切换语言后单独缓存，需点击才生成。引用保留原文。" : "Questions, reasons and limitations follow the interface language. Each language is cached separately and generated only on request. Citations preserve the original text."}</p>
    <p>{a.pack?.result ? (a.zh ? "所选范围已有分析，查看不调用 Agent。" : "An analysis exists for the selected scope. Viewing never invokes an Agent.") : a.pack?.continuation?.can_reuse ? (a.zh ? "本次复用已有判读，不调用模型；原用量保留在上一版。" : "This action reuses existing judgments without a model call; original usage stays with the previous version.") : (a.zh ? "点击深度分析使用本机 Codex 账户，单独产生分析用量；普通浏览不调用 Agent。" : "Deep analysis uses the local Codex account with separate analysis usage; ordinary browsing never invokes an Agent.")}</p>
    <p>{a.zh ? "只分析已授权的固定轨迹，不改变研究历史。范围、方法与来源版本可在结果中查看。" : "Analyzes an authorized fixed trajectory without changing research history. Scope, method and source revisions remain available with the result."}</p>
    {a.pack?.continuation ? <p>{a.zh ? "接续保留上一版。新增、更正或删除可能影响旧判断，本版重新评估全部片段；仅证据内容与配置等价时复用已有判读。浏览与来源更新不启动分析。" : "Continuation preserves the previous version. Additions, corrections and removals can affect earlier judgments, so all episodes are reassessed. Existing judgments are reused only for equivalent evidence and configuration. Browsing and source updates never start analysis."}</p> : null}
  </ContextHelp></div>{a.pack && !a.pack.result && !unavailable ? <small>{a.pack.continuation?.can_reuse ? (a.zh ? "无需新模型调用" : "No new model call") : <>{a.pack.config.model} · {a.zh ? "单独用量" : "Separate usage"}</>}</small> : null}</div>;
}

const analysisActionLabel = (p: Pack | null, zh: boolean) => p?.continuation?.can_reuse
  ? (zh ? "复用分析" : "Reuse analysis") : p?.continuation ? (zh ? "接续分析" : "Continue analysis") : (zh ? "深度分析" : "Deep analysis");

function AnalysisContinuation({pack: p, analysis: a}: {pack: Pack; analysis: Controller}) {
  const c = p.continuation, zh = a.zh;
  if (!c) return p.continuation_unavailable ? <p className="analysis-warning">{zh ? "基础分析暂不可读；本版仍可独立分析完整范围。" : "The base analysis cannot be read; this version can still assess its complete scope independently."} <button type="button" onClick={() => void a.read().catch(() => undefined)}>{zh ? "重试读取" : "Retry reading"}</button></p> : null;
  const changes = [
    c.sources.added.length ? `${zh ? "新增来源" : "New sources"} ${c.sources.added.length}` : "",
    c.sources.revised.length ? `${zh ? "更新来源" : "Updated sources"} ${c.sources.revised.length}` : "",
    c.sources.removed.length ? `${zh ? "移除来源" : "Removed sources"} ${c.sources.removed.length}` : "",
    c.episodes.added.length ? `${zh ? "新增片段" : "New episodes"} ${c.episodes.added.length}` : "",
    c.episodes.revised.length ? `${zh ? "更新片段" : "Updated episodes"} ${c.episodes.revised.length}` : "",
    c.episodes.removed.length ? `${zh ? "移除片段" : "Removed episodes"} ${c.episodes.removed.length}` : "",
    c.can_reuse ? (zh ? "复用原判读" : "Reuse judgments") : `${zh ? (p.result ? "已复核全部" : "完整复核") : "Reassess all"} ${p.episode_count} ${zh ? "片段" : "episodes"}`,
  ].filter(Boolean).join(" · ");
  return <section className="analysis-continuation" aria-label={zh ? "分析版本变化" : "Analysis version changes"}>
    <div className="analysis-continuation-summary"><strong>{c.can_reuse ? (zh ? "证据内容未变" : "Evidence content unchanged") : (zh ? "接续上一版" : "Continue from previous version")}</strong>
      <span>{changes}</span>
      <button type="button" onClick={() => {a.select(c.base_pack_id); requestAnimationFrame(() => a.panel.current?.focus({preventScroll: true}));}}>{zh ? "查看上一版" : "Previous version"}</button>
    </div>
    {!c.base_available ? <p className="analysis-warning">{zh ? "上一版尚无适用分析，本次将独立分析完整范围。" : "No applicable previous analysis is available. This version will assess the full scope independently."}</p> : null}
    {c.configuration_changed || c.objective_changed ? <p className="analysis-warning">{zh ? "分析设置或目标已变更，两版判断不作为同条件比较。" : "The analysis configuration or objective changed. Judgments are not a matched comparison."}</p> : null}
    <details className="analysis-method"><summary>{p.result && c.base_available ? `${c.verdict_changes.length} ${zh ? "项判断变化" : "changed judgments"}` : (zh ? "查看变化" : "Inspect changes")}</summary>
      <p>{c.base_title} · {c.base_scope.revision} → {c.current_scope.revision}</p>
      <p>{zh ? "上次覆盖" : "Previous coverage"}: {c.base_episode_count} · {c.base_scope.window}<br/>{zh ? "本次覆盖" : "Current coverage"}: {p.episode_count} · {c.current_scope.window}</p>
      <p>{zh ? "来源（新增 / 更正 / 删除）" : "Sources (added / revised / removed)"}: {c.sources.added.length} / {c.sources.revised.length} / {c.sources.removed.length}<br/>{zh ? "片段（新增 / 更正 / 删除）" : "Episodes (added / revised / removed)"}: {c.episodes.added.length} / {c.episodes.revised.length} / {c.episodes.removed.length}<br/>{zh ? "适用判断项（新增 / 移除）" : "Eligible judgments (added / removed)"}: {c.new_pair_count} / {c.removed_pair_count}</p>
      {c.episodes.added.length ? <p>{zh ? "新增片段" : "New episodes"}: {c.episodes.added.join(" · ")}</p> : null}
      {c.episodes.revised.length ? <p>{zh ? "更正片段" : "Revised episodes"}: {c.episodes.revised.join(" · ")}</p> : null}
      {c.episodes.removed.length ? <p>{zh ? "移除片段" : "Removed episodes"}: {c.episodes.removed.join(" · ")}</p> : null}
      <p>{zh ? "无法仅凭局部变化证明旧结论不受影响，因此实质变化均复核完整新版证据；指标按本版适用项重新计算，旧版不覆盖、不相加。" : "Local changes cannot prove earlier conclusions unaffected. Substantive changes therefore reassess the complete new evidence. Metrics are recalculated from this version’s eligible items; previous results are neither overwritten nor added."}</p>
      {c.verdict_changes.length ? <ul>{c.verdict_changes.map(row => <li key={`${row.episode_id}:${row.metric}`}>{row.episode_id} · {metricCopy[row.metric][zh ? 0 : 1]}: {verdictLabel(row.metric, row.before, zh)} → {verdictLabel(row.metric, row.after, zh)}</li>)}</ul> : null}
    </details>
  </section>;
}

export function ResearchAnalysisStatus({analysis: a}: {analysis: Controller}) {
  const p = a.pack;
  if (a.readOnly && !p) return <p className="research-history-note">{a.zh ? "此运行尚未接入深度分析。" : "Deep analysis is not connected for this runtime."}</p>;
  if (!p) return <p className="research-history-note">{a.loading ? (a.zh ? "正在核对可分析来源；浏览不调用 Agent。" : "Checking analysis sources. Browsing never invokes an Agent.") : reasonCopy(a.snapshot?.catalog.reason_code, a.zh)}</p>;
  return !p.result && p.availability !== "available" ? <p className="research-history-note">{reasonCopy(p.reason_code, a.zh)}</p> : null;
}

export function ResearchProgressSummary({analysis: a}: {analysis: Controller}) {
  const packs = a.snapshot?.catalog.packs ?? [];
  if (!a.pack && !packs.length) return null;
  return <ResearchProgress pack={a.pack} packs={packs} selectedId={a.location.packId ?? a.snapshot?.catalog.default_pack_id ?? ""} error={a.readError} zh={a.zh} onSelect={a.select} onCitation={a.readCitation}/>;
}

export function ResearchAnalysisPanel({analysis: a}: {analysis: Controller}) {
  if (!a.location.open) return null;
  const zh = a.zh, p = a.pack, result = p?.result;
  const failed = p?.job?.status === "failed" || p?.job?.status === "cancelled";
  const available = p?.availability === "available";
  const benefit = result?.metrics.find(m => m.metric === "benefit_test");
  const headline = !result ? null : benefit?.no ? (zh ? "仍有研究片段尚未检验原目标收益" : "Some episodes have not tested original-objective utility")
    : benefit?.unknown ? (zh ? "原目标收益的检验情况仍未知" : "Original-objective utility testing remains unknown")
      : benefit?.yes ? (zh ? "模型记录了收益检验；这不等于收益为正" : "The model identified a utility test; this does not imply positive utility")
        : (zh ? "此方法未覆盖原目标收益检验" : "This method does not cover original-objective utility testing");
  const citationButtons = (citations: AssessmentCitation[]) => <div className="analysis-citations">{citations.map(c => <button type="button" key={`${c.source_id}:${c.first_line}:${c.last_line}`} onClick={event => {void a.readCitation(c, event.currentTarget);}}><ArrowUpRight size={13} aria-hidden/>{c.source_id} · {zh ? "行" : "lines"} {c.first_line}–{c.last_line}</button>)}</div>;
  return <div className="research-analysis" ref={a.panel} tabIndex={-1} role="region" aria-label={zh ? "轨迹深度分析" : "Trajectory analysis"} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); a.close();}}}>
    <header><div><h3>{zh ? "轨迹深度分析" : "Trajectory analysis"}</h3><p>{p?.title ?? (zh ? "只分析已授权的固定轨迹，不改变研究历史。" : "Analyze an authorized fixed trajectory without changing its history.")}</p></div><div className="analysis-header-actions">{result ? <button type="button" onClick={() => void a.read().catch(() => undefined)} aria-label={zh ? "刷新分析结果" : "Refresh analysis result"}><RefreshCw size={15} aria-hidden/></button> : null}<button type="button" onClick={a.close} aria-label={zh ? "关闭分析" : "Close analysis"}><X size={16} aria-hidden/></button></div></header>
    {a.error ? <p className="analysis-error" role="alert">{a.error} <button type="button" onClick={() => void a.read().catch(() => undefined)}>{zh ? "重试读取" : "Retry reading"}</button></p> : null}
    {a.snapshot?.catalog.unreadable_pack_ids?.length && !p?.continuation_unavailable ? <p className="analysis-warning">{zh ? "部分历史分析暂不可读" : "Some historical analyses cannot be read"} · {a.snapshot.catalog.unreadable_pack_ids.length}</p> : null}
    {a.loading && !p ? <p role="status">{zh ? "正在核对可分析来源…" : "Checking authorized sources…"}</p> : null}
    {!a.loading && !p ? <p>{reasonCopy(a.snapshot?.catalog.reason_code, zh)} <button type="button" onClick={() => void a.read().catch(() => undefined)}><RefreshCw size={14} aria-hidden/>{zh ? "刷新" : "Refresh"}</button></p> : null}
    {p ? <>
      <div className="analysis-scope">{p.episode_count} {zh ? "个研究片段" : "research episodes"} · {p.selection.exhaustive ? (zh ? "固定范围" : "Fixed scope") : (zh ? "定向抽样" : "Purposive sample")}<ContextHelp label={zh ? "分析范围" : "Analysis scope"}>
        <p>{p.selection.exhaustive ? (zh ? "覆盖此固定分析范围；不等于覆盖全部研究。" : "Covers this fixed analysis scope, not necessarily the whole research history.") : (zh ? "定向抽样，仅分析选定研究片段；范围外或未捕获的工作未纳入，不代表全程。" : "Purposive sample of selected episodes; work outside this scope or not captured is excluded. It does not represent the whole run.")}</p>
        <p>{zh ? "正向诊断、证据采用与最终任务收益分别判断。没有综合分数，也不据此推断因果或整体研究质量。引用存在不代表模型解释已被独立核验。" : "Diagnostic value, evidence adoption and task utility are separate. There is no total score, causal claim or whole-run quality rating. Citations do not independently verify model interpretations."}</p>
        <p>{zh ? "下方仍显示当前研究 Goal 的已记录历史。" : "The current research Goal’s recorded history remains below."}</p>
      </ContextHelp></div>
      <AnalysisContinuation pack={p} analysis={a}/>
      {!available ? <p className="analysis-warning">{reasonCopy(p.reason_code, zh)}</p> : null}
      {!result || a.pending || a.recordedRunning ? <div className="analysis-actions">
        {a.pending || a.recordedRunning ? <><p role="status">{a.pending ? <LoaderCircle size={15} aria-hidden className="analysis-spin"/> : null}{a.pending ? (zh ? "正在处理分析请求…" : "Processing analysis request…") : (zh ? "上次记录仍待分析结果；当前执行状态尚未核验。" : "The last receipt awaits a result; current execution is unverified.")}</p><button type="button" onClick={() => void a.cancel()} disabled={a.cancelPending || p.job?.cancel_requested}>{p.job?.cancel_requested ? (zh ? "已请求取消" : "Cancellation requested") : a.cancelPending ? (zh ? "正在请求取消…" : "Requesting cancellation…") : (zh ? "取消分析" : "Cancel analysis")}</button></>
          : result ? <button type="button" onClick={() => void a.read().catch(() => undefined)}><RefreshCw size={14} aria-hidden/>{zh ? "刷新结果" : "Refresh result"}</button>
            : <button className="analysis-primary" type="button" disabled={!available} onClick={() => void a.execute(failed)}><Sparkles size={15} aria-hidden/>{failed ? (zh ? "重试分析" : "Retry analysis") : p.continuation ? analysisActionLabel(p, zh) : (zh ? "分析所选范围" : "Analyze selected scope")}</button>}
        {!result && !a.pending && !a.recordedRunning ? <small>{p.continuation?.can_reuse ? (zh ? "复用已有判读，无需新模型调用。" : "Reuses existing judgments without a new model call.") : zh ? "使用本机 Codex 账户，单独产生分析用量；浏览不调用 Agent。" : "Uses the local Codex account with separate analysis usage. Browsing never invokes an Agent."}</small> : null}
      </div> : null}
      {failed && !a.pending ? <p className="analysis-warning" role="status">{p.job?.status === "cancelled" ? (zh ? "分析已取消，研究历史保持可读。" : "Analysis cancelled. Research history remains readable.") : (zh ? "上次分析未完成；可重试，原失败记录会保留。" : "The last analysis did not complete. Retry preserves its failure record.")}</p> : null}
      {result ? <>
        <div className="analysis-finding"><span>{zh ? "Agent 判断 · 未校准" : "Agent interpretation · uncalibrated"}{p.applicability === "historical" ? (zh ? " · 历史结果" : " · Historical result") : ""}</span><h4>{headline}</h4></div>
        {p.episodes?.map(episode => <details className="analysis-episode" key={`${p.pack_id}:${p.analysis_key}:${episode.id}`}>
          <summary><span className="analysis-episode-name">{episode.id}</span><span className="analysis-episode-verdicts">{(["benefit_test", "evidence_adoption"] as const).map(metric => {
            const row = result.assessments.find(item => item.episode_id === episode.id && item.metric === metric);
            return row ? <span key={metric}>{metricCopy[metric][zh ? 0 : 1]} · {verdictLabel(metric, row.verdict, zh)}</span> : null;
          })}</span><ChevronDown size={14} aria-hidden/></summary>
          <div className="analysis-episode-body"><h4>{result.episode_questions?.find(q => q.episode_id === episode.id)?.question ?? episode.question}</h4>
          {result.episode_questions ? <details><summary>{zh ? "原始研究问题" : "Original research question"}</summary><p>{episode.question}</p></details> : null}
          <div className="analysis-episode-context">{(["benefit_test", "evidence_adoption"] as const).map(metric => {
            const row = result.assessments.find(item => item.episode_id === episode.id && item.metric === metric);
            return row ? <div key={metric}><strong>{metric === "benefit_test" ? (zh ? "原目标收益的检验与缺口" : "Original-objective utility test and its gaps") : (zh ? "证据如何影响后续行动" : "How evidence informed subsequent action")}</strong><p>{row.reason}</p>{citationButtons(row.citations)}</div> : null;
          })}</div>
          <div className="analysis-judgments">{result.assessments.filter(row => row.episode_id === episode.id && row.metric !== "benefit_test" && row.metric !== "evidence_adoption").map(row => {
          const label = metricCopy[row.metric];
          return <details key={row.metric}><summary><strong>{label?.[zh ? 0 : 1] ?? row.metric}</strong><span>{verdictLabel(row.metric, row.verdict, zh)}</span></summary><p className="analysis-question">{label?.[zh ? 2 : 3]}</p><p>{row.reason}</p>{citationButtons(row.citations)}</details>;
        })}</div></div></details>)}
        <details className="analysis-method"><summary>{zh ? "来源、方法、覆盖与分析成本" : "Sources, method, coverage and analysis cost"}</summary><p>{p.objective}</p><dl><dt>{zh ? "来源范围" : "Source scope"}</dt><dd>{p.scope.case_id} · {p.scope.run_id}<br/>{p.scope.window}</dd><dt>{zh ? "方法" : "Method"}</dt><dd>{result.method_version} · {zh ? "模型判断未校准" : "Uncalibrated model judgment"}</dd><dt>{zh ? "模型设置" : "Requested model"}</dt><dd>{p.config.model} / {p.config.effort} / {p.config.service_tier} · {zh ? "实际服务档位" : "Served tier"}: {p.job?.served_service_tier ?? (zh ? "未知" : "unknown")}</dd><dt>{zh ? "分析用量" : "Analysis usage"}</dt><dd>{p.job?.reused_from_analysis_key ? (zh ? "复用上一版判读，无新增模型调用；原用量保留在上一版。" : "Reused previous judgments without a new model call; original usage stays with the previous version.") : <>{p.job?.analysis_wall_seconds == null ? "—" : `${p.job.analysis_wall_seconds.toFixed(1)}s`} · {zh ? "输入" : "input"} {p.job?.usage?.input_tokens?.toLocaleString() ?? "—"} / {zh ? "输出" : "output"} {p.job?.usage?.output_tokens?.toLocaleString() ?? "—"} tokens · {zh ? "金额未采集，推理 token 已包含于输出" : "Dollar cost unmeasured; reasoning tokens are included in output"}</>}</dd><dt>{zh ? "覆盖" : "Coverage"}</dt><dd>{p.episode_count} {zh ? "个固定片段" : "fixed episodes"} · {p.source_count} {zh ? "份来源" : "sources"} · {zh ? "总体片段数" : "Population"}: {p.selection.population_episodes ?? (zh ? "未知" : "unknown")}</dd><dt>{zh ? "取样规则" : "Selection"}</dt><dd>{p.selection.rule}</dd><dt>{zh ? "版本" : "Revision"}</dt><dd>{p.scope.revision}</dd></dl><ul>{result.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul><p>{zh ? "引用行存在不等于解释正确；本页不提供独立科学校准结论。" : "A valid citation range does not establish entailment or independent scientific calibration."}</p></details>
      </> : null}
      {/* Bound native evidence is readable before any model assessment exists. */}
      {a.citation || a.citationBusy || a.citationError ? <section className="analysis-source" ref={a.sourcePanel} tabIndex={-1} aria-label={zh ? "引用原文" : "Cited original"} onKeyDown={event => {if (event.key === "Escape") {event.stopPropagation(); a.closeCitation();}}}>
        <header><h4>{zh ? "引用原文" : "Cited original"}</h4><button type="button" onClick={a.closeCitation} aria-label={zh ? "关闭原文" : "Close source"}><X size={14} aria-hidden/></button></header>
        {a.citationBusy ? <p role="status">{zh ? "正在读取引用原文…" : "Reading cited source…"}</p> : null}
        {a.citationError ? <p className="analysis-error" role="alert">{a.citationError}</p> : null}
        {a.citation ? <><p>{a.citation.artifact} · {zh ? "行" : "lines"} {a.citation.first_line}–{a.citation.last_line}</p><pre>{a.citation.text}</pre></> : null}
      </section> : null}
    </> : null}
  </div>;
}
