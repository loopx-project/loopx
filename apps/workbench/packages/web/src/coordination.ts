import type { CoordinationMessage } from './api';

/** One thing said under a message: a result that came back, or a remark that followed it. */
export interface TalkReply {
  id: string;
  from: string;
  text: string;
  /** When it was said. Null for an answer recovered from the message itself, which carries no time. */
  at: string | null;
  failed: boolean;
  /** A file the member left on its machine, which the hub kept for this result. */
  resultFile?: { name: string; size: number; truncated?: boolean };
}

/** A message of the team with everything said in answer to it, oldest first. */
export interface Talk {
  root: CoordinationMessage;
  /** The root's own words, without the status line a result opens with. */
  text: string;
  replies: TalkReply[];
}

/** A result opens with how its turn went; that is shown as a state, not read as text. */
export function resultText(text: string): { text: string; failed: boolean } {
  const state = /^状态：(失败|本轮已返回，仍需验收)(?:\n|$)/.exec(text);
  return { text: text.slice(state?.[0].length ?? 0).trim(), failed: state?.[1] === '失败' };
}

/**
 * The team's messages as conversations: each message that answers nothing starts one, and each
 * result hangs under the message it came out of, however long the chain of answers grew.
 */
export function buildTalks(messages: CoordinationMessage[]): Talk[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const rootOf = (m: CoordinationMessage): CoordinationMessage => {
    let at = m;
    // A chain is short, and the bound keeps a damaged record from looping.
    for (let n = 0; n < messages.length; n++) {
      const up = at.replyTo ? byId.get(at.replyTo) : undefined;
      if (!up) break;
      at = up;
    }
    return at;
  };
  const talks = new Map<string, Talk>();
  for (const m of messages) {
    const root = rootOf(m);
    if (root === m) {
      talks.set(m.id, { root: m, text: m.kind === 'result' ? resultText(m.text).text : m.text, replies: [] });
      continue;
    }
    const said = resultText(m.text);
    talks.get(root.id)?.replies.push({ id: m.id, from: m.from, text: said.text, at: m.createdAt, failed: said.failed, ...(m.resultFile ? { resultFile: m.resultFile } : {}) });
  }
  for (const talk of talks.values()) {
    const { root } = talk;
    // An answer that never became a message of its own (the team was paused) is still an answer.
    if (root.kind === 'message' && root.result && !messages.some((m) => m.replyTo === root.id)) {
      talk.replies.unshift({ id: `${root.id}:result`, from: root.to, text: root.result, at: null, failed: root.state === 'failed', ...(root.resultFile ? { resultFile: root.resultFile } : {}) });
    }
  }
  return [...talks.values()];
}
