import {ContextHelp} from "./context-help";
import {ArrowRight, Check, CircleHelp, Hand} from "lucide-react";
import {useState, type ReactNode} from "react";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceActionPreview, WorkspaceAttention, WorkspaceGoal, WorkspaceRunHistoryEntry, WorkspaceTimelineItem } from "./personal-workspace-model";
import { workspaceHomeLaneForGoal } from "./personal-workspace-model";
import { goalShortLabel } from "./goal-display";
import { goalWorkKind, presentGoalActivity } from "./goal-activity";
import { GoalIdentityMark, useExecutionDetail } from "./goal-activity-view";
import { canReviewAttention } from "./attention-details";

import { recentCompletions } from "./recent-completions";

const briefRowLimit = 3;

type BriefRow = { goal: WorkspaceGoal; key: string; meta?: ReactNode; text: string; proposal?: WorkspaceActionPreview; attention?: WorkspaceAttention };

function RunningMeta({ goal }: { goal: WorkspaceGoal }) {
  return <>{useExecutionDetail(goal.execution).join(" · ") || goalShortLabel(goal)}</>;
}

function BriefTile({ count, empty, kind, live = false, onSelectGoal, onSelectOperation, onSelectAttention, rows, title, total }: {
  count: number;
  empty: string;
  kind: "needs" | "needs-goals" | "confirmations" | "operations" | "running" | "claimed" | "completed";
  live?: boolean;
  onSelectGoal: (goalId: string) => void;
  onSelectOperation?: (proposal: WorkspaceActionPreview) => void;
  onSelectAttention?: (item: WorkspaceAttention) => void;
  rows: BriefRow[];
  title: string;
  total?: string | null;
}) {
  const { t } = useWorkspaceI18n();
  const [expanded, setExpanded] = useState(false);
  return (
    <section className={`personal-brief-tile is-${kind}${live ? " is-live" : ""}`} data-empty={count === 0 || undefined} data-testid={`personal-brief-${kind}`}>
      <header><span>{title}{!rows.length ? <ContextHelp label={empty} icon={CircleHelp}><p>{empty}</p></ContextHelp> : null}</span><b>{count}</b></header>
      {rows.length ? <div className="personal-brief-rows">
        {(expanded ? rows : rows.slice(0, briefRowLimit)).map((row) => (
          <button className="personal-brief-row" key={row.key} data-operation-id={row.proposal?.previewId}
            data-attention-id={row.attention?.todoId} data-attention-source={row.attention?.sourceId}
            onClick={() => row.proposal ? onSelectOperation?.(row.proposal)
              : row.attention && onSelectAttention ? onSelectAttention(row.attention) : onSelectGoal(row.goal.goalId)} type="button">
            {kind === "completed" ? <span className="personal-brief-check"><Check size={12} /></span>
              : row.attention ? <Hand aria-hidden size={16}/> : <GoalIdentityMark goal={row.goal} />}
            <span><strong>{row.text}</strong><small>{row.meta ?? goalShortLabel(row.goal)}</small></span>
            <ArrowRight aria-hidden size={14} />
          </button>
        ))}
      </div> : null}
      {total || rows.length > briefRowLimit ? <footer>{total ? <span>{total}</span> : null}
        {rows.length > briefRowLimit ? <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? t("brief.collapse") : t("brief.viewAll", { count: rows.length })}</button> : null}
      </footer> : null}
    </section>
  );
}


function RunBand({ runs }: { runs: WorkspaceRunHistoryEntry[] }) {
  const recent = runs.slice(-28);
  // Nothing recorded yet: an empty band would be permanent furniture.
  if (!recent.length) return null;
  const failed = recent.filter((run) => run.status === "failed").length;
  const day = (at: string) => at.slice(5, 10).replace("-", "/");
  const span = recent.length > 1 ? `${day(recent[0].at)} → ${day(recent[recent.length - 1].at)}` : null;
  return (
    <section aria-label="Recent runs" className="personal-runband" data-testid="personal-runband">
      <div className="personal-runband-track" role="img" aria-label={`${recent.length} recent runs`}>
        {recent.map((run) => (
          <span
            className={`personal-runband-seg is-${run.status === "failed" ? "failed" : "done"}`}
            key={run.id}
            title={`${run.goalTitle} · ${run.classification ?? "run"} · ${run.at}`}
          />
        ))}
      </div>
      <p className="personal-runband-legend">
        <span>最近 {recent.length} 次运行</span>
        {span ? <span className="personal-runband-span">{span}</span> : null}
        <span className="personal-runband-key is-done">{recent.length - failed} 完成</span>
        <span className="personal-runband-key is-failed">{failed} 失败</span>
      </p>
    </section>
  );
}

export function ManagerBrief({ goals, onSelectGoal, attention = [], onSelectAttention, operations = [], onSelectOperation, timeline = [], runs = [] }: {
  goals: WorkspaceGoal[]; onSelectGoal: (goalId: string) => void;
  attention?: WorkspaceAttention[]; onSelectAttention?: (item: WorkspaceAttention) => void;
  operations?: WorkspaceActionPreview[]; onSelectOperation?: (proposal: WorkspaceActionPreview) => void;
  onViewAllOperations?: () => void;
  timeline?: WorkspaceTimelineItem[];
  runs?: WorkspaceRunHistoryEntry[];
}) {
  const { t } = useWorkspaceI18n();
  const active = goals.filter((goal) => goal.activationState === "active" && !goal.loadState);
  const needs = active.filter((goal) => workspaceHomeLaneForGoal(goal) === "needs_you");
  // Count only observed native items. A Goal digest is a different unit, and
  // equal titles never prove that an attention item and an operation are one.
  const seen = new Set<string>();
  const attentionRows: BriefRow[] = attention.flatMap(item => {
    const goal = goals.find(candidate => candidate.goalId === item.goalId && !candidate.loadState);
    if (!goal || !canReviewAttention(item)) return [];
    const key = JSON.stringify([item.sourceId ?? "", item.goalId, item.decisionSource ?? "todo", item.todoId]);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{goal, key, attention: item, text: item.text, meta: [goalShortLabel(goal),
      item.decisionSource === "run_operator_gate" ? t("brief.runGate")
        : item.details?.interaction === "decision" ? t("brief.decision")
          : item.details?.interaction === "user_action" ? t("brief.userAction") : t("brief.attention"),
    ].join(" · ")}];
  });
  const summarizedGoals = needs.filter(goal => !attentionRows.some(row => row.goal === goal));
  const incomplete = goals.some(goal => goal.loadState);
  const operationRows: BriefRow[] = operations.flatMap(proposal => {
    const goal = goals.find(goal => goal.goalId === proposal.goalId && !goal.loadState);
    return goal ? [{goal, key: proposal.previewId, proposal, text: proposal.title,
      meta: `${goalShortLabel(goal)} · ${proposal.primaryLabel}`}]: [];
  });
  const deliveredOperations = operationRows.filter(row => row.proposal?.reviewPlan?.operationFrame?.kind === "confirmation"
    && row.proposal.reviewPlan.operationFrame.confirmationDeliveryVerified);
  const awaitingDelivery = operationRows.filter(row => !deliveredOperations.includes(row));
  const running = active.filter((goal) => goalWorkKind(goal) === "executing");
  const claimed = active.filter((goal) => goalWorkKind(goal) === "claimed");
  const queued = active.filter((goal) => goal.state === "已安排" && goalWorkKind(goal) === "none").length;
  const modeUnrecognized = active.filter(goal => goalWorkKind(goal) === "unrecognized").length;
  const executionRead = active.some((goal) => goal.execution && goal.execution.kind !== "unknown");
  const executionPending = active.some((goal) => !goal.execution);
  const completed = recentCompletions(active);
  const completedTotal = active.reduce((sum, goal) => sum + Math.max(goal.doneTodoCount ?? 0, goal.agentTodos.filter((todo) => todo.done && todo.status === "done" && todo.taskClass === "advancement_task").length), 0);
  const runningEmpty = executionRead
    ? modeUnrecognized ? t("brief.runningModeUnrecognized", { count: modeUnrecognized })
      : queued ? t("brief.runningEmptyQueued", { count: queued }) : t("brief.runningEmpty")
    : executionPending ? t("brief.runningReading") : t("activity.executionUnknown");
  return (
    <section aria-label={t("brief.title")} className="personal-brief">
      <RunBand runs={runs} />
      <BriefTile count={completed.length} empty={t("brief.completedEmpty")} kind="completed" onSelectGoal={onSelectGoal}
        rows={completed} title={t("brief.completed")}
        total={completedTotal > completed.length ? t("brief.completedTotal", { count: completedTotal }) : null} />
      <BriefTile count={attentionRows.length} empty={t("brief.needsEmpty")} kind="needs" onSelectGoal={onSelectGoal}
        onSelectAttention={onSelectAttention} rows={attentionRows} title={t("brief.needs")}
        total={incomplete ? t("brief.partialGoals") : attentionRows.length ? t("brief.affectedGoals", {count: new Set(attentionRows.map(row => row.goal.goalId)).size}) : null} />
      {summarizedGoals.length ? <BriefTile count={summarizedGoals.length} empty="" kind="needs-goals" onSelectGoal={onSelectGoal}
        rows={summarizedGoals.map(goal => ({goal, key: goal.goalId, meta: goalShortLabel(goal), text: goal.needsYou ?? goal.nextSentence}))}
        title={t("brief.needsGoals")} /> : null}
      {deliveredOperations.length ? <BriefTile count={deliveredOperations.length} empty="" kind="confirmations" onSelectGoal={onSelectGoal}
        onSelectOperation={onSelectOperation} rows={deliveredOperations} title={t("brief.confirmations")} /> : null}
      {awaitingDelivery.length ? <BriefTile count={awaitingDelivery.length} empty="" kind="operations" onSelectGoal={onSelectGoal}
        onSelectOperation={onSelectOperation} rows={awaitingDelivery}
        title={t("brief.operationDeliveryPending")} /> : null}

    </section>
  );
}
