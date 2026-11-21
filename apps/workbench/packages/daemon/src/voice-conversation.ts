import { createHash, randomUUID } from 'node:crypto';
import type { VoiceSignal } from './voice-sessions.js';

export interface VoiceMessage {
  itemId: string;
  role: 'user' | 'assistant';
  text: string;
  final: boolean;
  at: string;
  afterSeq: number;
  order: number;
}

/**
 * A display transcript, never an agent prompt. Deltas remain in memory; canonical final text
 * replaces the draft and is committed once. On disconnect, unfinished speech is retained as
 * unfinished text. The namespace prevents native item ids colliding across voice sessions.
 */
export class VoiceConversation {
  readonly id = randomUUID();
  readonly startedAt = new Date().toISOString();
  private readonly entries = new Map<string, VoiceMessage>();
  private closed = false;
  private nextOrder = 0;

  constructor(
    private readonly anchor: () => number,
    private readonly commit: (message: VoiceMessage) => void,
  ) {}

  accept(signal: VoiceSignal): VoiceSignal {
    if (this.closed || signal.type !== 'transcript') return signal;
    if (signal.role !== 'user' && signal.role !== 'assistant') return signal;
    if (typeof signal.itemId !== 'string' || !signal.itemId || signal.itemId.length > 256
      || typeof signal.text !== 'string' || signal.text.length > 32768
      || typeof signal.final !== 'boolean') return signal;
    const itemId = `${this.id}:${createHash('sha256').update(signal.itemId).digest('hex').slice(0, 32)}`;
    const previous = this.entries.get(itemId);
    // A canonical role cannot change in place; neither can a delta reopen a committed item.
    if (previous && (previous.role !== signal.role || previous.final && !signal.final)) {
      return { ...signal, itemId, role: previous.role, text: previous.text, final: previous.final,
        at: previous.at, afterSeq: previous.afterSeq, order: previous.order };
    }
    const message: VoiceMessage = {
      itemId, role: signal.role,
      text: (signal.final ? signal.text : (previous?.text ?? '') + signal.text).slice(0, 32768),
      final: signal.final,
      at: previous?.at ?? new Date().toISOString(),
      afterSeq: previous?.afterSeq ?? this.anchor(),
      order: previous?.order ?? this.nextOrder++,
    };
    this.entries.set(itemId, message);
    if (message.final && (!previous?.final || previous.text !== message.text)) this.commit({ ...message });
    // Keep memory bounded even on a long call. Evicted drafts remain explicitly unfinished.
    while (this.entries.size > 256) {
      const [key, oldest] = this.entries.entries().next().value!;
      if (!oldest.final) this.commit({ ...oldest });
      this.entries.delete(key);
    }
    return { ...signal, itemId, at: message.at, afterSeq: message.afterSeq, order: message.order };
  }

  finalMessages(): VoiceMessage[] {
    return [...this.entries.values()].filter(m => m.final && m.text.trim()).map(m => ({ ...m }));
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    for (const message of this.entries.values()) if (!message.final && message.text) this.commit({ ...message });
    this.entries.clear();
  }
}
