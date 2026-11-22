import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

for (const adapter of ['agy', 'claude']) for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`${adapter} signed-out models recover in place: ${mobile ? 'phone' : 'desktop'} ${colorScheme}`, { timeout: 60000 }, async () => {
    const home = mkdtempSync(join(tmpdir(), 'agy-recovery-'));
    const config = join(home, '.gemini', 'antigravity-cli');
    mkdirSync(config, { recursive: true });
    const id = `${adapter}-secondary`;
    const label = adapter === 'agy' ? 'Antigravity' : 'Claude Code';
    const credentials = adapter === 'agy' ? join(config, 'antigravity-oauth-token') : join(home, '.credentials.json');
    const fixture = name => fileURLToPath(new URL(`../../daemon/test/fixtures/${name}`, import.meta.url));
    const daemon = await startDaemon({ LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id, adapter, label,
      command: adapter === 'agy' ? ['fake-agy'] : [process.execPath, fixture('fake-acp.mjs')],
      env: { HOME: home, LOOCHAT_AGY_HOME: config, FAKE_AGY_LOGIN_REQUIRED: '1', CLAUDE_CONFIG_DIR: home, FAKE_LOGIN_FILE: credentials, LOOCHAT_CLAUDE_BIN: fixture('fake-claude-auth.mjs') } }] }) });
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
      await recovery.getByText(`${label} 尚未登录`, { exact: true }).waitFor();
      assert.equal(await page.locator('.banner').count(), 0, 'signed out is not a raw command error');
      assert.equal(starts.length, 0, 'reading a catalog never starts OAuth');
      const shot = async name => {
        if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
        mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `recovery-${adapter}-${mobile ? 'phone' : 'desktop'}-${colorScheme}-${name}.png`), animations: 'disabled' });
      };
      await shot('signed-out');
      await recovery.getByRole('button', { name: '去登录', exact: true }).click();
      const panel = recovery.locator('.agent-login-panel');
      const link = panel.getByRole('link', { name: '打开登录链接' });
      await link.waitFor();
      assert.equal(new URL(await link.getAttribute('href')).hostname, adapter === 'agy' ? 'accounts.google.com' : 'claude.com');
      assert.deepEqual(starts, [`/api/agents/${id}/login`]);
      assert.equal(await page.locator('.dialog-scrim').count(), 0);
      await panel.scrollIntoViewIfNeeded();
      await shot('authorize');
      await panel.getByLabel(adapter === 'agy' ? '授权码或回跳地址' : '验证码').fill(adapter === 'agy' ? 'test-agy-code' : 'test-code#test-state');
      await panel.getByRole('button', { name: '提交验证码' }).click();
      await recovery.waitFor({ state: 'detached' });
      // No picker click or page refresh is allowed to trigger this recovery.
      await page.waitForFunction(({ id, model }) => Object.values(JSON.parse(localStorage.getItem('calli.host.v1') ?? '{}'))
        .some(state => state.models?.[id]?.some(item => item.id === model)), { id, model: adapter === 'agy' ? 'fast' : 'big' });
      await page.locator('.model-control-trigger').first().click();
      await page.getByText(adapter === 'agy' ? 'Fast' : 'Big 1.0', { exact: true }).first().waitFor();
      await shot('models-restored');
      assert.ok(existsSync(credentials));
      const agents = await (await api(daemon, '/api/agents')).json();
      assert.equal(agents.find(agent => agent.id === id).account, adapter === 'agy' ? 'agy-new@example.com' : 'new@example.com');
      assert.deepEqual(errors, []);
      await context.close();
    } finally { await browser?.close(); await daemon.stop(); rmSync(home, { recursive: true, force: true }); }
  });
}
