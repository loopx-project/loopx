import {ContextHelp} from "./context-help";
import type {GoalWorkMap} from "../../data/goal-work-map";
import { AlertCircle, ChevronRight, CircleHelp, Clock3, Info, LoaderCircle, Pause, RotateCcw } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import {
  compileGoalWorkspaceDigest,
  type DigestExecution,
  type GoalWorkspaceDigest,
} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import { goalHostSurfaces, hostSurfaceLabel, presentGoalActivity } from "./goal-activity";
import { goalShortLabel } from "./goal-display";
import { relativeTime, useExecutionDetail } from "./goal-activity-view";
import { useWorkspaceI18n, type WorkspaceMessageKey } from "./i18n";
import { formatCostUsd, formatDurationMs, type WorkspaceGoal, type WorkspaceModel, type WorkspaceTimelineItem } from "./personal-workspace-model";
import "./goal-masthead.css";

function digestExecution(execution: WorkspaceGoal["execution"]): DigestExecution | undefined {
  if (!execution) return undefined;
  if (execution.kind !== "running") return execution.kind === "idle" ? { kind: "idle" } : { kind: "unknown" };
  if (execution.work === "unrecognized") return {kind: "unknown"};
  return { kind: "running", observed: execution.work === "executing", quiet: execution.quiet, lastActivityAt: execution.lastActivityAt };
}

export function useGoalWorkspaceDigest(goal: WorkspaceGoal | null, userTodos: WorkspaceModel["userTodos"], items: WorkspaceTimelineItem[], map?: GoalWorkMap | null): GoalWorkspaceDigest {
  return useMemo(() => {
    const nodes = new Map(map?.nodes.map(node => [node.node_id, node]));
    const dependencies = new Map<string, string[]>();
    for (const edge of map?.edges ?? []) {
      if (edge.relation !== "depends_on" || edge.enforcement === "lineage_only") continue;
      const from = nodes.get(edge.from_node_id)?.refs.todo_ids?.[0], target = nodes.get(edge.to_node_id);
      const to = target?.refs.todo_ids?.[0];
      // A monitor change condition cannot be translated into Todo completion.
      if (from && to && target?.kind !== "monitor" && target?.state !== "done") {
        dependencies.set(from, [...dependencies.get(from) ?? [], to]);
      }
    }
    const byTodo = new Map(map?.nodes.flatMap(node => (node.refs.todo_ids ?? []).map(id => [id, node] as const)));
    return compileGoalWorkspaceDigest({
      observedAt: new Date().toISOString(),
      objective: goal?.objective,
      activation: goal?.activationState ?? "active",
      todos: (goal?.agentTodos ?? []).map(todo => ({
        todoId: todo.todoId, done: todo.done, status: todo.status, taskClass: todo.taskClass,
        dependencies: dependencies.get(todo.todoId) ?? todo.dependencies,
        ownerId: todo.claimedBy, completedAt: todo.completedAt, dependencyOrder: byTodo.get(todo.todoId)?.depth,
      })),
      doneCount: goal?.doneTodoCount, openCount: goal?.openTodoCount,
      runningTodoIds: items.flatMap(item => item.kind === "run" && item.run.goalId === goal?.goalId && item.run.status === "running" && item.run.todoId ? [item.run.todoId] : []),
      attention: goal?.loadState === "error" ? null : userTodos.filter(item => item.goalId === goal?.goalId).map(item => ({todoId: item.todoId, blocking: item.blocking, updatedAt: item.updatedAt})),
      attentionSummary: goal?.attentionSummary, usage: goal?.usage, execution: digestExecution(goal?.execution),
      activity: (goal?.hostThreadActivity?.threads ?? []).map(thread => ({actor: hostSurfaceLabel(thread.hostSurface), at: thread.lastEventAt})),
    });
  }, [goal, userTodos, items, map]);
}

export function GoalMastheadIdentity({ goal, onOpenWorkspace, onOpenDetails }: { goal: WorkspaceGoal; onOpenWorkspace: () => void; onOpenDetails?: () => void }) {
  const { t } = useWorkspaceI18n();
  const agents = (goal.agentLanes ?? []).map((lane) => lane.label);
  const context = [
    agents.length > 1 ? t("masthead.team", { count: agents.length }) : agents[0],
    goal.repository?.label,
  ].filter(Boolean);
  return (
    <>
      <nav aria-label={t("masthead.breadcrumb")} className="goal-masthead-breadcrumb">
        <button onClick={onOpenWorkspace} type="button">{t("masthead.workspace")}</button>
        <ChevronRight aria-hidden size={12} />
        <span>{t("masthead.goals")}</span>
        {/* Announce the current page to assistive readers; show its long identity once in the heading. */}
        <span className="personal-sr-only" aria-current="page">{goal.title}</span>

      </nav>
      <div className="goal-masthead-title"><h1 title={goal.title}>{goalShortLabel(goal)}</h1>{onOpenDetails ? <button type="button" onClick={onOpenDetails} aria-label={t("masthead.completeTitle")} title={t("masthead.completeTitle")}><Info aria-hidden size={16} /></button> : null}</div>
      {context.length ? <p className="goal-masthead-context" title={agents.join(", ")}>{context.join(" · ")}</p> : null}
    </>
  );
}

/** The one consequential control, confirmed once with the owner's stated effect.
 * A reviewed Resume already shows the owner's preview, so it is not asked twice. */
export function GoalLifecycleControl({ busy, goal, onRequest, resumeReviewed }: {
  busy: boolean; goal: WorkspaceGoal; onRequest: (operation: "stop" | "resume") => void; resumeReviewed: boolean;
}) {
  const { t } = useWorkspaceI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const stopped = goal.activationState === "stopped";
  const operation = stopped ? "resume" : "stop";
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key !== "Escape" : rootRef.current?.contains(event.target as Node)) return;
      setOpen(false);
      if (event instanceof KeyboardEvent) triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  useEffect(() => setOpen(false), [goal.goalId, goal.activationState]);
  const label = busy ? t("masthead.stopping") : t(stopped ? "sidebar.resume" : "sidebar.stop");
  return (
    <div className="goal-lifecycle-control" ref={rootRef}>
      <button
        aria-busy={busy || undefined}
        aria-expanded={stopped && resumeReviewed ? undefined : open}
        aria-haspopup={stopped && resumeReviewed ? undefined : "dialog"}
        className="goal-lifecycle-trigger"
        disabled={busy}
        onClick={() => (stopped && resumeReviewed ? onRequest("resume") : setOpen((current) => !current))}
        ref={triggerRef}
        type="button"
      >
        {busy ? <LoaderCircle aria-hidden className="is-spinning" size={15} /> : stopped ? <RotateCcw aria-hidden size={15} /> : <Pause aria-hidden size={15} />}
        <span>{label}</span>
      </button>
      {open ? (
        <div aria-labelledby={titleId} className="goal-lifecycle-confirm" role="dialog">
          <strong id={titleId}>{t(stopped ? "masthead.resumeTitle" : "masthead.stopTitle")}</strong>
          <p>{t(stopped ? "masthead.resumeEffect" : "masthead.stopEffect")}</p>
          <div>
            <button onClick={() => { setOpen(false); triggerRef.current?.focus(); }} type="button">{t("common.cancel")}</button>
            <button className="is-primary" onClick={() => { setOpen(false); onRequest(operation); }} ref={confirmRef} type="button">
              {t(stopped ? "sidebar.resumeGoal" : "sidebar.stopGoal")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export type GoalVitalRegion = "overview" | "tasks" | "attention" | "usage";

/** Holds the strip's geometry while a Goal's first read is in flight. */
export function GoalVitalsSkeleton() {
  const { t } = useWorkspaceI18n();
  return (
    <section aria-busy="true" aria-label={t("vitals.label")} className="goal-vitals is-loading" data-testid="goal-vitals-loading">
      {Array.from({ length: 6 }, (_, index) => <span aria-hidden className="goal-vital goal-vital-skeleton" key={index}><i /><i /></span>)}
    </section>
  );
}

export function GoalVitals({ goal, items, onFocusRegion, userTodos, digest: sharedDigest }: {
  digest?: GoalWorkspaceDigest;
  goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; onFocusRegion: (region: GoalVitalRegion) => void; userTodos: WorkspaceModel["userTodos"];
}) {
  const { locale, t } = useWorkspaceI18n();
  const localDigest = useGoalWorkspaceDigest(goal, userTodos, items);
  const digest = sharedDigest ?? localDigest;
  const activity = presentGoalActivity(goal);
  const detail = useExecutionDetail(goal.execution);
  const finishing = digest.lifecycle.finishingTurn;
  const stateSub = finishing ? t("vitals.turnFinishing")
    : activity.alsoKey ? t(activity.alsoKey as WorkspaceMessageKey, { host: goalHostSurfaces(goal).map(hostSurfaceLabel).join(" / ") })
      : detail.length ? detail.join(" · ")
        : goal.execution?.kind === "unknown" ? t("vitals.activityUnknown") : null;

  const { work } = digest;

  // Cost and Agent time stay reachable in the bar: one group, values never hidden behind hover.
  const unmeasured = t("vitals.notMeasured");
  const usageText = (value: number | null, format: (value: number) => string) => value === null ? unmeasured : format(value);
  const windowText = (value: number | undefined, format: (value: number) => string) => typeof value === "number" && Number.isFinite(value) ? format(value) : unmeasured;
  const usageTitle = [
    `${t("vitals.cost")} · ${t("vitals.window24h")} ${windowText(goal.usage?.costUsd24h, formatCostUsd)} · ${t("vitals.window7d")} ${windowText(goal.usage?.costUsd7d, formatCostUsd)}`,
    `${t("vitals.agentTime")} · ${t("vitals.window24h")} ${windowText(goal.usage?.durationMs24h, formatDurationMs)} · ${t("vitals.window7d")} ${windowText(goal.usage?.durationMs7d, formatDurationMs)}`,
  ].join(" · ");
  const costValue = usageText(digest.usage.costUsd, formatCostUsd);
  const agentTimeValue = usageText(digest.usage.agentTimeMs, formatDurationMs);
  const needs = digest.needsYou;
  const last = digest.lastActivity;
  const lastTime = last ? relativeTime(last.at, locale) : null;
  const stateLabel = finishing ? t("vitals.stopping") : t(activity.labelKey as WorkspaceMessageKey);

  const stages = locale === "zh-CN" ? ["计划", "执行", "验收"] : ["Plan", "Run", "Accept"];
  const stageIndex = goal.state === "已完成" ? 3
    : (work.classes.running_observed > 0 || work.open > 0 || goal.execution?.kind === "running") ? 1
      : goal.acceptanceObservation ? 2 : 0;
  const waitingOn = needs.availability === "ok" && needs.blocking
    ? (locale === "zh-CN" ? "等你决定" : "Waiting on you")
    : work.classes.running_observed ? (locale === "zh-CN" ? "Agent 执行中" : "Agent running") : null;

  return (
    <section aria-label={t("vitals.label")} className="goal-stagebar" data-testid="goal-vitals">
      <ol className="goal-stagebar-track">
        {stages.map((label, index) => (
          <li key={label} className={index < stageIndex ? "is-done" : index === stageIndex ? "is-current" : "is-todo"}>
            <button onClick={() => onFocusRegion(index === 1 ? "tasks" : index === 2 ? "attention" : "overview")} type="button">
              <span aria-hidden="true" className="goal-stagebar-dot" /><span>{label}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="goal-stagebar-meta">
        <span title={stateSub ?? undefined}><i aria-hidden="true" className={`goal-stagebar-state is-${finishing ? "stopped" : activity.tone}`} />{stateLabel}</span>
        {waitingOn ? <span className="goal-stagebar-wait">{waitingOn}</span> : null}
        <span className="goal-stagebar-work">{work.total === null ? t("vitals.workOpen", { count: work.open }) : `${work.done}/${work.total}`}</span>
        <span className="goal-stagebar-time" title={last?.actor ?? undefined}>{lastTime ?? t("vitals.noActivity")}</span>
        {/* Cost and Agent time belong to the usage detail; the group is the only bar control for it. */}
        <span aria-hidden="true" className="goal-stagebar-divider" />
        <button className="goal-stagebar-usage" onClick={() => onFocusRegion("usage")} title={usageTitle} type="button">
          <span className="goal-stagebar-usage-item">
            <span className="goal-stagebar-usage-label">{t("vitals.cost")}</span>
            <span className={digest.usage.costUsd === null ? "goal-stagebar-usage-value is-unmeasured" : "goal-stagebar-usage-value"}>{costValue}</span>
          </span>
          <span className="goal-stagebar-usage-sep">·</span>
          <span className="goal-stagebar-usage-item">
            <span className="goal-stagebar-usage-label">{t("vitals.agentTime")}</span>
            <span className={digest.usage.agentTimeMs === null ? "goal-stagebar-usage-value is-unmeasured" : "goal-stagebar-usage-value"}>{agentTimeValue}</span>
          </span>
        </button>
      </div>
    </section>
  );
}
