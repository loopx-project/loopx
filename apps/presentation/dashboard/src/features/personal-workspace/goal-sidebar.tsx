import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Bot, ChevronDown, ChevronRight, Focus, LoaderCircle, Pause, Plus, RotateCcw, Settings2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DesktopUpdate } from "./desktop-update";
import { useGoalOrder } from "./use-goal-order";
import { goalHierarchy, visibleGoalTree } from "./goal-hierarchy";

import { GoalActivityHelp, GoalIdentityMark, useGoalActivity } from "./goal-activity-view";
import { useWorkspaceI18n } from "./i18n";
import type { WorkspaceGoal, WorkspaceGoalArchiveLoadState } from "./personal-workspace-model";
import { StatusSourceSwitcher, type StatusSourceControl } from "./status-source-switcher";

function GoalRowActivity({ goal, showLoad }: { goal: WorkspaceGoal; showLoad: boolean }) {
  const { t } = useWorkspaceI18n();
  const activity = useGoalActivity(goal);
  if (goal.loadState && showLoad) return <small>{t(goal.loadState === "error" ? "startup.goalError" : "startup.goalLoading")}</small>;
  return (
    <small className={`is-${activity.tone}`} title={activity.text}>
      <span className="personal-goal-row-activity-copy">{activity.text}</span>
      {activity.windowBadge ? <span className={`personal-goal-window-badge is-${activity.windowBadge.state}`}>{activity.windowBadge.text}</span> : null}
    </small>
  );
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
  rootGoalId,
  onChangeGoalRoot,
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
  rootGoalId?: string;
  onChangeGoalRoot?: (goalId: string | null) => void;
  statusSourceControl?: StatusSourceControl;
}) {
  const { t } = useWorkspaceI18n();
  const [sorting, setSorting] = useState(false);
  const tree = useMemo(() => goalHierarchy(goals), [goals]);
  const siblings = (from: string, to: string) => tree.parents.get(from) === tree.parents.get(to);
  const ordering = useGoalOrder(goals.filter((goal) => goal.activationState !== "stopped"), statusSourceControl?.activeSource.statusUrl ?? "/status.json", siblings, statusSourceControl?.activeSource.kind === "runtime");
  const activeGoals = ordering.sorted;
  const stoppedGoals = goals.filter((goal) => goal.activationState === "stopped");
  const siblingGroups = new Map<string | null, WorkspaceGoal[]>();
  for (const goal of activeGoals) {
    const parent = tree.parents.get(goal.goalId) ?? null;
    const group = siblingGroups.get(parent) ?? [];
    group.push(goal);
    siblingGroups.set(parent, group);
  }
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(rootGoalId ? [rootGoalId] : []));
  const ancestors: string[] = [];
  let parent = selectedGoalId ? tree.parents.get(selectedGoalId) : undefined;
  while (parent) { ancestors.push(parent); parent = tree.parents.get(parent); }
  const ancestorKey = JSON.stringify(ancestors);
  useEffect(() => {
    const parents: string[] = JSON.parse(ancestorKey);
    if (parents.length) setExpanded(current => new Set([...current, ...parents]));
  }, [ancestorKey]);
  const hasTree = tree.parents.size > 0;
  const toggle = (id: string) => setExpanded(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const lifecycleOperationEnabled = (operation: "stop" | "resume" | "delete") => (
    Boolean(onRequestGoalLifecycle)
    && (!goalLifecycleOperations || goalLifecycleOperations.includes(operation))
  );
  const goalRow = (goal: WorkspaceGoal, stopped: boolean, depth = 0, hasChildren = false) => (
    <div className={`personal-goal-row${hasTree ? " has-goal-tree" : ""}${ordering.target?.id === goal.goalId ? ordering.target.after ? " is-drop-after" : " is-drop-before" : ""}`} key={goal.goalId} data-reorder-goal={stopped ? undefined : goal.goalId} data-load-error={goal.loadError} data-goal-depth={depth} style={depth ? {paddingInlineStart: Math.min(depth, 4) * 10} : undefined}>
      {hasChildren ? <button type="button" className="personal-goal-tree-toggle" aria-expanded={expanded.has(goal.goalId)} aria-label={t(expanded.has(goal.goalId) ? "sidebar.collapseChildren" : "sidebar.expandChildren", {goal: goal.title})} onClick={() => toggle(goal.goalId)}>{expanded.has(goal.goalId) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
        : hasTree ? <span className="personal-goal-tree-spacer" aria-hidden /> : null}
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
          <strong title={goal.title}>{goal.title}</strong>
          {tree.parents.has(goal.goalId) ? <span className="personal-sr-only">{t("sidebar.childOf", {goal: tree.byId.get(tree.parents.get(goal.goalId)!)!.title})}</span> : null}
          <GoalRowActivity goal={goal} showLoad={!stopped || selectedGoalId === goal.goalId} />
        </span>
        {tree.invalidParents.has(goal.goalId) && goal.goalId !== rootGoalId ? <AlertTriangle size={14} aria-label={t("sidebar.invalidParent")}><title>{t("sidebar.invalidParent")}</title></AlertTriangle>
          : !sorting && goal.hostThreadActivity && !stopped && !goal.loadState ? null : <ChevronRight size={15} />}
      </button>
      {!sorting ? <GoalActivityHelp key={`${statusSourceControl?.activeSource.statusUrl}:${goal.sourceAccess?.namespace}:${goal.goalId}`} goal={goal} /> : null}
      {!stopped && sorting ? <div className="personal-goal-move-actions">
        <button type="button" aria-label={t("sidebar.moveUp", { goal: goal.title })} disabled={siblingGroups.get(tree.parents.get(goal.goalId) ?? null)?.[0]?.goalId === goal.goalId} onClick={() => ordering.moveBy(goal.goalId, -1)}><ArrowUp aria-hidden="true" size={13} /></button>
        <button type="button" aria-label={t("sidebar.moveDown", { goal: goal.title })} disabled={siblingGroups.get(tree.parents.get(goal.goalId) ?? null)?.at(-1)?.goalId === goal.goalId} onClick={() => ordering.moveBy(goal.goalId, 1)}><ArrowDown aria-hidden="true" size={13} /></button>
      </div> : null}
      {!goal.sourceAccess?.readOnly && onRequestGoalLifecycle && lifecycleOperationEnabled(stopped ? "resume" : "stop") ? (
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
        {!rootGoalId ? <button
          aria-current={selectedGoalId === null ? "page" : undefined}
          className="personal-manager-link"
          onClick={() => onSelectGoal(null)}
          type="button"
        >
          <span className="personal-manager-icon"><Bot size={17} /></span>
          <span>{t("sidebar.manager")}</span>
          {attentionCount > 0 ? <span className="personal-sidebar-count">{attentionCount}</span> : null}
          <ChevronRight size={15} />
        </button> : null}

        <div className="personal-sidebar-section-title">
          <span>Goals</span>
          <span className="personal-sidebar-title-actions"><small>{activeGoals.length}</small><button aria-label={t("sidebar.sortGoals")} title={t("sidebar.sortGoals")} aria-pressed={sorting} onClick={() => setSorting(!sorting)} type="button"><ArrowUpDown aria-hidden="true" size={15} /></button>{onRequestGoalCreate ? <button aria-label={t("sidebar.createGoal")} onClick={onRequestGoalCreate} type="button"><Plus size={15} /></button> : null}</span>
        </div>
        {onChangeGoalRoot && (rootGoalId || (selectedGoalId && tree.children.has(selectedGoalId))) ? <button className="personal-goal-root-scope" type="button" onClick={() => onChangeGoalRoot(rootGoalId ? null : selectedGoalId)} title={t("sidebar.scopeHelp")}><Focus size={14} />{t(rootGoalId ? "sidebar.allGoals" : "sidebar.focusGoal")}</button> : null}
        {ordering.saveFailed ? <p role="status">{t("sidebar.orderNotSaved")}</p> : null}
        <span className="personal-sr-only" role="status">{ordering.lastMoved ? t("sidebar.goalMoved", { goal: ordering.lastMoved.title, position: ordering.lastMoved.position }) : ""}</span>
        <div className="personal-goal-list">
          {visibleGoalTree(activeGoals, expanded).map(({goal, depth, hasChildren}) => goalRow(goal, false, depth, hasChildren))}
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
              {visibleGoalTree(stoppedGoals, expanded).map(({goal, depth, hasChildren}) => goalRow(goal, true, depth, hasChildren))}
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
