// Run npm run build from the repository root before capturing screenshots.
// Captures the session readings where they live now — the overview panel's footer, a faint line
// under the thread's groups — and the in-thread trace they open, on desktop (1440) and phone (390),
// light and dark, against a real daemon driven by the fake pi agent. The demo workspace shows the
// hub-estimated (≈) wording in the same footer.
// Run: node session-stats-shots.mjs (writes PNGs into shots/session-stats/)
import { mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, launchBrowser, openUi } from './harness.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, 'shots', 'session-stats');
mkdirSync(outDir, { recursive: true });

const shot = (page, name, opts = {}) => page.screenshot({ path: join(outDir, `${name}.png`), ...opts });

// A conversation with two finished turns, so the readings have a session to summarize. Plain
// prompts: the fake agent answers them with tool calls and (under FAKE_TELEMETRY) a usage sample.
async function makeTurns(page) {
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /^pi/ }).click();
  for (const ask of ['please one', 'please two']) {
    await page.locator('.composer textarea').click();
    await page.keyboard.type(ask);
    // A phone composer sends with the button; Enter only breaks the line there.
    await page.locator('.composer .send-btn:not(.stop)').click();
    await page.getByText(`hi there ${ask}`).waitFor({ timeout: 30_000 });
  }
  // The readings follow at their own pace: one refresh after the last turn settles.
  await page.waitForTimeout(3_600);
}

/** Opens the overview (a desktop dock opens by itself) and waits for its footer readings. */
async function openOverview(page) {
  if (!(await page.locator('.overview-panel').count())) await page.locator('.topbar-overview').click();
  await page.locator('.overview-panel').waitFor();
  await page.waitForFunction(() => document.querySelector('.overview-readings')?.textContent?.trim(), null, { timeout: 10_000 });
  await page.waitForTimeout(300);
}

/** From the panel footer into the thread's trace; a click that lands mid-render is repeated. */
async function openTrace(page) {
  for (let i = 0; i < 3; i++) {
    await page.locator('.overview-trace').click();
    try {
      await page.locator('.trace-side .ov').waitFor({ timeout: 5_000 });
      // Let the panel's slide-in and the first paint settle, so hovers land on steady bars.
      await page.waitForTimeout(700);
      return;
    } catch {
      if (!(await page.locator('.overview-panel').count())) await openOverview(page);
    }
  }
  throw new Error('the trace panel did not open');
}

const daemon = await startDaemon({ FAKE_TELEMETRY: '1', FAKE_PI_TWO_REQUESTS: '1' });
const browser = await launchBrowser();
try {
  // ---------- Desktop: readings in the overview footer, the trace beside the conversation ----------
  {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    await openUi(page, daemon);
    await makeTurns(page);
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
    await openOverview(page);
    const clip = await panelClip(page, 1440, 900);
    await shot(page, 'desktop-footer-light', { clip });

    // The footer's trace opens the timeline pinned over the steps.
    await openTrace(page);
    await shot(page, 'desktop-trace-light');
    // Hovering a bar says when it began and how it split; clicking selects and opens its step.
    await page.locator('.trace-side .ov-bar.ov-seg').last().hover();
    await page.locator('.ov-tip').waitFor({ timeout: 5_000 });
    await page.waitForTimeout(200);
    await shot(page, 'desktop-trace-hover-light');
    await page.locator('.trace-side .ov-bar.ov-seg').last().click();
    await page.waitForTimeout(700);
    await shot(page, 'desktop-trace-selected-light');
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await page.waitForTimeout(300);
    await shot(page, 'desktop-trace-dark');
    await page.locator('.trace-side .icon-btn[aria-label="关闭"]').click();
    await page.locator('.trace-side').waitFor({ state: 'detached', timeout: 5_000 });
    await openOverview(page);
    await shot(page, 'desktop-footer-dark', { clip });
    await page.close();
  }

  // ---------- Phone: the footer leads into the full-screen trace, whose readings still open ----------
  {
    const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })).newPage();
    await openUi(page, daemon);
    await makeTurns(page);
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
      await page.waitForTimeout(300);
      await openOverview(page);
      await shot(page, `phone-sheet-${theme}`);
      await openTrace(page);
      await shot(page, `phone-trace-${theme}`);
      await page.locator('.trace-side.phone .stats-top .stat').first().click();
      await page.locator('.pop-rows').waitFor({ timeout: 5_000 });
      await page.waitForTimeout(250);
      await shot(page, `phone-stats-sheet-${theme}`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      // Back out of the trace page for the next theme.
      await page.getByRole('button', { name: '返回主线程' }).click();
      await page.locator('.trace-side.phone').waitFor({ state: 'detached', timeout: 5_000 });
    }
    await page.close();
  }

  // ---------- The demo workspace: hub-estimated numbers carry the ≈ wording ----------
  {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    await page.goto(daemon.base + '/');
    await page.locator('.thread-row', { hasText: '修复商品列表' }).click();
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
    await openOverview(page);
    await shot(page, 'demo-footer-light', { clip: await panelClip(page, 1440, 900) });
    await openTrace(page);
    await shot(page, 'demo-trace-light');
    await page.close();
  }
} finally {
  await browser?.close();
  await daemon?.stop();
}
console.log('shots written to', outDir);

/** The panel plus a little air, kept inside the window so the clip is always valid. */
async function panelClip(page, width, height) {
  const panel = await page.locator('.overview-panel').boundingBox();
  const x = Math.max(0, panel.x - 8);
  const y = Math.max(0, panel.y - 8);
  return { x, y, width: Math.min(panel.width + 16, width - x), height: Math.min(panel.height + 16, height - y) };
}
