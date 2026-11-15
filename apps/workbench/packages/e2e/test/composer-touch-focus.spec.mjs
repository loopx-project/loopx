import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { openCollaboration, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('touching collaboration keeps the empty composer mounted when taps do not focus buttons', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const post = async (path, body) => {
    const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(response.ok, await response.clone().text());
    return response.json();
  };
  try {
    for (const mobile of [true, false]) for (const colorScheme of ['light', 'dark']) {
      const title = `协作点击 ${mobile}-${colorScheme}`;
      const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
      await post(`/api/threads/${thread.id}/coordination`, { directories: [daemon.workdir] });
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-row').filter({ hasText: title }).click();
        // Model the no-native-button-focus path: pressing a button blurs the previous field,
        // but does not focus the button. Chromium's normal touch behaviour misses this case.
        if (mobile) await page.evaluate(() => {
          document.addEventListener('mousedown', event => {
            const button = event.target.closest?.('.composer button');
            if (!button) return;
            event.preventDefault();
            if (document.activeElement !== button && document.activeElement instanceof HTMLElement) document.activeElement.blur();
          }, true);
        });
        const input = page.locator('.composer textarea');
        const panel = page.getByRole('menu', { name: '协作', exact: true });
        const tap = async locator => mobile ? locator.tap() : locator.click();
        for (const running of [false, true]) {
          if (running) await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
          await input.focus();
          await openCollaboration(page);
          await page.waitForTimeout(300);
          assert.ok(await panel.isVisible(), 'the menu survives blur and pointer release');
          assert.equal(await page.locator('.composer-box.collapsed').count(), 0);
          await tap(panel.locator('.coord-picker-head button'));
          await page.waitForTimeout(100);
          assert.ok(await panel.locator('.coord-setup').isVisible(), 'settings remain usable with the same focus behaviour');
          if (process.env.CALLI_E2E_SHOTS_DIR) {
            mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
            await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `collab-${mobile ? 'phone' : 'desktop'}-${colorScheme}-${running ? 'running' : 'idle'}.png`) });
          }
          if (mobile) await page.locator('.topbar').tap({ position: { x: 150, y: 3 } });
          else await page.keyboard.press('Escape');
          await panel.waitFor({ state: 'detached' });
          // Ordinary focus leaving the composer still folds it; draft text still keeps it open.
          await input.focus();
          await input.blur();
          if (mobile) await page.locator('.composer-box.collapsed').waitFor();
          await input.fill('保留草稿');
          await input.blur();
          assert.equal(await page.locator('.composer-box.collapsed').count(), 0);
          await input.fill('');
          if (running) await post(`/api/threads/${thread.id}/cancel`, {});
        }
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
