import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { closeSync, openSync, readdirSync, readSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Looking through a thread's directory, read only: one folder at a time for the file browser, and
 * a text file's contents for its viewer. Nothing here writes. Which paths may be looked at at all
 * is decided by the caller (see `ThreadManager.resolveInThread`).
 */

/** The most of a file the viewer is sent. A longer one is cut at a line and marked as such. */
export const MAX_TEXT_BYTES = 1024 * 1024;
/** How much of a file is looked at to tell text from binary. */
const SNIFF_BYTES = 8192;
/** A folder with more entries than this is listed only this far. */
export const MAX_ENTRIES = 2000;
/** Never listed: git's own store says nothing a person browsing the project wants. */
const HIDDEN = new Set(['.git']);

export interface DirEntry {
  name: string;
  type: 'dir' | 'file';
  /** Bytes, for files. */
  size?: number;
  /** Ignored by git (build output, dependencies): shown, but quieter. */
  ignored?: boolean;
}

export interface Listing {
  entries: DirEntry[];
  /** Some entries were left out: the folder has more than MAX_ENTRIES. */
  truncated: boolean;
}

/** Which of these names in `dir` git ignores. Outside a repository, none of them. */
function ignoredNames(dir: string, names: string[]): Promise<Set<string>> {
  return new Promise((resolve) => {
    if (names.length === 0) return resolve(new Set());
    const child = spawn('git', ['check-ignore', '--stdin', '-z'], {
      cwd: dir,
      env: { ...process.env, GIT_TERMINAL_PROMPT: '0', LC_ALL: 'C' },
      stdio: ['pipe', 'pipe', 'ignore'],
    });
    let out = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => (out += chunk));
    // Exit code 1 means nothing matched, 128 means no repository: either way the answer is in `out`.
    child.on('close', () => resolve(new Set(out.split('\0').filter(Boolean))));
    child.on('error', () => resolve(new Set()));
    child.stdin.on('error', () => {});
    child.stdin.end(names.join('\0') + '\0');
  });
}

/** One folder's entries: folders first, then files, each by name as a person would sort them. */
export async function listDir(dir: string): Promise<Listing> {
  const all = readdirSync(dir, { withFileTypes: true }).filter((d) => !HIDDEN.has(d.name));
  const truncated = all.length > MAX_ENTRIES;
  const entries: DirEntry[] = [];
  for (const d of all.slice(0, MAX_ENTRIES)) {
    // A link is shown as what it points at. One that points nowhere is left out: there is nothing to open.
    let isDir = d.isDirectory();
    let size: number | undefined;
    if (d.isSymbolicLink() || d.isFile()) {
      try {
        const st = statSync(join(dir, d.name));
        isDir = st.isDirectory();
        if (st.isFile()) size = st.size;
        else if (!isDir) continue;
      } catch {
        continue;
      }
    } else if (!isDir) {
      continue;
    }
    entries.push(isDir ? { name: d.name, type: 'dir' } : { name: d.name, type: 'file', size });
  }
  const ignored = await ignoredNames(
    dir,
    entries.map((e) => e.name),
  );
  for (const e of entries) if (ignored.has(e.name)) e.ignored = true;
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  entries.sort((a, b) => (a.type === b.type ? collator.compare(a.name, b.name) : a.type === 'dir' ? -1 : 1));
  return { entries, truncated };
}

export interface TextFile {
  size: number;
  /** Null for a binary file: it has no lines to show. */
  text: string | null;
  /** Only the start of the file is in `text`. */
  truncated: boolean;
}

/** A file's text, up to MAX_TEXT_BYTES and cut at a line. A file with a zero byte in its start is binary. */
export function readText(path: string): TextFile & { hash: string | null } {
  const size = statSync(path).size;
  const want = Math.min(size, MAX_TEXT_BYTES);
  const buf = Buffer.alloc(want);
  const fd = openSync(path, 'r');
  let got = 0;
  try {
    while (got < want) {
      const n = readSync(fd, buf, got, want - got, got);
      if (n === 0) break;
      got += n;
    }
  } finally {
    closeSync(fd);
  }
  let bytes = buf.subarray(0, got);
  if (bytes.subarray(0, SNIFF_BYTES).includes(0)) return { size, text: null, truncated: false, hash: null };
  const truncated = size > got;
  if (truncated) {
    // End on a whole line, so the last one shown is not half a line or half a character.
    const lastNewline = bytes.lastIndexOf(0x0a);
    if (lastNewline > 0) bytes = bytes.subarray(0, lastNewline + 1);
  }
  return { size, text: bytes.toString('utf8'), truncated, hash: truncated ? null : createHash('sha256').update(bytes).digest('hex') };
}

/** The most file names a search returns: enough to pick from, not a dump of the project. */
export const MAX_FOUND = 200;
/** A folder that is not a repository is walked this far and no further. */
const MAX_WALKED = 20_000;
/** Skipped when walking a folder that is not a repository: what git would usually be told to ignore. */
const SKIPPED_DIRS = new Set(['.git', 'node_modules', '.worktrees', 'dist', 'build', '.venv', '__pycache__', 'target']);

/** Every file in `dir`, relative to it: the ones git tracks or would track, or a bounded walk outside a repository. */
function allFiles(dir: string): Promise<string[]> {
  return new Promise((resolve) => {
    const child = spawn('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], {
      cwd: dir,
      env: { ...process.env, GIT_TERMINAL_PROMPT: '0', LC_ALL: 'C' },
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    let out = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => (out += chunk));
    const walk = () => {
      const found: string[] = [];
      const queue = [''];
      while (queue.length > 0 && found.length < MAX_WALKED) {
        const rel = queue.shift()!;
        let entries;
        try {
          entries = readdirSync(join(dir, rel), { withFileTypes: true });
        } catch {
          continue;
        }
        for (const e of entries) {
          const path = rel ? `${rel}/${e.name}` : e.name;
          if (e.isDirectory()) {
            if (!SKIPPED_DIRS.has(e.name)) queue.push(path);
          } else if (e.isFile()) found.push(path);
        }
      }
      return found;
    };
    child.on('close', (code) => resolve(code === 0 ? [...new Set(out.split('\0').filter(Boolean))] : walk()));
    child.on('error', () => resolve(walk()));
  });
}

/**
 * Files in `dir` whose path has every word of the query in it, ignoring case. Those whose name
 * itself matches come first, then shorter paths: the file meant is usually the obvious one.
 */
export async function findFiles(dir: string, query: string): Promise<{ paths: string[]; truncated: boolean }> {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return { paths: [], truncated: false };
  const matches = (await allFiles(dir)).filter((p) => {
    const lower = p.toLowerCase();
    return words.every((w) => lower.includes(w));
  });
  const nameHit = (p: string) => {
    const name = p.slice(p.lastIndexOf('/') + 1).toLowerCase();
    return words.every((w) => name.includes(w)) ? 0 : 1;
  };
  matches.sort((a, b) => nameHit(a) - nameHit(b) || a.length - b.length || a.localeCompare(b));
  return { paths: matches.slice(0, MAX_FOUND), truncated: matches.length > MAX_FOUND };
}
