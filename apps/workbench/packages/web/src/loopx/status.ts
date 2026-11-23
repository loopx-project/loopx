/**
 * The LoopX core status, read through the loochat proxy (`/api/loopx/*` → the Python service).
 * Batch A keeps a deliberately narrow model: the goal tree, the attention queue and todo counts.
 * The full dashboard projection is much larger; grow this only when a shipped page needs more.
 */

import { t } from '../i18n';
import { parseObservation, type Observation } from './acceptance';

/** One goal as the sidebar and the work page's overview show it. */
export interface LoopxGoal {
  goalId: string;
  parentId: string | null;
  title: string;
  objective: string | null;
  status: string;
  activationState: string;
  lifecyclePhase: string | null;
  updatedAt: string | null;
  /** The agents the core says may work this Goal, when it says any. */
  agents: string[];
  /** The core's read-only acceptance observation: gaps, guards, next action. Null when it sent none. */
  acceptance?: Observation | null;
}

/** One attention-queue row: what the core says is waiting, and on whom. */
export interface LoopxAttention {
  goalId: string;
  status: string | null;
  waitingOn: string | null;
  severity: string | null;
  recommendedAction: string | null;
  /** The core's own words for what the active state should do next, when it says. */
  nextAction: string | null;
  /** The first open todo the projection carries (the user's own first, else the agent's). */
  topTodo: { text: string; priority: string | null; claimedBy: string | null; updatedAt: string | null } | null;
  /** The newest todo touch among what the summary carried; null when it carried none. */
  lastActivityAt: string | null;
  /** The projection carries per-role todo summaries (counts), not the todo rows themselves. */
  userTodos: LoopxTodoCounts;
  agentTodos: LoopxTodoCounts;
}

export interface LoopxTodoCounts {
  open: number;
  done: number;
  total: number;
}

export interface LoopxTodo {
  id: string;
  text: string;
  done: boolean;
}

/** Where a Goal's Lark group stands: bound or not, and which of the automatic notices are on. */
export interface LoopxNotification {
  goalId: string;
  configured: boolean;
  enabled: boolean;
  humanGateAutoNotify: boolean;
  blockedNoticeAutoNotify: boolean;
  targetRef: string | null;
  receiptCount: number;
  lastNotifiedAt: string | null;
}

export interface LoopxStatus {
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  notifications: LoopxNotification[];
  observedAt: string;
}

/** Why no status is shown. The three states a person can tell apart. */
export type LoopxStatusFailure = { kind: 'not-connected' } | { kind: 'unavailable'; detail: string } | { kind: 'bad-data'; detail: string };

export type LoopxStatusState =
  | { state: 'loading' }
  | { state: 'ready'; status: LoopxStatus }
  | { state: 'failed'; failure: LoopxStatusFailure };

/** The goal list sorted the way the sidebar shows it: tree order, then title. */
export function sortGoalTree(goals: LoopxGoal[]): LoopxGoal[] {
  const byParent = new Map<string | null, LoopxGoal[]>();
  for (const goal of goals) {
    const key = goals.some(g => g.goalId === goal.parentId) ? goal.parentId : null;
    byParent.set(key, [...(byParent.get(key) ?? []), goal]);
  }
  const out: LoopxGoal[] = [];
  const walk = (parent: string | null) => {
    for (const goal of (byParent.get(parent) ?? []).sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'))) {
      out.push(goal);
      walk(goal.goalId);
    }
  };
  walk(null);
  return out;
}

/** The goals whose attention rows wait on the person, not on an agent or the outside. */
export function needsYouGoalIds(attention: LoopxAttention[]): Set<string> {
  const ids = new Set<string>();
  for (const item of attention) {
    if (item.userTodos.open > 0 || item.waitingOn === 'user' || item.waitingOn === 'owner') ids.add(item.goalId);
  }
  return ids;
}

interface RawTodoSummary {
  open_count?: number;
  done_count?: number;
  total_count?: number;
  first_open_items?: RawTodoItem[];
  items?: RawTodoItem[];
}

interface RawTodoItem {
  text?: string;
  title?: string;
  done?: boolean;
  priority?: string;
  claimed_by?: string;
  updated_at?: string;
}

interface RawAttention {
  goal_id?: string;
  status?: string;
  waiting_on?: string;
  severity?: string;
  recommended_action?: string;
  active_state_next_action?: string;
  user_todos?: RawTodoSummary;
  agent_todos?: RawTodoSummary;
}

interface RawNotification {
  goal_id?: string;
  configured?: boolean;
  enabled?: boolean;
  human_gate_auto_notify_enabled?: boolean;
  blocked_notice_auto_notify_enabled?: boolean;
  target_ref?: string | null;
  receipt_count?: number;
  last_notified_at?: string | null;
}

interface RawGoal {
  id?: string;
  parent_goal_id?: string | null;
  display_name?: string;
  title?: string;
  goal_objective?: string;
  status?: string;
  activation_state?: string;
  lifecycle_phase?: string;
  updated_at?: string;
  zcode_goal_eligible_agent_ids?: unknown;
  acceptance_observation?: unknown;
}

function todoCounts(raw: RawTodoSummary | undefined): LoopxTodoCounts {
  return { open: Number(raw?.open_count ?? 0), done: Number(raw?.done_count ?? 0), total: Number(raw?.total_count ?? 0) };
}

/** The first todo of a summary, as one honest line: what the core carried, nothing invented. */
function topTodoOf(...summaries: (RawTodoSummary | undefined)[]): LoopxAttention['topTodo'] {
  for (const raw of summaries) {
    const item = raw?.first_open_items?.[0] ?? raw?.items?.find(i => i.done !== true);
    const text = typeof item?.text === 'string' && item.text.trim() ? item.text.trim()
      : typeof item?.title === 'string' && item.title.trim() ? item.title.trim() : null;
    if (text) return {
      text,
      priority: typeof item?.priority === 'string' ? item.priority : null,
      claimedBy: typeof item?.claimed_by === 'string' ? item.claimed_by : null,
      updatedAt: typeof item?.updated_at === 'string' ? item.updated_at : null,
    };
  }
  return null;
}

/** The newest `updated_at` among the todo items the summaries carried. */
function lastActivityOf(...summaries: (RawTodoSummary | undefined)[]): string | null {
  const stamps: number[] = [];
  for (const raw of summaries) for (const item of [...(raw?.items ?? []), ...(raw?.first_open_items ?? [])]) {
    const at = typeof item?.updated_at === 'string' ? Date.parse(item.updated_at) : NaN;
    if (Number.isFinite(at)) stamps.push(at);
  }
  return stamps.length ? new Date(Math.max(...stamps)).toISOString() : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

/** Parse the narrow slice; anything unexpected says so instead of guessing. */
export function parseLoopxStatus(payload: unknown): LoopxStatus {
  if (typeof payload !== 'object' || payload === null) throw new Error('status is not an object');
  const record = payload as Record<string, unknown>;
  const runHistory = (record.run_history ?? {}) as Record<string, unknown>;
  const rawGoals = Array.isArray(runHistory.goals) ? (runHistory.goals as RawGoal[]) : [];
  if (!Array.isArray(runHistory.goals)) throw new Error('run_history.goals is missing');
  // A deleted Goal leaves run-history rows the core still shows ("legacy-runtime": runs without a
  // registry entry). Those are ghosts of the deletion, not Goals the person can work — leave them out.
  const goals: LoopxGoal[] = rawGoals.map(goal => ({
    goalId: String(goal.id ?? ''),
    parentId: goal.parent_goal_id != null && rawGoals.some(g => g.id === goal.parent_goal_id) ? String(goal.parent_goal_id) : null,
    title: String(goal.display_name ?? goal.title ?? goal.id ?? '').trim(),
    objective: text(goal.goal_objective),
    status: String(goal.status ?? goal.activation_state ?? 'unknown'),
    activationState: String(goal.activation_state ?? 'unknown'),
    lifecyclePhase: text(goal.lifecycle_phase),
    updatedAt: text(goal.updated_at),
    agents: Array.isArray(goal.zcode_goal_eligible_agent_ids)
      ? goal.zcode_goal_eligible_agent_ids.filter((id): id is string => typeof id === 'string' && id.trim() !== '')
      : [],
    acceptance: parseObservation(goal.acceptance_observation),
  })).filter(goal => goal.goalId && goal.status !== 'legacy-runtime');
  const attentionQueue = (record.attention_queue ?? {}) as Record<string, unknown>;
  const rawAttention = Array.isArray(attentionQueue.items) ? (attentionQueue.items as RawAttention[]) : [];
  const attention: LoopxAttention[] = rawAttention.map(item => ({
    goalId: String(item.goal_id ?? ''),
    status: text(item.status),
    waitingOn: text(item.waiting_on),
    severity: text(item.severity),
    recommendedAction: text(item.recommended_action),
    nextAction: text(item.active_state_next_action),
    topTodo: topTodoOf(item.user_todos, item.agent_todos),
    lastActivityAt: lastActivityOf(item.user_todos, item.agent_todos),
    userTodos: todoCounts(item.user_todos),
    agentTodos: todoCounts(item.agent_todos),
  })).filter(item => item.goalId);
  const projection = (record.goal_channel_notification_projection ?? {}) as { goals?: RawNotification[] };
  const notifications: LoopxNotification[] = (Array.isArray(projection.goals) ? projection.goals : []).map(row => ({
    goalId: String(row.goal_id ?? ''),
    configured: row.configured === true,
    enabled: row.enabled === true,
    humanGateAutoNotify: row.human_gate_auto_notify_enabled === true,
    blockedNoticeAutoNotify: row.blocked_notice_auto_notify_enabled === true,
    targetRef: text(row.target_ref),
    receiptCount: Number(row.receipt_count ?? 0),
    lastNotifiedAt: text(row.last_notified_at),
  })).filter(row => row.goalId);
  return { goals, attention, notifications, observedAt: text(record.served_at) ?? new Date().toISOString() };
}

/** Fetch and classify. 404 means the proxy is not serving the core yet; that is a state, not an error. */
export async function fetchLoopxStatus(options: { url?: string; token?: string; signal?: AbortSignal } = {}): Promise<LoopxStatus> {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  const response = await fetch(options.url ?? '/api/loopx/status.json', { headers, signal: options.signal });
  if (response.status === 404) throw new LoopxStatusError('not-connected', 'the LoopX core is not proxied');
  if (response.status === 401) throw new LoopxStatusError('unavailable', 'the LoopX core rejected the token');
  if (response.status === 502) {
    // The proxy's own word for "the Python service is down"; keep it, it says more than the status code.
    const body = await response.json().catch(() => null);
    throw new LoopxStatusError('unavailable', body && typeof body === 'object' && 'error' in body ? String((body as { error: unknown }).error) : 'the LoopX core service is down');
  }
  if (!response.ok) throw new LoopxStatusError('unavailable', `status answered ${response.status}`);
  try {
    return parseLoopxStatus(await response.json());
  } catch (error) {
    throw new LoopxStatusError('bad-data', error instanceof Error ? error.message : String(error));
  }
}

export class LoopxStatusError extends Error {
  constructor(readonly kind: LoopxStatusFailure['kind'], detail: string) {
    super(detail);
  }
}

export function loopxFailureOf(error: unknown): LoopxStatusFailure {
  if (error instanceof LoopxStatusError) return { kind: error.kind, detail: error.message };
  return { kind: 'unavailable', detail: error instanceof Error ? error.message : String(error) };
}

/** Shared status-reader error copy; a failed read never establishes Goal absence. */
export function failureLabel(failure: LoopxStatusFailure | null): string {
  if (!failure) return '';
  if (failure.kind === 'not-connected') return t('loopx.notConnected');
  if (failure.kind === 'bad-data') return t('loopx.badData', failure.detail);
  if (failure.detail === 'loopx_core_unavailable') return t('loopx.coreDown');
  return t('loopx.unavailable', failure.detail);
}
