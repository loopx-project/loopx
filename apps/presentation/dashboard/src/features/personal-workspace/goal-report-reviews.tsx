import {useEffect, useMemo, useRef, useState} from "react";
import {RefreshCw} from "lucide-react";
import {fetchReportReviews, readReportReview, type ReportReviewRead, type ReportReviewRow} from "../../data/report-reviews";
import type {PersonalWorkspaceCallbacks, WorkspaceAttention} from "./personal-workspace-model";
import {attentionDetails} from "./attention-details";
import {AttentionActions} from "./attention-actions";
import {ContextHelp} from "./context-help";
import {MarkdownText} from "./markdown";
import {ResultCatalogReturn, ResultCatalogRow, useResultCatalogFocus} from "./result-catalog";
import "./report-reviews.css";

/** Generated HTML gets no scripts, forms, same-origin access or host functions.
 * Review controls stay in the trusted host; the exact Markdown remains readable. */
function inertReportHtml(content: string, zh: boolean): string {
  const document = new DOMParser().parseFromString(content, "text/html");
  // The built-in standalone renderer has script-driven search/copy/print.
  // Its static embedded reading view keeps the content and navigation, without
  // presenting controls that cannot run in the scriptless frame.
  document.querySelectorAll("script, .tools, [data-report-search]").forEach(node => node.remove());
  for (const anchor of document.querySelectorAll("a[href]")) {
    const href = anchor.getAttribute("href")!;
    if (href.startsWith("#")) anchor.setAttribute("href", `about:srcdoc${href}`);
    else {
      anchor.removeAttribute("href");
      anchor.setAttribute("aria-disabled", "true");
      anchor.setAttribute("title", document.documentElement.lang.startsWith("zh") ? "请在文字版打开来源链接" : "Open source links in text view");
    }
  }
  // The host already supplies the exact title and edition. Keep every summary,
  // period, highlight and source inside the reading view without a second hero.
  document.querySelector(".masthead h1")?.remove();
  const metadata = document.querySelector(".supporting-body");
  // Counts and the reporting window remain available with the other report
  // facts. The reading viewport starts with editorial summaries and content.
  for (const node of document.querySelectorAll(".masthead .eyebrow, .masthead .period, .report-facts.fallback")) {
    if (metadata) metadata.append(node);
  }
  const mast = document.querySelector(".masthead");
  if (mast && !mast.textContent?.trim()) mast.remove();
  const rail = document.querySelector(".rail");
  if (rail) {
    const index = document.createElement("details");
    index.className = "embedded-index";
    const summary = document.createElement("summary");
    summary.textContent = zh ? "目录" : "Contents";
    index.append(summary);
    const nav = rail.querySelector("nav");
    if (nav) index.append(nav);
    rail.replaceWith(index);
  }
  const embedded = document.createElement("style");
  embedded.textContent = ".shell{width:100%;max-width:none;margin:0;padding:24px;box-sizing:border-box}.masthead{padding:0 0 16px;border-top:0}.mast-grid{gap:16px}.layout{grid-template-columns:minmax(0,1fr);margin-top:0;gap:16px}.masthead+.layout{margin-top:16px}.embedded-index summary{cursor:pointer;min-height:32px}.section-heading h2{font-size:24px}.report-row{padding:16px 0}.report-facts.fallback{grid-template-columns:repeat(3,minmax(0,1fr))}.report-facts.fallback .fact{min-width:0}a[aria-disabled=true]{color:inherit;text-decoration:none;cursor:help}@media(max-width:640px){.shell{padding:16px}}";
  document.head.append(embedded);
  const policy = document.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'";
  document.head.prepend(policy);
  return "<!doctype html>" + document.documentElement.outerHTML;
}

export function GoalReportReviews({goalId, sourceId, zh, focusTodoId, refreshKey, callbacks, readOnly = false}: {
  goalId: string; sourceId?: string; zh: boolean; focusTodoId?: string; refreshKey: string;
  callbacks: PersonalWorkspaceCallbacks; readOnly?: boolean;
}) {
  const [rows, setRows] = useState<ReportReviewRow[]>([]);
  const [selected, setSelected] = useState<ReportReviewRead | null>(null);
  const [reading, setReading] = useState<string | null>(focusTodoId ?? null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [textOnly, setTextOnly] = useState(false);
  const generation = useRef(0);
  const navigation = useRef("");
  const selectedId = useRef<string | null>(focusTodoId ?? null);
  const focus = useResultCatalogFocus();
  const storageKey = JSON.stringify(["loopx-report-reading", sourceId ?? location.origin, goalId]);
  useEffect(() => {
    const destination = JSON.stringify([storageKey, focusTodoId]);
    if (navigation.current !== destination) {
      navigation.current = destination;
      let saved: string | null = null;
      try {saved = sessionStorage.getItem(storageKey);} catch { /* Reading works without storage. */ }
      selectedId.current = focusTodoId ?? saved;
      setReading(selectedId.current); setRows([]); setLoaded(false); setTextOnly(false);
    }
    void load();
    return () => {generation.current++; focus.cancel();};
  }, [goalId, sourceId, focusTodoId, refreshKey]);

  async function read(todoId: string, current: number) {
    const value = await readReportReview(goalId, todoId);
    if (current !== generation.current) return;
    if (value.goal_id !== goalId || value.review.todo_id !== todoId || value.todo.todo_id !== todoId)
      throw Error(zh ? "材料来源已变化" : "Material source changed");
    setSelected(value);
    try {sessionStorage.setItem(storageKey, todoId);} catch { /* Reading works without storage. */ }
  }
  async function load(next?: string) {
    const current = ++generation.current;
    setBusy(true); setError(""); setSelected(null);
    try {
      const page = await fetchReportReviews(goalId, next);
      if (current !== generation.current) return;
      if (!page.available) {
        setRows([]); setCursor(null); setLoaded(true);
        if (selectedId.current) setError(zh ? "请先按 Goal 的提示完成权威存储迁移，再刷新原材料。" : "Complete the Goal's authority migration, then refresh the original material.");
        return;
      }
      setRows(previous => next ? [...previous, ...page.items] : page.items);
      setCursor(page.next_cursor); setLoaded(true);
      if (selectedId.current) await read(selectedId.current, current);
    } catch {
      if (current === generation.current) setError(zh ? "材料或审阅状态暂不可读，请重试。" : "Material or review state is unavailable. Retry the read.");
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function select(row: ReportReviewRow) {
    const current = ++generation.current;
    selectedId.current = row.todo_id; setReading(row.todo_id); setSelected(null); setTextOnly(false);
    try {sessionStorage.setItem(storageKey, row.todo_id);} catch { /* Selection is optional. */ }
    setBusy(true); setError(""); focus.reader();
    try {await read(row.todo_id, current);}
    catch {if (current === generation.current) setError(zh ? "原版本已不可读取，请重试。" : "The original edition cannot be read. Retry.");}
    finally {if (current === generation.current) setBusy(false);}
  }
  const label = (row: ReportReviewRow) => row.superseded_by ? (zh ? "已有修订" : "Superseded")
    : row.status === "done" ? (zh ? "审阅已处理" : "Review handled")
      : row.status === "deferred" ? (zh ? "稍后审阅" : "Review deferred")
        : ["open", "blocked"].includes(row.status) ? (zh ? "待验收与反馈" : "Awaiting review") : row.status;
  const markdown = selected?.artifacts.find(item => item.renderer_id === "markdown_v0");
  const html = selected?.artifacts.find(item => item.renderer_id === "html_artifact_v0");
  const htmlView = useMemo(() => html ? inertReportHtml(html.content, zh) : "", [html?.content, zh]);
  function returnToCatalog() {
    generation.current++; selectedId.current = null; setReading(null); setSelected(null); setBusy(false); setError("");
    try {sessionStorage.removeItem(storageKey);} catch { /* Optional selection. */ }
    if (reading) focus.row(reading);
  }
  const attention: WorkspaceAttention | null = selected ? {goalId, sourceId, todoId: selected.review.todo_id,
    text: selected.review.title, blocking: false, decisionSource: "todo", details: {...attentionDetails(selected.todo),
      ...(selected.source_session_id ? {reportSessionId: selected.source_session_id} : {})}} : null;
  if (loaded && !rows.length && !reading && !error) return null;
  return <section ref={focus.root} className="goal-report-reviews" aria-label={zh ? "审阅材料" : "Review materials"} aria-busy={busy}>
    <header data-result-reader={reading ? "" : undefined} tabIndex={reading ? -1 : undefined}>{reading ? <ResultCatalogReturn zh={zh} onReturn={returnToCatalog}/> : <h3>{zh ? "审阅材料" : "Review materials"}</h3>}
      <div className="report-review-tools"><ContextHelp label={zh ? "审阅与验收范围" : "Review scope"}>
      <p>{zh ? "阅读不会完成验收。每份材料的审阅分别记录；处理材料不等于整个 Goal 验收通过。反馈回到负责 Agent 的原工作对话。页面版保留静态目录；外部来源可从文字版打开。" : "Reading does not accept a result. Each edition has its own review; handling it does not accept the entire Goal. Feedback returns to its responsible Agent's work conversation. Page view keeps the static index; open external sources from text view."}</p>
    </ContextHelp><button type="button" disabled={busy} onClick={() => void load()}><RefreshCw size={14}/>{zh ? "刷新" : "Refresh"}</button></div></header>
    {busy ? <p role="status">{zh ? "正在读取材料…" : "Reading material…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <nav data-result-catalog tabIndex={-1} hidden={Boolean(reading)} aria-label={zh ? "材料目录" : "Material catalog"}>
      {rows.map(row => <ResultCatalogRow key={row.todo_id} resultKey={row.todo_id} title={row.title}
        source={row.created_by ?? row.bound_agent ?? ""} status={label(row)} time={row.updated_at} zh={zh} disabled={busy}
        onSelect={() => void select(row)}/>)}
      {cursor ? <button type="button" disabled={busy} onClick={() => void load(cursor)}>{zh ? "更多材料" : "More materials"}</button> : null}
    </nav>
    {reading ? <article className="report-review-reader">
      {selected ? <><div className="report-review-masthead"><h2>{selected.document.title}</h2>
        <div><span>{label(selected.review)}</span><time dateTime={selected.document.generated_at}>{new Date(selected.document.generated_at).toLocaleString(zh ? "zh-CN" : "en")}</time>
          {html && markdown ? <button type="button" onClick={() => setTextOnly(value => !value)}>{textOnly ? (zh ? "页面版" : "Page view") : (zh ? "文字版" : "Text view")}</button> : null}</div></div>
        {attention ? <div className="report-review-actions"><AttentionActions item={attention} callbacks={callbacks}
          fallbackAgentId={selected.review.bound_agent ?? ""} readOnly={readOnly}/></div> : null}
        {html && !textOnly ? <iframe title={selected.document.title} sandbox="" referrerPolicy="no-referrer" srcDoc={htmlView}/>
          : markdown ? <div className="report-review-prose"><MarkdownText text={markdown.content}/></div> : <p role="alert">{zh ? "缺少可读正文" : "Readable content is missing"}</p>}
        <details><summary>{zh ? "来源与版本" : "Source and edition"}</summary><p>{selected.generation_id}</p><code>{selected.review.bundle_sha256}</code></details>
      </> : null}
    </article> : null}
  </section>;
}
