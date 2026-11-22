import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

const fixture = fileURLToPath(new URL('../../daemon/test/fixtures/fake-oar-grok.mjs', import.meta.url));
const ids = ['cursor', 'codebuddy', 'devin', 'factory', 'grok', 'kimi', 'opencode', 'qoder', 'qwen'];
const names = ['Cursor', 'CodeBuddy Code', 'Devin', 'Factory Droid', 'Kimi Code', 'OpenCode', 'Qoder', 'Qwen Code'];

test('OAR dependencies cannot shadow the existing native Pi on the shipped server path', { timeout: 30000 }, async () => {
  const bin = mkdtempSync(join(tmpdir(), 'native-pi-bin-'));
  const nativeFixture = new URL('../../daemon/test/fixtures/fake-pi.mjs', import.meta.url).href;
  writeFileSync(join(bin, 'pi'), `#!${process.execPath}\nawait import(${JSON.stringify(nativeFixture)});\n`, { mode: 0o755 });
  const serverBin = fileURLToPath(new URL('../../../node_modules/.bin', import.meta.url));
  let daemon;
  try {
    daemon = await startDaemon({
      PATH: `${serverBin}:${bin}:${dirname(process.execPath)}:/usr/bin:/bin`,
      LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: ['pi'] }] }),
    });
    const models = await (await api(daemon, '/api/agents/pi/models')).json();
    assert.deepEqual(models.map(model => model.id), ['fake/think', 'fake/plain', 'fake/loose']);
    const discovered = await (await api(daemon, '/api/agents/discover')).json();
    assert.ok(!discovered.some(agent => agent.command?.includes('@earendil-works/pi-coding-agent')));
  } finally { await daemon?.stop(); rmSync(bin, { recursive: true, force: true }); }
});

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`OAR discovery and native ACP chat: ${mobile ? 'phone' : 'desktop'} ${colorScheme}`, { timeout: 90000 }, async () => {
    const daemon = await startDaemon({ PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'grok', adapter: 'grok', command: [fixture] }] }) });
    const browser = await launchBrowser();
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const shot = async name => {
      if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
      mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `oar-${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
    };
    try {
      const supported = await (await api(daemon, '/api/agents/supported')).json();
      for (const id of ids) assert.ok(supported.some(agent => agent.adapter === id), id);
      await openUi(page, daemon);
      await page.locator('.composer textarea').last().fill('hello');
      await page.locator('.composer .send-btn:not(.stop)').last().click();
      await page.getByText(/Native ACP answer: hello/).first().waitFor({ timeout: 25000 });
      const threads = await (await api(daemon, '/api/threads')).json();
      assert.equal(threads.length, 1);
      assert.equal(threads[0].permissionMode, 'full_access');
      assert.equal(threads[0].agentSessionId, 'public-fixture-session');
      await page.waitForFunction(async ({ base, token, id }) => { const response = await fetch(base + '/api/threads/' + id, { headers: { Authorization: 'Bearer ' + token } }); return (await response.json()).status === 'idle'; }, { base: daemon.base, token: daemon.token, id: threads[0].id });
      await shot('chat');
      await page.goto(daemon.base + '/#/thread/' + threads[0].id, { waitUntil: 'domcontentloaded' });
      await page.getByText(/Native ACP answer: hello/).first().waitFor();
      const detail = await (await api(daemon, '/api/threads/' + threads[0].id)).json();
      assert.equal(detail.resumeCommand, null, 'no unusable native terminal shortcut');
      await page.locator('.composer textarea').last().fill('crash');
      await page.locator('.composer .send-btn:not(.stop)').last().click();
      await page.waitForFunction(async ({ base, token, id }) => {
        const response = await fetch(base + '/api/threads/' + id, { headers: { Authorization: 'Bearer ' + token } });
        return (await response.json()).status === 'error';
      }, { base: daemon.base, token: daemon.token, id: threads[0].id });
      await page.locator('.error-box').last().waitFor();
      await page.locator('.error-box').last().scrollIntoViewIfNeeded();
      await shot('failure');
      await page.locator('.composer textarea').last().fill('recovered');
      await page.locator('.composer .send-btn:not(.stop)').last().click();
      await page.getByText('Native ACP answer: recovered', { exact: true }).waitFor({ timeout: 20000 });
      assert.equal((await (await api(daemon, '/api/threads/' + threads[0].id)).json()).agentSessionId, 'public-fixture-session');
      await page.goto(daemon.base + '/#/agents');
      await page.getByRole('button', { name: '添加 agent', exact: true }).click();
      await page.getByRole('button', { name: '接入已安装的 agent 或已有账号', exact: true }).click();
      const dialog = page.locator('.onboard-dialog-results');
      await dialog.waitFor();
      for (const name of names) await dialog.getByText(name, { exact: true }).waitFor({ timeout: 20000 });
      assert.equal(await dialog.getByRole('link', { name: '安装说明', exact: true }).count(), 7);
      await shot('discovery-first');
      await dialog.getByText('Qwen Code', { exact: true }).scrollIntoViewIfNeeded();
      await shot('discovery-agents');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal overflow');
      assert.deepEqual(errors, []);
    } finally { await context.close(); await browser.close(); await daemon.stop(); }
  });
}
