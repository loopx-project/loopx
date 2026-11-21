import type { GoalWorkspaceDigest, GoalWorkDisplayClass } from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import type { CollaborationReadback } from "../../data/chat-model";
import type { RequestControlView } from "../../data/request-control";
import type { GoalHostThread } from "./goal-activity";

/** Mirrors `quietTurnMinutes` from `goal-activity.ts` to keep zero runtime imports for Node test execution. */
const QUIET_TURN_MINUTES = 15;
import type {
  WorkspaceAgentTodo,
  WorkspaceAttention,
  WorkspaceGoal,
  WorkspaceRun,
  WorkspaceTimelineItem,
} from "./personal-workspace-model";

export const WORKER_ROSTER_STATES = [
  "needs_decision",
  "working",
  "lease_expired",
  "waiting",
] as const;

export type WorkerRosterState = (typeof WORKER_ROSTER_STATES)[number];
export type CollaborationLocale = "zh-CN" | "en";

export type ProgressCounter = {
  done: number | null;
  total: number | null;
  open: number | null;
  label: string;
};

export type CollaborationCardRosterStatus = {
  state: WorkerRosterState;
  stateLabel: string;
  scope: string;
  now: string;
  progress: ProgressCounter;
  revisionLabel: string | null;
  readLabel: string;
  decisionLabel: string;
  conclusionLabel: string | null;
  decisionReason: string;
  unavailable: boolean;
};

export type CollaborationRosterSummary = {
  total: number;
  concluded: number;
  counts: Record<WorkerRosterState, number>;
  progress: ProgressCounter;
  workers: Array<{
    requestId: string;
    agentId: string;
    scope: string;
    state: WorkerRosterState;
    stateLabel: string;
    now: string;
    progress: ProgressCounter;
  }>;
};

export type WorkerRosterEntry = {
  agentId: string;
  label: string;
  state: WorkerRosterState;
  stateLabel: string;
  scope: string;
  now: string;
  progress: ProgressCounter;
  hostSurfaces: string[];
  hasConversation: boolean;
};

export type GoalWorkerRoster = {
  workers: WorkerRosterEntry[];
  counts: Record<WorkerRosterState, number>;
  progress: ProgressCounter;
};

export type AttachedWorkerStatusInput = {
  agentId: string;
  label?: string | null;
  connected?: boolean | null;
  canReply?: boolean | null;
  activeTurn: boolean;
  liveActive?: boolean;
  bindingRejected?: boolean;
  bindingError?: string | null;
  turnModel?: { model: string; reasoning_effort?: string | null } | null;
  hostSurfaces?: readonly string[];
  latestFocus?: string | null;
  locale: CollaborationLocale;
};

export type AttachedWorkerStatus = {
  agentId: string;
  label: string;
  state: WorkerRosterState;
  stateLabel: string;
  scope: string;
  now: string;
};

const STATE_PRIORITY: Record<WorkerRosterState, number> = {
  needs_decision: 0,
  working: 1,
  lease_expired: 2,
  waiting: 3,
};

const STATE_LABELS: Record<CollaborationLocale, Record<WorkerRosterState, string>> = {
  "zh-CN": {
    needs_decision: "需决定",
    working: "执行中",
    lease_expired: "租约过期",
    waiting: "等待中",
  },
  en: {
    needs_decision: "Needs decision",
    working: "Working",
    lease_expired: "Lease expired",
    waiting: "Waiting",
  },
};

const CARD_WORDS = {
  "zh-CN": {
    supplied: "已提供给接收方",
    pending: "等待接收方读取",
    unavailable: "暂时无法读取",
    adopt: "已采纳",
    defer: "已暂缓",
    reject: "未采纳",
    no_change: "无需调整",
    unknown: "尚未记录",
    delivered: "回复已送达",
    result: "回复等待送达",
    unverified: "回复送达尚未核验",
    originalHandoff: "等待原会话回传确认",
    revisionPrefix: "请求版本",
    awaitingReadNow: "等待接收方读取交办说明",
    cancelledNow: "请求已取消，已交付任务保留",
    decisionPendingNow: "待确认请求调整或暂缓决定",
    leaseExpiredNow: "读取或回执状态不可用，需核对原会话",
    unverifiedReturnNow: "回复送达尚未核验，请确认原发送记录",
    workingFallbackNow: "已采纳请求，正在推进回传",
    settledNow: "结论已送达",
  },
  en: {
    supplied: "Supplied to receiver",
    pending: "Awaiting receiver read",
    unavailable: "Readback unavailable",
    adopt: "Adopted",
    defer: "Deferred",
    reject: "Rejected",
    no_change: "No change needed",
    unknown: "Not recorded",
    delivered: "Reply delivered",
    result: "Reply awaiting delivery",
    unverified: "Reply delivery unverified",
    originalHandoff: "Awaiting original handoff proof",
    revisionPrefix: "Request revision",
    awaitingReadNow: "Awaiting receiver readback",
    cancelledNow: "Request cancelled; committed work retained",
    decisionPendingNow: "Needs decision on deferred or pending change",
    leaseExpiredNow: "Readback or receipt unavailable; verify in host",
    unverifiedReturnNow: "Reply delivery unverified; check original handoff",
    workingFallbackNow: "Adopted brief; preparing return",
    settledNow: "Conclusion delivered",
  },
} as const;

const ROSTER_WORDS = {
  "zh-CN": {
    standingBy: "待命，等待下一项分配",
    completedStandingBy: "近期任务已完成，待命",
    replanPaused: "需要补充方向或确认，已暂停复核",
    outcomeUnknown: "原请求结果未知，待核实后再继续",
    staleWindow: "宿主投递窗口已过期，待核实连接",
    leaseExpired: "执行租约已过期或未刷新",
    connecting: "正在连接原会话",
    acceptingReplies: "原会话空闲，可直接回复",
    runningTurn: "原会话回合运行中",
    connectionLost: "原会话连接或状态未确认",
    bindingRejected: "无法验证原会话绑定与访问权限",
    openCount: (open: number) => `${open} 待办`,
  },
  en: {
    standingBy: "Standing by for next assignment",
    completedStandingBy: "Recent work completed; standing by",
    replanPaused: "Needs direction; review paused",
    outcomeUnknown: "Original outcome unknown; check settlement",
    staleWindow: "Host delivery window stale; check connection",
    leaseExpired: "Lease expired or turn silent",
    connecting: "Connecting to original conversation",
    acceptingReplies: "Idle and accepting replies",
    runningTurn: "Running active turn in original host",
    connectionLost: "Original conversation unavailable or unconfirmed",
    bindingRejected: "Binding or access verification failed",
    openCount: (open: number) => `${open} open`,
  },
} as const;

// eslint-disable-next-line no-control-regex
const ANSI_PATTERN = /\u001b\[[0-9;]*[A-Za-z]/g;
// eslint-disable-next-line no-control-regex
const CONTROL_PATTERN = /[\u0000-\u0008\u000b-\u001f\u007f]/g;

export function workerRosterStateLabel(state: WorkerRosterState, locale: CollaborationLocale): string {
  return STATE_LABELS[locale][state];
}

export function truncateCodePoints(text: string, maxLength: number): string {
  const safeLimit = Math.max(2, Math.floor(maxLength));
  const points = Array.from(text);
  if (points.length <= safeLimit) return text;
  const trimmed = points
    .slice(0, safeLimit - 1)
    .join("")
    .replace(/[\s,.;:!?，。；：！？\-—/]+$/u, "");
  return `${trimmed || points.slice(0, safeLimit - 1).join("")}…`;
}

function isTerminalOrJsonNoiseLine(line: string): boolean {
  if (!line) return true;
  if (/^(```|~~~|---+|===+|\*{3,})/.test(line)) return true;
  if (/^[{\[]/.test(line) && (/[}\]]$/.test(line) || /"[^"]+"\s*:/.test(line))) return true;
  if (/^(?:\$\s+|(?:npm|pnpm|yarn)\s+(?:run|test|exec|install)\b|(?:uv\s+run|pytest|node\s+--test)\b)/.test(line)) return true;
  if (/^(Traceback\b|File\s+"|at\s+[\w.$<>]+\s*\(|EXIT_CODE[:=]|\[(?:stdout|stderr|debug|trace)\])/i.test(line)) return true;
  if (/^sha256:[a-f0-9]{12,}$/i.test(line)) return true;
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?$/.test(line)) return true;
  if (/^\/Users\/[^/\s]+\/[^\s]*$/.test(line)) return true;
  return false;
}

function cleanSingleLine(line: string): string {
  return line
    .replace(ANSI_PATTERN, "")
    .replace(CONTROL_PATTERN, " ")
    .replace(/\{[^{}]*"[^"]+"\s*:[^{}]*\}/g, " ")
    .replace(/\[[^[\]]*"[^"]+"\s*:[^[\]]*\]/g, " ")
    .replace(/\/(?:Users|home)\/[^/\s]+/g, "~")
    .replace(/\bsha256:[a-f0-9]{12,}\b/gi, "")
    .replace(/\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?\b\s*[-:|]?\s*/g, "")
    .replace(/^([#>*]+\s+|\d+\.\s+)/, "")
    .replace(/^(?:Now|Current|Focus|当前|实时)\s*[:：]\s*/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function sanitizeFocusLine(raw: string | null | undefined, maxLength = 88): string {
  if (!raw) return "";
  const stripped = raw.replace(ANSI_PATTERN, "").replace(CONTROL_PATTERN, "\n");
  const lines = stripped.split(/\r?\n+/).map((entry) => entry.trim()).filter(Boolean);
  for (const candidate of lines) {
    if (isTerminalOrJsonNoiseLine(candidate)) continue;
    const cleaned = cleanSingleLine(candidate);
    if (!cleaned || /^[{}\[\](),.;:!?'"`~\\/|+\-=*_]+$/.test(cleaned)) continue;
    return truncateCodePoints(cleaned, maxLength);
  }
  return "";
}

export function formatProgressCounter(
  done: number | null | undefined,
  total: number | null | undefined,
  open?: number | null,
  locale: CollaborationLocale = "en",
): ProgressCounter {
  const safeDone = typeof done === "number" && Number.isFinite(done) ? Math.max(0, Math.round(done)) : null;
  const safeOpen = typeof open === "number" && Number.isFinite(open) ? Math.max(0, Math.round(open)) : null;
  const safeTotal = typeof total === "number" && Number.isFinite(total) && total >= 0
    ? Math.round(total)
    : safeDone !== null && safeOpen !== null
      ? safeDone + safeOpen
      : null;

  if (safeDone !== null && safeTotal !== null) {
    return { done: safeDone, total: safeTotal, open: safeOpen, label: `[${safeDone}/${safeTotal}]` };
  }
  if (safeDone !== null) {
    return { done: safeDone, total: null, open: safeOpen, label: `[${safeDone}/—]` };
  }
  if (safeOpen !== null) {
    return {
      done: null,
      total: null,
      open: safeOpen,
      label: `[${ROSTER_WORDS[locale].openCount(safeOpen)}]`,
    };
  }
  return { done: null, total: null, open: null, label: "" };
}

function currentConclusion(request: CollaborationReadback, revision: number | undefined) {
  const replies = request.returns.filter(
    (reply) => reply.phase === "conclusion"
      && (revision === undefined || (reply.request_revision ?? 1) === revision),
  );
  return replies.find((reply) => reply.status === "delivered")
    ?? replies.find((reply) => reply.status !== "superseded")
    ?? replies[0];
}

function isConclusionProofMissing(conclusion?: { error?: string | null; status: string }) {
  return conclusion?.error === "initial_delivery_receipt_unavailable"
    && conclusion.status !== "delivered"
    && conclusion.status !== "superseded";
}

function formatScope(parts: Array<string | null | undefined>, fallback = ""): string {
  const cleaned = parts
    .map((part) => sanitizeFocusLine(part, 36))
    .filter(Boolean);
  const unique = [...new Set(cleaned)];
  if (!unique.length) return fallback;
  return truncateCodePoints(unique.join(" / "), 44);
}

export function deriveCollaborationCardStatus({
  request,
  observed,
  unavailable = false,
  locale,
}: {
  request: CollaborationReadback;
  observed?: RequestControlView | null;
  unavailable?: boolean;
  locale: CollaborationLocale;
}): CollaborationCardRosterStatus {
  const c = CARD_WORDS[locale];
  const brief = observed?.brief ?? request.brief;
  const revision = observed?.revision ?? request.request_revision;
  const readStatus = unavailable
    ? "unavailable"
    : observed
      ? observed.read_readback === "recorded"
        ? "supplied"
        : observed.read_readback === "unavailable"
          ? "unavailable"
          : "pending"
      : request.read_status;
  const recordedDecision = unavailable
    ? "unavailable"
    : observed
      ? observed.assessment?.decision ?? (observed.assessment_readback === "unavailable" ? "unavailable" : "pending")
      : request.decision;
  const controlState = observed?.control_state ?? request.request_control?.control_state ?? null;
  const conclusion = currentConclusion(request, revision);
  const decisionReason = sanitizeFocusLine(observed ? observed.assessment?.reason : request.decision_reason, 120);

  const decisionLabel = (() => {
    switch (recordedDecision) {
      case "adopt": return c.adopt;
      case "defer": return c.defer;
      case "reject": return c.reject;
      case "no_change": return c.no_change;
      case "unavailable": return c.unavailable;
      default: return c.unknown;
    }
  })();

  const readLabel = readStatus === "supplied"
    ? c.supplied
    : readStatus === "unavailable"
      ? c.unavailable
      : c.pending;

  const proofMissing = isConclusionProofMissing(conclusion);
  const conclusionLabel = conclusion
    ? proofMissing
      ? c.originalHandoff
      : conclusion.status === "delivered"
        ? c.delivered
        : conclusion.status === "queued"
          ? c.result
          : c.unverified
    : null;

  const hasExpiredReadback = unavailable
    || readStatus === "unavailable"
    || recordedDecision === "unavailable"
    || proofMissing
    || conclusion?.status === "explicit_unverified"
    || conclusion?.status === "failed";

  const needsDecision = recordedDecision === "defer"
    || recordedDecision === "reject"
    || controlState === "effect_pending"
    || controlState === "cancellation_pending";

  const isAdoptedActive = !unavailable
    && recordedDecision === "adopt"
    && controlState !== "cancelled"
    && conclusion?.status !== "delivered"
    && conclusion?.status !== "superseded"
    && !hasExpiredReadback;

  const state: WorkerRosterState = hasExpiredReadback
    ? "lease_expired"
    : needsDecision
      ? "needs_decision"
      : isAdoptedActive
        ? "working"
        : "waiting";

  const stepDone = conclusion?.status === "delivered"
    ? 3
    : ["adopt", "defer", "reject", "no_change"].includes(recordedDecision)
      ? 2
      : readStatus === "supplied"
        ? 1
        : 0;

  const cleanPurpose = sanitizeFocusLine(brief.purpose, 72);
  const cleanReturnReq = sanitizeFocusLine(brief.return_requirement, 72);
  const cleanContext = sanitizeFocusLine(brief.context, 72);
  const cleanAcceptance = sanitizeFocusLine(brief.acceptance[0], 72);

  const now = (() => {
    if (controlState === "cancelled") return c.cancelledNow;
    if (hasExpiredReadback) return proofMissing ? c.unverifiedReturnNow : c.leaseExpiredNow;
    if (needsDecision) return decisionReason || cleanContext || c.decisionPendingNow;
    if (conclusion?.status === "delivered") return decisionReason || cleanReturnReq || c.settledNow;
    if (recordedDecision === "adopt") return decisionReason || cleanReturnReq || cleanAcceptance || c.workingFallbackNow;
    if (readStatus !== "supplied") return cleanReturnReq || cleanContext || c.awaitingReadNow;
    return [cleanReturnReq, cleanAcceptance, cleanContext].find((candidate) => candidate && candidate !== cleanPurpose)
      || decisionReason
      || c.workingFallbackNow;
  })();

  return {
    state,
    stateLabel: workerRosterStateLabel(state, locale),
    scope: formatScope([request.goal_id, request.agent_id], request.agent_id),
    now: sanitizeFocusLine(now, 88),
    progress: formatProgressCounter(stepDone, 3, null, locale),
    revisionLabel: revision !== undefined ? `${c.revisionPrefix} ${revision}` : null,
    readLabel,
    decisionLabel,
    conclusionLabel,
    decisionReason,
    unavailable,
  };
}

export function deriveCollaborationRosterSummary(
  requests: readonly CollaborationReadback[],
  locale: CollaborationLocale,
): CollaborationRosterSummary {
  const counts: Record<WorkerRosterState, number> = {
    needs_decision: 0,
    working: 0,
    lease_expired: 0,
    waiting: 0,
  };
  let concluded = 0;
  const workers = requests.map((request) => {
    const status = deriveCollaborationCardStatus({ request, locale });
    counts[status.state] += 1;
    if (status.progress.done === 3) concluded += 1;
    return {
      requestId: request.request_id,
      agentId: request.agent_id,
      scope: status.scope,
      state: status.state,
      stateLabel: status.stateLabel,
      now: status.now,
      progress: status.progress,
    };
  });
  return {
    total: requests.length,
    concluded,
    counts,
    progress: formatProgressCounter(concluded, requests.length, null, locale),
    workers,
  };
}

function pickPrioritizedTodo(
  todos: readonly WorkspaceAgentTodo[],
  rowById: ReadonlyMap<string, { displayClass: GoalWorkDisplayClass }>,
): WorkspaceAgentTodo | undefined {
  const order: Record<GoalWorkDisplayClass, number> = {
    running_observed: 0,
    needs_you: 1,
    blocked: 2,
    open: 3,
    waiting_dependency: 4,
    deferred: 5,
    done: 6,
  };
  return [...todos]
    .filter((todo) => !todo.done)
    .sort((left, right) => {
      const leftClass = rowById.get(left.todoId)?.displayClass ?? "open";
      const rightClass = rowById.get(right.todoId)?.displayClass ?? "open";
      return order[leftClass] - order[rightClass];
    })[0];
}

function isFreshHostTurn(thread: GoalHostThread, nowMs: number, quietMinutes = QUIET_TURN_MINUTES): boolean {
  if (thread.state !== "turn_open") return false;
  if (thread.deliveryWindow?.state === "stale" || thread.deliveryWindow?.state === "missing") return false;
  const stamp = thread.lastEventAt ?? thread.turnStartedAt;
  const parsed = stamp ? Date.parse(stamp) : Number.NaN;
  if (Number.isNaN(parsed)) return false;
  return nowMs - parsed <= quietMinutes * 60_000;
}

function isExpiredOrStaleHostThread(thread: GoalHostThread, nowMs: number, quietMinutes = QUIET_TURN_MINUTES): boolean {
  if (thread.state === "unknown") return true;
  if (thread.deliveryWindow?.state === "stale" || thread.deliveryWindow?.state === "missing") return true;
  if (thread.state === "turn_open") {
    const stamp = thread.lastEventAt ?? thread.turnStartedAt;
    const parsed = stamp ? Date.parse(stamp) : Number.NaN;
    if (Number.isNaN(parsed) || nowMs - parsed > quietMinutes * 60_000) return true;
  }
  return false;
}

export function deriveGoalWorkerRoster({
  goal,
  digest = null,
  items = [],
  userTodos = [],
  locale,
  nowMs = Date.now(),
}: {
  goal: WorkspaceGoal;
  digest?: GoalWorkspaceDigest | null;
  items?: readonly WorkspaceTimelineItem[];
  userTodos?: readonly WorkspaceAttention[];
  locale: CollaborationLocale;
  nowMs?: number;
}): GoalWorkerRoster {
  const w = ROSTER_WORDS[locale];
  const counts: Record<WorkerRosterState, number> = {
    needs_decision: 0,
    working: 0,
    lease_expired: 0,
    waiting: 0,
  };
  const laneById = new Map((goal.agentLanes ?? []).map((lane) => [lane.agentId, lane]));
  const registeredSet = new Set(goal.registeredAgentIds ?? []);
  const runsByAgent = new Map<string, WorkspaceRun[]>();
  const runningTodoIds = new Set<string>();
  for (const item of items) {
    if (item.kind !== "run" || item.run.goalId !== goal.goalId) continue;
    const list = runsByAgent.get(item.run.agentId) ?? [];
    list.push(item.run);
    runsByAgent.set(item.run.agentId, list);
    if (item.run.status === "running" && item.run.todoId) runningTodoIds.add(item.run.todoId);
  }
  const digestRows: ReadonlyArray<{ todoId: string; ownerId: string | null; displayClass: GoalWorkDisplayClass }> = digest
    ? digest.work.rows
    : goal.agentTodos.filter((todo) => !todo.done).map((todo) => ({
      todoId: todo.todoId,
      ownerId: todo.claimedBy ?? null,
      displayClass: todo.status === "blocked"
        ? "blocked"
        : todo.status === "deferred"
          ? "deferred"
          : runningTodoIds.has(todo.todoId)
            ? "running_observed"
            : "open",
    }));
  const rowById = new Map(digestRows.map((row) => [row.todoId, row]));
  const coverageComplete = digest
    ? digest.work.coverage === "complete"
    : goal.openTodoCount === undefined || goal.agentTodos.filter((todo) => !todo.done).length >= goal.openTodoCount;
  const goalAttention = userTodos.filter((item) => item.goalId === goal.goalId);

  const discoveredIds = [
    ...(goal.agentLanes ?? []).map((lane) => lane.agentId),
    ...(goal.registeredAgentIds ?? []),
    ...(goal.workerObservations?.rows ?? []).map((row) => row.agent_id),
    ...(goal.hostThreadActivity?.threads ?? []).flatMap((thread) => (thread.agentId ? [thread.agentId] : [])),
    ...digestRows.flatMap((row) => (row.ownerId ? [row.ownerId] : [])),
    ...runsByAgent.keys(),
  ];
  const workerIds = [...new Set(discoveredIds.filter(Boolean))];

  const workers: WorkerRosterEntry[] = workerIds.map((agentId) => {
    const lane = laneById.get(agentId);
    const label = sanitizeFocusLine(lane?.label, 48) || agentId;
    const workerRuns = runsByAgent.get(agentId) ?? [];
    const activeRun = workerRuns.find((run) => run.status === "running");
    const failedRun = workerRuns.find((run) => run.status === "failed" || run.sessionStatus === "resume_failed");
    const workerTodos = goal.agentTodos.filter((todo) => todo.claimedBy === agentId);
    const workerRows = digestRows.filter((row) => row.ownerId === agentId);
    const threads = (goal.hostThreadActivity?.threads ?? []).filter((thread) => thread.agentId === agentId);
    const observations = (goal.workerObservations?.rows ?? []).filter((row) => row.agent_id === agentId);

    const freshThread = threads.some((thread) => isFreshHostTurn(thread, nowMs));
    const staleThread = threads.find((thread) => isExpiredOrStaleHostThread(thread, nowMs));
    const pausedObs = observations.find((row) => row.status === "replan_paused");
    const unknownObs = observations.find((row) => row.status === "replan_outcome_unknown");
    const expiredTodo = workerTodos.find(
      (todo) => !todo.done && (
        (todo.resumeInvalidReason && todo.resumeInvalidReason !== "dependency_cycle")
        || (todo.expiresAt && !Number.isNaN(Date.parse(todo.expiresAt)) && Date.parse(todo.expiresAt) < nowMs)
      ),
    );
    const laneStateNormalized = lane?.state?.trim().toLowerCase() ?? "";
    const laneExpired = laneStateNormalized === "lease_expired" || laneStateNormalized === "stale" || laneStateNormalized === "expired";

    const hasExpiredLease = Boolean(staleThread || unknownObs || expiredTodo || laneExpired || failedRun?.sessionStatus === "resume_failed");
    const hasObservedActive = Boolean(activeRun) || freshThread || (!hasExpiredLease && workerRows.some((row) => row.displayClass === "running_observed"));

    const matchingAttention = goalAttention.find((item) =>
      item.blocking && workerTodos.some((todo) => todo.todoId === item.todoId || (todo.dependencies ?? []).includes(item.todoId)),
    );
    const blockingTodo = workerTodos.find((todo) => !todo.done && (todo.status === "blocked" || rowById.get(todo.todoId)?.displayClass === "needs_you" || rowById.get(todo.todoId)?.displayClass === "blocked"));
    const hasDecisionGate = Boolean(pausedObs || blockingTodo || matchingAttention);

    const state: WorkerRosterState = pausedObs
      ? "needs_decision"
      : hasExpiredLease && !activeRun && !freshThread
        ? "lease_expired"
        : hasObservedActive
          ? "working"
          : hasDecisionGate
            ? "needs_decision"
            : "waiting";

    counts[state] += 1;

    const hostSurfaces = [...new Set([...(lane?.hostSurfaces ?? []), ...threads.map((thread) => thread.hostSurface)].filter(Boolean))];
    const domains = [...new Set(workerTodos.map((todo) => todo.taskDomain).filter((value): value is string => Boolean(value && value.trim())))];
    const scope = formatScope(
      domains.length
        ? domains.slice(0, 2)
        : hostSurfaces.length
          ? [hostSurfaces[0], label !== agentId ? agentId : null]
          : [label !== agentId ? agentId : goal.repository?.branch ?? goal.goalId],
      agentId,
    );

    const openTodos = workerTodos.filter((todo) => !todo.done);
    const doneTodos = workerTodos.filter((todo) => todo.done);
    const runProgress = activeRun && activeRun.totalSteps > 0
      ? formatProgressCounter(activeRun.completedSteps, activeRun.totalSteps, null, locale)
      : null;
    const progress = (workerTodos.length > 0 && (coverageComplete || doneTodos.length > 0))
      ? formatProgressCounter(doneTodos.length, doneTodos.length + openTodos.length, openTodos.length, locale)
      : runProgress
        ? runProgress
        : openTodos.length > 0
          ? formatProgressCounter(null, null, openTodos.length, locale)
          : formatProgressCounter(null, null, null, locale);

    const prioritizedTodo = pickPrioritizedTodo(workerTodos, rowById);
    const rawNow = (() => {
      if (state === "working") {
        return sanitizeFocusLine(activeRun?.latestActivity, 84)
          || sanitizeFocusLine(activeRun?.title, 84)
          || sanitizeFocusLine(prioritizedTodo?.text, 84)
          || w.runningTurn;
      }
      if (state === "needs_decision") {
        return sanitizeFocusLine(blockingTodo?.blockedReason, 84)
          || sanitizeFocusLine(matchingAttention?.text, 84)
          || sanitizeFocusLine(blockingTodo?.text, 84)
          || (pausedObs ? w.replanPaused : w.standingBy);
      }
      if (state === "lease_expired") {
        if (unknownObs) return w.outcomeUnknown;
        if (staleThread?.deliveryWindow?.state === "stale" || staleThread?.deliveryWindow?.state === "missing") return w.staleWindow;
        return sanitizeFocusLine(prioritizedTodo?.text, 84) || w.leaseExpired;
      }
      return sanitizeFocusLine(prioritizedTodo?.text, 84)
        || sanitizeFocusLine(prioritizedTodo?.resumeWhen, 84)
        || sanitizeFocusLine(workerRuns[0]?.latestActivity, 84)
        || (doneTodos.length > 0 ? w.completedStandingBy : w.standingBy);
    })();

    return {
      agentId,
      label,
      state,
      stateLabel: workerRosterStateLabel(state, locale),
      scope,
      now: sanitizeFocusLine(rawNow, 84),
      progress,
      hostSurfaces,
      hasConversation: registeredSet.has(agentId),
    };
  });

  workers.sort((left, right) => {
    const priorityDiff = STATE_PRIORITY[left.state] - STATE_PRIORITY[right.state];
    return priorityDiff !== 0 ? priorityDiff : left.label.localeCompare(right.label);
  });

  const overallDone = digest ? digest.work.done : (goal.doneTodoCount ?? null);
  const overallOpen = digest ? digest.work.open : (goal.openTodoCount ?? goal.agentTodos.filter((todo) => !todo.done).length);
  const overallTotal = digest ? digest.work.total : (overallDone !== null ? overallDone + overallOpen : null);

  return {
    workers,
    counts,
    progress: formatProgressCounter(overallDone, overallTotal, overallOpen, locale),
  };
}

export function deriveAttachedWorkerStatus({
  agentId,
  label,
  connected,
  canReply,
  activeTurn,
  liveActive = false,
  bindingRejected = false,
  bindingError,
  turnModel,
  hostSurfaces = [],
  latestFocus,
  locale,
}: AttachedWorkerStatusInput): AttachedWorkerStatus {
  const w = ROSTER_WORDS[locale];
  const state: WorkerRosterState = bindingRejected || Boolean(bindingError) || connected === false
    ? "lease_expired"
    : liveActive || activeTurn
      ? "working"
      : canReply === false && !activeTurn && connected !== undefined
        ? "lease_expired"
        : "waiting";

  const scope = formatScope(
    [hostSurfaces[0] ?? "codex-app", label && label !== agentId ? agentId : null],
    agentId,
  );
  const modelText = turnModel
    ? [turnModel.model, turnModel.reasoning_effort].filter(Boolean).join(" · ")
    : "";
  const now = sanitizeFocusLine(latestFocus, 84) || (
    bindingRejected
      ? w.bindingRejected
      : bindingError
        ? sanitizeFocusLine(bindingError, 84) || w.connectionLost
        : state === "lease_expired"
          ? w.connectionLost
          : state === "working"
            ? (modelText ? `${w.runningTurn} · ${modelText}` : w.runningTurn)
            : canReply
              ? w.acceptingReplies
              : w.connecting
  );

  return {
    agentId,
    label: sanitizeFocusLine(label, 48) || agentId,
    state,
    stateLabel: workerRosterStateLabel(state, locale),
    scope,
    now,
  };
}
