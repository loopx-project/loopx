/** Shared by the desk and briefs; no storage or browser dependencies. */
interface PriorityTodo {
  importance?: 'high' | 'normal' | 'low' | null;
  owner?: 'me' | 'agent';
  dueAt?: string | null;
  urgency?: { urgent: boolean };
}

export type Quadrant = 'now' | 'plan' | 'quick' | 'later';

/** An explicit answer wins; an unweighed deadline belonging to the person stays visible. */
export function importantOf(todo: PriorityTodo): boolean {
  if (todo.importance === 'high') return true;
  if (todo.importance) return false;
  return todo.owner !== 'agent' && Boolean(todo.dueAt);
}

export function quadrantOf(todo: PriorityTodo): Quadrant {
  const urgent = Boolean(todo.urgency?.urgent);
  if (importantOf(todo)) return urgent ? 'now' : 'plan';
  return urgent ? 'quick' : 'later';
}

export type TodoStage = 'answer' | 'review' | 'blocked' | 'working' | 'idle';
export interface StageFacts {
  /** Linked work threads that still count: not the Router, a review or an archived thread. */
  workThreads: number;
  /** A work thread is waiting on an approval, a question or a plan. */
  threadWaiting: boolean;
  /** The matter itself is waiting, and on the person. */
  waitingOnYou: boolean;
  openDoneProposal: boolean;
  readyRun: boolean;
  /** The most recently active work thread's last turn failed; a newer turn would have cleared it. */
  lastTurnFailed: boolean;
  latestRunFailed: boolean;
  threadRunning: boolean;
}

/** The first matching row wins. Null: no work thread and nothing to review. */
export function stageOf(facts: StageFacts): TodoStage | null {
  if (facts.threadWaiting || facts.waitingOnYou) return 'answer';
  if (facts.openDoneProposal || facts.readyRun) return 'review';
  if (facts.lastTurnFailed || facts.latestRunFailed) return 'blocked';
  if (facts.threadRunning) return 'working';
  return facts.workThreads > 0 ? 'idle' : null;
}

/** The three stages that cannot move without the person. */
export function needsPerson(stage: TodoStage | null | undefined): stage is 'answer' | 'review' | 'blocked' {
  return stage === 'answer' || stage === 'review' || stage === 'blocked';
}
