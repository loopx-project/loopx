import { execFile } from 'node:child_process';
import { appendFileSync, cpSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';

/**
 * Git worktrees for forked threads: the copy of a conversation gets a checkout of its own, on a
 * branch of its own, next to the others in `<repository>/.worktrees/`. Everything here is plain
 * `git`: the hub keeps nothing about a worktree that git does not keep itself, apart from which
 * thread stands in it.
 */

/** Where worktrees go, inside the repository's own working directory. */
export const WORKTREES_DIR = '.worktrees';

/** More than this left untracked is not carried along: it is a data set, not work in progress. */
const MAX_UNTRACKED_FILES = 5000;
const MAX_UNTRACKED_BYTES = 500 * 1024 * 1024;

/** Where a directory stands in a git repository. */
export interface Checkout {
  /** The root of the checkout the directory is in: the repository itself or one of its worktrees. */
  top: string;
  /** The repository's own working directory. `.worktrees/` goes here. */
  main: string;
  /** The directory within the checkout, relative to its root. Empty at the root. */
  prefix: string;
  /** The branch checked out there, or null when HEAD is detached. */
  branch: string | null;
}

export interface Worktree {
  /** The root of the new checkout. */
  root: string;
  branch: string;
}

function git(cwd: string, args: string[]): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    execFile(
      'git',
      args,
      // Never wait for a password or an editor: there is nobody at this terminal.
      { cwd, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, GIT_TERMINAL_PROMPT: '0', GIT_EDITOR: 'true', LC_ALL: 'C' } },
      (err, stdout, stderr) => {
        if (err) reject(new Error((stderr || err.message).trim().split('\n').slice(-1)[0] || `git ${args[0]} failed`));
        else resolvePromise(stdout);
      },
    );
  });
}

/**
 * Where `cwd` stands in a repository a worktree can be added to, or null: not in a repository,
 * no commit yet, or a layout this does not handle (a bare repository, a submodule).
 */
export async function findCheckout(cwd: string): Promise<Checkout | null> {
  let out: string;
  try {
    out = await git(cwd, ['rev-parse', '--show-toplevel', '--git-common-dir', '--show-prefix']);
    await git(cwd, ['rev-parse', '--verify', '--quiet', 'HEAD^{commit}']);
  } catch {
    return null;
  }
  const [top, common, prefix = ''] = out.split('\n');
  // The common directory is `<repository>/.git` for an ordinary repository and for every worktree of it.
  const commonDir = resolve(cwd, common);
  if (!top || basename(commonDir) !== '.git') return null;
  const branch = await git(cwd, ['symbolic-ref', '--quiet', '--short', 'HEAD']).then(
    (s) => s.trim() || null,
    () => null,
  );
  return { top, main: dirname(commonDir), prefix: prefix.replace(/\/+$/, ''), branch };
}

/** How many files differ from the last commit: changed, staged or new. Ignored files do not count. */
export async function countChanges(top: string): Promise<number> {
  const out = await git(top, ['status', '--porcelain=v1', '-z', '--untracked-files=all']);
  // A rename takes two entries, the old name following the new one; it is one change.
  let count = 0;
  const entries = out.split('\0').filter(Boolean);
  for (let i = 0; i < entries.length; i++) {
    count += 1;
    if (entries[i][0] === 'R' || entries[i][0] === 'C') i += 1;
  }
  return count;
}

/** Makes sure git does not report `.worktrees/` as new files, without touching the tracked `.gitignore`. */
async function ignoreWorktreesDir(main: string): Promise<void> {
  const ignored = await git(main, ['check-ignore', '--quiet', `${WORKTREES_DIR}/probe`]).then(
    () => true,
    () => false,
  );
  if (ignored) return;
  const exclude = join(main, '.git', 'info', 'exclude');
  mkdirSync(dirname(exclude), { recursive: true });
  const current = existsSync(exclude) ? readFileSync(exclude, 'utf8') : '';
  appendFileSync(exclude, `${current && !current.endsWith('\n') ? '\n' : ''}/${WORKTREES_DIR}/\n`);
}

/** Brings what is not committed in `from` over to `to`, a checkout of the same commit. `from` is left as it is. */
async function carryChanges(from: string, to: string): Promise<void> {
  // Changes to tracked files, staged or not: a stash that is never stored anywhere, applied over there.
  // The identity is for that throwaway commit only: a machine without one configured must still
  // work, and one that signs its commits must not be asked for a key.
  const throwaway = ['-c', 'user.name=calli', '-c', 'user.email=hub@localhost', '-c', 'commit.gpgsign=false'];
  const stash = (await git(from, [...throwaway, 'stash', 'create'])).trim();
  if (stash) {
    await git(to, ['stash', 'apply', '--index', stash]).catch(() => git(to, ['stash', 'apply', stash]));
  }

  // New files git does not know yet. Ignored ones stay behind: dependencies, build output, secrets.
  const untracked = (await git(from, ['ls-files', '--others', '--exclude-standard', '-z'])).split('\0').filter(Boolean);
  // A nested repository shows up as a directory; it is not this repository's work.
  const files = untracked.filter((f) => !f.endsWith('/'));
  if (files.length > MAX_UNTRACKED_FILES) throw new Error(`${files.length} untracked files are too many to bring along; commit or ignore them first`);
  let bytes = 0;
  for (const file of files) {
    const stat = lstatSync(join(from, file));
    bytes += stat.size;
    if (bytes > MAX_UNTRACKED_BYTES) throw new Error('the untracked files are too large to bring along; commit or ignore them first');
  }
  for (const file of files) {
    mkdirSync(dirname(join(to, file)), { recursive: true });
    cpSync(join(from, file), join(to, file), { verbatimSymlinks: true });
  }
}

/**
 * Adds a worktree named `name` on a new branch, at the commit `from` is on, with whatever is not
 * committed there brought along. On any failure nothing is left behind.
 */
export async function addWorktree(from: Checkout, name: string): Promise<Worktree> {
  const tree: Worktree = { root: join(from.main, WORKTREES_DIR, name), branch: `calli/${name}` };
  if (existsSync(tree.root)) throw new Error(`${tree.root} already exists`);
  await ignoreWorktreesDir(from.main);
  await git(from.top, ['worktree', 'add', '--quiet', '-b', tree.branch, tree.root, 'HEAD']);
  try {
    await carryChanges(from.top, tree.root);
  } catch (err) {
    await removeWorktree(from.main, tree).catch(() => undefined);
    throw err;
  }
  return tree;
}

/** What would be lost with a worktree: files not committed there, and commits only its branch has. */
export async function worktreeLeftovers(main: string, tree: Worktree): Promise<{ changes: number; commits: number }> {
  const changes = existsSync(tree.root) ? await countChanges(tree.root).catch(() => 0) : 0;
  const commits = await git(main, ['rev-list', '--count', tree.branch, '--not', `--exclude=${tree.branch}`, '--branches', '--remotes']).then(
    (s) => Number(s.trim()) || 0,
    () => 0, // the branch is gone already
  );
  return { changes, commits };
}

/** Removes the checkout and its branch, whatever is in them. Check worktreeLeftovers first. */
export async function removeWorktree(main: string, tree: Worktree): Promise<void> {
  if (existsSync(tree.root)) await git(main, ['worktree', 'remove', '--force', tree.root]);
  else await git(main, ['worktree', 'prune']);
  await git(main, ['branch', '-D', tree.branch]).catch(() => undefined);
}

/** True when two paths are the same directory, however they are spelled. */
export function samePath(a: string, b: string): boolean {
  const real = (p: string) => {
    try {
      return realpathSync(p);
    } catch {
      return resolve(p);
    }
  };
  return real(a) === real(b);
}

/**
 * Makes `dir` a repository with one empty commit, so a worktree can be added to it at once. The
 * commit is made as whoever git is set up for; on a machine with no identity, as the hub.
 */
export async function initRepository(dir: string): Promise<void> {
  await git(dir, ['init', '--quiet']);
  const message = ['commit', '--quiet', '--allow-empty', '-m', 'Initial commit'];
  await git(dir, message).catch(() =>
    git(dir, ['-c', 'user.name=calli', '-c', 'user.email=hub@localhost', '-c', 'commit.gpgsign=false', ...message]),
  );
}
