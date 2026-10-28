import {useEffect, useMemo, useRef, useState} from "react";

import {fetchChatSession, type ChatSessionSnapshot, type ChatVisibleMessage} from "../../data/chat";
import {visibleAgentMessage} from "./answer-text";
import {readWorkspaceLocale} from "./i18n";
import {resolveConversationScope} from "../../../../../../loopx/control_plane/collaboration/conversation_scope";
import {ReturnDeliveryStatus} from "./return-delivery-status";
import {MarkdownText} from "./markdown";
import {markdownOutline, parseMarkdownBlocks} from "./markdown-blocks";
import {readWorkspaceTheme} from "./workspace-theme";
import "./personal-workspace.css";
import "./goal-loopx-mode.css";
import "./answer-report-page.css";

type ReportState =
  | {kind: "loading"}
  | {kind: "ready"; snapshot: ChatSessionSnapshot; message: ChatVisibleMessage}
  | {kind: "missing"; snapshot: ChatSessionSnapshot}
  | {kind: "error"};

export function AnswerReportPage({sessionId, messageId, statusUrl}: {
  sessionId: string;
  messageId: string;
  statusUrl: string;
}) {
  const scope = JSON.stringify([statusUrl || "/status.json", sessionId, messageId]);
  const [loaded, setLoaded] = useState<{scope: string; value: ReportState} | null>(null);
  // A late read from the preceding source never appears in the new reader.
  const state: ReportState = loaded?.scope === scope ? loaded.value : {kind: "loading"};
  const [readRevision, setReadRevision] = useState(0);
  const manualCopy = useRef<HTMLTextAreaElement>(null);
  const copyAttempt = useRef(0);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const zh = readWorkspaceLocale() === "zh-CN";

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    copyAttempt.current += 1;
    const previousTitle = document.title;
    document.title = zh ? "答复记录 · LoopX" : "Answer record · LoopX";
    setLoaded({scope, value: {kind: "loading"}});
    setCopied(false);
    setCopyFailed(false);
    void fetchChatSession(sessionId, controller.signal).then((snapshot) => {
      if (!live) return;
      if (snapshot.ok !== true || snapshot.schema_version !== "loopx_chat_store_v1"
        || snapshot.session.session_id !== sessionId) throw new Error("The saved Session changed during read.");
      const message = snapshot.messages.find((item) => item.message_id === messageId
        && ["agent", "assistant"].includes(item.role));
      setLoaded({scope, value: message ? {kind: "ready", snapshot, message} : {kind: "missing", snapshot}});
    }).catch(() => { if (live) setLoaded({scope, value: {kind: "error"}}); });
    return () => {live = false; controller.abort(); copyAttempt.current += 1; document.title = previousTitle;};
  }, [scope, sessionId, messageId, zh, readRevision]);

  const navigation = answerReportNavigation(
    (state.kind === "ready" || state.kind === "missing") ? state.snapshot.session : null, window.location.href, statusUrl, zh,
  );
  const closeOrReturn = () => {
    if (window.opener) window.close();
    else window.location.assign(navigation.url);
  };

  const answer = state.kind === "ready" ? state.message : null;
  const answerText = answer ? visibleAgentMessage(answer.text) : "";
  const headingPrefix = `answer-${sessionId}-${messageId}`;
  const headings = useMemo(() => markdownOutline(parseMarkdownBlocks(answerText), headingPrefix), [answerText, headingPrefix]);
  useEffect(() => {
    if (!copyFailed) return;
    manualCopy.current?.focus(); manualCopy.current?.select();
  }, [copyFailed]);
  useEffect(() => {
    if (state.kind !== "ready") return;
    let frame = window.requestAnimationFrame(() => {
      frame = window.requestAnimationFrame(() => {
        const anchor = headings.find(heading => `#${encodeURIComponent(heading.id)}` === window.location.hash
          || `#${heading.id}` === window.location.hash);
        if (anchor) document.getElementById(anchor.id)?.scrollIntoView({block: "start"});
        else window.scrollTo({top: readAnswerPosition(scope), behavior: "instant"});
      });
    });
    const save = () => saveAnswerPosition(scope, window.scrollY);
    window.addEventListener("pagehide", save);
    return () => {window.cancelAnimationFrame(frame); window.removeEventListener("pagehide", save); save();};
  }, [scope, state.kind, headings]);
  async function copyAnswer() {
    const attempt = ++copyAttempt.current;
    setCopyFailed(false);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(answerText);
      if (attempt === copyAttempt.current) setCopied(true);
    } catch {if (attempt === copyAttempt.current) {setCopied(false); setCopyFailed(true);}}
  }
  const sourceQuestion = state.kind === "ready" && answer?.turn_id
    ? state.snapshot.messages.find((item) => item.turn_id === answer?.turn_id && item.role === "user")
    : null;
  const created = answer?.created_at ? new Date(answer.created_at) : null;
  const dateLabel = created && !Number.isNaN(created.valueOf())
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {dateStyle: "medium", timeStyle: "short"}).format(created)
    : null;

  return <main className="answer-report-page personal-workspace-shell" data-pw-theme={readWorkspaceTheme()}>
    <div className="answer-report-shell">
      <header className="answer-report-header">
        <div><span className="answer-report-brand">LoopX</span><span className="answer-report-context">
          {navigation.context}
        </span></div>
        <button type="button" onClick={closeOrReturn}>{window.opener ? (zh ? "关闭答复" : "Close answer") : navigation.label}</button>
      </header>
      <article className="answer-report-body">
        <div className="answer-report-heading"><div><small>{zh ? "已保存的答复" : "Saved answer"}</small>
          <AnswerReportSummary message={answer ?? undefined} zh={zh}/>
          {dateLabel ? <p><time dateTime={answer?.created_at}>{dateLabel}</time></p> : null}
        </div>{answer ? <button type="button" onClick={() => void copyAnswer()}>{copied ? (zh ? "已复制 Markdown" : "Markdown copied") : (zh ? "复制 Markdown" : "Copy Markdown")}</button> : null}</div>
        {copyFailed ? <div className="answer-report-copy-fallback"><p role="alert">{zh ? "复制失败。可从下面的 Markdown 原文中选中并复制。" : "Copy failed. Select and copy the Markdown source below."}</p>
          <textarea ref={manualCopy} aria-label={zh ? "答复 Markdown 原文" : "Answer Markdown source"} readOnly value={answerText} rows={8}/></div> : null}
        {state.kind === "loading" ? <p role="status">{zh ? "正在读取已保存的答复…" : "Loading the saved answer…"}</p> : null}
        {state.kind === "error" ? <p role="alert">{zh ? "暂时无法读取这份答复。" : "Could not load this answer."} <button type="button" onClick={() => setReadRevision(value => value + 1)}>{zh ? "重试读取" : "Retry reading"}</button></p> : null}
        {state.kind === "missing" ? <p role="alert">{zh ? "找不到这份答复。它可能已被清理，或链接有误。" : "This answer was not found. It may have been removed, or the link may be incorrect."}</p> : null}
        {answer ? <>
          {headings.length > 1 ? <details className="answer-report-outline"><summary>{zh ? "目录" : "Contents"}</summary>
            <nav aria-label={zh ? "答复目录" : "Answer contents"}><ol>{headings.map(heading => <li key={heading.id} data-level={heading.level}>
              <a href={`#${encodeURIComponent(heading.id)}`} onClick={() => window.requestAnimationFrame(() => document.getElementById(heading.id)?.focus({preventScroll: true}))}>{heading.text}</a>
            </li>)}</ol></nav></details> : null}
          <div className="answer-report-content"><MarkdownText text={answerText} headingPrefix={headingPrefix}/></div>
          <footer>
            <details><summary>{zh ? "来源与版本" : "Source and version"}</summary><p>{zh ? "来自本机保存的对话消息；打开此页不会重新运行 Agent。" : "From a locally saved conversation message. Opening this page does not rerun the Agent."}</p>
              <dl><dt>{zh ? "消息" : "Message"}</dt><dd><code>{answer.message_id}</code></dd>
                <dt>Session</dt><dd><code>{sessionId}</code></dd></dl>
              {sourceQuestion?.text ? <p><strong>{zh ? "原问题：" : "Original question: "}</strong>{sourceQuestion.reply_context?.source_text ?? sourceQuestion.text}</p> : null}
            </details>
          </footer>
        </> : null}
      </article>
    </div>
  </main>;
}

const readingStorageKey = "loopx-answer-reading-v1";
function storedAnswerPositions(): Array<[string, number]> {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(readingStorageKey) ?? "[]");
    return Array.isArray(value) ? value.filter((entry): entry is [string, number] => Array.isArray(entry)
      && typeof entry[0] === "string" && typeof entry[1] === "number" && Number.isFinite(entry[1]) && entry[1] >= 0).slice(-40) : [];
  } catch {return [];}
}
function readAnswerPosition(scope: string) {return storedAnswerPositions().find(([key]) => key === scope)?.[1] ?? 0;}
function saveAnswerPosition(scope: string, top: number) {
  try {sessionStorage.setItem(readingStorageKey, JSON.stringify([...storedAnswerPositions().filter(([key]) => key !== scope), [scope, Math.max(0, top)]].slice(-40)));}
  catch { /* Reading remains available when browser storage is disabled. */ }
}

/** Present and return to the trusted original conversation, never its storage Goal. */
export function answerReportNavigation(
  session: Partial<ChatSessionSnapshot["session"]> | null,
  currentUrl: string, statusUrl: string, zh: boolean,
) {
  const scope = resolveConversationScope(session ?? {});
  const goal = scope.kind === "owner_goal" ? scope.goal_ids[0] : "";
  const url = new URL(currentUrl);
  const sessionId = session?.session_id ?? url.searchParams.get("reportSessionId") ?? "";
  const messageId = url.searchParams.get("reportMessageId") ?? "";
  // Public Session summaries intentionally omit private project context. These
  // references only restore a read selection; the existing catalog/binding
  // reader still verifies the owner's grant before opening the conversation.
  const projectRef = session?.goal_id === null && /^[a-f0-9]{24}$/.test(session.project_ref ?? "")
    ? session.project_ref : null;
  const localProject = Boolean(projectRef && session?.channel_id === `project.${projectRef}`);
  const externalProject = projectRef ? session?.channel_id?.match(/^project\.external\.([a-f0-9]{24})\.[a-f0-9]{24}$/u) : null;
  const bound = Boolean(session && sessionId && url.searchParams.get("conversationSession") === sessionId
    && /^[a-f0-9]{24}$/.test(url.searchParams.get("conversationBinding") ?? "")
    && (scope.kind === "external_audience" || scope.kind === "project_workspace"
      || externalProject?.[1] === url.searchParams.get("conversationBinding")));
  const managed = scope.kind === "owner_goal" && session?.managed_agent_id;
  url.searchParams.delete("reportSessionId");
  url.searchParams.delete("reportMessageId");
  url.searchParams.delete("todo");
  url.searchParams.delete("messageSession");
  url.searchParams.delete("message");
  url.searchParams.set("goalId", goal);
  url.searchParams.set("view", "conversation");
  url.hash = "";
  if (!bound) {
    url.searchParams.delete("conversationBinding");
    url.searchParams.delete("conversationSession");
  }
  if (managed) {
    url.searchParams.set("workAgent", managed);
    url.searchParams.set("workSession", `managed:${sessionId}`);
  } else {
    url.searchParams.delete("workAgent"); url.searchParams.delete("workSession");
  }
  if (localProject && projectRef) url.searchParams.set("workspace", projectRef);
  else url.searchParams.delete("workspace");
  if ((scope.kind === "owner_goal" || scope.kind === "owner_portfolio" || localProject || bound)
    && /^[A-Za-z0-9._-]{1,160}$/.test(sessionId) && /^[A-Za-z0-9._-]{1,160}$/.test(messageId)) {
    url.searchParams.set("messageSession", sessionId);
    url.searchParams.set("message", messageId);
  }
  if (statusUrl) url.searchParams.set("statusUrl", statusUrl);
  return {
    url: url.toString(),
    context: scope.kind === "owner_goal" ? goal
      : scope.kind === "owner_portfolio" ? (zh ? "管家" : "Steward")
      : scope.kind === "external_audience" ? (zh ? "管家 · 外部对话" : "Steward · External conversation")
      : (zh ? "已保存的对话" : "Saved conversation"),
    label: bound ? (zh ? "返回原对话" : "Back to original conversation")
      : scope.kind === "owner_goal" ? (zh ? "返回对话" : "Back to conversation")
      : scope.kind === "owner_portfolio" ? (zh ? "返回管家" : "Back to Steward")
      : scope.kind === "external_audience" ? (zh ? "打开管家" : "Open Steward")
      : (zh ? "打开工作区" : "Open workspace"),
  };
}

/** Delivery confirms receipt; the existing phase alone distinguishes a progress update. */
export function AnswerReportSummary({message, zh}: {message?: ChatVisibleMessage; zh: boolean}) {
  const delivery = message?.return_delivery;
  return <>
    <h1>{delivery?.phase === "decision" ? (zh ? "进度更新" : "Progress update")
      : delivery?.phase === "conclusion" ? (zh ? "处理结论" : "Conclusion")
      : (zh ? "完整答复" : "Full answer")}</h1>
    <ReturnDeliveryStatus delivery={delivery}/>
  </>;
}
