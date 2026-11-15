import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

const outputs = (groups = true) => ({
  changes: { files: groups ? Array.from({ length: 8 }, (_, i) => ({ path: `src/change-${i}.ts`, added: i + 1, removed: 0, refs: [] })) : [], added: 36, removed: 0, total: groups ? 8 : 0 },
  images: groups ? Array.from({ length: 8 }, (_, i) => ({ path: `shots/image-${i}.svg`, turnId: 'one', at: '' })) : [],
  files: Array.from({ length: 8 }, (_, i) => ({ path: `docs/report-${i}.html`, turnId: 'one', at: '' })), seq: 1,
});
const geometry = page => page.evaluate(() => {
  const el = document.querySelector('.overview-panel');
  const r = el.getBoundingClientRect();
  const footer = el.querySelector('.overview-foot').getBoundingClientRect();
  return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom, scroll: el.scrollHeight, client: el.clientHeight, footer: footer.top, content: el.querySelector('.overview-content').getBoundingClientRect().bottom, limit: el.querySelector('.thread-overview').dataset.limit };
});
const sameFrame = (before, after) => {
  for (const key of ['x', 'y', 'width', 'height', 'footer']) assert.ok(Math.abs(before[key] - after[key]) < 1, `${key} jumped: ${before[key]} → ${after[key]}`);
};

test('adaptive overview waits at its trigger, fits without scrolling and preserves its frame during refresh', { timeout: 180000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '概况自适应预览' }) })).json();
    for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
      const mobile = width < 640;
      const context = await browser.newContext({ viewport: { width, height: mobile ? 700 : 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      await context.addInitScript(mode => localStorage.setItem('calli.appearance', JSON.stringify({ mode, palette: 'graphite' })), theme);
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      const releases = [];
      const answer = async () => { while (!releases.length) await page.waitForTimeout(10); releases.shift()(); };
      let requests = 0;
      let result = outputs();
      let failed = false;
      const pictures = [];
      await page.route(`**/api/threads/${thread.id}/outputs`, async route => {
        requests++;
        await new Promise(resolve => { releases.push(resolve); });
        await route.fulfill({ status: failed ? 503 : 200, json: failed ? { error: 'unavailable' } : result });
      });
      await page.route(`**/api/threads/${thread.id}/files?*`, async route => {
        pictures.push(new URL(route.request().url()).searchParams.get('path'));
        await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100" height="80" fill="#888"/></svg>' });
      });
      try {
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-row', { hasText: thread.title }).click();
        await page.evaluate(() => {
          window.overviewFrames = [];
          const sample = () => {
            const el = document.querySelector('.overview-panel');
            if (el && getComputedStyle(el).visibility !== 'hidden') { const r = el.getBoundingClientRect(); window.overviewFrames.push({ x: r.x, y: r.y, width: r.width, height: r.height, footer: el.querySelector('.overview-foot').getBoundingClientRect().top }); }
            window.overviewFrame = requestAnimationFrame(sample);
          };
          sample();
        });
        if (mobile) await page.locator('.topbar-overview').click();
        await page.waitForFunction(() => document.querySelector('.topbar-overview')?.getAttribute('aria-busy') === 'true');
        assert.equal(await page.locator('.overview-panel:visible').count(), 0, 'cold load stays at the existing button');
        await answer();
        const panel = page.locator('.overview-panel');
        await panel.locator('.thread-overview[data-ready="true"]').waitFor();
        await page.waitForTimeout(350); // Keep production animations enabled; measure at rest.
        const before = await geometry(page);
        for (const frame of await page.evaluate(() => window.overviewFrames)) sameFrame(before, frame);
        await page.evaluate(() => { window.overviewFrames = []; });
        assert.ok(before.scroll <= before.client + 1, `scrollbar at ${width}: ${JSON.stringify(before)}`);
        assert.ok(before.content <= before.footer + 1, `content overlaps footer: ${JSON.stringify(before)}`);
        assert.ok(before.bottom <= (mobile ? 700 : 1000) - 10, 'all actions stay inside the viewport');
        assert.equal(before.limit, mobile ? '1' : '3');
        assert.equal(pictures.length, mobile ? 1 : 3, 'only previewed images are read');
        if (shots) await page.screenshot({ path: `${shots}/overview-${width}-${theme}.png` });
        // Reopening uses cached output immediately, without showing the cold loading state.
        if (mobile) await page.keyboard.press('Escape'); else await page.locator('.topbar-overview').click();
        await panel.waitFor({ state: 'detached' });
        await page.locator('.topbar-overview').click();
        await panel.locator('.thread-overview[data-ready="true"]').waitFor();
        await page.waitForTimeout(180);
        sameFrame(before, await geometry(page));
        // Refresh status and errors stay inside a reserved footer, even if the data changes shape.
        await panel.locator('.overview-refresh').click();
        await panel.locator('.overview-refresh[aria-busy="true"]').waitFor();
        sameFrame(before, await geometry(page));
        failed = true; await answer();
        await panel.locator('.overview-status.failed').waitFor();
        sameFrame(before, await geometry(page));
        assert.equal(await panel.locator('.overview-rowwrap').count(), mobile ? 2 : 6, 'valid rows survive an error');
        await panel.locator('.overview-refresh').click();
        await panel.locator('.overview-refresh[aria-busy="true"]').waitFor();
        failed = false; result = outputs(false); await answer();
        await panel.locator('.overview-refresh[aria-busy="false"]').waitFor();
        sameFrame(before, await geometry(page));
        for (const frame of await page.evaluate(() => window.overviewFrames)) sameFrame(before, frame);
        await page.evaluate(() => cancelAnimationFrame(window.overviewFrame));
        // Full lists are pages, with search and the existing preview; returning restores the overview.
        const files = panel.locator('.overview-group').last();
        await files.getByRole('button', { name: '查看全部', exact: true }).click();
        const detail = page.locator('.overview-details-page');
        await detail.waitFor();
        assert.equal(await detail.locator('.overview-rowwrap').count(), 8);
        await detail.getByRole('textbox', { name: '搜索名称或路径' }).fill('report-7');
        assert.equal(await detail.locator('.overview-rowwrap').count(), 1);
        if (shots && width !== 320) await page.screenshot({ path: `${shots}/overview-files-${width}-${theme}.png` });
        await detail.locator('.overview-row').hover();
        await detail.locator('.overview-preview').click();
        await page.locator('.preview-scrim').waitFor();
        await page.locator('.preview-bar').getByRole('button', { name: '关闭预览' }).click();
        assert.equal(await detail.locator('input').inputValue(), 'report-7');
        if (mobile) await detail.locator('.overview-details-head button').click();
        else await page.evaluate(() => history.back());
        await detail.waitFor({ state: 'detached' });
        await panel.locator('.thread-overview[data-ready="true"]').waitFor();
        assert.equal(await page.locator('.main > [inert]').count(), 0, 'the conversation is interactive again');
        // The shorter set is naturally sized and shows three real files, rather than a fixed tall frame.
        await page.waitForTimeout(200);
        assert.equal(await panel.locator('.overview-rowwrap').count(), 3);
        assert.ok((await geometry(page)).height < before.height);
        assert.ok(requests >= 3);
        assert.deepEqual(errors, []);
      } finally { releases.forEach(release => release()); await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('cancelled cold opens stay closed; failures can retry, and another thread never sees cached rows', { timeout: 90000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const make = async title => (await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) })).json());
    const first = await make('缓存来源'); const second = await make('另一个线程');
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const releases = [];
    const answer = async () => { while (!releases.length) await page.waitForTimeout(10); releases.shift()(); };
    let failed = true;
    await page.route('**/api/threads/*/outputs', async route => {
      if (route.request().url().includes(second.id)) { await route.fulfill({ json: { changes: { files: [], added: 0, removed: 0, total: 0 }, images: [], files: [], seq: 0 } }); return; }
      await new Promise(resolve => releases.push(resolve));
      await route.fulfill({ status: failed ? 503 : 200, json: failed ? { error: 'unavailable' } : outputs(false) });
    });
    try {
      await openUi(page, daemon);
      await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: first.title }).click();
      await page.locator('.topbar-overview').click();
      await page.locator('.topbar-overview[aria-busy="true"]').waitFor();
      await page.touchscreen.tap(2, 400);
      await answer(); await page.waitForTimeout(100);
      assert.equal(await page.locator('.overview-panel').count(), 0, 'a cancelled read cannot open late');
      // First failure is unknown, with a retry action; no invented zero or empty list.
      await page.locator('.topbar-overview').click();
      await answer();
      const panel = page.locator('.overview-panel');
      await panel.getByText('概况没有读到', { exact: true }).waitFor();
      assert.equal(await panel.locator('.overview-row').count(), 0);
      assert.equal(await panel.getByText('还没有产出', { exact: true }).count(), 0);
      failed = false;
      await panel.locator('.overview-refresh').click();
      await page.locator('.topbar-overview[aria-busy="true"]').waitFor();
      await answer();
      await panel.locator('.overview-row').first().waitFor();
      assert.equal(await panel.locator('.overview-rowwrap').count(), 3);
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill('保留未发送的草稿');
      await page.locator('.topbar-overview').click();
      await panel.locator('.overview-foot .overview-trace').click();
      const detail = page.locator('.overview-details-page');
      await detail.waitFor();
      assert.equal(await detail.locator('.overview-rowwrap').count(), 8);
      await detail.evaluate(el => {
        const touch = x => new Touch({ identifier: 1, target: el, clientX: x, clientY: 200 });
        el.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [touch(4)] }));
        el.dispatchEvent(new TouchEvent('touchmove', { bubbles: true, touches: [touch(140)] }));
        el.dispatchEvent(new TouchEvent('touchend', { bubbles: true, touches: [] }));
      });
      assert.equal(await page.locator('.app.drawer-open').count(), 0, 'an edge swipe on the detail must not open the thread list');
      await page.evaluate(() => history.back());
      await detail.waitFor({ state: 'detached', timeout: 2500 });
      await panel.waitFor();
      assert.equal(await page.locator('.app.drawer-open').count(), 0, 'Back leaves the detail before opening the thread list');
      await panel.locator('.overview-foot .overview-trace').click();
      await detail.waitFor();
      await detail.locator('.overview-details-head button').click();
      await panel.waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.composer textarea').inputValue(), '保留未发送的草稿');
      await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: second.title }).click();
      await page.locator('.topbar-overview').click();
      await panel.getByText('还没有产出', { exact: true }).waitFor();
      assert.equal(await panel.locator('.overview-row').count(), 0, 'no rows from the previous thread');
    } finally { releases.forEach(release => release()); await context.close(); }
  } finally { await browser.close(); await daemon.stop(); }
});

test('five categories retain important summaries and extremely short windows can leave the full page', { timeout: 120000 }, async () => {
  const daemon = await startDaemon(); const browser = await launchBrowser();
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  try {
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const mobile = width === 390;
      const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      await context.addInitScript(mode => localStorage.setItem('calli.appearance', JSON.stringify({ mode, palette: 'graphite' })), theme);
      try {
        const page = await context.newPage(); page.setDefaultTimeout(10000);
        await page.goto(daemon.base);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
        if (mobile) await page.locator('.topbar-overview').click();
        const panel = page.locator('.overview-panel');
        await panel.locator('.thread-overview[data-ready="true"]').waitFor();
        await page.waitForTimeout(200);
        assert.equal(await panel.locator('.overview-group').count(), 5);
        assert.match(await panel.locator('.overview-summary').first().innerText(), /需要你.*失败/);
        assert.equal(await panel.locator('.overview-member').count(), 1);
        let bounds = await geometry(page);
        assert.ok(bounds.scroll <= bounds.client + 1);
        assert.ok(bounds.content <= bounds.footer + 1);
        if (shots) await page.screenshot({ path: `${shots}/overview-five-${width}-${theme}.png` });
        if (mobile) {
          await page.setViewportSize({ width, height: 350 });
          await panel.locator('.thread-overview[data-limit="0"]').waitFor();
          bounds = await geometry(page);
          assert.ok(bounds.scroll <= bounds.client + 1);
          assert.ok(bounds.bottom <= 338);
        }
        // If even the category headings cannot fit, use the full page instead of a scrollbar.
        await page.setViewportSize({ width, height: 220 });
        const detail = page.locator('.overview-details-page');
        await detail.waitFor();
        await detail.locator('.overview-details-head button').click();
        await detail.waitFor({ state: 'detached' });
        await page.waitForTimeout(100);
        assert.equal(await page.locator('.overview-panel').count(), 0, 'Back must not open the same oversized panel again');
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
