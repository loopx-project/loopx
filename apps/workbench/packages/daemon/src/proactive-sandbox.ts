/**
 * The git layer for L2 proactive sandbox runs (see docs/proactive-design.md). Pure git: this
 * module knows nothing about the store, threads or HTTP — the proactive service wires it.
 *
 * Every matter's sandbox is a worktree of the target repository under `<repo>/.worktrees/`
 * on a branch of its own, `calli/proactive/<matterId>`, created from the repository's default
 * branch. Merges are `--no-ff` and carry the trailer `Calli-Proactive: <matterId>`; reverting
 * walks HEAD for commits carrying that trailer, newest first.
 */
import { execFile } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { HttpError } from './errors.js';

/** Where every sandbox worktree of a repository lives. */
const WORKTREES_DIR = '.worktrees';

/** The hub's own git identity, for when the machine has none of its own. */
const IDENTITY = ['-c', 'user.name=calli', '-c', 'user.email=hub@localhost', '-c', 'commit.gpgsign=false'];

/** A git failure: the message is the last line for the person, `stderr` keeps the whole output. */
type GitError = Error & { stderr: string };

function git(cwd: string, args: string[]): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    execFile(
      'git',
      args,
      // Never wait for a password or an editor: there is nobody at this terminal.
      { cwd, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, GIT_TERMINAL_PROMPT: '0', GIT_EDITOR: 'true', LC_ALL: 'C' } },
      (err, stdout, stderr) => {
        if (err) {
          const output = (stderr || err.message).trim();
          const failure = new Error(output.split('\n').filter(Boolean).slice(-1)[0] || `git ${args[0]} failed`) as GitError;
          failure.stderr = output;
          reject(failure);
        } else {
          resolvePromise(stdout);
        }
      },
    );
  });
}

/** Like `git`, but a commit falls back to the hub's own identity when the machine has none. */
async function committing(cwd: string, args: string[]): Promise<string> {
  // Decided before the first attempt: a commit command that fails part-way (revert applies its
  // patch before it needs an identity) leaves the working directory dirty for a retry to trip on.
  const known = await git(cwd, ['config', 'user.name']).then(() => true, () => false)
    && await git(cwd, ['config', 'user.email']).then(() => true, () => false);
  return git(cwd, known ? args : [...IDENTITY, ...args]);
}

/** True when two paths are the same directory, however they are spelled. */
function samePath(a: string, b: string): boolean {
  const real = (p: string) => {
    try {
      return realpathSync(p);
    } catch {
      return resolve(p);
    }
  };
  return real(a) === real(b);
}

/** True when a local branch of that name exists. */
async function branchExists(cwd: string, branch: string): Promise<boolean> {
  return git(cwd, ['show-ref', '--verify', '--quiet', `refs/heads/${branch}`]).then(() => true, () => false);
}

/** The roots of every worktree git still knows about, as git spells them. */
async function worktreeRoots(cwd: string): Promise<string[]> {
  const out = await git(cwd, ['worktree', 'list', '--porcelain']);
  return out.split('\n').filter(line => line.startsWith('worktree ')).map(line => line.slice('worktree '.length));
}

/** Keeps `<repo>/.worktrees/` out of `git status`, without touching the tracked .gitignore. */
async function ignoreWorktreesDir(main: string): Promise<void> {
  const ignored = await git(main, ['check-ignore', '--quiet', `${WORKTREES_DIR}/probe`]).then(() => true, () => false);
  if (ignored) return;
  const exclude = join(main, '.git', 'info', 'exclude');
  mkdirSync(dirname(exclude), { recursive: true });
  const current = existsSync(exclude) ? readFileSync(exclude, 'utf8') : '';
  appendFileSync(exclude, `${current && !current.endsWith('\n') ? '\n' : ''}/${WORKTREES_DIR}/\n`);
}

/** A repository the sandbox can work in: a working directory with at least one commit. */
async function assertRepository(repoPath: string): Promise<void> {
  let bare: string;
  try {
    bare = (await git(repoPath, ['rev-parse', '--is-bare-repository'])).trim();
  } catch {
    throw new HttpError(400, `${repoPath} 不是 git 仓库`);
  }
  if (bare === 'true') throw new HttpError(400, `${repoPath} 是 bare 仓库，沙盒需要一个能写的工作区`);
  const hasCommit = await git(repoPath, ['rev-parse', '--verify', '--quiet', 'HEAD^{commit}']).then(() => true, () => false);
  if (!hasCommit) throw new HttpError(400, `${repoPath} 还没有任何提交，沙盒分支无从切出；请先提交一次`);
}

/** Matter ids name branches and directories; keep them to safe characters. */
function assertMatterId(matterId: string): void {
  if (!/^[\w.-]+$/.test(matterId)) {
    throw new HttpError(400, `事项 ID 只能包含字母、数字、点、横线和下划线，收到的是 "${matterId}"`);
  }
}

/** Head-changing work happens in the repository's own working directory, on the default branch, clean. */
async function assertReadyForHead(repoPath: string, base: string, doing: string): Promise<void> {
  const on = await git(repoPath, ['symbolic-ref', '--quiet', '--short', 'HEAD']).then(s => s.trim(), () => '');
  if (on !== base) {
    throw new HttpError(409, on
      ? `仓库主工作区当前在 "${on}" 上，不在默认分支 "${base}"；系统不会自动切分支，请手动切回后再${doing}`
      : `仓库主工作区不在任何分支上（分离 HEAD）；系统不会自动切分支，请手动检出 "${base}" 后再${doing}`);
  }
  const dirty = await git(repoPath, ['status', '--porcelain']);
  if (dirty.trim()) throw new HttpError(409, `仓库主工作区有未提交的改动；为避免覆盖，请先提交或清理后再${doing}`);
}

/** True when the message's trailer block (its last paragraph) carries exactly that trailer line. */
function carriesTrailer(body: string, trailer: string): boolean {
  const paragraphs = body.split(/\n[ \t]*\n/).filter(p => p.trim().length > 0);
  if (paragraphs.length === 0) return false;
  return paragraphs[paragraphs.length - 1].split('\n').some(line => line.trimEnd() === trailer);
}

/** Per-file change counts of a sandbox branch, against its merge-base with the default branch. */
export interface SandboxChanges {
  files: { path: string; additions: number; deletions: number }[];
}

/** The default branch of a repository: `origin/HEAD` when it names one, else main, else master. */
async function defaultBranch(cwd: string): Promise<string> {
  const head = await git(cwd, ['symbolic-ref', '--short', 'refs/remotes/origin/HEAD']).then(s => s.trim(), () => '');
  if (head.startsWith('origin/') && (await branchExists(cwd, head.slice('origin/'.length)))) return head.slice('origin/'.length);
  for (const name of ['main', 'master']) if (await branchExists(cwd, name)) return name;
  throw new HttpError(400, `${cwd} 没有可认的默认分支：没有 origin/HEAD，也没有 main 或 master`);
}

export class ProactiveSandbox {
  /** The branch a matter's sandbox lives on: `calli/proactive/<matterId>`. */
  static branchFor(matterId: string): string {
    return `calli/proactive/${matterId}`;
  }

  /** Where a matter's sandbox worktree lives: `<repo>/.worktrees/calli-proactive-<matterId>`. */
  private static worktreeFor(repoPath: string, matterId: string): string {
    return join(resolve(repoPath), WORKTREES_DIR, `calli-proactive-${matterId}`);
  }

  /**
   * Creates the worktree `<repo>/.worktrees/calli-proactive-<matterId>` on
   * `calli/proactive/<matterId>`, branching from the default branch. When both already exist
   * they are reused as-is (a discarded run starts over; a live one continues). Rejects a
   * repository with no commits, a bare repo, or a branch name already checked out elsewhere.
   */
  async prepare(repoPath: string, matterId: string): Promise<{ root: string; branch: string }> {
    assertMatterId(matterId);
    const repo = resolve(repoPath);
    await assertRepository(repo);
    const branch = ProactiveSandbox.branchFor(matterId);
    const root = ProactiveSandbox.worktreeFor(repoPath, matterId);
    await ignoreWorktreesDir(repo);
    await git(repo, ['worktree', 'prune']);
    const registered = await worktreeRoots(repo);
    const hasBranch = await branchExists(repo, branch);
    if (registered.some(path => samePath(path, root))) {
      if (!hasBranch) throw new HttpError(409, `沙盒 worktree ${root} 还在，但它的分支 ${branch} 已不存在；请手动删掉这个目录后重试`);
      return { root, branch }; // a live run continues where it is
    }
    if (hasBranch) {
      // The worktree was lost but the branch lives: hang a fresh checkout of it back on.
      if (existsSync(root)) throw new HttpError(409, `${root} 已存在，但不是本仓库的 worktree；请手动移走或删除后再试`);
      await git(repo, ['worktree', 'add', '--quiet', root, branch]).catch((err: GitError) => {
        const elsewhere = /already checked out at '(.+)'/.exec(err.stderr)?.[1];
        throw new HttpError(409, elsewhere
          ? `分支 ${branch} 已在别处检出（${elsewhere}）；沙盒无法共用，请先处理那个 worktree`
          : `无法把分支 ${branch} 挂回 ${root}：${err.message}`);
      });
      return { root, branch };
    }
    // A fresh run branches from the default branch; git itself refuses a branch checked out elsewhere.
    const base = await defaultBranch(repo);
    await git(repo, ['worktree', 'add', '--quiet', '-b', branch, root, base]).catch((err: GitError) => {
      if (/already exists/i.test(err.stderr)) throw new HttpError(409, `${root} 已存在，但不是本仓库的 worktree；请手动移走或删除后再试`);
      throw new HttpError(409, `无法从 ${base} 切出沙盒分支 ${branch}：${err.message}`);
    });
    return { root, branch };
  }

  /**
   * What the matter's branch changed: `git diff --numstat` from its merge-base with the
   * default branch to the branch tip. Binary files count as 0/0 and are listed.
   */
  async changes(repoPath: string, matterId: string): Promise<SandboxChanges> {
    assertMatterId(matterId);
    const repo = resolve(repoPath);
    await assertRepository(repo);
    const branch = ProactiveSandbox.branchFor(matterId);
    if (!(await branchExists(repo, branch))) throw new HttpError(404, `沙盒分支 ${branch} 不存在：该事项还没有准备过沙盒`);
    const base = await defaultBranch(repo);
    const mergeBase = (await git(repo, ['merge-base', base, branch])).trim();
    const numstat = await git(repo, ['diff', '--numstat', mergeBase, branch]);
    const files: SandboxChanges['files'] = [];
    for (const line of numstat.split('\n')) {
      if (!line.trim()) continue;
      const first = line.indexOf('\t');
      const second = line.indexOf('\t', first + 1);
      const additions = line.slice(0, first);
      const deletions = line.slice(first + 1, second);
      // A binary file is one `-` per side: still listed, counted as nothing.
      files.push({
        path: line.slice(second + 1),
        additions: additions === '-' ? 0 : Number(additions),
        deletions: deletions === '-' ? 0 : Number(deletions),
      });
    }
    return { files };
  }

  /**
   * Merges the branch back into the default branch with `--no-ff`. The merge happens in the
   * repository's own working directory, which must sit on the default branch and be clean;
   * anything else is an error for the person to see, never a forced switch or stash. The merge
   * commit message ends with the trailer line `Calli-Proactive: <matterId>`. Returns its SHA.
   */
  async merge(repoPath: string, matterId: string): Promise<{ commit: string }> {
    assertMatterId(matterId);
    const repo = resolve(repoPath);
    await assertRepository(repo);
    const branch = ProactiveSandbox.branchFor(matterId);
    if (!(await branchExists(repo, branch))) throw new HttpError(404, `沙盒分支 ${branch} 不存在：该事项还没有准备过沙盒`);
    const base = await defaultBranch(repo);
    await assertReadyForHead(repo, base, '合入沙盒改动');
    if ((await git(repo, ['rev-list', '--count', `${base}..${branch}`])).trim() === '0') {
      throw new HttpError(409, `沙盒分支 ${branch} 在 ${base} 之上没有任何提交，无东西可合入`);
    }
    const message = `Merge sandbox branch ${branch}\n\nCalli-Proactive: ${matterId}`;
    let out: string;
    try {
      out = await committing(repo, ['merge', '--no-ff', branch, '-m', message]);
    } catch (err) {
      // A half-made merge is worse than none: hand the working directory back exactly as it was.
      await git(repo, ['merge', '--abort']).catch(() => undefined);
      throw new HttpError(409, `把 ${branch} 合入 ${base} 失败，已整体回滚：${err instanceof Error ? err.message : String(err)}`);
    }
    if (/already up to date/i.test(out)) {
      throw new HttpError(409, `沙盒分支 ${branch} 在 ${base} 之上没有任何提交，无东西可合入`);
    }
    return { commit: (await git(repo, ['rev-parse', 'HEAD'])).trim() };
  }

  /** Removes the worktree and deletes the branch. Gone means gone: no trace must remain. */
  async discard(repoPath: string, matterId: string): Promise<void> {
    assertMatterId(matterId);
    const repo = resolve(repoPath);
    const branch = ProactiveSandbox.branchFor(matterId);
    const root = ProactiveSandbox.worktreeFor(repoPath, matterId);
    const inRepo = await git(repo, ['rev-parse', '--git-dir']).then(() => true, () => false);
    if (!inRepo) {
      // The repository is gone already: nothing of the sandbox can outlive it, but tidy the slot.
      if (existsSync(root)) rmSync(root, { recursive: true, force: true });
      return;
    }
    await git(repo, ['worktree', 'prune']);
    const registered = await worktreeRoots(repo);
    if (registered.some(path => samePath(path, root))) await git(repo, ['worktree', 'remove', '--force', root]);
    else if (existsSync(root)) rmSync(root, { recursive: true, force: true }); // a slot git forgot about
    await git(repo, ['branch', '-D', branch]).catch((err: GitError) => {
      if (/not found/i.test(`${err.message} ${err.stderr ?? ''}`)) return; // gone is fine; the rest is worth seeing
      throw new HttpError(409, `无法删除分支 ${branch}：${err.message}`);
    });
  }

  /**
   * Reverts every commit reachable from HEAD whose message carries the trailer
   * `Calli-Proactive: <matterId>` (exact id — `m-1` must not match `m-10`), newest first,
   * each as its own revert commit; history is rewritten never. A conflict rolls the whole
   * revert back and throws. Returns the reverted SHAs; empty when nothing carries the trailer.
   */
  async revert(repoPath: string, matterId: string): Promise<{ commits: string[] }> {
    assertMatterId(matterId);
    const repo = resolve(repoPath);
    await assertRepository(repo);
    const base = await defaultBranch(repo);
    await assertReadyForHead(repo, base, '撤回改动');
    const trailer = `Calli-Proactive: ${matterId}`;
    // Merge commits carry the trailer too, but undoing a merge is a different animal (`-m`) that
    // would swallow the per-commit reverts: only the sandbox's own commits are reverted.
    // Known edge: reverting the same merge a second time finds the original commits still
    // reachable and conflicts on the already-absent changes — that refusal (whole rollback,
    // clear error) is deliberate; reverted commits carry no marker, and none is added, since
    // that would mean messages the contract does not specify.
    const log = await git(repo, ['log', '--no-merges', '--format=%H%x1f%B%x1e']);
    const victims: string[] = [];
    for (const record of log.split('\x1e')) {
      const separator = record.indexOf('\x1f');
      const sha = record.slice(0, separator).trim();
      if (!/^[0-9a-f]{40}$/.test(sha)) continue;
      if (carriesTrailer(record.slice(separator + 1), trailer)) victims.push(sha);
    }
    if (victims.length === 0) return { commits: [] }; // git log listed them newest first; nothing to undo
    const reverted: string[] = [];
    for (const sha of victims) {
      try {
        await committing(repo, ['revert', '--no-edit', sha]);
      } catch (err) {
        // One conflict undoes the whole undo: no half-reverted history is left behind.
        await git(repo, ['revert', '--abort']).catch(() => undefined);
        throw new HttpError(409, `撤回 ${sha} 时冲突，整次撤回已回滚：${err instanceof Error ? err.message : String(err)}`);
      }
      reverted.push(sha);
    }
    return { commits: reverted };
  }
}
