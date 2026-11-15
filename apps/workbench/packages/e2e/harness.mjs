/**
 * End-to-end harness: uses the prebuilt UI, starts the daemon against the fake agents from
 * packages/daemon/test/fixtures, and drives a real browser through playwright-core.
 *
 * The browsers come from wherever they are: the playwright cache, or a system Chrome.
 */
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = join(here, '..');
const fixtures = join(repoRoot, 'daemon', 'test', 'fixtures');

export const TOKEN = 'e2e-token-0123456789abcdef';

/** A free TCP port, so parallel test files do not fight over one. */
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on('error', reject);
  });
}

/** Every process below `pid`, so stop() can wait for agent CLIs the daemon probed to exit. */
function descendants(pid) {
  let table;
  try {
    table = execFileSync('ps', ['-eo', 'pid=,ppid='], { encoding: 'utf8' });
  } catch {
    return [];
  }
  const children = new Map();
  for (const line of table.trim().split('\n')) {
    const [child, parent] = line.trim().split(/\s+/).map(Number);
    children.set(parent, [...(children.get(parent) ?? []), child]);
  }
  const found = [];
  for (let queue = [pid]; queue.length; ) {
    for (const child of children.get(queue.shift()) ?? []) found.push(child), queue.push(child);
  }
  return found;
}

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

/** Wraps a fixture script in an executable shell shim, so `CALLI_*_BIN` can point at a plain name. */
function shim(dir, name, script) {
  const path = join(dir, name);
  writeFileSync(path, `#!/bin/sh\nexec "${process.execPath}" "${join(fixtures, script)}" "$@"\n`, { mode: 0o755 });
  return path;
}

/**
 * One daemon with its own data dir, workdir and fake agents. Returns the base URL plus handles.
 * `env` is merged into the daemon's environment (extra CALLI_* switches, fake-agent switches).
 */
export async function startDaemon(env = {}, opts = {}) {
  for (const artifact of ['daemon/dist/index.js', 'web/dist/index.html']) {
    if (!existsSync(join(repoRoot, artifact))) {
      throw new Error(`Missing ${artifact}. Run npm run test:e2e, or npm run build before running tests directly.`);
    }
  }
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-e2e-data-'));
  // A fixed name reads better in screenshots than the random suffix mkdtemp adds.
  const workdir = opts.workdir ?? mkdtempSync(join(tmpdir(), 'calli-demo-'));
  if (opts.workdir) mkdirSync(opts.workdir, { recursive: true });
  const homeDir = join(dataDir, 'home'); mkdirSync(homeDir);
  const binDir = join(dataDir, 'bin');
  mkdirSync(binDir);
  shim(binDir, 'fake-pi', 'fake-pi.mjs');
  shim(binDir, 'fake-claude', 'fake-acp.mjs');
  shim(binDir, 'fake-agy', 'fake-agy.mjs');
  shim(binDir, 'fake-doubao', 'fake-doubao.mjs');
  let doubaoRuntime = '';
  if (env.CALLI_DOUBAO_STREAM === '1' || env.CALLI_E2E_DOUBAO_RUNTIME === '1') {
    const { createFakeDoubaoRuntime } = await import('../daemon/test/fixtures/doubao-runtime.mjs');
    doubaoRuntime = join(dataDir, 'doubao-runtime');
    createFakeDoubaoRuntime(doubaoRuntime);
  }
  // The dsh adapter wants the same fake ACP agent shaped the way DeepSeek Harness answers.
  writeFileSync(
    join(binDir, 'fake-dsh'),
    `#!/bin/sh\nFAKE_ACP_FLAVOR=dsh exec "${process.execPath}" "${join(fixtures, 'fake-acp.mjs')}" "$@"\n`,
    { mode: 0o755 },
  );
  // An extra CLI for the discovery tests: looks like another copy of agy on PATH.
  if (env.CALLI_E2E_DISCOVER_AGY2 === '1') shim(binDir, 'agy2', 'fake-agy.mjs');
  if (env.CALLI_E2E_DISCOVER_DOUBAO2 === '1') shim(binDir, 'doubao2', 'fake-doubao.mjs');

  const port = await freePort();
  const daemonEnv = {
    ...process.env,
    HOME: homeDir,
    // The fake agents are shims in binDir, so that directory leads the PATH.
    PATH: `${binDir}:${process.env.PATH ?? ''}`,
    // A test hub's agents are the fakes it was given; the machine's real CLIs and accounts
    // must not auto-join it, however the fakes were configured (inline or agents.json).
    CALLI_AUTO_JOIN: 'off',
    CALLI_HUB_URL: '',
    CALLI_HUB_TOKEN: '',
    CALLI_TOKEN: TOKEN,
    CALLI_HOST: '127.0.0.1',
    CALLI_PORT: String(port),
    CALLI_DATA_DIR: dataDir,
    CALLI_WORKDIRS: workdir,
    // Serve this repo's own build, never a dist inherited from the ambient environment.
    CALLI_WEB_DIR: join(repoRoot, 'web', 'dist'),
    CALLI_PI_BIN: 'fake-pi',
    CALLI_CLAUDE_ACP_BIN: 'fake-claude',
    CALLI_AGY_BIN: 'fake-agy',
    CALLI_DSH_BIN: 'fake-dsh',
    CALLI_DOUBAO_BIN: 'fake-doubao',
    CALLI_DOUBAO_STREAM: '',
    CALLI_DOUBAO_RUNTIME: doubaoRuntime,
    PI_CODING_AGENT_DIR: join(dataDir, 'pi-agent'),
    CALLI_AGY_HOME: join(dataDir, 'agy-home'),
    ZCODE_DATA_BASE_DIR: dataDir,
    // The person's writing habits note lives here, not in the home directory the test machine has.
    CALLI_NOTES_DIR: join(dataDir, 'notes'),
    // Its own socket, never the one in the home directory that a real hub on this machine holds.
    CALLI_SOCKET: join(dataDir, 'daemon.sock'),
    ...env,
  };
  if (opts.agents !== undefined) {
    // A seeded file exercises persistent registry writes; inline JSON intentionally stays read-only.
    writeFileSync(join(dataDir, 'agents.json'), JSON.stringify({ agents: opts.agents }, null, 2) + '\n', { mode: 0o600 });
    delete daemonEnv.CALLI_AGENTS_JSON;
  }
  const child = spawn(process.execPath, [join(repoRoot, 'daemon', 'dist', 'index.js')], {
    env: daemonEnv,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', (c) => (log += c));
  child.stderr.on('data', (c) => (log += c));

  // A wildcard bind still answers on the loopback; a named host does not, so poll the one asked for.
  const bound = daemonEnv.CALLI_HOST || '127.0.0.1';
  const base = `http://${bound === '0.0.0.0' || bound === '::' ? '127.0.0.1' : bound}:${port}`;
  const deadline = Date.now() + 20_000;
  for (;;) {
    try {
      const res = await fetch(`${base}/api/health`);
      if (res.ok) break;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) throw new Error(`daemon did not start; log:\n${log}`);
    if (child.exitCode !== null) throw new Error(`daemon exited with ${child.exitCode}; log:\n${log}`);
    await new Promise((r) => setTimeout(r, 100));
  }

  let relay;
  return {
    async connectRelay(hub, token) {
      relay?.stop();
      const { RelayClient } = await import('../daemon/dist/relay-client.js');
      relay = new RelayClient({ hubUrl: hub.base, token, socket: daemonEnv.CALLI_SOCKET, version: 'e2e-node', retryMs: 50 });
      relay.start();
      const id = token.split('.')[0];
      for (const deadline = Date.now() + 10_000; ; ) {
        const hosts = await (await api(hub, '/api/hosts')).json();
        if (hosts.some(host => host.id === id && host.status === 'online')) return;
        if (Date.now() > deadline) throw new Error(`Node did not connect: ${relay.error ?? relay.status}`);
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    },
    base,
    pid: child.pid,
    token: TOKEN,
    dataDir,
    workdir,
    binDir,
    notesDir: join(dataDir, 'notes'),
    log: () => log,
    async stop() {
      relay?.stop();
      // Real agent CLIs on PATH (zcode, codex) get probed too, and may still write into dataDir
      // while shutting down; removing it before they are gone leaves it behind in /tmp.
      const below = descendants(child.pid);
      if (child.exitCode === null) {
        child.kill('SIGTERM');
        await new Promise((r) => child.on('exit', r));
      }
      for (const deadline = Date.now() + 5_000; below.some(alive) && Date.now() < deadline; ) {
        await new Promise((r) => setTimeout(r, 100));
      }
      rmSync(dataDir, { recursive: true, force: true });
      rmSync(workdir, { recursive: true, force: true });
    },
  };
}

/**
 * The tests read the interface in its source language. Without this the page follows the
 * browser's language, which on most machines is English.
 */
function inChinese(browser) {
  const newContext = browser.newContext.bind(browser);
  const newPage = browser.newPage.bind(browser);
  browser.newContext = (options = {}) => newContext({ locale: 'zh-CN', ...options });
  browser.newPage = (options = {}) => newPage({ locale: 'zh-CN', ...options });
  return browser;
}

/** Finds a Chromium the installed playwright-core can drive: the cache, then a system browser. */
export function launchBrowser() {
  const home = process.env.HOME ?? '';
  const candidates = [
    `${home}/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`,
    '/usr/bin/chromium',
    '/usr/lib/chromium/chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
  ];
  return (async () => {
    try {
      return inChinese(await chromium.launch({ headless: true, args: ['--no-sandbox'] }));
    } catch {
      // fall through to an explicit executable
    }
    let lastErr;
    for (const executablePath of candidates) {
      if (!existsSync(executablePath)) continue;
      try {
        return inChinese(await chromium.launch({ headless: true, args: ['--no-sandbox'], executablePath }));
      } catch (err) {
        lastErr = err;
      }
    }
    throw lastErr ?? new Error('no Chromium found for e2e tests');
  })();
}

/** Opens the UI and connects with the token, ready for test steps. Idempotent per page. */
export async function openUi(page, daemon) {
  await page.goto(daemon.base + '/');
  // On phones the sidebar is a drawer: it exists but is not visible until opened.
  await page.locator('.sidebar-inner').waitFor({ state: 'attached', timeout: 15_000 });
  // Without a saved token the page opens on the demo workspace; connecting is the way in from there.
  if (await page.locator('.demo-bar').count()) await connect(page, daemon.token);
}

/** Connects the demo page to the hub through the connect dialog, and waits for the demo to go. */
export async function connect(page, token) {
  await page.locator('.demo-bar-btn:visible').first().click();
  await page.getByLabel('访问 token').fill(token);
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.demo-bar').first().waitFor({ state: 'detached', timeout: 15_000 });
  await page.locator('.sidebar-inner').waitFor({ state: 'attached', timeout: 15_000 });
}

/** Signed fetch against the daemon's API, for arranging state outside the UI. */
export function api(daemon, path, init = {}) {
  return fetch(daemon.base + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${daemon.token}`, ...(init.headers ?? {}) },
  });
}

/** Leave a settings detail and its phone index through the same Back control as the product. */
export async function closeSettings(page) {
  for (let i = 0; i < 4 && await page.locator('.settings-page').count(); i++) {
    await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
  }
  await page.locator('.settings-page').waitFor({ state: 'detached' });
}

/** Collaboration is a secondary action in the composer's add menu on every screen. */
export async function openCollaboration(page) {
  await page.getByRole('button', { name: '添加与模式', exact: true }).click();
  await page.getByRole('menuitem', { name: '协作', exact: true }).or(page.getByRole('option', { name: '协作', exact: true })).click();
  await page.getByRole('menu', { name: '协作', exact: true }).waitFor();
}

/** Register a node and connect its real relay client to this test hub. */
export async function linkDaemon(hub, node, label = 'devbox') {
  const response = await api(hub, '/api/hosts', { method: 'POST', body: JSON.stringify({ label }) });
  if (response.status !== 201) throw new Error(`Registering node: ${response.status} ${await response.text()}`);
  const machine = await response.json();
  await node.connectRelay(hub, machine.token);
  return machine;
}
