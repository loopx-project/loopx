// The fork button under the last answer, next to copy: one click copies the whole thread and
// opens the copy, the way Codex does it. It only appears once there is a conversation to fork.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;
// What the fake agent says its sessions hold, so the hub can find a turn in the agent's own record.
const scriptDir = mkdtempSync(join(tmpdir(), 'calli-fork-'));
const messagesFile = join(scriptDir, 'messages.json');

before(async () => {
  daemon = await startDaemon({ FAKE_PI_MESSAGES_FILE: messagesFile });
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  rmSync(scriptDir, { recursive: true, force: true });
});

const forkButton = () => page.locator('.message-actions button[aria-label="分叉"]');

test('a fresh draft has no fork button: there is nothing to fork yet', async () => {
  await openUi(page, daemon);
  await page.locator('.composer textarea').waitFor();
  assert.equal(await forkButton().count(), 0);
});

test('the fork button copies the thread and opens the copy', async () => {
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('hello fork');
  await page.keyboard.press('Enter');
  await page.getByText('hi there hello fork').waitFor({ timeout: 10_000 });

  // The button sits in the row under the answer, with copy and the time the turn ended.
  await forkButton().waitFor();
  assert.match(await page.locator('.message-actions .message-time').innerText(), /^\d\d:\d\d$/);
  await forkButton().click();

  // The copy opens: the title carries the fork mark, and the conversation came with it.
  await page.locator('.topbar-title', { hasText: '（分叉）' }).waitFor({ timeout: 10_000 });
  await page.getByText('hello fork').first().waitFor({ timeout: 5_000 });
  await page.getByText('hi there hello fork').waitFor({ timeout: 5_000 });

  // The copy goes on from where the original stood: it answers as its own session.
  await page.locator('.composer textarea').click();
  await page.keyboard.type('second turn');
  await page.keyboard.press('Enter');
  await page.getByText('hi there second turn').waitFor({ timeout: 10_000 });
  assert.equal((await page.locator('.topbar-title').innerText()).includes('（分叉）'), true);

  // Every finished answer offers a fork: the copy ends with that turn.
  await page.locator('.message-actions').nth(1).waitFor({ timeout: 10_000 }); // the second turn has ended
  assert.equal(await page.locator('.message-actions').count(), 2);
  assert.equal(await forkButton().count(), 2);
});

test('forking under an earlier answer copies the thread up to that turn only', async () => {
  // The thread on screen has two turns. The agent's own record of its session says the same.
  const threads = await (await api(daemon, '/api/threads')).json();
  const source = threads.find((t) => t.title.includes('（分叉）'));
  writeFileSync(
    messagesFile,
    JSON.stringify({
      [source.agentSessionId]: [
        { role: 'user', content: 'hello fork' },
        { role: 'assistant', content: [{ type: 'text', text: 'hi there hello fork' }] },
        { role: 'user', content: 'second turn' },
        { role: 'assistant', content: [{ type: 'text', text: 'hi there second turn' }] },
      ],
    }),
  );

  await forkButton().first().click();
  await page.locator('.topbar-title', { hasText: '（分叉）（分叉）' }).waitFor({ timeout: 10_000 });
  await page.getByText('hi there hello fork').waitFor({ timeout: 5_000 });
  // The second turn stayed behind.
  assert.equal(await page.locator('.row.user').count(), 1);
  assert.equal(await page.getByText('hi there second turn').count(), 0);

  // On the agent's side the copy was forked from the second turn's message, not cloned whole.
  const copy = (await (await api(daemon, '/api/threads')).json()).find((t) => t.title.includes('（分叉）（分叉）'));
  assert.equal(copy.agentSessionId, `${source.agentSessionId}-fork-e2`);
  assert.equal(copy.forkedFrom, source.id);

  // And it carries on from there.
  await page.locator('.composer textarea').click();
  await page.keyboard.type('another way');
  await page.keyboard.press('Enter');
  await page.getByText('hi there another way').waitFor({ timeout: 10_000 });
  assert.equal(await page.locator('.row.user').count(), 2);
});
