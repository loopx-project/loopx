/** What deserves the user's attention in threads they are not looking at. Pure, so it can be tested directly. */
import type { Thread } from './api';
import { t } from './i18n';
import { isWait, threadAttention } from './sidebar';

export interface Notice {
  threadId: string;
  kind: 'waiting' | 'error';
  message: string;
  /** Label of the button that takes the user to the thread. */
  action: string;
}

const short = (title: string) => (title.length > 24 ? `${title.slice(0, 23)}…` : title);

const waitingReason = (thread: Thread) => thread.waitingReason ?? 'unknown';
export const waitingLabel = (thread: Thread): string => t(`notices.${waitingReason(thread)}.label`);

/**
 * Compares two snapshots of the thread list and says what just happened elsewhere: a thread started
 * waiting for a response, or a turn failed. `viewingId` is the thread on
 * screen (null when the page is hidden or no thread is open); what happens there needs no notice.
 *
 * Successful completion stays quiet; unread markers and the tab count carry that state.
 * A failed turn is recognised by becoming unread, even if it ran between two snapshots.
 */
export function threadNotices(before: Thread[], after: Thread[], viewingId: string | null): Notice[] {
  const previous = new Map(before.map((t) => [t.id, t]));
  const out: Notice[] = [];
  for (const thread of after) {
    const was = previous.get(thread.id);
    if (!was || thread.id === viewingId) continue;
    if (thread.waiting && (!was.waiting || thread.waitingReason !== was.waitingReason)) {
      const reason = waitingReason(thread);
      out.push({ threadId: thread.id, kind: 'waiting', message: t(`notices.${reason}.message`, short(thread.title)), action: t(`notices.${reason}.action`) });
    } else if (thread.unread && !was.unread && thread.status === 'error') {
      out.push({ threadId: thread.id, kind: 'error', message: t('notices.error', short(thread.title)), action: t('notices.open') });
    }
  }
  return out;
}

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
