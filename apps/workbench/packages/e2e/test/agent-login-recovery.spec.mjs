import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`signed-out models recover in place: ${mobile ? 'phone' : 'desktop'} ${colorScheme}`, { timeout: 60000 }, async () => {
    const home = mkdtempSync(join(tmpdir(), 'agy-recovery-'));
    const config = join(home, '.gemini', 'antigravity-cli');
    mkdirSync(config, { recursive: true });
    const id = 'agy-secondary';
    const daemon = await startDaemon({ LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id, adapter: 'agy', label: 'Antigravity',
      command: ['fake-agy'], env: { HOME: home, LOOCHAT_AGY_HOME: config, FAKE_AGY_LOGIN_REQUIRED: '1' } }] }) });
    let browser;
    try {
      browser = await launchBrowser();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      const page = await context.newPage();
      const errors = [], starts = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/login')) starts.push(new URL(request.url()).pathname); });
      await openUi(page, daemon);
      const recovery = page.locator('.agent-login-recovery');
      await recovery.getByText('Antigravity 尚未登录', { exact: true }).waitFor();
      assert.equal(await page.locator('.banner').count(), 0, 'signed out is not a raw command error');
      assert.equal(starts.length, 0, 'reading a catalog never starts OAuth');
      const shot = async name => {
        if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
        mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `recovery-${mobile ? 'phone' : 'desktop'}-${colorScheme}-${name}.png`), animations: 'disabled' });
      };
      await shot('signed-out');
      await recovery.getByRole('button', { name: '去登录', exact: true }).click();
      const panel = recovery.locator('.agent-login-panel');
      const link = panel.getByRole('link', { name: '打开登录链接' });
      await link.waitFor();
      assert.equal(new URL(await link.getAttribute('href')).hostname, 'accounts.google.com');
      assert.deepEqual(starts, [`/api/agents/${id}/login`]);
      assert.equal(await page.locator('.dialog-scrim').count(), 0);
      await panel.scrollIntoViewIfNeeded();
      await shot('authorize');
      await panel.getByLabel('授权码或回跳地址').fill('test-agy-code');
      await panel.getByRole('button', { name: '提交验证码' }).click();
      await recovery.waitFor({ state: 'detached' });
      // No picker click or page refresh is allowed to trigger this recovery.
      await page.waitForFunction(id => Object.values(JSON.parse(localStorage.getItem('calli.host.v1') ?? '{}'))
        .some(state => state.models?.[id]?.some(model => model.id === 'fast-low')), id);
      await page.locator('.model-control-trigger').first().click();
      await page.getByText('Fast (Low)', { exact: true }).first().waitFor();
      await shot('models-restored');
      assert.ok(existsSync(join(config, 'antigravity-oauth-token')));
      const agents = await (await api(daemon, '/api/agents')).json();
      assert.equal(agents.find(agent => agent.id === id).account, 'agy-new@example.com');
      assert.deepEqual(errors, []);
      await context.close();
    } finally { await browser?.close(); await daemon.stop(); rmSync(home, { recursive: true, force: true }); }
  });
}
