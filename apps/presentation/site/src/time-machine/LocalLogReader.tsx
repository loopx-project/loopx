import {useEffect, useMemo, useRef, useState} from "react";
import {ArrowLeft, ArrowRight, FileText, FolderOpen, X} from "lucide-react";
import {LocalLogError, LOCAL_LOG_MAX_BYTES, readLocalExploreLog, selectLocalLogEntries, type LocalExploreLog, type LocalLogIssue} from "./local-log";

const copy = {
  en: {
    title: "Your research record",
    privacy: "Local reading · No file upload or storage.",
    choose: "Choose another file", close: "Back to the three episodes", reading: "Reading your file…",
    span: "Recorded between", unknown: "Unknown record time", unknownEvents: "distinct events have unknown record times.",
    search: "Find in the original record", placeholder: "A question, a phrase, an evidence reference…",
    status: "Source label", allLabels: "All labels", emptyLabel: "Unrecorded label", time: "Record time", allTime: "Whole recording",
    phases: ["First third", "Middle third", "Last third"], match: "Matching records", of: "of", line: "Source line",
    previous: "Previous record", next: "Next record", previousPage: "Previous 50", nextPage: "Next 50", page: "Page",
    noMatch: "No matching record.", reset: "Clear filters", original: "Original text · untranslated", structured: "Source sections · untranslated",
    observation: "Source observation", applicability: "Tested scope · source", interpretation: "Source interpretation", fullOriginal: "Read the complete original text",
    details: "Source details and reading limits", references: "Evidence references · text only", raw: "Original JSON record",
    limits: "Explore JSONL, UTF-8, one Goal per file. Up to 20 MiB and 20,000 records; larger files are rejected in full.",
    boundary: "Source labels are claims in your file, not independently verified findings. The time strip uses recorded_at, not execution time. Reading does not restore a past state or start an Agent.",
    coverage: "Every source line remains readable, including identical repeated events. The time strip reuses Explore’s unique-event projection; unknown times and missing links remain unknown.",
    failed: "No records have been loaded from this file.", retry: "Choose a file to try again.",
    issues: {size: "This file exceeds the reading limit.", empty: "This file has no records.", encoding: "This file is not valid UTF-8.", json: "This line is not valid JSON.", record: "This is not a supported Explore record, or it belongs to another Goal.", identity: "One event ID has conflicting records.", read: "The browser could not read this file."},
  },
  zh: {
    title: "你的研究记录",
    privacy: "本地阅读 · 文件内容不上传、不保存。",
    choose: "换一份文件", close: "回到三个案例", reading: "正在读取你的文件…",
    span: "记录跨度", unknown: "记录时间未知", unknownEvents: "个事件的记录时间未知（按事件 ID 去重）。",
    search: "在原记录里找一找", placeholder: "一个问题、一段话、一条证据引用…",
    status: "来源标记", allLabels: "全部标记", emptyLabel: "未记录标记", time: "记录时间", allTime: "完整记录跨度",
    phases: ["前段", "中段", "后段"], match: "匹配记录", of: "/", line: "来源行",
    previous: "上一条记录", next: "下一条记录", previousPage: "前 50 条", nextPage: "后 50 条", page: "页",
    noMatch: "没有找到匹配的记录。", reset: "清除筛选", original: "原文 · 未翻译", structured: "来源分段 · 未翻译",
    observation: "来源中的观察", applicability: "测试范围 · 来源原文", interpretation: "来源中的解释", fullOriginal: "展开完整原文",
    details: "来源详情与阅读范围", references: "证据引用 · 仅文本", raw: "原始 JSON 记录",
    limits: "支持 UTF-8 Explore JSONL，每份文件一个 Goal。最多 20 MiB、20,000 条记录；超出时整份拒绝，不截取部分。",
    boundary: "来源标记是文件中的声明，未在这里独立核验。时间条使用记录时间 recorded_at，不是执行耗时。阅读不会恢复历史状态或启动 Agent。",
    coverage: "每条来源记录均可阅读，包括相同事件的重复行。时间条复用 Explore 的去重事件投影；未知时间和缺失关联仍保持未知。",
    failed: "当前未加载这份文件中的任何记录。", retry: "重新选择文件即可重试。",
    issues: {size: "这份文件超过了阅读上限。", empty: "这份文件没有记录。", encoding: "这份文件不是有效的 UTF-8 文本。", json: "这一行不是有效的 JSON。", record: "这不是支持的 Explore 记录，或记录来自另一个 Goal。", identity: "同一个事件 ID 出现了冲突的记录。", read: "浏览器无法读取这份文件。"},
  },
};
type ReadState = {kind: "reading"} | {kind: "ready"; log: LocalExploreLog} | {kind: "error"; issue: LocalLogIssue; line: number | null};
type Props = {file: File; language: "en" | "zh"; onChoose: () => void; onClose: () => void};

export default function LocalLogReader({file, language, onChoose, onClose}: Props) {
  const c = copy[language];
  const [state, setState] = useState<ReadState>({kind: "reading"});
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    let active = true;
    title.current?.focus({preventScroll: true});
    void (async () => {
      if (file.size > LOCAL_LOG_MAX_BYTES) throw new LocalLogError("size");
      const bytes = await file.arrayBuffer();
      if (!active) return;
      const log = readLocalExploreLog(bytes);
      if (active) setState({kind: "ready", log});
    })().catch(error => {
      if (active) setState({kind: "error", issue: error instanceof LocalLogError ? error.issue : "read", line: error instanceof LocalLogError ? error.line : null});
    });
    return () => {active = false;};
  }, [file]);

  return <section className="tm-log" aria-busy={state.kind === "reading"}>
    <div className="tm-log-heading">
      <h1 className="tm-log-file" ref={title} id="tm-local-heading" tabIndex={-1} aria-label={`${c.title}: ${file.name}`}><FileText size={18} aria-hidden="true"/><bdi>{file.name}</bdi></h1>
      <div className="tm-log-actions"><button type="button" aria-label={c.choose} title={c.choose} onClick={onChoose}><FolderOpen size={16} aria-hidden="true"/><span>{c.choose}</span></button>
        <button type="button" aria-label={c.close} title={c.close} onClick={onClose}><X size={16} aria-hidden="true"/><span>{c.close}</span></button></div>
    </div>
    <p className="tm-log-privacy">{c.privacy}</p>
    {state.kind === "reading" ? <p className="tm-log-message" role="status">{c.reading}</p> : state.kind === "error" ?
      <div className="tm-log-message tm-log-error"><p role="alert">{c.issues[state.issue]} {state.line !== null && <span>{c.line} {state.line}.</span>}</p>
        <p>{c.failed} {c.retry}</p><p>{c.limits}</p><button type="button" onClick={onChoose}><FolderOpen size={16} aria-hidden="true"/>{c.choose}</button></div> :
      <LoadedLog log={state.log} language={language}/>}
  </section>;
}

function LoadedLog({log, language}: {log: LocalExploreLog; language: "en" | "zh"}) {
  const c = copy[language];
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [phase, setPhase] = useState<number | "unknown" | null>(null);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const original = useRef<HTMLHeadingElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const entries = useMemo(() => selectLocalLogEntries(log, query, status, phase), [log, query, status, phase]);
  const labels = useMemo(() => [...new Set(log.entries.map(entry => entry.event.status))], [log]);
  const selected = entries.find(entry => entry.line === selectedLine) ?? entries[0];
  const position = selected ? entries.indexOf(selected) : -1;
  const page = Math.max(0, Math.floor(position / 50));
  const pages = Math.ceil(entries.length / 50);
  const {timeline, history} = log;
  // Reuse the existing codec for Explore-authored section labels only. Other
  // summaries stay raw; the complete source text is always available unchanged.
  const sections = selected ? timeline.findings.find(item => item.event.event_id === selected.event.event_id)?.sections : null;
  const maxBin = Math.max(1, ...timeline.bins.map(bin => bin.events));
  const date = (value: number | null) => value === null ? c.unknown : new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-GB",
    {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short"}).format(value);
  function move(index: number) {
    setSelectedLine(entries[index]!.line);
    window.requestAnimationFrame(() => original.current?.focus());
  }
  function reset() { setQuery(""); setStatus(null); setPhase(null); search.current?.focus(); }

  return <>
    <div className="tm-log-overview">
      <p className="tm-log-span">{c.span}: {timeline.start === null ? c.unknown : <><time>{date(timeline.start)}</time> — <time>{date(timeline.end)}</time></>}</p>
      {timeline.bins.length > 0 && <svg className="tm-log-strip" viewBox="0 0 720 40" role="img" aria-label={`${c.span}: ${date(timeline.start)} — ${date(timeline.end)}`} preserveAspectRatio="none">
        {timeline.bins.map((bin, index) => <rect key={index} x={index * 720 / timeline.bins.length} y={40 - 36 * bin.events / maxBin}
          width={Math.max(1, 720 / timeline.bins.length - 3)} height={Math.max(1, 36 * bin.events / maxBin)}/>)}</svg>}
    </div>
    <div className="tm-log-filters">
      <label htmlFor="tm-log-search">{c.search}<input ref={search} id="tm-log-search" type="search" value={query} placeholder={c.placeholder} onChange={event => setQuery(event.target.value)}/></label>
      <label htmlFor="tm-log-label">{c.status}<select id="tm-log-label" value={status === null ? "" : JSON.stringify(status)} onChange={event => setStatus(event.target.value ? JSON.parse(event.target.value) as string : null)}>
        <option value="">{c.allLabels}</option>{labels.map(label => <option key={label} value={JSON.stringify(label)}>{label || c.emptyLabel}</option>)}</select></label>
      <label htmlFor="tm-log-time">{c.time}<select id="tm-log-time" value={phase === null ? "" : phase} onChange={event => setPhase(event.target.value === "" ? null : event.target.value === "unknown" ? "unknown" : Number(event.target.value))}>
        <option value="">{c.allTime}</option>{timeline.phases.map((item, index) => <option key={index} value={index}>{c.phases[index]} · {date(item.start)}</option>)}
        {history.unknownTimeCount > 0 && <option value="unknown">{c.unknown}</option>}</select></label>
    </div>
    <p className="tm-log-match" role="status">{c.match}: <strong>{entries.length}</strong> {c.of} {log.entries.length}
      {(query || status !== null || phase !== null) && <button type="button" onClick={reset}>{c.reset}</button>}</p>
    {!selected ? <div className="tm-log-message"><p>{c.noMatch}</p><button type="button" onClick={reset}>{c.reset}</button></div> :
      <div className="tm-log-reading">
        <div className="tm-log-detail"><article className="tm-original">
          <small>{sections ? c.structured : c.original}</small><p className="tm-record-meta"><time>{selected.event.recorded_at}</time> · {selected.event.status || c.emptyLabel} · {selected.event.event_kind}</p>
          <h2 ref={original} tabIndex={-1}>{selected.event.title}</h2>
          {sections ? <div className="tm-log-finding"><p className="tm-log-field-label">{c.observation}</p><p className="tm-log-observation">{sections.observation}</p>
            <p className="tm-log-field-label">{c.applicability}</p><p>{sections.applicability}</p>
            <p className="tm-log-field-label">{c.interpretation}</p><p>{sections.interpretation}</p>
            <details><summary>{c.fullOriginal}</summary><p className="tm-record-body">{selected.event.summary}</p></details></div> : <p className="tm-record-body">{selected.event.summary}</p>}
          <details className="tm-log-source"><summary>{c.details}</summary>
            <dl><dt>{c.line}</dt><dd>{selected.line}</dd><dt>Goal</dt><dd>{log.goalId}</dd><dt>Result</dt><dd>{selected.event.result_id}</dd><dt>Event</dt><dd>{selected.event.event_id}</dd></dl>
            {selected.event.evidence_refs.length > 0 && <><p>{c.references}</p><ul>{selected.event.evidence_refs.map((ref, index) => <li key={index}><code>{ref}</code></li>)}</ul></>}
            <p>{c.boundary}</p><p>{c.coverage}</p><p>{c.limits}</p>
            <details><summary>{c.raw}</summary><pre>{JSON.stringify(selected.source, null, 2)}</pre></details>
          </details>
        </article><div className="tm-log-record-nav"><button type="button" disabled={position === 0} onClick={() => move(position - 1)}><ArrowLeft size={16} aria-hidden="true"/>{c.previous}</button>
          <span>{position + 1}/{entries.length}</span><button type="button" disabled={position === entries.length - 1} onClick={() => move(position + 1)}>{c.next}<ArrowRight size={16} aria-hidden="true"/></button></div></div>
        <div className="tm-log-index"><ol aria-label={c.match}>
          {entries.slice(page * 50, (page + 1) * 50).map(entry => <li key={entry.line}><button type="button" aria-current={entry === selected ? "true" : undefined} onClick={() => move(entries.indexOf(entry))}>
            <span className="tm-log-row-meta">{c.line} {entry.line} · {entry.event.status || c.emptyLabel}</span><span>{entry.event.title}</span></button></li>)}
        </ol>{pages > 1 && <div className="tm-log-pagination"><button type="button" disabled={page === 0} onClick={() => move((page - 1) * 50)}>{c.previousPage}</button>
          <span>{c.page} {page + 1}/{pages}</span><button type="button" disabled={page === pages - 1} onClick={() => move((page + 1) * 50)}>{c.nextPage}</button></div>}</div>
      </div>}
    <p className="tm-log-note">{c.boundary}{history.unknownTimeCount > 0 && <> {history.unknownTimeCount} {c.unknownEvents}</>}</p>
  </>;
}
