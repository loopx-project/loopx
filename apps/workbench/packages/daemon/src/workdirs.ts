import { existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, resolve, sep } from 'node:path';

/** The path with symlinks resolved, or just normalised when it does not exist. */
function real(path: string): string {
  try {
    return realpathSync(path);
  } catch {
    return resolve(path);
  }
}

/**
 * True when `dir` is one of the allowed working directories or lies inside one.
 * Symlinks are resolved first, so a link inside an allowed directory cannot lead out of it.
 */
export function isWithinWorkdirs(dir: string, workdirs: string[]): boolean {
  const target = real(dir);
  return workdirs.some((w) => {
    const root = real(w);
    return target === root || target.startsWith(root.endsWith(sep) ? root : root + sep);
  });
}

/** Too wide to hand to an agent as a whole: the filesystem root, and a home directory itself. */
function tooWide(dir: string): boolean {
  const target = real(dir);
  return target === sep || target === real(homedir());
}

/**
 * The directories threads may work in: the ones the hub was started with (fixed), the ones added
 * from the page since (kept in `file`, removable), and the hub's own scratch area for threads not
 * in any project. Read on every check, so a directory added from the page is usable at once.
 */
export class Workdirs {
  private extra: string[] = [];
  /** The effective personal directory is allowed without becoming a listed project root. */
  personalDirectory: (() => string) | null = null;

  constructor(
    /** From CALLI_WORKDIRS: always allowed, never removed from the page. */
    private readonly base: string[],
    /** Where added directories are kept; null keeps them in memory only (tests). */
    private readonly file: string | null = null,
    /** Where threads that are in no project work. Allowed, but not offered as a project root. */
    readonly scratchRoot: string | null = null,
  ) {
    if (file && existsSync(file)) {
      try {
        const parsed = JSON.parse(readFileSync(file, 'utf8')) as unknown;
        if (Array.isArray(parsed)) this.extra = parsed.filter((p): p is string => typeof p === 'string' && isAbsolute(p));
      } catch {
        // A broken file means no added directories; the base ones still work.
      }
    }
  }

  /** Every directory a thread may work in, the scratch area included. */
  all(): string[] {
    return [...this.roots(), ...(this.scratchRoot ? [this.scratchRoot] : []), ...(this.personalDirectory ? [this.personalDirectory()] : [])];
  }

  /** The roots offered to people: the fixed ones first, then the added ones. */
  roots(): string[] {
    return [...this.base, ...this.extra.filter((p) => !this.base.includes(p))];
  }

  /** Each root and whether it can be removed from the page. */
  describe(): Array<{ path: string; removable: boolean }> {
    return this.roots().map((path) => ({ path, removable: !this.base.includes(path) }));
  }

  /** Adds a directory from the page. Refuses what does not exist, what is already covered, and what is too wide. */
  add(input: string): void {
    if (!input || !isAbsolute(input)) throw new Error('give an absolute path');
    const dir = resolve(input);
    if (!existsSync(dir) || !statSync(dir).isDirectory()) throw new Error('no such directory');
    if (tooWide(dir)) throw new Error('too wide: pick a project folder, not the root or a home directory itself');
    if (isWithinWorkdirs(dir, this.roots())) throw new Error('already allowed');
    // A new root that holds added ones takes their place.
    this.extra = [...this.extra.filter((p) => !isWithinWorkdirs(p, [dir])), dir];
    this.save();
  }

  /** Removes a directory added from the page. The fixed ones stay. */
  remove(input: string): void {
    const dir = resolve(input);
    if (this.base.includes(dir)) throw new Error('this directory comes from the hub settings and cannot be removed here');
    if (!this.extra.includes(dir)) throw new Error('not an added directory');
    this.extra = this.extra.filter((p) => p !== dir);
    this.save();
  }

  private save(): void {
    if (!this.file) return;
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, JSON.stringify(this.extra, null, 2) + '\n');
  }
}
