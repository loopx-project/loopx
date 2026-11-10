import {ArrowLeft, ArrowRight, RefreshCw} from "lucide-react";
import {useEffect, useRef, useState} from "react";
import {z} from "zod";
import {requestJson} from "../../data/chat";
import {sourceReadApiUrl} from "../../data/local-status-query";
import {ContextHelp} from "./context-help";
import {MarkdownText} from "./markdown";
import "./goal-source-strategies.css";

const versionId = /^[a-f0-9]{64}$/;
const object = z.record(z.string(), z.unknown());
const snapshotSchema = z.object({
  ok: z.literal(true), goal_id: z.string(),
  source: z.object({namespace: z.string().regex(versionId)}),
  total: z.number().int().nonnegative(), native_run_count: z.number().int().nonnegative(),
  items: z.array(z.object({id: z.string().regex(versionId), recorded_at: z.string(),
    agent_id: z.string().min(1), classification: z.string(), agent_vision: object})),
});
type Snapshot = z.infer<typeof snapshotSchema>;
const record = (value: unknown): Record<string, unknown> => object.safeParse(value).data ?? {};

/** URL state is a reading selection, never a source or execution authority. */
export function useGoalStrategyLocation(goalId: string, sourceUrl?: string) {
  const scope = `${goalId}:${sourceUrl ?? ""}`;
  const read = () => {
    const query = new URLSearchParams(window.location.search);
    return query.get("research_goal") === goalId ? query.get("research_state") : null;
  };
  const [location, setLocation] = useState(() => ({scope, selection: read()}));
  const selection = location.scope === scope ? location.selection : read();
  const opener = useRef<HTMLElement | null>(null);
  const wasReading = useRef(Boolean(selection));
  useEffect(() => {
    const changed = () => setLocation({scope, selection: read()});
    changed(); window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, [scope]);
  useEffect(() => {
    if (wasReading.current && !selection) {
      requestAnimationFrame(() => opener.current?.focus({preventScroll: true}));
    }
    wasReading.current = Boolean(selection);
  }, [selection]);
  const choose = (id: string | null, replace = false) => {
    const url = new URL(window.location.href);
    if (id === null) url.searchParams.delete("research_state");
    else {url.searchParams.set("research_goal", goalId); url.searchParams.set("research_state", id);}
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    setLocation({scope, selection: id});
  };
  return {selection, choose, opener, scope};
}

export function GoalSourceStrategies({goalId, originalGoalId, sourceUrl, zh, selection, onSelect, onClose, embedded = false}: {
  goalId: string; originalGoalId: string; sourceUrl?: string; zh: boolean; selection: string;
  onSelect: (id: string, replace?: boolean) => void; onClose?: () => void; embedded?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(true), [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const overview = useRef<HTMLOListElement>(null);
  const previousSelection = useRef<string | null>(null);
  const namespace = sourceUrl ? new URL(sourceUrl, window.location.href).searchParams.get("source_namespace") : null;
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setBusy(true); setFailed(false);
    const load = async () => requestJson<unknown>(sourceReadApiUrl(sourceUrl,
      `/api/chat/goal-source-strategies?${new URLSearchParams({goal_id: goalId})}`), {signal: controller.signal});
    void load().then(value => {
      const result = snapshotSchema.parse(value);
      if (result.goal_id !== originalGoalId || result.source.namespace !== namespace
          || result.total !== result.items.length || result.native_run_count < result.total
          || new Set(result.items.map(item => item.id)).size !== result.total) throw new Error("Inconsistent source history");
      if (current) setSnapshot(result);
    }).catch(() => {if (current) setFailed(true);}).finally(() => {if (current) setBusy(false);});
    return () => {current = false; controller.abort();};
  }, [goalId, originalGoalId, sourceUrl, namespace, revision]);
  useEffect(() => {
    const previous = previousSelection.current;
    previousSelection.current = selection;
    if (selection === "all" && previous && previous !== "all") {
      const entry = overview.current?.querySelector<HTMLElement>(`[data-strategy-id="${versionId.test(previous) ? previous : ""}"]`);
      entry?.focus({preventScroll: true}); entry?.scrollIntoView({block: "nearest"});
    } else if (!embedded || selection !== "all") heading.current?.focus({preventScroll: true});
  }, [selection, embedded]);
  const items = snapshot?.items ?? [];
  // Only recorded time orders this view. It establishes neither execution time
  // nor causal relationships; records without a usable time remain readable.
  const chronology = [...items].sort((a, b) => {
    const left = Date.parse(a.recorded_at), right = Date.parse(b.recorded_at);
    return Number.isFinite(left) && Number.isFinite(right) ? left - right
      : Number.isFinite(left) ? -1 : Number.isFinite(right) ? 1 : 0;
  });
  const dated = chronology.filter(item => Number.isFinite(Date.parse(item.recorded_at)));
  const index = items.findIndex(item => item.id === selection);
  const selected = items[index];
  const vision = record(selected?.agent_vision), patch = record(vision.vision_patch), path = record(vision.path_delta);
  const stride = record(vision.stride_decision), probe = record(stride.next_probe), review = record(stride.previous_review);
  const time = (stamp: string) => Number.isFinite(Date.parse(stamp))
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {dateStyle: "medium", timeStyle: "medium"}).format(new Date(stamp))
    : (zh ? "时间未记录" : "Time not recorded");
  const prose = (label: string, value: unknown) => typeof value === "string" && value
    ? <section key={label}><h3>{label}</h3><MarkdownText text={value} headingLevel={4}/></section> : null;
  const list = (label: string, value: unknown) => Array.isArray(value) && value.length
    ? <section key={label}><h3>{label}</h3><ul>{value.map((item, i) => <li key={i}>
      {typeof item === "string" ? <MarkdownText text={item} headingLevel={4}/> : <pre>{JSON.stringify(item, null, 2)}</pre>}
    </li>)}</ul></section> : null;
  const nextLabels = zh ? {scope: "范围", prediction: "预期", adoption_criterion: "采用条件", cost_bound: "成本边界", minimum_evidence: "所需依据"}
    : {scope: "Scope", prediction: "Prediction", adoption_criterion: "Adoption criterion", cost_bound: "Cost bound", minimum_evidence: "Required evidence"};
  return <section className="research-history source-strategies" aria-label={zh ? "策略历史" : "Strategy history"} aria-busy={busy}>
    {selection !== "all" || onClose ? <button type="button" className="research-reader-back" onClick={() => selection === "all" ? onClose?.() : onSelect("all")}><ArrowLeft size={16} aria-hidden/>{selection === "all" ? (zh ? "返回研究历程" : "Back to research history") : (zh ? "返回策略总览" : "Back to strategy overview")}</button> : null}
    <header className="source-strategies-heading"><div className="research-history-title">
      <h2 ref={heading} tabIndex={-1}>{zh ? "策略历史" : "Strategy history"}</h2>
      <ContextHelp label={zh ? "策略历史来源" : "Strategy history source"}><p>{zh
        ? "直接读取原 Goal 保留的全部策略记录，原文保持原语言，按记录时间（本地）排列。记录空窗不等于没有工作，时间相邻不代表继承或因果。它们与 Explore 事件分开；计划、采用声明和重规划并不证明后续行动或效果。打开和切换记录不会运行模型或实验。"
        : "All retained strategy records are read directly from the original Goal, in their original language, ordered by recorded time (local). Gaps do not establish inactivity; adjacency does not establish inheritance or causality. These are separate from Explore events. Plans, adoption claims and replans do not establish subsequent actions or benefit. Reading records runs no model or experiment."}</p></ContextHelp>
    </div><button type="button" disabled={busy} aria-label={zh ? "刷新策略历史" : "Refresh strategy history"} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} aria-hidden/></button></header>
    {failed ? <div className="research-history-read-error" role="alert"><strong>{snapshot
      ? (zh ? "刷新失败，当前记录已保留" : "Refresh failed; current records are preserved")
      : (zh ? "无法读取原 Goal 的策略历史" : "The original Goal's strategy history is unavailable")}</strong>
      <button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)}>{zh ? "重试" : "Retry"}</button></div> : null}
    {busy && !snapshot ? <p role="status">{zh ? "正在读取全部策略记录…" : "Reading all strategy records…"}</p> : null}
    {snapshot ? <>
      {selection !== "all" ? <nav className="source-strategies-navigation" aria-label={zh ? "选择策略记录" : "Choose a strategy record"}>
        <button type="button" aria-label={zh ? "较早一份" : "Older record"} disabled={index < 0 || index === items.length - 1} onClick={() => onSelect(items[index + 1]!.id)}><ArrowLeft size={16} aria-hidden/></button>
        <select aria-label={zh ? "策略记录" : "Strategy record"} value={selected?.id ?? ""} onChange={event => onSelect(event.target.value)}>
          {!selected ? <option value="">{selection === "all" ? (zh ? "选择记录" : "Choose a record") : (zh ? "所选版本不可用" : "Selected version unavailable")}</option> : null}
          {items.map((item, i) => <option key={item.id} value={item.id}>{items.length - i} · {time(item.recorded_at)} · {item.agent_id}</option>)}
        </select>
        <button type="button" aria-label={zh ? "较新一份" : "Newer record"} disabled={index <= 0} onClick={() => onSelect(items[index - 1]!.id)}><ArrowRight size={16} aria-hidden/></button>
      </nav> : null}
      <p className="source-strategies-meta">{snapshot.total} {zh ? "份策略记录" : "strategy records"}{dated.length < items.length ? <> · {items.length - dated.length} {zh ? "份时间未记录" : "without a recorded time"}</> : null}{dated.length ? <> · {zh ? "记录时间（本地）" : "Recorded time (local)"}<br/>
        <time dateTime={dated[0]!.recorded_at}>{time(dated[0]!.recorded_at)}</time> — <time dateTime={dated.at(-1)!.recorded_at}>{time(dated.at(-1)!.recorded_at)}</time></> : null}</p>
      {selection === "all" && items.length ? <ol ref={overview} className="source-strategy-chronology" aria-label={zh ? "全部策略记录" : "All strategy records"}>
        {chronology.map(item => {
          const itemVision = record(item.agent_vision), itemPatch = record(itemVision.vision_patch), itemPath = record(itemVision.path_delta);
          const summary = typeof itemPatch.vision_summary === "string" && itemPatch.vision_summary;
          const observation = typeof itemPath.observed_reality === "string" && itemPath.observed_reality;
          return <li key={item.id}><button type="button" data-strategy-id={item.id} onClick={() => onSelect(item.id)}>
            <span className="source-strategy-stamp"><time dateTime={Number.isFinite(Date.parse(item.recorded_at)) ? item.recorded_at : undefined}>{time(item.recorded_at)}</time><span>{item.agent_id}</span></span>
            <strong>{summary || (zh ? "原始策略记录" : "Original strategy record")}</strong>
            {observation && observation !== summary ? <span className="source-strategy-observation">{observation}</span> : null}
          </button></li>;
        })}
      </ol> : null}
      {!items.length ? <p role="status">{zh ? "原 Goal 没有保留策略记录。" : "The original Goal has no retained strategy records."}</p>
        : !selected && selection !== "all" ? <p role="status">{zh ? "原来源中找不到所选版本。可选择其他记录。" : "The selected version is absent from this source. Other records remain readable."}</p> : null}
      {selected ? <article className="source-strategy-document" aria-label={zh ? "策略原文" : "Original strategy"}>
        <p className="source-strategies-meta"><time dateTime={selected.recorded_at}>{time(selected.recorded_at)}</time> · {selected.agent_id}</p>
        {prose(zh ? "当时的目标" : "Goal at the time", patch.vision_summary)}
        {prose(zh ? "验收条件" : "Acceptance", patch.acceptance_summary)}
        {prose(zh ? "原先的判断" : "Prior assumption", path.prior_assumption)}
        {prose(zh ? "当时观察" : "Recorded observation", path.observed_reality)}
        {list(zh ? "改变" : "Changed", path.changed)}
        {list(zh ? "保留" : "Retained", path.retained)}
        {list(zh ? "停止" : "Stopped", path.stopped)}
        {list(zh ? "未解问题" : "Unresolved questions", path.unresolved_questions)}
        {prose(zh ? "再次进入的条件" : "Reentry condition", path.reentry_condition)}
        {Object.keys(probe).length ? <section><h3>{zh ? "当时的后续计划" : "Recorded next plan"}</h3><dl>{Object.entries(nextLabels).map(([key, label]) => typeof probe[key] === "string" && probe[key]
          ? <FragmentField key={key} label={label} value={String(probe[key])}/> : null)}</dl></section> : null}
        {prose(zh ? "对上轮的记录性回顾" : "Recorded review of the previous decision", review.lesson)}
        {list(zh ? "依据引用" : "Evidence references", path.evidence_refs)}
        <details className="research-node-provenance"><summary>{zh ? "完整原始记录与来源" : "Complete original record and source"}</summary>
          <dl><FragmentField label="Goal" value={originalGoalId}/><FragmentField label={zh ? "来源" : "Source"} value={snapshot.source.namespace}/>
            <FragmentField label={zh ? "版本" : "Version"} value={selected.id}/><FragmentField label={zh ? "记录时间" : "Recorded time"} value={selected.recorded_at}/></dl>
          <pre tabIndex={0}>{JSON.stringify(selected, null, 2)}</pre>
        </details>
      </article> : null}
    </> : null}
  </section>;
}

function FragmentField({label, value}: {label: string; value: string}) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}
