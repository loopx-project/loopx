import {ArrowRight, ChevronDown, CircleHelp, Hand, Info} from "lucide-react";
import {ContextHelp} from "./context-help";
import {useEffect, useState, type ReactNode} from "react";
import {deliveryReviewWarning, reviewCoverageIncomplete} from "../../data/delivery-review";
import {type DeliveryReviewRead} from "./delivery-review-state";
import {goalWorkClassLabel, GoalWorkspaceWork} from "./goal-workspace-work";
import { DeliveryReviewContent, DeliveryWorkMap } from "./delivery-review";
import { relativeTime } from "./goal-activity-view";
import { goalAcceptanceContractCopy } from "./delivery-review-copy";
import type {GoalWorkspaceDigest} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
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

export function GoalOverview({ active, goal, items, userTodos, readOnly, onOpenDetails, onSelect, onView, selectedTodoId, board, proposals, onPreview, historyRefreshRevision, review, digest, railOpen, onCloseRail, onOpenAgentConversation, sinceYouLooked, onMarkSeen }: {
  onOpenAgentConversation?: (agentId: string) => void;
  /** Material changes since the owner's last acknowledged look; null when none. */
  sinceYouLooked?: SinceYouLookedDelta | null; onMarkSeen?: () => void;
  railOpen: boolean; onCloseRail: () => void;
  review: DeliveryReviewRead; digest: GoalWorkspaceDigest;
  active: boolean; goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  selectedTodoId: string | null; board: ReactNode; historyRefreshRevision: number;
  proposals: Record<string, WorkspaceActionPreview>; onPreview: (request: WorkspaceActionPreviewRequest) => Promise<WorkspaceActionPreview>;
  readOnly: boolean; onOpenDetails: () => void; onSelect: (selection: WorkspaceDrawerSelection | null) => void; onView: (view: WorkspaceGoalTab) => void;
}) {
  const { t, locale } = useWorkspaceI18n();
  const copy = locale === "zh-CN" ? {
    direction: "方向", current: "当前推进", agentSummary: "Agent 的进展摘要", attention: "需要你", details: "Goal 信息",
    tasks: "查看任务", execution: "执行记录", optional: "{count} 项可选",
    objectiveUnavailable: "此来源未提供目标声明",
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
  const currentRow = digest.work.rows.find(row => row.displayClass === "running_observed")
    ?? digest.work.rows.find(row => row.displayClass !== "done");
  const currentWork = goal.agentTodos.find(todo => todo.todoId === currentRow?.todoId);
  const progressNote = [goal.agentSentence, goal.nextSentence].find(note => note && note !== currentWork?.text);
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
    <GoalWorkspaceWork onOpenAgentConversation={onOpenAgentConversation} goal={goal} digest={digest} items={items} userTodos={userTodos} selectedTodoId={selectedTodoId} onSelect={onSelect}
      readOnly={readOnly} active={active} railOpen={railOpen} onCloseRail={onCloseRail} historyRefreshRevision={historyRefreshRevision} proposals={proposals} onPreview={onPreview} board={board}
      brief={<section className="goal-overview-direction" aria-label={copy.direction}>
        {digest.objective ? <p className="goal-overview-objective" data-testid="goal-declared-objective">{digest.objective}</p> : <ContextHelp icon={CircleHelp} label={locale === "zh-CN" ? "目标声明未读到" : "Declared objective unavailable"}><p>{copy.objectiveUnavailable}</p></ContextHelp>}
        {currentWork && currentRow ? <details className="goal-overview-current">
          <summary><small>{copy.current} · {goalWorkClassLabel(currentRow.displayClass, locale === "zh-CN")}</small><span>{currentWork.text}</span><ChevronDown aria-hidden size={15}/></summary>
          {progressNote ? <div className="goal-overview-agent-note"><h3>{copy.agentSummary}</h3><p className="goal-overview-note">{progressNote}</p></div> : null}
        </details> : null}
        <div className="goal-overview-heading">
          <button onClick={onOpenDetails} type="button"><Info aria-hidden size={14} />{copy.details}</button>
          {run ? <button className="goal-overview-run" type="button" onClick={() => onSelect({ kind: "run", item: run.run })}>{copy.execution}<ArrowRight aria-hidden size={15} /></button> : null}
        </div>
        <WorkerReadback goal={goal} zh={locale === "zh-CN"} onViewTasks={() => onView("tasks")}/>
        <GoalAcceptanceContractSection overview goalId={goal.goalId} contract={observation?.goal_acceptance_contract} copy={acceptanceCopy} current={review.usable} localSource={!readOnly} canManage={!readOnly && active && review.usable}/>
      </section>}
      attention={showAttention ? <section className="goal-overview-attention" aria-label={copy.attention} id={goalOverviewAttentionId} tabIndex={-1}>
        <header>
          <h2>{copy.attention}</h2>
          <span className={blocking ? "goal-overview-count is-blocking" : "goal-overview-count"}>{blocking ?? "—"}</span>
          {optional ? <small>{copy.optional.replace("{count}", String(optional))}</small> : null}
        </header>
        <ul>{attention.slice(0, 3).map(item => <li key={item.todoId}><button type="button" data-attention-id={item.todoId} onClick={() => onSelect({ kind: "attention", item })}>
          <Hand aria-hidden size={17} />
          <span className="goal-attention-text"><span>{item.text}</span>
            {item.updatedAt ? <time dateTime={item.updatedAt}>{relativeTime(item.updatedAt, locale)}</time> : null}</span>
          <ArrowRight aria-hidden size={14} />
        </button></li>)}</ul>
        {moreAttention ? <button className="goal-overview-more" type="button" onClick={() => onView("tasks")}>{copy.tasks}{totalAttention !== null ? ` (${totalAttention})` : ""}<ArrowRight aria-hidden size={14} /></button> : null}
      </section> : null}
      map={readOnly ? <ContextHelp label={locale === "zh-CN" ? "来源读取范围" : "Source reading scope"} icon={CircleHelp}><p className="goal-work-scope">{copy.remote}</p></ContextHelp> : <DeliveryWorkMap goal={goal} userTodos={userTodos} review={review} onSelect={onSelect} selectedTodoId={selectedTodoId}/>}/>
    {readOnly ? <GoalAcceptanceObservationCard goal={goal} overview />
      : <OverviewDeliveryEvidence key={`delivery:${goal.goalId}`} review={review} zh={locale === "zh-CN"}>
        <DeliveryReviewContent review={review} hideWorkMap hideAcceptanceContract active={active} goal={goal} items={items} userTodos={userTodos} onSelect={onSelect} />
      </OverviewDeliveryEvidence>}
  </section>;
}

function OverviewDeliveryEvidence({review, zh, children}: {review: DeliveryReviewRead; zh: boolean; children: ReactNode}) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (review.kind === "error" || review.stale) setOpen(true); }, [review.kind, review.stale]);
  const snapshot = review.snapshot;
  const status = review.kind === "error" ? (zh ? "读取失败" : "Read failed")
    : review.stale ? (zh ? "需要刷新" : "Refresh needed")
    : !snapshot ? (zh ? "正在读取" : "Loading")
    : deliveryReviewWarning(snapshot, {
      sourceUnknown: zh ? "来源状态未知" : "Source status unknown",
      sourceWarning: zh ? "来源已过期" : "Source is stale",
      coverageWarning: zh ? "覆盖不完整" : "Incomplete coverage",
    }) ?? (!snapshot.graph ? (zh ? "交付链不可用" : "Delivery chain unavailable")
      : reviewCoverageIncomplete(snapshot.graph) ? (zh ? "交付链不完整" : "Incomplete delivery chain") : null);
  return <details className="goal-overview-evidence" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary><span>{zh ? "交付与依据" : "Delivery and evidence"}</span>{status ? <small>{status}</small> : null}</summary>
    {children}
  </details>;
}

function WorkerReadback({goal, zh, onViewTasks}: {goal: WorkspaceGoal; zh: boolean; onViewTasks: () => void}) {
  const source = goal.workerObservations;
  if (!source) return null;
  return <section className="goal-overview-worker-state" aria-label={zh ? "外部执行器状态" : "External worker status"}>
    <h3>{zh ? "执行器最近的观察" : "Last worker observation"}</h3>
    {source.read_status !== "read" ? <ContextHelp label={zh ? "执行结果未知" : "Execution outcome unknown"} icon={CircleHelp}><p role="status">{zh
      ? "执行器记录缺失有效来源或与当前目标配置不一致。当前执行结果未知。"
      : "The worker record is unreadable or does not match the current Goal configuration. Its current outcome is unknown."}</p></ContextHelp> : null}
    {source.rows.map(row => <div key={row.agent_id}>
      <p><strong>{row.status === "replan_paused" ? (zh ? "需要方向，已暂停复核" : "Direction needed; review paused")
        : (zh ? "原请求结果未知，已暂停新派发" : "Original outcome unknown; new dispatch paused")}</strong></p>
      <p>{row.status === "replan_paused" ? (zh
        ? "本次复核没有解决原义务。补充具体任务或相关结果，再重启执行器以检查当前准入。"
        : "The review did not resolve its original obligation. Add concrete work or relevant results, then restart the worker to check current admission.") : (zh
        ? "先核实原操作及其结算。已发送请求仍可能产生费用；新事件或重启不能证明原请求未执行。"
        : "Check the original operation and its settlement first. An issued request may still incur costs; a new event or restart cannot prove it did not run.")}</p>
      <ContextHelp label={zh ? "执行记录范围" : "Execution record scope"}><p className="goal-overview-note">{zh ? "此记录不证明 Goal 达成；完整费用未知。" : "This record does not establish Goal completion. Complete costs are unknown."}</p></ContextHelp>
      <details><summary>{zh ? "原工作与观察时间" : "Original work and observation time"}</summary>
        <dl><div><dt>Agent</dt><dd>{row.agent_id}</dd></div>
          <div><dt>{zh ? "义务" : "Obligation"}</dt><dd>{row.obligation_id}</dd></div>
          <div><dt>{zh ? "原回合" : "Original Turn"}</dt><dd>{row.turn_instance_id ?? (zh ? "未绑定，需原 Host 核实" : "Unbound; check the original Host")}</dd></div>
          <div><dt>{zh ? "记录时间" : "Observed at"}</dt><dd><time dateTime={row.observed_at}>{row.observed_at}</time></dd></div></dl>
      </details>
    </div>)}
    <button type="button" onClick={onViewTasks}>{zh ? "查看任务与依赖" : "View work and dependencies"}</button>
  </section>;
}
