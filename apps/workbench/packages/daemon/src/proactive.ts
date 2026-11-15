import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { realpathSync, statSync } from 'node:fs';
import { basename, dirname, isAbsolute } from 'node:path';
import { promisify } from 'node:util';
import type { AgentAdapter } from './adapters/types.js';
import { turnAnswer } from './coordination.js';
import { HttpError } from './errors.js';
import type { StoredEvent } from './events.js';
import { turnOutcome } from './lifecycle.js';
import type { DispatchTask, PersonalService } from './personal.js';
import { advancePrompt, parseAdvance, parsePrecheck, precheckPrompt, transientError, type AdvanceDecision, type PrecheckContext } from './proactive-advance.js';
import { ProactiveSandbox, type SandboxChanges } from './proactive-sandbox.js';
import { urgencyOf, type Store, type ThreadRecord, type TodoRecord } from './store.js';
import type { ThreadManager } from './threads.js';
import { isWithinWorkdirs } from './workdirs.js';

export type ProactiveMode = 'off' | 'observe' | 'auto';
const MODES: ProactiveMode[] = ['off', 'observe', 'auto'];
export interface ProactiveSettings {
  mode: ProactiveMode;
  repos: string[];
  /** Read-only compatibility for clients of the old switch: on whenever proactive may act at all. */
  enabled: boolean;
}
export interface ProactiveRun {
  id: string;
  /** Empty for an advance on a session that belongs to no matter. */
  matterId: string;
  /** The session's title when the run was recorded, for cards that have no matter to name them. */
  threadTitle?: string;
  /** A check of recent sessions and commits made before proposing anything for the matter. */
  precheck?: 'done' | 'doing' | 'todo';
  /** What the check cited: `session:<id>` or `commit:<id>`. */
  source?: string;
  kind: 'prepare' | 'sandbox' | 'advance' | 'dispatch';
  agent?: string;
  cwd?: string;
  task?: string;
  title?: string;
  model?: string;
  variant?: 'deadline' | 'plan';
  needsRepo?: true;
  turnId?: string;
  phase?: AdvanceDecision['phase'];
  on?: AdvanceDecision['on'];
  line?: string;
  question?: AdvanceDecision['question'];
  wait?: AdvanceDecision['wait'];
  wakeAt?: string;
  attempts?: number;
  reason?: 'transient' | 'external' | 'user' | 'done';
  repo?: string;
  branch?: string;
  /** Empty while setting up, when setup fails before a worker can be created, or for an observation. */
  threadId: string;
  startedAt: string;
  endedAt?: string;
  status: 'running' | 'observed' | 'approved' | 'ready' | 'merged' | 'discarded' | 'reverted' | 'failed' | 'waiting' | 'asking' | 'answered' | 'continued' | 'closed';
  summary?: string;
  /** Why a sandbox round ended silently: failed tests or nothing reviewable. An observation needs no reason. */
  discardReason?: string;
  testReport?: string;
  files?: SandboxChanges['files'];
}
const SETTINGS = 'personalProactiveSettings';
/** Shared with the review's watchdog, which reads the same record. */
export const PROACTIVE_RUNS_KEY = 'personalProactiveRuns';
const RUNS = PROACTIVE_RUNS_KEY;
/** Matters a check found no trace of, by when: no proposal is held back for them this week. */
const PRECHECKS = 'personalProactivePrechecks';
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
/** How far back advance looks: the sessions the person is working in now, not ones long left alone. */
const RECENT = 3 * DAY;
const clip = (text: string, length: number) => [...text].slice(0, length).join('');
/** What a worker's "## 测试结果" section must say to count as a failure; anything else passes. */
const TEST_FAILED = /失败|failed|not ok/i;
const exec = promisify(execFile);
const localDay = (now: Date) => `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
const minutes = (time: string) => { const [h, m] = time.split(':').map(Number); return h * 60 + m; };
/** The repository a directory belongs to, as its main checkout: every worktree of it is the same repository. */
async function mainRepository(cwd: string): Promise<string | null> {
  try {
    const common = (await exec('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd, timeout: 5000 })).stdout.trim();
    if (basename(common) === '.git') return realpathSync(dirname(common));
    return realpathSync((await exec('git', ['rev-parse', '--show-toplevel'], { cwd, timeout: 5000 })).stdout.trim());
  } catch { return null; }
}
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Time-sensitive preparation only; a pin alone does not invent a deadline or a meeting. */
export function preparationCandidate(todo: TodoRecord, note: string, now: Date): boolean {
  if (todo.done || todo.state === 'done' || todo.proposed || note.includes('已备好')) return false;
  const meeting = todo.source?.type === 'lark-calendar' && Boolean(todo.source.at)
    && localDay(new Date(todo.source.at!)) === localDay(now);
  const soon = Boolean(todo.dueAt) && Date.parse(todo.dueAt!) >= new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() && Date.parse(todo.dueAt!) <= now.getTime() + 48 * HOUR;
  return (todo.importance === 'high' || urgencyOf(todo, now).urgent || meeting) && (soon || meeting);
}

const dayLabel = (due: Date, now: Date) => {
  if (localDay(due) === localDay(now)) return '今天';
  if (localDay(due) === localDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1))) return '明天';
  return `${due.getFullYear()}-${due.getMonth() + 1}-${due.getDate()} `;
};

/** Why this matter is worth preparing for, in plain words a person can judge at a glance. */
function prepareReasons(todo: TodoRecord, now: Date): string[] {
  const meeting = todo.source?.type === 'lark-calendar' && Boolean(todo.source.at)
    && localDay(new Date(todo.source.at!)) === localDay(now);
  const reasons: string[] = [];
  if (todo.importance === 'high') reasons.push('重要');
  if (todo.dueAt) reasons.push(`${dayLabel(new Date(todo.dueAt), now)}截止`);
  else if (urgencyOf(todo, now).urgent) reasons.push('紧急');
  if (meeting) reasons.push('今天有日程');
  return reasons;
}

/** What would be done and why, assembled from the same rules that picked the candidate; no model call. */
function observationSummary(todo: TodoRecord, kind: ProactiveRun['kind'], repo: string | undefined, now: Date): string {
  if (kind === 'sandbox') return `会在 ${repo!.split('/').pop()} 的沙盒里继续「${todo.text}」：你之前开始过这件事`;
  return `会为「${todo.text}」提前准备材料：${prepareReasons(todo, now).join('，')}`;
}

/** One shared scheduler drives this service; only a worker's ending reads its answer and files. */
export class ProactiveService {
  private ticking = false;
  private readonly finishing = new Map<string, Promise<void>>();
  private readonly turns = new Map<string, string>();
  private readonly acting = new Set<string>();

  constructor(private readonly store: Store, private readonly manager: ThreadManager,
    private readonly personal: PersonalService, private readonly adapters: Map<string, AgentAdapter>,
    private readonly sandbox = new ProactiveSandbox()) {
    manager.subscribe(event => this.onEvent(event));
  }

  /** Legacy stored switches ({ enabled }) migrate on read: on meant acting, so it becomes auto. */
  settings(): ProactiveSettings {
    const stored = this.store.hubSetting<{ mode?: ProactiveMode; enabled?: boolean; repos?: string[] }>(SETTINGS, {});
    const mode = stored.mode ?? (stored.enabled === true ? 'auto' : 'off');
    return { mode, repos: stored.repos ?? [], enabled: mode !== 'off' };
  }
  runs(matterId?: string): ProactiveRun[] {
    const runs = this.store.hubSetting<ProactiveRun[]>(RUNS, []);
    return matterId === undefined ? runs : runs.filter(run => run.matterId === matterId);
  }
  /** Matters a person can still look into: a discarded run left nothing behind, so it does not light the desk. */
  counts(): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const run of this.runs()) if (run.status !== 'discarded') counts[run.matterId] = (counts[run.matterId] ?? 0) + 1;
    return counts;
  }

  /** A person's verdict on an observation: kept as feedback, never a trigger to execute. */
  approve(id: string): ProactiveRun {
    const run = this.get(id);
    if (run.status !== 'observed') throw new HttpError(409, '只有观察记录可以标记');
    return this.update(id, { status: 'approved' });
  }

  async save(input: Record<string, unknown>): Promise<ProactiveSettings> {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, '请提供主动行动设置对象');
    if (input.enabled !== undefined && typeof input.enabled !== 'boolean') throw new HttpError(400, '主动行动开关必须是开或关');
    if (input.mode !== undefined && !MODES.includes(input.mode as ProactiveMode)) throw new HttpError(400, '主动行动模式必须是 off、observe 或 auto');
    if (input.repos !== undefined && (!Array.isArray(input.repos) || input.repos.some(repo => typeof repo !== 'string' || !isAbsolute(repo)))) {
      throw new HttpError(400, '允许的仓库必须是绝对路径列表');
    }
    let repos: string[] | undefined;
    if (input.repos !== undefined) {
      repos = [];
      for (const path of input.repos as string[]) {
        let repo: string;
        try { repo = realpathSync(path); if (!statSync(repo).isDirectory()) throw new Error(); }
        catch { throw new HttpError(400, `仓库目录不存在：${path}`); }
        if (!isWithinWorkdirs(repo, this.manager.allowedWorkdirs)) throw new HttpError(403, `仓库不在允许的工作目录内：${path}`);
        let root: string;
        try { root = realpathSync((await exec('git', ['rev-parse', '--show-toplevel'], { cwd: repo })).stdout.trim()); }
        catch { throw new HttpError(400, `目录不是可用的 git 仓库：${path}`); }
        if (repo !== root) throw new HttpError(400, `请选择 git 仓库根目录：${root}`);
        if (!repos.includes(repo)) repos.push(repo);
      }
    }
    // Read the current settings only after git validation awaited I/O, so a concurrent patch survives.
    const current = this.settings();
    // An old client turning the switch on lands in observe first; an explicit mode wins over it.
    const mode: ProactiveMode = input.mode !== undefined ? input.mode as ProactiveMode
      : input.enabled !== undefined ? (input.enabled ? 'observe' : 'off') : current.mode;
    const repos0 = repos ?? current.repos;
    this.store.saveHubSetting(SETTINGS, { mode, repos: repos0 });
    return { mode, repos: repos0, enabled: mode !== 'off' };
  }

  private saveRun(run: ProactiveRun): void {
    const runs = this.runs();
    const index = runs.findIndex(candidate => candidate.id === run.id);
    if (index >= 0) runs[index] = run;
    else runs.push(run);
    this.store.saveHubSetting(RUNS, runs.slice(-200));
    this.manager.broadcastTodo();
  }
  proposeDispatch(task: DispatchTask): ProactiveRun {
    const run: ProactiveRun = { ...task, id: randomUUID(), kind: 'dispatch', threadId: '',
      startedAt: new Date().toISOString(), status: 'asking', summary: clip(task.task, 300) };
    this.saveRun(run);
    return run;
  }

  async startDispatch(id: string, authorize: () => void = () => {}): Promise<ProactiveRun> {
    const run = this.get(id);
    const check = () => {
      authorize();
      const current = this.get(id);
      if (current.kind !== 'dispatch' || current.status !== 'asking') throw new HttpError(409, '只有待开始的派活卡片可以开始', { run: current });
    };
    check();
    if (this.acting.has(id)) throw new HttpError(409, '派活正在开始', { run: this.get(id), starting: true });
    this.acting.add(id);
    try {
      await this.personal.dispatchThread({ agent: run.agent!, cwd: run.cwd!, task: run.task!, title: run.title!,
        matterId: run.matterId, ...(run.model ? { model: run.model } : {}) }, threadId => {
        check();
        this.update(id, { status: 'answered', threadId, endedAt: new Date().toISOString() });
      });
      return this.get(id);
    } catch (error) {
      if (error instanceof HttpError && error.status === 409) throw new HttpError(409, error.message, { ...error.details, run: this.get(id) });
      throw error;
    } finally { this.acting.delete(id); }
  }

  private update(id: string, patch: Partial<ProactiveRun>): ProactiveRun {
    const run = { ...this.get(id), ...patch };
    this.saveRun(run);
    return run;
  }
  private get(id: string): ProactiveRun {
    const run = this.runs().find(candidate => candidate.id === id);
    if (!run) throw new HttpError(404, '找不到这次主动行动记录');
    return run;
  }

  /** A dead worker cannot remain "running" forever after a restart or missed ending. */
  recover(now = new Date()): void {
    if (this.ticking) return;
    for (const run of this.runs()) {
      if (run.status !== 'running' || this.finishing.has(run.id) || this.store.getThread(run.threadId)?.status === 'running') continue;
      this.turns.delete(run.id);
      this.update(run.id, { status: 'failed', endedAt: now.toISOString(), summary: '主动行动被中断，工作线程已结束；请查看线程后重试' });
    }
  }

  private busy(matterId: string): boolean {
    return this.store.todoThreadIds(matterId).some(id => this.store.getThread(id)?.status === 'running');
  }
  private atCapacity(): boolean {
    return new Set(this.runs().filter(run => run.kind !== 'advance' && run.status === 'running'
      && this.store.getThread(run.threadId)?.status === 'running').map(run => run.threadId)).size >= 2;
  }
  private quiet(now: Date): boolean {
    // This is exactly the source used by PersonalBriefSchedule, including user edits.
    const quiet = this.personal.review.status().quietHours;
    const start = minutes(quiet.start), end = minutes(quiet.end), current = now.getHours() * 60 + now.getMinutes();
    return start !== end && (start < end ? current >= start && current < end : current >= start || current < end);
  }

  async tick(now = new Date()): Promise<void> {
    if (this.ticking) return;
    this.recover(now);
    this.invalidate();
    if (this.settings().mode === 'off' || this.quiet(now) || this.acting.size) return;
    this.ticking = true;
    try {
      for (const run of this.runs()) {
        if (run.status === 'waiting' && run.wakeAt && Date.parse(run.wakeAt) <= now.getTime()) await this.sendAdvance(run, now);
      }
      const runs = this.runs(), settings = this.settings();
      const today = runs.filter(run => !['observed', 'approved'].includes(run.status) && !run.needsRepo && localDay(new Date(run.startedAt)) === localDay(now));
      const todos = this.store.listTodos().filter(todo => !todo.done && todo.state !== 'done' && !todo.proposed && !this.busy(todo.id)
        && !runs.some(run => run.matterId === todo.id && ['running', 'waiting', 'asking'].includes(run.status)));
      const canOpen = !this.atCapacity() && !runs.some(run => (run.kind === 'prepare' || run.kind === 'sandbox') && !run.needsRepo && now.getTime() - Date.parse(run.startedAt) < HOUR);
      const observed = (todo: TodoRecord, kind: ProactiveRun['kind']) => settings.mode === 'observe' && runs.some(run => !run.needsRepo && run.matterId === todo.id && run.kind === kind
        && ['observed', 'approved'].includes(run.status) && now.getTime() - Date.parse(run.startedAt) < DAY);
      if (canOpen && today.filter(run => run.kind === 'prepare').length < 5) {
        const todo = todos.find(todo => !observed(todo, 'prepare') && !runs.some(run => run.matterId === todo.id && run.kind === 'prepare' && run.status === 'ready')
          && preparationCandidate(todo, this.store.todoNote(todo.id).text, now));
        if (todo) { if (!await this.handledAlready(todo, '提前准备材料', now)) await this.prepareOrObserve(todo, 'prepare', now, undefined, 'deadline'); return; }
      }
      if (await this.advance(now)) return;
      if (today.filter(run => run.kind === 'sandbox').length < 2) {
        for (const todo of todos) {
          if (runs.some(run => run.matterId === todo.id && run.kind === 'sandbox' && ['ready', 'merged'].includes(run.status))) continue;
          for (const id of this.store.todoThreadIds(todo.id)) {
            const thread = this.store.getThread(id);
            if (!thread || thread.archivedAt || runs.some(run => run.kind !== 'advance' && run.threadId === id) || !isWithinWorkdirs(thread.cwd, this.manager.allowedWorkdirs)) continue;
            const repo = await mainRepository(thread.cwd);
            if (!repo || isWithinWorkdirs(repo, [this.personal.directory()])) continue;
            if (!isWithinWorkdirs(repo, this.manager.allowedWorkdirs) || this.busy(todo.id) || this.settings().mode === 'off') continue;
            if (!this.settings().repos.includes(repo)) {
              // One question per matter a week: a matter's many worktrees are one repository, and one "no" is enough.
              if (runs.some(run => run.needsRepo && run.matterId === todo.id && now.getTime() - Date.parse(run.startedAt) < 7 * DAY)) continue;
              if (await this.handledAlready(todo, '在沙盒里继续', now)) return;
              this.saveRun({ id: randomUUID(), matterId: todo.id, kind: 'sandbox', needsRepo: true, repo, threadId: '', startedAt: now.toISOString(), status: 'observed',
                summary: `这件事的线程在 ${repo.split('/').pop()} 里，允许它在沙盒里继续吗？` });
              return;
            }
            if (canOpen && !observed(todo, 'sandbox')) { if (!await this.handledAlready(todo, '在沙盒里继续', now)) await this.prepareOrObserve(todo, 'sandbox', now, repo); return; }
          }
        }
      }
      if (canOpen && today.filter(run => run.kind === 'prepare').length < 5) {
        const todo = todos.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).find(todo => this.store.todoThreadIds(todo.id).length === 0
          && [...todo.text].length >= 6 && !this.store.todoNote(todo.id).text.includes('已备好')
          && !runs.some(run => run.matterId === todo.id && run.kind === 'prepare' && (run.status === 'ready' || now.getTime() - Date.parse(run.startedAt) < 7 * DAY)));
        if (todo && !await this.handledAlready(todo, '拆解步骤、写第一步方案', now)) await this.prepareOrObserve(todo, 'prepare', now, undefined, 'plan');
      }
    } finally { this.ticking = false; }
  }

  /** What the person did lately, for a check to cite: recent sessions and the commits of their repositories. */
  private async recentContext(now: Date): Promise<PrecheckContext> {
    const router = this.personal.router.status().threadId;
    const threads = this.store.listThreads().filter(thread => thread.id !== router && !this.runs().some(run => run.kind !== 'advance' && run.threadId === thread.id)
      && now.getTime() - Date.parse(thread.updatedAt) < 14 * DAY).slice(0, 40);
    const sessions = threads.map(thread => ({ id: thread.id, title: thread.title, preview: clip(thread.preview ?? '', 120),
      progress: thread.report ? `${thread.report.stage}: ${clip(thread.report.note, 120)}` : '', updatedAt: thread.updatedAt }));
    const repos: string[] = [];
    for (const thread of threads) {
      if (repos.length >= 6 || !thread.cwd || !isWithinWorkdirs(thread.cwd, this.manager.allowedWorkdirs)) continue;
      const repo = await mainRepository(thread.cwd);
      if (repo && !repos.includes(repo) && !isWithinWorkdirs(repo, [this.personal.directory()])) repos.push(repo);
    }
    const commits: PrecheckContext['commits'] = [];
    for (const repo of repos) {
      try {
        const log = (await exec('git', ['log', '--all', '--no-merges', '--since=30.days', '-n', '60', '--format=%h%x09%cs%x09%s'], { cwd: repo, timeout: 5000 })).stdout;
        for (const line of log.split('\n').filter(Boolean)) {
          const [id, date, ...subject] = line.split('\t');
          commits.push({ id, repo: basename(repo), date, subject: clip(subject.join('\t'), 160) });
        }
      } catch { /* A repository that cannot be read adds no evidence. */ }
    }
    return { sessions, commits };
  }

  /**
   * Before proposing anything for a matter, look at what was done lately. Done: a card offers to close
   * it, citing the evidence. Doing: leave it alone. Either is remembered for a week, and so is "go
   * ahead"; a "not done yet" on the card lets the proposal through. True means: do not propose now.
   */
  private async handledAlready(todo: TodoRecord, action: string, now: Date): Promise<boolean> {
    const checks = this.runs().filter(run => run.precheck && run.matterId === todo.id && now.getTime() - Date.parse(run.startedAt) < 7 * DAY);
    const last = checks.at(-1);
    if (last) return last.precheck === 'doing' || (last.precheck === 'done' && last.status === 'asking');
    // "Nothing found" is kept apart from the run history: it is no event, only a reason not to ask again this week.
    const clear = this.store.hubSetting<Record<string, string>>(PRECHECKS, {});
    if (clear[todo.id] && now.getTime() - Date.parse(clear[todo.id]) < 7 * DAY) return false;
    const settings = await this.personal.resolvedSettings();
    if (!settings.agent || this.budget(now).models >= 30) return true;
    const budget = this.budget(now); budget.models++;
    this.store.saveHubSetting('personalProactiveBudget', budget);
    const context = await this.recentContext(now);
    let decision = parsePrecheck('', context);
    try {
      decision = parsePrecheck(await this.manager.askOnce(settings.agent, precheckPrompt(todo.text, this.store.todoNote(todo.id).text, action, context, now),
        { model: settings.model, title: '事前核对', recordPrompt: false, timeoutMs: 60_000 }), context);
    } catch { /* No answer is no evidence: the proposal goes ahead as before. */ }
    const sourceThread = decision.source?.startsWith('session:') ? decision.source.slice(8) : '';
    const run: ProactiveRun = { id: randomUUID(), matterId: todo.id, kind: 'advance', threadId: sourceThread, startedAt: now.toISOString(), precheck: decision.state,
      ...(decision.source ? { source: decision.source } : {}), status: 'discarded', line: decision.evidence };
    if (decision.state === 'done') Object.assign(run, { status: 'asking', reason: 'done', phase: 'done', summary: `看起来已经做过了：${decision.evidence}` });
    else if (decision.state === 'doing') Object.assign(run, { summary: `正在别处进行：${decision.evidence}`, discardReason: '已有会话在做' });
    const current = this.store.getTodo(todo.id);
    if (!current || current.done || current.state === 'done' || current.proposed || this.settings().mode === 'off') return true;
    if (decision.state === 'todo') {
      const kept = Object.fromEntries(Object.entries(this.store.hubSetting<Record<string, string>>(PRECHECKS, {})).filter(([, at]) => now.getTime() - Date.parse(at) < 7 * DAY));
      this.store.saveHubSetting(PRECHECKS, { ...kept, [todo.id]: now.toISOString() });
      return false;
    }
    this.saveRun(run);
    // Said in her own conversation, not typed into the session the evidence came from.
    const router = this.personal.router.status().threadId;
    if (run.status === 'asking' && router && this.store.getThread(router)) this.manager.notice(router, `需要你：${todo.text}`, false, true);
    return true;
  }

  private async prepareOrObserve(todo: TodoRecord, kind: 'prepare' | 'sandbox', now: Date, repo?: string, variant?: ProactiveRun['variant']): Promise<void> {
    if (this.settings().mode === 'observe') {
      this.saveRun({ id: randomUUID(), matterId: todo.id, kind, repo, variant, threadId: '', startedAt: now.toISOString(), status: 'observed',
        summary: variant === 'plan' ? `会为「${todo.text}」拆解步骤，备好第一步方案` : observationSummary(todo, kind, repo, now) });
    } else if (this.settings().mode === 'auto') await this.start(todo, kind, now, repo, variant);
  }

  asking(): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const run of this.runs()) if (run.status === 'asking') counts[run.matterId] = (counts[run.matterId] ?? 0) + 1;
    return counts;
  }

  private latest(threadId: string) {
    const events = this.store.recentLifecycle(threadId, 2);
    const end = events[0], start = events[1];
    if (!end || !start || start.type !== 'turn.started' || end.type === 'turn.started' || end.turnId !== start.turnId) return null;
    if (start.data.imported || start.data.fromTerminal || end.data.imported || end.data.fromTerminal) return null;
    return { start, end, turnId: end.turnId };
  }

  private invalidate(threadId?: string): void {
    for (const run of this.runs()) {
      if (run.kind === 'dispatch' || !['waiting', 'asking'].includes(run.status) || run.precheck || threadId && run.threadId !== threadId) continue;
      if (threadId || this.store.recentLifecycle(run.threadId, 1)[0]?.turnId !== run.turnId) {
        this.update(run.id, { status: 'discarded', discardReason: '线程已继续' });
      }
    }
  }

  /** Persist counters separately from the bounded, user-facing run history. */
  private budget(now: Date) {
    const value = this.store.hubSetting<{ day: string; models: number; sends: number; sent: Record<string, number> }>('personalProactiveBudget', { day: '', models: 0, sends: 0, sent: {} });
    return value.day === localDay(now) ? value : { day: localDay(now), models: 0, sends: 0, sent: value.sent };
  }

  /**
   * Sessions the person worked in lately, newest first: the ones whose last turn ended within the
   * window, started from the hub, and that nobody else follows (not a collaboration member, a
   * scheduled run, her own conversation, or a thread a proactive run opened).
   */
  private recentSessions(now: Date): Array<{ thread: ThreadRecord; latest: NonNullable<ReturnType<ProactiveService['latest']>>; todo: TodoRecord | null }> {
    const runs = this.runs(), router = this.personal.router.status().threadId;
    const owner = new Map<string, TodoRecord>();
    for (const todo of this.store.listTodos()) {
      if (todo.done || todo.state === 'done' || todo.proposed) continue;
      for (const id of this.store.todoThreadIds(todo.id)) if (!owner.has(id)) owner.set(id, todo);
    }
    const sessions = [];
    for (const thread of this.store.listThreads()) {
      if (thread.status === 'running' || thread.archivedAt || thread.doneAt || thread.schedule || thread.id === router) continue;
      if (now.getTime() - Date.parse(thread.updatedAt) > RECENT) continue;
      const team = this.manager.coordination?.metadata(thread.id);
      if (team && team.rootId !== thread.id) continue;
      if (runs.some(run => run.kind !== 'advance' && run.threadId === thread.id)) continue;
      const latest = this.latest(thread.id);
      if (!latest) continue;
      const age = now.getTime() - Date.parse(latest.end.ts);
      if (age < 15 * 60_000 || age > RECENT) continue;
      sessions.push({ thread, latest, todo: owner.get(thread.id) ?? null });
    }
    return sessions.sort((a, b) => Date.parse(b.latest.end.ts) - Date.parse(a.latest.end.ts));
  }

  private async advance(now: Date): Promise<boolean> {
    for (const { thread, latest, todo } of this.recentSessions(now)) {
      const threadId = thread.id, runs = this.runs();
      if (runs.some(run => run.kind === 'advance' && run.threadId === threadId && run.turnId === latest.turnId)) continue;
      if (todo && this.busy(todo.id)) continue;
      const matterId = todo?.id ?? '';
      const began = Date.now();
      const outcome = turnOutcome(latest.end)!;
      const transient = outcome.outcome === 'error' && transientError(outcome.code, outcome.message ?? '');
      let decision: AdvanceDecision = { phase: 'unsure', line: '' };
      // The agent's own [进度] line for this turn says it best; only an unclear one goes to the model.
      const report = thread.report && !thread.report.by && Date.parse(thread.report.at) >= Date.parse(latest.start.ts) ? thread.report : null;
      if (transient) { /* A rule: no model call. */ }
      else if (report?.stage === 'blocked' && report.note.trim()) decision = { phase: 'blocked', on: 'user', line: clip(report.note.trim(), 30), question: { text: report.note.trim(), options: [] } };
      else if (report?.stage === 'review') decision = { phase: 'done', line: clip(report.note.trim(), 30) };
      else {
        if (this.budget(now).models >= 30) continue;
        const settings = await this.personal.resolvedSettings();
        if (!settings.agent || this.settings().mode === 'off' || (todo && this.busy(todo.id))) continue;
        const budget = this.budget(now); budget.models++;
        this.store.saveHubSetting('personalProactiveBudget', budget);
        const answer = turnAnswer(this.store.voiceAnswerEvents(threadId, latest.turnId), latest.turnId, Number.MAX_SAFE_INTEGER).trim();
        try {
          decision = parseAdvance(await this.manager.askOnce(settings.agent,
            advancePrompt(todo?.text ?? thread.title, todo ? this.store.todoNote(todo.id).text : '', String(latest.start.data.text ?? ''), answer, now),
            { model: settings.model, title: '推进分流', recordPrompt: false, timeoutMs: 60_000 }), answer);
        } catch { /* A classifier outage produces an audit entry, never a continuation. */ }
      }
      // A finished session with no matter has nothing to close; its [进度] line already says so in the sidebar.
      if (decision.phase === 'done' && !todo) decision = { ...decision, phase: 'unsure' };
      const reason = transient ? 'transient' : decision.phase === 'done' ? 'done' : decision.phase === 'blocked' ? decision.on : undefined;
      const previous = [...runs].reverse().find(run => run.kind === 'advance' && run.threadId === threadId && run.status === 'continued');
      const attempts = latest.start.data.proactive && previous && previous.reason === reason ? previous.attempts ?? 0 : 0;
      const run: ProactiveRun = { id: randomUUID(), matterId, threadTitle: thread.title, kind: 'advance', threadId, turnId: latest.turnId, startedAt: now.toISOString(),
        status: 'discarded', phase: transient ? 'blocked' : decision.phase, on: decision.on, line: decision.line, reason, question: decision.question, wait: decision.wait };
      if (reason === 'transient' || reason === 'external') {
        run.attempts = attempts;
        const limit = reason === 'transient' ? 3 : 6;
        if (attempts >= limit) {
          run.status = 'asking';
          run.question = reason === 'transient' ? { text: `模型服务连续出错：${outcome.message ?? ''}`, options: ['再试一次', '先放着'] }
            : { text: `等了 6 次还在等 ${decision.wait!.what}`, options: ['继续等', '不等了'] };
        } else {
          run.status = 'waiting';
          const delay = reason === 'transient' ? Math.min(30, 2 ** attempts)
            : attempts && previous?.wakeAt ? Math.min(60, Math.max(2, (Date.parse(previous.wakeAt) - Date.parse(previous.startedAt)) / 60_000) * 2) : decision.checkMinutes ?? 10;
          run.wakeAt = new Date(now.getTime() + delay * 60_000).toISOString();
        }
      } else if (reason === 'done' || reason === 'user') run.status = 'asking';
      run.summary = reason === 'transient' ? '会在退避后续跑临时出错的线程' : reason === 'external' ? `会定时检查：${decision.wait!.what}`
        : reason === 'user' ? `会请你回答：${decision.question!.text}` : reason === 'done' ? `看起来做完了：${decision.line}` : '无法确定下一步，保留记录';
      if (this.settings().mode === 'observe' && run.status === 'waiting') run.status = 'observed';
      const currentTodo = todo ? this.store.getTodo(todo.id) : null;
      if (this.settings().mode === 'off' || this.quiet(new Date(now.getTime() + Date.now() - began)) || (todo && (!currentTodo || currentTodo.done || currentTodo.state === 'done' || currentTodo.proposed))
        || this.store.getThread(threadId)?.archivedAt || (todo && this.busy(todo.id)) || this.store.getThread(threadId)?.status === 'running' || this.latest(threadId)?.turnId !== latest.turnId) {
        run.status = 'discarded'; run.discardReason = '线程或主动行动设置已改变';
      }
      this.saveRun(run);
      if (run.status === 'asking') this.manager.notice(threadId, `需要你：${todo?.text ?? thread.title}`, false, true);
      return true;
    }
    return false;
  }

  private checkAdvance(run: ProactiveRun, now: Date, automatic: boolean): void {
    const current = this.get(run.id), thread = this.store.getThread(run.threadId), todo = this.store.getTodo(run.matterId);
    if (!['waiting', 'asking'].includes(current.status)) throw new HttpError(409, '这张卡片已失效，请刷新');
    if (run.precheck) {
      if (!todo || todo.done || todo.state === 'done' || todo.proposed) throw new HttpError(409, '事项已结束或尚未确认');
      return;
    }
    if (!thread || thread.archivedAt) throw new HttpError(409, '线程已归档或不存在');
    if (thread.status === 'running' || (run.matterId && this.busy(run.matterId))) throw new HttpError(409, '线程正在运行');
    if (this.latest(run.threadId)?.turnId !== run.turnId) throw new HttpError(409, '线程已继续');
    if (run.matterId && (!todo || todo.done || todo.state === 'done' || todo.proposed)) throw new HttpError(409, '事项已结束或尚未确认');
    if (automatic && (this.settings().mode !== 'auto' || this.quiet(now))) throw new HttpError(409, '主动行动已关闭或进入安静时段');
  }

  private async sendAdvance(run: ProactiveRun, now: Date, answer?: string): Promise<ProactiveRun> {
    const began = Date.now();
    const currentTime = () => new Date(now.getTime() + Date.now() - began);
    const automatic = answer === undefined;
    const discard = (error: unknown) => {
      const current = this.get(run.id);
      return ['waiting', 'asking'].includes(current.status) ? this.update(run.id, { status: 'discarded', discardReason: message(error) }) : current;
    };
    if (automatic) {
      try { this.checkAdvance(run, now, true); }
      catch (error) { return discard(error); }
      const budget = this.budget(now);
      if (budget.sends >= 20 || now.getTime() - (budget.sent[run.threadId] ?? 0) < 2 * 60_000) return run;
    } else this.checkAdvance(run, now, false);
    const text = answer !== undefined ? run.reason === 'transient' && answer === '再试一次' ? '继续' : `关于你上面的问题：${run.question?.text ?? run.line ?? ''} → ${answer}`
      : run.reason === 'transient' ? '继续' : `上一轮说在等：${run.wait?.what}。等到后：${run.wait?.then}。请检查条件是否已满足；好了就继续，没好请说明还要多久。不推送远端、不发消息、不合入，不做其他未经人确认的对外动作。`;
    try {
      await this.manager.startTurn(run.threadId, { text, ...(automatic ? { proactive: true } : {}) }, undefined, false, undefined, () => {
        this.checkAdvance(run, currentTime(), automatic);
        if (automatic) {
          const sendingAt = currentTime(), budget = this.budget(sendingAt);
          if (budget.sends >= 20 || sendingAt.getTime() - (budget.sent[run.threadId] ?? 0) < 2 * 60_000) throw new HttpError(409, '已达到自动发送限制');
          budget.sends++;
          budget.sent = Object.fromEntries(Object.entries(budget.sent).filter(([, at]) => sendingAt.getTime() - at < 2 * 60_000));
          budget.sent[run.threadId] = sendingAt.getTime();
          this.store.saveHubSetting('personalProactiveBudget', budget);
        }
        // Claim before turn.started invalidates all other cards on this thread.
        this.update(run.id, { status: automatic ? 'continued' : 'answered', endedAt: now.toISOString(), attempts: (run.attempts ?? 0) + (automatic ? 1 : 0) });
      });
      return this.get(run.id);
    } catch (error) {
      if (!automatic) throw error;
      return discard(error);
    }
  }

  async answer(id: string, answer: unknown): Promise<ProactiveRun> {
    const run = this.get(id);
    if (run.status !== 'asking' || !['user', 'transient', 'external'].includes(run.reason ?? '')) throw new HttpError(409, '只有等待回答的卡片可以回答');
    if (typeof answer !== 'string' || !answer.trim() || answer.length > 4000) throw new HttpError(400, '请提供 1–4000 字的回答');
    this.checkAdvance(run, new Date(), false);
    answer = answer.trim();
    if ((run.reason === 'transient' && answer === '先放着') || (run.reason === 'external' && answer === '不等了')) return this.dismiss(id);
    if (run.reason === 'external' && answer === '继续等') return this.update(id, { status: 'waiting', attempts: 0, wakeAt: new Date(Date.now() + 10 * 60_000).toISOString() });
    return this.sendAdvance(run, new Date(), answer as string);
  }

  closeMatter(id: string): ProactiveRun {
    const run = this.get(id);
    if (run.status !== 'asking' || run.reason !== 'done') throw new HttpError(409, '只有看起来做完的卡片可以关掉事项');
    this.checkAdvance(run, new Date(), false);
    this.personal.update(run.matterId, { done: true });
    return this.update(id, { status: 'closed', endedAt: new Date().toISOString() });
  }

  dismiss(id: string): ProactiveRun {
    const run = this.get(id);
    if (!['asking', 'waiting'].includes(run.status) && !(run.needsRepo && run.status === 'observed')) throw new HttpError(409, '当前卡片不能忽略');
    return this.update(id, { status: 'discarded', endedAt: new Date().toISOString() });
  }

  async allowRepo(id: string): Promise<ProactiveRun> {
    const run = this.get(id);
    if (!run.needsRepo || run.status !== 'observed' || !run.repo) throw new HttpError(409, '只有待授权的仓库卡片可以允许');
    if (this.acting.has('allow-repo')) throw new HttpError(409, '正在允许另一个仓库，请稍后重试');
    this.acting.add('allow-repo');
    try {
      await this.save({ repos: [...new Set([...this.settings().repos, run.repo])] });
      return this.update(id, { status: 'approved' });
    } finally { this.acting.delete('allow-repo'); }
  }

  private async start(todo: TodoRecord, kind: 'prepare' | 'sandbox', now: Date, repo?: string, variant?: ProactiveRun['variant']): Promise<void> {
    const began = Date.now();
    const run: ProactiveRun = { id: randomUUID(), matterId: todo.id, kind, variant, ...(repo ? { repo } : {}), threadId: '', startedAt: now.toISOString(), status: 'running' };
    this.saveRun(run);
    let createdThreadId = '';
    try {
      const settings = await this.personal.resolvedSettings();
      const adapter = settings.agent ? this.adapters.get(settings.agent) : undefined;
      if (!adapter) throw new HttpError(409, '没有可用的私人 agent，请先在私人设置选择');
      const artifacts = kind === 'prepare' ? await this.personal.artifacts(todo.id) : undefined;
      const sandbox = repo ? await this.sandbox.prepare(repo, todo.id) : undefined;
      if (sandbox) this.update(run.id, { branch: sandbox.branch });
      // Settings and threads may change while preparing git or discovering the agent.
      if (this.settings().mode !== 'auto' || repo && !this.settings().repos.includes(repo)) throw new HttpError(409, '主动行动已关闭或该仓库已移出允许列表');
      if (this.busy(todo.id) || this.atCapacity()) throw new HttpError(409, '有工作线程正在运行，本次主动行动未启动');
      const thread = await this.manager.createThread({ agent: settings.agent!, cwd: sandbox?.root ?? settings.directory,
        title: todo.text, permissionMode: adapter.capabilities.defaultPermissionMode, ...(settings.model ? { model: settings.model } : {}) });
      this.update(run.id, { threadId: thread.id });
      createdThreadId = thread.id;
      const note = this.store.todoNote(todo.id).text || '尚未写入。';
      let prompt = kind === 'prepare'
        ? `由 Calli 发起的提前准备。\n事项：${todo.text}\nID：${todo.id}\n材料目录：${artifacts}\n接续说明：\n${note}\n\n按接续说明提前备好回复草稿、会前提纲或其他值得准备的材料，只写材料目录下的文件。这个目录中的 artifacts 是本轮唯一允许直接写入的事项材料，matter.md 仍只由 Calli 管理。\n先用 calli todo show ${todo.id} 读取最新 rev；产出写入 ${artifacts}/<名称>.md，再用 calli todo update ${todo.id} --rev N --note-file FILE 在接续说明记一句「已备好 X」，保留原有说明。\n不发消息、不推送、不删除、不改外部仓库，不做任何对外动作。不要自行 git 提交，Calli 会为材料留底。没有值得准备的材料就安静结束，不凑内容、不通知。结束回复只写材料摘要。`
        : `由 Calli 发起的沙盒动手。\n事项：${todo.text}\nID：${todo.id}\n接续说明：\n${note}\n\n这是独立 worktree 沙盒；只按接续说明推进已由人开始的工作。每个提交必须带 git trailer：\nCalli-Proactive: ${todo.id}\n\n跑该仓库测试；只改当前沙盒，不动主工作区、不推送远端、不发消息、不做其他对外动作。只在此沙盒提交改动。不要查看其他 agent 会话，也不要修改它们的目录或配置。结束回复按「## 改动摘要」「## 测试结果」两段写清实际改动、执行的测试和结果；没有运行测试也要写清原因。`;
      if (variant === 'plan') prompt += `\n本轮是笼统事项拆解：把事项拆成 3–7 步，写出第一步的具体方案（要改哪里、怎么验证、要问人什么），写入 ${artifacts}/plan.md。可以只读查看相关仓库，不改任何仓库、不对外。用 calli todo update 在接续说明记一句「已备好 拆解与第一步方案」。`;
      await this.manager.startTurn(thread.id, { text: prompt, scheduled: true, proactive: true }, undefined, false, undefined, turnId => {
        if (this.busy(todo.id) || this.atCapacity()) throw new HttpError(409, '有工作线程正在运行，本次主动行动未启动');
        if (this.settings().mode !== 'auto' || this.quiet(new Date(now.getTime() + Date.now() - began)) || repo && !this.settings().repos.includes(repo)) throw new HttpError(409, '主动行动设置或安静时段已改变，本次未启动');
        this.personal.linkWorkThread(todo.id, thread.id, todo.rev);
        this.turns.set(run.id, turnId);
      });
    } catch (error) {
      this.turns.delete(run.id);
      // A thread whose turn never started is an empty shell on the matter; remove it, as delegate does.
      if (createdThreadId && !this.store.listEventsOfType(createdThreadId, 'turn.started').length) await this.manager.deleteThread(createdThreadId).catch(() => undefined);
      this.update(run.id, { status: 'failed', endedAt: new Date().toISOString(), summary: message(error) });
    }
  }

  private onEvent(event: StoredEvent): void {
    if (event.type === 'turn.started') this.invalidate(event.threadId);
    const outcome = turnOutcome(event);
    if (!outcome || outcome.imported || outcome.fromTerminal || !event.turnId) return;
    const run = this.runs().find(run => run.status === 'running' && run.threadId === event.threadId && this.turns.get(run.id) === event.turnId);
    if (!run || this.finishing.has(run.id)) return;
    // Reserve synchronously so a tick cannot mistake an asynchronously collected result for a crash.
    const finish = Promise.resolve().then(() => this.finish(run, event)).catch(error => {
      this.update(run.id, { status: 'failed', endedAt: event.ts, summary: message(error) });
    }).finally(() => { this.finishing.delete(run.id); this.turns.delete(run.id); });
    this.finishing.set(run.id, finish);
  }

  private async finish(run: ProactiveRun, event: StoredEvent): Promise<void> {
    const outcome = turnOutcome(event)!;
    if (outcome.outcome !== 'ok') {
      this.update(run.id, { status: 'failed', endedAt: event.ts, summary: outcome.message ?? '主动行动已停止，尚未备好可审阅的结果' });
      return;
    }
    const answer = turnAnswer(this.store.voiceAnswerEvents(run.threadId, event.turnId!), event.turnId!).trim();
    if (run.kind === 'prepare' && this.store.getThread(run.threadId)?.cwd !== this.personal.directory()) {
      throw new HttpError(409, '私人目录已改变，材料仍在原工作线程目录中，尚未自动提交');
    }
    const files = run.kind === 'prepare' ? await this.personal.commitArtifacts(run.matterId) : (await this.sandbox.changes(run.repo!, run.matterId)).files;
    const testReport = run.kind === 'sandbox' ? answer.match(/(?:^|\n)## 测试结果\s*\n([\s\S]*?)(?=\n## |$)/)?.[1]?.trim() || '工作线程未报告测试结果' : undefined;
    const summary = answer.replace(/(?:^|\n)## 测试结果[\s\S]*$/, '').replace(/^## 改动摘要\s*/, '').trim().slice(0, 4000);
    const extras = testReport ? { testReport: testReport.slice(0, 4000) } : {};
    // A silent trial: failing tests or nothing reviewable ends quietly — an audit record, no card, no notice.
    if (run.kind === 'sandbox' && (!files.length || (testReport && TEST_FAILED.test(testReport)))) {
      this.update(run.id, { status: 'discarded', endedAt: event.ts, files, summary, discardReason: files.length ? '测试未通过' : '没有可审阅的改动', ...extras });
      return;
    }
    // A quiet no-op leaves an audit record, but never a misleading ready card or notification.
    this.update(run.id, { status: files.length ? 'ready' : 'discarded', endedAt: event.ts, files, summary, ...extras });
    if (files.length) {
      const title = this.store.getTodo(run.matterId)?.text ?? run.matterId;
      this.manager.notice(run.threadId, `${run.kind === 'prepare' ? '已备好材料' : '已在沙盒做好'}：${title}`, false, true);
    }
  }

  /** Waits for file/git collection before closing the store; no extra polling loop or timer. */
  async flush(): Promise<void> { await Promise.all([...this.finishing.values()]); }

  async diff(id: string): Promise<SandboxChanges> {
    const run = this.get(id);
    if (run.kind !== 'sandbox' || !run.repo) throw new HttpError(400, '这次行动是准备材料，没有代码沙盒');
    if (run.status === 'discarded') throw new HttpError(409, '沙盒已丢弃，无法再读取改动');
    try { return await this.sandbox.changes(run.repo, run.matterId); }
    catch (error) { throw new HttpError(409, message(error)); }
  }

  async act(id: string, action: 'merge' | 'discard' | 'revert'): Promise<ProactiveRun> {
    const run = this.get(id);
    if (run.kind !== 'sandbox' || !run.repo) throw new HttpError(400, '这次行动是准备材料，没有代码沙盒');
    const allowed = action === 'revert' ? run.status === 'merged' : action === 'discard' ? ['ready', 'failed'].includes(run.status) : run.status === 'ready';
    if (!allowed) throw new HttpError(409, action === 'revert' ? '只有已合入的主动行动才能撤回' : '当前状态不能执行此操作，请刷新后查看');
    if (this.store.getThread(run.threadId)?.status === 'running') throw new HttpError(409, '沙盒工作线程仍在运行，请等它结束后再操作');
    if (this.acting.has(run.repo)) throw new HttpError(409, '这个仓库正在处理另一项操作，请稍后重试');
    if (!isWithinWorkdirs(run.repo, this.manager.allowedWorkdirs)) throw new HttpError(403, '仓库已不在允许的工作目录内');
    this.acting.add(run.repo);
    try {
      await this.sandbox[action](run.repo, run.matterId);
      return this.update(run.id, { status: action === 'merge' ? 'merged' : action === 'discard' ? 'discarded' : 'reverted' });
    } catch (error) { throw new HttpError(409, message(error)); }
    finally { this.acting.delete(run.repo); }
  }
}
