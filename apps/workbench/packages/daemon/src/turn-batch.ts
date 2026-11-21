import type { TurnChannel, TurnPart } from './events.js';

/** The Router's queued next turn: what it says, what rides along once, and what arrived. */
export interface TurnBatch {
  body: string;
  /** Sent once at the end of the merged text; the newest wins. */
  context: string;
  parts: TurnPart[];
}

/** A larger batch starts a new queued turn rather than sending one prompt without bound. */
export const MAX_BATCH_TEXT = 64_000;
const SEPARATOR = '\n\n---\n\n';
const LABELS = { 'meeting-prep': (count: number) => `会前准备 · ${count} 场会议`, lark: (count: number) => `飞书 · ${count} 条新信号`, worker: (count: number) => `工作 · ${count} 条进展` };

/** Null when the merged text would be too long. Neighbouring channel parts add up; a person's words stay separate. */
export function mergeBatches(older: TurnBatch, newer: TurnBatch): TurnBatch | null {
  const body = older.body + SEPARATOR + newer.body;
  if (body.length + newer.context.length > MAX_BATCH_TEXT) return null;
  const parts = [...older.parts];
  for (const part of newer.parts) {
    const last = parts.at(-1);
    if (last && part.kind !== 'person' && last.kind === part.kind) parts[parts.length - 1] = { kind: part.kind, count: (last as { count: number }).count + part.count };
    else parts.push(part);
  }
  return { body, context: newer.context, parts };
}

/** What the page shows for a batch. A single arrival looks as it always did; only a real mix is `mixed`. */
export function batchShape(parts: TurnPart[]): { displayText: string; channel: TurnChannel | undefined } {
  const displayText = parts.map(part => part.kind === 'person' ? part.text : LABELS[part.kind](part.count)).join('\n\n');
  if (parts.length > 1) return { displayText, channel: { kind: 'mixed', count: parts.reduce((sum, part) => sum + (part.kind === 'person' ? 1 : part.count), 0), parts } };
  const [only] = parts;
  return { displayText, channel: only.kind === 'person' ? undefined : { kind: only.kind, count: only.count } };
}
