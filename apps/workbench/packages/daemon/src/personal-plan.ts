import { executeLark, parseLarkOutput, timestamp } from './channels/lark.js';
import { HttpError } from './errors.js';
import { contextEntries } from './personal-context.js';
import type { TodoRecord } from './store.js';
import type { ThreadManager } from './threads.js';
import { todoStage } from './todo-stage.js';
import type { TodoStage } from './todo-priority.js';

export type PlanRange = 'hour' | 'day' | 'week' | 'month';
export interface PlanMeeting { id: string; title: string; start: string; end: string; recurring: boolean; url?: string }
export interface PlanTodo { id: string; text: string; dueAt: string | null; importance: string | null; horizon: 'week' | 'month' | null; parentId: string | null; stage: TodoStage | null }
export interface PlanThread { id: string; title: string; line: string; at: string }
export interface PersonalPlan {
  range: PlanRange; from: string; to: string;
  meetings: { state: 'ok' | 'unavailable' | 'off'; error?: string; items: PlanMeeting[] };
  urgent: PlanTodo[]; due: PlanTodo[]; goals: PlanTodo[]; needsYou: PlanThread[]; running: PlanThread[];
}
export interface AgendaMeeting extends PlanMeeting { attendees: string[]; description: string }
type Agenda = Omit<PersonalPlan['meetings'], 'items'> & { items: AgendaMeeting[] };

/** Date arithmetic uses the hub process timezone, including DST and Monday week boundaries. */
export function planWindow(value: unknown = 'day', now = new Date()) {
  if (!['hour', 'day', 'week', 'month'].includes(String(value))) throw new HttpError(400, 'range must be hour, day, week or month');
  const range = value as PlanRange, from = new Date(now), to = new Date(now);
  if (range === 'hour') to.setTime(from.getTime() + 4 * 3600_000);
  else {
    from.setHours(0, 0, 0, 0);
    if (range === 'week') from.setDate(from.getDate() - (from.getDay() + 6) % 7);
    if (range === 'month') from.setDate(1);
    to.setTime(from.getTime());
    if (range === 'month') to.setMonth(to.getMonth() + 1); else to.setDate(to.getDate() + (range === 'week' ? 7 : 1));
  }
  return { range, from: from.toISOString(), to: to.toISOString() };
}
export function horizonDeadline(horizon: 'week' | 'month', now = new Date()): string {
  const end = new Date(planWindow(horizon, now).to);
  end.setMinutes(end.getMinutes() - 1);
  return end.toISOString();
}
const object = (value: unknown): Record<string, any> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {};
const time = (value: unknown) => timestamp(object(value).datetime ?? value);
const clip = (value: unknown, n = 300) => [...String(value ?? '')].slice(0, n).join('');
const inWindow = (at: string | null, window: { from: string; to: string }) => !!at && at >= window.from && at < window.to;

export class PersonalPlanService {
  private cache = new Map<PlanRange, { at: number; from: string; to: string; result: Promise<Agenda> }>();
  constructor(private readonly manager: ThreadManager, private readonly execute: typeof executeLark = executeLark) {}
  async calendar(range: PlanRange, now = new Date()): Promise<Agenda> {
    if (!this.manager.lark.settings().enabled) { this.cache.clear(); return { state: 'off', items: [] }; }
    const window = planWindow(range, now), old = this.cache.get(range);
    // Round the sliding window outwards so repeated reads share a five-minute cache.
    const from = range === 'hour' ? new Date(Math.floor(now.getTime() / 300_000) * 300_000).toISOString() : window.from;
    const to = range === 'hour' ? new Date(Date.parse(from) + 4 * 3600_000 + 300_000).toISOString() : window.to;
    let result: Promise<Agenda>;
    if (old && now.getTime() - old.at < 300_000 && old.from === from && old.to === to) result = old.result;
    else {
      result = this.readCalendar(from, to);
      this.cache.set(range, { at: now.getTime(), from, to, result });
    }
    const agenda = await result;
    return { ...agenda, items: agenda.items.filter(item => item.start < window.to && (range === 'hour' ? item.end > window.from : item.start >= window.from)) };
  }
  private async readCalendar(from: string, to: string): Promise<Agenda> {
    try {
      const parsed = parseLarkOutput(await this.execute(['calendar', '+agenda', '--as', 'user', '--format', 'json', '--start', from, '--end', to], { timeoutMs: 12_000 }));
      if (parsed.code && parsed.code !== 0) throw new Error('cli_failed');
      const data = parsed.data;
      const rows: unknown = Array.isArray(data) ? data : object(data).events ?? object(data).items;
      if (!Array.isArray(rows)) throw new Error('cli_unexpected_output');
      const unique = new Map<string, AgendaMeeting>();
      let invalid = 0;
      for (const value of rows) {
        const row = object(value);
        if (row.status === 'cancelled' || row.is_cancelled || row.attendee_status === 'declined' || row.self_rsvp_status === 'decline') continue;
        const start = time(row.start_time ?? row.start), end = time(row.end_time ?? row.end);
        const id = String(row.event_id ?? row.id ?? ''), title = clip(row.summary ?? row.title);
        if (!id || !title || !start || !end || end <= start) { invalid++; continue; }
        const url = row.app_link ?? object(row.vchat).meeting_url ?? row.url ?? row.event_url ?? row.html_link;
        unique.set(JSON.stringify([id, start]), { id, title, start, end, recurring: Boolean(row.recurring_event_id || row.recurrence || row.rrule),
          ...(typeof url === 'string' && /^https:\/\//.test(url) ? { url } : {}),
          attendees: Array.isArray(row.attendees) ? row.attendees.slice(0, 50).map((a: unknown) => clip(object(a).display_name ?? object(a).name ?? object(a).user_id ?? a, 100)) : [],
          description: clip(row.description, 4000) });
      }
      if (rows.length && invalid === rows.length) throw new Error('cli_unexpected_event');
      return { state: 'ok', items: [...unique.values()].sort((a, b) => a.start.localeCompare(b.start)) };
    } catch (error) { return { state: 'unavailable', error: error instanceof Error ? error.message : 'calendar unavailable', items: [] }; }
  }
  async get(range: unknown = 'day', now = new Date()): Promise<PersonalPlan> {
    const window = planWindow(range, now), agenda = await this.calendar(window.range, now);
    const store = this.manager.personal.store;
    const todos = store.listTodos().filter(todo => !todo.done && todo.state !== 'done');
    const urgent = todos.filter(todo => (todo.urgentUntil && todo.urgentUntil > now.toISOString()) || (todo.dueAt && todo.dueAt < now.toISOString()) || (todo.importance === 'high' && inWindow(todo.dueAt, window)));
    const urgentIds = new Set(urgent.map(todo => todo.id));
    const week = planWindow('week', now), month = planWindow('month', now);
    const monthIds = new Set(todos.filter(todo => todo.horizon === 'month' && inWindow(todo.dueAt, month)).map(todo => todo.id));
    const goals = todos.filter(todo => window.range === 'month' ? monthIds.has(todo.id) || todo.horizon === 'week' && (!!todo.parentId && monthIds.has(todo.parentId) || inWindow(todo.dueAt, month)) : todo.horizon === 'week' && inWindow(todo.dueAt, week));
    const deadline = (todo: TodoRecord) => [todo.dueAt, todo.followUpAt].filter((at): at is string => !!at).sort()[0] ?? todo.urgentUntil ?? todo.createdAt;
    const pack = (rows: TodoRecord[]): PlanTodo[] => rows.sort((a, b) => deadline(a).localeCompare(deadline(b))).slice(0, 20)
      .map(todo => ({ id: todo.id, text: todo.text, dueAt: todo.dueAt, importance: todo.importance, horizon: todo.horizon, parentId: todo.parentId, stage: todoStage(this.manager, todo) }));
    const entries = contextEntries(store, this.manager, this.manager.personal.router.status().threadId ?? '', now);
    const threads = (category: 'needs' | 'running') => entries.filter(entry => entry.category === category).sort((a, b) => a.at.localeCompare(b.at)).slice(0, 20).map(entry => ({ id: entry.id, title: entry.title, line: entry.note, at: entry.at }));
    return { ...window, meetings: { ...agenda, items: agenda.items.slice(0, 20).map(({ attendees, description, ...meeting }) => meeting) }, urgent: pack(urgent),
      due: pack(todos.filter(todo => !urgentIds.has(todo.id) && !todo.horizon && (inWindow(todo.dueAt, window) || inWindow(todo.followUpAt, window)))), goals: pack(goals), needsYou: threads('needs'), running: threads('running') };
  }
  async upcoming(now = new Date()) {
    const calendar = await this.calendar('hour', now), day = planWindow('day', now);
    const category = 'upcoming' as const;
    const entries = calendar.items.slice(0, 5).map(item => ({ id: `meeting:${item.id}:${item.start}`, category, title: item.title, note: `开始：${item.start}`, at: item.start }));
    if (calendar.state !== 'ok') entries.push({ id: 'calendar-state', category, title: calendar.state === 'off' ? '日程未开启' : '日程读取失败', note: calendar.error ?? '', at: day.from });
    const todos = this.manager.personal.store.listTodos().filter(todo => !todo.done && todo.state !== 'done' && inWindow(todo.dueAt, day)).sort((a, b) => a.dueAt!.localeCompare(b.dueAt!));
    entries.push(...todos.slice(0, 5).map(todo => ({ id: `due:${todo.id}`, category, title: todo.text, note: `截止：${todo.dueAt}`, at: todo.dueAt! })));
    return entries;
  }
}
