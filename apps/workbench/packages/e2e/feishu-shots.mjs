// Screenshots of the Feishu chat settings page (配置 → 飞书): the menu row beside 推送, the
// pairing pane (create → waiting link → failed → retry), the configured page (switch with its
// status line, agent and directory rows, echo switch, recent records on demand, test send), and
// the loading shape that must not jump. Desktop 1440 and phone 390, light and dark for the main
// view; light for the rest. The daemon's /api/feishu routes are mocked per page so every state is
// deterministic while the rest of the app runs against the real daemon. One extra shot asks the
// real daemon for its unconfigured state. Run after `npm run build`:
//   node packages/e2e/feishu-shots.mjs [outdir]
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, launchBrowser, openUi } from './harness.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const out = process.argv[2] ?? join(here, '..', '..', 'e2e', 'shots', 'feishu');
mkdirSync(out, { recursive: true });

const daemon = await startDaemon({}, { agents: [
  { id: 'claude', adapter: 'claude', command: ['fake-claude'] },
  { id: 'pi', adapter: 'pi', command: ['fake-pi'] },
]});

const now = Date.now();
const at = (minutesAgo) => new Date(now - minutesAgo * 60_000).toISOString();
const SETTINGS = { enabled: true, agent: 'claude', cwd: '/srv/code/calli-demo', owner: 'ou_demo7f2e', echoWeb: false };
const RECORDS = [
  { key: 'r3', at: at(3), direction: 'out', threadId: 'th1', title: '修一下登录页的报错', result: 'sent', reason: null },
  { key: 'r2', at: at(62), direction: 'in', threadId: null, title: '在吗', result: 'dropped', reason: 'not_owner' },
  { key: 'r1', at: at(65), direction: 'out', threadId: null, title: '测试', result: 'failed', reason: 'cli_failed' },
];
const STATUS = { cli: true, configured: true, botReady: true, realtime: 'live', reason: null, pendingOwner: null, setup: { state: 'idle', kind: null, url: null, reason: null }, records: [RECORDS[0]], dropped: 2 };
const WAITING = { ...STATUS, configured: false, botReady: false, realtime: 'off', setup: { state: 'waiting', kind: 'create', url: 'https://open.feishu.cn/base/auth?code=demo-8c31f2', reason: null } };
const FAILED = { ...STATUS, configured: false, botReady: false, realtime: 'off', setup: { state: 'failed', kind: 'create', url: null, reason: 'timeout' } };
const UNCONFIGURED = { ...STATUS, configured: false, botReady: false, realtime: 'off', setup: { state: 'idle', kind: null, url: null, reason: null } };
const LOGIN_WAITING = { ...STATUS, setup: { state: 'waiting', kind: 'login', url: 'https://open.feishu.cn/base/auth?code=demo-login-4a17', reason: null } };
const LOGIN_FAILED = { ...STATUS, setup: { state: 'failed', kind: 'login', url: null, reason: 'timeout' } };
const LOGIN_DONE = { ...STATUS, setup: { state: 'done', kind: 'login', url: null, reason: null } };

/**
 * Stands in for the daemon's /api/feishu routes on one page. `state.status` is read per request,
 * so the script can flip a state mid-scene (waiting → done) and the page follows at its own pace.
 */
async function mockFeishu(page, state, counts = {}) {
  await page.route('**/api/feishu/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    counts[`${method} ${path.endsWith('/status') && url.searchParams.get('summary') === 'true' ? 'status?summary' : path.split('/').pop()}`] = (counts[`${method} ${path.endsWith('/status') && url.searchParams.get('summary') === 'true' ? 'status?summary' : path.split('/').pop()}`] ?? 0) + 1;
    if (path.endsWith('/status') && state.hold) await new Promise((r) => setTimeout(r, state.hold));
    if (path.endsWith('/settings') && method === 'GET') return route.fulfill({ json: state.settings });
    if (path.endsWith('/settings') && method === 'PUT') return route.fulfill({ json: { ...state.settings, ...(route.request().postDataJSON() ?? {}) } });
    if (path.endsWith('/status')) {
      const status = typeof state.status === 'function' ? state.status() : state.status;
      const body = url.searchParams.get('summary') === 'true' ? { ...status, records: status.records.slice(0, 1) } : { ...status, records: state.records ?? RECORDS };
      return route.fulfill({ json: body });
    }
    if (path.endsWith('/setup') && method === 'POST') return route.fulfill({ json: WAITING });
    if (path.endsWith('/login') && method === 'POST') return route.fulfill({ json: typeof state.status === 'function' ? state.status() : state.status });
    if (path.endsWith('/setup') && method === 'DELETE') return route.fulfill({ json: UNCONFIGURED });
    if (path.endsWith('/test')) return route.fulfill({ status: 202, json: { key: 't0', at: new Date().toISOString(), direction: 'out', threadId: null, title: '测试', result: 'sent', reason: null } });
    return route.continue();
  });
}

const browser = await launchBrowser();
try {
  const open = async (name, viewport, scheme, state, counts) => {
    const page = await browser.newPage({ viewport, colorScheme: scheme });
    page.on('console', (m) => { if (m.type() === 'error') console.log(`[console:${name}]`, m.text().slice(0, 300)); });
    if (state) await mockFeishu(page, state, counts);
    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/settings/feishu');
    return page;
  };

  // The menu row, beside 推送, on both shapes of the settings page.
  for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    const page = await browser.newPage({ viewport, colorScheme: 'light' });
    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/settings');
    await page.locator('.settings-nav-row', { hasText: '飞书' }).first().waitFor({ timeout: 15_000 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(out, `menu-${name}-light.png`) });
    console.log('menu', name);
    await page.close();
  }

  // The configured page: switch with its line, agent, directory, echo, recent, test — the four looks.
  for (const [name, viewport, scheme] of [
    ['desktop-light', { width: 1440, height: 900 }, 'light'],
    ['desktop-dark', { width: 1440, height: 900 }, 'dark'],
    ['phone-light', { width: 390, height: 844 }, 'light'],
    ['phone-dark', { width: 390, height: 844 }, 'dark'],
  ]) {
    const page = await open(name, viewport, scheme, { settings: SETTINGS, status: STATUS });
    await page.locator('.feishu-settings .personal-switch').first().waitFor({ timeout: 15_000 });
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(out, `configured-${name}.png`) });
    console.log('configured', name);
    if (name === 'desktop-light') {
      // The records on demand: the row opens, reads the full list, keeps its height stable.
      await page.locator('.feishu-bottom .text-btn').click();
      await page.locator('.feishu-records > div').first().waitFor({ timeout: 15_000 });
      await page.waitForTimeout(200);
      await page.screenshot({ path: join(out, 'records-desktop-light.png') });
      // The test send: a notice, then the recent line carries the fresh record.
      await page.getByRole('button', { name: '发一条测试' }).click();
      await page.locator('.push-test-result').waitFor({ timeout: 15_000 });
      await page.waitForTimeout(200);
      await page.screenshot({ path: join(out, 'tested-desktop-light.png') });
      console.log('records and test');
    }
    await page.close();
  }

  // A warning line a person can act on: the console has event subscription off.
  {
    const page = await open('console-off', { width: 1440, height: 900 }, 'light', { settings: SETTINGS, status: { ...STATUS, realtime: 'console-off' } });
    await page.locator('.feishu-settings .personal-warning').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(out, 'console-off-desktop-light.png') });
    await page.close();
    console.log('console-off');
  }

  // Loading that holds its shape: the status read is held back a moment; the page shows its one
  // placeholder line, then the same pane with its rows — no extra "updating" row in between.
  {
    const page = await open('loading', { width: 1440, height: 900 }, 'light', { settings: SETTINGS, status: STATUS, hold: 1500 });
    await page.locator('.feishu-settings').waitFor({ timeout: 15_000 });
    await page.screenshot({ path: join(out, 'loading-desktop-light.png') });
    await page.locator('.feishu-settings .personal-switch').first().waitFor({ timeout: 15_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(out, 'loading-settled-desktop-light.png') });
    await page.close();
    console.log('loading');
  }

  // Pairing: create → waiting (link, copy, cancel) → done lands on the configured page.
  for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    const state = { settings: { ...SETTINGS, enabled: false }, status: UNCONFIGURED };
    const counts = {};
    const page = await open(`waiting-${name}`, viewport, 'light', state, counts);
    await page.getByRole('button', { name: '创建飞书机器人' }).click();
    await page.locator('.feishu-setup-waiting').waitFor({ timeout: 15_000 });
    state.status = WAITING; // the poll keeps being told the scan has not landed
    await new Promise((r) => setTimeout(r, 3600));
    await page.screenshot({ path: join(out, `waiting-${name}-light.png`) });
    const polls = (counts['GET status?summary'] ?? 0) + (counts['GET status'] ?? 0);
    console.log(`waiting ${name}: ${polls} status polls while the link was out`);
    // The scan lands: the pane gives way to the configured page, settings reload.
    state.status = STATUS;
    state.settings = SETTINGS;
    await page.locator('.feishu-settings .personal-switch').first().waitFor({ timeout: 15_000 });
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(out, `paired-${name}-light.png`) });
    console.log(`paired ${name}`);
    await page.close();
  }

  // A failed setup reads its reason and offers the retry; retry asks again.
  {
    const state = { settings: { ...SETTINGS, enabled: false }, status: FAILED };
    const counts = {};
    const page = await open('failed', { width: 1440, height: 900 }, 'light', state, counts);
    await page.locator('.feishu-setup .personal-warning').waitFor({ timeout: 15_000 });
    await page.screenshot({ path: join(out, 'failed-desktop-light.png') });
    await page.getByRole('button', { name: '重试' }).click();
    await page.locator('.feishu-setup-waiting').waitFor({ timeout: 15_000 });
    console.log('failed setup retried, setup posts:', counts['POST setup'] ?? 0);
    await page.screenshot({ path: join(out, 'failed-retried-desktop-light.png') });
    await page.close();
    console.log('failed');
  }

  // The owner fallback: no owner bound, someone messaged the bot — the pending account is offered
  // for binding, and the bind button saves it straight away.
  {
    const state = { settings: { ...SETTINGS, owner: '' }, status: { ...STATUS, pendingOwner: 'ou_pending9c' } };
    const counts = {};
    const page = await open('owner', { width: 1440, height: 900 }, 'light', state, counts);
    await page.locator('.feishu-owner input').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(out, 'pending-owner-desktop-light.png') });
    await page.getByRole('button', { name: '绑定这个账号' }).click();
    await page.locator('.feishu-settings', { hasText: '已保存' }).waitFor({ timeout: 15_000 });
    console.log('owner bound, PUT settings:', counts['PUT settings'] ?? 0);
    await page.screenshot({ path: join(out, 'owner-bound-desktop-light.png') });
    await page.close();
    console.log('pending owner');
  }

  // Saving a change: the dirty draft goes out whole, and the line follows the fresh status.
  {
    const state = { settings: { ...SETTINGS, enabled: false }, status: { ...STATUS, realtime: 'connecting' } };
    const counts = {};
    const page = await open('save', { width: 1440, height: 900 }, 'light', state, counts);
    await page.locator('.feishu-settings .personal-switch').first().waitFor({ timeout: 15_000 });
    await page.locator('.feishu-settings .personal-switch').first().click();
    await page.getByRole('button', { name: '保存' }).click();
    await page.locator('.feishu-settings', { hasText: '已保存' }).waitFor({ timeout: 15_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(out, 'saved-connecting-desktop-light.png') });
    console.log('saved, PUT settings:', counts['PUT settings'] ?? 0, 'status after:', counts['GET status?summary'] ?? 0);
    await page.close();
  }

  // Sign-in to confirm identity: the owner is empty, so the scan button leads and the hints sit
  // below the input; the waiting panel speaks of signing in, never of creating; when the scan
  // lands, the owner fills in and an unsaved echo edit survives. Desktop and phone, light and dark.
  for (const [shape, viewport] of [['desktop', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    for (const scheme of ['light', 'dark']) {
      const state = { settings: { ...SETTINGS, owner: '' }, status: { ...STATUS, setup: { state: 'idle', kind: null, url: null, reason: null } } };
      const counts = {};
      const page = await open(`login-${shape}-${scheme}`, viewport, scheme, state, counts);
      await page.locator('.feishu-owner-main .primary').waitFor({ timeout: 15_000 });
      await page.waitForTimeout(200);
      if (scheme === 'light') {
        await page.screenshot({ path: join(out, `owner-empty-${shape}-light.png`) });
        // An unsaved edit made before the scan must survive the owner filling in.
        await page.locator('.feishu-settings .personal-switch').nth(1).click();
      }
      state.status = LOGIN_WAITING; // the daemon is asked before the click so the POST itself reads waiting
      await page.getByRole('button', { name: '扫码确认我的身份' }).click();
      await page.locator('.feishu-setup-waiting').waitFor({ timeout: 15_000 });
      await new Promise((r) => setTimeout(r, 3600));
      await page.screenshot({ path: join(out, `login-waiting-${shape}-${scheme}.png`) });
      // The scan lands: the daemon wrote the owner; the page picks it up at its next poll.
      state.settings = { ...SETTINGS, owner: 'ou_scanned5d' };
      state.status = LOGIN_DONE;
      await page.waitForFunction(() => document.querySelector('.feishu-owner-main input')?.value === 'ou_scanned5d', null, { timeout: 15_000 });
      await page.waitForTimeout(300);
      if (scheme === 'light') {
        const echo = await page.locator('.feishu-settings .personal-switch').nth(1).getAttribute('aria-checked');
        console.log(`login landed ${shape}: owner filled, unsaved echo edit kept:`, echo === 'true' ? 'yes' : 'NO');
        await page.screenshot({ path: join(out, `login-done-${shape}-light.png`) });
      }
      await page.close();
      console.log(`login flow ${shape}-${scheme}, POST login:`, counts['POST login'] ?? 0);
    }
  }

  // A failed sign-in reads its reason and retries through the same pane.
  {
    const state = { settings: { ...SETTINGS, owner: '' }, status: LOGIN_FAILED };
    const page = await open('login-failed', { width: 1440, height: 900 }, 'light', state);
    await page.locator('.feishu-setup .personal-warning').waitFor({ timeout: 15_000 });
    await page.screenshot({ path: join(out, 'login-failed-desktop-light.png') });
    console.log('login failed shot');
    await page.close();
  }

  // The real daemon, no mocks: its own unconfigured state, whatever it is today.
  {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    page.on('console', (m) => { if (m.type() === 'error') console.log('[console:real]', m.text().slice(0, 300)); });
    await openUi(page, daemon);
    await page.goto(daemon.base + '/#/settings/feishu');
    await page.locator('.feishu-settings').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: join(out, 'unconfigured-real-desktop-light.png') });
    console.log('real daemon unconfigured shot');
    await page.close();
  }

  console.log(`shots in ${out}`);
  console.log(`daemon ${daemon.pid} (${daemon.base})`);
} finally {
  await browser.close();
  await daemon.stop();
}
