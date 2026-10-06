import { goalCreateRequest } from "./goal-create-request";
import { readSteeringRequest, retainSteeringRequest, retireSteeringRequest } from "./steering-recovery";
import { useConversationInputState } from "./use-conversation-input-state";
import type { ConversationHistoryStatus } from "../../data/use-conversation-history";
import { GoalDraftCard } from "./goal-draft-card";
import type { GoalDraft } from "../../../../../../loopx/control_plane/collaboration/goal_draft.js";
import { CollaborationCard } from "./collaboration-card";
import {
  compileActionReviewPlan,
  isStaleActionFailure,
} from "../../../../../../loopx/control_plane/presentation/action_review_plan.js";
import { refreshAttention } from "./attention-details";
import { teamPlanAssignments, teamPlanAppliedLine, teamPlanAppliedOutcome, teamPlanFields, teamPlanGoalId, teamPlanLaneCount, teamPlanReceiptGapLanes, teamPlanTodoIds } from "./team-plan-preview";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent as ReactClipboardEvent, type ReactNode } from "react";
import { AlertCircle, Bot, CalendarClock, FileText, ListPlus, MessageCircleQuestion, Paperclip, Plus, RefreshCw, Send, X } from "lucide-react";

import {
  applyTypedAction,
  cancelTypedAction,
  ChatApiError,
  configureGoalChannelAutoNotify,
  conversationContextKey,
  fetchGoalContexts,
  fetchGoalChannelTargets,
  fetchLarkConnections,
  fetchLoopXMode,
  previewTypedAction,
  setupGoalChannel,
  stewardPrompts,
  transitionTypedAction,
  type GoalRepositoryContext,
  type LarkGoalConnection,
  type ManagerChannelBinding,
  type ManagerRuntimeSessionReadback,
  type TypedActionProposal,
} from "../../data/chat";
import { useTypedActionReadback } from "../../data/use-typed-action-readback";

import { ChannelHeader } from "./channel-header";
import { GoalLoopXMode } from "./goal-loopx-mode";
import { GoalTeamResults } from "./goal-team-results";
import { GoalManagedResults } from "./goal-managed-results";
import { sendLoopXMessage, type LoopXModeSnapshot } from "../../data/chat";
import { MessageActivity } from "./message-activity";
import { ChannelTimeline } from "./channel-timeline";
import { ContextDrawer } from "./context-drawer";
import { monitorScheduleReadback } from "./monitor-readback";
import { GoalSidebar } from "./goal-sidebar";
import { GoalTasksView } from "./goal-tasks-view";
import { GoalOverview } from "./goal-overview";
import { GoalWorkspacePanels } from "./goal-workspace-panels";
import { localizedGoalState, localizedSessionStatus, useWorkspaceI18n, type WorkspaceTranslate } from "./i18n";
import { MarkdownText } from "./markdown";
import { ReturnDeliveryStatus } from "./return-delivery-status";
import type {
  PersonalWorkspaceCallbacks,
  WorkspaceAgentOption,
  WorkspaceActionPreview,
  WorkspaceActionPreviewRequest,
  WorkspaceDrawerSelection,
  WorkspaceGoal,
  WorkspaceGoalArchiveLoadState,
  WorkspaceGoalTab,
  WorkspaceImageAttachment,
  WorkspaceModel,
  WorkspaceRun,
  WorkspaceSystemHealth,
  WorkspaceConversationDirectory,
  WorkspaceTimelineItem,
  WorkspaceTodo,
} from "./personal-workspace-model";
import { goalHasExecutionSummary, goalTitleFor, workspaceHomeLaneForGoal } from "./personal-workspace-model";
import { goalWorkKind } from "./goal-activity";
import { WorkspaceActionForm, type WorkspaceActionDraft } from "./workspace-action-form";
import { GoalActivityChip, GoalIdentityMark } from "./goal-activity-view";
import { ManagerBrief } from "./manager-brief";
import { WorkspaceSettingsPage } from "./workspace-settings-page";
import { UsageStatisticsNotice } from "./usage-statistics-notice";
import { readWorkspaceTheme, writeWorkspaceTheme, type WorkspaceTheme } from "./workspace-theme";
import { compareProposalRecency } from "./proposal-recency";
import { WorkspaceShell } from "./workspace-shell";
import type { StatusSourceControl } from "./status-source-switcher";
import "./personal-workspace.css";

function dedupeProposals(proposals: WorkspaceActionPreview[]): WorkspaceActionPreview[] {
  const latest = new Map<string, WorkspaceActionPreview>();
  proposals.forEach((proposal) => {
    const subject = proposal.fields.find((field) => field.key === "todo_id")?.value ?? "";
    const key = proposal.actionKind === "operation.execute" ? proposal.previewId
      : [proposal.actionKind, proposal.goalId ?? "", subject, proposal.title].join(":");
    // Two records can describe the same draft; keep the newest by its stored
    // time rather than whichever one this list happened to end with, since a
    // restored list and a session-created draft arrive in opposite orders.
    const current = latest.get(key);
    if (!current || compareProposalRecency(proposal, current) < 0) latest.set(key, proposal);
  });
  return [...latest.values()];
}

function activityTimeLabel(value: string | undefined, locale: string, t: WorkspaceTranslate) {
  if (!value) return t("home.noFirstActivity");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const today = new Date();
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false }).format(parsed);
  if (parsed.toDateString() === today.toDateString()) return t("home.todayAt", { time });
  return new Intl.DateTimeFormat(locale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(parsed);
}

function ManagerHomeBoard({
  goals,
  onSelectGoal,
  onRetry,
  systemHealth,
  operations,
  onSelectOperation,
  onViewAllOperations,
}: {
  goals: WorkspaceGoal[];
  onSelectGoal: (goalId: string) => void;
  onRetry?: () => void;
  systemHealth?: WorkspaceSystemHealth;
  operations: WorkspaceActionPreview[];
  onSelectOperation: (proposal: WorkspaceActionPreview) => void;
  onViewAllOperations: () => void;
}) {
  const { locale, t } = useWorkspaceI18n();
  const currentGoals = goals.filter((goal) => goal.activationState === "active");
  const failedCount = currentGoals.filter((goal) => goal.loadState === "error").length;
  const activeHomeLanes = [
    { key: "needs_you", label: t("home.lane.needsYou") },
    { key: "running", label: t("home.lane.running") },
    { key: "claimed", label: t("home.lane.claimed") },
    { key: "observing", label: t("home.lane.observing") },
    { key: "scheduled", label: t("home.lane.scheduled") },
  ] as const;
  const active = Object.fromEntries(activeHomeLanes.map((lane) => [lane.key, [] as WorkspaceGoal[]])) as Record<(typeof activeHomeLanes)[number]["key"], WorkspaceGoal[]>;
  const history: WorkspaceGoal[] = [];
  goals.filter((goal) => !goal.loadState).forEach((goal) => {
    const lane = workspaceHomeLaneForGoal(goal);
    if (lane === "history") history.push(goal);
    else if (lane !== "stopped") active[lane].push(goal);
  });
  const goalCard = (goal: WorkspaceGoal) => (
    <button className="personal-home-goal-card" data-goal-state={goal.loadState ?? goal.state} data-load-error={goal.loadError} key={goal.goalId} onClick={() => onSelectGoal(goal.goalId)} type="button">
      <span className="personal-home-goal-title"><GoalIdentityMark goal={goal} /><strong>{goal.title}</strong></span>
      <span className="personal-home-goal-meta">{goal.agentLaneCount && goal.agentLaneCount > 1
        ? t("header.workAgentCount", { count: goal.agentLaneCount })
        : goal.agentLabel ?? goal.agentId}</span>
      <p>{goal.loadError ? t(`startup.error.${goal.loadError}`) : goal.needsYou ?? goal.nextSentence}</p>
      <footer>{goal.loadState ? <span>{t(goal.loadState === "error" ? "startup.goalError" : "startup.goalLoading")}</span> : <GoalActivityChip goal={goal} />}<small title={goal.latestActivity}>{goal.loadState ? "" : goal.latestActivity ? activityTimeLabel(goal.latestActivity, locale, t) : goal.agentTodos.length ? t("home.taskCount", { count: goal.agentTodos.length }) : t("home.noActivity")}</small></footer>
    </button>
  );
  return (
    <section aria-label={t("home.workspace")} className="personal-home-board">
      {systemHealth && (!systemHealth.ok || systemHealth.issues.length > 0 || systemHealth.freshnessWarning) ? (
        <div className="personal-system-health-banner" role="alert">
          <div className="personal-system-health-header">
            <AlertCircle size={15} />
            <strong>{t("home.systemHealth", { summary: systemHealth.summary })}</strong>
            {systemHealth.freshnessWarning ? <small>（{systemHealth.freshnessWarning}）</small> : null}
          </div>
          {systemHealth.issues.length > 0 ? (
            <ul className="personal-system-health-issues">
              {systemHealth.issues.map((issue, idx) => (
                <li key={idx}>{issue}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {currentGoals.some((goal) => goal.loadState) ? <section className="personal-home-lane" aria-live="polite">
        <header>{t("startup.progress", { loaded: currentGoals.filter((goal) => !goal.loadState).length, total: currentGoals.length })}</header>
        {failedCount ? <div className="personal-stopped-goal-error" role="status"><span>{t("startup.failedCount", { count: failedCount })}</span>
          <button className="min-h-11 rounded-md border px-3 py-2 text-sm" onClick={onRetry} type="button">{t("startup.retryFailed")}</button></div> : null}
        {currentGoals.filter((goal) => goal.loadState).map(goalCard)}
      </section> : null}
      {currentGoals.some((goal) => !goal.loadState) ? <ManagerBrief goals={goals} onSelectGoal={onSelectGoal}
        operations={operations} onSelectOperation={onSelectOperation} onViewAllOperations={onViewAllOperations} /> : null}
      <h2 className="personal-home-section-title">{t("home.allGoals")}</h2>
      <div className="personal-home-lanes">
        {activeHomeLanes.filter((lane) => active[lane.key].length > 0).map((lane) => (
          <section className={`personal-home-lane is-${lane.key}`} data-testid={`personal-home-lane-${lane.key}`} key={lane.key}>
            <header><span><i />{lane.label}</span><b>{active[lane.key].length}</b></header>
            <div className="personal-home-lane-list">
              {active[lane.key].map(goalCard)}
            </div>
          </section>
        ))}
      </div>
      {history.length ? <details className="personal-home-history">
        <summary><span>{t("home.history")}</span><b>{history.length}</b><small>{t("home.completedGoals")}</small></summary>
        <div>{history.map(goalCard)}</div>
      </details> : null}
    </section>
  );
}

function GoalOutputsView({
  active,
  items,
  onSelect,
  reportState,
  teamSessionId,
  goalId,
  localResults,
}: {
  active: boolean;
  items: Array<Extract<WorkspaceTimelineItem, { kind: "output" }>>;
  onSelect: (selection: WorkspaceDrawerSelection) => void;
  reportState?: WorkspaceModel["periodicReports"];
  teamSessionId?: string;
  goalId: string;
  localResults: boolean;
}) {
  const { locale, t } = useWorkspaceI18n();
  const [teamSnapshot, setTeamSnapshot] = useState<LoopXModeSnapshot | null>(null);
  const [teamError, setTeamError] = useState(false);
  const [teamRefresh, setTeamRefresh] = useState(0);
  useEffect(() => {
    if (!active || !teamSessionId) {
      setTeamSnapshot(null);
      setTeamError(false);
      return;
    }
    let current = true;
    setTeamSnapshot(null);
    setTeamError(false);
    void fetchLoopXMode(teamSessionId).then((snapshot) => {
      if (current) setTeamSnapshot(snapshot);
    }).catch(() => {
      if (current) setTeamError(true);
    });
    return () => { current = false; };
  }, [active, teamSessionId, teamRefresh]);
  const teamConfigured = Boolean(teamSnapshot?.session_id === teamSessionId
    && teamSnapshot?.settings.agent_id && teamSnapshot?.settings.execution_config);
  return (
    <>
      <section className="personal-object-list personal-files-list" data-testid="personal-goal-outputs">
        <header><strong>{t("files.title")}</strong>{!teamConfigured ? <span>{items.length}</span> : null}</header>
        {reportState?.loading ? (
          <p className="personal-object-list-state" role="status"><RefreshCw className="is-spinning" size={14} />{t("files.loadingReports")}</p>
        ) : null}
        {reportState?.error ? (
          <p className="personal-object-list-state is-error" role="alert"><AlertCircle size={14} />{t("files.reportLoadFailed")}: {reportState.error}</p>
        ) : null}
        {!reportState?.loading && !reportState?.error && items.length === 0 && !teamConfigured && !localResults
          && (!teamSessionId || Boolean(teamSnapshot)) ? (
          <p className="personal-object-list-state"><FileText size={14} />{t("files.empty")}</p>
        ) : null}
        {items.map((item) => (
          <button data-output-kind={item.output.kind} key={item.id} onClick={() => onSelect({ item: item.output, kind: "output" })} type="button">
            <span className="personal-file-icon"><FileText size={16} /></span>
            <strong>{item.output.title}</strong>
            {item.output.report ? <em>{t("files.reportDelta", { added: item.output.report.addedCount, changed: item.output.report.changedCount })}</em> : null}
            <p>{item.output.summary ?? item.output.safePreview ?? item.output.kind ?? t("files.emptySummary")}</p>
            <small title={item.output.createdAt}>{[
              item.output.kind === "report" ? t("files.verifiedReport") : null,
              activityTimeLabel(item.output.createdAt, locale, t),
            ].filter(Boolean).join(" · ")}</small>
          </button>
        ))}
        {localResults ? <GoalManagedResults goalId={goalId} zh={locale === "zh-CN"} /> : null}
      </section>
      {active && teamSessionId && !teamSnapshot && !teamError ? <p className="personal-object-list-state" role="status">{t("files.checkingTeam")}</p> : null}
      {active && teamSessionId && teamError ? <p className="personal-object-list-state is-error" role="alert">{t("files.teamLoadFailed")} <button type="button" onClick={() => setTeamRefresh(value => value + 1)}>{t("startup.retry")}</button></p> : null}
      {active && teamConfigured && teamSessionId ? <GoalTeamResults sessionId={teamSessionId} zh={locale === "zh-CN"} refreshKey={JSON.stringify(teamSnapshot?.deliveries ?? [])} /> : null}
    </>
  );
}

function ManagerConversationTray({
  agentLabel,
  messages,
  onClose,
  onDraftTask,
  onReviewGoalDraft,
  onSuggestReply,
  onOpenConversation,
  onInterruptTurn,
  onSteerTurn,
  onCancelPreparation,
  title,
}: {
  agentLabel?: string;
  messages: Array<Extract<WorkspaceTimelineItem, { kind: "message" }>['message']>;
  onClose?: () => void;
  onDraftTask?: (text: string) => void;
  onReviewGoalDraft?: (draft: GoalDraft, edit?: boolean, draftId?: string) => Promise<void>;
  onSuggestReply?: (text: string) => void;
  onOpenConversation: () => void;
  onInterruptTurn?: (turnId: string) => Promise<void>;
  onSteerTurn?: (turnId: string, text: string, ingressId: string) => Promise<void>;
  onCancelPreparation?: () => void;
  title?: string;
}) {
  const { t } = useWorkspaceI18n();
  const latestUserIndex = messages.reduce((latest, message, index) => message.role === "user" ? index : latest, 0);
  const latestExchange = messages.slice(Math.max(0, latestUserIndex));
  const visibleMessages = latestExchange.slice(-3);
  const latestAssistantMessage = visibleMessages.filter((item) => item.role === "assistant" && !item.pending).at(-1);

  useEffect(() => {
    if (!onClose) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <aside aria-label={t("conversation.receipt")} className="personal-manager-conversation-tray">
      <header>
        <span>
          <Bot size={16} />
          <strong>{title ?? t("conversation.title")}</strong>
          <small>{messages.at(-1)?.pending ? t("conversation.replying") : t("common.recently")}</small>
        </span>
        <div className="personal-manager-conversation-actions">
          {onDraftTask && latestAssistantMessage ? (
            <button
              className="personal-manager-conversation-btn"
              onClick={() => onDraftTask(latestAssistantMessage.text)}
              title={t("conversation.convertHint")}
              type="button"
            >
              <ListPlus size={13} />
              <span>{t("conversation.toTask")}</span>
            </button>
          ) : null}
          <button className="personal-manager-conversation-link" onClick={onOpenConversation} type="button">{t("conversation.full")}</button>
          {onClose ? (
            <button
              aria-label={t("conversation.close")}
              className="personal-manager-conversation-close"
              onClick={onClose}
              title={t("conversation.close")}
              type="button"
            >
              <X size={14} />
            </button>
          ) : null}
        </div>
      </header>
      <div aria-live="polite" className="personal-manager-conversation-messages">
        {visibleMessages.map((message) => (
          <article className={`is-${message.role}`} key={message.id}>
            <strong>{message.role === "user" ? t("common.you") : message.agentLabel ?? agentLabel ?? t("header.manager")}</strong>
            <div className="personal-manager-conversation-bubble">
              {message.role === "user" ? <p>{message.text}</p> : <MarkdownText text={message.text} />}
              {message.role === "assistant" ? <MessageActivity message={message} onInterruptTurn={onInterruptTurn} onSteerTurn={onSteerTurn} onCancelPreparation={onCancelPreparation} /> : null}
              {message.role === "assistant" && !message.pending && message.goalDraft ? <GoalDraftCard draftId={`${message.sourceSessionId ?? ""}:${message.id}`} draft={message.goalDraft} onReview={onReviewGoalDraft} onSuggest={onSuggestReply}/> : null}
              <CollaborationCard request={message.collaboration} />
              <ReturnDeliveryStatus delivery={message.returnDelivery} />
            </div>
          </article>
        ))}
      </div>
    </aside>
  );
}

function SessionRecordHeader({ onClose, onOpenDetails, run }: {
  onClose: () => void;
  onOpenDetails: () => void;
  run: WorkspaceRun;
}) {
  const { t } = useWorkspaceI18n();
  return (
    <section aria-label={t("session.record")} className="personal-session-record">
      <header>
        <span><Bot size={17} />{t("session.record")}</span>
        <button aria-label={t("session.closeRecord")} onClick={onClose} type="button"><X size={15} /></button>
      </header>
      <div>
        <strong>{run.title}</strong>
      </div>
      <dl>
        <div><dt>Agent</dt><dd>{run.agentLabel}</dd></div>
        <div><dt>{t("common.status")}</dt><dd>{localizedSessionStatus(run.sessionStatus ?? run.status, t)}</dd></div>
        <div><dt>Session</dt><dd title={run.sessionId}>{run.sessionId}</dd></div>
      </dl>
      <button className="personal-secondary-action" onClick={onOpenDetails} type="button">{t("session.details")}</button>
    </section>
  );
}

function defaultTimeline(model: WorkspaceModel, selectedGoalId: string | null, t: WorkspaceTranslate): WorkspaceTimelineItem[] {  const items: WorkspaceTimelineItem[] = [];
  if (selectedGoalId === null) {
    model.userTodos.slice(0, 4).forEach((attention) => items.push({
      attention: { ...attention, goalTitle: attention.goalTitle ?? goalTitleFor(model, attention.goalId) },
      id: `attention:${attention.todoId}`,
      kind: "attention",
    }));
    model.goals.filter((goal) => workspaceHomeLaneForGoal(goal) === "running").slice(0, 4).forEach((goal) => items.push({
      id: `run:${goal.goalId}`,
      kind: "run",
      run: {
        agentId: goal.agentId,
        agentLabel: goal.agentLabel ?? goal.agentId,
        completedSteps: goal.doneTodoCount ?? goal.agentTodos.filter((todo) => todo.done).length,
        goalId: goal.goalId,
        goalTitle: goal.title,
        latestActivity: goal.agentSentence,
        runId: `goal:${goal.goalId}`,
        status: goalWorkKind(goal) === "executing" ? "running" : "failed",
        title: goal.nextSentence,
        totalSteps: Math.max(
          (goal.doneTodoCount ?? 0) + goal.agentTodos.filter((todo) => !todo.done).length,
          1,
        ),
      },
    }));
    return items;
  }
  const goal = model.goals.find((candidate) => candidate.goalId === selectedGoalId);
  if (!goal) return items;
  if (goal.needsYou) {
    const currentAttention = model.userTodos.find((item) => item.goalId === goal.goalId);
    items.push({
      attention: currentAttention ? { ...currentAttention, goalTitle: goal.title } : {
        blocking: goal.needsYouBlocking ?? false,
        goalId: goal.goalId,
        goalTitle: goal.title,
        text: goal.needsYou,
        todoId: `${goal.goalId}:attention`,
      },
      id: `attention:${goal.goalId}`,
      kind: "attention",
    });
  }
  if (goalHasExecutionSummary(goal)) items.push({
    id: `run:${goal.goalId}`,
    kind: "run",
    run: {
      agentId: goal.agentId,
      agentLabel: goal.agentLabel ?? goal.agentId,
      completedSteps: goal.doneTodoCount ?? goal.agentTodos.filter((todo) => todo.done).length,
      goalId: goal.goalId,
      goalTitle: goal.title,
      latestActivity: goal.agentSentence,
      runId: `goal:${goal.goalId}`,
      status: goalWorkKind(goal) === "executing" ? "running" : goal.state === "需修复" ? "failed" : goal.state === "已安排" ? "queued" : "waiting",
      title: goal.nextSentence,
      totalSteps: Math.max(
        (goal.doneTodoCount ?? 0) + goal.agentTodos.filter((todo) => !todo.done).length,
        1,
      ),
    },
  });
  goal.agentTodos.filter((todo) => todo.taskClass === "continuous_monitor").forEach((todo) => {
    const monitorRun = model.timeline?.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
      item.kind === "run"
      && item.run.goalId === goal.goalId
      && item.run.todoId === todo.todoId
      && Boolean(item.run.sessionId));
    items.push({
    id: `schedule:${goal.goalId}:${todo.todoId}`,
    kind: "schedule",
    schedule: {
      ...monitorScheduleReadback(todo),
      executionHistory: monitorRun ? [{
        label: monitorRun.run.latestActivity || monitorRun.run.title,
        runId: monitorRun.run.runId,
        status: monitorRun.run.status === "waiting" || monitorRun.run.status === "queued" ? "running" : monitorRun.run.status,
        timestamp: goal.latestActivity || t("common.recently"),
      }] : [],
      goalId: goal.goalId,
      label: todo.text,
      scheduleId: todo.todoId,
      scheduleKind: "monitor",
      sessionId: monitorRun?.run.sessionId,
      status: todo.done || todo.status === "paused" ? "paused" : "active",
      target: todo.targetKey || todo.text,
    },
    });
  });
  const heartbeatProposal = model.timeline?.find((item): item is Extract<WorkspaceTimelineItem, { kind: "proposal" }> =>
    item.kind === "proposal" && item.proposal.actionKind === "heartbeat.bind" && item.proposal.goalId === goal.goalId);
  if (heartbeatProposal) {
    const field = (key: string) => heartbeatProposal.proposal.fields.find((item) => item.key === key)?.value;
    items.push({
      id: `schedule:${goal.goalId}:heartbeat`,
      kind: "schedule",
      schedule: {
        agentId: goal.agentId,
        executionHistory: [],
        goalId: goal.goalId,
        label: `${t("schedule.heartbeat")} · ${goal.title}`,
        nextRunAt: t("drawer.schedulePending"),
        notificationRule: t("drawer.scheduleDefaultNotification"),
        schedule: field("cadence") ?? t("schedule.summary"),
        scheduleId: `${goal.goalId}:heartbeat`,
        scheduleKind: "heartbeat",
        status: heartbeatProposal.proposal.status === "applied" ? "active" : "draft",
        stopCondition: field("stop_condition") ?? t("drawer.scheduleDefaultStop"),
        timezone: field("timezone") ?? "Asia/Shanghai",
      },
    });
  }
  return items;
}

function proposalStatus(status: TypedActionProposal["status"]): WorkspaceActionPreview["status"] {
  if (status === "preview_ready") return "ready";
  if (status === "cancelled") return "draft";
  if (status === "failed") return "error";
  return status;
}

function proposalFields(parameters: Record<string, unknown>, t: WorkspaceTranslate) {
  const fieldLabels: Record<string, string> = {
    agent_id: t("proposal.field.agentId"),
    cadence: t("proposal.field.cadence"),
    completion_criteria: t("proposal.field.completionCriteria"),
    execution_boundary: t("proposal.field.executionBoundary"),
    goal_id: t("proposal.field.goalId"),
    heartbeat: t("proposal.field.heartbeat"),
    initial_todos: t("proposal.field.initialTodos"),
    objective: t("proposal.field.objective"),
    operation: t("proposal.field.operation"),
    permission: t("proposal.field.permission"),
    reason: t("proposal.field.reason"),
    stop_condition: t("proposal.field.stopCondition"),
    target: t("proposal.field.target"),
    timezone: t("proposal.field.timezone"),
    title: t("proposal.field.title"),
    workspace_ref: t("proposal.field.workspace"),
  };
  const priority = ["title", "objective", "completion_criteria", "execution_boundary", "permission", "agent_id", "workspace_ref", "initial_todos", "heartbeat", "stop_condition", "goal_id"];
  return Object.entries(parameters)
    .sort(([left], [right]) => {
      const leftIndex = priority.indexOf(left);
      const rightIndex = priority.indexOf(right);
      return (leftIndex < 0 ? priority.length : leftIndex) - (rightIndex < 0 ? priority.length : rightIndex);
    })
    .slice(0, 10)
    .map(([key, value]) => ({
    key,
    label: fieldLabels[key] ?? key.replaceAll("_", " "),
    value: key === "workspace_ref"
      ? value === "current"
        ? t("proposal.workspace.current")
        : t("proposal.workspace.named", { workspace: String(value ?? "current") })
      : Array.isArray(value) ? value.join(" · ") : typeof value === "object" && value !== null
      ? JSON.stringify(value)
      : String(value ?? "—"),
    }));
}

function operationProposalFields(
  proposal: TypedActionProposal,
  reviewPlan: ReturnType<typeof compileActionReviewPlan>,
  t: WorkspaceTranslate,
) {
  const frame = reviewPlan.operationFrame;
  const projectedFields = frame?.content.fields.map((field, index) => ({
    key: `projection:${index}`,
    label: field.label,
    value: field.value,
  })).slice(0, 8) ?? [];
  return [
    {
      key: "operation_state",
      label: t("proposal.field.operationState"),
      value: frame?.kind === "pending" && frame.executionState
        ? t(`proposal.operationState.${frame.executionState}`)
        : frame?.kind === "inactive" ? t(`actionReview.${frame.reason}`)
        : frame?.kind === "result" && frame.resultKind === "unknown"
        ? t("proposal.operationState.submission_unknown")
        : frame?.kind === "result" && frame.resultKind === "cancelled"
        ? t("proposal.primary.operationCancelled")
        : frame?.kind === "confirmation" && !frame.confirmationDeliveryVerified
        ? t("proposal.primary.operationDeliveryPending")
        : frame?.lifecycleState ?? proposal.status,
    },
    ...(frame?.kind === "result" && frame.resultKind !== "cancelled" ? [{
      key: "result_delivery",
      label: t("proposal.field.resultDelivery"),
      value: frame.resultDeliveryVerified
        ? t("proposal.resultDelivery.verified")
        : t("proposal.resultDelivery.pending"),
    }] : []),
    ...projectedFields,
    ...(frame ? [{
      key: "warning",
      label: t("proposal.field.confirmationBoundary"),
      value: frame.content.warning,
    }] : []),
    ...(frame ? [{
      key: "expires_at",
      label: t("proposal.field.expiresAt"),
      value: frame.expiresAt,
    }] : []),
  ].slice(0, 10);
}

type GoalLifecycleOperation = "stop" | "resume" | "delete";

type GoalLifecycleProjection = {
  goalId: string;
  next: "active" | "stopped";
  optimisticApplied: boolean;
  previous: "active" | "stopped";
};

function lifecycleOperationFor(proposal: TypedActionProposal): GoalLifecycleOperation | undefined {
  if (proposal.action_kind !== "goal.lifecycle") return undefined;
  const operation = proposal.normalized_parameters.operation;
  return operation === "stop" || operation === "resume" || operation === "delete"
    ? operation
    : undefined;
}

function workspaceCandidatesFromGate(gate: Record<string, unknown> | null | undefined) {
  const candidates = Array.isArray(gate?.candidates) ? gate.candidates : [];
  return candidates.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object") return [];
    const item = candidate as Record<string, unknown>;
    return typeof item.workspace_ref === "string" && typeof item.label === "string"
      ? [{ label: item.label, workspaceRef: item.workspace_ref }]
      : [];
  });
}

function workspaceProposal(proposal: TypedActionProposal, t: WorkspaceTranslate): WorkspaceActionPreview {
  const lifecycleOperation = lifecycleOperationFor(proposal);
  const reviewPlan = compileActionReviewPlan(proposal, Date.now());
  const title = typeof proposal.normalized_parameters.title === "string"
    ? proposal.normalized_parameters.title
    : typeof proposal.normalized_parameters.goal_id === "string"
      ? proposal.normalized_parameters.goal_id
      : "";
  const workspaceCandidates = workspaceCandidatesFromGate(proposal.gate);
  const target = typeof proposal.normalized_parameters.target === "string"
    ? proposal.normalized_parameters.target
    : "";
  const operationFrame = reviewPlan.operationFrame;
  const decision = reviewPlan.decisionFrame?.decision;
  const operationTitle = operationFrame?.content.title ?? proposal.summary;
  const localizedSummary = proposal.action_kind === "operation.execute"
    ? operationTitle
    : proposal.action_kind === "team.plan"
    ? proposal.status === "applied"
      ? teamPlanAppliedLine(teamPlanAppliedOutcome(proposal.receipt), t)
      : t("proposal.summary.teamPlan", {
      goal: teamPlanGoalId(proposal.normalized_parameters),
      count: teamPlanLaneCount(proposal.normalized_parameters),
    })
    : proposal.action_kind === "goal.create"
    ? t("proposal.summary.goalCreate", { title })
    : proposal.action_kind === "heartbeat.bind"
      ? t("proposal.summary.heartbeat")
      : proposal.action_kind === "monitor.create"
        ? t("proposal.summary.monitor", { target })
        : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "stop"
          ? t("proposal.summary.lifecycleStop", { title })
          : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "delete"
            ? t("proposal.summary.lifecycleDelete", { title })
            : proposal.action_kind === "goal.lifecycle"
              ? t("proposal.summary.lifecycleResume", { title })
        : proposal.summary;
  return {
    actionKind: proposal.action_kind,
    reviewPlan,
    fields: proposal.action_kind === "operation.execute"
      ? operationProposalFields(proposal, reviewPlan, t)
      : proposal.action_kind === "team.plan"
      ? teamPlanFields(proposal.normalized_parameters, t)
      : decision ? []
      : proposalFields(proposal.normalized_parameters, t),
    goalId: typeof proposal.normalized_parameters.goal_id === "string" ? proposal.normalized_parameters.goal_id : undefined,
    impact: reviewPlan.retryOriginal ? t(`actionReview.${reviewPlan.reason}`) : proposal.action_kind === "operation.execute"
      ? operationFrame?.kind === "inactive" ? t(`actionReview.${operationFrame.reason}`)
        : operationFrame?.kind === "pending" && operationFrame.executionState
        ? t(operationFrame.executionState === "consumed_outcome_pending"
          ? "proposal.impact.operationConsumed" : operationFrame.executionState === "managed_turn_pending"
          ? "proposal.impact.operationManagedPending" : operationFrame.executionState === "managed_turn_started"
          ? "proposal.impact.operationManagedStarted" : "proposal.impact.operationAuthorized")
        : operationFrame?.kind === "result" && operationFrame.resultKind === "unknown"
        ? t("proposal.impact.operationUnknown")
        : operationFrame?.kind === "result"
        ? t(operationFrame.resultKind === "cancelled" ? "proposal.impact.operationCancelled" : "proposal.impact.operationResult")
        : operationFrame?.kind === "confirmation" && !operationFrame.confirmationDeliveryVerified
        ? t("proposal.impact.operationDeliveryPending") : t("proposal.impact.operation")
      : proposal.action_kind === "team.plan"
      ? proposal.status === "applied" ? t("proposal.teamPlan.assignedHint") : t("proposal.impact.teamPlan")
      : decision ? proposal.status === "applied" ? "" : t(`proposal.impact.gate.${decision}`)
      : proposal.action_kind === "goal.create"
      ? t("proposal.impact.goalCreate")
      : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "stop"
        ? t("proposal.impact.lifecycleStop")
        : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "delete"
          ? t("proposal.impact.lifecycleDelete")
        : proposal.action_kind === "goal.lifecycle"
          ? t("proposal.impact.lifecycleResume")
      : proposal.permission_classification === "protected"
      ? t("proposal.impact.protected")
      : "",
    previewId: proposal.proposal_id,
    lifecycleOperation,
    gate: proposal.gate ? {
      kind: String(proposal.gate.kind ?? "protected_action"),
      nextAction: typeof proposal.gate.next_action === "string" ? proposal.gate.next_action : undefined,
      summary: String(proposal.gate.summary ?? t("proposal.gate.default")),
    } : undefined,
    sourceRequest: proposal.status === "gated" && proposal.action_kind === "goal.create" ? {
      actionKind: proposal.action_kind,
      context: proposal.context,
      idempotencyKey: `${proposal.proposal_id}-workspace`,
      normalizedParameters: proposal.normalized_parameters,
      summary: proposal.summary,
    } : undefined,
    workspaceCandidates,
    primaryLabel: reviewPlan.retryOriginal ? t("drawer.retryOriginal") : proposal.action_kind === "operation.execute"
      ? operationFrame?.kind === "inactive" ? t(`actionReview.${operationFrame.reason}`)
        : operationFrame?.kind === "pending" && operationFrame.executionState
        ? t(`proposal.operationState.${operationFrame.executionState}`)
        : operationFrame?.kind === "result" && operationFrame.resultKind === "unknown"
        ? t("proposal.operationState.submission_unknown")
        : operationFrame?.kind === "result"
        ? operationFrame.resultKind === "cancelled"
          ? t("proposal.primary.operationCancelled") : operationFrame.resultDeliveryVerified
          ? t("proposal.primary.operationResultVerified")
          : t("proposal.primary.operationResultPending")
        : operationFrame?.kind === "confirmation" && operationFrame.confirmationDeliveryVerified
        ? t("proposal.primary.operationGroup") : t("proposal.primary.operationDeliveryPending")
      : proposal.action_kind === "team.plan" ? t(proposal.status === "applied" ? "proposal.teamPlan.viewResult" : "proposal.primary.teamPlan")
      : decision ? t(`proposal.primary.gate.${decision}`)
      : proposal.action_kind === "goal.create" ? t("proposal.primary.goalCreate")
      : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "stop"
        ? t("proposal.primary.lifecycleStop")
        : proposal.action_kind === "goal.lifecycle" && lifecycleOperation === "delete"
          ? t("proposal.primary.lifecycleDelete")
        : proposal.action_kind === "goal.lifecycle"
          ? t("proposal.primary.lifecycleResume")
      : proposal.action_kind === "todo.create" && proposal.normalized_parameters.start_execution === true
        ? t("proposal.primary.todoStart")
        : t("proposal.primary.apply"),
    status: reviewPlan.retryOriginal || (operationFrame?.kind === "result" && operationFrame.resultKind === "unknown")
      ? "error" : proposal.status === "applied"
      && proposal.action_kind !== "operation.execute"
      && reviewPlan.interaction !== "completed"
      ? "error"
      : proposalStatus(proposal.status),
    teamPlanOutcome: proposal.action_kind === "team.plan" ? teamPlanAppliedOutcome(proposal.receipt) ?? undefined : undefined,
    teamPlanAssignments: proposal.action_kind === "team.plan" ? teamPlanAssignments(proposal.receipt, proposal.normalized_parameters) : undefined,
    teamPlanTodoIds: proposal.action_kind === "team.plan" ? teamPlanTodoIds(proposal.receipt) : undefined,
    teamPlanGapLanes: proposal.action_kind === "team.plan"
      ? teamPlanReceiptGapLanes(proposal.receipt, proposal.normalized_parameters)
      : undefined,
    title: localizedSummary,
    updatedAt: proposal.updated_at,
    createdAt: proposal.created_at,
  };
}

const acceptedImageTypes = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const maxImageAttachmentBytes = 5 * 1024 * 1024;
const maxImageAttachmentCount = 4;
// Project refs are alphanumeric, so this value can never name a workspace.
const stewardScopeValue = "@steward";
const maxImageAttachmentTotalBytes = 12 * 1024 * 1024;

function readImageAttachment(file: File, t: WorkspaceTranslate): Promise<WorkspaceImageAttachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(t("composer.imageReadError", { name: file.name })));
    reader.onload = () => resolve({
      dataUrl: String(reader.result ?? ""),
      id: crypto.randomUUID(),
      mimeType: file.type,
      name: file.name,
      size: file.size,
    });
    reader.readAsDataURL(file);
  });
}

export function PersonalWorkspacePage({
  conversationQueuesFollowUps = false,
  conversationSupportsSteering = false,
  conversationSessionId,
  conversationHistoryState,
  agents = [{ agentId: "codex", available: true, capability: "代码与项目执行", label: "Codex" }],
  callbacks = {},
  goalArchiveLoadState = { error: null, phase: "ready" },
  selectedView: controlledView,
  managerChannelBinding,
  managerRuntime,
  model,
  readOnly = false,
  typedActionsRevision = 0,
  selectedAgentId: controlledAgentId,
  selectedGoalId: controlledGoalId,
  statusSourceControl,
  serviceNotice,
  workspaceConversations,
}: {
  workspaceConversations?: WorkspaceConversationDirectory;
  /** The bound Session's mode queues a message sent while its Turn runs. */
  conversationQueuesFollowUps?: boolean;
  /** The bound managed executor offers native exact-turn steering. */
  conversationSupportsSteering?: boolean;
  conversationSessionId?: string;
  conversationHistoryState?: ConversationHistoryStatus;
  agents?: WorkspaceAgentOption[];
  callbacks?: PersonalWorkspaceCallbacks;
  goalArchiveLoadState?: WorkspaceGoalArchiveLoadState;
  selectedView?: WorkspaceGoalTab;
  managerChannelBinding?: ManagerChannelBinding | null;
  managerRuntime?: ManagerRuntimeSessionReadback | null;
  model: WorkspaceModel;
  ownerLabel?: string;
  readOnly?: boolean;
  // Bumped when typed previews were stored outside this page, so the page
  // re-reads the store instead of waiting for the next mount.
  typedActionsRevision?: number;
  selectedAgentId?: string;
  selectedGoalId?: string | null;
  statusSourceControl?: StatusSourceControl;
  serviceNotice?: ReactNode;
}) {
  const { locale, t } = useWorkspaceI18n();
  const [localGoalId, setLocalGoalId] = useState<string | null>(controlledGoalId ?? null);
  const [localAgentId, setLocalAgentId] = useState(controlledAgentId ?? agents.find((agent) => agent.available)?.agentId ?? "codex");
  const [selection, setSelection] = useState<WorkspaceDrawerSelection | null>(null);
  const [taskInspectorExpanded, setTaskInspectorExpanded] = useState(false);
  const [activeSessionRun, setActiveSessionRun] = useState<WorkspaceRun | null>(null);
  const [proposals, setProposals] = useState<Record<string, WorkspaceActionPreview>>({});
  const [localView, setLocalView] = useState<WorkspaceGoalTab>(controlledGoalId ? "chat" : "overview");
  const selectedGoalTab = controlledView ?? localView;
  const selectedWorkspaceRef = workspaceConversations?.selectedRef ?? null;
  const selectedWorkspaceProject = workspaceConversations?.projects?.find((project) => project.project_ref === selectedWorkspaceRef) ?? null;
  // A workspace scope exists only inside the steward conversation tab.
  const managerChatOpen = selectedWorkspaceRef !== null || selectedGoalTab === "chat";
  const workspaceUnavailable = selectedWorkspaceRef !== null && workspaceConversations?.projects != null && !selectedWorkspaceProject;
  const workspaceTitle = selectedWorkspaceRef ? selectedWorkspaceProject?.title ?? t("workspace.unknownTitle") : null;
  function setSelectedGoalTab(view: WorkspaceGoalTab) {
    setLocalView(view);
    callbacks.onSelectView?.(view);
  }
  const [managerConversationReceiptVisible, setManagerConversationReceiptVisible] = useState(false);
  const [goalConversationReceiptVisible, setGoalConversationReceiptVisible] = useState(false);
  const [actionDraft, setActionDraft] = useState<WorkspaceActionDraft | null>(null);
  const [loopxMode, setLoopxMode] = useState<LoopXModeSnapshot | null>(null);
  const [loopxDelivery, setLoopxDelivery] = useState<"queue" | "inbox" | "steer">("queue");
  const [lifecycleBusyGoalIds, setLifecycleBusyGoalIds] = useState<ReadonlySet<string>>(() => new Set());
  const [quickCompletingTodoIds, setQuickCompletingTodoIds] = useState<ReadonlySet<string>>(() => new Set());
  const [refreshState, setRefreshState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [historyRefreshRevision, setHistoryRefreshRevision] = useState(0);
  const [sessionProposalIds, setSessionProposalIds] = useState<string[]>([]);
  const [managerChannelProposalIds, setManagerChannelProposalIds] = useState<string[]>([]);
  // Cards this page created from the Manager channel. A card created from a
  // Goal conversation stays in that Goal's timeline and never joins Manager Chat.
  const [managerSessionProposalIds, setManagerSessionProposalIds] = useState<string[]>([]);
  const restoredProposalIdsRef = useRef(new Set<string>());
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<WorkspaceTheme>(readWorkspaceTheme);
  const [goalContexts, setGoalContexts] = useState<Record<string, GoalRepositoryContext>>({});
  const [larkConnections, setLarkConnections] = useState<LarkGoalConnection[]>([]);
  const digestInitRef = useRef(false);
  const digestSinceRef = useRef(Number.NaN);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const channelScrollRef = useRef<HTMLDivElement>(null);
  const followConversationRef = useRef(true);
  const [showLatestMessage, setShowLatestMessage] = useState(false);
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const lifecyclePendingGoalIdsRef = useRef(new Set<string>());
  const quickCompletingTodoIdsRef = useRef(new Set<string>());
  const [digest, setDigest] = useState<{ done: number; failed: number } | null>(null);
  const selectedGoalId = controlledGoalId === undefined ? localGoalId : controlledGoalId;
  const actionReadback = useTypedActionReadback(readOnly, selectedGoalId);
  const selectedAgentId = controlledAgentId ?? localAgentId;
  const conversationKey = selectedWorkspaceRef
    ? conversationContextKey({ kind: "project", projectRef: selectedWorkspaceRef })
    : selectedGoalId ?? "manager";
  const composerDraftKey = `${conversationKey}:${selectedAgentId}`;
  const { composer, setComposer, restoreFailedSubmission,
    sending, setSending, steering, setSteering, actionFeedback, setActionFeedback,
    imageAttachments, setImageAttachments, imageAttachmentError, setImageAttachmentError,
    loopxMessageReceipt, setLoopxMessageReceipt, isCurrentConversation,
  } = useConversationInputState(composerDraftKey);
  useEffect(() => {
    const input = composerRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }, [composer, selectedGoalId, managerChatOpen]);
  async function reviewGoalDraft(draft: GoalDraft, edit = false, draftId = "") {
    // Source message + reviewed contents survive retry without merging distinct requests.
    if (!edit && !draft.question && draft.completion_criteria.trim()) {
      const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
        JSON.stringify([draftId, draft, selectedAgentId, locale])));
      const operationId = Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, "0")).join("");
      await createPreview(goalCreateRequest({ objective: draft.objective,
        completion: draft.completion_criteria, boundary: draft.execution_boundary,
        permission: "read_only", agentId: selectedAgentId, contextGoalId: null, operationId }, t));
      return;
    }
    setActionDraft({ kind: "goal", goalId: null, goalTitle: "", agentId: selectedAgentId,
      text: draft.objective, completionCriteria: draft.completion_criteria,
      executionBoundary: draft.execution_boundary, permission: "read_only" });
  }
  function suggestReply(text: string) {
    setComposer(composer ? `${composer}\n${text}` : text);
    composerRef.current?.focus();
  }
  // The steward prompt set is owned by the client model; the quick-prompt row
  // reuses it so one affordance answers "what now / what blocks / what is proven".
  function stewardPromptText(id: string) {
    return stewardPrompts.find((item) => item.id === id)?.prompt ?? "";
  }
  const workspaceGoals = useMemo(() => model.goals.map((goal) => {
    const repository = goalContexts[goal.goalId];
    return repository ? {
      ...goal,
      repository: {
        branch: repository.branch,
        identity: repository.identity,
        label: repository.label,
        readOnly: true as const,
      },
    } : goal;
  }), [goalContexts, model.goals]);
  const managerNeedsYouCount = useMemo(
    () => workspaceGoals.filter((goal) => workspaceHomeLaneForGoal(goal) === "needs_you").length,
    [workspaceGoals],
  );
  const managerBlockingCount = useMemo(
    () => workspaceGoals.filter((goal) =>
      workspaceHomeLaneForGoal(goal) === "needs_you"
      && (goal.needsYouBlocking || goal.state === "等你")
    ).length,
    [workspaceGoals],
  );
  const selectedGoal = workspaceGoals.find((goal) => goal.goalId === selectedGoalId) ?? null;
  function openSettings(target: Extract<WorkspaceDrawerSelection, { kind: "settings" }>) {
    settingsReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setMobileSidebarOpen(false);
    setSelection(target);
  }
  function closeSettings() {
    setSelection(null);
    window.requestAnimationFrame(() => {
      const target = settingsReturnFocusRef.current;
      if (target?.isConnected && target.getClientRects().length) target.focus({ preventScroll: true });
      else document.querySelector<HTMLElement>(".personal-mobile-menu")?.focus({ preventScroll: true });
    });
  }
  const settingsOpen = selection?.kind === "settings";
  const managerProjectionId = selectedGoalId;
  const items = useMemo(() => {
    const heartbeatSchedules: WorkspaceTimelineItem[] = Object.values(proposals)
      .filter((proposal) => proposal.actionKind === "heartbeat.bind" && proposal.goalId && proposal.status === "applied")
      .map((proposal) => ({
        id: `schedule:${proposal.goalId}:heartbeat`,
        kind: "schedule" as const,
        schedule: {
          agentId: selectedAgentId,
          executionHistory: [],
          goalId: proposal.goalId!,
          label: proposal.title,
          nextRunAt: t("drawer.schedulePending"),
          notificationRule: t("drawer.scheduleDefaultNotification"),
          schedule: proposal.fields.find((field) => field.key === "cadence")?.value ?? t("schedule.summary"),
          scheduleId: `${proposal.goalId}:heartbeat`,
          scheduleKind: "heartbeat" as const,
          status: proposal.status === "applied" ? "active" as const : "draft" as const,
          stopCondition: proposal.fields.find((field) => field.key === "stop_condition")?.value ?? t("drawer.scheduleDefaultStop"),
          timezone: proposal.fields.find((field) => field.key === "timezone")?.value ?? "Asia/Shanghai",
        },
      }));
    const merged: WorkspaceTimelineItem[] = [
      ...defaultTimeline(model, managerProjectionId, t),
      ...(model.timeline ?? []),
      ...heartbeatSchedules,
      ...dedupeProposals(Object.values(proposals))
        .filter((proposal) => proposal.actionKind !== "heartbeat.bind" || proposal.status !== "applied")
        .map((proposal) => ({ id: `proposal:${proposal.previewId}`, kind: "proposal" as const, proposal })),
    ];
    const projected = [...new Map(merged.map((item) => [item.id, item])).values()]
      .filter((item) => item.kind !== "proposal"
        || !["stale", "error"].includes(item.proposal.status)
        || item.proposal.reviewPlan?.retryOriginal === true
        || (item.proposal.reviewPlan?.operationFrame?.kind === "result"
          && item.proposal.reviewPlan.operationFrame.resultKind === "unknown")
        || sessionProposalIds.includes(item.proposal.previewId));
    return projected.filter((item) => {
      if (!selectedGoalId) return true;
      if (item.kind === "message") return true;
      if (item.kind === "proposal") return !item.proposal.goalId || item.proposal.goalId === selectedGoalId;
      if (item.kind === "attention") return item.attention.goalId === selectedGoalId;
      if (item.kind === "run") return item.run.goalId === selectedGoalId;
      if (item.kind === "schedule") return item.schedule.goalId === selectedGoalId;
      return item.output.goalId === selectedGoalId;
    });
  }, [managerProjectionId, model, proposals, selectedAgentId, selectedGoalId, sessionProposalIds, t]);
  const visibleTimelineItems = useMemo(() => {
    if (!activeSessionRun) return items;
    return items.filter((item) => {
      if (item.kind === "message") return true;
      if (item.kind === "run") return item.run.runId === activeSessionRun.runId;
      if (item.kind === "output") return item.output.runId === activeSessionRun.runId;
      return false;
    });
  }, [activeSessionRun, items]);
  useEffect(() => {
    if (!activeSessionRun) return;
    const latestRun = items.find((item) => item.kind === "run" && item.run.runId === activeSessionRun.runId);
    if (!latestRun || latestRun.kind !== "run") return;
    const currentSignature = JSON.stringify({
      completedSteps: activeSessionRun.completedSteps,
      latestActivity: activeSessionRun.latestActivity,
      messages: activeSessionRun.sessionMessages,
      sessionStatus: activeSessionRun.sessionStatus,
      status: activeSessionRun.status,
      totalSteps: activeSessionRun.totalSteps,
    });
    const latestSignature = JSON.stringify({
      completedSteps: latestRun.run.completedSteps,
      latestActivity: latestRun.run.latestActivity,
      messages: latestRun.run.sessionMessages,
      sessionStatus: latestRun.run.sessionStatus,
      status: latestRun.run.status,
      totalSteps: latestRun.run.totalSteps,
    });
    if (currentSignature !== latestSignature) setActiveSessionRun(latestRun.run);
  }, [activeSessionRun, items]);
  const managerMessages = useMemo(
    () => items.flatMap((item) => item.kind === "message" ? [item.message] : []),
    [items],
  );
  const goalMessages = useMemo(
    () => selectedGoal ? items.flatMap((item) => item.kind === "message" ? [item.message] : []) : [],
    [items, selectedGoal],
  );
  useEffect(() => {
    if (selectedGoal || managerChatOpen) return;
    if (managerMessages.some((message) => message.pending)) {
      setManagerConversationReceiptVisible(true);
    }
  }, [managerChatOpen, managerMessages, selectedGoal]);
  useEffect(() => {
    if (!selectedGoal || selectedGoalTab === "chat") return;
    if (goalMessages.some((message) => message.pending)) {
      setGoalConversationReceiptVisible(true);
    }
  }, [goalMessages, selectedGoal, selectedGoalTab]);
  // One composer for both conversations. Running managed Codex work receives
  // exact-turn instructions; attached hosts and LoopX mode keep their queues.
  const loopxDeliveryOpen = Boolean(conversationSessionId && loopxMode?.session_id === conversationSessionId
    && loopxMode?.enabled && loopxMode.active_turn_id);
  const runningMessage = managerMessages.find((message) => message.pending && Boolean(message.sourceTurnId)
    && message.sourceSessionId === conversationSessionId);
  const conversationTurnRunning = !loopxDeliveryOpen && !conversationQueuesFollowUps && Boolean(runningMessage);
  const steeringTurnId = conversationTurnRunning && conversationSupportsSteering && !readOnly
    && callbacks.onSteerConversationTurn ? runningMessage?.sourceTurnId : undefined;
  const composerBlocked = steering || (!steeringTurnId && (sending || conversationTurnRunning));
  const quickPromptBlocked = steering || sending || conversationTurnRunning;
  const managerChatItems = useMemo(
    () => items.filter((item) => item.kind === "message"
      || (item.kind === "proposal" && (managerSessionProposalIds.includes(item.proposal.previewId)
        || managerChannelProposalIds.includes(item.proposal.previewId)))),
    [items, managerSessionProposalIds, managerChannelProposalIds],
  );
  const conversationOpen = selectedGoal ? selectedGoalTab === "chat" : managerChatOpen;
  const conversationMessages = selectedGoal ? goalMessages : managerMessages;
  const latestMessage = conversationMessages.at(-1);
  const latestMessageTextLength = latestMessage?.text.length ?? 0;
  function scrollToLatestMessage() {
    followConversationRef.current = true;
    setShowLatestMessage(false);
    const scroller = channelScrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }
  useEffect(() => {
    followConversationRef.current = true;
    setShowLatestMessage(false);
  }, [conversationKey, selectedAgentId, conversationOpen]);
  useEffect(() => {
    if (!conversationOpen || !followConversationRef.current) return;
    const frame = window.requestAnimationFrame(scrollToLatestMessage);
    return () => window.cancelAnimationFrame(frame);
  }, [conversationOpen, conversationKey, selectedAgentId, conversationMessages.length,
    latestMessageTextLength, latestMessage?.pending, latestMessage?.activity?.length, latestMessage?.steps]);
  const drawerSelection = useMemo<Exclude<WorkspaceDrawerSelection, { kind: "settings" }> | null>(() => {
    if (selection?.kind === "settings") return null;
    if (selection?.kind === "proposal") {
      const current = proposals[selection.item.previewId];
      return current ? { kind: "proposal", item: current } : selection;
    }
    if (selection?.kind === "attention") return { kind: "attention", item: refreshAttention(selection.item, model.attentionHistory ?? model.userTodos) };
    if (selection?.kind === "goal") {
      const currentGoal = workspaceGoals.find((goal) => goal.goalId === selection.item.goalId);
      return currentGoal ? { item: currentGoal, kind: "goal" } : selection;
    }
    if (selection?.kind === "todo") {
      const goal = workspaceGoals.find((goal) => goal.goalId === selection.item.goalId);
      const todo = goal?.agentTodos.find((item) => item.todoId === selection.item.todoId);
      return goal && todo ? { kind: "todo", item: {
        ...todo, goalId: goal.goalId, goalTitle: goal.title,
        ownerLabel: goal.agentLanes?.find((lane) => lane.agentId === todo.claimedBy)?.label ?? todo.claimedBy,
      } } : selection;
    }
    if (selection?.kind !== "run") return selection;
    const currentRun = items.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> =>
      item.kind === "run" && item.run.runId === selection.item.runId
    );
    return currentRun ? { item: currentRun.run, kind: "run" } : selection;
  }, [items, selection, proposals, workspaceGoals, model.attentionHistory, model.userTodos]);

  useEffect(() => {
    if (readOnly) {
      setGoalContexts({});
      setLarkConnections([]);
      return;
    }
    let cancelled = false;
    // Goal repositories and Lark connections are independent optional sources:
    // a missing lark-cli must not also hide every Goal's repository context.
    void fetchGoalContexts()
      .then((contexts) => {
        if (!cancelled) setGoalContexts(Object.fromEntries(contexts.map((row) => [row.goal_id, row.repository])));
      })
      .catch(() => {
        // Local context is optional; the Goal workspace stays usable without it.
      });
    void fetchLarkConnections()
      .then((connections) => {
        if (!cancelled) setLarkConnections(connections);
      })
      .catch(() => {
        // Lark is optional; Settings reports why it is unavailable.
      });
    return () => { cancelled = true; };
  }, [readOnly]);

  useEffect(() => {
    if (!mobileSidebarOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileSidebarOpen(false);
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileSidebarOpen]);

  useEffect(() => {
    if (selectedGoalId || !items.length) return;
    if (!digestInitRef.current) {
      digestInitRef.current = true;
      try {
        digestSinceRef.current = Date.parse(window.localStorage.getItem("loopx-pw-last-visit") ?? "");
        window.localStorage.setItem("loopx-pw-last-visit", new Date().toISOString());
      } catch {
        digestSinceRef.current = Number.NaN;
      }
    }
    const since = digestSinceRef.current;
    const runs = items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run").map((item) => item.run);
    const isFresh = (time?: string) => {
      const parsed = Date.parse(time ?? "");
      return !Number.isNaN(since) && !Number.isNaN(parsed) && parsed > since;
    };
    const nextDigest = {
      done: runs.filter((run) => run.status === "completed" && isFresh(run.latestActivity)).length,
      failed: runs.filter((run) => (run.status === "failed" || run.status === "interrupted") && isFresh(run.latestActivity)).length,
    };
    setDigest((current) => current?.done === nextDigest.done && current.failed === nextDigest.failed ? current : nextDigest);
  }, [items, selectedGoalId]);

  useEffect(() => {
    if (readOnly) {
      setProposals({});
      restoredProposalIdsRef.current.clear();
      setManagerChannelProposalIds([]);
      return;
    }
    if (!actionReadback.data) return;
    const knownGoals = new Set(workspaceGoals.map(goal => goal.goalId));
    const stored = actionReadback.data.filter(proposal => selectedGoalId || proposal.context.kind === "manager"
      || (proposal.action_kind === "operation.execute" && knownGoals.has(String(proposal.normalized_parameters.goal_id))));
    const restoreable = stored
      .filter((proposal) => ["preview_ready", "gated", "deferred", "applying"].includes(proposal.status)
        || compileActionReviewPlan(proposal).retryOriginal === true
        || (proposal.action_kind === "team.plan" && proposal.status === "applied")
        // Terminal operations replace cached gated cards, including in an
        // already-open drawer. They never authorize local execution.
        || proposal.action_kind === "operation.execute")
      .map((proposal) => workspaceProposal(proposal, t));
    const restored = Object.fromEntries(restoreable.map((proposal) => [proposal.previewId, proposal]));
    const previousIds = restoredProposalIdsRef.current;
    restoredProposalIdsRef.current = new Set(Object.keys(restored));
    setProposals((current) => ({...Object.fromEntries(Object.entries(current).filter(([id]) => !previousIds.has(id))), ...restored}));
    // The manager conversation shows the cards this channel offered: a team
    // plan the steward proposed from here is confirmed here, instead of the
    // owner hunting for the Goal whose workspace happens to hold the card.
    // A Goal-scoped fetch belongs to that Goal's workspace, not to this
    // conversation, so it is left alone.
    if (!selectedGoalId) setManagerChannelProposalIds(stored.filter(proposal => proposal.context.kind === "manager"
      || proposal.action_kind === "operation.execute")
      .map(proposal => proposal.proposal_id));
  }, [readOnly, selectedGoalId, actionReadback.data, actionReadback.dataUpdatedAt, t, workspaceGoals]);

  const homeOperations = Object.values(proposals).filter(proposal => proposal.actionKind === "operation.execute"
    && proposal.reviewPlan?.operationFrame?.kind === "confirmation" && proposal.status === "gated");
  function rememberSessionProposal(previewId: string, channelGoalId: string | null) {
    setSessionProposalIds((current) => current.includes(previewId) ? current : [...current, previewId]);
    if (channelGoalId === null) {
      setManagerSessionProposalIds((current) => current.includes(previewId) ? current : [...current, previewId]);
    }
  }

  async function createPreview(
    request: WorkspaceActionPreviewRequest,
    options: { select?: boolean } = {},
  ) {
    // The card belongs to the conversation on screen when the request started
    // (this render's selectedGoalId), even if its answer lands after the owner
    // moved elsewhere.
    if (readOnly) throw new Error(t("source.readOnlyWriteError"));
    let local: WorkspaceActionPreview;
    try {
      local = callbacks.onPreviewAction
        ? await callbacks.onPreviewAction(request)
        : workspaceProposal(await previewTypedAction(request), t);
    } catch (error) {
      if (!(error instanceof ChatApiError) || error.payload.error_code !== "action_preview_gate") throw error;
      const rawGate = error.payload.gate && typeof error.payload.gate === "object"
        ? error.payload.gate as Record<string, unknown>
        : {};
      const workspaceCandidates = workspaceCandidatesFromGate(rawGate);
      const gateKind = String(rawGate.kind ?? "workspace_selection_required");
      const requiresAgentBinding = gateKind === "agent_binding_required"
        || gateKind === "agent_identity_selection_required";
      local = {
        actionKind: request.actionKind,
        fields: workspaceCandidates.map((candidate) => ({
          key: `workspace_ref:${candidate.workspaceRef}`,
          label: candidate.label,
          value: candidate.workspaceRef,
        })),
        gate: {
          kind: gateKind,
          nextAction: typeof rawGate.next_action === "string" ? rawGate.next_action : undefined,
          summary: String(rawGate.summary ?? t("proposal.workspaceGate.defaultSummary")),
        },
        impact: requiresAgentBinding
          ? t("proposal.workspaceGate.agentImpact")
          : t("proposal.workspaceGate.selectionImpact"),
        previewId: `workspace-choice-${Date.now().toString(36)}`,
        sourceRequest: request,
        status: "gated",
        title: requiresAgentBinding ? t("proposal.workspaceGate.agentTitle") : t("proposal.workspaceGate.selectionTitle"),
        workspaceCandidates,
      };
    }
    rememberSessionProposal(local.previewId, selectedGoalId);
    setProposals((current) => ({ ...current, [local.previewId]: local }));
    if (options.select !== false && isCurrentConversation()) setSelection({ item: local, kind: "proposal" });
    return local;
  }

  function requestGoalCreate() {
    setActionDraft({ kind: "goal", goalId: null, goalTitle: "", agentId: selectedAgentId });
  }

  async function requestGoalLifecycle(goal: WorkspaceGoal, operation: GoalLifecycleOperation) {
    setMobileSidebarOpen(false);
    const reasonByOperation: Record<GoalLifecycleOperation, string> = {
      delete: "Deleted from the owner workspace",
      resume: "Resumed from the owner workspace",
      stop: "Stopped from the owner workspace",
    };
    const summaryByOperation: Record<GoalLifecycleOperation, string> = {
      delete: t("proposal.summary.lifecycleDelete", { title: goal.title }),
      resume: t("proposal.summary.lifecycleResume", { title: goal.title }),
      stop: t("proposal.summary.lifecycleStop", { title: goal.title }),
    };
    let stopProjection: GoalLifecycleProjection | null = null;
    let projectionOwnedByApply = false;
    try {
      if (operation === "stop") {
        if (lifecyclePendingGoalIdsRef.current.has(goal.goalId)) return;
        lifecyclePendingGoalIdsRef.current.add(goal.goalId);
        setLifecycleBusyGoalIds(new Set(lifecyclePendingGoalIdsRef.current));
        setSelection(null);
        stopProjection = {
          goalId: goal.goalId,
          next: "stopped",
          optimisticApplied: true,
          previous: goal.activationState,
        };
        setActionFeedback(t("feedback.applying", { title: summaryByOperation.stop }));
        callbacks.onGoalActivationStateChange?.(goal.goalId, "stopped");
      }
      if (callbacks.onExecuteGoalLifecycle) {
        if (operation === "delete") {
          throw new Error("The selected status source does not authorize Goal deletion.");
        }
        const result = await callbacks.onExecuteGoalLifecycle({
          goalId: goal.goalId,
          operation,
          reason: reasonByOperation[operation],
        });
        if (!result.projectionVerified) {
          throw new Error("Goal lifecycle projection did not verify.");
        }
        projectionOwnedByApply = true;
        callbacks.onGoalActivationStateChange?.(goal.goalId, result.activationState);
        setActionFeedback(t("feedback.completed", { title: summaryByOperation[operation] }));
        if (operation === "stop") selectGoal(null);
        await reconcileStatus([goal.goalId]);
        return;
      }
      const proposal = await createPreview({
        actionKind: "goal.lifecycle",
        context: { kind: "goal_directory", goal_id: goal.goalId },
        idempotencyKey: `workspace-goal-${operation}-${goal.goalId}-${Date.now().toString(36)}`,
        normalizedParameters: {
          goal_id: goal.goalId,
          operation,
          reason: reasonByOperation[operation],
        },
        summary: summaryByOperation[operation],
      }, { select: operation !== "stop" });
      if (proposal.goalId !== goal.goalId || proposal.lifecycleOperation !== operation) {
        setSelection(null);
        throw new Error(t("actionReview.targetChanged"));
      }
      if (operation === "stop") {
        if (proposal.reviewPlan?.interaction === "direct") {
          projectionOwnedByApply = true;
          await applyProposal(proposal, {
            lifecycleProjection: stopProjection ?? undefined,
            presentation: "feedback",
          });
        } else {
          if (stopProjection) {
            callbacks.onGoalActivationStateChange?.(stopProjection.goalId, stopProjection.previous);
          }
          setActionFeedback(proposal.gate
            ? t("feedback.gateRequired", { summary: proposal.gate.summary })
            : t("feedback.notCompleted", { status: proposal.status }));
          setSelection({ item: proposal, kind: "proposal" });
        }
      }
    } catch (error) {
      if (stopProjection && !projectionOwnedByApply) {
        callbacks.onGoalActivationStateChange?.(stopProjection.goalId, stopProjection.previous);
      }
      setActionFeedback(t("feedback.executionFailed", {
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      if (operation === "stop") {
        lifecyclePendingGoalIdsRef.current.delete(goal.goalId);
        setLifecycleBusyGoalIds(new Set(lifecyclePendingGoalIdsRef.current));
      }
    }
  }

  function prepareScheduleDraft(kind: "heartbeat" | "monitor", goalId: string | null) {
    setActionDraft({ kind, goalId, goalTitle: workspaceGoals.find((goal) => goal.goalId === goalId)?.title ?? "", agentId: selectedAgentId });
    setSelection(null);
  }

  async function requestQuickTodoCompletion(todo: WorkspaceTodo) {
    if (quickCompletingTodoIdsRef.current.has(todo.todoId)) return;
    quickCompletingTodoIdsRef.current.add(todo.todoId);
    setQuickCompletingTodoIds(new Set(quickCompletingTodoIdsRef.current));
    setActionFeedback(t("feedback.preparingPreview", { title: todo.text }));
    try {
      await createPreview({
        actionKind: "todo.update",
        context: { goal_id: todo.goalId, kind: "todo", todo_id: todo.todoId },
        idempotencyKey: `workspace-todo-${todo.todoId}-complete-${Date.now().toString(36)}`,
        normalizedParameters: { goal_id: todo.goalId, operation: "complete", todo_id: todo.todoId },
        summary: t("tasks.markComplete", { name: todo.text }),
      });
      setActionFeedback(null);
    } catch (error) {
      setActionFeedback(t("feedback.previewFailed", {
        error: error instanceof Error ? error.message : String(error),
      }));
    } finally {
      quickCompletingTodoIdsRef.current.delete(todo.todoId);
      setQuickCompletingTodoIds(new Set(quickCompletingTodoIdsRef.current));
    }
  }

  /**
   * Reconcile the projection after an applied action. The touched Goal is the
   * only one whose snapshot is dropped; a peer keeps the snapshot it already
   * had, so one Goal's pause does not send the rest of the workspace back to
   * its loading lane.
   */
  function reconcileStatus(invalidateGoalIds?: string[]) {
    const reconcile = callbacks.onReconcileStatus;
    const request = reconcile
      ? reconcile({ invalidateGoalIds })
      : callbacks.onRefresh?.();
    return Promise.resolve(request).catch(() => {
      setActionFeedback(t("feedback.goalRefreshFailed"));
    });
  }

  async function applyProposal(
    proposal: WorkspaceActionPreview,
    options: {
      lifecycleProjection?: GoalLifecycleProjection;
      presentation?: "drawer" | "feedback";
    } = {},
  ) {
    // A failed/uncertain assignment retries its original authorized operation.
    // The server still revalidates admission or recovers its immutable receipt.
    const retryTeamAssignment = proposal.actionKind === "team.plan" && proposal.status === "error"
      && ["apply_failed", "readback_unverified"].includes(proposal.reviewPlan?.reason ?? "");
    if (proposal.reviewPlan && !proposal.reviewPlan.canApply && !retryTeamAssignment) return;
    const showDrawer = options.presentation !== "feedback";
    const inferredLifecycleChange = proposal.actionKind === "goal.lifecycle"
      && proposal.goalId
      && (proposal.lifecycleOperation === "stop" || proposal.lifecycleOperation === "resume")
      ? {
          goalId: proposal.goalId,
          next: proposal.lifecycleOperation === "stop" ? "stopped" as const : "active" as const,
          optimisticApplied: false,
          previous: model.goals.find((goal) => goal.goalId === proposal.goalId)?.activationState
            ?? (proposal.lifecycleOperation === "stop" ? "active" as const : "stopped" as const),
        }
      : null;
    const lifecycleChange = options.lifecycleProjection ?? inferredLifecycleChange;
    setActionFeedback(t("feedback.applying", { title: proposal.title }));
    const applying = { ...proposal, reviewPlan: proposal.reviewPlan ? { ...proposal.reviewPlan, interaction: "pending" as const, reason: "apply_pending" as const, canApply: false as const } : undefined, status: "applying" as const };
    setProposals((current) => ({ ...current, [proposal.previewId]: applying }));
    if (showDrawer) setSelection({ item: applying, kind: "proposal" });
    if (lifecycleChange && !lifecycleChange.optimisticApplied) {
      callbacks.onGoalActivationStateChange?.(lifecycleChange.goalId, lifecycleChange.next);
    }
    try {
      if (callbacks.onApplyProposal) {
        await callbacks.onApplyProposal(proposal);
        const applied = { ...proposal, status: "applied" as const };
        setProposals((current) => ({ ...current, [proposal.previewId]: applied }));
        if (showDrawer) setSelection({ item: applied, kind: "proposal" });
        setActionFeedback(t("feedback.completed", { title: proposal.title }));
        if (proposal.actionKind === "goal.lifecycle") {
          if (proposal.lifecycleOperation === "stop" || proposal.lifecycleOperation === "delete") {
            selectGoal(null);
          }
          if (proposal.lifecycleOperation === "delete" && proposal.goalId) {
            callbacks.onGoalDeleted?.(proposal.goalId);
          }
          void reconcileStatus(proposal.goalId ? [proposal.goalId] : undefined);
        }
        if (proposal.actionKind === "todo.update") {
          void reconcileStatus(proposal.goalId ? [proposal.goalId] : undefined);
        }
        return;
      }
      const result = await applyTypedAction(proposal.previewId);
      if (result.proposal.proposal_id !== proposal.previewId
        || result.proposal.action_kind !== proposal.actionKind
        || (proposal.actionKind === "goal.lifecycle" && (
          result.proposal.normalized_parameters.goal_id !== proposal.goalId
          || lifecycleOperationFor(result.proposal) !== proposal.lifecycleOperation
        ))) {
        throw new ChatApiError(t("actionReview.targetChanged"), { error_code: "action_response_mismatch" });
      }
      await actionReadback.acceptProposal(result.proposal);
      const applied = workspaceProposal(result.proposal, t);
      setProposals((current) => ({ ...current, [proposal.previewId]: applied }));
      if (showDrawer) setSelection({ item: applied, kind: "proposal" });
      if (applied.reviewPlan?.interaction !== "completed") {
        if (lifecycleChange) {
          callbacks.onGoalActivationStateChange?.(lifecycleChange.goalId, lifecycleChange.previous);
        }
        setSelection({ item: applied, kind: "proposal" });
        setActionFeedback(
          result.proposal.status === "stale"
            ? t("feedback.stale")
            : t(`actionReview.${applied.reviewPlan!.reason}`),
        );
        return;
      }
      setActionFeedback(t("feedback.completed", { title: applied.title }));
      // Keep the success receipt visible for reviewed actions. Direct actions
      // surface the same result through the persistent feedback receipt.
      if (applied.actionKind === "todo.create") {
        await callbacks.onRefresh?.();
      }
      if (applied.actionKind === "goal.lifecycle" && (applied.lifecycleOperation === "stop" || applied.lifecycleOperation === "delete")) {
        selectGoal(null);
      }
      if (applied.actionKind === "goal.lifecycle" && applied.lifecycleOperation === "delete" && applied.goalId) {
        callbacks.onGoalDeleted?.(applied.goalId);
      }
      if (applied.actionKind === "goal.lifecycle" || applied.actionKind === "gate.resolve" || applied.actionKind === "todo.update") {
        void reconcileStatus(applied.goalId ? [applied.goalId] : undefined);
      }
    } catch (error) {
      if (lifecycleChange) {
        callbacks.onGoalActivationStateChange?.(lifecycleChange.goalId, lifecycleChange.previous);
      }
      if (error instanceof ChatApiError && error.payload.error_code === "protected_action") {
        const rawGate = error.payload.gate;
        const gate = rawGate && typeof rawGate === "object" ? rawGate as Record<string, unknown> : {};
        const gated = {
          ...proposal,
          reviewPlan: proposal.reviewPlan ? { ...proposal.reviewPlan, interaction: "gated" as const, reason: "authority_gate" as const, canApply: false as const } : undefined,
          gate: {
            kind: String(gate.kind ?? "protected_action"),
            nextAction: typeof gate.next_action === "string" ? gate.next_action : undefined,
            summary: String(gate.summary ?? error.message),
          },
          status: "gated" as const,
        };
        setProposals((current) => ({ ...current, [proposal.previewId]: gated }));
        // A newly discovered authority gate always deserves review, including
        // when the action started on the direct path.
        setSelection({ item: gated, kind: "proposal" });
        setActionFeedback(t("feedback.gateRequired", { summary: gated.gate.summary }));
        if (proposal.actionKind === "goal.create" && proposal.goalId) {
          callbacks.onRefresh?.();
          selectGoal(proposal.goalId);
        }
        return;
      }
      const stale = error instanceof ChatApiError && isStaleActionFailure(error.payload);
      const readbackMismatch = error instanceof ChatApiError && error.payload.error_code === "action_response_mismatch";
      const failed = {
        ...proposal,
        reviewPlan: proposal.reviewPlan ? { ...proposal.reviewPlan, interaction: stale ? "refresh" as const : "repair" as const, reason: readbackMismatch ? "readback_unverified" as const : stale ? "stale_proposal" as const : "apply_failed" as const, canApply: false as const } : undefined,
        errorMessage: error instanceof Error ? error.message : String(error),
        status: (stale ? "stale" : "error") as "stale" | "error",
      };
      setProposals((current) => ({ ...current, [proposal.previewId]: failed }));
      setSelection({ item: failed, kind: "proposal" });
      setActionFeedback(t("feedback.executionFailed", { error: failed.errorMessage }));
    }
  }

  function openGoalConversation() {
    setSelectedGoalTab("chat");
    setActiveSessionRun(null);
    setGoalConversationReceiptVisible(false);
    window.requestAnimationFrame(() => {
      const replies = channelScrollRef.current?.querySelectorAll<HTMLElement>(".personal-message.is-assistant");
      replies?.item(replies.length - 1)?.scrollIntoView({ block: "start" });
    });
  }

  const drawerCallbacks: PersonalWorkspaceCallbacks = {
    ...callbacks,
    onOpenRunSession: async (run) => {
      if (run.goalId !== selectedGoalId) selectGoal(run.goalId, "chat");
      else setSelectedGoalTab("chat");
      await callbacks.onOpenRunSession?.(run);
      setActiveSessionRun(run);
      setSelection(null);
    },
    onOpenGoal: (goalId) => {
      selectGoal(goalId);
      void reconcileStatus([goalId]);
    },
    onOpenGoalView: (tab) => {
      if (tab === "chat") openGoalConversation();
      else setSelectedGoalTab(tab);
      setSelection(null);
    },
    onOpenOutput: (output) => {
      if (output.goalId !== selectedGoalId) selectGoal(output.goalId, "files");
      else setSelectedGoalTab("files");
      callbacks.onOpenOutput?.(output);
    },
    onApplyProposal: applyProposal,
    onCancelProposal: async (proposal) => {
      setSelection(null);
      setProposals((current) => {
        const next = { ...current };
        delete next[proposal.previewId];
        return next;
      });
      try {
        callbacks.onCancelProposal?.(proposal);
        if (!callbacks.onCancelProposal) {
          await actionReadback.acceptProposal(await cancelTypedAction(proposal.previewId));
        }
      } catch (error) {
        setProposals((current) => ({ ...current, [proposal.previewId]: proposal }));
        setActionFeedback(t("feedback.cancelFailed", { error: error instanceof Error ? error.message : String(error) }));
      }
    },
    onTransitionProposal: async (proposal, transition) => {
      const result = await transitionTypedAction(proposal.previewId, transition);
      await actionReadback.acceptProposal(result, transition === "regenerate" ? proposal.previewId : undefined);
      const transitioned = workspaceProposal(result, t);
      const managerOwned = managerSessionProposalIds.includes(proposal.previewId)
        || managerChannelProposalIds.includes(proposal.previewId);
      rememberSessionProposal(transitioned.previewId, managerOwned ? null : proposal.goalId ?? selectedGoalId);
      setProposals((current) => {
        const next = { ...current };
        if (transition === "regenerate") delete next[proposal.previewId];
        next[transitioned.previewId] = transitioned;
        return next;
      });
      setSelection({ item: transitioned, kind: "proposal" });
    },
    onSelectWorkspaceCandidate: async (proposal, workspaceRef) => {
      if (!proposal.sourceRequest) return;
      setProposals((current) => {
        const next = { ...current };
        delete next[proposal.previewId];
        return next;
      });
      await createPreview({
        ...proposal.sourceRequest,
        idempotencyKey: `${proposal.sourceRequest.idempotencyKey}-${workspaceRef}`,
        normalizedParameters: { ...proposal.sourceRequest.normalizedParameters, workspace_ref: workspaceRef },
      });
    },
    onPreviewAction: createPreview,
    onRequestScheduleConfig: (kind, goalId) => prepareScheduleDraft(kind, goalId),
    onOpenNotificationSettings: (goalId) => openSettings({ goalId, kind: "settings", tab: "lark" }),
    onFetchNotificationTargets: () => fetchGoalChannelTargets(),
    onSetupGoalChannel: (options) => setupGoalChannel(options),
    onToggleGoalAutoNotify: (options) => configureGoalChannelAutoNotify(options),
    onUpdateSchedule: async (schedule, operation) => {
      const timestamp = Date.now().toString(36);
      const heartbeat = schedule.scheduleKind === "heartbeat";
      await createPreview({
        actionKind: heartbeat ? "heartbeat.bind" : "monitor.update",
        context: { kind: "schedule", goal_id: schedule.goalId },
        idempotencyKey: `workspace-monitor-${schedule.scheduleId}-${operation}-${timestamp}`,
        normalizedParameters: {
          agent_id: schedule.agentId ?? selectedAgentId,
          ...(!heartbeat && operation === "run_now" ? { endpoint_id: selectedAgentId } : {}),
          ...(operation === "edit" ? { cadence: "2h", ...(heartbeat ? { timezone: schedule.timezone ?? "Asia/Shanghai" } : {}) } : {}),
          goal_id: schedule.goalId,
          operation,
          ...(!heartbeat && operation === "run_now" && schedule.sessionId ? { session_id: schedule.sessionId } : {}),
          ...(!heartbeat ? { todo_id: schedule.scheduleId } : {}),
        },
        summary: operation === "pause" ? `暂停自动运行：${schedule.label}`
          : operation === "resume" ? `恢复自动运行：${schedule.label}`
            : operation === "run_now" ? `立即运行：${schedule.label}`
              : operation === "stop" ? `停止自动运行：${schedule.label}`
                : `编辑自动运行生命周期：${schedule.label}`,
      });
    },
  };
  const effectiveDrawerCallbacks: PersonalWorkspaceCallbacks = readOnly ? {
    onOpenGoal: drawerCallbacks.onOpenGoal,
    onOpenGoalView: drawerCallbacks.onOpenGoalView,
    onOpenOutput: drawerCallbacks.onOpenOutput,
  } : drawerCallbacks;

  function selectGoal(goalId: string | null, view: WorkspaceGoalTab = goalId ? "tasks" : "overview") {
    setLocalGoalId(goalId);
    setManagerConversationReceiptVisible(false);
    setGoalConversationReceiptVisible(false);
    setActiveSessionRun(null);
    setSelection(null);
    setLocalView(view);
    setMobileSidebarOpen(false);
    callbacks.onSelectGoal?.(goalId, view);
  }

  function selectAgent(agentId: string) {
    setLocalAgentId(agentId);
    callbacks.onSelectAgent?.(agentId);
  }

  function updateTheme(next: WorkspaceTheme) {
    setTheme(next);
    writeWorkspaceTheme(next);
  }

  async function sendMessage(messageOverride?: string) {
    const pendingImages = messageOverride ? [] : imageAttachments;
    const message = (messageOverride ?? composer).trim() || (pendingImages.length ? t("composer.imageAnalysisPrompt") : "");
    if (!message || composerBlocked || conversationHistoryState?.sendBlocked) return;
    const previousSteering = readSteeringRequest(composerDraftKey);
    const retry = previousSteering && previousSteering.sessionId === conversationSessionId && previousSteering.text === message
      ? previousSteering : undefined;
    if ((retry || steeringTurnId) && conversationSessionId && callbacks.onSteerConversationTurn) {
      if (pendingImages.length) {
        setImageAttachmentError(locale === "zh-CN" ? "本轮追加指令暂不支持图片，图片和草稿已保留。" : "This turn accepts text instructions only. Images and draft retained.");
        return;
      }
      const request = retry ?? { sessionId: conversationSessionId, turnId: steeringTurnId!, text: message, id: crypto.randomUUID() };
      retainSteeringRequest(composerDraftKey, request);
      setSteering(true);
      setActionFeedback(null);
      setImageAttachmentError(null);
      try {
        await callbacks.onSteerConversationTurn(conversationKey, request.turnId, message, request.id);
        retireSteeringRequest(composerDraftKey, request.id);
        if (!messageOverride) setComposer("", composer);
        setActionFeedback(locale === "zh-CN" ? "执行器已接收本轮追加指令。" : "The executor accepted instructions for this turn.");
      } catch (error) {
        // Unknown delivery retries the original Turn even after it completes.
        // A confirmed non-delivery may use a new ingress after recovery.
        if (error instanceof ChatApiError && error.payload.delivery_state === "not_delivered") {
          retireSteeringRequest(composerDraftKey, request.id);
        }
        setActionFeedback(error instanceof Error ? error.message : t("feedback.sendGenericError"));
      } finally { setSteering(false); }
      return;
    }
    if (sending) return;
    followConversationRef.current = true;
    setShowLatestMessage(false);
    if (loopxMode?.session_id === conversationSessionId && loopxMode?.enabled && loopxMode.active_turn_id && conversationSessionId) {
      if (pendingImages.length) {
        setImageAttachmentError(locale === "zh-CN" ? "运行中的消息投递暂不支持图片，请暂停后发送。" : "Pause execution before sending images.");
        return;
      }
      setSending(true);
      try {
        const receipt = await sendLoopXMessage(conversationSessionId, message, loopxDelivery);
        if (!messageOverride) setComposer("", composer);
        setLoopxMessageReceipt(locale === "zh-CN" ? `${loopxDelivery === "queue" ? "已排队，等待后续回合" : loopxDelivery === "inbox" ? "已进入收件箱" : "已提交纠偏"} · ${receipt.status}` : `${loopxDelivery}: ${receipt.status}`);
      } catch (error) {setImageAttachmentError(error instanceof Error ? error.message : String(error));}
      finally {setSending(false);}
      return;
    }
    if (conversationTurnRunning) return;
    if (!messageOverride) {
      setComposer("");
      setImageAttachments([]);
    }
    setImageAttachmentError(null);
    setSending(true);
    try {
      if (!selectedGoalId) setManagerConversationReceiptVisible(true);
      else if (selectedGoalTab !== "chat") setGoalConversationReceiptVisible(true);
      const previews = await callbacks.onSendMessage?.(message, selectedAgentId, conversationKey, pendingImages.length ? pendingImages : undefined);
      if (previews?.candidates?.length) {
        const drafted = await Promise.allSettled(previews.candidates.map((request) => createPreview(request, { select: false })));
        if (drafted.some((result) => result.status === "rejected")) setActionFeedback(t("feedback.proposalDraftFailed"));
      }
      // The decision is created last so it keeps the drawer selection.
      if (previews?.decision) await createPreview(previews.decision);
    } catch (error) {
      if (!messageOverride) {
        restoreFailedSubmission(message, pendingImages);
      }
      const errorMessage = error instanceof Error ? error.message : t("feedback.sendGenericError");
      setActionFeedback(t("feedback.sendFailed", { error: errorMessage }));
    } finally {
      setSending(false);
    }
  }

  const selectedAgentLabel = agents.find((agent) => agent.agentId === selectedAgentId)?.label ?? selectedAgentId;


  async function selectImages(files: FileList | readonly File[] | null) {
    if (!files?.length) return;
    const available = maxImageAttachmentCount - imageAttachments.length;
    const selected = Array.from(files).slice(0, Math.max(0, available));
    const invalid = selected.find((file) => !acceptedImageTypes.has(file.type));
    const oversized = selected.find((file) => file.size > maxImageAttachmentBytes);
    if (available <= 0) {
      setImageAttachmentError(t("composer.imageCountError", { count: maxImageAttachmentCount }));
      return;
    }
    if (invalid) {
      setImageAttachmentError(t("composer.imageTypeError"));
      return;
    }
    if (oversized) {
      setImageAttachmentError(t("composer.imageSizeError", { size: maxImageAttachmentBytes / 1024 / 1024 }));
      return;
    }
    if ([...imageAttachments, ...selected].reduce((total, image) => total + image.size, 0) > maxImageAttachmentTotalBytes) {
      setImageAttachmentError(t("composer.imageTotalSizeError", { size: maxImageAttachmentTotalBytes / 1024 / 1024 }));
      return;
    }
    try {
      const loaded = await Promise.all(selected.map((file) => readImageAttachment(file, t)));
      setImageAttachments((current) => [...current, ...loaded].slice(0, maxImageAttachmentCount));
      setImageAttachmentError(files.length > selected.length ? t("composer.imageCountError", { count: maxImageAttachmentCount }) : null);
    } catch (error) {
      setImageAttachmentError(error instanceof Error ? error.message : t("composer.imageReadGenericError"));
    } finally {
      if (imageInputRef.current) imageInputRef.current.value = "";
    }
  }

  function handleComposerPaste(event: ReactClipboardEvent<HTMLTextAreaElement>) {
    const images = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .flatMap((item) => {
        const file = item.getAsFile();
        return file ? [file] : [];
      });
    if (!images.length) return;
    event.preventDefault();
    void selectImages(images);
  }

  async function refreshLarkState() {
    const connections = await fetchLarkConnections();
    setLarkConnections(connections);
  }

  async function refreshSettingsState() {
    await Promise.all([
      refreshLarkState(),
      callbacks.onRefresh?.(),
    ]);
  }

  async function refreshWorkspace() {
    if (!callbacks.onRefresh || refreshState === "loading") return;
    setRefreshState("loading");
    try {
      await callbacks.onRefresh();
      setHistoryRefreshRevision((revision) => revision + 1);
      setRefreshState("done");
    } catch {
      setRefreshState("error");
    }
    window.setTimeout(() => setRefreshState("idle"), 1800);
  }

  const settingsPage = settingsOpen ? (
      <WorkspaceSettingsPage
        callbacks={effectiveDrawerCallbacks}
        focusGoalConnection={Boolean(selection?.kind === "settings" && selection.goalId)}
        goalNotifications={model.goalNotifications ?? []}
        goals={workspaceGoals}
        initialGoalId={selection?.kind === "settings" ? selection.goalId ?? selectedGoalId : selectedGoalId}
        initialTab={selection?.kind === "settings" ? selection.tab ?? (selectedGoalId ? "lark" : "steward") : "steward"}
        onChanged={() => void refreshSettingsState()}
        onClose={closeSettings}
        onThemeChange={updateTheme}
        theme={theme}
      />
  ) : null;

  return (
    <>
    <div hidden={settingsOpen}>
    <WorkspaceShell
      notice={serviceNotice}
      drawer={drawerSelection ? <ContextDrawer agents={agents} attentionHistory={model.attentionHistory ?? model.userTodos} onSelectAttention={(item) => setSelection({ kind: "attention", item })} callbacks={effectiveDrawerCallbacks} goalNotifications={model.goalNotifications ?? []} goals={workspaceGoals} inspectorExpanded={taskInspectorExpanded} larkConnections={readOnly ? [] : larkConnections}
        todoReadbackUnavailable={drawerSelection.kind === "todo" && !workspaceGoals.some((goal) =>
          goal.goalId === drawerSelection.item.goalId && (drawerSelection.item.done
            || goal.agentTodos.some((todo) => todo.todoId === drawerSelection.item.todoId)))}
        proposalReadbackUnavailable={actionReadback.isError || !actionReadback.data
          || (drawerSelection.kind === "proposal" && !actionReadback.data.some(proposal => proposal.proposal_id === drawerSelection.item.previewId))}
        proposalReadbackFetching={actionReadback.isFetching} onRetryProposalReadback={() => void actionReadback.refetch()} onClose={() => {
        if (drawerSelection.kind === "proposal"
          && ["applied", "rejected"].includes(drawerSelection.item.status)
          && !(drawerSelection.item.status === "applied" && ["heartbeat.bind", "team.plan"].includes(drawerSelection.item.actionKind))) {
          setProposals((current) => {
            const next = { ...current };
            delete next[drawerSelection.item.previewId];
            return next;
          });
        }
        setTaskInspectorExpanded(false);
        setSelection(null);
      }} onToggleInspectorSize={() => setTaskInspectorExpanded((current) => !current)} readOnly={readOnly} runs={items.flatMap((item) => item.kind === "run" ? [item.run] : [])} selection={drawerSelection} /> : null}
      drawerMode={drawerSelection?.kind === "todo" ? (taskInspectorExpanded ? "inspector-full" : "inspector") : "panel"}
      drawerOpen={drawerSelection !== null}
      mobileSidebarOpen={mobileSidebarOpen}
      onCloseMobileSidebar={() => setMobileSidebarOpen(false)}
      theme={theme}
      main={(
        <div className="personal-channel">
          <div>
          <ChannelHeader
            agents={agents}
            conversationScope={workspaceConversations && (workspaceConversations.projects?.length || workspaceConversations.readFailed || selectedWorkspaceRef) ? {
              onChange: (value) => {
                setActiveSessionRun(null);
                workspaceConversations.onSelect(value === stewardScopeValue ? null : value);
              },
              options: [
                { label: t("header.manager"), value: stewardScopeValue },
                ...(workspaceConversations.projects ?? []).map((project) => ({ label: project.title, value: project.project_ref })),
                ...(selectedWorkspaceRef && !selectedWorkspaceProject ? [{ disabled: true, label: workspaceTitle!, value: selectedWorkspaceRef }] : []),
                ...(workspaceConversations.readFailed ? [{ disabled: true, label: t("header.scopeWorkspacesUnavailable"), value: "workspaces-unavailable" }] : []),
              ],
              value: selectedWorkspaceRef ?? stewardScopeValue,
            } : null}
            workspaceGrantLabel={selectedWorkspaceRef ? t(workspaceUnavailable ? "workspace.grantRevoked" : selectedWorkspaceProject?.grant === "workspace_write" ? "workspace.grantWrite" : "workspace.grantRead") : null}
            managerChatOpen={managerChatOpen}
            managerChannelBinding={managerChannelBinding}
            managerRuntime={managerRuntime}
            mobileNavigationOpen={mobileSidebarOpen}
            onOpenGoalCapabilities={selectedGoal && !readOnly ? () => openSettings({ goalId: selectedGoal.goalId, kind: "settings", tab: "capabilities" }) : undefined}
            onOpenManagerSettings={!readOnly ? () => openSettings({ kind: "settings", tab: "steward" }) : undefined}
            onRefresh={callbacks.onRefresh ? () => void refreshWorkspace() : undefined}
            onOpenNavigation={() => setMobileSidebarOpen(true)}
            onOpenManagerChat={() => {
              setManagerConversationReceiptVisible(false);
              setSelectedGoalTab("chat");
            }}
            onSelectGoalTab={(tab) => {
              if (tab === "chat") openGoalConversation();
              else setSelectedGoalTab(tab);
            }}
            onSelectAgent={selectAgent}
            onReturnManagerHome={() => {
              if (selectedWorkspaceRef) workspaceConversations?.onSelect(null);
              setSelectedGoalTab("overview");
              setManagerConversationReceiptVisible(false);
              window.requestAnimationFrame(() => channelScrollRef.current?.scrollTo({ behavior: "smooth", top: 0 }));
            }}
            selectedAgentId={selectedAgentId}
            refreshState={refreshState}
            readOnlySourceLabel={readOnly ? statusSourceControl?.activeSource.label : undefined}
            selectedGoal={selectedGoal}
            selectedGoalTab={selectedGoalTab}
          />
          {!readOnly ? <UsageStatisticsNotice onDetails={() => openSettings({ kind: "settings", tab: "machine" })} /> : null}
          </div>
            {selectedGoalId && selectedGoalTab === "chat" && !readOnly && selectedAgentId === "codex" && callbacks.onStartLoopX ? <GoalLoopXMode
              onPrepare={() => callbacks.onPrepareLoopX!(selectedAgentId, selectedGoalId)}
              key={`${selectedGoalId}:${selectedAgentId}`} sessionId={conversationSessionId} onChange={setLoopxMode}
              onExecute={(operation, settings) => callbacks.onStartLoopX?.(operation, selectedAgentId, selectedGoalId, settings)}
            /> : null}
          <div className="personal-channel-scroll" data-active-goal-view={selectedGoal ? selectedGoalTab : undefined} ref={channelScrollRef}
            onScroll={(event) => {
              if (!conversationOpen) return;
              const { scrollHeight, scrollTop, clientHeight } = event.currentTarget;
              const nearBottom = scrollHeight - scrollTop - clientHeight < 64;
              followConversationRef.current = nearBottom;
              setShowLatestMessage(!nearBottom);
            }}>
            {selectedGoalId && selectedGoalTab === "chat" && !readOnly && selectedAgentId === "codex" && conversationSessionId && loopxMode?.settings.execution_config && loopxMode.settings.agent_id ? <GoalTeamResults
              key={`${conversationSessionId}:${loopxMode.settings.agent_id}:${loopxMode.settings.execution_config}`}
              sessionId={conversationSessionId} zh={locale === "zh-CN"}
              refreshKey={JSON.stringify(loopxMode.deliveries)}/>
              : null}
            {!selectedGoal && !managerChatOpen && digest && (digest.done + digest.failed) > 0 ? (
              <section className="personal-digest-card" aria-label={t("digest.away")}>
                <strong>{t("digest.away")}</strong>
                <div className="personal-digest-stats">
                  {digest.done > 0 ? <span><b>{digest.done}</b>{t("digest.completed")}</span> : null}
                  {digest.failed > 0 ? <span><b>{digest.failed}</b>{t("digest.failed")}</span> : null}
                </div>
              </section>
            ) : null}
            {!selectedGoal && !managerChatOpen ? (
              <section className="personal-manager-greeting">
                <span><Bot size={20} /></span>
                <div><small className="personal-brief-date">{t("brief.title")} · {new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", weekday: "short" }).format(new Date())}</small><strong>{t("home.greeting")}</strong><p>{model.goals.some((goal) => goal.activationState === "active" && goal.loadState) ? t("startup.partial") : <>{t("home.waitingCount", { count: managerNeedsYouCount })} {managerBlockingCount > 0 ? t("home.blockingSummary", { count: managerBlockingCount }) : null} {!actionReadback.isError && homeOperations.length ? t("home.operationSummary", {count: homeOperations.length}) : null}</>}</p></div>
              </section>
            ) : null}
            {selectedGoal?.loadState ? (
              <section className="personal-manager-greeting" role="status" data-testid="goal-status-loading">
                <div><strong>{t(selectedGoal.loadState === "error" ? "startup.goalError" : "startup.goalLoading")}</strong>
                <p>{t(selectedGoal.loadError ? `startup.error.${selectedGoal.loadError}` : "startup.independent")}</p>
                {selectedGoal.loadState === "error" ? <button className="min-h-11 rounded-md border px-3 py-2 text-sm" type="button" onClick={() => void callbacks.onRefresh?.("missing")}>{t("startup.retry")}</button> : null}</div>
              </section>
            ) : selectedGoal ? (
              <GoalWorkspacePanels key={`${statusSourceControl?.activeSource.statusUrl ?? "/status.json"}:${selectedGoal.goalId}`}
                activeTab={selectedGoalTab} scrollRef={channelScrollRef} panels={{
                  overview: <GoalOverview active={!settingsOpen && selectedGoalTab === "overview"} goal={selectedGoal} items={items} userTodos={model.userTodos} readOnly={readOnly}
                    onOpenDetails={() => setSelection({ kind: "goal", item: selectedGoal })} onSelect={setSelection} onView={setSelectedGoalTab} />,
                  tasks: (<GoalTasksView
                    historyEnabled={!readOnly}
                    historyRefreshRevision={historyRefreshRevision}
                    goal={selectedGoal}
                    items={items}
                    onDraftTaskFromMessage={readOnly ? undefined : (reply) => {
                      const taskDraft = reply.trim();
                      setActionDraft({ kind: "todo", text: taskDraft, goalId: selectedGoalId, goalTitle: selectedGoal.title, agentId: selectedAgentId });
                    }}
                    onOpenChat={openGoalConversation}
                    onQuickComplete={readOnly ? undefined : requestQuickTodoCompletion}
                    onSelect={setSelection}
                    quickCompletingTodoIds={quickCompletingTodoIds}
                    selectedTodoId={drawerSelection?.kind === "todo" ? drawerSelection.item.todoId : null}
                    userTodos={model.userTodos}
                  />),
                  files: (<GoalOutputsView
                    active={selectedGoalTab === "files"}
                    items={items.filter((item): item is Extract<WorkspaceTimelineItem, { kind: "output" }> => item.kind === "output")}
                    onSelect={setSelection}
                    reportState={model.periodicReports}
                    teamSessionId={!readOnly && selectedAgentId === "codex" ? conversationSessionId : undefined}
                    goalId={selectedGoal.goalId}
                    localResults={!readOnly && selectedGoalTab === "files"}
                  />),
                  chat: (<>
                    {selectedGoal && activeSessionRun?.goalId === selectedGoal.goalId ? (
                      <SessionRecordHeader
                        onClose={() => setActiveSessionRun(null)}
                        onOpenDetails={() => setSelection({ item: activeSessionRun, kind: "run" })}
                        run={activeSessionRun}
                      />
                    ) : null}
                    <ChannelTimeline onReviewGoalDraft={readOnly ? undefined : reviewGoalDraft} onSuggestReply={readOnly ? undefined : suggestReply} items={visibleTimelineItems} onSelect={setSelection} selectedGoal={selectedGoal}
                      onSteerTurn={!readOnly && callbacks.onSteerConversationTurn
                        ? (turnId, text, ingressId) => callbacks.onSteerConversationTurn!(selectedGoal.goalId, turnId, text, ingressId)
                        : undefined}
                      onCancelPreparation={!readOnly && callbacks.onCancelConversationPreparation
                        ? () => callbacks.onCancelConversationPreparation!(selectedGoal.goalId) : undefined}
                      onInterruptTurn={!readOnly && callbacks.onInterruptConversationTurn
                        ? (turnId) => callbacks.onInterruptConversationTurn!(selectedGoal.goalId, turnId)
                        : undefined} />
                  </>),
                }} />
            ) : !managerChatOpen ? (
              <ManagerHomeBoard goals={workspaceGoals} onRetry={() => void callbacks.onRefresh?.("missing")} onSelectGoal={selectGoal} systemHealth={model.systemHealth}
                operations={actionReadback.isError ? [] : homeOperations} onSelectOperation={proposal => setSelection({kind: "proposal", item: proposal})}
                onViewAllOperations={() => setSelectedGoalTab("chat")} />
            ) : (
              <ChannelTimeline emptyState={workspaceTitle ? { title: workspaceTitle, description: t("workspace.empty") } : undefined} onReviewGoalDraft={readOnly ? undefined : reviewGoalDraft} onSuggestReply={readOnly ? undefined : suggestReply}
                items={selectedWorkspaceRef ? managerChatItems.filter((item) => item.kind === "message") : managerChatItems}
                onSelect={setSelection} selectedGoal={null} showManagerTeamResults={!selectedWorkspaceRef}
                onSteerTurn={!readOnly && callbacks.onSteerConversationTurn
                  ? (turnId, text, ingressId) => callbacks.onSteerConversationTurn!(conversationKey, turnId, text, ingressId)
                  : undefined}
                onCancelPreparation={!readOnly && callbacks.onCancelConversationPreparation
                  ? () => callbacks.onCancelConversationPreparation!(conversationKey) : undefined}
                onInterruptTurn={!readOnly && callbacks.onInterruptConversationTurn
                  ? (turnId) => callbacks.onInterruptConversationTurn!(conversationKey, turnId)
                  : undefined}
                onOpenGoalEvidence={(goalId) => { selectGoal(goalId, "chat"); }} />
            )}
          </div>
          <div className="personal-composer-wrap">
            {!readOnly && actionReadback.isError ? <div className="personal-history-notice" role="status" data-testid="personal-action-readback-error">
              <span>{t("proposal.readbackUnavailable")}</span><button type="button" disabled={actionReadback.isFetching}
                onClick={() => void actionReadback.refetch()}>{t("proposal.readbackRetry")}</button>
            </div> : null}
            {workspaceUnavailable ? <div className="personal-history-notice" role="status" data-testid="workspace-scope-unavailable">
              <span>{t("workspace.unavailable")}</span>
            </div> : null}
            {conversationHistoryState && conversationHistoryState.phase !== "ready" ? (
              <div className="personal-history-notice" role="status">
                <div><span>{t(`history.${conversationHistoryState.phase}`)}</span>
                  {conversationHistoryState.sendBlocked && conversationHistoryState.phase !== "loading"
                    ? <span>{t("history.currentSessionRecovering")}</span> : null}</div>
                {conversationHistoryState.phase !== "loading" ? <button type="button"
                  disabled={conversationHistoryState.reading} onClick={conversationHistoryState.retry}>
                  {t(conversationHistoryState.reading ? "history.retrying" : "history.retry")}
                </button> : null}
              </div>
            ) : null}
            {loopxMode?.session_id === conversationSessionId && loopxMode?.enabled && loopxMode.active_turn_id ? <label className="goal-loopx-message-mode">{locale === "zh-CN" ? "消息处理" : "Message delivery"}<select aria-label={locale === "zh-CN" ? "消息处理方式" : "Message delivery mode"} value={loopxDelivery} onChange={event => setLoopxDelivery(event.target.value as typeof loopxDelivery)}><option value="queue">{locale === "zh-CN" ? "下一轮处理" : "Next turn"}</option><option value="inbox">{locale === "zh-CN" ? "放入收件箱" : "Inbox"}</option><option value="steer">{locale === "zh-CN" ? "立即纠偏" : "Steer now"}</option></select><span role="status">{loopxMessageReceipt}</span></label> : null}
            {readOnly ? (
              <div className="personal-read-only-notice"><strong>{t("source.readOnlyNoticeTitle")}</strong><span>{t("source.readOnlyNoticeDescription")}</span></div>
            ) : <>
            {!selectedGoal && !managerChatOpen && managerConversationReceiptVisible && managerMessages.length ? (
              <ManagerConversationTray onReviewGoalDraft={reviewGoalDraft} onSuggestReply={suggestReply}
                onCancelPreparation={!readOnly && callbacks.onCancelConversationPreparation ? () => callbacks.onCancelConversationPreparation!("manager") : undefined}
                onInterruptTurn={!readOnly && callbacks.onInterruptConversationTurn ? (turnId) => callbacks.onInterruptConversationTurn!("manager", turnId) : undefined}
                onSteerTurn={!readOnly && callbacks.onSteerConversationTurn ? (turnId, text, ingressId) => callbacks.onSteerConversationTurn!("manager", turnId, text, ingressId) : undefined}
                messages={managerMessages}
                onClose={() => setManagerConversationReceiptVisible(false)}
                onOpenConversation={() => {
                  setManagerConversationReceiptVisible(false);
                  setSelectedGoalTab("chat");
                }} />
            ) : null}
            {selectedGoal && selectedGoalTab !== "chat" && goalConversationReceiptVisible && goalMessages.length ? (
              <ManagerConversationTray onReviewGoalDraft={reviewGoalDraft} onSuggestReply={suggestReply}
                agentLabel={selectedAgentLabel}
                onCancelPreparation={!readOnly && callbacks.onCancelConversationPreparation ? () => callbacks.onCancelConversationPreparation!(selectedGoal.goalId) : undefined}
                onInterruptTurn={!readOnly && callbacks.onInterruptConversationTurn ? (turnId) => callbacks.onInterruptConversationTurn!(selectedGoal.goalId, turnId) : undefined}
                onSteerTurn={!readOnly && callbacks.onSteerConversationTurn ? (turnId, text, ingressId) => callbacks.onSteerConversationTurn!(selectedGoal.goalId, turnId, text, ingressId) : undefined}
                messages={goalMessages}
                onClose={() => setGoalConversationReceiptVisible(false)}
                onDraftTask={selectedGoalTab === "tasks" ? (reply) => {
                  const taskDraft = reply.trim();
                  setActionDraft({ kind: "todo", text: taskDraft, goalId: selectedGoalId, goalTitle: selectedGoal.title, agentId: selectedAgentId });
                } : undefined}
                onOpenConversation={openGoalConversation}
                title={`${selectedGoal.title} · ${selectedAgentLabel}`}
              />
            ) : null}
            {actionFeedback ? (
              <div className="personal-action-feedback" role="status">
                <span>{actionFeedback}</span>
                <button aria-label={t("common.closeActionReceipt")} onClick={() => setActionFeedback(null)} type="button"><X size={14} /></button>
              </div>
            ) : null}
            {conversationOpen && showLatestMessage ? <button className="personal-conversation-latest" type="button" onClick={scrollToLatestMessage}>
              {locale === "zh-CN" ? "回到最新消息 ↓" : "Latest message ↓"}
            </button> : null}
            {!selectedWorkspaceRef && (!conversationOpen || !conversationMessages.length) ? <details className="personal-composer-tools" key={conversationKey}>
              <summary>{locale === "zh-CN" ? "快捷提问" : "Suggestions"}</summary>
            {selectedGoal ? (
              <div className="personal-quick-prompts">
                <button aria-label={t("composer.nextAction")} disabled={quickPromptBlocked} onClick={() => void sendMessage(t("composer.nextActionPrompt"))} title={t("composer.sendMessageHint")} type="button"><MessageCircleQuestion size={13} /><span>{t("composer.nextAction")}</span></button>
                <button aria-label={t("composer.agentProgress")} disabled={quickPromptBlocked} onClick={() => void sendMessage(t("composer.agentProgressPrompt"))} title={t("composer.sendMessageHint")} type="button"><Send size={13} /><span>{t("composer.agentProgress")}</span></button>
                <button aria-label={t("composer.monitor")} disabled={sending} onClick={() => prepareScheduleDraft("monitor", selectedGoalId)} title={t("composer.sendMessageHint")} type="button"><CalendarClock size={13} /><span>{t("composer.monitor")}</span></button>
                <button aria-label={t("composer.blockers")} disabled={quickPromptBlocked || !stewardPromptText("gate")} onClick={() => void sendMessage(stewardPromptText("gate"))} title={t("composer.sendMessageHint")} type="button"><AlertCircle size={13} /><span>{t("composer.blockers")}</span></button>
                <button aria-label={t("composer.evidence")} disabled={quickPromptBlocked || !stewardPromptText("evidence")} onClick={() => void sendMessage(stewardPromptText("evidence"))} title={t("composer.sendMessageHint")} type="button"><FileText size={13} /><span>{t("composer.evidence")}</span></button>
              </div>
            ) : (
              <div className="personal-quick-prompts">
                <button aria-label={t("composer.globalTasks")} disabled={quickPromptBlocked} onClick={() => void sendMessage(t("composer.globalTasksPrompt"))} title={t("composer.sendMessageHint")} type="button"><MessageCircleQuestion size={13} /><span>{t("composer.globalTasks")}</span></button>
                <button aria-label={t("composer.globalProgress")} disabled={quickPromptBlocked} onClick={() => void sendMessage(t("composer.globalProgressPrompt"))} title={t("composer.sendMessageHint")} type="button"><Send size={13} /><span>{t("composer.globalProgress")}</span></button>
                <button aria-label={t("composer.createGoal")} onClick={requestGoalCreate} title={t("composer.createGoalHint")} type="button"><Plus size={13} /><span>{t("composer.createGoal")}</span></button>
              </div>
            )}
            </details> : null}
            {actionDraft ? <WorkspaceActionForm draft={actionDraft} onClose={() => setActionDraft(null)} onPreview={(request) => createPreview(request)} /> : null}
            {imageAttachments.length ? <div className="personal-composer-images" aria-label={t("composer.imagesPending")}>{imageAttachments.map((attachment) => (
              <figure key={attachment.id}>
                <img alt={attachment.name} src={attachment.dataUrl} />
                <button aria-label={t("composer.sentImageAlt", { name: attachment.name })} onClick={() => setImageAttachments((current) => current.filter((item) => item.id !== attachment.id))} type="button"><X size={13} /></button>
              </figure>
            ))}</div> : null}
            {imageAttachmentError ? <p className="personal-composer-error" role="alert">{imageAttachmentError}</p> : null}
            {conversationTurnRunning ? <p className="personal-composer-status" role="status">{steeringTurnId
              ? (locale === "zh-CN" ? "本轮进行中 · 发消息可调整当前工作" : "Turn in progress · send instructions to adjust this work")
              : t("composer.turnRunning")}</p> : null}
            <div
              className="personal-channel-composer"
              onDragOver={(event) => {
                if ([...event.dataTransfer.items].some((item) => item.kind === "file" && item.type.startsWith("image/"))) {
                  event.preventDefault();
                }
              }}
              onDrop={(event) => {
                const images = [...event.dataTransfer.files].filter((file) => file.type.startsWith("image/"));
                if (!images.length) return;
                event.preventDefault();
                void selectImages(images);
              }}
            >
              <button
                aria-label={t("composer.addImage")}
                className="personal-composer-attach"
                disabled={sending || imageAttachments.length >= maxImageAttachmentCount}
                onClick={() => imageInputRef.current?.click()}
                title={t("composer.attachImageHint")}
                type="button"
              >
                <Paperclip size={17} />
              </button>
              <input accept="image/png,image/jpeg,image/webp,image/gif" aria-label={t("composer.imagePicker")} className="personal-composer-file-input" disabled={sending || imageAttachments.length >= maxImageAttachmentCount} multiple onChange={(event) => void selectImages(event.target.files)} ref={imageInputRef} type="file" />
              <textarea
                aria-label={t("composer.sendMessage")}
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
                onPaste={handleComposerPaste}
                placeholder={sending ? (locale === "zh-CN" ? "可以先写下后续问题…" : "Draft your next message…") : selectedGoal ? t("composer.goalPlaceholder", { goal: selectedGoal.title }) : workspaceTitle ? t("workspace.placeholder", { workspace: workspaceTitle }) : t("composer.managerPlaceholder")}
                ref={composerRef}
                rows={1}
                value={composer}
              />
              <button aria-label={t("composer.send")} disabled={(!composer.trim() && imageAttachments.length === 0) || composerBlocked || workspaceUnavailable || conversationHistoryState?.sendBlocked} onClick={() => void sendMessage()} title={t("composer.sendMessageHint")} type="button"><Send size={18} /></button>
            </div>
            {conversationOpen ? <div className="personal-composer-hint">{steering
              ? (locale === "zh-CN" ? "正在发送本轮追加指令…" : "Sending instructions for this turn…")
              : sending && !steeringTurnId
              ? (locale === "zh-CN" ? "正在回复 · 修改当前任务请使用“调整本轮”" : "Reply in progress · use Adjust turn to change the current task")
              : (locale === "zh-CN" ? "Enter 发送 · Shift+Enter 换行" : "Enter to send · Shift+Enter for a new line")}</div> : null}
            </>}
          </div>
        </div>
      )}
      sidebar={(
        <GoalSidebar
          key={statusSourceControl?.activeSource.statusUrl ?? "/status.json"}
          attentionCount={managerNeedsYouCount}
          goals={workspaceGoals}
          goalArchiveLoadState={goalArchiveLoadState}
          goalLifecycleOperations={callbacks.onExecuteGoalLifecycle ? ["stop", "resume"] : undefined}
          lifecycleBusyGoalIds={lifecycleBusyGoalIds}
          onRequestGoalCreate={readOnly ? undefined : requestGoalCreate}
          onRequestGoalLifecycle={readOnly && !callbacks.onExecuteGoalLifecycle
            ? undefined
            : (goal, operation) => void requestGoalLifecycle(goal, operation)}
          onRetryGoalArchive={callbacks.onRetryGoalArchive || callbacks.onRefresh
            ? () => void (callbacks.onRetryGoalArchive ?? callbacks.onRefresh)?.()
            : undefined}
          onOpenSettings={readOnly ? undefined : () => openSettings({ kind: "settings" })}
          onSelectGoal={selectGoal}
          selectedGoalId={selectedGoalId}
          statusSourceControl={statusSourceControl}
        />
      )}
    />
    </div>
    {settingsPage}
    </>
  );
}
