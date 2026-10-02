import {useEffect, useState} from "react";

import {fetchChatSession, type ChatSessionSnapshot, type ChatVisibleMessage} from "../../data/chat";
import {visibleAgentMessage} from "./answer-text";
import {readWorkspaceLocale} from "./i18n";
import {resolveConversationScope} from "../../../../../../loopx/control_plane/collaboration/conversation_scope";
import {ReturnDeliveryStatus} from "./return-delivery-status";
import {MarkdownText} from "./markdown";
import "./personal-workspace.css";
import "./goal-loopx-mode.css";
import "./answer-report-page.css";

type ReportState =
  | {kind: "loading"}
  | {kind: "ready"; snapshot: ChatSessionSnapshot; message: ChatVisibleMessage}
  | {kind: "missing"}
  | {kind: "error"};

export function AnswerReportPage({sessionId, messageId, statusUrl}: {
  sessionId: string;
  messageId: string;
  statusUrl: string;
}) {
  const [state, setState] = useState<ReportState>({kind: "loading"});
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const zh = readWorkspaceLocale() === "zh-CN";

  useEffect(() => {
    let live = true;
    const previousTitle = document.title;
    document.title = zh ? "答复记录 · LoopX" : "Answer record · LoopX";
    setState({kind: "loading"});
    void fetchChatSession(sessionId).then((snapshot) => {
      if (!live) return;
      const message = snapshot.messages.find((item) => item.message_id === messageId
        && ["agent", "assistant"].includes(item.role));
      setState(message ? {kind: "ready", snapshot, message} : {kind: "missing"});
    }).catch(() => { if (live) setState({kind: "error"}); });
    return () => { live = false; document.title = previousTitle; };
  }, [sessionId, messageId, zh]);

  const navigation = answerReportNavigation(
    state.kind === "ready" ? state.snapshot.session : null, window.location.href, statusUrl, zh,
  );
  const closeOrReturn = () => {
    if (window.opener) window.close();
    else window.location.assign(navigation.url);
  };

  const answer = state.kind === "ready" ? state.message : null;
  const answerText = answer ? visibleAgentMessage(answer.text) : "";
  const sourceQuestion = state.kind === "ready"
    ? state.snapshot.messages.find((item) => item.turn_id === answer?.turn_id && item.role === "user")
    : null;
  const created = answer?.created_at ? new Date(answer.created_at) : null;
  const dateLabel = created && !Number.isNaN(created.valueOf())
    ? new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {dateStyle: "medium", timeStyle: "short"}).format(created)
    : null;

  return <main className="answer-report-page">
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
        </div>{answer ? <button type="button" onClick={() => {
          setCopyFailed(false);
          void navigator.clipboard.writeText(answerText).then(() => setCopied(true)).catch(() => {
            setCopied(false);
            setCopyFailed(true);
          });
        }}>{copied ? (zh ? "已复制 Markdown" : "Markdown copied") : (zh ? "复制 Markdown" : "Copy Markdown")}</button> : null}</div>
        {copyFailed ? <p role="alert">{zh ? "复制失败。请从正文中选中并复制。" : "Copy failed. Select and copy the answer text instead."}</p> : null}
        {state.kind === "loading" ? <p role="status">{zh ? "正在读取已保存的答复…" : "Loading the saved answer…"}</p> : null}
        {state.kind === "error" ? <p role="alert">{zh ? "暂时无法读取这份答复；请检查本机 LoopX 服务。" : "Could not load this answer. Check the local LoopX service."}</p> : null}
        {state.kind === "missing" ? <p role="alert">{zh ? "找不到这份答复。它可能已被清理，或链接有误。" : "This answer was not found. It may have been removed, or the link may be incorrect."}</p> : null}
        {answer ? <>
          <div className="answer-report-content"><MarkdownText text={answerText}/></div>
          <footer><p>{zh ? "来自本机保存的对话消息；打开此页不会重新运行 Agent。" : "From a locally saved conversation message. Opening this page does not rerun the Agent."}</p>
            <details><summary>{zh ? "来源与版本" : "Source and version"}</summary>
              <dl><dt>{zh ? "消息" : "Message"}</dt><dd><code>{answer.message_id}</code></dd>
                <dt>Session</dt><dd><code>{sessionId}</code></dd></dl>
              {sourceQuestion?.text ? <p><strong>{zh ? "原问题：" : "Original question: "}</strong>{sourceQuestion.text}</p> : null}
            </details>
          </footer>
        </> : null}
      </article>
    </div>
  </main>;
}

/** Present the trusted conversation scope, rather than its storage Goal. */
export function answerReportNavigation(
  session: Pick<ChatSessionSnapshot["session"], "goal_id" | "channel_id"> | null,
  currentUrl: string, statusUrl: string, zh: boolean,
) {
  const scope = resolveConversationScope({goal_id: session?.goal_id, channel_id: session?.channel_id});
  const goal = scope.kind === "owner_goal" ? scope.goal_ids[0] : "";
  const url = new URL(currentUrl);
  url.searchParams.delete("reportSessionId");
  url.searchParams.delete("reportMessageId");
  url.searchParams.set("goalId", goal);
  url.searchParams.set("view", "conversation");
  if (statusUrl) url.searchParams.set("statusUrl", statusUrl);
  return {
    url: url.toString(),
    context: scope.kind === "owner_goal" ? goal
      : scope.kind === "owner_portfolio" ? (zh ? "管家" : "Steward")
      : scope.kind === "external_audience" ? (zh ? "管家 · 外部对话" : "Steward · External conversation")
      : (zh ? "已保存的对话" : "Saved conversation"),
    label: scope.kind === "owner_goal" ? (zh ? "返回对话" : "Back to conversation")
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
