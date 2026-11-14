/** What the import panel shows. Pure, so it can be tested directly. */
import type { AgentSession } from './api';
import { folderName } from './paths';
import { formatDate, t } from './i18n';

/** How many sessions the panel lists at once; searching narrows down the rest. */
export const SESSION_LIMIT = 100;

/** Sessions whose title or directory contains the search text, in the order given. */
export function filterSessions(sessions: AgentSession[], search: string): AgentSession[] {
  const query = search.trim().toLowerCase();
  if (!query) return sessions;
  return sessions.filter((s) => s.title.toLowerCase().includes(query) || s.cwd.toLowerCase().includes(query));
}

export function sessionTitle(session: AgentSession): string {
  return session.title.trim() || t('sessions.untitled', folderName(session.cwd));
}

/** "3 hours ago" for recent times, a date for older ones, nothing when the time is unknown. */
export function timeAgo(iso: string | null, now: number): string {
  const then = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(then)) return '';
  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return t('time.justNow');
  if (minutes < 60) return t('time.minutesAgo', minutes);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t('time.hoursAgo', hours);
  const days = Math.floor(hours / 24);
  if (days < 7) return t('time.daysAgo', days);
  const d = new Date(then);
  const thisYear = d.getFullYear() === new Date(now).getFullYear();
  return formatDate(d, { ...(thisYear ? {} : { year: 'numeric' }), month: 'short', day: 'numeric' });
}
