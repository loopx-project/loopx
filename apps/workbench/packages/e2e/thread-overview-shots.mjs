// Run `npm run build` from the repository root before capturing.
// Screenshots of the thread overview as a panel from one top-bar button: the demo workspace's
// thread with four members, thirteen changes, a plan, pictures and files — enough for the panel to
// fold its last groups instead of scrolling — then one change's diff dialog, and the same panel as
// a bottom sheet on a phone. Writes PNGs into shots/popover/.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser } from './harness.mjs';

const outDir = join(process.cwd(), 'shots', 'popover');
mkdirSync(outDir, { recursive: true });

const daemon = await startDaemon();
const browser = await launchBrowser();

try {
  const contextFor = async (width, height, look) => {
    const made = await browser.newContext({
      viewport: { width, height },
      colorScheme: look === 'light' ? 'light' : 'dark',
      isMobile: width <= 390,
      hasTouch: width <= 390,
    });
    if (look === 'nord-dark') {
      await made.addInitScript(
        (palette) => localStorage.setItem('calli.appearance', JSON.stringify({ mode: 'dark', palette })),
        'nord',
      );
    }
    return made;
  };
  const openThread = async (context) => {
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    // No token: the page opens the demo workspace, which has the fullest overview.
    await page.goto(`${daemon.base}/`);
    // A phone keeps its thread list in a drawer behind the conversation.
    if (context._options.viewport.width < 900) await page.locator('.menu-btn').click();
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    // The desktop panel stands open by itself; a phone opens its sheet from the button.
    if (context._options.viewport.width < 1240) await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await page.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    // The outputs are read only once the panel is open, after a short debounce.
    await page.locator('.overview-toggle', { hasText: '变更' }).waitFor();
    await page.waitForTimeout(350); // the panel's entrance, so the picture is of the panel at rest
    return page;
  };
  const shot = (page, name) => page.screenshot({ path: join(outDir, `${name}.png`) });

  for (const look of ['light', 'dark', 'nord-dark']) {
    const context = await contextFor(1440, 900, look);
    const page = await openThread(context);
    await shot(page, `desktop-1440-${look}`);
    await page.close();
    await context.close();
  }

  // One change's whole history: the path, its numbers, and the diff each turn left.
  for (const look of ['light', 'dark']) {
    const context = await contextFor(1440, 900, look);
    const page = await openThread(context);
    const changes = page.locator('.overview-group', { has: page.locator('.overview-toggle strong', { hasText: '变更' }) });
    await changes.locator('.overview-row').first().click();
    await page.locator('.change-diff-dialog').waitFor();
    await page.locator('.change-diff-dialog .diff-line').first().waitFor();
    await page.waitForTimeout(250);
    await shot(page, `diff-dialog-1440-${look}`);
    await page.close();
    await context.close();
  }

  for (const look of ['light', 'dark']) {
    const context = await contextFor(390, 844, look);
    const page = await openThread(context);
    await shot(page, `phone-390-${look}`);
    await page.close();
    await context.close();
  }

  console.log(`shots written to ${outDir}`);
} finally {
  await browser.close();
  await daemon.stop();
}
