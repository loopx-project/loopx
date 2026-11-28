import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { api, startDaemon, openUi, launchBrowser, repoRoot, closeSettings } from '../harness.mjs';

let dir, daemon, browser;
const quota = (agent, gemini = 10, claude = 20) => writeFileSync(join(dir, `${agent}.json`), JSON.stringify({ 'Gemini Models': gemini, 'Claude Models': claude }));
const request = async (path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await res.json(); assert.ok(res.ok, JSON.stringify(data)); return data;
};
const wait = async fn => {
  const end = Date.now() + 20000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 60)); }
};
const thread = (id) => request(`/api/threads/${id}`);
const idle = id => wait(async () => { const t = await thread(id); return t.status !== 'running' && !['checking', 'forking'].includes(t.autoHandover?.state) && !t.queued.length && t; });
const create = title => request('/api/threads', 'POST', { agent: 'agy', model: 'gemini-pro', cwd: daemon.workdir, title });
const send = (id, text, queue = false) => request(`/api/threads/${id}/turns`, 'POST', { text, queue });
const configure = (targets, enabled = true) => request('/api/handover-settings', 'PUT', { enabled, threshold: 90, orders: { agy: targets } });
const select = async (page, title, mobile = false) => {
  if (mobile && !await page.locator('.drawer-open').count()) await page.locator('.menu-btn').click();
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: new RegExp(`^${title}$`) }) }).click();
  if (mobile) await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden' && Math.abs(document.querySelector('.main').getBoundingClientRect().x) < 1, null, { timeout: 5000 });
};

const shots = join(repoRoot, 'e2e', 'shots', 'auto-handover');
before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'loochat-auto-e2e-')); mkdirSync(shots, { recursive: true });
  for (const id of ['agy', 'agy2', 'agy3']) quota(id);
  daemon = await startDaemon({ LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [
    ...['agy', 'agy2', 'agy3'].map((id, i) => ({ id, adapter: 'agy', label: `Agent ${i + 1}`, command: ['fake-agy'], env: { FAKE_HANDOVER_QUOTA: join(dir, `${id}.json`) } })),
    { id: 'offline', adapter: 'agy', label: 'Offline', command: ['missing-auto-fixture'] },
  ] }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); rmSync(dir, { recursive: true, force: true }); });

test('plan exhaustion auto-forks to a lightweight continuation with 0-token anchor and jump notice', async () => {
  quota('agy'); quota('agy2', 95); quota('agy3');
  await configure([{ agent: 'offline' }, { agent: 'agy2', model: 'gemini-pro' }, { agent: 'agy3', model: 'gemini-pro' }], true);
  const source = await create('自动轻量分叉');
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await openUi(page, daemon); await select(page, source.title);
    await send(source.id, 'quota-near'); await idle(source.id);
    // Healthy near-quota turn does not hijack or spawn a fork.
    assert.equal((await request('/api/threads')).some(t => t.forkedFrom === source.id), false);

    await send(source.id, 'quota-exhausted');
    await send(source.id, 'next queued step', true);
    const fork = await wait(async () => (await request('/api/threads')).find(t => t.forkedFrom === source.id));
    await idle(fork.id);
    assert.equal(fork.agent, 'agy3');
    assert.equal(fork.forkGroup, source.id);
    assert.equal(fork.forkTag, '自动续跑');

    const starts = (await request(`/api/threads/${fork.id}/events`)).filter(e => e.type === 'turn.started');
    assert.match(starts[0].data.text, /<loochat-fork-anchor>/);
    assert.match(starts[0].data.text, /quota-near/);
    assert.equal(starts[0].data.displayText ?? starts[0].data.text, 'quota-exhausted');
    assert.equal(starts[1].data.displayText ?? starts[1].data.text, 'next queued step');

    // Source thread shows the concise auto-fork notice with direct jump button.
    await page.locator('.auto-notice').filter({ hasText: '已自动分叉至 Agent 3' }).waitFor();
    await page.locator('.auto-notice').getByRole('button', { name: '查看新分支' }).click();
    await page.locator('.topbar-title', { hasText: '自动续跑' }).waitFor();
  } finally { await context.close(); quota('agy2'); }
});

test('manual exhausted card offers one-click lightweight fork continuation and minimal fork card', async () => {
  quota('agy'); quota('agy2', 95); quota('agy3');
  await configure([{ agent: 'agy2', model: 'gemini-pro' }, { agent: 'agy3', model: 'gemini-pro' }], false);
  const source = await create('手动分叉跳过空额度');
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await openUi(page, daemon); await select(page, source.title);
    await send(source.id, 'quota-exhausted'); await idle(source.id);
    await page.getByRole('button', { name: '用 Agent 3 · gemini-pro 分叉继续', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: /Agent 2/ }).count(), 0);
    await page.getByRole('button', { name: '换其他模型', exact: true }).click();
    await page.locator('.fork-subchat-dialog').waitFor();
    await page.locator('.fork-subchat-dialog').getByRole('button', { name: '取消' }).click();
    await page.getByRole('button', { name: '用 Agent 3 · gemini-pro 分叉继续', exact: true }).click();
    const next = await wait(async () => (await request('/api/threads')).find(t => t.forkedFrom === source.id));
    assert.equal(next.agent, 'agy3'); assert.equal(next.model, 'gemini-pro');
  } finally { await context.close(); quota('agy2'); }
});

for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
  test(`simplified auto-continue settings: ${mobile ? 'phone' : 'desktop'} ${theme}`, async () => {
    await configure([{ agent: 'agy', model: 'gemini-pro' }, { agent: 'agy', model: 'claude-sonnet' }, { agent: 'agy2', model: 'gemini-pro' }]);
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
    const page = await context.newPage();
    try {
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '自动续跑', exact: true }).click();
      const dialog = page.getByRole('region', { name: '自动续跑', exact: true });
      await dialog.getByRole('switch', { name: '额度用完时自动分叉继续', exact: true }).waitFor();
      // Legacy auto-compact & method/threshold controls are gone.
      assert.equal(await dialog.getByText('自动压缩').count(), 0);
      assert.equal(await dialog.getByText('已用额度阈值').count(), 0);
      await dialog.locator('.auto-order-head').filter({ hasText: 'Agent 1' }).click();
      await dialog.getByRole('button', { name: '添加备选目标' }).waitFor();
      if (mobile) {
        await dialog.locator('li').first().getByRole('button', { name: /^调整/ }).click();
        await page.getByRole('menuitem', { name: '下移', exact: true }).click();
      } else {
        await dialog.locator('li').first().locator('.auto-order-grip').dragTo(dialog.locator('li').nth(1));
      }
      assert.match(await dialog.locator('li').first().innerText(), /claude-sonnet/);
      assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1));
      await page.screenshot({ path: join(shots, `settings-${mobile ? 'phone' : 'desktop'}-${theme}.png`) });
      await dialog.getByRole('button', { name: '保存续跑设置' }).click();
      await dialog.getByRole('status').filter({ hasText: '已保存' }).waitFor();
      await closeSettings(page);
      assert.equal((await request('/api/handover-settings')).orders.agy[0].model, 'claude-sonnet');
    } finally { await context.close(); }
  });
}
