// Run `npm run build` from the repository root before capturing.
// The app's look at desktop widths: the window frame's surround and the rounded paper (sidebar and
// conversation together), plus the overview panel open and put away, in light, dark and Nord; the
// same window in the window-controls-overlay mode; and a phone, which keeps the old full-bleed
// layout. Writes PNGs into shots/look/<before|after>/.
// Run: node look-shots.mjs before   (or `after`, the default)
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser } from './harness.mjs';

const phase = process.argv[2] ?? 'after';
const outDir = join(process.cwd(), 'shots', 'look', phase);
mkdirSync(outDir, { recursive: true });

const WCO = { left: 8, right: 138, row: 40 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const daemon = await startDaemon();
const browser = await launchBrowser();

/** A window in the window-controls-overlay mode: the shipped rules ungated, the window areas set. */
async function wcoContext(width, height, colorScheme = 'light') {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme });
  await context.route('**/*.css', async (route) => {
    const res = await route.fetch();
    const css = await res.text();
    await route.fulfill({ response: res, body: css.replaceAll('(display-mode: window-controls-overlay)', 'all') });
  });
  await context.addInitScript((w) => {
    const css = `html:root{--titlebar-left:${w.left}px;--titlebar-right:${w.right}px;--titlebar-row:${w.row}px}`;
    const add = () => {
      const style = document.createElement('style');
      style.textContent = css;
      (document.head ?? document.documentElement).append(style);
    };
    if (document.documentElement) add();
    else document.addEventListener('DOMContentLoaded', add, { once: true });
  }, WCO);
  return context;
}

async function contextFor(width, height, look, wco = false) {
  const context = wco ? await wcoContext(width, height, look === 'light' ? 'light' : 'dark')
    : await browser.newContext({ viewport: { width, height }, colorScheme: look === 'light' ? 'light' : 'dark' });
  if (look === 'nord-dark') {
    await context.addInitScript(
      (palette) => localStorage.setItem('calli.appearance', JSON.stringify({ mode: 'dark', palette })),
      'nord',
    );
  }
  return context;
}

async function openThread(context, title) {
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  // No token: the page opens the demo workspace.
  await page.goto(`${daemon.base}/`);
  if (context._options.viewport.width < 900) await page.locator('.menu-btn').click();
  await page.locator('.thread-row', { hasText: title }).click();
  await page.waitForTimeout(700);
  return page;
}

const shot = (page, name) => page.screenshot({ path: join(outDir, `${name}.png`) });

try {
  for (const look of ['light', 'dark', 'nord-dark']) {
    const context = await contextFor(1440, 900, look);
    const page = await openThread(context, '上线前收尾');
    if (!(await page.locator('.overview-panel').count())) await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await page.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    await sleep(400);
    await shot(page, `desktop-1440-${look}-overview`);
    // The same window with the panel put away: the paper, its sidebar and the full column.
    if (await page.locator('.topbar-overview').getAttribute('aria-pressed') === 'true') {
      await page.locator('.topbar-overview').click();
    }
    await page.locator('.overview-panel').waitFor({ state: 'detached' });
    await sleep(450);
    await shot(page, `desktop-1440-${look}-plain`);
    // With the list away the column is centred, and the panel still leaves it room.
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await sleep(450);
    await page.getByRole('button', { name: '隐藏侧边栏' }).first().click();
    await sleep(450);
    await shot(page, `desktop-1440-${look}-no-sidebar`);
    await page.close();
    await context.close();
  }

  // A very wide window: the overview's room must not swallow the lean. The same column with the
  // panel open and put away.
  {
    const context = await contextFor(1920, 1000, 'light');
    const page = await openThread(context, '上线前收尾');
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    await sleep(450);
    await shot(page, 'desktop-1920-light-overview');
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor({ state: 'detached' });
    await sleep(450);
    await shot(page, 'desktop-1920-light-plain');
    await page.close();
    await context.close();
  }

  // The installed window: the paper starts under the system's title-band, which the top bar still
  // fills and drags by.
  {
    const context = await contextFor(1440, 900, 'light', true);
    const page = await openThread(context, '上线前收尾');
    await page.locator('.overview-panel').waitFor();
    await sleep(400);
    await shot(page, 'wco-1440-light');
    await page.close();
    await context.close();
  }

  // A phone keeps the full-bleed layout; the overview is still the bottom sheet.
  for (const look of ['light', 'dark']) {
    const context = await contextFor(390, 844, look);
    const page = await openThread(context, '上线前收尾');
    await shot(page, `phone-390-${look}`);
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await sleep(400);
    await shot(page, `phone-390-${look}-sheet`);
    await page.close();
    await context.close();
  }
} finally {
  await browser.close();
  await daemon.stop();
}
console.log('shots written to', outDir);
