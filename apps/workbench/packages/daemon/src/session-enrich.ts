import type { AgentAdapter } from './adapters/types.js';
import { goalClues, lastGoal, readSessionEnds } from './session-classify.js';

/** What makes a native session a LoopX Goal session, and how that was found out. */
export interface SessionLoopx {
  goalId: string;
  /** The Goal's name; the id itself when the core does not give one. */
  goalTitle: string;
  /** The core has no such Goal any more: the session is still listed, but nothing is bound. */
  missing?: boolean;
  /** `core`: the core's external-session bindings say so; `content`: the session's own text does; `workbench`: started here. */
  basis: 'core' | 'content' | 'workbench';
  /** The finer cause: the host surface the core recorded, or the text shape (`bootstrap`, `activation`), or `launch`/`join`. */
  source: string;
  /** Every Goal the text named, oldest first, when more than one. */
  goals?: string[];
}

/** The two things this needs from the LoopX adapter, so the scan never depends on how it asks the core. */
export interface LoopxLookup {
  /** The core's bindings by host session id. Throws when the core cannot be asked. */
  bindings(): Promise<Map<string, { goalId: string; goalTitle: string | null; hostSurface: string }>>;
  /** The Goal's name, `'missing'` when the core has no such Goal, null when it could not be asked. */
  goal(goalId: string): Promise<{ title: string } | 'missing' | null>;
}

interface Read {
  updatedAt: string | null;
  origin: 'app' | 'cli' | null;
  clue: { goalId: string; kind: 'bootstrap' | 'activation'; all: string[] } | null;
}

/**
 * Works out, for each listed session, where it came from and which Goal (if any) it belongs to.
 * The files are read once per change of the session's last-activity time; the core's answers are
 * read on every pass, because a Goal is deleted or bound independently of the session file.
 */
export class SessionEnricher {
  private readonly reads = new Map<string, Read>();

  constructor(private readonly loopx: LoopxLookup | null, private readonly concurrency = 4) {}

  async enrich(agent: string, adapter: AgentAdapter, sessions: Array<{ sessionId: string; cwd: string; updatedAt: string | null; origin?: 'app' | 'cli' | null; loopx?: SessionLoopx }>): Promise<void> {
    const pending = sessions.filter((s) => this.reads.get(`${agent}/${s.sessionId}`)?.updatedAt !== s.updatedAt || !this.reads.has(`${agent}/${s.sessionId}`));
    const queue = [...pending];
    await Promise.all(Array.from({ length: Math.min(this.concurrency, queue.length) }, async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        this.reads.set(`${agent}/${next.sessionId}`, await this.read(adapter, next));
      }
    }));

    let core: Awaited<ReturnType<LoopxLookup['bindings']>> | null = null;
    if (this.loopx) core = await this.loopx.bindings().catch(() => null);
    const titles = new Map<string, Awaited<ReturnType<LoopxLookup['goal']>>>();
    const goal = async (goalId: string) => {
      if (!titles.has(goalId)) titles.set(goalId, await this.loopx!.goal(goalId).catch(() => null));
      return titles.get(goalId)!;
    };
    for (const session of sessions) {
      const read = this.reads.get(`${agent}/${session.sessionId}`);
      session.origin = read?.origin ?? null;
      if (!this.loopx) continue;
      const bound = core?.get(session.sessionId);
      const clue = read?.clue ?? null;
      const goalId = bound?.goalId ?? clue?.goalId;
      if (!goalId) continue;
      const found = await goal(goalId);
      // A core that cannot be asked proves nothing about the Goal, so the session is left plain until it can.
      if (found === null && !bound) continue;
      const missing = found === 'missing';
      session.loopx = {
        goalId,
        goalTitle: (found && found !== 'missing' ? found.title : bound?.goalTitle) || goalId,
        ...(missing ? { missing: true } : {}),
        basis: bound ? 'core' : 'content',
        source: bound ? bound.hostSurface : clue!.kind,
        ...(!bound && clue!.all.length > 1 ? { goals: clue!.all } : {}),
      };
    }
    // A session that is no longer listed would be read again if it came back; one that is gone is forgotten.
    const listed = new Set(sessions.map((s) => `${agent}/${s.sessionId}`));
    for (const key of this.reads.keys()) if (key.startsWith(`${agent}/`) && !listed.has(key)) this.reads.delete(key);
  }

  private async read(adapter: AgentAdapter, session: { sessionId: string; cwd: string; updatedAt: string | null }): Promise<Read> {
    const none: Read = { updatedAt: session.updatedAt, origin: null, clue: null };
    try {
      const { sessionPath } = (await adapter.sessionLocation?.(session.sessionId, session.cwd)) ?? { sessionPath: null };
      if (!sessionPath) return none;
      const ends = await readSessionEnds(sessionPath);
      if (!ends) return none;
      return { updatedAt: session.updatedAt, origin: adapter.sessionOrigin?.(ends.firstLine) ?? null, clue: lastGoal(goalClues(ends.text)) };
    } catch {
      return none;
    }
  }
}
