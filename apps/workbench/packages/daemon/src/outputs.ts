import { isAbsolute, relative, resolve, sep } from 'node:path';
import { stat } from 'node:fs/promises';
import { homedir } from 'node:os';

/**
 * What a thread has produced, gathered over all its turns for the overview beside the conversation:
 * the files its agents changed, as they reported them, and the pictures and files its answers point
 * at. Only names and counts; diffs, pictures and file text are read when one is opened.
 */
export interface ChangedFile {
  path: string;
  added: number;
  removed: number;
  /** The thread's first change to it wrote it from nothing. */
  created: boolean;
  /** How many turns changed it, and the last of them. */
  turns: number;
  turnId: string;
  at: string;
  /** Latest 20 diff references, in log order; bodies are read only when opened. */
  details: Array<{ turnId: string; id: number; version: number }>;
  /** Present only when older references were omitted. */
  detailsTotal?: number;
  /** The last disk check found no ordinary file at this path. */
  missing?: true;
}

export interface MentionedFile {
  path: string;
  /** The last answer that named it. */
  turnId: string;
  at: string;
  /** The last disk check found no ordinary file at this path. */
  missing?: true;
}

export interface ThreadOutputs {
  changes: { files: ChangedFile[]; added: number; removed: number; total: number };
  images: MentionedFile[];
  files: MentionedFile[];
  /** The log position this was read at, so a caller can tell a fresh list from a stale one. */
  seq: number;
}

/** Enough for any overview; a thread that wrote more is summarised by `total`. */
export const MAX_OUTPUT_ITEMS = 200;

/** Inspect only the returned names; duplicate paths across groups share one bounded check. */
export async function checkMissingFiles(outputs: ThreadOutputs): Promise<ThreadOutputs> {
  const paths = [...new Set([...outputs.changes.files, ...outputs.images, ...outputs.files].map(f => f.path))];
  const missing = new Set<string>();
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(16, paths.length) }, async () => {
    for (let i = next++; i < paths.length; i = next++) {
      const path = paths[i];
      try {
        if (!(await stat(path)).isFile()) missing.add(path);
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (code !== 'ENOENT' && code !== 'ENOTDIR') throw error;
        missing.add(path);
      }
    }
  }));
  const mark = <T extends MentionedFile | ChangedFile>(file: T): T => {
    const copy = { ...file };
    if (missing.has(file.path)) copy.missing = true;
    else delete copy.missing;
    return copy;
  };
  return { ...outputs, changes: { ...outputs.changes, files: outputs.changes.files.map(mark) }, images: outputs.images.map(mark), files: outputs.files.map(mark) };
}

export const IMAGE_PATH = /\.(?:png|jpe?g|gif|webp|svg)$/i;
/** Pictures the user sent, which the agent opens by their stored path: inputs, not outputs. */
const ATTACHMENT = /\/attachments\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.[a-z]+$/i;

export interface DiffEvent {
  turnId: string | null; seq: number; ts: string;
  data: { id: string; files?: Array<{ path: string; added: number; removed: number; created: boolean; detail?: ChangedFile['details'][number] }> };
}
export interface Answer { turnId: string; seq: number; ts: string; text: string }
interface OrderedChange extends ChangedFile { seq: number }

const expandHome = (path: string) => path.startsWith('~/') ? resolve(homedir(), path.slice(2)) : path;

/** A path as the thread names it, made absolute against where the thread runs. */
export function outputPath(path: string, cwd: string, cwdReal = cwd): string {
  path = expandHome(path);
  if (!isAbsolute(path)) return resolve(cwdReal, path);
  const inside = relative(cwd, path);
  return inside !== '..' && !inside.startsWith(`..${sep}`) && !isAbsolute(inside) ? resolve(cwdReal, inside) : resolve(path);
}

export function changedFiles(events: DiffEvent[], cwd: string, cwdReal = cwd): Omit<ThreadOutputs['changes'], 'files'> & { files: OrderedChange[] } {
  // A tool call reports its diffs again with each update; the last report is what it did.
  const calls = new Map<string, { turnId: string; seq: number; ts: string; diffs: NonNullable<DiffEvent['data']['files']> }>();
  for (const ev of events) {
    if (!ev.turnId) continue;
    const diffs = ev.data.files?.filter(f => f.path) ?? [];
    if (diffs.length) calls.set(`${ev.turnId}\0${ev.data.id}`, { turnId: ev.turnId, seq: ev.seq, ts: ev.ts, diffs });
  }
  const files = new Map<string, OrderedChange & { turnSet: Set<string>; detailCount: number }>();
  for (const call of calls.values()) {
    const referenced = new Set<string>();
    for (const d of call.diffs) {
      const path = outputPath(d.path, cwd, cwdReal);
      const { added, removed } = d;
      let file = files.get(path);
      if (!file) {
        file = { path, added, removed, created: d.created, turns: 1, turnId: call.turnId, at: call.ts, seq: call.seq, turnSet: new Set([call.turnId]), details: [], detailCount: 0 };
        files.set(path, file);
      } else {
        file.added += added;
        file.removed += removed;
        file.turnSet.add(call.turnId);
        file.turns = file.turnSet.size;
        if (call.seq >= file.seq) { file.at = call.ts; file.turnId = call.turnId; file.seq = call.seq; }
      }
      if (d.detail && !referenced.has(path)) {
        referenced.add(path);
        file.detailCount++;
        file.details.push(d.detail);
        // Calls can interleave, so insertion order is not their final report order.
        file.details.sort((a, b) => a.version - b.version);
        if (file.details.length > 20) file.details.shift();
      }
    }
  }
  const all = [...files.values()].map(({ turnSet: _turns, detailCount, ...file }) => ({ ...file, ...(detailCount > 20 ? { detailsTotal: detailCount } : {}) }))
    .sort((a, b) => b.seq - a.seq || a.path.localeCompare(b.path));
  return {
    files: all,
    added: all.reduce((n, f) => n + f.added, 0),
    removed: all.reduce((n, f) => n + f.removed, 0),
    total: all.length,
  };
}

const LINK = /!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|((?:\\.|[^\\\s()]|\([^()\s]*\))+))(?:\s+"[^"\n]*")?\s*\)/g;
// Bare angle references need a file URL, another path segment or a filename extension.
const QUOTED = /([`'"])((?:(?:file:\/\/)?\/|~\/)[^\r\n]*?)\1|<((?:file:\/\/\/|\/[^/>\r\n]+[/.])[^>\r\n]+)>/gi;
const ABSOLUTE = /(?<=^|[\s`'"(（：，。])((?:(?:file:\/\/)?\/|~\/)[^\s`'"<>()，。；！？]+)/gi;
/** A path written out in the prose: after a space, quote or bracket, never mid-word or inside a URL. */
const BARE = /(?<=^|[\s`'"(（：，。])((?:file:\/\/)?[\p{L}\p{N}_@%+.~\-/]+\.[\p{L}\p{N}]{1,8})(?=$|[\s`'")）：，。,;；!]|[.:](?:\s|$)|:\d)/gmu;

/** The local paths an answer names: links of any kind, and paths standing on their own. */
export function pathsIn(text: string): Array<{ path: string; linked: boolean }> {
  const out: Array<{ path: string; linked: boolean }> = [];
  const add = (raw: string, linked: boolean) => {
    let path = raw.trim();
    const fileUrl = /^file:\/\//i.test(path);
    if (fileUrl) path = path.slice(7);
    if (fileUrl && !isAbsolute(path)) return;
    if (linked || fileUrl) {
      try { path = decodeURIComponent(path); } catch { /* A literal percent sign. */ }
    }
    path = path.replace(/(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)$/, '');
    if (!path || path.startsWith('#') || path.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(path) || /[*?[{]/.test(path)) return;
    // Reject abbreviated paths and directory markers before resolve() hides them.
    const segments = path.split('/');
    if (path.includes('…') || segments.includes('...') || !/[\p{L}\p{N}]/u.test(segments.at(-1)!)) return;
    out.push({ path: expandHome(path), linked });
  };
  const mask = (value: string) => '\u0000'.repeat(value.length);
  let rest = text.replace(LINK, (match: string, angle: string | undefined, plain: string) => {
    add((angle ?? plain).replace(/\\([\\()[\]<> ])/g, '$1'), true);
    return mask(match);
  });
  rest = rest.replace(QUOTED, (match: string, _quote: string, quoted: string | undefined, angle: string) => {
    add(quoted ?? angle, false);
    return mask(match);
  });
  rest = rest.replace(ABSOLUTE, (match: string, path: string) => {
    add(path.replace(/[.,;!：]+$/, ''), false);
    return mask(match);
  });
  for (const m of rest.matchAll(BARE)) add(m[1], false);
  return out;
}

/**
 * Pictures are every image path an answer names. Files are what it hands over on purpose: a link,
 * or an absolute path written out; a relative name in passing ("see src/a.ts") is talk, not output.
 * Files the thread created count as well. Sorting is stable, so an answer stays after the edits of its turn.
 */
export function mentionedFiles(answers: Answer[], cwd: string, created: OrderedChange[] = [], cwdReal = cwd): { images: MentionedFile[]; files: MentionedFile[] } {
  const images = new Map<string, MentionedFile>();
  const files = new Map<string, MentionedFile>();
  // What the thread wrote from nothing is output too, named or not, as Codex lists generated files.
  const made = created.filter(f => f.created).map(f => ({ turnId: f.turnId, seq: f.seq, ts: f.at, path: f.path }));
  const events = [...answers.map(a => ({ ...a, made: null as string | null })), ...made.map(m => ({ turnId: m.turnId, seq: m.seq, ts: m.ts, text: '', made: m.path }))]
    .sort((a, b) => a.seq - b.seq);
  for (const answer of events) {
    if (answer.made) {
      if (ATTACHMENT.test(answer.made)) continue;
      const into = IMAGE_PATH.test(answer.made) ? images : files;
      into.delete(answer.made);
      into.set(answer.made, { path: answer.made, turnId: answer.turnId, at: answer.ts });
      continue;
    }
    for (const { path: raw, linked } of pathsIn(answer.text)) {
      const path = outputPath(raw, cwd, cwdReal);
      if (ATTACHMENT.test(path)) continue;
      const into = IMAGE_PATH.test(path) ? images : linked || isAbsolute(raw) ? files : null;
      if (!into) continue;
      into.delete(path); // Named again: it moves to the newest.
      into.set(path, { path, turnId: answer.turnId, at: answer.ts });
    }
  }
  const newest = (m: Map<string, MentionedFile>) => [...m.values()].reverse().slice(0, MAX_OUTPUT_ITEMS);
  return { images: newest(images), files: newest(files) };
}
