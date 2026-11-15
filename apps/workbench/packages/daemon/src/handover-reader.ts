import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { HttpError } from './errors.js';
import type { Store, ThreadRecord } from './store.js';
import type { ThreadManager } from './threads.js';

/** Separate credentials grant access only to the recipient and its handover ancestors. */
export class HandoverReader {
  private credentials = new Map<string, string>();
  constructor(private store: Store, private manager: ThreadManager, private socket: string) {}

  environment(id: string): Record<string, string> | undefined {
    if (!this.store.getThread(id)?.handover) return undefined;
    let key = this.credentials.get(id);
    if (!key) { key = randomUUID(); this.credentials.set(id, key); }
    return { CALLI_HANDOVER_SOCKET: this.socket, CALLI_HANDOVER_KEY: key,
      CALLI_BIN_DIR: resolve(dirname(fileURLToPath(import.meta.url)), '../bin') };
  }

  private chain(id: string): ThreadRecord[] {
    const chain: ThreadRecord[] = [];
    const seen = new Set<string>();
    let thread = this.store.getThread(id);
    if (!thread?.handover) throw new HttpError(403, 'handover access unavailable');
    while (thread && !seen.has(thread.id)) {
      seen.add(thread.id); chain.unshift(thread);
      thread = thread.handover ? this.store.getThread(thread.handover.sourceId) : null;
    }
    return chain;
  }

  async command(key: string, input: Record<string, unknown>) {
    const id = [...this.credentials].find(([, value]) => value === key)?.[0];
    if (!id) throw new HttpError(401, 'invalid handover credential');
    const chain = this.chain(id);
    if (input.op === 'handover') {
      return { threadId: id, scope: 'self-and-ancestors', segments: await Promise.all(chain.map(t => this.manager.handoverLocation(t.id))),
        missingSource: chain[0]?.handover ?? null };
    }
    if (input.op !== 'history') throw new HttpError(403, 'handover credentials are read-only');
    if (typeof input.target !== 'string' || !chain.some(t => t.id === input.target)) throw new HttpError(403, 'thread outside handover chain');
    return readHistory(this.manager, input.target, input, 'calli history');
  }
}

/** Pages a thread's history in complete rounds, or one answer in chunks; `prefix` is the CLI command that continues. */
export function readHistory(manager: ThreadManager, target: string, input: Record<string, unknown>, prefix: string) {
  const number = (value: unknown, fallback: number | undefined, max: number, min = 1) => {
    if (value === undefined) return fallback;
    if ((typeof value !== 'number' && typeof value !== 'string') || !/^(0|[1-9]\d*)$/.test(String(value))) throw new HttpError(400, 'expected an integer');
    const n = Number(value);
    if (!Number.isSafeInteger(n) || n < min || n > max) throw new HttpError(400, `value must be between ${min} and ${max}`);
    return n;
  };
  // IDs from imported history can contain shell syntax; quote them as literal arguments.
  const quote = (value: string) => `'${value.replace(/'/g, "'\\''")}'`;
  const command = (turn: string, offset: number, snapshot: number) =>
    `${prefix} ${quote(target)} --turn ${quote(turn)} --snapshot ${snapshot} --offset ${offset}`;
  if (input.limit !== undefined) throw new HttpError(400, 'use --turns to page complete rounds');
  if (input.turn !== undefined) {
    if (typeof input.turn !== 'string' || !input.turn || input.before !== undefined || input.turns !== undefined) throw new HttpError(400, 'use --turn TURN_ID without --before or --turns');
    const offset = number(input.offset, 0, Number.MAX_SAFE_INTEGER, 0)!;
    const snapshot = number(input.snapshot, undefined, Number.MAX_SAFE_INTEGER);
    if (offset > 0 && snapshot === undefined) throw new HttpError(400, 'continuing text requires --snapshot from the previous response');
    const text = manager.history(target).handoverText(target, input.turn, offset, snapshot);
    return { ...text, command: text.nextOffset === null ? null : command(input.turn, text.nextOffset, text.snapshotSeq) };
  }
  if (input.offset !== undefined || input.snapshot !== undefined) throw new HttpError(400, '--offset and --snapshot require --turn');
  const page = manager.history(target).handoverPage(target,
    number(input.before, undefined, Number.MAX_SAFE_INTEGER), number(input.turns, 10, 50));
  return { ...page, turns: page.turns.map(turn => ({
    ...turn, answer: { ...turn.answer, fullText: turn.answer.fullText
      ? { ...turn.answer.fullText, command: command(turn.turnId, 0, page.snapshotSeq) } : null },
  })) };
}
