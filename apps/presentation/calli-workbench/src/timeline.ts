/**
 * Turns the daemon's event log into the items the thread view renders.
 * Pure: no React, no I/O, so it can be unit-tested directly.
 */
import { diffStats } from './diff';
import { IMAGE_FILE, relativePath } from './paths';
import type { TurnChannel, TurnPart, VideoRef } from './api';
import { formatDate, formatList, t } from './i18n';

export interface StoredEvent {
  threadId: string;
  seq: number;
  turnId: string | null;
  type: string;
  ts: string;
  data: Record<string, any>;
}

export interface DetailRef { turnId: string; id: number; version: number }

/** One edited region of a file, as the agent reported it, with its line counts. */
export interface FileDiff {
  path: string;
  oldText: string | null;
  newText: string;
  added: number;
  removed: number;
}

export interface Step {
  liveVersion?: number;
  detail?: DetailRef;
  files?: Array<{ path: string; added: number; removed: number; detail: DetailRef }>;
  id: string;
  title: string;
  kind: string;
  status: string;
  input?: unknown;
  output?: string;
  diffs: FileDiff[];
  /** When the agent started and finished this call, from the event log's timestamps. */
  startedAt?: string;
  endedAt?: string;
}

export interface ImageRef {
  id: string;
  mimeType: string;
}

/** Everything a turn did to one file. */
export interface FileChange {
  details?: DetailRef[];
  path: string;
  added: number;
  removed: number;
  diffs: FileDiff[];
}

/** One task in the agent's own to-do list for a turn. */
export interface PlanEntry {
  content: string;
  /** pending | in_progress | completed */
  status: string;
}

/** How full the agent's context window is, as it last reported. */
export interface Usage {
  used: number;
  size: number;
  cost?: { amount: number; currency: string };
}

export interface ApprovalOption {
  optionId: string;
  name: string;
  kind: string;
}

/** One stretch of the agent's thinking, between two things it did or said. */
export interface Thought {
  updates?: Array<{ seq: number; text: string }>;
  detail?: DetailRef;
  key: string;
  text: string;
  startedAt: string;
  endedAt: string;
}

/** What a turn's work is made of, in the order it happened. */
export type WorkEntry =
  | { kind: 'thought'; thought: Thought }
  | { kind: 'step'; step: Step }
  | { kind: 'compaction'; key: string; id: string; status: string; automatic?: boolean }
  /** Something the agent said on the way, before going on to do more. */
  | { kind: 'note'; key: string; text: string };

/** A turn another agent of the team started: who sent it and what it is, apart from its text. */
export interface CoordinationNote {
  /** The sender's name in the team; empty when the turn does not say. */
  from: string;
  kind: 'result' | 'task';
  failed: boolean;
}

/**
 * Splits what the daemon shows for a delivered message ("来自 NAME 的结果", a blank line, then the
 * text) into who and what. Text in any other shape is kept whole.
 */
export function coordinationNote(shown: string): { note: CoordinationNote; text: string } {
  const m = /^来自 (.+?) 的(结果|任务)(?:\n\n([\s\S]*))?$/.exec(shown);
  if (!m) return { note: { from: '', kind: 'result', failed: false }, text: shown };
  const kind = m[2] === '结果' ? 'result' : 'task';
  // Only a result opens with how the turn went; a task's first line is the sender's own words.
  const state = kind === 'result' ? /^状态：(失败|本轮已返回，仍需验收)(?:\n|$)/.exec(m[3] ?? '') : null;
  return { note: { from: m[1], kind, failed: state?.[1] === '失败' }, text: (m[3] ?? '').slice(state?.[0].length ?? 0).trim() };
}

/** The opening of a delivered message as plain words, for the folded card: Markdown marks taken off. */
export function deliveredPreview(text: string): string {
  return text
    .split('\n')
    .map((line) => line.replace(/^\s*(#{1,6}|[-*+>]|\d+\.)\s+/, '').replace(/[*_`]/g, '').trim())
    .filter((line) => line && !line.startsWith('|') && !/^[-=]+$/.test(line))
    .slice(0, 3)
    .join(' ')
    .slice(0, 200);
}

export type { TurnChannel, TurnPart };

function turnPart(raw: unknown): TurnPart | null {
  if (!raw || typeof raw !== 'object') return null;
  const { kind, text, count } = raw as Record<string, unknown>;
  if (kind === 'person') return typeof text === 'string' ? { kind, text } : null;
  if (kind === 'lark' || kind === 'worker') return { kind, count: typeof count === 'number' && Number.isFinite(count) ? count : 0 };
  return null;
}

export function turnChannel(raw: unknown): TurnChannel | null {
  if (!raw || typeof raw !== 'object') return null;
  const { kind, count, parts: rawParts } = raw as Record<string, unknown>;
  if (typeof kind !== 'string' || !kind) return null;
  const parts = Array.isArray(rawParts) ? rawParts.map(turnPart).filter((part): part is TurnPart => part !== null) : [];
  // A batch that cannot say what it holds is shown as a plain message, so a person's words never turn into a quiet line.
  if (kind === 'mixed' && !parts.length) return null;
  return { kind, count: typeof count === 'number' && Number.isFinite(count) ? count : 0, ...(parts.length ? { parts } : {}) };
}

/** The quiet line for what a channel brought; null for a channel this client has no words for. */
export function channelLabel(kind: string, count: number): string | null {
  if (kind === 'lark') return t('view.channelLark', count);
  if (kind === 'worker') return t('view.channelWorker', count);
  return null;
}

/** What a channel turn shows, in order: a person's words stay words, the rest are quiet lines. */
export function channelRows(channel: TurnChannel, text: string): Array<{ kind: 'person' | 'note'; text: string }> {
  if (channel.parts) return channel.parts.map(part => part.kind === 'person' ? { kind: 'person', text: part.text } : { kind: 'note', text: channelLabel(part.kind, part.count) ?? '' });
  return [{ kind: 'note', text: channelLabel(channel.kind, channel.count) ?? text }];
}

export type Item =
  | { kind: 'handover-summary'; key: string; turnId: string; target: string; done: boolean; version: number }
  | { kind: 'notice'; key: string; turnId: string; text: string; confirmation: boolean }
  | { kind: 'model-switch'; key: string; turnId: string; model: string; previous: string; reason: string }
  | { kind: 'quota-resume'; key: string; turnId: string | null; resetsAt?: string }
  | { kind: 'proposal'; key: string; turnId: string; id: string; text: string; done: boolean }
  /**
   * A question or plan approval the agent asked. `ended` on a cancelled answer means the turn ran
   * out under it (stopped, failed), not that the user chose to leave it unanswered.
   */
  | { kind: 'input'; key: string; turnId: string; inputId: string; title: string; inputKind?: string; plan?: string; questions: import('./api').UserQuestion[]; answer: import('./api').UserInputAnswer | { cancelled: true; ended: true } | null }
  | { kind: 'user'; voice?: boolean; background?: boolean; streaming?: boolean; handoverCollapsed?: boolean; workMode?: string; key: string; turnId: string; text: string; images: ImageRef[]; videos?: VideoRef[]; fromTerminal?: boolean; scheduled?: boolean; coordination?: CoordinationNote; channel?: TurnChannel }
  /** The turn's answer: the text after everything the agent did. */
  | { kind: 'assistant'; key: string; turnId: string; text: string; done: boolean }
  /**
   * Everything a turn did on the way to its answer, as one block: thinking, tool calls and the
   * remarks in between, in order. Open while the turn runs, folded to one line once it is done.
   */
  | {
      kind: 'work';
      deferred?: DetailRef;
      liveEvents?: StoredEvent[];
      count?: number;
      files?: Array<{ path: string; added: number; removed: number; detail: DetailRef }>;
      key: string;
      turnId: string;
      entries: WorkEntry[];
      /** The tool calls among the entries, for counting and for the files they changed. */
      steps: Step[];
      /** From the turn's start to its end; until it ends, to the last thing that happened. */
      startedAt: string;
      endedAt: string | null;
      done: boolean;
      /** False for turns imported from the agent's history: their events were all written at once. */
      timed: boolean;
      /** The user stopped the turn: it reads "You stopped after Ns", like Codex. */
      stopped?: boolean;
    }
  /** A turn the user stopped before it did anything worth a work block. */
  | { kind: 'stopped'; key: string; turnId: string; startedAt: string; endedAt: string; timed: boolean }
  /** Only shown until the active turn produces its first visible output. */
  | { kind: 'waiting'; key: string; turnId: string; startedAt: string; phase: 'preparing' | 'response' }
  | {
      kind: 'approval';
      key: string;
      turnId: string;
      approvalId: string;
      title: string;
      options: ApprovalOption[];
      decision: { optionId: string } | { cancelled: true } | null;
      /** What is about to be written, when the agent says. */
      diffs: FileDiff[];
      /** The shell command about to run, when the request is for one. */
      command: string | null;
    }
  /** The agent's task list for a turn. Later updates replace the entries in place. */
  | { kind: 'plan'; key: string; turnId: string; entries: PlanEntry[]; done: boolean }
  /** Files a turn changed, shown once after everything else in the turn. */
  | { kind: 'changes'; key: string; turnId: string; files: FileChange[]; added: number; removed: number }
  /** The row under a finished answer. `text` is what "copy" copies; `at` is when the turn ended, where the hub saw it end. */
  | { kind: 'actions'; key: string; turnId: string; text: string; at?: string }
  /** `planExhausted`: the agent's account ran out of usage, so another agent can be asked to take over. */
  | { kind: 'error'; key: string; turnId: string | null; message: string; planExhausted?: boolean }
  /** Where a fork begins: above it is the conversation it was copied from, below it its own. `branch` when it got a worktree. */
  | { kind: 'fork'; key: string; turnId: null; branch?: string };

/**
 * What to show for a failed turn. Failures the daemon marks with a code get an explanation and a
 * way out; anything else is shown as the agent reported it.
 */
export function errorText(data: Record<string, any>): string {
  if (data.code === 'interrupted') return t('timeline.error.interrupted');
  if (data.code === 'plan_exhausted') return t('timeline.error.planExhausted', String(data.message ?? '').replace(/^Internal error:\s*/, ''));
  if (data.code === 'permission_refused') {
    return t('timeline.error.permissionRefused', Array.isArray(data.refused) && data.refused.length ? formatList(data.refused.map(String)) : '');
  }
  return String(data.message ?? 'error');
}

export interface Timeline {
  items: Item[];
  lastSeq: number;
  /** A turn has started and not yet finished (turn.done or turn.error). */
  running: boolean;
  /** An approval is waiting for the user. */
  awaitingApproval: boolean;
  /** The latest context usage the agent reported, if it reports any. */
  usage: Usage | null;
}

/**
 * Text a user picked out of a message, quoted into the text they send. The quote rides along as
 * ordinary Markdown block-quote lines, so the agent, the terminal and every copy of the thread
 * see the same thing with no storage of our own.
 */
export function withQuote(quote: string, body: string): string {
  const quoted = quote
    .trim()
    .split('\n')
    .map((line) => `> ${line}`.trimEnd())
    .join('\n');
  const rest = body.trim();
  return rest ? `${quoted}\n\n${rest}` : quoted;
}

/** A passage picked out of the conversation to talk about, with what the user says about it. */
export interface Note {
  quote: string;
  /** Optional: a passage can go along on its own. */
  comment: string;
  /** The thread-relative path the passage was picked out of, when it is a file's text. Parsed by
   * the agent as the 文件： line, so the note can be found in the file without line numbers. */
  file?: string;
}

const NOTE_HEAD = /^\[注释 (\d+)\]$/;
const NOTE_FILE = '文件：';
const NOTE_COMMENT = '评论：';

/**
 * A message with numbered notes in front, in plain text any agent reads and can refer back to
 * ("注释 2"):
 *
 *   [注释 1]
 *   文件：writing/题目.md
 *   > the passage
 *   评论：what the user says about it
 *
 *   the message itself
 */
export function withNotes(notes: Note[], body: string): string {
  const blocks = notes.map((note, i) => {
    const quoted = note.quote
      .trim()
      .split('\n')
      .map((line) => `> ${line}`.trimEnd())
      .join('\n');
    const comment = note.comment.trim().replace(/\s*\n\s*/g, ' ');
    return [`[注释 ${i + 1}]`, ...(note.file ? [NOTE_FILE + note.file] : []), quoted, ...(comment ? [NOTE_COMMENT + comment] : [])].join('\n');
  });
  const rest = body.trim();
  return [...blocks, ...(rest ? [rest] : [])].join('\n\n');
}

/** The notes at the start of a sent message, split back out to draw as a chip above the bubble. */
export function splitNotes(text: string): { notes: Note[]; body: string } {
  const lines = text.split('\n');
  const notes: Note[] = [];
  let i = 0;
  let end = 0;
  while (i < lines.length) {
    while (i < lines.length && !lines[i].trim()) i += 1;
    if (i >= lines.length || !NOTE_HEAD.test(lines[i].trim())) break;
    i += 1;
    let file: string | undefined;
    if (i < lines.length && lines[i].startsWith(NOTE_FILE)) file = lines[i++].slice(NOTE_FILE.length).trim() || undefined;
    const quoted: string[] = [];
    while (i < lines.length && lines[i].startsWith('>')) quoted.push(lines[i++].replace(/^>\s?/, ''));
    let comment = '';
    if (i < lines.length && lines[i].startsWith(NOTE_COMMENT)) comment = lines[i++].slice(NOTE_COMMENT.length).trim();
    notes.push({ quote: quoted.join('\n').trim(), comment, ...(file ? { file } : {}) });
    end = i;
  }
  if (notes.length === 0) return { notes, body: text };
  return { notes, body: lines.slice(end).join('\n').replace(/^\n+/, '') };
}

/** The leading block-quote lines of a sent message, split back out to draw above the bubble. */
export function splitUserQuote(text: string): { quote: string | null; body: string } {
  const lines = text.split('\n');
  let end = 0;
  while (end < lines.length && lines[end].startsWith('>')) end += 1;
  if (end === 0) return { quote: null, body: text };
  const quote = lines
    .slice(0, end)
    .map((line) => line.replace(/^>\s?/, ''))
    .join('\n')
    .trim();
  const body = lines.slice(end).join('\n').replace(/^\n+/, '');
  return { quote: quote || null, body };
}

// The timeline is rebuilt on every new event. Line counts are the costly part, and the raw
// diff objects in the event log never change, so each one is measured only once.
const diffCache = new WeakMap<object, FileDiff>();

export function diffsOf(content: unknown): FileDiff[] {
  if (!Array.isArray(content)) return [];
  const out: FileDiff[] = [];
  for (const part of content) {
    if (!part || typeof part !== 'object' || (part as any).type !== 'diff') continue;
    let diff = diffCache.get(part);
    if (!diff) {
      const raw = part as { path?: unknown; oldText?: unknown; newText?: unknown };
      const oldText = typeof raw.oldText === 'string' ? raw.oldText : null;
      const newText = typeof raw.newText === 'string' ? raw.newText : '';
      diff = { path: String(raw.path ?? ''), oldText, newText, ...diffStats(oldText, newText) };
      diffCache.set(part, diff);
    }
    out.push(diff);
  }
  return out;
}

/** The readable text in a tool's content, leaving out diffs (those are shown as diffs). */
function textOfContent(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((part: any) => {
      if (part?.type === 'diff') return '';
      if (part?.type === 'content' && part.content?.type === 'text') return String(part.content.text);
      if (part?.type === 'text' && typeof part.text === 'string') return part.text;
      return '';
    })
    .filter(Boolean)
    .join('\n');
}

function outputText(output: unknown): string {
  if (output === undefined || output === null || output === '') return '';
  return typeof output === 'string' ? output : JSON.stringify(output, null, 2);
}

function applyToolData(step: Step, data: Record<string, any>): void {
  if (data.detail) step.detail = data.detail;
  if (data.files) step.files = data.files;
  if (data.status) step.status = String(data.status);
  if (data.title) step.title = String(data.title);
  // A call that surfaced while its input still streamed is named by a later event.
  if (data.kind) step.kind = String(data.kind);
  if (data.input && typeof data.input === 'object' && Object.keys(data.input).length) step.input = data.input;
  const diffs = diffsOf(data.content);
  if (diffs.length) { step.diffs = diffs; step.files = undefined; }
  const text = outputText(data.output) || textOfContent(data.content);
  if (text) step.output = text;
}

export function buildTimeline(events: StoredEvent[]): Timeline {
  const items: Item[] = [];
  // Finished turns, with the time each ended. Turns the hub only copied in (imported history,
  // turns synced from the terminal) ended at some earlier time it never saw: those have none.
  const finished = new Map<string, string | undefined>();
  // Turns the user stopped. What they said so far stays on screen, but there is no answer to copy
  // or fork from: the agent may not have kept the turn at all.
  const stopped = new Set<string>();
  const imported = new Set<string>();
  let lastSeq = 0;
  let running = false;
  let usage: Usage | null = null;
  let waiting: Extract<Item, { kind: 'waiting' }> | null = null;

  type Work = Extract<Item, { kind: 'work' }>;
  type Answer = Extract<Item, { kind: 'assistant' }>;
  type Plan = Extract<Item, { kind: 'plan' }>;

  // The turn that has started and not ended yet, with the items of it that are still being written.
  const open: { turn: string | null; startedAt: string; work: Work | null; answer: Answer | null; plan: Plan | null } = {
    turn: null,
    startedAt: '',
    work: null,
    answer: null,
    plan: null,
  };

  /**
   * The work block of the open turn, made on first use. Text the agent has said so far turns out
   * to have been a remark on the way, not the answer, so it moves into the block as a note: the
   * block reads top-down in the order things happened, and the answer is what comes after it all.
   */
  const workBlock = (key: string, ts: string): Work => {
    const turnId = open.turn ?? '';
    const { answer } = open;
    if (!open.work) {
      open.work = { kind: 'work', key, turnId, entries: [], steps: [], startedAt: open.startedAt || ts, endedAt: null, done: false, timed: !imported.has(turnId) };
      // Before the remark if there is one, so the note keeps its place on screen.
      const at = answer ? items.indexOf(answer) : -1;
      if (at >= 0) items.splice(at, 0, open.work);
      else items.push(open.work);
    }
    if (answer) {
      if (answer.text.trim()) open.work.entries.push({ kind: 'note', key: answer.key, text: answer.text });
      items.splice(items.indexOf(answer), 1);
      open.answer = null;
    }
    open.work.endedAt = ts;
    return open.work;
  };

  /** Ends the open turn's items. A turn that ended gets its duration; one that was cut off keeps its last sign of life. */
  const closeTurn = (endedAt?: string) => {
    const { answer, plan } = open;
    // A card that asked the user something ended the block above it, so a turn can hold several
    // work blocks. All of them are finished here; only the last can hand its closing remark over
    // as the answer. Blocks with no turn at all came from events that arrived after their turn
    // had already ended (two daemons once wrote overlapping): they are closed too, or they would
    // read as working forever.
    const works = items.filter((it): it is Work => it.kind === 'work' && (it.turnId === open.turn || (open.turn === null && it.turnId === '' && !it.done)));
    for (const work of works) {
      work.done = true;
      for (const entry of work.entries) {
        if (entry.kind === 'compaction' && entry.status === 'in_progress') entry.status = 'interrupted';
      }
      if (endedAt) work.endedAt = endedAt;
    }
    const last = works.at(-1);
    if (last && !answer && !last.stopped) {
      // A turn whose last act was a tool call still said something: its last remark is the answer.
      const noteAt = last.entries.map((e) => e.kind).lastIndexOf('note');
      if (noteAt >= 0) {
        const [note] = last.entries.splice(noteAt, 1);
        if (note.kind === 'note') items.splice(items.indexOf(last) + 1, 0, { kind: 'assistant', key: note.key, turnId: last.turnId, text: note.text, done: true });
      }
    }
    for (const item of items) if (item.turnId === open.turn) {
      if (item.kind === 'proposal') item.done = true;
      if (item.kind === 'input' && !item.answer) item.answer = { cancelled: true, ended: true };
    }
    if (answer) answer.done = true;
    if (plan) plan.done = true;
    open.turn = null;
    open.work = null;
    open.answer = null;
    open.plan = null;
    // Not the last turn's start: a block made by events that arrive later must not inherit it,
    // or its timer would count from a turn that is already over.
    open.startedAt = '';
  };

  for (const ev of events) {
    lastSeq = Math.max(lastSeq, ev.seq);
    const key = `e${ev.seq}`;
    const turn = ev.turnId ?? '';
    if (ev.type === 'turn.error') {
      const summary = items.find(i => i.kind === 'handover-summary' && i.turnId === turn);
      if (summary?.kind === 'handover-summary') { summary.done = true; summary.version = ev.seq; }
    }
    if (ev.type === 'history.handover') {
      closeTurn();
      const existing = items.find(i => i.kind === 'handover-summary' && i.turnId === turn);
      const marker: Item = { kind: 'handover-summary', key: `handover-${turn}`, turnId: turn, target: String(ev.data.target), done: Boolean(ev.data.done), version: Number(ev.data.version) };
      if (existing) Object.assign(existing, marker); else items.push(marker);
      running = !marker.done;
      continue;
    }
    if (ev.type === 'agent.notice') { items.push({ kind: 'notice', key, turnId: turn, text: String(ev.data.text ?? ''), confirmation: Boolean(ev.data.confirmation) }); continue; }
    if (ev.type === 'handover.model') { items.push({ kind: 'model-switch', key, turnId: turn, model: String(ev.data.model ?? ''), previous: String(ev.data.previous ?? ''), reason: String(ev.data.reason ?? 'near') }); continue; }
    if (ev.type === 'handover.resume') { items.push({ kind: 'quota-resume', key, turnId: turn, ...(ev.data.resetsAt ? { resetsAt: String(ev.data.resetsAt) } : {}) }); continue; }
    // Keep complete events received after a historical snapshot. They also hydrate deferred
    // entries when expanded, including updates for tools whose starts predate this connection.
    if (ev.type === 'tool.update' || ev.type === 'tool.end') {
      for (const item of items) if (item.kind === 'work' && item.turnId === turn && item.deferred && ev.seq > item.deferred.version) {
        (item.liveEvents ??= []).push(ev);
      }
    } else if (open.work?.deferred && ev.seq > open.work.deferred.version
      && ['tool.start', 'reasoning.delta', 'message.delta', 'message.replace', 'context.compaction', 'turn.done', 'turn.error'].includes(ev.type)) {
      (open.work.liveEvents ??= []).push(ev);
    }

    switch (ev.type) {
      case 'turn.started': {
        // A turn the agent started itself begins with its own output, so there is nothing to wait for.
        waiting = ev.data.imported || ev.data.fromTerminal || ev.data.source === 'background' ? null : {
          kind: 'waiting', key: `${key}-waiting`, turnId: turn, startedAt: ev.ts, phase: 'preparing',
        };
        // A turn that never got its ending (the daemon died under it) ends where the next begins,
        // or it would read as working for the rest of the thread. So does a block left by events
        // that arrived after their turn's ending with no turn open.
        if (open.turn !== null || open.work !== null) closeTurn();
        open.turn = turn;
        open.startedAt = ev.ts;
        running = true;
        if (ev.data.imported) imported.add(turn);
        const shown = String(ev.data.displayText ?? ev.data.text ?? '');
        const delivered = ev.data.coordination ? coordinationNote(shown) : null;
        items.push({
          kind: 'user',
          ...(ev.data.voiceSummary ? { handoverCollapsed: true } : {}),
          ...(ev.data.handoverCollapsed === true ? { handoverCollapsed: true } : {}),
          workMode: ev.data.workMode,
          key,
          turnId: turn,
          text: delivered ? delivered.text : shown,
          images: Array.isArray(ev.data.images) ? ev.data.images : [],
          ...(Array.isArray(ev.data.videos) && ev.data.videos.length ? { videos: ev.data.videos } : {}),
          ...(ev.data.fromTerminal ? { fromTerminal: true } : {}),
          ...(ev.data.scheduled ? { scheduled: true } : {}),
          ...(ev.data.source === 'background' ? { background: true } : {}),
          ...(delivered ? { coordination: delivered.note } : {}),
          ...(turnChannel(ev.data.channel) ? { channel: turnChannel(ev.data.channel)! } : {}),
        });
        break;
      }

      case 'message.delta':
        if (!open.answer) {
          open.answer = { kind: 'assistant', key, turnId: turn, text: '', done: false };
          items.push(open.answer);
        }
        open.answer.text += String(ev.data.text ?? '');
        break;

      case 'message.replace': {
        const { previous, text } = ev.data;
        if (typeof previous !== 'string' || !previous || typeof text !== 'string') break;
        const replace = (target: { text: string }) => {
          if (!target.text.endsWith(previous)) return false;
          target.text = target.text.slice(0, -previous.length) + text;
          return true;
        };
        if (open.answer?.turnId === turn && replace(open.answer)) break;
        for (const item of [...items].reverse()) {
          if (item.turnId !== turn || item.kind !== 'work') continue;
          const note = [...item.entries].reverse().find(entry => entry.kind === 'note' && entry.text.endsWith(previous));
          if (note?.kind === 'note') { replace(note); break; }
        }
        break;
      }

      case 'plan.updated': {
        const entries: PlanEntry[] = (Array.isArray(ev.data.entries) ? ev.data.entries : [])
          .filter((e: any) => e && typeof e.content === 'string' && e.content.trim())
          .map((e: any) => ({ content: String(e.content), status: String(e.status ?? 'pending') }));
        if (!open.plan) {
          if (entries.length === 0) break;
          open.plan = { kind: 'plan', key, turnId: turn, entries, done: false };
          items.push(open.plan);
        } else {
          open.plan.entries = entries;
        }
        break;
      }

      case 'usage.updated':
        if (typeof ev.data.size === 'number' && Number.isFinite(ev.data.size) && ev.data.size > 0) {
          // An explicit unknown reading invalidates the previous measurement, e.g. after compaction.
          if (ev.data.used === null) usage = null;
          else if (typeof ev.data.used === 'number' && Number.isFinite(ev.data.used) && ev.data.used >= 0) {
            usage = { used: ev.data.used, size: ev.data.size, ...(ev.data.cost ? { cost: ev.data.cost } : {}) };
          }
        }
        break;

      case 'turn.phase':
        if (waiting && waiting.turnId === turn) {
          waiting.phase = ['session_ready', 'prompt_sent', 'model_request', 'model_queue'].includes(ev.data.phase) ? 'response' : 'preparing';
        }
        break;

      case 'reasoning.delta': {
        const block = workBlock(key, ev.ts);
        const last = block.entries.at(-1);
        // Thinking carries on in the same stretch until the agent does or says something.
        if (last?.kind === 'thought') {
          last.thought.text += String(ev.data.text ?? '');
          last.thought.endedAt = ev.ts;
          if (last.thought.detail && ev.seq > last.thought.detail.version) (last.thought.updates ??= []).push({ seq: ev.seq, text: String(ev.data.text ?? '') });
        } else {
          block.entries.push({ kind: 'thought', thought: { key, text: String(ev.data.text ?? ''), startedAt: ev.ts, endedAt: ev.ts } });
        }
        break;
      }

      case 'history.work': {
        const block = workBlock(key, ev.ts);
        block.deferred = { turnId: turn, id: ev.seq, version: ev.data.version };
        block.count = ev.data.count;
        block.files = ev.data.files;
        block.liveEvents = [...(ev.data.liveEvents ?? [])];
        // A deferred stretch that no turn ending ever closes — events that arrived after their
        // turn's ending — still knows how long it ran; the placeholder carries its last timestamp.
        if (typeof ev.data.endedAt === 'string') block.endedAt = ev.data.endedAt;
        break;
      }

      case 'history.thought': {
        const block = workBlock(`e${ev.data.id}`, ev.data.startedAt);
        const thoughtKey = `e${ev.data.id}`;
        const existing = block.entries.find(e => e.kind === 'thought' && e.thought.key === thoughtKey);
        const thought: Thought = { key: thoughtKey, text: ev.data.text, startedAt: ev.data.startedAt, endedAt: ev.data.endedAt, detail: ev.data.detail };
        if (existing?.kind === 'thought') existing.thought = thought;
        else block.entries.push({ kind: 'thought', thought });
        break;
      }

      case 'context.compaction': {
        if (typeof ev.data.id !== 'string' || !ev.data.id) break;
        const existing = open.work?.turnId === turn
          ? open.work.entries.find((entry) => entry.kind === 'compaction' && entry.id === ev.data.id) : undefined;
        if (existing?.kind === 'compaction') {
          // Replayed or enriched terminal updates keep their original place; a late start
          // must not turn a completed operation back into a spinner.
          if (ev.data.status && (existing.status === 'in_progress' || ev.data.status !== 'in_progress')) existing.status = ev.data.status;
          if (typeof ev.data.automatic === 'boolean') existing.automatic = ev.data.automatic;
        } else if (ev.data.status) {
          workBlock(key, ev.ts).entries.push({ kind: 'compaction', key, id: ev.data.id, status: ev.data.status,
            ...(typeof ev.data.automatic === 'boolean' ? { automatic: ev.data.automatic } : {}) });
        }
        break;
      }

      case 'history.tool.start':
      case 'tool.start': {
        const block = workBlock(key, ev.ts);
        if (block.count !== undefined && ev.type === 'tool.start') block.count++;
        const step: Step = {
          id: String(ev.data.id),
          title: String(ev.data.title ?? ''),
          kind: String(ev.data.kind ?? 'other'),
          status: String(ev.data.status ?? 'in_progress'),
          input: ev.data.input,
          diffs: [],
          startedAt: ev.ts,
          endedAt: ev.data.endedAt,
          detail: ev.data.detail,
          files: ev.data.files,
        };
        applyToolData(step, { content: ev.data.content });
        block.entries.push({ kind: 'step', step });
        block.steps.push(step);
        break;
      }

      case 'tool.update':
      case 'history.tool.update':
      case 'history.tool.end':
      case 'tool.end': {
        // A result goes to the row its call started in. The call may have started in a block above
        // a card that asked the user something, so every block of the turn is searched, newest
        // first. A result whose call is not here updates nothing.
        let work: Work | undefined;
        let step: Step | undefined;
        for (let i = items.length - 1; i >= 0; i--) {
          const it = items[i];
          // A block with no turn came from events that arrived after their turn's ending; while no
          // turn is open, such a turn's results still belong there.
          if (it.kind !== 'work' || (it.turnId !== turn && !(open.turn === null && it.turnId === ''))) continue;
          step = it.steps.find((s) => s.id === String(ev.data.id));
          if (step) { work = it; break; }
        }
        if (!work || !step) break;
        applyToolData(step, ev.data);
        if (ev.type === 'tool.update' || ev.type === 'tool.end') step.liveVersion = ev.seq;
        if (ev.type === 'tool.end' || ev.type === 'history.tool.end') step.endedAt = ev.ts;
        work.endedAt = ev.ts;
        break;
      }

      case 'plan.delta':
      case 'plan.completed': {
        const id = String(ev.data.id ?? 'plan');
        let proposal = items.find((it): it is Extract<Item, { kind: 'proposal' }> => it.kind === 'proposal' && it.turnId === turn && it.id === id);
        if (!proposal) {
          // The card stands where the turn has got to; what the agent does after it goes below.
          open.work = null;
          open.answer = null;
          proposal = { kind: 'proposal', key, turnId: turn, id, text: '', done: false };
          items.push(proposal);
        }
        if (ev.type === 'plan.completed') { proposal.text = String(ev.data.text ?? ''); proposal.done = true; }
        else proposal.text += String(ev.data.text ?? '');
        break;
      }
      case 'input.request':
        // The question interrupts the turn's flow: work that goes on after it lands below it.
        open.work = null;
        open.answer = null;
        items.push({ kind: 'input', key, turnId: turn, inputId: String(ev.data.inputId), title: ev.data.title, inputKind: ev.data.kind, plan: ev.data.plan, questions: ev.data.questions ?? [], answer: null });
        break;
      case 'input.resolved':
        for (const it of items) if (it.kind === 'input' && it.inputId === ev.data.inputId) {
          // `reason: 'ended'`: the turn ran out under the question, not that the user skipped it.
          it.answer = ev.data.reason === 'ended' ? { cancelled: true, ended: true } : ev.data.answer;
        }
        break;

      case 'approval.request': {
        const toolCall = (ev.data.toolCall ?? {}) as { content?: unknown; rawInput?: { command?: unknown } };
        // Like a question: what the agent does once it is granted goes below the card.
        open.work = null;
        open.answer = null;
        items.push({
          kind: 'approval',
          key,
          turnId: turn,
          approvalId: String(ev.data.approvalId),
          title: String(ev.data.title ?? 'Permission requested'),
          options: (ev.data.options ?? []) as ApprovalOption[],
          decision: null,
          diffs: diffsOf(toolCall.content),
          command: typeof toolCall.rawInput?.command === 'string' ? toolCall.rawInput.command : null,
        });
        break;
      }

      case 'approval.resolved': {
        const id = String(ev.data.approvalId);
        for (const it of items) {
          if (it.kind === 'approval' && it.approvalId === id) it.decision = ev.data.decision ?? null;
        }
        break;
      }

      case 'turn.done':
        if (ev.data.stopReason === 'cancelled') {
          stopped.add(turn);
          if (open.answer?.text.trim()) {
            // A stopped turn has no final answer. Text produced before the stop is process
            // output, so keep it under the stopped work disclosure instead of a copyable answer.
            workBlock(key, ev.ts).stopped = true;
          } else if (open.work) open.work.stopped = true;
          else {
            const line: Item = { kind: 'stopped', key, turnId: turn, startedAt: open.startedAt || ev.ts, endedAt: ev.ts, timed: !imported.has(turn) };
            // Above whatever the agent had said, where a work block would stand.
            const at = open.answer ? items.indexOf(open.answer) : -1;
            if (at >= 0) items.splice(at, 0, line);
            else items.push(line);
          }
        }
        closeTurn(ev.ts);
        finished.set(turn, ev.data.imported || ev.data.fromTerminal ? undefined : ev.ts);
        running = false;
        break;

      case 'turn.error':
        closeTurn(ev.ts);
        items.push({ kind: 'error', key, turnId: turn, message: errorText(ev.data), ...(ev.data.code === 'plan_exhausted' ? { planExhausted: true } : {}) });
        running = false;
        break;

      case 'thread.updated': {
        // Most of these are settings changing, which the thread view does not show. The one a fork
        // ends its copied history with marks where the copy starts going its own way.
        if (typeof ev.data.forkedFrom !== 'string') break;
        const branch = (ev.data.worktree as { branch?: unknown } | undefined)?.branch;
        items.push({ kind: 'fork', key, turnId: null, ...(typeof branch === 'string' ? { branch } : {}) });
        break;
      }

      default:
        break; // thread.created: not rendered in the thread view
    }
  }

  // Acknowledgements and usage do not count as output; tool/approval/history rows do.
  if (running && waiting && !items.some(it => it.turnId === waiting!.turnId && it.kind !== 'user')) items.push(waiting);
  const withSummaries = addTurnSummaries(items, finished, stopped);
  const awaitingApproval = running && items.some((it) => (it.kind === 'approval' && it.decision === null) || (it.kind === 'input' && it.answer === null));
  return { items: withSummaries, lastSeq, running, awaitingApproval, usage };
}

const compactTokens = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

/** What the context meter shows: a whole percentage, whether it is getting full, and the detail behind it. */
export function usageSummary(usage: Usage): { percent: number; high: boolean; detail: string } {
  const percent = Math.min(100, Math.max(0, Math.round((usage.used / usage.size) * 100)));
  const cost = usage.cost && usage.cost.amount > 0 ? ` · ${usage.cost.currency === 'USD' ? '$' : `${usage.cost.currency} `}${usage.cost.amount.toFixed(2)}` : '';
  return { percent, high: percent >= 80, detail: t('timeline.contextUsed', compactTokens(usage.used), compactTokens(usage.size)) + cost };
}

const MAX_IMAGE_PATHS = 6;

/**
 * Local image files an answer points at: Markdown images, and paths written out in the text.
 * Web addresses are left alone. Whether a file really exists and is an image is for the daemon to say.
 */
export function imagePathsIn(text: string, roots: string[] = []): string[] {
  const found: string[] = [];
  const seen = new Set<string>();
  const add = (raw: string, encoded = false) => {
    let path = raw.replace(/^file:\/\//, '');
    if (encoded || path !== raw) {
      try {
        path = decodeURIComponent(path); // an address, so a space in the name was written as %20
      } catch {
        // Not encoded after all.
      }
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith('//')) return; // http:, data:, …
    // The same returned artifact is often linked absolutely and named relatively in the prose.
    const key = relativePath(path, roots).replace(/^(?:\.\/)+/, '');
    if (!IMAGE_FILE.test(path) || seen.has(key) || found.length >= MAX_IMAGE_PATHS) return;
    seen.add(key);
    found.push(path);
  };
  const rest = text.replace(/!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|((?:\\.|[^\\\s()]|\([^()\s]*\))+))(?:\s+"[^"\n]*")?\s*\)/g, (_match, angle: string | undefined, plain: string) => {
    add((angle ?? plain).replace(/\\([\\()[\]<> ])/g, '$1'), true);
    return '';
  });
  // A path stands on its own: after a space, a quote, a bracket or CJK punctuation, never mid-word or inside a URL.
  for (const m of rest.matchAll(/(?<=^|[\s`'"(（：，。])((?:file:\/\/)?[\p{L}\p{N}_@%+.~\-/]+\.(?:png|jpe?g|gif|webp|svg))(?=$|[\s`'")）：，。,;；!?]|[.:](?:\s|$))/gimu)) {
    add(m[1]);
  }
  return found;
}

/** Add file counts discovered on an explicit work expansion without changing the view layout. */
export function hydrateHistoryFiles(items: Item[], files: Map<string, NonNullable<Extract<Item, { kind: 'work' }>['files']>>): Item[] {
  if (!files.size) return items;
  const finished = new Map<string, string | undefined>();
  const stopped = new Set<string>();
  for (const item of items) {
    if (item.kind === 'actions') finished.set(item.turnId, item.at);
    if (item.kind === 'work' && item.done) {
      if (!finished.has(item.turnId)) finished.set(item.turnId, item.timed ? item.endedAt ?? undefined : undefined);
      if (item.stopped) stopped.add(item.turnId);
    }
  }
  return addTurnSummaries(items.filter(i => i.kind !== 'actions' && i.kind !== 'changes').map(i => {
    if (i.kind !== 'work' || !files.has(`${i.turnId}:${i.key}`)) return i;
    const shown = new Set(i.steps.map(step => step.id));
    const local = new Set((i.liveEvents ?? []).filter(e => e.type === 'tool.start' && shown.has(String(e.data.id))).map(e => e.seq));
    return { ...i, files: files.get(`${i.turnId}:${i.key}`)!.filter(file => !local.has(file.detail.id)) };
  }), finished, stopped);
}

/**
 * Appends, once a turn has finished, the files it changed and the action row for its answer.
 * A turn still running gets neither: its live steps are the story on screen. Turns never overlap
 * in a thread, so a turn ends where the next user item begins.
 */
function addTurnSummaries(items: Item[], finished: Map<string, string | undefined>, stopped: Set<string>): Item[] {
  const out: Item[] = [];
  let turnItems: Item[] = [];

  const flush = () => {
    if (turnItems.length === 0) return;
    const turnId = (turnItems[0] as { turnId: string }).turnId;
    out.push(...turnItems);

    // Only edits that went through count; a refused or failed edit changed nothing.
    const files = new Map<string, FileChange>();
    const addSummary = (summary: { path: string; added: number; removed: number; detail: DetailRef }) => {
      const file = files.get(summary.path) ?? { path: summary.path, added: 0, removed: 0, diffs: [], details: [] };
      file.added += summary.added;
      file.removed += summary.removed;
      file.details ??= [];
      if (!file.details.some(d => d.id === summary.detail.id)) file.details.push(summary.detail);
      files.set(file.path, file);
    };
    for (const it of turnItems) {
      if (it.kind !== 'work') continue;
      for (const file of it.files ?? []) addSummary(file);
      for (const step of it.steps) {
        if (step.status !== 'completed') continue;
        for (const file of step.files ?? []) addSummary(file);
        for (const diff of step.diffs) {
          const file = files.get(diff.path) ?? { path: diff.path, added: 0, removed: 0, diffs: [] };
          file.added += diff.added;
          file.removed += diff.removed;
          file.diffs.push(diff);
          files.set(diff.path, file);
        }
      }
    }
    // The summary belongs to the finished turn, not to the work in flight: while the turn runs,
    // the live steps are the story, and a card of totals would read as an ending it has not had.
    if (files.size > 0 && finished.has(turnId)) {
      const list = [...files.values()];
      out.push({
        kind: 'changes',
        key: `changes-${turnId}`,
        turnId,
        files: list,
        added: list.reduce((n, f) => n + f.added, 0),
        removed: list.reduce((n, f) => n + f.removed, 0),
      });
    }

    if (finished.has(turnId) && !stopped.has(turnId)) {
      const answers = turnItems.filter((it): it is Extract<Item, { kind: 'assistant' | 'proposal' }> => (it.kind === 'assistant' || it.kind === 'proposal') && it.text.trim() !== '');
      const last = answers[answers.length - 1];
      const at = finished.get(turnId);
      if (last) out.push({ kind: 'actions', key: `actions-${turnId}`, turnId, text: last.text, ...(at ? { at } : {}) });
    }
    turnItems = [];
  };

  for (const it of items) {
    // A fork's mark stands between turns: after everything of the turn before it, summaries included.
    if (it.kind === 'user' || it.kind === 'fork') flush();
    if (it.kind === 'fork') out.push(it);
    else turnItems.push(it);
  }
  flush();
  return out;
}

/**
 * When a turn ended, as short as it can be said: the time alone today, the date with it earlier
 * this year, and the year too before that.
 */
export function clockLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (sameDay) return time;
  const thisYear = d.getFullYear() === now.getFullYear();
  return `${formatDate(d, { ...(thisYear ? {} : { year: 'numeric' }), month: 'short', day: 'numeric' })} ${time}`;
}

/** Round before splitting units, so carries never leave a "60s" or "60m" remainder. */
function wholeDurationLabel(seconds: number): string {
  if (!Number.isFinite(seconds)) return '';
  const secs = Math.max(0, Math.round(seconds));
  if (secs < 60) return t('duration.seconds', secs);
  if (secs < 3600) return t('duration.minutes', Math.floor(secs / 60), secs % 60);
  return t('duration.hours', Math.floor(secs / 3600), Math.floor(secs % 3600 / 60), secs % 60);
}

/** Time between the first and last event of a work group, for the "worked for X" label. */
export function durationLabel(startedAt: string, endedAt: string | null): string {
  if (!endedAt) return '';
  return wholeDurationLabel((Date.parse(endedAt) - Date.parse(startedAt)) / 1000);
}

/** A tool call's own duration. Short calls keep one decimal, so instant calls do not all read "0s". */
export function stepDurationLabel(startedAt?: string, endedAt?: string): string {
  if (!startedAt || !endedAt) return '';
  const secs = Math.max(0, (Date.parse(endedAt) - Date.parse(startedAt)) / 1000);
  if (secs < 10) return t('duration.seconds', secs.toFixed(1));
  return wholeDurationLabel(secs);
}

/** The tool kinds the adapters report that have a verb. `other` gets none: the title already speaks. */
const STEP_VERBS = ['execute', 'read', 'edit', 'write', 'create', 'delete', 'fetch', 'search', 'move', 'switch'] as const;
const hasVerb = (kind: string): kind is (typeof STEP_VERBS)[number] => (STEP_VERBS as readonly string[]).includes(kind);

/** `cd <dir> && …` at the start of a command: where it ran, which the row's detail still shows. */
const LEADING_CD = /^cd\s+(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*(?=\S)/;

/** The step's one line, verb first: "Ran npm test". A command is named by what it runs, not by the directory it first steps into. */
export function stepTitle(step: Step): string {
  const title = step.kind === 'execute' ? step.title.replace(LEADING_CD, '') : step.title;
  return hasVerb(step.kind) ? t(`step.${step.kind}`, title) : title;
}

/** The command a step ran, when its input names one. */
export function stepCommand(step: Pick<Step, 'kind' | 'input'>): string | null {
  const command = (step.input as { command?: unknown } | undefined)?.command;
  return step.kind === 'execute' && typeof command === 'string' && command.trim() ? command : null;
}

/** A row shows timing; failure belongs to the tool's expanded detail, not the conversation. */
export function stepStatusLabel(step: Step): string {
  if (stepActive(step)) return t('step.running');
  return stepDurationLabel(step.startedAt, step.endedAt) || (step.status === 'failed' ? t('step.ended') : t('step.done'));
}

/** A call that has started and not come back yet. */
export const stepActive = (step: Step) => step.status === 'in_progress' || step.status === 'pending';

/**
 * A turn's work as it is laid out: what the agent said on the way, and between those remarks the
 * stretches of thinking and tool calls. A stretch of several calls is shown as one line that
 * counts them, so a long turn reads as what was said, not as every command that was run.
 */
export type WorkSection =
  | { kind: 'note'; key: string; text: string }
  | Extract<WorkEntry, { kind: 'compaction' }>
  | { kind: 'run'; key: string; entries: Extract<WorkEntry, { kind: 'thought' | 'step' }>[]; steps: Step[] };

export function compactionLabel(entry: Extract<WorkEntry, { kind: 'compaction' }>): string {
  switch (entry.status) {
    case 'in_progress': return t('compaction.in_progress');
    case 'completed': return entry.automatic ? t('compaction.automatic') : t('compaction.completed');
    case 'failed': return t('compaction.failed');
    case 'cancelled': return t('compaction.cancelled');
    case 'skipped': return t('compaction.skipped');
    default: return t('compaction.unfinished');
  }
}

export function workSections(entries: WorkEntry[]): WorkSection[] {
  const out: WorkSection[] = [];
  for (const entry of entries) {
    if (entry.kind === 'note' || entry.kind === 'compaction') {
      out.push(entry);
      continue;
    }
    let run = out[out.length - 1];
    if (run?.kind !== 'run') {
      run = { kind: 'run', key: `run-${entry.kind === 'step' ? entry.step.id : entry.thought.key}`, entries: [], steps: [] };
      out.push(run);
    }
    run.entries.push(entry);
    if (entry.kind === 'step') run.steps.push(entry.step);
  }
  return out;
}

/** Rebuild the work rows already received live while the lazy historical detail is loading. */
export function liveWorkFromEvents(item: Extract<Item, { kind: 'work' }>): Extract<Item, { kind: 'work' }> | undefined {
  const live = item.liveEvents ?? [];
  if (!live.length) return undefined;
  const first = live.reduce((n, event) => Math.min(n, event.seq), Number.POSITIVE_INFINITY);
  const started: StoredEvent = {
    threadId: live[0]?.threadId ?? '',
    turnId: item.turnId,
    seq: Number.isFinite(first) ? first - 1 : 0,
    type: 'turn.started',
    ts: item.startedAt,
    data: { text: '' },
  };
  const events = live.some(event => event.type === 'turn.started') ? live : [started, ...live];
  return buildTimeline([...events].sort((a, b) => a.seq - b.seq)).items.find((it): it is Extract<Item, { kind: 'work' }> => it.kind === 'work');
}

/** The kinds of call a summary counts apart, in the order it names them. Anything else is "a tool". */
const RUN_GROUPS: Array<{ kinds: string[]; say: (n: number) => string }> = [
  { kinds: ['read'], say: (n) => t('run.read', n) },
  { kinds: ['search'], say: (n) => t('run.search', n) },
  { kinds: ['fetch'], say: (n) => t('run.fetch', n) },
  { kinds: ['edit', 'write'], say: (n) => t('run.edit', n) },
  { kinds: ['create'], say: (n) => t('run.create', n) },
  { kinds: ['delete'], say: (n) => t('run.delete', n) },
  { kinds: ['move'], say: (n) => t('run.move', n) },
  { kinds: ['execute'], say: (n) => t('run.execute', n) },
];

/**
 * A stretch of tool calls in one line: "Explored 2 files, searched 1 time". Also which kind of call
 * there was most of (for the row's icon). Each call keeps its result in its own detail.
 */
export function runSummary(steps: Step[]): { text: string; kind: string } {
  const parts: string[] = [];
  let lead = { kind: 'other', count: 0 };
  const count = (kind: string, n: number, say: (n: number) => string) => {
    if (n === 0) return;
    parts.push(say(n));
    if (n > lead.count) lead = { kind, count: n };
  };
  const known = new Set(RUN_GROUPS.flatMap((g) => g.kinds));
  for (const group of RUN_GROUPS) {
    const calls = steps.filter((s) => group.kinds.includes(s.kind));
    // Three edits to one file changed one file. A call that does not say what it wrote counts as one.
    const files = new Set(calls.flatMap((s) => (s.diffs.length ? s.diffs.map((d) => d.path) : [`call:${s.id}`])));
    count(group.kinds[0], group.kinds[0] === 'edit' ? files.size : calls.length, group.say);
  }
  count('other', steps.filter((s) => !known.has(s.kind)).length, (n) => t('run.other', n));
  return { text: parts.length ? t('run.summary', parts) : '', kind: lead.kind };
}

/** Thinking text the model wrote as one stream is shown as short paragraphs. */
const ABBREV_END = /(?:e\.g|i\.e|etc|vs|Fig|Eq|No)\.$/i;

/**
 * Splits a thinking stream into readable paragraphs. Paragraph breaks the model did write are
 * kept; sentences that were concatenated without a space ("codebase.Let me") are split apart so
 * the stream does not read as one jammed line. Common abbreviations are left alone.
 */
export function splitThinking(text: string): string[] {
  const out: string[] = [];
  for (const paragraph of text.split(/\n{2,}/)) {
    const trimmed = paragraph.replace(/\s+/g, ' ').trim();
    if (!trimmed) continue;
    const sentences: string[] = [];
    let start = 0;
    for (let i = 0; i < trimmed.length; i++) {
      const ch = trimmed[i];
      if (ch !== '.' && ch !== '。' && ch !== '！' && ch !== '？' && ch !== '!' && ch !== '?') continue;
      const after = trimmed[i + 1];
      // A sentence ends where the next word starts a fresh one: a capital, a digit or CJK. Quotes
      // and brackets let ".)Next" style endings through.
      if (!after || !/[A-Z0-9\u4e00-\u9fff""'(（]/.test(after)) continue;
      const soFar = trimmed.slice(start, i + 1);
      if (ABBREV_END.test(soFar.trimEnd())) continue;
      sentences.push(soFar.trim());
      start = i + 1;
    }
    sentences.push(trimmed.slice(start).trim());
    out.push(...sentences.filter(Boolean));
  }
  return out;
}

/** How much of the thinking in progress is shown under "Thinking". */
const THINKING_PREVIEW_CHARS = 140;

/**
 * The latest of a thinking stream in progress: the end of its last paragraph, cut to a few lines.
 * It is the end that is shown, because that is where the stream is being written.
 */
export function thinkingPreview(paragraphs: string[]): string {
  const last = paragraphs.at(-1) ?? '';
  if (last.length <= THINKING_PREVIEW_CHARS) return last;
  const tail = last.slice(-THINKING_PREVIEW_CHARS);
  // Start at a word where there is one to start at; CJK text has no spaces and is cut as it is.
  const space = tail.indexOf(' ');
  return `…${space >= 0 && space < 24 ? tail.slice(space + 1) : tail}`;
}

/** Items of one turn, in order. A turn starts at its user item; stray items share the '' group. */
export interface TurnGroup {
  turnId: string;
  items: Item[];
}

/** Groups a timeline into turns, for the "render only the recent turns" collapse. */
export function groupByTurn(items: Item[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  for (const it of items) {
    const last = groups[groups.length - 1];
    if (it.kind === 'user' || it.kind === 'handover-summary' || !last) groups.push({ turnId: it.turnId ?? '', items: [it] });
    else last.items.push(it);
  }
  return groups;
}

/** What the ruler's preview popup shows for one turn: the question and how the answer starts. */
export interface TurnPreview {
  question: string;
  answer: string;
}

/** Previews keyed by turn id, built from the same items the ruler marks. */
export function turnPreviews(items: Item[]): Map<string, TurnPreview> {
  const out = new Map<string, TurnPreview>();
  for (const group of groupByTurn(items)) {
    const question = group.items.find((it): it is Extract<Item, { kind: 'user' }> => it.kind === 'user');
    if (!question) continue;
    const answer = group.items
      .filter((it): it is Extract<Item, { kind: 'assistant' }> => it.kind === 'assistant')
      .map((it) => it.text.trim())
      .filter(Boolean)
      .join('\n')
      .slice(0, 140);
    out.set(group.turnId, { question: question.text.trim(), answer });
  }
  return out;
}

/** The conversation as Markdown, for "copy conversation". Tool output is left out; file changes are listed. */
export function toMarkdown(title: string, items: Item[]): string {
  const parts: string[] = [`# ${title}`];
  for (const it of items) {
    switch (it.kind) {
      case 'user': {
        const images = it.images.length ? `\n\n${t('markdown.images', it.images.length)}` : '';
        parts.push(`## ${t('markdown.you')}\n\n${it.text}${images}`.trimEnd());
        break;
      }
      case 'proposal':
      case 'assistant':
        if (it.text.trim()) parts.push(it.text.trim());
        break;
      case 'changes':
        parts.push(
          [`**${t('markdown.changed', it.files.length)}** (+${it.added} −${it.removed})`, ...it.files.map((f) => `- \`${f.path}\` +${f.added} −${f.removed}`)].join('\n'),
        );
        break;
      case 'error':
        parts.push(`> ${t('markdown.error', it.message)}`);
        break;
      default:
        break;
    }
  }
  return parts.join('\n\n') + '\n';
}
