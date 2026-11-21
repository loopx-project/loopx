import { randomUUID } from 'node:crypto';
import type { UserInputAnswer, UserInputRequest } from './adapters/types.js';
import type { EventType } from './events.js';
import { HttpError } from './errors.js';

/** Pending native questions live only as long as their owning turn. The log survives reconnects. */
export class UserInputs {
  private pending = new Map<string, { threadId: string; turnId: string; request: UserInputRequest; promise: Promise<UserInputAnswer>; resolve: (answer: UserInputAnswer) => void }>();
  constructor(private emit: (threadId: string, turnId: string, type: EventType, data: Record<string, unknown>) => void) {}

  waiting(threadId: string): boolean { return this.waitingReason(threadId) !== null; }

  /** The oldest unanswered input determines the next action shown in the thread list. */
  waitingReason(threadId: string): 'question' | 'plan' | null {
    for (const p of this.pending.values()) if (p.threadId === threadId) return p.request.kind ?? 'question';
    return null;
  }

  request(threadId: string, turnId: string, request: UserInputRequest): Promise<UserInputAnswer> {
    // A decision already made is said and done in one event: the log carries the request and its
    // answer together, so a reload shows the record without ever asking about it again.
    if (request.auto) {
      const id = randomUUID();
      this.emit(threadId, turnId, 'input.request', { inputId: id, ...request });
      return Promise.resolve(request.auto);
    }
    if (request.id) {
      const existing = [...this.pending.values()].find(p => p.threadId === threadId && p.turnId === turnId && p.request.id === request.id);
      if (existing) return existing.promise;
    }
    const id = randomUUID();
    let resolve!: (answer: UserInputAnswer) => void;
    const promise = new Promise<UserInputAnswer>(r => { resolve = r; });
    this.pending.set(id, { threadId, turnId, request, promise, resolve });
    this.emit(threadId, turnId, 'input.request', { inputId: id, ...request });
    return promise;
  }

  answer(id: string, body: unknown): boolean {
    const p = this.pending.get(id);
    if (!p) return false;
    const value = body as any;
    let answer: UserInputAnswer;
    if (value?.cancelled === true) answer = { cancelled: true };
    else {
      if (!value?.answers || typeof value.answers !== 'object' || Array.isArray(value.answers)) throw new HttpError(400, '请提供问题答案');
      const answers: Record<string, string[]> = Object.create(null);
      for (const key of Object.keys(value.answers)) if (!p.request.questions.some(q => q.id === key)) throw new HttpError(400, '问题不存在');
      for (const q of p.request.questions) {
        const values = value.answers[q.id] ?? [];
        if (!Array.isArray(values) || values.length > 30 || values.some(v => typeof v !== 'string' || !v.trim() || v.length > 8000)) throw new HttpError(400, '答案格式无效');
        if (q.required !== false && !values.length) throw new HttpError(400, '请回答所有问题');
        if (!q.multiple && values.length > 1) throw new HttpError(400, '此问题只能选择一个答案');
        if (!q.freeText && values.some(v => !q.options.some(o => o.value === v))) throw new HttpError(400, '请选择提供的选项');
        answers[q.id] = [...new Set(values)];
      }
      answer = { answers };
    }
    this.pending.delete(id);
    this.emit(p.threadId, p.turnId, 'input.resolved', { inputId: id, answer });
    p.resolve(answer);
    return true;
  }

  /** `ended`: the turn ran out under the question (stopped, failed, expired), rather than the user leaving it unanswered. */
  cancel(threadId: string, nativeId?: string, ended = false): void {
    for (const [id, p] of this.pending) {
      if (p.threadId !== threadId || (nativeId !== undefined && p.request.id !== nativeId)) continue;
      this.pending.delete(id);
      const answer: UserInputAnswer = { cancelled: true };
      this.emit(p.threadId, p.turnId, 'input.resolved', { inputId: id, answer, ...(ended ? { reason: 'ended' } : {}) });
      p.resolve(answer);
    }
  }
}
