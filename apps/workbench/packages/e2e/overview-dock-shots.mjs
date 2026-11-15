// Run `npm run build` from the repository root before capturing.
// Screenshots of the overview as a standing panel: open by default beside the conversation, the
// column making room for it; the readings on their own; a thread with nothing yet; the first read
// still under way; the popover a narrow window still gets; and the same panel in the dark.
// Writes PNGs into shots/popover/.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from './harness.mjs';

const outDir = join(process.cwd(), 'shots', 'popover');
mkdirSync(outDir, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Waits for a thread to stop running, and stay stopped. */
async function idle(daemon, threadId) {
  for (let i = 0; i < 300; i++) {
    const thread = await (await api(daemon, `/api/threads/${threadId}`)).json();
    if (thread.status !== 'running') { await sleep(300); return; }
    await sleep(100);
  }
  throw new Error(`thread ${threadId} never settled`);
}

const daemon = await startDaemon({ FAKE_TELEMETRY: '1' });
const browser = await launchBrowser();

const shot = (page, name, opts = {}) => page.screenshot({ path: join(outDir, `${name}.png`), ...opts });
/** The panel with a little air above and around, so the shot shows what it hangs from. */
async function panelClip(page) {
  const panel = await page.locator('.overview-panel').boundingBox();
  return { x: Math.max(0, panel.x - 10), y: Math.max(0, panel.y - 62), width: panel.width + 20, height: panel.height + 72 };
}

try {
  await api(daemon, '/api/threads', {
    method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: daemon.workdir, title: '空线程' }),
  });
  const readings = await (await api(daemon, '/api/threads', {
    method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '只有读数' }),
  })).json();
  await api(daemon, `/api/threads/${readings.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  await idle(daemon, readings.id);
  // The readings follow at their own pace: one refresh after the last turn settles.
  await sleep(4_000);

  // ---------- Desktop 1440: the standing panel and the column it made room for ----------
  for (const look of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: look });
    const page = await context.newPage();
    await page.goto(`${daemon.base}/`);
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    await page.locator('.overview-toggle', { hasText: '变更' }).waitFor();
    await page.waitForTimeout(400); // the panel's entrance and the column's move, at rest
    await shot(page, `overview-dock-1440-${look}`);
    await page.close();
    await context.close();
  }

  // ---------- Readings only: no groups above the footer, no separator ----------
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    const page = await context.newPage();
    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '只有读数' }).click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.locator('.overview-readings').filter({ hasText: '轮' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'overview-dock-readings', { clip: await panelClip(page) });
    await page.close();
    await context.close();
  }

  // ---------- A thread with nothing yet: one faint line, not a blank card ----------
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    const page = await context.newPage();
    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '空线程' }).click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.locator('.overview-empty').filter({ hasText: '还没有产出' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'overview-dock-empty', { clip: await panelClip(page) });
    await page.close();
    await context.close();
  }

  // ---------- The first read, still under way: distinct from truly empty ----------
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    const page = await context.newPage();
    // The outputs request never answers, so the panel stays in its first-read state.
    await page.route('**/api/threads/*/outputs', () => {});
    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '空线程' }).click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.locator('.overview-empty').filter({ hasText: '读取中' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'overview-dock-loading', { clip: await panelClip(page) });
    await page.close();
    await context.close();
  }

  // ---------- Around 1100: the old popover, closed by default ----------
  {
    const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, colorScheme: 'light' });
    const page = await context.newPage();
    await page.goto(`${daemon.base}/`);
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await page.waitForTimeout(500);
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await page.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    await page.waitForTimeout(400);
    await shot(page, 'overview-dock-1100-popover');
    await page.close();
    await context.close();
  }
} finally {
  await browser.close();
  await daemon.stop();
}
console.log('shots written to', outDir);
