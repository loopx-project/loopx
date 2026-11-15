import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer, request, type Server } from 'node:http';
import { connect, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHttpServer } from '../src/http.js';
import { HostRegistry, type HostInfo } from '../src/hosts.js';
import { RelayClient } from '../src/relay-client.js';
import type { ThreadManager } from '../src/threads.js';

const TOKEN = 'hub-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };

let root: string;
let hosts: HostRegistry;
let hubServer: Server;
let hubBase: string;
let wsServer: Server;
/** The other machine's daemon: a plain server on its private socket, enough to answer what is relayed. */
let nodeServer: Server;
let nodeSocket: string;
let client: RelayClient | null = null;
/** Set when the node saw its event stream closed by the far end. */
let streamClosed = false;

async function waitFor<T>(fn: () => Promise<T | null | undefined | false> | T | null | undefined | false, timeoutMs = 10_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - start > timeoutMs) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 25));
  }
}

const listHosts = async (): Promise<HostInfo[]> => (await fetch(`${hubBase}/api/hosts`, { headers: auth })).json() as Promise<HostInfo[]>;

before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-relay-'));
  nodeSocket = join(root, 'node.sock');
  nodeServer = createServer((req, res) => {
    if (req.url === '/api/health') return void res.end(JSON.stringify({ ok: true, version: 'node-test' }));
    if (req.url === '/api/agents') return void res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify([{ id: 'pi', seen: req.headers.authorization ?? null }]));
    if (req.url === '/api/echo' && req.method === 'POST') {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => res.writeHead(201, { 'Content-Type': 'application/octet-stream', 'x-node': 'yes' }).end(Buffer.concat(chunks)));
      return;
    }
    if (req.url === '/api/stream') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write('data: first\n\n');
      res.on('close', () => (streamClosed = true));
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: 'not here' }));
  });
  // The desktop's WebSocket stands in as an echo of raw bytes after a 101.
  nodeServer.on('upgrade', (req, socket) => {
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: echo\r\nConnection: Upgrade\r\nx-path: ${req.url}\r\n\r\n`);
    socket.pipe(socket);
  });
  await new Promise<void>((r) => nodeServer.listen(nodeSocket, r));

  hosts = new HostRegistry({ file: join(root, 'hosts.json') });
  // The hub's own threads are not what this is about; the routes under test never reach them.
  hubServer = createHttpServer({ manager: {} as ThreadManager, hosts, token: TOKEN, webDir: null, version: 'hub-test' });
  await new Promise<void>((r) => hubServer.listen(0, '127.0.0.1', r));
  // A WebSocket route that passes on to a machine, as the hub's desktop route does.
  wsServer = createServer();
  wsServer.on('upgrade', (req, socket, head) => {
    const m = /^\/test-ws\/([^/]+)$/.exec(req.url ?? '');
    if (!m || !hosts.upgrade(m[1], '/api/desktop/vnc', req, socket, head)) socket.destroy();
  });
  await new Promise<void>((r) => wsServer.listen(0, '127.0.0.1', r));
  hubBase = `http://127.0.0.1:${(hubServer.address() as AddressInfo).port}`;
});

after(async () => {
  client?.stop();
  hosts.dispose();
  hubServer.closeAllConnections();
  nodeServer.closeAllConnections();
  wsServer.closeAllConnections();
  await new Promise((r) => wsServer.close(r));
  await new Promise((r) => hubServer.close(r));
  await new Promise((r) => nodeServer.close(r));
  rmSync(root, { recursive: true, force: true });
});

let machine: HostInfo & { token: string };

test('adding a relay machine returns a join token once and keeps only its hash', async () => {
  const res = await fetch(`${hubBase}/api/hosts`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/json' }, body: JSON.stringify({ label: 'Build box' }) });
  assert.equal(res.status, 201);
  machine = (await res.json()) as HostInfo & { token: string };
  assert.equal(machine.id, 'build-box');
  assert.equal(machine.status, 'offline');
  assert.equal(machine.code, 'not_joined');
  assert.match(machine.token, /^build-box\.[A-Za-z0-9_-]{40,}$/);
  const saved = readFileSync(join(root, 'hosts.json'), 'utf8');
  assert.ok(!saved.includes(machine.token.split('.')[1]), 'the secret is not written down');
  assert.ok((await listHosts()).every((h) => !('token' in h)));
});

test('a machine that is not connected is refused at once, not after a timeout', async () => {
  const res = await fetch(`${hubBase}/api/hosts/build-box/proxy/agents`, { headers: auth });
  assert.equal(res.status, 502);
  assert.equal(((await res.json()) as { code: string }).code, 'host_offline');
});

test('a wrong join token cannot connect or claim work, and a join token is not the hub token', async () => {
  const bad = new RelayClient({ hubUrl: hubBase, token: 'build-box.wrong', socket: nodeSocket, version: 'x', retryMs: 60_000 });
  bad.start();
  await waitFor(() => bad.error);
  assert.match(bad.error!, /does not know/);
  bad.stop();
  assert.equal((await fetch(`${hubBase}/api/relay/poll`, { method: 'POST', headers: { Authorization: 'Bearer build-box.wrong' } })).status, 401);
  assert.equal((await fetch(`${hubBase}/api/hosts`, { headers: { Authorization: `Bearer ${machine.token}` } })).status, 401);
});

test('the machine connects out and comes online with its version and name', async () => {
  client = new RelayClient({ hubUrl: hubBase, token: machine.token, socket: nodeSocket, version: 'node-test', retryMs: 50 });
  client.start();
  const online = await waitFor(async () => (await listHosts()).find((h) => h.id === 'build-box' && h.status === 'online'));
  assert.equal(online.version, 'node-test');
  assert.ok(online.target);
});

test('requests pass through without the hub token, bodies and headers both ways', async () => {
  const agents = await fetch(`${hubBase}/api/hosts/build-box/proxy/agents`, { headers: auth });
  assert.equal(agents.status, 200);
  assert.deepEqual(await agents.json(), [{ id: 'pi', seen: null }]);

  const big = Buffer.alloc(3 * 1024 * 1024, 7);
  const echo = await fetch(`${hubBase}/api/hosts/build-box/proxy/echo`, { method: 'POST', headers: auth, body: big });
  assert.equal(echo.status, 201);
  assert.equal(echo.headers.get('x-node'), 'yes');
  assert.ok(Buffer.from(await echo.arrayBuffer()).equals(big));

  const missing = await fetch(`${hubBase}/api/hosts/build-box/proxy/nothing`, { headers: auth });
  assert.equal(missing.status, 404);
});

test('an event stream arrives as it is written, and closing it closes it on the machine', async () => {
  streamClosed = false;
  const controller = new AbortController();
  const res = await fetch(`${hubBase}/api/hosts/build-box/proxy/stream`, { headers: auth, signal: controller.signal });
  assert.equal(res.headers.get('content-type'), 'text/event-stream');
  const reader = res.body!.getReader();
  const { value } = await reader.read();
  assert.equal(Buffer.from(value!).toString(), 'data: first\n\n');
  controller.abort();
  await waitFor(() => streamClosed);
});

test('call and exec reach the machine for collaboration', async () => {
  assert.deepEqual(await hosts.call('build-box', 'GET', '/api/agents'), [{ id: 'pi', seen: null }]);
  await assert.rejects(hosts.call('build-box', 'GET', '/api/nothing'), /not here/);
  const run = await hosts.exec('build-box', 'read -r line; printf "got %s" "$line"; echo oops >&2; exit 3', { input: 'hello\n' });
  assert.equal(run.stdout.toString(), 'got hello');
  assert.equal(run.stderr.trim(), 'oops');
  assert.equal(run.code, 3);
  await assert.rejects(hosts.exec('build-box', 'head -c 5000 /dev/zero', { maxBytes: 1000 }), /more than/);
});

test('a WebSocket upgrade is tunnelled byte for byte', async () => {
  const port = (wsServer.address() as AddressInfo).port;
  const socket = connect(port, '127.0.0.1');
  socket.write('GET /test-ws/build-box HTTP/1.1\r\nHost: x\r\nConnection: Upgrade\r\nUpgrade: echo\r\n\r\n');
  let got = '';
  socket.on('data', (c: Buffer) => {
    got += c.toString();
    if (got.includes('\r\n\r\n') && !got.includes('ping')) socket.write('ping');
  });
  await waitFor(() => got.includes('ping'));
  assert.match(got, /^HTTP\/1.1 101/);
  assert.match(got, /x-path: \/api\/desktop\/vnc/);
  socket.destroy();
});

test('the machine reconnects after the hub drops it, and removing it shuts it out', async () => {
  // Cut the machine's connection from the hub's side, as the hub shutting down does.
  const before = client!.connections;
  hosts.dispose();
  await waitFor(() => client!.connections > before);
  await waitFor(async () => (await listHosts()).find((h) => h.id === 'build-box' && h.status === 'online'));
  const res = await new Promise<number>((resolve) => {
    request(`${hubBase}/api/hosts/build-box/proxy/agents`, { headers: auth }, (r) => (r.resume(), resolve(r.statusCode ?? 0))).end();
  });
  assert.equal(res, 200);

  assert.equal((await fetch(`${hubBase}/api/hosts/build-box`, { method: 'DELETE', headers: auth })).status, 204);
  assert.equal((await fetch(`${hubBase}/api/relay/poll`, { method: 'POST', headers: { Authorization: `Bearer ${machine.token}` } })).status, 401);
  await waitFor(() => client!.status !== 'online');
});
