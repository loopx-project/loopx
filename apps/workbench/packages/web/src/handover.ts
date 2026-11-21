import type { AgentMention, Thread } from './api';

/** Follow descendants only: another branch of the same root is not this conversation's recipient. */
export function latestHandoverThread(thread: Thread, threads: Thread[]): Thread {
  const descendants = new Set([thread.id]);
  for (let changed = true; changed;) {
    changed = false;
    for (const item of threads) {
      if (item.handover?.mode === 'continue' && descendants.has(item.handover.sourceId) && !descendants.has(item.id)) {
        descendants.add(item.id); changed = true;
      }
    }
  }
  const candidates = threads.filter(item => descendants.has(item.id));
  const parents = new Set(candidates.map(item => item.handover?.sourceId));
  const leaves = candidates.filter(item => !parents.has(item.id));
  return (leaves.length ? leaves : candidates).sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id))[0] ?? thread;
}

/**
 * The segment after `from` on the way to the chain's `latest` one: what reading an earlier segment
 * goes on into. Branches that do not lead to `latest` are not part of the way.
 */
export function nextSegment(from: Thread, latest: Thread, threads: Thread[]): Thread | undefined {
  const byId = new Map(threads.map(item => [item.id, item]));
  const seen = new Set<string>();
  for (let at: Thread | undefined = latest; at && !seen.has(at.id); at = at.handover?.mode === 'continue' ? byId.get(at.handover.sourceId) : undefined) {
    seen.add(at.id);
    if (at.handover?.mode === 'continue' && at.handover.sourceId === from.id) return at;
  }
  return undefined;
}

/**
 * What reading `from` goes on into: the next segment of its automatic chain toward `latest`, else
 * the newest conversation it was handed to by hand. A hand-over only counts while `from` has stood
 * still since: once it has had a turn of its own, or is running one, the two are separate
 * conversations and the successor does not follow it.
 */
export function laterSegment(from: Thread, latest: Thread, threads: Thread[]): Thread | undefined {
  const chained = nextSegment(from, latest, threads);
  if (chained || from.status === 'running') return chained;
  return threads
    .filter(item => item.handover?.sourceId === from.id && !item.handover.automatic && !item.handover.detached && item.handover.sourceMoved === false)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id))[0];
}

/** Reset loaded ancestors when the daemon changes a relation anywhere on the visible path. */
export function handoverHistoryKey(thread: Thread | undefined, threads: Thread[]): string {
  const seen = new Set<string>();
  const path: string[] = [];
  while (thread && !seen.has(thread.id)) {
    seen.add(thread.id); path.push(`${thread.id}:${thread.handover?.mode ?? ''}`);
    thread = thread.handover?.mode === 'continue' ? threads.find(t => t.id === thread!.handover!.sourceId) : undefined;
  }
  return path.join('/');
}

/**
 * While the thread's own agent is out of plan, bringing one other local instance in with @ hands it
 * the thread instead of asking the stranded agent to send it work.
 */
export function takeoverMention(mentions: AgentMention[], exhausted: boolean, current?: string): AgentMention | null {
  const [only] = mentions;
  return exhausted && mentions.length === 1 && only.kind === 'agent' && !only.host && only.id !== current ? only : null;
}
