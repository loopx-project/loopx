import type { ThreadSummary } from '@calli/extension-api';
import type { BoardCard, ColumnId } from './columns';

export type Stage = 'backlog' | 'doing' | 'blocked' | 'review';
export type RejectReason = 'running' | 'needsYou';

/** What dropping a card on a column does. */
export type DropAction =
  | { kind: 'setStage'; stage: Stage }
  | { kind: 'setDone' }
  | { kind: 'none' }
  | { kind: 'reject'; reason: RejectReason };

const STAGES: Record<Exclude<ColumnId, 'done'>, Stage> = { backlog: 'backlog', doing: 'doing', waiting: 'blocked', review: 'review' };

/**
 * A card goes to any column by hand, with two limits: one that is running or waits on the person
 * is only theirs to finish, because placing it elsewhere would be undone by what it is doing.
 */
export function dropAction(card: BoardCard, target: ColumnId): DropAction {
  if (card.column === target) return { kind: 'none' };
  if (target === 'done') return { kind: 'setDone' };
  if (card.reason.kind === 'running') return { kind: 'reject', reason: 'running' };
  if (card.reason.kind === 'attention') return { kind: 'reject', reason: 'needsYou' };
  return { kind: 'setStage', stage: STAGES[target] };
}

/** The thread as the board shows it right after the drop, before the host confirms. */
export function applyDrop(thread: ThreadSummary, action: DropAction, now: number): ThreadSummary {
  if (action.kind === 'setDone') return { ...thread, doneAt: new Date(now).toISOString() };
  if (action.kind === 'setStage') return { ...thread, doneAt: null, report: { stage: action.stage, note: '', at: new Date(now).toISOString(), by: 'user' } };
  return thread;
}

/** The parts of a thread a drop changes; when they differ from what was dropped on, the host has answered. */
export function placement(thread: ThreadSummary): string {
  return `${thread.doneAt ?? ''}|${thread.report?.stage ?? ''}|${thread.report?.at ?? ''}|${thread.updatedAt}|${thread.status}|${thread.attention?.kind ?? ''}`;
}

export interface Guess {
  /** The thread before the drop. */
  base: ThreadSummary;
  thread: ThreadSummary;
}

/** The threads with the person's unconfirmed drops laid over them, each until its real state moves. */
export function withGuesses(threads: readonly ThreadSummary[], guesses: ReadonlyMap<string, Guess>): ThreadSummary[] {
  if (guesses.size === 0) return threads as ThreadSummary[];
  return threads.map((thread) => {
    const guess = guesses.get(thread.id);
    return guess && placement(guess.base) === placement(thread) ? guess.thread : thread;
  });
}

export const LONG_PRESS_MS = 500;
export const LONG_PRESS_SLOP = 10;

/**
 * Tells a long press from a tap or a scroll: fires after `delay` unless the finger moved more than
 * `slop` or lifted first. After it fires, the click that follows the lift is not an open.
 */
export function createLongPress(
  onFire: (x: number, y: number) => void,
  timers: { set: (fn: () => void, ms: number) => unknown; clear: (id: unknown) => void } = { set: setTimeout, clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>) },
  delay = LONG_PRESS_MS,
  slop = LONG_PRESS_SLOP,
) {
  let timer: unknown = null;
  let origin = { x: 0, y: 0 };
  let fired = false;
  const cancel = () => { if (timer !== null) timers.clear(timer); timer = null; };
  return {
    start(x: number, y: number) {
      cancel();
      fired = false;
      origin = { x, y };
      timer = timers.set(() => { timer = null; fired = true; onFire(origin.x, origin.y); }, delay);
    },
    move(x: number, y: number) {
      if (timer !== null && Math.hypot(x - origin.x, y - origin.y) > slop) cancel();
    },
    cancel,
    /** The finger lifted: no more waiting, and a click that does not come must not stay swallowed. */
    release() {
      cancel();
      if (fired) timers.set(() => { fired = false; }, 400);
    },
    /** The browser raised its own context menu for this press: take it as the long press, once. */
    trigger() {
      if (timer === null) return;
      cancel();
      fired = true;
      onFire(origin.x, origin.y);
    },
    get pending() { return timer !== null; },
    /** True once for the click after a long press: the caller swallows it. */
    consumeClick(): boolean {
      const was = fired;
      fired = false;
      return was;
    },
    get firing() { return fired; },
  };
}
