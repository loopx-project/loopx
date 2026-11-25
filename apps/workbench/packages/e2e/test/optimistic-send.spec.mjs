// Optimistic thread creation, optimistic user messages, and mobile/desktop composer guarantees.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('desktop new thread navigates immediately on send without waiting for createThread or GET /api/threads', async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const page = await context.newPage();
  await openUi(page, daemon);

  // Delay thread creation, turn start, and thread-list refresh to expose any non-optimistic blocking.
  await page.route('**/api/threads', async (route) => {
    const method = route.request().method();
    if (method === 'POST') await new Promise(r => setTimeout(r, 900));
    else if (method === 'GET') await new Promise(r => setTimeout(r, 1200));
    await route.continue().catch(() => undefined);
  });
  await page.route('**/api/threads/*/turns', async (route) => {
    await new Promise(r => setTimeout(r, 600));
    await route.continue().catch(() => undefined);
  });

  const textarea = page.locator('.composer-box textarea');
  await textarea.fill('Optimistic desktop prompt');
  const sentAt = Date.now();
  await page.locator('.composer .send-btn:not(.stop)').click();

  // Immediately leaves the empty new-thread screen and shows the user message (well before the 900ms POST finishes).
  await page.locator('.row.user', { hasText: 'Optimistic desktop prompt' }).waitFor({ timeout: 500 });
  const elapsed = Date.now() - sentAt;
  assert.ok(elapsed < 500, `expected instant optimistic navigation, took ${elapsed}ms`);
  assert.equal(await page.locator('.empty').count(), 0, 'new-thread empty prompt must disappear immediately');
  assert.equal(await textarea.inputValue(), '', 'composer clears immediately so follow-up typing never sticks');

  // Once the delayed backend finishes, the message never disappears or duplicates and the reply arrives.
  await page.locator('.row.assistant .markdown').first().waitFor({ timeout: 15_000 });
  assert.equal(await page.locator('.row.user').count(), 1, 'optimistic and confirmed messages reconcile into one bubble');
  assert.equal(await page.locator('.row.user[data-optimistic]').count(), 0, 'optimistic marker clears after confirmation');

  await context.close();
});

test('typing a follow-up during a slow send does not stick old sent text in the box', async () => {
  const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
  await post(`/api/threads/${thread.id}/turns`, { text: 'seed turn' });
  for (let i = 0; i < 80; i++) {
    if ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status !== 'running') break;
    await new Promise(r => setTimeout(r, 100));
  }

  const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const page = await context.newPage();
  await openUi(page, daemon);
  await page.locator('.thread-item', { hasText: 'seed turn' }).click();
  await page.locator('.row.assistant .markdown').waitFor();

  await page.route('**/turns', async (route) => {
    await new Promise(r => setTimeout(r, 900));
    await route.continue().catch(() => undefined);
  });

  const textarea = page.locator('.composer-box textarea');
  await textarea.fill('first slow message');
  await page.locator('.composer .send-btn:not(.stop)').click();
  // User immediately sees the message in timeline and starts typing the next message while POST is in flight.
  await page.locator('.row.user', { hasText: 'first slow message' }).waitFor({ timeout: 500 });
  assert.equal(await textarea.inputValue(), '');
  await textarea.fill('next draft while sending');

  await page.locator('.row.user:not([data-optimistic])', { hasText: 'first slow message' }).waitFor({ timeout: 10_000 });
  assert.equal(await textarea.inputValue(), 'next draft while sending', 'only the new draft remains; old sent text never sticks');

  await context.close();
});

test('failed first turn on a new thread rolls back to new thread, restores draft, and removes orphan thread', async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const page = await context.newPage();
  await openUi(page, daemon);

  const beforeThreads = await (await api(daemon, '/api/threads')).json();
  await page.route('**/api/threads/*/turns', async (route) => {
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'simulated failure' }) });
  });

  const textarea = page.locator('.composer-box textarea');
  await textarea.fill('must not become orphan');
  await page.locator('.composer .send-btn:not(.stop)').click();

  // Rolls back to the new-thread screen and restores the exact draft text in the composer.
  await page.locator('.empty').waitFor({ timeout: 5000 });
  await page.waitForFunction(() => document.querySelector('.composer-box textarea')?.value === 'must not become orphan', { timeout: 5000 });

  // Wait briefly for DELETE /api/threads/:id cleanup and confirm no orphan thread remains on daemon.
  await page.waitForTimeout(400);
  const afterThreads = await (await api(daemon, '/api/threads')).json();
  assert.equal(afterThreads.length, beforeThreads.length, 'orphan 0-turn thread must be cleaned up on first-turn failure');

  await context.close();
});

test('mobile touch: optimistic new-thread send, IME blur without compositionend never deadlocks, and composer blurs after send', async () => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  await openUi(page, daemon);

  await page.route('**/api/threads', async (route) => {
    if (route.request().method() === 'POST') await new Promise(r => setTimeout(r, 700));
    await route.continue().catch(() => undefined);
  });

  const textarea = page.locator('.composer-box textarea');
  await textarea.tap();
  await textarea.fill('Mobile optimistic send test');

  // Simulate an IME that enters compositionstart and commits silently on blur without firing compositionend.
  await textarea.evaluate((el) => {
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: 'test' }));
  });
  await page.locator('.composer .send-btn:not(.stop)').tap();

  // Fallback timer (120ms) unblocks IME and sends; UI leaves empty state well before the 700ms POST returns.
  await page.locator('.row.user', { hasText: 'Mobile optimistic send test' }).waitFor({ timeout: 600 });
  assert.equal(await page.locator('.empty').count(), 0);

  // Wait for confirmation & reply; on touch devices the textarea should not force-refocus after send.
  await page.locator('.row.assistant .markdown').first().waitFor({ timeout: 15_000 });
  const activeTag = await page.evaluate(() => document.activeElement?.tagName ?? '');
  assert.notEqual(activeTag, 'TEXTAREA', 'mobile send must not force-refocus textarea after sending');

  await context.close();
});
