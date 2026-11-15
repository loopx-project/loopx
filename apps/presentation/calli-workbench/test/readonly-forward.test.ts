import assert from 'node:assert/strict';
import { createServer, request, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { createReadOnlyForwarder } from '../readonly-forward.ts';

// A loopback stand-in for each LoopX service that records every request it receives.
interface Fake { server: Server; seen: string[]; base: string }
const listen = (server: Server) => new Promise<number>((resolve) => server.listen(0, '127.0.0.1', () => resolve((server.address() as AddressInfo).port)));
async function fake(name: string): Promise<Fake> {
  const seen: string[] = [];
  const server = createServer((req, res) => { seen.push(`${req.method} ${req.url}`); res.setHeader('x-from', name); res.end(req.method === 'HEAD' ? undefined : `${name}:${req.url}`); });
  return { server, seen, base: `http://127.0.0.1:${await listen(server)}` };
}

let status: Fake, chat: Fake, front: Server, port: number;
before(async () => {
  status = await fake('status');
  chat = await fake('chat');
  const forward = createReadOnlyForwarder({ statusBase: status.base, chatBase: chat.base });
  front = createServer((req, res) => forward(req, res, () => { res.statusCode = 299; res.end('left to vite'); }));
  port = await listen(front);
});
after(() => { front.close(); status.server.close(); chat.server.close(); });

/** A raw request line, so the path reaches the forwarder exactly as written (no client-side normalizing). */
function send(method: string, path: string): Promise<{ code: number; body: string; from?: string }> {
  return new Promise((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, method, path, setHost: true }, (res) => {
      let body = ''; res.on('data', (c) => { body += c; }); res.on('end', () => resolve({ code: res.statusCode ?? 0, body, from: res.headers['x-from'] as string | undefined }));
    });
    req.on('error', reject); req.end();
  });
}
const reached = () => status.seen.length + chat.seen.length;

const ALLOWED: Array<[string, 'status' | 'chat']> = [
  ['/status.json', 'status'],
  ['/ssh-hosts', 'chat'],
  ['/api/chat/capabilities', 'chat'],
  ['/api/chat/endpoints', 'chat'],
  ['/api/chat/goals/contexts', 'chat'],
  ['/api/chat/sessions', 'chat'],
  ['/api/chat/sessions/abc-123', 'chat'],
];

test('each enumerated route is forwarded to its own upstream exactly once', async () => {
  for (const [path, which] of ALLOWED) {
    const own = which === 'status' ? status : chat, other = which === 'status' ? chat : status;
    const before = [own.seen.length, other.seen.length];
    const res = await send('GET', path);
    assert.equal(res.code, 200, path);
    assert.equal(res.from, which, path);
    assert.equal(own.seen.length, before[0] + 1, `${path}: own upstream hit once`);
    assert.equal(other.seen.length, before[1], `${path}: the other upstream untouched`);
  }
});

test('the query string travels, the fragment does not, HEAD is forwarded as HEAD', async () => {
  const start = chat.seen.length;
  assert.equal((await send('GET', '/api/chat/sessions?limit=5#frag')).code, 200);
  assert.equal(chat.seen.at(-1), 'GET /api/chat/sessions?limit=5');
  assert.equal((await send('HEAD', '/api/chat/endpoints')).code, 200);
  assert.equal(chat.seen.at(-1), 'HEAD /api/chat/endpoints');
  assert.equal(chat.seen.length, start + 2);
});

test('a write to an enumerated route is a local 405 and the upstream sees nothing', async () => {
  const before = reached();
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE'])
    for (const [path] of ALLOWED) assert.equal((await send(method, path)).code, 405, `${method} ${path}`);
  assert.equal(reached(), before, 'no request reached an upstream');
});

test('anything not enumerated is a local 404 and the upstream sees nothing', async () => {
  const before = reached();
  for (const path of [
    '/api/chat/preferences', '/api/actions/x', '/api/chat', '/api/', '/api', '/status.json/extra', '/ssh-hosts/x',
    '/api/chat/sessions/a/b', '/api/chat/sessions/', '/api/chat/sessionsx', '/api/chat/goals', '/api/chat/capabilities/x',
    '/api/chat/sessions/%2e%2e/preferences', '/api/chat/sessions/%2E%2E', '/api/chat/sessions/..', '/api/chat/sessions/.',
    '/api/chat/sessions/..%2fpreferences', '/api/chat/sessions/a%2fb', '/api/chat/sessions/%252e%252e', '/api/chat/sessions/a%5cb',
    '/api/chat/sessions/%00', '//api/chat/preferences', '/API/chat/preferences', '/Status.json',
    '/api/chat/sessions;x/y', '/status.json%00', '/status.json%2fx',
  ]) assert.equal((await send('GET', path)).code, 404, `GET ${path}`);
  for (const path of ['/api/chat/preferences', '/api/chat/sessions/%2e%2e/preferences', '/api/actions/x', '/status.json/x'])
    assert.equal((await send('POST', path)).code, 404, `POST ${path}`);
  assert.equal(reached(), before, 'no request reached an upstream');
});

test('paths outside the service namespace are left to the page server', async () => {
  const before = reached();
  for (const path of ['/', '/index.html', '/src/App.tsx', '/assets/a.js', '/@vite/client', '/manifest.webmanifest'])
    assert.equal((await send('GET', path)).code, 299, path);
  assert.equal(reached(), before);
});

test('an upstream that is down gives 502, not a hang or a crash', async () => {
  const dead = createServer(); const deadPort = await listen(dead); dead.close();
  const forward = createReadOnlyForwarder({ statusBase: `http://127.0.0.1:${deadPort}`, chatBase: `http://127.0.0.1:${deadPort}` });
  const front2 = createServer((req, res) => forward(req, res, () => res.end()));
  const p2 = await listen(front2);
  const code = await new Promise<number>((resolve, reject) => { const r = request({ host: '127.0.0.1', port: p2, path: '/status.json' }, (res) => { res.resume(); resolve(res.statusCode ?? 0); }); r.on('error', reject); r.end(); });
  front2.close();
  assert.equal(code, 502);
});
