import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('temporary images, reports and files elsewhere open from answers on desktop and phone, light and dark', async () => {
  const artifacts = mkdtempSync(join(tmpdir(), 'calli-preview-artifacts-'));
  let daemon, browser;
  try {
    daemon = await startDaemon();
    browser = await launchBrowser();
    const picture = join(artifacts, '截图 (1).svg');
    const report = join(artifacts, 'report.md');
    // A file outside both the thread and the temp folders, as an answer about a sibling worktree names one.
    const elsewhere = join(dirname(fileURLToPath(import.meta.url)), '..', 'harness.mjs');
    writeFileSync(picture, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300"><rect width="640" height="300" rx="16" fill="#25322f"/><text x="30" y="48" fill="#dae6dc" font-size="24">TPM</text><path d="M30 240 L110 210 L190 225 L270 100 L350 160 L430 70 L510 115 L610 45" stroke="#b2cf80" stroke-width="5" fill="none"/><script>window.artifactScriptRan = true</script></svg>');
    writeFileSync(report, '# 临时报告\n\n这份报告可以直接在当前线程中阅读。\n');
    const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    const thread = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
    await post(`/api/threads/${thread.id}/turns`, { text: `临时产物预览\n\n[查看截图](<${picture}>)\n\n[阅读报告](${report})\n\n[已清理的截图](${artifacts}/removed.png)\n\n[别处的脚本](${elsewhere})\n\n[已删除的报告](${artifacts}/removed.md)` });
    const deadline = Date.now() + 10_000;
    while ((await (await api(daemon, `/api/threads/${thread.id}`)).json()).status === 'running') {
      assert.ok(Date.now() < deadline, 'fake agent finished');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const shot = async name => {
          if (!process.env.CALLI_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        };
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: '临时产物预览' }).click();
        await page.locator('.file-image img').waitFor();
        await page.locator('.markdown a', { hasText: '查看截图' }).click();
        await page.locator('.lightbox img').waitFor();
        await page.waitForFunction(() => document.querySelector('.lightbox img')?.naturalWidth > 0);
        assert.equal(await page.evaluate(() => window.artifactScriptRan), undefined);
        await shot('temporary-image');
        await page.keyboard.press('Escape');
        await page.locator('.lightbox').waitFor({ state: 'detached' });
        await page.locator('.markdown a', { hasText: '阅读报告' }).click();
        const panel = page.locator('.files-panel');
        await panel.getByText('这份报告可以直接在当前线程中阅读。', { exact: true }).waitFor();
        await shot('temporary-report');
        if (mobile) {
          // On a phone the folder view is a step behind the file's own "more" menu; the popover
          // sits in an anchored layer of its own, not inside the panel.
          await panel.getByRole('button', { name: '文件的更多操作', exact: true }).click();
          await page.getByRole('menuitem', { name: '浏览文件夹', exact: true }).click();
          await panel.getByRole('tree').waitFor();
          assert.equal(await panel.getByText('打不开这个文件夹', { exact: true }).count(), 0);
          await panel.getByRole('button', { name: '回到文件', exact: true }).click();
        }
        await panel.getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
        await panel.waitFor({ state: 'detached' });
        await page.locator('.markdown a', { hasText: '已清理的截图' }).click();
        await page.locator('.file-link-note').filter({ hasText: '文件不存在' }).waitFor();
        await shot('temporary-missing');
        await page.locator('.markdown a', { hasText: '已删除的报告' }).click();
        await page.locator('.file-link-note').filter({ hasText: '文件不存在' }).waitFor();
        await shot('missing-report');
        await page.locator('.markdown a', { hasText: '别处的脚本' }).click();
        await panel.getByText('export async function startDaemon', { exact: false }).first().waitFor();
        await shot('elsewhere-file');
        await panel.getByRole('button', { name: mobile ? '关闭' : '关闭文件', exact: true }).click();
        await panel.waitFor({ state: 'detached' });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.reload();
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-item', { hasText: '临时产物预览' }).click();
        await page.locator('.file-image img').waitFor();
        await page.locator('.file-image button').click();
        await page.locator('.lightbox img').waitFor();
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
    rmSync(artifacts, { recursive: true, force: true });
  }
});
