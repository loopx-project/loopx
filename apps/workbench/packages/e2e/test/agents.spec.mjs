// Several agent instances (another account of the same CLI) in the picker, discovery of more
// CLIs on PATH, and the terminal-activity warning that guards a session the terminal also uses.
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
  // Two configured instances (the built-ins plus a second pi account with its own environment)
  // and an extra `agy2` shim on PATH for discovery.
  daemon = await startDaemon({
    CALLI_AGENTS_JSON: JSON.stringify({
      agents: [
        { id: 'claude', adapter: 'claude', command: ['fake-claude'] },
        { id: 'pi', adapter: 'pi', command: ['fake-pi'] },
        { id: 'pi2', adapter: 'pi', label: 'pi 备用', command: ['fake-pi'], env: { FAKE_PI_LABEL: 'pi2' } },
        { id: 'agy', adapter: 'agy', command: ['fake-agy'] },
        { id: 'traex', adapter: 'traex', command: ['traex', 'acp', 'serve'] },
      ],
    }),
    FAKE_PI_MESSAGES_FILE: (messagesFile = join(mkdtempSync(join(tmpdir(), 'calli-e2e-msgs-')), 'messages.json')),
    CALLI_E2E_DISCOVER_AGY2: '1',
  });
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  if (messagesFile) rmSync(join(messagesFile, '..'), { recursive: true, force: true });
});

test('a second account of the same CLI is its own entry, with its own environment', async () => {
  await openUi(page, daemon);
  const agents = await (await api(daemon, '/api/agents')).json();
  assert.deepEqual(
    agents.map((a) => a.id),
    ['claude', 'pi', 'pi2', 'agy', 'traex'],
  );
  assert.equal(agents.find((a) => a.id === 'pi2').label, 'pi 备用');

  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: 'pi 备用' }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('hello');
  await page.keyboard.press('Enter');
  // The instance's environment reaches the CLI it runs: the fake pi introduces itself.
  await page.getByText('[pi2] there hello').waitFor({ timeout: 15_000 });
});

test('a CLI found on PATH can be added from the picker', async () => {
  await page.locator('.new-thread').click();
  await page.locator('.composer .context-btn').first().click();
  const found = page.getByRole('button', { name: /agy2/ });
  await found.waitFor({ timeout: 10_000 });
  await found.click();
  // Adding selects it: the picker's trigger now says agy2, and the agents list has it.
  await page.waitForFunction(() => document.querySelector('.composer .context-btn')?.textContent?.includes('agy2'), null, { timeout: 10_000 });
  const agents = await (await api(daemon, '/api/agents')).json();
  assert.ok(agents.some((a) => a.id === 'agy2' && a.available));
});

test('turns the terminal added show up marked, and sending asks first', async () => {
  await page.locator('.new-thread').click();
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi$/ }).click();
  await page.locator('.composer textarea').click();
  await page.keyboard.type('from the web');
  await page.keyboard.press('Enter');
  await page.getByText('hi there from the web').waitFor({ timeout: 15_000 });

  const threads = await (await api(daemon, '/api/threads')).json();
  const thread = threads.find((t) => t.title === 'from the web');
  writeFileSync(
    messagesFile,
    JSON.stringify({
      [thread.agentSessionId]: [
        { role: 'user', content: 'from the web' },
        { role: 'assistant', content: [{ type: 'text', text: 'hi there from the web' }] },
        { role: 'user', content: 'typed in the terminal' },
        { role: 'assistant', content: [{ type: 'text', text: 'answered in the terminal' }] },
      ],
    }),
  );
  const synced = await (await api(daemon, `/api/threads/${thread.id}/sync`, { method: 'POST' })).json();
  assert.equal(synced.added, 1);

  // The synced turn arrives over SSE with its terminal marker.
  await page.locator('.term-chip').first().waitFor({ timeout: 10_000 });
  await page.getByText('typed in the terminal').waitFor({ timeout: 10_000 });
  // The thread list refresh (and with it the warning flag) trails the stream by a debounce.
  await page.waitForTimeout(600);

  // Sending into that session asks first; cancelling puts the text back; confirming sends.
  await page.locator('.composer textarea').click();
  await page.keyboard.type('back on the web');
  await page.keyboard.press('Enter');
  await page.getByRole('dialog').waitFor({ timeout: 10_000 });
  assert.match(await page.locator('.dialog-title').innerText(), /终端/);
  await page.getByRole('button', { name: '取消' }).click();
  await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === 'back on the web', null, { timeout: 5000 });

  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: '仍然发送' }).click();
  await page.getByText('hi there back on the web').waitFor({ timeout: 15_000 });
});
