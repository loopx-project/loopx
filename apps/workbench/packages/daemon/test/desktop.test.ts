import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import WebSocket from 'ws';
import { AttachmentStore } from '../src/attachments.js';
import { writeFileSync } from 'node:fs';
import { Desktop, listeningPorts, parseDesktopSetting } from '../src/desktop.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { fakeVnc } from './fixtures/fake-vnc.js';

const TOKEN = 'desktop-token-0123456789';
const auth = { Authorization: `Bearer ${TOKEN}` };

let dataDir: string;
let store: Store;
let manager: ThreadManager;
let vnc: Awaited<ReturnType<typeof fakeVnc>>;
let clock = Date.now();
let desktop: Desktop;
let server: Server;
let base: string;

before(async () => {
  dataDir = mkdtempSync(join(tmpdir(), 'calli-desktop-'));
  store = new Store(dataDir);
  manager = new ThreadManager(store, new Map(), [dataDir], new AttachmentStore(dataDir));
  vnc = await fakeVnc();
  desktop = new Desktop({ targets: [{ host: '127.0.0.1', port: vnc.port }], now: () => clock });
  server = createHttpServer({ manager, desktop, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(async () => {
  desktop.dispose();
  server.close();
  server.closeAllConnections();
  await vnc.close();
  await manager.shutdown();
  store.close();
  rmSync(dataDir, { recursive: true, force: true });
});

const ticket = async () => ((await (await fetch(`${base}/api/desktop/ticket`, { headers: auth })).json()) as { url: string }).url;

/** Opens the WebSocket; resolves with it once open, or with the HTTP status it was refused with. */
function open(path: string): Promise<WebSocket | number> {
  return new Promise((resolve) => {
    const ws = new WebSocket(`ws://127.0.0.1:${new URL(base).port}${path}`);
    ws.on('open', () => resolve(ws));
    ws.on('unexpected-response', (_req, res) => resolve(res.statusCode ?? 0));
    ws.on('error', () => resolve(0));
  });
}

function nextMessage(ws: WebSocket): Promise<string> {
  return new Promise((resolve) => ws.once('message', (data: Buffer) => resolve(data.toString('latin1'))));
}

test('CALLI_DESKTOP_VNC: unset looks at displays :0 to :9 here, off turns it off, host:port names one', () => {
  const unset = parseDesktopSetting(undefined)!;
  assert.equal(unset.length, 10);
  assert.deepEqual(unset[1], { host: '127.0.0.1', port: 5901 });
  assert.equal(parseDesktopSetting('off'), null);
  assert.deepEqual(parseDesktopSetting('localhost:5905'), [{ host: 'localhost', port: 5905 }]);
  assert.deepEqual(parseDesktopSetting('[::1]:5901'), [{ host: '::1', port: 5901 }]);
  assert.throws(() => parseDesktopSetting('5901'), /host:port or off/);
  assert.throws(() => parseDesktopSetting('h:70000'), /host:port or off/);
});

test('a desktop is found from the ports listening here, without connecting to it', async () => {
  const ports = listeningPorts()!;
  assert.ok(ports.has(vnc.port), 'the fake server is seen listening');
  let connections = 0;
  vnc.server.on('connection', () => connections++);
  const seen = new Desktop({ targets: [{ host: '127.0.0.1', port: vnc.port + 1 }, { host: '127.0.0.1', port: vnc.port }] });
  assert.deepEqual(await seen.find(), { host: '127.0.0.1', port: vnc.port });
  assert.equal(connections, 0, 'looking never connects: TigerVNC would count it as a failed sign-in');
  seen.dispose();

  // /proc as Linux writes it: a header, then rows; 0A is LISTEN and the port is hex.
  const file = join(dataDir, 'tcp');
  writeFileSync(file, '  sl  local_address rem_address   st\n   0: 0100007F:170D 00000000:0000 0A\n   1: 0100007F:170E 0100007F:9C40 01\n');
  assert.deepEqual([...listeningPorts([file])!], [5901]);
  assert.equal(listeningPorts([join(dataDir, 'nope')]), null);

  // Without /proc: a server named outright is taken as given, the ports guessed at are not.
  const named = new Desktop({ targets: [{ host: '127.0.0.1', port: 5901 }], listening: () => null });
  assert.deepEqual(await named.find(), { host: '127.0.0.1', port: 5901 });
  const guessed = new Desktop({ targets: parseDesktopSetting(undefined), listening: () => null });
  assert.equal(await guessed.find(), null);
  // Another host is never looked at here.
  const remote = new Desktop({ targets: [{ host: 'desk.example', port: 5901 }], listening: () => new Set() });
  assert.deepEqual(await remote.find(), { host: 'desk.example', port: 5901 });
  for (const d of [named, guessed, remote]) d.dispose();
});

test('the page learns whether there is a desktop, behind the token', async () => {
  assert.equal((await fetch(`${base}/api/desktop`)).status, 401);
  assert.deepEqual(await (await fetch(`${base}/api/desktop`, { headers: auth })).json(), { available: true, port: vnc.port });
  assert.equal((await fetch(`${base}/api/desktop/ticket`)).status, 401);
  assert.equal((await fetch(`${base}/api/desktop/ticket?host=nope`, { headers: auth })).status, 404);
});

test('a ticket opens one WebSocket, joined byte for byte to the VNC server', async () => {
  const url = await ticket();
  assert.match(url, /^\/api\/desktop\/vnc\?exp=\d+&n=[\w-]+&sig=/);
  assert.notEqual(await ticket(), url, 'tickets asked for in the same second differ');
  const ws = await open(url);
  assert.ok(ws instanceof WebSocket);
  assert.equal(await nextMessage(ws), 'RFB 003.008\n');
  const echoed = nextMessage(ws);
  ws.send(Buffer.from([0, 1, 2, 255]));
  assert.deepEqual([...Buffer.from(await echoed, 'latin1')], [0, 1, 2, 255]);
  ws.close();
  assert.equal(await open(url), 403, 'a ticket is spent once used');
});

test("a server's version line reaches the page in a message of its own, even with more behind it", async () => {
  // TigerVNC refusing an address sends the version and the reason at once, then closes.
  const refusing = await fakeVnc('RFB 003.003\n\0\0\0\0\0\0\0\x03no!');
  const d = new Desktop({ targets: [{ host: '127.0.0.1', port: refusing.port }] });
  const s = createHttpServer({ manager, desktop: d, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => s.listen(0, '127.0.0.1', () => r()));
  const b = `http://127.0.0.1:${(s.address() as AddressInfo).port}`;
  const { url } = (await (await fetch(`${b}/api/desktop/ticket`, { headers: auth })).json()) as { url: string };
  const ws = new WebSocket(b.replace('http', 'ws') + url);
  const messages: string[] = [];
  await new Promise<void>((resolve) => {
    ws.on('message', (m: Buffer) => {
      messages.push(m.toString('latin1'));
      if (messages.length === 2) resolve();
    });
  });
  assert.deepEqual(messages, ['RFB 003.003\n', '\0\0\0\0\0\0\0\x03no!']);
  ws.close();
  s.close();
  s.closeAllConnections();
  d.dispose();
  await refusing.close();
});

test('no ticket, a forged one, an expired one, or another path is turned away', async () => {
  assert.equal(await open('/api/desktop/vnc'), 403);
  const url = await ticket();
  assert.equal(await open(url.replace(/sig=[^&]+/, 'sig=forged')), 403);
  assert.equal(await open(url.replace(/n=[^&]+/, 'n=other')), 403);
  assert.equal(await open(url.replace('/api/desktop/vnc', '/api/hosts/x/proxy/desktop/vnc')), 403, 'signed for its own path only');
  clock += 61_000;
  assert.equal(await open(url), 403);
  assert.equal(await open('/api/terminals'), 404);
});

test('without a VNC server, or with the desktop off, there is no desktop', async () => {
  const off = new Desktop({ targets: null });
  assert.equal(await off.find(), null);
  const gone = await fakeVnc();
  await gone.close();
  const none = new Desktop({ targets: [{ host: '127.0.0.1', port: gone.port }] });
  const noneServer = createHttpServer({ manager, desktop: none, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => noneServer.listen(0, '127.0.0.1', () => r()));
  const noneBase = `http://127.0.0.1:${(noneServer.address() as AddressInfo).port}`;
  assert.deepEqual(await (await fetch(`${noneBase}/api/desktop`, { headers: auth })).json(), { available: false });
  const { url } = (await (await fetch(`${noneBase}/api/desktop/ticket`, { headers: auth })).json()) as { url: string };
  const status = await new Promise<number>((resolve) => {
    const ws = new WebSocket(`ws://127.0.0.1:${new URL(noneBase).port}${url}`);
    ws.on('unexpected-response', (_req, res) => resolve(res.statusCode ?? 0));
    ws.on('open', () => resolve(101));
  });
  assert.equal(status, 404);
  noneServer.close();
  none.dispose();
  off.dispose();
});
