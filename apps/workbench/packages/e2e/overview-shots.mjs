// Run npm run build from the repository root before capturing screenshots.
// Captures the overview page for eyeballing: desktop and phone, light and dark, in demo mode.
// Run: node overview-shots.mjs (writes PNGs into shots/)
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser } from './harness.mjs';

const outDir = join(process.cwd(), 'shots');
mkdirSync(outDir, { recursive: true });

// The daemon only serves the page; the demo workspace never calls it.
const daemon = await startDaemon();
const browser = await launchBrowser();
try {
  for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    const page = await (await browser.newContext({ viewport, isMobile: name === 'phone', hasTouch: name === 'phone' })).newPage();
    await page.goto(daemon.base + '/');
    // On a phone the demo bar lives in the pulled-away list, so the page itself is the landmark.
    await page.locator('.activity-page, .composer').first().waitFor({ timeout: 15_000 });
    await page.evaluate(() => window.location.assign('#/activity'));
    await page.locator('.activity-page').waitFor({ timeout: 10_000 });
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
      await page.waitForTimeout(300);
      await page.screenshot({ path: join(outDir, `overview-${name}-${theme}.png`), fullPage: name === 'phone' });
    }
    // The system prompt dialog, on the conversation that has one.
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
    await page.locator('.activity-sys-btn').first().click();
    await page.locator('.activity-sys-body pre').waitFor({ timeout: 10_000 });
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(outDir, `overview-${name}-dialog.png`) });
    await page.keyboard.press('Escape');
    // One turn as a trace: the ask open, then a tool opened to its input and output.
    await page.locator('.activity-ask', { hasText: '商品列表' }).click();
    await page.locator('.trace-step.user .trace-text').waitFor({ timeout: 10_000 });
    await page.locator('.trace-step.tool', { hasText: 'npm test' }).locator('.trace-step-head').click();
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
      await page.waitForTimeout(300);
      await page.screenshot({ path: join(outDir, `overview-${name}-trace-${theme}.png`) });
    }
    await page.keyboard.press('Escape');
    await page.close();
  }
} finally {
  await browser?.close();
  await daemon?.stop();
}
console.log('shots written to', outDir);
