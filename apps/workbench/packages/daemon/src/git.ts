import { execFile } from 'node:child_process';
import { existsSync, realpathSync } from 'node:fs';
import { constants, open, lstat } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { HttpError } from './errors.js';

export interface GitFile { path: string; originalPath?: string; index: string; worktree: string }
export interface GitStatus {
  isRepo: boolean; root: string | null; branch: string | null; head: string | null;
  upstream: string | null; ahead: number | null; behind: number | null;
  files: GitFile[]; operation: boolean; agentBusy: boolean; fetchedAt: string | null;
}
export interface GitCommit { hash: string; subject: string; author: string; date: string }
export interface GitLog { head: string | null; commits: GitCommit[]; more: boolean }
export interface GitCommitFiles { files: GitFile[] }
export interface GitDiff { text: string; truncated: boolean; binary: boolean; untracked: boolean }
export interface GitOperation { operation: 'fetch' | 'pull'; changed: boolean | null; status: GitStatus | null; statusError: boolean }
const MAX_OUTPUT = 4 * 1024 * 1024;
const MAX_DIFF = 512 * 1024;
const MAX_DIFF_LINES = 4000;
const PAGE = 30;
const fail = (code: string, status = 409): never => { throw new HttpError(status, `Git: ${code}`, { gitCode: code }); };
const within = (root: string, path: string) => path === root || path.startsWith(root + sep);

/** A small file made of empty lines must not turn into hundreds of thousands of DOM rows. */
function capDiff(text: string, truncated: boolean): { text: string; truncated: boolean } {
  let end = -1;
  for (let line = 0; line < MAX_DIFF_LINES; line++) {
    end = text.indexOf('\n', end + 1);
    if (end === -1) return { text, truncated };
  }
  return end + 1 < text.length ? { text: text.slice(0, end + 1), truncated: true } : { text, truncated };
}

/** Synchronous identity for the turn-start guard; separate worktrees have separate roots. */
export function gitWorktreeRoot(cwd: string): string | null {
  let dir: string;
  try { dir = realpathSync(cwd); } catch { return null; }
  for (;;) {
    if (existsSync(join(dir, '.git'))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

/** No shell, credential prompts, optional index writes, external diff drivers, or hooks. */
function git(cwd: string, args: string[], opts: { network?: boolean; diff?: boolean } = {}): Promise<{ text: string; truncated: boolean }> {
  const env = { ...process.env, LC_ALL: 'C', GIT_OPTIONAL_LOCKS: '0', GIT_TERMINAL_PROMPT: '0', GIT_ASKPASS: '/bin/false', SSH_ASKPASS: '/bin/false', GCM_INTERACTIVE: 'never' };
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete env[key as keyof typeof env];
  return new Promise((resolveResult, reject) => {
    const child = execFile('git', ['--literal-pathspecs', '--no-pager', '-C', cwd, '-c', 'core.quotepath=false', '-c', 'core.hooksPath=/dev/null', ...args], {
      env, encoding: 'utf8', timeout: opts.network ? 60_000 : 15_000, maxBuffer: opts.diff ? MAX_DIFF : MAX_OUTPUT,
    }, (error, stdout, stderr) => {
      const overflow = error && (error as NodeJS.ErrnoException).code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER';
      if (overflow && opts.diff) {
        const end = stdout.lastIndexOf('\n');
        return resolveResult({ text: end >= 0 ? stdout.slice(0, end + 1) : '', truncated: true });
      }
      if (error) {
        // Git's stderr may contain a credential-bearing remote URL. Only stable codes leave here.
        const code = overflow ? 'too_large' : (error as { killed?: boolean }).killed ? 'timeout' : /fast-forward|diverg/i.test(stderr) ? 'diverged' : 'command_failed';
        reject(new HttpError(422, `Git: ${code}`, { gitCode: code }));
      } else resolveResult({ text: stdout, truncated: false });
    });
    child.stdin?.end();
  });
}

function statusFiles(text: string): GitFile[] {
  const parts = text.split('\0'), out: GitFile[] = [];
  for (let i = 0; i < parts.length; i++) {
    const row = parts[i];
    if (row.startsWith('? ')) { out.push({ path: row.slice(2), index: '?', worktree: '?' }); continue; }
    const match = row.match(/^1 (\S{2}) (?:\S+ ){6}([\s\S]+)$/)
      ?? row.match(/^2 (\S{2}) (?:\S+ ){7}([\s\S]+)$/)
      ?? row.match(/^u (\S{2}) (?:\S+ ){8}([\s\S]+)$/);
    if (!match) continue;
    const file: GitFile = { path: match[2], index: match[1][0].replace('.', ' '), worktree: match[1][1].replace('.', ' ') };
    if (row.startsWith('2 ')) file.originalPath = parts[++i];
    out.push(file);
  }
  return out;
}

/** Git data belongs to a thread's checkout, never to an agent adapter or its session. */
export class GitManager {
  private readonly locks = new Set<string>();
  private readonly pulling = new Set<string>();
  private readonly fetched = new Map<string, string>();
  constructor(private readonly cwd: (id: string) => string, private readonly authorize: (id: string, path: string) => void, private readonly agentBusy: (root: string) => boolean) {}

  assertTurnAllowed(cwd: string): void {
    if (!this.pulling.size) return;
    const root = gitWorktreeRoot(cwd);
    if (root && this.pulling.has(root)) fail('busy');
  }

  private async repo(id: string): Promise<{ root: string; common: string } | null> {
    const cwd = this.cwd(id);
    const root = gitWorktreeRoot(cwd);
    if (!root) return null;
    this.authorize(id, root);
    // Validate the actual Git root as well; a corrupt .git is a failure, not an empty repository.
    const actual = (await git(cwd, ['rev-parse', '--show-toplevel'])).text.trim();
    if (realpathSync(actual) !== root) fail('invalid_repo', 422);
    const common = realpathSync(resolve(root, (await git(root, ['rev-parse', '--git-common-dir'])).text.trim()));
    return { root, common };
  }

  async status(id: string): Promise<GitStatus> {
    const repo = await this.repo(id);
    const empty: GitStatus = { isRepo: false, root: null, branch: null, head: null, upstream: null, ahead: null, behind: null, files: [], operation: false, agentBusy: false, fetchedAt: null };
    if (!repo) return empty;
    const { root, common } = repo;
    const result = await git(root, ['status', '--porcelain=v2', '--branch', '-z', '--untracked-files=all']);
    const headers = new Map<string, string>();
    for (const line of result.text.split('\0')) if (line.startsWith('# ')) {
      const split = line.indexOf(' ', 2); headers.set(line.slice(2, split), line.slice(split + 1));
    }
    const ab = headers.get('branch.ab')?.match(/^\+(\d+) -(\d+)$/);
    const files = statusFiles(result.text);
    const branch = headers.get('branch.head'), head = headers.get('branch.oid');
    const gitDir = resolve(root, (await git(root, ['rev-parse', '--git-dir'])).text.trim());
    return { ...empty, isRepo: true, root, branch: branch === '(detached)' ? null : branch ?? null,
      head: head === '(initial)' ? null : head ?? null, upstream: headers.get('branch.upstream') ?? null,
      ahead: ab ? Number(ab[1]) : null, behind: ab ? Number(ab[2]) : null, files,
      operation: ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'].some(name => existsSync(join(gitDir, name))),
      agentBusy: this.agentBusy(root), fetchedAt: this.fetched.get(common) ?? null };
  }

  private async requireRepo(id: string) { const repo = await this.repo(id); if (!repo) return fail('not_repo'); return repo; }

  private path(root: string, raw: unknown): string {
    if (typeof raw !== 'string' || !raw || raw.length > 4096 || raw.includes('\0') || isAbsolute(raw) || raw.split(/[\\/]/).some(p => p === '..' || p.toLowerCase() === '.git')) return fail('invalid_path', 400);
    const full = resolve(root, raw);
    if (!within(root, full) || relative(root, full) !== raw) return fail('invalid_path', 400);
    // Check the nearest existing ancestor too, including deleted historical paths and symlinks.
    let current = full;
    while (!existsSync(current) && current !== root) current = dirname(current);
    if (!within(root, realpathSync(current))) return fail('invalid_path', 403);
    return full;
  }

  private hash(raw: unknown): string {
    if (typeof raw !== 'string' || !/^[a-f0-9]{40,64}$/.test(raw)) return fail('invalid_commit', 400);
    return raw;
  }

  async log(id: string, head?: string | null, skip = 0): Promise<GitLog> {
    const { root } = await this.requireRepo(id);
    if (!Number.isSafeInteger(skip) || skip < 0 || skip > 100_000) return fail('invalid_page', 400);
    const at = head ? this.hash(head) : (await this.status(id)).head;
    if (!at) return { head: null, commits: [], more: false };
    const fields = (await git(root, ['log', at, `--skip=${skip}`, `--max-count=${PAGE + 1}`, '--format=%H%x00%an%x00%aI%x00%s%x00', '--'])).text.split('\0');
    const commits: GitCommit[] = [];
    for (let i = 0; i + 3 < fields.length; i += 4) commits.push({ hash: fields[i].trim(), author: fields[i + 1], date: fields[i + 2], subject: fields[i + 3] });
    return { head: at, commits: commits.slice(0, PAGE), more: commits.length > PAGE };
  }

  private async commitBase(root: string, commit: string): Promise<string | null> {
    const parents = (await git(root, ['show', '-s', '--format=%P', this.hash(commit), '--'])).text.trim();
    return parents.split(' ')[0] || null;
  }

  async commitFiles(id: string, commit: string): Promise<GitCommitFiles> {
    const { root } = await this.requireRepo(id);
    const parent = await this.commitBase(root, commit);
    const args = parent ? ['diff', parent, commit] : ['diff-tree', '--root', '--no-commit-id', '-r', commit];
    const fields = (await git(root, [...args, '--name-status', '--no-renames', '-z', '--'])).text.split('\0');
    const files: GitFile[] = [];
    for (let i = 0; i + 1 < fields.length; i += 2) files.push({ path: fields[i + 1], index: fields[i], worktree: ' ' });
    return { files };
  }

  async diff(id: string, raw: unknown, staged = false, commit?: string | null): Promise<GitDiff> {
    const { root } = await this.requireRepo(id);
    const full = this.path(root, raw), path = raw as string;
    let args: string[];
    const paths = [path];
    if (commit) {
      const files = await this.commitFiles(id, this.hash(commit));
      if (!files.files.some(f => f.path === path)) return fail('file_changed', 404);
      const parent = await this.commitBase(root, commit);
      args = parent ? ['diff', parent, commit] : ['show', '--format=', '--root', commit];
    } else {
      const status = await this.status(id), file = status.files.find(f => f.path === path);
      if (!file) return fail('file_changed', 404);
      if (file.index === '?' && file.worktree === '?') {
        if (staged) return fail('file_changed', 404);
        const stat = await lstat(full);
        if (!stat.isFile()) return fail('unsupported_file', 422);
        const handle = await open(full, constants.O_RDONLY | constants.O_NOFOLLOW);
        try {
          const buffer = Buffer.alloc(MAX_DIFF + 1);
          const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
          const binary = buffer.subarray(0, Math.min(bytesRead, 8192)).includes(0);
          let text = binary ? '' : buffer.subarray(0, Math.min(bytesRead, MAX_DIFF)).toString('utf8');
          if (bytesRead > MAX_DIFF) text = text.slice(0, Math.max(0, text.lastIndexOf('\n') + 1));
          return { ...capDiff(text, bytesRead > MAX_DIFF), binary, untracked: true };
        } finally { await handle.close(); }
      }
      if (file.originalPath) { this.path(root, file.originalPath); paths.push(file.originalPath); }
      args = ['diff', ...(staged ? ['--cached'] : [])];
    }
    const out = await git(root, [...args, '--no-ext-diff', '--no-textconv', '--no-renames', '--no-color', '--', ...paths], { diff: true });
    return { ...capDiff(out.text, out.truncated), binary: /^Binary files .* differ$/m.test(out.text) || /^GIT binary patch$/m.test(out.text), untracked: false };
  }

  async operate(id: string, operation: 'fetch' | 'pull'): Promise<GitOperation> {
    const { root, common } = await this.requireRepo(id);
    if (this.locks.has(common)) return fail('busy');
    this.locks.add(common);
    if (operation === 'pull') this.pulling.add(root);
    try {
      let before: GitStatus | null = null;
      if (operation === 'pull') {
        before = await this.status(id);
        if (this.agentBusy(root)) return fail('agent_busy');
        if (before.operation) return fail('operation_in_progress');
        if (!before.branch) return fail('detached');
        if (!before.upstream) return fail('no_upstream');
        if (before.files.length) return fail('dirty');
        if ((before.ahead ?? 0) > 0 && (before.behind ?? 0) > 0) return fail('diverged');
        await git(root, ['-c', 'pull.rebase=false', '-c', 'merge.autoStash=false', '-c', 'rebase.autoStash=false', 'pull', '--ff-only', '--no-rebase', '--no-autostash', '--no-recurse-submodules'], { network: true });
      } else {
        const remotes = (await git(root, ['remote'])).text.trim();
        if (!remotes) return fail('no_remote');
        await git(root, ['fetch', '--all', '--no-recurse-submodules'], { network: true });
      }
      this.fetched.set(common, new Date().toISOString());
      // A successful mutation is never reported as failed merely because the following read failed.
      try {
        const status = await this.status(id);
        return { operation, changed: operation === 'pull' && before?.head !== status.head, status, statusError: false };
      } catch { return { operation, changed: null, status: null, statusError: true }; }
    } finally { this.locks.delete(common); this.pulling.delete(root); }
  }
}
