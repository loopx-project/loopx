// Whitelisted metadata the host hands to extensions holding `events.summary`.
// Anything that can carry conversation content stays out: message text, images, tool calls,
// answers, the thread `preview` and the text of queued messages.
// Thread titles may come from the first line of the first message; the install dialog says so.

/** Why a thread needs a person, in the order the host checks them. */
export type AttentionKind =
  /** A tool call waits for approval. */
  | 'approval'
  /** The agent asked a question. */
  | 'question'
  /** A plan waits for confirmation. */
  | 'plan'
  /** The last turn failed. */
  | 'error'
  /** A finished turn nobody has looked at yet. */
  | 'unread';

export interface Attention {
  kind: AttentionKind;
}

export type ThreadStatus = 'idle' | 'running' | 'error';

/**
 * What the thread's agent said at the end of its last turn, in a `[进度]` line:
 * - `doing`   — more work to do, e.g. waiting on CI.
 * - `review`  — the agent thinks it is done and asks the person to check.
 * - `blocked` — the agent cannot continue without the person.
 * Agents never mark a thread done; only the person does.
 */
/** `backlog` is only ever the person's: 「以后再做」. Agents report the other three. */
export type ProgressStage = 'doing' | 'review' | 'blocked' | 'backlog';

export interface ProgressReport {
  stage: ProgressStage;
  /** One line from the agent; may be empty. Content: handed on only with `progress.report`. */
  note: string;
  /** ISO time the turn ended, or the person placed the thread. */
  at: string;
  /** Set when the person placed the thread from its menu rather than the agent reporting. */
  by?: 'user';
}

export interface ThreadSummary {
  id: string;
  title: string;
  /** Agent instance id, e.g. `codex` or `claude-2`. Use capabilities, never branch on it. */
  agent: string;
  /**
   * Project directory with symlinks resolved, so two spellings of one directory are one project
   * (worktrees resolve to the directory they were made from). `''` for a thread in no project.
   */
  project: string;
  /** The project directory no longer exists, e.g. a temporary checkout removed after a run. */
  projectGone?: boolean;
  /**
   * The machine the thread runs on, present only while the view shows several machines
   * (the sidebar's 「全部」). `id` null is the hub's own machine.
   */
  machine?: { id: string | null; label: string };
  status: ThreadStatus;
  /**
   * Same judgement as the sidebar's 「按活动查看」. `null` means nothing to do.
   * `unread` is computed for the viewing device; the thread on screen is never unread.
   */
  attention: Attention | null;
  /** ISO time the running turn started, when `status` is `running`. */
  runningSince: string | null;
  unread: boolean;
  pinned: boolean;
  archived: boolean;
  /** Created by a scheduled task. */
  scheduled: boolean;
  /** Runs in a git worktree. */
  worktree: boolean;
  /** Continues a thread handed over from another agent. */
  handover: boolean;
  /** Number of team members when this thread leads a team, else 0. */
  teamSize: number;
  /** ISO time the person marked the thread done; cleared when they send it new work. */
  doneAt: string | null;
  /** The agent's latest progress line. Always null without `progress.report`. */
  report: ProgressReport | null;
  createdAt: string;
  updatedAt: string;
}

/** How a turn ended. */
export type TurnOutcome =
  /** Finished normally. */
  | 'ok'
  /** Stopped by the user, including before the agent started. */
  | 'cancelled'
  /** Failed; `code` may say why. */
  | 'error'
  /** Cut off by a daemon restart. */
  | 'interrupted';

/** Where a turn came from. Three separate dimensions, not one enum. */
export interface TurnOrigin {
  /** Who started it. `extension` is reserved for host-driven turns (planned). */
  initiator: 'user' | 'schedule' | 'coordination' | 'handover' | 'extension' | 'unknown';
  /** Nature of the turn. Imported and terminal turns are history, not new work. */
  imported: boolean;
  fromTerminal: boolean;
  /** `/goal` control turns answered next to a running turn. */
  control: boolean;
}

export interface TurnSummary {
  threadId: string;
  turnId: string;
  origin: TurnOrigin;
  startedAt: string;
  /** Present once the turn ended. */
  finishedAt?: string;
  outcome?: TurnOutcome;
  /** Failure code such as `plan_exhausted`, `permission_refused` or `interrupted`. */
  code?: string;
}

export type WaitKind = 'approval' | 'question' | 'plan';

export interface WaitSummary {
  threadId: string;
  turnId: string | null;
  kind: WaitKind;
  /** `approvalId` or `inputId`. */
  id: string;
}
