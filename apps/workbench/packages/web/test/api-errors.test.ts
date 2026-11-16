import test from 'node:test';
import assert from 'node:assert/strict';
import { Api, ApiError } from '../src/api.js';
import { t } from '../src/i18n/index.js';

test('missing backend endpoints explain the upgrade while domain and other HTTP errors retain their meaning', () => {
  for (const message of ['not found', 'HTTP 404']) {
    const error = new ApiError(message, {}, 404);
    assert.equal(String(error), t('api.updateBackend'));
    assert.equal(error.status, 404);
  }
  assert.equal(new ApiError('thread not found', {}, 404).message, 'thread not found');
  assert.equal(new ApiError('not found', {}, 500).message, 'not found');
  assert.equal(new ApiError('unauthorized', {}, 401).message, 'unauthorized');
});


test('successful responses with unreadable bodies reject instead of returning empty records', async context => {
  const interrupted = new TypeError('body read interrupted');
  const responses = [
    new Response('{', { status: 200 }),
    new Response(new ReadableStream({ start(controller) { controller.error(interrupted); } }), { status: 200 }),
  ];
  context.mock.method(globalThis, 'fetch', async () => responses.shift()!);
  const api = new Api('test-token');
  await assert.rejects(api.outputs('thread'), SyntaxError);
  await assert.rejects(api.outputs('thread'), error => error === interrupted);
});

test('non-JSON HTTP errors retain their status and empty successful responses remain valid', async context => {
  const responses = [new Response('unavailable', { status: 503 }), new Response(null, { status: 204 })];
  context.mock.method(globalThis, 'fetch', async () => responses.shift()!);
  const api = new Api('test-token');
  await assert.rejects(api.outputs('thread'), error => error instanceof ApiError && error.status === 503 && error.message === 'HTTP 503');
  assert.equal(await api.outputs('thread'), undefined);
});
