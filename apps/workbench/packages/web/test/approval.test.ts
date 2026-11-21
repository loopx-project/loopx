import test from 'node:test';
import assert from 'node:assert/strict';
import { approvalContent } from '../src/approval';
import { Api, ApiError } from '../src/api';
import { t } from '../src/i18n';

const content = (title: string, command: string | null) => approvalContent(title, command, 'Run command', 'Approve operation?');

test('commands and oversized titles remain complete without becoming the heading', () => {
  const command = "python3 - <<'PY'\n" + 'print("permission details")\n'.repeat(120) + 'PY';
  assert.deepEqual(content(command, command), { title: 'Run command', detail: command });
  assert.deepEqual(content(` ${command}\n`, command), { title: 'Run command', detail: command });
  assert.deepEqual(content(command, null), { title: 'Approve operation?', detail: command });
  assert.deepEqual(content('x'.repeat(161), null), { title: 'Approve operation?', detail: 'x'.repeat(161) });
  assert.deepEqual(content('Important scope\nOutside the workspace', command), {
    title: 'Run command', detail: `Important scope\nOutside the workspace\n\n${command}`,
  });
  assert.deepEqual(content('Read one file', 'cat "a b.txt"'), { title: 'Read one file', detail: 'cat "a b.txt"' });
  assert.deepEqual(content('Read one file', null), { title: 'Read one file', detail: null });
  assert.deepEqual(content('  ', null), { title: 'Approve operation?', detail: null });
  assert.deepEqual(content('', command), { title: 'Run command', detail: command });
});

test('approval POST preserves native option IDs, cancellation and host routing, and clears its timeout', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const requests: Array<{ url: string; init: RequestInit }> = [];
  context.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    requests.push({ url, init });
    return new Response(null, { status: 204 });
  });
  const api = new Api('fictional-token', 'https://hub.example.test', 'other');
  await api.decide('approval-1', { optionId: 'native-scope-choice' });
  await api.decide('approval-2', { cancel: true });
  assert.equal(requests[0].url, 'https://hub.example.test/api/hosts/other/proxy/approvals/approval-1');
  assert.equal(requests[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(String(requests[0].init.body)), { optionId: 'native-scope-choice' });
  assert.deepEqual(JSON.parse(String(requests[1].init.body)), { cancel: true });
  context.mock.timers.tick(20_000);
  assert.ok(requests.every(request => request.init.signal?.aborted === false));
});

test('a stalled approval aborts after 15 seconds with a retryable error', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let signal: AbortSignal;
  context.mock.method(globalThis, 'fetch', (_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    signal = init.signal as AbortSignal;
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
  }));
  const request = new Api('fictional-token').decide('approval-1', { optionId: 'allow' });
  const checked = assert.rejects(request, { message: t('sheet.approval.timeout') });
  context.mock.timers.tick(14_999);
  assert.equal(signal!.aborted, false);
  context.mock.timers.tick(1);
  await checked;
  assert.equal(signal!.aborted, true);
});

test('approval failures retain their status and do not leave a timer running', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let signal: AbortSignal;
  context.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => {
    signal = init.signal as AbortSignal;
    return Response.json({ error: 'approval not found or already resolved' }, { status: 404 });
  });
  await assert.rejects(new Api('fictional-token').decide('approval-1', { cancel: true }), error =>
    error instanceof ApiError && error.status === 404 && error.message === 'approval not found or already resolved');
  context.mock.timers.tick(20_000);
  assert.equal(signal!.aborted, false);
});
