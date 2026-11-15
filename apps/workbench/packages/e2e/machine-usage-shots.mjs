// Screenshots of the machines page (a first-class page at #/machines; desktop 1440 and phone 390,
// light and dark): the sidebar's way in, the list's cards, the detail's charts, the number on a
// 配置 → 机器 row jumping to a detail, and the observability page back to its two tabs. The
// meter's own tables are seeded with a day-shaped usage history — the daemon cannot have one
// after seconds of life, and screenshots of empty charts say nothing. Run after `npm run build`:
//   node packages/e2e/machine-usage-shots.mjs [outdir]
import { mkdirSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from './harness.mjs';

const out = process.argv[2] ?? '/tmp/calli-machine-usage-shots';
mkdirSync(out, { recursive: true });

const daemon = await startDaemon({}, { agents: [
  { id: 'claude', adapter: 'claude', command: ['fake-claude'] },
  { id: 'pi', adapter: 'pi', command: ['fake-pi'] },
]});

try {
  // Let the daemon's first meter pass finish (it replaces the whole window); only then seed.
  for (let i = 0; i < 50; i++) {
    const res = await api(daemon, '/api/machine-usage?hours=24');
    if (res.ok) break;
    await new Promise((r) => setTimeout(r, 300));
  }
  await new Promise((r) => setTimeout(r, 1500));

  // A day-shaped history: up through the working day and the evening, quiet at night, off for a
  // stretch last night (grey in the charts). Deterministic, so every run shots the same shape.
  const db = new DatabaseSync(join(daemon.dataDir, 'hub.db'));
  db.exec('PRAGMA busy_timeout = 5000');
  const up = db.prepare('INSERT OR IGNORE INTO machine_uptime (slot) VALUES (?)');
  const row = db.prepare(`INSERT OR REPLACE INTO machine_usage
    (minute, step, model, launcher, input, cache_write, cache_read, output, replies)
    VALUES (?, 60, ?, ?, ?, ?, ?, ?, ?)`);
  const nowSec = Math.floor(Date.now() / 1000);
  const start = Math.floor((nowSec - 26 * 3600) / 60) * 60;
  // The number on the list reads "today since midnight": a run in the small hours after
  // midnight belongs to it, or the line reads zero while the charts beside it carry millions.
  const midnightToday = Math.floor(new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000);
  for (let minute = start; minute < nowSec; minute += 60) {
    const date = new Date(minute * 1000);
    const hour = date.getHours();
    // Last night, 02:00 to 05:30, the machine was off — old enough never to fall inside the hour view.
    if (hour >= 2 && hour < 5 && nowSec - minute > 6 * 3600) continue;
    up.run(Math.floor(minute / 600) * 600);
    const busy = (hour >= 9 && hour < 12) || (hour >= 14 && hour < 19);
    const light = (hour >= 19 && hour < 23) || (hour >= 6 && hour < 9) || (minute >= midnightToday && minute % 3 === 0);
    if (!busy && !(light && minute % 3 === 0)) continue;
    let seed = minute;
    const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
    const model = hour < 13 || rand() < 0.6 ? 'claude-sonnet-4-6' : 'claude-opus-4-8';
    const launcher = rand() < 0.75 ? 'claude' : '';
    row.run(minute, model, launcher,
      Math.round(2000 + rand() * 4000), Math.round(1000 + rand() * 2000),
      Math.round(25000 + rand() * 35000), Math.round(300 + rand() * 600),
      3 + Math.floor(rand() * 5));
  }
  db.close();

  // The report reads what was seeded; wait until the numbers are in before opening the page.
  for (let i = 0; i < 100; i++) {
    const res = await api(daemon, '/api/machine-usage?hours=24');
    const report = res.ok ? await res.json() : null;
    if (report?.points?.some((p) => !p.gap && p.input + p.output > 0)) break;
    await new Promise((r) => setTimeout(r, 300));
  }

  const browser = await launchBrowser();
  const machineLink = (page) => page.locator('.side-link', { hasText: '机器' }).first();
  try {
    // The first look: everything in its loading shape (the usage routes held back a moment), then
    // the same views settled — the two must differ only in content, never in layout. The detail is
    // its own hash, so nothing on the held-back list stands in the way.
    for (const [name, viewport, scheme] of [
      ['desktop-light', { width: 1440, height: 900 }, 'light'],
      ['phone-light', { width: 390, height: 844 }, 'light'],
    ]) {
      const page = await browser.newPage({ viewport, colorScheme: scheme });
      await page.route('**/api/machine-usage*', async (route) => {
        await new Promise((r) => setTimeout(r, 1500));
        // A request the page gave up on may already be settled: the hold is all we wanted.
        await route.continue().catch(() => undefined);
      });
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/machines');
      // One machine names no total card (it would only repeat the card's own number).
      await page.locator('.mu-cards').waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(out, `loading-list-${name}.png`) });
      console.log('loading list', name);

      await page.goto(daemon.base + '/#/machines/local');
      await page.locator('.mu-figures').waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(out, `loading-detail-${name}.png`) });
      console.log('loading detail', name);

      await page.unroute('**/api/machine-usage*');
      await page.waitForTimeout(2500);
      await page.screenshot({ path: join(out, `loading-settled-${name}.png`) });
      console.log('settled', name);
      await page.close();
    }

    for (const [name, viewport, scheme] of [
      ['desktop-light', { width: 1440, height: 900 }, 'light'],
      ['desktop-dark', { width: 1440, height: 900 }, 'dark'],
      ['phone-light', { width: 390, height: 844 }, 'light'],
      ['phone-dark', { width: 390, height: 844 }, 'dark'],
    ]) {
      const page = await browser.newPage({ viewport, colorScheme: scheme });
      page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text().slice(0, 300)); });
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/machines');
      await page.locator('.mu-card').first().waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(out, `list-${name}.png`) });
      console.log('list', name);

      // The detail: the figures row, the stacked chart with its offline stretch, the hit rate line.
      await page.locator('.mu-card').first().click();
      await page.locator('.mu-figures').waitFor({ timeout: 20_000 });
      await page.locator('.obs-chart-plot').nth(1).waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(out, `detail-${name}.png`) });
      console.log('detail', name);

      if (name === 'phone-light') {
        // A long model name, picked: the filter row must still fit, the pick cut with an ellipsis.
        await page.locator('.mu-toolbar .obs-select').first().click();
        await page.waitForTimeout(300);
        const items = await page.locator('.pop-item').allInnerTexts();
        const long = items.filter(v => (v ?? '').length > 10).sort((a, b) => b.length - a.length)[0];
        if (long) {
          await page.locator('.pop-item', { hasText: long.trim().split(/\s/)[0] }).first().click();
          await page.waitForTimeout(600);
          await page.screenshot({ path: join(out, 'detail-picked-phone-light.png') });
          console.log('picked shot:', long.trim());
        }

        // The page's own back button returns to the list.
        await page.locator('.mu-detail-head .text-btn').click();
        await page.locator('.mu-card').first().waitFor({ timeout: 20_000 });
        await page.screenshot({ path: join(out, 'detail-back-phone-light.png') });
        console.log('back to list', name);
      }

      if (name === 'desktop-light') {
        // The crosshair readout over the stacked chart, and the 合计 view of the same range.
        const plot = page.locator('.obs-chart-plot').first();
        const box = await plot.boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2);
          await page.waitForTimeout(200);
          await page.screenshot({ path: join(out, 'detail-hover-desktop-light.png') });
        }
        await page.locator('.mu-mode button').nth(1).click();
        await page.waitForTimeout(300);
        await page.screenshot({ path: join(out, 'detail-total-desktop-light.png') });
        console.log('hover and total', name);
      }
      await page.close();
    }

    // The sidebar's way in, entered like a user enters — the link beside the plans one, the
    // drawer's on a phone — and the browser's back walking out again: a detail backs to the
    // list, the list to the thread the page was opened from.
    for (const [name, viewport, scheme] of [
      ['desktop-light', { width: 1440, height: 900 }, 'light'],
      ['phone-light', { width: 390, height: 844 }, 'light'],
    ]) {
      const page = await browser.newPage({ viewport, colorScheme: scheme });
      await openUi(page, daemon);
      if (viewport.width < 700) await page.locator('.menu-btn').first().click();
      await machineLink(page).waitFor({ timeout: 10_000 });
      if (name === 'desktop-light') await page.screenshot({ path: join(out, 'sidebar-entry-desktop-light.png') });
      await machineLink(page).click();
      await page.locator('.mu-card').first().waitFor({ timeout: 20_000 });
      console.log('sidebar entry', name, 'hash', await page.evaluate(() => window.location.hash));
      await page.locator('.mu-card').first().click();
      await page.locator('.mu-figures').waitFor({ timeout: 20_000 });
      await page.goBack({ waitUntil: 'commit' }).catch(() => {});
      await page.locator('.mu-card').first().waitFor({ timeout: 10_000 });
      console.log('browser back: detail -> list');
      // The popstate handler puts the app's history entry back only after a paint; a back that
      // lands before it walks out of the app entirely (a blank page, not the thread).
      await page.waitForTimeout(600);
      await page.goBack({ waitUntil: 'commit' }).catch(() => {});
      await page.waitForTimeout(1600);
      const hash = await page.evaluate(() => window.location.hash);
      // The thread view answers with its composer; an empty hash alone could also be a blank exit.
      const composer = await page.locator('.composer').count();
      console.log('browser back: page ->', hash || 'thread', composer ? 'thread restored' : 'NO COMPOSER');
      if (name === 'phone-light') await page.screenshot({ path: join(out, 'back-to-thread-phone-light.png') });
      await page.close();
    }

    // The number on a 配置 → 机器 row opens the machines page's detail for that machine.
    for (const [name, viewport, scheme] of [
      ['desktop-light', { width: 1440, height: 900 }, 'light'],
      ['phone-light', { width: 390, height: 844 }, 'light'],
    ]) {
      const page = await browser.newPage({ viewport, colorScheme: scheme });
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/settings/machines');
      await page.locator('.host-today').first().waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(out, `hosts-${name}.png`) });
      await page.locator('.host-today').first().click();
      await page.locator('.mu-figures').waitFor({ timeout: 20_000 });
      await page.waitForTimeout(400);
      console.log('hosts row jump', name, await page.evaluate(() => window.location.hash));
      await page.screenshot({ path: join(out, `hosts-open-${name}.png`) });
      await page.close();
    }

    // The observability page after the move: usage and diagnostics, and no machines tab.
    {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/observability');
      await page.locator('.obs-tabs button').first().waitFor({ timeout: 20_000 });
      const tabs = await page.locator('.obs-tabs button').count();
      console.log('observability tabs:', tabs);
      await page.screenshot({ path: join(out, 'observability-desktop-light.png') });
      await page.close();
    }

    // The demo: no token, the made-up machines read on the same page, drawer included.
    {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
      await page.goto(daemon.base + '/#/machines');
      await page.locator('.mu-card').first().waitFor({ timeout: 20_000 });
      await page.waitForTimeout(2500);
      await page.screenshot({ path: join(out, 'demo-machines-phone.png') });
      await page.locator('.mu-card').first().click();
      await page.locator('.mu-figures').waitFor({ timeout: 20_000 });
      await page.waitForTimeout(1500);
      await page.screenshot({ path: join(out, 'demo-detail-phone.png') });
      console.log('demo page ok');
      await page.close();
    }
    console.log(`shots in ${out}`);
    console.log(`daemon ${daemon.pid} (${daemon.base})`);
  } finally {
    await browser.close();
  }
} finally {
  await daemon.stop();
}
