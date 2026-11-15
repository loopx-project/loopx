import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { addWorktree, countChanges, findCheckout, removeWorktree, worktreeLeftovers, WORKTREES_DIR } from '../src/worktrees.js';

// A machine with no git identity and no settings of its own: the hub must not lean on either.
const scratch = realpathSync(mkdtempSync(join(tmpdir(), 'calli-test-git-')));
writeFileSync(join(scratch, 'gitconfig'), '');
process.env.GIT_CONFIG_GLOBAL = join(scratch, 'gitconfig');
process.env.GIT_CONFIG_NOSYSTEM = '1';
after(() => rmSync(scratch, { recursive: true, force: true }));

/** git as the person at the keyboard would run it: with a name to commit under. */
const git = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-c', 'user.name=Tester', '-c', 'user.email=tester@example.com', ...args], { cwd, encoding: 'utf8' }).trim();

let made = 0;
/** A repository with one commit: two tracked files, and `ignored/` and `.env` in its .gitignore. */
function repo(): string {
  const dir = join(scratch, `repo-${++made}`);
  mkdirSync(join(dir, 'src'), { recursive: true });
  git(dir, 'init', '--quiet', '--initial-branch=main');
  writeFileSync(join(dir, '.gitignore'), 'ignored/\n.env\n');
  writeFileSync(join(dir, 'README.md'), 'hello\n');
  writeFileSync(join(dir, 'src', 'app.ts'), 'export const a = 1;\n');
  git(dir, 'add', '.');
  git(dir, 'commit', '--quiet', '-m', 'first');
  return dir;
}

test('findCheckout says where a directory stands, or that no worktree can start there', async () => {
  const plain = join(scratch, 'not-a-repo');
  mkdirSync(plain);
  assert.equal(await findCheckout(plain), null);

  const empty = join(scratch, 'no-commit-yet');
  mkdirSync(empty);
  git(empty, 'init', '--quiet');
  assert.equal(await findCheckout(empty), null, 'nothing to start a worktree from');

  const dir = repo();
  assert.deepEqual(await findCheckout(dir), { top: dir, main: dir, prefix: '', branch: 'main' });
  assert.deepEqual(await findCheckout(join(dir, 'src')), { top: dir, main: dir, prefix: 'src', branch: 'main' });

  // From inside a worktree: the checkout is the worktree, the repository is still the repository.
  const tree = await addWorktree((await findCheckout(dir))!, 'aaaa1111');
  assert.deepEqual(await findCheckout(join(tree.root, 'src')), { top: tree.root, main: dir, prefix: 'src', branch: 'calli/aaaa1111' });

  git(dir, 'checkout', '--quiet', '--detach');
  assert.equal((await findCheckout(dir))!.branch, null);

  // A repository reached through a link is found under the spelling it was asked by.
  const link = join(scratch, 'link-to-repo');
  symlinkSync(dir, link);
  assert.equal((await findCheckout(join(link, 'src')))!.main, link);
});

test('a new worktree starts at the same commit, on its own branch, with what was not committed', async () => {
  const dir = repo();
  writeFileSync(join(dir, 'README.md'), 'hello, changed\n'); // changed, not staged
  writeFileSync(join(dir, 'src', 'new.ts'), 'export const n = 2;\n');
  git(dir, 'add', 'src/new.ts'); // new and staged
  rmSync(join(dir, 'src', 'app.ts')); // deleted
  mkdirSync(join(dir, 'notes'));
  writeFileSync(join(dir, 'notes', 'todo.txt'), 'untracked\n'); // git does not know it yet
  mkdirSync(join(dir, 'ignored'));
  writeFileSync(join(dir, 'ignored', 'big.bin'), 'x');
  writeFileSync(join(dir, '.env'), 'SECRET=1\n');
  const before = git(dir, 'status', '--porcelain');
  assert.equal(await countChanges(dir), 4);

  const tree = await addWorktree((await findCheckout(dir))!, 'bbbb2222');
  assert.deepEqual(tree, { root: join(dir, WORKTREES_DIR, 'bbbb2222'), branch: 'calli/bbbb2222' });
  assert.equal(git(tree.root, 'rev-parse', 'HEAD'), git(dir, 'rev-parse', 'HEAD'));
  assert.equal(git(tree.root, 'symbolic-ref', '--short', 'HEAD'), 'calli/bbbb2222');

  // The same work in progress, staged where it was staged.
  assert.equal(readFileSync(join(tree.root, 'README.md'), 'utf8'), 'hello, changed\n');
  assert.equal(readFileSync(join(tree.root, 'src', 'new.ts'), 'utf8'), 'export const n = 2;\n');
  assert.equal(existsSync(join(tree.root, 'src', 'app.ts')), false);
  assert.equal(readFileSync(join(tree.root, 'notes', 'todo.txt'), 'utf8'), 'untracked\n');
  assert.equal(git(tree.root, 'status', '--porcelain'), before);
  // What git ignores stays behind: dependencies, build output, secrets.
  assert.equal(existsSync(join(tree.root, 'ignored')), false);
  assert.equal(existsSync(join(tree.root, '.env')), false);

  // The directory it was made from is exactly as it was, and git does not list the worktrees as new files.
  assert.equal(git(dir, 'status', '--porcelain'), before);
  assert.equal(git(dir, 'stash', 'list'), '');
  assert.match(readFileSync(join(dir, '.git', 'info', 'exclude'), 'utf8'), /^\/\.worktrees\/$/m);

  // A second one does not add the line again; a repository that ignores the directory itself gets none.
  await addWorktree((await findCheckout(dir))!, 'cccc3333');
  assert.equal(readFileSync(join(dir, '.git', 'info', 'exclude'), 'utf8').match(/\.worktrees/g)!.length, 1);
  const own = repo();
  writeFileSync(join(own, '.gitignore'), 'ignored/\n.env\n.worktrees/\n');
  git(own, 'commit', '--quiet', '-am', 'ignore worktrees');
  await addWorktree((await findCheckout(own))!, 'dddd4444');
  assert.doesNotMatch(readFileSync(join(own, '.git', 'info', 'exclude'), 'utf8'), /\.worktrees/);
});

test('a worktree made from a worktree starts from that one, and lands beside it', async () => {
  const dir = repo();
  const first = await addWorktree((await findCheckout(dir))!, 'eeee5555');
  writeFileSync(join(first.root, 'README.md'), 'only in the first worktree\n');
  git(first.root, 'commit', '--quiet', '-am', 'on the branch');
  writeFileSync(join(first.root, 'scratch.txt'), 'not committed\n');

  const second = await addWorktree((await findCheckout(first.root))!, 'ffff6666');
  assert.equal(second.root, join(dir, WORKTREES_DIR, 'ffff6666'));
  assert.equal(git(second.root, 'rev-parse', 'HEAD'), git(first.root, 'rev-parse', 'HEAD'));
  assert.equal(readFileSync(join(second.root, 'scratch.txt'), 'utf8'), 'not committed\n');
});

test('adding fails whole: a name that is taken leaves nothing new behind', async () => {
  const dir = repo();
  const checkout = (await findCheckout(dir))!;
  await addWorktree(checkout, 'aaaa0000');
  await assert.rejects(addWorktree(checkout, 'aaaa0000'), /already exists/);
  git(dir, 'branch', 'calli/bbbb0000');
  await assert.rejects(addWorktree(checkout, 'bbbb0000'));
  assert.equal(existsSync(join(dir, WORKTREES_DIR, 'bbbb0000')), false);
  assert.equal(git(dir, 'worktree', 'list').split('\n').length, 2);
});

test('what a worktree would take with it is counted, and removing it takes the branch too', async () => {
  const dir = repo();
  const tree = await addWorktree((await findCheckout(dir))!, 'abab1212');
  assert.deepEqual(await worktreeLeftovers(dir, tree), { changes: 0, commits: 0 });

  writeFileSync(join(tree.root, 'draft.md'), 'wip\n');
  assert.deepEqual(await worktreeLeftovers(dir, tree), { changes: 1, commits: 0 });
  git(tree.root, 'add', '.');
  git(tree.root, 'commit', '--quiet', '-m', 'on the branch only');
  assert.deepEqual(await worktreeLeftovers(dir, tree), { changes: 0, commits: 1 });
  // Once another branch has the commit, nothing is lost with this one.
  git(dir, 'merge', '--quiet', 'calli/abab1212');
  assert.deepEqual(await worktreeLeftovers(dir, tree), { changes: 0, commits: 0 });

  writeFileSync(join(tree.root, 'draft.md'), 'changed again\n');
  await removeWorktree(dir, tree);
  assert.equal(existsSync(tree.root), false);
  assert.equal(git(dir, 'branch', '--list', 'calli/*'), '');
  assert.equal(git(dir, 'worktree', 'list').split('\n').length, 1);

  // Someone deleted the directory by hand: git's own record of it is cleared all the same.
  const gone = await addWorktree((await findCheckout(dir))!, 'cdcd3434');
  rmSync(gone.root, { recursive: true, force: true });
  await removeWorktree(dir, gone);
  assert.equal(git(dir, 'worktree', 'list').split('\n').length, 1);
  assert.equal(git(dir, 'branch', '--list', 'calli/*'), '');
});

test('a rename is one change', async () => {
  const dir = repo();
  git(dir, 'mv', 'README.md', 'READ.md');
  assert.equal(await countChanges(dir), 1);
});
