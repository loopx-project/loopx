// Importing a sidebar session from outside the project roots adds its directory as a root; the
// page reads the roots again, so the new one is there without a reload.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let elsewhere, messagesDir, daemon, browser;
const sessionId = randomUUID();

before(async () => {
  elsewhere = mkdtempSync(join(tmpdir(), 'loochat-e2e-elsewhere-'));
  messagesDir = mkdtempSync(join(tmpdir(), 'loochat-e2e-msgs-'));
  // The session is there before the hub starts, so its first scan finds it.
  const sessions = join(messagesDir, 'pi-agent', 'sessions', 'e2e');
  mkdirSync(sessions, { recursive: true });
  writeFileSync(join(sessions, `e2e_${sessionId}.jsonl`), JSON.stringify({ type: 'session', id: sessionId, cwd: elsewhere }) + '\n' + JSON.stringify({ type: 'session_info', name: '别处的会话' }) + '\n');
  writeFileSync(join(messagesDir, 'messages.json'), JSON.stringify({
    [sessionId]: [{ role: 'user', content: '你好' }, { role: 'assistant', content: [{ type: 'text', text: '在别的目录里' }] }],
  }));
  daemon = await startDaemon({ FAKE_PI_MESSAGES_FILE: join(messagesDir, 'messages.json'), PI_CODING_AGENT_DIR: join(messagesDir, 'pi-agent') });
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
  rmSync(elsewhere, { recursive: true, force: true });
  rmSync(messagesDir, { recursive: true, force: true });
});

test('importing a session from outside the roots adds its directory, and the page shows it', async () => {
  const before = await (await api(daemon, '/api/config')).json();
  assert.ok(!before.workdirs.includes(elsewhere));

  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await openUi(page, daemon);
    const row = page.locator('.thread-row.external', { hasText: '别处的会话' });
    // The scan after startup may still be running when the page first asks; a reload reads it again.
    await row.waitFor({ timeout: 10_000 }).catch(async () => { await page.reload(); await row.waitFor({ timeout: 10_000 }); });
    const reread = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/config' && res.request().method() === 'GET', { timeout: 15_000 });
    await row.locator('.thread-item').click();
    const roots = await (await reread).json();
    assert.ok(roots.workdirs.includes(elsewhere), 'the page reads the roots with the new directory');
    assert.ok((await (await api(daemon, '/api/config')).json()).workdirs.includes(elsewhere));
  } finally {
    await context.close();
  }
});
