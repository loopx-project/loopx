/** What extensions see of a thread. Pure, so it can be tested directly. */
import type { ThreadSummary } from '@calli/extension-api';
import type { Thread } from '../api';
import { projectDir, threadAttention } from '../sidebar';

/**
 * The whitelisted summary of a thread, as the daemon's extension host makes it, with `unread`
 * and `attention` worked out for this device: the thread on screen is never unread. Nothing that
 * may hold conversation content is copied: no `preview`, no queued messages.
 */
export function threadSummary(t: Thread, selectedId: string | null): ThreadSummary {
  return {
    id: t.id,
    title: t.title,
    agent: t.agent,
    project: t.scratch ? '' : projectDir(t).real,
    projectGone: Boolean(t.projectGone),
    status: t.status,
    attention: threadAttention(t, selectedId),
    runningSince: t.status === 'running' ? (t.runningSince ?? null) : null,
    unread: t.unread && t.id !== selectedId,
    pinned: t.pinned,
    archived: t.archivedAt !== null,
    scheduled: Boolean(t.schedule),
    worktree: Boolean(t.worktree),
    handover: Boolean(t.handover),
    teamSize: t.coordination && t.coordination.rootId === t.id ? t.coordination.children : 0,
    doneAt: t.doneAt ?? null,
    // Content: the host hands it on only to views holding `progress.report`.
    report: t.report ?? null,
    createdAt: t.createdAt,
    updatedAt: t.updatedAt,
  };
}

/** The threads a view gets: not archived, and team members only when their lead is not listed (as in the sidebar). */
export function threadSummaries(threads: Thread[], selectedId: string | null): ThreadSummary[] {
  const ids = new Set(threads.map((t) => t.id));
  return threads
    .filter((t) => !t.archivedAt && (!t.coordination || t.coordination.rootId === t.id || !ids.has(t.coordination.rootId)))
    .map((t) => threadSummary(t, selectedId));
}

/** Machine metadata appears only in the merged view. */
export function machineSummaries(sources: Array<{ hostId: string | null; label: string; threads: Thread[] }>, selectedId: string | null, merged: boolean): ThreadSummary[] {
  return sources.flatMap(source => threadSummaries(source.threads, selectedId).map(thread =>
    merged ? { ...thread, machine: { id: source.hostId, label: source.label } } : thread));
}

/** UUIDs are unique across machines; unknown threads must never fall through to the local API. */
export function threadMachine(sources: Array<{ hostId: string | null; threads: Thread[] }>, id: string): string | null {
  const source = sources.find(source => source.threads.some(thread => thread.id === id));
  if (!source) throw new Error('Thread not found');
  return source.hostId;
}

export function parseThreadLink(hash: string): { id: string; machine: string | null } | null {
  if (!hash.startsWith('#/thread/')) return null;
  try {
    const [id, query] = hash.slice('#/thread/'.length).split('?');
    return { id: decodeURIComponent(id), machine: new URLSearchParams(query).get('machine') || null };
  } catch { return null; }
}
