import type { StoredEvent } from './events.js';
import { turnOutcome, type TurnEnd } from './lifecycle.js';
import { turnAnswer } from './coordination.js';
import { parseProgress } from './progress-report.js';
import { routerReply } from './personal-router.js';
import type { TodoRecord } from './store.js';
import type { ThreadManager } from './threads.js';
import { needsPerson, type TodoStage } from './todo-priority.js';
import { isWorkThread, todoStage } from './todo-stage.js';

const THROTTLE_MS = 10 * 60_000;
const SUMMARY_LENGTH = 300;
const clip = (text: string) => [...text.trim()].slice(0, SUMMARY_LENGTH).join('');
/** The first line under the note's 「下一步」 heading. */
const nextStep = (note: string) => /^##\s*下一步\s*\n+\s*(\S.*)$/m.exec(note)?.[1] ?? '';

/**
 * Tells the Router when a Worker's turn leaves a matter needing the person. Only entering such a
 * stage counts, and one matter reports at most once in ten minutes. All of this is memory: a
 * restart may repeat one report, never lose a matter, since the stage itself stays on the desk.
 */
export class WorkerReports {
  private readonly stages = new Map<string, TodoStage | null>();
  private readonly reported = new Map<string, number>();
  /** Turns that ended before their thread's status was settled. */
  private readonly ended = new Map<string, StoredEvent>();
  private readonly takeovers = new Set<string>();
  private unsubscribe?: () => void;

  constructor(private readonly manager: ThreadManager, private readonly now: () => number = Date.now) {}

  start(): void { this.unsubscribe = this.manager.subscribe(event => this.onEvent(event)); }
  stop(): void { this.unsubscribe?.(); }

  private get store() { return this.manager.personal.store; }

  private matters(threadId: string): TodoRecord[] {
    if (!this.store.getThread(threadId) || !isWorkThread(this.manager, threadId)) return [];
    return this.store.listTodos().filter(todo => this.store.todoThreadIds(todo.id).includes(threadId));
  }

  /** Runs inside the event loop of a live turn, so it must never throw into it. */
  private onEvent(event: StoredEvent): void {
    try {
      if (event.type === 'turn.started') {
        if (event.data.control || event.data.imported || event.data.fromTerminal) return;
        if (event.turnId && typeof event.data.assistantSay === 'string') this.takeovers.add(event.turnId);
        this.ended.delete(event.threadId);
        // The stage this turn started from is what its ending is compared with.
        for (const todo of this.matters(event.threadId)) this.stages.set(todo.id, todoStage(this.manager, todo));
        return;
      }
      if (turnOutcome(event)) {
        if (!event.data.imported && !event.data.fromTerminal && !event.data.control) this.ended.set(event.threadId, event);
        return;
      }
      if (event.type !== 'thread.updated') return;
      const ended = this.ended.get(event.threadId);
      if (!ended) return;
      // A turn's ending is announced before the thread leaves 'running'.
      if (this.store.getThread(event.threadId)?.status === 'running') return;
      this.ended.delete(event.threadId);
      void this.settle(ended).catch(error => console.error(`could not report a worker's turn: ${error instanceof Error ? error.message : error}`));
    } catch (error) {
      console.error(`could not read a worker's turn: ${error instanceof Error ? error.message : error}`);
    }
  }

  private async settle(event: StoredEvent): Promise<void> {
    // A proactive turn's result (ready, failed) is recorded just after its ending.
    await this.manager.personal.proactive.flush();
    const outcome = turnOutcome(event)!;
    const dispatch = this.manager.personal.proactive.runs().find(run => run.kind === 'dispatch' && run.status === 'answered' && run.threadId === event.threadId);
    const takeover = event.turnId ? this.takeovers.delete(event.turnId) : false;
    if (dispatch || takeover) {
      if (!this.worthSaying(event.threadId, outcome) || !event.turnId) return;
      const events = this.store.voiceAnswerEvents(event.threadId, event.turnId);
      const answer = turnAnswer(events, event.turnId).trim();
      const progress = parseProgress(answer);
      const stage = outcome.outcome === 'error' ? 'blocked' : progress?.stage === 'blocked' ? 'answer'
        : progress?.stage === 'review' ? 'review' : 'progress';
      const title = this.store.getTodo((dispatch?.matterId ?? ''))?.text ?? dispatch?.title ?? this.store.getThread(event.threadId)!.title;
      await this.manager.personal.router.receiveWorkerReport({ matterId: (dispatch?.matterId ?? ''), threadId: event.threadId, title, stage,
        summary: clip(outcome.outcome === 'error' ? `失败：${outcome.message ?? ''}` : answer),
        ...(stage === 'answer' ? { question: progress!.note } : {}),
        ...(outcome.outcome === 'error' ? { pushed: true } : {}),
        ...(this.store.listTurnEvents(event.threadId, event.turnId).some(item => item.type === 'agent.notice') ? { notified: true } : {}),
      });
      return;
    }
    for (const todo of this.matters(event.threadId)) {
      const stage = todoStage(this.manager, todo);
      const previous = this.stages.get(todo.id);
      this.stages.set(todo.id, stage);
      if (!needsPerson(stage) || stage === previous || !this.worthSaying(event.threadId, outcome)) continue;
      await this.report(todo, stage, event, outcome);
    }
  }

  private worthSaying(threadId: string, outcome: TurnEnd): boolean {
    // The person stopped it, or a restart did: nobody needs telling.
    if (outcome.outcome === 'cancelled' || outcome.outcome === 'interrupted') return false;
    if (outcome.code !== 'plan_exhausted') return true;
    // Handover or the quota watch is about to carry this on by itself.
    const { autoHandover } = this.manager.threadView(threadId);
    return !autoHandover?.enabled && !autoHandover?.resume && !this.manager.autoHandover.settings().resume;
  }

  private async report(todo: TodoRecord, stage: 'answer' | 'review' | 'blocked', event: StoredEvent, outcome: TurnEnd): Promise<void> {
    // A matter can exist on the plan page alone: never conjure a Router nobody set up.
    const { personal } = this.manager;
    if (!personal.settings().agent && !personal.router.status().threadId) return;
    if (!(await personal.resolvedSettings()).agent) return;
    const at = this.now();
    if (at - (this.reported.get(todo.id) ?? -Infinity) < THROTTLE_MS) return;
    this.reported.set(todo.id, at);
    const turnEvents = event.turnId ? this.store.listTurnEvents(event.threadId, event.turnId) : [];
    const said = event.turnId ? routerReply(this.store.voiceAnswerEvents(event.threadId, event.turnId)) : null;
    const summary = outcome.outcome === 'error' ? `失败：${outcome.message ?? ''}` : said ?? nextStep(this.store.todoNote(todo.id).text);
    const notified = turnEvents.some(item => item.type === 'agent.notice');
    await this.manager.personal.router.receiveWorkerReport({
      matterId: todo.id, title: todo.text, stage, summary: clip(summary),
      ...(stage === 'answer' && todo.waitingOn ? { question: todo.waitingOn } : {}),
      ...(stage === 'blocked' && outcome.outcome === 'error' ? { pushed: true } : {}),
      ...(notified ? { notified: true } : {}),
    }).catch(error => {
      // Not delivered, so not reported: the next ending may try again.
      this.reported.delete(todo.id);
      throw error;
    });
  }
}
