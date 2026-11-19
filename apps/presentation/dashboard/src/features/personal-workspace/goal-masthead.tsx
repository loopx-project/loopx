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
      <div className="goal-masthead-title"><h1>{goal.title}</h1>{onOpenDetails ? <button type="button" onClick={onOpenDetails} aria-label={t("masthead.completeTitle")} title={t("masthead.completeTitle")}><Info aria-hidden size={16} /></button> : null}</div>
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

const ribbonOrder = ["done", "running_observed", "needs_you", "open", "waiting_dependency", "blocked", "deferred"] as const;

function Vital({ children, empty, extra, help, label, onClick, sub, tone, valueKey }: {
  children: ReactNode; empty?: boolean; extra?: ReactNode; help?: ReactNode; label: string; onClick: () => void; sub?: ReactNode; tone?: string; valueKey?: string;
}) {
  return (
    <div className="goal-vital-cell"><button aria-disabled={empty} className={`goal-vital${empty ? " is-empty" : ""}${tone ? ` is-${tone}` : ""}`} onClick={empty ? undefined : onClick} tabIndex={empty ? -1 : undefined} type="button">
      <span className="goal-vital-value goal-vital-swap" key={valueKey}>{children}</span>
      <span className="goal-vital-meta">
        <span className="goal-vital-label">{label}</span>
        {sub ? <span className="goal-vital-sub">{sub}</span> : null}
      </span>
      {extra}
    </button>{help}</div>
  );
}

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
  const workSub = [
    work.classes.running_observed ? t("vitals.workRunning", { count: work.classes.running_observed }) : null,
    t("vitals.workOpen", { count: work.open }),
    work.classes.blocked ? t("vitals.workBlocked", { count: work.classes.blocked }) : null,
  ].filter(Boolean).join(" · ");
  const ribbonTotal = ribbonOrder.reduce((sum, name) => sum + work.classes[name], 0);

  const windowLabel = digest.usage.window === "7d" ? t("vitals.window7d") : digest.usage.window === "24h" ? t("vitals.window24h") : t("vitals.notMeasured");
  const needs = digest.needsYou;
  const needsEmpty = needs.availability === "ok" && needs.coverage === "complete"
    && needs.blocking === 0 && needs.optional === 0;
  const last = digest.lastActivity;
  const lastTime = last ? relativeTime(last.at, locale) : null;
  const stateLabel = finishing ? t("vitals.stopping") : t(activity.labelKey as WorkspaceMessageKey);

  return (
    <section aria-label={t("vitals.label")} className="goal-vitals" data-testid="goal-vitals">
      <Vital label={t("vitals.state")} onClick={() => onFocusRegion("overview")} sub={finishing ? t("vitals.turnFinishing") : activity.alsoKey ? t(activity.alsoKey as WorkspaceMessageKey, {host: goalHostSurfaces(goal).map(hostSurfaceLabel).join(" / ")}) : undefined}
        help={stateSub ? <ContextHelp label={`${t("vitals.state")} · ${stateLabel}`}><p>{stateSub}</p></ContextHelp> : undefined}
        tone={finishing ? "stopped" : activity.tone} valueKey={`${goal.goalId}:${stateLabel}`}>
        <span aria-hidden className={`goal-vital-dot${activity.live && !finishing ? " is-live" : ""}`} />
        <span>{stateLabel}</span>
      </Vital>
      <Vital label={t("vitals.work")} onClick={() => onFocusRegion("tasks")} valueKey={`${goal.goalId}:${work.done}:${work.total}:${work.open}`}
        sub={workSub}
        help={work.done === null || work.coverage !== "complete" ? <ContextHelp icon={CircleHelp} label={`${t("vitals.work")} · ${work.total === null ? t("vitals.workTotalUnknown") : (locale === "zh-CN" ? "部分记录" : "Partial records")}`}>
          <p>{t("vitals.scope")}</p>{work.coverage !== "complete" ? <p>{t("vitals.workCoverageUnknown")}</p> : null}
          {work.done === null ? <p>{t("vitals.workDoneUnknown")}</p> : null}
          <p>{work.projectedOpen !== null ? t("vitals.workPartial", {listed: work.listedOpen, projected: work.projectedOpen}) : `${work.listedOpen} ${locale === "zh-CN" ? "项已列出" : "listed"}`}</p>
        </ContextHelp> : undefined}
        extra={<>
          {work.total !== null && ribbonTotal > 0 ? <span aria-hidden className="goal-work-ribbon">{ribbonOrder.map((name) => work.classes[name]
            ? <i className={`is-${name}`} key={name} style={{ flexGrow: work.classes[name] }} /> : null)}</span> : null}
        </>}>
        {work.total === null ? work.open : <span>{work.done}<small>/{work.total}</small></span>}
      </Vital>
      <Vital label={t("vitals.needsYou")} empty={needsEmpty} onClick={() => onFocusRegion("attention")} valueKey={`${goal.goalId}:${needs.availability}:${needs.availability === "ok" ? needs.blocking : ""}`}
        tone={needs.availability === "ok" && needs.blocking !== null && needs.blocking > 0 ? "danger" : needs.availability === "ok" ? "recede" : undefined}
        help={needs.availability !== "ok" || needs.coverage !== "complete" ? <ContextHelp icon={CircleHelp} label={`${t("vitals.needsYou")} · ${needs.availability !== "ok" ? t("vitals.unavailable") : (locale === "zh-CN" ? "部分记录" : "Partial records")}`}><p>{needs.availability === "ok" ? t("vitals.needsYouPartial", {count: needs.listed}) : t("vitals.unavailable")}</p></ContextHelp> : undefined}
        sub={needs.availability !== "ok" ? null : [
          needs.oldestWaitingSince ? t("vitals.needsYouOldest", { time: relativeTime(needs.oldestWaitingSince, locale) ?? "" }) : null,
          needs.optional ? t("vitals.needsYouOptional", { count: needs.optional }) : null,
        ].filter(Boolean).join(" · ")}>
        {needs.availability !== "ok" ? t("vitals.unavailable") : needs.blocking === null ? "—" : needsEmpty ? t("vitals.needsYouNone")
          : <>{needs.blocking > 0 ? <AlertCircle aria-hidden size={15} /> : null}{needs.blocking}</>}
      </Vital>
      <Vital label={t("vitals.agentTime")} onClick={() => onFocusRegion("usage")} sub={digest.usage.agentTimeMs === null ? undefined : windowLabel}
        help={digest.usage.agentTimeMs === null ? <ContextHelp icon={Clock3} label={`${t("vitals.agentTime")} · ${t("vitals.notMeasured")}`}><p>{t("vitals.notMeasured")}</p></ContextHelp> : undefined}
        empty={digest.usage.agentTimeMs === null}
        valueKey={`${goal.goalId}:${digest.usage.window}:${digest.usage.agentTimeMs}`}>
        {digest.usage.agentTimeMs === null ? t("vitals.notMeasured") : formatDurationMs(digest.usage.agentTimeMs)}
      </Vital>
      <Vital label={t("vitals.cost")} onClick={() => onFocusRegion("usage")} sub={digest.usage.costUsd === null ? undefined : windowLabel}
        help={digest.usage.costUsd === null ? <ContextHelp icon={CircleHelp} label={`${t("vitals.cost")} · ${t("vitals.notMeasured")}`}><p>{t("vitals.notMeasured")}</p></ContextHelp> : undefined}
        empty={digest.usage.costUsd === null}
        valueKey={`${goal.goalId}:${digest.usage.window}:${digest.usage.costUsd}`}>
        {digest.usage.costUsd === null ? t("vitals.notMeasured") : formatCostUsd(digest.usage.costUsd)}
      </Vital>
      <Vital label={t("vitals.lastActivity")} onClick={() => onFocusRegion("overview")}
        sub={last ? last.actor || t("vitals.turn") : undefined}
        help={!last ? <ContextHelp icon={Clock3} label={t("vitals.noActivity")}><p>{t("vitals.noActivity")}</p></ContextHelp> : undefined} empty={!last}
        tone={last ? undefined : "recede"}>
        {last && lastTime ? lastTime : t("vitals.noActivity")}
      </Vital>
    </section>
  );
}
