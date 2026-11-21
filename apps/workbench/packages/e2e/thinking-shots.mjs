// Watch a long streaming thought and prove the page holds still while it is written: the live
// thinking window keeps its height and place, the newest text is followed inside it, and the
// scroller's height does not move. Screenshots desktop and phone, light and dark.
// Run `npm run build` from the repository root before capturing.
// Run: node packages/e2e/thinking-shots.mjs [phase]   (writes packages/e2e/shots/thinking/<phase>/)
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser } from './harness.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const phase = process.argv[2] ?? 'after';
const outDir = join(here, 'shots', 'thinking', phase);
mkdirSync(outDir, { recursive: true });

const daemon = await startDaemon();
const browser = await launchBrowser();

const shots = [
  ['desktop-light', { width: 1280, height: 900 }, 'light'],
  ['desktop-dark', { width: 1280, height: 900 }, 'dark'],
  ['mobile-light', { width: 390, height: 844 }, 'light'],
  ['mobile-dark', { width: 390, height: 844 }, 'dark'],
];

const readState = () =>
  page.evaluate(() => {
    const win = document.querySelector('.thinking-live');
    const scroller = document.querySelector('.scroller');
    return {
      win: win ? (({ top, height }) => ({ top, height }))(win.getBoundingClientRect()) : null,
      pageHeight: scroller.scrollHeight,
      dist: Math.round(scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight),
      last: win ? (win.textContent ?? '').slice(-40) : '',
      faded: !!win?.classList.contains('overflow'),
    };
  });

let page; // eslint-disable-line no-unused-vars -- readState closes over the current page
const wanted = process.argv[3];
for (const [name, viewport, scheme] of wanted ? shots.filter((s) => s[0] === wanted) : shots) {
  const context = await browser.newContext({ viewport, colorScheme: scheme });
  page = await context.newPage();
  page.setDefaultTimeout(15_000);
  await openUi(page, daemon);
  if (viewport.width < 900) await page.locator('.menu-btn').click();

  const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
  await page.locator('.thread-row', { hasText: 'New thread' }).first().click();
  await page.waitForTimeout(600);
  await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'longthink 稳定性' }) });

  await page.locator('.thinking-live').waitFor();
  // The turn's rows are still assembling around the first deltas (the work group opens a frame
  // later, the waiting row leaves); let that settle, then measure the stream itself.
  await page.waitForTimeout(500);
  // The stream writes for a few seconds from here. Sample it closely, catching two moments of
  // it — an early sentence and a late one — as screenshots.
  const samples = [];
  let shotEarly = false;
  let shotLate = false;
  const start = Date.now();
  while (Date.now() - start < 3700) {
    samples.push(await readState());
    if (!shotEarly && samples.length === 3) {
      shotEarly = true;
      await page.locator('.thinking').screenshot({ path: join(outDir, `window-early-${name}.png`) });
    }
    if (!shotLate && Date.now() - start > 2600) {
      shotLate = true;
      await page.locator('.thinking').screenshot({ path: join(outDir, `window-late-${name}.png`) });
      await page.screenshot({ path: join(outDir, `thread-${name}.png`) });
    }
    await page.waitForTimeout(160);
  }
  if (!shotEarly || !shotLate) console.log(`${name}: the stream ran quicker than the shots; captured early=${shotEarly} late=${shotLate}`);

  const withWin = samples.filter((s) => s.win);
  const heights = new Set(withWin.map((s) => Math.round(s.win.height)));
  const tops = new Set(withWin.map((s) => Math.round(s.win.top)));
  const pageHeights = new Set(withWin.map((s) => s.pageHeight));
  const texts = new Set(withWin.map((s) => s.last));
  if (withWin.length < 8) throw new Error(`${name}: only ${withWin.length} samples saw the live window`);
  if (heights.size !== 1) throw new Error(`${name}: window height moved while streaming: ${[...heights].join(', ')}`);
  if (tops.size !== 1) throw new Error(`${name}: window moved up or down while streaming: ${withWin.map((s) => s.win.top.toFixed(0)).join(' ')}`);
  if (pageHeights.size !== 1) throw new Error(`${name}: page height moved while streaming: ${[...pageHeights].join(', ')}`);
  if (texts.size < 4) throw new Error(`${name}: the window did not follow the stream (${texts.size} distinct tails)`);
  if (!withWin.some((s) => s.faded)) throw new Error(`${name}: the window never cut into what scrolled by (no fade)`);
  if (!withWin.every((s) => s.dist === 0)) throw new Error(`${name}: the view left the bottom while the thought streamed`);
  console.log(`${name}: held ${[...heights][0]}px through ${withWin.length} samples, ${texts.size} tails followed, fade engaged`);
  await context.close();
}

await browser.close();
await daemon.stop();
console.log(`wrote ${outDir}`);
