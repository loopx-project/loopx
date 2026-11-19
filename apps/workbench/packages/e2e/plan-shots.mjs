// Screenshot the plan-approval card in the demo workspace, desktop and phone, light and dark.
// Run `npm run build` from the repository root before capturing.
// Run: node packages/e2e/plan-shots.mjs before|after   (writes packages/e2e/shots/plan/<phase>/)
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, launchBrowser } from './harness.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const phase = process.argv[2] ?? 'after';
const outDir = join(here, 'shots', 'plan', phase);
mkdirSync(outDir, { recursive: true });

const daemon = await startDaemon();
const browser = await launchBrowser();

const shots = [
  ['desktop-light', { width: 1280, height: 900 }, 'light'],
  ['desktop-dark', { width: 1280, height: 900 }, 'dark'],
  ['mobile-light', { width: 390, height: 844 }, 'light'],
  ['mobile-dark', { width: 390, height: 844 }, 'dark'],
];

for (const [name, viewport, scheme] of shots) {
  const context = await browser.newContext({ viewport, colorScheme: scheme });
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);
  await page.goto(`${daemon.base}/`);
  if (viewport.width < 900) await page.locator('.menu-btn').click();
  await page.locator('.thread-row', { hasText: '商品列表加筛选' }).click();
  await page.waitForTimeout(600);
  const card = page.locator('.proposal').last();
  await card.scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  await card.screenshot({ path: join(outDir, `card-${name}.png`) });
  // The card in conversation context: a band around it.
  await page.mouse.wheel(0, -200);
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(outDir, `thread-${name}.png`) });
  await context.close();
}

await browser.close();
await daemon.stop();
console.log(`wrote ${outDir}`);
