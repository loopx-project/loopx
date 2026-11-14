/** Why a thread needs a person, in the order the sidebar checks them. */
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

/**
 * What the thread's agent said at the end of its last turn, in its closing progress line:
 * - `doing`   — more work to do, e.g. waiting on CI.
 * - `review`  — the agent thinks it is done and asks the person to check.
 * - `blocked` — the agent cannot continue without the person.
 */
export type ProgressStage = 'doing' | 'review' | 'blocked';

export interface ProgressReport {
  stage: ProgressStage;
  /** One line from the agent; may be empty. */
  note: string;
  /** ISO time the turn ended. */
  at: string;
}
