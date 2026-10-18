import {type ReactNode} from "react";
import {type DeliveryReviewRead} from "./delivery-review-state";
import {GoalWorkspaceWork} from "./goal-workspace-work";
import { ArrowRight, Hand, Info } from "lucide-react";
import { DeliveryReviewContent, DeliveryWorkMap } from "./delivery-review";
import { relativeTime } from "./goal-activity-view";
import { goalAcceptanceContractCopy } from "./delivery-review-copy";
import type {GoalWorkspaceDigest} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import { GoalResearchHistory } from "./goal-research-history";
import { GoalAcceptanceContractSection } from "./goal-acceptance-contract";
import type { WorkspaceActionPreview, WorkspaceActionPreviewRequest, WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceGoalTab, WorkspaceModel, WorkspaceTimelineItem } from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";
import type { SinceYouLookedDelta } from "./since-you-looked";
import "./goal-overview.css";

export const goalOverviewAttentionId = "goal-overview-attention";

/** "3 hours ago" for the owner's last look; falls back to the date for older marks. */
function relativeLookLabel(iso: string, locale: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const minutes = Math.max(1, Math.round((Date.now() - then) / 60_000));
  const format = new Intl.RelativeTimeFormat(locale === "zh-CN" ? "zh-CN" : "en", { numeric: "always" });
  if (minutes < 60) return format.format(-minutes, "minute");
  if (minutes < 60 * 24) return format.format(-Math.round(minutes / 60), "hour");
  if (minutes < 60 * 24 * 14) return format.format(-Math.round(minutes / (60 * 24)), "day");
  return new Intl.DateTimeFormat(locale === "zh-CN" ? "zh-CN" : "en", { month: "short", day: "numeric" }).format(then);
}

export function GoalOverview({ active, goal, items, userTodos, readOnly, onOpenDetails, onSelect, onView, selectedTodoId, board, results, proposals, onPreview, historyRefreshRevision, readSourceUrl, review, digest, railOpen, onCloseRail, onOpenAgentConversation, sinceYouLooked, onMarkSeen }: {
  readSourceUrl?: string;
  onOpenAgentConversation?: (agentId: string) => void;
  /** Material changes since the owner's last acknowledged look; null when none. */
  sinceYouLooked?: SinceYouLookedDelta | null; onMarkSeen?: () => void;
  railOpen: boolean; onCloseRail: () => void;
  review: DeliveryReviewRead; digest: GoalWorkspaceDigest;
  active: boolean; goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  selectedTodoId: string | null; board: ReactNode; results: ReactNode; historyRefreshRevision: number;
  proposals: Record<string, WorkspaceActionPreview>; onPreview: (request: WorkspaceActionPreviewRequest) => Promise<WorkspaceActionPreview>;
  readOnly: boolean; onOpenDetails: () => void; onSelect: (selection: WorkspaceDrawerSelection | null) => void; onView: (view: WorkspaceGoalTab) => void;
}) {
  const { t, locale } = useWorkspaceI18n();
  const copy = locale === "zh-CN" ? {
    direction: "方向", current: "当前推进", agentSummary: "Agent 的进展摘要", attention: "需要你", details: "Goal 信息",
    tasks: "查看任务", execution: "执行记录", optional: "{count} 项可选",
    objectiveUnavailable: "此来源尚未提供 Goal 的目标声明。",
    remote: "此来源仅提供同步的状态与验收观察，交付链需要实时本机来源。",
  } : {
    direction: "Direction", current: "Current work", agentSummary: "Agent's progress summary", attention: "Needs you", details: "Goal information",
    tasks: "View tasks", execution: "Execution", optional: "{count} optional",
    objectiveUnavailable: "This source has not provided the Goal's declared objective.",
    remote: "This source provides synchronized status and acceptance observations. The delivery chain requires the live local source.",
  };
  const attention = userTodos.filter(item => item.goalId === goal.goalId);
  const needs = digest.needsYou;
  const blocking = needs.availability === "ok" ? needs.blocking : null;
  const optional = needs.availability === "ok" ? needs.optional : null;
  const totalAttention = blocking !== null && optional !== null ? blocking + optional : null;
  const showAttention = attention.length > 0 || (totalAttention !== null && totalAttention > 0);
  const moreAttention = needs.availability !== "ok" || needs.coverage !== "complete" || (totalAttention ?? attention.length) > 3;
  const run = items.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run" && item.run.goalId === goal.goalId);
  const observation = review.snapshot?.acceptance ?? (goal.acceptanceObservation?.goal_id === goal.goalId ? goal.acceptanceObservation : null);
  const acceptanceCopy = goalAcceptanceContractCopy[locale === "zh-CN" ? "zh-CN" : "en"];
  const sinceParts = sinceYouLooked ? [
    sinceYouLooked.newlyDone ? t("since.newlyDone", { count: sinceYouLooked.newlyDone }) : null,
    sinceYouLooked.newDecisions ? t("since.newDecisions", { count: sinceYouLooked.newDecisions }) : null,
    sinceYouLooked.newlyBlocked ? t("since.newlyBlocked", { count: sinceYouLooked.newlyBlocked }) : null,
    sinceYouLooked.resultsReturned ? t("since.resultsReturned", { count: sinceYouLooked.resultsReturned }) : null,
  ].filter((part): part is string => part !== null) : [];
  return <section className="goal-overview" aria-label={t("header.overview")}>
    {sinceYouLooked && sinceParts.length ? (
      <p className="goal-overview-since" data-testid="goal-since-you-looked" role="status">
        <span>{t("since.lead", { when: relativeLookLabel(sinceYouLooked.since, locale) })} {sinceParts.join(locale === "zh-CN" ? "、" : ", ")}</span>
        {onMarkSeen ? <button onClick={onMarkSeen} type="button">{t("since.markSeen")}</button> : null}
      </p>
    ) : null}
    <div className={`goal-overview-summary${showAttention ? " has-attention" : ""}`}>
      <section className="goal-overview-direction" aria-label={copy.direction}>
        <header className="goal-overview-heading">
          <h2>{copy.direction}</h2>
          <button onClick={onOpenDetails} type="button"><Info aria-hidden size={14} />{copy.details}</button>
        </header>
        <p className={`goal-overview-objective${digest.objective ? "" : " is-unavailable"}`} data-testid="goal-declared-objective">{digest.objective ?? copy.objectiveUnavailable}</p>
        <details className="goal-overview-agent-note"><summary>{copy.current}</summary>
          <p className="goal-overview-note">{goal.nextSentence}</p>
          {goal.agentSentence && goal.agentSentence !== goal.nextSentence ? <details className="goal-overview-agent-note"><summary>{copy.agentSummary}</summary><p className="goal-overview-note">{goal.agentSentence}</p></details> : null}
        </details>
        <GoalAcceptanceContractSection overview goalId={goal.goalId} contract={observation?.goal_acceptance_contract} copy={acceptanceCopy} current={review.usable} canManage={!readOnly && active && review.usable}/>
        {run ? <button className="goal-overview-run" type="button" onClick={() => onSelect({ kind: "run", item: run.run })}><small>{copy.execution} · {run.run.agentLabel}</small><ArrowRight aria-hidden size={15} /></button> : null}
      </section>
      {showAttention ? <section className="goal-overview-attention" aria-label={copy.attention} id={goalOverviewAttentionId} tabIndex={-1}>
        <header>
          <h2>{copy.attention}</h2>
          <span className={blocking ? "goal-overview-count is-blocking" : "goal-overview-count"}>{blocking ?? "—"}</span>
          {optional ? <small>{copy.optional.replace("{count}", String(optional))}</small> : null}
        </header>
        <ul>{attention.slice(0, 3).map(item => <li key={item.todoId}><button type="button" onClick={() => onSelect({ kind: "attention", item })}>
          <Hand aria-hidden size={15} />
          <span className="goal-attention-text"><span>{item.text}</span>
            {item.updatedAt ? <time dateTime={item.updatedAt}>{relativeTime(item.updatedAt, locale)}</time> : null}</span>
          <ArrowRight aria-hidden size={14} />
        </button></li>)}</ul>
        {needs.availability === "ok" && needs.coverage !== "complete" ? <small>{t("vitals.needsYouPartial", { count: needs.listed })}</small> : null}
        {moreAttention ? <button className="goal-overview-more" type="button" onClick={() => onView("tasks")}>{copy.tasks}{totalAttention !== null ? ` (${totalAttention})` : ""}<ArrowRight aria-hidden size={14} /></button> : null}
      </section> : null}
    </div>
    {!readOnly || readSourceUrl ? <GoalResearchHistory key={goal.goalId} goalId={goal.goalId} readSourceUrl={readSourceUrl} active={active} zh={locale === "zh-CN"} refreshRevision={historyRefreshRevision}/> : null}
    <GoalWorkspaceWork onOpenAgentConversation={onOpenAgentConversation} goal={goal} digest={digest} items={items} userTodos={userTodos} selectedTodoId={selectedTodoId} onSelect={onSelect}
      readOnly={readOnly} active={active} railOpen={railOpen} onCloseRail={onCloseRail} historyRefreshRevision={historyRefreshRevision} proposals={proposals} onPreview={onPreview} board={board} results={results}
      map={readOnly ? <p className="goal-work-scope">{copy.remote}</p> : <DeliveryWorkMap goal={goal} userTodos={userTodos} review={review} onSelect={onSelect} selectedTodoId={selectedTodoId}/>}/>
    {readOnly ? <><p className="goal-overview-source-note">{copy.remote}</p><GoalAcceptanceObservationCard goal={goal} /></>
      : <DeliveryReviewContent review={review} hideWorkMap hideAcceptanceContract active={active} goal={goal} items={items} userTodos={userTodos} onSelect={onSelect} />}
  </section>;
}
