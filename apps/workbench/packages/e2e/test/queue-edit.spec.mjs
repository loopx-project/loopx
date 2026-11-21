import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

const sample = join(repoRoot, 'daemon/test/fixtures/sample-video.mp4');

test('taking a waiting message back is immediate: the row goes and the text is in the box before the thread list reloads', async () => {
  const daemon = await startDaemon();
  let browser;
  try {
    browser = await launchBrowser();
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '撤回排队' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await openUi(page, daemon);
    await page.locator('.thread-item', { hasText: '撤回排队' }).click();
    await page.locator('.composer input[type=file]').setInputFiles(sample);
    await page.locator('.composer textarea').fill('主要是支持不用登录官方配置的 Claude Code');
    await page.getByRole('button', { name: '排队发送', exact: true }).click();
    await page.locator('.queue-item').waitFor();

    // A slow thread list and a slow video download, as on a big hub over a VPN.
    let slow = true;
    const hold = ms => new Promise(resolve => setTimeout(resolve, ms));
    await page.route(url => url.pathname === '/api/threads', async route => { if (slow) await hold(4000); await route.continue(); });
    await page.route('**/attachments/**', async route => { if (slow) await hold(2500); await route.continue(); });
    const started = Date.now();
    await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
    await page.locator('.queue-item').waitFor({ state: 'detached', timeout: 1000 });
    await page.waitForFunction(() => document.querySelector('.composer textarea')?.value.includes('Claude Code'), null, { timeout: 1500 });
    assert.ok(Date.now() - started < 2000, 'the text does not wait for the video or the list');
    assert.equal(await page.locator('.queue-item').count(), 0, 'the row does not come back while the list is still loading');
    await page.getByRole('button', { name: '预览视频：sample-video.mp4', exact: true }).waitFor();
    slow = false;
    await hold(4500);
    assert.equal(await page.locator('.queue-item').count(), 0);
  } finally { await browser?.close(); await daemon.stop(); }
});
