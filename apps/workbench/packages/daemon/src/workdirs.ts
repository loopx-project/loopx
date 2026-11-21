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
export function tooWide(dir: string): boolean {
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
    /** From LOOCHAT_WORKDIRS: always allowed, never removed from the page. */
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

  /** Why {@link add} would refuse `input`, or null when it would take it; changes nothing. */
  refusal(input: string): string | null {
    if (!input || !isAbsolute(input)) return 'give an absolute path';
    const dir = resolve(input);
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return 'no such directory';
    if (tooWide(dir)) return 'too wide: pick a project folder, not the root or a home directory itself';
    if (isWithinWorkdirs(dir, this.roots())) return 'already allowed';
    return null;
  }

  /**
   * Adds a directory from the page and returns a rollback for a larger operation that cannot
   * complete. The in-memory list is restored too when persistence itself fails.
   */
  add(input: string): () => void {
    const refused = this.refusal(input);
    if (refused) throw new Error(refused);
    const dir = resolve(input);
    const previous = this.extra;
    // A new root that holds added ones takes their place.
    this.commit([...this.extra.filter((p) => !isWithinWorkdirs(p, [dir])), dir]);
    return () => this.commit(previous);
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

  /** Publish and persist one list, restoring the previous in-memory view if the write fails. */
  private commit(next: string[]): void {
    const previous = this.extra;
    this.extra = next;
    try {
      this.save();
    } catch (err) {
      this.extra = previous;
      throw err;
    }
  }
}
