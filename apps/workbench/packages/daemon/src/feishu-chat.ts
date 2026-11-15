import { execFile, spawn, type ChildProcess } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { isAbsolute } from 'node:path';
import { larkProcess } from './channels/lark.js';
import { turnAnswer } from './coordination.js';
import { HttpError } from './errors.js';
import type { StoredEvent } from './events.js';
import { turnOrigin, turnOutcome } from './lifecycle.js';
import type { Store } from './store.js';
import type { ThreadManager } from './threads.js';

/** Talking to Calli from Feishu: one private chat with a bot of its own, one Calli thread per conversation. */
export interface FeishuSettings {
  enabled: boolean;
  agent: string;
  cwd: string;
  /** The only Feishu user (open_id) whose messages are heard. */
  owner: string;
  /** Also send back turns that were started on the web, in threads that began in Feishu. */
  echoWeb: boolean;
}
export type FeishuRealtime = 'off' | 'connecting' | 'live' | 'console-off' | 'busy-elsewhere' | 'unavailable' | 'error';
export interface FeishuRecord {
  key: string;
  at: string;
  direction: 'in' | 'out';
  threadId: string | null;
  title: string;
  result: 'received' | 'sent' | 'failed' | 'dropped';
  reason: string | null;
}
/** `create` makes the bot; `login` signs the person in once, which is how their own open_id becomes known. */
export interface FeishuSetup { state: 'idle' | 'waiting' | 'done' | 'failed'; url: string | null; reason: string | null; kind: 'create' | 'login' | null }

/** The bot gets its own lark-cli profile so it never shares an app (and its event connection) with another tool. */
export const FEISHU_PROFILE = 'calli';
const defaults: FeishuSettings = { enabled: false, agent: '', cwd: '', owner: '', echoWeb: false };
const MAX_REPLY = 20_000;
const MAX_INBOUND = 8_000;
const SETUP_TIMEOUT_MS = 10 * 60_000;
const object = (v: unknown): Record<string, any> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, any> : {};

interface Options { executable?: string; env?: NodeJS.ProcessEnv; timeoutMs?: number; retryMs?: number; setupTimeoutMs?: number; now?: () => number }
type Inbound = { messageId: string; chatId: string; text: string; sender: string; parent: string | null };

/** Pull the first message_id out of a send response, whichever envelope the CLI wrapped it in. */
export function sentMessageId(output: string): string | null {
  let value: unknown;
  try { value = JSON.parse(output); } catch { return null; }
  const walk = (v: unknown, depth: number): string | null => {
    if (depth > 4) return null;
    const o = object(v);
    if (typeof o.message_id === 'string' && o.message_id.startsWith('om_')) return o.message_id;
    for (const child of Object.values(o)) { const found = walk(child, depth + 1); if (found) return found; }
    return null;
  };
  return walk(value, 0);
}

/** One incoming event line to a message we may act on; null for anything else. */
export function inboundOf(line: unknown): Inbound | null {
  const row = object(line), event = object(row.event);
  const m = Object.keys(event).length ? { ...object(event.message), sender_id: object(event.sender).sender_id?.open_id, sender_type: object(event.sender).sender_type } : row;
  const messageId = m.message_id ?? m.id;
  const sender = typeof m.sender_id === 'string' ? m.sender_id : object(m.sender_id).open_id;
  if (typeof messageId !== 'string' || !/^om_[\w-]+$/.test(messageId) || typeof m.chat_id !== 'string' || typeof sender !== 'string') return null;
  if (m.chat_type !== 'p2p' || (m.sender_type !== undefined && m.sender_type !== 'user')) return null;
  const parent = [m.reply_to, m.parent_id, m.root_id].find(v => typeof v === 'string' && v.startsWith('om_')) ?? null;
  // Only words are heard. The CLI renders content as text; a raw envelope is unwrapped, anything else refused.
  const kind = m.message_type ?? m.msg_type;
  if (kind !== undefined && kind !== 'text' && kind !== 'post') return { messageId, chatId: m.chat_id, sender, parent, text: '' };
  let text = typeof m.content === 'string' ? m.content : '';
  // Only a raw envelope carries its words inside JSON; rendered content is the person's own text, braces and all.
  if (Object.keys(event).length && kind === 'text' && text.trimStart().startsWith('{')) {
    try { const inner = JSON.parse(text); text = typeof inner.text === 'string' ? inner.text : text; } catch { /* already plain text */ }
  }
  return { messageId, chatId: m.chat_id, sender, parent, text };
}

export class FeishuChat {
  private readonly off: () => void;
  private readonly records: FeishuRecord[] = [];
  /** Feishu messages not yet picked up by a turn, per thread (a busy thread queues them). */
  private readonly awaiting = new Map<string, Array<{ id: string; text: string }>>();
  /** Turn -> the Feishu message it answers, bound when the turn starts; a turn started on the web never matches. */
  private readonly bound = new Map<string, string>();
  private readonly told = new Set<string>();
  private dropped = 0;
  private realtime: FeishuRealtime = 'off';
  private pendingOwner: string | null = null;
  private readonly hinted = new Set<string>();
  private setup: FeishuSetup = { state: 'idle', url: null, reason: null, kind: null };
  private setupChild: ChildProcess | null = null;
  private consumer: ChildProcess | null = null;
  private consumerGeneration = 0;
  private retryTimer: NodeJS.Timeout | null = null;
  private attempts = 0;
  private chain: Promise<unknown> = Promise.resolve();
  private stopped = false;
  private readonly abort = new AbortController();
  private signature = '';

  constructor(private readonly store: Store, private readonly manager: ThreadManager, private readonly options: Options = {}) {
    this.off = manager.subscribe(e => this.onEvent(e));
  }

  // --- settings ---

  getSettings(): FeishuSettings { return { ...defaults, ...this.store.hubSetting<Partial<FeishuSettings>>('feishu', {}) }; }
  putSettings(input: Record<string, unknown>): FeishuSettings {
    const next = this.getSettings();
    for (const key of ['enabled', 'echoWeb'] as const) {
      if (input[key] !== undefined) {
        if (typeof input[key] !== 'boolean') throw new HttpError(400, `${key} must be a boolean`);
        next[key] = input[key];
      }
    }
    for (const key of ['agent', 'cwd', 'owner'] as const) {
      if (input[key] !== undefined) {
        if (typeof input[key] !== 'string' || input[key].length > 2000) throw new HttpError(400, `${key} must be a string`);
        next[key] = input[key].trim();
      }
    }
    if (next.owner && !/^ou_[\w-]+$/.test(next.owner)) throw new HttpError(400, 'invalid owner');
    if (next.agent && !this.manager.hasAgent(next.agent)) throw new HttpError(400, `unknown agent: ${next.agent}`);
    if (next.cwd && !isAbsolute(next.cwd)) throw new HttpError(400, 'cwd must be an absolute path');
    if (next.enabled && (!next.agent || !next.cwd)) throw new HttpError(400, 'choose an agent and a directory first');
    if (next.owner && next.owner === this.pendingOwner) this.pendingOwner = null;
    this.store.saveHubSetting('feishu', next);
    void this.configure();
    return next;
  }

  // --- lark-cli ---

  private process() { return larkProcess({ executable: this.options.executable, env: this.options.env }); }
  /** Never expose CLI output or the execFile error message: either may contain credentials. */
  private run(args: string[], input = ''): Promise<string> {
    const { executable, env } = this.process();
    return new Promise((resolve, reject) => {
      const child = execFile(executable, ['--profile', FEISHU_PROFILE, ...args], { env, timeout: this.options.timeoutMs ?? 15000, killSignal: 'SIGKILL', maxBuffer: 1024 * 1024, signal: this.abort.signal }, (error, stdout) => {
        if (error) reject(new Error((error as NodeJS.ErrnoException).code === 'ENOENT' ? 'cli_missing' : error.killed ? 'timeout' : 'cli_failed'));
        else resolve(stdout);
      });
      child.stdin?.on('error', () => {});
      child.stdin?.end(input);
    });
  }
  private async identity(): Promise<{ cli: boolean; configured: boolean; botReady: boolean; user: string; reason: string | null }> {
    try {
      const value = JSON.parse(await this.run(['auth', 'status']));
      const configured = typeof value.appId === 'string' && value.appId !== '';
      const botReady = configured && value.identities?.bot?.available === true && value.identities?.bot?.verified !== false;
      const user = value.identities?.user;
      return { cli: true, configured, botReady, user: user?.available === true && typeof user.openId === 'string' ? user.openId : '', reason: !configured ? null : botReady ? null : 'bot_not_ready' };
    } catch (e) {
      const reason = e instanceof SyntaxError ? 'invalid_status' : (e as Error).message;
      // A profile that does not exist yet answers with an error: the CLI is there, the bot is not.
      return { cli: reason !== 'cli_missing', configured: false, botReady: false, user: '', reason: reason === 'cli_failed' ? null : reason };
    }
  }

  async status(summary = false) {
    const id = await this.identity();
    return {
      cli: id.cli, configured: id.configured, botReady: id.botReady, reason: id.reason,
      realtime: this.realtime, setup: { ...this.setup }, pendingOwner: this.pendingOwner,
      records: (summary ? this.records.slice(0, 1) : this.records).map(r => ({ ...r })), dropped: this.dropped,
    };
  }

  // --- creating the bot ---

  /** `lark-cli config init --new` blocks until someone approves it in Feishu; its first URL is the way in. */
  async startSetup() {
    if (this.setupChild) return this.status();
    const id = await this.identity();
    if (!id.cli) throw new HttpError(409, 'lark-cli is not installed');
    if (id.configured) throw new HttpError(409, 'the bot already exists');
    return this.runSetup('create');
  }
  /** Signing in once tells us who the person is; their open_id then needs no typing. */
  async startLogin() {
    if (this.setupChild) return this.status();
    const id = await this.identity();
    if (!id.cli) throw new HttpError(409, 'lark-cli is not installed');
    if (!id.configured) throw new HttpError(409, 'create the bot first');
    return this.runSetup('login');
  }
  private async runSetup(kind: 'create' | 'login') {
    const { executable, env } = this.process();
    this.setup = { state: 'waiting', url: null, reason: null, kind };
    const args = kind === 'create' ? ['config', 'init', '--new', '--name', FEISHU_PROFILE] : ['--profile', FEISHU_PROFILE, 'auth', 'login', '--domain', 'im'];
    const child = spawn(executable, args, { env, stdio: ['ignore', 'pipe', 'pipe'] });
    this.setupChild = child;
    let text = '';
    const take = (chunk: Buffer) => {
      text = (text + String(chunk)).slice(-16_000);
      if (!this.setup.url) {
        const url = /https:\/\/[^\s"'<>　]+/.exec(text)?.[0].replace(/[).,，。]+$/, '');
        if (url) this.setup = { ...this.setup, url };
      }
    };
    child.stdout!.on('data', take); child.stderr!.on('data', take);
    const timer = setTimeout(() => child.kill('SIGTERM'), this.options.setupTimeoutMs ?? SETUP_TIMEOUT_MS);
    timer.unref();
    child.on('error', e => { this.setupChild = null; clearTimeout(timer); this.setup = { state: 'failed', url: null, reason: (e as NodeJS.ErrnoException).code === 'ENOENT' ? 'cli_missing' : 'cli_failed', kind }; });
    child.on('close', code => {
      clearTimeout(timer);
      const cancelled = this.setupChild !== child;
      this.setupChild = null;
      if (cancelled || this.stopped) return;
      if (code !== 0) { this.setup = { state: 'failed', url: null, reason: 'cli_failed', kind }; return; }
      void this.identity().then(after => {
        if (!after.configured || kind === 'login' && !after.user) { this.setup = { state: 'failed', url: null, reason: 'cli_failed', kind }; return; }
        // The person who approved is the one the bot should listen to; signing in again is a deliberate change of owner.
        const settings = this.getSettings();
        if (after.user && (!settings.owner || kind === 'login')) this.store.saveHubSetting('feishu', { ...settings, owner: after.user });
        this.setup = { state: 'done', url: null, reason: null, kind };
        void this.configure();
      });
    });
    // The URL shows up a moment after launch; give the first status something to show.
    for (let i = 0; i < 20 && !this.setup.url && this.setupChild === child; i++) await new Promise(r => setTimeout(r, 100));
    return this.status();
  }
  async cancelSetup() {
    const child = this.setupChild;
    this.setupChild = null;
    child?.kill('SIGTERM');
    if (this.setup.state === 'waiting') this.setup = { state: 'idle', url: null, reason: null, kind: null };
    return this.status();
  }

  // --- receiving ---

  /** Start or stop the connection to match the settings; safe to call after any change. */
  async configure(): Promise<void> {
    if (this.stopped) return;
    const settings = this.getSettings();
    const signature = JSON.stringify([settings.enabled]);
    if (!settings.enabled) { this.stopConsumer(); this.realtime = 'off'; this.signature = ''; return; }
    if (this.consumer || this.retryTimer || signature === this.signature && this.realtime === 'unavailable') return;
    this.signature = signature;
    this.connect();
  }
  private setRealtime(next: FeishuRealtime) { if (!this.stopped) this.realtime = next; }
  private connect(): void {
    if (this.stopped || !this.getSettings().enabled) return;
    this.setRealtime('connecting');
    this.launch(true);
  }
  private retry(): void {
    if (this.stopped || this.retryTimer || !this.getSettings().enabled) return;
    const slow = ['busy-elsewhere', 'console-off', 'unavailable'].includes(this.realtime);
    const delay = slow ? 300_000 : Math.min(300_000, 5000 * 2 ** Math.min(this.attempts++, 6));
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.connect(); }, this.options.retryMs ?? delay);
    this.retryTimer.unref();
  }
  private stopConsumer(): void {
    this.consumerGeneration++;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const child = this.consumer;
    this.consumer = null;
    if (child) { child.kill('SIGTERM'); const t = setTimeout(() => child.kill('SIGKILL'), 2000); t.unref(); child.once('close', () => clearTimeout(t)); }
  }
  private launch(preflight: boolean): void {
    const { executable, env } = this.process();
    const generation = ++this.consumerGeneration;
    const child = spawn(executable, ['--profile', FEISHU_PROFILE, 'event', 'consume', 'im.message.receive_v1', '--as', 'bot', ...(preflight ? ['--dry-run'] : [])],
      { env, stdio: ['pipe', 'pipe', 'pipe'], ...(preflight ? { timeout: 20_000 } : {}) });
    this.consumer = child;
    const current = () => generation === this.consumerGeneration && !this.stopped;
    let output = '', diagnostics = '';
    const diagnose = (text: string) => {
      if (!current()) return;
      if (/another event bus is already connected/i.test(text)) { this.setRealtime('busy-elsewhere'); child.kill('SIGTERM'); }
      else if (/console_event_published[^\n]*(?:blocked|missing|not.ok)|event types not subscribed|subscribe.*event.*console/i.test(text)) this.setRealtime('console-off');
      else if (/not logged in|credentials.*(?:missing|unavailable)|login required/i.test(text)) this.setRealtime('unavailable');
      else if (!preflight && /\bready\b/i.test(text) && this.realtime === 'connecting') { this.setRealtime('live'); this.attempts = 0; }
    };
    child.stderr!.on('data', chunk => { diagnostics = (diagnostics + String(chunk)).slice(-16_000); diagnose(diagnostics); });
    if (preflight) child.stdout!.on('data', chunk => { output = (output + String(chunk)).slice(-128_000); diagnose(output); });
    else {
      createInterface({ input: child.stdout!, crlfDelay: Infinity }).on('line', line => {
        if (!current() || !line.trimStart().startsWith('{')) return;
        let value: unknown;
        try { value = JSON.parse(line); } catch { return; }
        if (this.realtime === 'connecting') this.setRealtime('live');
        this.attempts = 0;
        const message = inboundOf(value);
        if (message) this.chain = this.chain.catch(() => undefined).then(() => this.receive(message)).catch(() => undefined);
      });
    }
    child.on('error', error => { if (current()) this.setRealtime((error as NodeJS.ErrnoException).code === 'ENOENT' ? 'unavailable' : 'error'); });
    child.on('close', code => {
      if (!current()) return;
      this.consumer = null;
      if (preflight && output.trimStart().startsWith('{')) {
        try {
          const decision = object(object(JSON.parse(output).data).decision);
          const conditions: Array<Record<string, unknown>> = Array.isArray(decision.preconditions) ? decision.preconditions : [];
          if (conditions.some(c => c.name === 'console_event_published' && c.status !== 'ok')) this.setRealtime('console-off');
          else if (conditions.some(c => c.name === 'credentials_available' && c.status !== 'ok')) this.setRealtime('unavailable');
          else if (code === 0 && decision.status === 'ready') { this.launch(false); return; }
          else this.setRealtime('error');
        } catch { this.setRealtime('error'); }
      } else if (!['busy-elsewhere', 'console-off', 'unavailable'].includes(this.realtime)) this.setRealtime('error');
      this.retry();
    });
  }

  private record(key: string, direction: FeishuRecord['direction'], threadId: string | null, title: string, result: FeishuRecord['result'], reason: string | null = null): FeishuRecord {
    const record: FeishuRecord = { key, at: new Date(this.options.now?.() ?? Date.now()).toISOString(), direction, threadId, title, result, reason };
    this.records.unshift(record); this.records.splice(20);
    if (result === 'dropped') this.dropped++;
    return record;
  }

  /** The text of a message becomes a turn as it is: nothing about Feishu is added to what the agent reads. */
  private async receive(message: Inbound): Promise<void> {
    if (this.stopped) return;
    const settings = this.getSettings();
    const text = message.text;
    if (!this.store.feishuSeen(message.messageId)) return;
    if (!settings.owner) {
      this.pendingOwner = message.sender;
      this.record(message.messageId, 'in', null, '', 'dropped', 'owner_missing');
      // Say once why nothing happened, so the person knows where to go next.
      if (!this.hinted.has(message.sender)) {
        this.hinted.add(message.sender);
        await this.reply(message.messageId, '[Calli] 还没有绑定主人。请回到 Calli 的「飞书」设置页，点「绑定这个账号」，再发一次。', `${message.messageId}:hint`, null, '');
      }
      return;
    }
    if (message.sender !== settings.owner) { this.record(message.messageId, 'in', null, '', 'dropped', 'not_owner'); return; }
    // The words reach the agent exactly as written, so one too long is refused rather than cut.
    if (!text.trim() || text.length > MAX_INBOUND) { this.record(message.messageId, 'in', null, '', 'dropped', 'unsupported'); return; }
    let threadId = message.parent ? this.store.feishuThreadOf(message.parent) : null;
    if (threadId && !this.store.getThread(threadId)) threadId = null;
    let title = text.trim().replace(/\s+/g, ' ').slice(0, 40);
    try {
      if (!threadId) {
        if (!settings.agent || !settings.cwd) throw new HttpError(400, 'owner_missing');
        const created = await this.manager.createThread({ agent: settings.agent, cwd: settings.cwd, title });
        threadId = created.id;
      } else title = this.store.getThread(threadId)?.title ?? title;
      this.store.feishuLink(message.messageId, message.chatId, threadId);
      this.record(message.messageId, 'in', threadId, title, 'received');
      void this.run(['im', 'reactions', 'create', '--params', JSON.stringify({ message_id: message.messageId }), '--data', JSON.stringify({ reaction_type: { emoji_type: 'OnIt' } }), '--as', 'bot']).catch(() => undefined);
      const list = this.awaiting.get(threadId) ?? [];
      list.push({ id: message.messageId, text: text.trim() });
      this.awaiting.set(threadId, list);
      await this.manager.startTurn(threadId, { text, queue: true });
    } catch (e) {
      const reason = e instanceof HttpError ? e.message : 'failed';
      this.record(message.messageId, 'in', threadId, title, 'failed', reason);
      if (threadId) this.unawait(threadId, message.messageId);
      await this.reply(message.messageId, `[Calli] 没能开始：${reason}`, `${message.messageId}:error`, threadId, title);
    }
  }

  // --- answering ---

  private baseUrl(): string { return this.store.hubSetting<{ baseUrl?: string }>('push', {}).baseUrl ?? ''; }
  private link(threadId: string): string { const base = this.baseUrl(); return base ? `\n\n打开：${base}/#/thread/${encodeURIComponent(threadId)}` : ''; }

  private unawait(threadId: string, id: string): void {
    const rest = (this.awaiting.get(threadId) ?? []).filter(m => m.id !== id);
    if (rest.length) this.awaiting.set(threadId, rest); else this.awaiting.delete(threadId);
  }

  private onEvent(ev: StoredEvent): void {
    if (!ev.threadId || this.stopped) return;
    if (ev.type === 'turn.started' && ev.turnId) {
      // The words that started the turn say which Feishu message it is for.
      const asked = String(ev.data.displayText ?? ev.data.text ?? '').trim();
      const match = this.awaiting.get(ev.threadId)?.find(m => m.text === asked);
      if (match) { this.bound.set(ev.turnId, match.id); this.unawait(ev.threadId, match.id); }
      return;
    }
    if (!this.getSettings().enabled) return;
    const end = turnOutcome(ev);
    const waiting = ev.type === 'approval.request' || ev.type === 'input.request';
    if (!end && !waiting) return;
    const threadId = ev.threadId;
    const started = ev.turnId ? this.store.listEventsOfType(threadId, 'turn.started').find(e => e.turnId === ev.turnId) : undefined;
    if (started?.data.background) return;
    const origin = turnOrigin(started ?? { data: ev.data });
    if (origin.imported || origin.fromTerminal || origin.control || ev.data.imported || ev.data.fromTerminal || end?.imported || end?.fromTerminal) return;
    let target = ev.turnId ? this.bound.get(ev.turnId) : undefined;
    // A turn started on the web is only echoed if asked for, and only in a thread that began here.
    if (!target && this.getSettings().echoWeb && this.store.feishuLastOf(threadId) && !ev.data.quiet) target = this.store.feishuLastOf(threadId) ?? undefined;
    if (!target) return;
    const title = this.store.getThread(threadId)?.title ?? '';
    if (waiting) {
      if (this.told.has(`${threadId}:${ev.turnId}`)) return;
      this.told.add(`${threadId}:${ev.turnId}`);
      if (this.told.size > 500) this.told.delete(this.told.values().next().value!);
      const kind = ev.type === 'approval.request' ? '等你批准' : '等你回答';
      void this.reply(target, `[Calli] ${kind}${this.link(threadId) || '\n请回到 Calli 处理。'}`, `${threadId}:${ev.seq}`, threadId, title);
      return;
    }
    if (!end) return;
    if (ev.turnId) this.bound.delete(ev.turnId);
    let body: string;
    if (end.outcome === 'ok') {
      // Whole: the reply keeps the start of a long answer and says it was cut.
      const answer = turnAnswer(this.store.listEvents(threadId, 0, -1), ev.turnId ?? null, Number.MAX_SAFE_INTEGER);
      body = (answer || '（没有文字回答）').slice(0, MAX_REPLY);
      if (answer.length > MAX_REPLY) body += `\n\n…（已截断）${this.link(threadId)}`;
    } else if (end.outcome === 'cancelled') body = '[Calli] 已停止。';
    else body = `[Calli] 这一轮没跑完${end.message ? `：${String(end.message).slice(0, 500)}` : ''}${this.link(threadId)}`;
    void this.reply(target, body, `${threadId}:${ev.seq}`, threadId, title);
  }

  /** One reply, retried twice. The key keeps a retry from posting the same answer twice. */
  private async reply(to: string, body: string, key: string, threadId: string | null, title: string): Promise<void> {
    const record = this.record(key, 'out', threadId, title, 'failed');
    // A stable key per answer: a retry, or the same event seen twice, cannot post it twice.
    const idempotency = createHash('sha1').update(key).digest('hex').slice(0, 40);
    for (let attempt = 0; attempt < 3 && !this.stopped; attempt++) {
      try {
        const out = await this.run(['im', '+messages-reply', '--message-id', to, '--markdown', '-', '--as', 'bot', '--idempotency-key', idempotency], body);
        const sent = sentMessageId(out);
        if (sent && threadId) this.store.feishuLink(sent, null, threadId);
        record.result = 'sent'; record.reason = null;
        return;
      } catch (e) {
        record.reason = (e as Error).message;
        if (attempt < 2) await new Promise(r => { const t = setTimeout(r, (this.options.retryMs ?? 1000) * (attempt + 1)); t.unref(); });
      }
    }
  }

  /** Send the owner a line, so a person can see the path works before relying on it. */
  async test(): Promise<FeishuRecord> {
    const owner = this.getSettings().owner;
    const record = this.record(randomUUID(), 'out', null, 'Calli', 'failed', owner ? null : 'owner_missing');
    if (!owner) return record;
    try {
      await this.run(['im', '+messages-send', '--as', 'bot', '--user-id', owner, '--markdown', '-', '--idempotency-key', record.key.slice(0, 50)], '[Calli] 飞书对话已连通。直接发消息给我即可开始一个新线程。');
      record.result = 'sent';
    } catch (e) { record.reason = (e as Error).message; }
    return record;
  }

  dispose(): void {
    this.stopped = true;
    this.off();
    this.stopConsumer();
    this.setupChild?.kill('SIGTERM'); this.setupChild = null;
    this.abort.abort();
  }
}
