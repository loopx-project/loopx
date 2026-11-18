import type { Locale } from '@calli/extension-api/web';
import type { BoardCard } from './columns';
import { t } from './i18n';

/** Why the card is where it is, coloured only when it asks something of the person, and since when. */
export function reasonOf(card: BoardCard, locale: Locale, now: number): { text: string; tone: string; at: string | null } {
  const { reason, thread } = card;
  const report = thread.report;
  if (reason.kind === 'attention') {
    return { text: t(locale, reason.attention), tone: reason.attention === 'error' ? 'error' : 'attention', at: thread.updatedAt };
  }
  if (reason.kind === 'running') {
    const start = reason.since ? Date.parse(reason.since) : NaN;
    const text = Number.isFinite(start) ? t(locale, 'runningMinutes', Math.max(0, Math.floor((now - start) / 60_000))) : t(locale, 'doing');
    return { text, tone: 'running', at: null };
  }
  if (reason.kind === 'report' && report) {
    const byUser = report.by === 'user';
    const text = t(locale, report.stage === 'backlog' ? 'placedBacklog' : report.stage === 'blocked' ? (byUser ? 'placedBlocked' : 'reportBlocked')
      : report.stage === 'doing' ? (byUser ? 'placedDoing' : 'reportDoing') : (byUser ? 'placedReview' : 'reportReview'));
    return { text, tone: report.stage === 'blocked' ? 'attention' : 'idle', at: report.at };
  }
  if (reason.kind === 'done') return { text: t(locale, 'finished'), tone: 'idle', at: thread.doneAt };
  const unread = reason.kind === 'idle' && reason.unread;
  return { text: t(locale, unread ? 'unread' : 'idle'), tone: unread ? 'unread' : 'idle', at: thread.updatedAt };
}

/** A machine's name; one with no label of its own is the hub's machine. */
export function machineName(machine: { label: string }, locale: Locale): string {
  return machine.label.trim() || t(locale, 'localMachine');
}
