/**
 * LoopX chat-store messages → Calli StoredEvent[].
 *
 * LoopX rows are flat {message_id, turn_id, role, text, created_at}. The Calli
 * timeline needs a turn vocabulary: `turn.started` (user ask, data.text),
 * `message.delta` (assistant text, appended whole), `turn.done`/`turn.error`
 * (closes the turn so the renderer does not read it as working forever).
 * seq is 1-based in message order; tool/reasoning events are not projected
 * (registered placeholder: "StoredEvent tool.* / reasoning.* ...").
 */
import type { ChatVisibleMessage } from '../contracts/chat-schema.ts';
import type { StoredEvent } from '../types.ts';

function isBlank(text: string): boolean {
  return text.trim().length === 0;
}

export function mapMessagesToEvents(threadId: string, messages: ChatVisibleMessage[], lastErrorCode?: string | null): StoredEvent[] {
  const events: StoredEvent[] = [];
  let seq = 0;
  const next = (): number => ++seq;
  let openTurn: string | null = null;
  const closeTurn = (ts: string, error?: string) => {
    if (openTurn == null) return;
    events.push({
      threadId,
      seq: next(),
      turnId: openTurn,
      type: error ? 'turn.error' : 'turn.done',
      ts,
      data: error ? { error } : {},
    });
    openTurn = null;
  };
  for (const m of messages) {
    if (m.role === 'user') {
      closeTurn(m.created_at);
      const turnId = m.turn_id ?? m.message_id;
      openTurn = turnId;
      events.push({
        threadId,
        seq: next(),
        turnId,
        type: 'turn.started',
        ts: m.created_at,
        data: { text: m.text },
      });
    } else {
      // assistant/system rows land inside the currently open turn, or their own
      // turn when the store hands them over without a preceding user row.
      const turnId = m.turn_id ?? openTurn ?? m.message_id;
      if (openTurn != null && m.turn_id != null && m.turn_id !== openTurn) closeTurn(m.created_at);
      if (openTurn == null) openTurn = m.turn_id ?? null;
      if (!isBlank(m.text)) {
        events.push({
          threadId,
          seq: next(),
          turnId,
          type: 'message.delta',
          ts: m.created_at,
          data: { text: m.text },
        });
      }
    }
  }
  // A session that ended on an error code closes its last turn as failed, so the thread shows it.
  closeTurn(messages.at(-1)?.created_at ?? new Date().toISOString(), lastErrorCode ?? undefined);
  return events;
}

export function eventsAfter(events: StoredEvent[], after: number): StoredEvent[] {
  return events.filter((e) => e.seq > after);
}

export function eventsBefore(events: StoredEvent[], before: number): StoredEvent[] {
  return events.filter((e) => e.seq < before);
}
