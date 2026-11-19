import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { EmittedEvent } from '../src/events.js';
import { agySearchTool } from '../src/adapters/agy-web.js';
import { codexReadTool, codexSearchTool } from '../src/adapters/codex-tools.js';
import { readInput, searchInput } from '../src/adapters/borrowed-web.js';

const answer = 'See [Example](https://example.com/) and [Bad](file:///secret).';
const events = (tool: string, action?: string, status = 'completed'): EmittedEvent[] => [
  { type: 'tool.start', data: { id: 'one', tool, action: { type: 'other' } } },
  { type: 'tool.end', data: { id: 'one', status, action: { type: action },
    results: [{ url: 'https://example.com', title: 'Native example' }, { url: 'javascript:alert(1)', title: 'bad' }] } },
];

test('web collectors require successful native execution of the requested capability', () => {
  assert.throws(() => agySearchTool.collect([], answer, {}), /native/);
  assert.throws(() => agySearchTool.collect(events('run_command'), answer, {}), /native/);
  assert.throws(() => agySearchTool.collect(events('search_web', undefined, 'failed'), answer, {}), /native/);
  assert.throws(() => codexSearchTool.collect(events('web_search', 'openPage'), answer, {}), /native/);
  assert.throws(() => codexReadTool.collect(events('web_search', 'search'), answer, {}), /native/);
  assert.throws(() => codexSearchTool.collect(events('web_search', 'search'), '', {}), /no answer/);
  const result = codexSearchTool.collect(events('web_search', 'search'), answer, {});
  assert.deepEqual(result.data, { sources: [{ url: 'https://example.com/', title: 'Native example', provenance: 'native' }], nativeCalls: 1 });
});

test('web citations distinguish native results from model-provided links', () => {
  const result = agySearchTool.collect([
    { type: 'tool.start', data: { id: 'search', tool: 'search_web' } },
    { type: 'tool.end', data: { id: 'search', status: 'completed' } },
  ], answer, {});
  assert.deepEqual(result.data, { sources: [{ url: 'https://example.com/', title: 'Example', provenance: 'answer' }], nativeCalls: 1 });
});

test('reading a different page cannot satisfy a URL-specific task', () => {
  assert.throws(() => codexReadTool.collect(events('web_search', 'openPage'), answer, { url: 'https://other.example/' }), /native/);
  const result = codexReadTool.collect(events('web_search', 'openPage'), answer, { url: 'https://example.com/' });
  assert.equal(result.data?.nativeCalls, 1);
});

test('web input rejects extra fields, empty queries, unsafe URL schemes and embedded credentials', () => {
  for (const input of [null, [], 'query', {}, { query: '' }, { query: 'x'.repeat(2001) }, { query: 'fine', shell: 'rm' }]) {
    assert.throws(() => searchInput(input, 'native'), /input|query/);
  }
  for (const url of ['file:///etc/passwd', 'data:text/html,foo', 'https://user:password@example.com', 'not a URL']) {
    assert.throws(() => readInput({ url }, 'native'), /HTTP/);
  }
  assert.throws(() => readInput({ url: 'https://example.com', question: '' }, 'native'), /question/);
  assert.deepEqual(readInput({ url: 'https://example.com' }, 'native').input,
    { url: 'https://example.com/', question: 'Summarize the main content.' });
  assert.deepEqual(searchInput({ query: ' query ' }, 'native').input, { query: 'query' });
});
