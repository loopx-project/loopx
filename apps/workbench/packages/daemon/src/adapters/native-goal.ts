/** Native goal command syntax shared by adapters, never a hub continuation loop. */
export type GoalControl = 'show' | 'pause' | 'clear';
export type GoalCommand = { action: GoalControl | 'resume' | 'set'; objective?: string };

/** A native goal as the hub shows it: what the adapter knows right now, nothing more. */
export interface GoalSnapshot {
  objective: string;
  /** Active, paused, blocked, complete… in the agent's own vocabulary. */
  status: string;
  tokensUsed?: number;
  tokenBudget?: number;
}

/** Picks the fields the hub understands out of a native goal object; null when there is none. */
export function goalSnapshot(goal: unknown): GoalSnapshot | null {
  if (!goal || typeof goal !== 'object' || typeof (goal as any).objective !== 'string') return null;
  const g = goal as { objective: string; status?: unknown; tokensUsed?: unknown; tokenBudget?: unknown };
  return {
    objective: g.objective,
    status: typeof g.status === 'string' && g.status ? g.status : 'unknown',
    ...(typeof g.tokensUsed === 'number' && Number.isFinite(g.tokensUsed) ? { tokensUsed: g.tokensUsed } : {}),
    ...(typeof g.tokenBudget === 'number' && Number.isFinite(g.tokenBudget) ? { tokenBudget: g.tokenBudget } : {}),
  };
}

export function parseGoalCommand(text: string): GoalCommand | null {
  const match = /^\/goal(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match) return null;
  const args = match[1]?.trim() ?? '';
  if (!args) return { action: 'show' };
  if (args === 'pause' || args === 'resume' || args === 'clear') return { action: args };
  return { action: 'set', objective: args };
}

export function goalSummary(goal: any): string {
  if (!goal) return '当前没有目标。';
  const labels: Record<string, string> = {
    active: '进行中', paused: '已暂停', blocked: '遇到阻塞', complete: '原生 agent 报告已完成',
    achieved: '原生 agent 报告已完成', completed: '原生 agent 报告已完成',
    usageLimited: '额度受限', budgetLimited: '预算用完', failed: '未完成',
  };
  return [`目标：${goal.objective ?? ''}`, `状态：${labels[goal.status] ?? goal.status}`,
    ...(typeof goal.tokensUsed === 'number' ? [`用量：${goal.tokensUsed} tokens${typeof goal.tokenBudget === 'number' ? ` / ${goal.tokenBudget}` : ''}`] : []),
  ].join('\n\n');
}
