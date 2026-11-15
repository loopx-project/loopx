// A message that names another thread — 「标题」（id 前缀） as the assistant writes it, or a whole
// id — becomes a jump link: one click (or Enter) opens that thread as a pick in the list would.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { api, startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = join(repoRoot, 'e2e', 'shots', 'thread-links');
before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

const request = async (path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await res.json(); assert.ok(res.ok, JSON.stringify(data)); return data;
};
const wait = async fn => {
  const end = Date.now() + 20000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 60)); }
};
// The fake pi echoes the message back, so the answer carries the citation the test typed.
const create = title => request('/api/threads', 'POST', { agent: 'pi', cwd: daemon.workdir, title });
const send = (id, text) => request(`/api/threads/${id}/turns`, 'POST', { text });
const idle = async id => wait(async () => (await request(`/api/threads/${id}`)).status !== 'running');

async function setup(t, phone = false, theme = 'light') {
  const context = await browser.newContext({
    viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 },
    isMobile: phone, hasTouch: phone, colorScheme: theme,
  });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await openUi(page, daemon);
  // On a phone the sidebar is a drawer: open it to reach the list.
  if (phone) await page.locator('.menu-btn').click();
  return page;
}

async function pickThread(page, title) {
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: new RegExp(`^${title}$`) }) }).click();
}

test('「标题」（id 前缀） in an answer links to that thread, and the click opens it', async t => {
  const source = await create('跳转源会话');
  await send(source.id, '先在这边说一句'); await idle(source.id);
  const citing = await create('引用会话');
  await send(citing.id, `是会话「跳转源会话」（${source.id.slice(0, 8)}）。`); await idle(citing.id);

  const page = await setup(t);
  await pickThread(page, citing.title);
  const link = page.locator('a.thread-link');
  await link.waitFor();
  // Like a file link, it carries no href: the click opens the thread instead of an address.
  assert.equal(await link.getAttribute('title'), '跳转源会话');
  await page.screenshot({ path: join(shots, 'desktop-light.png') });
  await link.click();
  await wait(async () => (await page.locator('.topbar-title').textContent()) === source.title);
  assert.equal(await page.locator('.thread-row.active .thread-title').textContent(), source.title);
});

test('Enter on a focused thread link opens the thread too', async t => {
  const source = await create('键盘跳转源');
  await send(source.id, '起点'); await idle(source.id);
  const citing = await create('键盘引用会话');
  await send(citing.id, `接手 hub 线程 ${source.id} 的任务`); await idle(citing.id);

  const page = await setup(t);
  await pickThread(page, citing.title);
  const link = page.locator('a.thread-link');
  await link.waitFor();
  assert.equal(await link.getAttribute('title'), '键盘跳转源');
  await link.focus();
  await page.keyboard.press('Enter');
  await wait(async () => (await page.locator('.topbar-title').textContent()) === source.title);
});

test('a whole thread id in prose links the same way on a phone', async t => {
  const source = await create('手机跳转源');
  await send(source.id, '起点'); await idle(source.id);
  const citing = await create('手机引用会话');
  await send(citing.id, `看会话「手机跳转源」（${source.id.slice(0, 8)}）的进度`); await idle(citing.id);

  const page = await setup(t, true);
  await pickThread(page, citing.title);
  const link = page.locator('a.thread-link');
  await link.waitFor();
  await page.screenshot({ path: join(shots, 'phone-light.png') });
  await link.click();
  await wait(async () => (await page.locator('.topbar-title').textContent()) === source.title);
});

test('dark themes show the link the same way on desktop and phone', async t => {
  const source = await create('深色跳转源');
  await send(source.id, '起点'); await idle(source.id);
  const citing = await create('深色引用会话');
  await send(citing.id, `是会话「深色跳转源」（${source.id.slice(0, 8)}）。`); await idle(citing.id);

  for (const [phone, name] of [[false, 'desktop'], [true, 'phone']]) {
    const page = await setup(t, phone, 'dark');
    await pickThread(page, citing.title);
    await page.locator('a.thread-link').waitFor();
    await page.screenshot({ path: join(shots, `${name}-dark.png`) });
  }
});
