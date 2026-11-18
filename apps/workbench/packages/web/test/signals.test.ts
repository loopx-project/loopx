import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Api, ApiError, type SignalMaterialRead } from '../src/api.js';
import { t } from '../src/i18n/index.js';
import { boundedSignalMaterial, canRefreshSignalMaterial, SIGNAL_MATERIAL_MAX_CHARS, signalMaterialLine, signalSourceUrl } from '../src/signals.js';

const key = 'fictional:meeting/1?note=%23#fragment';

test('material GET encodes the entire signal key and follows host routing without reading the list', async context => {
  const result: SignalMaterialRead = { key, material: { state: 'ready', kind: 'note', partial: true }, content: '# Fictional follow-up' };
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  context.mock.method(globalThis, 'fetch', async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    return Response.json(result);
  });
  const api = new Api('fictional-token', 'https://hub.example.test', 'fictional/host');
  assert.equal(requests.length, 0, 'constructing the client does not fetch materials');
  assert.deepEqual(await api.readSignalMaterial(key), result);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `https://hub.example.test/api/hosts/fictional%2Fhost/proxy/personal/signals/${encodeURIComponent(key)}/material`);
  assert.equal(requests[0].init?.method, 'GET');
  assert.equal(requests[0].init?.body, undefined);
  assert.equal(new Headers(requests[0].init?.headers).get('Accept-Language'), 'zh-CN');
  assert.equal(new Headers(requests[0].init?.headers).get('Authorization'), 'Bearer fictional-token');
});

test('an older hub without the material endpoint reports an upgrade error instead of an empty read', async context => {
  context.mock.method(globalThis, 'fetch', async () => Response.json({ error: 'not found' }, { status: 404 }));
  await assert.rejects(new Api('fictional-token').readSignalMaterial(key), error => error instanceof ApiError && error.status === 404 && error.message === t('api.updateBackend'));
});

test('HTTP failure remains a failure and every explicit material request reaches the endpoint', async context => {
  const fetch = context.mock.method(globalThis, 'fetch', async () => Response.json({ error: 'fictional read failure' }, { status: 503 }));
  const api = new Api('fictional-token');
  await assert.rejects(api.readSignalMaterial(key), error => error instanceof ApiError && error.status === 503 && error.message === 'fictional read failure');
  assert.equal(fetch.mock.callCount(), 1);
  fetch.mock.mockImplementation(async () => Response.json({ key, material: { state: 'empty', partial: true }, content: null }));
  const result = await api.readSignalMaterial(key);
  assert.equal(result.material.state, 'empty');
  assert.equal(result.material.partial, true);
  assert.equal(result.content, null);
  await api.readSignalMaterial(key);
  assert.equal(fetch.mock.callCount(), 3, 'retry and refresh are fresh user requests, not a sticky client error or empty cache');
});

test('row metadata distinguishes unread, available, waiting, failed, empty and partial-empty reads', () => {
  assert.deepEqual(signalMaterialLine(null, false), { text: '资料未读取', warn: false });
  assert.deepEqual(signalMaterialLine({ state: 'unread' }, false), { text: '资料未读取', warn: false });
  assert.deepEqual(signalMaterialLine({ state: 'ready' }, false), { text: '资料可读取', warn: false });
  assert.deepEqual(signalMaterialLine({ state: 'ready' }, true), { text: '资料已读取', warn: false });
  assert.deepEqual(signalMaterialLine({ state: 'not-ready' }, false), { text: '资料尚未就绪', warn: true });
  assert.deepEqual(signalMaterialLine({ state: 'failed', error: 'fictional failure' }, false), { text: '资料读取失败', warn: true });
  assert.deepEqual(signalMaterialLine({ state: 'empty' }, true), { text: '已读取，没有内容', warn: false });
  assert.deepEqual(signalMaterialLine({ state: 'empty', kind: 'note', partial: true }, true), { text: '本次未找到匹配内容', warn: false });
});

test('only a complete ready read needs no fresh-read action', () => {
  assert.equal(canRefreshSignalMaterial({ state: 'ready' }), false);
  assert.equal(canRefreshSignalMaterial({ state: 'ready', partial: false }), false);
  assert.equal(canRefreshSignalMaterial({ state: 'ready', partial: true }), true);
  for (const state of ['unread', 'not-ready', 'failed', 'empty'] as const) assert.equal(canRefreshSignalMaterial({ state }), true);
  assert.equal(canRefreshSignalMaterial(null), true);
});

test('material rendering is bounded without splitting a surrogate pair or hiding that it was clipped', () => {
  assert.deepEqual(boundedSignalMaterial(null), { text: '', clipped: false });
  assert.deepEqual(boundedSignalMaterial('  # Fictional text\n'), { text: '  # Fictional text\n', clipped: false });
  assert.equal(boundedSignalMaterial('x'.repeat(SIGNAL_MATERIAL_MAX_CHARS)).clipped, false);
  const bounded = boundedSignalMaterial('x'.repeat(SIGNAL_MATERIAL_MAX_CHARS - 1) + '\u{1F4C4}' + ' trailing');
  assert.equal(bounded.text.length, SIGNAL_MATERIAL_MAX_CHARS - 1);
  assert.equal(bounded.clipped, true);
});

test('source links allow only explicit web URLs, not scripts, data, relative paths or local files', () => {
  assert.equal(signalSourceUrl('https://meeting.example.test/notes/fictional?view=1'), 'https://meeting.example.test/notes/fictional?view=1');
  assert.equal(signalSourceUrl('http://meeting.example.test/fictional'), 'http://meeting.example.test/fictional');
  for (const url of [undefined, '', 'javascript:alert(1)', 'data:text/html,fictional', 'file:///fictional/note', '//meeting.example.test/fictional', '/fictional/note']) assert.equal(signalSourceUrl(url), null);
});
