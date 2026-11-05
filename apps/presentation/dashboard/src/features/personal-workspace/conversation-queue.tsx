import { useEffect, useRef, useState } from "react";
import { CornerUpLeft, MoreHorizontal, Pencil, RefreshCw, X } from "lucide-react";
import { ChatApiError, chatQueueInputRequestSchema, fetchQueuedChatInput, mutateQueuedChatTurn, steerQueuedChatTurn,
  type ChatQueueInputRequest, type ChatQueuedInput, type ChatQueuedTurn } from "../../data/chat";
import { composerKeyAction } from "./composer-keyboard";
import { DocumentAttachment, isTextDocument } from "./document-attachment";
import { ContextHelp } from "./context-help";
import { queueInputKeys, readQueueInputRecovery, retireQueueInputRequest } from "./queue-input-recovery";
import { RetainedQueueInputs } from "./retained-queue-inputs";
import { useWorkspaceI18n } from "./i18n";
import "./conversation-queue.css";

function QueuedMessage({sessionId, turn, index, targetTurnId, onChange, onInputChanged}: {
  sessionId: string; turn: ChatQueuedTurn; index: number; targetTurnId?: string; onChange: () => void; onInputChanged: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false), [retry, setRetry] = useState(0);
  const [message, setMessage] = useState<ChatQueuedInput | null>(null);
  const [failed, setFailed] = useState(false);
  const lock = useRef(false);
  const [busy, setBusy] = useState(false), [actionError, setActionError] = useState("");
  const {request: recoveryKey, draft: draftKey} = queueInputKeys(sessionId, turn.turn_id);
  const [recovery] = useState(() => {
    try {
      if (typeof window === "undefined") return {request: null, editor: null, failed: false};
      return {...readQueueInputRecovery(localStorage, sessionId, turn.turn_id), failed: false};
    } catch {return {request: null, editor: null, failed: true};}
  });
  const [pending, setPending] = useState<ChatQueueInputRequest | null>(recovery.request);
  const serverPending = turn.mutation ? chatQueueInputRequestSchema.parse({
    action: turn.mutation.action, operation_id: turn.mutation.operation_id,
    expected_revision: turn.mutation.expected_revision,
    ...(turn.mutation.action === "edit" ? {message: turn.mutation.message} : {}),
  }) : null;
  const unresolved = serverPending ?? pending;
  const [menu, setMenu] = useState(false), [editing, setEditing] = useState(Boolean(recovery.editor));
  const [draft, setDraft] = useState<string | null>(recovery.editor?.message ?? null);
  const [editRevision, setEditRevision] = useState<number | null>(recovery.editor?.revision ?? null);
  const textarea = useRef<HTMLTextAreaElement>(null), menuButton = useRef<HTMLButtonElement>(null);
  const composing = useRef(false);
  const held = turn.steering?.status === "pending" ? turn.steering : null;
  const canSteer = !unresolved && !editing && !recovery.failed && Boolean(held || (targetTurnId && !turn.attachment_count));
  const editable = !held && !unresolved && !recovery.failed && turn.origin === "web";
  useEffect(() => {setMessage(null);}, [turn.input_revision]);
  useEffect(() => {if (editing && message && draft === null) {
    setDraft(message.message); setEditRevision(message.input_revision);
  }}, [editing, message, draft]);
  useEffect(() => {if (editing && draft !== null) textarea.current?.focus();}, [editing, draft === null]);
  async function mutate(request: ChatQueueInputRequest) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setActionError(""); setMenu(false);
    let retained = false;
    try {
      // Failure to durably retain the operation must precede any network effect.
      localStorage.setItem(recoveryKey, JSON.stringify(request));
      retained = true;
      setPending(request);
      await mutateQueuedChatTurn(sessionId, turn.turn_id, request);
      setPending(null); setEditing(false); setDraft(null); setMessage(null); setEditRevision(null);
      retireQueueInputRequest(localStorage, sessionId, turn.turn_id, request, true);
    } catch (cause) {
      const code = cause instanceof ChatApiError ? cause.payload.error_code : "";
      const rejected = cause instanceof ChatApiError && (cause.payload.http_status === 400 || cause.payload.http_status === 404 ||
        ["queue_turn_already_started", "queue_input_revision_conflict", "queue_mutation_identity_conflict",
          "queue_mutation_source_unavailable", "queue_steering_in_progress", "queue_mutation_in_progress", "session_closed"].includes(String(code)));
      if (rejected) {
        setPending(null);
        try {retireQueueInputRequest(localStorage, sessionId, turn.turn_id, request, false);} catch { /* Preserve the draft if storage is unavailable. */ }
      }
      setActionError(!retained
        ? (zh ? "恢复信息无法保存，修改尚未提交。请保留文本后重试。" : "Recovery could not be saved. The change was not submitted; retain the text and retry.")
        : rejected
        ? (zh ? "消息状态或权限已变化，修改未应用。请核对当前状态；编辑草稿已保留。" : "The message state or permission changed. Check its current state; your edit is retained.")
        : (zh ? "修改尚未确认，请核对原操作；不会重新发送消息。" : "The change is unconfirmed. Check the original operation; the message will not be resent."));
    } finally {lock.current = false; setBusy(false); onInputChanged();}
  }
  function save() {
    if (draft && [...draft].length > 12000) {
      setActionError(zh ? "待处理消息最多 12000 个字符，请缩短后保存。" : "Queued messages support up to 12000 characters. Shorten the text before saving."); return;
    }
    if (draft?.trim() && editRevision !== null) void mutate({action: "edit", operation_id: crypto.randomUUID(),
      expected_revision: editRevision, message: draft});
  }
  function discard() {
    try {localStorage.removeItem(draftKey);} catch {setActionError(zh ? "草稿恢复信息无法清除，请保留文本。" : "Draft recovery could not be cleared. Retain the text."); return;}
    setEditing(false); setDraft(null); setEditRevision(null); setMenu(false); menuButton.current?.focus();
  }
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
    if ((!open && !editing) || message) return;
    const controller = new AbortController();
    setFailed(false);
    void fetchQueuedChatInput(sessionId, turn.turn_id, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]))
      .then(input => {if (!controller.signal.aborted) setMessage(input);})
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [open, editing, message, sessionId, turn.turn_id, turn.input_revision, retry]);
  return <li onKeyDown={event => {
    if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === "Escape" && !busy && !unresolved) {
      event.preventDefault(); event.stopPropagation(); discard(); return;
    }
    if (editing) {
      if (composerKeyAction({...event, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode}, false) === "steer") {
        event.preventDefault(); event.stopPropagation(); save();
      }
      return;
    }
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
        : <><p>{message.message}</p>
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
  </button><div className="personal-queue-actions"><button ref={menuButton} type="button" disabled={busy || !editable}
    aria-label={zh ? `更多操作：待处理消息 ${index + 1}` : `More actions for queued message ${index + 1}`}
    aria-expanded={menu} onClick={() => setMenu(!menu)}><MoreHorizontal size={16}/></button>
    {menu ? <div className="personal-queue-menu">
      <button type="button" onClick={() => {setMenu(false); setEditing(true); setOpen(true);}}><Pencil size={14}/>{zh ? "编辑" : "Edit"}</button>
      <button type="button" onClick={() => void mutate({action: "cancel", operation_id: crypto.randomUUID(),
        expected_revision: turn.input_revision})}><X size={14}/>{zh ? "取消排队" : "Cancel queued message"}</button>
    </div> : null}</div></div>
    {editing ? <div className="personal-queue-editor">
      {draft === null ? <p role="status">{failed ? (zh ? "完整消息读取失败" : "Full message unavailable") : (zh ? "正在读取…" : "Reading…")}</p>
        : <textarea ref={textarea} aria-label={zh ? `编辑待处理消息 ${index + 1}` : `Edit queued message ${index + 1}`}
          maxLength={24000} value={draft} onChange={event => {
            setDraft(event.target.value);
            try {localStorage.setItem(draftKey, JSON.stringify({message: event.target.value, revision: editRevision}));}
            catch {setActionError(zh ? "草稿无法保存，切换前请保留文本。" : "Draft could not be saved. Retain the text before switching.");}
          }} onCompositionStart={() => {composing.current = true;}} onCompositionEnd={() => {composing.current = false;}}
          disabled={busy || Boolean(unresolved)} rows={3}/>}
      <div><button type="button" onClick={save} disabled={busy || !draft?.trim() || Boolean(unresolved)}>{zh ? "保存" : "Save"}</button>
        <button type="button" disabled={busy || Boolean(unresolved)} onClick={discard}>{zh ? "放弃修改" : "Discard edit"}</button></div>
    </div> : null}
    {unresolved ? <div className="personal-queue-feedback"><span>{zh ? "修改尚未确认" : "Change unconfirmed"}</span>
      <button type="button" disabled={busy} onClick={() => void mutate(unresolved)}>{zh ? "核对修改" : "Check change"}</button></div> : null}
    {held || actionError || recovery.failed || turn.steering?.status === "failed" ? <p className="personal-queue-feedback" role={actionError || recovery.failed ? "alert" : "status"}>
      {recovery.failed ? (zh ? "恢复信息不可读，请保留当前草稿并重新打开会话核对。" : "Recovery information is unavailable. Retain your draft and reopen the conversation to check.") : null}
      {actionError || (held ? (zh ? "插入尚未确认，已暂停自动出队。" : "Insertion is unconfirmed; automatic dispatch is paused.")
        : (zh ? "上次插入未送达，消息仍在队列中。" : "The previous insertion was not delivered. The message is still queued."))}
    </p> : null}
  </li>;
}

export function ConversationQueue({sessionId, turns, error, readReady, onRetry, targetTurnId, onInputChanged = onRetry}: {
  sessionId: string; turns: ChatQueuedTurn[]; error: Error | null; readReady: boolean; onRetry: () => void; targetTurnId?: string; onInputChanged?: () => void;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  return <>{turns.length || error ? <section className="personal-conversation-queue" aria-label={zh ? "待处理消息" : "Queued messages"}>
    <header><span>{zh ? "待处理" : "Queued"}{error ? "" : ` · ${turns.length}`}</span>
      {error ? <><span role="alert">{zh ? "读取失败" : "Read failed"}</span><button type="button" onClick={onRetry}
        aria-label={zh ? "重试读取待处理消息" : "Retry queued messages"}><RefreshCw size={14}/></button>
        <ContextHelp label={zh ? "队列读取失败原因" : "Queue read error"}><p>{error.message}</p></ContextHelp></> : null}</header>
    <ol>{turns.map((turn, index) => <QueuedMessage key={`${sessionId}:${turn.turn_id}`} sessionId={sessionId} turn={turn} index={index}
      targetTurnId={targetTurnId} onChange={onRetry} onInputChanged={onInputChanged}/>)}</ol>
  </section> : null}
    {readReady && !error ? <RetainedQueueInputs key={sessionId} sessionId={sessionId} turns={turns} onChange={onInputChanged}/> : null}
  </>;
}
