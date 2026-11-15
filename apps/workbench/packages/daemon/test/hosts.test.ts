import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { SOCKET_IN_HOME } from '../src/config.js';
import WebSocket from 'ws';
import { Desktop } from '../src/desktop.js';
import { ExtensionHost } from '../src/extension-host.js';
import { loadCatalog } from '../src/extensions.js';
import { AUTOTITLE_ID, AutoTitle } from '../src/autotitle.js';
import { createHttpServer, listenPrivate, socketHeld } from '../src/http.js';
import { HostRegistry, type HostInfo } from '../src/hosts.js';
import { RelayClient } from '../src/relay-client.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { fakeVnc } from './fixtures/fake-vnc.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'hub-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };

/** Everything in one temporary directory: the hub's data, and a home for each pretend machine. */
let root: string;
let hubBase: string;
let hubServer: Server;
let hosts: HostRegistry;
let client: RelayClient;
let joinToken: string;

async function connectNode() {
  client = new RelayClient({ hubUrl: hubBase, token: joinToken, socket: nodeSocket, version: 'node-test', retryMs: 50 });
  client.start();
  await waitFor(() => hosts.list().some(h => h.id === 'devbox' && h.status === 'online'));
}
/** The other machine: a daemon of its own, answering on the socket in its home. */
let nodeHome: string;
let nodeSocket: string;
let nodeServer: Server;
/** The other machine's desktop, and the hub's own (it has none, but signs the tickets). */
let nodeDesktop: Desktop;
let hubDesktop: Desktop;
let vnc: Awaited<ReturnType<typeof fakeVnc>>;
const closers: Array<() => Promise<void> | void> = [];

async function waitFor<T>(fn: () => Promise<T | null | undefined | false> | T | null | undefined | false, timeoutMs = 10_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - start > timeoutMs) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 25));
  }
}

function makeManager(name: string): { manager: ThreadManager; store: Store; workdir: string } {
  const dataDir = join(root, `${name}-data`);
  const workdir = join(root, `${name}-work`);
  mkdirSync(workdir);
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')]));
  const store = new Store(dataDir);
  const manager = new ThreadManager(store, adapters, new Workdirs([workdir], join(dataDir, 'workdirs.json'), join(dataDir, 'scratch')), new AttachmentStore(dataDir));
  closers.push(async () => {
    await manager.shutdown();
    store.close();
  });
  return { manager, store, workdir };
}

let node: ReturnType<typeof makeManager>;
let hub: ReturnType<typeof makeManager>;
/** The other machine's extensions, as its own daemon would start them. */
let nodeExtensions: ExtensionHost;

function startNode(): Promise<boolean> {
  nodeExtensions = new ExtensionHost({ store: node.store.extensions, source: node.manager, catalog: loadCatalog() });
  const autotitle = new AutoTitle(nodeExtensions.builtin(AUTOTITLE_ID)!, node.manager, node.store, nodeExtensions);
  nodeExtensions.register(autotitle);
  nodeExtensions.start();
  nodeExtensions.install(AUTOTITLE_ID, ['events.summary', 'transcript.read', 'threads.rename', 'agents.ask']);
  nodeServer = createHttpServer({ manager: node.manager, desktop: nodeDesktop, extensions: nodeExtensions, autotitle, trusted: true, token: 'never-asked-0123456789', webDir: null, version: 'node-test' });
  return listenPrivate(nodeServer, nodeSocket);
}

const hubApi = (path: string, init: RequestInit = {}) => fetch(hubBase + path, { ...init, headers: { 'Content-Type': 'application/json', ...auth, ...(init.headers ?? {}) } });
const listHosts = async () => (await (await hubApi('/api/hosts')).json()) as HostInfo[];
const hostNamed = async (id: string) => (await listHosts()).find((h) => h.id === id);

before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-hosts-test-'));
  process.env.PI_CODING_AGENT_DIR = join(root, 'pi-agent');
  node = makeManager('node');
  hub = makeManager('hub');

  nodeHome = join(root, 'devbox');
  nodeSocket = join(nodeHome, SOCKET_IN_HOME);
  mkdirSync(nodeHome);
  vnc = await fakeVnc();
  nodeDesktop = new Desktop({ targets: [{ host: '127.0.0.1', port: vnc.port }] });
  hubDesktop = new Desktop({ targets: null });
  assert.equal(await startNode(), true);

  hosts = new HostRegistry({ file: join(root, 'hub-data', 'hosts.json') });
  hubServer = createHttpServer({ manager: hub.manager, hosts, desktop: hubDesktop, token: TOKEN, webDir: null, version: 'hub-test' });
  await new Promise<void>((r) => hubServer.listen(0, '127.0.0.1', () => r()));
  hubBase = `http://127.0.0.1:${(hubServer.address() as AddressInfo).port}`;
});

after(async () => {
  client?.stop();
  nodeExtensions?.dispose();
  nodeDesktop.dispose();
  hubDesktop.dispose();
  await vnc.close();
  hosts.dispose();
  hubServer.close();
  nodeServer.close();
  hubServer.closeAllConnections();
  nodeServer.closeAllConnections();
  for (const close of closers) await close();
  rmSync(root, { recursive: true, force: true });
});

test('legacy registrations are skipped once without rewriting the file; relay registrations survive', (t) => {
  const file = join(root, 'migration.json');
  const original = JSON.stringify({ hosts: [
    { id: 'old', label: 'old', target: 'old', socket: '/old.sock' },
    { id: 'older', target: 'older', socket: '/older.sock' },
    { transport: 'relay', id: 'saved', label: 'Saved', tokenHash: 'hash' },
    { id: 'current', label: 'Current', tokenHash: 'hash2' },
  ] });
  writeFileSync(file, original);
  const warn = t.mock.method(console, 'warn', () => {});
  const loaded = new HostRegistry({ file });
  assert.deepEqual(loaded.list().map(h => h.id), ['saved', 'current']);
  assert.equal(warn.mock.callCount(), 1);
  assert.match(warn.mock.calls[0].arguments[0], /add them again/);
  assert.equal(readFileSync(file, 'utf8'), original);
  loaded.dispose();
});

test('socketHeld tells a live daemon from a leftover socket file, so a second daemon can be turned away before it writes', async () => {
  // A path that does not exist is nobody's.
  assert.equal(await socketHeld(join(root, 'no-such.sock')), false);
  // A plain file a crashed daemon left behind answers nobody.
  const stale = join(root, 'stale.sock');
  writeFileSync(stale, '');
  assert.equal(await socketHeld(stale), false);
  // A live listener is what a running hub marks itself with.
  const live = createServer();
  const livePath = join(root, 'live.sock');
  await new Promise<void>((r) => live.listen(livePath, () => r()));
  assert.equal(await socketHeld(livePath), true);
  await new Promise((r) => live.close(r));
});

test("a daemon's socket is for its user alone, is left to a daemon that holds it, and replaces a leftover", async () => {
  assert.equal(statSync(nodeSocket).mode & 0o077, 0, 'no access for group or others');
  assert.equal(statSync(dirname(nodeSocket)).mode & 0o077, 0);

  // A second daemon started for the same user does not take the socket away.
  const second = createServer();
  assert.equal(await listenPrivate(second, nodeSocket), false);
  assert.equal(second.listening, false);
  assert.equal((await fetchSocket(nodeSocket, '/api/health')).version, 'node-test');

  // A file nobody listens on is what a killed daemon leaves behind.
  const leftover = join(root, 'leftover.sock');
  const dead = createServer();
  await new Promise<void>((r) => dead.listen(leftover, () => r()));
  // Closing a server removes its socket; put a plain file there, as a crash would leave something.
  await new Promise((r) => dead.close(r));
  writeFileSync(leftover, '');
  const fresh = createServer((_req, res) => res.end('{"version":"fresh"}'));
  assert.equal(await listenPrivate(fresh, leftover), true);
  assert.equal((await fetchSocket(leftover, '/api/health')).version, 'fresh');
  await new Promise((r) => fresh.close(r));
  assert.equal(existsSync(leftover), false, 'gone when the daemon stops');
});

/** A GET on a unix socket, with no token: what the node's relay client uses. */
async function fetchSocket(socketPath: string, path: string): Promise<any> {
  const { request } = await import('node:http');
  return new Promise((resolve, reject) => {
    const req = request({ socketPath, path, agent: false }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ status: res.statusCode, ...JSON.parse(body || '{}') }));
    });
    req.on('error', reject);
    req.end();
  });
}

test('the socket needs no token, and shows that machine only', async () => {
  const threads = await fetchSocket(nodeSocket, '/api/threads');
  assert.equal(threads.status, 200);
  // Even a daemon that has machines of its own does not offer them to a hub that reached it this way.
  const chained = createHttpServer({ manager: node.manager, hosts, trusted: true, token: TOKEN, webDir: null, version: 'x' });
  const path = join(root, 'chained.sock');
  assert.equal(await listenPrivate(chained, path), true);
  assert.equal((await fetchSocket(path, '/api/hosts')).status, 404);
  await new Promise((r) => chained.close(r));
});

test('machine registration is authenticated and requires a label', async () => {
  assert.equal((await fetch(hubBase + '/api/hosts')).status, 401);
  assert.equal((await hubApi('/api/hosts/candidates')).status, 404);
  for (const label of ['', '   ', 42, null]) {
    assert.equal((await hubApi('/api/hosts', { method: 'POST', body: JSON.stringify({ label }) })).status, 400);
  }
  assert.deepEqual(await listHosts(), []);
});

test('adding a machine returns join settings and the node connects to the hub', async () => {
  const res = await hubApi('/api/hosts', { method: 'POST', body: JSON.stringify({ label: 'devbox' }) });
  assert.equal(res.status, 201);
  const added = await res.json() as HostInfo & { token: string };
  assert.equal(added.status, 'offline');
  assert.equal(added.code, 'not_joined');
  joinToken = added.token;
  await connectNode();
  const file = join(root, 'hub-data', 'hosts.json');
  const saved = JSON.parse(readFileSync(file, 'utf8'));
  assert.deepEqual(Object.keys(saved.hosts[0]).sort(), ['id', 'label', 'tokenHash']);
  assert.equal(saved.hosts[0].id, 'devbox');
  assert.ok(!readFileSync(file, 'utf8').includes(joinToken));
  assert.equal(statSync(file).mode & 0o077, 0);

  // The same API, answered over there: its directories, its threads.
  const config = (await (await hubApi('/api/hosts/devbox/proxy/config')).json()) as { workdirs: string[] };
  assert.deepEqual(config.workdirs, [node.workdir]);
  const created = await hubApi('/api/hosts/devbox/proxy/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: node.workdir, title: '在开发机上' }) });
  assert.equal(created.status, 201);
  const thread = (await created.json()) as { id: string; cwd: string };
  assert.equal(thread.cwd, node.workdir);
  const outputs = await hubApi(`/api/hosts/devbox/proxy/threads/${thread.id}/outputs`);
  assert.equal(outputs.status, 200);
  assert.deepEqual((await outputs.json() as { changes: unknown }).changes, { files: [], added: 0, removed: 0, total: 0 });
  assert.equal((await hubApi('/api/hosts/devbox/proxy/threads/nope/outputs')).status, 404);
  const there = (await (await hubApi('/api/hosts/devbox/proxy/threads?archived=0')).json()) as Array<{ id: string }>;
  assert.deepEqual(there.map((t) => t.id), [thread.id]);
  assert.deepEqual(await (await hubApi('/api/threads')).json(), [], "not among the hub's own");
  // Its errors come back as they are.
  assert.equal((await hubApi('/api/hosts/devbox/proxy/threads/nope')).status, 404);

  assert.equal((await fetch(`${hubBase}/api/hosts/devbox/proxy/threads`)).status, 401, "the hub's token still guards it");
  assert.equal((await hubApi('/api/hosts/devbox/proxy/hosts')).status, 400, 'one level only');
  assert.equal((await hubApi('/api/hosts/other/proxy/threads')).status, 404);
  // The hub's daemon asks the machine on its own behalf too, for members it runs there.
  assert.deepEqual((await hosts.call<Array<{ id: string }>>('devbox', 'GET', '/api/threads')).map((t) => t.id), [thread.id]);
  const made = await hosts.call<{ cwd: string }>('devbox', 'POST', '/api/threads', { agent: 'pi', cwd: node.workdir, title: 'member' });
  assert.equal(made.cwd, node.workdir);
  await assert.rejects(hosts.call('devbox', 'GET', '/api/threads/nope'), (err: { status?: number }) => err.status === 404);
  await assert.rejects(hosts.call('other', 'GET', '/api/threads'), (err: { status?: number }) => err.status === 404);
});

test('a command runs on the machine over relay, with its input on stdin and its exit code kept', async () => {
  const echo = await hosts.exec('devbox', 'cat', { input: 'hello\n' });
  assert.equal(echo.code, 0);
  assert.equal(echo.stdout.toString('utf8'), 'hello\n');
  assert.equal(echo.stderr, '');
  const failed = await hosts.exec('devbox', 'echo oops >&2; exit 7');
  assert.equal(failed.code, 7);
  assert.match(failed.stderr, /oops/);
  await assert.rejects(hosts.exec('nope', 'true'), (err: { status?: number }) => err.status === 404);
  await assert.rejects(hosts.exec('devbox', 'head -c 4096 /dev/zero', { maxBytes: 1024 }), (err: { status?: number }) => err.status === 413);
});

test('the other machine\'s extension settings answer through the hub too', async () => {
  // The socket server is the road a hub takes: a daemon-side service missing there breaks only
  // requests from other machines, which is exactly what this must catch (autotitle once was).
  const settings = await hubApi('/api/hosts/devbox/proxy/extensions/autotitle/settings');
  assert.equal(settings.status, 200);
  const body = (await settings.json()) as { settings: { agent: string | null } };
  assert.equal(body.settings.agent, null, 'the machine\'s own settings, not the hub\'s');
});

test('a turn on the other machine streams back through the hub as it happens', async () => {
  const [thread] = (await (await hubApi('/api/hosts/devbox/proxy/threads')).json()) as Array<{ id: string }>;
  const ctrl = new AbortController();
  const stream = await hubApi(`/api/hosts/devbox/proxy/threads/${thread.id}/stream?after=0`, { signal: ctrl.signal });
  assert.equal(stream.status, 200);
  assert.match(stream.headers.get('content-type') ?? '', /text\/event-stream/);
  const reader = stream.body!.getReader();
  let seen = '';
  const until = async (mark: string) => {
    while (!seen.includes(mark)) {
      const { value, done } = await reader.read();
      if (done) throw new Error(`stream ended before ${mark}`);
      seen += new TextDecoder().decode(value);
    }
  };
  // Open before anything is said: what follows can only have arrived live.
  await until(': connected');
  const turn = await hubApi(`/api/hosts/devbox/proxy/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello over there' }) });
  assert.equal(turn.status, 202);
  await until('event: turn.started');
  await until('event: turn.done');
  assert.match(seen, /hello over there/);
  ctrl.abort();
});

test("the other machine's desktop opens through the hub, on a ticket the hub signs", async () => {
  assert.deepEqual(await (await hubApi('/api/hosts/devbox/proxy/desktop')).json(), { available: true, port: vnc.port });
  assert.deepEqual(await (await hubApi('/api/desktop')).json(), { available: false }, "the hub's own has none");
  const { url } = (await (await hubApi('/api/desktop/ticket?host=devbox')).json()) as { url: string };
  assert.match(url, /^\/api\/hosts\/devbox\/proxy\/desktop\/vnc\?exp=/);
  const wsBase = hubBase.replace('http', 'ws');
  const open = (path: string) =>
    new Promise<WebSocket | number>((resolve) => {
      const ws = new WebSocket(wsBase + path);
      ws.on('open', () => resolve(ws));
      ws.on('unexpected-response', (_req, res) => resolve(res.statusCode ?? 0));
    });
  const ws = await open(url);
  assert.ok(ws instanceof WebSocket);
  const message = () => new Promise<string>((r) => ws.once('message', (d: Buffer) => r(d.toString('latin1'))));
  assert.equal(await message(), 'RFB 003.008\n');
  const echoed = message();
  ws.send(Buffer.from('over there'));
  assert.equal(await echoed, 'over there');
  ws.close();
  assert.equal(await open('/api/hosts/devbox/proxy/desktop/vnc'), 403, "the hub's ticket is needed");
});

test('maintenance exec requires the hub token and preserves command input and exit status', async () => {
  const path = '/api/hosts/devbox/exec';
  assert.equal((await fetch(hubBase + path, { method: 'POST', body: '{}' })).status, 401);
  assert.equal((await fetch(hubBase + path, { method: 'POST', headers: { Authorization: `Bearer ${joinToken}` }, body: '{}' })).status, 401);
  for (const body of [null, {}, { command: 4 }, { command: 'cat', input: 5 }]) {
    assert.equal((await hubApi(path, { method: 'POST', body: JSON.stringify(body) })).status, 400);
  }
  const response = await hubApi(path, { method: 'POST', body: JSON.stringify({ command: 'cat; echo problem >&2; exit 7', input: 'hello\n' }) });
  assert.equal(response.status, 200);
  const result = await response.json() as { stdout: string; stderr: string; code: number };
  assert.equal(Buffer.from(result.stdout, 'base64').toString(), 'hello\n');
  assert.match(result.stderr, /problem/);
  assert.equal(result.code, 7);
  assert.equal((await hubApi('/api/hosts/missing/exec', { method: 'POST', body: JSON.stringify({ command: 'true' }) })).status, 404);
});

test('a dropped relay connection is reopened by the node', async () => {
  const connections = client.connections;
  hosts.dispose();
  await waitFor(() => client.connections > connections);
  await waitFor(async () => (await hostNamed('devbox'))?.status === 'online');
  assert.equal((await hubApi('/api/hosts/devbox/proxy/threads')).status, 200);
});

test('a stopped node is offline until a new client connects', async () => {
  client.stop();
  const down = await waitFor(async () => {
    const h = await hostNamed('devbox');
    return h?.status === 'offline' ? h : null;
  });
  assert.equal(down.code, 'relay_lost');
  const refused = await hubApi('/api/hosts/devbox/proxy/threads');
  assert.equal(refused.status, 502);
  assert.equal(((await refused.json()) as { code: string }).code, 'host_offline');
  await assert.rejects(hosts.exec('devbox', 'true'), (err: { status?: number }) => err.status === 502);
  await connectNode();
  const refreshed = await (await hubApi('/api/hosts/devbox/reconnect', { method: 'POST' })).json() as HostInfo;
  assert.equal(refreshed.status, 'online');
  assert.equal((await hubApi('/api/hosts/nope/reconnect', { method: 'POST' })).status, 404);
});

test('saved machines accept their original token after hub restart; removal revokes it', async () => {
  client.stop();
  hosts.dispose();
  hubServer.closeAllConnections();
  await new Promise<void>(r => hubServer.close(() => r()));
  hosts = new HostRegistry({ file: join(root, 'hub-data', 'hosts.json') });
  assert.equal(hosts.list()[0].label, 'devbox');
  hubServer = createHttpServer({ manager: hub.manager, hosts, desktop: hubDesktop, token: TOKEN, webDir: null, version: 'hub-test' });
  await new Promise<void>(r => hubServer.listen(0, '127.0.0.1', r));
  hubBase = `http://127.0.0.1:${(hubServer.address() as AddressInfo).port}`;
  await connectNode();
  assert.equal((await hubApi('/api/hosts/devbox/proxy/threads')).status, 200);
  assert.equal((await hubApi('/api/hosts/devbox', { method: 'DELETE' })).status, 204);
  assert.deepEqual(await listHosts(), []);
  assert.deepEqual(JSON.parse(readFileSync(join(root, 'hub-data', 'hosts.json'), 'utf8')), { hosts: [] });
  assert.equal((await hubApi('/api/hosts/devbox/proxy/threads')).status, 404);
  assert.equal((await hubApi('/api/hosts/devbox', { method: 'DELETE' })).status, 404);
  await waitFor(() => client.status !== 'online');
  assert.equal((await fetch(`${hubBase}/api/relay/poll`, { method: 'POST', headers: { Authorization: `Bearer ${joinToken}` } })).status, 401);
});
