import {useEffect, useRef, useState} from "react";
import {ArrowLeft, ArrowRight, ArrowUpRight, FolderOpen} from "lucide-react";
import {usePublicPageNavigation} from "../usePublicPageNavigation";
import {captureRevision, datasetUrl, readPinnedCapture, type CapturedRecord} from "./records";
import scenes from "./scenes.json";
import LocalLogReader from "./LocalLogReader";

const strings = {
  en: {
    back: "LoopX home", name: "Time Machine",
    title: "If you were there,\nwould you let it continue?",
    deck: "Make a choice for the Agent. Then see what the real record says.",
    caseNav: "Choose a research episode", count: "Episodes explored", stage: "Your decision",
    locked: "Choose first. Then see what happened.", lockedNote: "No quiz score. Just evidence worth questioning.",
    loading: "Reading the original capture…", error: "This capture could not be verified. No result is shown.", retry: "Try again",
    next: "Try another episode", chosen: "Your choice", choiceNote: "Both choices reveal the same recorded outcome; no alternative outcome is simulated.",
    boundary: "Curated from public solver records. Source labels are the solver’s claims, not independent replication or evidence of a LoopX performance gain.",
    local: "Your choices stay in this page’s memory. Reload starts over. No Agent is started.",
    reader: "Read the original records", openRecord: "Inspect this finding", readerHelp: "A single-record reader in original append order. It does not restore a research state or show everything known at a past time.",
    cursor: "Original record number", record: "Record", previous: "Previous record", following: "Next record", original: "Original text · untranslated", identifiers: "Source identifiers",
    sources: "Fixed public source", fixture: "This exact capture", totals: "76 + 134 + 105 = 315 Explore records. Record counts are not experiment counts.",
    close: "Back to the question", noJs: "Enable JavaScript to make choices and browse records. The fixed public archive is also available directly.",
    lesson: "What stays with the next decision?",
    openLog: "Open your research log", localStage: "Your research record",
  },
  zh: {
    back: "LoopX 首页", name: "时光机",
    title: "如果你在场，\n会让它继续吗？", deck: "先替 Agent 做一次选择，再看看真实记录里发生了什么。",
    caseNav: "选择研究案例", count: "已探索片段", stage: "你的判断", locked: "先做选择，再看结果。", lockedNote: "没有答题分数，只有值得追问的证据。",
    loading: "正在读取原始记录…", error: "无法核验这份记录，当前不显示结果。", retry: "重试",
    next: "换一个案例", chosen: "你选择了", choiceNote: "两种选择都展示同一段实际经过，不模拟另一种选择的结果。",
    boundary: "三道题编排自公开研究记录。来源中的状态是原 solver 的声明，未经这里独立复验，也不证明 LoopX 带来性能提升。",
    local: "选择只保留在当前页面内存，刷新后重新开始，不会启动 Agent。",
    reader: "自己翻原记录", openRecord: "核对这条发现", readerHelp: "按原追加顺序逐条阅读；不恢复研究状态，也不表示某个时点已知的全部知识。",
    cursor: "原始记录序号", record: "记录", previous: "上一条记录", following: "下一条记录", original: "原文 · 未翻译", identifiers: "来源标识",
    sources: "固定版本公开来源", fixture: "这份原始记录", totals: "76 + 134 + 105 = 315 条 Explore 记录，记录数量不是实验次数。",
    close: "回到问题", noJs: "启用 JavaScript 后可做选择、浏览原记录，也可以直接打开固定版本的公开来源。", lesson: "下一次判断，能留下什么？",
    openLog: "打开你的研究日志", localStage: "你的研究记录",
  },
};
const readCase = () => {
  const id = typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("case");
  return Math.max(0, scenes.findIndex(scene => scene.id === id));
};

export default function TimeMachine() {
  const [language, setLanguage] = usePublicPageNavigation("timeMachine");
  const [current, setCurrent] = useState(readCase);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [loaded, setLoaded] = useState<{id: string; rows: CapturedRecord[]} | null>(null);
  const [failedFor, setFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [position, setPosition] = useState(0);
  const [upload, setUpload] = useState<{file: File; version: number} | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const openLog = useRef<HTMLButtonElement>(null);
  const fileVersion = useRef(0);
  const localReading = upload !== null;
  const resultTitle = useRef<HTMLHeadingElement>(null);
  const questionTitle = useRef<HTMLHeadingElement>(null);
  const reader = useRef<HTMLDetailsElement>(null);
  const original = useRef<HTMLHeadingElement>(null);
  const pendingFocus = useRef(false);
  const c = strings[language];
  const scene = scenes[current];
  const records = loaded?.id === scene.id ? loaded.rows : null;
  const failed = failedFor === scene.id;
  const copy = scene[language];
  const answer = answers[scene.id];
  const chosen = answer !== undefined;
  const basePath = import.meta.env.BASE_URL;
  const fileUrl = `${basePath}lab/time-machine/data/${scene.file}`;
  const record = records?.[position];

  useEffect(() => {
    const restore = () => setCurrent(readCase());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let active = true;
    setLoaded(null); setFailed(null); setPosition(0);
    if (reader.current) reader.current.open = false;
    if (localReading) return () => {active = false; abort.abort();};
    void (async () => {
      const response = await fetch(fileUrl, {signal: abort.signal});
      if (!response.ok) throw new Error(`Capture HTTP ${response.status}`);
      const rows = await readPinnedCapture(await response.arrayBuffer(), scene);
      if (active) setLoaded({id: scene.id, rows});
    })().catch(() => {if (active) setFailed(scene.id);});
    return () => {active = false; abort.abort();};
  }, [fileUrl, scene, attempt, localReading]);

  useEffect(() => {
    if (chosen && records && pendingFocus.current) {
      pendingFocus.current = false;
      resultTitle.current?.focus({preventScroll: true});
      resultTitle.current?.closest(".tm-result")?.scrollIntoView({behavior: "instant", block: "nearest"});
    }
  }, [chosen, records]);

  function select(index: number) {
    if (index === current) return;
    // Clear synchronously: no previous case may appear under a new question.
    setLoaded(null); setFailed(null); setPosition(0); pendingFocus.current = false;
    setCurrent(index);
    const url = new URL(window.location.href);
    url.searchParams.set("case", scenes[index].id);
    url.hash = "";
    window.history.pushState(window.history.state, "", url);
    window.requestAnimationFrame(() => questionTitle.current?.focus({preventScroll: true}));
  }
  function choose(index: number) {
    pendingFocus.current = true;
    setAnswers(previous => ({...previous, [scene.id]: index}));
    if (records) setPosition(records.findIndex(row => row.result_id === scene.evidenceId));
  }
  function inspect() {
    if (!records || !reader.current) return;
    setPosition(records.findIndex(row => row.result_id === scene.evidenceId));
    reader.current.open = true;
    window.requestAnimationFrame(() => original.current?.focus());
  }

  return <div className="tm-page">
    <a className="skip-link" href={localReading ? "#tm-local-heading" : "#tm-question"}>{localReading ? c.localStage : c.stage}</a>
    <header className="tm-header">
      <a href={`${basePath}${language === "zh" ? "?lang=zh" : ""}`} className="brand" aria-label={c.back}>
        <span className="product-mark" aria-hidden="true"><span/><span/></span>LoopX <span className="tm-brand-suffix">/ {c.name}</span>
      </a>
      <div className="tm-language" aria-label="Language">
        <button type="button" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>EN</button>
        <button type="button" aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>中文</button>
      </div>
    </header>
    <main className="tm-shell">
      <input hidden ref={fileInput} id="tm-log-file" type="file" accept=".jsonl,application/x-ndjson,application/jsonl" aria-label={c.openLog} onChange={event => {
        const file = event.target.files?.[0];
        event.target.value = ""; // Selecting the same file again is a real retry.
        if (!file) return; // Cancelling the chooser leaves the current reading intact.
        fileVersion.current += 1;
        setUpload({file, version: fileVersion.current});
        window.scrollTo({top: 0, behavior: "instant"});
      }}/>
      {upload ? <LocalLogReader key={upload.version} file={upload.file} language={language} onChoose={() => fileInput.current?.click()} onClose={() => {
        setUpload(null);
        window.requestAnimationFrame(() => openLog.current?.focus());
      }}/> : <>
      <div className="tm-intro"><div><p className="tm-eyebrow">IF YOU WERE THERE</p><h1>{c.title}</h1><p>{c.deck}</p>
        <button ref={openLog} id="tm-open-log" className="tm-open-log" type="button" onClick={() => fileInput.current?.click()}><FolderOpen size={16} aria-hidden="true"/>{c.openLog}<span className="tm-log-format">Explore .jsonl</span></button></div>
        <p className="tm-counter">{c.count} {Object.keys(answers).length} / 3</p>
      </div>
      <nav className="tm-cases" aria-label={c.caseNav}>{scenes.map((item, index) =>
        <button key={item.id} type="button" aria-pressed={index === current} onClick={() => select(index)}>{item[language].label}</button>)}</nav>
      <section className="tm-stage" aria-label={c.stage}>
        <div className="tm-question"><div><p className="tm-tag">{copy.tag}</p>
          <h2 ref={questionTitle} id="tm-question" tabIndex={-1}>{copy.question}</h2><p>{copy.intro}</p></div>
          <div className="tm-choices">{copy.choices.map((choice, index) =>
            <button type="button" key={index} disabled={!records} aria-pressed={chosen && answer === index} onClick={() => choose(index)}>
              {choice}<ArrowUpRight size={18} aria-hidden="true"/>
            </button>)}</div>
        </div>
        <div className="tm-result" aria-busy={!records && !failed}>
          {failed ? <div className="tm-error"><p role="alert">{c.error}</p><button type="button" onClick={() => setAttempt(value => value + 1)}>{c.retry}</button></div> : !records ?
            <p role="status">{c.loading}</p> : !chosen ? <div className="tm-locked"><span aria-hidden="true">?↗</span><h3>{c.locked}</h3><p>{c.lockedNote}</p></div> :
            <div className="tm-revealed"><small>{copy.status}</small><h3 ref={resultTitle} tabIndex={-1}>{copy.title}</h3><p>{copy.body}</p>
              <dl className="tm-metrics">{copy.metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
              <p className="tm-your-choice">{c.chosen}: {copy.choices[answer]}. {c.choiceNote}</p>
              <div className="tm-result-actions"><button type="button" onClick={inspect}>{c.openRecord}</button>
                <button type="button" onClick={() => select((current + 1) % scenes.length)}>{c.next}<ArrowRight size={16} aria-hidden="true"/></button></div>
            </div>}
        </div>
      </section>
      <div className="tm-boundary"><p>{c.boundary}</p><p>{chosen && records ? copy.lesson : c.local}</p></div>
      <details className="tm-reader" ref={reader}><summary>{c.reader}<span>{scene.count} {c.record}</span></summary>
        <p>{c.readerHelp}</p>
        {!records ? <p role="status">{failed ? c.error : c.loading}</p> : <>
          <div className="tm-reader-controls"><label htmlFor="tm-cursor">{c.cursor} <strong>{position + 1} / {records.length}</strong></label>
            <input id="tm-cursor" type="range" min="1" max={records.length} value={position + 1} onChange={event => setPosition(Number(event.target.value) - 1)}/>
            <div><button type="button" disabled={position === 0} onClick={() => setPosition(value => value - 1)}><ArrowLeft size={16} aria-hidden="true"/>{c.previous}</button>
              <button type="button" disabled={position === records.length - 1} onClick={() => setPosition(value => value + 1)}>{c.following}<ArrowRight size={16} aria-hidden="true"/></button></div>
          </div>
          {record && <article className="tm-original"><small>{c.original}</small><p className="tm-record-meta"><time>{record.recorded_at}</time> · {record.status} · {record.event_kind}</p>
            <h3 ref={original} tabIndex={-1}>{record.title || c.record}</h3><p className="tm-record-body">{record.summary}</p>
            <details><summary>{c.identifiers}</summary><code>{record.result_id}<br/>{record.event_id}<br/>{captureRevision}</code></details>
          </article>}
          <a className="tm-source" href={fileUrl} target="_blank" rel="noopener">{c.fixture}<ArrowUpRight size={14} aria-hidden="true"/></a>
        </>}
        <button className="tm-return" type="button" onClick={() => {if (reader.current) reader.current.open = false; questionTitle.current?.focus();}}><ArrowLeft size={14} aria-hidden="true"/>{c.close}</button>
      </details>
      {Object.keys(answers).length === scenes.length && <section className="tm-takeaway"><h2>{c.lesson}</h2><p>{language === "zh" ? "失败的路线、有效的范围、尚未回答的问题。长程研究需要把这些一起带进下一轮。" : "Failed routes, useful scope, and unanswered questions. Long-running research needs to carry all three into the next round."}</p>
        <a href={`${basePath}docs/book/${language === "en" ? "en/" : ""}`}>{language === "zh" ? "了解 LoopX 如何延续工作" : "See how LoopX continues work"}<ArrowUpRight size={16} aria-hidden="true"/></a></section>}
      <footer className="tm-footer"><span>{c.totals}</span><a href={datasetUrl} target="_blank" rel="noopener" referrerPolicy="strict-origin-when-cross-origin">{c.sources}<ArrowUpRight size={14} aria-hidden="true"/></a></footer>
      <noscript><p>{c.noJs}</p></noscript>
      </>}
    </main>
  </div>;
}
