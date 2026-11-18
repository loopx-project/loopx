/** What deserves the user's attention in threads they are not looking at. Pure, so it can be tested directly. */
import type { Thread } from './api';
import { t } from './i18n';
import { isWait, threadAttention } from './sidebar';

const waitingReason = (thread: Thread) => thread.waitingReason ?? 'unknown';
export const waitingLabel = (thread: Thread): string => t(`notices.${waitingReason(thread)}.label`);

/** Threads that want attention: waiting for a response, or finished and not yet looked at. */
export function attentionCount(threads: Thread[], viewingId: string | null): number {
  return threads.filter((t) => t.waiting || (t.unread && t.id !== viewingId)).length;
}

/** The browser tab title: the count goes first so it survives a narrow tab. */
export function pageTitle(base: string, count: number): string {
  // The app can remount and read back a title that already carries a count; drop it so counts never stack.
  const plain = base.replace(/^(\(\d+\) )+/, '');
  return count > 0 ? `(${count}) ${plain}` : plain;
}

export type ThreadMark = 'waiting' | 'running' | 'error' | 'unread' | null;

/**
 * The one dot a thread gets in the list. Waiting for the user outranks everything else. A failure is
 * news like any other result: once the thread has been looked at, the dot goes; the error stays in the thread.
 */
export function threadMark(thread: Thread, selected: boolean): ThreadMark {
  const attention = threadAttention(thread, selected ? thread.id : null);
  if (isWait(attention)) return 'waiting';
  if (thread.status === 'running') return 'running';
  return attention?.kind === 'error' || attention?.kind === 'unread' ? attention.kind : null;
}
