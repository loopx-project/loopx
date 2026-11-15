import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { HttpError } from '../src/errors.js';
import { PersonalFiles, matterSlug } from '../src/personal-files.js';
import { PersonalService } from '../src/personal.js';
import { Store, type TodoNote, type TodoRecord } from '../src/store.js';
import type { ThreadManager } from '../src/threads.js';

// The mirror is fire-and-forget: it answers at once and finishes in the background. Tests wait
// for the files and commits it is supposed to leave behind.
const wait = async (fn: () => boolean) => { for (let i = 0; i < 500; i++) { if (fn()) return; await new Promise(r => setTimeout(r, 20)); } throw new Error('timed out'); };
const git = (dir: string, args: string[]): string => execFileSync('git', ['-C', dir, ...args], { encoding: 'utf8' });

const todo = (overrides: Partial<TodoRecord> = {}): TodoRecord => ({
  owner: 'me', state: 'open', lane: null, importance: null, urgentUntil: null, source: null, waitingOn: null, dueAt: null, followUpAt: null,
  followUpNoProgress: 0, followUpPaused: false, rev: 1, proposed: false, hasNote: false,
  notePreview: null, noteUpdatedAt: null, id: 'm-7f3a', text: '阈值实验结论发给老板', done: false,
  parentId: null, position: 1, createdAt: '2026-10-28T09:00:00.000Z', updatedAt: '2026-10-28T09:00:00.000Z', doneAt: null, ...overrides,
});
const note = (text = '', updatedAt: string | null = null): TodoNote => ({ text, updatedAt, threadId: null });

test('personal files: ensure scaffolds once and never overwrites what is already there', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-files-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    assert.match(readFileSync(join(dir, '.gitignore'), 'utf8'), /(^|\n)\.calli\/(\n|$)/);
    const agents = readFileSync(join(dir, 'AGENTS.md'), 'utf8');
    assert.match(agents, /calli todo/);
    assert.match(agents, /matters/);
    const profile = readFileSync(join(dir, 'profile.md'), 'utf8');
    for (const heading of ['我在意什么', '老板和 PM', '周报节奏', '重要的判断', '不要打扰我的时间']) {
      assert.match(profile, new RegExp(`^## ${heading}$`, 'm'));
    }
    assert.ok(existsSync(join(dir, 'matters')));
    // Local history only: no remote is configured.
    assert.equal(git(dir, ['remote']).trim(), '');
    assert.match(git(dir, ['log', '--format=%s']), /^init personal directory$/m);

    // A second run, even on a fresh instance, leaves hand-written files alone.
    writeFileSync(join(dir, 'AGENTS.md'), 'hand written');
    writeFileSync(join(dir, 'profile.md'), '我的笔记');
    await new PersonalFiles(dir).ensure();
    assert.equal(readFileSync(join(dir, 'AGENTS.md'), 'utf8'), 'hand written');
    assert.equal(readFileSync(join(dir, 'profile.md'), 'utf8'), '我的笔记');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: write, lane mapping, rename on title change, remove, git history', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-matter-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    const base = todo({ id: 'm-7f3a', rev: 7, state: 'waiting', waitingOn: '等老板回复', dueAt: '2026-10-31T18:00:00.000Z' });
    await files.writeMatter(base, note('## 欠谁什么\n一条说明\n## 下一步\n等回复\n', '2026-10-31T10:12:00.000Z'), ['t1', 't2']);

    const dirName = `m-7f3a-${matterSlug(base.text)}`;
    const file = join(dir, 'matters', dirName, 'matter.md');
    await wait(() => existsSync(file));
    const text = readFileSync(file, 'utf8');
    assert.match(text, /^---\nid: "m-7f3a"\ntitle: "阈值实验结论发给老板"\nlane: wait\n/);
    assert.match(text, /^owner: me$/m);
    assert.match(text, /^due: "2026-10-31T18:00:00\.000Z"$/m);
    assert.match(text, /^follow_up: null$/m);
    assert.match(text, /^waiting_on: "等老板回复"$/m);
    assert.match(text, /^rev: 7$/m);
    assert.match(text, /^created: "2026-10-28T09:00:00\.000Z"$/m);
    assert.match(text, /^updated: "2026-10-31T10:12:00\.000Z"$/m);
    assert.match(text, /^threads: \["t1", "t2"\]$/m);
    // The body is the continuation note, verbatim.
    assert.ok(text.includes('---\n## 欠谁什么\n一条说明\n## 下一步\n等回复\n'));
    // The temporary file was renamed into place, nothing torn is left behind.
    assert.deepEqual(readdirSync(join(dir, 'matters', dirName)), ['matter.md']);

    // Every write is committed, as Calli, with the operation and the id in the message.
    await wait(() => git(dir, ['log', '--format=%s']).includes(`matter update m-7f3a`));
    const log = git(dir, ['log', '--format=%an|%ae|%s']);
    assert.match(log, /Calli\|calli@localhost\|matter update m-7f3a/);

    // Done items carry the done lane; the same directory is overwritten in place.
    await files.writeMatter(todo({ ...base, state: 'done', done: true, doneAt: '2026-10-31T11:00:00.000Z' }), note('完成'), []);
    await wait(() => /^lane: done$/m.test(readFileSync(file, 'utf8')));

    // A title change renames the directory; nothing stays under the old name.
    const renamed = todo({ ...base, text: '给老板发阈值实验结论' });
    const newDir = join(dir, 'matters', `m-7f3a-${matterSlug(renamed.text)}`);
    await files.writeMatter(renamed, note('x'), []);
    await wait(() => existsSync(join(newDir, 'matter.md')));
    assert.ok(!existsSync(join(dir, 'matters', dirName)));
    assert.deepEqual(readdirSync(join(dir, 'matters')).filter(n => n.startsWith('m-7f3a-')), [basename(newDir)]);

    // Removal takes the directory away and records it in git.
    await files.removeMatter('m-7f3a');
    await wait(() => !existsSync(newDir));
    assert.match(git(dir, ['log', '--format=%s']), /matter delete m-7f3a/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: matter lane prefers the explicit field; done wins; waiting defaults to wait', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-lane-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    const laneOf = async (overrides: Partial<TodoRecord>) => {
      const item = todo({ id: 'm-lane', ...overrides });
      await files.writeMatter(item, note('x'), []);
      const file = join(dir, 'matters', `m-lane-${matterSlug(item.text)}`, 'matter.md');
      return /^lane: (.+)$/m.exec(readFileSync(file, 'utf8'))![1];
    };
    assert.equal(await laneOf({ lane: 'you' }), 'you');
    assert.equal(await laneOf({ lane: 'later' }), 'later');
    assert.equal(await laneOf({ lane: null }), 'doing');
    assert.equal(await laneOf({ state: 'waiting', lane: null }), 'wait');
    assert.equal(await laneOf({ state: 'waiting', lane: 'you' }), 'you');
    assert.equal(await laneOf({ state: 'done', done: true, lane: 'you' }), 'done');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: artifacts commit on their own; mirror commits never sweep them', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-artifacts-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    await files.writeMatter(todo({ id: 'm-art', text: 'note' }), note('x'), []);
    const matterDir = join(dir, 'matters', 'm-art-note');
    await wait(() => existsSync(join(matterDir, 'matter.md')));

    // The artifacts directory is created on demand and reported relative to the root.
    assert.equal(await files.artifacts('m-art'), 'matters/m-art-note/artifacts');
    // A worker drops an artifact next to the mirror; the next mirror commit leaves it alone.
    writeFileSync(join(matterDir, 'artifacts', 'reply-draft.md'), '草稿 v1\n');
    await files.writeMatter(todo({ id: 'm-art', text: 'note', rev: 2 }), note('y'), []);
    assert.match(git(dir, ['status', '--porcelain']), /^\?\? matters\/m-art-note\/artifacts\/$/m);
    assert.ok(!git(dir, ['show', '--name-only', '--format=', 'HEAD']).includes('reply-draft.md'));

    // commitArtifacts takes it, as Calli, with the id in the message, and reports the numbers.
    assert.deepEqual(await files.commitArtifacts('m-art'),
      [{ path: 'matters/m-art-note/artifacts/reply-draft.md', additions: 1, deletions: 0 }]);
    assert.match(git(dir, ['log', '--format=%an|%s']), /Calli\|proactive prepare m-art/);
    assert.equal(git(dir, ['status', '--porcelain']).trim(), '');
    // Nothing new: an empty list, not an empty commit.
    assert.deepEqual(await files.commitArtifacts('m-art'), []);

    // Files the person staged for themselves never ride along with an artifacts commit.
    writeFileSync(join(dir, 'my-own.md'), 'mine');
    execFileSync('git', ['-C', dir, 'add', 'my-own.md']);
    writeFileSync(join(matterDir, 'artifacts', 'onepager.md'), '一页纸\n');
    assert.deepEqual(await files.commitArtifacts('m-art'),
      [{ path: 'matters/m-art-note/artifacts/onepager.md', additions: 1, deletions: 0 }]);
    assert.match(git(dir, ['status', '--porcelain']), /my-own\.md/);

    // An unknown matter is an error an explicit action can act on, not a swallowed log line.
    await assert.rejects(() => files.artifacts('m-missing'), /not mirrored/);
    await assert.rejects(() => files.commitArtifacts('m-missing'), /not mirrored/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: a repository Calli did not create is never committed', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-foreign-'));
  try {
    // The person's own repository, with history and uncommitted work of theirs.
    execFileSync('git', ['-C', dir, 'init', '--quiet']);
    writeFileSync(join(dir, 'notes.txt'), 'mine');
    execFileSync('git', ['-C', dir, 'add', 'notes.txt']);
    execFileSync('git', ['-C', dir, '-c', 'user.name=Me', '-c', 'user.email=me@localhost', 'commit', '--quiet', '-m', 'my notes']);
    writeFileSync(join(dir, 'draft.md'), 'uncommitted work');

    const files = new PersonalFiles(dir);
    await files.ensure();
    await files.writeMatter(todo({ id: 'm-no-commit', text: 'note' }), note('x'), []);
    await wait(() => existsSync(join(dir, 'matters', 'm-no-commit-note', 'matter.md')));
    // The mirror file is written, but history and the working tree stay the person's own.
    assert.equal(git(dir, ['log', '--format=%s']).trim(), 'my notes');
    const status = git(dir, ['status', '--porcelain']);
    assert.match(status, /draft\.md/);
    assert.match(status, /matters/);
    // An explicit artifacts commit in a foreign repository refuses instead of committing.
    await assert.rejects(() => files.commitArtifacts('m-no-commit'), /not a Calli-owned/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: a subdirectory of someone else\'s repository is not initialized', async () => {
  const parent = mkdtempSync(join(tmpdir(), 'calli-personal-parent-'));
  try {
    execFileSync('git', ['-C', parent, 'init', '--quiet']);
    writeFileSync(join(parent, 'readme.md'), 'repo');
    execFileSync('git', ['-C', parent, 'add', 'readme.md']);
    execFileSync('git', ['-C', parent, '-c', 'user.name=Me', '-c', 'user.email=me@localhost', 'commit', '--quiet', '-m', 'init']);
    const root = join(parent, 'personal');
    const files = new PersonalFiles(root);
    await files.ensure();
    await files.writeMatter(todo({ id: 'm-sub', text: 'note' }), note('x'), []);
    await wait(() => existsSync(join(root, 'matters', 'm-sub-note', 'matter.md')));
    // No nested repository appears, and the parent's history is untouched.
    assert.ok(!existsSync(join(root, '.git')));
    assert.equal(git(parent, ['rev-parse', '--show-toplevel']).trim(), parent);
    assert.equal(git(parent, ['log', '--format=%s']).trim(), 'init');
  } finally { rmSync(parent, { recursive: true, force: true }); }
});

test('personal files: a Calli-created repository commits only the managed paths', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-owned-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    writeFileSync(join(dir, 'sketch.txt'), 'the person\'s own draft');
    await files.writeMatter(todo({ id: 'm-owned', text: 'note' }), note('x'), []);
    await wait(() => git(dir, ['log', '--format=%s']).includes('matter update m-owned'));
    assert.ok(existsSync(join(dir, '.calli', 'git-owned')));
    for (const path of git(dir, ['ls-files']).split('\n').filter(Boolean)) {
      assert.match(path, /^(matters\/|AGENTS\.md$|profile\.md$|\.gitignore$)/);
    }
    assert.match(git(dir, ['status', '--porcelain']), /sketch\.txt/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: matter.md carries importance, urgent_until and source', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-importance-'));
  try {
    const files = new PersonalFiles(dir);
    await files.ensure();
    const file = join(dir, 'matters', 'm-imp-note', 'matter.md');
    await files.writeMatter(todo({ id: 'm-imp', text: 'note', importance: 'high', urgentUntil: '2026-12-01T10:00:00.000Z',
      source: { type: 'lark-task', ref: 'task-7', url: 'https://lark.example.com/task/7' } }), note('x'), []);
    const text = readFileSync(file, 'utf8');
    assert.match(text, /^importance: "high"$/m);
    assert.match(text, /^urgent_until: "2026-12-01T10:00:00\.000Z"$/m);
    assert.match(text, /^source: \{"type":"lark-task","ref":"task-7","url":"https:\/\/lark\.example\.com\/task\/7"\}$/m);
    // Unset fields read back as explicit nulls, not missing lines.
    await files.writeMatter(todo({ id: 'm-imp', text: 'note' }), note('x'), []);
    const bare = readFileSync(file, 'utf8');
    assert.match(bare, /^importance: null$/m);
    assert.match(bare, /^urgent_until: null$/m);
    assert.match(bare, /^source: null$/m);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: syncAll writes what the database has and moves what it does not', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-sync-'));
  try {
    const files = new PersonalFiles(dir);
    await files.syncAll([{ todo: todo({ id: 'm-keep', text: '整理报销单据' }), note: note('## 下一步\n收集发票'), threads: [] }]);
    const kept = join(dir, 'matters', `m-keep-${matterSlug('整理报销单据')}`, 'matter.md');
    await wait(() => existsSync(kept));

    // A mirror the database no longer knows, plus a stale duplicate under an old title.
    const stale = join(dir, 'matters', 'm-stale-old');
    mkdirSync(stale);
    writeFileSync(join(stale, 'matter.md'), '---\nid: "m-stale"\n---\nold');
    const duplicate = join(dir, 'matters', 'm-keep-旧标题');
    mkdirSync(duplicate);
    writeFileSync(join(duplicate, 'matter.md'), '---\nid: "m-keep"\n---\nold title');
    // A directory with no readable frontmatter is not proven to be a mirror and stays.
    const stranger = join(dir, 'matters', 'not-a-matter');
    mkdirSync(stranger);
    writeFileSync(join(stranger, 'notes.txt'), 'keep me');

    await files.syncAll([{ todo: todo({ id: 'm-keep', text: '整理报销单据' }), note: note('## 下一步\n收集发票'), threads: [] }]);
    await wait(() => !existsSync(stale) && !existsSync(duplicate));
    assert.ok(existsSync(kept));
    assert.ok(existsSync(join(stranger, 'notes.txt')));
    const subjects = git(dir, ['log', '--format=%s']);
    assert.match(subjects, /matter delete m-stale/);
    assert.match(subjects, /matter delete m-keep/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('personal files: slug is path-safe, bounded and never empty', () => {
  assert.equal(matterSlug('给老板发阈值实验结论'), '给老板发阈值实验结论');
  assert.equal(matterSlug('a/b\\c:d*e?"f"g'), 'a-b-c-d-e-f-g');
  assert.equal(matterSlug('  spaced  out  '), 'spaced-out');
  assert.equal(matterSlug('..'), 'matter');
  assert.equal(matterSlug(''), 'matter');
  assert.ok(matterSlug('长'.repeat(100)).length <= 40);
});

test('personal files: a broken mirror never breaks its caller', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-blocked-'));
  try {
    // The root is a file, so every write inside must fail; the calls still resolve.
    const blocker = join(dir, 'blocker');
    writeFileSync(blocker, 'not a directory');
    const files = new PersonalFiles(blocker);
    await files.ensure();
    await files.writeMatter(todo(), note('x'));
    await files.removeMatter('m-7f3a');
    await files.syncAll([{ todo: todo(), note: note('x'), threads: [] }]);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('PersonalService mirrors adds, edits, notes and removals; credential checks still apply', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-personal-service-'));
  const store = new Store(dir);
  try {
    const personal = join(dir, 'personal');
    mkdirSync(personal);
    store.saveHubSetting('personal', { directory: personal, agent: null, model: null });
    const service = new PersonalService(store, { broadcastTodo() {}, subscribe() {} } as unknown as ThreadManager, new Map());

    const created = service.create({ text: '给老板的周报草稿' });
    const matterDir = join(personal, 'matters', `${created.id}-${matterSlug(created.text)}`);
    const matter = join(matterDir, 'matter.md');
    await wait(() => existsSync(matter));
    assert.match(readFileSync(matter, 'utf8'), /^title: "给老板的周报草稿"$/m);
    assert.ok(existsSync(join(personal, 'AGENTS.md')));

    // An edit renames the mirror directory to the new slug.
    service.update(created.id, { rev: created.rev, text: '周报草稿交给老板' });
    const renamedDir = join(personal, 'matters', `${created.id}-${matterSlug('周报草稿交给老板')}`);
    await wait(() => existsSync(join(renamedDir, 'matter.md')));
    assert.ok(!existsSync(matterDir));

    // Credential rejection happens before the mirror: the note stays without the secret.
    const rev = store.getTodo(created.id)!.rev;
    assert.throws(() => service.writeNote(created.id, { rev, text: '-----BEGIN PRIVATE KEY-----' }), /credential/);
    assert.throws(() => service.update(created.id, { rev, text: 'sk-abcdefghijklmnopqrstuvwxyz123456' }), /credential/);
    assert.ok(!readFileSync(join(renamedDir, 'matter.md'), 'utf8').includes('PRIVATE KEY'));

    // A valid note write lands in the file verbatim.
    service.writeNote(created.id, { rev, text: '## 下一步\n发送草稿' });
    await wait(() => readFileSync(join(renamedDir, 'matter.md'), 'utf8').includes('发送草稿'));

    // Removal, through the hook the web's plan-page delete uses, takes the directory away.
    service.mirrorTodoRemoval(created.id);
    await wait(() => !existsSync(renamedDir));
    assert.match(git(personal, ['log', '--format=%an|%ae']), /Calli\|calli@localhost/);
    // Everything queued has been written before the store and the directory go away.
    await service.flushMirror();
  } finally { store.close(); rmSync(dir, { recursive: true, force: true }); }
});
