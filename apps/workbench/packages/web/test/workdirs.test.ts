import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rootRows, withWorkdirs } from '../src/workdirs.js';

test('rootRows: the daemon\'s per-root flags stand; an older one gets none removable', () => {
  assert.deepEqual(
    rootRows({ workdirs: ['/code', '/work'], roots: [{ path: '/code', removable: false }, { path: '/work', removable: true }] }),
    [{ path: '/code', removable: false }, { path: '/work', removable: true }],
  );
  assert.deepEqual(rootRows({ workdirs: ['/code'] }), [{ path: '/code', removable: false }]);
});

test('withWorkdirs: every agent of that machine carries the new list; other machines keep theirs', () => {
  const claude = { id: 'claude@box', host: 'box', directories: ['/old'] };
  const codex = { id: 'codex@box', host: 'box', directories: ['/old'] };
  const here = { id: 'codex', host: '', directories: ['/here'] };
  const next = withWorkdirs([claude, codex, here], 'box', { workdirs: ['/fresh'], roots: [{ path: '/fresh', removable: true }] });
  assert.deepEqual(next[0].directories, ['/fresh']);
  assert.deepEqual(next[1].directories, ['/fresh']);
  assert.deepEqual(next[2].directories, ['/here']);
});

test('withWorkdirs: a machine with no agents, or already carrying the list, returns it unchanged', () => {
  const one = { id: 'claude@box', host: 'box', directories: ['/old'] };
  const fresh = { workdirs: ['/fresh'] };
  const next = withWorkdirs([one], 'box', fresh);
  assert.equal(withWorkdirs(next, 'elsewhere', fresh), next);
  assert.equal(withWorkdirs(next, 'box', fresh), next);
});
