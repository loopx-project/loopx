import { t } from './i18n';
import type { TeamStatus, ThreadBrief } from './api';

export type PetClip = 'idle' | 'thinking' | 'success' | 'poke' | 'head_pat' | 'idle_yawn';

/** How she shows up away from the desk: always there, only when something needs the person, or off. */
export type PetPresence = 'always' | 'event' | 'off';

/** A thread as her status bar reads it. Only the fields the bar needs; `Thread` satisfies this. */
export interface PetThreadLike {
  id: string;
  status: 'idle' | 'running' | 'error';
  waiting: boolean;
  waitingReason?: 'approval' | 'question' | 'plan' | null;
  unread: boolean;
  updatedAt: string;
  doneAt?: string | null;
  /** Why it stopped, while the status is `error`. Missing from older daemons. */
  failure?: { code: string | null; message: string; at: string } | null;
  handover?: { sourceId: string } | null;
  autoHandover?: { resume?: { resetsAt: string | null } | null };
  report?: { note: string } | null;
  coordination?: { team?: { status: TeamStatus; error?: string } } | null;
}

/**
 * What a thread is doing, as the bar names it: waiting for the person, stopped on an error, finished
 * and unread, running, waiting for its quota with a resume armed, stopped long ago, or nothing.
 */
export type PetThreadState = 'waiting' | 'failed' | 'review' | 'running' | 'parked' | 'stale' | 'idle';

/** The order the bar and the tray put them in: what needs the person first. */
export const PET_THREAD_ORDER: Record<PetThreadState, number> = { waiting: 0, failed: 1, review: 2, running: 3, parked: 4, stale: 5, idle: 6 };

/** A stop older than this is history rather than something to chase: it folds away in the tray. */
export const PET_STALE_MS = 3 * 24 * 3600_000;

/** Reads a thread's state from what the page already holds; a team member's word outranks the thread's own. */
export function petThreadState(thread: PetThreadLike, now = Date.now()): PetThreadState {
  const team = thread.coordination?.team?.status;
  if (team === 'awaiting_confirm' || thread.waiting) return 'waiting';
  // Marked done on the board: whatever it last did, the person has closed it.
  if (thread.doneAt) return 'idle';
  if (team === 'needs_attention') return 'failed';
  if (thread.status === 'error') {
    if (thread.autoHandover?.resume) return 'parked';
    const at = Date.parse(thread.failure?.at ?? thread.updatedAt);
    return now - at > PET_STALE_MS ? 'stale' : 'failed';
  }
  if (thread.unread) return 'review';
  if (team === 'running' || thread.status === 'running') return 'running';
  return 'idle';
}

/** What she can do about a thread from where she is: send the failed ask again, or take the person there. */
export type PetStep = 'retry' | 'open';

/** Why a thread wants the person, in a few words, and the step she suggests. `since` dates a stop. */
export interface PetAdvice {
  why: string;
  step: PetStep | null;
  label: string;
  since?: string;
  /** Verbatim text that follows `why`, set in monospace: a command to be run. */
  code?: string;
}

export type PetFailureKind = 'quota' | 'interrupted' | 'permission' | 'auth' | 'transient' | 'other';

/** Sorts a failure by what fixes it. Providers word quota and login trouble differently; the code wins when there is one. */
export function petFailureKind(failure: { code: string | null; message: string }): PetFailureKind {
  if (failure.code === 'plan_exhausted') return 'quota';
  if (failure.code === 'interrupted') return 'interrupted';
  if (failure.code === 'permission_refused') return 'permission';
  const message = failure.message;
  // Escaped CJK: "limit", "quota" and "sign in" as Chinese providers word them.
  if (/\b429\b|quota|rate.?limit|usage limit|session limit|exhausted|\u4e0a\u9650|\u9650\u989d|\u989d\u5ea6/i.test(message)) return 'quota';
  if (/authenticat|unauthori[sz]ed|log(?:ged)? ?in|sign(?:ed)? ?in|\u767b\u5f55/i.test(message)) return 'auth';
  // The server's own trouble, gone on its own: overload, a bad gateway, a dropped connection, a timeout.
  if (/\b(?:502|503|504|529)\b|overloaded|service unavailable|bad gateway|gateway time-?out|econnreset|etimedout|socket hang up|connection reset|time(?:d)?[ -]?out/i.test(message)) return 'transient';
  return 'other';
}

/** The first line of a provider's error, without the wrapper words, short enough for one line. */
export function petFailureLine(message: string): string {
  const line = message.replace(/^(Internal error|Error|API Error):\s*/i, '').split('\n')[0].trim();
  const chars = Array.from(line);
  return chars.length > 48 ? chars.slice(0, 47).join('') + '…' : line;
}

/** Her reading of one thread: why it is here and what she would do next. */
export function petAdvice(thread: PetThreadLike, now = Date.now()): PetAdvice {
  const state = petThreadState(thread, now);
  const team = thread.coordination?.team;
  if (state === 'waiting') {
    if (thread.waitingReason === 'approval') return { why: t('pet.why.approval'), step: 'open', label: t('pet.step.approve') };
    if (thread.waitingReason === 'question') return { why: t('pet.why.question'), step: 'open', label: t('pet.step.answer') };
    if (thread.waitingReason === 'plan') return { why: t('pet.why.plan'), step: 'open', label: t('pet.step.readPlan') };
    return { why: t('pet.why.confirm'), step: 'open', label: t('pet.step.confirm') };
  }
  if (state === 'parked') return { why: t('pet.why.parked'), step: 'open', label: t('pet.goSee') };
  if (state === 'failed' || state === 'stale') {
    if (team?.status === 'needs_attention' && thread.status !== 'error') {
      return { why: team.error ? petFailureLine(team.error) : t('pet.why.team'), step: 'open', label: t('pet.goSee') };
    }
    const failure = thread.failure;
    if (!failure) return { why: t('pet.why.failed'), step: 'open', label: t('pet.goSee'), since: thread.updatedAt };
    const since = failure.at;
    switch (petFailureKind(failure)) {
      case 'quota': return { why: t('pet.why.quota'), step: 'retry', label: t('pet.step.continue'), since };
      case 'interrupted': return { why: t('pet.why.interrupted'), step: 'retry', label: t('pet.step.continue'), since };
      case 'permission': return { why: t('pet.why.permission'), step: 'open', label: t('pet.step.fix'), since };
      case 'auth': return { why: t('pet.why.auth'), step: 'open', label: t('pet.goSee'), since };
      case 'transient': return { why: t('pet.why.transient'), step: 'retry', label: t('pet.step.retry'), since };
      default: return { why: petFailureLine(failure.message) || t('pet.why.failed'), step: 'retry', label: t('pet.step.retry'), since };
    }
  }
  if (state === 'review') return { why: thread.report?.note || t('pet.why.review'), step: 'open', label: t('pet.step.review') };
  if (state === 'running') return { why: thread.report?.note || t('pet.why.running'), step: null, label: '' };
  return { why: '', step: null, label: '' };
}

/** A failure she can read more into: one a retry or a look can fix, not one that needs a login or an approval. */
function briefableFailure(thread: PetThreadLike): boolean {
  return thread.status === 'error' && !!thread.failure && ['quota', 'interrupted', 'transient', 'other'].includes(petFailureKind(thread.failure));
}

/**
 * Whether the thread's brief would sharpen what she says: it waits on an approval, stopped on an
 * error a retry could meet, or finished with nothing of her own to report. Everything else is
 * already said in full, so nothing is asked for.
 */
export function petBriefWanted(thread: PetThreadLike, now = Date.now()): boolean {
  const state = petThreadState(thread, now);
  if (state === 'waiting') return thread.waitingReason === 'approval';
  if (state === 'failed') return briefableFailure(thread);
  if (state === 'review') return !thread.report?.note;
  return false;
}

const LOCK_FILE = /(^|[-._])lock(\.\w+)?$|\.lockb$|^go\.sum$/i;
const baseName = (path: string): string => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** Drops the full stop a sentence ends in, so it can be joined to another or given its own. */
const withoutStop = (text: string): string => text.trim().replace(/[.。!！?？…]+$/, '');

/** What a pending approval asks for, as one short sentence; '' when the agent said too little to tell. */
function approvalSentence(approval: NonNullable<ThreadBrief['approval']>): string {
  if (approval.action === 'run') return approval.command ? t('pet.brief.run') : '';
  if (approval.action === 'edit' || approval.action === 'delete') {
    if (!approval.paths.length) return '';
    const names = approval.paths.map(baseName);
    const total = names.length + approval.moreFiles;
    const lockOnly = approval.action === 'edit' && !approval.moreFiles && approval.paths.every(path => LOCK_FILE.test(baseName(path)));
    return t(approval.action === 'edit' ? 'pet.brief.edit' : 'pet.brief.delete', names, total, lockOnly);
  }
  return approval.title ? t('pet.brief.other', approval.title) : '';
}

/**
 * Her reading of one thread with what the daemon found in its log: the same advice, with the
 * plain classification replaced by the specific thing (what waits for a yes, whether a retry is
 * safe, what the finished turn did). A brief for another version of the thread, or for a
 * different kind of matter, says nothing about this one and changes nothing.
 */
export function petBriefAdvice(thread: PetThreadLike, brief: ThreadBrief | null | undefined, now = Date.now()): PetAdvice {
  const advice = petAdvice(thread, now);
  if (!brief || brief.updatedAt !== thread.updatedAt || !petBriefWanted(thread, now)) return advice;
  const state = petThreadState(thread, now);
  if (state === 'waiting' && brief.kind === 'approval' && brief.approval) {
    const why = approvalSentence(brief.approval);
    if (!why) return advice;
    // A command is shown as it is, apart from the words around it.
    return { ...advice, why, code: brief.approval.action === 'run' ? brief.approval.command ?? undefined : undefined };
  }
  if (state === 'failed' && brief.kind === 'failure' && brief.failure && advice.step === 'retry') {
    const { failure } = brief;
    // A watch on the quota already resends it, and says so.
    if (failure.resume) return advice;
    // The same error again and again: sending it a fourth time will not change it.
    if (failure.repeats >= 3) return { ...advice, why: t('pet.brief.repeated', failure.repeats), step: 'open', label: t('pet.goSee') };
    const judgement = failure.edits === 0 ? t('pet.brief.noEdits') : t('pet.brief.edited', failure.files);
    const cause = withoutStop(advice.why);
    return { ...advice, why: t('pet.brief.join', cause, judgement, /[A-Za-z0-9]$/.test(cause)) };
  }
  if (state === 'review' && brief.kind === 'result' && brief.result) {
    const { first, files } = brief.result;
    const said = first ? withoutStop(first) : '';
    return said || files > 0 ? { ...advice, why: t('pet.brief.done', said || null, files) } : advice;
  }
  return advice;
}

/**
 * The threads worth a place under her, most needing first, and the old stops folded away. A thread
 * whose work was handed to another is history, and so is one the person set aside, until it changes.
 * What changed most recently breaks ties.
 */
export function petTriage<T extends PetThreadLike>(threads: readonly T[], now = Date.now(), dismissed: Readonly<Record<string, string>> = {}): { active: T[]; earlier: T[] } {
  const handedOn = new Set(threads.map(thread => thread.handover?.sourceId).filter(Boolean));
  const entries = threads
    .filter(thread => !handedOn.has(thread.id) && dismissed[thread.id] !== thread.updatedAt)
    .map(thread => ({ thread, state: petThreadState(thread, now) }))
    .filter(entry => entry.state !== 'idle')
    .sort((a, b) => PET_THREAD_ORDER[a.state] - PET_THREAD_ORDER[b.state] || b.thread.updatedAt.localeCompare(a.thread.updatedAt));
  return {
    active: entries.filter(entry => entry.state !== 'stale').map(entry => entry.thread),
    earlier: entries.filter(entry => entry.state === 'stale').map(entry => entry.thread),
  };
}

/** Whether she brings a thread up on her own: it asks the person to act, or it finished and nobody has looked. */
export function petSpeaksOf(state: PetThreadState): boolean {
  return state === 'waiting' || state === 'failed' || state === 'review';
}

/** Whether a thread asks the person to act: waiting on them or stopped. Finished, running and parked do not. */
export function petNeedsPerson(state: PetThreadState): boolean {
  return state === 'waiting' || state === 'failed';
}

/** A reply starts fading after this long and is gone a moment after that. */
export const PET_BUBBLE_FADE_MS = 8_000;
export const PET_BUBBLE_GONE_MS = 11_000;

/** Keeps her whole self inside the viewport, however it is resized. */
export function petClampPosition(x: number, y: number, w: number, h: number, viewW: number, viewH: number): { x: number; y: number } {
  const maxX = Math.max(8, viewW - w - 8);
  const maxY = Math.max(8, viewH - h - 8);
  return { x: Math.min(Math.max(8, x), maxX), y: Math.min(Math.max(8, y), maxY) };
}

/** What the desk knows when she speaks. Null until the matters have loaded: no data is not "nothing urgent". */
export type PetGreeting = { you: number; first: string | null } | null;

/** The size of every frame in the artwork. */
export const PET_FRAME = { width: 412, height: 344 };

/** Her small portrait, cut from the first idle frame: a picture the size of where it is shown. */
export interface PetBust { src: string; aspect: number }

/** The height the cut portrait keeps: enough for every place it is shown, small enough to keep on the device. */
export const PET_BUST_HEIGHT = 160;

/** Frames kept decoded: the one on screen and those just ahead, about half a megabyte each. */
export const PET_RING_SIZE = 8;
export const PET_WEBP_QUALITY = 0.9;
export const PET_CACHE_PREFIX = 'calli-pet-';

/** Named after the artwork and how it is cleaned, so changing either starts over. */
export function petCacheName(id: string, commit: string): string {
  return `${PET_CACHE_PREFIX}${id}-${commit}-1`;
}

/** The license that came with the artwork, at the commit the hub pinned. */
export function petLicenseUrl(commit: string): string {
  return `https://github.com/QCYTSN/dsh-dafeiyu/blob/${commit}/assets/dsh-pet-LICENSE.txt`;
}

/** Never fetched: Cache Storage only wants an address of this origin to file the frame under. */
export function petCacheKey(clip: PetClip, index: number): string {
  return `/calli-pet-frames/${clip}/${index}.webp`;
}

/** The frames to keep decoded: this one and the next ones, wrapping if it loops, stopping at the end if not. */
export function petRing(frame: number, frames: number, loop: boolean, size: number): number[] {
  const ring: number[] = [];
  for (let step = 0; step < Math.min(size, frames); step += 1) {
    if (!loop && frame + step >= frames) break;
    ring.push((frame + step) % frames);
  }
  return ring;
}

/** The frame after this one, or null when a one-shot clip has played out. */
export function nextPetFrame(frame: number, frames: number, loop: boolean): number | null {
  if (frame + 1 < frames) return frame + 1;
  return loop ? 0 : null;
}

/** How long she stays awake between yawns: 40 to 80 seconds. */
export function yawnDelay(random: () => number = Math.random): number {
  return 40_000 + random() * 40_000;
}

/** Local date, so "once a day" turns over at the person's own midnight. */
export function petDay(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** What she says on the first visit of the day: what needs the person, or something light when nothing does. */
export function petGreetingLine(greeting: NonNullable<PetGreeting>, now: Date): string {
  if (greeting.you > 0) return t('pet.greeting', greeting.you, greeting.first);
  const calm = [t('pet.calm1'), t('pet.calm2'), t('pet.calm3')];
  return calm[now.getDate() % calm.length];
}

/** The openings offered above the keyboard. Each is a label and the words it puts in the field. */
export function petStarters(): { label: string; start: string }[] {
  return [
    { label: t('pet.urgent'), start: t('pet.urgentStart') },
    { label: t('pet.note'), start: t('pet.noteStart') },
    { label: t('pet.done'), start: t('pet.doneStart') },
  ];
}

/** Puts an opening in front of what is written, in place of another opening if one is already there. */
export function applyPetStarter(draft: string, start: string, starts: readonly string[]): string {
  const opening = starts.find((s) => draft.startsWith(s));
  return start + (opening ? draft.slice(opening.length) : draft);
}

/** A message on its way to the Router. Kept across pages, because leaving the desk must not lose the answer. */
export interface PetPending {
  /** The Router's last reply when this was sent: only something newer answers it. */
  since: string | null;
  threadId: string;
  sentAt: number;
  /** What was said, to send again if no answer comes. */
  text: string;
}

/** After this long she says she is still at it; the wait goes on. */
export const PET_LATE_MS = 2 * 60_000;
/** After this long the answer is left in the conversation. */
export const PET_GIVE_UP_MS = 30 * 60_000;
/** Polls in a row with the Router at rest and nothing new before it counts as having ended without a reply. */
export const PET_QUIET_POLLS = 6;
/** Failed polls in a row before she says she cannot reach the hub. */
export const PET_LOST_POLLS = 5;

export function parsePetPending(raw: string | null, now: number): PetPending | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<PetPending>;
    if (typeof value.threadId !== 'string' || typeof value.sentAt !== 'number') return null;
    if (now - value.sentAt > PET_GIVE_UP_MS) return null;
    return { since: typeof value.since === 'string' ? value.since : null, threadId: value.threadId, sentAt: value.sentAt, text: typeof value.text === 'string' ? value.text : '' };
  } catch {
    return null;
  }
}

export interface PetRouterReading {
  threadId: string | null;
  status: string | null;
  lastReply: string | null;
  lastReplyAt: string | null;
}

export type PetWait = 'waiting' | 'quiet' | 'failed' | { reply: string };

/** Reads one poll of the Router: answered, gone wrong, or still to wait for. `restingPolls` counts this one. */
export function readPetRouter(router: PetRouterReading, since: string | null, restingPolls: number): PetWait {
  const fresh = Boolean(router.lastReplyAt && router.lastReply) && (!since || Date.parse(router.lastReplyAt!) > Date.parse(since));
  if (fresh) return { reply: petReplyPreview(router.lastReply!) };
  if (router.status === 'error') return 'failed';
  if (router.status !== 'running' && restingPolls >= PET_QUIET_POLLS) return 'quiet';
  return 'waiting';
}

/**
 * The artwork carries a sheet of nearly invisible black (alpha 1 to 3) across every frame, which shows
 * as a faint box on a pale page. Clears it in place; real edges and sparkles are well above this.
 */
export function clearPetHaze(pixels: Uint8ClampedArray): void {
  for (let alpha = 3; alpha < pixels.length; alpha += 4) {
    if (pixels[alpha] <= 3) pixels[alpha] = 0;
  }
}

/** How much of her height, from the top of her hair, a small portrait keeps: head and upper body. */
export const PET_BUST = 0.68;

/**
 * The box around her head and upper body in one frame, trimmed to her outline: nothing but the
 * artwork, no margin of empty canvas. Null for a frame with nothing in it.
 */
export function petBustBox(pixels: Uint8ClampedArray, width: number, height: number, keep = PET_BUST): { x: number; y: number; width: number; height: number } | null {
  const solid = (x: number, y: number) => pixels[(y * width + x) * 4 + 3] > 24;
  let top = -1, bottom = -1;
  for (let y = 0; y < height && top < 0; y++) for (let x = 0; x < width; x++) if (solid(x, y)) { top = y; break; }
  if (top < 0) return null;
  for (let y = height - 1; y >= top && bottom < 0; y--) for (let x = 0; x < width; x++) if (solid(x, y)) { bottom = y; break; }
  const end = top + Math.max(1, Math.round((bottom - top + 1) * keep));
  let left = width, right = -1;
  for (let y = top; y < end; y++) for (let x = 0; x < width; x++) if (solid(x, y)) { if (x < left) left = x; if (x > right) right = x; }
  return { x: left, y: top, width: right - left + 1, height: end - top };
}

/** The technical part of a failure, for the details line: never the addresses in it. Null when nothing is left. */
export function petErrorDetail(error: unknown): string | null {
  const message = error instanceof Error ? error.message : String(error ?? '');
  const detail = message.replace(/\b[a-z]+:\/\/\S+/gi, '').replace(/[\s:：,，-]+$/, '').trim();
  return detail || null;
}

export type PetReplyPart = { text: string; todoId?: string };

/** Splits a reply around the matters it names, longest name first, so each can be opened from the bubble. */
export function petReplyParts(reply: string, todos: readonly { id: string; text: string }[]): PetReplyPart[] {
  const names = todos.filter((todo) => Array.from(todo.text).length >= 2).sort((a, b) => b.text.length - a.text.length);
  const parts: PetReplyPart[] = [];
  let rest = reply;
  while (rest) {
    let first: { at: number; todo: { id: string; text: string } } | null = null;
    for (const todo of names) {
      const at = rest.indexOf(todo.text);
      if (at >= 0 && (!first || at < first.at)) first = { at, todo };
    }
    if (!first) { parts.push({ text: rest }); break; }
    if (first.at > 0) parts.push({ text: rest.slice(0, first.at) });
    parts.push({ text: first.todo.text, todoId: first.todo.id });
    rest = rest.slice(first.at + first.todo.text.length);
  }
  return parts;
}

/** Keep the bubble short without cutting a surrogate pair. The Router is asked for 100 characters, not forced to. */
export function petReplyPreview(text: string): string {
  const chars = Array.from(text.trim());
  return chars.length > 100 ? chars.slice(0, 99).join('') + '…' : chars.join('');
}
