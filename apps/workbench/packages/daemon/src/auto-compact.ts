import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';
import type { StoredEvent } from './events.js';
import { instanceWindows, type HandoverTarget } from './auto-handover.js';
import { synthesizeBrief, turnMessage } from './handover.js';
import { compressPrompt, mergePrompt } from './compress-prompts.js';
import { HttpError } from './errors.js';

/**
 * Quota-aware context compression: when the account a thread runs on is close to its plan limit
 * (85%, say) but the handover has not moved the work yet, the thread's transcript — the hub event
 * log, readable for free, followed back through handover links so a relayed thread brings the
 * generations it inherited — is compressed into a brief by *another* agent (a bridge to third-party
 * models, so official-account quota buys no summarizing), and the work continues in a fresh session
 * of the same account carrying that brief instead of half a million tokens of context. Without a
 * compression agent the thread is natively `/compact`ed in place, which spends one turn of the same
 * account but no relay.
 */
export interface AutoCompactSettings {
  enabled: boolean;
  /** Plan usage, in percent, at which a turn's end triggers the compaction. Below the handover threshold. */
  threshold: number;
  /**
   * The agents that read the transcript and write the brief, in the order they are tried: the next
   * one is asked when one is unavailable or fails. Empty for a native `/compact` in place.
   */
  agents: HandoverTarget[];
  /** A transcript shorter than this is not worth a compaction, however close the quota is. */
  minChars: number;
  /** Chunks of this size are compressed in parallel; their notes are then merged into one brief. */
  chunkChars: number;
  /** The transcript is capped at this size, keeping the newest part. */
  maxChars: number;
}

export interface AutoCompactStatus {
  last?: { threadId: string; successorId?: string; native: boolean; chars: number; chunks: number; at: string };
  error?: { threadId: string; message: string; at: string };
  skipped?: { threadId: string; reason: 'transcript-too-small'; chars: number; at: string };
}

const DEFAULTS: AutoCompactSettings = { enabled: false, threshold: 85, agents: [], minChars: 240_000, chunkChars: 240_000, maxChars: 1_500_000 };

/** Settings saved before the list of agents named one agent and its model. */
type LegacyInput = { agent?: string | null; model?: string | null };
const legacyAgents = (agent?: string | null, model?: string | null): HandoverTarget[] => (agent ? [{ agent, ...(model ? { model } : {}) }] : []);

/** Per attempt. Antigravity took 281s on its default model for a 35k-character record (160s before the first word), so five minutes is too tight. */
const COMPRESS_TIMEOUT_MS = 600_000;

const trunc = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n)}…` : s);

/** The conversation as the hub recorded it: one user ask and its answer per turn, handover and control turns left out. */
export function transcript(events: StoredEvent[]): string {
  const byTurn = new Map<string, StoredEvent[]>();
  for (const e of events) {
    if (!e.turnId) continue;
    const list = byTurn.get(e.turnId);
    if (list) list.push(e); else byTurn.set(e.turnId, [e]);
  }
  const parts: string[] = [];
  for (const [, evs] of byTurn) {
    const start = evs.find(e => e.type === 'turn.started');
    if (!start || start.data.handover || start.data.control) continue;
    const ask = String(start.data.displayText ?? start.data.text ?? '').trim();
    if (!ask) continue;
    const answer = turnMessage(evs).trim();
    parts.push(`## 用户\n${trunc(ask, 2000)}\n## 回答\n${answer ? trunc(answer, 4000) : '（无文字输出）'}`);
  }
  return parts.join('\n\n');
}

/** Slices `text` into chunks of at most `size` chars, overlapping a little so a cut lands in both. */
export function chunkText(text: string, size: number): string[] {
  if (size < 1000) return [text];
  if (text.length <= size) return [text];
  const overlap = Math.min(2000, Math.floor(size / 10));
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size - overlap) out.push(text.slice(i, i + size));
  return out;
}

export class AutoCompact {
  constructor(private store: Store, private manager: ThreadManager) {}

  settings(): AutoCompactSettings {
    const { agent, model, ...stored } = this.store.hubSetting<LegacyInput & Partial<AutoCompactSettings>>('autocompact.settings', {});
    return { ...DEFAULTS, ...stored, agents: stored.agents ?? legacyAgents(agent, model) };
  }

  /** The compression agents as given, checked: known instances, no repeats, a handful at most. */
  private parseAgents(input: unknown): HandoverTarget[] {
    const invalid = (): never => { throw new HttpError(400, 'invalid auto-compact settings'); };
    if (!Array.isArray(input) || input.length > 8) return invalid();
    const seen = new Set<string>();
    return input.map(t => {
      if (!t || typeof t.agent !== 'string' || !this.manager.hasAgent(t.agent)
        || t.model !== undefined && t.model !== null && (typeof t.model !== 'string' || !t.model || t.model.length > 200)) return invalid();
      const key = JSON.stringify([t.agent, t.model ?? null]);
      if (seen.has(key)) return invalid();
      seen.add(key);
      return { agent: t.agent, ...(t.model ? { model: t.model } : {}) };
    });
  }

  configure(input: unknown): AutoCompactSettings {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'invalid auto-compact settings');
    const v = input as LegacyInput & Partial<AutoCompactSettings>;
    const int = (x: unknown, min: number, max: number): number => {
      if (!Number.isSafeInteger(x) || (x as number) < min || (x as number) > max) throw new HttpError(400, 'invalid auto-compact settings');
      return x as number;
    };
    if (typeof v.enabled !== 'boolean') throw new HttpError(400, 'invalid auto-compact settings');
    const threshold = int(v.threshold, 50, 98);
    const agents = this.parseAgents(v.agents ?? legacyAgents(v.agent, v.model));
    const minChars = v.minChars === undefined ? DEFAULTS.minChars : int(v.minChars, 100, 5_000_000);
    const chunkChars = v.chunkChars === undefined ? DEFAULTS.chunkChars : int(v.chunkChars, 20_000, 1_000_000);
    const maxChars = v.maxChars === undefined ? DEFAULTS.maxChars : int(v.maxChars, 100_000, 10_000_000);
    if (chunkChars > maxChars) throw new HttpError(400, 'invalid auto-compact settings');
    const handover = this.manager.autoHandover.settings();
    if (v.enabled && handover.enabled && threshold >= handover.threshold) {
      throw new HttpError(400, `auto-compact must fire before the handover threshold (${handover.threshold}), got ${threshold}`);
    }
    const settings: AutoCompactSettings = { enabled: v.enabled, threshold, agents, minChars, chunkChars, maxChars };
    this.store.saveHubSetting('autocompact.settings', settings);
    return settings;
  }

  status(): AutoCompactStatus { return this.store.hubSetting<AutoCompactStatus>('autocompact.status', {}); }
  private report(status: AutoCompactStatus): void { this.store.saveHubSetting('autocompact.status', status); }

  /** One compaction per session: after it the work sits in a fresh (native) or successor (brief) session. */
  private done(): Record<string, boolean> { return this.store.hubSetting('autocompact.done', {}); }
  private markDone(threadId: string, sessionId: string): void {
    const done = this.done();
    done[`${threadId}:${sessionId}`] = true;
    this.store.saveHubSetting('autocompact.done', done);
  }

  /**
   * After a turn ended: compact when the account is near its plan limit and the conversation is
   * long, unless the handover is already moving the work. Returns true when something was started.
   */
  /** The thread's transcript, followed back through handover links: a relayed thread's own events
   *  are only its generation, while the session it would hand a successor carries the whole chain.
   *  Newest part kept, capped at the compressor's input budget. */
  private async chainTranscript(threadId: string, maxChars: number): Promise<string> {
    const parts: string[] = [];
    let before: number | undefined;
    let total = 0;
    for (let i = 0; i < 500; i++) {
      const page = await this.store.history.page(threadId, before, 0, true);
      const text = transcript(page.events as StoredEvent[]);
      parts.unshift(text);
      total += text.length;
      if (total >= maxChars || page.nextCursor === null) break;
      before = page.nextCursor;
    }
    const all = parts.join('\n');
    return all.length > maxChars ? all.slice(all.length - maxChars) : all;
  }

  async settled(threadId: string, turnId: string): Promise<boolean> {
    const settings = this.settings();
    if (!settings.enabled) return false;
    const thread = this.store.getThread(threadId);
    if (!thread || thread.archivedAt || !thread.agentSessionId) return false;
    if (this.done()[`${threadId}:${thread.agentSessionId}`]) return false;
    const events = this.store.listEvents(threadId, 0, -1);
    const turnEvents = events.filter(e => e.turnId === turnId);
    const start = turnEvents.find(e => e.type === 'turn.started');
    const end = turnEvents.find(e => e.type === 'turn.done' || e.type === 'turn.error');
    // A failed turn is the handover's business; a compaction needs a healthy account to continue in.
    if (!start || start.data.handover || start.data.control || !end || end.type !== 'turn.done' || end.data.stopReason === 'cancelled') return false;
    if (this.busy(threadId)) return false;
    // A reading up to a few minutes old decides as well as a fresh one, and a probe that fails must
    // not silently shelve the compaction: the account is still running down either way.
    const quota = await this.manager.decisionQuota(thread.agent);
    if (!quota) return false;
    const model = await this.manager.handoverModel(thread.agent, thread.model);
    if (!instanceWindows(quota, model).some(w => w.usedPercent >= settings.threshold)) return false;
    const text = await this.chainTranscript(threadId, settings.maxChars);
    if (text.length < settings.minChars) {
      this.report({ ...this.status(), skipped: { threadId, reason: 'transcript-too-small', chars: text.length, at: new Date().toISOString() } });
      return false;
    }
    try {
      if (!settings.agents.length) {
        // No compression agent: spend one turn of the same account on its own native compaction.
        this.markDone(threadId, thread.agentSessionId);
        await this.manager.startTurn(threadId, { text: '/compact' });
        this.report({ ...this.status(), last: { threadId, native: true, chars: text.length, chunks: 1, at: new Date().toISOString() } });
        return true;
      }
      const brief = await this.compress(threadId, thread.cwd, text, settings);
      // The account may have answered a queued message while the compressor was reading; the relay
      // needs an idle thread, and a busy one keeps its context for the next attempt.
      if (this.busy(threadId)) return false;
      const target: HandoverTarget = { agent: thread.agent, ...(model ? { model } : {}) };
      const successor = await this.manager.executeAutoHandover(threadId, target, brief, null);
      this.markDone(threadId, thread.agentSessionId);
      this.report({ ...this.status(), last: { threadId, successorId: successor.id, native: false, chars: text.length, chunks: chunkText(text.length > settings.maxChars ? text.slice(text.length - settings.maxChars) : text, settings.chunkChars).length, at: new Date().toISOString() } });
      return true;
    } catch (error) {
      this.report({ ...this.status(), error: { threadId, message: error instanceof Error ? error.message : String(error), at: new Date().toISOString() } });
      return false;
    }
  }

  private busy(threadId: string): boolean {
    return this.manager.threadView(threadId).waiting || this.manager.getThread(threadId).status === 'running'
      || this.manager.autoHandover.held(threadId) || this.manager.autoHandover.queuePaused(threadId);
  }

  /** The transcript compressed into one summary by the compression agent, and who wrote it. */
  private async compressBody(threadId: string, text: string, settings: AutoCompactSettings): Promise<{ body: string; label: string }> {
    const agents = await this.manager.listAgents();
    const body = text.length > settings.maxChars ? `（更早的记录已截断）\n${text.slice(text.length - settings.maxChars)}` : text;
    const chunks = chunkText(body, settings.chunkChars);
    let failure: unknown = new HttpError(409, 'no compression agent configured');
    // In the configured order; one that is unavailable or fails hands the job to the next.
    for (const target of settings.agents) {
      const compressor = agents.find(a => a.id === target.agent && a.available && !a.hidden);
      try {
        if (!compressor) throw new HttpError(409, `compression agent unavailable: ${target.agent}`);
        const ask = (prompt: string): Promise<string> => this.manager.askOnce(target.agent, prompt,
          { model: target.model ?? null, title: `自动压缩 · ${threadId.slice(0, 8)}`, timeoutMs: COMPRESS_TIMEOUT_MS });
        const notes = await Promise.all(chunks.map((c, i) => ask(compressPrompt(i + 1, chunks.length, c))));
        const compressed = (chunks.length > 1 ? await ask(mergePrompt(notes)) : notes[0]).trim();
        if (!compressed) throw new HttpError(502, 'the compression agent returned nothing');
        return { body: compressed, label: `${compressor.label}${target.model ? ` · ${target.model}` : ''}` };
      } catch (error) {
        failure = error;
        console.error(`[compress] ${target.agent} could not compress ${threadId}: ${error instanceof Error ? error.message : error}`);
      }
    }
    throw failure;
  }

  /**
   * A summary for a handover whose source cannot write one itself (its plan ran out, or its summary
   * turn failed): the compression agent reads the hub's record, so neither the source's quota nor
   * the successor's context pays for it. Null without a compression agent, or when it fails, and
   * the handover goes on with the brief the hub assembles from the log.
   */
  async summarizeForHandover(threadId: string): Promise<string | null> {
    const settings = this.settings();
    if (!settings.agents.length) return null;
    try {
      const text = await this.chainTranscript(threadId, settings.maxChars);
      if (!text.trim()) return null;
      const { body, label } = await this.compressBody(threadId, text, settings);
      return [`## 进度（由 ${label} 从记录整理）`, '', trunc(body, 30_000)].join('\n');
    } catch (error) {
      console.error(`[handover] could not summarize ${threadId}: ${error instanceof Error ? error.message : error}`);
      return null;
    }
  }

  private async compress(threadId: string, cwd: string, text: string, settings: AutoCompactSettings): Promise<string> {
    const { body: compressed, label } = await this.compressBody(threadId, text, settings);
    const facts = synthesizeBrief(this.store.listEvents(threadId, 0, -1));
    return [
      '# 上下文压缩',
      '',
      `线程 ${threadId} 的上下文已由 ${label} 压缩；你继续的是同一个任务，不是新任务。`,
      `工作目录 ${cwd}。先运行 git status、git diff 对齐现状，再从「未完成·下一步」继续。`,
      '',
      compressed,
      ...(facts ? ['', facts] : []),
    ].join('\n');
  }
}
