import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';
import { freshQuota, instanceWindows, type AutoHandover } from './auto-handover.js';
import type { PlanQuota } from './adapters/types.js';
import { HttpError } from './errors.js';

/**
 * 额度恢复自动续跑：a thread whose plan ran out and was NOT handed over stays parked on its
 * exhausted agent. When the person (or the global setting) arms a watch, the daemon wakes at the
 * quota reset time, verifies a fresh reading is really below the threshold, then resends the
 * failed turn and releases the queued messages. Runs entirely in the daemon: browsers need not
 * be online, and watches survive restarts because they live in hub settings.
 */

export interface ResumeWatch {
  agent: string;
  /** The turn that died of `plan_exhausted`; resent verbatim on recovery. */
  failedTurn: string;
  origin: 'user' | 'global';
  /** Reset time of the binding quota window when one was known; null when only probing. */
  resetsAt: string | null;
  nextCheckAt: string;
  probes: number;
  createdAt: string;
  lastError?: string;
}

export interface ResumeView {
  origin: 'user' | 'global';
  resetsAt: string | null;
  nextCheckAt: string;
  lastError?: string;
}

const RESUMES_KEY = 'handover.resumes';
/** Give the provider a moment past the stated reset time before asking it again. */
export const RESUME_GRACE_MS = 2 * 60_000;
/** Give up after this many probes that still read exhausted, or after a day of waiting. */
const MAX_PROBES = 10;
const MAX_AGE_MS = 24 * 3600_000;
/** Backoff between probes when the reset time is unknown or was missed. Minutes. */
const RESUME_BACKOFF_MS = [5, 15, 30, 60, 120].map(m => m * 60_000);
const envNumber = (name: string, fallback: number) => {
  const v = Number(process.env[name]);
  return Number.isFinite(v) && v > 0 ? v : fallback;
};

export class QuotaResume {
  private timer: ReturnType<typeof setInterval> | null = null;
  private closed = false;
  private checking = false;

  private graceMs = envNumber('CALLI_RESUME_GRACE_MS', RESUME_GRACE_MS);

  constructor(
    private readonly store: Store,
    private readonly manager: ThreadManager,
    private readonly autoHandover: AutoHandover,
    private readonly tickMs = envNumber('CALLI_RESUME_TICK_MS', 30_000),
    private readonly backoffMs: number[] = RESUME_BACKOFF_MS,
  ) {}

  /** The daemon owns the loop; tests drive `check()` directly instead. */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => void this.check().catch(() => undefined), this.tickMs);
    this.timer.unref?.();
  }

  dispose(): void {
    this.closed = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private watches(): Record<string, ResumeWatch> { return this.store.hubSetting(RESUMES_KEY, {}); }
  private save(watches: Record<string, ResumeWatch>): void { this.store.saveHubSetting(RESUMES_KEY, watches); }

  view(id: string): ResumeView | null {
    const w = this.watches()[id];
    if (!w) return null;
    return { origin: w.origin, resetsAt: w.resetsAt, nextCheckAt: w.nextCheckAt, ...(w.lastError ? { lastError: w.lastError } : {}) };
  }

  /** A thread the person can arm: present, idle, and its last turn died of plan exhaustion. */
  private parkedCheck(id: string): { agent: string; failedTurn: string; model?: string } {
    const thread = this.manager.getThread(id);
    if (thread.archivedAt) throw new HttpError(409, 'the thread is archived');
    const view = this.manager.threadView(id);
    if (view.status === 'running' || view.waiting) throw new HttpError(409, 'the thread is busy');
    const last = this.store.listTurns(id).at(-1)?.turnId ?? null;
    const failed = last && this.store.listTurnEvents(id, last).some(e => e.type === 'turn.error' && e.data.code === 'plan_exhausted')
      ? last : null;
    if (!failed) throw new HttpError(409, 'the thread did not just run out of plan');
    return { agent: thread.agent, failedTurn: failed, ...(thread.model ? { model: thread.model } : {}) };
  }

  /** The person's click on the exhausted turn's card, or a park while the global setting is on. */
  async arm(id: string, origin: 'user' | 'global' = 'user'): Promise<ResumeView> {
    const parked = this.parkedCheck(id);
    const watches = this.watches();
    const existing = watches[id];
    const resetsAt = await this.resetHint(parked.agent, parked.model);
    watches[id] = {
      agent: parked.agent,
      failedTurn: parked.failedTurn,
      origin: existing?.origin === 'user' ? 'user' : origin,
      resetsAt,
      nextCheckAt: this.schedule(resetsAt),
      probes: 0,
      createdAt: new Date().toISOString(),
    };
    this.save(watches);
    this.manager.notifyHandover(id);
    return this.view(id)!;
  }

  drop(id: string): void {
    const watches = this.watches();
    if (!watches[id]) return;
    delete watches[id];
    this.save(watches);
    this.manager.notifyHandover(id);
  }

  /** Called after a turn ended exhausted and no handover moved the work away. */
  parked(id: string): void {
    if (!this.autoHandover.settings().resume) return;
    void this.arm(id, 'global').catch(() => undefined); // not in an armable state: the card's link stays
  }

  /** The global switch: off drops everything it armed, on adopts the threads parked right now. */
  async onSettings(resume: boolean): Promise<void> {
    if (!resume) {
      const watches = this.watches();
      const kept = Object.fromEntries(Object.entries(watches).filter(([, w]) => w.origin === 'user'));
      if (Object.keys(kept).length !== Object.keys(watches).length) this.save(kept);
      return;
    }
    for (const thread of this.manager.listThreads()) {
      if (thread.archivedAt || thread.status === 'running' || this.view(thread.id)) continue;
      try { this.parkedCheck(thread.id); await this.arm(thread.id, 'global'); } catch { /* not parked */ }
    }
  }

  /** Earliest future reset among the instance's windows over threshold; free windows do not bind.
   * Refreshed on purpose: a cached reading (another thread's arm, say) would schedule this watch
   * on a reset time that is no longer this thread's. */
  private async resetHint(agent: string, model?: string): Promise<string | null> {
    const quota = await this.manager.agentQuota(agent, true).catch(() => null);
    if (!quota) return null;
    const now = Date.now();
    const windows = instanceWindows(quota, model ?? null);
    const binding = windows
      .filter(w => w.usedPercent >= this.autoHandover.settings().threshold && w.resetsAt && Date.parse(w.resetsAt!) > now)
      .map(w => Date.parse(w.resetsAt!)).sort((a, b) => a - b)[0];
    const any = binding ?? windows
      .filter(w => w.resetsAt && Date.parse(w.resetsAt!) > now)
      .map(w => Date.parse(w.resetsAt!)).sort((a, b) => a - b)[0];
    return any ? new Date(any).toISOString() : null;
  }

  private schedule(resetsAt: string | null): string {
    const at = resetsAt ? Math.max(Date.parse(resetsAt) + this.graceMs, Date.now()) : Date.now();
    return new Date(at).toISOString();
  }

  /** One pass over due watches. The interval calls it; tests call it directly. */
  async check(): Promise<void> {
    if (this.closed || this.checking) return;
    const due = Object.entries(this.watches()).filter(([, w]) => Date.parse(w.nextCheckAt) <= Date.now());
    if (!due.length) return;
    this.checking = true;
    try {
      const quotaByAgent = new Map<string, PlanQuota | null>();
      for (const [id, w] of due) {
        const thread = this.store.getThread(id);
        if (!thread || thread.archivedAt || !this.manager.hasAgent(w.agent)) { this.drop(id); continue; }
        const view = this.manager.threadView(id);
        // The person took the thread back by hand; waiting for the quota is moot.
        if (view.status === 'running') { this.drop(id); continue; }
        if (view.waiting) { this.postpone(id, Date.now() + 15 * 60_000); continue; }
        if (!quotaByAgent.has(w.agent)) {
          const quota = await this.manager.agentQuota(w.agent, true)
            .then(q => freshQuota(q) ? q : null)
            .catch(() => null);
          quotaByAgent.set(w.agent, quota);
        }
        const quota = quotaByAgent.get(w.agent) ?? null;
        const threshold = this.autoHandover.settings().threshold;
        const windows = quota ? instanceWindows(quota, thread.model ?? null) : [];
        const over = windows.filter(x => x.usedPercent >= threshold);
        if (windows.length && !over.length) { await this.resume(id); continue; }
        const binding = over.map(x => x.resetsAt ? Date.parse(x.resetsAt) : null)
          .filter(t => t !== null && t > Date.now()).sort((a, b) => a! - b!)[0] ?? null;
        this.postpone(id, binding ? binding + this.graceMs : undefined, '额度还没有恢复');
      }
    } finally { this.checking = false; }
  }

  private postpone(id: string, at?: number, error?: string): void {
    const watches = this.watches();
    const w = watches[id];
    if (!w) return;
    w.probes += 1;
    w.lastError = error;
    const age = Date.now() - Date.parse(w.createdAt);
    if (w.probes >= MAX_PROBES || age > MAX_AGE_MS) { this.drop(id); return; }
    const backoff = this.backoffMs[Math.min(w.probes - 1, this.backoffMs.length - 1)];
    w.nextCheckAt = new Date(at ?? Date.now() + backoff).toISOString();
    this.save(watches);
    this.manager.notifyHandover(id);
  }

  /** Quota verified back: clear the stop, mark the timeline, and resend the failed ask. */
  private async resume(id: string): Promise<void> {
    const w = this.watches()[id];
    if (!w) return;
    this.drop(id);
    this.autoHandover.clearNotice(id);
    this.manager.recordQuotaResume(id, w.resetsAt);
    try {
      await this.manager.retryAutoHandoverTurn(id, w.failedTurn);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      try { this.manager.notice(id, `额度已恢复，但自动继续失败：${message.slice(0, 200)}`); } catch { /* the thread went away */ }
    }
  }
}
