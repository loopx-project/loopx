// Before it is connected the page shows a made-up workspace instead of asking for a token. It
// talks to no daemon; anything that would change something opens the connect dialog, which tries
// a token before keeping it.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;
const apiCalls = [];

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith('/api/')) apiCalls.push(req.url());
  });
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const savedToken = () => page.evaluate(() => localStorage.getItem('calli.token'));
const dialog = () => page.getByRole('dialog', { name: '连接到 hub' });

test('without a token the page opens on the demo, and asks the daemon nothing', async () => {
  await page.goto(daemon.base + '/');
  await page.locator('.demo-bar').waitFor({ timeout: 15_000 });
  await page.locator('.sidebar').getByText('设置页加深色模式').click();
  // The finished turn: the answer and the files it changed.
  await page.getByText('设置页「语言」下面多了一行「外观」').waitFor({ timeout: 5000 });
  await page.locator('.thread').getByText('src/theme.ts').first().waitFor();
  // The one waiting on an approval.
  await page.locator('.sidebar').getByText('修复商品列表滚动跳动').click();
  await page.getByText('npm run e2e -- product-list').first().waitFor();
  // The plan page is filled in too.
  await page.locator('.sidebar').getByRole('button', { name: '计划' }).click();
  await page.getByText('发布 2.0').waitFor();
  assert.deepEqual(apiCalls, [], 'the demo is all in the page');
});

test('sending asks to connect, and the draft stays', async () => {
  await page.locator('.sidebar').getByText('设置页加深色模式').click();
  const box = page.locator('.composer textarea');
  await box.click();
  await page.keyboard.type('再加一个高对比度');
  await page.keyboard.press('Enter');
  await dialog().waitFor({ timeout: 5000 });
  await dialog().getByText('连接后再发送').waitFor();
  assert.equal(await box.inputValue(), '再加一个高对比度');
  assert.deepEqual(apiCalls, [], 'nothing was sent anywhere');
  await dialog().getByRole('button', { name: '取消' }).click();
  await dialog().waitFor({ state: 'detached' });
});

test('a wrong token is said in the dialog and not kept', async () => {
  await page.locator('.demo-bar-btn').click();
  await page.getByLabel('访问 token').fill('not-the-token-at-all');
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await dialog().getByText('token 不对').waitFor({ timeout: 5000 });
  assert.equal(await savedToken(), null);
  assert.equal(await page.locator('.demo-bar').count(), 1, 'still the demo');
});

test('the right token connects, and the demo is gone', async () => {
  await page.getByLabel('访问 token').fill(daemon.token);
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.demo-bar').waitFor({ state: 'detached', timeout: 15_000 });
  assert.equal(await savedToken(), daemon.token);
  assert.equal(await page.locator('.sidebar').getByText('设置页加深色模式').count(), 0, 'no demo thread is left over');
  assert.equal(await page.locator('.composer textarea').getAttribute('placeholder').then((p) => p?.includes('连接 hub')), false);
});

test('a token that stops working goes back to the demo with the dialog saying why', async () => {
  await page.evaluate(() => localStorage.setItem('calli.token', 'an-old-token-0123456789'));
  await page.reload();
  await dialog().getByText('token 无效或已过期').waitFor({ timeout: 15_000 });
  assert.equal(await savedToken(), null);
  await page.locator('.demo-bar').waitFor();
});

test('signing out goes back to the demo without asking for a token', async () => {
  await page.getByLabel('访问 token').fill(daemon.token);
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.demo-bar').waitFor({ state: 'detached', timeout: 15_000 });
  await page.getByRole('button', { name: '配置' }).click();
  await page.locator('.settings-nav').getByRole('button', { name: '退出' }).click();
  await page.getByRole('dialog').getByRole('button', { name: '退出' }).click();
  await page.locator('.demo-bar').waitFor({ timeout: 5000 });
  assert.equal(await dialog().count(), 0);
  assert.equal(await savedToken(), null);
});
