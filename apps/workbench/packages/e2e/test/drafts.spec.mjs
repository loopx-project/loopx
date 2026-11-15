// What is typed in a thread's box stays with that thread: switching away keeps it, switching back
// brings it back, and other threads start with their own.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

const startThread = async (text) => {
  const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
  await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) });
};

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  page = await ctx.newPage();
  await startThread('first thread');
  await startThread('second thread');
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const box = () => page.locator('.composer textarea');
const open = async (title) => {
  await page.locator('.thread-row', { hasText: title }).click();
  await page.locator('.topbar-title', { hasText: title }).waitFor();
};

test('each thread keeps its own draft', async () => {
  await openUi(page, daemon);
  await open('first thread');
  await box().fill('half a thought for the first');

  await open('second thread');
  assert.equal(await box().inputValue(), '', 'the draft does not follow to another thread');
  await box().fill('something for the second');

  await open('first thread');
  assert.equal(await box().inputValue(), 'half a thought for the first');

  // The plan page has no box; coming back from it, the draft is still there.
  await page.locator('.side-link', { hasText: '计划' }).click();
  await page.locator('.todos-page').waitFor();
  await open('second thread');
  assert.equal(await box().inputValue(), 'something for the second');

  // Words outlast a reload, each in its own thread.
  await page.waitForTimeout(600);
  await page.reload();
  await open('first thread');
  assert.equal(await box().inputValue(), 'half a thought for the first');
  await open('second thread');
  assert.equal(await box().inputValue(), 'something for the second');
});

test('a sent first message does not linger in the next new thread', async () => {
  await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).click();
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await box().fill('third thread');
  await box().press('Enter');
  await page.locator('.topbar-title', { hasText: 'third thread' }).waitFor({ timeout: 10_000 });
  assert.equal(await box().inputValue(), '');

  await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).click();
  assert.equal(await box().inputValue(), '');
});
