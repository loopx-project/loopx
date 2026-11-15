import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { api, startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';

// 额度恢复自动续跑: the quiet link beside the takeover buttons on an exhausted turn, the watch
// it arms, the global switch in the handover settings, and the daemon resending the failed turn
// once the quota reads healthy again.
let dir, daemon, browser;
const shots = join(repoRoot, 'e2e', 'shots', 'quota-resume');
/** agy's quota file: a used percent, or { used, resetsAt } to place the reset in time. */
const setQuota = (value) => writeFileSync(join(dir, 'agy.json'), JSON.stringify({ 'Gemini Models': value }));
const request = async (path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await res.json(); assert.ok(res.ok, JSON.stringify(data)); return data;
};
const wait = async (fn, timeout = 20000) => {
  const end = Date.now() + timeout;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 60)); }
};
const thread = (id) => request(`/api/threads/${id}`);
const idle = id => wait(async () => { const t = await thread(id); return t.status !== 'running' && !['checking', 'preparing', 'countdown'].includes(t.autoHandover?.state) && !t.queued.length && t; });
const create = title => request('/api/threads', 'POST', { agent: 'agy', model: 'gemini-pro', cwd: daemon.workdir, title });
const send = (id, text) => request(`/api/threads/${id}/turns`, 'POST', { text });
const configure = (settings) => request('/api/handover-settings', 'PUT', { enabled: false, threshold: 90, orders: {}, ...settings });
const select = async (page, title, mobile = false) => {
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: new RegExp(`^${title}$`) }) }).click();
  if (mobile) await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden' && Math.abs(document.querySelector('.main').getBoundingClientRect().x) < 1, null, { timeout: 5000 });
};

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'calli-resume-e2e-')); mkdirSync(shots, { recursive: true });
  setQuota(10);
  daemon = await startDaemon({
    // Fast ticks and a short grace keep the recovery within a test run; production keeps 30 s / 2 min.
    CALLI_RESUME_TICK_MS: '1000', CALLI_RESUME_GRACE_MS: '1000',
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [
      { id: 'agy', adapter: 'agy', label: 'Agent 1', command: ['fake-agy'], env: { FAKE_HANDOVER_QUOTA: join(dir, 'agy.json'), FAKE_HANDOVER_EXHAUST_ONCE: join(dir, 'exhausted-convs') } },
      { id: 'agy2', adapter: 'agy', label: 'Agent 2', command: ['fake-agy'], env: { FAKE_HANDOVER_QUOTA: join(dir, 'agy2.json') } },
    ] }),
  });
  writeFileSync(join(dir, 'agy2.json'), JSON.stringify({ 'Gemini Models': 10 }));
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); rmSync(dir, { recursive: true, force: true }); });

test('the exhausted turn offers waiting beside takeover; arming shows the reset time and cancels', async () => {
  await configure({ resume: false });
  setQuota({ used: 100, resetsAt: new Date(Date.now() + 2 * 3600_000).toISOString() });
  const source = await create('额度续跑链接');
  await send(source.id, 'quota-exhausted'); await idle(source.id);
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await openUi(page, daemon); await select(page, source.title);
    const card = page.locator('.exhausted-note');
    await card.getByRole('button', { name: '交给 Agent 2 接手', exact: true }).waitFor();
    const link = card.getByRole('button', { name: '等额度恢复后自动继续', exact: true });
    await link.waitFor();
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(shots, 'link-desktop-light.png'), animations: 'disabled' });
    await link.click();
    await card.getByText(/额度恢复后自动继续 · 预计/).waitFor();
    assert.equal((await thread(source.id)).autoHandover.resume.origin, 'user');
    await page.screenshot({ path: join(shots, 'armed-desktop-light.png'), animations: 'disabled' });
    await card.getByRole('button', { name: '取消等待', exact: true }).click();
    await link.waitFor();
    assert.equal((await thread(source.id)).autoHandover.resume, null);
  } finally { await context.close(); }
});

test('the global switch arms parked threads; once the quota is back the daemon continues by itself', async () => {
  await configure({ resume: true });
  // The binding window resets fifteen seconds out: past the turn and the arm, near enough to
  // wait out. The watch's first probe lands just after it.
  setQuota({ used: 100, resetsAt: new Date(Date.now() + 15_000).toISOString() });
  const source = await create('全局额度续跑');
  await send(source.id, 'quota-exhausted'); await idle(source.id);
  await wait(async () => (await thread(source.id)).autoHandover?.resume?.origin === 'global');
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
  const page = await phone.newPage();
  try {
    await openUi(page, daemon); await select(page, source.title, true);
    const card = page.locator('.exhausted-note');
    await card.getByText(/额度恢复后自动继续/).waitFor();
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(shots, 'armed-phone-dark.png'), animations: 'disabled' });
    setQuota(10);
    await wait(async () => {
      const events = await request(`/api/threads/${source.id}/events`);
      return events.some(e => e.type === 'handover.resume') && events.filter(e => e.type === 'turn.started').length === 2;
    }, 30000);
    await idle(source.id);
    const divider = page.locator('.handover-divider').filter({ hasText: '额度恢复，已自动继续' });
    await divider.waitFor();
    await divider.scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(shots, 'resumed-phone-dark.png'), animations: 'disabled' });
    await page.reload(); await select(page, source.title, true);
    await divider.waitFor(); // durable across reloads, like the model-switch divider
    assert.equal((await thread(source.id)).autoHandover.resume, null);
  } finally { await phone.close(); }
});

test('the handover settings carry the global resume switch', async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await openUi(page, daemon);
    await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
    await page.locator('.settings-nav').getByRole('button', { name: '自动交接', exact: true }).click();
    const dialog = page.getByRole('region', { name: '自动交接', exact: true });
    const row = dialog.locator('.auto-setting-line').filter({ hasText: '额度恢复后自动继续' });
    await row.getByRole('switch', { name: '额度恢复后自动继续' }).waitFor();
    await page.screenshot({ path: join(shots, 'settings-desktop-light.png'), animations: 'disabled' });
    assert.equal(await row.locator('.auto-toggle').getAttribute('aria-checked'), 'true'); // left on by the previous test
    await row.locator('.auto-toggle').click();
    await dialog.getByRole('button', { name: '保存' }).click();
    await wait(async () => (await request('/api/handover-settings')).resume === false);
  } finally { await context.close(); }
});
