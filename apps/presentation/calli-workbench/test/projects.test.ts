import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildProjectOptions, projectPath, projectRoot } from '../src/projects.js';

const thread = (cwd: string, cwdReal = cwd, extra = {}) => ({
  cwd, cwdReal, updatedAt: '2026-01-01', archivedAt: null, scratch: false, ...extra,
});

test('a symlink-spelled thread and its configured root appear once and select once', () => {
  const list = buildProjectOptions([thread('/home/test/code', '/data/test/code')], ['/home/test/code', '/home/test/code/'], '/home/test/code');
  assert.equal(list.length, 1);
  assert.equal(list[0].cwd, '/home/test/code');
  assert.deepEqual(list[0].paths, ['/home/test/code', '/data/test/code']);
  for (const selected of ['/home/test/code', '/data/test/code', '/home/test/code/']) {
    assert.equal(list.filter((p) => p.paths.includes(projectPath(selected))).length, 1);
  }
});

test('resolved paths, configured spellings and a draft share one identity', () => {
  const list = buildProjectOptions([
    thread('/home/test/code', '/data/test/code'),
    thread('/data/test/code', '/data/test/code', { updatedAt: '2026-01-02' }),
  ], ['/home/test/code', '/data/test/code'], '/data/test/code/');
  assert.equal(list.length, 1);
  assert.ok(list[0].paths.includes('/home/test/code'));
  assert.ok(list[0].paths.includes('/data/test/code'));
});

test('same folder names at different locations remain distinct; scratch and archived projects are omitted', () => {
  const list = buildProjectOptions([
    thread('/work/first/code'), thread('/work/second/code'),
    thread('/scratch/temp', undefined, { scratch: true }),
    thread('/archived', undefined, { archivedAt: '2026-01-01' }),
  ], [], '');
  assert.equal(list.length, 2);
  assert.deepEqual(list.map((p) => p.name), ['code', 'code']);
});

test('worktrees count under their source project and new drafts come first', () => {
  const list = buildProjectOptions([
    thread('/work/code/.worktrees/task', '/data/code/.worktrees/task', {
      worktree: { root: '/work/code/.worktrees/task', branch: 'task', of: '/work/code', ofReal: '/data/code' },
    }),
  ], ['/work/code'], '/work/new');
  assert.deepEqual(list.map((p) => p.cwd), ['/work/new', '/work/code']);
});

test('a session started in a .worktrees checkout offers the repository, not the checkout', () => {
  // No worktree metadata: calli opened the session inside the checkout directly.
  const list = buildProjectOptions([
    thread('/data/code/.worktrees/agent-team'),
    thread('/data/code/.worktrees/b0b51d7d', '/data/code/.worktrees/b0b51d7d', { updatedAt: '2026-01-02' }),
    thread('/work/code'),
  ], [], '/data/code/.worktrees/agent-team');
  assert.deepEqual(list.map((p) => p.cwd).sort(), ['/data/code', '/work/code']);
  // The draft, too, points at the repository once picked.
  assert.ok(list.find((p) => p.cwd === '/data/code')!.paths.includes('/data/code'));
});

test('projectRoot strips the checkout and leaves ordinary paths alone', () => {
  assert.equal(projectRoot('/data/code/.worktrees/task'), '/data/code');
  assert.equal(projectRoot('/data/code/.worktrees'), '/data/code');
  assert.equal(projectRoot('/data/code/.worktrees/task/packages/web'), '/data/code');
  assert.equal(projectRoot('/data/code'), '/data/code');
  assert.equal(projectRoot('/data/code/worktrees/task'), '/data/code/worktrees/task');
});
