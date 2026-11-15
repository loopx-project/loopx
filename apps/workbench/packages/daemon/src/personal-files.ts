import { execFile } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import type { TodoNote, TodoRecord } from './store.js';

/**
 * File mirror of the plan page's todos inside the personal directory (step 1 of
 * docs/personal-files-design.md). The database stays the source of truth: every add, change,
 * note write and removal also lands as `matters/<id>-<slug>/matter.md`, and a startup
 * `syncAll` reconciles the whole tree. Git keeps local file history (no remote), but only for
 * a repository Calli initialized itself — proven by the .calli/git-owned marker; a directory
 * inside someone else's worktree is never initialized and never committed, and the add is
 * scoped to Calli's own paths. Nothing here may break the API: every failure is logged and
 * swallowed.
 */

/** Commits as Calli no matter how git is configured on the machine; never signs. */
const GIT_IDENTITY = ['-c', 'user.name=Calli', '-c', 'user.email=calli@localhost', '-c', 'commit.gpgsign=false'];

/** The only paths a routine mirror commit may touch: the mirrored files themselves, never a
 *  matter's artifacts (committed by commitArtifacts) and never something the person staged. */
const OWNED_PATHS = ['matters/*/matter.md', 'AGENTS.md', 'profile.md', '.gitignore'];

/** Marks a repository Calli initialized itself; lives in the gitignored .calli/. */
const OWNERSHIP_MARKER = '.calli/git-owned';

const GITIGNORE = '.calli/\n.matter.md.tmp\n';

const AGENTS_MD = `# Personal directory

This is the person's private Calli directory. \`matters/\` holds one folder per matter: the
plan page's items, mirrored here by Calli from its database. The directory is a local git
repository for history; there is no remote.

For agents:

- Read freely. A matter's state is in its \`matter.md\` frontmatter, its continuation note in
  the body.
- Change matters only through the \`calli todo\` command (see \`calli todo --help\`). Never edit
  a matter's \`matter.md\` by hand: Calli overwrites it from its database, and hand edits are
  lost. A matter's \`artifacts/\` is different — working material (drafts, agendas, one-pagers)
  that Calli's proactive runs may write there.
- \`profile.md\` is the person's own notes; do not rewrite it unless asked.

说明（中文）：这是 Calli 为人维护的私人目录；\`matters/\` 下每个事项的 \`matter.md\` 由 Calli 从数据库
镜像生成，事项只能用 \`calli todo\` 命令修改，不要直接改 \`matter.md\`。事项的 \`artifacts/\` 是工作材料
（草稿、提纲、一页纸），Calli 的主动准备会写进去；\`profile.md\` 由人自己写。
`;

/** The scaffold written only when profile.md does not exist; also the isTemplate marker. */
export const PROFILE_TEMPLATE = `# 我的说明

这里由我自己写，Calli 和各家 agent 都会读：了解我在意什么、找谁、什么节奏、什么算重要。

## 我在意什么

## 老板和 PM

写名字与角色，不写 ID。

## 周报节奏

## 重要的判断

什么算重要：例如老板提的、OKR 相关、对外承诺。

## 不要打扰我的时间
`;

/** Shown for a matter whose note was never written, so the file shows what belongs where. */
const MATTER_SCAFFOLD = '## 欠谁什么\n## 定了什么\n## 做到哪\n## 下一步\n';

/** One todo as the mirror needs it: the record, its note, its linked thread IDs. */
export interface MatterInput { todo: TodoRecord; note: TodoNote; threads: string[] }

/** A path-safe slug from a title: letters and numbers (any script) kept, one dash per run. */
export function matterSlug(title: string): string {
  const slug = [...title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/gu, '')].slice(0, 40).join('').replace(/-+$/u, '');
  return slug || 'matter';
}

const yamlString = (value: string): string => JSON.stringify(value);
const yamlValue = (value: string | null): string => (value === null ? 'null' : yamlString(value));

/** matter.md with the design's frontmatter; the body is the continuation note, verbatim. */
function matterMarkdown(todo: TodoRecord, note: TodoNote, threads: string[]): string {
  // Done stays done; an explicit lane wins; without one, waiting maps to wait and the rest to
  // doing. The spec's you/later lanes and its `who` field are covered by the todo's lane field.
  const lane = todo.done || todo.state === 'done' ? 'done' : todo.lane ?? (todo.state === 'waiting' ? 'wait' : 'doing');
  const yaml = [
    `id: ${yamlString(todo.id)}`,
    `title: ${yamlString(todo.text)}`,
    `lane: ${lane}`,
    `importance: ${todo.importance === null ? 'null' : yamlString(todo.importance)}`,
    `owner: ${todo.owner}`,
    `due: ${yamlValue(todo.dueAt)}`,
    `follow_up: ${yamlValue(todo.followUpAt)}`,
    `urgent_until: ${yamlValue(todo.urgentUntil)}`,
    `source: ${todo.source === null ? 'null' : JSON.stringify(todo.source)}`,
    `waiting_on: ${yamlValue(todo.waitingOn)}`,
    `rev: ${todo.rev}`,
    `created: ${yamlValue(todo.createdAt)}`,
    `updated: ${yamlValue(note.updatedAt)}`,
    `threads: [${threads.map(yamlString).join(', ')}]`,
  ].join('\n');
  const body = note.text.trim() ? note.text : MATTER_SCAFFOLD;
  return `---\n${yaml}\n---\n${body.endsWith('\n') ? body : `${body}\n`}`;
}

export class PersonalFiles {
  /** The directory prepare() last set up, so repeated writes skip the checks. */
  private preparedFor: string | null = null;
  /** False once git itself proved unavailable (no binary, init failed); file writes continue. */
  private gitUsable = true;
  /** True only for a repository Calli initialized itself, proven by the ownership marker. */
  private gitOwned = false;
  /** The foreign-repository notice is logged once per instance, not per write. */
  private foreignLogged = false;
  /** Writes run one at a time: git cannot take two index updates at once. */
  private queue: Promise<void> = Promise.resolve();

  constructor(private readonly root: string | (() => string)) {}

  private dir(): string { return typeof this.root === 'string' ? this.root : this.root(); }

  /** One job after another; a failure is logged here and never reaches the caller. */
  private run(work: () => Promise<void>): Promise<void> {
    const next = this.queue.then(work).catch(error => this.log('mirror step failed', error));
    this.queue = next;
    return next;
  }

  /** Like run, but the caller needs the value and the failure: errors are not swallowed. */
  private expect<T>(work: () => Promise<T>): Promise<T> {
    const next = this.queue.then(work);
    this.queue = next.then(() => undefined, () => undefined);
    return next;
  }

  /** The prepared root: the checks run once per directory, like every writer here does. */
  private async ready(): Promise<string> {
    const root = this.dir();
    if (this.preparedFor !== root) { await this.prepare(root); this.preparedFor = root; }
    return root;
  }

  /** Waits for everything queued so far, and for anything that arrives while waiting. */
  async dispose(): Promise<void> {
    const last = this.queue;
    await last;
    if (this.queue !== last) return this.dispose();
  }

  /** Writes profile.md atomically; commits only in a Calli-owned repository. */
  writeProfile(text: string): Promise<void> {
    return this.run(async () => {
      try {
        await this.ready();
        // Write a temporary file and rename, like matter.md: a crash cannot tear the text.
        const tmp = join(this.dir(), '.profile.md.tmp');
        writeFileSync(tmp, text);
        renameSync(tmp, join(this.dir(), 'profile.md'));
        await this.commit('profile update');
      } catch (error) { this.log('cannot write profile.md', error); }
    });
  }

  /** Creates the skeleton on first use. Existing files are never overwritten. */
  ensure(): Promise<void> {
    return this.run(async () => { await this.ready(); });
  }

  writeMatter(todo: TodoRecord, note: TodoNote, threads: string[] = []): Promise<void> {
    return this.run(async () => {
      try {
        await this.ready();
        await this.writeInto(join(this.dir(), 'matters'), todo, note, threads);
      } catch (error) { this.log(`cannot mirror matter ${todo.id}`, error); }
    });
  }

  removeMatter(id: string): Promise<void> {
    return this.run(async () => {
      try {
        const matters = join(this.dir(), 'matters');
        const dir = this.matterDir(matters, id);
        if (!dir) return;
        const name = relative(matters, dir);
        // Deleted outright, not parked in a trash folder: the database is the authority and git
        // history preserves the file, so trash would only add untracked clutter that a later
        // index rebuild (step 3 of the design) would have to skip.
        rmSync(dir, { recursive: true, force: true });
        // The whole directory went, artifacts included: the delete commit scopes to it by name.
        await this.commit(`matter delete ${id}`, [`matters/${name}`]);
      } catch (error) { this.log(`cannot remove mirrored matter ${id}`, error); }
    });
  }

  /** Startup reconciliation: everything the database has is written, everything it has not is removed. */
  syncAll(todos: MatterInput[]): Promise<void> {
    return this.run(async () => {
      try {
        const root = await this.ready();
        const matters = join(root, 'matters');
        const keep = new Map<string, string>();
        for (const { todo, note, threads } of todos) {
          await this.writeInto(matters, todo, note, threads);
          keep.set(todo.id, `${todo.id}-${matterSlug(todo.text)}`);
        }
        for (const name of this.listMatters(matters)) {
          const id = this.frontmatterId(join(matters, name));
          // Directories the database does not know go away, as do duplicates left under an old
          // title. An unreadable directory is not proven to be a mirror, so it stays.
          if (id && keep.get(id) !== name) {
            rmSync(join(matters, name), { recursive: true, force: true });
            await this.commit(`matter delete ${id}`, [`matters/${name}`]);
          }
        }
      } catch (error) { this.log('cannot sync the matter mirror', error); }
    });
  }

  /** The matter's artifacts directory relative to the root, created on demand, for a proactive
   *  prepare run to write into and record. Errors propagate: an explicit action, not a mirror. */
  artifacts(id: string): Promise<string> {
    return this.expect(async () => {
      const root = await this.ready();
      const dir = this.matterDir(join(root, 'matters'), id);
      if (!dir) throw new Error(`matter ${id} is not mirrored yet`);
      mkdirSync(join(dir, 'artifacts'), { recursive: true });
      return relative(root, join(dir, 'artifacts')).split(sep).join('/');
    });
  }

  /** Commits the matter's new and changed artifacts as Calli (`proactive prepare <id>`), scoped
   *  to that directory alone so nothing else is swept in. Errors propagate to the proactive run. */
  commitArtifacts(id: string): Promise<{ path: string; additions: number; deletions: number }[]> {
    return this.expect(async () => {
      const root = await this.ready();
      const dir = this.matterDir(join(root, 'matters'), id);
      if (!dir) throw new Error(`matter ${id} is not mirrored yet`);
      if (!this.gitUsable) throw new Error('git is unavailable');
      if (!this.gitOwned) throw new Error('the personal directory is not a Calli-owned git repository');
      const scope = `${relative(root, dir).split(sep).join('/')}/artifacts`;
      const status = await this.git(['status', '--porcelain', '--', scope]);
      if (!status.trim()) return [];
      const before = (await this.git(['rev-parse', 'HEAD'])).trim();
      await this.git(['add', '-A', '--', scope]);
      await this.git([...GIT_IDENTITY, 'commit', '--quiet', '-m', `proactive prepare ${id}`, '--', scope]);
      const numstat = await this.git(['-c', 'core.quotepath=false', 'diff', '--numstat', before, 'HEAD', '--', scope]);
      return numstat.split('\n').filter(Boolean).map(line => {
        const [additions, deletions, path] = line.split('\t');
        return { path, additions: Number(additions) || 0, deletions: Number(deletions) || 0 };
      });
    });
  }

  /** Writes one matter.md; renames the directory first when the title moved it. */
  private async writeInto(matters: string, todo: TodoRecord, note: TodoNote, threads: string[]): Promise<void> {
    const target = join(matters, `${todo.id}-${matterSlug(todo.text)}`);
    const current = this.matterDir(matters, todo.id);
    let scope: string[] | undefined;
    if (current && current !== target) {
      // The title changed: the directory is renamed, nothing stays under the old name. The
      // commit scopes to both directories — the old one vanishes whole, artifacts included, and
      // the new one only carries what the rename brought with it.
      scope = [`matters/${relative(matters, current)}`, `matters/${relative(matters, target)}`];
      rmSync(target, { recursive: true, force: true });
      renameSync(current, target);
    }
    mkdirSync(target, { recursive: true });
    // Write a temporary file and rename, so a crash mid-write cannot leave a torn matter.md.
    const tmp = join(target, '.matter.md.tmp');
    writeFileSync(tmp, matterMarkdown(todo, note, threads));
    renameSync(tmp, join(target, 'matter.md'));
    await this.commit(`matter update ${todo.id}`, scope);
  }

  private async prepare(root: string): Promise<void> {
    mkdirSync(root, { recursive: true });
    mkdirSync(join(root, 'matters'), { recursive: true });
    await this.claimRepository(root);
    for (const [name, content] of [['.gitignore', GITIGNORE], ['AGENTS.md', AGENTS_MD], ['profile.md', PROFILE_TEMPLATE]] as const) {
      const path = join(root, name);
      if (existsSync(path)) continue;
      try { writeFileSync(path, content); } catch (error) { this.log(`cannot write ${name}`, error); }
    }
    await this.commit('init personal directory');
  }

  /**
   * Only a repository Calli initialized itself is ever committed to: the marker in the
   * gitignored .calli/ proves it. A directory inside someone else's worktree is neither
   * initialized nor committed — the person's unrelated work must not be swept into Calli's
   * commits under Calli's name.
   */
  private async claimRepository(root: string): Promise<void> {
    const real = (path: string) => { try { return realpathSync(path); } catch { return path; } };
    let toplevel: string | null = null;
    try { toplevel = (await this.git(['rev-parse', '--show-toplevel'])).trim() || null; }
    catch { toplevel = null; }
    if (toplevel) {
      this.gitOwned = real(toplevel) === real(root) && existsSync(join(root, OWNERSHIP_MARKER));
      if (!this.gitOwned && !this.foreignLogged) {
        this.foreignLogged = true;
        this.log(`git repository at ${toplevel} was not created by Calli; mirror files are written but never committed`, '' );
      }
      return;
    }
    try {
      await this.git(['init', '--quiet']);
      mkdirSync(join(root, '.calli'), { recursive: true });
      writeFileSync(join(root, OWNERSHIP_MARKER), '');
      this.gitOwned = true;
    } catch (error) {
      this.gitUsable = false;
      this.gitOwned = false;
      this.log('git init failed', error);
    }
  }

  /** The directory a matter currently lives in, under whatever slug it was first written. */
  private matterDir(matters: string, id: string): string | null {
    const found = this.listMatters(matters).filter(name => name === id || name.startsWith(`${id}-`)).sort();
    return found.length ? join(matters, found[0]) : null;
  }

  private listMatters(matters: string): string[] {
    try {
      return readdirSync(matters).filter(name => !name.startsWith('.') && statSync(join(matters, name)).isDirectory());
    } catch { return []; }
  }

  /** Reads a directory's matter id out of its frontmatter; null when the file is not a mirror. */
  private frontmatterId(dir: string): string | null {
    try {
      const match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(readFileSync(join(dir, 'matter.md'), 'utf8'));
      const line = match?.[1].split(/\r?\n/).find(candidate => candidate.startsWith('id:'));
      if (!line) return null;
      const value = line.slice(3).trim();
      return /^".*"$/s.test(value) ? JSON.parse(value) as string : value;
    } catch { return null; }
  }

  /** Commits exactly the given scopes, as Calli: the commit itself carries the pathspec, so
   *  files someone else staged in the same repository are never swept into a mirror commit. An
   *  unowned repository, an empty tree and a broken git are all skipped or logged, never raised. */
  private async commit(message: string, scope: string[] = OWNED_PATHS): Promise<void> {
    if (!this.gitUsable || !this.gitOwned) return;
    try {
      // One pathspec at a time: `git add` treats a pathspec that matches nothing as an error,
      // and on a first commit the matter glob matches nothing yet.
      const live: string[] = [];
      for (const path of scope) {
        if ((await this.git(['status', '--porcelain', '--', path])).trim()) live.push(path);
      }
      if (!live.length) return;
      for (const path of live) await this.git(['add', '-A', '--', path]);
      await this.git([...GIT_IDENTITY, 'commit', '--quiet', '-m', message, '--', ...live]);
    } catch (error) { this.log('git commit failed', error); }
  }

  /** Only execFile, never a shell: titles and notes reach git as plain arguments. */
  private git(args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      execFile('git', args, { cwd: this.dir() }, (error, stdout, stderr) => {
        if (error) reject(new Error(`git ${args[0]} failed: ${(stderr || error.message).trim().split('\n').slice(-1)[0]}`));
        else resolve(stdout);
      });
    });
  }

  private log(message: string, error: unknown): void {
    console.error(`[personal-files] ${message}${error ? `: ${error instanceof Error ? error.message : error}` : ''}`);
  }
}
