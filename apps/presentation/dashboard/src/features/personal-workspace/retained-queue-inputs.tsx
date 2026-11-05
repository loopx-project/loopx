import { useEffect, useRef, useState } from "react";
import { ChatApiError, mutateQueuedChatTurn, type ChatQueuedTurn } from "../../data/chat";
import { listQueueInputRecovery, queueInputKeys, retireQueueInputRequest, type QueueInputRecovery } from "./queue-input-recovery";
import { useWorkspaceI18n } from "./i18n";

function readSessionRecovery(sessionId: string) {
  try {
    return typeof window === "undefined" ? {entries: [], failed: false} : listQueueInputRecovery(window.localStorage, sessionId);
  } catch {return {entries: [], failed: true};}
}

function RetainedInput({sessionId, input, onChange}: {sessionId: string; input: QueueInputRecovery; onChange: () => void}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false), text = useRef<HTMLTextAreaElement>(null);
  const draft = input.editor?.message ?? (input.request?.action === "edit" ? input.request.message : null);
  async function check() {
    if (!input.request || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      // Retry only the retained operation. The owner can replay its receipt
      // after dispatch; a new edit against an already started Turn is rejected.
      await mutateQueuedChatTurn(sessionId, input.turnId, input.request);
      retireQueueInputRequest(localStorage, sessionId, input.turnId, input.request, true);
    } catch (cause) {
      const code = cause instanceof ChatApiError ? cause.payload.error_code : "";
      if (["queue_turn_already_started", "queue_input_revision_conflict", "queue_mutation_identity_conflict"].includes(String(code))) {
        // Keep the full text, including an edit retained only in the request.
        try {retireQueueInputRequest(localStorage, sessionId, input.turnId, input.request, false);}
        catch { /* An unreadable record must not be replaced with a fresh send. */ }
        setError(zh ? "修改未应用，草稿已保留。" : "The change was not applied. Your draft is retained.");
      } else setError(zh ? "原操作尚未确认，恢复记录已保留。" : "The original operation is unconfirmed. Recovery is retained.");
    } finally {lock.current = false; setBusy(false); onChange();}
  }
  function discard() {
    try {localStorage.removeItem(queueInputKeys(sessionId, input.turnId).draft); onChange();}
    catch {setError(zh ? "草稿无法清除，请保留文本后重试。" : "The draft could not be cleared. Retain the text and retry.");}
  }
  return <details className="personal-queue-retained-input">
    <summary>{draft !== null ? (zh ? "保留的编辑草稿" : "Retained edit draft") : (zh ? "未确认的队列修改" : "Unconfirmed queue change")}</summary>
    <p>{zh ? "原消息已不在待处理队列中。" : "The original message is no longer in the pending queue."}</p>
    {draft !== null ? <textarea ref={text} readOnly rows={3} value={draft}
      aria-label={zh ? "保留的编辑草稿" : "Retained edit draft"}/> : null}
    <div className="personal-queue-retained-actions">
      {draft !== null ? <button type="button" onClick={() => {text.current?.focus(); text.current?.select();}}>{zh ? "选择草稿" : "Select draft"}</button> : null}
      {input.request ? <button type="button" disabled={busy} onClick={() => void check()}>{zh ? "核对原操作" : "Check original operation"}</button>
        : <button type="button" onClick={discard}>{zh ? "清除草稿" : "Clear draft"}</button>}
    </div>
    {error ? <p role="alert">{error}</p> : null}
  </details>;
}

export function RetainedQueueInputs({sessionId, turns, onChange}: {sessionId: string; turns: ChatQueuedTurn[]; onChange: () => void}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const [recovery, setRecovery] = useState(() => readSessionRecovery(sessionId));
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const read = () => setRecovery(readSessionRecovery(sessionId));
    read(); window.addEventListener("storage", read);
    return () => window.removeEventListener("storage", read);
  }, [sessionId, turns, attempt]);
  const inputs = recovery.entries.filter(input => !turns.some(turn => turn.turn_id === input.turnId));
  if (!inputs.length && !recovery.failed) return null;
  return <section className="personal-conversation-queue personal-queue-recovery" aria-label={zh ? "保留的队列修改" : "Retained queue changes"}>
    {inputs.length ? <details><summary>{zh ? "保留的修改" : "Retained changes"} · {inputs.length}</summary>
      {inputs.map(input => <RetainedInput key={input.turnId} sessionId={sessionId} input={input}
        onChange={() => {setAttempt(value => value + 1); onChange();}}/>)}</details> : null}
    {recovery.failed ? <p role="alert">{zh ? "队列修改的恢复信息不可读。" : "Queue change recovery is unreadable."}</p> : null}
  </section>;
}
