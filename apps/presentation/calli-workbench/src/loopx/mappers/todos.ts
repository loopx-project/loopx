/**
 * LoopX todo projections → Calli Todo[].
 *
 * Source of truth: status.todo_index.items (the flattened cross-goal index).
 * Fallback when a status snapshot has no todo_index (e.g. older service):
 * goal_channel_projection user_todos + agent_todos of every attention item.
 * Priority→importance mapping is declared in placeholders.ts, not per site.
 */
import { placeholder } from '../placeholders.ts';
import type { ProjectedTodoRow, QueueItem, StatusPayload, TodoIndexItem } from '../contracts/status-schema.ts';
import type { Todo } from '../types.ts';

function importanceFromPriority(priority: string | null | undefined): Todo['importance'] {
  if (priority === 'P0') return 'high';
  if (priority === 'P1' || priority === 'P2') return 'normal';
  return 'low';
}

function todoId(item: { todo_id?: string | null; index?: number | null }, fallback: number): string {
  return item.todo_id?.trim() || `loopx-idx-${item.index ?? fallback}`;
}

/**
 * What the desk needs to place a todo, from typed fields only:
 * done -> done; status "blocked" -> state "waiting" with stage "blocked" and the status as the reason;
 * an open todo owned by the user -> stage "answer"; one an agent has claimed -> "working"; the rest "idle".
 */
function todoFacts(status: string | null | undefined, done: boolean, role: 'user' | 'agent', claimedBy?: string | null): Pick<Todo, 'state' | 'stage' | 'waitingOn'> {
  if (done) return { state: 'done', stage: 'idle', waitingOn: null };
  if (status === 'blocked') return { state: 'waiting', stage: 'blocked', waitingOn: 'blocked' };
  if (role === 'user') return { state: 'open', stage: 'answer', waitingOn: null };
  return { state: 'open', stage: claimedBy ? 'working' : 'idle', waitingOn: null };
}

function fromIndexItem(item: TodoIndexItem, position: number): Todo {
  return {
    id: todoId(item, position),
    text: item.text,
    done: item.done,
    parentId: null,
    position: item.index ?? position,
    createdAt: item.updated_at ?? '', // empty when LoopX gives no time; the desk shows no age then
    updatedAt: item.updated_at ?? undefined,
    doneAt: item.completed_at ?? null,
    threads: placeholder<[]>('Todo.threads'),
    owner: item.role === 'user' ? 'me' : 'agent',
    ...todoFacts(item.status, item.done, item.role === 'user' ? 'user' : 'agent', item.claimed_by),
    lane: placeholder<null>('Todo.lane / Todo.urgency / Todo.source / Todo.dueAt / Todo.waitingOn / Todo.followUpAt / Todo.subscriptions'),
    importance: importanceFromPriority(item.priority),
  };
}

function fromProjectedRow(row: ProjectedTodoRow, role: 'user' | 'agent', position: number): Todo {
  const done = row.status === 'done' || row.done === true;
  return {
    id: todoId(row, position),
    text: row.text ?? row.title ?? '',
    done,
    parentId: null,
    position,
    createdAt: row.updated_at ?? '',
    updatedAt: row.updated_at ?? undefined,
    doneAt: row.completed_at ?? null,
    threads: placeholder<[]>('Todo.threads'),
    owner: role === 'user' ? 'me' : 'agent',
    ...todoFacts(row.status, done, role),
    lane: placeholder<null>('Todo.lane / Todo.urgency / Todo.source / Todo.dueAt / Todo.waitingOn / Todo.followUpAt / Todo.subscriptions'),
    importance: importanceFromPriority(row.priority),
  };
}

function rowsFromAttentionItems(items: QueueItem[]): Array<{ row: ProjectedTodoRow; role: 'user' | 'agent' }> {
  const out: Array<{ row: ProjectedTodoRow; role: 'user' | 'agent' }> = [];
  for (const item of items) {
    const projection = item.goal_channel_projection;
    if (!projection) continue;
    for (const row of projection.user_todos ?? []) out.push({ row, role: 'user' });
    for (const row of projection.agent_todos ?? []) out.push({ row, role: 'agent' });
  }
  return out;
}

export function mapTodos(status: StatusPayload): Todo[] {
  const indexed = status.todo_index?.items;
  if (indexed && indexed.length) {
    return indexed.map((item, i) => fromIndexItem(item, i + 1));
  }
  const rows = rowsFromAttentionItems(status.attention_queue.items ?? []);
  return rows.map(({ row, role }, i) => fromProjectedRow(row, role, i + 1));
}
