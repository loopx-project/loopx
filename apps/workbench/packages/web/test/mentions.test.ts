import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mentionAt } from '../src/mentions.js';

test('mentions follow the caret in an existing draft and preserve surrounding text', () => {
  for (const [text, caret, expected] of [
    ['@ 另外，请审查方案', 1, ' 另外，请审查方案'],
    ['前文 @cl 后文', 6, '前文  后文'],
    ['前文\n@cl 后文', 6, '前文\n 后文'],
    ['@请审查方案', 1, '请审查方案'],
    ['前文 @claude', 10, '前文 '],
  ] as const) {
    const match = mentionAt(text, caret);
    assert.ok(match, text);
    assert.equal(text.slice(0, match.start) + text.slice(match.end), expected);
  }
  assert.deepEqual(mentionAt('@claude 后文', 3), { start: 0, end: 3, query: 'cl' });
});

test('email addresses, selected text and a caret outside the mention do not open suggestions', () => {
  assert.equal(mentionAt('mail a@b', 8), null);
  assert.equal(mentionAt('@claude 后文', 10), null);
  assert.equal(mentionAt('@claude', 1, 7), null);
  assert.equal(mentionAt('@', 0), null);
});
