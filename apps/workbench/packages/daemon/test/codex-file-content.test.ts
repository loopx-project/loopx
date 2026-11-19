import { test } from 'node:test';
import assert from 'node:assert/strict';
import { codexDiff, mapCodexItemEnd, mapCodexItemStart } from '../src/adapters/codex.js';

for (const kind of ['add', 'delete']) {
  test(`Codex ${kind} preserves indented full file content in start and end cards`, () => {
    for (const text of ['def f():\n    return 1\n', '- item\n+ another\n', '\\command\n']) {
      const change = { path: 'example.py', kind: { type: kind }, diff: text };
      const expected = { type: 'diff', path: change.path, oldText: kind === 'delete' ? text : null, newText: kind === 'add' ? text : '' };
      assert.deepEqual(codexDiff(change), expected);
      const item = { id: 'file-1', type: 'fileChange', status: 'completed', changes: [change] };
      assert.deepEqual(mapCodexItemStart(item)?.data.content, [expected]);
      assert.deepEqual(mapCodexItemEnd(item)?.data.content, [expected]);
    }
  });
}
