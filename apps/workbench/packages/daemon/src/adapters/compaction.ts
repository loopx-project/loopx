import type { TurnHooks, TurnResult } from './types.js';

/** Shared presentation; each protocol must keep run pending until compaction actually settles. */
export async function trackCompaction(hooks: TurnHooks, run: () => Promise<TurnResult>): Promise<TurnResult> {
  const id = 'context-compaction';
  hooks.emit({ type: 'tool.start', data: { id, title: '压缩上下文', kind: 'other', status: 'in_progress' } });
  try {
    const result = await run();
    hooks.emit({ type: 'tool.end', data: { id, status: result.stopReason === 'end_turn' ? 'completed' : 'failed', output: result.stopReason === 'cancelled' ? '压缩已停止' : result.error ?? '' } });
    return result;
  } catch (error) {
    hooks.emit({ type: 'tool.end', data: { id, status: 'failed', output: error instanceof Error ? error.message : String(error) } });
    throw error;
  }
}

export const isCompaction = (text: string): boolean => /^\/compact(?:\s|$)/.test(text.trim());
