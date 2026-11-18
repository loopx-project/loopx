// Smoke test for the e2e harness itself: sign in, pick an agent, run one turn, see the answer.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('signs in and lists agents', async () => {
  await openUi(page, daemon);
  const agents = await (await api(daemon, '/api/agents')).json();
  assert.ok(agents.some((a) => a.id === 'pi' && a.available));
  assert.ok(agents.some((a) => a.id === 'claude' && a.available));
});

test('picks pi, sends a message, sees the streamed answer', async () => {
  await openUi(page, daemon);
  // The draft composer's agent picker is the first context button in the header.
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('hello');
  await page.keyboard.press('Enter');
  await page.getByText('hi there hello').waitFor({ timeout: 20_000 });
});

test('a page left open says when a newer build is being served, and reloads into it', async () => {
  await openUi(page, daemon);
  assert.equal(await page.getByText('有新版本').count(), 0, 'nothing to say while the build is the one running');

  // A deploy happened: index.html now names another entry script.
  const html = await (await fetch(daemon.base + '/')).text();
  const served = /src="([^"]+)"/.exec(html)[1];
  await page.route(daemon.base + '/', (route) => route.fulfill({ contentType: 'text/html', body: html.replace(served, '/assets/index-NEWER.js') }));
  // The page comes back to the foreground, which is when a phone user would be looking.
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.getByText('有新版本').waitFor({ timeout: 5000 });
  await page.waitForTimeout(7000);
  assert.equal(await page.getByText('有新版本').count(), 1, 'it waits for the user instead of fading out');

  await page.unroute(daemon.base + '/');
  await page.getByRole('button', { name: '刷新' }).click();
  await page.locator('.sidebar-inner').waitFor({ timeout: 15_000 });
  assert.equal(await page.getByText('有新版本').count(), 0);
});
