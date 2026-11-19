import type { AttentionKind, ThreadSummary } from '@calli/extension-api';
import { parseTitleTags, validTag } from './tags';
import type { ConnectionState, MachineState } from '@calli/extension-api/web';

export function showStaleWarning(state: ConnectionState, observedAt: string | null): boolean {
  return state !== 'live' && observedAt !== null;
}

export const COLUMN_IDS = ['backlog', 'doing', 'waiting', 'review', 'done'] as const;
export type ColumnId = (typeof COLUMN_IDS)[number];
/** Threads waiting to be checked stay in view this long after they last moved; older ones fold. Three days spans a weekend. */
export const REVIEW_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;
/** Done threads stay in view this long; older ones fold. */
export const DONE_WINDOW_MS = 24 * 60 * 60 * 1000;

/** Where the wide layout keeps whether the backlog column is open; it starts as a narrow rail. */
export const BACKLOG_OPEN_KEY = 'backlog.open.v1';

/** Untrusted persisted value: anything but an explicit true leaves the backlog collapsed. */
export function parseBacklogOpen(value: string | null): boolean {
  try {
    return JSON.parse(value ?? 'null') === true;
  } catch {
    return false;
  }
}

/** Bumped when a saved filter's meaning changes. */
export const FILTERS_VERSION = 3;

export interface BoardFilters {
  tags: string[];
  project: string[];
  agent: string[];
  scheduled: boolean[];
  /** Machine keys (`machineKey`); only meaningful while threads carry a machine. */
  machine: string[];
}

/** Why a card sits in its column, so the card can say it in one line. */
export type CardReason =
  | { kind: 'running'; since: string | null }
  | { kind: 'attention'; attention: Exclude<AttentionKind, 'unread'> }
  | { kind: 'report' }
  | { kind: 'idle'; unread: boolean }
  | { kind: 'done' };

export interface BoardCard {
  thread: ThreadSummary;
  column: ColumnId;
  reason: CardReason;
}

export interface BoardColumn {
  id: ColumnId;
  cards: BoardCard[];
  /** Past the column's window, newest first; folded unless the column is expanded. */
  older: BoardCard[];
  olderCount: number;
  total: number;
}

export function emptyFilters(): BoardFilters {
  return { tags: [], project: [], agent: [], scheduled: [], machine: [] };
}

function timestamp(iso: string | null | undefined): number {
  const time = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(time) ? time : 0;
}

/**
 * Where a thread goes, checked in order: marked done by the person; waiting for the person;
 * running; otherwise the latest progress line decides, and without one a finished turn waits to
 * be checked. Agents can move a card between doing, waiting and review, never to done or backlog:
 * those are the person's. A new turn lifts a backlog card into doing, and its end replaces the mark.
 */
export function cardFor(thread: ThreadSummary): BoardCard {
  const card = (column: ColumnId, reason: CardReason): BoardCard => ({ thread, column, reason });
  if (thread.doneAt) return card('done', { kind: 'done' });
  const attention = thread.attention?.kind;
  if (attention && attention !== 'unread') return card('waiting', { kind: 'attention', attention });
  if (thread.status === 'running') return card('doing', { kind: 'running', since: thread.runningSince });
  if (thread.report?.stage === 'backlog') return card('backlog', { kind: 'report' });
  if (thread.report?.stage === 'blocked') return card('waiting', { kind: 'report' });
  if (thread.report?.stage === 'doing') return card('doing', { kind: 'report' });
  if (thread.report) return card('review', { kind: 'report' });
  return card('review', { kind: 'idle', unread: thread.unread });
}

/** When the card last moved: done cards by when they were marked. */
export function activeAt(card: BoardCard): number {
  return card.column === 'done' ? timestamp(card.thread.doneAt) : Math.max(timestamp(card.thread.updatedAt), timestamp(card.thread.report?.at));
}

/** Threads whose project directory was removed share one bucket. Paths never start with ':'. */
export const GONE_PROJECT = ':gone';

/** What a thread is filtered and labelled by: its project, `''` for none, or the removed bucket. */
export function projectKey(thread: ThreadSummary): string {
  return thread.projectGone ? GONE_PROJECT : thread.project;
}

export interface ProjectOption {
  key: string;
  count: number;
}

/** The projects to pick from: most threads first, then by name; a selection no thread has any more stays, to be cleared. */
export function projectOptions(threads: readonly ThreadSummary[], selected: readonly string[]): ProjectOption[] {
  const counts = new Map<string, number>(selected.map((key) => [key, 0]));
  for (const thread of threads) if (!thread.archived) counts.set(projectKey(thread), (counts.get(projectKey(thread)) ?? 0) + 1);
  return [...counts].map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

/**
 * Names for project directories: the folder's own name, with its parents added only as far as
 * needed to tell apart folders that share it (`code/personal`, `x/personal`).
 */
export function projectLabels(keys: readonly string[]): Map<string, string> {
  const parts = new Map(keys.filter((key) => key && key !== GONE_PROJECT).map((key) => [key, key.split(/[\\/]/).filter(Boolean)]));
  const labels = new Map<string, string>();
  for (const [key, segments] of parts) {
    let depth = 1;
    const label = (n: number) => segments.slice(-n).join('/') || key;
    const clash = (n: number) => [...parts].some(([other, s]) => other !== key && s.slice(-n).join('/') === label(n));
    while (depth < segments.length && clash(depth)) depth += 1;
    labels.set(key, label(depth));
  }
  return labels;
}

/** The hub's own machine has no id; filters keep it under this key. Ids never start with ':'. */
export const LOCAL_MACHINE = ':local';

export function machineKey(machine: { id: string | null }): string {
  return machine.id ?? LOCAL_MACHINE;
}

/** The machine id a filter key stands for; null is the hub's own. */
export function machineIdOf(key: string): string | null {
  return key === LOCAL_MACHINE ? null : key;
}

export interface MachineOption {
  key: string;
  label: string;
  /** Threads on it; null while they are not known (still loading, or never read), never 0 for those. */
  count: number | null;
}

/** The machines to pick from, in the host's order; a selection no machine has any more stays, to be cleared. */
export function machineOptions(threads: readonly ThreadSummary[], machines: readonly MachineState[], selected: readonly string[]): MachineOption[] {
  const counts = new Map<string, number>();
  for (const thread of threads) if (!thread.archived && thread.machine) counts.set(machineKey(thread.machine), (counts.get(machineKey(thread.machine)) ?? 0) + 1);
  const options = machines.map((machine): MachineOption => {
    const key = machineKey(machine);
    const unknown = machine.state === 'loading' || ((machine.state === 'failed' || machine.state === 'offline') && machine.observedAt === null && !counts.has(key));
    return { key, label: machine.label, count: unknown ? null : counts.get(key) ?? 0 };
  });
  const known = new Set(options.map((option) => option.key));
  for (const thread of threads) if (thread.machine && !known.has(machineKey(thread.machine))) {
    known.add(machineKey(thread.machine));
    options.push({ key: machineKey(thread.machine), label: thread.machine.label, count: counts.get(machineKey(thread.machine)) ?? 0 });
  }
  for (const key of selected) if (!known.has(key)) options.push({ key, label: key, count: 0 });
  return options;
}

/** Machines whose threads may be out of date: they could not be read just now. */
export function staleMachines(machines: readonly MachineState[]): MachineState[] {
  return machines.filter((machine) => machine.state === 'failed' || machine.state === 'offline');
}

/** What a new conversation starts from, given the filters: a project or machine only when exactly one is chosen. */
export function newThreadOptions(filters: BoardFilters): { project?: string; machine?: string | null } {
  const options: { project?: string; machine?: string | null } = {};
  const [project] = filters.project;
  if (filters.project.length === 1 && project && project !== GONE_PROJECT) options.project = project;
  if (filters.machine.length === 1) options.machine = machineIdOf(filters.machine[0]);
  return options;
}

export function matchesFilters(thread: ThreadSummary, filters: BoardFilters): boolean {
  return (filters.tags.length === 0 || parseTitleTags(thread.title).tags.some(tag => filters.tags.some(value => value.toLowerCase() === tag.toLowerCase())))
    && (filters.project.length === 0 || filters.project.includes(projectKey(thread)))
    && (filters.agent.length === 0 || filters.agent.includes(thread.agent))
    && (filters.scheduled.length === 0 || filters.scheduled.includes(thread.scheduled))
    // Without machines on the threads (one machine shown) the filter has nothing to say.
    && (filters.machine.length === 0 || !thread.machine || filters.machine.includes(machineKey(thread.machine)));
}

/** The host owns attention and the thread state; the board only places what it publishes. */
export function buildColumns(
  threads: readonly ThreadSummary[],
  filters: BoardFilters,
  now: number,
  expanded: ReadonlySet<ColumnId> = new Set(),
): BoardColumn[] {
  const groups: Record<ColumnId, BoardCard[]> = { backlog: [], doing: [], waiting: [], review: [], done: [] };
  for (const thread of threads) {
    if (!thread.archived && matchesFilters(thread, filters)) {
      const card = cardFor(thread);
      groups[card.column].push(card);
    }
  }
  return COLUMN_IDS.map((id) => {
    const sorted = [...groups[id]].sort((a, b) => Number(b.thread.pinned) - Number(a.thread.pinned)
      || activeAt(b) - activeAt(a) || a.thread.id.localeCompare(b.thread.id));
    const window = id === 'done' ? DONE_WINDOW_MS : id === 'review' ? REVIEW_WINDOW_MS : Infinity;
    const isOlder = (card: BoardCard) => activeAt(card) < now - window;
    const older = sorted.filter(isOlder);
    return {
      id,
      cards: expanded.has(id) ? sorted : sorted.filter((card) => !isOlder(card)),
      older,
      olderCount: older.length,
      total: sorted.length,
    };
  });
}

/** Marks the threads done a few at a time, reporting progress; resolves to how many failed. */
export async function markAllDone(ids: readonly string[], setDone: (id: string) => Promise<void>, progress: (done: number) => void): Promise<number> {
  const queue = [...ids];
  let done = 0;
  let failed = 0;
  const worker = async () => {
    for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
      try { await setDone(id); } catch { failed += 1; }
      progress(++done);
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker));
  return failed;
}

/** On phones one column shows at a time: the first that asks something of the person. */
export function firstColumn(columns: readonly BoardColumn[]): ColumnId {
  for (const id of ['waiting', 'review', 'doing'] as const) {
    if (columns.find((column) => column.id === id)?.total) return id;
  }
  return 'waiting';
}

export function projectName(project: string): string {
  return project.split(/[\\/]/).filter(Boolean).at(-1) ?? project;
}

/** Untrusted persisted preferences must never prevent the view from opening. */
export function parseFilters(value: string | null): BoardFilters {
  try {
    const parsed: unknown = value ? JSON.parse(value) : null;
    if (!parsed || typeof parsed !== 'object') return emptyFilters();
    const object = parsed as Record<string, unknown>;
    const strings = (input: unknown) => Array.isArray(input)
      ? [...new Set(input.filter((item): item is string => typeof item === 'string'))] : [];
    return {
      tags: [...new Set(strings(object.tags).map(tag => tag.trim()).filter(validTag).map(tag => tag.toLowerCase()))],
      // Before version 2 projects were kept as the path was typed, not resolved; they match nothing now.
      project: typeof object.v === 'number' && object.v >= 2 ? strings(object.project) : [],
      agent: strings(object.agent),
      scheduled: Array.isArray(object.scheduled)
        ? [...new Set(object.scheduled.filter((item): item is boolean => typeof item === 'boolean'))] : [],
      // Added in version 3.
      machine: typeof object.v === 'number' && object.v >= 3 ? strings(object.machine) : [],
    };
  } catch {
    return emptyFilters();
  }
}
