import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Api, hostPath } from '../src/api';
import { hostProblem } from '../src/hosts';

test('on another machine, a path of the API goes through the hub to the same path there', () => {
  assert.equal(hostPath('devbox', '/api/threads'), '/api/hosts/devbox/proxy/threads');
  assert.equal(hostPath('devbox', '/api/threads/t1/stream?after=42'), '/api/hosts/devbox/proxy/threads/t1/stream?after=42');
  assert.equal(hostPath('devbox', '/api/directories?path=%2Fsrv%2Fa%20b'), '/api/hosts/devbox/proxy/directories?path=%2Fsrv%2Fa%20b', 'the query is left as it was');
  assert.equal(hostPath('a/b', '/api/config'), '/api/hosts/a%2Fb/proxy/config', 'an id cannot reach outside its own segment');
  // Only the API moves: the page itself is always the hub's.
  assert.equal(hostPath('devbox', '/'), '/');
});

test('relay connection failures explain the node-side recovery', () => {
  assert.match(hostProblem('not_joined'), /接入配置/);
  assert.match(hostProblem('relay_lost'), /自己重连/);
  assert.match(hostProblem(undefined), /检查节点/);
});

test('a machine\'s auto-titles and extension memory are asked on that machine, not this hub', async () => {
  const urls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    urls.push(`${init?.method ?? 'GET'} ${String(_input)}`);
    return new Response('{}', { status: 200 });
  }) as typeof fetch;
  try {
    const api = new Api('t', '', 'devbox');
    await api.autotitleSettings();
    await api.saveAutotitleSettings({ rules: 'x' });
    await api.previewAutotitle('t1');
    await api.organizeAutotitle();
    await api.autotitle('t1');
    await api.memorySources('autotitle');
    await api.memoryList('autotitle', 'notes');
    await api.memoryRead('autotitle', 'notes', 'f1');
    for (const url of urls) assert.match(url, / \/api\/hosts\/devbox\/proxy\//, `${url} must go through the machine's proxy`);
    assert.equal(urls[0], 'GET /api/hosts/devbox/proxy/extensions/autotitle/settings');
    assert.equal(urls[4], 'POST /api/hosts/devbox/proxy/threads/t1/autotitle');
    assert.equal(urls[7], 'GET /api/hosts/devbox/proxy/extensions/autotitle/memory/notes/document?id=f1');
    // Without a machine, the same calls stay on this hub.
    urls.length = 0;
    const local = new Api('t');
    await local.autotitleSettings();
    await local.organizeAutotitle();
    assert.deepEqual(urls, ['GET /api/extensions/autotitle/settings', 'POST /api/extensions/autotitle/organize']);
  } finally {
    globalThis.fetch = realFetch;
  }
});
