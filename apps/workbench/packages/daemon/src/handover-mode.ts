import type { DatabaseSync } from 'node:sqlite';

export type HandoverMode = 'continue' | 'branch';
export interface HandoverLink {
  sourceId: string;
  title: string;
  agent: string;
  mode?: HandoverMode;
  /** The user split an automatic continuation back into independent conversations. */
  detached?: boolean;
  /** Source event boundary at creation; sequence numbers avoid equal timestamp ambiguity. */
  sourceSeq?: number;
  /** How the work was carried over: the source's session copied as it was, or a written summary. */
  method?: 'resume' | 'summary';
}

/** Whether the source started a turn after the handover: from then on the two go their own ways. */
export function handoverSourceMoved(db: DatabaseSync, link: HandoverLink, createdAt: string): boolean {
  return Boolean(link.sourceSeq === undefined
    ? db.prepare("SELECT 1 FROM events WHERE thread_id=? AND type='turn.started' AND ts>? LIMIT 1").get(link.sourceId, createdAt)
    : db.prepare("SELECT 1 FROM events WHERE thread_id=? AND type='turn.started' AND seq>? LIMIT 1").get(link.sourceId, link.sourceSeq));
}

/** Derive the display relation from facts without rewriting stored links. */
export function effectiveHandoverMode(db: DatabaseSync, link: HandoverLink, createdAt: string): HandoverMode {
  if (link.mode !== 'continue' || link.detached) return 'branch';
  return handoverSourceMoved(db, link, createdAt) ? 'branch' : 'continue';
}
