import { urgencyOf, type TodoRecord } from './store.js';
import { importantOf, needsPerson, quadrantOf, type TodoStage } from './todo-priority.js';

export type BriefKind = 'morning' | 'evening';
export interface PersonalBrief { title: string; lines: string[]; text: string }
export interface LastPersonalBrief { text: string; sentAt: string }
export const LAST_BRIEF_KEY = 'personalBriefLast';
export type BriefTodo = Pick<TodoRecord, 'id' | 'text' | 'parentId' | 'owner' | 'state' | 'lane' | 'importance' | 'urgentUntil' | 'dueAt' | 'done' | 'proposed' | 'createdAt' | 'updatedAt' | 'doneAt'> & { threads?: ReadonlyArray<{ updatedAt: string }>; stage?: TodoStage | null };
const DAY = 86_400_000;
const at = (value: string | null) => value ? Date.parse(value) : NaN;
const dayStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
const clip = (text: string, length: number) => {
  const chars = [...text.replace(/\s+/g, ' ').trim()];
  return chars.length <= length ? chars.join('') : chars.slice(0, Math.max(0, length - 1)).join('') + '…';
};
const compare = (a: BriefTodo, b: BriefTodo) => (at(a.dueAt) || Infinity) - (at(b.dueAt) || Infinity)
  || (at(a.urgentUntil) || Infinity) - (at(b.urgentUntil) || Infinity) || a.id.localeCompare(b.id, 'en');
function whyUrgent(todo: BriefTodo, now: Date): string {
  const reason = urgencyOf(todo, now).reason;
  if (reason === 'overdue') return '已逾期';
  if (reason === 'pinned') return '已钉紧急';
  if (reason === 'due-soon') {
    const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime();
    const after = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2).getTime();
    return at(todo.dueAt) < tomorrow ? '今天截止' : at(todo.dueAt) < after ? '明天截止' : '48小时内截止';
  }
  return '今天的会';
}

/** Read only current summaries. No note reads, agent calls, mutation or clock reads. */
export function buildBrief(kind: BriefKind, todos: readonly BriefTodo[], now: Date): PersonalBrief | null {
  const start = dayStart(now), end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime();
  const roots = todos.filter(todo => !todo.parentId);
  const active = roots.filter(todo => !todo.done && todo.state !== 'done' && !todo.proposed);
  const activity = new Map<string, number>();
  for (const todo of todos) {
    const key = todo.parentId ?? todo.id;
    activity.set(key, Math.max(activity.get(key) ?? 0, at(todo.updatedAt) || at(todo.createdAt) || 0, ...(todo.threads?.map(thread => at(thread.updatedAt) || 0) ?? [])));
  }
  const urgent = active.filter(todo => quadrantOf({ ...todo, urgency: urgencyOf(todo, now) }) === 'now').sort(compare);
  const stale = active.filter(todo => (todo.lane === 'wait' || !todo.lane && todo.state === 'waiting')
    && now.getTime() - (activity.get(todo.id) ?? 0) > 3 * DAY);
  const discoveries = roots.filter(todo => todo.proposed && !todo.done && at(todo.createdAt) >= start && at(todo.createdAt) < end).length;
  const done = roots.filter(todo => (todo.done || todo.state === 'done') && !todo.proposed && at(todo.doneAt) >= start && at(todo.doneAt) < end).length;
  const untouched = active.filter(todo => importantOf(todo) && (activity.get(todo.id) ?? 0) < start).sort(compare).slice(0, 2);
  // Children count: a delegated sub-item can be the one holding a question.
  const waiting = todos.filter(todo => needsPerson(todo.stage) && !todo.done && todo.state !== 'done' && !todo.proposed).length;
  if (kind === 'morning' ? !urgent.length && !stale.length && !discoveries && !waiting : !done && !untouched.length && !urgent.length) return null;
  const title = kind === 'morning' ? `早报${urgent.length ? ` · ${urgent.length} 件现在做` : ''}` : '晚报';
  const footer = [stale.length ? `等别人 ${stale.length} 件超 3 天` : '', discoveries ? `新发现 ${discoveries} 条` : ''].filter(Boolean).join(' · ');
  const render = (nameLength: number): string[] => kind === 'morning' ? [
    ...urgent.slice(0, 3).map((todo, index) => `${index === 0 ? '先做 ' : ''}${clip(todo.text, nameLength)}（${whyUrgent(todo, now)}）`),
    ...(waiting ? [`${waiting} 件等你`] : []),
    ...(footer ? [footer] : []),
    ...(urgent.length > 3 ? [`…另 ${urgent.length - 3} 件`] : []),
  ] : [
    `今天完成${done}件`,
    ...(untouched.length ? [`重要未动：${untouched.map(todo => clip(todo.text, nameLength)).join('、')}`] : []),
    ...(urgent[0] ? [`明天先做：${clip(urgent[0].text, nameLength)}（${whyUrgent(urgent[0], now)}）`] : []),
  ];
  // Keep every requested section and its reason. Shrink names evenly, never chop off the last line.
  let length = 30, lines = render(length);
  while ([...`${title}\n${lines.join('\n')}`].length > 100 && length > 1) lines = render(--length);
  return { title, lines, text: lines.join('\n') };
}
