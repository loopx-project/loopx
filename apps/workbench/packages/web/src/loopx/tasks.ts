/**
 * A Goal's tasks as the scoped status lists them (`/api/loopx/status.json?goal_id=`): each role's todo rows
 * with their own status, priority, owner and class, next to the counts the core holds. The core may list fewer
 * rows than it counts; `hiddenOpen`/`hiddenDone` say how many, and the board shows that number instead of pretending to be whole.
 */
import { LoopxStatusError } from './status';

export type TaskState = 'open' | 'blocked' | 'deferred' | 'done';
export type TaskRole = 'user' | 'agent';

export interface Task {
  id: string;
  role: TaskRole;
  /** The title when the core sent one, else the text without its `[P1]` prefix. */
  title: string;
  state: TaskState;
  /** P0..P4, or null when unprioritised. */
  priority: string | null;
  owner: string | null;
  taskClass: string | null;
  archived: boolean;
  updatedAt: string | null;
  blockedReason: string | null;
  resumeWhen: string | null;
}

export interface TaskGroup {
  role: TaskRole;
  tasks: Task[];
  total: number;
  open: number;
  done: number;
  /** Open and done rows the core counted but did not list this time. */
  hiddenOpen: number;
  hiddenDone: number;
}

export interface GoalTasks { goalId: string; user: TaskGroup; agent: TaskGroup; observedAt: string }

type Raw = Record<string, unknown>;
const rec = (v: unknown): Raw => (typeof v === 'object' && v !== null && !Array.isArray(v) ? v as Raw : {});
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const int = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null);

const PRIORITY_PREFIX = /^\s*\[(P[0-4])\]\s*/;

function parseTask(raw: unknown, role: TaskRole): Task | null {
  const r = rec(raw), id = str(r.todo_id), text = str(r.text) ?? str(r.title);
  if (!id || !text) return null;
  const status = str(r.status);
  const state: TaskState = r.done === true || status === 'done' ? 'done' : status === 'blocked' ? 'blocked' : status === 'deferred' ? 'deferred' : 'open';
  const prefix = PRIORITY_PREFIX.exec(text)?.[1] ?? null;
  const priority = str(r.priority) ?? prefix;
  return {
    id, role, state, priority,
    title: str(r.title) ?? text.replace(PRIORITY_PREFIX, ''),
    owner: str(r.claimed_by),
    taskClass: str(r.task_class),
    archived: str(r.archive_state) !== null && r.archive_state !== 'active',
    updatedAt: str(r.updated_at),
    blockedReason: str(r.blocked_reason),
    resumeWhen: str(r.resume_when),
  };
}

function parseGroup(raw: unknown, role: TaskRole): TaskGroup {
  const g = rec(raw);
  const tasks = (Array.isArray(g.items) ? g.items : []).flatMap(item => { const t = parseTask(item, role); return t ? [t] : []; });
  const total = int(g.total_count) ?? tasks.length;
  const done = int(g.done_count) ?? tasks.filter(t => t.state === 'done').length;
  const open = int(g.open_count) ?? Math.max(0, total - done);
  const listedDone = tasks.filter(t => t.state === 'done').length;
  return { role, tasks, total, open, done, hiddenOpen: Math.max(0, open - (tasks.length - listedDone)), hiddenDone: Math.max(0, done - listedDone) };
}

/** A scoped answer for another Goal, or one with no row for this Goal, is an error, never a guess. */
export function parseGoalTasks(payload: unknown, goalId: string): GoalTasks {
  const p = rec(payload);
  if (p.goal_filter !== goalId) throw new Error('the status answer is not scoped to this Goal');
  const rows = Array.isArray(rec(p.attention_queue).items) ? rec(p.attention_queue).items as unknown[] : [];
  const row = rows.map(rec).find(item => item.goal_id === goalId && item.status !== 'status_collection_failed');
  if (!row) throw new Error('this Goal is not in the attention queue');
  return { goalId, user: parseGroup(row.user_todos, 'user'), agent: parseGroup(row.agent_todos, 'agent'), observedAt: str(p.served_at) ?? new Date().toISOString() };
}

export async function fetchGoalTasks(token: string | undefined, goalId: string, signal?: AbortSignal): Promise<GoalTasks> {
  const res = await fetch(`/api/loopx/status.json?goal_id=${encodeURIComponent(goalId)}`, { signal, headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (res.status === 404) throw new LoopxStatusError('not-connected', 'the LoopX core is not proxied');
  if (res.status === 502) {
    const body = await res.json().catch(() => null);
    throw new LoopxStatusError('unavailable', body && typeof body === 'object' && 'error' in body ? String((body as { error: unknown }).error) : 'the LoopX core service is down');
  }
  if (!res.ok) throw new LoopxStatusError('unavailable', `status answered ${res.status}`);
  try { return parseGoalTasks(await res.json(), goalId); }
  catch (error) { throw new LoopxStatusError('bad-data', error instanceof Error ? error.message : String(error)); }
}

// ---------------------------------------------------------------- browsing

export type TaskFilter = 'all' | 'needsYou' | 'open' | 'blocked' | 'done';
export const TASK_FILTERS: TaskFilter[] = ['all', 'needsYou', 'open', 'blocked', 'done'];
export type TaskView = 'board' | 'list';

export interface Browse { view: TaskView; query: string; filter: TaskFilter; /** 'all', '' for unassigned, or an agent id. */ owner: string }

export const DEFAULT_BROWSE: Browse = { view: 'board', query: '', filter: 'all', owner: 'all' };

const PRIORITY_RANK: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };
const rank = (task: Task) => PRIORITY_RANK[task.priority ?? ''] ?? 50;

/** Standing monitors are not work to finish; the board lists everything else (the dashboard does the same). */
export const isWork = (task: Task): boolean => task.taskClass !== 'continuous_monitor' && !task.archived;

export function matchesQuery(task: Task, query: string): boolean {
  const q = query.trim().toLocaleLowerCase();
  return !q || task.title.toLocaleLowerCase().includes(q) || task.id.toLocaleLowerCase().includes(q) || (task.owner ?? '').toLocaleLowerCase().includes(q);
}

const ownerMatches = (task: Task, owner: string) => owner === 'all' || (task.owner ?? '') === owner;

export interface TaskLanes {
  /** The person's own tasks that are open: what waits on them. */
  needsYou: Task[];
  /** Agent work not yet done, most urgent first; blocked and deferred ones stay in it with their state. */
  todo: Task[];
  /** Done, most recently updated first. */
  done: Task[];
}

/** The three lanes after the owner, state and search filters. */
export function laneTasks(goal: GoalTasks, browse: Browse): TaskLanes {
  const { filter, owner, query } = browse;
  const byUrgency = (a: Task, b: Task) => rank(a) - rank(b) || (a.updatedAt ?? '').localeCompare(b.updatedAt ?? '') * -1;
  const user = goal.user.tasks.filter(t => isWork(t) && t.state !== 'done' && matchesQuery(t, query));
  const agent = goal.agent.tasks.filter(t => isWork(t) && ownerMatches(t, owner) && matchesQuery(t, query));
  const done = (list: Task[]) => list.filter(t => t.state === 'done').sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
  const open = agent.filter(t => t.state !== 'done');
  return {
    needsYou: filter === 'all' || filter === 'needsYou' ? user.sort(byUrgency) : [],
    todo: (filter === 'all' ? open : filter === 'open' ? open.filter(t => t.state === 'open') : filter === 'blocked' ? open.filter(t => t.state === 'blocked' || t.state === 'deferred') : []).sort(byUrgency),
    done: filter === 'all' || filter === 'done' ? done([...agent, ...goal.user.tasks.filter(t => isWork(t) && matchesQuery(t, query))]) : [],
  };
}

/** The agents that own agent tasks, for the owner filter, and whether any agent task has none. */
export function owners(goal: GoalTasks): { ids: string[]; unassigned: boolean } {
  const work = goal.agent.tasks.filter(isWork);
  return { ids: [...new Set(work.flatMap(t => t.owner ? [t.owner] : []))].sort(), unassigned: work.some(t => !t.owner) };
}
