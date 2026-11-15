import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(repoRoot, 'daemon', 'test', 'fixtures');

function setupIsolatedBin() {
  const dir = mkdtempSync(join(tmpdir(), 'calli-isolated-bin-'));
  symlinkSync(process.execPath, join(dir, 'node'));
  writeFileSync(
    join(dir, 'agy2'),
    `#!/bin/sh\nexec "${process.execPath}" "${join(fixtures, 'fake-agy.mjs')}" "$@"\n`,
    { mode: 0o755 },
  );
  return {
    dir,
    path: `${dir}:/usr/local/bin:/usr/bin:/bin`,
    cleanup() {
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

test('onboarding discovery dialog in 4 viewport and theme variations', { timeout: 180_000 }, async () => {
  const isolated = setupIsolatedBin();
  let browser;

  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) {
      for (const colorScheme of ['light', 'dark']) {
        const daemon = await startDaemon({
          CALLI_AGENTS_JSON: JSON.stringify({ agents: [] }),
          PATH: isolated.path,
        });

        const context = await browser.newContext({
          viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
          isMobile: mobile,
          hasTouch: mobile,
          colorScheme,
        });

        try {
          const page = await context.newPage();
          const errors = [];
          page.on('pageerror', (error) => errors.push(String(error)));

          await openUi(page, daemon);

          // 1. Initial connect with no agents auto-pops the discovery prompt dialog
          const askDialog = page.locator('.onboard-dialog');
          await askDialog.waitFor({ timeout: 15_000 });
          await askDialog.getByText('发现 coding agent').waitFor();
          // Allow pop-in/sheet-in animation to settle completely before screenshot
          await page.waitForTimeout(300);

          // Screenshot Step 1 (ask dialog) in 4 variations
          if (process.env.CALLI_E2E_SHOTS_DIR) {
            mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
            await page.screenshot({
              path: join(
                process.env.CALLI_E2E_SHOTS_DIR,
                `onboard-ask-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`,
              ),
            });
          }

          // 2. Click "自动发现" to transition to results view
          await page.getByRole('button', { name: '自动发现', exact: true }).click();

          // 3. Results view lists installed and uninstalled agents
          const resultsDialog = page.locator('.onboard-dialog-results');
          await resultsDialog.waitFor({ timeout: 15_000 });
          await page.getByText('本机已安装').waitFor();
          await page.getByText('支持但未安装').waitFor();

          // agy2 is found on PATH and checked by default using custom checkbox
          const agy2Row = page.locator('.onboard-row-installed').filter({ hasText: 'agy2' });
          await agy2Row.waitFor();
          assert.equal(await agy2Row.getAttribute('role'), 'checkbox');
          assert.equal(await agy2Row.getAttribute('aria-checked'), 'true');
          assert.equal(await agy2Row.locator('.onboard-checkbox svg').count(), 1);

          // Verify uninstalled agents: Codex has install guide, TRAE CLI has no hint (only badge)
          const codexRow = page.locator('.onboard-row-uninstalled').filter({ hasText: 'Codex' });
          await codexRow.waitFor();
          assert.equal(await codexRow.locator('.onboard-item-hint').textContent(), 'npm install -g @openai/codex');
          const traeRow = page.locator('.onboard-row-uninstalled').filter({ hasText: 'TRAE CLI' });
          await traeRow.waitFor();
          assert.equal(await traeRow.locator('.onboard-item-hint').count(), 0);
          assert.equal(await traeRow.locator('.onboard-badge').textContent(), '未安装');

          // Test toggling with click
          await agy2Row.click();
          assert.equal(await agy2Row.getAttribute('aria-checked'), 'false');
          const zeroBtn = page.getByRole('button', { name: /连接 0 个/, exact: true });
          assert.equal(await zeroBtn.isDisabled(), true);
          await agy2Row.click();
          assert.equal(await agy2Row.getAttribute('aria-checked'), 'true');

          // Wait for button transition to settle in stable enabled solid primary state
          await page.waitForFunction(() => {
            const btn = document.querySelector('.dialog-actions .primary');
            return btn && !btn.disabled && window.getComputedStyle(btn).opacity === '1';
          });

          // Screenshot Step 2 (results dialog) in 4 variations
          if (process.env.CALLI_E2E_SHOTS_DIR) {
            await page.screenshot({
              path: join(
                process.env.CALLI_E2E_SHOTS_DIR,
                `onboard-results-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`,
              ),
            });
            await page.screenshot({
              path: join(
                process.env.CALLI_E2E_SHOTS_DIR,
                `onboard-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`,
              ),
            });
          }

          // Verify no horizontal overflow
          assert.ok(
            await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
            `no horizontal overflow on ${mobile ? 'mobile' : 'desktop'} ${colorScheme}`,
          );

          // 4. One-click connect adds the checked agents
          const connectBtn = page.getByRole('button', { name: /连接 1 个/, exact: true });
          await connectBtn.waitFor();
          await connectBtn.click();

          // Dialog closes and the new agent appears in the UI
          await resultsDialog.waitFor({ state: 'detached', timeout: 15_000 });
          await page.waitForFunction(() => document.body.textContent?.includes('agy2'), null, { timeout: 15_000 });

          // Verify backend has the new agent configured and available
          const agents = await (await api(daemon, '/api/agents')).json();
          assert.ok(agents.some((a) => a.id === 'agy2' && a.available));

          assert.deepEqual(errors, []);
        } finally {
          await context.close();
          await daemon.stop();
        }
      }
    }
  } finally {
    await browser?.close();
    isolated.cleanup();
  }
});

test('onboarding: clicking later prevents auto-popup after refresh, manual trigger works', { timeout: 60_000 }, async () => {
  const isolated = setupIsolatedBin();
  let daemon;
  let browser;

  try {
    daemon = await startDaemon({
      CALLI_AGENTS_JSON: JSON.stringify({ agents: [] }),
      PATH: isolated.path,
    });

    browser = await launchBrowser();
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      colorScheme: 'light',
    });

    try {
      const page = await context.newPage();
      await openUi(page, daemon);

      // Auto-popup appears
      const askDialog = page.locator('.onboard-dialog');
      await askDialog.waitFor({ timeout: 15_000 });

      // Click "稍后"
      await page.getByRole('button', { name: '稍后', exact: true }).click();
      await askDialog.waitFor({ state: 'detached', timeout: 10_000 });

      // Refresh page
      await page.reload();
      await page.waitForTimeout(1000);

      // Dialog does NOT auto-popup after refresh
      assert.equal(await page.locator('.onboard-dialog').count(), 0);

      // Manual trigger from settings menu (labeled "配置" in zh-CN)
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.getByRole('menuitem', { name: '添加 agent' }).click();

      // Opens directly into results view
      const resultsDialog = page.locator('.onboard-dialog-results');
      await resultsDialog.waitFor({ timeout: 15_000 });
      await page.getByText('本机已安装').waitFor();
      await page.getByText('支持但未安装').waitFor();
    } finally {
      await context.close();
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    isolated.cleanup();
  }
});
