/**
 * A Goal's accepted reports: local result files bound to a completed task whose acceptance still holds.
 * An inventory row is a pointer, not proof; opening one re-verifies it against the same row.
 */
import { ActionError } from './actions';

export interface ResultRow {
  todoId: string;
  title: string;
  producer: string;
  sha256: string;
  contentType: string;
  sizeBytes: number;
  completedAt: string | null;
}
export interface ResultPage { items: ResultRow[]; nextCursor: string | null; unavailable: number }
export interface ResultRead { row: ResultRow; text: string }

const record = (v: unknown): Record<string, unknown> => (typeof v === 'object' && v !== null && !Array.isArray(v) ? v as Record<string, unknown> : {});

async function call(token: string | undefined, path: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const res = await fetch(path, { signal, headers: { Authorization: `Bearer ${token ?? ''}` } });
  const json = record(await res.json().catch(() => ({})));
  if (!res.ok) throw new ActionError(res.status, typeof json.error_code === 'string' ? json.error_code : `http_${res.status}`, typeof json.error === 'string' ? json.error : `HTTP ${res.status}`);
  return json;
}

export const resultKey = (row: ResultRow): string => JSON.stringify([row.todoId, row.sha256, row.producer, row.contentType]);

/** An incomplete inventory is refused, not patched up: a half-read list would look like a complete one. */
export function parseResultPage(raw: Record<string, unknown>): ResultPage {
  const items = raw.items, unavailable = raw.unavailable_count, ids = raw.unavailable_todo_ids, next = raw.next_cursor;
  if (!Array.isArray(items) || !Number.isInteger(unavailable) || !Array.isArray(ids) || ids.length !== unavailable || (next !== null && typeof next !== 'string')) {
    throw new ActionError(502, 'bad_inventory', 'The report list is incomplete');
  }
  return {
    items: items.map(item => {
      const r = record(item);
      return {
        todoId: String(r.todo_id ?? ''), title: String(r.title ?? r.todo_id ?? ''), producer: String(r.producer_agent_id ?? ''),
        sha256: String(r.sha256 ?? ''), contentType: String(r.content_type ?? 'text/plain'), sizeBytes: Number(r.size_bytes ?? 0),
        completedAt: typeof r.completed_at === 'string' ? r.completed_at : null,
      };
    }).filter(row => row.todoId && row.sha256),
    nextCursor: next as string | null,
    unavailable: unavailable as number,
  };
}

export const fetchResults = (token: string | undefined, goalId: string, cursor?: string, signal?: AbortSignal): Promise<ResultPage> =>
  call(token, `/api/loopx/api/chat/goal-results?goal_id=${encodeURIComponent(goalId)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, signal).then(parseResultPage);

/** Read one report and check it is still the one the row named; a changed version or acceptance is an error, not new content. */
export async function readResult(token: string | undefined, goalId: string, row: ResultRow, signal?: AbortSignal): Promise<ResultRead> {
  const raw = await call(token, `/api/loopx/api/chat/goal-results/${encodeURIComponent(row.todoId)}?goal_id=${encodeURIComponent(goalId)}`, signal);
  const result = record(raw.result);
  if (raw.goal_id !== goalId || raw.todo_id !== row.todoId || result.sha256 !== row.sha256 || result.producer_agent_id !== row.producer || result.content_type !== row.contentType || typeof raw.text !== 'string') {
    throw new ActionError(409, 'result_changed', 'The report version or its acceptance changed');
  }
  return { row, text: raw.text };
}
