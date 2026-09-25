import type { ReactNode } from "react";
import { ArrowRight, Check } from "lucide-react";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal } from "./personal-workspace-model";
import { workspaceHomeLaneForGoal } from "./personal-workspace-model";
import { GoalIdentityMark, useExecutionDetail } from "./goal-activity-view";

const briefRowLimit = 3;

type BriefRow = { goal: WorkspaceGoal; key: string; meta?: ReactNode; text: string };

function RunningMeta({ goal }: { goal: WorkspaceGoal }) {
  return <>{useExecutionDetail(goal.execution).join(" · ") || goal.title}</>;
}

function BriefTile({ count, empty, kind, live = false, onSelectGoal, rows, title, total }: {
  count: number;
  empty: string;
  kind: "needs" | "running" | "completed";
  live?: boolean;
  onSelectGoal: (goalId: string) => void;
  rows: BriefRow[];
  title: string;
  total?: string | null;
}) {
  return (
    <section className={`personal-brief-tile is-${kind}${live ? " is-live" : ""}`} data-empty={count === 0 || undefined} data-testid={`personal-brief-${kind}`}>
      <header><span>{title}</span><b>{count}</b></header>
      {rows.length ? <div className="personal-brief-rows">
        {rows.slice(0, briefRowLimit).map((row) => (
          <button className="personal-brief-row" key={row.key} onClick={() => onSelectGoal(row.goal.goalId)} type="button">
            {kind === "completed" ? <span className="personal-brief-check"><Check size={12} /></span> : <GoalIdentityMark goal={row.goal} />}
            <span><strong>{row.text}</strong><small>{row.meta ?? row.goal.title}</small></span>
            <ArrowRight aria-hidden size={14} />
          </button>
        ))}
      </div> : <p className="personal-brief-empty">{empty}</p>}
      {total ? <footer>{total}</footer> : null}
    </section>
  );
}

export function ManagerBrief({ goals, onSelectGoal }: { goals: WorkspaceGoal[]; onSelectGoal: (goalId: string) => void }) {
  const { t } = useWorkspaceI18n();
  const active = goals.filter((goal) => goal.activationState === "active" && !goal.loadState);
  const needs = active.filter((goal) => workspaceHomeLaneForGoal(goal) === "needs_you");
  const running = active.filter((goal) => goal.execution?.kind === "running");
  const queued = active.filter((goal) => goal.state === "已安排" && goal.execution?.kind !== "running").length;
  const executionRead = active.some((goal) => goal.execution && goal.execution.kind !== "unknown");
  const executionPending = active.some((goal) => !goal.execution);
  const completed = active.flatMap((goal) => goal.agentTodos
    .filter((todo) => todo.done && todo.status !== "deferred")
    .map((todo) => ({ goal, key: `${goal.goalId}:${todo.todoId}`, text: todo.text })));
  const completedTotal = active.reduce((sum, goal) => sum + Math.max(goal.doneTodoCount ?? 0, goal.agentTodos.filter((todo) => todo.done).length), 0);
  const runningEmpty = executionRead
    ? queued ? t("brief.runningEmptyQueued", { count: queued }) : t("brief.runningEmpty")
    : executionPending ? t("brief.runningReading") : t("activity.executionUnknown");
  return (
    <section aria-label={t("brief.title")} className="personal-brief">
      <BriefTile count={needs.length} empty={t("brief.needsEmpty")} kind="needs" onSelectGoal={onSelectGoal}
        rows={needs.map((goal) => ({ goal, key: goal.goalId, meta: goal.title, text: goal.needsYou ?? goal.nextSentence }))}
        title={t("brief.needs")} />
      <BriefTile count={running.length} empty={runningEmpty} kind="running" live={running.some((goal) => goal.execution?.kind === "running" && !goal.execution.quiet)} onSelectGoal={onSelectGoal}
        rows={running.map((goal) => ({ goal, key: goal.goalId, meta: <RunningMeta goal={goal} />, text: goal.title }))}
        title={t("brief.running")} total={running.length && queued ? t("brief.alsoQueued", { count: queued }) : null} />
      <BriefTile count={completed.length} empty={t("brief.completedEmpty")} kind="completed" onSelectGoal={onSelectGoal}
        rows={completed} title={t("brief.completed")}
        total={completedTotal > completed.length ? t("brief.completedTotal", { count: completedTotal }) : null} />
    </section>
  );
}
