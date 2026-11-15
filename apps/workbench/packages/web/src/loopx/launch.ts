/** The daemon's LoopX Goal routes (`/api/loopx/...`): who can hold a Goal, launching one, and its bar. */
import { localeTag } from '../i18n';

export type GoalPermission = 'read_only' | 'workspace_auto';

export interface GoalBinding {
  goalId: string;
  goalAgentId: string;
  managedAgentId: string | null;
  goalInstanceId: string | null;
  workspace: string;
  permission: GoalPermission;
}

/** The binding a thread carries when it is a Goal conversation; absent on an ordinary thread. */
export const goalOf = (thread: object | null | undefined): GoalBinding | null => (thread as { loopx?: GoalBinding | null } | null | undefined)?.loopx ?? null;

export interface GoalAgentCapability {
  agent: string;
  available: boolean;
  reason?: string;
  permissions: { id: GoalPermission; available: boolean; reason?: string; isDefault?: boolean }[];
}

export interface GoalTodoGroup { total: number; open: number; items: { id: string; text: string; status: string }[]; complete: boolean }
export interface GoalState {
  goalId: string; title: string; status: string; summary?: string; waitingOn?: string; activation?: string;
  agentTodos: GoalTodoGroup; userTodos: GoalTodoGroup; updatedAt?: string;
}
export interface GoalBarRead {
  binding: GoalBinding;
  state: GoalState | null;
  freshness: 'fresh' | 'unavailable';
  observedAt: string;
  error?: string;
}

export interface GoalPreview {
  proposalId: string; goalId: string; title: string; workspace: string; agent: string; goalAgent: string;
  permission: GoalPermission; objective: string; completion: string; boundary: string;
}

/** The daemon refused or the core was unreachable. `code` names the case the card handles itself. */
export class LoopxError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string, readonly candidates?: { path: string; title: string }[]) { super(message); }
}

async function call<T>(token: string | undefined, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: { Authorization: `Bearer ${token ?? ''}`, 'Accept-Language': localeTag(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const json = await res.json().catch(() => ({})) as { error?: string; code?: string; candidates?: { path: string; title: string }[] };
  if (!res.ok) throw new LoopxError(json.error ?? `HTTP ${res.status}`, res.status, json.code, json.candidates);
  return json as T;
}

export const fetchGoalCapabilities = (token: string | undefined, signal?: AbortSignal) =>
  call<{ agents: GoalAgentCapability[] }>(token, '/api/loopx/capabilities', undefined, signal).then((r) => r.agents);

export interface LaunchInput { agent: string; cwd: string; permission: GoalPermission; objective: string; completion?: string; boundary?: string }
export const previewGoal = (token: string | undefined, input: LaunchInput, signal?: AbortSignal) =>
  call<GoalPreview>(token, '/api/loopx/launch/preview', input, signal);

export interface ApplyInput { proposalId: string; agent: string; permission: GoalPermission; model?: string | null; effort?: string | null; text?: string }
export const applyGoal = (token: string | undefined, input: ApplyInput) =>
  call<{ threadId: string; goalId: string; turnId?: string | null; reused?: boolean }>(token, '/api/loopx/launch/apply', { ...input, model: input.model ?? undefined, effort: input.effort ?? undefined });

export const fetchGoalBar = (token: string | undefined, threadId: string, refresh = false, signal?: AbortSignal) =>
  call<GoalBarRead>(token, `/api/loopx/goal/${encodeURIComponent(threadId)}${refresh ? '?refresh=1' : ''}`, undefined, signal);

/** Which permission a new Goal conversation starts under for this agent: the agent's own default, else the first available. */
export function defaultGoalPermission(cap: GoalAgentCapability | undefined): GoalPermission {
  return cap?.permissions.find((p) => p.available && p.isDefault)?.id ?? cap?.permissions.find((p) => p.available)?.id ?? 'read_only';
}

const ZH = localeTag().toLowerCase().startsWith('zh');
/** Short copy for this adapter; the hub's own i18n tables stay free of it. */
export const lx = (zh: string, en: string) => (ZH ? zh : en);

export const PERMISSION_COPY: Record<GoalPermission, { label: string; hint: string }> = {
  read_only: { label: lx('只读', 'Read only'), hint: lx('可以读项目，不能写任何文件；没人盯着也安全', 'Can read the project, cannot write; safe unattended') },
  workspace_auto: { label: lx('工作区内自动', 'Auto in workspace'), hint: lx('只能写这个目录，由沙箱强制；没人盯着也能继续跑', 'Writes only this directory, enforced by the sandbox; keeps going unattended') },
};

/** The core's status words, as the sidebar and the work page show them, kept short for the bar. */
export const STATUS_WORDS: Record<string, string> = {
  connected_without_run: lx('已关联，待执行', 'Connected, awaiting work'),
  active_state_agent_todo: lx('有待推进的任务', 'Work to continue'),
  state_refreshed: lx('状态已刷新', 'State refreshed'),
};
