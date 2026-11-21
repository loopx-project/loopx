/**
 * One Goal's own status page, read through the scoped status the core already serves
 * (`/api/loopx/status.json?goal_id=...`). The scoped answer carries the todo rows themselves,
 * not only the counts the sidebar's global read has, so the work page can list them.
 */
import { LoopxStatusError, type LoopxTodoCounts } from './status';

/** One todo row with its own identity, as the attention queue lists it. */
export interface LoopxTodoItem {
  id: string;
  text: string;
  status: string;
}

/** A role's todos: the counts plus as many rows as the core listed this time. */
export interface LoopxTodoGroup extends LoopxTodoCounts {
  items: LoopxTodoItem[];
  /** False when the core listed fewer rows than it counted; the rest exist but are not here. */
  complete: boolean;
}

export interface LoopxGoalDetail {
  goalId: string;
  title: string;
  activationState: string;
  status: string | null;
  waitingOn: string | null;
  recommendedAction: string | null;
  userTodos: LoopxTodoGroup;
  agentTodos: LoopxTodoGroup;
}

interface RawItem {
  todo_id?: string;
  text?: string;
  status?: string;
}

interface RawGroup {
  open_count?: number;
  done_count?: number;
  total_count?: number;
  items?: RawItem[];
}

/** Parse the scoped attention row; a foreign or missing row is an error, not a guess. */
export function parseGoalDetail(payload: unknown, goalId: string): LoopxGoalDetail {
  if (typeof payload !== 'object' || payload === null) throw new Error('status is not an object');
  const record = payload as Record<string, unknown>;
  if (record.goal_filter !== goalId) throw new Error('the status answer is not scoped to this Goal');
  const queue = (record.attention_queue ?? {}) as Record<string, unknown>;
  const rows = Array.isArray(queue.items) ? (queue.items as Array<Record<string, unknown>>) : [];
  const row = rows.find(item => item.goal_id === goalId && item.status !== 'status_collection_failed');
  if (!row) throw new Error('this Goal is not in the attention queue');
  const channel = (row.goal_channel_projection ?? {}) as Record<string, unknown>;
  return {
    goalId,
    title: typeof channel.display_name === 'string' && channel.display_name.trim() ? channel.display_name : goalId,
    activationState: typeof row.activation_state === 'string' ? row.activation_state : 'unknown',
    status: typeof row.status === 'string' ? row.status : null,
    waitingOn: typeof row.waiting_on === 'string' ? row.waiting_on : null,
    recommendedAction: typeof row.recommended_action === 'string' ? row.recommended_action : null,
    userTodos: group(row.user_todos),
    agentTodos: group(row.agent_todos),
  };
}

function group(raw: unknown): LoopxTodoGroup {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as RawGroup;
  const items = (Array.isArray(value.items) ? value.items : []).flatMap(item =>
    typeof item?.todo_id === 'string' && item.todo_id && typeof item.text === 'string'
      ? [{ id: item.todo_id, text: item.text, status: typeof item.status === 'string' ? item.status : 'unknown' }]
      : []);
  const counted = (n: unknown, fallback: number) => (typeof n === 'number' && Number.isInteger(n) ? n : fallback);
  const total = counted(value.total_count, items.length);
  const open = counted(value.open_count, items.filter(item => item.status === 'open').length);
  const done = counted(value.done_count, items.filter(item => item.status !== 'open').length);
  return { total, open, done, items, complete: items.length >= total };
}

export async function fetchGoalDetail(token: string | undefined, goalId: string, options: { signal?: AbortSignal } = {}): Promise<LoopxGoalDetail> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api/loopx/status.json?goal_id=${encodeURIComponent(goalId)}`, { headers, signal: options.signal });
  if (response.status === 404) throw new LoopxStatusError('not-connected', 'the LoopX core is not proxied');
  if (response.status === 502) {
    const body = await response.json().catch(() => null);
    throw new LoopxStatusError('unavailable', body && typeof body === 'object' && 'error' in body ? String((body as { error: unknown }).error) : 'the LoopX core service is down');
  }
  if (!response.ok) throw new LoopxStatusError('unavailable', `status answered ${response.status}`);
  try {
    return parseGoalDetail(await response.json(), goalId);
  } catch (error) {
    throw new LoopxStatusError('bad-data', error instanceof Error ? error.message : String(error));
  }
}
