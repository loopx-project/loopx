import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser, thread;
before(async () => {
  daemon = await startDaemon({ FAKE_TELEMETRY: '1', CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'pi', adapter: 'pi', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-pi.mjs')] },
    { id: 'zcode-empty', adapter: 'zcode', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-zcode.mjs')], env: { FAKE_ZCODE_NO_MODELS: '1' } },
  ] }) });
  browser = await launchBrowser();
  thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '用量统计验收' }) })).json();
  assert.ok((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) })).ok);
  for (let i = 0; i < 100; i++) {
    if ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'idle') break;
    await new Promise(r => setTimeout(r, 50));
  }
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('usage, request details and actionable diagnostics on desktop/phone in light/dark', async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '用量与诊断' }).click();
      const panel = page.locator('.obs-page');
      await panel.getByText('TPM · 最近 60 秒', { exact: true }).waitFor();
      assert.equal(await panel.locator('.obs-metric').nth(1).locator('strong').innerText(), '170');
      // The range menu must open fully inside the scrolling body, with styled rows.
      await panel.getByRole('button', { name: '统计范围', exact: true }).click();
      const menu = page.getByRole('menu', { name: '统计范围' });
      const week = menu.getByRole('menuitemradio', { name: '最近 7 天' });
      await week.waitFor();
      const [box, body] = [await menu.boundingBox(), await panel.locator('.obs-body').boundingBox()];
      if (!mobile) assert.ok(box.y >= body.y && box.y + box.height <= body.y + body.height, `menu ${JSON.stringify(box)} outside body ${JSON.stringify(body)}`);
      assert.equal(await menu.getByRole('menuitemradio', { name: '最近 24 小时' }).getAttribute('aria-checked'), 'true');
      if (process.env.CALLI_E2E_SHOTS_DIR) { mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true }); await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `range-selector-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) }); }
      await week.click();
      await menu.waitFor({ state: 'hidden' });
      await panel.getByRole('button', { name: '统计范围', exact: true }).filter({ hasText: '最近 7 天' }).waitFor();
      await panel.getByRole('button', { name: '统计实例', exact: true }).click();
      const instances = page.getByRole('menu', { name: '统计实例' });
      await instances.getByRole('menuitemradio', { name: 'pi', exact: true }).waitFor();
      if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `instance-selector-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await instances.getByRole('menuitemradio', { name: 'pi', exact: true }).click();
      await panel.locator('.obs-turn summary').click();
      await panel.getByText('fixture-model · fixture-provider').waitFor();
      await panel.getByText('估算 USD 0.0123', { exact: true }).last().waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `usage-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      }
      await panel.getByRole('button', { name: 'Agent 诊断', exact: true }).click();
      await panel.getByText('没有可选模型', { exact: true }).waitFor({ timeout: 30000 });
      const empty = panel.locator('.obs-diagnostic').filter({ has: page.getByText('没有可选模型', { exact: true }) });
      await empty.scrollIntoViewIfNeeded();
      await empty.getByRole('button', { name: /刷新 .* 诊断/ }).click();
      await empty.getByText('没有可选模型', { exact: true }).waitFor();
      await empty.locator('button[disabled]').waitFor({ state: 'hidden' });
      await empty.getByRole('button', { name: /复制 .* 诊断/ }).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `diagnostics-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await panel.waitFor({ state: 'hidden' });
    } finally { await context.close(); }
  }
});


test('multi-instance chart supports selection, output rates, touch, keys and narrow layouts', async () => {
  const native = await (await api(daemon, '/api/usage')).json();
  const points = Array.from({ length: 145 }, (_, i) => ({ at: new Date(Date.UTC(2026, 9, 14, 12) + i * 600000).toISOString(),
    tpm: Math.round((Math.sin(i / 12) + 1.3) * 450000), outputTpm: Math.round((Math.cos(i / 10) + 1.2) * 4500) }));
  const report = { ...native, totals: { ...native.totals, total: 12917704, input: 12869386, output: 48318 }, tpm: 1028871,
    outputTpm: 48318, outputMeasuredSamples: 2, series: { stepMinutes: 10, estimated: true, points,
      agents: [{ agent: 'pi', tpm: points.map(p => Math.round(p.tpm * .7)), outputTpm: points.map(p => Math.round(p.outputTpm * .6)) },
        { agent: 'zcode-empty', tpm: points.map(p => Math.round(p.tpm * .3)), outputTpm: points.map(p => Math.round(p.outputTpm * .4)) }] } };
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/usage?*', route => route.fulfill({ json: report }));
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '用量与诊断' }).click();
      const panel = page.locator('.obs-page');
      const chart = panel.getByRole('region', { name: 'TPM 走势', exact: true });
      await chart.locator('svg').waitFor();
      assert.equal(await chart.locator('.obs-chart-line').count(), 3);
      assert.equal(await panel.locator('.obs-primary strong').innerText(), '1.03M');
      const total = chart.getByRole('button', { name: '合计', exact: true });
      await total.click();
      assert.equal(await chart.locator('.obs-chart-line').count(), 2);
      assert.equal(await total.getAttribute('aria-pressed'), 'false');
      await total.click();
      const plot = chart.locator('.obs-chart-plot');
      if (mobile) await plot.tap(); else await plot.hover();
      await chart.locator('.obs-chart-tip').waitFor();
      const plotBox = await plot.boundingBox(), tipBox = await chart.locator('.obs-chart-tip').boundingBox();
      assert.ok(tipBox.x >= plotBox.x && tipBox.x + tipBox.width <= plotBox.x + plotBox.width + 1);
      await plot.focus();
      await page.keyboard.press('End');
      assert.match(await chart.locator('.obs-chart-tip').innerText(), /10\/15/);
      if (process.env.CALLI_E2E_SHOTS_DIR) await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `chart-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await panel.getByRole('button', { name: 'TPM 口径', exact: true }).click();
      await page.getByRole('menuitemradio', { name: '仅输出 token' }).click();
      assert.equal(await panel.locator('.obs-primary strong').innerText(), '48.3K');
      await chart.getByText(/输出 TPM 走势/).waitFor();
      await chart.getByText('查看数值表', { exact: true }).click();
      assert.equal(await chart.locator('tbody tr').count(), 145);
      assert.equal(await chart.locator('thead th').count(), 4);
      await chart.getByText('查看数值表', { exact: true }).click();
      if (mobile) await page.setViewportSize({ width: 320, height: 740 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  }
});
