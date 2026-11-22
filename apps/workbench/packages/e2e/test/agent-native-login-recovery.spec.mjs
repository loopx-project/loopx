import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

for (const adapter of ['codex', 'dsh', 'pi']) for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`${adapter} native recovery: ${mobile ? 'phone' : 'desktop'} ${colorScheme}`, { timeout: 60000 }, async () => {
    const home = mkdtempSync(join(tmpdir(), 'native-login-recovery-'));
    const marker = join(home, 'signed-in');
    const id = `${adapter}-secondary`, label = adapter === 'codex' ? 'Codex secondary' : adapter === 'pi' ? 'pi' : 'DeepSeek Harness';
    const fixture = new URL(`../../daemon/test/fixtures/${adapter === 'codex' ? 'fake-codex.mjs' : adapter === 'pi' ? 'fake-pi.mjs' : 'fake-acp.mjs'}`, import.meta.url);
    const cli = join(home, 'native-cli.mjs');
    // The real login-terminal route launches this isolated native-CLI fixture. No browser route mock.
    writeFileSync(cli, `#!${process.execPath}
import { createInterface } from 'node:readline';
import { writeFileSync } from 'node:fs';
process.env.FAKE_LOGIN_FILE = ${JSON.stringify(marker)};
if (process.argv.includes('login')) {
  console.log('Fixture sign-in: enter test-native-code');
  await new Promise(resolve => createInterface({ input: process.stdin }).on('line', code => {
    if (code === 'test-native-code') { writeFileSync(${JSON.stringify(marker)}, 'fixture-authenticated'); resolve(); }
  }));
  process.exit(0);
}
await import(${JSON.stringify(fixture.href)});
`, { mode: 0o755 });
    const daemon = await startDaemon({ LOOCHAT_TERM_SHELL: '/bin/sh', LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{ id, adapter, label,
      command: adapter === 'codex' ? [cli] : [process.execPath, fileURLToPath(fixture)],
      env: { CODEX_HOME: home, FAKE_LOGIN_FILE: marker, FAKE_ACP_FLAVOR: 'dsh', ...(adapter === 'pi' ? { FAKE_PI_EMPTY_MODELS: '1' } : {}) } }] }) });
    let browser;
    try {
      browser = await launchBrowser();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      const page = await context.newPage(), errors = [], starts = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (request.method() === 'POST' && /\/login(?:-terminal)?$/.test(new URL(request.url()).pathname)) starts.push(new URL(request.url()).pathname); });
      await openUi(page, daemon);
      const recovery = page.locator('.agent-login-recovery');
      await recovery.getByText(adapter === 'pi' ? `${label} 暂无可用模型，请检查登录或模型配置` : `${label} 尚未登录`, { exact: true }).waitFor();
      await page.locator('.composer textarea').last().fill('Keep this draft while signing in');
      assert.equal(await page.locator('.banner').count(), 0);
      assert.deepEqual(starts, []);
      if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
        mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `recovery-${adapter}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      }
      if (adapter === 'codex') {
        const login = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/agents/${id}/login-terminal`);
        await recovery.getByRole('button', { name: '去登录', exact: true }).click();
        const { terminalId } = await (await login).json();
        await page.locator('.term-panel').waitFor();
        assert.deepEqual(starts, [`/api/agents/${id}/login-terminal`]);
        assert.equal(existsSync(marker), false, 'opening a terminal is not successful login');
        // Deliver the fixture's approval through the real terminal input API.
        const sent = await api(daemon, `/api/terminals/${terminalId}/input`, { method: 'POST', body: JSON.stringify({ data: 'test-native-code\r' }) });
        assert.ok(sent.ok);
        await recovery.waitFor({ state: 'detached' });
        await page.locator('.term-panel').getByRole('button', { name: '关闭', exact: true }).click();
      } else {
        assert.equal(await recovery.getByRole('button', { name: '去登录', exact: true }).count(), 0);
        await recovery.getByText('请在原生客户端完成登录或配置凭据，然后重试。', { exact: true }).waitFor();
        if (adapter === 'pi') {
          await recovery.getByRole('button', { name: '重试', exact: true }).click();
          await recovery.getByText('仍未获取到可用模型，请检查原生客户端后重试。', { exact: true }).waitFor();
        }
        writeFileSync(marker, 'fixture-authenticated');
        await recovery.getByRole('button', { name: '重试', exact: true }).click();
        await recovery.waitFor({ state: 'detached' });
      }
      assert.equal(await page.locator('.composer textarea').last().inputValue(), 'Keep this draft while signing in');
      await page.waitForFunction(id => Object.values(JSON.parse(localStorage.getItem('calli.host.v1') ?? '{}')).some(state => state.models?.[id]?.length > 0), id);
      assert.deepEqual(errors, []);
      await context.close();
    } finally { await browser?.close(); await daemon.stop(); rmSync(home, { recursive: true, force: true }); }
  });
}
