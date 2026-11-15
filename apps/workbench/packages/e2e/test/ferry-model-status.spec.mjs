import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';
import { modelGateway } from '../../daemon/test/fixtures/model-gateway.mjs';

test('Ferry picker shows observed queues and safely degrades without a cache across themes and sizes', { timeout: 120_000 }, async () => {
  // Every provider is a fake: opening or selecting a model must not run real inference.
  const home = mkdtempSync(join(tmpdir(), 'calli-ferry-ui-'));
  const gateway = await modelGateway();
  let daemon, browser;
  try {
    const fixtures = fileURLToPath(new URL('../../daemon/test/fixtures/', import.meta.url));
    const fakeCodex = join(home, 'fake-codex');
    writeFileSync(fakeCodex, `#!/bin/sh\nexec '${process.execPath}' '${join(fixtures, 'fake-codex.mjs')}' "$@"\n`, { mode: 0o755 });
    const statusFile = join(home, 'models.json');
    const seed = (load) => writeFileSync(statusFile, JSON.stringify([
      { name: 'GPT-6.1-Sol', config_name: 'gpt-6.1-sol', _meta: { trae: { load: { percent: load } } } },
      { name: 'GPT-6-Astra', config_name: 'gpt-6-astra', _meta: { trae: { load: { percent: 0 } } } },
    ]));
    const ferryHome = join(home, 'ferry');
    mkdirSync(ferryHome);
    const queueFile = join(ferryHome, 'queue-seen.json');
    const seedQueue = (position) => writeFileSync(queueFile, JSON.stringify({
      'gpt-6.1-sol': { position, at: Date.now() - 120_000 },
      'gpt-6-astra': { at: Date.now() },
      'gpt-5.5': { position: 999, at: Date.now() - 31 * 60_000 },
    }));
    seed(184);
    seedQueue(550);
    const capabilities = { effort: { low: { supported: true }, medium: { supported: true }, high: { supported: true } } };
    gateway.state.models = [
      { id: 'claude-gpt-6-1-sol', display_name: 'Ferry GPT-6.1-Sol', capabilities },
      { id: 'claude-gpt-6-1-sol-max[1m]', display_name: 'Ferry GPT-6.1-Sol Max 1M', capabilities },
      { id: 'claude-gpt-6-astra', display_name: 'Ferry GPT-6-Astra', capabilities },
      { id: 'claude-openrouter-4o', display_name: 'Ferry Opus 5.5', capabilities },
      { id: 'claude-gpt-5-5', display_name: 'Ferry GPT-5.5', capabilities },
    ];
    // Do not forward credentials, a live gateway or a real Ferry cache inherited from this session.
    const cleanEnv = Object.fromEntries(Object.keys(process.env)
      .filter(name => /^(ANTHROPIC_|CLAUDE_CODE_|CLAUDE_CONFIG_DIR$|FERRY_HOME$|XDG_DATA_HOME$)/.test(name)).map(name => [name, '']));
    daemon = await startDaemon({
      ...cleanEnv, HOME: home, CLAUDE_CONFIG_DIR: join(home, '.claude'), CODEX_HOME: join(home, '.codex'), FERRY_HOME: ferryHome,
      CALLI_CODEX_BIN: fakeCodex, CALLI_CLAUDE_QUOTA_BIN: join(fixtures, 'fake-claude-usage.sh'),
      CALLI_TRAEX_BIN: 'fake-claude', FAKE_TRAEX_MODELS_FILE: statusFile, FAKE_TRAEX_MODELS_FAIL: '0',
      ANTHROPIC_BASE_URL: gateway.url, ANTHROPIC_MODEL: 'claude-gpt-6-1-sol',
      CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: '1',
    });
    browser = await launchBrowser();
    const shots = process.env.CALLI_FERRY_SHOTS;
    if (shots) mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      seed(184);
      seedQueue(550);
      const context = await browser.newContext({
        viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 },
        isMobile: phone, hasTouch: phone, colorScheme: theme,
      });
      try {
        const page = await context.newPage();
        await page.clock.install({ time: new Date() });
        await page.addInitScript(({ token, theme }) => {
          localStorage.setItem('calli.token', token);
          localStorage.setItem('calli.appearance', JSON.stringify({ mode: theme, palette: 'paper' }));
        }, { token: daemon.token, theme });
        await openUi(page, daemon);
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        await page.locator('.composer .context-btn').first().click();
        await page.getByRole('menuitemradio', { name: /^Claude Code/ }).click();
        await page.locator('.model-control-trigger').click();
        const selectedStatus = page.locator('.model-status-line .model-inline-load');
        await page.getByRole('button', { name: /切换模型，当前.*负载184%.*排550（.*分钟前）/ }).waitFor();
        assert.equal((await selectedStatus.allTextContents())[0], '负载184%');
        assert.match((await selectedStatus.allTextContents())[1], /排550（.*分钟前）/);
        const fileName = `${phone ? 'phone' : 'desktop'}-${theme}`;
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-selected.png`) });
        await page.getByRole('button', { name: /^切换模型，当前/ }).click();
        const rows = page.locator('.model-control-panel [role="menuitemradio"]');
        assert.equal(await rows.count(), 6, 'the default and every discovered model stay selectable');
        assert.match(await rows.nth(0).innerText(), /默认.*Ferry GPT-6\.1-Sol.*排550/s);
        assert.match(await rows.nth(1).innerText(), /Ferry Opus 5\.5.*负载未提供.*排队未知/s);
        assert.match(await rows.nth(2).innerText(), /Ferry GPT-6\.1-Sol.*负载184%.*排550/s);
        assert.match(await rows.nth(3).innerText(), /Max 1M.*负载184%.*排550/s);
        assert.match(await rows.nth(4).innerText(), /负载0%.*未排队（刚刚）/s);
        assert.match(await rows.nth(5).innerText(), /Ferry GPT-5\.5.*排队已过期/s);
        assert.equal(await page.getByText('排队未提供', { exact: true }).count(), 0, 'position status must not contradict unavailable total size');
        assert.equal(await page.getByText('排队0', { exact: true }).count(), 0, 'missing total queues never become zero');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'the picker does not overflow the viewport');
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-models.png`) });
        await rows.nth(5).scrollIntoViewIfNeeded();
        assert.equal(await rows.nth(5).isVisible(), true, 'a stale model remains accessible by scrolling');
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-stale-list.png`) });
        await rows.nth(3).click();
        await page.getByRole('button', { name: /切换模型，当前 Ferry GPT-6\.1-Sol Max 1M.*负载184%.*排550/ }).waitFor();
        assert.ok(await page.locator('.effort-model-name').evaluate(el => el.scrollWidth <= el.clientWidth), 'status does not crowd out the selected model name');
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-max.png`) });
        await page.keyboard.press('Escape');
        // Reopening requests fresh metadata; load errors do not hide independent queue observations.
        seed(23);
        seedQueue(37);
        await page.locator('.model-control-trigger').click();
        await page.getByRole('button', { name: /切换模型，当前.*负载23%.*排37/ }).waitFor();
        await page.keyboard.press('Escape');
        writeFileSync(statusFile, 'invalid metadata');
        await page.locator('.model-control-trigger').click();
        await page.getByRole('button', { name: /切换模型，当前.*负载读取失败.*排37/ }).waitFor();
        assert.equal((await selectedStatus.allTextContents())[0], '负载读取失败');
        await page.getByRole('button', { name: /^切换模型，当前/ }).click();
        assert.equal(await rows.count(), 6);
        assert.equal(await rows.filter({ hasText: '负载读取失败' }).count(), 6);
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-failed.png`) });
        await page.keyboard.press('Escape');
        // Ferry need not be installed: missing and corrupt optional cache files leave selection working.
        seed(23);
        rmSync(queueFile);
        await page.locator('.model-control-trigger').click();
        await page.getByRole('button', { name: /切换模型，当前.*负载23%.*排队未知/ }).waitFor();
        assert.deepEqual(await selectedStatus.allTextContents(), ['负载23%', '排队未知']);
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-no-cache.png`) });
        await page.keyboard.press('Escape');
        writeFileSync(queueFile, 'invalid cache');
        await page.locator('.model-control-trigger').click();
        await page.getByRole('button', { name: /切换模型，当前.*负载23%.*排队未知/ }).waitFor();
        await page.getByRole('button', { name: /^切换模型，当前/ }).click();
        assert.equal(await rows.count(), 6);
        assert.equal(await rows.filter({ hasText: '排队未知' }).count(), 6);
        await rows.nth(2).click();
        await page.getByRole('button', { name: /切换模型，当前 Ferry GPT-6\.1-Sol.*负载23%.*排队未知/ }).waitFor();
        await page.keyboard.press('Escape');
        // An open card ages cached observations locally, without re-querying the gateway.
        seedQueue(37);
        await page.locator('.model-control-trigger').click();
        await page.getByRole('button', { name: /切换模型，当前.*负载23%.*排37/ }).waitFor();
        const catalogReads = gateway.state.requests.length;
        await page.clock.setSystemTime(Date.now() + 31 * 60_000);
        await page.clock.runFor(60_001);
        await page.getByRole('button', { name: /切换模型，当前.*负载23%.*排队已过期/ }).waitFor();
        assert.equal(gateway.state.requests.length, catalogReads, 'observation age does not trigger provider polling');
        assert.deepEqual(await selectedStatus.allTextContents(), ['负载23%', '排队已过期']);
        if (shots) await page.screenshot({ path: join(shots, `${fileName}-expired.png`) });
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    await gateway.close();
    rmSync(home, { recursive: true, force: true });
  }
});
