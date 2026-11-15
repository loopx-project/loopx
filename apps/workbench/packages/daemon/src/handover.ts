import { replaceMessageTail, type StoredEvent } from './events.js';

/**
 * Handing a thread's task to another agent: the successor gets a brief that points at the source —
 * `calli handover` and `calli history` read it inside the hub — plus a few lines of where things
 * stood, either written by the outgoing agent while it still could, or assembled by the hub from
 * the event log, which holds every turn, tool call and diff anyway.
 */

/** The prompt the current agent is asked to answer in its own session. */
export function handoverPrompt(targetLabel: string): string {
  return [
    `请写一份交接摘要，交给另一个 agent（${targetLabel}）接手这个任务继续做。`,
    '它会读你写的摘要，但看不到这段对话，也不要假设它了解任何背景。',
    '用 Markdown，包含这些小节：',
    '## 任务 — 用户想做什么，一两句话',
    '## 已完成 — 到现在为止做成了什么',
    '## 未完成 / 下一步 — 还剩什么，建议从哪里继续',
    '## 改过的文件 — 哪些文件动过、为什么',
    '## 验证情况 — 跑过哪些测试或检查、结果如何；哪些改动还没验证；有没有改到一半、现在跑不通的文件',
    '## 关键决定和注意事项 — 容易踩的坑、用户特别提过的要求',
    '每节几行即可，整体不超过 30 行：对方可以自己查阅原对话，细节不必复述。',
    '直接输出摘要本身，不要寒暄，也不要解释你在做什么。',
  ].join('\n');
}

/** What one diff-carrying tool event changed: paths, in order, without duplicates. */
function changedFiles(events: StoredEvent[]): string[] {
  const seen = new Set<string>();
  const paths: string[] = [];
  for (const ev of events) {
    if (!ev.type.startsWith('tool.')) continue;
    const content = (ev.data.content ?? []) as Array<Record<string, unknown>>;
    for (const item of content) {
      if (item.type !== 'diff' || typeof item.path !== 'string') continue;
      if (!seen.has(item.path)) {
        seen.add(item.path);
        paths.push(item.path);
      }
    }
  }
  return paths;
}

/** The assistant text of a stretch of events, joined from its fragments. */
export function turnMessage(events: StoredEvent[]): string {
  return events.reduce((text, event) => event.type === 'message.replace'
    ? replaceMessageTail(text, event.data)
    : event.type === 'message.delta' && typeof event.data.text === 'string' ? text + event.data.text : text, '');
}

const trunc = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n)}…` : s);

/** What the event log can say about a thread, leaving out the hub's own handover turns. */
function logFacts(events: StoredEvent[]) {
  // Hub-injected handover turns are not part of the conversation: not the user's asks, and not
  // where the thread's story ends. Imported terminal history is real conversation and stays.
  const handoverTurns = new Set(
    events.filter((e) => e.type === 'turn.started' && e.data.handover).map((e) => e.turnId as string),
  );
  const asks = events
    .filter((e) => e.type === 'turn.started' && !e.data.handover)
    .map((e) => String(e.data.displayText ?? e.data.text ?? '').trim())
    .filter(Boolean);
  const turnIds = [...new Set(events.filter((e) => e.turnId && !handoverTurns.has(e.turnId)).map((e) => e.turnId as string))];
  const lastTurnEvents = turnIds.length ? events.filter((e) => e.turnId === turnIds[turnIds.length - 1]) : [];
  const lastError = [...events].reverse().find((e) => e.type === 'turn.error');
  const lastFailed = lastTurnEvents.some((e) => e.type === 'turn.error');
  const plan = [...events].reverse().find((e) => e.type === 'plan.updated');
  return {
    asks,
    lastFailed,
    lastAnswer: turnMessage(lastTurnEvents).trim(),
    why: lastError ? [lastError.data.code, lastError.data.error].filter(Boolean).join('：') : '',
    plan: (plan?.data.entries ?? []) as Array<Record<string, unknown>>,
    files: changedFiles(events),
  };
}

/**
 * The short handover for a successor the hub starts itself. It can read the source with `calli`,
 * so it gets where to look and how, plus a few lines of where things stood, not a retelling.
 */
export function handoverBrief(info: {
  threadId: string;
  from: string;
  cwd: string;
  worktree?: { root: string; branch: string | null } | null;
  lead?: boolean;
}): string {
  const place = info.worktree
    ? `${info.cwd}（worktree ${info.worktree.root}，分支 ${info.worktree.branch ?? '游离 HEAD'}）`
    : info.cwd;
  return [
    '# 接手',
    '',
    `你接手 ${info.from} 在 hub 线程 ${info.threadId} 里的任务，看不到原对话。需要时自己读（找不到 calli 就用 "$CALLI_BIN_DIR/calli"）：`,
    '- `calli handover`：来源会话信息',
    `- \`calli history ${info.threadId}\`：按轮读问答和工具调用，按返回的 command 翻页`,
    '',
    `工作目录 ${place}。沿用它和未提交的改动，先用 git status、git diff 对齐现状再继续。`,
    ...(info.lead ? ['', '你现在是本次协作的主控：成员的结果会投给你，用 `calli status` 查看成员和未交回的任务。'] : []),
  ].join('\n');
}

/**
 * What a folded handover shows of its text. The brief opens with the same lead-in every time
 * (the heading, where to look, the working directory), which says nothing about this task, so the
 * preview starts after it and skips the headings of the sections.
 */
export function handoverPreview(text: string, limit = 160): string {
  const lines = text.split('\n');
  const lead = text.startsWith('# 接手') ? lines.findIndex((line) => line.startsWith('工作目录')) : -1;
  const body = lines.slice(lead + 1)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#') && !line.startsWith('你现在是本次协作的主控'))
    .map((line) => line.replace(/^(?:[-*]|\d+\.)\s+/, ''))
    .join(' ')
    .replace(/\*\*/g, '');
  return trunc(body, limit);
}

/** A few lines of where the thread stood, for the brief handover. The rest is in `calli history`. */
export function synthesizeBrief(events: StoredEvent[]): string {
  const { asks, lastFailed, lastAnswer, why, plan, files } = logFacts(events);
  // A failed last ask is carried over word for word after the brief; it is not repeated here.
  const ask = lastFailed ? asks.at(-2) : asks.at(-1);
  const open = plan.filter((en) => en.status !== 'completed').slice(0, 5);
  const lines = [
    ...(ask ? [`- 最近的要求：${trunc(ask.replace(/\s+/g, ' '), 200)}`] : []),
    ...(lastAnswer ? [`- 最后的回答：${trunc(lastAnswer.replace(/\s+/g, ' '), 300)}`] : []),
    ...(open.length ? [`- 未完成的计划：${open.map((en) => trunc(String(en.content ?? ''), 80)).join('；')}`] : []),
    ...(files.length ? [`- 改过的文件：${files.slice(0, 8).join('、')}${files.length > 8 ? ` 等 ${files.length} 个` : ''}`] : []),
    ...(why ? [`- 中断原因：${trunc(why, 160)}`] : []),
  ];
  return lines.length ? ['## 进度（hub 从记录整理，可能不全）', ...lines].join('\n') : '';
}
