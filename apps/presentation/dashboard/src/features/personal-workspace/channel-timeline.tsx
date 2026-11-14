import {DocumentAttachment, isDocumentAttachment} from "./document-attachment";
import { MessageActivity } from "./message-activity";
import { GoalDraftCard } from "./goal-draft-card";
import type { GoalDraft } from "../../../../../../loopx/control_plane/collaboration/goal_draft.js";
import {Fragment, useEffect, useEffectEvent, useLayoutEffect, useRef, useState} from "react";
import { CollaborationCards } from "./collaboration-card";
import {ConversationReplyContext} from "./conversation-requests";
import { Activity, Bot, Sparkles } from "lucide-react";

import { AttentionRow } from "./cards/attention-row";
import { MIN_SEPARATE_ANSWER_LENGTH } from "./answer-text";
import {HistoricalMessageText} from "./message-reading";
import type {ConversationHistoryStatus} from "../../data/use-conversation-history";
import { OutputRow } from "./cards/output-row";
import { RunRow } from "./cards/run-row";
import { ScheduleRow } from "./cards/schedule-row";
import { useWorkspaceI18n } from "./i18n";
import { ReturnDeliveryStatus } from "./return-delivery-status";
import {ManagerTeamResult} from "./manager-team-result";
import { compareProposalRecency } from "./proposal-recency";
import { conversationOrder } from "./conversation-order";
import { ConversationFind, conversationMessageKey } from "./conversation-find";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceTimelineItem } from "./personal-workspace-model";

function answerLink(sessionId: string, messageId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("reportSessionId", sessionId);
  url.searchParams.set("reportMessageId", messageId);
  url.searchParams.set("view", "conversation");
  url.hash = "";
  return url.toString();
}

function LinkedHistoryImage({name, source, ready, zh}: {name: string; source: string; ready: boolean; zh: boolean}) {
  const ref = useRef<HTMLImageElement>(null);
  const [imageRead, setImageRead] = useState<{source: string; failed?: boolean}>();
  const requested = imageRead?.source === source;
  useEffect(() => {
    const image = ref.current;
    if (!image || !ready || requested) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) return;
      observer.disconnect();
      setImageRead({source});
    }, {root: image.closest(".personal-channel-scroll"), rootMargin: "64px 0px"});
    observer.observe(image);
    return () => observer.disconnect();
  }, [ready, requested, source]);
  if (requested && imageRead.failed) return <div className="personal-history-notice">
    <div role="status"><span>{name}</span><span>{zh ? "图片无法显示" : "Could not load image"}</span></div>
    <button type="button" onClick={() => setImageRead({source})}>{zh ? "重试图片" : "Retry image"}</button>
  </div>;
  return <img ref={ref} alt={name} src={requested ? source : undefined}
    onError={() => setImageRead(current => current?.source === source ? {source, failed: true} : current)}/>;
}

export function ChannelTimeline({
  emptyState,
  readOnly = false,
  items,
  onSelect,
  selectedGoal,
  showManagerTeamResults = false,
  onReviewGoalDraft,
  onSuggestReply,
  onOpenGoalEvidence,
  onInterruptTurn,
  onAdjustTurn,
  onCancelPreparation,
  messageFocus,
  historyPhase = "ready",
  historyPositionReady = true,
  onReadMessage,
  onRetryHistory,
  queuedTurnKeys,
}: {
  emptyState?: { title: string; description: string };
  messageFocus?: {sessionId: string; messageId: string};
  historyPhase?: ConversationHistoryStatus["phase"];
  historyPositionReady?: boolean;
  onReadMessage?: () => void;
  onRetryHistory?: () => void;
  queuedTurnKeys?: ReadonlySet<string>;
  readOnly?: boolean;
  items: WorkspaceTimelineItem[];
  onCancelPreparation?: () => void;
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  selectedGoal: WorkspaceGoal | null;
  showManagerTeamResults?: boolean;
  onReviewGoalDraft?: (draft: GoalDraft, edit?: boolean, draftId?: string) => Promise<void>;
  onSuggestReply?: (text: string) => void;
  onOpenGoalEvidence?: (goalId: string) => void;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onAdjustTurn?: (turnId: string) => void;
}) {
  const { locale, t } = useWorkspaceI18n();
  const [findNavigation, setFindNavigation] = useState(0);
  const [findMessageId, setFindMessageId] = useState<string | null>(null);
  const timeline = useRef<HTMLDivElement>(null);
  const readMessage = useEffectEvent(() => onReadMessage?.());
  useLayoutEffect(() => {
    if (!findMessageId) return;
    readMessage();
    const frame = requestAnimationFrame(() => {
      const row = Array.from(timeline.current?.querySelectorAll<HTMLElement>("[data-timeline-id]") ?? [])
        .find(node => node.dataset.timelineId === findMessageId);
      row?.scrollIntoView({block: "start"});
    });
    return () => cancelAnimationFrame(frame);
  }, [findMessageId, findNavigation]);
  const orderedItems = conversationOrder(items.filter(item => {
    if (item.kind !== "message") return true;
    if (item.message.queued) return false;
    const queued = item.message.role === "user" && queuedTurnKeys?.has(
      JSON.stringify([item.message.sourceSessionId, item.message.sourceTurnId]));
    const focused = messageFocus?.sessionId === item.message.sourceSessionId
      && messageFocus?.messageId === item.message.sourceMessageId;
    return !queued || focused;
  }));
  const messageKey = conversationMessageKey;
  const [historical, setHistorical] = useState<Set<string> | null>(null);
  // Freeze only the first settled historical batch. A live or newly completed
  // response, or a message already being read, never folds on a later refresh.
  if (historical === null && historyPhase !== "loading" && items.some(item => item.kind === "message")) {
    const messages = orderedItems.filter((item): item is Extract<WorkspaceTimelineItem, {kind: "message"}> => item.kind === "message");
    const latestAssistant = [...messages].reverse().find(item => item.message.role === "assistant");
    setHistorical(new Set(messages.filter(item => item !== latestAssistant && item.message.role === "assistant"
      && !item.message.pending && item.message.sourceSessionId && item.message.sourceMessageId).map(item => messageKey(item.message))));
  }
  const focusTarget = useRef<HTMLElement>(null);
  const [restoredFocus, setRestoredFocus] = useState<string | null>(null);
  const focusKey = messageFocus ? JSON.stringify([messageFocus.sessionId, messageFocus.messageId]) : null;
  const focusPresent = Boolean(messageFocus && items.some(item => item.kind === "message"
    && item.message.sourceSessionId === messageFocus.sessionId && item.message.sourceMessageId === messageFocus.messageId));
  useLayoutEffect(() => {
    if (!focusKey) {if (restoredFocus !== null) setRestoredFocus(null); return;}
    if (!focusPresent || restoredFocus === focusKey || !focusTarget.current) return;
    onReadMessage?.();
    const frame = window.requestAnimationFrame(() => {
      if (!focusTarget.current) return;
      onReadMessage?.();
      focusTarget.current.scrollIntoView({block: "start"});
      focusTarget.current.focus({preventScroll: true});
      setRestoredFocus(focusKey);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focusKey, focusPresent, onReadMessage, restoredFocus]);
  const loadHistoryImages = historyPositionReady && (!focusPresent || restoredFocus === focusKey);
  const focusNotice = messageFocus && !focusPresent ? <div className="personal-message-focus-notice" role="status">
    <span>{historyPhase === "loading" ? (locale === "zh-CN" ? "正在定位原消息…" : "Finding the original message…")
      : historyPhase === "ready" ? (locale === "zh-CN" ? "原消息不在当前已读取的对话中。" : "The original message is not in the loaded conversation.")
      : (locale === "zh-CN" ? "原消息尚未读到；对话记录读取不完整。" : "The original message has not been read; conversation history is incomplete.")}</span>
    {historyPhase !== "loading" && onRetryHistory ? <button type="button" onClick={onRetryHistory}>{locale === "zh-CN" ? "重新读取对话" : "Retry conversation read"}</button> : null}
  </div> : null;
  if (items.length === 0) {
    return (
      <>{focusNotice}<div className="personal-timeline-empty">
        <span><Sparkles size={20} /></span>
        <strong>{emptyState?.title ?? (readOnly ? t("timeline.emptyReadOnly") : selectedGoal ? t("timeline.emptyGoal") : t("timeline.emptyWorkspace"))}</strong>
        {!readOnly ? <p>{emptyState?.description ?? (selectedGoal ? t("timeline.emptyGoalDescription") : t("timeline.emptyWorkspaceDescription"))}</p> : null}
      </div></>
    );
  }

  const latestAnnounceable = [...orderedItems].reverse().find((item) =>
    (item.kind === "message" && item.message.role !== "user")
    || (item.kind === "proposal" && ["applied", "stale", "error", "gated"].includes(item.proposal.status))
    || (item.kind === "run" && item.run.status === "completed"));
  const liveAnnouncement = latestAnnounceable?.kind === "message"
    ? `${latestAnnounceable.message.agentLabel ?? t("header.manager")}：${latestAnnounceable.message.pending ? t("timeline.pending")
      : latestAnnounceable.message.role === "assistant" ? (locale === "zh-CN" ? "有新答复" : "New answer available")
      : (locale === "zh-CN" ? "会话状态已更新" : "Conversation status updated")}`
    : latestAnnounceable?.kind === "proposal"
      ? `${locale === "zh-CN" ? "请求已更新" : "Request updated"}：${t(`proposal.status.${latestAnnounceable.proposal.status}`)}`
      : latestAnnounceable?.kind === "run"
        ? t("timeline.runCompleted", { run: latestAnnounceable.run.title })
        : "";

  const gatedItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "proposal" }> =>
    item.kind === "proposal" && item.proposal.status === "gated" && item.proposal.actionKind !== "operation.execute");
  // Only routine execution is folded. Waiting, interruption, and failures stay
  // visible; no prose-based inference that a waiting run is safe to ignore.
  const routineRuns = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
    item.kind === "run" && ["queued", "running", "completed"].includes(item.run.status));
  const scheduleItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "schedule" }> => item.kind === "schedule");
  const backgroundIds = new Set([...routineRuns, ...scheduleItems].map(item => item.id));
  const primaryItems = orderedItems.filter(item => item.kind !== "proposal" && !backgroundIds.has(item.id));
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
    item.kind === "proposal" && (item.proposal.status !== "gated" || item.proposal.actionKind === "operation.execute"));
  // Only drafts awaiting the owner fold behind the newest one; applying, applied and failed results stay visible.
  // "Newest" is read from the stored proposal, not from the position in this
  // list: a restore arrives newest first and a draft created in this session is
  // appended last, so a positional rule keeps the wrong draft on the first screen.
  const readyProposalItems = activeProposalItems.filter(item => item.proposal.status === "ready");
  const readyByRecency = [...readyProposalItems].sort((a, b) => compareProposalRecency(a.proposal, b.proposal));
  const foldedProposalItems = readyByRecency.slice(1);
  const foldedProposalIds = new Set(foldedProposalItems.map(item => item.id));
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
      const taskReceipt = item.proposal.reviewPlan?.taskExecution;
      const pendingOperation = item.proposal.reviewPlan?.operationFrame?.kind === "pending";
      return (
        <Fragment key={item.id}><button className={`personal-proposal-row is-${item.proposal.status}`} data-action-kind={item.proposal.actionKind} onClick={() => onSelect({ item: item.proposal, kind: "proposal" })} type="button">
          <span><Sparkles size={17} /></span>
          <span><small>{pendingOperation ? t(`proposal.kind.${item.proposal.actionKind}`)
            : taskReceipt ? t("drawer.taskLaunchReceipt")
            : appliedTeamPlan ? (locale === "zh-CN" ? "团队分配 · 已记录" : "Team assignment · Recorded")
            : `${t(`proposal.kind.${item.proposal.actionKind}`)} · ${t(`proposal.status.${item.proposal.status}`)}`}</small><strong>{item.proposal.title}</strong>{item.proposal.impact ? <p>{item.proposal.impact}</p> : null}</span>
          <b>{taskReceipt ? t("feedback.taskStartRequested")
            : item.proposal.status === "gated" && item.proposal.actionKind !== "operation.execute" ? t("timeline.review")
            : item.proposal.primaryLabel ?? t("timeline.reviewAndConfirm")}</b>
        </button>{showManagerTeamResults && onOpenGoalEvidence && appliedTeamPlan
          && item.proposal.goalId && item.proposal.teamPlanTodoIds?.length
          ? <ManagerTeamResult goalId={item.proposal.goalId} todoIds={item.proposal.teamPlanTodoIds} zh={locale === "zh-CN"} onOpenGoalEvidence={onOpenGoalEvidence}/>
          : null}</Fragment>
      );
    }
    return (
      <article className={`personal-message is-${item.message.role}${messageKey(item.message) === findMessageId ? " is-find-match" : ""}`} key={item.id}
        data-timeline-id={messageKey(item.message)}
        data-message-session={item.message.sourceSessionId} data-message-id={item.message.sourceMessageId}
        data-returned-answer={item.message.role === "assistant" && !item.message.pending && item.message.returnDelivery ? true : undefined}
        ref={messageFocus && messageKey(item.message) === focusKey ? focusTarget : undefined}
        tabIndex={messageFocus && messageKey(item.message) === focusKey ? -1 : undefined}>
        {item.message.role !== "user" ? <span className="personal-message-avatar"><Bot size={17} /></span> : null}
        <div>
          <header><strong>{item.message.role === "user" ? item.message.agentLabel ?? t("common.you") : item.message.agentLabel ?? t("header.manager")}</strong>{item.message.time ? <time>{item.message.time}</time> : null}</header>
          <ReturnDeliveryStatus delivery={item.message.returnDelivery} />
          {item.message.queueInputCancelled ? <span>{locale === "zh-CN" ? "已取消排队" : "Cancelled before starting"}</span> : null}
          {item.message.attachments?.length ? <div className="personal-message-images">{item.message.attachments.map((attachment) => {
            if (isDocumentAttachment(attachment.mimeType)) return <DocumentAttachment key={attachment.id} {...attachment} zh={locale === "zh-CN"}/>;
            const linked = attachment.dataUrl.startsWith("/api/chat/sessions/") && attachment.dataUrl.includes("/images/");
            return linked ? <LinkedHistoryImage key={attachment.id} name={attachment.name} source={attachment.dataUrl}
              ready={loadHistoryImages} zh={locale === "zh-CN"}/>
              : <img alt={attachment.name} key={attachment.id} src={attachment.dataUrl} loading="lazy"/>;
          })}</div> : null}
          {item.message.role === "user" ? <p>{item.message.text}</p> : item.message.text ? <HistoricalMessageText text={item.message.text}
            initiallyCollapsed={Boolean(historical?.has(messageKey(item.message)))}
            focusRequest={messageKey(item.message) === findMessageId ? findNavigation : undefined}
            focused={messageKey(item.message) === findMessageId || Boolean(messageFocus && messageKey(item.message) === focusKey)} zh={locale === "zh-CN"} onRead={onReadMessage}/> : null}
          <ConversationReplyContext context={item.message.replyContext} zh={locale === "zh-CN"}/>
          {item.message.role === "assistant" && !item.message.pending && item.message.text.length >= MIN_SEPARATE_ANSWER_LENGTH
            && item.message.sourceSessionId && item.message.sourceMessageId
            ? <a className="personal-answer-link" href={answerLink(item.message.sourceSessionId, item.message.sourceMessageId)}
                target="_blank" rel="noopener noreferrer">{locale === "zh-CN" ? "单独阅读完整答复" : "Read full answer separately"}</a>
            : null}
          {item.message.role !== "user" && (item.message.pending || item.message.sourceTurnId || item.message.activity?.length || item.message.steps?.length) ? <MessageActivity message={item.message} onCancelPreparation={onCancelPreparation} onInterruptTurn={onInterruptTurn} onAdjustTurn={onAdjustTurn}/> : null}
          {item.message.role === "assistant" && !item.message.pending && item.message.goalDraft
            ? <GoalDraftCard draftId={`${item.message.sourceSessionId ?? ""}:${item.message.id}`} draft={item.message.goalDraft} onReview={onReviewGoalDraft} onSuggest={onSuggestReply}/> : null}
          <CollaborationCards request={item.message.collaboration} requests={item.message.collaborations} />
        </div>
      </article>
    );
  }

  return (
    <>
      <p aria-atomic="true" aria-live="polite" className="personal-live-region" role="status"><span key={latestAnnounceable?.id}>{liveAnnouncement}</span></p>
      {focusNotice}
      {orderedItems.some(item => item.kind === "message") ? <ConversationFind items={orderedItems.flatMap(item => item.kind === "message" ? [item.message] : [])}
        incomplete={historyPhase !== "ready"} zh={locale === "zh-CN"} onSelect={setFindMessageId} onNavigate={() => setFindNavigation(value => value + 1)}/> : null}
      <div className="personal-channel-timeline" ref={timeline}>
        {backgroundIds.size ? <details className="personal-activity-summary">
          <summary><Activity size={16} aria-hidden="true"/><strong>{t("timeline.background")}</strong><span>{activitySummary}</span></summary>
          <div>{scheduleItems.map(renderItem)}{routineRuns.map(renderItem)}</div>
        </details> : null}
        {gatedItems.length ? (
          <details className="personal-gated-summary">
            <summary><span><Sparkles size={16} /></span><strong>{t("timeline.waitingConfirmation")}</strong><small>{t("timeline.gateHistory", { count: gatedItems.length })}</small></summary>
            <div>{gatedItems.map(renderItem)}</div>
          </details>
        ) : null}
        {foldedProposalIds.size ? (
          <details className="personal-proposal-backlog">
            <summary><Sparkles size={16} aria-hidden="true" /><strong>{t("timeline.olderDrafts", { count: foldedProposalIds.size })}</strong></summary>
            <div>{foldedProposalItems.map(renderItem)}</div>
          </details>
        ) : null}
        {conversationOrder([...primaryItems, ...visibleProposalItems]).map(renderItem)}
      </div>
    </>
  );
}
