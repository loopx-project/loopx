import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diffLines, diffStats, numberLines } from '../src/diff.js';

const show = (old: string | null, next: string) =>
  diffLines(old, next).map((l) => `${l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' '}${l.text}`);

test('a new file is all additions', () => {
  assert.deepEqual(show(null, 'a\nb\n'), ['+a', '+b']);
  assert.deepEqual(diffStats(null, 'a\nb\n'), { added: 2, removed: 0 });
  assert.deepEqual(diffStats(null, ''), { added: 0, removed: 0 });
});

test('a changed line in the middle keeps its neighbours as context', () => {
  assert.deepEqual(show('const a = 1;\nconst b = 2;\nconst c = 3;', 'const a = 1;\nconst b = 20;\nconst c = 3;'), [
    ' const a = 1;',
    '-const b = 2;',
    '+const b = 20;',
    ' const c = 3;',
  ]);
});

test('insertions and deletions are found without marking unchanged lines', () => {
  assert.deepEqual(show('a\nb\nc\nd', 'a\nc\nd\ne'), [' a', '-b', ' c', ' d', '+e']);
  assert.deepEqual(diffStats('a\nb\nc\nd', 'a\nc\nd\ne'), { added: 1, removed: 1 });
  assert.deepEqual(show('one\ntwo\nthree', 'one\nthree'), [' one', '-two', ' three']);
  assert.deepEqual(show('x', 'w\nx\ny'), ['+w', ' x', '+y']);
});

test('identical text has no changes; a trailing newline is not an extra line', () => {
  assert.deepEqual(diffStats('same\ntext\n', 'same\ntext\n'), { added: 0, removed: 0 });
  assert.deepEqual(diffStats('a\nb', 'a\nb\n'), { added: 0, removed: 0 });
});

test('deleting everything is all removals', () => {
  assert.deepEqual(show('a\nb', ''), ['-a', '-b']);
});

test('very large regions fall back to removed-then-added instead of hanging', () => {
  const old = Array.from({ length: 1500 }, (_, i) => `old ${i}`).join('\n');
  const next = Array.from({ length: 1500 }, (_, i) => `new ${i}`).join('\n');
  const started = Date.now();
  assert.deepEqual(diffStats(old, next), { added: 1500, removed: 1500 });
  assert.ok(Date.now() - started < 1000);
});

test('numberLines counts old and new line numbers separately', () => {
  const numbered = numberLines(diffLines('a\nb\nc', 'a\nB\nc\nd'));
  assert.deepEqual(
    numbered.map((l) => [l.type, l.oldNo, l.newNo]),
    [
      ['ctx', 1, 1],
      ['del', 2, null],
      ['add', null, 2],
      ['ctx', 3, 3],
      ['add', null, 4],
    ],
  );
});
