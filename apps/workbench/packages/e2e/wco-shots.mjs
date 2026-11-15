// Run `npm run build` from the repository root before capturing.
// The installed app can draw the window's own controls over the page (window-controls-overlay),
// and every panel that fills the window then has to keep its own controls out from under the
// system's. Playwright cannot put a window in that display mode — the CDP media emulation ignores
// the feature — so the stylesheet the page loads has its media gate rewritten (`all` for the
// window-controls-overlay condition; the declarations are the shipped ones), and the window areas
// a desktop browser would report through env() are supplied as variables. Each panel's buttons
// are then measured against the band the system draws its buttons in. Shots land in shots/wco/.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from './harness.mjs';

const outDir = join(process.cwd(), 'shots', 'wco');
mkdirSync(outDir, { recursive: true });
// What a Chrome window reports with its controls drawn over the page: they own the top-right
// band, and the draggable title row is 40px tall.
const WCO = { left: 8, right: 138, row: 40 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const daemon = await startDaemon();
const browser = await launchBrowser();
const post = async (path, body) => {
  const res = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
};
const get = async (path) => {
  const res = await api(daemon, path);
  if (!res.ok) throw new Error(`${path}: ${await res.text()}`);
  return res.json();
};
/** Idle that stays idle: a member's result can start another turn of the lead's right after the
 * first one ends, and the shots want the conversation settled. */
const idle = async (threadId) => {
  for (let i = 0; i < 200; i++) {
    const t = await get(`/api/threads/${threadId}`);
    if (t.status !== 'running') {
      await sleep(400);
      if ((await get(`/api/threads/${threadId}`)).status !== 'running') return t;
    } else await sleep(100);
  }
  throw new Error(`thread ${threadId} is still running`);
};
const turn = async (threadId, body) => {
  await post(`/api/threads/${threadId}/turns`, body);
  return idle(threadId);
};

/** A context in the window-controls-overlay look: the shipped rules ungated, the window areas set. */
const wcoContext = async (width, height, colorScheme = 'light') => {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme });
  await context.route('**/*.css', async (route) => {
    const res = await route.fetch();
    // The whole condition goes, not just the feature name: `@media (all)` would be a feature named
    // all, matching nothing — `@media all` is the type, matching everything.
    const css = await res.text();
    await route.fulfill({ response: res, body: css.replaceAll('(display-mode: window-controls-overlay)', 'all') });
  });
  // html:root outranks the shipped :root wherever the style lands in the tree. At the document's
  // start the root element may not exist yet, so the style waits for the parse if it has to — the
  // variables are read live, and every check below runs long after the load.
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
};

/** A bar read against the window: how tall, where its buttons sit, which parts drag. */
const readBar = (page, barSel) => page.evaluate(({ barSel, band }) => {
  const bar = document.querySelector(barSel);
  if (!bar) return null;
  const regionOf = (el) => {
    const cs = getComputedStyle(el);
    return cs.getPropertyValue('app-region') || cs.getPropertyValue('-webkit-app-region');
  };
  const r = bar.getBoundingClientRect();
  const buttons = [...bar.querySelectorAll('button, a, input')].map((el) => {
    const b = el.getBoundingClientRect();
    return {
      label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 16),
      // In the band the system draws its own buttons in: overlapping it is the bug.
      inBand: b.right > innerWidth - band.right && b.top < band.row,
      region: regionOf(el),
    };
  });
  return {
    height: r.height, top: r.top, width: r.width,
    region: regionOf(bar), padRight: getComputedStyle(bar).paddingRight,
    buttons,
  };
}, { barSel, band: WCO });
const checkBar = (name, read, wantHeight) => {
  if (!read) throw new Error(`${name}: not on the page`);
  if (Math.round(read.height) !== wantHeight) throw new Error(`${name}: ${read.height}px tall, wanted ${wantHeight}px`);
  if (read.top > 1) throw new Error(`${name}: starts ${read.top}px down, wanted the window's top edge`);
  if (read.region !== 'drag') throw new Error(`${name}: the empty bar is "${read.region}", wanted a draggable one`);
  for (const b of read.buttons) {
    if (b.inBand) throw new Error(`${name}: the "${b.label}" button sits under the window controls`);
    if (b.region !== 'no-drag') throw new Error(`${name}: the "${b.label}" button is "${b.region}", wanted no-drag`);
  }
};

try {
  // A thread with a team (so the agents panel has a member waiting on the person), an edited file,
  // a written report and a chart: the overview's change row opens the preview, its picture opens
  // the lightbox, its footer opens the trace, and a member row opens the agents panel.
  const spawn = (name, key, task) => `CALLI_COMMAND:${Buffer.from(JSON.stringify({
    args: ['spawn', '--agent', 'claude', '--name', name, '--message-file', '-', '--request-id', key],
    text: task,
  })).toString('base64')}`;
  const thread = await post('/api/threads', { agent: 'claude', cwd: daemon.workdir, title: '周报与图表' });
  await turn(thread.id, {
    text: '请审查接口文档',
    mentions: [{ id: 'claude', kind: 'agent', label: 'Claude Code', task: '复核报告里的数字' }],
  });
  await post(`/api/threads/${thread.id}/coordination`, { action: 'permission', permission: 'ask' });
  await turn(thread.id, { text: spawn('配色检查', 'colors', '核对图表中的颜色 approve') });
  await turn(thread.id, { text: 'edit file, plan, make outputs' });

  const openThread = async (context) => {
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    await openUi(page, daemon);
    // A window this narrow keeps its thread list in a drawer behind the conversation.
    if (context._options.viewport.width < 900) await page.locator('.menu-btn').click();
    await page.locator('.thread-row', { hasText: '周报与图表' }).click();
    return page;
  };
  const shot = (page, name) => page.screenshot({ path: join(outDir, `${name}.png`) });

  // The preview: its bar is the window's title bar, tall as it, its three buttons clear of the
  // system's. Light and dark.
  for (const look of ['light', 'dark']) {
    const context = await wcoContext(1440, 900, look);
    const page = await openThread(context);
    // The standing panel is open already; only a phone opens its sheet from the button.
    await page.locator('.overview-panel').waitFor();
    const report = page.locator('.overview-rowwrap', { hasText: 'report.html' }).first();
    await report.locator('.overview-row').hover();
    await report.locator('.overview-preview').click();
    await page.locator('.preview-frame').waitFor();
    checkBar('the preview bar', await readBar(page, '.preview-bar'), WCO.row);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(250);
    await shot(page, `preview-1440-${look}`);
    if (look === 'light') {
      // The lightbox over the same window: its close sits a row down and left of the system's.
      await page.locator('.preview-bar button').last().click();
      // The dock stayed behind the preview, so the picture is one click away.
      await page.locator('.overview-picture').first().waitFor();
      await page.evaluate(() => document.querySelector('.overview-picture').click());
      await page.locator('.lightbox-close').waitFor();
      const close = await page.evaluate(() => {
        const r = document.querySelector('.lightbox-close').getBoundingClientRect();
        return { top: r.top, right: r.right, shown: Boolean(document.querySelector('.lightbox')) };
      });
      if (!close.shown) throw new Error('the picture did not open the lightbox');
      if (close.top < WCO.row) throw new Error(`the lightbox close sits ${close.top}px down, inside the title row`);
      if (close.right > 1440 - WCO.right) throw new Error(`the lightbox close ends ${close.right}px over, inside the system buttons`);
      await page.waitForTimeout(250);
      await shot(page, 'lightbox-1440-light');
      // The overview page's trace dialog: centred lower, its head clear of the row.
      await page.evaluate(() => document.querySelector('.lightbox-close').click());
      await page.evaluate(() => { window.location.hash = '#/activity'; });
      await page.locator('.activity-ask').first().waitFor();
      await page.locator('.activity-ask').first().click();
      await page.locator('.trace-panel').waitFor();
      const panel = await page.evaluate(() => {
        const el = document.querySelector('.trace-panel');
        const r = el.getBoundingClientRect();
        const x = el.querySelector('.panel-head button, .dialog-head button, button');
        const b = x.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, closeTop: b.top, closeRight: b.right };
      });
      if (panel.top < WCO.row) throw new Error(`the trace dialog starts ${panel.top}px down, inside the title row`);
      if (panel.bottom > 900) throw new Error(`the trace dialog runs to ${panel.bottom}, past the window`);
      if (panel.closeTop < WCO.row) throw new Error('the trace dialog close sits inside the title row');
      await page.waitForTimeout(250);
      await shot(page, 'trace-dialog-1440-light');
    }
    await page.close();
    await context.close();
  }

  // The whole app under the same window, for the look of it: the top bar its own title bar.
  {
    const context = await wcoContext(1440, 900, 'light');
    const page = await openThread(context);
    await page.waitForTimeout(250);
    await shot(page, 'app-1440-light');
    await page.close();
    await context.close();
  }

  // A window the width of a phone's: the panels that cover it whole — files (with the writing page
  // inside), the trace page, the agents panel — grow their heads to the title bar the same way.
  {
    const context = await wcoContext(390, 844, 'light');
    const page = await openThread(context);
    await page.locator('.topbar-files').click();
    await page.locator('.files-panel.phone').waitFor();
    await page.waitForTimeout(400); // the drawer's slide still shifts fixed panels while it runs
    checkBar('the files page head', await readBar(page, '.files-panel.phone .files-head'), 54);
    await page.waitForTimeout(250);
    await shot(page, 'files-phone-390-light');
    await page.locator('.files-panel.phone .files-head button').first().click();
    await page.locator('.files-panel.phone').waitFor({ state: 'detached' });
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await page.locator('.overview-trace').click();
    await page.locator('.trace-side.phone').waitFor();
    await page.waitForTimeout(400); // the drawer's slide still shifts fixed panels while it runs
    checkBar('the trace page head', await readBar(page, '.trace-phone-head'), 56);
    await page.waitForTimeout(250);
    await shot(page, 'trace-phone-390-light');
    await page.locator('.trace-phone-head button').first().click();
    await page.locator('.trace-side.phone').waitFor({ state: 'detached' });
    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel').waitFor();
    await page.locator('.overview-member').first().click();
    await page.locator('.agent-panel').waitFor();
    await page.waitForTimeout(400); // the drawer's slide still shifts fixed panels while it runs
    const head = await readBar(page, '.agent-panel-head');
    if (!head) throw new Error('the agents panel head is not on the page');
    if (head.top > 1) throw new Error(`the agents panel head starts ${head.top}px down, wanted the top edge`);
    if (head.height < WCO.row) throw new Error(`the agents panel head is ${head.height}px tall, under the ${WCO.row}px title row`);
    if (head.region !== 'drag') throw new Error(`the agents panel head is "${head.region}", wanted a draggable one`);
    for (const b of head.buttons) {
      if (b.inBand) throw new Error(`the agents panel's "${b.label}" button sits under the window controls`);
      if (b.region !== 'no-drag') throw new Error(`the agents panel's "${b.label}" button is "${b.region}", wanted no-drag`);
    }
    await page.waitForTimeout(250);
    await shot(page, 'agents-phone-390-light');
    await page.close();
    await context.close();
  }

  console.log(`shots written to ${outDir}`);
} finally {
  await browser.close();
  await daemon.stop();
}
