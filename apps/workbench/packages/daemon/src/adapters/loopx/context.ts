import type { GoalState } from './status.js';
import type { LoopxBinding } from './binding.js';

const PERMISSION_TEXT: Record<LoopxBinding['permission'], string> = {
  read_only: '只读：可以读取项目和 Goal 状态，不能写任何文件，也不要尝试执行会改动环境的命令。',
  workspace_auto: '工作区内自动：只改 Goal 的项目目录，不要写目录之外的任何东西。',
};

function todoLines(label: string, group: GoalState['agentTodos']): string[] {
  const open = group.items.filter((t) => t.status === 'open').slice(0, 8);
  return [`${label}：未完成 ${group.open}/${group.total}${group.complete ? '' : '（列表不完整）'}`, ...open.map((t) => `- ${t.text}`)];
}

/**
 * The block attached to each message of a Goal conversation (hidden in the UI). It takes the place of
 * what the core's own chat gives its Goal sessions: the Goal's identity, its current state and the
 * boundary the conversation runs under. A state that could not be read is said so, never filled in.
 */
export function goalContextBlock(binding: LoopxBinding, state: GoalState | null, unavailable?: string): string {
  const lines = [
    '<loochat-loopx>',
    '这是一个 LoopX Goal 会话。Goal 由 LoopX 核心持续推进；这里是你和用户谈话、汇报进度、讨论下一步的地方。',
    `Goal：${state?.title ?? binding.goalId}（${binding.goalId}）`,
    `权限：${PERMISSION_TEXT[binding.permission]}`,
    `工作目录：${binding.workspace}`,
  ];
  if (state) {
    lines.push(`状态：${state.status}${state.waitingOn ? `，等待 ${state.waitingOn}` : ''}`);
    if (state.summary) lines.push(`下一步建议：${state.summary}`);
    lines.push(...todoLines('Agent 待办', state.agentTodos), ...todoLines('用户待办', state.userTodos));
  } else {
    lines.push(`Goal 状态暂时读不到（${unavailable ?? '未知原因'}）。不要凭记忆说明进度，需要时请用户刷新。`);
  }
  lines.push('Goal 的待办、验收和状态由核心维护，这里不要直接改；需要改动时说明你的建议，由用户在 Goal 页确认。', '</loochat-loopx>', '');
  return lines.join('\n');
}
