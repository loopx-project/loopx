import { test } from 'node:test';
import assert from 'node:assert/strict';
import { quoteMarkdown } from '../src/clipboard';
import { splitUserQuote } from '../src/timeline';

test('a copied quote keeps every line quoted and reads back as the same passage', () => {
  const reply = '\nFirst paragraph with **bold**.\n\n1. one\n2. two\n\n```ts\nconst a = 1;\n```\n';
  const quoted = quoteMarkdown(reply);
  assert.ok(quoted.split('\n').every(line => line === '>' || line.startsWith('> ')));
  assert.deepEqual(splitUserQuote(`${quoted}\n\nWhat do you think?`), { quote: reply.trim(), body: 'What do you think?' });
});
