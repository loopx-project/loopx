import { createHash } from 'node:crypto';
import type { Store } from './store.js';

/**
 * The hub appends short protocol blocks to what the person sent (how to use `calli`, where to write
 * progress, ...). An agent keeps its conversation, so a block it has already read needs no copy on
 * every turn: it is attached again only when the agent may no longer have it in view, which is
 * when the block itself changed (another role, other folders), the agent's session changed, the
 * context was compacted or a turn failed before it was read, or enough turns have passed that it
 * is worth a reminder. What was attached lives in the database, so a restart does not repeat it.
 */

export type InjectionKind = 'coordination' | 'progress' | 'tools' | 'todo';

/** Whether a block must ride along with this message. */
export type InjectionGate = (kind: InjectionKind, block: string) => boolean;

/** Turns after which a block already read is attached once more. */
export const REMIND_EVERY = 12;

const BLOCK = /<calli-(coordination|progress|tools|todo)>[\s\S]*?<\/calli-\1>/;
const digest = (block: string) => createHash('sha256').update(block).digest('hex').slice(0, 16);

export class Injections {
  constructor(private readonly store: Store) {}

  /** For the message about to go to this thread's agent. */
  needed(threadId: string, kind: InjectionKind, block: string): boolean {
    const seen = this.store.injection(threadId, kind);
    if (!seen || seen.digest !== digest(block) || seen.turns >= REMIND_EVERY) return true;
    const session = this.store.getThread(threadId)?.agentSessionId ?? null;
    // The first turn begins before its session exists: that one is the session that read the block.
    if (seen.session === null && session !== null) this.store.saveInjection(threadId, kind, { ...seen, session });
    return seen.session !== null && session !== seen.session;
  }

  /** A turn began with this text: what it carried is now read, and the blocks it left out got one turn older. */
  started(threadId: string, text: string): void {
    const session = this.store.getThread(threadId)?.agentSessionId ?? null;
    const carried = new Map<string, string>();
    for (const part of text.matchAll(new RegExp(BLOCK, 'g'))) if (!carried.has(part[1])) carried.set(part[1], part[0]);
    for (const kind of ['coordination', 'progress', 'tools', 'todo'] as const) {
      const block = carried.get(kind);
      if (block) this.store.saveInjection(threadId, kind, { digest: digest(block), session, turns: 0 });
      else this.store.ageInjection(threadId, kind);
    }
  }

  /** The agent may no longer have what it was told in view: everything is attached again. */
  forget(threadId: string): void {
    this.store.clearInjections(threadId);
  }
}
