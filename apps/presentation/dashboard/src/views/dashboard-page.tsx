import {compactWorkspaceText as compactShareText} from "../features/personal-workspace/personal-workspace-model";
import type { GoalAcceptanceObservation } from "../data/goal-acceptance-observation";
import { attentionDetails, sourceAttention } from "../features/personal-workspace/attention-details";
import type { AttentionDetails } from "../features/personal-workspace/attention-details";
import { directoryStatusPayload, fetchWorkspaceDirectory, loadWorkspaceGoalSnapshots, reusableGoalSnapshots, type WorkspaceProgress, type WorkspaceLoadError } from "../data/workspace-progressive-status";
import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, Moon, RefreshCw, Sun } from "lucide-react";

import { dashboardRoute } from "../router";
import {
  QueueItem,
  RunGoal,
  RunRecord,
  StatusPayload,
  type PeriodicReportProjection,
  AgentManagementProjection,
  TodoItem,
  TodoGroup,
  TodoIndexSummary,
  UsageSummary,
  exampleStatusPayload,
  formatStatusError,
  parseStatusPayload,
  withGoalActivationState,
  withoutGoal,
} from "../data/status";
import {
  fetchPeriodicReportIndex,
  fetchPeriodicReportProjection,
  periodicReportApiUrls,
  resolveLocalStatusUrl,
  scopedStatusUrl,
} from "../data/local-status-query";
import {
  ChatApiError,
  applyGoalSubagentConfiguration,
  applyTypedAction,
  applyTodo,
  closeChatSession,
  createChatSession,
  updateLoopXMode,
  type LoopXModeSettings,
  fetchChatCapabilities,
  fetchChatHistory,
  fetchChatSession,
  fetchChatSessions,
  interruptChatTurn,
  steerChatTurn,
  previewGoalSubagentConfiguration,
  previewTodo,
  previewTypedAction,
  recordProjectionExchange,
  resumeChatSession,
  resumeChatTurnStreaming,
  sendChatTurnStreaming,
  selectAvailableChatAgent,
  sessionInvalidatedByPayload,
  todoNoWriteReceiptFromPayload,
  todoReceiptLabel,
  isTodoProposal,
  type ChatSessionSnapshot,
  type ChatSessionSummary,
  type ChatImageAttachment,
  type ChatVisibleMessage,
  type ManagerChannelBinding,
  type ManagerRuntimeSessionReadback,
  type ProtectedActionProposal,
  type TodoProposal,
} from "../data/chat";
import {
  beginStatusRequest,
  createStatusRequestFence,
  reserveStatusSourceSelection,
  statusRequestCanCommit,
  statusRequestIsCurrent,
  type StatusRequest,
  type StatusRequestFence,
} from "../data/status-request-fence";
import { mergeScopedStatusProjections } from "../data/status-merge";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import {
  agentFamily,
  presentedAgentFamily,
} from "../features/personal-workspace/agent-family";
import { PersonalWorkspacePage } from "../features/personal-workspace/personal-workspace-page";
import { MIN_SEPARATE_ANSWER_LENGTH, visibleAgentMessage } from "../features/personal-workspace/answer-text";
import { useWorkspaceI18n, type WorkspaceTranslate } from "../features/personal-workspace/i18n";
import {
  agentStatusSentence,
  projectionSentence,
} from "../features/personal-workspace/projection-localization";
import {
  goalHasExecutionSummary,
  normalizePersonalHomeModel,
  type WorkspaceAgentOption,
  type WorkspaceAttention,
  type WorkspaceGoal,
  type WorkspaceGoalUsage,
  type WorkspaceHomeLane,
  type WorkspaceImageAttachment,
  type WorkspaceModel,
  type WorkspaceOutput,
  type WorkspaceRun,
  type WorkspaceSchedule,
  type WorkspaceScheduleKind,
  type WorkspaceSystemHealth,
  type WorkspaceTimelineItem,
  type WorkspaceWorker,
  type WorkspaceGoalNotification,
  type WorkspaceGoalArchiveLoadState,
  type WorkspaceActionPreview,
  type WorkspaceActionPreviewRequest,
} from "../features/personal-workspace/personal-workspace-model";
import { goalExecutionFromSessions } from "../features/personal-workspace/goal-activity";

const protectedOperationLabels: Record<ProtectedActionProposal["operation"], string> = {
  delete: "删除",
  deploy: "部署",
  merge: "合并",
  payment: "付款",
  release: "发布",
};

function semanticProtectedActionPreview(
  goalId: string,
  message: string,
  proposal: ProtectedActionProposal,
): WorkspaceActionPreviewRequest | null {
  const normalizedMessage = message.replace(/\s+/gu, " ").trim().toLowerCase();
  const normalizedTarget = proposal.target.replace(/\s+/gu, " ").trim().toLowerCase();
  if (!normalizedTarget || !normalizedMessage.includes(normalizedTarget)) return null;
  return {
    actionKind: "goal.update",
    context: {
      goal_id: goalId,
      kind: "goal",
      natural_language: message,
      semantic_proposal: {
        operation: proposal.operation,
        target: proposal.target,
      },
    },
    idempotencyKey: `workspace-semantic-protected-${goalId}-${Date.now().toString(36)}`,
    normalizedParameters: { goal_id: goalId, status: "operator_gate_requested" },
    summary: `请求受保护操作：${protectedOperationLabels[proposal.operation]} · ${proposal.target}`,
  };
}
import type { StatusSourceControl } from "../features/personal-workspace/status-source-switcher";
import { applyRemoteGoalLifecycle, ensureSshSource } from "../data/ssh-host-catalog";
import {
  addSshTunnelStatusSource,
  bindConfiguredSshHostAliases,
  defaultLocalStatusSourceUrl,
  emptyStatusSourceCatalog,
  loadStatusSourceCatalog,
  localStatusSource,
  activeStatusSourceForUrl,
  projectedStatusSourceForUrl,
  removeStatusSource,
  saveStatusSourceCatalog,
  type StatusSource,
} from "../data/status-source-catalog";

const defaultGlobalStatusUrl = defaultLocalStatusSourceUrl;

type BadgeVariant = "neutral" | "info" | "success" | "warning" | "danger";

type DataSource =
  | { kind: "example"; label: string }
  | { kind: "file"; label: string }
  | { kind: "url"; label: string };

async function fetchStatusPayload(url: string) {
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(30_000) });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while loading ${url}`);
  }
  return parseStatusPayload(await response.json());
}

type GoalDirectoryRow = {
  goal: RunGoal;
  queueItem?: QueueItem;
  latestRun?: RunRecord;
  status: string;
  waitingOn: string;
  severity: string;
  lifecyclePhase: string;
  lifecycleFlags: string[];
};

type AgentManagementRow = {
  agentId: string;
  claimedTodos: TodoExplorerItem[];
  currentTodo: AgentManagementProjection["agents"][number]["current_todo"];
  evidenceRefs: string[];
  goalIds: string[];
  handoffNote: string | null;
  lastActivity: string | null;
  nextSafeAction: string;
  primaryGoalId: string;
  quotaHints: string[];
  staleClaimHint: string | null;
  status: { label: string; summary: string; variant: "neutral" | "info" | "success" | "warning" | "danger" };
  workspaceRef: string | null;
};

type TodoExplorerItem = {
  goalId: string;
  role: "user" | "agent";
  todo: TodoItem;
};

type PersonalAgentTodoItem = {
  resumeWhen?: string | null;
  resumeReady?: boolean | null;
  resumeReceiptId?: string | null;
  claimedBy?: string | null;
  done: boolean;
  evidence?: string | null;
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

function inferLifecyclePhase(status?: string | null, run?: RunRecord) {
  if (run?.controller_readiness?.decision_advisor_ready || run?.controller_readiness?.write_controller_ready) {
    return "controller_ready";
  }
  if (run?.controller_readiness) {
    return "controller_gated";
  }
  if (run?.human_reward) {
    return "reward_judged";
  }
  if (run?.operator_gate?.decision === "approve") {
    return "operator_approved";
  }
  if (run?.operator_gate) {
    return "operator_gated";
  }
  const value = status || run?.classification || "";
  if (value === "connected_without_run") {
    return "connected";
  }
  if (value === "read_only_project_map" || run?.project_map) {
    return "mapped";
  }
  if (value === "state_refreshed") {
    return "refreshed";
  }
  if (value && value !== "no_status") {
    return "adapter_inspected";
  }
  return "registered";
}

function buildGoalDirectoryRows(goals: RunGoal[], queueItems: QueueItem[]): GoalDirectoryRow[] {
  const queueByGoal = new Map(queueItems.map((item) => [item.goal_id, item]));
  const seen = new Set<string>();
  const rows: GoalDirectoryRow[] = goals.map((goal) => {
    seen.add(goal.id);
    const queueItem = queueByGoal.get(goal.id);
    const latestRun = goal.latest_runs[0];
    const phase = queueItem?.lifecycle_phase
      ?? goal.lifecycle_phase
      ?? latestRun?.lifecycle_phase
      ?? inferLifecyclePhase(queueItem?.status ?? latestRun?.classification ?? goal.status, latestRun);
    const flags = queueItem?.lifecycle_flags?.length
      ? queueItem.lifecycle_flags
      : goal.lifecycle_flags?.length
        ? goal.lifecycle_flags
        : latestRun?.lifecycle_flags?.length
          ? latestRun.lifecycle_flags
          : [phase];
    return {
      goal,
      queueItem,
      latestRun,
      status: queueItem?.status ?? latestRun?.classification ?? goal.status ?? "no_status",
      waitingOn: queueItem?.waiting_on ?? "clear",
      severity: queueItem?.severity ?? "clear",
      lifecyclePhase: phase,
      lifecycleFlags: flags,
    };
  });

  for (const item of queueItems) {
    if (seen.has(item.goal_id)) {
      continue;
    }
    rows.push({
      goal: {
        activation_state: item.activation_state,
        id: item.goal_id,
        status: item.status,
        display_name: item.goal_id,
        latest_runs: [],
        lifecycle_flags: [item.lifecycle_phase ?? "registered"],
        registry_member: true,
        legacy_runtime_goal: false,
        index_exists: false,
        raw_index_records: 0,
        unique_runs: 0,
      } as unknown as RunGoal,
      queueItem: item,
      status: item.status,
      waitingOn: item.waiting_on,
      severity: item.severity,
      lifecyclePhase: item.lifecycle_phase ?? "registered",
      lifecycleFlags: item.lifecycle_flags ?? ["registered"],
    });
  }

  return rows;
}

function cleanShareText(value?: string | null) {
  return (value ?? "").replace(/\s+/g, " ").trim();
}


function shareUsageById(usage?: UsageSummary | null) {
  const map = new Map<string, NonNullable<UsageSummary["goals"]>[number]>();
  for (const item of usage?.goals ?? []) {
    map.set(item.goal_id, item);
  }
  return map;
}

function sumMeasuredUsage(left?: number, right?: number): number | undefined {
  return left === undefined || right === undefined ? undefined : left + right;
}

function getShareTodos(row: GoalDirectoryRow | undefined, role: "user" | "agent") {
  if (!row) {
    return null;
  }
  const assetTodos = role === "user"
    ? row.queueItem?.project_asset?.user_todos
    : row.queueItem?.project_asset?.agent_todos;
  if (assetTodos?.items?.length) {
    return {
      done_count: assetTodos.done ?? assetTodos.items.filter((item) => item.done).length,
      items: assetTodos.items,
      open_count: assetTodos.open ?? assetTodos.items.filter((item) => !item.done).length,
      total_count: assetTodos.total ?? assetTodos.items.length,
    };
  }
  const queueTodos = role === "user" ? row.queueItem?.user_todos : row.queueItem?.agent_todos;
  if (queueTodos?.items?.length) {
    return queueTodos;
  }
  return null;
}

function firstOpenTodo(todos?: TodoGroup | null) {
  return todos?.items.find((todo) => !todo.done);
}

function todosFromProjectAssetSummary(
  summary?: { items?: TodoItem[]; total?: number; open?: number; done?: number; advancement_done_count?: number; source_section?: string | null } | null,
  fallback?: TodoGroup | null,
  _label = "todos",
): TodoGroup | null {
  if (summary?.items?.length) {
    return {
      advancement_done_count: summary.advancement_done_count ?? fallback?.advancement_done_count,
      done_count: summary.done ?? summary.items.filter((item) => item.done).length,
      items: summary.items,
      open_count: summary.open ?? summary.items.filter((item) => !item.done).length,
      total_count: summary.total ?? summary.items.length,
    };
  }
  return fallback ?? null;
}

function quotaStateForShare(row?: GoalDirectoryRow) {
  return row?.queueItem?.project_asset?.quota?.state ?? row?.queueItem?.quota?.state ?? row?.goal.quota?.state ?? "waiting";
}

function buildAgentManagementRows(
  rows: GoalDirectoryRow[],
  todoIndex?: TodoIndexSummary | null,
  projection?: AgentManagementProjection | null,
): AgentManagementRow[] {
  const items: TodoExplorerItem[] = [];
  for (const row of rows) {
    const todos = getShareTodos(row, "agent");
    for (const todo of todos?.items ?? []) {
      items.push({ goalId: row.goal.id, role: "agent", todo });
    }
  }
  const grouped = new Map<string, TodoExplorerItem[]>();
  for (const item of items) {
    const agentId = item.todo.claimed_by || "codex";
    const bucket = grouped.get(agentId) ?? [];
    bucket.push(item);
    grouped.set(agentId, bucket);
  }
  const projectedByAgent = new Map((projection?.agents ?? []).map((row) => [row.agent_id, row]));
  const agentIds = Array.from(new Set([...grouped.keys(), ...projectedByAgent.keys()]));

  return agentIds.map((agentId) => {
    const claimedTodos = grouped.get(agentId) ?? [];
    const projected = projectedByAgent.get(agentId);
    const goalIds = Array.from(new Set([
      ...claimedTodos.map((item) => item.goalId),
      projected?.current_todo?.goal_id,
      ...(projected?.goal_ids ?? []),
    ].filter(Boolean) as string[]));
    const openTodos = claimedTodos.filter((item) => !item.todo.done);
    const primaryTodo = openTodos[0] ?? claimedTodos[0];
    const primaryGoalId = primaryTodo?.goalId ?? projected?.current_todo?.goal_id ?? goalIds[0] ?? "";
    const latestActivity = projected?.last_activity_at ?? null;
    return {
      agentId,
      claimedTodos,
      currentTodo: projected?.current_todo ?? null,
      evidenceRefs: [],
      goalIds,
      handoffNote: null,
      lastActivity: latestActivity,
      nextSafeAction: projected?.next_action?.trim() || "Inspect status projection before taking work",
      primaryGoalId,
      quotaHints: [],
      staleClaimHint: null,
      status: { label: "可用", summary: "正常运行", variant: "success" },
      workspaceRef: null,
    };
  });
}

type PersonalGoalState = "需修复" | "等你" | "等待条件" | "已安排" | "已完成" | "安静运行" | "已停止";

type PersonalGoalItem = {
  acceptanceObservation?: GoalAcceptanceObservation | null;
  loadState?: "loading" | "error";
  loadError?: WorkspaceLoadError;
  activationState: "active" | "stopped";
  agentId: string;
  agentSentence: string;
  agentTodos: PersonalAgentTodoItem[];
  doneTodoCount: number;
  goalId: string;
  latestActivity?: string;
  needsYou?: string | null;
  needsYouActionKind?: string | null;
  needsYouBlocking?: boolean;
  needsYouTaskClass?: string | null;
  needsYouTodoId?: string | null;
  nextSentence: string;
  hasRunObservation: boolean;
  state: PersonalGoalState;
  subagentExecution?: {
    allowedDomains: string[];
    domainCandidates: Array<{
      domain: string;
      matchingTodoCount: number;
    }>;
    enabled: boolean;
    executionConfig?: string;
    maxChildren: number;
  };
  title: string;
  usage?: WorkspaceGoalUsage | null;
};

type PersonalNeedsYouItem = {
  details?: AttentionDetails;
  actionKind?: string | null;
  blocking: boolean;
  goalId: string;
  taskClass?: string | null;
  text: string;
  todoId: string;
  updatedAt?: string | null;
};

type PersonalHomeModel = {
  blockingTodoCount: number;
  goalNotifications?: WorkspaceGoalNotification[];
  goals: PersonalGoalItem[];
  openUserTodoCount: number;
  systemHealth?: WorkspaceSystemHealth;
  attentionHistory?: PersonalNeedsYouItem[];
  userTodos: PersonalNeedsYouItem[];
  visibleUserTodos: PersonalNeedsYouItem[];
  workers?: WorkspaceWorker[];
};
type PersonalManagerMessage = {
  sourceMessageId?: string;
  sourceSessionId?: string;
  sourceTurnId?: string;
  activity?: string[];
  agentLabel?: string;
  attachments?: WorkspaceImageAttachment[];
  id: number;
  lines: string[];
  pending?: boolean;
  returnDelivery?: ChatVisibleMessage["return_delivery"];
  collaboration?: ChatVisibleMessage["collaboration"];
  reconnect?: boolean;
  role: "assistant" | "user";
  sourceLabel?: string;
  text: string;
};

function workspaceImageAttachments(attachments?: ChatImageAttachment[]): WorkspaceImageAttachment[] | undefined {
  return attachments?.map((attachment) => ({
    dataUrl: attachment.data_url,
    id: attachment.id,
    mimeType: attachment.mime_type,
    name: attachment.name,
    size: attachment.size,
  }));
}

type PersonalProposalState =
  | "candidate"
  | "previewing"
  | "ready"
  | "applying"
  | "approved"
  | "rejected"
  | "cancelled"
  | "stale"
  | "error";

type PersonalProposalCard = {
  goalId: string;
  id: number;
  previewId: string | null;
  proposal: TodoProposal;
  receiptLabel: string | null;
  state: PersonalProposalState;
  statusMessage: string | null;
};

type PersonalAgentOption = {
  adapterKind?: string;
  agentId: string;
  available: boolean;
  capability: string;
  interrupt?: boolean;
  label: string;
  location?: string;
  resume?: boolean;
  source?: string;
  statusLabel: string;
  streaming?: boolean;
  toolCalls?: boolean;
  trustScope?: string;
};

type PersonalRuntimeBinding = {
  agentId: string;
  resumable: boolean;
  sessionId: string;
  status: string;
  turnId?: string;
};

const personalAgentSelectionStorageKey = "loopx.personal-agent-selection.v1";

function readPersonalAgentSelections() {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const value = JSON.parse(window.localStorage.getItem(personalAgentSelectionStorageKey) ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value).filter((entry): entry is [string, string] =>
        typeof entry[0] === "string" && typeof entry[1] === "string"
      ),
    );
  } catch {
    return {};
  }
}

type PersonalManagerAnswer = {
  lines: string[];
  text: string;
};

const personalGoalStateVariant: Record<PersonalGoalState, BadgeVariant> = {
  "需修复": "danger",
  "等你": "warning",
  "等待条件": "info",
  "已安排": "info",
  "安静运行": "neutral",
  "已停止": "neutral",
  "已完成": "neutral",
};

function personalGoalTitle(goalId: string, displayName?: string | null) {
  const registeredDisplayName = cleanShareText(displayName);
  if (registeredDisplayName) {
    return registeredDisplayName;
  }
  return goalId
    .replace(/^loopx[-_]/i, "LoopX ")
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part, index) => index === 0 ? `${part.slice(0, 1).toUpperCase()}${part.slice(1)}` : part)
    .join(" ");
}

function isAgentResultMessage(role: string, text: string) {
  return ["agent", "assistant"].includes(role.trim().toLowerCase()) && text.trim().length > 0;
}

const PERSONAL_AGENT_FALLBACK_CAPABILITY = "已发现的项目 Agent";

function personalAgentLabel(agentId: string) {
  switch (agentFamily(agentId)) {
    case "codex":
      return "Codex";
    case "claude":
      return "Claude Code";
    case "kiro":
      return "Kiro CLI";
    case "trae":
      return "Trae CLI Agent";
    case "coco":
      return "Coco Agent";
    default:
      return personalGoalTitle(agentId);
  }
}

function personalAgentCapability(agentId: string, adapterKind?: string | null) {
  switch (presentedAgentFamily(agentId, adapterKind)) {
    case "codex":
      return "代码与项目执行";
    case "claude":
      return "复杂分析与长任务";
    case "openai":
    case "anthropic":
      return "管家问答 · 无工具";
    case "kiro":
      return "终端编码 · 原生 /goal 循环";
    case "trae":
      return "前端与交互实现";
    case "coco":
      return "通用任务";
    default:
      return PERSONAL_AGENT_FALLBACK_CAPABILITY;
  }
}

function personalVisibleAgentMessage(value: string) {
  const openTag = "<loopx-review-json>";
  const closeTag = "</loopx-review-json>";
  const start = value.lastIndexOf(openTag);
  const end = value.lastIndexOf(closeTag);
  if (start < 0 || end <= start) return value.trim();
  const envelope = value.slice(start + openTag.length, end).trim();
  try {
    const parsed = JSON.parse(envelope) as { message?: unknown };
    if (typeof parsed.message === "string" && parsed.message.trim()) return parsed.message.trim();
  } catch {
    const match = envelope.match(/"message"\s*:\s*("(?:\\.|[^"\\])*")/s);
    if (match) {
      try {
        const message = JSON.parse(match[1]);
        if (typeof message === "string" && message.trim()) return message.trim();
      } catch {
        // Fall through to the bounded visible answer before the malformed envelope.
      }
    }
  }
  return value.slice(0, start).trim();
}

function personalTodosForQueueItem(item: QueueItem, role: "user" | "agent") {
  const projectAsset = item.project_asset;
  return role === "user"
    ? todosFromProjectAssetSummary(projectAsset?.user_todos, item.user_todos, "project_asset.user_todos")
    : todosFromProjectAssetSummary(projectAsset?.agent_todos, item.agent_todos, "project_asset.agent_todos");
}

function personalTodoText(todo: TodoItem) {
  return compactShareText(todo.title ?? todo.text, 112);
}

function personalTodoResumeReceiptId(todo: TodoItem) {
  const receipt = todo.resume_condition?.resume_receipt;
  if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) return null;
  const receiptId = (receipt as Record<string, unknown>).receipt_id;
  return typeof receiptId === "string" && receiptId.trim() ? receiptId.trim() : null;
}

function personalAgentTodoFromItem(todo: TodoItem, row: GoalDirectoryRow): PersonalAgentTodoItem {
  const latestValidationRevision = todo.completion_validation_revision_history.at(-1);
  return {
    resumeWhen: todo.resume_when ?? null,
    resumeReady: todo.resume_ready ?? null,
    resumeReceiptId: personalTodoResumeReceiptId(todo),
    claimedBy: todo.claimed_by ?? null,
    // Legacy summaries mark deferred entries checked; they are not completed work.
    done: todo.status === "deferred" ? false : todo.done,
    evidence: todo.evidence ? compactShareText(todo.evidence, 96) : null,
    priority: todo.priority ?? null,
    status: todo.status ?? null,
    taskClass: todo.task_class ?? null,
    taskDomain: todo.task_domain ?? null,
    text: personalTodoText(todo),
    todoId: todo.todo_id?.trim() || `${row.goal.id}:agent:${todo.index}`,
    validationDigest: todo.completion_validation_sha256 ?? null,
    validationRevision: todo.completion_validation_revision ?? null,
    validationRevisionActor: latestValidationRevision?.actor_agent_id ?? null,
  };
}

// Owner Workspace needs the queue, not the bounded public-share preview.
// Use the same items for cards and completion deduplication.
function personalAgentTodoItems(row: GoalDirectoryRow): TodoItem[] {
  const queue = row.queueItem?.agent_todos;
  const items = queue?.items ?? row.queueItem?.project_asset?.agent_todos?.items ?? [];
  const merged = new Map(items.map((todo) => [todo.todo_id?.trim() || `${row.goal.id}:agent:${todo.index}`, todo]));
  for (const todo of queue?.deferred_items ?? []) {
    const key = todo.todo_id?.trim() || `${row.goal.id}:agent:${todo.index}`;
    if (!merged.has(key)) merged.set(key, todo);
  }
  return [...merged.values()];
}

function personalAgentTodos(row: GoalDirectoryRow): PersonalAgentTodoItem[] {
  return personalAgentTodoItems(row).map((todo) => personalAgentTodoFromItem(todo, row));
}

function personalSubagentDomainCandidates(
  payload: StatusPayload,
  row: GoalDirectoryRow,
  fallbackTodos: PersonalAgentTodoItem[],
) {
  const candidateTodos = new Map<string, PersonalAgentTodoItem>();
  for (const todo of payload.todo_index?.items ?? []) {
    if (todo.goal_id !== row.goal.id || todo.role !== "agent") continue;
    const projected = personalAgentTodoFromItem(todo, row);
    candidateTodos.set(projected.todoId, projected);
  }
  for (const todo of fallbackTodos) {
    if (!candidateTodos.has(todo.todoId)) candidateTodos.set(todo.todoId, todo);
  }

  const counts = new Map<string, number>();
  for (const todo of candidateTodos.values()) {
    if (todo.done || todo.taskClass !== "advancement_task") continue;
    const domain = todo.taskDomain?.trim();
    if (!domain) continue;
    counts.set(domain, (counts.get(domain) ?? 0) + 1);
  }
  return [...counts].map(([domain, matchingTodoCount]) => ({ domain, matchingTodoCount }));
}

function personalAgentTodoFromProjection(
  todo: NonNullable<AgentManagementProjection["agents"][number]["current_todo"]>,
  row: GoalDirectoryRow,
): PersonalAgentTodoItem {
  return {
    claimedBy: todo.claimed_by ?? null,
    done: todo.status === "done" || todo.status === "completed",
    priority: todo.priority ?? null,
    status: todo.status ?? null,
    taskClass: todo.task_class ?? null,
    text: compactShareText(todo.title, 112),
    todoId: todo.todo_id?.trim() || `${row.goal.id}:agent:${todo.claimed_by ?? "unknown"}:current`,
  };
}

function mergePersonalAgentTodos(
  projectedTodos: PersonalAgentTodoItem[],
  agentRows: AgentManagementRow[],
  row: GoalDirectoryRow,
): PersonalAgentTodoItem[] {
  const merged = new Map(projectedTodos.map((todo) => [todo.todoId, todo]));
  for (const agent of agentRows) {
    const current = agent.currentTodo;
    if (!current || current.goal_id !== row.goal.id) continue;
    const todo = personalAgentTodoFromProjection(current, row);
    if (!merged.has(todo.todoId)) merged.set(todo.todoId, todo);
  }
  return [...merged.values()];
}

/**
 * Projected completion facts for a Goal. The status payload reports completed
 * Todos as a count (project_asset.agent_todos.done) plus a bounded
 * recent-completed lane. Queue items may also include completed Todos.
 */
function personalAgentTodoFacts(row: GoalDirectoryRow): {
  doneTodoCount: number;
  nextTodoText: string | null;
  recentCompleted: PersonalAgentTodoItem[];
} {
  const assetTodos = row.queueItem?.project_asset?.agent_todos;
  const queueTodos = row.queueItem?.agent_todos;
  const items = personalAgentTodoItems(row);
  const doneFromCount = assetTodos?.advancement_done_count
    ?? queueTodos?.advancement_done_count
    ?? assetTodos?.done
    ?? queueTodos?.done_count
    ?? null;
  const doneFromItems = items.filter((todo) => todo.done && todo.status !== "deferred").length;
  const doneTodoCount = Math.max(doneFromCount ?? 0, doneFromItems);
  const seenTodoIds = new Set(
    items
      .map((todo) => todo.todo_id?.trim())
      .filter((value): value is string => Boolean(value)),
  );
  const recentCompleted = (assetTodos?.recent_completed_advancement_items ?? [])
    .filter((todo) => !todo.todo_id?.trim() || !seenTodoIds.has(todo.todo_id.trim()))
    .map((todo) => personalAgentTodoFromItem(todo, row));
  const firstOpen = items.find((todo) => !todo.done);
  const nextTodoText = cleanShareText(assetTodos?.next ?? "")
    || (firstOpen ? cleanShareText(firstOpen.title ?? "") || cleanShareText(firstOpen.text ?? "") : "")
    || null;
  return { doneTodoCount, nextTodoText, recentCompleted };
}

function personalVisiblePlanTodos(todos: PersonalAgentTodoItem[], limit = 4) {
  if (todos.length <= limit) {
    return todos;
  }
  const firstOpenIndex = todos.findIndex((todo) => !todo.done);
  if (firstOpenIndex < 0) {
    return todos.slice(-limit);
  }
  const start = Math.max(0, Math.min(firstOpenIndex - 2, todos.length - limit));
  return todos.slice(start, start + limit);
}

function personalDecisionPrimaryLabel(goal: PersonalGoalItem) {
  const signal = `${goal.needsYouTaskClass ?? ""} ${goal.needsYouActionKind ?? ""}`.toLowerCase();
  return /approve|approval|merge|release|submit|write|publish/.test(signal) ? "确认处理" : "回复 Agent";
}

function personalProposalStateLabel(state: PersonalProposalState) {
  const labels: Record<PersonalProposalState, string> = {
    candidate: "候选 Todo",
    previewing: "正在生成写入预览",
    ready: "预览已锁定，等待你批准",
    applying: "正在写入",
    approved: "已批准并写入",
    rejected: "已拒绝，未写入",
    cancelled: "已取消，未写入",
    stale: "状态已变化，需重新预览",
    error: "暂时无法处理",
  };
  return labels[state];
}

function isPersonalGoalTerminal(row: GoalDirectoryRow) {
  return [row.status, row.goal.status, row.latestRun?.classification, row.lifecyclePhase]
    .filter(Boolean)
    .some((value) => /(^|[_\s-])(done|complete|completed|finished|terminal|closed|success)([_\s-]|$)/i.test(value ?? ""));
}

function personalGoalRegistryFinding(payload: StatusPayload, row: GoalDirectoryRow) {
  return payload.global_registry?.findings?.find((finding) =>
    finding.severity === "high"
    && (finding.goal_id === row.goal.id || finding.goal_ids.includes(row.goal.id)),
  );
}

function personalGoalHasFailureStatus(row: GoalDirectoryRow) {
  return [row.status, row.goal.status, row.latestRun?.classification, row.lifecyclePhase]
    .filter(Boolean)
    .some((value) =>
      /(^|[_\s-])(failure|failed|error|broken|unhealthy|blocked[_\s-]?health|health[_\s-]?blocked)([_\s-]|$)/i
        .test(value ?? ""),
    );
}

function personalGoalNeedsRepair(payload: StatusPayload, row: GoalDirectoryRow) {
  const staleWarning = row.queueItem?.stale_latest_run_warning;
  return row.severity === "high"
    || Boolean(personalGoalRegistryFinding(payload, row))
    || Boolean(staleWarning?.requires_refresh_state || staleWarning?.severity === "high")
    || personalGoalHasFailureStatus(row);
}

function personalRepairText(payload: StatusPayload, row: GoalDirectoryRow) {
  const healthFinding = personalGoalRegistryFinding(payload, row);
  return row.queueItem?.stale_latest_run_warning?.recommended_action
    ?? row.queueItem?.stale_latest_run_warning?.reason
    ?? healthFinding?.recommended_action
    ?? healthFinding?.message
    ?? row.queueItem?.recommended_action
    ?? row.latestRun?.recommended_action
    ?? null;
}

function personalGoalHasPendingOperatorGate(row: GoalDirectoryRow) {
  const gate = row.latestRun?.operator_gate;
  const decision = gate?.decision?.trim().toLowerCase() ?? "";
  const terminalDecisions = new Set(["approve", "approved", "reject", "rejected", "defer", "deferred", "cancel", "cancelled"]);
  const projectedAction = [row.queueItem?.recommended_action, row.latestRun?.recommended_action]
    .filter(Boolean)
    .join(" ");
  const explicitUserWait = /(?:等待|需要)(?:用户|你|owner).{0,24}(?:批准|确认|授权|补充|选择|决定)|(?:批准|确认|授权).{0,16}(?:后|才能|方可)/i
    .test(projectedAction);
  return Boolean(gate && !terminalDecisions.has(decision))
    || (row.lifecyclePhase === "operator_gated" && !terminalDecisions.has(decision))
    || explicitUserWait;
}

function personalPendingOperatorGateText(row: GoalDirectoryRow, t: WorkspaceTranslate) {
  const gate = row.latestRun?.operator_gate;
  return projectionSentence(
    gate?.operator_question
      ?? gate?.reason_summary
      ?? gate?.follow_up
      ?? row.queueItem?.recommended_action
      ?? row.latestRun?.recommended_action,
    t,
    "projection.confirmAgentDecision",
  );
}

function personalGoalState(payload: StatusPayload, row: GoalDirectoryRow): PersonalGoalState {
  if (row.goal.activation_state === "stopped") {
    return "已停止";
  }
  const userTodos = getShareTodos(row, "user");
  const agentTodos = getShareTodos(row, "agent");
  const hasOpenUserTodo = Boolean(firstOpenTodo(userTodos));
  const hasOpenAgentTodo = Boolean(firstOpenTodo(agentTodos));
  if (["user_or_controller", "controller"].includes(row.waitingOn) || hasOpenUserTodo || personalGoalHasPendingOperatorGate(row)) {
    return "等你";
  }
  if (personalGoalNeedsRepair(payload, row)) {
    return "需修复";
  }
  if (row.waitingOn === "external_evidence") {
    return "等待条件";
  }
  // Eligibility and open Todos mean work is queued; execution comes from the session owner.
  if (quotaStateForShare(row) === "eligible" || hasOpenAgentTodo) {
    return "已安排";
  }
  if (isPersonalGoalTerminal(row)) {
    return "已完成";
  }
  return "安静运行";
}

function personalAgentSentence(payload: StatusPayload, row: GoalDirectoryRow, state: PersonalGoalState, t: WorkspaceTranslate) {
  if (state === "已停止") {
    return agentStatusSentence("stopped", t);
  }
  if (state === "需修复") {
    return projectionSentence(personalRepairText(payload, row), t, "projection.statusRefreshNeeded");
  }
  if (state === "等你") {
    return agentStatusSentence("needs_you", t);
  }
  if (state === "已安排") {
    const todoText = (getShareTodos(row, "agent")?.items ?? [])
      .filter((todo) => !todo.done)
      .flatMap((todo) => [todo.title, todo.text])
      .map((value) => cleanShareText(value))
      .find((value) => value !== "" && value !== "暂无");
    const progressText = [
      todoText,
      row.queueItem?.recommended_action,
      row.latestRun?.recommended_action,
    ].map((value) => cleanShareText(value))
      .find((value) => value !== "" && value !== "暂无");
    return progressText
      ? projectionSentence(progressText, t, "projection.agentWorkQueued")
      : agentStatusSentence("queued", t);
  }
  if (state === "等待条件") {
    return agentStatusSentence("waiting_external", t);
  }
  return agentStatusSentence("idle", t);
}

// The explicit status-only profile is a snapshot, not a keyword-driven answer.
function personalManagerSnapshot(model: PersonalHomeModel): PersonalManagerAnswer {
  if (model.goals.some((goal) => goal.activationState === "active" && goal.loadState)) return {
    text: "Goal 状态尚未全部加载。当前是只读状态模式；切换到 Agent 后可继续提问或执行任务。", lines: [],
  };
  return {
    text: "这是当前只读状态快照，未调用 Agent。切换到 Agent 后可继续提问或执行任务。",
    lines: model.goals.filter((goal) => goal.activationState === "active").slice(0, 3)
      .map((goal) => `${goal.title} · ${goal.state} · ${goal.agentSentence}`),
  };
}

function buildPersonalHomeModel(
  payload: StatusPayload,
  rows: GoalDirectoryRow[],
  t: WorkspaceTranslate,
  goalSubagentConfigurationEnabled = false,
): PersonalHomeModel {
  const rowById = new Map(rows.map((row) => [row.goal.id, row]));
  const stoppedGoalIds = new Set(
    payload.run_history.goals
      .filter((goal) => goal.activation_state === "stopped")
      .map((goal) => goal.id),
  );
  const usageById = shareUsageById(payload.usage_summary);
  const agentRows = buildAgentManagementRows(rows, payload.todo_index, payload.agent_management_projection);
  const attentionHistory = payload.attention_queue.items.flatMap((item, sourceOrder) => {
    if (stoppedGoalIds.has(item.goal_id)) return [];
    const blocking = ["user_or_controller", "controller"].includes(item.waiting_on);
    return (personalTodosForQueueItem(item, "user")?.items ?? [])
      .map((todo, todoOrder): PersonalNeedsYouItem & { sourceOrder: number; todoOrder: number; projectedDone: boolean } => ({
        projectedDone: todo.done,
        details: attentionDetails(todo),
        actionKind: todo.action_kind ?? null,
        blocking,
        goalId: item.goal_id,
        sourceOrder,
        taskClass: todo.task_class ?? null,
        text: personalTodoText(todo),
        todoId: todo.todo_id?.trim() || `${item.goal_id}:user:${todo.index}`,
        todoOrder,
        updatedAt: todo.updated_at ?? null,
      }));
  });
  const projectedUserTodos = attentionHistory.filter((todo) => !todo.projectedDone);
  const projectedGoalIds = new Set(projectedUserTodos.map((todo) => todo.goalId));
  const pendingOperatorGates = rows.flatMap((row, rowOrder) => {
    if (stoppedGoalIds.has(row.goal.id) || projectedGoalIds.has(row.goal.id) || !personalGoalHasPendingOperatorGate(row)) return [];
    return [{
      details: attentionDetails({ task_class: "user_gate", status: "open", note: row.latestRun?.operator_gate?.reason_summary }),
      actionKind: "gate.resolve",
      blocking: true,
      goalId: row.goal.id,
      sourceOrder: payload.attention_queue.items.length + rowOrder,
      taskClass: "user_gate",
      text: personalPendingOperatorGateText(row, t),
      todoId: `${row.goal.id}:operator-gate`,
      todoOrder: 0,
      updatedAt: row.latestRun?.operator_gate?.recorded_at ?? row.latestRun?.generated_at ?? null,
    } satisfies PersonalNeedsYouItem & { sourceOrder: number; todoOrder: number }];
  });
  const allUserTodos = [...projectedUserTodos, ...pendingOperatorGates].sort((left, right) =>
    Number(right.blocking) - Number(left.blocking)
    || left.sourceOrder - right.sourceOrder
    || left.todoOrder - right.todoOrder
  );
  const goals = payload.run_history.goals.flatMap((goal) => {
    if (goal.registry_member === false) {
      return [];
    }
    const row = rowById.get(goal.id);
    if (!row) {
      return [];
    }
    const state = personalGoalState(payload, row);
    const needsYouTodo = allUserTodos.find((todo) => todo.goalId === goal.id);
    const needsYou = needsYouTodo?.text ?? null;
    const agentTodoFacts = personalAgentTodoFacts(row);
    const registeredAgentIds = goal.coordination?.registered_agents ?? [];
    const registeredAgentSet = new Set(registeredAgentIds);
    const goalAgentRows = agentRows.filter(
      (agent) => agent.goalIds.includes(goal.id)
        && !/unassigned|unknown/i.test(agent.agentId)
        && (registeredAgentSet.size === 0 || registeredAgentSet.has(agent.agentId))
        && (agent.currentTodo?.goal_id === goal.id || agent.claimedTodos.some((todo) => todo.goalId === goal.id)),
    );
    const sortedGoalAgentRows = [...goalAgentRows].sort((left, right) =>
      (right.lastActivity ?? "").localeCompare(left.lastActivity ?? ""),
    );
    const knownAgentIds = new Set(sortedGoalAgentRows.map((agent) => agent.agentId));
    const goalAgentLanes = [
      ...sortedGoalAgentRows.map((agent) => ({
        agentId: agent.agentId,
        label: agent.agentId,
        lastActivityAt: agent.lastActivity,
        state: agent.status.label,
      })),
      ...registeredAgentIds
        .filter((agentId) => !knownAgentIds.has(agentId))
        .map((agentId) => ({ agentId, label: agentId, lastActivityAt: null, state: "registered" })),
    ];
    const agentRow = sortedGoalAgentRows[0];
    const goalAgentTodos = mergePersonalAgentTodos(personalAgentTodos(row), sortedGoalAgentRows, row);
    const nextSentence = [
      agentTodoFacts.nextTodoText,
      row.queueItem?.recommended_action,
      row.latestRun?.recommended_action,
      personalAgentSentence(payload, row, state, t),
    ].map((value) => projectionSentence(value, t))
      .find((value) => value !== "" && value !== "暂无") ?? t("projection.nextUpdatePending");
    return [{
      activationState: goal.activation_state,
      agentId: agentRow?.agentId ?? registeredAgentIds[0] ?? "codex",
      agentLaneCount: goalAgentLanes.length,
      agentLanes: goalAgentLanes,
      agentLabel: agentRow?.agentId,
      agentSentence: personalAgentSentence(payload, row, state, t),
      agentTodos: [...goalAgentTodos, ...agentTodoFacts.recentCompleted],
      boundHostSurfaces: Array.from(new Set((goal.coordination?.thread_agent_bindings ?? [])
        .flatMap((binding) => binding.host_surface ? [binding.host_surface] : []))),
      doneTodoCount: agentTodoFacts.doneTodoCount,
      acceptanceObservation: goal.acceptance_observation,
      goalId: goal.id,
      latestActivity: row.latestRun?.generated_at ?? "",
      needsYou,
      needsYouActionKind: needsYouTodo?.actionKind ?? null,
      needsYouBlocking: needsYouTodo?.blocking ?? false,
      needsYouTaskClass: needsYouTodo?.taskClass ?? null,
      needsYouTodoId: needsYouTodo?.todoId ?? null,
      nextSentence,
      hasRunObservation: Boolean(row.queueItem?.project_asset?.latest_validation
        || row.latestRun
        || payload.event_ledger_summary?.goals.some((item) => item.goal_id === goal.id)),
      state,
      ...(goalSubagentConfigurationEnabled ? {
        subagentExecution: {
          allowedDomains: goal.spawn_policy?.allowed_domains ?? [],
          domainCandidates: personalSubagentDomainCandidates(payload, row, goalAgentTodos),
          enabled: goal.spawn_policy?.mode === "multi_subagent"
            && goal.spawn_policy.spawn_allowed === true
            && goal.spawn_policy.max_children > 0,
          executionConfig: goal.spawn_policy?.execution_config,
          maxChildren: goal.spawn_policy?.max_children ?? 0,
          modelConfig: goal.spawn_policy?.model_config,
        },
      } : {}),
      title: personalGoalTitle(goal.id, goal.display_name),
      usage: (() => {
        const goalUsage = usageById.get(goal.id);
        return goalUsage ? {
          costUsd24h: goalUsage.cost_usd_24h,
          costUsd7d: goalUsage.cost_usd_7d,
          durationMs24h: goalUsage.duration_ms_24h,
          durationMs7d: goalUsage.duration_ms_7d,
          tokens24h: sumMeasuredUsage(goalUsage.input_tokens_24h, goalUsage.output_tokens_24h),
          tokens7d: sumMeasuredUsage(goalUsage.input_tokens_7d, goalUsage.output_tokens_7d),
        } : null;
      })(),
    }];
  });

  const systemHealthIssues: string[] = [];
  if (!payload.ok) {
    systemHealthIssues.push("状态载荷未标记为正常 (payload.ok === false)");
  }
  if (payload.contract && !payload.contract.ok) {
    const summary = payload.contract.summary;
    const detail = summary
      ? `${summary.errors} 项错误 / ${summary.warnings} 项警告`
      : (payload.contract.errors?.[0] || "请检查控制面契约");
    systemHealthIssues.push(`契约检查未通过: ${detail}`);
  }
  if (payload.global_registry) {
    if (!payload.global_registry.ok) {
      systemHealthIssues.push(`注册表状态异常: ${payload.global_registry.summary.high} 项高危`);
    }
    for (const finding of payload.global_registry.findings || []) {
      if (finding.severity === "high") {
        systemHealthIssues.push(`[${finding.kind}] ${finding.message}`);
      }
    }
  }
  const freshnessWarning = payload.decision_freshness_summary?.summary?.stale_count
    ? `${payload.decision_freshness_summary.summary.stale_count} 项决策状态已过期`
    : null;
  const isHealthy = systemHealthIssues.length === 0 && !freshnessWarning;
  const systemHealth: WorkspaceSystemHealth = {
    ok: isHealthy,
    summary: isHealthy
      ? "所有控制面契约与注册表检查均正常"
      : `发现 ${systemHealthIssues.length + (freshnessWarning ? 1 : 0)} 项系统健康关注点`,
    issues: systemHealthIssues,
    freshnessWarning,
  };

  return {
    blockingTodoCount: allUserTodos.filter((todo) => todo.blocking).length,
    goalNotifications: (payload.goal_channel_notification_projection?.goals ?? []).map((row) => ({
      goalId: row.goal_id,
      configured: row.configured,
      enabled: row.enabled,
      humanGateAutoNotifyEnabled: row.human_gate_auto_notify_enabled,
      lastNotifiedAt: row.last_notified_at ?? null,
      receiptCount: row.receipt_count,
      targetRef: row.target_ref ?? null,
    })),
    goals,
    openUserTodoCount: allUserTodos.length,
    systemHealth,
    attentionHistory: [...attentionHistory, ...pendingOperatorGates],
    userTodos: allUserTodos,
    visibleUserTodos: allUserTodos.slice(0, 5),
    workers: (payload.agent_management_projection?.agents ?? []).map((agent) => ({
      agentId: agent.agent_id,
      currentTodoGoalId: agent.current_todo?.goal_id ?? null,
      currentTodoText: agent.current_todo?.title ? compactShareText(agent.current_todo.title, 96) : null,
      lastActivityAt: agent.last_activity_at ?? null,
      state: agent.state ?? null,
    })),
  };
}
function PersonalGoalHome({
  goalArchiveLoadState,
  initialManagerChatOpen,
  isLoading,
  onGoalActivationStateChange,
  onGoalDeleted,
  onSelectGoal,
  onReconcileStatus,
  onRefresh,
  onRetryGoalArchive,
  payload,
  progress,
  rows,
  selectedGoalId,
  statusSourceControl,
  theme,
  toggleTheme,
}: {
  goalArchiveLoadState: WorkspaceGoalArchiveLoadState;
  initialManagerChatOpen: boolean;
  isLoading: boolean;
  onGoalActivationStateChange: (goalId: string, activationState: "active" | "stopped") => void;
  onGoalDeleted: (goalId: string) => void;
  onSelectGoal: (goalId: string) => void;
  onReconcileStatus: (options?: { invalidateGoalIds?: string[] }) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onRetryGoalArchive: () => void | Promise<void>;
  payload: StatusPayload;
  progress: WorkspaceProgress | null;
  rows: GoalDirectoryRow[];
  selectedGoalId: string;
  statusSourceControl: StatusSourceControl;
  theme: "light" | "dark";
  toggleTheme: () => void;
}) {
  const readOnly = statusSourceControl.activeSource.readOnly;
  const remoteGoalLifecycleHost = statusSourceControl.activeSource.kind === "ssh_tunnel"
    ? statusSourceControl.activeSource.hostAlias
    : undefined;
  const { t } = useWorkspaceI18n();
  const [runtimeAgents, setRuntimeAgents] = useState<Array<{
    adapter_kind: string;
    agent_id: string;
    available: boolean;
    display_name: string;
    interrupt: boolean;
    location?: string;
    resume: boolean;
    source?: string;
    streaming: boolean;
    tool_calls?: boolean;
    trust_scope?: string;
  }>>([]);
  const [goalSubagentConfigurationEnabled, setGoalSubagentConfigurationEnabled] = useState(false);
  const [managerRuntime, setManagerRuntime] = useState<ManagerRuntimeSessionReadback | null>(null);
  const [managerChannelBinding, setManagerChannelBinding] = useState<ManagerChannelBinding | null>(null);
  const model = useMemo(() => {
    const base = buildPersonalHomeModel(payload, rows, t, goalSubagentConfigurationEnabled);
    if (!progress) return base;
    const models = Object.values(progress.snapshots).map((snapshot) => buildPersonalHomeModel(
      snapshot, buildGoalDirectoryRows(snapshot.run_history.goals, snapshot.attention_queue.items),
      t,
      goalSubagentConfigurationEnabled,
    ));
    const loadedGoals = new Map(models.flatMap((item) => item.goals).map((goal) => [goal.goalId, goal]));
    const goals = base.goals.map((goal) => loadedGoals.get(goal.goalId) ?? {
      ...goal, loadError: progress.errors[goal.goalId], loadState: progress.errors[goal.goalId] ? "error" as const : "loading" as const,
      agentId: "", agentSentence: "", nextSentence: "", subagentExecution: undefined,
    });
    const userTodos = models.flatMap((item) => item.userTodos);
    const incomplete = goals.some((goal) => goal.activationState === "active" && goal.loadState);
    const issues = [...new Set(models.flatMap((item) => item.systemHealth?.issues ?? []))];
    return {
      ...base, goals, userTodos, attentionHistory: models.flatMap((item) => item.attentionHistory ?? item.userTodos), visibleUserTodos: userTodos.slice(0, 5),
      openUserTodoCount: userTodos.length, blockingTodoCount: userTodos.filter((todo) => todo.blocking).length,
      workers: [...new Map(models.flatMap((item) => item.workers ?? []).map((worker) => [worker.agentId, worker])).values()],
      goalNotifications: models.flatMap((item) => item.goalNotifications ?? []),
      systemHealth: incomplete || models.length === 0 ? undefined : {
        ok: models.every((item) => item.systemHealth?.ok), issues,
        summary: issues.length ? `发现 ${issues.length} 项系统健康关注点` : "状态检查已完成",
        freshnessWarning: models.map((item) => item.systemHealth?.freshnessWarning).filter(Boolean).join("；") || null,
      },
    };
  }, [payload, rows, progress, goalSubagentConfigurationEnabled, t]);
  const selectedGoal = model.goals.find((goal) => goal.goalId === selectedGoalId) ?? null;
  const selectedPayload = progress?.snapshots[selectedGoalId] ?? payload;
  const [periodicReport, setPeriodicReport] = useState<PeriodicReportProjection | null>(null);
  const [periodicReportError, setPeriodicReportError] = useState<string | null>(null);
  const [periodicReportLoading, setPeriodicReportLoading] = useState(false);
  const sessionDiscoveryKey = model.goals.some((goal) => goal.activationState === "active" && goal.loadState === "loading")
    ? "loading" : model.goals.map((goal) => `${goal.goalId}:${goal.agentId}`).join("|");
  const contextId = selectedGoal?.goalId ?? "manager";
  const managerSummary = model.goals.some((goal) => goal.activationState === "active" && goal.loadState)
    ? "Goal 状态正在逐个更新，当前统计尚不完整。"
    : (model.systemHealth ? !model.systemHealth.ok : !payload.ok)
    ? "LoopX 当前存在状态问题，可以打开运行详情查看原因。"
    : model.openUserTodoCount > 0
      ? `你有 ${model.openUserTodoCount} 项需要处理，其中 ${model.blockingTodoCount} 项正在阻塞 Agent。`
      : "暂时没有需要你处理的事项，Agent 会按当前计划继续推进。";
  const discoveredAgents: PersonalAgentOption[] = runtimeAgents.length > 0
    ? runtimeAgents.map((agent) => ({
        agentId: agent.agent_id,
        adapterKind: agent.adapter_kind,
        available: agent.available,
        capability: personalAgentCapability(agent.agent_id, agent.adapter_kind),
        interrupt: agent.interrupt,
        label: agent.display_name,
        location: agent.location,
        resume: agent.resume,
        source: agent.source,
        statusLabel: agent.available ? "可用" : "需要配置",
        streaming: agent.streaming,
        toolCalls: agent.tool_calls,
        trustScope: agent.trust_scope,
      }))
    : [{
        agentId: "codex",
        available: true,
        capability: personalAgentCapability("codex"),
        label: "Codex",
        statusLabel: "正在检测",
      }];
  const agentOptions = [
    ...discoveredAgents,
    {
      agentId: "status-only",
      available: true,
      capability: "不调用模型",
      adapterKind: "status_projection",
      interrupt: false,
      label: "仅查状态",
      resume: true,
      statusLabel: "只读",
      streaming: false,
      toolCalls: false,
      trustScope: "read_only",
    },
  ];
  const defaultAgentId = discoveredAgents.find((agent) => agent.label === "Codex" && agent.available)?.agentId
    ?? discoveredAgents.find((agent) => agent.available)?.agentId
    ?? "status-only";
  // The manager channel answers on the executor this machine declares for the
  // steward, and the client is expected to send no endpoint until the operator
  // picks one. The picker therefore has to show that same resolution: an
  // executor merely discovered on this machine is not a reason to present
  // itself as the steward's runtime, or the header chip, the composer and the
  // answer would tell three different stories.
  const declaredStewardEndpoint = managerChannelBinding?.executor_endpoint?.trim() ?? "";
  const stewardExecutorAgentId = declaredStewardEndpoint
    ? discoveredAgents.find((agent) => agent.agentId === declaredStewardEndpoint)?.agentId
    : undefined;
  const agentDefaultForContext = (targetContextId: string) =>
    targetContextId === "manager" ? stewardExecutorAgentId ?? defaultAgentId : defaultAgentId;
  const [selectedAgents, setSelectedAgents] = useState<Record<string, string>>(readPersonalAgentSelections);
  const selectedAgentId = selectedAgents[contextId] ?? agentDefaultForContext(contextId);
  const selectedAgent = selectAvailableChatAgent(agentOptions, selectedAgentId, defaultAgentId);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"chat" | "goals">("chat");
  const [managerInput, setManagerInput] = useState("");
  const [messagesByContext, setMessagesByContext] = useState<Record<string, PersonalManagerMessage[]>>({});
  const [proposalsByContext, setProposalsByContext] = useState<Record<string, PersonalProposalCard[]>>({});
  const [sendingContextId, setSendingContextId] = useState<string | null>(null);
  const [runtimeBindings, setRuntimeBindings] = useState<Record<string, PersonalRuntimeBinding>>({});
  const [executionSessions, setExecutionSessions] = useState<ChatSessionSummary[]>([]);
  const [executionDiscoveryError, setExecutionDiscoveryError] = useState<"partial" | "offline" | null>(null);
  const [executionSessionSnapshots, setExecutionSessionSnapshots] = useState<Record<string, ChatSessionSnapshot>>({});
  // undefined: not read yet; null: the session owner could not be read.
  const [goalSessionFacts, setGoalSessionFacts] = useState<ChatSessionSummary[] | null | undefined>(undefined);
  const managerMessageId = useRef(1);
  const proposalId = useRef(1);
  const sessionIds = useRef(new Map<string, string>());
  const newSessionRequired = useRef(new Set<string>());
  const activeTurnIds = useRef(new Map<string, string>());
  const streamControllers = useRef(new Map<string, AbortController>());
  const interruptedTurnIds = useRef(new Set<string>());
  const recoveringTurnKeys = useRef(new Set<string>());
  const agentMenuRef = useRef<HTMLDivElement>(null);
  const agentTriggerRef = useRef<HTMLButtonElement>(null);
  const detailsCloseRef = useRef<HTMLButtonElement>(null);
  const detailsTriggerRef = useRef<HTMLButtonElement>(null);
  const managerInputRef = useRef<HTMLInputElement>(null);
  const managerQuickPrompts = ["我现在该做什么？", "哪些 Goal 在等我？", "Agent 在做什么？"];
  const contextMessages = messagesByContext[contextId] ?? [];
  const contextProposals = proposalsByContext[contextId] ?? [];

  // Who is speaking in the transcript. The manager channel answers as the LoopX
  // Manager: the executor that served the turn (and the model behind it) belongs
  // to the machine-capability chip, so a person reading an answer is never told
  // the CLI brand of whatever host happened to run it. Goal channels still name
  // the Goal's own Agent.
  const answerIdentityLabel = (targetContextId: string, goalFallback: string) =>
    targetContextId === "manager" ? t("header.manager") : goalFallback;
  const goalUserTodos = selectedGoal
    ? model.userTodos.filter((todo) => todo.goalId === selectedGoal.goalId)
    : model.userTodos;
  const goalAgentTodos = selectedGoal?.agentTodos ?? [];
  const visiblePlanTodos = personalVisiblePlanTodos(goalAgentTodos, selectedGoal?.needsYou ? 3 : 4);
  const completedAgentTodoCount = goalAgentTodos.filter((todo) => todo.done).length;
  const goalProgressLabel = goalAgentTodos.length > 0
    ? `${completedAgentTodoCount}/${goalAgentTodos.length}`
    : "暂无计划";
  const questionModel = selectedGoal
    ? {
        ...model,
        blockingTodoCount: goalUserTodos.filter((todo) => todo.blocking).length,
        goals: [selectedGoal],
        openUserTodoCount: goalUserTodos.length,
        userTodos: goalUserTodos,
        visibleUserTodos: goalUserTodos,
      }
    : model;

  useEffect(() => {
    const resolved = resolveLocalStatusUrl(
      statusSourceControl.activeSource.statusUrl,
      window.location.href,
    );
    const urls = resolved.source ? periodicReportApiUrls(selectedPayload, resolved.source) : null;
    if (!selectedGoal || !urls?.indexUrl || !urls.detailUrl) {
      setPeriodicReport(null);
      setPeriodicReportError(null);
      setPeriodicReportLoading(false);
      return;
    }
    const { detailUrl, indexUrl } = urls;
    let cancelled = false;
    setPeriodicReport(null);
    setPeriodicReportError(null);
    setPeriodicReportLoading(true);
    void fetchPeriodicReportIndex(indexUrl, selectedGoal.goalId)
      .then(async (index) => {
        const ref = index.items[0]?.detail_ref;
        return ref ? fetchPeriodicReportProjection(detailUrl, ref) : null;
      })
      .then((report) => {
        if (!cancelled) setPeriodicReport(report);
      })
      .catch((error) => {
        if (!cancelled) setPeriodicReportError(formatStatusError(error));
      })
      .finally(() => {
        if (!cancelled) setPeriodicReportLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    selectedPayload,
    selectedGoal?.goalId,
    statusSourceControl.activeSource.statusUrl,
  ]);

  // Worker returns are transcript messages, not new model turns. Keep an open
  // conversation current without replacing in-flight user/agent text.
  const conversationReturnSessionId = runtimeBindings[contextId]?.sessionId;
  useEffect(() => {
    if (readOnly || !conversationReturnSessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const receive = async () => {
      try {
        const snapshot = await fetchChatSession(conversationReturnSessionId);
        if (cancelled) return;
        const replies = snapshot.messages.filter((row) => row.origin === "manager_followup");
        setMessagesByContext((current) => {
          const previous = current[contextId] ?? [];
          const seen = new Set(previous.map((row) => row.sourceMessageId));
          const fresh = replies.filter((row) => !seen.has(row.message_id));
          const deliveryByMessage = new Map(
            replies.map((row) => [row.message_id, row.return_delivery]),
          );
          const byTurn = new Map(snapshot.messages.filter((row) => row.role !== "user" && row.origin !== "manager_followup").map((row) => [row.turn_id, row]));
          const collaborationByMessage = new Map(snapshot.messages.map((row) => [row.message_id, row.collaboration]));
          let deliveryChanged = false;
          const updated = previous.map((row) => {
            const delivery = row.sourceMessageId
              ? deliveryByMessage.get(row.sourceMessageId)
              : undefined;
            const source = row.sourceTurnId ? byTurn.get(row.sourceTurnId) : undefined;
            const collaboration = row.sourceMessageId ? collaborationByMessage.get(row.sourceMessageId) : source?.collaboration;
            if (JSON.stringify(delivery) === JSON.stringify(row.returnDelivery) && JSON.stringify(collaboration) === JSON.stringify(row.collaboration)) return row;
            deliveryChanged = true;
            return { ...row, sourceMessageId: row.sourceMessageId ?? source?.message_id,
              sourceSessionId: row.sourceSessionId ?? (source?.message_id ? conversationReturnSessionId : undefined),
              returnDelivery: delivery, collaboration };
          });
          if (!fresh.length && !deliveryChanged) return current;
          return { ...current, [contextId]: [...updated, ...fresh.map((row) => ({
            id: managerMessageId.current++, sourceMessageId: row.message_id,
            sourceSessionId: conversationReturnSessionId,
            role: "assistant" as const,
            agentLabel: "协作回执",
            sourceLabel: "协作回执", text: visibleAgentMessage(row.text), lines: [],
            returnDelivery: row.return_delivery,
          }))] };
        });
      } catch {
        // The durable transcript is retried after reconnection; no model replay.
      } finally {
        if (!cancelled) timer = setTimeout(receive, 3000);
      }
    };
    void receive();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [readOnly, conversationReturnSessionId, contextId, selectedAgent.label]);

  function recordRuntimeBinding(targetContextId: string, binding: PersonalRuntimeBinding | null) {
    setRuntimeBindings((current) => {
      if (binding === null) {
        const next = { ...current };
        delete next[targetContextId];
        return next;
      }
      return { ...current, [targetContextId]: binding };
    });
  }

  useEffect(() => {
    if (readOnly) {
      setRuntimeAgents([]);
      setGoalSubagentConfigurationEnabled(false);
      setManagerRuntime(null);
      setManagerChannelBinding(null);
      return;
    }
    let cancelled = false;
    void fetchChatCapabilities()
      .then((capabilities) => {
        if (!cancelled) {
          setRuntimeAgents(capabilities.adapters ?? []);
          const runtime = capabilities.manager?.runtime;
          setManagerChannelBinding(capabilities.manager?.channel_binding ?? null);
          setManagerRuntime(runtime ? {
            schema_version: "manager_runtime_session_readback_v0",
            runtime_profile: runtime.runtime_profile,
            configuration_revision: runtime.configuration_revision,
            status: runtime.status,
            sandbox: runtime.sandbox,
            standing_grant: runtime.standing_grant,
            tool_classes: runtime.tool_classes,
          } : null);
          setGoalSubagentConfigurationEnabled(
            capabilities.goal_subagent_configuration === "preview_locked",
          );
        }
      })
      .catch(() => {
        if (!cancelled) setGoalSubagentConfigurationEnabled(false);
        // The Codex fallback stays visible while the local control plane reconnects.
      });
    return () => {
      cancelled = true;
    };
  }, [readOnly]);

  useEffect(() => {
    try {
      window.localStorage.setItem(personalAgentSelectionStorageKey, JSON.stringify(selectedAgents));
    } catch {
      // The selector remains usable when browser storage is unavailable.
    }
  }, [selectedAgents]);

  useEffect(() => {
    if (readOnly) return;
    if (!selectedAgent.available) return;
    const targetContextId = contextId;
    const sessionKey = `${targetContextId}:${selectedAgent.agentId}`;
    const contextKind = selectedGoal ? "goal" : "manager";
    const channelId = selectedGoal ? `goal.${selectedGoal.goalId}` : "manager";
    let cancelled = false;
    let recoveryController: AbortController | null = null;
    let latestDiscoveredSessionId: string | null = null;
    void (async () => {
      try {
        const history = await fetchChatHistory({
          agentId: contextKind === "manager" ? undefined : selectedAgent.agentId,
          channelId,
          goalId: selectedGoal?.goalId,
        });
        if (cancelled) return;
        setMessagesByContext((current) => {
          if ((current[targetContextId]?.length ?? 0) > 0) return current;
          return {
            ...current,
            [targetContextId]: history.messages.map((message) => ({
              sourceMessageId: message.message_id,
              sourceSessionId: message.session_id,
              agentLabel: message.role === "user"
                ? undefined
                : message.origin === "manager_followup"
                  ? "协作回执"
                : answerIdentityLabel(targetContextId, selectedAgent.label),
              attachments: workspaceImageAttachments(message.attachments),
              id: managerMessageId.current++,
              lines: [],
              role: message.role === "user" ? "user" : "assistant",
              returnDelivery: message.return_delivery,
              collaboration: message.collaboration,
              sourceLabel: message.role === "user"
                ? undefined
                : message.role === "error"
                  ? "本地会话记录"
                  : targetContextId === "manager"
                    ? `恢复的${t("header.manager")}会话`
                    : `恢复的 ${selectedAgent.label} 会话`,
              text: message.role === "user" ? message.text : visibleAgentMessage(message.text),
            })),
          };
        });
        if (selectedAgent.agentId === "status-only") return;
        const latest = history.sessions[0];
        latestDiscoveredSessionId = latest?.session_id ?? null;
        if (latest && !latest.resumable) {
          newSessionRequired.current.add(sessionKey);
          recordRuntimeBinding(targetContextId, {
            agentId: selectedAgent.agentId,
            resumable: false,
            sessionId: latest.session_id,
            status: "resume_failed",
          });
          return;
        }
        const sessionGoalId = contextKind === "manager" ? "" : selectedGoal?.goalId ?? "";
        if (contextKind === "goal" && !sessionGoalId) return;
        // The steward channel owns its executor default; only a pick the owner
        // actually made for this context is sent.
        const sessionEndpoint =
          contextKind === "manager" ? selectedAgents[targetContextId] : selectedAgent.agentId;
        const created = await createChatSession(
          sessionGoalId,
          sessionEndpoint,
          "resume_latest",
          contextKind,
        );
        if (cancelled) return;
        if (contextKind === "manager" && created.session.manager_runtime) {
          setManagerRuntime(created.session.manager_runtime);
        }
        sessionIds.current.set(sessionKey, created.session_id);
        const activeSnapshot = history.snapshots.find(
          (snapshot) => snapshot.session.session_id === created.session_id,
        );
        const activeTurnId = activeSnapshot?.session.active_turn_id ?? "";
        recordRuntimeBinding(targetContextId, {
          agentId: created.agent_id || selectedAgent.agentId,
          resumable: true,
          sessionId: created.session_id,
          status: activeTurnId ? "running" : "ready",
          turnId: activeTurnId || undefined,
        });
        newSessionRequired.current.delete(sessionKey);
        if (!activeTurnId) return;
        const recoveryKey = `${created.session_id}:${activeTurnId}`;
        if (recoveringTurnKeys.current.has(recoveryKey)) return;
        recoveringTurnKeys.current.add(recoveryKey);
        activeTurnIds.current.set(targetContextId, activeTurnId);
        recordRuntimeBinding(targetContextId, {
          agentId: selectedAgent.agentId,
          resumable: true,
          sessionId: created.session_id,
          status: "running",
          turnId: activeTurnId,
        });
        setSendingContextId(targetContextId);
        recoveryController = new AbortController();
        streamControllers.current.set(targetContextId, recoveryController);
        let streamedText = "";
        const streamingMessageId = appendManagerAssistantMessage(targetContextId, {
          activity: ["正在恢复进行中的 Agent 回合"],
          sourceTurnId: activeTurnId,
          agentLabel: answerIdentityLabel(targetContextId, selectedAgent.label),
          lines: [],
          pending: true,
          sourceLabel: targetContextId === "manager"
            ? `恢复的${t("header.manager")}会话`
            : `恢复的 ${selectedAgent.label} 会话`,
          text: "",
        });
        try {
          const streamed = await resumeChatTurnStreaming(created.session_id, activeTurnId, {
            signal: recoveryController.signal,
            onDelta: (delta) => {
              streamedText += delta;
              updateManagerAssistantMessage(targetContextId, streamingMessageId, {
                text: streamedText,
              });
            },
            onActivity: (label) => {
              setMessagesByContext((messages) => ({
                ...messages,
                [targetContextId]: (messages[targetContextId] ?? []).map((message) =>
                  message.id !== streamingMessageId
                    ? message
                    : {
                        ...message,
                        activity: message.activity?.at(-1) === label ? message.activity : [...(message.activity ?? []), label].slice(-6),
                      }
                ),
              }));
            },
          });
          if (cancelled) return;
          updateManagerAssistantMessage(targetContextId, streamingMessageId, {
            lines: streamed.response.gate
              ? [streamed.response.gate.summary, streamed.response.gate.next_action].filter(Boolean).slice(0, 2)
              : [],
            pending: false,
            text: streamed.response.message
              || streamedText.trim()
              || `${answerIdentityLabel(targetContextId, selectedAgent.label)} 已完成分析。`,
          });
          const recoveryGoal = model.goals.find((goal) => goal.goalId === activeSnapshot?.session.goal_id)
            ?? selectedGoal
            ?? model.goals[0]
            ?? null;
          if (recoveryGoal && streamed.response.proposals.length > 0) {
            // A recovered Turn may carry the steward's admitted team plan beside
            // its todo proposals. The plan is not a candidate Todo: the manager
            // channel already stored it as the typed card the owner confirms, so
            // only the todos become cards here.
            const cards = streamed.response.proposals.filter(isTodoProposal).map((proposal) => ({
              goalId: recoveryGoal.goalId,
              id: proposalId.current++,
              previewId: null,
              proposal,
              receiptLabel: null,
              state: "candidate" as const,
              statusMessage: null,
            }));
            if (cards.length > 0) {
              setProposalsByContext((current) => ({
                ...current,
                [targetContextId]: [...(current[targetContextId] ?? []), ...cards],
              }));
            }
          }
        } catch (error) {
          if (cancelled) return;
          const interrupted = interruptedTurnIds.current.delete(activeTurnId)
            || (error instanceof ChatApiError && error.payload.error_code === "turn_interrupted");
          updateManagerAssistantMessage(targetContextId, streamingMessageId, {
            lines: [],
            pending: false,
            reconnect: error instanceof ChatApiError && error.payload.reconnectable === true,
            sourceLabel: "LoopX Chat 本地后端",
            text: interrupted ? [streamedText.trim(), "已中断。你可以在当前会话继续发送消息。"].filter(Boolean).join("\n\n") : error instanceof Error ? error.message : "无法恢复进行中的 Agent 回合。",
          });
        } finally {
          recoveringTurnKeys.current.delete(recoveryKey);
          if (activeTurnIds.current.get(targetContextId) === activeTurnId) {
            activeTurnIds.current.delete(targetContextId);
          }
          recordRuntimeBinding(targetContextId, {
            agentId: selectedAgent.agentId,
            resumable: true,
            sessionId: created.session_id,
            status: "ready",
          });
          if (streamControllers.current.get(targetContextId) === recoveryController) {
            streamControllers.current.delete(targetContextId);
          }
          if (!cancelled) {
            setSendingContextId((current) => current === targetContextId ? null : current);
          }
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ChatApiError && error.payload.error_code === "resume_failed") {
          newSessionRequired.current.add(sessionKey);
          if (latestDiscoveredSessionId) {
            recordRuntimeBinding(targetContextId, {
              agentId: selectedAgent.agentId,
              resumable: false,
              sessionId: latestDiscoveredSessionId,
              status: "resume_failed",
            });
          }
        }
      }
    })();
    return () => {
      cancelled = true;
      recoveryController?.abort();
    };
  }, [contextId, model.goals[0]?.goalId, readOnly, selectedGoal?.goalId, selectedAgent.agentId, selectedAgent.available, selectedAgent.label, selectedAgents]);

  useEffect(() => {
    if (readOnly) return;
    if (selectedGoal || model.goals.length === 0 || sessionDiscoveryKey === "loading") return;
    let cancelled = false;
    void Promise.all(model.goals.filter((goal) => !goal.loadState).map(async (goal) => {
      const listed = await fetchChatSessions({
        agentId: goal.agentId,
        channelId: `goal.${goal.goalId}`,
        goalId: goal.goalId,
      });
      return { goalId: goal.goalId, session: listed.sessions[0] ?? null };
    })).then((rows) => {
      if (cancelled) return;
      setRuntimeBindings((current) => {
        const next = { ...current };
        for (const row of rows) {
          if (!row.session) continue;
          next[row.goalId] = {
            agentId: row.session.agent_id,
            resumable: row.session.resumable,
            sessionId: row.session.session_id,
            status: row.session.active_turn_id ? "running" : row.session.status,
            turnId: row.session.active_turn_id ?? undefined,
          };
        }
        return next;
      });
    }).catch(() => {
      // The read-only status projection remains available while Session discovery reconnects.
    });
    return () => {
      cancelled = true;
    };
  }, [readOnly, sessionDiscoveryKey, selectedGoal?.goalId]);

  useEffect(() => {
    if (readOnly) {
      setGoalSessionFacts(null);
      return;
    }
    let cancelled = false;
    let timer = 0;
    let failures = 0;
    const read = async () => {
      if (cancelled) return;
      if (!document.hidden) {
        try {
          const listed = await fetchChatSessions({});
          if (cancelled) return;
          failures = 0;
          setGoalSessionFacts(listed.sessions);
        } catch {
          failures += 1;
          if (!cancelled) setGoalSessionFacts(null);
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void read(), document.hidden ? 20_000 : Math.min(60_000, 8_000 * 2 ** Math.min(failures, 3)));
    };
    void read();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [readOnly]);

  useEffect(() => {
    setExecutionDiscoveryError(null);
    if (readOnly) {
      setExecutionSessions([]);
      setExecutionSessionSnapshots({});
      return;
    }
    if (!selectedGoal) {
      setExecutionSessions([]);
      setExecutionSessionSnapshots({});
      return;
    }
    let cancelled = false;
    let timer = 0;
    let failures = 0;
    setExecutionSessions([]);
    setExecutionSessionSnapshots({});
    const discover = async () => {
      if (cancelled) return;
      if (document.hidden) {
        timer = window.setTimeout(() => void discover(), 10_000);
        return;
      }
      try {
        const listed = await fetchChatSessions({ goalId: selectedGoal.goalId });
        if (!cancelled) {
          const taskSessions = listed.sessions.filter((session) => session.channel_id?.startsWith("task."));
          setExecutionSessions(taskSessions);
          const snapshots = await Promise.allSettled(taskSessions.map((session) => fetchChatSession(session.session_id)));
          if (!cancelled) {
            const partialFailure = snapshots.some((result) => result.status === "rejected");
            failures = partialFailure ? failures + 1 : 0;
            setExecutionDiscoveryError(partialFailure ? "partial" : null);
            setExecutionSessionSnapshots(Object.fromEntries(snapshots.flatMap((result, index) => {
              if (result.status !== "fulfilled") return [];
              return [[taskSessions[index].session_id, result.value]];
            })));
          }
        }
      } catch {
        failures += 1;
        if (!cancelled) setExecutionDiscoveryError("offline");
      }
      if (!cancelled) timer = window.setTimeout(() => void discover(), Math.min(30_000, 2_000 * 2 ** Math.min(failures, 4)));
    };
    void discover();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [readOnly, selectedGoal?.goalId]);

  useEffect(() => {
    if (!agentMenuOpen) {
      return;
    }
    const focusHandle = window.requestAnimationFrame(() => {
      agentMenuRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
    });
    return () => {
      window.cancelAnimationFrame(focusHandle);
      agentTriggerRef.current?.focus();
    };
  }, [agentMenuOpen]);

  useEffect(() => {
    if (!detailsOpen) {
      return;
    }
    const focusHandle = window.requestAnimationFrame(() => detailsCloseRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(focusHandle);
      detailsTriggerRef.current?.focus();
    };
  }, [detailsOpen]);

  useEffect(() => {
    if (!agentMenuOpen && !detailsOpen) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAgentMenuOpen(false);
        setDetailsOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [agentMenuOpen, detailsOpen]);

  function appendManagerAssistantMessage(
    targetContextId: string,
    message: Omit<PersonalManagerMessage, "id" | "role">,
  ) {
    const id = managerMessageId.current++;
    setMessagesByContext((messages) => ({
      ...messages,
      [targetContextId]: [
        ...(messages[targetContextId] ?? []),
        { ...message, id, role: "assistant" },
      ],
    }));
    return id;
  }

  function updateManagerAssistantMessage(
    targetContextId: string,
    messageId: number,
    update: Partial<Omit<PersonalManagerMessage, "id" | "role">>,
  ) {
    setMessagesByContext((messages) => ({
      ...messages,
      [targetContextId]: (messages[targetContextId] ?? []).map((message) =>
        message.id === messageId ? { ...message, ...update } : message
      ),
    }));
  }

  function updatePersonalProposal(
    targetContextId: string,
    targetProposalId: number,
    update: Partial<PersonalProposalCard>,
  ) {
    setProposalsByContext((current) => ({
      ...current,
      [targetContextId]: (current[targetContextId] ?? []).map((proposal) =>
        proposal.id === targetProposalId ? { ...proposal, ...update } : proposal
      ),
    }));
  }

  async function prepareGoalConversation(goalId: string, agentId: string) {
    const key = `${goalId}:${agentId}`;
    const existing = sessionIds.current.get(key);
    if (existing) return existing;
    const session = await createChatSession(goalId, agentId, newSessionRequired.current.has(key) ? "new" : "resume_latest", "goal");
    sessionIds.current.set(key, session.session_id);
    newSessionRequired.current.delete(key);
    recordRuntimeBinding(goalId, {agentId, resumable: true, sessionId: session.session_id, status: session.session.status});
    return session.session_id;
  }

  async function sendManagerQuestion(rawQuestion: string, route?: { agentId?: string; goalId?: string | null; attachments?: WorkspaceImageAttachment[]; loopxMode?: {operation: "start" | "resume"; settings?: LoopXModeSettings} }) {
    const question = rawQuestion.trim();
    if (!question) {
      return;
    }
    const targetContextId = route && "goalId" in route
      ? route.goalId ?? "manager"
      : contextId;
    const targetGoal = targetContextId === "manager"
      ? null
      : model.goals.find((goal) => goal.goalId === targetContextId) ?? null;
    const selectedRoute = route?.agentId
      ? selectAvailableChatAgent(agentOptions, route.agentId, defaultAgentId)
      : selectedAgent;
    const targetQuestionModel = targetContextId === "manager"
      ? model
      : targetGoal
      ? {
          ...model,
          blockingTodoCount: model.userTodos.filter((todo) => todo.goalId === targetGoal.goalId && todo.blocking).length,
          goals: [targetGoal],
          openUserTodoCount: model.userTodos.filter((todo) => todo.goalId === targetGoal.goalId).length,
          userTodos: model.userTodos.filter((todo) => todo.goalId === targetGoal.goalId),
          visibleUserTodos: model.userTodos.filter((todo) => todo.goalId === targetGoal.goalId),
        }
      : model;
    const userMessageId = managerMessageId.current++;
    setMessagesByContext((messages) => ({
      ...messages,
      [targetContextId]: [
        ...(messages[targetContextId] ?? []),
        { attachments: route?.attachments, id: userMessageId, lines: [], role: "user", text: question },
      ],
    }));
    setManagerInput("");
    setSendingContextId(targetContextId);

    if (selectedRoute.agentId === "status-only" || (!targetGoal && targetContextId !== "manager")) {
      const answer = personalManagerSnapshot(targetQuestionModel);
      const usesStatusOnlyRoute = selectedRoute.agentId === "status-only";
      appendManagerAssistantMessage(targetContextId, {
        agentLabel: usesStatusOnlyRoute ? "仅查状态" : "LoopX 管家",
        lines: answer.lines.slice(0, 3),
        sourceLabel: usesStatusOnlyRoute ? "LoopX 状态投影 · 仅查状态" : "LoopX 状态投影",
        text: answer.text,
      });
      void recordProjectionExchange({
        answer: [answer.text, ...answer.lines.slice(0, 3)].filter(Boolean).join("\n"),
        contextKind: targetContextId === "manager" ? "manager" : "goal",
        goalId: targetContextId === "manager" ? undefined : targetContextId,
        question,
      }).catch(() => {
        // The current projection answer remains visible when local history persistence is unavailable.
      });
      setSendingContextId(null);
      return;
    }

    const sessionKey = `${targetContextId}:${selectedRoute.agentId}`;
    let streamingMessageId: number | null = null;
    let submittedTurnId: string | undefined;
    let streamedText = "";
    try {
      let sessionId = targetContextId === "manager" ? sessionIds.current.get(sessionKey) : await prepareGoalConversation(targetContextId, selectedRoute.agentId);
      if (!sessionId) {
        const mode = newSessionRequired.current.has(sessionKey) ? "new" : "resume_latest";
        const sessionEndpoint =
          targetContextId === "manager" ? selectedAgents[targetContextId] : selectedRoute.agentId;
        const session = await createChatSession(
          targetContextId === "manager" ? "" : targetGoal!.goalId,
          sessionEndpoint,
          mode,
          targetContextId === "manager" ? "manager" : "goal",
        );
        if (targetContextId === "manager" && session.session.manager_runtime) {
          setManagerRuntime(session.session.manager_runtime);
        }
        sessionId = session.session_id;
        sessionIds.current.set(sessionKey, sessionId);
        recordRuntimeBinding(targetContextId, {
          agentId: session.agent_id || selectedRoute.agentId,
          resumable: true,
          sessionId,
          status: "ready",
        });
        newSessionRequired.current.delete(sessionKey);
      }
      streamingMessageId = appendManagerAssistantMessage(targetContextId, {
        activity: [targetContextId === "manager" ? "正在连接管家" : "正在连接 Agent"],
        agentLabel: answerIdentityLabel(targetContextId, selectedRoute.label),
        lines: [],
        pending: true,
        sourceLabel: targetContextId !== "manager"
          ? `${selectedRoute.label} Agent · ${personalGoalTitle(targetGoal!.goalId)}`
          : `${t("header.manager")} · 跨 Goal`,
        text: "",
      });
      const streamOptions = {
        attachments: route?.attachments,
        signal: (() => {
          const controller = new AbortController();
          streamControllers.current.set(targetContextId, controller);
          return controller.signal;
        })(),
        onDelta: (delta: string) => {
          streamedText += delta;
          if (streamingMessageId !== null) {
            updateManagerAssistantMessage(targetContextId, streamingMessageId, {
              text: streamedText,
            });
          }
        },
        onActivity: (label: string) => {
          if (streamingMessageId === null) return;
          setMessagesByContext((messages) => ({
            ...messages,
            [targetContextId]: (messages[targetContextId] ?? []).map((message) =>
              message.id !== streamingMessageId
                ? message
                : {
                    ...message,
                    activity: message.activity?.at(-1) === label ? message.activity : [...(message.activity ?? []), label].slice(-6),
                  }
            ),
          }));
        },
        onPhase: (_phase: string, turnId: string) => {
          submittedTurnId = turnId;
          if (streamingMessageId !== null) updateManagerAssistantMessage(targetContextId, streamingMessageId, { sourceTurnId: turnId });
          activeTurnIds.current.set(targetContextId, turnId);
          recordRuntimeBinding(targetContextId, {
            agentId: selectedRoute.agentId,
            resumable: true,
            sessionId,
            status: "running",
            turnId,
          });
        },
      };
      let streamed;
      if (route?.loopxMode) {
        const accepted = await updateLoopXMode(sessionId, route.loopxMode.operation, route.loopxMode.settings);
        if (!accepted.turn_id) throw new Error("LoopX execution returned no turn identity");
        streamOptions.onPhase("turn.accepted", accepted.turn_id);
        streamed = await resumeChatTurnStreaming(sessionId, accepted.turn_id, streamOptions);
      } else {
        streamed = await sendChatTurnStreaming(sessionId, question, streamOptions);
      }
      const response = streamed.response;
      updateManagerAssistantMessage(targetContextId, streamingMessageId, {
        lines: response.gate ? [response.gate.summary, response.gate.next_action].filter(Boolean).slice(0, 2) : [],
        pending: false,
        text: visibleAgentMessage(response.message || streamedText.trim())
          || `${answerIdentityLabel(targetContextId, selectedRoute.label)} 已完成分析。`,
      });
      // The completed transcript is the immutable answer owner. Resolve its
      // stored identity before offering a link; a failed read never hides the
      // visible answer or retries the model Turn.
      const completedMessageId = streamingMessageId;
      if (completedMessageId !== null && (response.message || streamedText).length >= MIN_SEPARATE_ANSWER_LENGTH) {
        void fetchChatSession(sessionId).then((stored) => {
          const answer = stored.messages.find((item) =>
            item.turn_id === streamed.turnId && ["agent", "assistant"].includes(item.role));
          if (answer) updateManagerAssistantMessage(targetContextId, completedMessageId, {
            sourceMessageId: answer.message_id, sourceSessionId: sessionId,
          });
        }).catch(() => { /* The original conversation remains readable. */ });
      }
      const todoProposals = response.proposals.filter(isTodoProposal);
      // The channel already states where a team plan is confirmed: its answer
      // names the Goal whose workspace holds the card, so a manager-channel
      // proposal here is only ever a Todo the owner has to be sent to.
      if (todoProposals.length > 0 && !targetGoal) {
        updateManagerAssistantMessage(targetContextId, streamingMessageId, {
          lines: ["请进入要修改的 Goal，预览并确认具体变更。"],
        });
      }
      if (todoProposals.length > 0 && targetGoal) {
        const cards = todoProposals.map((proposal) => ({
          goalId: targetGoal.goalId,
          id: proposalId.current++,
          previewId: null,
          proposal,
          receiptLabel: null,
          state: "candidate" as const,
          statusMessage: null,
        }));
        setProposalsByContext((current) => ({
          ...current,
          [targetContextId]: [...(current[targetContextId] ?? []), ...cards],
        }));
      }
      if (targetContextId !== "manager" && response.protected_action) {
        const protectedPreview = semanticProtectedActionPreview(
          targetContextId,
          question,
          response.protected_action,
        );
        if (protectedPreview) return protectedPreview;
      }
    } catch (error) {
      const userInterrupted = (submittedTurnId && interruptedTurnIds.current.delete(submittedTurnId))
        || (error instanceof ChatApiError && error.payload.error_code === "turn_interrupted");
      if (userInterrupted) {
        const interruptedMessage = {
          agentLabel: answerIdentityLabel(targetContextId, selectedRoute.label),
          lines: [],
          pending: false,
          sourceLabel: targetContextId === "manager"
            ? `${t("header.manager")}会话`
            : `${selectedRoute.label} 会话`,
          text: [streamedText.trim(), "已中断。你可以在当前会话继续发送消息。"].filter(Boolean).join("\n\n"),
        };
        if (streamingMessageId === null) {
          appendManagerAssistantMessage(targetContextId, interruptedMessage);
        } else {
          updateManagerAssistantMessage(targetContextId, streamingMessageId, interruptedMessage);
        }
        return;
      }
      const payloadError = error instanceof ChatApiError ? error.payload : null;
      if (payloadError && sessionInvalidatedByPayload(payloadError)) {
        sessionIds.current.delete(sessionKey);
      }
      if (payloadError?.error_code === "resume_failed") {
        sessionIds.current.delete(sessionKey);
        newSessionRequired.current.add(sessionKey);
        recordRuntimeBinding(targetContextId, {
          agentId: selectedRoute.agentId,
          resumable: false,
          sessionId: runtimeBindings[targetContextId]?.sessionId ?? "resume-failed",
          status: "resume_failed",
        });
      }
      const gate = payloadError?.gate;
      const gateSummary = gate && typeof gate === "object"
        ? String((gate as { summary?: unknown }).summary ?? "")
        : "";
      const failureMessage = {
        agentLabel: answerIdentityLabel(targetContextId, selectedRoute.label),
        lines: gateSummary ? [gateSummary] : [],
        pending: false,
        reconnect: payloadError?.reconnectable === true,
        sourceLabel: "LoopX Chat 本地后端",
        text: payloadError?.error_code === "resume_failed"
          ? `原 ${answerIdentityLabel(targetContextId, selectedRoute.label)} 会话无法恢复。本地历史已经保留，请在运行详情里选择“重试恢复”或“开始新 Session”。`
          : error instanceof Error
            ? error.message
            : `${answerIdentityLabel(targetContextId, selectedRoute.label)} 会话暂时不可用。`,
      };
      if (streamingMessageId === null) {
        appendManagerAssistantMessage(targetContextId, failureMessage);
      } else {
        updateManagerAssistantMessage(targetContextId, streamingMessageId, failureMessage);
      }
    } finally {
      activeTurnIds.current.delete(targetContextId);
      streamControllers.current.delete(targetContextId);
      const boundSessionId = sessionIds.current.get(sessionKey);
      if (boundSessionId) {
        recordRuntimeBinding(targetContextId, {
          agentId: selectedRoute.agentId,
          resumable: true,
          sessionId: boundSessionId,
          status: "ready",
        });
      }
      setSendingContextId((current) => current === targetContextId ? null : current);
    }
  }

  async function interruptManagerTurn(run?: { agentId: string; goalId: string; sessionId?: string; turnId?: string }) {
    const targetContextId = run?.goalId ?? contextId;
    const binding = runtimeBindings[targetContextId];
    const agentId = run?.agentId ?? binding?.agentId ?? selectedAgent.agentId;
    const sessionKey = `${targetContextId}:${agentId}`;
    const sessionId = run?.sessionId ?? binding?.sessionId ?? sessionIds.current.get(sessionKey);
    const turnId = run?.turnId ?? binding?.turnId ?? activeTurnIds.current.get(targetContextId);
    if (!sessionId || !turnId) return;
    const controller = streamControllers.current.get(targetContextId);
    const receipt = await interruptChatTurn(sessionId, turnId);
    // A completed Turn wins the race. A failed request leaves its stream live.
    // Never let a late receipt abort or reset a newer Turn in this context.
    if (receipt.status !== "interrupted" || activeTurnIds.current.get(targetContextId) !== turnId) return;
    if (controller && streamControllers.current.get(targetContextId) === controller) {
      interruptedTurnIds.current.add(turnId);
      controller.abort();
    } else {
      activeTurnIds.current.delete(targetContextId);
      recordRuntimeBinding(targetContextId, {
        agentId,
        resumable: true,
        sessionId,
        status: "ready",
      });
      setSendingContextId((current) => current === targetContextId ? null : current);
    }
  }

  async function retryManagerSession(run: { agentId: string; goalId: string; sessionId?: string }) {
    const targetContextId = run.goalId;
    const sessionKey = `${targetContextId}:${run.agentId}`;
    const sessionId = run.sessionId ?? runtimeBindings[targetContextId]?.sessionId ?? sessionIds.current.get(sessionKey);
    if (!sessionId) return;
    try {
      const restored = await resumeChatSession(sessionId);
      sessionIds.current.set(sessionKey, sessionId);
      newSessionRequired.current.delete(sessionKey);
      recordRuntimeBinding(targetContextId, {
        agentId: run.agentId,
        resumable: restored.session.resumable,
        sessionId,
        status: restored.session.status,
      });
    } catch {
      recordRuntimeBinding(targetContextId, {
        agentId: run.agentId,
        resumable: false,
        sessionId,
        status: "resume_failed",
      });
    }
  }

  function startNewManagerSession(run: { agentId: string; goalId: string }) {
    const sessionKey = `${run.goalId}:${run.agentId}`;
    sessionIds.current.delete(sessionKey);
    newSessionRequired.current.add(sessionKey);
    recordRuntimeBinding(run.goalId, {
      agentId: run.agentId,
      resumable: true,
      sessionId: "new-session-pending",
      status: "ready",
    });
  }

  async function closeManagerSession(run: { agentId: string; goalId: string; sessionId?: string }) {
    const sessionKey = `${run.goalId}:${run.agentId}`;
    const sessionId = run.sessionId ?? runtimeBindings[run.goalId]?.sessionId ?? sessionIds.current.get(sessionKey);
    if (sessionId && sessionId !== "new-session-pending") await closeChatSession(sessionId);
    sessionIds.current.delete(sessionKey);
    newSessionRequired.current.add(sessionKey);
    recordRuntimeBinding(run.goalId, null);
  }

  async function previewPersonalProposal(card: PersonalProposalCard) {
    const targetContextId = contextId;
    updatePersonalProposal(targetContextId, card.id, {
      state: "previewing",
      statusMessage: null,
    });
    try {
      const preview = await previewTodo(card.goalId, card.proposal.text);
      updatePersonalProposal(targetContextId, card.id, {
        previewId: preview.preview_id,
        state: "ready",
        statusMessage: "LoopX 已锁定这次写入预览，请确认后再提交。",
      });
    } catch (error) {
      updatePersonalProposal(targetContextId, card.id, {
        state: "error",
        statusMessage: error instanceof Error ? error.message : "无法生成 Todo 预览。",
      });
    }
  }

  async function approvePersonalProposal(card: PersonalProposalCard) {
    if (!card.previewId) {
      return;
    }
    const targetContextId = contextId;
    updatePersonalProposal(targetContextId, card.id, {
      state: "applying",
      statusMessage: null,
    });
    try {
      const result = await applyTodo(card.goalId, card.proposal.text, card.previewId);
      updatePersonalProposal(targetContextId, card.id, {
        receiptLabel: todoReceiptLabel(result.receipt),
        state: "approved",
        statusMessage: result.receipt.already_exists ? "Todo 已存在，本次没有重复写入。" : "Todo 已写入，并返回可核对回执。",
      });
      onRefresh();
    } catch (error) {
      const noWriteReceipt = error instanceof ChatApiError
        ? todoNoWriteReceiptFromPayload(error.payload)
        : null;
      updatePersonalProposal(targetContextId, card.id, noWriteReceipt ? {
        previewId: null,
        receiptLabel: `未写入 · 回执 ${noWriteReceipt.receipt_id.slice(0, 12)}`,
        state: "stale",
        statusMessage: "Goal 状态已变化，本次保持零写入。请重新生成预览。",
      } : {
        state: "error",
        statusMessage: error instanceof Error ? error.message : "Todo 写入失败。",
      });
    }
  }

  function settlePersonalProposal(card: PersonalProposalCard, state: "rejected" | "cancelled") {
    updatePersonalProposal(contextId, card.id, {
      previewId: null,
      receiptLabel: "未写入",
      state,
      statusMessage: state === "rejected" ? "你已拒绝这个候选 Todo。" : "你已取消本次处理。",
    });
  }

  function chooseAgent(agentId: string) {
    if (!agentOptions.some((agent) => agent.agentId === agentId && agent.available)) {
      return;
    }
    setSelectedAgents((current) => ({ ...current, [contextId]: agentId }));
    setAgentMenuOpen(false);
  }

  function openManagerChat() {
    onSelectGoal("");
    setMobilePanel("chat");
  }

  function openGoalList() {
    onSelectGoal("");
    setMobilePanel("goals");
  }

  function openGoalChat(goalId: string) {
    onSelectGoal(goalId);
    setMobilePanel("chat");
  }

  function beginDecisionReply(value: string) {
    setManagerInput(value);
    managerInputRef.current?.focus();
  }

  const selectedGoalStateVariant = selectedGoal ? personalGoalStateVariant[selectedGoal.state] : "neutral";
  const selectedGoalHeaderSummary = selectedGoal
    ? `${selectedAgent.label} · ${selectedGoal.state}${goalAgentTodos.length > 0 ? ` ${goalProgressLabel}` : ""}${goalUserTodos.length > 0 ? ` · ${goalUserTodos.length} 项等你` : ""}`
    : "跨 Goal 的个人工作入口";
  const diagnosis = selectedGoal?.state === "需修复" || (!selectedGoal && !payload.ok)
    ? {
        impact: "可见进度可能已经过期，受影响的执行路径会暂停。",
        label: "需要关注",
        owner: selectedGoal ? personalAgentLabel(selectedGoal.agentId) : "LoopX 管家",
        suggestion: selectedGoal?.nextSentence ?? "刷新 LoopX 状态并检查健康信息。",
        title: selectedGoal?.agentSentence ?? "LoopX 状态需要检查",
      }
    : selectedGoal?.state === "等你"
      ? {
          impact: selectedGoal.needsYouBlocking
            ? "这项 Gate 约束的路径暂停；独立执行路径仍按 interaction contract 推进。"
            : "Agent 可以继续推进；这项用户待办会持续保留。",
          label: selectedGoal.needsYouBlocking ? "等待你的决定" : "有一项待办等你",
          owner: "你",
          suggestion: selectedGoal.needsYou ?? selectedGoal.nextSentence,
          title: selectedGoal.needsYou ?? "请处理当前用户待办",
        }
      : {
          impact: selectedGoal ? "Agent 可以按当前计划继续。" : "各 Goal 会按各自 interaction contract 推进。",
          label: "运行正常",
          owner: selectedGoal ? personalAgentLabel(selectedGoal.agentId) : "LoopX 管家",
          suggestion: selectedGoal?.nextSentence ?? "继续观察跨 Goal 状态。",
          title: selectedGoal ? "当前没有阻止 Agent 继续的运行异常" : "当前没有全局健康异常",
        };

  const workspaceTimeline: WorkspaceTimelineItem[] = [
    ...(!selectedGoal && runtimeBindings.manager?.status === "resume_failed" ? [{
      id: "run:manager:resume-failed",
      kind: "run" as const,
      run: {
        agentId: runtimeBindings.manager.agentId,
        agentLabel: personalAgentLabel(runtimeBindings.manager.agentId),
        canInterrupt: false,
        completedSteps: 0,
        goalId: "manager",
        goalTitle: "LoopX 管家",
        latestActivity: "本地聊天记录已保留，点我查看恢复方式。",
        resumable: false,
        runId: "manager:resume-failed",
        sessionId: runtimeBindings.manager.sessionId,
        sessionStatus: "resume_failed",
        status: "failed" as const,
        title: "上次会话需要恢复",
        totalSteps: 1,
        outputs: [],
      },
    }] : []),
    ...(selectedGoal ? executionSessions.map((session): WorkspaceTimelineItem => {
      const todoId = session.channel_id?.startsWith("task.") ? session.channel_id.slice(5) : undefined;
      const todo = selectedGoal.agentTodos.find((candidate) => candidate.todoId === todoId);
      const running = Boolean(session.active_turn_id);
      const snapshot = executionSessionSnapshots[session.session_id];
      const hasResult = snapshot?.messages.some((message) => isAgentResultMessage(message.role, message.text)) === true;
      return {
        id: `run:task:${session.session_id}`,
        kind: "run",
        run: {
          agentId: session.agent_id,
          agentLabel: personalAgentLabel(session.agent_id),
          canInterrupt: running,
          completedSteps: todo?.done || hasResult ? 1 : 0,
          goalId: selectedGoal.goalId,
          goalTitle: selectedGoal.title,
          latestActivity: running
            ? "Agent 正在执行，可进入 Session 查看过程或发送纠偏。"
            : hasResult
              ? "Agent 已返回结果，点击查看结果与完整运行记录。"
              : "执行 Session 已保留，可继续纠偏或恢复。",
          resumable: session.resumable,
          runId: session.session_id,
          sessionId: session.session_id,
          sessionMessages: snapshot?.messages.map((message) => ({
            createdAt: message.created_at,
            messageId: message.message_id,
            role: message.role === "user" ? "user" : isAgentResultMessage(message.role, message.text) ? "assistant" : "error",
            text: message.role === "user" ? message.text : visibleAgentMessage(message.text),
          })),
          sessionStatus: hasResult ? "completed" : session.status,
          status: todo?.done || hasResult ? "completed" : running ? "running" : session.status === "resume_failed" ? "failed" : "waiting",
          title: todo?.text ?? "Agent 执行任务",
          todoId,
          totalSteps: 1,
          turnId: session.active_turn_id ?? undefined,
        },
      };
    }) : []),
    // A persistent chat session is not itself waiting work. Only surface a
    // Goal-level execution row when there is execution, a status observation,
    // or a wait/fault. Observations are not deliverable Files.
    ...(selectedGoal && (runtimeBindings[selectedGoal.goalId]?.turnId
      || selectedGoal.hasRunObservation || goalHasExecutionSummary(selectedGoal)) ? [{
      id: `run:${selectedGoal.goalId}`,
      kind: "run" as const,
      run: {
        agentId: runtimeBindings[selectedGoal.goalId]?.agentId ?? selectedGoal.agentId,
        agentLabel: personalAgentLabel(runtimeBindings[selectedGoal.goalId]?.agentId ?? selectedGoal.agentId),
        canInterrupt: Boolean(runtimeBindings[selectedGoal.goalId]?.turnId),
        completedSteps: selectedGoal.agentTodos.filter((todo) => todo.done).length,
        goalId: selectedGoal.goalId,
        goalTitle: selectedGoal.title,
        latestActivity: selectedGoal.agentSentence,
        resumable: runtimeBindings[selectedGoal.goalId]?.resumable ?? true,
        runId: `goal:${selectedGoal.goalId}`,
        sessionId: runtimeBindings[selectedGoal.goalId]?.sessionId,
        sessionStatus: runtimeBindings[selectedGoal.goalId]?.status,
        status: runtimeBindings[selectedGoal.goalId]?.turnId
          ? "running" as const
          : selectedGoal.state === "需修复"
            ? "failed" as const
            : "waiting" as const,
        title: selectedGoal.nextSentence,
        totalSteps: selectedGoal.agentTodos.length || 1,
        turnId: runtimeBindings[selectedGoal.goalId]?.turnId,
      },
    }] : []),
    ...contextMessages.map((message): WorkspaceTimelineItem => ({
      id: `message:${message.id}`,
      kind: "message",
        message: {
          activity: message.activity,
          agentLabel: message.agentLabel,
          attachments: message.attachments,
        id: String(message.id),
        pending: message.pending,
        returnDelivery: message.returnDelivery,
        collaboration: message.collaboration,
        role: message.role,
        sourceTurnId: message.sourceTurnId,
        sourceMessageId: message.sourceMessageId,
        sourceSessionId: message.sourceSessionId,
        text: message.text || (message.pending ? "" : message.lines.join("\n")),
      },
    })),
    ...(selectedGoal && periodicReport ? [{
      id: `output:${selectedGoal.goalId}:report:${periodicReport.publication.publication_id}`,
      kind: "output" as const,
      output: {
        agentId: periodicReport.agent_id,
        agentLabel: personalAgentLabel(periodicReport.agent_id),
        createdAt: periodicReport.publication.delivered_at,
        goalId: selectedGoal.goalId,
        goalTitle: selectedGoal.title,
        kind: "report" as const,
        outputId: periodicReport.publication.publication_id,
        report: {
          addedCount: periodicReport.delta.added_count,
          changedCount: periodicReport.delta.changed_count,
          deliveredAt: periodicReport.publication.delivered_at,
          generationId: periodicReport.generation_id,
          items: periodicReport.delta.items.map((item) => ({
            changeKind: item.change_kind,
            previousStatus: item.previous_status,
            sourceRef: item.source_ref,
            status: item.status,
            summary: item.summary,
            title: item.title,
          })),
          periodEndAt: periodicReport.period_window.end_at,
          periodStartAt: periodicReport.period_window.start_at,
          predecessorPublicationId: periodicReport.publication.predecessor_publication_id,
          publicationId: periodicReport.publication.publication_id,
        },
        safePreview: periodicReport.delta.items
          .map((item) => `${item.change_kind === "added" ? "+" : "~"} ${item.title}\n${item.summary}`)
          .join("\n\n"),
        summary: periodicReport.summary,
        title: periodicReport.title,
      },
    }] : []),
  ];
  const sourceIsReady = statusSourceControl.connectionState === "connected";
  const goalTitles = new Map(model.goals.map((goal) => [goal.goalId, goal.title]));
  const attentionForWorkspace = (item: PersonalNeedsYouItem) => sourceAttention(
    item, statusSourceControl.activeSource.statusUrl,
    sourceIsReady && !progress?.errors[item.goalId], goalTitles.get(item.goalId),
  );
  const normalizedModel = normalizePersonalHomeModel(model);
  const workspaceModel = {
    ...normalizedModel,
    goals: goalSessionFacts === undefined ? normalizedModel.goals : normalizedModel.goals.map((goal) => ({
      ...goal,
      execution: goalExecutionFromSessions(goalSessionFacts, goal.goalId),
    })),
    userTodos: model.userTodos.map(attentionForWorkspace),
    attentionHistory: (model.attentionHistory ?? model.userTodos).map(attentionForWorkspace),
    periodicReports: {
      error: periodicReportError,
      loading: periodicReportLoading,
    },
    timeline: workspaceTimeline,
  };
  return (
    <div className={theme === "dark" ? "dark" : ""} data-testid="personal-goal-home">
      {executionDiscoveryError ? <p role="status" className="m-0 bg-amber-50 px-4 py-2 text-sm text-amber-900">{t(executionDiscoveryError === "partial" ? "runs.discoveryPartial" : "runs.discoveryOffline")}</p> : null}
      <PersonalWorkspacePage
        agents={agentOptions.map((agent) => ({
          adapterKind: agent.adapterKind,
          agentId: agent.agentId,
          available: agent.available,
          capability: agent.capability,
          interrupt: agent.interrupt,
          label: agent.label,
          location: agent.location,
          resume: agent.resume,
          source: agent.source,
          streaming: agent.streaming,
          toolCalls: agent.toolCalls,
          trustScope: agent.trustScope,
          workspaceCompatibility: agent.available ? "当前 Goal 写入前验证" : "不可用，需先修复 Endpoint",
        }))}
        callbacks={{
          onApplyAttention: (attention) => openGoalChat(attention.goalId),
          onCorrectRun: async (run, message) => {
            if (!run.sessionId) throw new Error("这个 Run 还没有可纠偏的执行 Session。");
            const proposal = await previewTypedAction({
              actionKind: "run.correct",
              context: { kind: "run", goal_id: run.goalId, todo_id: run.todoId },
              idempotencyKey: `workspace-run-correct-${run.sessionId}-${Date.now().toString(36)}`,
              normalizedParameters: { goal_id: run.goalId, message, session_id: run.sessionId },
              summary: `纠偏执行任务：${run.title}`,
            });
            const applied = await applyTypedAction(proposal.proposal_id);
            const turnId = typeof applied.turn?.turn_id === "string" ? applied.turn.turn_id : undefined;
            recordRuntimeBinding(run.goalId, {
              agentId: run.agentId,
              resumable: true,
              sessionId: run.sessionId,
              status: turnId ? "running" : "ready",
              turnId,
            });
            if (turnId) {
              activeTurnIds.current.set(run.goalId, turnId);
              const controller = new AbortController();
              streamControllers.current.set(run.goalId, controller);
              let streamedText = "";
              const messageId = appendManagerAssistantMessage(run.goalId, {
                activity: ["正在把纠偏送入原执行 Session"],
                sourceTurnId: turnId,
                agentLabel: run.agentLabel,
                lines: [],
                pending: true,
                sourceLabel: `${run.agentLabel} · 执行 Session`,
                text: "",
              });
              try {
                const streamed = await resumeChatTurnStreaming(run.sessionId, turnId, {
                  signal: controller.signal,
                  onDelta: (delta) => {
                    streamedText += delta;
                    updateManagerAssistantMessage(run.goalId, messageId, { text: streamedText });
                  },
                });
                updateManagerAssistantMessage(run.goalId, messageId, {
                  activity: [],
                  pending: false,
                  text: visibleAgentMessage(streamed.response.message || streamedText.trim()) || `${run.agentLabel} 已完成纠偏。`,
                });
              } catch (error) {
                const interrupted = interruptedTurnIds.current.delete(turnId)
                  || (error instanceof ChatApiError && error.payload.error_code === "turn_interrupted");
                updateManagerAssistantMessage(run.goalId, messageId, {
                  activity: [],
                  pending: false,
                  text: interrupted ? [streamedText.trim(), "已中断。你可以在当前会话继续发送消息。"].filter(Boolean).join("\n\n") : error instanceof Error ? error.message : "纠偏回合失败。",
                });
              } finally {
                activeTurnIds.current.delete(run.goalId);
                streamControllers.current.delete(run.goalId);
                recordRuntimeBinding(run.goalId, {
                  agentId: run.agentId,
                  resumable: true,
                  sessionId: run.sessionId,
                  status: "ready",
                });
              }
            }
          },
          onCloseRunSession: closeManagerSession,
          onInterruptRun: async (run) => interruptManagerTurn(run),
          onInterruptConversationTurn: async (targetContextId, turnId) => {
            if (activeTurnIds.current.get(targetContextId) !== turnId) {
              throw new Error("该回合已结束或已被新的回合取代，请刷新后查看。");
            }
            const binding = runtimeBindings[targetContextId];
            if (!binding?.sessionId || binding.turnId !== turnId) {
              throw new Error("当前会话与回合不匹配，请刷新后查看。");
            }
            await interruptManagerTurn({
              agentId: binding.agentId,
              goalId: targetContextId,
              sessionId: binding.sessionId,
              turnId,
            });
          },
          onSteerConversationTurn: async (targetContextId, turnId, message, ingressId) => {
            const binding = runtimeBindings[targetContextId];
            if (!binding?.sessionId || binding.turnId !== turnId || activeTurnIds.current.get(targetContextId) !== turnId) {
              throw new Error("本轮已结束或已被新的回合取代，追加指令未发送，草稿已保留。");
            }
            await steerChatTurn(binding.sessionId, turnId, message, ingressId);
            const id = managerMessageId.current++;
            setMessagesByContext(current => {
              const messages = current[targetContextId] ?? [];
              if (messages.some(item => item.sourceMessageId === `steer:${ingressId}`)) return current;
              return { ...current, [targetContextId]: [...messages, { id, sourceMessageId: `steer:${ingressId}`, sourceTurnId: turnId, lines: [], role: "user", text: message }] };
            });
          },
          onOpenGoal: openGoalChat,
          onOpenRunSession: async (run) => {
            if (!run.sessionId) return;
            const sessionId = run.sessionId;
            const snapshot = await fetchChatSession(sessionId);
            setExecutionSessionSnapshots((current) => ({
              ...current,
              [sessionId]: snapshot,
            }));
            openGoalChat(run.goalId);
            setMessagesByContext((current) => ({
              ...current,
              [run.goalId]: snapshot.messages.map((message) => ({
                sourceMessageId: message.message_id,
                sourceSessionId: sessionId,
                agentLabel: message.role === "user" ? undefined : run.agentLabel,
                attachments: workspaceImageAttachments(message.attachments),
                id: managerMessageId.current++,
                lines: [],
                role: message.role === "user" ? "user" : "assistant",
                sourceLabel: message.role === "user" ? undefined : `${run.agentLabel} · 执行 Session`,
                text: message.role === "user" ? message.text : visibleAgentMessage(message.text),
              })),
            }));
            recordRuntimeBinding(run.goalId, {
              agentId: run.agentId,
              resumable: snapshot.session.resumable,
              sessionId,
              status: snapshot.session.active_turn_id ? "running" : snapshot.session.status,
              turnId: snapshot.session.active_turn_id ?? undefined,
            });
          },
          onOpenOutput: (output) => openGoalChat(output.goalId),
          ...(goalSubagentConfigurationEnabled ? {
          onPreviewGoalSubagentConfiguration: async (request) => {
            const preview = await previewGoalSubagentConfiguration(request);
            return {
              changed: preview.changed,
              configuration: {
                allowedDomains: preview.after.orchestration.allowed_domains,
                codexHostCapacity: {
                  configuredChildren: preview.codex_host_capacity.configured_children,
                  newSessionRequired: preview.codex_host_capacity.new_session_required,
                  requiredChildren: preview.codex_host_capacity.required_children,
                  status: preview.codex_host_capacity.status,
                  writeRequired: preview.codex_host_capacity.write_required,
                  written: preview.codex_host_capacity.written,
                },
                enabled: preview.feature_summary.multi_subagent === "enabled",
                executionConfig: preview.after.orchestration.execution_config,
                maxChildren: preview.after.orchestration.max_children,
                modelConfig: preview.after.orchestration.model_config,
              },
              previewId: preview.preview_id,
            };
          },
          onApplyGoalSubagentConfiguration: async ({ previewId, ...request }) => {
            const result = await applyGoalSubagentConfiguration(request, previewId);
            return {
              allowedDomains: result.after.orchestration.allowed_domains,
              codexHostCapacity: {
                configuredChildren: result.codex_host_capacity.configured_children,
                newSessionRequired: result.codex_host_capacity.new_session_required,
                requiredChildren: result.codex_host_capacity.required_children,
                status: result.codex_host_capacity.status,
                writeRequired: result.codex_host_capacity.write_required,
                written: result.codex_host_capacity.written,
              },
              enabled: result.feature_summary.multi_subagent === "enabled",
              executionConfig: result.after.orchestration.execution_config,
              maxChildren: result.after.orchestration.max_children,
              modelConfig: result.after.orchestration.model_config,
            };
          },
          } : {}),
          ...(remoteGoalLifecycleHost ? {
            onExecuteGoalLifecycle: async ({ goalId, operation, reason }) => {
              const result = await applyRemoteGoalLifecycle(
                remoteGoalLifecycleHost,
                goalId,
                operation,
                reason,
              );
              return {
                activationState: result.activation_state,
                projectionVerified: result.projection_verified,
              };
            },
          } : {}),
          onGoalActivationStateChange,
          onGoalDeleted,
          onReconcileStatus,
          onRetryGoalArchive,
          onExportOutput: async (output) => {
            const contents = [
              `# ${output.title}`,
              "",
              output.summary ?? "",
              "",
              output.safePreview ?? "此产出没有可用的公开安全预览。",
              "",
              `Goal: ${output.goalId}`,
              `Todo: ${output.todoId ?? "unlinked"}`,
              `Run: ${output.runId ?? "unlinked"}`,
            ].join("\n");
            const url = URL.createObjectURL(new Blob([contents], { type: "text/markdown;charset=utf-8" }));
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = `${output.outputId.replace(/[^a-z0-9._-]+/gi, "-")}.md`;
            anchor.click();
            URL.revokeObjectURL(url);
          },
          onRefresh,
          onRetryResumeRun: retryManagerSession,
          onSelectAgent: chooseAgent,
          onSelectGoal: (goalId) => goalId ? openGoalChat(goalId) : openManagerChat(),
          onSendMessage: async (message, agentId, goalId, attachments) => sendManagerQuestion(message, { agentId, goalId, attachments }),
          onPrepareLoopX: (agentId, goalId) => prepareGoalConversation(goalId, agentId),
          onStartLoopX: (operation, agentId, goalId, settings) => { void sendManagerQuestion(operation === "start" ? "开启 LoopX 模式，持续推进当前 Goal。" : "恢复 LoopX 模式。", {agentId, goalId, loopxMode: {operation, settings}}); },
          onStartNewRunSession: startNewManagerSession,
        }}
        goalArchiveLoadState={goalArchiveLoadState}
        initialManagerChatOpen={initialManagerChatOpen}
        managerChannelBinding={managerChannelBinding}
        managerRuntime={managerRuntime}
        conversationSessionId={runtimeBindings[contextId]?.sessionId}
        model={workspaceModel}
        readOnly={readOnly}
        selectedAgentId={selectedAgent.agentId}
        selectedGoalId={selectedGoal?.goalId ?? null}
        statusSourceControl={statusSourceControl}
      />
    </div>
  );
}


function StatusRequestView({
  error,
  isLoading,
  onRetry,
  requestedUrl,
  theme,
  toggleTheme,
}: {
  error: string | null;
  isLoading: boolean;
  onRetry: () => void;
  requestedUrl: string;
  theme: "light" | "dark";
  toggleTheme: () => void;
}) {
  const statusServiceUnavailable = Boolean(
    error
    && /failed to fetch|networkerror|load failed/i.test(error)
    && requestedUrl.includes("status.json"),
  );
  return (
    <div className={theme === "dark" ? "dark" : ""}>
      <main className="min-h-screen bg-[#f6f7f9] text-slate-950 dark:bg-[#09090b] dark:text-zinc-50">
        <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950 sm:px-6">
          <div>
            <h1 className="text-xl font-semibold">LoopX Workspace</h1>
            <p className="mt-1 break-all text-sm text-slate-500 dark:text-zinc-400">Personal Workspace</p>
          </div>
          <Button aria-label="切换主题" onClick={toggleTheme} size="icon" variant="secondary">
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </header>
        <div className="grid min-h-[calc(100vh-80px)] sm:grid-cols-[240px_1fr]">
          <aside className="hidden border-r border-slate-200 p-6 dark:border-zinc-800 sm:block" aria-label="Workspace">
            <strong>LoopX</strong><p className="mt-6 text-sm">Workspace</p>
            <p className="mt-8 text-xs text-slate-500">Goals</p>
            {[1, 2, 3].map((item) => <div key={item} className="mt-4 h-8 rounded bg-slate-100 dark:bg-zinc-900" />)}
          </aside>
          <div className="p-4 sm:p-8">
          <Card data-testid="initial-status-state">
            <CardContent className="flex min-h-64 items-center justify-center p-6">
              <div className="max-w-xl text-center">
                {error ? (
                  <CircleAlert className="mx-auto h-6 w-6 text-rose-600 dark:text-rose-300" />
                ) : (
                  <RefreshCw className="mx-auto h-6 w-6 animate-spin text-slate-500 dark:text-zinc-400" />
                )}
                <p className="mt-3 text-sm font-medium">
                  {error ? "无法加载实时状态" : "正在加载实时状态"}
                </p>
                {error ? (
                  <>
                    <p className="mt-2 break-words text-sm leading-6 text-slate-500 dark:text-zinc-400">
                      {statusServiceUnavailable
                        ? "本地状态服务暂时未连接。升级或启动期间可能短暂断开，请重试；仍失败时重新打开 LoopX App，或运行 loopx doctor。"
                        : error}
                    </p>
                    {statusServiceUnavailable ? (
                      <p className="mt-2 text-xs leading-5 text-slate-400 dark:text-zinc-500">
                        重新加载不会执行任务，也不会改变 Goal 配置。
                      </p>
                    ) : null}
                    <div className="mt-4 flex flex-wrap justify-center gap-2">
                      <Button disabled={isLoading} onClick={onRetry}>
                        <RefreshCw className="h-4 w-4" />
                        重试
                      </Button>
                    </div>
                  </>
                ) : <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-zinc-400" role="status">
                  正在连接 Workspace，Goal 列表将先出现，详细状态会逐个补齐。 / Connecting to your Workspace. Goals load independently.
                </p>}
              </div>
            </CardContent>
          </Card>
          </div>
        </div>
      </main>
    </div>
  );
}


export function DashboardPage() {
  const search = dashboardRoute.useSearch();
  const navigate = dashboardRoute.useNavigate();
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [progress, setProgress] = useState<WorkspaceProgress | null>(null);
  const progressiveAbortRef = useRef<AbortController | null>(null);
  const preferredGoalRef = useRef(search.goalId);
  preferredGoalRef.current = search.goalId;
  const [payload, setPayload] = useState<StatusPayload>(exampleStatusPayload);
  const [source, setSource] = useState<DataSource>({ kind: "example", label: "bundled example" });
  const [statusSourceCatalog, setStatusSourceCatalog] = useState(() => {
    try {
      return loadStatusSourceCatalog(window.localStorage, window.location.href);
    } catch {
      // Browsers may reject access to the storage object itself.
      return emptyStatusSourceCatalog();
    }
  });
  const statusSourceCatalogRef = useRef(statusSourceCatalog);
  statusSourceCatalogRef.current = statusSourceCatalog;
  const [statusUrl, setStatusUrl] = useState(search.statusUrl);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [goalArchiveLoadState, setGoalArchiveLoadState] = useState<WorkspaceGoalArchiveLoadState>({
    error: null,
    phase: "idle",
  });
  const [requestedStatusUrl, setRequestedStatusUrl] = useState<string | null>(
    search.statusUrl.trim() || null,
  );
  const [exampleModeRequested, setExampleModeRequested] = useState(false);
  const suppressedStatusUrlRef = useRef<string | null>(null);
  const statusRequestFenceRef = useRef<StatusRequestFence>(
    createStatusRequestFence(search.statusUrl.trim() || null),
  );
  const routeStatusRequestUrl = !exampleModeRequested && source.kind === "example"
    ? search.statusUrl.trim()
    : "";
  const activeStatusRequestUrl = requestedStatusUrl ?? routeStatusRequestUrl;
  const loadedStatusUrl = source.kind === "url" ? source.label : defaultGlobalStatusUrl;
  const requestFailed = Boolean(loadError && requestedStatusUrl);
  const activeStatusSource: StatusSource = activeStatusSourceForUrl(
    statusSourceCatalog,
    requestedStatusUrl,
    loadedStatusUrl,
    window.location.href,
  );

  const statusRequestActive = source.kind === "example"
    && !exampleModeRequested;
  const queue = payload.attention_queue;
  const runHistory = payload.run_history;
  const goalRows = useMemo(
    () => buildGoalDirectoryRows(runHistory.goals, queue.items),
    [runHistory.goals, queue.items],
  );

  function loadGoalArchive(url: string, request: StatusRequest, resyncAttempt = 0) {
    setGoalArchiveLoadState({ error: null, phase: "loading" });
    void fetchStatusPayload(scopedStatusUrl(url, "stopped", window.location.href))
      .then((archivePayload) => {
        if (!statusRequestCanCommit(statusRequestFenceRef.current, request)) return;
        const archiveRevision = archivePayload.goal_projection?.registry_revision ?? null;
        const revisionMismatch = request.registryRevision !== null
          && request.registryRevision !== undefined
          && archiveRevision !== null
          && request.registryRevision !== archiveRevision;
        setPayload((current) => mergeScopedStatusProjections(current, archivePayload));
        if (revisionMismatch && resyncAttempt < 1) {
          void loadFromUrl(url, { background: true, resyncAttempt: resyncAttempt + 1 });
          return;
        }
        setGoalArchiveLoadState(
          revisionMismatch
            ? { error: "Goal 状态在加载历史时发生变化，请重试。", phase: "error" }
            : { error: null, phase: "ready" },
        );
      })
      .catch((error) => {
        if (!statusRequestCanCommit(statusRequestFenceRef.current, request)) return;
        setGoalArchiveLoadState({ error: formatStatusError(error), phase: "error" });
      });
  }

  function retryGoalArchive() {
    const url = source.kind === "url" ? source.label : (statusUrl || defaultGlobalStatusUrl);
    const request = beginStatusRequest(statusRequestFenceRef.current, url, { background: true });
    if (progress) { void loadFromUrl(url); return; }
    if (request) loadGoalArchive(url, request);
  }

  async function commitLoadedStatus(
    url: string,
    nextPayload: StatusPayload,
    request: StatusRequest,
  ) {
    if (request.background) {
      setPayload((current) => mergeScopedStatusProjections(current, nextPayload));
      return true;
    }
    const nextSource: DataSource = { kind: "url", label: url };
    statusRequestFenceRef.current.loadedUrl = url;
    setPayload(nextPayload);
    setSource(nextSource);
    setStatusUrl(url);
    await navigate({
      search: (current) => ({
        ...current,
        statusUrl: url,
      }),
    });
    if (!statusRequestIsCurrent(statusRequestFenceRef.current, request)) return false;
    statusRequestFenceRef.current.requestedUrl = null;
    setRequestedStatusUrl(null);
    return true;
  }

  async function loadFromUrl(
    url: string,
    options: {
      background?: boolean;
      retryOnly?: boolean;
      reuseSnapshots?: boolean;
      invalidateGoalIds?: string[];
      resyncAttempt?: number;
      selectionRevision?: number;
    } = {},
  ) {
    const trimmed = url.trim();
    const background = options.background === true;
    if (!trimmed) {
      if (!background) setLoadError("状态地址不能为空");
      return;
    }
    const request = beginStatusRequest(statusRequestFenceRef.current, trimmed, {
      background,
      selectionRevision: options.selectionRevision,
    });
    if (!request) return;
    progressiveAbortRef.current?.abort();
    const progressiveAbort = new AbortController();
    progressiveAbortRef.current = progressiveAbort;
    if (!background) {
      suppressedStatusUrlRef.current = null;
      setExampleModeRequested(false);
      setRequestedStatusUrl(trimmed);
      setIsLoading(true);
      setLoadError(null);
      setGoalArchiveLoadState({ error: null, phase: "idle" });
    }
    try {
      const directory = await fetchWorkspaceDirectory(trimmed, window.location.href).catch(() => null);
      if (!statusRequestCanCommit(statusRequestFenceRef.current, request)) return;
      if (directory) {
        // A refresh that keeps the same source only re-reads the Goals whose
        // directory entry moved or that the caller just acted on. Dropping every
        // snapshot here would send the whole workspace back to its loading lane
        // after one Goal's pause, resume or open.
        const retained = (options.retryOnly || options.reuseSnapshots)
          && source.kind === "url" && source.label === trimmed
          ? reusableGoalSnapshots(progress, directory, { invalidateGoalIds: options.invalidateGoalIds })
          : {};
        setProgress({ directory, snapshots: retained, errors: {} });
        const requestedDirectory = { ...directory, goals: directory.goals.filter((goal) => !retained[goal.id]) };
        let directoryChanged = false;
        const initial = directoryStatusPayload(directory);
        if (background) setPayload(initial);
        else if (!await commitLoadedStatus(trimmed, initial, request)) return;
        setGoalArchiveLoadState({ error: null, phase: "loading" });
        await loadWorkspaceGoalSnapshots(trimmed, window.location.href, requestedDirectory,
          (id, snapshot, error) => {
            if (error === "revision") directoryChanged = true;
            setProgress((current) => current ? {
            ...current,
            snapshots: snapshot ? { ...current.snapshots, [id]: snapshot } : current.snapshots,
            errors: error ? { ...current.errors, [id]: error } : current.errors,
          } : current);
          },
          () => statusRequestCanCommit(statusRequestFenceRef.current, request),
          () => preferredGoalRef.current,
          progressiveAbort.signal,
        );
        if (directoryChanged && (options.resyncAttempt ?? 0) < 1
          && statusRequestCanCommit(statusRequestFenceRef.current, request)) {
          await loadFromUrl(trimmed, { resyncAttempt: 1 });
          return;
        }
        if (statusRequestCanCommit(statusRequestFenceRef.current, request)) {
          setGoalArchiveLoadState({ error: null, phase: "ready" });
        }
        return;
      }
      const nextPayload = await fetchStatusPayload(
        scopedStatusUrl(trimmed, "active", window.location.href),
      );
      if (!statusRequestCanCommit(statusRequestFenceRef.current, request)) return;
      setProgress(null);
      request.registryRevision = nextPayload.goal_projection?.registry_revision ?? null;
      if (!await commitLoadedStatus(trimmed, nextPayload, request)) return;
      if (nextPayload.goal_projection?.scope !== "active"
        || nextPayload.goal_projection.complete) {
        setGoalArchiveLoadState({ error: null, phase: "ready" });
        return;
      }
      loadGoalArchive(trimmed, request, options.resyncAttempt ?? 0);
    } catch (error) {
      if (!statusRequestIsCurrent(statusRequestFenceRef.current, request)) return;
      if (!background) setLoadError(formatStatusError(error));
    } finally {
      if (!background && statusRequestIsCurrent(statusRequestFenceRef.current, request)) {
        setIsLoading(false);
      }
    }
  }

  function selectStatusSource(nextSource: StatusSource, options: { ensureTunnel?: boolean } = {}) {
    progressiveAbortRef.current?.abort();
    const selectionRevision = reserveStatusSourceSelection(
      statusRequestFenceRef.current,
      nextSource.statusUrl,
    );
    suppressedStatusUrlRef.current = null;
    setExampleModeRequested(false);
    setRequestedStatusUrl(nextSource.statusUrl);
    setIsLoading(true);
    setLoadError(null);
    void (async () => {
      if (options.ensureTunnel && nextSource.kind === "ssh_tunnel") {
        const port = new URL(nextSource.statusUrl, window.location.href).port;
        if (port) {
          try {
            await ensureSshSource(nextSource.label, port);
          } catch {
            // The tunnel may already exist; the status fetch reports the authoritative result.
          }
        }
      }
      if (statusRequestFenceRef.current.selectionRevision !== selectionRevision) return;
      await loadFromUrl(nextSource.statusUrl, { selectionRevision });
    })();
  }

  function persistStatusSourceCatalog(nextCatalog: typeof statusSourceCatalog) {
    statusSourceCatalogRef.current = nextCatalog;
    setStatusSourceCatalog(nextCatalog);
    try {
      saveStatusSourceCatalog(window.localStorage, nextCatalog);
    } catch {
      // Private browsing may disable storage; the source remains usable for this page session.
    }
  }

  const statusSourceControl: StatusSourceControl = {
    activeSource: activeStatusSource,
    connectionState: isLoading
      ? "loading"
      : requestFailed
        ? "error"
        : "connected",
    errorMessage: requestFailed
      ? `未切换到 ${requestedStatusUrl ?? "所选来源"}：${loadError}`
      : null,
    onAdd: (input) => {
      const result = addSshTunnelStatusSource(statusSourceCatalog, input, window.location.href);
      if ("error" in result) return { error: result.error };
      persistStatusSourceCatalog(result.catalog);
      selectStatusSource(result.source, { ensureTunnel: input.ensureTunnel });
      return {};
    },
    onConfiguredHostsLoaded: (hostAliases) => {
      const currentCatalog = statusSourceCatalogRef.current;
      const nextCatalog = bindConfiguredSshHostAliases(currentCatalog, hostAliases);
      if (nextCatalog !== currentCatalog) persistStatusSourceCatalog(nextCatalog);
    },
    onRemove: (sourceId) => {
      const nextCatalog = removeStatusSource(statusSourceCatalog, sourceId);
      persistStatusSourceCatalog(nextCatalog);
      if (activeStatusSource.id === sourceId) selectStatusSource(localStatusSource);
    },
    onSelect: (sourceId) => {
      const nextSource = statusSourceCatalog.sources.find((candidate) => candidate.id === sourceId);
      if (!nextSource) return;
      selectStatusSource(nextSource, { ensureTunnel: nextSource.kind === "ssh_tunnel" });
    },
    sources: activeStatusSource.id === "temporary"
      ? [...statusSourceCatalog.sources, activeStatusSource]
      : statusSourceCatalog.sources,
  };

  useEffect(() => {
    const trimmedStatusUrl = search.statusUrl.trim();
    if (trimmedStatusUrl) {
      if (suppressedStatusUrlRef.current === trimmedStatusUrl) {
        return;
      }
      // Do not let the route effect hijack an in-flight selection that is
      // switching to a different source; the user's request wins.
      if (requestedStatusUrl && requestedStatusUrl !== trimmedStatusUrl) {
        return;
      }
      if (source.kind !== "url" || source.label !== trimmedStatusUrl) {
        void loadFromUrl(trimmedStatusUrl);
      }
      return;
    }
    suppressedStatusUrlRef.current = null;
    if (exampleModeRequested) {
      return;
    }
    if (requestedStatusUrl) {
      return;
    }
    if (source.kind === "example") {
      void loadFromUrl(defaultGlobalStatusUrl);
    }
  }, [exampleModeRequested, requestedStatusUrl, search.statusUrl, source.kind, source.label]);

  useEffect(() => {
    if (search.statusUrl && source.kind === "example") {
      return;
    }
    const goalIds = new Set(goalRows.map((row) => row.goal.id));
    if (goalRows.length === 0) {
      if (search.goalId) {
        void navigate({
          search: (current) => ({
            ...current,
            goalId: "",
          }),
        });
      }
      return;
    }
    if (search.goalId && !goalIds.has(search.goalId)
      && goalArchiveLoadState.phase !== "loading") {
      void navigate({
        search: (current) => ({
          ...current,
          goalId: "",
        }),
      });
    }
  }, [goalArchiveLoadState.phase, goalRows, navigate, search.goalId, search.statusUrl, source.kind]);

  useEffect(() => {
    if (!progress || isLoading || !search.goalId || source.kind !== "url") return;
    const goal = progress.directory.goals.find((item) => item.id === search.goalId);
    if (goal?.activation_state === "stopped" && !progress.snapshots[goal.id] && !progress.errors[goal.id]) {
      void loadFromUrl(source.label, { retryOnly: true });
    }
  }, [search.goalId, isLoading, progress, source]);

  function selectGoal(goalId: string) {
    void navigate({
      search: (current) => ({
        ...current,
        goalId,
      }),
    });
  }

  if (statusRequestActive) {
    return (
      <StatusRequestView
        error={loadError}
        isLoading={isLoading}
        onRetry={() => void loadFromUrl(activeStatusRequestUrl || defaultGlobalStatusUrl)}
        requestedUrl={activeStatusRequestUrl || defaultGlobalStatusUrl}
        theme={theme}
        toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
      />
    );
  }

  return (
    <PersonalGoalHome
      goalArchiveLoadState={goalArchiveLoadState}
      initialManagerChatOpen={search.view === "conversation" && !search.goalId}
      isLoading={isLoading}
      onGoalActivationStateChange={(goalId, activationState) => {
        statusRequestFenceRef.current.projectionRevision += 1;
        setPayload((current) => withGoalActivationState(current, goalId, activationState));
        setProgress((current) => current ? { ...current, snapshots: Object.fromEntries(
          Object.entries(current.snapshots).map(([id, snapshot]) => [id, withGoalActivationState(snapshot, goalId, activationState)]),
        ) } : current);
      }}
      onGoalDeleted={(goalId) => {
        statusRequestFenceRef.current.projectionRevision += 1;
        setPayload((current) => withoutGoal(current, goalId));
        setProgress((current) => current ? { ...current, snapshots: Object.fromEntries(
          Object.entries(current.snapshots).filter(([id]) => id !== goalId),
        ) } : current);
      }}
      onSelectGoal={selectGoal}
      onReconcileStatus={(options) => loadFromUrl(
        source.kind === "url" ? source.label : (statusUrl || defaultGlobalStatusUrl),
        { background: true, invalidateGoalIds: options?.invalidateGoalIds, reuseSnapshots: true },
      )}
      onRetryGoalArchive={retryGoalArchive}
      onRefresh={() => loadFromUrl(source.kind === "url" ? source.label : (statusUrl || defaultGlobalStatusUrl), { retryOnly: Boolean(progress && Object.keys(progress.errors).length) })}
      payload={payload}
      progress={progress}
      rows={goalRows}
      selectedGoalId={search.goalId}
      statusSourceControl={statusSourceControl}
      theme={theme}
      toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
    />
  );
}
