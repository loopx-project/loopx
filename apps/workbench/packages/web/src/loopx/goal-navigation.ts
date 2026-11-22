import { emptyFilters, FILTERS_KEY, FILTERS_VERSION } from '@calli/ext-board/filters';
import type { WebHostStorage } from '@calli/extension-api/web';
import type { Thread } from '../api';
import { goalOf } from './launch';

export const GOALS_BOARD_HASH = '#/board';

/** Legacy list and detail addresses have one successor. Malformed ids fall back to the board. */
export function legacyGoalRoute(hash: string): { goalId: string | null } | null {
  if (hash === '#/goals' || hash === '#/goals/') return { goalId: null };
  if (!hash.startsWith('#/goals/')) return null;
  try { return { goalId: decodeURIComponent(hash.slice('#/goals/'.length).split(/[/?]/)[0]) || null }; }
  catch { return { goalId: null }; }
}

/** All Goals means every bound conversation, without an older project or tag narrowing it. */
export function goalsBoardTarget(storage: WebHostStorage): string {
  storage.set(FILTERS_KEY, JSON.stringify({ ...emptyFilters(), goalOnly: true, v: FILTERS_VERSION }));
  return GOALS_BOARD_HASH;
}

/** Reading a Goal reuses its conversation; it never creates one or starts work. */
export function goalConversation(goalId: string, threads: Thread[]): Thread | null {
  return threads.find(thread => !thread.archivedAt && !thread.loopxInfo?.missing
    && (goalOf(thread)?.goalId ?? thread.loopxInfo?.goalId) === goalId) ?? null;
}
