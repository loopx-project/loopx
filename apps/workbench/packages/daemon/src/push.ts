import { LAST_BRIEF_KEY, type BriefKind, type PersonalBrief, type LastPersonalBrief } from './personal-brief.js';
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import type { StoredEvent } from './events.js';
import { HttpError } from './errors.js';
import { attentionOf, turnOrigin, turnOutcome } from './lifecycle.js';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';

export interface PushSettings {
  enabled: boolean;
  recipient: string;
  baseUrl: string;
  waiting: boolean;
  failure: boolean;
  notice: boolean;
  scheduled: boolean;
}
export type PushKind = 'approval' | 'question' | 'plan' | 'failure' | 'notice' | 'scheduled' | 'confirmation' | 'test' | BriefKind;
export interface PushRecord {
  key: string;
  at: string;
  kind: PushKind;
  threadId: string | null;
  title: string;
  result: 'queued' | 'sent' | 'failed' | 'dropped';
  reason: string | null;
}
const defaults: PushSettings = { enabled: false, recipient: '', baseUrl: '', waiting: true, failure: true, notice: true, scheduled: false };
const labels: Record<PushKind, string> = { approval: '等你批准', question: '等你回答', plan: '计划待确认', failure: '一轮失败', notice: '通知你', scheduled: '定时任务完成', confirmation: '待你确认', test: '测试推送', morning: '早报', evening: '晚报' };
type Identity = { cli: boolean; botReady: boolean; recipient: string; reason: string | null };
type Item = { record: PushRecord; body: string; settings: PushSettings; briefText?: string };

/** Only live events enter this bounded, in-memory queue; startup never replays history. */
export class PushService {
  private readonly off: () => void;
  private readonly waits = new Map<string, Set<string>>();
  private readonly notices = new Map<string, number>();
  private hourly: number[] = [];
  private readonly records: PushRecord[] = [];
  private readonly queue: Item[] = [];
  private dropped = 0;
  private working = false;
  private stopped = false;
  private readonly abort = new AbortController();
  constructor(private readonly store: Store, private readonly manager: ThreadManager,
    private readonly options: { executable?: string; env?: NodeJS.ProcessEnv; timeoutMs?: number; retryMs?: number; now?: () => number } = {}) {
    this.off = manager.subscribe(e => this.onEvent(e));
  }
  private now() { return this.options.now?.() ?? Date.now(); }
  private settings(): PushSettings { return this.store.hubSetting('push', defaults); }
  async getSettings(): Promise<PushSettings> {
    const settings = this.settings();
    return settings.recipient ? settings : { ...settings, recipient: (await this.identity()).recipient };
  }
  putSettings(input: Record<string, unknown>): PushSettings {
    const next = { ...this.settings() };
    for (const key of ['enabled', 'waiting', 'failure', 'notice', 'scheduled'] as const) {
      if (input[key] !== undefined) {
        if (typeof input[key] !== 'boolean') throw new HttpError(400, `${key} must be a boolean`);
        next[key] = input[key];
      }
    }
    for (const key of ['recipient', 'baseUrl'] as const) {
      if (input[key] !== undefined) {
        if (typeof input[key] !== 'string' || input[key].length > 2000) throw new HttpError(400, `${key} must be a string`);
        next[key] = input[key].trim();
      }
    }
    if (next.recipient && !/^[\w-]+$/.test(next.recipient)) throw new HttpError(400, 'invalid recipient');
    if (next.baseUrl) {
      let url: URL;
      try { url = new URL(next.baseUrl); } catch { throw new HttpError(400, 'invalid Calli address'); }
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new HttpError(400, 'Calli address must be an HTTP(S) address without credentials, query or hash');
      next.baseUrl = url.href.replace(/\/$/, '');
    }
    this.store.saveHubSetting('push', next);
    return next;
  }
  private run(args: string[], input = ''): Promise<string> {
    const env = { ...(this.options.env ?? process.env) };
    const executable = this.options.executable ?? env.CALLI_LARK_CLI ?? 'lark-cli';
    for (const key of Object.keys(env)) if (key === 'CALLI_TOKEN' || key.startsWith('CALLI_')) delete env[key];
    return new Promise((resolve, reject) => {
      const child = execFile(executable, args, { env, timeout: this.options.timeoutMs ?? 15000, killSignal: 'SIGKILL', maxBuffer: 256 * 1024, signal: this.abort.signal }, (error, stdout) => {
        // Never expose CLI output or the execFile error message: either may contain credentials.
        if (error) reject(new Error((error as NodeJS.ErrnoException).code === 'ENOENT' ? 'cli_missing' : error.killed ? 'timeout' : 'cli_failed'));
        else resolve(stdout);
      });
      child.stdin?.on('error', () => {});
      child.stdin?.end(input);
    });
  }
  async identity(): Promise<Identity> {
    try {
      const value = JSON.parse(await this.run(['auth', 'status']));
      const bot = value.identities?.bot;
      const user = value.identities?.user;
      const botReady = bot?.available === true && bot?.verified !== false;
      return { cli: true, botReady, recipient: user?.available === true && typeof user.openId === 'string' ? user.openId : '', reason: botReady ? null : 'bot_not_ready' };
    } catch (e) {
      const reason = e instanceof SyntaxError ? 'invalid_status' : (e as Error).message;
      return { cli: reason !== 'cli_missing', botReady: false, recipient: '', reason };
    }
  }
  async status(summary = false) { return { ...await this.identity(), records: (summary ? this.records.slice(0, 1) : this.records).map(r => ({ ...r })), dropped: this.dropped }; }
  test(): PushRecord { return this.enqueue('test', null, randomUUID()); }

  /** A deterministic digest is a hub message, not an agent notice. Respect the push master switch. */
  brief(kind: BriefKind, brief: PersonalBrief, key: string): PushRecord | null {
    if (this.stopped || !this.settings().enabled) return null;
    return this.enqueue(kind, null, key, undefined, brief);
  }

  private onEvent(ev: StoredEvent) {
    if (!ev.threadId || this.stopped) return;
    const end = turnOutcome(ev);
    if (end || ev.type === 'turn.started') this.waits.delete(ev.threadId);
    if (ev.type === 'approval.resolved' || ev.type === 'input.resolved') {
      this.waits.get(ev.threadId)?.delete(String(ev.data.approvalId ?? ev.data.inputId));
      return;
    }
    if (!end && !['approval.request', 'input.request', 'agent.notice'].includes(ev.type)) return;
    const started = ev.turnId ? this.store.listEventsOfType(ev.threadId, 'turn.started').find(e => e.turnId === ev.turnId) : undefined;
    // A background question (askOnce) has nobody to answer it and the manager cancels it, so its
    // approvals, notices and failures are not news. Follow-ups on an archived thread still are.
    if (started?.data.background) return;
    // A proactive run surfaces on the desk, not on the phone: its notices and routine
    // completions never push. A failure still does — that is worth hearing about.
    const proactive = started?.data.proactive === true || ev.data.proactive === true;
    const origin = turnOrigin(started ?? { data: ev.data });
    if (origin.imported || origin.fromTerminal || origin.control || ev.data.imported || ev.data.fromTerminal || end?.imported || end?.fromTerminal) return;
    const settings = this.settings();
    if (!settings.enabled) return;
    let kind: PushKind | undefined;
    if (ev.type === 'approval.request' || ev.type === 'input.request') {
      const pending = this.waits.get(ev.threadId) ?? new Set<string>();
      const already = pending.size > 0;
      pending.add(String(ev.data.approvalId ?? ev.data.inputId));
      this.waits.set(ev.threadId, pending);
      if (already || !settings.waiting) return;
      // Approval requests are broadcast before the manager installs its pending approval.
      const attention = attentionOf({ waiting: true, waitingReason: ev.type === 'approval.request' ? 'approval' : ev.data.kind === 'plan' ? 'plan' : 'question', unread: false, status: 'running' });
      kind = attention!.kind as 'approval' | 'question' | 'plan';
    } else if (end?.outcome === 'error' && settings.failure && origin.initiator !== 'coordination') kind = 'failure';
    else if (!proactive && end?.outcome === 'ok' && started?.data.review !== true && ev.data.quiet !== true && settings.scheduled && origin.initiator === 'schedule') kind = 'scheduled';
    else if (ev.type === 'agent.notice' && settings.notice && !proactive) kind = ev.data.confirmation ? 'confirmation' : 'notice';
    if (kind) this.enqueue(kind, ev.threadId, `${ev.threadId}:${ev.seq}`, (kind === 'notice' || kind === 'confirmation') ? String(ev.data.text ?? '') : undefined);
  }
  private enqueue(kind: PushKind, threadId: string | null, key: string, notice?: string, brief?: PersonalBrief): PushRecord {
    const settings = this.settings(), now = this.now();
    const thread = threadId ? this.store.getThread(threadId) : null;
    const record: PushRecord = { key, at: new Date(now).toISOString(), kind, threadId, title: brief?.title ?? thread?.title ?? 'Calli', result: 'queued', reason: null };
    this.records.unshift(record); this.records.splice(20);
    for (const [id, at] of this.notices) if (now - at >= 60000) this.notices.delete(id);
    this.hourly = this.hourly.filter(t => now - t < 3600000);
    const last = threadId ? this.notices.get(threadId) : undefined;
    const reason = kind === 'notice' && last !== undefined && now - last < 60000 ? 'notice_rate_limit' : this.hourly.length >= 30 ? 'hourly_rate_limit' : null;
    if (reason) {
      record.result = 'dropped'; record.reason = reason; this.dropped++;
      console.warn(`[push] ${reason}`);
      return record;
    }
    this.hourly.push(now);
    if (kind === 'notice' && threadId) this.notices.set(threadId, now);
    const time = new Date(now).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    const body = brief ? [`[Calli] ${brief.title}`, brief.text, ...(settings.baseUrl ? [`打开：${settings.baseUrl}/#/desk`] : [])].join('\n') : [`[Calli] ${labels[kind]} · ${record.title}`, `${thread?.agent ?? 'Calli'} · ${time}`,
      ...(notice !== undefined ? [`来自 agent：${notice}`] : []), ...(settings.baseUrl && threadId ? [`打开：${settings.baseUrl}/#/thread/${encodeURIComponent(threadId)}`] : [])].join('\n');
    this.queue.push({ record, body, settings, ...(brief ? { briefText: `${brief.title}\n${brief.text}` } : {}) });
    void this.drain();
    return record;
  }
  private async drain() {
    if (this.working || this.stopped) return;
    this.working = true;
    try {
      while (this.queue.length && !this.stopped) {
        const { record, body, settings, briefText } = this.queue.shift()!;
        const identity = await this.identity();
        const recipient = settings.recipient || identity.recipient;
        if (!identity.botReady || !recipient) {
          record.result = 'failed'; record.reason = identity.reason ?? 'recipient_missing'; continue;
        }
        for (let attempt = 0; attempt < 3 && !this.stopped; attempt++) {
          try {
            await this.run(['im', '+messages-send', '--as', 'bot', '--user-id', recipient, '--markdown', '-', '--idempotency-key', record.key], body);
            if (briefText !== undefined && (record.kind === 'morning' || record.kind === 'evening')) {
              const last = this.store.hubSetting<Partial<Record<BriefKind, LastPersonalBrief>>>(LAST_BRIEF_KEY, {});
              this.store.saveHubSetting(LAST_BRIEF_KEY, { ...last, [record.kind]: { text: briefText, sentAt: new Date(this.now()).toISOString() } });
            }
            record.result = 'sent'; record.reason = null; break;
          } catch (e) {
            record.result = attempt === 2 ? 'failed' : 'queued'; record.reason = (e as Error).message;
            if (attempt < 2) await new Promise<void>(resolve => {
              const done = () => { clearTimeout(timer); this.abort.signal.removeEventListener('abort', done); resolve(); };
              const timer = setTimeout(done, (this.options.retryMs ?? 1000) * (attempt + 1));
              if (this.stopped) done(); else this.abort.signal.addEventListener('abort', done, { once: true });
            });
          }
        }
      }
    } finally { this.working = false; }
  }
  dispose() { this.stopped = true; this.off(); this.queue.length = 0; this.abort.abort(); }
}
