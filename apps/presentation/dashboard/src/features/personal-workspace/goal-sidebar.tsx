import { ArrowDown, ArrowUp, ArrowUpDown, Bot, ChevronDown, ChevronRight, LoaderCircle, Pause, Plus, RotateCcw, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";
import { DesktopUpdate } from "./desktop-update";
import { useGoalOrder } from "./use-goal-order";

import { GoalIdentityMark, useGoalActivity } from "./goal-activity-view";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal, WorkspaceGoalArchiveLoadState } from "./personal-workspace-model";
import { StatusSourceSwitcher, type StatusSourceControl } from "./status-source-switcher";

function GoalRowActivity({ goal, showLoad }: { goal: WorkspaceGoal; showLoad: boolean }) {
  const { t } = useWorkspaceI18n();
  const activity = useGoalActivity(goal);
  if (goal.loadState && showLoad) return <small>{t(goal.loadState === "error" ? "startup.goalError" : "startup.goalLoading")}</small>;
  return <small className={`is-${activity.tone}`}>{activity.text}</small>;
}

export function GoalSidebar({
  attentionCount,
  goals,
  goalArchiveLoadState = { error: null, phase: "ready" },
  lifecycleBusyGoalIds,
  goalLifecycleOperations,
  onRequestGoalCreate,
  onOpenSettings,
  onRetryGoalArchive,
  onRequestGoalLifecycle,
  onSelectGoal,
  selectedGoalId,
  statusSourceControl,
}: {
  attentionCount: number;
  goals: WorkspaceGoal[];
  goalArchiveLoadState?: WorkspaceGoalArchiveLoadState;
  lifecycleBusyGoalIds?: ReadonlySet<string>;
  goalLifecycleOperations?: readonly ("stop" | "resume" | "delete")[];
  onRequestGoalCreate?: () => void;
  onOpenSettings?: () => void;
  onRetryGoalArchive?: () => void;
  onRequestGoalLifecycle?: (goal: WorkspaceGoal, operation: "stop" | "resume" | "delete") => void;
  onSelectGoal: (goalId: string | null) => void;
  selectedGoalId: string | null;
  statusSourceControl?: StatusSourceControl;
}) {
  const { t } = useWorkspaceI18n();
  const [sorting, setSorting] = useState(false);
  const ordering = useGoalOrder(goals.filter((goal) => goal.activationState !== "stopped"), statusSourceControl?.activeSource.statusUrl ?? "/status.json");
  const activeGoals = ordering.sorted;
  const stoppedGoals = goals.filter((goal) => goal.activationState === "stopped");
  const lifecycleOperationEnabled = (operation: "stop" | "resume" | "delete") => (
    Boolean(onRequestGoalLifecycle)
    && (!goalLifecycleOperations || goalLifecycleOperations.includes(operation))
  );
  const goalRow = (goal: WorkspaceGoal, stopped: boolean) => (
    <div className={`personal-goal-row${ordering.target?.id === goal.goalId ? ordering.target.after ? " is-drop-after" : " is-drop-before" : ""}`} key={goal.goalId} data-reorder-goal={stopped ? undefined : goal.goalId} data-load-error={goal.loadError}>
      <button
        {...(!stopped ? ordering.pointerProps(goal.goalId) : {})}
        title={stopped ? undefined : t("sidebar.dragGoal")}
        aria-current={selectedGoalId === goal.goalId ? "page" : undefined}
        className="personal-goal-link"
        onClick={() => onSelectGoal(goal.goalId)}
        type="button"
      >
        <GoalIdentityMark goal={goal} />
        <span className="personal-goal-link-copy">
          <strong>{goal.title}</strong>
          <GoalRowActivity goal={goal} showLoad={!stopped || selectedGoalId === goal.goalId} />
        </span>
        <ChevronRight size={15} />
      </button>
      {!stopped && sorting ? <div className="personal-goal-move-actions">
        <button type="button" aria-label={t("sidebar.moveUp", { goal: goal.title })} disabled={activeGoals[0]?.goalId === goal.goalId} onClick={() => ordering.moveBy(goal.goalId, -1)}><ArrowUp aria-hidden="true" size={13} /></button>
        <button type="button" aria-label={t("sidebar.moveDown", { goal: goal.title })} disabled={activeGoals.at(-1)?.goalId === goal.goalId} onClick={() => ordering.moveBy(goal.goalId, 1)}><ArrowDown aria-hidden="true" size={13} /></button>
      </div> : null}
      {onRequestGoalLifecycle && lifecycleOperationEnabled(stopped ? "resume" : "stop") ? (
        <>
          <button
            aria-label={`${stopped ? t("sidebar.resume") : t("sidebar.stop")} ${goal.title}`}
            aria-busy={lifecycleBusyGoalIds?.has(goal.goalId) || undefined}
            className={`personal-goal-lifecycle${lifecycleBusyGoalIds?.has(goal.goalId) ? " is-pending" : ""}`}
            disabled={lifecycleBusyGoalIds?.has(goal.goalId)}
            onClick={() => onRequestGoalLifecycle(goal, stopped ? "resume" : "stop")}
            title={stopped ? t("sidebar.resumeGoal") : t("sidebar.stopGoal")}
            type="button"
          >
            {lifecycleBusyGoalIds?.has(goal.goalId)
              ? <LoaderCircle size={13} />
              : stopped ? <RotateCcw size={13} /> : <Pause size={13} />}
          </button>
          {stopped && lifecycleOperationEnabled("delete") ? (
            <button
              aria-label={`${t("sidebar.delete")} ${goal.title}`}
              className="personal-goal-lifecycle personal-goal-delete"
              onClick={() => onRequestGoalLifecycle(goal, "delete")}
              title={t("sidebar.deleteGoal")}
              type="button"
            >
              <Trash2 size={13} />
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
  return (
    <div className="personal-goal-directory">
      <div className="personal-sidebar-brand">
        <span className="personal-brand-mark"><Bot size={18} /></span>
        <span><strong>LoopX</strong></span>
      </div>

      {statusSourceControl ? <StatusSourceSwitcher {...statusSourceControl} /> : null}

      <nav aria-label={t("home.workspace")} className="personal-sidebar-nav">
        <button
          aria-current={selectedGoalId === null ? "page" : undefined}
          className="personal-manager-link"
          onClick={() => onSelectGoal(null)}
          type="button"
        >
          <span className="personal-manager-icon"><Bot size={17} /></span>
          <span>{t("sidebar.manager")}</span>
          {attentionCount > 0 ? <span className="personal-sidebar-count">{attentionCount}</span> : null}
          <ChevronRight size={15} />
        </button>

        <div className="personal-sidebar-section-title">
          <span>Goals</span>
          <span className="personal-sidebar-title-actions"><small>{activeGoals.length}</small><button aria-label={t("sidebar.sortGoals")} title={t("sidebar.sortGoals")} aria-pressed={sorting} onClick={() => setSorting(!sorting)} type="button"><ArrowUpDown aria-hidden="true" size={15} /></button>{onRequestGoalCreate ? <button aria-label={t("sidebar.createGoal")} onClick={onRequestGoalCreate} type="button"><Plus size={15} /></button> : null}</span>
        </div>
        {ordering.saveFailed ? <p role="status">{t("sidebar.orderNotSaved")}</p> : null}
        <span className="personal-sr-only" role="status">{ordering.lastMoved ? t("sidebar.goalMoved", { goal: ordering.lastMoved.title, position: ordering.lastMoved.position }) : ""}</span>
        <div className="personal-goal-list">
          {activeGoals.map((goal) => goalRow(goal, false))}
        </div>
        {stoppedGoals.length || goalArchiveLoadState.phase === "loading" || goalArchiveLoadState.phase === "error" ? (
          <details
            className="personal-stopped-goals"
            open={goalArchiveLoadState.phase === "error" ? true : undefined}
          >
            <summary>
              <ChevronDown size={13} />
              <span>{t("sidebar.stopped")}</span>
              {goalArchiveLoadState.phase === "loading"
                ? <LoaderCircle aria-label={t("sidebar.stoppedLoading")} className="is-spinning" size={13} />
                : <small>{stoppedGoals.length}</small>}
            </summary>
            <div className="personal-goal-list is-stopped">
              {goalArchiveLoadState.phase === "error" ? (
                <div className="personal-stopped-goal-error" role="alert">
                  <span>{t("sidebar.stoppedLoadFailed")}</span>
                  {onRetryGoalArchive ? <button onClick={onRetryGoalArchive} type="button">{t("sidebar.retryStopped")}</button> : null}
                </div>
              ) : null}
              {stoppedGoals.map((goal) => goalRow(goal, true))}
            </div>
          </details>
        ) : null}
      </nav>

      <div className="personal-sidebar-footer">
        <DesktopUpdate />
        {onOpenSettings ? (
          <button aria-label={t("settings.open")} className="personal-sidebar-utility" onClick={onOpenSettings} type="button">
            <span className="personal-sidebar-utility-icon"><Settings2 size={17} /></span>
            <span className="personal-sidebar-utility-copy"><strong>{t("settings.open")}</strong></span>
            <ChevronRight aria-hidden="true" size={15} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
