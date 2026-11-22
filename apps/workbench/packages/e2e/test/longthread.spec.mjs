// Long threads load older turns in pages; the ruler indexes the entire conversation.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;
let messagesFile;

before(async () => {
  // The daemon's children read this file when asked for a session's messages.
  messagesFile = join(mkdtempSync(join(tmpdir(), 'loochat-e2e-msgs-')), 'messages.json');
  daemon = await startDaemon({ FAKE_PI_MESSAGES_FILE: messagesFile });
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  rmSync(join(messagesFile, '..'), { recursive: true, force: true });
});

test('a long thread folds its past, and the ruler jumps between the turns on screen', async () => {
  // Build a thread over the API: one turn from the web, then 35 the "terminal" added.
  const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
  await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'first from the web' }) });
  const afterTurn = await waitForIdle(made.id);
  const events = await (await api(daemon, `/api/threads/${made.id}/events`)).json();
  const prompt = events.find(event => event.type === 'turn.started').data.text;
  const messages = [{ role: 'user', content: prompt }, { role: 'assistant', content: [{ type: 'text', text: 'noted' }] }];
  for (let i = 1; i <= 35; i++) {
    messages.push({ role: 'user', content: `terminal turn ${i}` });
    messages.push({ role: 'assistant', content: [{ type: 'text', text: `terminal answer ${i}` }] });
  }
  writeFileSync(messagesFile, JSON.stringify({ [afterTurn.agentSessionId]: messages }));
  const synced = await (await api(daemon, `/api/threads/${made.id}/sync`, { method: 'POST' })).json();
  assert.equal(synced.added, 35);

  await openUi(page, daemon);
  await page.locator('.thread-title', { hasText: 'first from the web' }).first().click();

  // The past folds away; the marker says how much.
  const older = page.locator('.older-messages');
  await older.waitFor({ timeout: 10_000 });
  assert.equal(await older.innerText(), '加载更早的消息');
  assert.ok((await page.locator('[data-turn]').count()) < 40);

  // The ruler shows one tick per rendered turn; the current one is the long one.
  await page.locator('.turn-ruler .tick.active').waitFor({ timeout: 10_000 });
  const ticks = await page.locator('.turn-ruler .tick').count();
  assert.equal(ticks, 36, 'the ruler indexes loaded and unloaded turns');

  // Hovering a tick previews the turn; clicking jumps to it.
  await page.locator('.turn-ruler .tick').nth(29).hover();
  await page.locator('.ruler-pop').waitFor({ timeout: 5_000 });
  assert.match(await page.locator('.ruler-pop-q').innerText(), /terminal turn|first from the web/);
  await page.locator('.turn-ruler .tick').nth(29).click();
  await page.waitForTimeout(700); // smooth scroll
  const nearTop = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('[data-turn]')];
    const scroller = document.querySelector('.scroller');
    return rows.some((r) => Math.abs(r.getBoundingClientRect().top - scroller.getBoundingClientRect().top) < 200);
  });
  assert.ok(nearTop, 'a turn row should be near the top after jumping');

  // History loads in pages; the ruler retains the complete index throughout.
  for (let n = 0; n < 4 && await older.count(); n++) {
    const previous = await page.locator('[data-turn]').count();
    await older.click();
    await page.waitForFunction(count => document.querySelectorAll('[data-turn]').length > count, previous);
  }
  assert.equal(await page.locator('[data-turn]').count(), 36);
  assert.equal(await page.locator('.turn-ruler .tick').count(), 36);
  assert.equal(await older.count(), 0);

});

test('short threads show no ruler and no fold', async () => {
  await openUi(page, daemon);
  await page.locator('.sidebar').getByRole('button', { name: '新对话', exact: true }).click();
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('hello');
  await page.keyboard.press('Enter');
  await page.getByText('hi there hello').waitFor({ timeout: 15_000 });
  assert.equal(await page.locator('.turn-ruler').getAttribute('aria-hidden'), 'true');
  assert.equal(await page.locator('.turn-ruler .tick').count(), 0);
  assert.equal(await page.locator('.older-messages').count(), 0);
});

async function waitForIdle(threadId) {
  for (let i = 0; i < 100; i++) {
    const t = await (await api(daemon, `/api/threads/${threadId}`)).json();
    if (t.status !== 'running') return t;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error('thread never went idle');
}
