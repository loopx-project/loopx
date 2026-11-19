import {ChevronRight, FileText, RefreshCw} from "lucide-react";
import {useEffect, useRef, useState} from "react";
import {requestJson} from "../../data/chat";
import {sourceReadApiUrl} from "../../data/local-status-query";
import {ContextHelp} from "./context-help";
import {MarkdownText} from "./markdown";
import "./goal-source-document.css";

type Document = {text: string; sha256: string; captured: boolean};
type Read = {scope: string; document?: Document; error?: string; busy: boolean};

/** An optional original-file reader, separate from the Goal's current intent. */
export function GoalSourceDocument({goalId, originalGoalId, sourceUrl, zh}: {
  goalId: string; originalGoalId: string; sourceUrl: string; zh: boolean;
}) {
  const namespace = new URL(sourceUrl, window.location.href).searchParams.get("source_namespace");
  const scope = JSON.stringify([goalId, originalGoalId, sourceUrl]);
  const details = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false), [revision, setRevision] = useState(0);
  const [read, setRead] = useState<Read>({scope, busy: false});
  const current = read.scope === scope ? read : {scope, busy: true};
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setRead(previous => ({scope, document: previous.scope === scope ? previous.document : undefined, busy: true}));
    const query = new URLSearchParams({goal_id: goalId, source_namespace: namespace ?? ""});
    void requestJson<{ok: boolean; goal_id: string; source: {namespace: string}; document: Document}>(
      sourceReadApiUrl(sourceUrl, `/api/chat/goal-source-document?${query}`), {signal: controller.signal},
    ).then(value => {
      if (!value.ok || value.goal_id !== originalGoalId || value.source?.namespace !== namespace
        || typeof value.document?.text !== "string" || !value.document.text.length
        || typeof value.document.captured !== "boolean"
        || !/^[a-f0-9]{64}$/.test(value.document.sha256)
        || new TextEncoder().encode(value.document.text).length > 128 * 1024) throw new Error("document identity mismatch");
      if (!controller.signal.aborted) setRead({scope, document: value.document, busy: false});
    }).catch(() => {
      if (!controller.signal.aborted) setRead(previous => ({scope,
        document: previous.scope === scope ? previous.document : undefined, busy: false,
        error: zh ? "原始任务文件暂不可读。请恢复此来源或核对文档绑定后重试。" : "The original task file is unavailable. Restore this source or check its document binding, then retry."}));
    });
    return () => controller.abort();
  }, [open, revision, scope, goalId, originalGoalId, namespace, sourceUrl, zh]);
  return <details className="source-goal-document" ref={details} onToggle={event => setOpen(event.currentTarget.open)}
    onKeyDown={event => {if (event.key === "Escape") {
      event.stopPropagation(); if (details.current) details.current.open = false;
      details.current?.querySelector("summary")?.focus();
    }}}>
    <summary><ChevronRight className="source-document-chevron" size={14} aria-hidden/><FileText size={15} aria-hidden/>{zh ? "原始任务" : "Original task"}</summary>
    {open ? <section aria-label={zh ? "原始任务文件" : "Original task file"}>
      <header><span>{current.document?.captured ? (zh ? "捕获副本" : "Captured copy") : (zh ? "来源文件" : "Source file")}</span>
        <ContextHelp label={zh ? "原始任务来源" : "Original task source"}><p>{zh
          ? "读取原 Goal 登记的任务文件；它与后来的目标修订和 Agent 策略分别保留。浏览不会执行文档中的命令或启动分析。"
          : "The task file declared by the original Goal, kept separately from later objective revisions and Agent strategies. Reading runs no document commands or analysis."}</p></ContextHelp>
        <button type="button" onClick={() => setRevision(value => value + 1)} disabled={current.busy}
          aria-label={zh ? "刷新原始任务" : "Refresh original task"}><RefreshCw size={15}/></button>
      </header>
      {current.busy ? <p role="status">{zh ? "正在读取完整原文…" : "Reading the complete original…"}</p> : null}
      {current.error ? <div role="alert"><p>{current.document ? (zh ? "刷新失败，保留上次读取的原文。" : "Refresh failed; the last original is preserved.") : current.error}</p>
        <button type="button" onClick={() => setRevision(value => value + 1)}>{zh ? "重试读取原始任务" : "Retry original task"}</button></div> : null}
      {current.document ? <>
        <article tabIndex={0}><MarkdownText text={current.document.text}/></article>
        <details className="source-goal-document-provenance"><summary>{zh ? "文档来源与版本" : "Document source and version"}</summary>
          {current.document.captured ? <p>{zh ? "显示此来源配置的捕获副本，按固定内容版本核对；未重新访问原路径。来源绑定不代表独立核实。" : "Showing this source's configured captured copy, checked against its pinned content version. The original path was not reread; binding does not establish independent verification."}</p> : null}
          <dl><dt>Goal</dt><dd>{originalGoalId}</dd><dt>{zh ? "来源" : "Source"}</dt><dd>{namespace}</dd><dt>SHA-256</dt><dd>{current.document.sha256}</dd></dl>
          <pre tabIndex={0}>{current.document.text}</pre>
        </details>
      </> : null}
    </section> : null}
  </details>;
}
