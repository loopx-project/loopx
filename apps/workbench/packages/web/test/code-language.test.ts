import { test } from 'node:test';
import assert from 'node:assert/strict';
import { highlightLanguage, languageName } from '../src/code-language.js';

test('a block is named as people write the language, and plain text is named nothing', () => {
  assert.equal(languageName('bash'), 'Bash');
  assert.equal(languageName('TS'), 'TypeScript');
  assert.equal(languageName('json'), 'JSON');
  assert.equal(languageName('c++'), 'C++');
  assert.equal(languageName('elixir'), 'Elixir');
  for (const plain of ['', 'text', 'TXT', 'plaintext']) assert.equal(languageName(plain), null);
});

test('names the highlighter does not know go to the one it does', () => {
  assert.equal(highlightLanguage('shell'), 'bash');
  assert.equal(highlightLanguage('YML'), 'yaml');
  assert.equal(highlightLanguage('Python'), 'python');
});
