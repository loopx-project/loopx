import { randomUUID } from 'node:crypto';
import { lstatSync, mkdirSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { homeDirectory } from './config.js';
import { fileURLToPath } from 'node:url';
import type { AgentAdapter } from './adapters/types.js';
import type { LarkSignal } from './channels/lark.js';
import { isWithinWorkdirs } from './workdirs.js';
import { HttpError } from './errors.js';
import { PROFILE_TEMPLATE, PersonalFiles } from './personal-files.js';
import { PersonalExplore } from './personal-explore.js';
import { PersonalPlanService, horizonDeadline } from './personal-plan.js';
import { PersonalPrep } from './personal-prep.js';
import { PersonalReview } from './personal-review.js';
import { PersonalRouter } from './personal-router.js';
import { ProactiveService } from './proactive.js';
import type { SignalRecord, Store, TodoImportance, TodoLane, TodoNote, TodoPatch, TodoRecord, TodoSource, TodoSubscription, TodoThread, TodoProposal } from './store.js';
import { TODO_IMPORTANCES, TODO_LANES, TODO_SOURCE_TYPES, todoThread, urgencyOf } from './store.js';
import type { InjectionGate } from './injections.js';
import type { ThreadManager } from './threads.js';
import { SummaryService } from './summary.js';

// Keep the default location in one place so its final component can change independently.
// `~/.loochat/personal`, or `~/.limi/personal` where only the directory from before the rename exists.
const personalDirectory = () => [basename(homeDirectory(realpathSync(homedir()))), 'personal'];
export function defaultPersonalDirectory(): string {
  let path = realpathSync(homedir());
  for (const part of personalDirectory()) {
    path = join(path, part);
    try { mkdirSync(path, { mode: 0o700 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw new HttpError(400, 'cannot create the default personal directory'); }
    const stat = lstatSync(path);
    if (stat.isSymbolicLink() || !stat.isDirectory() || realpathSync(path) !== path) throw new HttpError(400, 'default personal directory must be a real directory, not a symbolic link');
  }
  return path;
}
export interface DispatchTask {
  agent: string;
  cwd: string;
  task: string;
  title: string;
  matterId: string;
  model?: string;
}
export interface PersonalSettings { directory: string | null; agent: string | null; model: string | null }
/** Reject only unmistakable credential formats; this is not a general secret detector. */
export function personalText(value: unknown, limit: number, empty = false): string {
  if (typeof value !== 'string' || (!empty && !value.trim()) || [...value].length > limit) throw new HttpError(400, `text must contain ${empty ? '0' : '1'}–${limit} characters`);
  if (/-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})/.test(value)) throw new HttpError(400, 'text contains a credential; remove secrets before saving');
  return value;
}
/** Archive keys are opaque JSON data, never document IDs or URL paths. */
export function signalArchiveKey(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 240 || /[\u0000-\u001f\u007f]/.test(value)) throw new HttpError(400, 'invalid signal key');
  return value;
}
const time = (value: unknown): string | null => {
  if (value === null || value === '') return null;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) || !Number.isFinite(Date.parse(value))) throw new HttpError(400, 'time must be ISO 8601');
  return new Date(value).toISOString();
};

/** Delegation starts a new work thread; linked threads are records, not a handover chain. */
function delegationPrompt(todo: TodoRecord, note: string, personal: boolean): string {
  const clarify = '只有目标笼统、看不出要交付什么时，这一轮才只做澄清：在回复中用 1～3 个具体问题问人，每个问题给出你建议的默认选项，不要自己猜着开工。';
  const workflow = personal
    ? `先用 loochat todo show ${todo.id} 读取事项与最新 rev。${clarify}
澄清时用 loochat todo update ${todo.id} --rev N --state waiting --waiting-on "等你说明目标" --note-file FILE，把事项设为等待，并写一版接续说明：用「## 欠谁什么」写已知部分，「## 定了什么」「## 做到哪」只写已知事实，「## 下一步」写「等回答后……」。
再用 loochat notify --text-file FILE 发一句话提醒人来回答，然后结束本轮。人回复后再按回答推进。
目标清楚时直接推进：有接续说明就按说明的「下一步」推进；没有说明也直接推进，并在结束前写第一版接续说明，已有说明则更新。需要人确认完成时用 loochat todo propose-done。`
    : `${clarify}
本对话没有事项管理凭证：需要澄清时只在回复中提问并结束本轮，人回复后再按回答推进。
目标清楚时直接推进：有接续说明就按说明的「下一步」推进；没有说明也直接推进，并在结束前于回复中写进展和第一版接续说明，已有说明则更新。需要人确认完成时向人提议确认。`;
  return `这是一件新交给你的事，不是交接；这个对话就是它的工作对话，没有更早的对话需要读。
由 LoopX 交给你的事项。
事项：${todo.text}
ID：${todo.id}；状态：${todo.state}；归属：${todo.owner}
接续说明：
${note.trim() ? note : '尚未写入。'}

${workflow}
关联对话只是记录；不要用 handover/history 查关联对话，也不要翻本地会话文件找上下文。
事项涉及不可逆操作（提交、付款、发消息、删除、推送代码）时，先停下来问人，得到明确同意后再操作。例如回邮件，可先起草给人看，发送前再问。`;
}

export class PersonalService {
  private readonly credentials = new Map<string, string>();
  /** The plan page's file mirror under the personal directory; the database stays the source of truth. */
  private readonly files = new PersonalFiles(() => this.directory());
  socket = '';
  readonly plan: PersonalPlanService;
  readonly prep: PersonalPrep;
  readonly summary: SummaryService;
  readonly review: PersonalReview;
  readonly router: PersonalRouter;
  readonly proactive: ProactiveService;
  /** The one-shot onboarding explore; mutable so tests can substitute a fixture-backed instance. */
  explore: PersonalExplore;
  constructor(readonly store: Store, readonly manager: ThreadManager, private readonly adapters: Map<string, AgentAdapter>) {
    this.plan = new PersonalPlanService(manager);
    this.prep = new PersonalPrep(this);
    this.summary = new SummaryService(store, manager, this, adapters);
    this.review = new PersonalReview(store);
    this.router = new PersonalRouter(store, manager, this, adapters);
    this.proactive = new ProactiveService(store, manager, this, adapters);
    this.explore = new PersonalExplore(store, manager, this);
  }
  private storedSettings(): PersonalSettings { return this.store.hubSetting('personal', { directory: null, agent: null, model: null }); }
  directory(): string {
    const configured = this.storedSettings().directory;
    if (!configured) return defaultPersonalDirectory();
    const defaultPath = join(realpathSync(homedir()), ...personalDirectory());
    // Older settings may explicitly name the default or one of its children.
    if (configured === defaultPath || configured.startsWith(defaultPath + sep)) defaultPersonalDirectory();
    return configured;
  }
  settings(): PersonalSettings & { directory: string } { return { ...this.storedSettings(), directory: this.directory() }; }
  async resolvedSettings(): Promise<PersonalSettings & { directory: string }> {
    const settings = this.settings();
    if (!settings.agent) {
      const agents = (await this.manager.listAgents()).filter(agent => agent.available && !agent.hidden);
      settings.agent = (agents.find(agent => this.adapters.get(agent.id)?.id === 'pi') ?? agents[0])?.id ?? null;
      settings.model = null;
    }
    return settings;
  }
  async settingsView() {
    const settings = await this.resolvedSettings(), home = realpathSync(homedir());
    const display = (path: string | null) => path === home ? '~' : path?.startsWith(home + sep) ? '~' + path.slice(home.length) : path;
    const defaultDirectory = defaultPersonalDirectory();
    return { ...settings, directoryDisplay: display(settings.directory), directoryIsDefault: settings.directory === defaultDirectory,
      defaultDirectory, defaultDirectoryDisplay: display(defaultDirectory), routerThreadId: this.router.status().threadId };
  }
  /**
   * Switches her agent from the conversation itself. With a conversation under way the new agent takes
   * it over through the ordinary handover (a summary, or the session itself for another account of the
   * same agent), so it goes on knowing what was said; without one only the setting changes.
   */
  async switchAgent(input: Record<string, unknown>): Promise<{ threadId: string | null; settings: PersonalSettings }> {
    const agent = input.agent, model = input.model ?? undefined;
    if (typeof agent !== 'string' || !this.adapters.has(agent)) throw new HttpError(400, 'unknown personal agent');
    if (model !== undefined && (typeof model !== 'string' || !model || model.length > 200)) throw new HttpError(400, 'model must be a string');
    const current = this.router.status().threadId;
    const thread = current ? this.store.getThread(current) : null;
    // A handover branches off a busy thread rather than waiting, so refuse first: a switch never
    // leaves her answering in one conversation while the new agent starts another.
    const busy = () => { const view = thread && this.manager.threadView(thread.id); return Boolean(view && (view.runningSince || view.queued.length)); };
    if (thread && thread.agent !== agent && busy()) throw new HttpError(409, 'the assistant is answering; switch after this turn');
    const info = (await this.manager.listAgents()).find(item => item.id === agent);
    if (!info?.available) throw new HttpError(409, 'agent is not available');
    if (thread && thread.agent !== agent) {
      if (busy()) throw new HttpError(409, 'the assistant is answering; switch after this turn');
      const successor = await this.manager.takeOver(thread.id, agent, model, undefined);
      this.router.adopt(successor.id);
      // The setting matches the thread exactly, or the Router would replace it on the next message.
      this.putSettings({ agent, model: successor.model ?? null });
      return { threadId: successor.id, settings: this.settings() };
    }
    this.putSettings({ agent, model: model ?? null });
    return { threadId: current, settings: this.settings() };
  }

  putSettings(input: Record<string, unknown>): PersonalSettings {
    // Compare the stored value, not the resolved directory: a broken path must stay resettable.
    const previousDirectory = input.directory !== undefined ? this.storedSettings().directory : undefined;
    const next = { ...this.storedSettings() };
    if (input.directory !== undefined) {
      if (input.directory === null || input.directory === '') next.directory = null;
      else {
        if (typeof input.directory !== 'string') throw new HttpError(400, 'directory must be a path');
        try { next.directory = realpathSync(input.directory); if (!statSync(next.directory).isDirectory()) throw new Error(); }
        catch { throw new HttpError(400, 'personal directory does not exist'); }
        const defaultDirectory = defaultPersonalDirectory();
        if (![...this.manager.dirs.roots(), defaultDirectory].some(root => this.within(next.directory!, root))) throw new HttpError(400, 'personal directory must be an allowed workdir or the default personal directory');
        if (next.directory === defaultDirectory) next.directory = null;
      }
    }
    if (input.agent !== undefined) {
      if (input.agent !== null && (typeof input.agent !== 'string' || !this.adapters.has(input.agent))) throw new HttpError(400, 'unknown personal agent');
      next.agent = input.agent as string | null;
    }
    if (input.model !== undefined) {
      if (input.model !== null && (typeof input.model !== 'string' || input.model.length > 200)) throw new HttpError(400, 'model must be a string');
      next.model = input.model ? String(input.model) : null;
    }
    this.store.saveHubSetting('personal', next);
    if (previousDirectory !== undefined && previousDirectory !== next.directory) this.manager.lark?.invalidateMaterials?.();
    return next;
  }
  async delegate(id: string, input: { cwd?: unknown } = {}) {
    const todo = this.store.getTodo(id);
    if (!todo) throw new HttpError(404, 'todo not found');
    if (todo.done || todo.proposed) throw new HttpError(409, 'accept an open plan before delegating');
    const settings = await this.resolvedSettings(), adapter = settings.agent ? this.adapters.get(settings.agent) : undefined;
    if (!settings.directory || !adapter) throw new HttpError(409, 'personal agent is not configured');
    if (input.cwd !== undefined && (typeof input.cwd !== 'string' || !input.cwd.trim())) throw new HttpError(400, 'cwd must be a nonempty directory');
    const cwd = typeof input.cwd === 'string' ? input.cwd : settings.directory;
    if (!isWithinWorkdirs(cwd, this.manager.allowedWorkdirs)) throw new HttpError(403, 'cwd is not in allowed workdirs');
    const active = this.store.todoThreadIds(id).map(t => this.store.getThread(t)).find(t => t?.status === 'running');
    if (active) return this.manager.threadView(active.id);
    const thread = await this.manager.createThread({ agent: settings.agent!, cwd, title: todo.text,
      permissionMode: adapter.capabilities.defaultPermissionMode, ...(settings.model ? { model: settings.model } : {}) });
    try {
      const note = this.store.todoNote(id);
      const text = delegationPrompt(todo, note.text, this.within(thread.cwd, settings.directory));
      await this.manager.startTurn(thread.id, { text }, undefined, false, undefined, () => {
        this.linkWorkThread(id, thread.id, todo.rev);
      });
      this.manager.broadcastTodo();
      return this.manager.threadView(thread.id);
    } catch (error) {
      if (!this.store.listEventsOfType(thread.id, 'turn.started').length) await this.manager.deleteThread(thread.id);
      throw error;
    }
  }
  async validateDispatch(input: Record<string, unknown>): Promise<DispatchTask> {
    const agent = personalText(input.agent, 200).trim();
    if (!this.adapters.has(agent)) throw new HttpError(400, 'unknown agent');
    if (!(await this.manager.listAgents()).some(item => item.id === agent && item.available)) throw new HttpError(409, 'agent is unavailable');
    if (typeof input.cwd !== 'string' || !isAbsolute(input.cwd)) throw new HttpError(400, 'cwd must be an absolute directory');
    let cwd: string;
    try { cwd = realpathSync(input.cwd); if (!statSync(cwd).isDirectory()) throw new Error(); }
    catch { throw new HttpError(400, 'cwd does not exist'); }
    if (!isWithinWorkdirs(cwd, this.manager.allowedWorkdirs)) throw new HttpError(403, 'cwd is not in allowed workdirs');
    const task = personalText(input.task, 32_000).trim();
    const title = input.title === undefined ? [...task.split('\n')[0]].slice(0, 120).join('') : personalText(input.title, 200).trim();
    const matterId = input.matterId == null || input.matterId === '' ? '' : personalText(input.matterId, 200).trim();
    if (matterId) {
      const todo = this.store.getTodo(matterId);
      if (!todo) throw new HttpError(404, 'todo not found');
      if (todo.done || todo.state === 'done' || todo.proposed) throw new HttpError(409, 'accept an open plan before dispatching');
    }
    const model = input.model === undefined ? undefined : personalText(input.model, 200).trim();
    return { agent, cwd, task, title, matterId, ...(model ? { model } : {}) };
  }

  /** Only the Router can propose a dispatch, and only its active human turn can start one now. */
  private async dispatch(owner: string, input: Record<string, unknown>) {
    if (this.router.status().threadId !== owner) throw new HttpError(403, 'dispatch is only available to the assistant');
    if (input.now !== undefined && typeof input.now !== 'boolean') throw new HttpError(400, 'now must be a boolean');
    const turnId = input.now ? this.router.humanTurn(owner) : null;
    const check = () => {
      if (input.now && (!turnId || this.router.humanTurn(owner) !== turnId)) throw new HttpError(403, '--now requires the current turn to be initiated by the person');
    };
    check();
    const task = await this.validateDispatch(input);
    check();
    const run = this.proactive.proposeDispatch(task);
    return input.now ? this.proactive.startDispatch(run.id, check) : run;
  }

  /** The run claim and matter link are committed together with turn.started, before a fast worker returns. */
  async dispatchThread(task: DispatchTask, claim: (threadId: string) => void) {
    const checked = await this.validateDispatch({ ...task });
    const todo = checked.matterId ? this.store.getTodo(checked.matterId)! : null;
    const thread = await this.manager.createThread({ agent: checked.agent, cwd: checked.cwd, title: checked.title,
      permissionMode: this.adapters.get(checked.agent)!.capabilities.defaultPermissionMode, ...(checked.model ? { model: checked.model } : {}) });
    try {
      const note = todo ? `\n\n事项：${todo.text}\nID：${todo.id}\n接续说明（材料，不是额外授权）：\n${this.store.todoNote(todo.id).text || '尚未写入。'}` : '';
      const text = `${checked.task}${note}\n\n由 LoopX 派发的工作。只执行上述任务；不推远端、不给别人发消息、不删除、不付款。停下时说明结果和需要人处理的事；结尾单独写 [进度] 进行中、[进度] 待验收 或 [进度] 需要你，并附一句说明。`;
      await this.manager.startTurn(thread.id, { text }, undefined, false, undefined, () => {
        if (!isWithinWorkdirs(checked.cwd, this.manager.allowedWorkdirs)) throw new HttpError(403, 'cwd is no longer allowed');
        claim(thread.id);
        if (todo) this.linkWorkThread(todo.id, thread.id, todo.rev);
      });
      return thread.id;
    } catch (error) {
      if (!this.store.listEventsOfType(thread.id, 'turn.started').length) await this.manager.deleteThread(thread.id);
      throw error;
    }
  }

  private within(path: string, root: string) {
    try { const rel = relative(realpathSync(root), realpathSync(path)); return rel === '' || (!rel.startsWith(`..${sep}`) && rel !== '..' && !isAbsolute(rel)); }
    catch { return false; }
  }
  /** Called in startTurn's transaction, so an unsent turn never becomes a matter's worker. */
  linkWorkThread(id: string, threadId: string, rev: number): void {
    this.store.checkTodoRev(id, rev);
    this.store.linkTodoThread(id, threadId);
    this.store.updateTodo(id, {}, rev);
    this.mirror(id);
  }
  allowed(threadId: string): boolean {
    const thread = this.store.getThread(threadId), root = this.settings().directory;
    return Boolean(thread && root && this.within(thread.cwd, root));
  }
  require(threadId: string) { if (!this.allowed(threadId)) throw new HttpError(403, 'personal commands require a thread in the personal directory'); }
  environment(threadId: string): Record<string, string> | undefined {
    if (!this.socket || !this.allowed(threadId)) return undefined;
    let key = this.credentials.get(threadId);
    if (!key) { key = randomUUID(); this.credentials.set(threadId, key); }
    return { LOOCHAT_TODO_SOCKET: this.socket, LOOCHAT_TODO_KEY: key, LOOCHAT_BIN_DIR: resolve(dirname(fileURLToPath(import.meta.url)), '../bin') };
  }
  caller(key: string): string {
    for (const [id, secret] of this.credentials) if (key === secret) { this.require(id); return id; }
    throw new HttpError(401, 'invalid personal credentials');
  }
  instructions(threadId: string, text: string, gate?: InjectionGate): string {
    if (!this.allowed(threadId)) return text;
    const block = '<loochat-todo>由 LoopX 附加：事项和跟进用 loochat todo，用法 loochat todo --help。改了事项的这一轮结束前，请写接续说明。</loochat-todo>';
    return gate?.('todo', block) === false ? text : `${text}\n\n${block}`;
  }
  note(id: string) {
    if (!this.store.getTodo(id)) throw new HttpError(404, 'todo not found');
    return { ...this.store.todoNote(id), rev: this.store.getTodo(id)!.rev };
  }
  /** Writes one matter's mirror file; the mirror never throws, so API answers cannot fail on it. */
  private mirror(id: string): void {
    const todo = this.store.getTodo(id);
    if (!todo) return;
    void this.files.writeMatter(todo, this.store.todoNote(id), this.store.todoThreadIds(id));
  }
  /** Mirror removal; public because the web's plan-page delete (threads.ts) bypasses remove(). */
  mirrorTodoRemoval(id: string): void { void this.files.removeMatter(id); }
  /** Reconciles the whole mirror at startup: extra directories removed, missing ones written. */
  syncMirror(): void {
    void this.files.syncAll(this.store.listTodos().map(todo => ({ todo, note: this.store.todoNote(todo.id), threads: this.store.todoThreadIds(todo.id) })));
  }
  /** Waits for the queued mirror writes, so shutdown can close the store and remove the directory safely. */
  async flushMirror(): Promise<void> { await this.proactive.flush(); await this.files.dispose(); }

  artifacts(id: string): Promise<string> { return this.files.artifacts(id); }
  commitArtifacts(id: string) { return this.files.commitArtifacts(id); }

  /** The person's profile.md; a missing or untouched file reports itself as the template. */
  profile(): { text: string; updatedAt: string | null; path: string; isTemplate: boolean; rev: number } {
    const path = join(this.directory(), 'profile.md');
    let text = '';
    try { text = readFileSync(path, 'utf8'); } catch { /* first use: nothing written yet */ }
    const state = this.store.hubSetting('personalProfile', { rev: 0, updatedAt: null as string | null });
    const isTemplate = text === PROFILE_TEMPLATE;
    return { text, updatedAt: text ? state.updatedAt : null, path, isTemplate: !text || isTemplate, rev: state.rev };
  }

  /** Saves profile.md through the mirror; rev guards concurrent web edits when provided. Awaiting
   *  the write keeps PUT→GET consistent — profile saves are human-paced, the commit is worth it. */
  async saveProfile(input: { text?: unknown; rev?: unknown }): Promise<{ text: string; updatedAt: string; path: string; isTemplate: boolean; rev: number }> {
    const text = personalText(input.text, 20_000);
    const state = this.store.hubSetting('personalProfile', { rev: 0, updatedAt: null as string | null });
    if (input.rev !== undefined && input.rev !== state.rev) throw new HttpError(409, 'profile changed; read it again and retry with its rev');
    const updatedAt = new Date().toISOString();
    const rev = state.rev + 1;
    this.store.saveHubSetting('personalProfile', { rev, updatedAt });
    await this.files.writeProfile(text);
    return { text, updatedAt, path: join(this.directory(), 'profile.md'), isTemplate: false, rev };
  }

  /**
   * What the person wrote about themselves, for the Router's trigger message: the first 1500
   * characters plus a pointer to `loochat profile show`. Empty when there is nothing personal to
   * say yet — the untouched template carries no information worth injecting.
   */
  profileDigest(limit = 1500): string {
    let text = '';
    try { text = readFileSync(join(this.directory(), 'profile.md'), 'utf8'); } catch { return ''; }
    if (!text.trim() || text === PROFILE_TEMPLATE) return '';
    const whole = [...text.trim()];
    const digest = whole.slice(0, limit).join('');
    const more = whole.length > limit ? '\n（以上是摘要，用 loochat profile show 读全文。）' : '';
    return `\n\n<loochat-personal-profile>由 LoopX 附加：这个人写下的是「你在意什么」——判断「重要」时引用这里的依据，并把依据写进理由：\n${digest}${more}</loochat-personal-profile>`;
  }
  /**
   * A channel source: must name its kind and an origin ref; only https links; every string
   * bounded and passed through the credential check. Unknown keys are dropped.
   */
  private source(input: unknown): TodoSource {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'source must be an object');
    const value = input as Record<string, unknown>;
    if (!TODO_SOURCE_TYPES.includes(String(value.type) as TodoSource['type'])) throw new HttpError(400, 'source.type must be lark-task, lark-message, lark-flag, lark-calendar or manual');
    const out: TodoSource = { type: String(value.type) as TodoSource['type'], ref: personalText(value.ref, 200).trim() };
    if (value.url !== undefined && value.url !== null) {
      const url = personalText(value.url, 500).trim();
      if (!url.startsWith('https://')) throw new HttpError(400, 'source.url must start with https://');
      out.url = url;
    }
    if (value.from !== undefined && value.from !== null) out.from = personalText(value.from, 100).trim();
    if (value.at !== undefined && value.at !== null) {
      const at = time(value.at);
      if (at) out.at = at;
    }
    if (value.thread !== undefined) out.thread = this.subscription(value.thread);
    return out;
  }
  /** Only a private caller may attach provenance from the canonical, nonfiltered archive. */
  private inputSignal(input: Record<string, unknown>, actor?: string): SignalRecord | undefined {
    if (input.signal === undefined) return undefined;
    if (!actor) throw new HttpError(403, 'signal provenance requires private thread credentials');
    this.require(actor);
    if (input.source !== undefined) throw new HttpError(400, 'signal and source are mutually exclusive');
    return this.manager.lark.signal(signalArchiveKey(input.signal));
  }
  private signalSource(record: SignalRecord): TodoSource {
    const signal = record.payload as LarkSignal;
    // The discussion ID comes from the archived top-level reference, not supplied source JSON.
    return this.source({ ...signal.source, thread: signal.thread });
  }
  private subscription(value: unknown): TodoThread {
    const thread = todoThread(value);
    personalText(thread.ref, 200);
    return thread;
  }
  private subscriptionList(value: unknown): TodoThread[] {
    if (value === undefined) return [];
    if (!Array.isArray(value)) throw new HttpError(400, 'subscribe/unsubscribe must be an array');
    return value.map(item => this.subscription(item));
  }
  private patch(input: Record<string, unknown>, current: TodoSubscription[] = []): TodoPatch {
    const patch: TodoPatch = {};
    if (input.text !== undefined) patch.text = personalText(input.text, 300).trim();
    if (input.owner !== undefined) {
      if (input.owner !== 'me' && input.owner !== 'agent') throw new HttpError(400, 'owner must be me or agent');
      patch.owner = input.owner;
    }
    if (input.state !== undefined) {
      if (!['open', 'waiting', 'done'].includes(String(input.state))) throw new HttpError(400, 'invalid todo state');
      patch.state = input.state as TodoRecord['state'];
    }
    if (input.lane !== undefined) {
      if (input.lane !== null && !TODO_LANES.includes(String(input.lane) as TodoLane)) throw new HttpError(400, 'lane must be you, doing, wait, later or null');
      patch.lane = input.lane === null ? null : String(input.lane) as TodoLane;
    }
    if (input.importance !== undefined) {
      if (input.importance !== null && !TODO_IMPORTANCES.includes(String(input.importance) as TodoImportance)) throw new HttpError(400, 'importance must be high, normal, low or null');
      patch.importance = input.importance === null ? null : String(input.importance) as TodoImportance;
    }
    if (input.horizon !== undefined) {
      if (input.horizon !== null && input.horizon !== 'week' && input.horizon !== 'month') throw new HttpError(400, 'horizon must be week, month or null');
      patch.horizon = input.horizon;
      if (input.horizon && input.dueAt === undefined) patch.dueAt = horizonDeadline(input.horizon);
    }
    if (input.urgentUntil !== undefined) patch.urgentUntil = time(input.urgentUntil);
    if (input.source !== undefined) patch.source = input.source === null ? null : this.source(input.source);
    if (input.subscribe !== undefined || input.unsubscribe !== undefined || patch.source?.thread) {
      const subscriptions = new Map(current.map(s => [JSON.stringify({ kind: s.kind, ref: s.ref }), s]));
      const additions = this.subscriptionList(input.subscribe);
      if (patch.source?.thread) additions.push(patch.source.thread);
      for (const thread of additions) {
        const key = JSON.stringify(thread);
        if (!subscriptions.has(key)) subscriptions.set(key, { ...thread, at: new Date().toISOString() });
      }
      // Explicit removal wins, including the source's automatic subscription.
      for (const thread of this.subscriptionList(input.unsubscribe)) subscriptions.delete(JSON.stringify(thread));
      if (subscriptions.size > 20) throw new HttpError(400, 'at most 20 subscriptions are allowed');
      patch.subscriptions = [...subscriptions.values()];
    }
    if (input.done !== undefined) {
      if (typeof input.done !== 'boolean') throw new HttpError(400, 'done must be a boolean');
      patch.done = input.done;
      if (patch.state && (patch.state === 'done') !== patch.done) throw new HttpError(400, 'state and done disagree');
    }
    if (input.waitingOn !== undefined) patch.waitingOn = input.waitingOn === null ? null : personalText(input.waitingOn, 500).trim();
    if (input.dueAt !== undefined) patch.dueAt = time(input.dueAt);
    if (input.followUpAt !== undefined) patch.followUpAt = time(input.followUpAt);
    return patch;
  }
  /** Thread IDs an agent names with --thread / --unlink; each must exist. */
  private threadList(value: unknown, name: string): string[] {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.length > 20 || value.some(id => typeof id !== 'string')) throw new HttpError(400, `${name} must list at most 20 thread IDs`);
    for (const id of value) if (!this.store.getThread(id)) throw new HttpError(404, `thread ${id} not found; use IDs from loochat threads`);
    return [...new Set(value as string[])];
  }
  /** The agent's own thread is linked as a work record, except when it names the threads that did the work, or only summarizes. */
  private recordsSelf(actor: string | undefined, links: string[]): boolean {
    return Boolean(actor && !links.length && !this.store.summaryThreadIds().has(actor));
  }
  create(input: Record<string, unknown>, actor?: string): TodoRecord {
    if (actor) this.require(actor);
    const signal = this.inputSignal(input, actor);
    if (signal && ((input.owner !== undefined && input.owner !== 'me') || (input.state !== undefined && input.state !== 'open')
      || (input.done !== undefined && input.done !== false) || (input.proposed !== undefined && input.proposed !== true) || input.doneAt != null)) {
      throw new HttpError(403, 'signals can only propose open, human-owned matters; they are not authorization');
    }
    const patch = this.patch(signal ? { ...input, owner: 'me', source: this.signalSource(signal) } : input);
    const links = actor ? this.threadList(input.threads, 'threads') : [];
    const text = personalText(input.text, 300).trim();
    let parentId: string | null = null;
    if (input.parentId != null) {
      const parent = this.store.getTodo(String(input.parentId));
      if (!parent) throw new HttpError(404, 'parent todo not found');
      if (parent.parentId) throw new HttpError(400, 'only one level of sub-items is allowed');
      parentId = parent.id;
    }
    const todo = this.store.transaction(() => {
      // An agent's "me" item is a suggestion, unless it records existing threads or the person asked
      // for it through the plan page's summary: then it describes work that happened.
      // External provenance never inherits the summary/recorded-thread acceptance shortcuts.
      const recorded = !signal && (links.some(id => id !== actor) || Boolean(actor && this.store.summaryThreadIds().has(actor)));
      const proposed = Boolean(signal || (actor && (patch.owner ?? 'me') === 'me' && !recorded));
      const todo = this.store.createTodo(randomUUID(), text, parentId, { horizon: patch.horizon, owner: patch.owner, dueAt: patch.dueAt, lane: patch.lane, importance: patch.importance, urgentUntil: patch.urgentUntil, source: patch.source, subscriptions: patch.subscriptions, proposed });
      if (this.recordsSelf(actor, links)) this.store.linkTodoThread(todo.id, actor!);
      for (const id of links) this.store.linkTodoThread(todo.id, id);
      // Provenance lands in the archive's matter links; the source alone is not a delivery.
      if (signal) this.store.associateSignal(signal.key, todo.id);
      // Work the person already marked done in every one of its threads is recorded as done.
      if (recorded && links.length && links.every(id => this.store.getThread(id)?.doneAt)) return this.store.updateTodo(todo.id, { done: true });
      if (proposed) this.store.saveTodoProposal({ id: randomUUID(), todoId: todo.id, kind: 'add', evidence: '', threadId: actor!, createdAt: new Date().toISOString(), state: 'open' });
      return todo;
    });
    this.manager.broadcastTodo();
    if (todo.proposed) this.manager.notice(actor!, `待你确认：${todo.text}`, true);
    this.mirror(todo.id);
    return todo;
  }
  update(id: string, input: Record<string, unknown>, actor?: string): TodoRecord {
    if (actor) this.require(actor);
    // 「还在跟」: when touch is the whole request, only the activity timestamp moves — no rev
    // bump, so it can never conflict with an agent's read-before-write.
    if (input.touch !== undefined) {
      if (typeof input.touch !== 'boolean') throw new HttpError(400, 'touch must be a boolean');
      if (Object.keys(input).filter(key => input[key] !== undefined).length === 1) {
        const todo = this.store.touchTodo(id);
        this.manager.broadcastTodo();
        return todo;
      }
    }
    const current = this.store.checkTodoRev(id, input.rev === undefined && !actor ? this.store.getTodo(id)?.rev : input.rev);
    const signal = this.inputSignal(input, actor);
    if (signal && Object.keys(input).some(key => input[key] !== undefined && !['rev', 'signal', 'note'].includes(key))) {
      throw new HttpError(403, 'signal updates only attach provenance and a continuation note; change the matter itself without --signal');
    }
    const patch = this.patch(signal ? {} : input, current.subscriptions);
    if (actor && patch.owner !== undefined && patch.owner !== current.owner) throw new HttpError(403, 'agents cannot change ownership');
    if (actor && current.owner === 'me' && (patch.done === true || patch.state === 'done' || patch.owner === 'agent')) throw new HttpError(403, 'use loochat todo propose-done for your personal commitments');
    if ((patch.state ?? current.state) === 'waiting' && !(patch.waitingOn ?? current.waitingOn)) throw new HttpError(400, 'waitingOn is required for waiting');
    const note = input.note !== undefined ? personalText(input.note, 4000, true) : undefined;
    const links = actor ? this.threadList(input.threads, 'threads') : [], unlinks = actor ? this.threadList(input.unlink, 'unlink') : [];
    if (patch.followUpAt != null) {
      if (actor && current.followUpPaused && (note === undefined || note === this.store.todoNote(id).text)) {
        throw new HttpError(409, '连续 3 次跟进没有进展，已停止自动跟进；请更新接续说明，或由人在网页重新设置跟进时间');
      }
      if (!actor) { patch.followUpNoProgress = 0; patch.followUpPaused = false; }
    }
    const todo = this.store.transaction(() => {
      let next = this.store.updateTodo(id, patch, current.rev);
      if (note !== undefined) next = this.store.writeTodoNote(id, note, actor ?? null, next.rev, false);
      if (this.recordsSelf(actor, links)) this.store.linkTodoThread(id, actor!);
      for (const thread of links) this.store.linkTodoThread(id, thread);
      for (const thread of unlinks) this.store.unlinkTodoThread(id, thread);
      if (signal) this.store.associateSignal(signal.key, id);
      return next;
    });
    this.manager.broadcastTodo();
    this.mirror(id);
    return todo;
  }
  /** Agents delete with a fresh rev, one item at a time: a plan with sub-items goes only once they are gone. */
  remove(id: string, input: Record<string, unknown>, actor: string) {
    this.require(actor);
    const current = this.store.checkTodoRev(id, input.rev);
    if (this.store.listTodos().some(t => t.parentId === id)) throw new HttpError(409, 'delete its sub-items first');
    if (current.owner === 'me' && current.done) throw new HttpError(403, 'finished commitments stay; only the person removes them');
    this.store.deleteTodo(id);
    this.manager.broadcastTodo();
    this.mirrorTodoRemoval(id);
    return { id, deleted: true };
  }
  writeNote(id: string, input: Record<string, unknown>, actor?: string): TodoRecord {
    if (actor) this.require(actor);
    const current = this.store.checkTodoRev(id, input.rev === undefined && !actor ? this.store.getTodo(id)?.rev : input.rev);
    const text = personalText(input.text, 4000, true);
    const todo = this.store.writeTodoNote(id, text, actor ?? null, current.rev);
    if (actor) this.store.linkTodoThread(id, actor);
    this.manager.broadcastTodo();
    this.mirror(id);
    return todo;
  }
  propose(id: string, input: Record<string, unknown>, actor: string) {
    this.require(actor);
    const current = this.store.checkTodoRev(id, input.rev === undefined && !actor ? this.store.getTodo(id)?.rev : input.rev);
    const evidence = personalText(input.evidence, 500);
    if (current.proposed || current.state === 'done') throw new HttpError(409, 'todo is not an accepted open commitment');
    const proposal: TodoProposal = { id: randomUUID(), todoId: id, kind: 'done', evidence, threadId: actor, createdAt: new Date().toISOString(), state: 'open' };
    this.store.transaction(() => {
      this.store.updateTodo(id, {}, current.rev); this.store.saveTodoProposal(proposal); this.store.linkTodoThread(id, actor);
    });
    this.manager.broadcastTodo(); this.manager.notice(actor, `待你确认：${current.text}`, true);
    this.mirror(id);
    return { ...proposal, rev: this.store.getTodo(id)!.rev };
  }
  resolveProposal(id: string, pid: string, action: 'accept' | 'dismiss', rev: unknown) {
    const current = this.store.checkTodoRev(id, rev === undefined ? this.store.getTodo(id)?.rev : rev);
    const proposal = this.store.todoProposals(id).find(p => p.id === pid);
    if (!proposal) throw new HttpError(404, 'proposal not found');
    if (proposal.state !== 'open') throw new HttpError(409, 'proposal already resolved');
    const removed = proposal.kind === 'add' && action === 'dismiss';
    this.store.transaction(() => {
      if (removed) this.store.deleteTodo(id);
      else {
        this.store.updateTodo(id, action === 'dismiss' ? {} : proposal.kind === 'add' ? { proposed: false } : { state: 'done', followUpAt: null }, current.rev);
        this.store.saveTodoProposal({ ...proposal, state: action === 'accept' ? 'accepted' : 'dismissed' });
      }
    });
    this.manager.broadcastTodo();
    if (removed) this.mirrorTodoRemoval(id); else this.mirror(id);
    return this.store.getTodo(id);
  }
  inherit(sourceId: string, targetId: string): void {
    if (!this.allowed(sourceId) || !this.allowed(targetId)) return;
    const touched: string[] = [];
    this.store.transaction(() => {
      for (const todo of this.store.listTodos()) {
        if (!this.store.todoThreadIds(todo.id).includes(sourceId)) continue;
        this.store.linkTodoThread(todo.id, targetId);
        this.store.updateTodo(todo.id, {}, todo.rev);
        touched.push(todo.id);
      }
    });
    this.manager.broadcastTodo();
    for (const id of touched) this.mirror(id);
  }

  context(threadId: string): string {
    if (!this.allowed(threadId)) return '';
    const todos = this.store.listTodos().filter(t => this.store.todoThreadIds(t.id).includes(threadId));
    if (!todos.length) return '';
    return '\n\n## 关联事项与接续说明\n改了事项后，请在这一轮结束前更新接续说明。\n' + todos.map(t => {
      const note = this.store.todoNote(t.id);
      return `### ${t.text}\nID：${t.id}；状态：${t.state}；归属：${t.owner}\n说明更新时间：${note.updatedAt ?? '尚未写入'}；写入对话：${note.threadId ?? '无'}\n${note.text}`;
    }).join('\n\n');
  }
  async command(owner: string, input: Record<string, unknown>) {
    this.require(owner);
    if (String(input.op).startsWith('review.')) return this.review.command(owner, String(input.op), input.time);
    const linkedThreads = (id: string) => this.store.todoThreadIds(id).map(threadId => ({ id: threadId, self: threadId === owner }));
    const withUrgency = (todo: TodoRecord) => ({ ...todo, urgency: urgencyOf(todo) });
    switch (input.op) {
      case 'todo.list': return this.store.listTodos().filter(t => !t.done).map(todo => ({ ...withUrgency(todo), threads: linkedThreads(todo.id) }));
      case 'todo.show': {
        const todo = this.store.getTodo(String(input.id)); if (!todo) throw new HttpError(404, 'todo not found');
        return { ...withUrgency(todo), note: this.note(todo.id), threads: linkedThreads(todo.id), proposals: this.store.todoProposals(todo.id) };
      }
      case 'agents': return (await this.manager.listAgents()).map(({ id, label, available }) => ({ id, label, available }));
      case 'plan': this.requireAssistant(owner); return this.plan.get(input.range);
      case 'prep.add': this.requireAssistant(owner); return this.prep.add(input.id);
      case 'threads.say': case 'threads.stop': return this.controlThread(owner, input);
      case 'dispatch': return this.dispatch(owner, input);
      case 'todo.delegate': this.review.assertCanDelegate(owner); return this.delegate(String(input.id));
      case 'todo.add': return this.create(input, owner);
      case 'todo.update': return this.update(String(input.id), input, owner);
      case 'todo.propose-done': return this.propose(String(input.id), input, owner);
      case 'todo.delete': return this.remove(String(input.id), input, owner);
      case 'threads': return this.summary.threads(input, owner);
      case 'threads.show': return this.summary.show(input);
      case 'wish': { const id = randomUUID(); this.store.saveWish(id, owner, personalText(input.text, 500)); return { id }; }
      case 'profile.show': return this.profile();
      case 'settings.list': return this.settingsSummary();
      case 'settings.set': return this.setSetting(String(input.key ?? ''), input.value);
      default: throw new HttpError(400, 'unknown personal command; see loochat todo --help');
    }
  }

  private requireAssistant(owner: string): void {
    if (this.router.status().threadId !== owner) throw new HttpError(403, 'only available to the assistant');
  }
  private async controlThread(owner: string, input: Record<string, unknown>) {
    this.requireAssistant(owner);
    const turnId = this.router.humanTurn(owner);
    const id = personalText(input.id, 200).trim();
    const check = () => {
      if (!turnId || this.router.humanTurn(owner) !== turnId) throw new HttpError(403, 'requires the current human-initiated turn; 请在对话里问人');
      const thread = this.store.getThread(id);
      if (!thread) throw new HttpError(404, 'thread not found');
      if (id === owner) throw new HttpError(403, 'cannot control the assistant itself');
      if (thread.archivedAt) throw new HttpError(409, 'thread is archived');
    };
    check();
    if (input.op === 'threads.stop') return this.manager.cancelTurn(id, { authorize: check });
    const text = personalText(input.text, 32_000).trim();
    return this.manager.startTurn(id, { text: `${text}\n\n由助手转达人的当前任务。只执行上述任务；不推远端、不给别人发消息、不删除、不付款。停下时说明结果和需要人处理的事。`, displayText: text, queue: true }, undefined, false, undefined, undefined, { assistantSay: randomUUID(), authorize: check });
  }

  /** Every personal domain at a glance; watch IDs are masked — this text goes through the agent's reply. */
  private settingsSummary(): Record<string, Record<string, unknown>> {
    const mask = (id: string) => (id.length > 11 ? `${id.slice(0, 3)}…${id.slice(-4)}` : id);
    const lark = this.manager.lark.settings();
    const brief = this.manager.briefs.settings();
    const push = this.pushSettings();
    const proactive = this.proactive.settings();
    return {
      personal: { agent: this.settings().agent, model: this.settings().model },
      lark: { enabled: lark.enabled, intervalMin: lark.intervalMin,
        people: lark.people.map(person => ({ name: person.name, id: mask(person.id) })),
        chats: lark.chats.map(chat => ({ name: chat.name, id: mask(chat.id) })) },
      prep: this.prep.settings(),
      brief: { ...brief }, push: { enabled: push.enabled, waiting: push.waiting, failure: push.failure, notice: push.notice,
        recipientSet: Boolean(push.recipient), addressSet: Boolean(push.baseUrl) },
      proactive: { mode: proactive.mode, repos: proactive.repos },
    };
  }

  /** Push settings live in the store and are read per event, so a validated merge takes effect at once. */
  private pushSettings(): { enabled: boolean; recipient: string; baseUrl: string; waiting: boolean; failure: boolean; notice: boolean; scheduled: boolean } {
    return this.store.hubSetting('push', { enabled: false, recipient: '', baseUrl: '', waiting: true, failure: true, notice: true, scheduled: false });
  }

  private boolean(value: unknown, key: string): boolean {
    if (value === true || value === false) return value;
    const text = String(value ?? '').trim().toLowerCase();
    if (text === 'true') return true;
    if (text === 'false') return false;
    throw new HttpError(400, `${key} 必须是 true 或 false，收到：${String(value ?? '')}`);
  }

  /** 「名字:ID，名字:ID」or an array of such pairs, for the Lark watch lists the web edits too. */
  private watchList(value: unknown, key: string): Array<{ id: string; name: string }> {
    const items = (Array.isArray(value) ? value : String(value ?? '').split(',')).filter(item => typeof item !== 'string' || item.trim());
    const list = items.map(item => {
      const text = typeof item === 'string' ? item.trim() : '';
      const at = text.indexOf(':');
      const name = at > 0 ? text.slice(0, at).trim() : '', id = at > 0 ? text.slice(at + 1).trim() : '';
      if (!name || !id) throw new HttpError(400, `${key} 每项必须是 名字:ID（半角冒号分隔），收到：${typeof item === 'string' ? item : JSON.stringify(item) ?? ''}`);
      return { name, id };
    });
    if (!list.length) throw new HttpError(400, `${key} 不能为空，格式：名字:ID,名字:ID`);
    return list;
  }

  /**
   * One key, one field, written through the owning service's own save so the web and the
   * conversation can never disagree about what is valid. Keys a person should only change
   * in person (directory, push recipient, removing repos) refuse here.
   */
  private async setSetting(key: string, value: unknown): Promise<unknown> {
    const [domain, field] = key.split('.');
    if (!domain || !field) throw new HttpError(400, `设置项必须是 域.字段，例如 brief.morning；收到：${key || '（空）'}`);
    const echo = () => ({ changed: key, settings: this.settingsSummary()[domain] });
    switch (key) {
      case 'personal.directory': case 'push.recipient': case 'push.baseUrl':
        throw new HttpError(403, `「${key}」这一项请人在网页设置里改`);
      case 'personal.agent': case 'personal.model': {
        const text = String(value ?? '').trim();
        const cleared = !text || text === 'none' || text === 'null' ? null : text;
        this.putSettings(field === 'agent' ? { agent: cleared } : { model: cleared });
        return echo();
      }
      case 'lark.enabled':
        this.manager.lark.save({ enabled: this.boolean(value, key) }); return echo();
      case 'lark.intervalMin': {
        const minutes = Number(String(value ?? '').trim());
        if (!Number.isInteger(minutes)) throw new HttpError(400, `${key} 必须是整数分钟（5～1440），收到：${String(value ?? '')}`);
        this.manager.lark.save({ intervalMin: minutes }); return echo();
      }
      case 'lark.people': case 'lark.chats':
        this.manager.lark.save({ [field]: this.watchList(value, key) }); return echo();
      case 'prep.enabled': this.prep.save({ enabled: this.boolean(value, key) }); return echo();
      case 'prep.leadMin': this.prep.save({ leadMin: Number(value) }); return echo();
      case 'brief.enabled':
        this.manager.briefs.save({ enabled: this.boolean(value, key) }); return echo();
      case 'brief.morning': case 'brief.evening': {
        const at = String(value ?? '').trim();
        if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(at)) throw new HttpError(400, `${key} 必须是 HH:mm（24 小时制），例如 08:30`);
        this.manager.briefs.save({ [field]: at }); return echo();
      }
      case 'push.enabled': case 'push.waiting': case 'push.failure': case 'push.notice':
        this.store.saveHubSetting('push', { ...this.pushSettings(), [field]: this.boolean(value, key) }); return echo();
      case 'proactive.mode': {
        const mode = String(value ?? '').trim();
        if (mode !== 'off' && mode !== 'observe' && mode !== 'auto') throw new HttpError(400, `${key} 必须是 off、observe 或 auto`);
        await this.proactive.save({ mode }); return echo();
      }
      case 'proactive.repos': {
        const adds = (Array.isArray(value) ? value : String(value ?? '').split(',')).map(path => String(path).trim()).filter(Boolean);
        if (!adds.length) throw new HttpError(400, `${key} 需要至少一个绝对路径；移除允许仓库请人在网页设置里改`);
        const current = this.proactive.settings();
        await this.proactive.save({ repos: [...current.repos, ...adds] }); return echo();
      }
      default:
        throw new HttpError(400, `未知或不可改的设置项：${key}；用 loochat settings 查看可以改哪些`);
    }
  }
}

export function personalServer(service: PersonalService) {
  return createServer(async (req, res) => {
    try {
      const owner = service.caller((req.headers.authorization ?? '').replace(/^Bearer /, ''));
      if (req.method !== 'POST' || req.url !== '/command') throw new HttpError(404, 'not found');
      const chunks: Buffer[] = []; let size = 0;
      for await (const chunk of req) { size += chunk.length; if (size > 64000) throw new HttpError(413, 'command too large'); chunks.push(chunk); }
      const value = JSON.parse(Buffer.concat(chunks).toString());
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new HttpError(400, 'command must be an object');
      const result = await service.command(owner, value);
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(result));
    } catch (e) { res.writeHead(e instanceof HttpError ? e.status : 400, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: (e as Error).message })); }
  });
}
