import { useEffect, useState } from "react";
import { Square } from "lucide-react";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceMessage } from "./personal-workspace-model";
import { currentTurnStepText, TurnStepsView } from "./turn-steps-view";

// One work surface for the conversation timeline and compact overview receipt.
export function MessageActivity({ message, onInterruptTurn, onAdjustTurn, onCancelPreparation }: {
  message: WorkspaceMessage;
  onCancelPreparation?: () => void;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onAdjustTurn?: (turnId: string) => void;
}) {
  const { locale, t } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
      {message.sourceTurnId && onAdjustTurn ? <button type="button" disabled={stopping} onClick={() => onAdjustTurn(message.sourceTurnId!)} aria-controls="personal-conversation-composer">
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
    {!message.pending && message.endedAt && duration ? <p className="personal-message-elapsed">{zh ? "已结束 · 用时 " : "Ended · Elapsed "}{duration}</p> : null}
    {steps.length ? <TurnStepsView steps={steps} zh={zh} live={Boolean(message.pending)}/> : activity.length ? <details className="personal-message-activity">
      <summary>{zh ? "最近活动" : "Recent activity"}<span>{activity.length}</span></summary>
      <ol>{activity.map((label, index) => <li key={`${index}:${label}`}>{label}</li>)}</ol>
    </details> : null}
    {message.pending && error ? <p className="personal-message-work-error" role="alert">{error}</p> : null}
  </div>;
}
