import type { EmittedEvent } from '../events.js';

/**
 * For agents with no plan of their own: in plan mode the reply that ends the turn is the plan.
 * Text is held back until it is known which it is: words followed by a tool are an aside and go
 * out as a message; the last words of a turn that ends well become the plan (`plan.completed`);
 * a turn that is stopped or fails gives back what it had as a message.
 */
export class PlanReply {
  private text = '';

  constructor(private readonly emit: (ev: EmittedEvent) => void, private readonly id: string) {}

  add(text: string): void {
    this.text += text;
  }

  /** More work follows: what was said so far was not the plan. */
  aside(): void {
    if (this.text) this.emit({ type: 'message.delta', data: { text: this.text } });
    this.text = '';
  }

  finish(ok: boolean): void {
    if (ok && this.text.trim()) this.emit({ type: 'plan.completed', data: { id: this.id, text: this.text } });
    else this.aside();
    this.text = '';
  }
}
