import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('a dropped text file is attached, sent to the agent by path and saved from history on desktop and phone, light and dark', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `文件 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, model: 'fake/plain', title }) })).json();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme, acceptDownloads: true });
      try {
        const page = await context.newPage();
        let reads = 0;
        page.on('request', req => { if (req.url().includes('/attachments/')) reads++; });
        const shot = async name => {
          if (!process.env.CALLI_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        };
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: title }).click();

        // Drag and drop, the way a file comes from Finder.
        const drop = await page.evaluateHandle(() => {
          const data = new DataTransfer();
          data.items.add(new File(['第一段\n第二段\n'], 'transcript.txt', { type: 'text/plain' }));
          return data;
        });
        await page.locator('.composer-box').dispatchEvent('dragover', { dataTransfer: drop });
        assert.equal(await page.locator('.composer textarea').getAttribute('placeholder'), '松开以添加附件');
        await page.locator('.composer-box').dispatchEvent('drop', { dataTransfer: drop });
        const chip = page.locator('.composer .file-card', { hasText: 'transcript.txt' });
        await chip.waitFor();
        await shot('file-dropped');

        await page.locator('.composer textarea').fill('总结一下');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await chip.waitFor({ state: 'detached' });
        const sent = page.getByRole('button', { name: '下载文件：transcript.txt', exact: true });
        await sent.waitFor();
        const log = await (await api(daemon, `/api/threads/${thread.id}/events?all`)).json();
        const started = (log.events ?? log).find(e => e.type === 'turn.started');
        assert.match(started.data.text, /The user attached 1 file\(s\)[\s\S]*\/transcript\.txt/, 'the agent is told where the file is');
        assert.equal(reads, 0, 'history does not fetch file bytes until asked');
        await shot('file-sent');

        const [download] = await Promise.all([page.waitForEvent('download'), sent.click()]);
        assert.equal(download.suggestedFilename(), 'transcript.txt');
        assert.equal(readFileSync(await download.path(), 'utf8'), '第一段\n第二段\n');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
