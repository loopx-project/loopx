import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { hostname, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AttachmentStore } from '../src/attachments.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { BorrowingService } from '../src/borrowing.js';
import { borrowingServer } from '../src/borrowing-server.js';
import { listenPrivate } from '../src/http.js';
import { agentBrowserExecutable, displayForDesktop, PersonalBrowser } from '../src/personal-browser.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';

const here = dirname(fileURLToPath(import.meta.url));
const fakeChrome = join(here, 'fixtures/fake-chrome.mjs');
const fakeAgentBrowser = join(here, 'fixtures/fake-agent-browser.mjs');
const cli = join(here, '../bin/calli');

type LogEntry = { pid?: number; display?: string; args?: string[]; hubSecrets?: string[]; opened?: string; exited?: boolean };

function harness(extra: string[] = []) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-browser-'));
  const profileDir = join(dir, 'profile');
  let display: string | undefined = ':7';
  const make = () => new PersonalBrowser({ profileDir, command: [process.execPath, fakeChrome, ...extra], display: async () => display, startTimeoutMs: 5000 });
  const log = (): LogEntry[] => existsSync(join(dir, 'fake-chrome.log'))
    ? readFileSync(join(dir, 'fake-chrome.log'), 'utf8').trim().split('\n').map(l => JSON.parse(l)) : [];
  const launches = () => log().filter(e => e.pid);
  const kill = () => { for (const e of launches()) { try { process.kill(e.pid!); } catch { /* already gone */ } } };
  return { dir, profileDir, make, log, launches, kill, setDisplay: (d: string | undefined) => { display = d; },
    close() { kill(); rmSync(dir, { recursive: true, force: true }); } };
}

test('starts one Chrome on the desktop with a private profile, and later callers reuse it', async () => {
  const h = harness();
  try {
    const browser = h.make();
    const saved = { CALLI_TOKEN: process.env.CALLI_TOKEN, CALLI_TOOLS_KEY: process.env.CALLI_TOOLS_KEY };
    process.env.CALLI_TOKEN = 'hub-token'; process.env.CALLI_TOOLS_KEY = 'thread-key';
    const [a, b] = await Promise.all([browser.ensure(), browser.ensure()]).finally(() => {
      for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
    });
    assert.equal(a, b);
    assert.equal(h.launches().length, 1, 'concurrent callers share one start');
    const launch = h.launches()[0];
    assert.equal(launch.display, ':7');
    assert.deepEqual(launch.hubSecrets, [], 'the hub token and tool credentials stay out of Chrome');
    assert.ok(launch.args!.includes(`--user-data-dir=${h.profileDir}`));
    assert.ok(launch.args!.includes('--remote-debugging-port=0'), 'Chrome picks a free loopback port');
    assert.equal(statSync(h.profileDir).mode & 0o777, 0o700, 'the profile holds sign-ins');

    // A restarted daemon finds the same Chrome through the profile instead of starting another.
    assert.equal(await h.make().ensure(), a);
    assert.equal(h.launches().length, 1);
    const status = await h.make().status();
    assert.equal(status.running, true);
    assert.equal(status.cdp, `http://127.0.0.1:${a}`);
  } finally { h.close(); }
});

test('a port file left by a Chrome that is gone starts a new one', async () => {
  const h = harness();
  try {
    const browser = h.make();
    const first = await browser.ensure();
    h.kill();
    await new Promise(r => setTimeout(r, 200));
    assert.ok(existsSync(join(h.profileDir, 'DevToolsActivePort')));
    const second = await browser.ensure();
    assert.notEqual(second, first);
    assert.equal(h.launches().length, 2);
  } finally { h.close(); }
});

test('opens web pages in a new tab and nothing else', async () => {
  const h = harness();
  try {
    const browser = h.make();
    const opened = await browser.open('https://example.com/verify?a=1&b=2#x');
    assert.equal(opened.url, 'https://example.com/verify?a=1&b=2#x');
    assert.deepEqual(h.log().filter(e => e.opened).map(e => e.opened), ['https://example.com/verify?a=1&b=2#x']);
    for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'chrome://settings', 'not a url', 42]) {
      await assert.rejects(browser.open(url), /http\(s\)|url is required/);
    }
  } finally { h.close(); }
});

test('without a desktop it says why and starts nothing', async () => {
  const h = harness();
  try {
    h.setDisplay(undefined);
    const browser = h.make();
    const status = await browser.status();
    assert.equal(status.available, false);
    assert.match(status.reason!, /no desktop/);
    await assert.rejects(browser.ensure(), /no desktop/);
    assert.equal(h.launches().length, 0);
  } finally { h.close(); }
});

test('a Chrome that exits without a debugging port is reported, not waited on', async () => {
  const h = harness(['--fake-exit']);
  try {
    const started = Date.now();
    await assert.rejects(h.make().ensure(), /exited without opening its debugging port/);
    assert.ok(Date.now() - started < 4500, 'gives up soon after Chrome exits');
  } finally { h.close(); }
});

test('a profile held by a Chrome Calli cannot reach is reported, and no second Chrome is started', async () => {
  const h = harness();
  try {
    mkdirSync(h.profileDir, { recursive: true });
    // As Chrome leaves it while running: host and pid of the holder, here this live test process.
    symlinkSync(`${hostname()}-${process.pid}`, join(h.profileDir, 'SingletonLock'));
    await assert.rejects(h.make().ensure(), /did not start and cannot reach/);
    assert.equal(h.launches().length, 0);
    // A lock left by a Chrome that is gone does not block a start.
    rmSync(join(h.profileDir, 'SingletonLock'));
    symlinkSync(`${hostname()}-999999999`, join(h.profileDir, 'SingletonLock'));
    await h.make().ensure();
    assert.equal(h.launches().length, 1);
  } finally { h.close(); }
});

test('the desktop display follows the VNC port, only for a local server with an X socket', () => {
  assert.equal(displayForDesktop(null), undefined);
  assert.equal(displayForDesktop({ host: '10.0.0.2', port: 5901 }), undefined);
  const n = [...Array(100).keys()].find(i => existsSync(`/tmp/.X11-unix/X${i}`));
  if (n !== undefined) assert.equal(displayForDesktop({ host: '127.0.0.1', port: 5900 + n }), `:${n}`);
  const free = [...Array(100).keys()].find(i => !existsSync(`/tmp/.X11-unix/X${i}`))!;
  assert.equal(displayForDesktop({ host: '127.0.0.1', port: 5900 + free }), undefined);
});

test('agent-browser is found from CALLI_AGENT_BROWSER or PATH, and only when it can run', () => {
  assert.equal(agentBrowserExecutable({ CALLI_AGENT_BROWSER: fakeAgentBrowser }), fakeAgentBrowser);
  assert.equal(agentBrowserExecutable({ CALLI_AGENT_BROWSER: '/nonexistent/agent-browser' }), null);
  const bin = mkdtempSync(join(tmpdir(), 'calli-ab-path-'));
  try {
    assert.equal(agentBrowserExecutable({ PATH: bin }), null);
    writeFileSync(join(bin, 'agent-browser'), '#!/bin/sh\n', { mode: 0o755 });
    assert.equal(agentBrowserExecutable({ PATH: `/nonexistent:${bin}` }), join(bin, 'agent-browser'));
  } finally { rmSync(bin, { recursive: true, force: true }); }
});

async function service(h: ReturnType<typeof harness>) {
  const cwd = join(h.dir, 'project'); mkdirSync(cwd);
  const adapters = new Map<string, AgentAdapter>([['claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures/fake-acp.mjs')])]]);
  const store = new Store(h.dir);
  const manager = new ThreadManager(store, adapters, new Workdirs([cwd], join(h.dir, 'dirs.json'), join(h.dir, 'scratch')), new AttachmentStore(h.dir));
  const socket = join(h.dir, 'ipc/calli.sock');
  const borrowing = new BorrowingService(store, manager, adapters, h.dir, socket);
  borrowing.browser = h.make();
  const server = borrowingServer(borrowing); await listenPrivate(server, socket);
  const root = await manager.createThread({ agent: 'claude', cwd });
  return { borrowing, root, env: { ...process.env, ...borrowing.environment(root.id), CALLI_AGENT_BROWSER: fakeAgentBrowser },
    async close() {
      await borrowing.dispose(); await manager.shutdown();
      await new Promise<void>(r => server.close(() => r())); store.close();
    } };
}

test('agents find the browser on demand through calli tools, with usage only when described', async () => {
  const h = harness();
  const s = await service(h);
  const prev = process.env.CALLI_AGENT_BROWSER;
  process.env.CALLI_AGENT_BROWSER = fakeAgentBrowser;
  try {
    for (const query of ['浏览器', '邮箱', 'gmail', 'browser']) {
      const found = await s.borrowing.command(s.root.id, { op: 'tools.search', query }) as any[];
      assert.deepEqual(found.map(t => t.id), ['calli/browser'], query);
      assert.equal(found[0].usage, undefined, 'search stays short');
    }
    assert.deepEqual(await s.borrowing.command(s.root.id, { op: 'tools.search', query: '生图' }), []);
    const spec = await s.borrowing.command(s.root.id, { op: 'tools.describe', tool: 'calli/browser' }) as any;
    assert.equal(spec.mode, 'direct');
    assert.equal(spec.availability, 'available');
    assert.equal(spec.running, false, 'describing does not start Chrome');
    assert.ok(spec.usage.some((line: string) => line.includes('calli browser snapshot')));
    assert.equal(h.launches().length, 0);
    await assert.rejects(s.borrowing.command(s.root.id, { op: 'tools.run', tool: 'calli/browser', requestId: 'x', input: {} }), /used directly/);

    process.env.CALLI_AGENT_BROWSER = '/nonexistent/agent-browser';
    const missing = await s.borrowing.command(s.root.id, { op: 'tools.describe', tool: 'calli/browser' }) as any;
    assert.equal(missing.availability, 'unavailable');
    assert.match(missing.reason, /征得同意后运行 npm install -g agent-browser/);
    assert.ok(missing.usage.length, 'usage is there for after the install');
    // Installing it later is enough: each look checks again, without restarting Calli.
    process.env.CALLI_AGENT_BROWSER = fakeAgentBrowser;
    assert.equal((await s.borrowing.command(s.root.id, { op: 'tools.describe', tool: 'calli/browser' }) as any).availability, 'available');
  } finally {
    if (prev === undefined) delete process.env.CALLI_AGENT_BROWSER; else process.env.CALLI_AGENT_BROWSER = prev;
    await s.close(); h.close();
  }
});

const run = (args: string[], env: NodeJS.ProcessEnv) => new Promise<{ code: number; stdout: string; stderr: string }>(resolve => {
  execFile(process.execPath, [cli, ...args], { env }, (err, stdout, stderr) => resolve({ code: err ? (err as any).code ?? 1 : 0, stdout, stderr }));
});

test('calli browser passes agent-browser commands through, connected to the person\'s browser', async () => {
  const h = harness();
  const s = await service(h);
  try {
    const status = await run(['browser'], s.env);
    assert.equal(status.code, 0, status.stderr);
    const { port, cdp } = JSON.parse(status.stdout);
    assert.equal(cdp, `http://127.0.0.1:${port}`);
    assert.equal(h.launches().length, 1, 'started on first use');

    const out = await run(['browser', 'get', 'attr', '@e3', 'href', '--help'], s.env);
    assert.equal(out.code, 0, out.stderr);
    const seen = JSON.parse(out.stdout);
    assert.deepEqual(seen.args, ['get', 'attr', '@e3', 'href', '--help'], 'arguments, --help included, belong to agent-browser');
    assert.equal(seen.cdp, String(port));
    assert.equal(seen.boundaries, '1');
    assert.equal(seen.session, 'calli');
    assert.equal(seen.idle, '600000');
    assert.equal(h.launches().length, 1, 'later commands reuse the running browser');

    assert.equal((await run(['browser', 'click', '@e1'], { ...s.env, FAKE_AGENT_BROWSER_EXIT: '3' })).code, 3, 'exit status passes through');

    const noAgentBrowser = await run(['browser', 'snapshot'], { ...s.env, CALLI_AGENT_BROWSER: '/nonexistent/agent-browser' });
    assert.notEqual(noAgentBrowser.code, 0);
    assert.match(noAgentBrowser.stderr, /征得同意后运行 npm install -g agent-browser/);

    const outsider = await run(['browser'], { ...process.env, CALLI_TOOLS_SOCKET: s.env.CALLI_TOOLS_SOCKET, CALLI_TOOLS_KEY: 'wrong' });
    assert.notEqual(outsider.code, 0, 'needs the thread\'s tool credentials');
  } finally { await s.close(); h.close(); }
});
