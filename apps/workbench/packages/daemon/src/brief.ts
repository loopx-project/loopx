import { realpathSync } from 'node:fs';
import { sep } from 'node:path';
import { approvalFacts, failureSignature, isWriteTool, namedPaths, type ApprovalFacts } from './adapters/brief.js';
import type { Store, ThreadRecord } from './store.js';

/**
 * What a thread that wants the person comes down to, as plain facts and never as sentences: the
 * client words them in the person's language. Read from the log without a model, so it costs no
 * quota and can be asked for each time a thread is brought up.
 */
export interface ThreadBrief {
  /** The thread's `updatedAt` when this was read; a client files the brief under it. */
  updatedAt: string;
  /** Which of the sections below applies, or null when the thread is not waiting, stopped or finished and unseen. */
  kind: 'approval' | 'failure' | 'result' | null;
  /** Waiting on an approval: what the newest one asks to do. */
  approval?: ApprovalFacts & { pending: number; moreFiles: number };
  /** Stopped on an error: whether its last turn changed files and how many turns in a row failed the same way. */
  failure?: {
    code: string | null;
    /** Write or edit tool calls in the failed turn. */
    edits: number;
    /** Files those calls touched. */
    files: number;
    /** Consecutive failed turns with the same error, this one included. */
    repeats: number;
    /** A quota-resume watch is armed on the thread. */
    resume: boolean;
  };
  /** Finished: the first sentence of its last reply and what the turn changed. */
  result?: { first: string | null; edits: number; files: number };
}

/** Longest path list or sentence returned; the client only has room for a line. */
const MAX_PATHS = 3;
const MAX_TEXT = 80;
const MAX_COMMAND = 100;

const cut = (text: string, max: number): string => {
  const chars = Array.from(text);
  return chars.length > max ? chars.slice(0, max - 1).join('') + '…' : text;
};

/** The first sentence of a reply, without markdown marks, or null when it says nothing in words. */
export function firstSentence(text: string, max = MAX_TEXT): string | null {
  let fenced = false;
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (line.startsWith('```')) { fenced = !fenced; continue; }
    if (fenced || !line || line.startsWith('|') || /^[-*_=]{3,}$/.test(line)) continue;
    const plain = line.replace(/^(?:#{1,6}|>|[-*+]|\d+[.)])\s+/, '').replace(/\*+|`/g, '').trim();
    const end = plain.match(/^.*?(?:[。！？!?]|\.(?=\s|$))/);
    const sentence = (end ? end[0] : plain).trim();
    if (sentence) return cut(sentence, max);
  }
  return null;
}

/** The files and edit calls of one turn, from its tool events. */
function touched(store: Store, threadId: string, turnId: string, fromSeq: number, roots: readonly string[]): { edits: number; files: number; paths: string[] } {
  const calls = new Map<string, { write: boolean; paths: Set<string> }>();
  for (const ev of store.turnToolEvents(threadId, turnId, fromSeq)) {
    const id = String(ev.data.id ?? ev.seq);
    const call = calls.get(id) ?? { write: false, paths: new Set<string>() };
    if (isWriteTool(ev.data)) call.write = true;
    for (const path of namedPaths(ev.data)) call.paths.add(relative(path, roots));
    calls.set(id, call);
  }
  const written = [...calls.values()].filter(call => call.write);
  const paths = new Set(written.flatMap(call => [...call.paths]));
  // A write that named no file still changed one.
  const unnamed = written.filter(call => call.paths.size === 0).length;
  return { edits: written.length, files: paths.size + unnamed, paths: [...paths] };
}

/** A path inside the thread's directory as the person knows it; anything else as the agent gave it. */
function relative(path: string, roots: readonly string[]): string {
  for (const root of roots) if (path.startsWith(root + sep)) return path.slice(root.length + 1);
  return path;
}

export interface BriefContext {
  /** Ids of the approvals the thread has open right now. */
  pendingApprovals: readonly string[];
  /** A quota-resume watch is armed on the thread. */
  resume: boolean;
}

export function readBrief(store: Store, thread: ThreadRecord, ctx: BriefContext): ThreadBrief {
  const base = { updatedAt: thread.updatedAt };
  let real = thread.cwd;
  try { real = realpathSync(thread.cwd); } catch { /* The directory may have been removed. */ }
  const roots = real === thread.cwd ? [thread.cwd] : [thread.cwd, real];

  // Newest first; a failed turn is followed (in this order) by the start of the attempt before it.
  const recent = store.recentLifecycle(thread.id, 24);
  // Events are indexed by position, so a turn is read from where it began. Without its start in
  // the log (the hub was restarted mid-turn) the whole thread is the bound.
  const startOf = (turnId: string) => recent.find(entry => entry.type === 'turn.started' && entry.turnId === turnId)?.seq ?? 0;

  if (ctx.pendingApprovals.length) {
    const request = store.latestApprovalRequest(thread.id, recent.find(entry => entry.type === 'turn.started')?.seq ?? 0);
    if (request && ctx.pendingApprovals.includes(String(request.data.approvalId))) {
      const facts = approvalFacts(request.data.title, request.data.toolCall);
      const paths = facts.paths.map(path => relative(path, roots));
      return {
        ...base, kind: 'approval',
        approval: {
          ...facts,
          command: facts.command && cut(facts.command, MAX_COMMAND),
          title: facts.title && cut(facts.title, MAX_TEXT),
          paths: paths.slice(0, MAX_PATHS),
          moreFiles: Math.max(0, paths.length - MAX_PATHS),
          pending: ctx.pendingApprovals.length,
        },
      };
    }
    return { ...base, kind: null };
  }

  const last = recent[0];
  if (thread.status === 'error' && last?.type === 'turn.error') {
    const signature = failureSignature(String(last.data.message ?? ''));
    let repeats = 0;
    for (const entry of recent) {
      if (entry.type === 'turn.started') continue;
      if (entry.type !== 'turn.error' || failureSignature(String(entry.data.message ?? '')) !== signature) break;
      repeats += 1;
    }
    const { edits, files } = touched(store, thread.id, last.turnId, startOf(last.turnId), roots);
    return {
      ...base, kind: 'failure',
      failure: { code: typeof last.data.code === 'string' ? last.data.code : null, edits, files, repeats, resume: ctx.resume },
    };
  }
  if (last?.type === 'turn.done' && last.data.stopReason !== 'cancelled') {
    const { edits, files } = touched(store, thread.id, last.turnId, startOf(last.turnId), roots);
    return { ...base, kind: 'result', result: { first: firstSentence(store.finalAnswer(thread.id, last.turnId, startOf(last.turnId))), edits, files } };
  }
  return { ...base, kind: null };
}
