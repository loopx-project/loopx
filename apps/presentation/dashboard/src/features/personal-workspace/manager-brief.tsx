import type { ReactNode } from "react";
import { ArrowRight, Check } from "lucide-react";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceActionPreview, WorkspaceGoal } from "./personal-workspace-model";
import { workspaceHomeLaneForGoal } from "./personal-workspace-model";
import { goalWorkKind, presentGoalActivity } from "./goal-activity";
import { GoalIdentityMark, useExecutionDetail } from "./goal-activity-view";

import { recentCompletions } from "./recent-completions";

const briefRowLimit = 3;

type BriefRow = { goal: WorkspaceGoal; key: string; meta?: ReactNode; text: string; proposal?: WorkspaceActionPreview };

function RunningMeta({ goal }: { goal: WorkspaceGoal }) {
  return <>{useExecutionDetail(goal.execution).join(" · ") || goal.title}</>;
}

function BriefTile({ count, empty, kind, live = false, onSelectGoal, onSelectOperation, onViewAll, rows, title, total }: {
  count: number;
  empty: string;
  kind: "needs" | "operations" | "running" | "claimed" | "completed";
  live?: boolean;
  onSelectGoal: (goalId: string) => void;
  onSelectOperation?: (proposal: WorkspaceActionPreview) => void;
  onViewAll?: () => void;
  rows: BriefRow[];
  title: string;
  total?: string | null;
}) {
  const { t } = useWorkspaceI18n();
  return (
    <section className={`personal-brief-tile is-${kind}${live ? " is-live" : ""}`} data-empty={count === 0 || undefined} data-testid={`personal-brief-${kind}`}>
      <header><span>{title}</span><b>{count}</b></header>
      {rows.length ? <div className="personal-brief-rows">
        {rows.slice(0, briefRowLimit).map((row) => (
          <button className="personal-brief-row" key={row.key} data-operation-id={row.proposal?.previewId}
            onClick={() => row.proposal ? onSelectOperation?.(row.proposal) : onSelectGoal(row.goal.goalId)} type="button">
            {kind === "completed" ? <span className="personal-brief-check"><Check size={12} /></span> : <GoalIdentityMark goal={row.goal} />}
            <span><strong>{row.text}</strong><small>{row.meta ?? row.goal.title}</small></span>
            <ArrowRight aria-hidden size={14} />
          </button>
        ))}
      </div> : <p className="personal-brief-empty">{empty}</p>}
      {total ? <footer>{total}</footer> : null}
      {onViewAll && rows.length > briefRowLimit ? <footer><button type="button" onClick={onViewAll}>{t("brief.viewAll", { count: rows.length })}</button></footer> : null}
    </section>
  );
}

export function ManagerBrief({ goals, onSelectGoal, operations = [], onSelectOperation, onViewAllOperations }: {
  goals: WorkspaceGoal[]; onSelectGoal: (goalId: string) => void;
  operations?: WorkspaceActionPreview[]; onSelectOperation?: (proposal: WorkspaceActionPreview) => void;
  onViewAllOperations?: () => void;
}) {
  const { t } = useWorkspaceI18n();
  const active = goals.filter((goal) => goal.activationState === "active" && !goal.loadState);
  const needs = active.filter((goal) => workspaceHomeLaneForGoal(goal) === "needs_you");
  const operationRows: BriefRow[] = operations.flatMap(proposal => {
    const goal = goals.find(goal => goal.goalId === proposal.goalId && !goal.loadState);
    return goal ? [{goal, key: proposal.previewId, proposal, text: proposal.title,
      meta: `${goal.title} · ${proposal.primaryLabel}`}]: [];
  });
  const deliveredOperations = operationRows.filter(row => row.proposal?.reviewPlan?.operationFrame?.kind === "confirmation"
    && row.proposal.reviewPlan.operationFrame.confirmationDeliveryVerified);
  const awaitingDelivery = operationRows.filter(row => !deliveredOperations.includes(row));
  const running = active.filter((goal) => goalWorkKind(goal) === "executing");
  const claimed = active.filter((goal) => goalWorkKind(goal) === "claimed");
  const queued = active.filter((goal) => goal.state === "已安排" && goalWorkKind(goal) === "none").length;
  const executionRead = active.some((goal) => goal.execution && goal.execution.kind !== "unknown");
  const executionPending = active.some((goal) => !goal.execution);
  const completed = recentCompletions(active);
  const completedTotal = active.reduce((sum, goal) => sum + Math.max(goal.doneTodoCount ?? 0, goal.agentTodos.filter((todo) => todo.done && todo.status === "done" && todo.taskClass === "advancement_task").length), 0);
  const runningEmpty = executionRead
    ? queued ? t("brief.runningEmptyQueued", { count: queued }) : t("brief.runningEmpty")
    : executionPending ? t("brief.runningReading") : t("activity.executionUnknown");
  return (
    <section aria-label={t("brief.title")} className="personal-brief">
      <BriefTile count={needs.length + deliveredOperations.length} empty={t("brief.needsEmpty")} kind="needs" onSelectGoal={onSelectGoal}
        onSelectOperation={onSelectOperation}
        onViewAll={deliveredOperations.length > briefRowLimit ? onViewAllOperations : undefined}
        rows={[...deliveredOperations, ...needs.map((goal) => ({ goal, key: goal.goalId, meta: goal.title, text: goal.needsYou ?? goal.nextSentence }))]}
        title={t("brief.needs")} />
      {awaitingDelivery.length ? <BriefTile count={awaitingDelivery.length} empty="" kind="operations" onSelectGoal={onSelectGoal}
        onSelectOperation={onSelectOperation} onViewAll={onViewAllOperations} rows={awaitingDelivery}
        title={t("brief.operationDeliveryPending")} /> : null}
      <BriefTile count={running.length} empty={runningEmpty} kind="running" live={running.some((goal) => presentGoalActivity(goal).live)} onSelectGoal={onSelectGoal}
        rows={running.map((goal) => ({ goal, key: goal.goalId, meta: <RunningMeta goal={goal} />, text: goal.title }))}
        title={t("brief.running")} total={running.length && queued ? t("brief.alsoQueued", { count: queued }) : null} />
      {claimed.length ? <BriefTile count={claimed.length} empty={t("brief.claimedEmpty")} kind="claimed" onSelectGoal={onSelectGoal}
        rows={claimed.map((goal) => ({ goal, key: goal.goalId, meta: <RunningMeta goal={goal} />, text: goal.title }))}
        title={t("brief.claimed")} /> : null}
      <BriefTile count={completed.length} empty={t("brief.completedEmpty")} kind="completed" onSelectGoal={onSelectGoal}
        rows={completed} title={t("brief.completed")}
        total={completedTotal > completed.length ? t("brief.completedTotal", { count: completedTotal }) : null} />
    </section>
  );
}
