import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

/** Half a year of uneven use, quiet weekends, a few days whose turns reported nothing, nothing before. */
function seed(dataDir, threadId) {
  const db = new DatabaseSync(join(dataDir, 'hub.db'));
  db.exec('PRAGMA busy_timeout = 5000');
  const turn = db.prepare("INSERT INTO telemetry_turns(thread_id,turn_id,started_at,agent,status) VALUES (?,?,?,'pi','done')");
  const sample = db.prepare('INSERT INTO telemetry_samples(thread_id,turn_id,sample_id,ts,data) VALUES (?,?,?,?,?)');
  const today = new Date();
  for (let ago = 180; ago >= 0; ago--) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - ago, 11);
    const weekend = day.getDay() === 0 || day.getDay() === 6;
    if (weekend && ago % 3) continue;
    const id = `seed-${ago}`, at = day.toISOString();
    turn.run(threadId, id, at);
    if (ago % 17 === 5) continue; // turns, but no usage report: unknown, not zero
    const wave = 1 + Math.sin(ago / 9) + (ago < 60 ? 1.5 : 0);
    const total = Math.round((weekend ? 40000 : 400000) * wave * (1 + (ago * 7919 % 13) / 6));
    sample.run(threadId, id, 'r', at, JSON.stringify({ id: 'r', scope: 'request', total, output: Math.round(total / 9) }));
  }
  db.close();
}

test('token activity shows a year of days, tells unknown from empty, and reads a day on hover, tap and keys', { timeout: 150000 }, async () => {
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] },
    { id: 'pi-2', label: '空闲 pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] },
  ] }) });
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  let browser;
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '热力图验收' }) })).json();
    seed(daemon.dataDir, thread.id);
    const daily = await (await api(daemon, '/api/usage/daily?agent=pi')).json();
    assert.ok(daily.days.length > 100 && daily.since, 'the seeded days come back');
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(String(error)));
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.getByRole('button', { name: '配置', exact: true }).click();
        await page.locator('.settings-nav').getByRole('button', { name: '用量与诊断' }).click();
        const panel = page.getByRole('dialog', { name: '用量与诊断', exact: true });
        const heatmap = panel.locator('.obs-heatmap');
        await heatmap.locator('.heat-cell').first().waitFor();
        const head = heatmap.locator('.obs-chart-head');
        assert.match(await head.innerText(), /Token 活动 · 按天[\s\S]*合计 .+ · \d+ 天有用量/);

        // As many weeks as fit: a year on a desktop, fewer on a phone; this week stops at today.
        const columns = await heatmap.locator('svg').evaluate(svg => new Set([...svg.querySelectorAll('rect')].map(r => r.getAttribute('x'))).size);
        assert.ok(mobile ? columns >= 20 && columns < 53 : columns === 53, `${columns} weeks`);
        const today = (new Date().getDay() + 6) % 7 + 1;
        assert.equal(await heatmap.locator('.heat-cell').count(), (columns - 1) * 7 + today);
        for (const kind of ['unknown', 'level-0', 'level-4']) assert.ok(await heatmap.locator(`.heat-cell.${kind}`).count() > 0, kind);

        // A day reads in the header: hovered on a desktop, tapped on a phone.
        const busiest = heatmap.locator('.heat-cell.level-4').last();
        if (mobile) await busiest.tap(); else await busiest.hover();
        await head.getByText(/token · \d+ 轮/).waitFor();
        assert.equal(await heatmap.locator('.heat-cell.active').count(), 1);
        if (shots) {
          await heatmap.scrollIntoViewIfNeeded();
          await page.screenshot({ path: join(shots, `token-heatmap-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
        }

        // Keys walk the days. A desktop's year reaches back past the first record; a phone's half year does not.
        await heatmap.locator('.obs-heatmap-plot').focus();
        await page.keyboard.press('Home');
        await heatmap.locator('.heat-cell').first().and(heatmap.locator('.heat-cell.active')).waitFor();
        if (!mobile) await head.getByText('还没开始记录').waitFor();
        await page.keyboard.press('End');
        assert.doesNotMatch(await head.innerText(), /还没开始记录/);

        // Another instance has no turns at all: every day unknown, the sum zero.
        await panel.getByRole('button', { name: '统计实例', exact: true }).click();
        await page.getByRole('menu', { name: '统计实例' }).getByRole('menuitemradio', { name: '空闲 pi', exact: true }).click();
        await head.getByText(/合计 0 · 0 天有用量/).waitFor();
        assert.equal(await heatmap.locator('.heat-cell:not(.unknown)').count(), 0);

        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
