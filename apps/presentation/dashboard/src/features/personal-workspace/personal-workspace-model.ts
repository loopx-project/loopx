import type { CollaborationReadback, LoopXModeSettings } from "../../data/chat-model";
import type { TeamPlanAppliedOutcome } from "./team-plan-preview";
import type { ActionReviewPlan } from "../../../../../../loopx/control_plane/presentation/action_review_plan.js";
import type { GoalAcceptanceObservation } from "../../data/goal-acceptance-observation";
import type { AttentionDetails } from "./attention-details";
import type { WorkspaceLoadError } from "../../data/workspace-progressive-status";
import type { GoalHostThread, WorkspaceGoalExecution } from "./goal-activity";
export type WorkspaceGoalState =
  | "需修复"
  | "等你"
  | "等待条件"
  | "已安排"
  | "安静运行"
  | "已完成"
  | "已停止";

export type WorkspaceHomeLane =
  | "needs_you"
  | "running"
  | "observing"
  | "scheduled"
  | "history"
  | "stopped";

export type WorkspaceAgentTodo = {
  resumeWhen?: string | null;
  resumeReady?: boolean | null;
  resumeReceiptId?: string | null;
  claimedBy?: string | null;
  dependencies?: string[];
  done: boolean;
  evidence?: string | null;
  nextTransition?: string | null;
  priority?: string | null;
  status?: string | null;
  taskClass?: string | null;
  taskDomain?: string | null;
  text: string;
  todoId: string;
  validationDigest?: string | null;
  validationRevision?: number | null;
  validationRevisionActor?: string | null;
};

export type WorkspaceTodo = WorkspaceAgentTodo & {
  goalId: string;
  goalTitle: string;
  ownerLabel?: string | null;
};

export type WorkspaceGoalUsage = {
  costUsd7d?: number;
  costUsd24h?: number;
  durationMs7d?: number;
  durationMs24h?: number;
  tokens7d?: number;
  tokens24h?: number;
};

export type WorkspaceRepositoryContext = {
  branch: string;
  identity: string;
  label: string;
  readOnly: true;
};

export type WorkspaceGoalSubagentConfiguration = {
  alignCodexHostCapacity?: boolean;
  codexHostCapacity?: {
    configuredChildren: number | null;
    newSessionRequired: boolean;
    requiredChildren: number;
    status: string;
    writeRequired: boolean;
    written: boolean;
  };
  modelConfig?: { model: string; reasoning_effort?: string } | null;
  executionConfig?: string;
  allowedDomains: string[];
  domainCandidates?: Array<{
    domain: string;
    matchingTodoCount: number;
  }>;
  enabled: boolean;
  maxChildren: number;
};

export type WorkspaceGoal = {
  acceptanceObservation?: GoalAcceptanceObservation | null;
  loadState?: "loading" | "error";
  loadError?: WorkspaceLoadError;
  activationState: "active" | "stopped";
  agentId: string;
  agentLanes?: Array<{
    agentId: string;
    label: string;
    lastActivityAt?: string | null;
    state?: string | null;
  }>;
  agentLaneCount?: number;
  agentLabel?: string;
  agentSentence: string;
  agentTodos: WorkspaceAgentTodo[];
  /** Host surfaces of threads bound to this Goal; binding proves ownership, never execution. */
  boundHostSurfaces?: string[];
  /** Completed agent Todo count from the status payload; item lists only carry open Todos. */
  doneTodoCount?: number;
  execution?: WorkspaceGoalExecution;
  goalId: string;
  /** Bound host threads as observed from the host's own records. */
  hostThreads?: GoalHostThread[];
  latestActivity?: string;
  needsYou?: string | null;
  needsYouBlocking?: boolean;
  nextSentence: string;
  repository?: WorkspaceRepositoryContext;
  state: WorkspaceGoalState;
  subagentExecution?: WorkspaceGoalSubagentConfiguration;
  title: string;
  usage?: WorkspaceGoalUsage | null;
};

export type WorkspaceAttention = {
  details?: AttentionDetails;
  sourceId?: string;
  blocking: boolean;
  evidence?: string | null;
  explanation?: string | null;
  goalId: string;
  goalTitle?: string;
  priority?: "high" | "medium" | "low";
  text: string;
  todoId: string;
  updatedAt?: string | null;
};

export type WorkspaceSessionMessage = {
  createdAt?: string;
  messageId: string;
  role: "user" | "assistant" | "error";
  text: string;
};

export type WorkspaceRun = {
  agentId: string;
  agentLabel: string;
  canInterrupt?: boolean;
  completedSteps: number;
  goalId: string;
  goalTitle: string;
  latestActivity: string;
  resumable?: boolean;
  runId: string;
  sessionId?: string;
  sessionMessages?: WorkspaceSessionMessage[];
  sessionStatus?: string;
  todoId?: string;
  status: "queued" | "running" | "waiting" | "completed" | "failed" | "interrupted";
  title: string;
  totalSteps: number;
  turnId?: string;
  outputs?: Array<Pick<WorkspaceOutput, "outputId" | "title" | "kind" | "createdAt">>;
};

export type WorkspaceOutput = {
  agentId?: string;
  agentLabel?: string;
  createdAt?: string;
  goalId: string;
  goalTitle?: string;
  kind?: "document" | "code" | "report" | "evidence";
  outputId: string;
  runId?: string;
  safePreview?: string;
  summary?: string;
  title: string;
  todoId?: string;
  report?: {
    addedCount: number;
    changedCount: number;
    deliveredAt: string;
    generationId: string;
    items: Array<{
      changeKind: "added" | "changed";
      previousStatus?: string;
      sourceRef: string;
      status: string;
      summary: string;
      title: string;
    }>;
    periodEndAt: string;
    periodStartAt: string;
    predecessorPublicationId?: string | null;
    publicationId: string;
  };
};

export type WorkspaceChannel = "manager" | "attention" | "running" | "outputs";
export type WorkspaceGoalTab = "overview" | "chat" | "tasks" | "files";

export type WorkspaceScheduleKind = "heartbeat" | "monitor";

export type WorkspaceSchedule = {
  agentId?: string;
  executionHistory?: Array<{
    label: string;
    runId?: string;
    status: "completed" | "failed" | "interrupted" | "running";
    timestamp: string;
  }>;
  goalId: string;
  label: string;
  nextRunAt?: string;
  notificationRule?: string;
  previousRunAt?: string;
  schedule?: string;
  scheduleId?: string;
  scheduleKind: WorkspaceScheduleKind;
  sessionId?: string;
  status?: "active" | "paused" | "draft" | "error";
  stopCondition?: string;
  target?: string;
  timezone?: string;
};

export type WorkspaceActionPreview = {
  reviewPlan?: ActionReviewPlan;
  actionKind:
    | "goal.create"
    | "goal.update"
    | "goal.lifecycle"
    | "todo.create"
    | "todo.update"
    | "agent.bind"
    | "heartbeat.bind"
    | "monitor.create"
    | "monitor.update"
    | "gate.resolve"
    | "run.correct"
    | "operation.execute"
    | "team.plan";
  agentLabel?: string;
  fields: Array<{ key: string; label: string; value: string }>;
  goalId?: string;
  lifecycleOperation?: "stop" | "resume" | "delete";
  impact: string;
  gate?: {
    kind: string;
    nextAction?: string;
    summary: string;
  };
  previewId: string;
  primaryLabel?: string;
  errorMessage?: string;
  status: "draft" | "ready" | "applying" | "applied" | "gated" | "stale" | "error" | "rejected" | "deferred";
  // The lanes a confirmed team plan left unstaffed, read from the apply
  // receipt so the card can name them after the confirmation, not only in the
  // preview that the confirmation replaced.
  teamPlanOutcome?: TeamPlanAppliedOutcome;
  teamPlanAssignments?: Array<{ laneId: string; agentId: string; task: string }>;
  teamPlanGapLanes?: Array<{ laneId: string; agentId: string; reasonCode: string; task?: string }>;
  teamPlanTodoIds?: string[];
  title: string;
  sourceRequest?: WorkspaceActionPreviewRequest;
  workspaceCandidates?: Array<{ label: string; workspaceRef: string }>;
};

export type WorkspaceMessage = {
  activity?: string[];
  collaboration?: CollaborationReadback;
  agentLabel?: string;
  attachments?: WorkspaceImageAttachment[];
  id: string;
  pending?: boolean;
  returnDelivery?: WorkspaceReturnDelivery;
  role: "assistant" | "user" | "system";
  sourceTurnId?: string;
  sourceMessageId?: string;
  sourceSessionId?: string;
  text: string;
  time?: string;
};

export type WorkspaceReturnDelivery = {
  error?: string | null;
  status: string;
  verification?: "reconciled_after_restart";
};

export type WorkspaceImageAttachment = {
  dataUrl: string;
  id: string;
  mimeType: string;
  name: string;
  size: number;
};

export type WorkspaceTimelineItem =
  | { id: string; kind: "message"; message: WorkspaceMessage }
  | { attention: WorkspaceAttention; id: string; kind: "attention" }
  | { id: string; kind: "run"; run: WorkspaceRun }
  | { id: string; kind: "schedule"; schedule: WorkspaceSchedule }
  | { id: string; kind: "output"; output: WorkspaceOutput }
  | { id: string; kind: "proposal"; proposal: WorkspaceActionPreview };

export type WorkspaceWorker = {
  agentId: string;
  currentTodoGoalId?: string | null;
  currentTodoText?: string | null;
  lastActivityAt?: string | null;
  state?: string | null;
};

export type WorkspaceGoalNotification = {
  goalId: string;
  configured: boolean;
  enabled: boolean;
  humanGateAutoNotifyEnabled: boolean;
  lastNotifiedAt?: string | null;
  receiptCount: number;
  targetRef?: string | null;
};

export type WorkspaceSystemHealth = {
  freshnessWarning?: string | null;
  issues: string[];
  ok: boolean;
  summary: string;
};

export type WorkspaceModel = {
  blockingTodoCount: number;
  goalNotifications?: WorkspaceGoalNotification[];
  goals: WorkspaceGoal[];
  openUserTodoCount: number;
  periodicReports?: {
    error?: string | null;
    loading: boolean;
  };
  systemHealth?: WorkspaceSystemHealth;
  timeline?: WorkspaceTimelineItem[];
  attentionHistory?: WorkspaceAttention[];
  userTodos: WorkspaceAttention[];
  workers?: WorkspaceWorker[];
};

export type WorkspaceAgentOption = {
  adapterKind?: string;
  agentId: string;
  available: boolean;
  capability?: string;
  interrupt?: boolean;
  label: string;
  location?: string;
  resume?: boolean;
  source?: string;
  streaming?: boolean;
  toolCalls?: boolean;
  trustScope?: string;
  workspaceCompatibility?: string;
};

export type WorkspaceDrawerSelection =
  | { item: WorkspaceAttention; kind: "attention" }
  | { item: WorkspaceGoal; kind: "goal" }
  | { item: WorkspaceTodo; kind: "todo" }
  | { item: WorkspaceRun; kind: "run" }
  | { item: WorkspaceOutput; kind: "output" }
  | { item: WorkspaceActionPreview; kind: "proposal" }
  | { goalId?: string; kind: "settings"; tab?: "appearance" | "capabilities" | "language" | "lark" | "machine" | "steward" }
  | {
      item: WorkspaceSchedule;
      kind: "schedule";
    };

export type PersonalHomeCompatibleModel = {
  blockingTodoCount: number;
  goalNotifications?: WorkspaceGoalNotification[];
  goals: WorkspaceGoal[];
  openUserTodoCount: number;
  systemHealth?: WorkspaceSystemHealth;
  attentionHistory?: WorkspaceAttention[];
  userTodos: WorkspaceAttention[];
  workers?: WorkspaceWorker[];
};

export type WorkspaceGoalArchiveLoadState =
  | { phase: "idle" | "loading" | "ready"; error: null }
  | { phase: "error"; error: string };

export type PersonalWorkspaceCallbacks = {
  onApplyProposal?: (proposal: WorkspaceActionPreview) => void | Promise<void>;
  onApplyAttention?: (attention: WorkspaceAttention) => void;
  onCancelProposal?: (proposal: WorkspaceActionPreview) => void;
  onTransitionProposal?: (proposal: WorkspaceActionPreview, transition: "regenerate" | "reject" | "defer") => void | Promise<void>;
  onSelectWorkspaceCandidate?: (proposal: WorkspaceActionPreview, workspaceRef: string) => void | Promise<void>;
  onCorrectRun?: (run: WorkspaceRun, message: string) => void | Promise<void>;
  onCloseRunSession?: (run: WorkspaceRun) => void | Promise<void>;
  onExplainDecision?: (attention: WorkspaceAttention) => void | Promise<void>;
  onExportOutput?: (output: WorkspaceOutput) => void | Promise<void>;
  onInterruptRun?: (run: WorkspaceRun) => void | Promise<void>;
  onInterruptConversationTurn?: (contextId: string, turnId: string) => Promise<void>;
  onSteerConversationTurn?: (contextId: string, turnId: string, message: string, ingressId: string) => Promise<void>;
  onOpenGoal?: (goalId: string) => void | Promise<void>;
  onOpenGoalView?: (tab: WorkspaceGoalTab) => void;
  onOpenRunSession?: (run: WorkspaceRun) => void | Promise<void>;
  onOpenOutput?: (output: WorkspaceOutput) => void;
  onPreviewGoalSubagentConfiguration?: (
    request: WorkspaceGoalSubagentConfiguration & { goalId: string },
  ) => Promise<{
    changed: boolean;
    configuration: WorkspaceGoalSubagentConfiguration;
    previewId: string;
  }>;
  onApplyGoalSubagentConfiguration?: (
    request: WorkspaceGoalSubagentConfiguration & { goalId: string; previewId: string },
  ) => Promise<WorkspaceGoalSubagentConfiguration>;
  onGoalActivationStateChange?: (goalId: string, activationState: "active" | "stopped") => void;
  onExecuteGoalLifecycle?: (request: {
    goalId: string;
    operation: "stop" | "resume";
    reason: string;
  }) => Promise<{
    activationState: "active" | "stopped";
    projectionVerified: boolean;
  }>;
  onGoalDeleted?: (goalId: string) => void;
  /** Re-read the workspace projection after an applied action. `invalidateGoalIds`
   * names the Goals the action touched, so a peer's snapshot is not re-read for it. */
  onReconcileStatus?: (options?: { invalidateGoalIds?: string[] }) => void | Promise<void>;
  onRefresh?: () => void | Promise<void>;
  onRetryGoalArchive?: () => void | Promise<void>;
  onPreviewAction?: (request: WorkspaceActionPreviewRequest) => WorkspaceActionPreview | Promise<WorkspaceActionPreview>;
  onRequestGoalCreate?: () => WorkspaceActionPreview | Promise<WorkspaceActionPreview | void> | void;
  onRequestScheduleConfig?: (kind: WorkspaceScheduleKind, goalId: string | null) => WorkspaceActionPreview | Promise<WorkspaceActionPreview | void> | void;
  onRetryResumeRun?: (run: WorkspaceRun) => void | Promise<void>;
  onStartNewRunSession?: (run: WorkspaceRun) => void | Promise<void>;
  onUpdateSchedule?: (schedule: WorkspaceSchedule, operation: "edit" | "pause" | "resume" | "run_now" | "stop") => void | Promise<void>;
  onSendMessage?: (
    message: string,
    agentId: string,
    goalId: string | null,
    attachments?: WorkspaceImageAttachment[],
  ) => void | WorkspaceActionPreviewRequest | Promise<void | WorkspaceActionPreviewRequest>;
  onPrepareLoopX?: (agentId: string, goalId: string) => Promise<string>;
  onStartLoopX?: (operation: "start" | "resume", agentId: string, goalId: string,
    settings?: LoopXModeSettings) => void;
  onSelectAgent?: (agentId: string) => void;
  onSelectChannel?: (channel: WorkspaceChannel) => void;
  onSelectGoal?: (goalId: string | null) => void;
  onOpenNotificationSettings?: (goalId?: string) => void;
  onFetchNotificationTargets?: () => Promise<Array<{ enabled: boolean; provider: string; target_name: string }>>;
  onSetupGoalChannel?: (options: { execute: boolean; goalId: string; target: string }) => Promise<{ ok: boolean; blocker?: string; public_summary?: string; status?: string }>;
  onToggleGoalAutoNotify?: (options: { autoNotify: boolean; goalId: string }) => Promise<{ ok: boolean; blocker?: string; public_summary?: string; status?: string }>;
};

export type WorkspaceActionPreviewRequest = {
  actionKind: WorkspaceActionPreview["actionKind"];
  context: Record<string, unknown>;
  idempotencyKey: string;
  normalizedParameters: Record<string, unknown>;
  summary: string;
};

export function normalizePersonalHomeModel(model: PersonalHomeCompatibleModel): WorkspaceModel {
  return {
    blockingTodoCount: model.blockingTodoCount,
    goalNotifications: model.goalNotifications,
    goals: model.goals,
    openUserTodoCount: model.openUserTodoCount,
    systemHealth: model.systemHealth,
    attentionHistory: model.attentionHistory,
    userTodos: model.userTodos,
    workers: model.workers,
  };
}

export function goalTitleFor(model: WorkspaceModel, goalId: string) {
  return model.goals.find((goal) => goal.goalId === goalId)?.title ?? goalId;
}

export function workspaceSessionStatusLabel(status?: string): string {
  if (!status) return "状态未知";
  return ({
    busy: "执行中",
    completed: "已完成",
    failed: "需检查",
    queued: "已安排",
    ready: "可继续",
    resume_failed: "恢复失败",
    running: "执行中",
    waiting: "等待条件",
  } as Record<string, string>)[status] ?? status;
}

/** A quiet persistent conversation must not masquerade as waiting work. */
export function goalHasExecutionSummary(goal: Pick<WorkspaceGoal, "state">): boolean {
  return ["已安排", "需修复", "等待条件"].includes(goal.state);
}

/**
 * Project the detailed Goal lifecycle onto the five manager-home buckets.
 * The home shows populated active lanes and collapses terminal work into history.
 */
export function workspaceHomeLaneForGoal(goal: WorkspaceGoal): WorkspaceHomeLane {
  if (goal.activationState === "stopped" || goal.state === "已停止") return "stopped";
  if (goal.state === "已完成") return "history";
  if (goal.needsYou || goal.state === "等你") return "needs_you";
  if (goal.execution?.kind === "running" || goal.state === "需修复") return "running";
  if (goal.state === "安静运行") return "observing";
  return "scheduled";
}

/** Human-readable waiting time for an open attention item; null when under an hour or unknown. */
export function attentionAgeLabel(updatedAt?: string | null): string | null {
  if (!updatedAt) return null;
  const then = new Date(updatedAt).getTime();
  if (Number.isNaN(then)) return null;
  const diff = Date.now() - then;
  if (diff < 3_600_000) return null;
  const days = Math.floor(diff / 86_400_000);
  if (days >= 1) return `${days} 天`;
  return `${Math.floor(diff / 3_600_000)} 小时`;
}

export function formatTokenCount(value: number): string {
  const n = value;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatCostUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function formatDurationMs(value: number): string {
  const ms = value;
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)}h`;
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${Math.round(ms / 1_000)}s`;
  return `${ms}ms`;
}

export function formatUsageValue(
  value: number | null | undefined,
  notMeasured: string,
  format: (value: number) => string,
): string {
  return value === undefined || value === null ? notMeasured : format(value);
}

/** True when a goal has at least one observed usage metric. A measured zero is still usage data. */
export function hasGoalUsage(usage?: WorkspaceGoalUsage | null): usage is WorkspaceGoalUsage {
  return Boolean(usage && [
    usage.tokens24h,
    usage.tokens7d,
    usage.costUsd24h,
    usage.costUsd7d,
    usage.durationMs24h,
    usage.durationMs7d,
  ].some((value) => value !== undefined && value !== null));
}

/** Compact header chip; null when no metric has been observed. */
export function goalUsageLabel(
  usage: WorkspaceGoalUsage | null | undefined,
  labels: { cost: string; duration: string; period24h: string; period7d: string; tokens: string },
): string | null {
  if (!hasGoalUsage(usage)) return null;
  const windowLabel = (
    period: string,
    tokens: number | null | undefined,
    cost: number | null | undefined,
    duration: number | null | undefined,
  ) => {
    const measurements = [
      tokens === undefined || tokens === null ? null : `${formatTokenCount(tokens)} ${labels.tokens}`,
      cost === undefined || cost === null ? null : `${labels.cost}: ${formatCostUsd(cost)}`,
      duration === undefined || duration === null ? null : `${labels.duration}: ${formatDurationMs(duration)}`,
    ].filter((value): value is string => value !== null);
    return measurements.length ? `${period} ${measurements.join(" · ")}` : null;
  };
  return windowLabel(labels.period7d, usage.tokens7d, usage.costUsd7d, usage.durationMs7d)
    ?? windowLabel(labels.period24h, usage.tokens24h, usage.costUsd24h, usage.durationMs24h);
}

export function workerStateLabel(state?: string | null): string {
  if (state === "running") return "执行中";
  if (state === "monitoring") return "监控中";
  if (state === "blocked") return "受阻";
  return "待命";
}

/** Bounded card copy; the owning item retains its complete detail text. */
export function compactWorkspaceText(value?: string | null, limit = 132) {
  const text = (value ?? "").replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}
