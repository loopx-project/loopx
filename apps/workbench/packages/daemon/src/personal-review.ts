import { HttpError } from './errors.js';
import type { StoredEvent } from './events.js';
import { turnOrigin, turnOutcome } from './lifecycle.js';
import { PROACTIVE_RUNS_KEY, type ProactiveRun } from './proactive.js';
import type { Store } from './store.js';

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const KEY = 'personalReview';
const LONG_RUN = 2 * 60 * MINUTE;
const IDLE_ROUNDS = 3;
const REPEATED_FAILURES = 2;
const localDay = (at: Date) => `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`;
export interface ReviewState {
  reviewEnabled: boolean;
  nextReviewAt: string | null;
  quietHours: { start: string; end: string };
  day: string;
  runsToday: number;
  unansweredReviews: number;
  enabledByThreadId: string | null;
  lastThreadId: string | null;
  lastFinishedAt: string | null;
  active: { threadId: string; turnId: string; notified: boolean } | null;
}

export type WatchFact =
  | { kind: 'long-running'; threadId: string; since: string }
  | { kind: 'note-unchanged'; rounds: number; noteUpdatedAt: string | null; sinceRoundEndedAt: string }
  | { kind: 'repeated-failure'; repo: string; failures: number };
export interface WatchInput {
  /** Linked work threads; `since` is when the running turn started. */
  threads: ReadonlyArray<{ id: string; status: string; since: string | null }>;
  noteUpdatedAt: string | null;
  /** When the work threads' turns ended, in any order. */
  turnEnds: readonly string[];
  /** This matter's runs, oldest first. */
  runs: ReadonlyArray<Pick<ProactiveRun, 'repo' | 'status'>>;
}

/** Facts only; whether any of them means idling or drifting is the review agent's call. */
export function watchFacts(input: WatchInput, now: number): WatchFact[] {
  const facts: WatchFact[] = [];
  for (const thread of input.threads) {
    if (thread.status === 'running' && thread.since && now - Date.parse(thread.since) > LONG_RUN) facts.push({ kind: 'long-running', threadId: thread.id, since: thread.since });
  }
  const oldest = [...input.turnEnds].sort().at(-IDLE_ROUNDS);
  if (oldest && (!input.noteUpdatedAt || input.noteUpdatedAt < oldest)) facts.push({ kind: 'note-unchanged', rounds: IDLE_ROUNDS, noteUpdatedAt: input.noteUpdatedAt, sinceRoundEndedAt: oldest });
  const streaks = new Map<string, number>();
  for (const run of input.runs) {
    // Not an outcome yet: an observed or approved run has not tried anything.
    if (!run.repo || ['running', 'observed', 'approved'].includes(run.status)) continue;
    streaks.set(run.repo, run.status === 'failed' ? (streaks.get(run.repo) ?? 0) + 1 : 0);
  }
  for (const [repo, failures] of streaks) if (failures >= REPEATED_FAILURES) facts.push({ kind: 'repeated-failure', repo, failures });
  return facts;
}

/** Read-only review instructions, separate from the work delegated after the person's reply. */
function reviewPrompt(summary: unknown[]): string {
  return `由 Calli 发起的私人事项盘点。
这轮只读、只问，不自己开工，不做不可逆操作，也不是交接；不读关联线程的历史或本地会话文件。
查看未完成事项摘要：笼统的事提 1～3 个具体问题，每个附建议默认；挑今天最值得做的 1～2 件，问要不要开始；卡住、逾期、已停止跟进的事项提醒人。
把以上合成一条 calli notify --text-file FILE（不超过 500 字）；一次自动盘点最多一条 notify，不要分开发。没有需要人的事就 calli quiet，线程会自动归档。
摘要只有说明预览，需要全文才用 calli todo show ID；关联线程是否运行只是状态，不是接手旧会话的入口。
带 watch 字段的事项列出了可疑事实（长时间运行、多轮无进展、连续失败）；逐个判断是否空转或跑偏，值得人介入的写进 notify 并点名，正常的不提。
结束前用 calli review next TIME 决定下次何时再看，依据截止、跟进、等待和人的回复节奏；时间至少 30 分钟后、最多 3 天后，Calli 会调整安静时段、每日上限和连续无回复的长间隔。没设置时默认下一个 09:00。
人会在这条线程回复。按回答更新事项、接续说明、等待或跟进；只有人明确说「开始 X」后，才用 calli todo delegate ID 新开私人工作线程。本盘点线程不做事项的实际工作，自动盘点轮禁止 delegate，不得把“有待办”当作人的授权。
未完成事项摘要：
${JSON.stringify(summary, null, 2)}`;
}

export class PersonalReview {
  constructor(private readonly store: Store) {}

  status(): ReviewState {
    const day = localDay(new Date());
    const state = this.store.hubSetting<ReviewState>(KEY, { reviewEnabled: false, nextReviewAt: null,
      quietHours: { start: '23:00', end: '08:00' }, day, runsToday: 0, unansweredReviews: 0,
      enabledByThreadId: null, lastThreadId: null, lastFinishedAt: null, active: null });
    return state.day === day ? state : { ...state, day, runsToday: 0 };
  }

  private save(state: ReviewState) { this.store.saveHubSetting(KEY, state); }

  /** Limits apply to the effective time, not only to the agent's requested clock reading. */
  private adjust(at: number, state: ReviewState, idleFloor: number): { at: number; reasons: string[] } {
    const reasons: string[] = [];
    if (state.unansweredReviews >= 3 && at < idleFloor) { at = idleFloor; reasons.push('idle_backoff'); }
    const date = new Date(at);
    const [endHour, endMinute] = state.quietHours.end.split(':').map(Number);
    if (localDay(date) === state.day && state.runsToday >= 4) {
      date.setDate(date.getDate() + 1); date.setHours(endHour, endMinute, 0, 0); reasons.push('daily_limit');
    }
    const [startHour, startMinute] = state.quietHours.start.split(':').map(Number);
    const start = startHour * 60 + startMinute, end = endHour * 60 + endMinute;
    const minute = date.getHours() * 60 + date.getMinutes();
    if (start !== end && (start < end ? minute >= start && minute < end : minute >= start || minute < end)) {
      if (start > end && minute >= start) date.setDate(date.getDate() + 1);
      date.setHours(endHour, endMinute, 0, 0); reasons.push('quiet_hours');
    }
    return { at: date.getTime(), reasons };
  }

  command(owner: string, op: string, value?: unknown) {
    const state = this.status(), now = Date.now();
    if (op === 'review.status') return state;
    if (op === 'review.off') { state.reviewEnabled = false; state.nextReviewAt = null; this.save(state); return state; }
    let at: number;
    if (op === 'review.on') {
      state.reviewEnabled = true; state.enabledByThreadId = owner;
      if (state.nextReviewAt) at = Date.parse(state.nextReviewAt);
      else {
        const date = new Date(now + 30 * MINUTE);
        if (date.getMinutes() || date.getSeconds() || date.getMilliseconds()) date.setHours(date.getHours() + 1, 0, 0, 0);
        at = date.getTime();
      }
    } else if (op === 'review.next') {
      if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) || !Number.isFinite(Date.parse(value))) throw new HttpError(400, 'review time must be ISO 8601');
      at = Date.parse(value);
      if (at < now + 30 * MINUTE || at > now + 3 * DAY) throw new HttpError(400, 'review time must be at least 30 minutes and at most 3 days from now');
    } else throw new HttpError(400, 'unknown review command');
    const adjusted = this.adjust(at, state, now + DAY);
    state.nextReviewAt = new Date(adjusted.at).toISOString(); this.save(state);
    return { ...state, reasons: adjusted.reasons };
  }

  private defaultNext(now: number) {
    const date = new Date(now); date.setHours(9, 0, 0, 0);
    if (date.getTime() < now + 30 * MINUTE) date.setDate(date.getDate() + 1);
    return date.getTime();
  }

  /** Recheck limits after downtime, or after a day boundary while a retry was pending. */
  due(): string | null {
    const state = this.status(), now = Date.now();
    if (!state.reviewEnabled || !state.nextReviewAt || state.active || Date.parse(state.nextReviewAt) > now || this.store.hasRunningReview()) return null;
    const adjusted = this.adjust(now, state, state.lastFinishedAt ? Date.parse(state.lastFinishedAt) + DAY : now);
    if (adjusted.at > now) { state.nextReviewAt = new Date(adjusted.at).toISOString(); this.save(state); return null; }
    return state.nextReviewAt;
  }

  started(expected: string, threadId: string, turnId: string) {
    const state = this.status();
    if (!state.reviewEnabled || state.active || state.nextReviewAt !== expected || this.store.hasRunningReview()) throw new HttpError(409, 'review settings changed before launch');
    state.active = { threadId, turnId, notified: false }; state.lastThreadId = threadId;
    state.nextReviewAt = null; state.runsToday++; this.save(state);
  }

  recordNotice(threadId: string, turnId: string | null) {
    const state = this.status();
    if (!state.active || state.active.threadId !== threadId || state.active.turnId !== turnId) return;
    if (state.active.notified) throw new HttpError(409, 'an automatic review can notify only once; combine the questions into one notice');
    state.active.notified = true; this.save(state);
  }

  assertCanDelegate(threadId: string) {
    if (!this.store.isReviewThread(threadId)) return;
    const latest = this.store.listEventsOfType(threadId, 'turn.started').at(-1);
    if (!latest || turnOrigin(latest).initiator !== 'user') throw new HttpError(409, 'automatic review cannot delegate; wait for the person to reply and explicitly say to start');
  }

  /** Returns true only for the automatic turn's ending, so the scheduler can reuse quiet archiving. */
  onEvent(event: StoredEvent): boolean {
    if (event.type === 'turn.started' && turnOrigin(event).initiator === 'user' && this.store.isReviewThread(event.threadId)) {
      const state = this.status(); state.unansweredReviews = 0; this.save(state);
    }
    if (!turnOutcome(event)) return false;
    const state = this.status();
    if (!state.active || state.active.turnId !== event.turnId) return false;
    this.finish(state); return true;
  }

  private finish(state: ReviewState) {
    const now = Date.now();
    state.unansweredReviews = state.active?.notified ? 0 : state.unansweredReviews + 1;
    state.active = null; state.lastFinishedAt = new Date(now).toISOString();
    if (state.reviewEnabled) {
      const requested = state.nextReviewAt ? Date.parse(state.nextReviewAt) : this.defaultNext(now);
      state.nextReviewAt = new Date(this.adjust(requested, state, now + DAY).at).toISOString();
    }
    this.save(state);
  }

  recover() { const state = this.status(); if (state.active) this.finish(state); }

  failureThread(): string | null {
    const state = this.status();
    return [state.lastThreadId, state.enabledByThreadId].find(id => id && this.store.getThread(id)) ?? null;
  }

  /** A turn the person stopped or a restart cut off, or one copied in from elsewhere, is not a round of work. */
  private turnEnds(threadIds: readonly string[]): string[] {
    // listTurns is indexed; the endings are read only for a matter that can have enough rounds.
    if (threadIds.flatMap(id => this.store.listTurns(id)).length < IDLE_ROUNDS) return [];
    return threadIds.flatMap(id => [...this.store.listEventsOfType(id, 'turn.done'), ...this.store.listEventsOfType(id, 'turn.error')]).filter(event => {
      const end = turnOutcome(event);
      return end && (end.outcome === 'ok' || end.outcome === 'error') && !end.imported && !end.fromTerminal;
    }).map(event => event.ts);
  }

  prompt(now = Date.now()): string {
    const summaries = this.store.summaryThreadIds();
    const runs = this.store.hubSetting<ProactiveRun[]>(PROACTIVE_RUNS_KEY, []);
    return reviewPrompt(this.store.listTodos().filter(todo => !todo.done).map(todo => {
      const linked = this.store.todoThreadIds(todo.id);
      const work = linked.flatMap(id => {
        const thread = this.store.getThread(id);
        return thread && !thread.archivedAt && !summaries.has(id) && !this.store.isRouterThread(id) && !this.store.isReviewThread(id) ? [thread] : [];
      });
      const watch = watchFacts({
        threads: work.map(thread => ({ id: thread.id, status: thread.status, since: thread.hubTurnAt ?? thread.updatedAt })),
        noteUpdatedAt: this.store.todoNote(todo.id).updatedAt,
        turnEnds: this.turnEnds(work.map(thread => thread.id)),
        runs: runs.filter(run => run.matterId === todo.id),
      }, now);
      return {
        id: todo.id, title: todo.text, owner: todo.owner, state: todo.state, waitingOn: todo.waitingOn,
        dueAt: todo.dueAt, followUpAt: todo.followUpAt, followUpPaused: todo.followUpPaused, notePreview: todo.notePreview,
        threads: linked.map(id => ({ id, running: this.store.getThread(id)?.status === 'running' })),
        ...(watch.length ? { watch } : {}),
      };
    }));
  }
}
