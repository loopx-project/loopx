import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

for (const provider of [
  { id: 'claude', label: 'Claude Code', field: '验证码', code: 'test-code#test-state', email: 'new@example.com', url: /^https:\/\/claude.com\/cai\/oauth\/authorize\?/ },
  { id: 'agy', label: 'Antigravity', field: '授权码或回跳地址', code: 'https://antigravity.google/oauth-callback?code=test-agy-code&state=fixture', email: 'agy-new@example.com', url: /^https:\/\/accounts.google.com\/o\/oauth2\/auth\?/ },
]) test(`${provider.id}: independent account creation and code login on desktop and phone, light and dark`, { timeout: 120_000 }, async () => {
  const source = mkdtempSync(join(tmpdir(), 'loochat-e2e-account-source-'));
  writeFileSync(join(source, 'CLAUDE.md'), 'Shared instructions');
  writeFileSync(join(source, '.credentials.json'), 'existing account');
  const fixture = fileURLToPath(new URL('../../daemon/test/fixtures/fake-claude-auth.mjs', import.meta.url));
  const config = join(source, '.gemini', 'antigravity-cli');
  mkdirSync(config, { recursive: true });
  writeFileSync(join(config, 'antigravity-oauth-token'), 'existing agy account');
  const daemon = await startDaemon({
    LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id: provider.id, adapter: provider.id, command: [`fake-${provider.id}`], env: provider.id === 'agy' ? { HOME: source, LOOCHAT_AGY_HOME: config } : { CLAUDE_CONFIG_DIR: source, LOOCHAT_CLAUDE_BIN: fixture } }] }),
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
        page.on('pageerror', error => errors.push(String(error)));
        const mode = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
        const shot = async name => {
          if (!process.env.LOOCHAT_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `accounts-${provider.id}-${mode}-${name}.png`) });
        };
        await openUi(page, daemon);
        await page.evaluate(() => { location.hash = '#/agents'; });
        await page.locator('.agents-page').waitFor();
        await page.getByRole('button', { name: '添加 agent', exact: true }).click();
        await page.locator('.agent-account-dialog .choice-row').filter({ hasText: provider.label }).click();
        const name = `${provider.label} ${mode}`;
        await page.getByLabel('名称（选填）').fill(name);
        await shot('add');
        await page.getByRole('button', { name: '添加账号', exact: true }).click();
        await page.locator('.agent-account-dialog').waitFor({ state: 'detached' });
        const row = page.locator('.agents-row').filter({ hasText: name });
        await row.getByText('待登录', { exact: false }).waitFor();
        await shot('list');
        await row.getByRole('button', { name: '登录', exact: true }).click();
        const link = page.getByRole('link', { name: '打开登录链接' });
        await link.waitFor();
        assert.match(await link.getAttribute('href'), provider.url);
        assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
        await shot('login');
        const box = await page.locator('.agent-login-dialog').boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= (mobile ? 390 : 1440));
        assert.ok(box.y >= 0 && box.y + box.height <= (mobile ? 844 : 1000));
        assert.ok(await page.getByRole('button', { name: '提交验证码' }).isDisabled());
        if (!mobile && colorScheme === 'dark') {
          await page.route('**/api/agents/*/login/*', route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'login not found' }) }), { times: 1 });
          await page.getByText('登录链接已过期，请重新登录。').waitFor();
          await page.getByRole('button', { name: '重试', exact: true }).click();
          await link.waitFor();
        }
        if (mobile && colorScheme === 'dark') {
          await page.getByLabel(provider.field, { exact: true }).fill('wrong');
          await page.getByRole('button', { name: '提交验证码' }).click();
          await page.getByText('登录未成功，请重试并使用新链接获取验证码。').waitFor();
          await shot('failed');
          await page.getByRole('button', { name: '重试', exact: true }).click();
          await link.waitFor();
        }
        await page.getByLabel(provider.field, { exact: true }).fill(provider.code);
        await page.getByRole('button', { name: '提交验证码' }).click();
        await page.getByText('登录成功，可以开始新会话了。').waitFor();
        await shot('success');
        await page.getByRole('button', { name: '关闭', exact: true }).click();
        await row.getByText(provider.email).waitFor();
        await row.getByRole('button', { name: '登录', exact: true }).click();
        await link.waitFor();
        await page.getByRole('button', { name: '取消', exact: true }).click();
        await page.locator('.agent-login-dialog').waitFor({ state: 'detached' });
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
    const agents = await (await api(daemon, '/api/agents')).json();
    assert.equal(agents.filter(a => a.account === provider.email).length, 4);
    assert.equal(readFileSync(join(source, '.credentials.json'), 'utf8'), 'existing account');
    assert.equal(readFileSync(join(config, 'antigravity-oauth-token'), 'utf8'), 'existing agy account');
  } finally {
    await browser?.close(); await daemon.stop(); rmSync(source, { recursive: true, force: true });
  }
});
