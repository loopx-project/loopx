import { MessageActivity } from "./message-activity";
import { GoalDraftCard } from "./goal-draft-card";
import type { GoalDraft } from "../../../../../../loopx/control_plane/collaboration/goal_draft.js";
import {Fragment} from "react";
import { CollaborationCard } from "./collaboration-card";
import { Activity, Bot, Sparkles } from "lucide-react";

import { AttentionRow } from "./cards/attention-row";
import { MIN_SEPARATE_ANSWER_LENGTH } from "./answer-text";
import { MarkdownText } from "./markdown";
import { OutputRow } from "./cards/output-row";
import { RunRow } from "./cards/run-row";
import { ScheduleRow } from "./cards/schedule-row";
import { useWorkspaceI18n } from "./i18n";
import { ReturnDeliveryStatus } from "./return-delivery-status";
import {ManagerTeamResult} from "./manager-team-result";
import { compareProposalRecency } from "./proposal-recency";
import { conversationOrder } from "./conversation-order";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceTimelineItem } from "./personal-workspace-model";

function answerLink(sessionId: string, messageId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("reportSessionId", sessionId);
  url.searchParams.set("reportMessageId", messageId);
  url.searchParams.set("view", "conversation");
  url.hash = "";
  return url.toString();
}


export function ChannelTimeline({
  items,
  onSelect,
  selectedGoal,
  showManagerTeamResults = false,
  onReviewGoalDraft,
  onSuggestReply,
  onOpenGoalEvidence,
  onInterruptTurn,
  onSteerTurn,
  onCancelPreparation,
}: {
  items: WorkspaceTimelineItem[];
  onCancelPreparation?: () => void;
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  selectedGoal: WorkspaceGoal | null;
  showManagerTeamResults?: boolean;
  onReviewGoalDraft?: (draft: GoalDraft, edit?: boolean, draftId?: string) => Promise<void>;
  onSuggestReply?: (text: string) => void;
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

  const latestAnnounceable = conversationOrder(items).reverse().find((item) =>
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
    item.kind === "proposal" && item.proposal.status === "gated" && item.proposal.actionKind !== "operation.execute");
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
      const pendingOperation = item.proposal.reviewPlan?.operationFrame?.kind === "pending";
      return (
        <Fragment key={item.id}><button className={`personal-proposal-row is-${item.proposal.status}`} data-action-kind={item.proposal.actionKind} onClick={() => onSelect({ item: item.proposal, kind: "proposal" })} type="button">
          <span><Sparkles size={17} /></span>
          <span><small>{pendingOperation ? t(`proposal.kind.${item.proposal.actionKind}`)
            : appliedTeamPlan ? (locale === "zh-CN" ? "团队分配 · 已记录" : "Team assignment · Recorded")
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
          {item.message.role !== "user" && (item.message.pending || item.message.sourceTurnId || item.message.activity?.length || item.message.steps?.length) ? <MessageActivity message={item.message} onCancelPreparation={onCancelPreparation} onInterruptTurn={onInterruptTurn} onSteerTurn={onSteerTurn}/> : null}
          {item.message.role === "assistant" && !item.message.pending && item.message.goalDraft
            ? <GoalDraftCard draftId={`${item.message.sourceSessionId ?? ""}:${item.message.id}`} draft={item.message.goalDraft} onReview={onReviewGoalDraft} onSuggest={onSuggestReply}/> : null}
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
