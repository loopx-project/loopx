import { test } from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

const sample = join(repoRoot, 'daemon/test/fixtures/sample-video.mp4');

test('a file an answer names can be played in the viewer and saved, on desktop and phone, light and dark', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    // A video the agent made, and a binary file it cannot show: the two dead ends this closes.
    const video = join(daemon.workdir, '火鸡与Claude神.mp4');
    copyFileSync(sample, video);
    const archive = join(daemon.workdir, 'renders.zip');
    writeFileSync(archive, Buffer.from([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0]));
    const videoSize = statSync(video).size;

    const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    const title = '视频与压缩包';
    const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, model: 'fake/plain', title })).json();
    await post(`/api/threads/${thread.id}/turns`, { text: `做好了两样东西\n\n[火鸡视频](<${video}>)\n\n[渲染结果](${archive})` });
    const deadline = Date.now() + 10_000;
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') {
      assert.ok(Date.now() < deadline, 'fake agent finished');
      await new Promise((resolve) => setTimeout(resolve, 50));
    }

    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
        isMobile: mobile,
        hasTouch: mobile,
        colorScheme,
        acceptDownloads: true,
      });
      try {
        const page = await context.newPage();
        const shot = async (name) => {
          if (!process.env.CALLI_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        };
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: title }).click();

        // The video: played where it lies, not downloaded and not called binary.
        await page.locator('.markdown a', { hasText: '火鸡视频' }).click();
        const panel = page.locator('.files-panel');
        const player = panel.locator('.files-video video');
        await player.waitFor();
        assert.equal(await panel.getByText(/二进制文件/).count(), 0, 'a video is not shown as an unviewable binary');
        // Playback proves the daemon streamed it by range: a whole-file-only server stalls here.
        await player.evaluate(async (el) => {
          el.muted = true;
          await el.play();
        });
        await page.waitForFunction(() => (document.querySelector('.files-video video')?.currentTime ?? 0) > 0.05);
        await shot('file-video');

        // Save from the panel: the browser gets a file with the name the server chose.
        const save = page.waitForEvent('download');
        if (mobile) {
          await panel.getByRole('button', { name: '文件的更多操作' }).click();
          await page.getByRole('menuitem', { name: '下载文件', exact: true }).click();
        } else await panel.getByRole('button', { name: '下载文件', exact: true }).first().click();
        const download = await save;
        assert.equal(download.suggestedFilename(), '火鸡与Claude神.mp4');
        await download.cancel();
        if (mobile) await page.keyboard.press('Escape');

        await panel.getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
        await panel.waitFor({ state: 'detached' });

        // A binary file: still not readable here, but no longer a dead end.
        await page.locator('.markdown a', { hasText: '渲染结果' }).click();
        await panel.getByText('二进制文件（8 B），不能在这里查看', { exact: true }).waitFor();
        const binaryDownload = page.waitForEvent('download');
        await panel.getByRole('button', { name: '下载文件', exact: true }).last().click();
        const zip = await binaryDownload;
        assert.equal(zip.suggestedFilename(), 'renders.zip');
        await zip.cancel();
        await shot('file-binary-download');
        if (mobile) {
          await panel.getByRole('button', { name: '文件的更多操作' }).click();
          await page.getByRole('menuitem', { name: '浏览文件夹', exact: true }).click();
          await panel.getByRole('tree').waitFor();
          await panel.getByRole('button', { name: '回到文件', exact: true }).click();
        }
        await panel.getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
        await panel.waitFor({ state: 'detached' });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      } finally {
        await context.close();
      }
    }
    assert.ok(videoSize > 1000, 'the sample video is a real file');
  } finally {
    await browser?.close();
    await daemon?.stop();
  }
});
