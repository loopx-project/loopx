import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

for (const adapter of ['claude', 'agy']) test(`${adapter}: independent account creation and code login on desktop and phone, light and dark`, { timeout: 120_000 }, async () => {
  const source = mkdtempSync(join(tmpdir(), 'loochat-e2e-account-source-'));
  const config = adapter === 'claude' ? source : join(source, '.gemini', 'antigravity-cli');
  mkdirSync(config, { recursive: true });
  const credentials = join(config, adapter === 'claude' ? '.credentials.json' : 'antigravity-oauth-token');
  writeFileSync(join(config, adapter === 'claude' ? 'CLAUDE.md' : 'GEMINI.md'), 'Shared instructions');
  writeFileSync(credentials, 'existing account');
  if (adapter === 'agy') {
    // Reproduce native optional configuration left as zero-byte or whitespace-only files.
    mkdirSync(join(source, '.gemini', 'config'), { recursive: true });
    writeFileSync(join(source, '.gemini', 'config', 'mcp_config.json'), '');
    writeFileSync(join(config, 'settings.json'), ' \n\t');
  }
  const label = adapter === 'claude' ? 'Claude Code' : 'Antigravity';
  const email = adapter === 'claude' ? 'new@example.com' : 'agy-new@example.com';
  const fixture = fileURLToPath(new URL('../../daemon/test/fixtures/fake-claude-auth.mjs', import.meta.url));
  const daemon = await startDaemon({
    LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: adapter, adapter, command: [adapter === 'claude' ? 'fake-claude' : 'fake-agy'], env: adapter === 'claude' ? { CLAUDE_CONFIG_DIR: source, LOOCHAT_CLAUDE_BIN: fixture } : { HOME: source, LOOCHAT_AGY_HOME: config } }] }),
  });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
        const page = await context.newPage();
        const errors = [];
        let creates = 0;
        page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/api/agents/accounts')) creates++; });
        page.on('pageerror', error => errors.push(String(error)));
        const mode = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
        const shot = async name => {
          if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `accounts-${adapter}-${mode}-${name}.png`), animations: 'disabled' });
        };
        await openUi(page, daemon);
        await page.evaluate(() => { location.hash = '#/agents'; });
        await page.locator('.agents-page').waitFor();
        await page.getByRole('button', { name: '添加 agent', exact: true }).click();
        await page.locator('.agent-account-dialog .choice-row').filter({ hasText: label }).waitFor();
        await shot('choices');
        await page.locator('.agent-account-dialog .choice-row').filter({ hasText: label }).click();
        const name = `${label} ${mode}`;
        await page.getByLabel('名称（选填）').fill(name);
        await shot('add');
        if (mobile && colorScheme === 'dark') {
          await page.route('**/api/agents/*/login', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Fixture login unavailable' }) }), { times: 1 });
        }
        await page.getByRole('button', { name: '添加账号', exact: true }).click();
        await page.locator('.agent-account-dialog').waitFor({ state: 'detached' });
        const row = page.locator('.agents-row').filter({ hasText: name });
        await row.getByText('待登录', { exact: false }).waitFor();
        if (mobile && colorScheme === 'dark') {
          await page.getByText('Fixture login unavailable', { exact: true }).waitFor();
          await shot('start-failed');
          await row.getByRole('button', { name: '登录', exact: true }).click();
        }
        const panel = page.locator('.agent-login-panel');
        const link = panel.getByRole('link', { name: '打开登录链接' });
        await link.waitFor();
        assert.equal(await page.locator('.dialog-scrim').count(), 0, 'inline login must not block the workspace with a modal scrim');
        assert.equal(new URL(await link.getAttribute('href')).hostname, adapter === 'claude' ? 'claude.com' : 'accounts.google.com');
        assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
        await panel.scrollIntoViewIfNeeded();
        await shot('login');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'login does not overflow horizontally');
        const inputBox = await panel.locator('input').boundingBox();
        assert.ok(inputBox.height >= 40, 'mobile stacking preserves the normal input height');
        const box = await panel.boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= (mobile ? 390 : 1440));
        assert.ok(await panel.getByRole('button', { name: '提交验证码' }).isDisabled());
        {
          // Collapsing or navigating away keeps the daemon login session alive so the user can return and paste the code.
          const originalHref = await link.getAttribute('href');
          const originalLogin = (await (await api(daemon, '/api/agents')).json()).find(agent => agent.label === name).activeLogin;
          assert.ok(originalLogin?.id);
          await panel.getByRole('button', { name: '收起', exact: true }).click();
          await panel.waitFor({ state: 'detached' });
          await row.getByText('等待验证码', { exact: true }).waitFor();
          await page.evaluate(() => { location.hash = '#/settings'; });
          await page.locator('.agents-page').waitFor({ state: 'detached' });
          await page.evaluate(() => { location.hash = '#/agents'; });
          await row.getByRole('button', { name: '继续登录', exact: true }).waitFor();
          await page.reload();
          await page.locator('.agents-page').waitFor();
          await row.getByRole('button', { name: '继续登录', exact: true }).click();
          await link.waitFor();
          assert.equal(await link.getAttribute('href'), originalHref, 'resumed login keeps the same session URL');
          const resumedLogin = (await (await api(daemon, '/api/agents')).json()).find(agent => agent.label === name).activeLogin;
          assert.equal(resumedLogin.id, originalLogin.id, 'collapse, navigation and reload must preserve the native login process');
          assert.equal(await page.locator('.dialog-scrim').count(), 0);
          // Explicit cancel terminates the login session.
          await panel.getByRole('button', { name: '取消登录', exact: true }).click();
          await panel.waitFor({ state: 'detached' });
          await row.getByText('待登录', { exact: false }).waitFor();
          await row.getByRole('button', { name: '登录', exact: true }).click();
          await link.waitFor();
        }
        if (!mobile && colorScheme === 'dark') {
          await page.route('**/api/agents/*/login/*', route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'login not found' }) }), { times: 1 });
          await panel.getByText('登录链接已过期，请重新登录。').waitFor();
          await panel.getByRole('button', { name: '重试', exact: true }).click();
          await link.waitFor();
        }
        if (mobile && colorScheme === 'dark') {
          await panel.getByLabel(adapter === 'claude' ? '验证码' : '授权码或回跳地址', { exact: true }).fill('wrong');
          await panel.getByRole('button', { name: '提交验证码' }).click();
          await panel.getByText('登录未成功，请重试并使用新链接获取验证码。').waitFor();
          await shot('failed');
          await panel.getByRole('button', { name: '重试', exact: true }).click();
          await link.waitFor();
        }
        await panel.getByLabel(adapter === 'claude' ? '验证码' : '授权码或回跳地址', { exact: true }).fill(adapter === 'claude' ? 'test-code#test-state' : 'https://antigravity.google/oauth-callback?code=test-agy-code&state=fixture');
        await panel.getByRole('button', { name: '提交验证码' }).click();
        await panel.getByText('登录成功，可以开始新会话了。').waitFor();
        await shot('success');
        await panel.getByRole('button', { name: '关闭', exact: true }).click();
        await row.getByText(email, { exact: false }).waitFor();
        await row.getByRole('button', { name: '登录', exact: true }).click();
        await link.waitFor();
        await panel.getByRole('button', { name: '取消登录', exact: true }).click();
        await panel.waitFor({ state: 'detached' });
        assert.equal(creates, 1, 'retrying or cancelling login must not create another account');
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
    const agents = await (await api(daemon, '/api/agents')).json();
    assert.equal(agents.filter(a => a.account === email).length, 4);
    assert.equal(readFileSync(credentials, 'utf8'), 'existing account');
  } finally {
    await browser?.close(); await daemon.stop(); rmSync(source, { recursive: true, force: true });
  }
});
