import { emptyFilters, FILTERS_KEY, FILTERS_VERSION } from '@calli/ext-board/filters';
import type { WebHostStorage } from '@calli/extension-api/web';
import type { Thread } from '../api';
import type { LoopxGoal } from './status';
import { threadSummaries } from '../extensions/project';
import { goalOf } from './launch';

export const GOALS_BOARD_HASH = '#/board';

/** Legacy list and detail addresses have one successor. Malformed ids fall back to the board. */
export function legacyGoalRoute(hash: string): { goalId: string | null } | null {
  if (hash === '#/goals' || hash === '#/goals/') return { goalId: null };
  if (!hash.startsWith('#/goals/')) return null;
  try { return { goalId: decodeURIComponent(hash.slice('#/goals/'.length).split(/[/?]/)[0]) || null }; }
  catch { return { goalId: null }; }
}

/** The conversation board clears old narrowing so every bound conversation can appear. */
export function goalsBoardTarget(storage: WebHostStorage): string {
  storage.set(FILTERS_KEY, JSON.stringify({ ...emptyFilters(), goalOnly: true, v: FILTERS_VERSION }));
  return GOALS_BOARD_HASH;
}

/** Reading a Goal reuses its conversation; it never creates one or starts work. */
export function goalConversation(goalId: string, threads: Thread[]): Thread | null {
  return threads.find(thread => !thread.archivedAt && !thread.loopxInfo?.missing
    && (goalOf(thread)?.goalId ?? thread.loopxInfo?.goalId) === goalId) ?? null;
}

/** Goal identity survives reloads on the steward's in-place reader. */
export function goalOverviewHash(goalId: string | null): string {
  return goalId ? `#/steward?goal=${encodeURIComponent(goalId)}` : '#/steward?goals';
}

export function goalOverviewRequest(hash: string): { goalId: string | null } | null {
  const legacy = legacyGoalRoute(hash);
  if (legacy) return legacy;
  if (!hash.startsWith('#/steward?')) return null;
  const query = new URLSearchParams(hash.slice('#/steward?'.length));
  return query.has('goal') ? { goalId: query.get('goal') || null } : query.has('goals') ? { goalId: null } : null;
}

/** A Goal without a conversation belongs to the Core inventory, never an empty board. */
export function goalDestination(goalId: string | null, threads: Thread[]): { view: 'thread' | 'steward'; threadId: string | null; goalId: string | null } {
  const thread = goalId ? goalConversation(goalId, threads) : null;
  return { view: thread ? 'thread' : 'steward', threadId: thread?.id ?? null, goalId };
}

/** Use the board's own projection: archived, deleted and hidden team bindings are not cards. */
export function hasGoalConversations(goals: Pick<LoopxGoal, 'goalId'>[], threads: Thread[]): boolean {
  const ids = new Set(goals.map(goal => goal.goalId));
  return threadSummaries(threads, null).some(thread => thread.goal && ids.has(thread.goal.goalId));
}
