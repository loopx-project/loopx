import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';
import { corePath, proxyToCore } from '../src/adapters/loopx/proxy.js';

test('only the core namespaces are forwarded', () => {
  assert.equal(corePath('/api/loopx/status.json'), '/status.json');
  assert.equal(corePath('/api/loopx/api/actions/preview'), '/api/actions/preview');
  assert.equal(corePath('/api/loopx/launch'), null);
  assert.equal(corePath('/api/threads'), null);
  assert.equal(corePath('/api/loopx/../x'), null);
});

test('a request is forwarded without the hub credentials, and a down core is a 502', async () => {
  let seen: { auth?: string; host?: string; url?: string; body: string; length?: string; encoding?: string } = { body: '' };
  const core = createServer((req, res) => {
    seen = { auth: req.headers.authorization, host: req.headers.host, url: req.url, body: '', length: req.headers['content-length'], encoding: req.headers['transfer-encoding'] };
    req.on('data', (c) => (seen.body += c));
    req.on('end', () => { res.writeHead(201, { 'Content-Type': 'application/json' }); res.end('{"ok":true}'); });
  });
  await new Promise<void>((r) => core.listen(0, '127.0.0.1', r));
  const coreUrl = new URL(`http://127.0.0.1:${(core.address() as AddressInfo).port}`);
  const front = createServer((req, res) => void proxyToCore(req, res, '/api/actions/preview', '?a=1', coreUrl));
  await new Promise<void>((r) => front.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(front.address() as AddressInfo).port}`;
  const answer = await fetch(`${base}/x`, { method: 'POST', body: '{"k":1}', headers: { authorization: 'Bearer secret', 'content-type': 'application/json' } });
  assert.equal(answer.status, 201);
  assert.deepEqual(await answer.json(), { ok: true });
  assert.equal(seen.auth, undefined);
  assert.equal(seen.host, coreUrl.host);
  assert.equal(seen.url, '/api/actions/preview?a=1');
  assert.equal(seen.body, '{"k":1}');
  // Python's http.server cannot read a chunked body: the length must be sent, never chunked.
  assert.equal(seen.length, '7');
  assert.equal(seen.encoding, undefined);
  core.close();
  const down = await fetch(`${base}/x`);
  assert.equal(down.status, 502);
  assert.equal((await down.json()).error, 'loopx_core_unavailable');
  front.close();
});
