import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ProactiveSandbox } from '../src/proactive-sandbox.js';

// A machine with no git identity and no settings of its own: the hub must not lean on either.
const scratch = realpathSync(mkdtempSync(join(tmpdir(), 'calli-test-proactive-')));
writeFileSync(join(scratch, 'gitconfig'), '');
process.env.GIT_CONFIG_GLOBAL = join(scratch, 'gitconfig');
process.env.GIT_CONFIG_NOSYSTEM = '1';
after(() => rmSync(scratch, { recursive: true, force: true }));

const sandbox = new ProactiveSandbox();

/** git as the person at the keyboard would run it: with a name to commit under. */
const git = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-c', 'user.name=Tester', '-c', 'user.email=tester@example.com', ...args], { cwd, encoding: 'utf8' }).trim();

let made = 0;
/** A repository with one commit, on the branch it was inited with (main unless said otherwise). */
function repo(initial = 'main'): string {
  const dir = join(scratch, `repo-${++made}`);
  mkdirSync(dir, { recursive: true });
  git(dir, 'init', '--quiet', `--initial-branch=${initial}`);
  writeFileSync(join(dir, 'README.md'), 'hello\n');
  git(dir, 'add', '.');
  git(dir, 'commit', '--quiet', '-m', 'first');
  return dir;
}

/** A commit in the sandbox the way the worker is told to make them: with the matter's trailer. */
function commitIn(root: string, file: string, content: string, matterId: string): string {
  writeFileSync(join(root, file), content);
  git(root, 'add', '.');
  git(root, 'commit', '--quiet', '-m', `work on ${file}\n\nCalli-Proactive: ${matterId}`);
  return git(root, 'rev-parse', 'HEAD');
}

/** Whether commit `of` is still an ancestor of `it`: history grew, never rewrote. */
const ancestor = (cwd: string, of: string, it: string): boolean => {
  try {
    git(cwd, 'merge-base', '--is-ancestor', of, it);
    return true;
  } catch {
    return false;
  }
};

test('prepare opens a worktree on its own branch from the default branch, and reuses what is there', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-1');
  assert.equal(tree.branch, 'calli/proactive/m-1');
  assert.equal(tree.root, join(dir, '.worktrees', 'calli-proactive-m-1'));
  assert.equal(git(tree.root, 'symbolic-ref', '--short', 'HEAD'), 'calli/proactive/m-1');
  assert.equal(git(tree.root, 'rev-parse', 'HEAD'), git(dir, 'rev-parse', 'main'));
  assert.equal(git(dir, 'status', '--porcelain'), '', 'the worktrees stay out of git status');

  // A live run continues: work in the sandbox, let the default branch move on, prepare again.
  commitIn(tree.root, 'fix.md', 'fixed\n', 'm-1');
  writeFileSync(join(dir, 'README.md'), 'hello, moved on\n');
  git(dir, 'commit', '--quiet', '-am', 'default branch moves on');
  assert.deepEqual(await sandbox.prepare(dir, 'm-1'), tree);
  assert.equal(git(tree.root, 'rev-parse', 'HEAD'), git(dir, 'rev-parse', 'calli/proactive/m-1'));

  // A worktree lost by hand is hung back onto the branch it had, commits included.
  rmSync(tree.root, { recursive: true, force: true });
  const again = await sandbox.prepare(dir, 'm-1');
  assert.equal(again.root, tree.root);
  assert.equal(git(again.root, 'rev-parse', 'HEAD'), git(dir, 'rev-parse', 'calli/proactive/m-1'));

  // After a discard a run starts over from the default branch.
  await sandbox.discard(dir, 'm-1');
  const fresh = await sandbox.prepare(dir, 'm-1');
  assert.equal(git(fresh.root, 'rev-parse', 'HEAD'), git(dir, 'rev-parse', 'main'));
});

test('prepare refuses an empty repository and a bare one', async () => {
  const empty = join(scratch, 'no-commits-yet');
  mkdirSync(empty);
  git(empty, 'init', '--quiet');
  await assert.rejects(sandbox.prepare(empty, 'm-1'), /没有任何提交/);

  const bare = join(scratch, 'bare.git');
  git(scratch, 'init', '--quiet', '--bare', 'bare.git');
  await assert.rejects(sandbox.prepare(bare, 'm-1'), /bare/);
});

test('prepare refuses a slot that is taken and a matter id that is not safe', async () => {
  const dir = repo();
  const slot = join(dir, '.worktrees', 'calli-proactive-m-9');
  mkdirSync(slot, { recursive: true });
  writeFileSync(join(slot, 'precious.txt'), 'not ours to touch\n');
  await assert.rejects(sandbox.prepare(dir, 'm-9'), /已存在/);
  assert.equal(existsSync(join(slot, 'precious.txt')), true, 'what is in the slot is left alone');
  await assert.rejects(sandbox.prepare(dir, '../escape'), /事项 ID/);
});

test('prepare refuses a branch already checked out elsewhere', async () => {
  const dir = repo();
  const other = join(scratch, `other-checkout-${++made}`);
  git(dir, 'worktree', 'add', '--quiet', '-b', 'calli/proactive/m-1', other, 'main');
  await assert.rejects(sandbox.prepare(dir, 'm-1'), /已在别处检出/);
});

test('the default branch is origin/HEAD, else main, else master', async () => {
  const trunk = repo('trunk');
  git(trunk, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk');
  const onTrunk = await sandbox.prepare(trunk, 'm-trunk');
  assert.equal(git(onTrunk.root, 'rev-parse', 'HEAD'), git(trunk, 'rev-parse', 'trunk'));

  const mastered = repo('master'); // no origin/HEAD here: master is all there is
  const onMaster = await sandbox.prepare(mastered, 'm-master');
  assert.equal(git(onMaster.root, 'rev-parse', 'HEAD'), git(mastered, 'rev-parse', 'master'));

  // An origin/HEAD naming a branch that is gone falls back to main.
  const ghost = repo();
  git(ghost, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/ghost');
  const onMain = await sandbox.prepare(ghost, 'm-ghost');
  assert.equal(git(onMain.root, 'rev-parse', 'HEAD'), git(ghost, 'rev-parse', 'main'));
});

test('changes count the branch against its merge-base, binary files as nothing', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-2');
  writeFileSync(join(tree.root, 'README.md'), 'goodbye\n'); // one line swapped: +1 -1
  writeFileSync(join(tree.root, 'src.ts'), 'export const a = 1;\nexport const b = 2;\n'); // new: +2
  writeFileSync(join(tree.root, 'logo.bin'), Buffer.from([0x89, 0x00, 0x50, 0x00])); // binary
  git(tree.root, 'add', '.');
  git(tree.root, 'commit', '--quiet', '-m', 'sandbox work');
  const changes = await sandbox.changes(dir, 'm-2');
  assert.deepEqual(changes.files, [
    { path: 'README.md', additions: 1, deletions: 1 },
    { path: 'logo.bin', additions: 0, deletions: 0 },
    { path: 'src.ts', additions: 2, deletions: 0 },
  ]);

  // Later work on the default branch does not count: the measure is the merge-base, not its tip.
  writeFileSync(join(dir, 'docs.md'), 'on main\n');
  git(dir, 'add', '.');
  git(dir, 'commit', '--quiet', '-m', 'main work');
  assert.deepEqual(await sandbox.changes(dir, 'm-2'), changes);

  // A branch with nothing on it yet, and no branch at all.
  await sandbox.prepare(dir, 'm-2-empty');
  assert.deepEqual(await sandbox.changes(dir, 'm-2-empty'), { files: [] });
  await assert.rejects(sandbox.changes(dir, 'm-never'), /沙盒分支/);
});

test('merge folds the branch back with the trailer, and only from a clean default branch', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-3');
  const branchTip = commitIn(tree.root, 'fix.md', 'fixed\n', 'm-3');

  // Refusals first: the working directory elsewhere, then dirty.
  git(dir, 'checkout', '--quiet', '-b', 'off-topic');
  await assert.rejects(sandbox.merge(dir, 'm-3'), /不在默认分支/);
  git(dir, 'checkout', '--quiet', 'main');
  writeFileSync(join(dir, 'README.md'), 'uncommitted\n');
  await assert.rejects(sandbox.merge(dir, 'm-3'), /未提交的改动/);
  git(dir, 'checkout', '--quiet', '--', 'README.md');

  const { commit } = await sandbox.merge(dir, 'm-3');
  assert.equal(commit, git(dir, 'rev-parse', 'HEAD'));
  const parents = git(dir, 'log', '-1', '--format=%P').split(/\s+/);
  assert.equal(parents.length, 2, 'the merge is a true merge, not a fast-forward');
  assert.equal(parents[1], branchTip);
  const lines = git(dir, 'log', '-1', '--format=%B').split('\n').filter(l => l.trim());
  assert.equal(lines[0], 'Merge sandbox branch calli/proactive/m-3');
  assert.equal(lines[lines.length - 1], 'Calli-Proactive: m-3', 'the trailer is the last line, on its own');

  // Merging again does nothing: the branch holds nothing new beyond the default branch.
  await assert.rejects(sandbox.merge(dir, 'm-3'), /无东西可合入/);

  // The branch survives the merge; cleaning it up is discard's job.
  assert.equal(existsSync(tree.root), true);
  assert.equal(git(tree.root, 'symbolic-ref', '--short', 'HEAD'), 'calli/proactive/m-3');
  assert.match(git(dir, 'branch', '--list', 'calli/proactive/m-3'), /calli\/proactive\/m-3/);
});

test('discard removes the worktree and the branch, and is happy when they are gone', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-4');
  writeFileSync(join(tree.root, 'wip.md'), 'unfinished\n'); // even uncommitted work goes
  await sandbox.discard(dir, 'm-4');
  assert.equal(existsSync(tree.root), false);
  assert.equal(git(dir, 'branch', '--list', 'calli/*'), '');
  assert.equal(git(dir, 'worktree', 'list').split('\n').length, 1);

  await sandbox.discard(dir, 'm-4'); // gone means gone, and twice is fine
  await sandbox.discard(dir, 'm-never-prepared'); // nothing was ever there
});

test('revert undoes the matter’s commits newest first and leaves the merge alone', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-5');
  const older = commitIn(tree.root, 'a.md', 'a\n', 'm-5');
  const newer = commitIn(tree.root, 'b.md', 'b\n', 'm-5');
  await sandbox.merge(dir, 'm-5');
  const mergeCommit = git(dir, 'rev-parse', 'HEAD');

  // The merge commit carries the trailer too; only the sandbox's own commits are reverted.
  const { commits } = await sandbox.revert(dir, 'm-5');
  assert.deepEqual(commits, [newer, older]);
  assert.equal(existsSync(join(dir, 'a.md')), false);
  assert.equal(existsSync(join(dir, 'b.md')), false);
  assert.equal(git(dir, 'status', '--porcelain'), '');
  assert.match(git(dir, 'log', '-2', '--format=%B'), /This reverts commit/);
  assert.equal(ancestor(dir, mergeCommit, 'HEAD'), true, 'history grew; nothing was rewritten');
  assert.equal(ancestor(dir, older, 'HEAD'), true);

  // A second revert has nothing clean left to undo: it refuses whole instead of meddling.
  const headBefore = git(dir, 'rev-parse', 'HEAD');
  await assert.rejects(sandbox.revert(dir, 'm-5'), /已回滚/);
  assert.equal(git(dir, 'rev-parse', 'HEAD'), headBefore);
  assert.equal(git(dir, 'status', '--porcelain'), '');
});

test('revert matches the matter id exactly: m-1 does not touch m-10', async () => {
  const dir = repo();
  const one = await sandbox.prepare(dir, 'm-1');
  commitIn(one.root, 'one.md', 'one\n', 'm-1');
  await sandbox.merge(dir, 'm-1');
  const ten = await sandbox.prepare(dir, 'm-10');
  commitIn(ten.root, 'ten.md', 'ten\n', 'm-10');
  await sandbox.merge(dir, 'm-10');

  const { commits } = await sandbox.revert(dir, 'm-1');
  assert.equal(commits.length, 1);
  assert.equal(existsSync(join(dir, 'ten.md')), true, 'm-10’s work stays');
  assert.equal(existsSync(join(dir, 'one.md')), false);
  assert.equal(git(dir, 'status', '--porcelain'), '');
});

test('revert works on a machine with no git identity at all, committing as the hub', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-7');
  const sha = commitIn(tree.root, 'note.md', 'from the sandbox\n', 'm-7');
  await sandbox.merge(dir, 'm-7');

  // No config, no env: every escape hatch an identity could hide in is closed (the file-wide
  // empty GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM and the temp HOME see to that); this clears the
  // rest. revert applies its patch before it needs an identity, so the hub's own must be there
  // from the first attempt — a retry after failure would find the working directory already dirty.
  const stripped = ['GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'EMAIL'];
  const saved = stripped.map(name => [name, process.env[name]] as const);
  for (const name of stripped) delete process.env[name];
  try {
    const { commits } = await sandbox.revert(dir, 'm-7');
    assert.deepEqual(commits, [sha]);
    assert.equal(existsSync(join(dir, 'note.md')), false);
    assert.equal(git(dir, 'log', '-1', '--format=%an'), 'calli', 'the undo commit is made as the hub');
  } finally {
    for (const [name, value] of saved) if (value !== undefined) process.env[name] = value;
  }
  assert.equal(git(dir, 'status', '--porcelain'), '');
});

test('a conflicting revert is rolled back whole', async () => {
  const dir = repo();
  const tree = await sandbox.prepare(dir, 'm-6');
  writeFileSync(join(tree.root, 'README.md'), 'from the sandbox\n');
  git(tree.root, 'add', '.');
  git(tree.root, 'commit', '--quiet', '-m', 'rewrite readme\n\nCalli-Proactive: m-6');
  const sandboxCommit = git(tree.root, 'rev-parse', 'HEAD');
  await sandbox.merge(dir, 'm-6');

  // Someone moves the same line afterwards, without the sandbox.
  writeFileSync(join(dir, 'README.md'), 'moved on without the sandbox\n');
  git(dir, 'add', '.');
  git(dir, 'commit', '--quiet', '-m', 'main rewrites readme');

  writeFileSync(join(dir, 'README.md'), 'dirty\n');
  await assert.rejects(sandbox.revert(dir, 'm-6'), /未提交的改动/);
  git(dir, 'checkout', '--quiet', '--', 'README.md');

  const headBefore = git(dir, 'rev-parse', 'HEAD');
  await assert.rejects(sandbox.revert(dir, 'm-6'), /已回滚/);
  assert.equal(git(dir, 'rev-parse', 'HEAD'), headBefore, 'no half revert stays');
  assert.equal(git(dir, 'status', '--porcelain'), '', 'and nothing is left in progress');
  assert.equal(ancestor(dir, sandboxCommit, 'HEAD'), true);
});

test('revert with nothing to undo does nothing', async () => {
  const dir = repo();
  const head = git(dir, 'rev-parse', 'HEAD');
  assert.deepEqual(await sandbox.revert(dir, 'm-404'), { commits: [] });
  assert.equal(git(dir, 'rev-parse', 'HEAD'), head);
  await assert.rejects(sandbox.merge(dir, 'm-404'), /沙盒分支/);
});
