import { useEffect, useRef, useState } from "react";
import { Square } from "lucide-react";
import { ChatApiError } from "../../data/chat.js";
import { readSteeringRequest, retainSteeringRequest, retireSteeringRequest, type SteeringRequest } from "./steering-recovery";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceMessage } from "./personal-workspace-model";
import { currentTurnStepText, TurnStepsView } from "./turn-steps-view";

// One work surface for the conversation timeline and compact overview receipt.
export function MessageActivity({ message, onInterruptTurn, onSteerTurn, onCancelPreparation }: {
  message: WorkspaceMessage;
  onCancelPreparation?: () => void;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onSteerTurn?: (turnId: string, text: string, ingressId: string) => Promise<void>;
}) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cacheKey = message.sourceSessionId && message.sourceTurnId
    ? JSON.stringify(["inline", message.sourceSessionId, message.sourceTurnId]) : null;
  const [restoredRequest] = useState(() => cacheKey ? readSteeringRequest(cacheKey) : undefined);
  const [editing, setEditing] = useState(Boolean(restoredRequest));
  const [draft, setDraft] = useState(restoredRequest?.text ?? "");
  const [steering, setSteering] = useState(false);
  const [steerError, setSteerError] = useState<string | null>(null);
  const [steerReceipt, setSteerReceipt] = useState(false);
  const request = useRef<SteeringRequest | null>(restoredRequest ?? null);
  useEffect(() => {
    const restored = cacheKey ? readSteeringRequest(cacheKey) : undefined;
    request.current = restored ?? null;
    setDraft(restored?.text ?? "");
    setEditing(Boolean(restored));
    setSteerError(null);
    setSteerReceipt(false);
  }, [cacheKey]);
  const activity = message.activity ?? [];
  const steps = message.steps ?? [];
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!message.pending) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [message.pending]);
  const elapsed = message.startedAt === undefined ? null
    : Math.max(0, Math.floor(((message.endedAt ?? now) - message.startedAt) / 1000));
  const duration = elapsed === null ? null : elapsed < 60 ? `${elapsed}${zh ? "秒" : "s"}`
    : `${Math.floor(elapsed / 60)}${zh ? "分" : "m "}${elapsed % 60}${zh ? "秒" : "s"}`;
  const quiet = message.updatedAt !== undefined && now - message.updatedAt >= 20000;

  function updateDraft(text: string) {
    setDraft(text);
    const previous = request.current;
    if (!text) {
      if (cacheKey && previous) retireSteeringRequest(cacheKey, previous.id);
      request.current = null;
      return;
    }
    const next = { sessionId: message.sourceSessionId ?? "", turnId: message.sourceTurnId ?? "", text,
      id: previous?.text.trim() === text.trim() ? previous.id : crypto.randomUUID() };
    request.current = next;
    if (cacheKey) retainSteeringRequest(cacheKey, next);
  }

  async function steer() {
    const text = draft.trim();
    if (!text || !message.pending || !message.sourceTurnId || !onSteerTurn || steering) return;
    // Retain the operation identity after a lost response; retry cannot deliver twice.
    if (request.current?.text.trim() !== text) updateDraft(draft);
    const sent = request.current!;
    if (cacheKey) retainSteeringRequest(cacheKey, sent);
    setSteering(true);
    setSteerError(null);
    try {
      await onSteerTurn(message.sourceTurnId, text, sent.id);
      if (cacheKey) retireSteeringRequest(cacheKey, sent.id);
      if (request.current?.id === sent.id) {
        setDraft(""); setEditing(false); setSteerReceipt(true); request.current = null;
      }
    } catch (cause) {
      const definitelyNotDelivered = cause instanceof ChatApiError && cause.payload.delivery_state === "not_delivered";
      if (definitelyNotDelivered && request.current?.id === sent.id) {
        // Preserve the draft; only confirmed non-delivery can renew retry identity.
        const next = { ...sent, id: crypto.randomUUID() };
        request.current = next;
        if (cacheKey && readSteeringRequest(cacheKey)?.id === sent.id) retainSteeringRequest(cacheKey, next);
      }
      const message = cause instanceof Error ? cause.message : (zh ? "未确认接收，草稿已保留。" : "Delivery unconfirmed. Draft retained.");
      setSteerError(definitelyNotDelivered
        ? (zh ? "本次未送达；请检查当前回合与执行器，条件恢复后可重试原文。" : "Not delivered; check the current turn and executor, then retry the unchanged draft.")
        : message);
    } finally { setSteering(false); }
  }
  async function interrupt() {
    if (!message.sourceTurnId || !onInterruptTurn || stopping) return;
    setStopping(true);
    setError(null);
    try { await onInterruptTurn(message.sourceTurnId); }
    catch (cause) { setError(cause instanceof Error ? cause.message : (zh ? "中断失败，请重试。" : "Could not interrupt. Try again.")); }
    finally { setStopping(false); }
  }
  return <div className="personal-message-work">
    {message.pending ? <div className="personal-message-work-current">
      <span className="personal-message-work-status">
        <span className="personal-message-pending">{currentTurnStepText(steps, zh) || activity.at(-1) || (zh ? "等待执行器更新" : "Waiting for executor update")}</span>
        {duration ? <span className="personal-message-elapsed" aria-live="off">{zh ? "已用时 " : "Elapsed "}{duration}</span> : null}
      </span>
      <span className="personal-message-work-actions">
      {message.preparing && onCancelPreparation ? <button type="button" onClick={onCancelPreparation}>
        <Square size={12} aria-hidden="true"/>{zh ? "取消发送" : "Cancel send"}
      </button> : null}
      {message.sourceTurnId && onSteerTurn ? <button type="button" disabled={steering || stopping} onClick={() => { setEditing(!editing); setSteerReceipt(false); }} aria-expanded={editing}>
        {zh ? "调整本轮" : "Adjust turn"}
      </button> : null}
      {message.sourceTurnId && onInterruptTurn ? <button type="button" disabled={stopping} onClick={() => void interrupt()}>
        <Square size={12} aria-hidden="true"/>{stopping ? (zh ? "正在中断…" : "Interrupting…") : (zh ? "中断本轮" : "Interrupt turn")}
      </button> : null}
      </span>
    </div> : null}
    {message.pending && quiet ? <p className="personal-message-quiet" role="status">{zh
      ? "暂时没有新的进展，仍在等待执行器更新。"
      : "No new activity yet. Waiting for the executor to update."}</p> : null}
    {editing ? <form className="personal-message-steer" onSubmit={event => { event.preventDefault(); void steer(); }}>
      <label>{zh ? "追加给本轮的指令" : "Instructions for this turn"}<textarea value={draft} maxLength={12000} disabled={steering}
        onChange={event => updateDraft(event.target.value)} rows={3}/></label>
      <span>{message.pending
        ? (zh ? "调整当前工作，保持原有任务与会话。" : "Adjust the current work in this conversation.")
        : (zh ? "本轮已结束，草稿已保留；可复制到输入框作为新消息发送。" : "This turn ended. Copy the retained draft to the composer to send a new message.")}</span>
      <button type="submit" disabled={!message.pending || !draft.trim() || steering || stopping}>
        {steering ? (zh ? "正在发送…" : "Sending…") : (zh ? "发送调整" : "Send adjustment")}
      </button>
      {steerError ? <p className="personal-message-work-error" role="alert">{steerError}</p> : null}
    </form> : null}
    {steerReceipt ? <p className="personal-message-steer-receipt" role="status">{zh ? "执行器已接收本轮追加指令。" : "The executor accepted instructions for this turn."}</p> : null}
    {!message.pending && message.endedAt && duration ? <p className="personal-message-elapsed">{zh ? "已结束 · 用时 " : "Ended · Elapsed "}{duration}</p> : null}
    {steps.length ? <TurnStepsView steps={steps} zh={zh} live={Boolean(message.pending)}/> : activity.length ? <details className="personal-message-activity">
      <summary>{zh ? "最近活动" : "Recent activity"}<span>{activity.length}</span></summary>
      <ol>{activity.map((label, index) => <li key={`${index}:${label}`}>{label}</li>)}</ol>
    </details> : null}
    {message.pending && error ? <p className="personal-message-work-error" role="alert">{error}</p> : null}
  </div>;
}
