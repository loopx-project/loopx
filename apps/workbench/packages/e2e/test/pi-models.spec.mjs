import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

test('Pi new-thread and personal model pickers work while MCP discovery is blocked', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pi-model-ui-')), state = join(dir, 'state');
  writeFileSync(state, JSON.stringify({ mcpHold: true }));
  const daemon = await startDaemon({ FAKE_PI_CATALOG_FILE: state, FAKE_PI_REQUIRE_NO_MCP: '1', FAKE_PI_MANY_MODELS: '1',
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] }) });
  const browser = await launchBrowser();
  try {
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 }, isMobile: mobile, hasTouch: mobile });
      const page = await context.newPage(); page.setDefaultTimeout(10_000);
      await openUi(page, daemon);
      if (mobile) await page.locator('.composer textarea').tap();
      await page.locator('.model-control-trigger').click();
      await page.locator('.effort-model').click();
      await page.getByRole('menuitemradio', { name: /^Plain\b/ }).click();
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill(`Model selection ${mobile ? 'phone' : 'desktop'}`);
      const created = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/threads');
      await page.locator('.composer .send-btn').click();
      assert.equal((await (await created).json()).model, 'fake/plain');
      await page.locator('.row.assistant').waitFor();
      await page.evaluate(() => { location.hash = '#/todos'; });
      await page.locator('.personal-summary').click();
      const settings = page.getByRole('region', { name: '私人设置', exact: true });
      await settings.locator('.personal-setting-line').nth(2).getByRole('button').click();
      await page.getByLabel('筛选模型').fill('Model 40');
      await page.getByRole('menuitemradio', { name: 'Model 40', exact: true }).click();
      await settings.getByRole('button', { name: '保存', exact: true }).click();
      await settings.waitFor({ state: 'detached' });
      assert.equal((await (await api(daemon, '/api/personal/settings')).json()).model, 'fake/model-40');
      await context.close();
    }
  } finally { writeFileSync(state, '{}'); await browser.close(); await daemon.stop(); rmSync(dir, { recursive: true, force: true }); }
});


test('Pi image capabilities refresh open desktop and phone composers after background discovery', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pi-vision-ui-')), state = join(dir, 'state');
  writeFileSync(state, JSON.stringify({ mcpHold: true }));
  const daemon = await startDaemon({ FAKE_PI_CATALOG_FILE: state, FAKE_PI_REQUIRE_NO_MCP: '1',
    CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] }) });
  const browser = await launchBrowser();
  const shots = resolve(import.meta.dirname, '../shots/personal'); mkdirSync(shots, { recursive: true });
  const pages = [];
  try {
    for (const mobile of [true, false]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 }, colorScheme: theme, isMobile: mobile, hasTouch: mobile });
      const page = await context.newPage(); page.setDefaultTimeout(10_000);
      await openUi(page, daemon);
      if (mobile) await page.locator('.composer textarea').tap();
      await page.locator('.model-control-trigger').click();
      await page.locator('.effort-model').click();
      await page.getByRole('menuitemradio', { name: /^Plain\b/ }).click();
      await page.keyboard.press('Escape');
      await page.locator('.composer textarea').fill('Inspect image');
      await page.setInputFiles('.composer input[type=file]', { name: 'pixel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64') });
      await page.locator('.attachments img').waitFor();
      assert.equal(await page.getByText('Plain 不能读图片，请换一个模型', { exact: true }).count(), 0, 'unknown does not claim unsupported');
      if (mobile) await page.locator('.composer-add').click();
      pages.push({ page, mobile, theme });
    }
    writeFileSync(state, '{}');
    for (const { page, mobile, theme } of pages) {
      if (mobile) {
        await page.locator('[title^="Plain 不能读图片"]').waitFor();
        await page.keyboard.press('Escape');
      }
      await page.locator('.composer .send-btn').click();
      await page.getByText('Plain 不能读图片，请换一个模型', { exact: true }).waitFor();
      await page.screenshot({ animations: 'disabled', path: join(shots, `pi-vision-${mobile ? 'mobile' : 'desktop'}-${theme}.png`) });
    }
    assert.equal((await (await api(daemon, '/api/threads')).json()).length, 0, 'the refreshed client rejects unsupported images before creating a thread');
  } finally { writeFileSync(state, '{}'); await browser.close(); await daemon.stop(); rmSync(dir, { recursive: true, force: true }); }
});
