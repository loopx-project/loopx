import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

const sample = join(repoRoot, 'daemon/test/fixtures/sample-video.mp4');

test('videos can be picked, previewed, retried and played from history on desktop and phone, light and dark', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `视频 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, model: 'fake/plain', title }) })).json();
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        let uploads = 0, reads = 0;
        page.on('request', req => { if (req.url().endsWith('/videos')) uploads++; if (req.url().includes('/attachments/')) reads++; });
        const select = async () => {
          if (mobile) await page.locator('.menu-btn').click();
          await page.locator('.thread-item', { hasText: title }).click();
        };
        const shot = async name => {
          if (!process.env.CALLI_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        };
        await openUi(page, daemon);
        await select();
        const picker = page.locator('.composer input[type=file]');
        assert.equal(await picker.getAttribute('accept'), null, 'the picker takes any file');
        await picker.setInputFiles(sample);
        await page.getByRole('button', { name: '预览视频：sample-video.mp4', exact: true }).waitFor();
        await shot('video-picked');
        await page.getByRole('button', { name: '预览视频：sample-video.mp4', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'sample-video.mp4', exact: true });
        await dialog.locator('video').evaluate(async video => { video.muted = true; await video.play(); });
        await page.waitForFunction(() => document.querySelector('.video-preview video')?.currentTime > 0.1);
        await shot('video-preview');
        await dialog.getByRole('button', { name: '关闭', exact: true }).click();
        await page.locator('.composer textarea').fill('帮我看看这个录屏');
        let failOnce = true;
        await page.route(`**/api/threads/${thread.id}/turns`, route => {
          if (failOnce) { failOnce = false; return route.fulfill({ status: 503, json: { error: '稍后重试' } }); }
          return route.continue();
        });
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.getByText('稍后重试', { exact: true }).waitFor();
        assert.equal(await page.locator('.video-attachment').count(), 1, 'failed send retains the selected video');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.locator('.video-attachment').waitFor({ state: 'detached' });
        const play = page.getByRole('button', { name: '播放视频：sample-video.mp4', exact: true });
        await play.waitFor();
        assert.equal(uploads, 1, 'retry reuses the completed upload');
        assert.equal(reads, 0, 'history does not fetch video bytes until playback');
        await play.click();
        await dialog.locator('video').evaluate(async video => { video.muted = true; await video.play(); });
        await page.waitForFunction(() => document.querySelector('.video-preview video')?.currentTime > 0.1);
        await shot('video-sent');
        await dialog.getByRole('button', { name: '关闭', exact: true }).click();
        const previousReads = reads;
        await page.reload();
        await select();
        await play.waitFor();
        assert.equal(reads, previousReads, 'reloading only loads the video reference');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});

test('a queued mobile video can be taken back, previewed and sent immediately without reuploading', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '视频排队' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    let uploads = 0;
    page.on('request', req => { if (req.url().endsWith('/videos')) uploads++; });
    await openUi(page, daemon);
    await page.locator('.menu-btn').click();
    await page.locator('.thread-item', { hasText: '视频排队' }).click();
    await page.locator('.composer input[type=file]').setInputFiles(sample);
    await page.getByRole('button', { name: '排队发送', exact: true }).click();
    await page.locator('.queue-item').waitFor();
    assert.match(await page.locator('.queue-item').innerText(), /1 个视频/);
    await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
    await page.getByRole('button', { name: '预览视频：sample-video.mp4', exact: true }).waitFor();
    await page.getByRole('button', { name: '排队发送', exact: true }).click();
    await page.getByRole('button', { name: '立即发送', exact: true }).click();
    await page.getByRole('button', { name: '播放视频：sample-video.mp4', exact: true }).waitFor();
    assert.equal(uploads, 1);
  } finally { await browser?.close(); await daemon.stop(); }
});

test('a new thread retains its video draft when the first upload fails', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await openUi(page, daemon);
    let failOnce = true;
    await page.route('**/api/threads/*/videos', route => {
      if (failOnce) { failOnce = false; return route.fulfill({ status: 503, json: { error: '上传中断，请重试' } }); }
      return route.continue();
    });
    await page.locator('.composer input[type=file]').setInputFiles(sample);
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByText('上传中断，请重试', { exact: true }).waitFor();
    await page.getByRole('button', { name: '预览视频：sample-video.mp4', exact: true }).waitFor();
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByRole('button', { name: '播放视频：sample-video.mp4', exact: true }).waitFor();
    assert.equal((await (await api(daemon, '/api/threads')).json()).length, 1, 'retry stays in the newly created thread');
  } finally { await browser?.close(); await daemon.stop(); }
});
