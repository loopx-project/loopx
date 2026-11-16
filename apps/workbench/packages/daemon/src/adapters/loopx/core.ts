import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { coreUrl } from './proxy.js';

/** The core refused or failed; `status` is the core's own, `code` its error_code when it gave one. */
export class CoreError extends Error {
  constructor(readonly status: number, message: string, readonly code?: string, readonly body?: unknown) { super(message); }
}

export async function coreJson<T = any>(path: string, init: { method?: string; body?: unknown } = {}, core: URL = coreUrl()): Promise<T> {
  let res: Response;
  try {
    res = await fetch(new URL(path, core), {
      method: init.method ?? (init.body === undefined ? 'GET' : 'POST'),
      headers: init.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: AbortSignal.timeout(60000),
    });
  } catch (err) {
    throw new CoreError(502, `LoopX 核心不可用（${core.origin}）：${err instanceof Error ? err.message : err}`, 'loopx_core_unavailable');
  }
  const text = await res.text();
  let body: any = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
  if (!res.ok) throw new CoreError(res.status, String(body?.error ?? body?.message ?? text.slice(0, 200) ?? res.status), body?.error_code, body);
  return body as T;
}

/** The token the core's goal.create gives a server-configured workspace root: sha256 of the path, first 12 hex. */
export function workspaceRef(root: string): string {
  return `workspace-${createHash('sha256').update(root).digest('hex').slice(0, 12)}`;
}

export interface CoreProject { title: string; goal_workspace_ref?: string }

/**
 * The directories the core lets a Goal be created in, by their opaque creation reference and display title.
 * The core does not say their paths; a directory chosen here is matched by computing its reference.
 * Others need the core restarted with --scan-path.
 */
export async function grantedWorkspaces(core?: URL): Promise<{ ref: string; title: string }[]> {
  const res = await coreJson<{ projects: CoreProject[] }>('/api/chat/projects', {}, core);
  return res.projects.flatMap((p) => p.goal_workspace_ref ? [{ ref: p.goal_workspace_ref, title: p.title }] : []);
}

/** The project root a Goal is registered under, read from the registry the status CLI reads. Null when it is not listed. */
export async function registeredGoalRoot(goalId: string, registry: string): Promise<string | null> {
  const raw = JSON.parse(await readFile(registry, 'utf8')) as { goals?: { id?: unknown; repo?: unknown }[] };
  const goal = (raw.goals ?? []).find((g) => g?.id === goalId);
  return goal && typeof goal.repo === 'string' && goal.repo ? goal.repo : null;
}

export interface GoalDraft {
  goalId: string;
  title: string;
  objective: string;
  completion: string;
  boundary: string;
  goalAgentId: string;
  workspaceRef: string;
  /** In the core's words (see corePermission). */
  permission: string;
  operationId: string;
  /** The words the Goal's own fields are written in (the first task, the labels in the objective). Defaults to Chinese, as the core's first users read it. */
  locale?: 'zh' | 'en';
}

/** `zh` for a Chinese Accept-Language, English for anything else. */
export const goalLocale = (acceptLanguage: unknown): 'zh' | 'en' => (typeof acceptLanguage === 'string' && /^\s*zh/i.test(acceptLanguage) ? 'zh' : 'en');

const WORDS = {
  zh: { create: (title: string) => `创建 Goal：${title}`, completion: '完成条件', boundary: '执行边界', advanceUntil: (c: string) => `推进目标直到满足：${c}`, advance: (o: string) => `推进目标：${o}` },
  en: { create: (title: string) => `Create Goal: ${title}`, completion: 'Completion', boundary: 'Boundary', advanceUntil: (c: string) => `Advance the goal until it is met: ${c}`, advance: (o: string) => `Advance the goal: ${o}` },
} as const;

/** The goal.create review request, shaped as the dashboard shapes it, plus open_session:false (loochat opens the thread). */
export function goalCreateRequest(d: GoalDraft) {
  const compact = (s: string) => s.replace(/\s+/g, ' ').trim();
  const words = WORDS[d.locale ?? 'zh'], sep = d.locale === 'en' ? ': ' : '：';
  const objective = compact(d.objective), completion = compact(d.completion), boundary = compact(d.boundary);
  return {
    action_kind: 'goal.create',
    context: { kind: 'manager', goal_id: null },
    idempotency_key: `workbench-goal.create-${d.operationId}`,
    summary: words.create(d.title),
    normalized_parameters: {
      goal_id: d.goalId,
      title: d.title,
      objective: compact([objective, completion ? `${words.completion}${sep}${completion}` : '', boundary ? `${words.boundary}${sep}${boundary}` : ''].filter(Boolean).join('\n')),
      ...(completion ? { completion_criteria: completion } : {}),
      ...(boundary ? { execution_boundary: boundary } : {}),
      initial_todos: [(completion ? words.advanceUntil(completion) : words.advance(objective)).slice(0, 1000)],
      agent_id: d.goalAgentId,
      permission: d.permission,
      workspace_ref: d.workspaceRef,
      heartbeat: { enabled: false, cadence: '1d', timezone: 'Asia/Shanghai' },
      stop_condition: 'goal_complete',
      open_session: false,
    },
  };
}

export const previewGoalCreate = (d: GoalDraft, core?: URL) => coreJson<{ proposal: any }>('/api/actions/preview', { body: goalCreateRequest(d) }, core);
export const loadProposal = (id: string, core?: URL) => coreJson<{ proposal: any }>(`/api/actions/${encodeURIComponent(id)}`, {}, core);
export const applyProposal = (id: string, core?: URL) => coreJson<{ proposal: any; turn: unknown }>(`/api/actions/${encodeURIComponent(id)}/apply`, { body: {} }, core);
