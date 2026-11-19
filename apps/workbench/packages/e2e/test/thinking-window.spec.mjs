// A thought being written must not move the page: the live thinking window holds its height and
// place while the stream writes, following its newest lines inside itself (the way the streaming
// plan reads), and the scroller's own height stays put with the view pinned to the bottom.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const readState = (page) =>
  page.evaluate(() => {
    const win = document.querySelector('.thinking-live');
    const scroller = document.querySelector('.scroller');
    return {
      win: win ? (({ top, height }) => ({ top, height }))(win.getBoundingClientRect()) : null,
      pageHeight: scroller.scrollHeight,
      dist: Math.round(scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight),
      tail: win ? (win.textContent ?? '').slice(-40) : '',
      faded: !!win?.classList.contains('overflow'),
    };
  });

for (const [name, viewport] of [['desktop', { width: 1280, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
  test(`a long thought streams without moving the page (${name})`, async () => {
    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    page.setDefaultTimeout(15_000);
    try {
      await openUi(page, daemon);
      if (viewport.width < 900) await page.locator('.menu-btn').click();
      const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
      await page.locator('.thread-row', { hasText: 'New thread' }).first().click();
      await page.waitForTimeout(600);
      await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'longthink 稳定性' }) });

      await page.locator('.thinking-live').waitFor();
      // The turn's rows settle in the first moments (the waiting row leaves, the work group opens);
      // what must hold still is the stream writing after that.
      await page.waitForTimeout(500);
      const samples = [];
      const start = Date.now();
      while (Date.now() - start < 2500) {
        samples.push(await readState(page));
        await page.waitForTimeout(150);
      }

      const withWin = samples.filter((s) => s.win);
      assert.ok(withWin.length >= 8, `${withWin.length} samples saw the live window`);
      // One height, one place, one page height: the window follows the stream inside itself.
      const height = Math.round(withWin[0].win.height);
      for (const s of withWin) {
        assert.equal(Math.round(s.win.height), height, 'window height must not change while streaming');
        assert.equal(Math.round(s.win.top), Math.round(withWin[0].win.top), 'window must not move while streaming');
        assert.equal(s.pageHeight, withWin[0].pageHeight, 'page height must not change while streaming');
        assert.equal(s.dist, 0, 'the view stays pinned to the bottom');
      }
      // The text moved on inside it, and the window cut into what scrolled by.
      assert.ok(new Set(withWin.map((s) => s.tail)).size >= 3, 'the window follows the newest lines');
      assert.ok(withWin.some((s) => s.faded), 'the top fades once the window overflows');

      // Done: the turn folds to its "用时" line and the window gives way to the one-line
      // "思考 · X 秒" record of the thought.
      await page.locator('.work-toggle', { hasText: '用时' }).first().waitFor({ timeout: 15_000 });
      await page.locator('.thinking-live').waitFor({ state: 'detached', timeout: 5_000 });
    } finally {
      await ctx.close();
    }
  });
}
