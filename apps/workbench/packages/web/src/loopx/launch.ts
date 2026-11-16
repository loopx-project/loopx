/** The daemon's LoopX Goal routes (`/api/loopx/...`): who can hold a Goal, launching one, and its bar. */
import { localeTag, t } from '../i18n';

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
  constructor(message: string, readonly status: number, readonly code?: string, readonly candidates?: { title: string }[]) { super(message); }
}

async function call<T>(token: string | undefined, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', signal,
    headers: { Authorization: `Bearer ${token ?? ''}`, 'Accept-Language': localeTag(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const json = await res.json().catch(() => ({})) as { error?: string; code?: string; candidates?: { title: string }[] };
  if (!res.ok) throw new LoopxError(json.error ?? `HTTP ${res.status}`, res.status, json.code, json.candidates);
  return json as T;
}

export const fetchGoalCapabilities = (token: string | undefined, signal?: AbortSignal) =>
  call<{ agents: GoalAgentCapability[] }>(token, '/api/loopx/capabilities', undefined, signal).then((r) => r.agents);

/** The directories the core lets a Goal be created in; `path` is where that is here, null when this machine cannot tell. */
export interface GoalWorkspace { ref: string; title: string; path: string | null }
export const fetchGoalWorkspaces = (token: string | undefined, signal?: AbortSignal) =>
  call<{ workspaces: GoalWorkspace[] }>(token, '/api/loopx/workspaces', undefined, signal).then((r) => r.workspaces);

export interface LaunchInput { agent: string; cwd: string; permission: GoalPermission; objective: string; completion?: string; boundary?: string }
export const previewGoal = (token: string | undefined, input: LaunchInput, signal?: AbortSignal) =>
  call<GoalPreview>(token, '/api/loopx/launch/preview', input, signal);

export interface ApplyInput { proposalId: string; agent: string; permission: GoalPermission; model?: string | null; effort?: string | null; text?: string }
export const applyGoal = (token: string | undefined, input: ApplyInput) =>
  call<{ threadId: string; goalId: string; turnId?: string | null; reused?: boolean; delivery?: 'uncertain' }>(token, '/api/loopx/launch/apply', { ...input, model: input.model ?? undefined, effort: input.effort ?? undefined });

export const fetchGoalBar = (token: string | undefined, threadId: string, refresh = false, signal?: AbortSignal) =>
  call<GoalBarRead>(token, `/api/loopx/goal/${encodeURIComponent(threadId)}${refresh ? '?refresh=1' : ''}`, undefined, signal);

/** Which permission a new Goal conversation starts under for this agent: the agent's own default, else the first available. */
export function defaultGoalPermission(cap: GoalAgentCapability | undefined): GoalPermission {
  return cap?.permissions.find((p) => p.available && p.isDefault)?.id ?? cap?.permissions.find((p) => p.available)?.id ?? 'read_only';
}

/** Read when rendered, so a language switch applies at once. */
export const permissionCopy = (p: GoalPermission): { label: string; hint: string } =>
  p === 'read_only' ? { label: t('loopx.goal.perm.read_only'), hint: t('loopx.goal.perm.read_only.hint') }
    : { label: t('loopx.goal.perm.workspace_auto'), hint: t('loopx.goal.perm.workspace_auto.hint') };

/** The core's status words for the bar; a status the hub has no word for shows as the core said it, without the underscores. */
export function statusWord(status: string): string {
  switch (status) {
    case 'connected_without_run': return t('loopx.goal.status.connected_without_run');
    case 'active_state_agent_todo': return t('loopx.goal.status.active_state_agent_todo');
    case 'active_state_user_gate': return t('loopx.goal.status.active_state_user_gate');
    case 'active_state_user_todo': return t('loopx.goal.status.active_state_user_todo');
    case 'state_refreshed': return t('loopx.goal.status.state_refreshed');
    case 'state_incomplete': return t('loopx.goal.status.state_incomplete');
    case 'state_projection_gap': return t('loopx.goal.status.state_projection_gap');
    case 'run_artifact_missing': return t('loopx.goal.status.run_artifact_missing');
    case 'unknown': return t('loopx.goal.status.unknown');
    default: return status.replace(/_/g, ' ');
  }
}
