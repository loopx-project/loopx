import type { EmittedEvent } from '../events.js';
import type { TurnResult } from './types.js';

const toolKinds: Record<string, string> = { Read: 'read', Write: 'edit', Edit: 'edit', Bash: 'execute', Grep: 'search', Glob: 'search' };

/** Decode only native output records. Completion snapshots correct, rather than duplicate, deltas. */
export class DoubaoStream {
  private tail: { id: string; text: string } | null = null;
  private readonly snapshots = new Map<string, string>();
  private readonly asides = new Map<string, string>();
  private readonly tools = new Map<string, { ended: boolean }>();
  private result: TurnResult | null = null;
  private hasText = false;

  constructor(private readonly emit: (event: EmittedEvent) => void, private readonly onSessionId: (id: string) => void) {}

  record(line: string): void {
    let record: any;
    try { record = JSON.parse(line); } catch { throw new Error('Doubao 流式桥收到非 JSON 输出，已停止本轮'); }
    if (!record || typeof record !== 'object' || Array.isArray(record)) throw new Error('Doubao 流式桥收到无效记录');
    if (record.session_id !== undefined) {
      if (typeof record.session_id !== 'string' || !/^[a-z0-9_-]{1,256}$/i.test(record.session_id)) throw new Error('Doubao 流式桥收到无效会话 ID');
      this.onSessionId(record.session_id);
    }
    if (this.result) return;
    switch (record.type) {
      case 'system': break;
      case 'stream_event': {
        const delta = record.event?.delta;
        if (record.event?.type === 'content_block_delta' && delta?.type === 'text_delta') {
          if (typeof record.message_id !== 'string' || typeof delta.text !== 'string') throw new Error('Doubao 流式桥收到无效文本增量');
          this.append(record.message_id, delta.text);
        }
        break;
      }
      case 'assistant': {
        const parts = record.message?.content;
        if (!Array.isArray(parts)) break;
        const text = parts.filter((part: any) => part?.type === 'text' && typeof part.text === 'string').map((part: any) => part.text).join('');
        if (typeof record.message.id === 'string' && parts.some((part: any) => part?.type === 'text' && typeof part.text === 'string')) this.complete(record.message.id, text);
        break;
      }
      case 'calli_bridge_tool': this.tool(record); break;
      case 'result': {
        if (record.is_error || record.subtype !== 'success') {
          const error = Array.isArray(record.errors) ? record.errors.filter((value: unknown) => typeof value === 'string').join('\n') : '';
          this.result = record.error?.code === 'cancelled' ? { stopReason: 'cancelled' } : { stopReason: 'error', error: error || 'Doubao 原生任务失败' };
        } else {
          if (record.result?.type === 'text' && typeof record.result.content === 'string' && typeof record.message_id === 'string') this.complete(record.message_id, record.result.content);
          if (Array.isArray(record.permission_denials) && record.permission_denials.length) {
            const refused = [...new Set<string>(record.permission_denials.map((denial: any) => String(denial?.tool_name || denial?.prompt || 'an action')))];
            this.result = { stopReason: 'error', errorCode: 'permission_refused', refused, error: 'Doubao 无界面模式拒绝了需要批准的操作；请检查任务范围与权限，或在原生终端继续。' };
          } else this.result = this.hasText ? { stopReason: 'end_turn' } : { stopReason: 'error', error: 'Doubao CLI 未返回回复' };
        }
        break;
      }
      // Unknown native records do not become assistant text or guessed capabilities.
    }
  }

  finish(): TurnResult {
    for (const [id, state] of this.tools) if (!state.ended) {
      state.ended = true;
      this.emit({ type: 'tool.end', data: { id, status: 'interrupted' } });
    }
    return this.result ?? { stopReason: 'error', error: 'Doubao 流式桥在最终结果到达前断开' };
  }

  private append(id: string, text: string): void {
    if (!text) return;
    if (this.tail?.id !== id) this.tail = { id, text: '' };
    this.tail!.text += text;
    this.hasText = true;
    this.emit({ type: 'message.delta', data: { id, text } });
  }

  private complete(id: string, text: string): void {
    const aside = this.asides.get(id);
    if (aside && text.startsWith(aside)) text = text.slice(aside.length);
    if (this.snapshots.get(id) === text && this.tail?.id !== id) return;
    const previous = this.tail?.id === id ? this.tail.text : '';
    if (previous !== text) {
      if (!previous || text.startsWith(previous)) this.append(id, text.slice(previous.length));
      else this.emit({ type: 'message.replace', data: { id, previous, text } });
    }
    this.tail = { id, text };
    this.hasText ||= Boolean(text);
    this.snapshots.set(id, text);
  }

  private tool(record: any): void {
    const step = record.step;
    if (typeof step?.key !== 'string' || !step.key || typeof step.finished !== 'boolean') throw new Error('Doubao 流式桥收到无效工具快照');
    const id = `doubao:${step.key}`, call = step.call;
    const state = this.tools.get(id);
    const title = typeof step.title === 'string' && step.title ? step.title : typeof call?.name === 'string' ? call.name : 'Tool';
    const name = typeof call?.name === 'string' && call.name ? call.name : undefined;
    const detail = { title, kind: name ? toolKinds[name] ?? 'other' : 'other', ...(name ? { tool: name } : {}), ...(call?.input !== undefined ? { input: call.input } : {}) };
    if (!state) {
      this.tools.set(id, { ended: false });
      this.emit({ type: 'tool.start', data: { id, ...detail, status: 'in_progress' } });
      // Subsequent text is a new visible segment. A final snapshot may not erase or repeat the aside.
      if (this.tail) this.asides.set(this.tail.id, (this.asides.get(this.tail.id) ?? '') + this.tail.text);
      this.tail = null;
    }
    if (step.finished && !this.tools.get(id)!.ended) {
      this.tools.get(id)!.ended = true;
      const result = call?.result;
      this.emit({ type: 'tool.end', data: { id, ...detail,
        // Native generic tools often carry only a title and a finish signal, not an outcome.
        status: typeof result?.isError === 'boolean' ? result.isError ? 'failed' : 'completed' : 'unknown',
        ...(typeof result?.text === 'string' ? { output: result.text } : {}),
      } });
    }
  }
}
