import { open, stat } from 'node:fs/promises';

/** The heading of the loader a LoopX heartbeat saves into a host, v1 and v2. */
const BOOTSTRAP = /LoopX managed heartbeat bootstrap/;
/** `--goal-id <id>`, quoted or not, also inside a JSON-escaped transcript line. */
const GOAL_FLAG = /--goal-id(?:\s+|=)\\?["']?([A-Za-z0-9][A-Za-z0-9._-]*)/g;
/** How far before the flag the command or the bootstrap heading may sit. */
const LOOKBACK = 1500;

export interface GoalClue {
  goalId: string;
  /** `bootstrap`: the heartbeat loader text; `activation`: a `loopx … heartbeat-prompt … --goal-id` command. */
  kind: 'bootstrap' | 'activation';
}

/**
 * The Goals a session's text names, in the order they appear. Only the two shapes a LoopX heartbeat
 * leaves behind count: the activation command (`loopx … heartbeat-prompt … --goal-id <id>`), whether it
 * stands alone or sits in the loader text (`LoopX managed heartbeat bootstrap`) a heartbeat saves. Other `loopx --goal-id` commands, such
 * as a status read while talking about a Goal, do not make a session a Goal session.
 */
export function goalClues(text: string): GoalClue[] {
  const clues: GoalClue[] = [];
  for (const match of text.matchAll(GOAL_FLAG)) {
    const before = text.slice(Math.max(0, match.index - LOOKBACK), match.index);
    // The heartbeat command reads `loopx … heartbeat-prompt … --goal-id`; nothing but one command line sits between.
    if (!/heartbeat-prompt[^\n`]*$/.test(before.replace(/\\n/g, '\n'))) continue;
    clues.push({ goalId: match[1], kind: BOOTSTRAP.test(before) ? 'bootstrap' : 'activation' });
  }
  return clues;
}

/** The Goal a session is on: the last one it activated. `all` lists every distinct Goal, oldest first. */
export function lastGoal(clues: GoalClue[]): { goalId: string; kind: GoalClue['kind']; all: string[] } | null {
  const last = clues.at(-1);
  if (!last) return null;
  const all = [...new Set(clues.map((c) => c.goalId))];
  return { ...last, all };
}

const HEAD_BYTES = 256 * 1024;
const TAIL_BYTES = 4 * 1024 * 1024;

/**
 * What of a session file is worth reading: the start (where the loader is first sent) and the end
 * (where the latest wake-up is), so one enormous transcript costs a bounded read. The first line
 * is returned apart, for the agent to say where the session came from.
 */
export async function readSessionEnds(path: string): Promise<{ firstLine: string; text: string; mtimeMs: number } | null> {
  let file;
  try {
    file = await open(path, 'r');
  } catch {
    return null;
  }
  try {
    const { size, mtimeMs } = await stat(path);
    const head = Buffer.alloc(Math.min(size, HEAD_BYTES));
    await file.read(head, 0, head.length, 0);
    const headText = head.toString('utf8');
    let text = headText;
    if (size > HEAD_BYTES) {
      const tailLen = Math.min(size - HEAD_BYTES, TAIL_BYTES);
      const tail = Buffer.alloc(tailLen);
      await file.read(tail, 0, tailLen, size - tailLen);
      text += `\n${tail.toString('utf8')}`;
    }
    const newline = headText.indexOf('\n');
    return { firstLine: newline < 0 ? headText : headText.slice(0, newline), text, mtimeMs };
  } finally {
    await file.close();
  }
}
