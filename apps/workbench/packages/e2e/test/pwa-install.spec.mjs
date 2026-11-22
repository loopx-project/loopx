import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

// Real packaged UI/backend; only browser-owned consent events are simulated. This does not
// certify OS installation on macOS or Windows. Screenshots contain synthetic conversations.
const shots = new URL('../../../../../output/playwright/pwa-install/', import.meta.url).pathname;
async function offer(page, outcome = 'dismissed', fail = false) {
  await page.evaluate(({ outcome, fail }) => {
    const event = new Event('beforeinstallprompt', { cancelable: true });
    event.prompt = () => {
      window.installCalls = (window.installCalls ?? 0) + 1;
      window.installClickedWithActivation = navigator.userActivation.isActive;
      return fail ? Promise.reject(new Error('simulated browser refusal')) : Promise.resolve();
    };
    event.userChoice = Promise.resolve({ outcome });
    window.dispatchEvent(event);
  }, { outcome, fail });
}

test('install entrypoints preserve consent, suppression, recovery and populated layouts', { timeout: 120000 }, async () => {
  mkdirSync(shots, { recursive: true });
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    for (let i = 0; i < 18; i++) await api(daemon, '/api/threads', {
      method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: `Workspace review ${i + 1} — long conversation title for layout verification` }),
    });
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      try {
        // A synthetic expiring credential exercises the other standing footer notice.
        await page.route('**/api/auth/kind', route => route.fulfill({ json: { kind: 'link', linkId: 'layout-fixture', expiresAt: new Date(Date.now() + 3600000).toISOString() } }));
        await openUi(page, daemon);
        await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
        if (!mobile && theme === 'light') {
          const cdp = await context.newCDPSession(page);
          const manifest = await cdp.send('Page.getAppManifest');
          assert.deepEqual(manifest.errors, [], 'the served manifest parses without errors');
          assert.equal(JSON.parse(manifest.data).name, 'LoopX');
          assert.deepEqual((await cdp.send('Page.getInstallabilityErrors')).installabilityErrors, []);
          await cdp.detach();
        }
        if (mobile) await page.getByRole('button', { name: '打开对话列表', exact: true }).click();
        await page.screenshot({ path: join(shots, `before-${mobile ? 'phone' : 'desktop'}-${theme}.png`) });
        await offer(page);
        const promo = page.locator('.pwa-install-promo');
        await promo.waitFor();
        await page.locator('.sidebar-scroll').evaluate(el => { el.scrollTop = el.scrollHeight; });
        const [list, card] = await Promise.all([page.locator('.sidebar-scroll').boundingBox(), promo.boundingBox()]);
        assert.ok(list.y + list.height <= card.y + 1, 'installation does not cover the populated list');
        assert.ok(card.x >= 0 && card.x + card.width <= (mobile ? 390 : 1440), 'card fits viewport');
        const notice = await page.locator('.link-login-notice').boundingBox();
        assert.ok(card.y + card.height <= notice.y + 1, 'installation does not cover the expiring-login notice');
        await page.screenshot({ path: join(shots, `after-${mobile ? 'phone' : 'desktop'}-${theme}.png`) });
        await promo.getByRole('button', { name: '以后再说' }).click();
        await promo.waitFor({ state: 'detached' });
        await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
        await page.locator('.settings-nav').getByRole('button', { name: '安装 LoopX', exact: true }).click();
        const settings = page.getByRole('region', { name: '安装 LoopX', exact: true });
        await settings.getByRole('button', { name: '安装', exact: true }).click();
        assert.equal(await page.evaluate(() => window.installCalls), 1);
        assert.equal(await page.evaluate(() => window.installClickedWithActivation), true);
        await settings.getByText('浏览器尚未提供快捷安装。', { exact: false }).waitFor();
        await page.reload(); await settings.waitFor(); await offer(page);
        assert.equal(await promo.count(), 0, 'dismissal survives reload and renewed offers');
        await offer(page, 'dismissed', true);
        await settings.getByRole('button', { name: '安装', exact: true }).click();
        await settings.getByRole('alert').waitFor();
        await page.screenshot({ path: join(shots, `recovery-${mobile ? 'phone' : 'desktop'}-${theme}.png`) });
        await offer(page, 'accepted');
        await settings.getByRole('button', { name: '安装', exact: true }).click();
        await settings.getByRole('status').filter({ hasText: '已接受安装' }).waitFor();
        await page.evaluate(() => window.dispatchEvent(new Event('appinstalled')));
        await settings.getByRole('status').filter({ hasText: '已安装' }).waitFor();
        assert.equal(await settings.getByRole('button', { name: '安装', exact: true }).count(), 0);
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
