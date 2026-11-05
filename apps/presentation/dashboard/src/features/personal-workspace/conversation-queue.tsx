import { useEffect, useRef, useState } from "react";
import { CornerUpLeft, RefreshCw } from "lucide-react";
import { ChatApiError, fetchChatSessionHistory, steerQueuedChatTurn, type ChatQueuedTurn, type ChatVisibleMessage } from "../../data/chat";
import { composerKeyAction } from "./composer-keyboard";
import { DocumentAttachment, isTextDocument } from "./document-attachment";
import { ContextHelp } from "./context-help";
import { useWorkspaceI18n } from "./i18n";
import "./conversation-queue.css";

function QueuedMessage({sessionId, turn, index, targetTurnId, onChange}: {
  sessionId: string; turn: ChatQueuedTurn; index: number; targetTurnId?: string; onChange: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false), [retry, setRetry] = useState(0);
  const [message, setMessage] = useState<ChatVisibleMessage | null>(null);
  const [failed, setFailed] = useState(false);
  const lock = useRef(false);
  const [busy, setBusy] = useState(false), [actionError, setActionError] = useState("");
  const held = turn.steering?.status === "pending" ? turn.steering : null;
  const canSteer = Boolean(held || (targetTurnId && !turn.attachment_count));
  async function steer() {
    if (lock.current || !canSteer) return;
    lock.current = true; setBusy(true); setActionError("");
    try {
      await steerQueuedChatTurn(sessionId, turn.turn_id, held?.target_turn_id ?? targetTurnId!,
        held?.operation_id ?? crypto.randomUUID());
    } catch (cause) {
      const code = cause instanceof ChatApiError ? cause.payload.error_code : "";
      setActionError(code === "queue_steering_source_unavailable"
        ? (zh ? "此消息须沿原协作入口处理，已保留在队列中。" : "Use this message's original collaboration entry. It is still queued.")
        : code === "queue_turn_already_started" || code === "live_steering_turn_mismatch"
        ? (zh ? "回合已变化，请查看当前状态。" : "The turn changed. Check its current state.")
        : (zh ? "尚未确认插入，原消息已保留。" : "Insertion is unconfirmed. The original message is retained."));
    } finally {lock.current = false; setBusy(false); onChange();}
  }
  useEffect(() => {
    if (!open || message) return;
    const controller = new AbortController();
    setFailed(false);
    void fetchChatSessionHistory(sessionId, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]), turn.turn_id)
      .then(snapshot => {
        const original = snapshot.messages.find(row => row.role === "user" && row.turn_id === turn.turn_id);
        if (!original) throw new Error("Original queued input is unavailable");
        if (!controller.signal.aborted) setMessage(original);
      }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [open, message, sessionId, turn.turn_id, retry]);
  return <li onKeyDown={event => {
    if (composerKeyAction({...event, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode}, false) === "steer" && canSteer) {
      event.preventDefault(); event.stopPropagation(); void steer();
    }
  }}><div className="personal-queue-row"><details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary aria-label={`${zh ? "待处理消息" : "Queued message"} ${index + 1}`}>
      <span>{turn.preview || (zh ? "附件消息" : "Attachments")}</span>
      {turn.attachment_count ? <small>{zh ? `${turn.attachment_count} 个附件` : `${turn.attachment_count} attachments`}</small> : null}
    </summary>
    {open ? <div className="personal-queue-message">
      {failed ? <p role="alert">{zh ? "完整消息读取失败" : "Full message unavailable"}<button type="button" onClick={() => setRetry(value => value + 1)}>{zh ? "重试" : "Retry"}</button></p>
        : !message ? <p role="status">{zh ? "正在读取完整消息…" : "Reading the full message…"}</p>
        : <><p>{message.reply_context?.source_text ?? message.text}</p>
          {message.attachments?.map(attachment => isTextDocument(attachment.mime_type)
            ? <DocumentAttachment key={attachment.id} dataUrl={attachment.data_url} name={attachment.name} size={attachment.size} zh={zh}/>
            : <img key={attachment.id} src={attachment.data_url} alt={attachment.name}/>)}</>}
    </div> : null}
  </details><button type="button" className="personal-queue-steer" onClick={() => void steer()}
    disabled={busy || !canSteer} aria-label={zh ? `插入本轮：待处理消息 ${index + 1}` : `Steer with queued message ${index + 1}`}
    title={turn.attachment_count ? (zh ? "原生本轮插入仅支持文本；附件仍在队列中" : "Native steering supports text only; attachments stay queued")
      : !targetTurnId && !held ? (zh ? "当前回合或执行器不支持插入，消息仍在队列中" : "This turn or executor cannot be steered; the message stays queued")
        : (zh ? "插入本轮 · Command+Enter" : "Steer · Command+Enter")}>
    {busy ? <RefreshCw size={14}/> : <CornerUpLeft size={14}/>}<span>{held ? (zh ? "核对插入" : "Check insertion") : (zh ? "插入本轮" : "Steer")}</span>
  </button></div>
    {held || actionError || turn.steering?.status === "failed" ? <p className="personal-queue-feedback" role={actionError ? "alert" : "status"}>
      {actionError || (held ? (zh ? "插入尚未确认，已暂停自动出队。" : "Insertion is unconfirmed; automatic dispatch is paused.")
        : (zh ? "上次插入未送达，消息仍在队列中。" : "The previous insertion was not delivered. The message is still queued."))}
    </p> : null}
  </li>;
}

export function ConversationQueue({sessionId, turns, error, onRetry, targetTurnId}: {
  sessionId: string; turns: ChatQueuedTurn[]; error: Error | null; onRetry: () => void; targetTurnId?: string;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  if (!turns.length && !error) return null;
  return <section className="personal-conversation-queue" aria-label={zh ? "待处理消息" : "Queued messages"}>
    <header><span>{zh ? "待处理" : "Queued"}{error ? "" : ` · ${turns.length}`}</span>
      {error ? <><span role="alert">{zh ? "读取失败" : "Read failed"}</span><button type="button" onClick={onRetry}
        aria-label={zh ? "重试读取待处理消息" : "Retry queued messages"}><RefreshCw size={14}/></button>
        <ContextHelp label={zh ? "队列读取失败原因" : "Queue read error"}><p>{error.message}</p></ContextHelp></> : null}</header>
    <ol>{turns.map((turn, index) => <QueuedMessage key={`${sessionId}:${turn.turn_id}`} sessionId={sessionId} turn={turn} index={index}
      targetTurnId={targetTurnId} onChange={onRetry}/>)}</ol>
  </section>;
}
