import assert from 'node:assert/strict';
import { createServer, request as httpRequest } from 'node:http';
import { createServer as createNetServer } from 'node:net';
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

test('a chunked request reaches the core as plain bytes with their length, and named hop-by-hop headers stay behind', async () => {
  // A raw socket sees exactly what Python's http.server would: headers, then content-length bytes.
  let raw = '';
  const got = new Promise<void>((resolve) => {
    const core = createNetServer((socket) => {
      socket.on('data', (c) => {
        raw += c;
        const [head, body = ''] = raw.split('\r\n\r\n');
        const length = Number(/content-length: (\d+)/i.exec(head!)?.[1] ?? -1);
        if (length >= 0 && Buffer.byteLength(body) >= length) {
          socket.end('HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{"ok":true}');
          core.close();
          resolve();
        }
      });
    });
    core.listen(0, '127.0.0.1', () => {
      const coreUrl = new URL(`http://127.0.0.1:${(core.address() as AddressInfo).port}`);
      const front = createServer((req, res) => void proxyToCore(req, res, '/api/actions/preview', '', coreUrl).then(() => front.close()));
      front.listen(0, '127.0.0.1', () => {
        const out = httpRequest({ port: (front.address() as AddressInfo).port, method: 'POST', path: '/x',
          headers: { 'content-type': 'application/json', connection: 'keep-alive, x-hop', 'x-hop': '1', te: 'trailers' } });
        out.write('{"kind":');
        out.end('"goal.create"}');
        out.on('response', (r) => r.resume());
      });
    });
  });
  await got;
  const [head, body] = raw.split('\r\n\r\n');
  assert.equal(body, '{"kind":"goal.create"}');
  assert.match(head!, /content-length: 22/i);
  assert.doesNotMatch(head!, /transfer-encoding|x-hop|^te:/im);
});
