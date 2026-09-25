import {Fragment, useRef, useState} from "react";
import { ChatApiError } from "../../data/chat.js";
import { CollaborationCard } from "./collaboration-card";
import { Activity, Bot, Sparkles, Square } from "lucide-react";

import { AttentionRow } from "./cards/attention-row";
import { MIN_SEPARATE_ANSWER_LENGTH } from "./answer-text";
import { MarkdownText } from "./markdown";
import { OutputRow } from "./cards/output-row";
import { RunRow } from "./cards/run-row";
import { ScheduleRow } from "./cards/schedule-row";
import { useWorkspaceI18n } from "./i18n";
import { ReturnDeliveryStatus } from "./return-delivery-status";
import {ManagerTeamResult} from "./manager-team-result";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceMessage, WorkspaceTimelineItem } from "./personal-workspace-model";

function answerLink(sessionId: string, messageId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("reportSessionId", sessionId);
  url.searchParams.set("reportMessageId", messageId);
  url.searchParams.set("view", "conversation");
  url.hash = "";
  return url.toString();
}

function MessageActivity({ message, onInterruptTurn, onSteerTurn }: {
  message: WorkspaceMessage;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onSteerTurn?: (turnId: string, text: string, ingressId: string) => Promise<void>;
}) {
  const { locale, t } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [steering, setSteering] = useState(false);
  const [steerError, setSteerError] = useState<string | null>(null);
  const [steerReceipt, setSteerReceipt] = useState(false);
  const request = useRef<{ text: string; id: string } | null>(null);
  const activity = message.activity ?? [];
  async function steer() {
    const text = draft.trim();
    if (!text || !message.pending || !message.sourceTurnId || !onSteerTurn || steering) return;
    // Retain the operation identity after a lost response; retry cannot deliver twice.
    if (request.current?.text !== text) request.current = { text, id: crypto.randomUUID() };
    setSteering(true);
    setSteerError(null);
    try {
      await onSteerTurn(message.sourceTurnId, text, request.current.id);
      setDraft(""); setEditing(false); setSteerReceipt(true); request.current = null;
    } catch (cause) {
      const definitelyNotDelivered = cause instanceof ChatApiError && cause.payload.delivery_state === "not_delivered";
      if (definitelyNotDelivered) request.current = null;
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
      <span className="personal-message-pending">{activity.at(-1) || t("timeline.pending")}</span>
      <span className="personal-message-work-actions">
      {message.sourceTurnId && onSteerTurn ? <button type="button" disabled={steering || stopping} onClick={() => { setEditing(!editing); setSteerReceipt(false); }} aria-expanded={editing}>
        {zh ? "调整本轮" : "Adjust turn"}
      </button> : null}
      {message.sourceTurnId && onInterruptTurn ? <button type="button" disabled={stopping} onClick={() => void interrupt()}>
        <Square size={12} aria-hidden="true"/>{stopping ? (zh ? "正在中断…" : "Interrupting…") : (zh ? "中断本轮" : "Interrupt turn")}
      </button> : null}
      </span>
    </div> : null}
    {editing ? <form className="personal-message-steer" onSubmit={event => { event.preventDefault(); void steer(); }}>
      <label>{zh ? "追加给本轮的指令" : "Instructions for this turn"}<textarea value={draft} maxLength={12000} disabled={steering}
        onChange={event => setDraft(event.target.value)} rows={3}/></label>
      <span>{message.pending
        ? (zh ? "调整当前工作，保持原有任务与会话。" : "Adjust the current work in this conversation.")
        : (zh ? "本轮已结束，草稿已保留；可复制到输入框作为新消息发送。" : "This turn ended. Copy the retained draft to the composer to send a new message.")}</span>
      <button type="submit" disabled={!message.pending || !draft.trim() || steering || stopping}>
        {steering ? (zh ? "正在发送…" : "Sending…") : (zh ? "发送调整" : "Send adjustment")}
      </button>
      {steerError ? <p className="personal-message-work-error" role="alert">{steerError}</p> : null}
    </form> : null}
    {steerReceipt ? <p className="personal-message-steer-receipt" role="status">{zh ? "执行器已接收本轮追加指令。" : "The executor accepted instructions for this turn."}</p> : null}
    {activity.length ? <details className="personal-message-activity">
      <summary>{zh ? "最近活动" : "Recent activity"}<span>{activity.length}</span></summary>
      <ol>{activity.map((label, index) => <li key={`${index}:${label}`}>{label}</li>)}</ol>
    </details> : null}
    {message.pending && error ? <p className="personal-message-work-error" role="alert">{error}</p> : null}
  </div>;
}

export function ChannelTimeline({
  items,
  onSelect,
  selectedGoal,
  showManagerTeamResults = false,
  onOpenGoalEvidence,
  onInterruptTurn,
  onSteerTurn,
}: {
  items: WorkspaceTimelineItem[];
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  selectedGoal: WorkspaceGoal | null;
  showManagerTeamResults?: boolean;
  onOpenGoalEvidence?: (goalId: string) => void;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onSteerTurn?: (turnId: string, text: string, ingressId: string) => Promise<void>;
}) {
  const { locale, t } = useWorkspaceI18n();
  if (items.length === 0) {
    return (
      <div className="personal-timeline-empty">
        <span><Sparkles size={20} /></span>
        <strong>{selectedGoal ? t("timeline.emptyGoal") : t("timeline.emptyWorkspace")}</strong>
        <p>{selectedGoal ? t("timeline.emptyGoalDescription") : t("timeline.emptyWorkspaceDescription")}</p>
      </div>
    );
  }

  const latestAnnounceable = [...items].reverse().find((item) =>
    (item.kind === "message" && item.message.role !== "user")
    || (item.kind === "proposal" && ["applied", "stale", "error", "gated"].includes(item.proposal.status))
    || (item.kind === "run" && item.run.status === "completed"));
  const liveAnnouncement = latestAnnounceable?.kind === "message"
    ? `${latestAnnounceable.message.agentLabel ?? t("header.manager")}：${latestAnnounceable.message.pending ? latestAnnounceable.message.activity?.at(-1) || t("timeline.pending") : latestAnnounceable.message.text}`
    : latestAnnounceable?.kind === "proposal"
      ? `${latestAnnounceable.proposal.title}：${latestAnnounceable.proposal.status}`
      : latestAnnounceable?.kind === "run"
        ? t("timeline.runCompleted", { run: latestAnnounceable.run.title })
        : "";

  const gatedItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "proposal" }> =>
    item.kind === "proposal" && item.proposal.status === "gated");
  // Only routine execution is folded. Waiting, interruption, and failures stay
  // visible; no prose-based inference that a waiting run is safe to ignore.
  const routineRuns = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
    item.kind === "run" && ["queued", "running", "completed"].includes(item.run.status));
  const scheduleItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "schedule" }> => item.kind === "schedule");
  const backgroundIds = new Set([...routineRuns, ...scheduleItems].map(item => item.id));
  const primaryItems = items.filter(item => item.kind !== "proposal" && !backgroundIds.has(item.id));
  const pausedScheduleCount = scheduleItems.filter(item => item.schedule.status === "paused").length;
  const enabledScheduleCount = scheduleItems.length - pausedScheduleCount;
  const workingCount = routineRuns.filter(item => item.run.status === "running" && Boolean(item.run.sessionId) && Boolean(item.run.canInterrupt)).length;
  const queuedCount = routineRuns.filter(item => item.run.status === "queued").length;
  const completedCount = routineRuns.filter(item => item.run.status === "completed").length;
  const progressCount = routineRuns.length - workingCount - queuedCount - completedCount;
  const activitySummary = ([
    enabledScheduleCount ? t("timeline.backgroundSchedules", { count: enabledScheduleCount }) : null,
    pausedScheduleCount ? t("timeline.backgroundPaused", { count: pausedScheduleCount }) : null,
  ] as Array<string | number | null>).concat(locale === "zh-CN"
    ? [workingCount && `${workingCount} 个执行中`, queuedCount && `${queuedCount} 个排队中`, completedCount && `${completedCount} 次执行已结束`, progressCount && `${progressCount} 项进展更新`]
    : [workingCount && `${workingCount} running`, queuedCount && `${queuedCount} queued`, completedCount && `${completedCount} runs finished`, progressCount && `${progressCount} progress updates`]).filter(Boolean).join(" · ");
  const activeProposalItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "proposal" }> =>
    item.kind === "proposal" && item.proposal.status !== "gated");
  // Only drafts awaiting the owner fold behind the newest one; applying, applied and failed results stay visible.
  const readyProposalItems = activeProposalItems.filter(item => item.proposal.status === "ready");
  const foldedProposalIds = new Set(readyProposalItems.slice(0, -1).map(item => item.id));
  const visibleProposalItems = activeProposalItems.filter(item => !foldedProposalIds.has(item.id));

  function renderItem(item: WorkspaceTimelineItem) {
    if (item.kind === "attention") {
      return <AttentionRow attention={item.attention} key={item.id} onSelect={() => onSelect({ item: item.attention, kind: "attention" })} />;
    }
    if (item.kind === "run") {
      return <RunRow showGoal={!selectedGoal} key={item.id} onSelect={() => onSelect({ item: item.run, kind: "run" })} run={item.run} />;
    }
    if (item.kind === "output") {
      return <OutputRow key={item.id} onSelect={() => onSelect({ item: item.output, kind: "output" })} output={item.output} />;
    }
    if (item.kind === "schedule") {
      return <ScheduleRow key={item.id} onSelect={() => onSelect({ item: item.schedule, kind: "schedule" })} schedule={item.schedule} />;
    }
    if (item.kind === "proposal") {
      const appliedTeamPlan = item.proposal.actionKind === "team.plan" && item.proposal.status === "applied";
      return (
        <Fragment key={item.id}><button className={`personal-proposal-row is-${item.proposal.status}`} data-action-kind={item.proposal.actionKind} onClick={() => onSelect({ item: item.proposal, kind: "proposal" })} type="button">
          <span><Sparkles size={17} /></span>
          <span><small>{appliedTeamPlan ? (locale === "zh-CN" ? "团队分配 · 已记录" : "Team assignment · Recorded")
            : `${t(`proposal.kind.${item.proposal.actionKind}`)} · ${t(`proposal.status.${item.proposal.status}`)}`}</small><strong>{item.proposal.title}</strong>{item.proposal.impact ? <p>{item.proposal.impact}</p> : null}</span>
          <b>{item.proposal.status === "gated" && item.proposal.actionKind !== "operation.execute" ? t("timeline.review") : item.proposal.primaryLabel ?? t("timeline.reviewAndConfirm")}</b>
        </button>{showManagerTeamResults && onOpenGoalEvidence && appliedTeamPlan
          && item.proposal.goalId && item.proposal.teamPlanTodoIds?.length
          ? <ManagerTeamResult goalId={item.proposal.goalId} todoIds={item.proposal.teamPlanTodoIds} zh={locale === "zh-CN"} onOpenGoalEvidence={onOpenGoalEvidence}/>
          : null}</Fragment>
      );
    }
    return (
      <article className={`personal-message is-${item.message.role}`} key={item.id}>
        {item.message.role !== "user" ? <span className="personal-message-avatar"><Bot size={17} /></span> : null}
        <div>
          <header><strong>{item.message.role === "user" ? t("common.you") : item.message.agentLabel ?? t("header.manager")}</strong>{item.message.time ? <time>{item.message.time}</time> : null}</header>
          {item.message.attachments?.length ? <div className="personal-message-images">{item.message.attachments.map((attachment) => <img alt={attachment.name} key={attachment.id} src={attachment.dataUrl} />)}</div> : null}
          {item.message.role === "user" ? <p>{item.message.text}</p> : item.message.text ? <MarkdownText text={item.message.text} /> : null}
          {item.message.role === "assistant" && !item.message.pending && item.message.text.length >= MIN_SEPARATE_ANSWER_LENGTH
            && item.message.sourceSessionId && item.message.sourceMessageId
            ? <a className="personal-answer-link" href={answerLink(item.message.sourceSessionId, item.message.sourceMessageId)}
                target="_blank" rel="noopener noreferrer">{locale === "zh-CN" ? "单独阅读完整答复" : "Read full answer separately"}</a>
            : null}
          {item.message.role !== "user" && (item.message.pending || item.message.sourceTurnId || item.message.activity?.length) ? <MessageActivity message={item.message} onInterruptTurn={onInterruptTurn} onSteerTurn={onSteerTurn}/> : null}
          <CollaborationCard request={item.message.collaboration} />
              <ReturnDeliveryStatus delivery={item.message.returnDelivery} />
        </div>
      </article>
    );
  }

  return (
    <>
      <p aria-atomic="true" aria-live="polite" className="personal-live-region" role="status">{liveAnnouncement}</p>
      <div className="personal-channel-timeline">
        {backgroundIds.size ? <details className="personal-activity-summary">
          <summary><Activity size={16} aria-hidden="true"/><strong>{t("timeline.background")}</strong><span>{activitySummary}</span></summary>
          <div>{scheduleItems.map(renderItem)}{routineRuns.map(renderItem)}</div>
        </details> : null}
        {primaryItems.map(renderItem)}
        {gatedItems.length ? (
          <details className="personal-gated-summary">
            <summary><span><Sparkles size={16} /></span><strong>{t("timeline.waitingConfirmation")}</strong><small>{t("timeline.gateHistory", { count: gatedItems.length })}</small></summary>
            <div>{gatedItems.map(renderItem)}</div>
          </details>
        ) : null}
        {foldedProposalIds.size ? (
          <details className="personal-proposal-backlog">
            <summary><Sparkles size={16} aria-hidden="true" /><strong>{t("timeline.olderDrafts", { count: foldedProposalIds.size })}</strong></summary>
            <div>{readyProposalItems.filter(item => foldedProposalIds.has(item.id)).map(renderItem)}</div>
          </details>
        ) : null}
        {visibleProposalItems.map(renderItem)}
      </div>
    </>
  );
}
