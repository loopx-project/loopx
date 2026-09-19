import { CollaborationCard } from "./collaboration-card";
import { Activity, Bot, Sparkles } from "lucide-react";

import { AttentionRow } from "./cards/attention-row";
import { MarkdownText } from "./markdown";
import { OutputRow } from "./cards/output-row";
import { RunRow } from "./cards/run-row";
import { ScheduleRow } from "./cards/schedule-row";
import { useWorkspaceI18n } from "./i18n";
import { ReturnDeliveryStatus } from "./return-delivery-status";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceTimelineItem } from "./personal-workspace-model";

export function ChannelTimeline({
  items,
  onSelect,
  selectedGoal,
}: {
  items: WorkspaceTimelineItem[];
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  selectedGoal: WorkspaceGoal | null;
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
    ? `${latestAnnounceable.message.agentLabel ?? t("header.manager")}：${latestAnnounceable.message.pending ? t("timeline.pending") : latestAnnounceable.message.text}`
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
  const routineIds = new Set(routineRuns.map(item => item.id));
  const primaryItems = items.filter(item => item.kind !== "proposal" && !routineIds.has(item.id));
  const workingCount = routineRuns.filter(item => item.run.status === "running" && Boolean(item.run.sessionId) && Boolean(item.run.canInterrupt)).length;
  const queuedCount = routineRuns.filter(item => item.run.status === "queued").length;
  const completedCount = routineRuns.filter(item => item.run.status === "completed").length;
  const progressCount = routineRuns.length - workingCount - queuedCount - completedCount;
  const activitySummary = locale === "zh-CN"
    ? [workingCount && `${workingCount} 个执行中`, queuedCount && `${queuedCount} 个排队中`, completedCount && `${completedCount} 次执行已结束`, progressCount && `${progressCount} 项进展更新`].filter(Boolean).join(" · ")
    : [workingCount && `${workingCount} running`, queuedCount && `${queuedCount} queued`, completedCount && `${completedCount} runs finished`, progressCount && `${progressCount} progress updates`].filter(Boolean).join(" · ");
  const activeProposalItems = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "proposal" }> =>
    item.kind === "proposal" && item.proposal.status !== "gated");

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
      return (
        <button className={`personal-proposal-row is-${item.proposal.status}`} key={item.id} onClick={() => onSelect({ item: item.proposal, kind: "proposal" })} type="button">
          <span><Sparkles size={17} /></span>
          <span><small>{item.proposal.actionKind} · {item.proposal.status}</small><strong>{item.proposal.title}</strong><p>{item.proposal.impact}</p></span>
          <b>{item.proposal.status === "gated" && item.proposal.actionKind !== "operation.execute" ? t("timeline.review") : item.proposal.primaryLabel ?? t("timeline.reviewAndConfirm")}</b>
        </button>
      );
    }
    return (
      <article className={`personal-message is-${item.message.role}`} key={item.id}>
        {item.message.role !== "user" ? <span className="personal-message-avatar"><Bot size={17} /></span> : null}
        <div>
          <header><strong>{item.message.role === "user" ? t("common.you") : item.message.agentLabel ?? t("header.manager")}</strong>{item.message.time ? <time>{item.message.time}</time> : null}</header>
          {item.message.attachments?.length ? <div className="personal-message-images">{item.message.attachments.map((attachment) => <img alt={attachment.name} key={attachment.id} src={attachment.dataUrl} />)}</div> : null}
          {item.message.role === "user" ? <p>{item.message.text}</p> : <MarkdownText text={item.message.text} />}
          {item.message.pending ? <span className="personal-message-pending">{t("timeline.pending")}</span> : null}
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
        {routineRuns.length ? <details className="personal-activity-summary">
          <summary><Activity size={16} aria-hidden="true"/><strong>{locale === "zh-CN" ? "执行动态" : "Execution activity"}</strong><span>{activitySummary}</span></summary>
          <div>{routineRuns.map(renderItem)}</div>
        </details> : null}
        {primaryItems.map(renderItem)}
        {gatedItems.length ? (
          <details className="personal-gated-summary">
            <summary><span><Sparkles size={16} /></span><strong>{t("timeline.waitingConfirmation")}</strong><small>{t("timeline.gateHistory", { count: gatedItems.length })}</small></summary>
            <div>{gatedItems.map(renderItem)}</div>
          </details>
        ) : null}
        {activeProposalItems.map(renderItem)}
      </div>
    </>
  );
}
