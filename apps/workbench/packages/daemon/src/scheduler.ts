import type { PushService } from './push.js';
import { randomUUID } from 'node:crypto';
import { existsSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import type { AgentAdapter } from './adapters/types.js';
import { countCronFires, cronMatches, describeCron, isValidTimezone, nextCron, parseCron, zonedParts, type CronFields } from './cron.js';
import type { StoredEvent } from './events.js';
import { HttpError } from './errors.js';
import { turnOutcome } from './lifecycle.js';
import type { ScheduleRecord, ScheduleRunRecord, Store, ThreadRecord } from './store.js';
import type { ThreadManager } from './threads.js';
import { isWithinWorkdirs } from './workdirs.js';

/** A schedule as the API returns it: the stored record plus what the page shows beside it. */
export interface ScheduleView extends ScheduleRecord {
  /** The cron expression in words. Empty when uncommon: the page shows the raw expression. */
  description: string;
  /** When this runs next. Null when disabled, or when the expression never matches again. */
  nextRunAt: string | null;
  /** The newest run, whatever it was. Null until the first one. */
  lastRun: ScheduleRunRecord | null;
  /** Why this schedule cannot run as-is: a broken expression, a gone agent, a gone thread. */
  problem: string | null;
}

/** How a fire ended, remembered briefly in case the turn finishes before its run row is written. */
interface RunOutcome {
  status: ScheduleRunRecord['status'];
  message: string | null;
}

const TICK_MS = 30_000;
const FOLLOW_UP_BACKOFF_MS = [60_000, 5 * 60_000, 30 * 60_000, 60 * 60_000];
const AUTO_ARCHIVE_SWEEP_MS = 60 * 60_000;
/** How long a turn's ending is remembered without its run row, for the race at fire time. */
const REMEMBERED_TURNS = 128;
const MAX_PROMPT = 8000;
const MAX_NAME = 60;

const pad = (n: number) => String(n).padStart(2, '0');
/** The local date, which is what `{{date}}` in a prompt and the daemon's cron both speak. */
const localDate = (at: Date, tz?: string | null) => {
  const p = zonedParts(at, tz);
  return `${p.year}-${pad(p.month)}-${pad(p.date)}`;
};
/** Local minute identity: two fires in one minute are one fire, whatever the clock does between ticks. */
const minuteKey = (at: Date, tz?: string | null) => {
  const p = zonedParts(at, tz);
  return `${p.year}-${pad(p.month)}-${pad(p.date)}T${pad(p.hours)}:${pad(p.minutes)}`;
};

type ChangeListener = () => void;

/**
 * Runs recurring tasks: on each tick, every enabled schedule whose cron matches the minute gets
 * its prompt sent by its agent in its directory, as a thread like any other. Skips while a
 * previous run is still going, records what was missed while the daemon was down, and closes out
 * runs a restart cut short.
 */
export class Scheduler {
  private unsubscribe?: () => void;
  private following = false;
  private reviewing = false;
  private reviewFailure: { attempts: number; retryAt: number } | null = null;
  private readonly followUpFailures = new Map<string, { attempts: number; retryAt: number }>();
  private stopped = false;
  private readonly quietArchives = new Set<string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastAutoArchiveAt = 0;
  private lastAutoArchiveDays: number | null = null;
  private readonly listeners = new Set<ChangeListener>();
  /** Turn endings that arrived before (or without) a run row, keyed by turn id. */
  private readonly outcomes = new Map<string, RunOutcome>();

  constructor(
    private readonly store: Store,
    private readonly manager: ThreadManager,
    private readonly adapters: Map<string, AgentAdapter>,
    private readonly push?: PushService,
  ) {}

  /** Page-change notifications, for the list stream. */
  subscribe(listener: ChangeListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const l of this.listeners) l();
  }

  /** Starts ticking. First closes out what a restart or downtime left behind. */
  start(): void {
    this.stopped = false;
    this.recover();
    this.unsubscribe = this.manager.subscribe((ev) => this.onTurnEvent(ev));
    void this.tick();
    this.timer = setInterval(() => void this.tick(), TICK_MS);
  }

  dispose(): void {
    this.stopped = true;
    this.unsubscribe?.();
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** A turn's ending settles its run: done, error, or skipped when it was stopped. */
  private onTurnEvent(ev: StoredEvent): void {
    if (this.manager.personal.review.onEvent(ev)) {
      if (ev.data.quiet === true) { this.quietArchives.add(ev.threadId); this.archiveQuiet(ev.threadId); }
      return;
    }
    if (ev.type === 'thread.updated') {
      if (this.quietArchives.has(ev.threadId)) this.archiveQuiet(ev.threadId);
      void this.followUps();
    }
    const end = turnOutcome(ev);
    if (!end || !ev.turnId) return;
    const outcome: RunOutcome =
      end.outcome === 'error' || end.outcome === 'interrupted'
        ? { status: 'error', message: end.message }
        : end.outcome === 'cancelled'
          ? { status: 'skipped', message: '这一轮被打断' }
          : { status: ev.data.quiet === true ? 'quiet' : 'done', message: null };
    const run = this.store.findScheduleRunByTurn(ev.turnId);
    if (run) {
      const paused = this.store.transaction(() => {
        this.store.updateScheduleRun(run.id, { status: outcome.status, finishedAt: new Date().toISOString(), ...(outcome.message ? { message: outcome.message } : {}) });
        return run.todoId ? this.store.finishTodoFollowUp(run.id) : false;
      });
      if (run.todoId) {
        this.manager.broadcastTodo();
        if (paused) {
          const recent = this.store.todoThreadIds(run.todoId).find(id => this.store.getThread(id));
          if (recent) this.manager.notice(recent, '连续 3 次跟进没有进展，已停止自动跟进');
        }
      }
      if (outcome.status === 'quiet' && run.threadId && (run.newThread || run.scheduleId && this.store.getSchedule(run.scheduleId)?.mode === 'new')) {
        this.quietArchives.add(run.threadId); this.archiveQuiet(run.threadId);
      }
      this.changed();
      return;
    }
    // The turn finished before its run row was written; remembered for when the row appears.
    this.outcomes.set(ev.turnId, outcome);
    if (this.outcomes.size > REMEMBERED_TURNS) this.outcomes.delete(this.outcomes.keys().next().value as string);
  }

  private recover(): void {
    this.manager.personal.review.recover();
    this.manager.personal.proactive.recover();
    const now = new Date();
    for (const run of this.store.runningScheduleRuns()) {
      this.store.updateScheduleRun(run.id, { status: 'error', finishedAt: now.toISOString(), message: 'daemon 重启时被打断' });
    }
    const toCatchUp: ScheduleRecord[] = [];
    for (const s of this.store.listSchedules()) {
      if (!s.enabled || !s.lastFiredAt) continue;
      let fields: CronFields;
      try {
        fields = parseCron(s.cron);
      } catch {
        continue;
      }
      const missed = countCronFires(fields, new Date(s.lastFiredAt), now, s.timezone);
      if (missed > 0) {
        this.store.insertScheduleRun({
          id: randomUUID(),
          scheduleId: s.id,
          status: 'missed',
          threadId: null,
          turnId: null,
          startedAt: now.toISOString(),
          finishedAt: now.toISOString(),
          message: missed < 0 ? '停机期间错过了多次运行' : `停机期间错过了 ${missed} 次运行`,
        });
        if (s.catchUp) {
          toCatchUp.push(s);
        }
      }
      // Whether or not anything was missed, downtime has been accounted for up to now.
      this.store.updateSchedule(s.id, { lastFiredAt: now.toISOString() });
    }
    for (const s of toCatchUp) {
      void this.fire(s, now);
    }
  }

  /** One pass of the clock. Awaiting fires is fine: a fire only starts a turn, it never waits for one. */
  async tick(now = new Date()): Promise<void> {
    for (const s of this.store.listSchedules()) {
      if (!s.enabled) continue;
      let fields: CronFields;
      try {
        fields = parseCron(s.cron);
      } catch {
        continue;
      }
      if (!cronMatches(fields, now, s.timezone)) continue;
      const key = minuteKey(now, s.timezone);
      if (s.lastFiredAt && minuteKey(new Date(s.lastFiredAt), s.timezone) === key) continue;
      this.store.updateSchedule(s.id, { lastFiredAt: now.toISOString() });
      await this.fire(s, now);
    }
    await this.followUps();
    await this.reviews();
    await this.autoArchive(now);
    await this.wakeSnoozed(now);
    this.manager.briefs.tick(now, this.push);
    void this.manager.personal.prep.tick(now).catch(error => console.error('meeting prep failed:', error));
    await this.manager.personal.proactive.tick(now);
    this.sweep();
    this.changed();
  }

  private async autoArchive(now: Date): Promise<void> {
    const days = this.manager.generalSettings().autoArchiveDays;
    if (days === null) { this.lastAutoArchiveDays = null; return; }
    const due = this.lastAutoArchiveDays !== days || now.getTime() - this.lastAutoArchiveAt >= AUTO_ARCHIVE_SWEEP_MS;
    if (!due) return;
    this.lastAutoArchiveDays = days;
    this.lastAutoArchiveAt = now.getTime();
    await this.manager.autoArchiveOldThreads(now);
  }

  /** The threads whose snooze ran out come back: into the list's attention and onto the phone. */
  private async wakeSnoozed(now: Date): Promise<void> {
    for (const thread of await this.manager.wakeSnoozedThreads(now)) {
      this.push?.snoozeWake(thread.id, thread.snoozedUntil ?? '');
    }
  }

  /** Reviews own their schedule in hub settings, while their conversations remain ordinary threads. */
  async reviews(): Promise<void> {
    if (this.stopped || this.reviewing || this.reviewFailure && Date.now() < this.reviewFailure.retryAt) return;
    const review = this.manager.personal.review, expected = review.due();
    if (!expected) return;
    this.reviewing = true;
    let threadId: string | undefined;
    try {
      const settings = await this.manager.personal.resolvedSettings();
      const adapter = settings.agent ? this.adapters.get(settings.agent) : undefined;
      if (!adapter || !settings.agent) throw new HttpError(503, 'personal agent unavailable');
      const thread = await this.manager.createThread({ agent: settings.agent, cwd: settings.directory,
        title: `盘点 · ${localDate(new Date())}`, permissionMode: adapter.capabilities.defaultPermissionMode,
        ...(settings.model ? { model: settings.model } : {}) });
      threadId = thread.id;
      await this.manager.startTurn(thread.id, { text: review.prompt(), scheduled: true, review: true }, undefined, false, undefined, turnId => {
        if (this.stopped) throw new HttpError(409, 'scheduler stopped');
        review.started(expected, thread.id, turnId);
      });
      this.reviewFailure = null;
    } catch (error) {
      if (threadId && this.store.getThread(threadId)?.status !== 'running' && !this.store.listEventsOfType(threadId, 'turn.started').length) await this.manager.deleteThread(threadId).catch(() => undefined);
      if (this.stopped || error instanceof HttpError && error.status === 409) return;
      const attempts = (this.reviewFailure?.attempts ?? 0) + 1;
      this.reviewFailure = { attempts, retryAt: Date.now() + FOLLOW_UP_BACKOFF_MS[Math.min(attempts - 1, FOLLOW_UP_BACKOFF_MS.length - 1)] };
      if (attempts === 1) {
        const recent = review.failureThread();
        if (recent) this.manager.notice(recent, '盘点没能启动：无法启动 agent，稍后自动重试');
        else console.warn('[review] unable to start agent; will retry');
      }
    } finally { this.reviewing = false; }
  }

  private archiveQuiet(threadId: string) {
    const thread = this.store.getThread(threadId);
    if (!thread) { this.quietArchives.delete(threadId); return; }
    if (thread.status === 'running') return;
    this.quietArchives.delete(threadId);
    // A notice sent during the turn is still something the person should see.
    if (!thread.unread) void this.manager.updateThread(threadId, { archived: true }).catch(() => undefined);
  }

  /** Due one-shot follow-ups share turn execution and run history with recurring schedules. */
  async followUps(): Promise<void> {
    if (this.following || this.stopped) return;
    this.following = true;
    try {
      for (const todo of this.store.listTodos()) {
        if (this.stopped) return;
        if (todo.done || todo.proposed || todo.followUpPaused || !todo.followUpAt || Date.parse(todo.followUpAt) > Date.now()) continue;
        const failure = this.followUpFailures.get(todo.id);
        if (failure && Date.now() < failure.retryAt) continue;
        let threadId: string | undefined;
        let created = false;
        try {
          const linked = this.store.todoThreadIds(todo.id).map(id => this.store.getThread(id)).find(t => t && this.manager.personal.allowed(t.id));
          if (linked?.status === 'running') continue;
          threadId = linked && !linked.archivedAt ? linked.id : undefined;
          if (!threadId) {
            const settings = await this.manager.personal.resolvedSettings();
            const agent = settings.agent;
            if (!agent || !this.adapters.has(agent)) throw new HttpError(503, 'personal agent unavailable');
            const thread = await this.manager.createThread({ agent, cwd: settings.directory, title: todo.text,
              permissionMode: this.adapters.get(agent)?.capabilities.defaultPermissionMode,
              ...(settings.model ? { model: settings.model } : {}) });
            threadId = thread.id; created = true;
          }
          const note = this.store.todoNote(todo.id);
          const text = `由 LoopX 发起的事项跟进。\n事项：${todo.text}\nID：${todo.id}；状态：${todo.state}；归属：${todo.owner}\n${todo.waitingOn ? `等待：${todo.waitingOn}\n` : ''}接续说明（${note.updatedAt ?? '尚未写入'}）：\n${note.text}\n\n处理完更新说明；没有进展就设下次跟进时间并 loochat quiet。事项操作见 loochat todo --help。`;
          const runId = randomUUID();
          await this.manager.startTurn(threadId, { text, scheduled: true }, undefined, false, undefined, turnId => {
            if (this.stopped) throw new HttpError(409, 'scheduler stopped');
            this.store.checkTodoRev(todo.id, todo.rev);
            this.store.updateTodo(todo.id, { followUpAt: null }, todo.rev);
            this.store.linkTodoThread(todo.id, threadId!);
            this.store.insertScheduleRun({ id: runId, scheduleId: null, todoId: todo.id, newThread: created,
              status: 'running', threadId: threadId!, turnId, startedAt: new Date().toISOString(), finishedAt: null, message: null }, note.text);
          });
          this.followUpFailures.delete(todo.id);
          this.manager.broadcastTodo();
        } catch (e) {
          // Only discard threads that never began a turn; keep all actual conversation history.
          if (created && threadId && this.store.getThread(threadId)?.status !== 'running' &&
              this.store.listEventsOfType(threadId, 'turn.started').length === 0) {
            await this.manager.deleteThread(threadId).catch(() => undefined);
          }
          // Busy or concurrently edited items remain due; the next tick reads their fresh state.
          if (this.stopped || e instanceof HttpError && e.status === 409) continue;
          const attempts = (failure?.attempts ?? 0) + 1;
          this.followUpFailures.set(todo.id, { attempts, retryAt: Date.now() + FOLLOW_UP_BACKOFF_MS[Math.min(attempts - 1, FOLLOW_UP_BACKOFF_MS.length - 1)] });
          if (!failure) {
            const recent = this.store.todoThreadIds(todo.id).find(id => this.store.getThread(id));
            if (recent) this.manager.notice(recent, '跟进没能启动：无法启动 agent，稍后自动重试');
          }
          console.warn('[follow-up] could not start a due item; retry delayed');
        }
      }
    } finally { this.following = false; }
  }

  /** Closes out runs whose thread was deleted under them: no event will ever end those turns. */
  private sweep(): void {
    for (const run of this.store.runningScheduleRuns()) {
      if (run.threadId && !this.store.getThread(run.threadId)) {
        this.store.updateScheduleRun(run.id, { status: 'error', finishedAt: new Date().toISOString(), message: '对话已删除' });
      }
    }
  }

  /**
   * Sends the schedule's prompt: in its bound thread (`same`), or a fresh one (`new`). A previous
   * run still going means this one is skipped, not stacked — nothing piles up unattended.
   */
  private async fire(s: ScheduleRecord, at: Date): Promise<{ status: 'running' | 'skipped' | 'error'; message?: string }> {
    const now = at.toISOString();
    const record = (status: 'skipped' | 'error', message: string, threadId: string | null = null): { status: 'skipped' | 'error'; message: string } => {
      this.store.insertScheduleRun({ id: randomUUID(), scheduleId: s.id, status, threadId, turnId: null, startedAt: now, finishedAt: now, message });
      return { status, message };
    };
    if (this.store.runningScheduleRun(s.id)) return record('skipped', '上一轮还没有结束，这次跳过');
    let threadId: string;
    const bound = s.mode === 'same' && s.threadId ? this.store.getThread(s.threadId) : null;
    if (s.mode === 'same' && s.threadId && !bound) return record('error', '绑定的对话已删除', s.threadId);
    if (bound?.archivedAt) return record('error', '绑定的对话已归档，恢复它或换一个', bound.id);
    if (bound) {
      threadId = bound.id;
    } else {
      try {
        const thread = await this.manager.createThread({
          agent: s.agent,
          cwd: s.cwd,
          title: `${s.name} · ${localDate(at, s.timezone)}`,
          permissionMode: s.permissionMode,
          schedule: { id: s.id, name: s.name },
        });
        threadId = thread.id;
        if (s.mode === 'same') this.store.updateSchedule(s.id, { threadId: thread.id });
      } catch (err) {
        return record('error', err instanceof Error ? err.message : String(err));
      }
    }
    const text = s.prompt.replace(/\{\{date\}\}/g, localDate(at, s.timezone));
    try {
      const started = await this.manager.startTurn(threadId, { text, scheduled: true });
      if (!('turnId' in started)) return record('skipped', '对话里已有一轮在运行', threadId);
      const run = this.store.insertScheduleRun({
        id: randomUUID(),
        scheduleId: s.id,
        status: 'running',
        threadId,
        turnId: started.turnId,
        startedAt: now,
        finishedAt: null,
        message: null,
      });
      const outcome = this.outcomes.get(started.turnId);
      if (outcome) {
        this.outcomes.delete(started.turnId);
        if (outcome.status === 'quiet' && s.mode === 'new') { this.quietArchives.add(threadId); this.archiveQuiet(threadId); }
        this.store.updateScheduleRun(run.id, { status: outcome.status, finishedAt: new Date().toISOString(), ...(outcome.message ? { message: outcome.message } : {}) });
      }
      return { status: 'running' };
    } catch (err) {
      if (err instanceof HttpError && err.status === 409) return record('skipped', '对话里已有一轮在运行', threadId);
      return record('error', err instanceof Error ? err.message : String(err), threadId);
    }
  }

  /** Fires now, whatever the clock says and whether the schedule is enabled. */
  async runNow(id: string): Promise<{ status: string; message?: string }> {
    const s = this.schedule(id);
    const out = await this.fire(s, new Date());
    this.changed();
    return out;
  }

  list(): ScheduleView[] {
    const latest = this.store.latestScheduleRuns();
    return this.store.listSchedules().map((s) => {
      let fields: CronFields | null = null;
      let problem: string | null = null;
      try {
        fields = parseCron(s.cron);
      } catch (err) {
        problem = err instanceof Error ? err.message : 'cron 表达式无效';
      }
      if (s.timezone && !isValidTimezone(s.timezone)) problem ??= '时区无效';
      if (!this.adapters.has(s.agent)) problem ??= 'agent 实例已不存在';
      if (s.mode === 'same' && s.threadId) {
        const bound = this.store.getThread(s.threadId);
        if (!bound) problem ??= '绑定的对话已删除';
        else if (bound.archivedAt) problem ??= '绑定的对话已归档';
      }
      const next = fields && s.enabled ? nextCron(fields, new Date(), s.timezone) : null;
      return {
        ...s,
        description: fields ? describeCron(fields) : '',
        nextRunAt: next ? next.toISOString() : null,
        lastRun: latest.get(s.id) ?? null,
        problem,
      };
    });
  }

  runs(id: string, limit = 20): ScheduleRunRecord[] {
    this.schedule(id);
    return this.store.listScheduleRuns(id, Math.min(Math.max(limit, 1), 100));
  }

  /** Validates a cron expression and says what it means: for the form's live preview. */
  preview(cronInput: unknown, tzInput?: unknown): { description: string; fires: string[] } {
    const fields = this.fields(cronInput);
    const tz = this.timezone(tzInput);
    const fires: string[] = [];
    let at = new Date();
    for (let i = 0; i < 3; i++) {
      const next = nextCron(fields, at, tz);
      if (!next) break;
      fires.push(next.toISOString());
      at = next;
    }
    return { description: describeCron(fields), fires };
  }

  async create(input: Record<string, unknown>): Promise<ScheduleView> {
    const now = new Date().toISOString();
    const id = randomUUID();
    const fields = this.fields(input.cron);
    const agent = this.agent(input.agent);
    const prompt = this.prompt(input.prompt);
    const name = this.name(input.name, prompt);
    const timezone = this.timezone(input.timezone);
    const catchUp = input.catchUp === true;
    const mode = input.mode === 'same' ? 'same' : input.mode === 'new' ? 'new' : undefined;
    if (!mode) throw new HttpError(400, "mode must be 'new' or 'same'");
    const thread = this.boundThread(input.threadId, mode);
    const record: ScheduleRecord = {
      id,
      name,
      agent: thread ? thread.agent : agent,
      cwd: thread ? thread.cwd : this.cwd(input.cwd ?? this.manager.personal.settings().directory),
      prompt,
      cron: (input.cron as string).trim(),
      mode,
      threadId: thread?.id ?? null,
      permissionMode: this.permission(agent, input.permissionMode),
      enabled: input.enabled !== false,
      timezone,
      catchUp,
      lastFiredAt: null,
      createdAt: now,
      updatedAt: now,
    };
    this.store.createSchedule(record);
    this.changed();
    return this.view(id);
  }

  async update(id: string, input: Record<string, unknown>): Promise<ScheduleView> {
    const current = this.schedule(id);
    const patch: Partial<ScheduleRecord> = {};
    if (input.name !== undefined || input.prompt !== undefined) {
      const prompt = input.prompt !== undefined ? this.prompt(input.prompt) : current.prompt;
      patch.prompt = prompt;
      patch.name = input.name !== undefined ? this.name(input.name, prompt) : current.name;
    }
    let agent = current.agent;
    if (input.agent !== undefined) agent = patch.agent = this.agent(input.agent);
    if (input.cwd !== undefined && !('threadId' in input && input.threadId !== undefined)) patch.cwd = this.cwd(input.cwd);
    if (input.cron !== undefined) {
      this.fields(input.cron);
      patch.cron = (input.cron as string).trim();
    }
    if (input.timezone !== undefined) patch.timezone = this.timezone(input.timezone);
    if (input.catchUp !== undefined) patch.catchUp = input.catchUp === true;
    if (input.mode !== undefined) {
      if (input.mode !== 'new' && input.mode !== 'same') throw new HttpError(400, "mode must be 'new' or 'same'");
      patch.mode = input.mode;
      // Going fresh again lets go of the thread; the old one keeps its badge and history.
      if (input.mode === 'new') patch.threadId = null;
    }
    if (input.threadId !== undefined) {
      const thread = this.boundThread(input.threadId, input.mode !== undefined ? (input.mode as string) : current.mode);
      if (thread) {
        patch.threadId = thread.id;
        patch.agent = agent = thread.agent;
        patch.cwd = thread.cwd;
      } else if (input.threadId === null) {
        patch.threadId = null;
      }
    }
    if (input.permissionMode !== undefined) patch.permissionMode = this.permission(agent, input.permissionMode);
    if (input.enabled !== undefined) patch.enabled = input.enabled === true;
    this.store.updateSchedule(id, patch);
    this.changed();
    return this.view(id);
  }

  remove(id: string): void {
    this.schedule(id);
    this.store.deleteSchedule(id);
    this.changed();
  }

  // ---------- validation, shared by create and update ----------

  private schedule(id: string): ScheduleRecord {
    const s = this.store.getSchedule(id);
    if (!s) throw new HttpError(404, 'schedule not found');
    return s;
  }

  private view(id: string): ScheduleView {
    const s = this.list().find((x) => x.id === id);
    if (!s) throw new HttpError(500, 'schedule could not be read back');
    return s;
  }

  private fields(cronInput: unknown): CronFields {
    if (typeof cronInput !== 'string' || !cronInput.trim()) throw new HttpError(400, 'cron is required');
    try {
      return parseCron(cronInput);
    } catch (err) {
      throw new HttpError(400, err instanceof Error ? err.message : 'cron expression is invalid');
    }
  }

  private timezone(tzInput: unknown): string | null {
    if (tzInput === undefined || tzInput === null || tzInput === '') return null;
    if (typeof tzInput !== 'string') throw new HttpError(400, 'timezone must be a string');
    const trimmed = tzInput.trim();
    if (!trimmed) return null;
    if (!isValidTimezone(trimmed)) throw new HttpError(400, `invalid timezone: ${trimmed}`);
    return trimmed;
  }

  private agent(agentInput: unknown): string {
    if (typeof agentInput !== 'string' || !this.adapters.has(agentInput)) throw new HttpError(400, `unknown agent: ${String(agentInput)}`);
    return agentInput;
  }

  private prompt(promptInput: unknown): string {
    if (typeof promptInput !== 'string' || !promptInput.trim()) throw new HttpError(400, 'prompt is empty');
    if (promptInput.length > MAX_PROMPT) throw new HttpError(400, `prompt is longer than ${MAX_PROMPT} characters`);
    return promptInput;
  }

  private name(nameInput: unknown, prompt: string): string {
    const value = typeof nameInput === 'string' ? nameInput.trim() : '';
    const name = value || prompt.split('\n')[0].slice(0, 24);
    if (!name) throw new HttpError(400, 'a schedule needs a name or a prompt to take it from');
    if (name.length > MAX_NAME) throw new HttpError(400, `name is longer than ${MAX_NAME} characters`);
    return name;
  }

  private cwd(cwdInput: unknown): string {
    if (typeof cwdInput !== 'string' || !cwdInput.trim()) throw new HttpError(400, 'cwd is required');
    const cwd = resolve(cwdInput);
    if (!isWithinWorkdirs(cwd, this.manager.dirs.all())) throw new HttpError(400, 'cwd is not in the allowed workdirs');
    if (!existsSync(cwd) || !statSync(cwd).isDirectory()) throw new HttpError(400, 'cwd does not exist');
    return cwd;
  }

  /** A thread to continue in (`same` mode). Null when none was given; a string that names nothing is an error. */
  private boundThread(threadInput: unknown, mode: string): ThreadRecord | null {
    if (threadInput === undefined || threadInput === null || threadInput === '') return null;
    if (mode !== 'same') throw new HttpError(400, 'a thread can only be bound in "same" mode');
    if (typeof threadInput !== 'string') throw new HttpError(400, 'threadId must be a string');
    const thread = this.store.getThread(threadInput);
    if (!thread) throw new HttpError(404, 'thread not found');
    if (thread.archivedAt) throw new HttpError(400, 'that thread is archived');
    return thread;
  }

  private permission(agent: string, modeInput: unknown): string {
    const modes = this.adapters.get(agent)?.capabilities.permissionModes ?? [];
    if (modes.length === 0) return 'auto_edit';
    if (modeInput === undefined || modeInput === null || modeInput === '') {
      return this.adapters.get(agent)?.capabilities.defaultPermissionMode ?? 'auto_edit';
    }
    if (typeof modeInput !== 'string' || !modes.includes(modeInput as never)) {
      throw new HttpError(400, `permission mode must be one of: ${modes.join(', ')}`);
    }
    return modeInput;
  }
}
