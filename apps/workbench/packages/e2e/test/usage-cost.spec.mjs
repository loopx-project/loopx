import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
before(async () => {
  daemon = await startDaemon({ FAKE_TELEMETRY: '1', FAKE_CODEX_PRICE_MODELS: '1', CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'priced', adapter: 'codex', label: 'Pricing fixture', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] },
  ] }) });
  console.log(`Pricing test daemon PID: ${daemon.pid}`);
  browser = await launchBrowser();
  for (const model of ['gpt-6-astra', 'openrouter-4o', 'Doubao-Seed-Evolving', 'seed-evol', 'unlisted-model']) {
    const response = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'priced', cwd: daemon.workdir, title: model, model }) });
    assert.equal(response.status, 201);
    const thread = await response.json();
    assert.ok((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) })).ok);
    for (let i = 0; i < 100; i++) {
      if ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'idle') break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  }
  const report = await (await api(daemon, '/api/usage')).json();
  assert.equal(report.costSummary.estimatedSamples, 4);
  assert.equal(report.costSummary.familySamples, 1);
  assert.equal(report.costSummary.unpricedSamples, 1);
  assert.equal(report.costSummary.groups, undefined);
  assert.deepEqual(report.costs, {});
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('API estimates load pricing details on demand, explain aliases and retry on desktop/phone in both themes', async () => {
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let detailsReads = 0;
      const testRetry = mobile && colorScheme === 'light';
      await page.route('**/api/usage/costs?*', route => {
        detailsReads++;
        return testRetry && detailsReads === 1 ? route.fulfill({ status: 503, json: { error: 'fixture unavailable' } }) : route.continue();
      });
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '用量与诊断' }).click();
      const panel = page.locator('.obs-page');
      await panel.getByText('API 标价估算', { exact: true }).waitFor();
      await panel.getByText('1 条用量未计价', { exact: true }).waitFor();
      await panel.getByText('含 1 条家族价格估算', { exact: true }).waitFor();
      assert.match(await panel.locator('.obs-cost').innerText(), /USD .+–/);
      assert.match(await panel.locator('.obs-cost').innerText(), /CNY/);
      assert.equal(detailsReads, 0);
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `cost-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      const trigger = panel.getByRole('button', { name: '查看计价依据', exact: true });
      if (mobile) await trigger.tap();
      else { await trigger.focus(); await page.keyboard.press('Enter'); }
      const details = page.getByRole('dialog', { name: '查看计价依据', exact: true });
      if (testRetry) {
        await details.getByRole('alert').waitFor();
        await details.getByRole('button', { name: '重试', exact: true }).click();
      }
      await details.getByText('openrouter-4o → claude-opus-4-7', { exact: true }).waitFor();
      await details.getByText('按用户提供的别名对应关系估算，尚未通过上游模型记录核实。', { exact: true }).waitFor();
      await details.getByText('暂无已核实的对应模型价格。', { exact: true }).waitFor();
      await details.getByText('参考单价：doubao-seed-2.1-pro', { exact: true }).waitFor();
      await details.getByText('该型号暂无精确价格，借用同家族模型的价格估算，可能与实际计价不同。', { exact: true }).waitFor();
      assert.equal(detailsReads, testRetry ? 2 : 1);
      const sources = await details.getByRole('link', { name: '官方定价' }).evaluateAll(links => links.map(link => link.href));
      assert.ok(sources.includes('https://platform.claude.com/docs/en/about-claude/pricing'));
      assert.ok(sources.includes('https://developers.openai.com/api/docs/models/gpt-6-astra'));
      assert.ok(sources.includes('https://docs.volcengine.com/docs/ark/model-pricing?lang=en&redirect=1'));
      const body = details.locator('.obs-cost-body');
      assert.equal(await body.evaluate(el => el.scrollWidth > el.clientWidth), false);
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `cost-details-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await details.getByText('openrouter-4o → claude-opus-4-7', { exact: true }).scrollIntoViewIfNeeded();
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `cost-alias-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await details.getByText('参考单价：doubao-seed-2.1-pro', { exact: true }).scrollIntoViewIfNeeded();
      if (shots) await page.screenshot({ animations: 'disabled', path: join(shots, `cost-family-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      if (mobile) {
        await page.setViewportSize({ width: 320, height: 740 });
        assert.equal(await body.evaluate(el => el.scrollWidth > el.clientWidth), false);
      }
      await page.keyboard.press('Escape');
      await details.waitFor({ state: 'hidden' });
      assert.equal(await panel.isVisible(), true);
      assert.equal(await trigger.evaluate(el => document.activeElement === el), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  }
});
