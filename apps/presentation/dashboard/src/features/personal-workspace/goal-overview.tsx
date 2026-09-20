import { ArrowRight, Info } from "lucide-react";
import { DeliveryReview } from "./delivery-review";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import { formatCostUsd, formatDurationMs, formatTokenCount, formatUsageValue, type WorkspaceDrawerSelection, type WorkspaceGoal, type WorkspaceGoalTab, type WorkspaceModel, type WorkspaceTimelineItem } from "./personal-workspace-model";
import { localizedGoalState, useWorkspaceI18n } from "./i18n";
import "./goal-overview.css";

export function GoalOverview({ active, goal, items, userTodos, readOnly, onOpenDetails, onSelect, onView }: {
  active: boolean; goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  readOnly: boolean; onOpenDetails: () => void; onSelect: (selection: WorkspaceDrawerSelection) => void; onView: (view: WorkspaceGoalTab) => void;
}) {
  const { t, locale } = useWorkspaceI18n();
  const copy = locale === "zh-CN" ? {
    progress: "当前进展", attention: "需要你", none: "当前没有已加载的待处理决定。", details: "Goal 信息",
    tasks: "查看任务", usage: "最近 24 小时", execution: "执行记录",
    remote: "此来源仅提供同步的状态与验收观察，交付链需要实时本机来源。",
  } : {
    progress: "Current progress", attention: "Needs you", none: "No pending decisions are loaded.", details: "Goal information",
    tasks: "View tasks", usage: "Last 24 hours", execution: "Execution",
    remote: "This source provides synchronized status and acceptance observations. The delivery chain requires the live local source.",
  };
  const attention = userTodos.filter(item => item.goalId === goal.goalId);
  const run = items.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run" && item.run.goalId === goal.goalId);
  return <section className="goal-overview" aria-label={t("header.overview")}>
    <header className="goal-overview-heading"><h2>{t("header.overview")}</h2><button onClick={onOpenDetails} type="button"><Info size={15} />{copy.details}</button></header>
    <div className={`goal-overview-summary${attention.length ? " has-attention" : ""}`}>
      <section><header><h3>{copy.progress}</h3><span>{localizedGoalState(goal.state, locale)}</span></header>
        <strong>{goal.nextSentence}</strong>
        {goal.agentSentence && goal.agentSentence !== goal.nextSentence ? <p>{goal.agentSentence}</p> : null}
        {!attention.length ? <p className="goal-overview-quiet">{copy.none}</p> : null}
        {run ? <button className="goal-overview-run" type="button" onClick={() => onSelect({ kind: "run", item: run.run })}><small>{copy.execution} · {run.run.agentLabel}</small><strong>{run.run.title}</strong><ArrowRight size={15} /></button> : null}
      </section>
      {attention.length ? <section className="goal-overview-attention"><header><h3>{copy.attention}</h3><span>{attention.length}</span></header>
        <ul>{attention.slice(0, 3).map(item => <li key={item.todoId}><button type="button" onClick={() => onSelect({ kind: "attention", item })}><span>{item.text}</span><ArrowRight size={15} /></button></li>)}</ul>
        {attention.length > 3 ? <button type="button" onClick={() => onView("tasks")}>{copy.tasks} ({attention.length})<ArrowRight size={14} /></button> : null}
      </section> : null}
    </div>
    <section className="goal-overview-usage" aria-label={copy.usage}>
      <h3>{copy.usage}</h3>
      <dl>
        <div><dt>{t("drawer.tokensShort")}</dt><dd>{formatUsageValue(goal.usage?.tokens24h, t("drawer.usageNotMeasured"), formatTokenCount)}</dd></div>
        <div><dt>{t("drawer.costShort")}</dt><dd>{formatUsageValue(goal.usage?.costUsd24h, t("drawer.usageNotMeasured"), formatCostUsd)}</dd></div>
        <div><dt>{t("drawer.durationShort")}</dt><dd>{formatUsageValue(goal.usage?.durationMs24h, t("drawer.usageNotMeasured"), formatDurationMs)}</dd></div>
      </dl>
    </section>
    {readOnly ? <><p className="goal-overview-source-note">{copy.remote}</p><GoalAcceptanceObservationCard goal={goal} /></>
      : <DeliveryReview active={active} goal={goal} items={items} userTodos={userTodos} onSelect={onSelect} />}
  </section>;
}
