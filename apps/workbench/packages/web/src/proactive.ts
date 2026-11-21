import type { ProactiveAction, ProactiveFile, ProactiveRun, ProactiveSettings, Todo } from './api';

/** What Save sends: `enabled` is a read-only echo of the mode. */
export const settingsPayload = ({ mode, repos }: ProactiveSettings): Pick<ProactiveSettings, 'mode' | 'repos'> => ({ mode, repos });

export const addRepo = (settings: ProactiveSettings, path: string): ProactiveSettings =>
  settings.repos.includes(path) ? settings : { ...settings, repos: [...settings.repos, path] };
export const removeRepo = (settings: ProactiveSettings, path: string): ProactiveSettings =>
  ({ ...settings, repos: settings.repos.filter((repo) => repo !== path) });

/** What the person can still do about a run: say an observation was right, decide on sandbox work, clear away a failed attempt. */
export type RunControl = 'diff' | ProactiveAction;
export function runControls(run: ProactiveRun): RunControl[] {
  if (run.status === 'asking') return run.kind === 'dispatch' ? ['start', 'dismiss'] : run.reason === 'done' ? ['close-matter', 'dismiss'] : ['answer'];
  if (run.status === 'waiting') return ['dismiss'];
  if (run.status === 'observed' && run.needsRepo) return ['allow-repo', 'dismiss'];
  if (run.status === 'observed') return ['approve'];
  if (run.kind !== 'sandbox') return [];
  if (run.status === 'ready') return ['diff', 'merge', 'discard'];
  if (run.status === 'merged') return ['revert'];
  if (run.status === 'failed' && run.branch) return ['discard'];
  return [];
}

/** What a refused `start` says, from the run the daemon sent back with the 409. Jumping is only for
 *  one that has truly begun; a concurrent start still on its way keeps its button busy instead. */
export type StartConflict = { threadId: string } | 'starting';
export function startConflict(details: Record<string, unknown>): StartConflict | null {
  const run = details.run;
  if (!run || typeof run !== 'object') return null;
  const candidate = run as ProactiveRun;
  if (candidate.kind !== 'dispatch') return null;
  if (candidate.status === 'answered' && typeof candidate.threadId === 'string' && candidate.threadId) return { threadId: candidate.threadId };
  return candidate.status === 'asking' && details.starting === true ? 'starting' : null;
}

/** The runs a conversation panel pins at its top: only what asks the person, newest first. */
/** The one line a waiting decision is known by while folded: what is asked, else what is proposed. */
export const askLine = (run: ProactiveRun): string =>
  (run.kind === 'dispatch' ? run.title || run.task || run.summary : run.question?.text || run.line || run.summary || run.threadTitle || '')
    ?.split('\n').map(line => line.trim()).find(Boolean) ?? '';

export const askingPins = (runs: ProactiveRun[] | null): ProactiveRun[] =>
  (runs ?? []).filter(run => run.status === 'asking').sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt));

/** Where a「需要你」row from the plan leads. A session's own entry opens as itself; one of her asks
 *  (`run:<id>` in the plan, an id no thread answers) opens as the ask in the tray above the composer,
 *  where its answer controls live; an ask already over opens the session it became, and one gone
 *  entirely leads nowhere, so the caller refreshes the list rather than open what does not exist. */
export type NeedTarget = { kind: 'thread'; id: string } | { kind: 'ask'; run: ProactiveRun } | { kind: 'gone' };
export function needTarget(id: string, runs: ProactiveRun[] | null): NeedTarget {
  if (!id.startsWith('run:')) return { kind: 'thread', id };
  const run = runs?.find(item => item.id === id.slice(4));
  if (run?.status === 'asking') return { kind: 'ask', run };
  if (run?.threadId) return { kind: 'thread', id: run.threadId };
  return { kind: 'gone' };
}

/** Sandbox work in hand or waiting for a decision comes before the note; materials and observations sit quietly after it. */
export const isDecision = (run: ProactiveRun): boolean => run.status === 'asking' || run.status === 'waiting' || (run.status === 'observed' && Boolean(run.needsRepo)) || (run.kind === 'sandbox' && run.status !== 'observed' && run.status !== 'approved');

const newestFirst = (a: ProactiveRun, b: ProactiveRun) => Date.parse(b.startedAt) - Date.parse(a.startedAt);

/**
 * The runs a matter's spread shows: everything still open, and of what is over only the latest of
 * each kind, so old attempts do not pile up on the desk.
 */
export function matterRuns(runs: readonly ProactiveRun[], matterId: string): ProactiveRun[] {
  // Discarded work is an audit record, not a card.
  const own = runs.filter((run) => run.matterId === matterId && run.status !== 'discarded').sort(newestFirst);
  const latest = new Set(['prepare', 'sandbox', 'advance'].map((kind) => own.find((run) => run.kind === kind)?.id));
  return own.filter((run) => latest.has(run.id) || run.status === 'asking' || run.status === 'waiting' || (run.needsRepo && run.status === 'observed') || run.status === 'running' || run.status === 'ready' || run.status === 'merged');
}

export const replaceRun = (runs: readonly ProactiveRun[], next: ProactiveRun): ProactiveRun[] =>
  runs.map((run) => (run.id === next.id ? next : run));

/** A matter she has acted on, by the run that says the most about it now. */
export interface ProactiveMatter {
  /** The matter, or `thread:<id>` for a session that belongs to none. */
  matterId: string;
  todo: Todo | null;
  run: ProactiveRun;
}

/**
 * The filter's list, latest first. A run waiting for a decision or still undoable speaks for the
 * matter over a later one that says nothing can be done, so "revert" never goes out of reach.
 */
export function proactiveMatters(runs: readonly ProactiveRun[], todos: readonly Todo[]): ProactiveMatter[] {
  const byMatter = new Map<string, ProactiveRun[]>();
  // A session that belongs to no matter is its own row, not one heap of every such session.
  const key = (run: ProactiveRun) => run.matterId || `thread:${run.threadId}`;
  for (const run of runs) if (run.status !== 'discarded') byMatter.set(key(run), [...(byMatter.get(key(run)) ?? []), run]);
  return [...byMatter.entries()]
    .map(([matterId, list]) => {
      const sorted = list.sort(newestFirst);
      return { matterId, todo: todos.find((todo) => todo.id === matterId) ?? null, run: sorted.find((run) => runControls(run).length) ?? sorted[0] };
    })
    .sort((a, b) => newestFirst(a.run, b.run));
}

/** How many matters the filter would show, from the counts that come with the plans. */
export const proactiveCount = (todos: readonly Todo[]): number => todos.filter((todo) => (todo.proactiveRunCount ?? 0) > 0).length;

export const fileName = (path: string): string => path.split('/').filter(Boolean).at(-1) ?? path;

export const diffTotals = (files: readonly ProactiveFile[]): { additions: number; deletions: number } =>
  files.reduce((sum, file) => ({ additions: sum.additions + file.additions, deletions: sum.deletions + file.deletions }), { additions: 0, deletions: 0 });
