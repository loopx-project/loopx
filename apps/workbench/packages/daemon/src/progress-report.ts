/**
 * With the board's `progress.report` granted, agents say where their thread stands in one
 * protocol line at the end of a turn: `[进度] 待验收：说明`. The line stays in the reply the person
 * reads; the hub only reads it back. Agents can say the work needs more time, needs the person,
 * or is ready to check — never that it is done: the person marks that.
 */

import type { InjectionGate } from './injections.js';

/** `backlog` is the person's alone: agents cannot report it (see STAGES). */
export type ProgressStage = 'doing' | 'review' | 'blocked' | 'backlog';
export const PROGRESS_STAGES: readonly ProgressStage[] = ['backlog', 'doing', 'review', 'blocked'];

export interface ProgressReport {
  stage: ProgressStage;
  note: string;
  at: string;
  /** Set when the person placed the thread from its menu rather than the agent reporting. */
  by?: 'user';
}

/** Protocol words agents write; not translated. */
const STAGES: Record<string, ProgressStage> = { 进行中: 'doing', 待验收: 'review', 需要你: 'blocked' };
const LINE = /^\s*[*_`>-]*\s*\[进度\]\s*(进行中|待验收|需要你)\s*(?:[:：\-—]\s*)?(.*?)[*_`\s]*$/;
const MAX_NOTE = 200;
const MARK = '<calli-progress>';
/** The mark from before the rename to Calli, still in older messages. */
const LEGACY_MARK = '<limi-progress>';

/** The last progress line in a turn's answer, or null when the agent did not write one. */
export function parseProgress(answer: string): { stage: ProgressStage; note: string } | null {
  let found: { stage: ProgressStage; note: string } | null = null;
  for (const line of answer.split('\n')) {
    const match = LINE.exec(line);
    if (match) found = { stage: STAGES[match[1]], note: match[2].trim().slice(0, MAX_NOTE) };
  }
  return found;
}

/** Appended to what the person sent, the way collaboration appends its own; never shown to them. */
const BLOCK = `${MARK}\n本轮结束时，在回复最后单独写一行进度：\n[进度] 进行中|待验收|需要你：一句话说明\n进行中：还有工作要做；待验收：你认为已经做完，请用户检查；需要你：没有用户就无法继续。是否完成由用户确认，不要说已完成。\n</calli-progress>`;

/** `gate` leaves the block out when the agent has it in view already. */
export function progressInstructions(text: string, gate?: InjectionGate): string {
  if (text.includes(MARK) || text.includes(LEGACY_MARK) || gate?.('progress', BLOCK) === false) return text;
  return `${text}\n\n${BLOCK}`;
}
