import type { Store, TodoProposal, TodoRecord } from './store.js';
import type { ThreadManager, ThreadView } from './threads.js';
import { stageOf, type TodoStage } from './todo-priority.js';

type StageTodo = Pick<TodoRecord, 'id' | 'done' | 'state' | 'waitingOn'>;
type StageThread = Pick<ThreadView, 'id' | 'status' | 'waiting' | 'archivedAt' | 'updatedAt' | 'hubTurnAt'>;

// A Router or review thread is one from its first turn on, so the answer never changes once a
// turn has run. These are the only per-thread reads here that touch the event log.
const logged = new WeakMap<Store, Map<string, boolean>>();
function isRouterOrReview(store: Store, thread: Pick<StageThread, 'id' | 'hubTurnAt'>): boolean {
  let known = logged.get(store);
  if (!known) logged.set(store, known = new Map());
  const cached = known.get(thread.id);
  if (cached !== undefined) return cached;
  const answer = store.isRouterThread(thread.id) || store.isReviewThread(thread.id);
  if (thread.hubTurnAt) known.set(thread.id, answer);
  return answer;
}

/** One reading of what is not work, and of the proactive runs, shared by every matter in a pass. */
class StagePass {
  private readonly notWork: Set<string>;
  private readonly runs = new Map<string, Array<{ status: string }>>();

  constructor(private readonly manager: ThreadManager) {
    const router = manager.personal.router.status().threadId;
    this.notWork = new Set([...(router ? [router] : []), ...manager.personal.store.summaryThreadIds()]);
    for (const run of manager.personal.proactive.runs()) {
      const runs = this.runs.get(run.matterId);
      if (runs) runs.push(run);
      else this.runs.set(run.matterId, [run]);
    }
  }

  isWork(thread: StageThread): boolean {
    return !thread.archivedAt && !this.notWork.has(thread.id) && !isRouterOrReview(this.manager.personal.store, thread);
  }

  stage(todo: StageTodo, threads: readonly StageThread[], proposals: readonly TodoProposal[]): TodoStage | null {
    if (todo.done || todo.state === 'done') return null;
    const work = threads.filter(thread => this.isWork(thread));
    const latest = work.reduce<StageThread | null>((newest, thread) => !newest || thread.updatedAt > newest.updatedAt ? thread : newest, null);
    const runs = this.runs.get(todo.id) ?? [];
    return stageOf({
      workThreads: work.length,
      threadWaiting: work.some(thread => thread.waiting),
      waitingOnYou: todo.state === 'waiting' && Boolean(todo.waitingOn?.startsWith('等你')),
      openDoneProposal: proposals.some(proposal => proposal.kind === 'done' && proposal.state === 'open'),
      readyRun: runs.some(run => run.status === 'ready'),
      // A thread's status is its last turn's ending: 'error' lasts until the next turn starts.
      lastTurnFailed: latest?.status === 'error',
      latestRunFailed: runs.at(-1)?.status === 'failed',
      threadRunning: work.some(thread => thread.status === 'running'),
    });
  }
}

/**
 * Stages for a page of todos already read with their threads and proposals. Adds one read each of
 * the Router state, summary runs and proactive runs, then O(links + runs) in memory: no scan per todo.
 */
export function todoStages(manager: ThreadManager, todos: ReadonlyArray<StageTodo & { threads: readonly StageThread[]; proposals: readonly TodoProposal[] }>): Map<string, TodoStage | null> {
  const pass = new StagePass(manager);
  return new Map(todos.map(todo => [todo.id, pass.stage(todo, todo.threads, todo.proposals)]));
}

function linkedThreads(manager: ThreadManager, id: string): ThreadView[] {
  return manager.personal.store.todoThreadIds(id).flatMap(threadId => manager.personal.store.getThread(threadId) ? [manager.threadView(threadId)] : []);
}

/** One matter's stage now, for callers that are not already holding the list. */
export function todoStage(manager: ThreadManager, todo: StageTodo): TodoStage | null {
  return new StagePass(manager).stage(todo, linkedThreads(manager, todo.id), manager.personal.store.todoProposals(todo.id));
}

/** Whether turns in this thread count as a Worker's. */
export function isWorkThread(manager: ThreadManager, threadId: string): boolean {
  return new StagePass(manager).isWork(manager.threadView(threadId));
}
