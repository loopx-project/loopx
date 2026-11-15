import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';

const label = '项目 Advisor / SystemOne 实验交接：换机器恢复与继续操作';
const destination = 'https://docs.example.com/docx/handover';

test('document links open externally without file requests; literal URLs and local files still work in every viewport and theme', { timeout: 120_000 }, async () => {
  let daemon, browser;
  try {
    daemon = await startDaemon({}, { agents: [{ id: 'pi', adapter: 'pi', command: ['fake-pi'] }] });
    const { Store } = await import(join(repoRoot, 'daemon', 'dist', 'store.js'));
    const localFile = join(daemon.workdir, 'handover.md');
    writeFileSync(localFile, '# Local handover\n\nRestore the code first.\n');
    const store = new Store(daemon.dataDir);
    try {
      store.createThread({ id: 'links', agent: 'pi', cwd: daemon.workdir, title: '链接回归', permissionMode: 'auto_edit', model: null, effort: null });
      store.appendEvent('links', 'reply', 'turn.started', { text: '查看交接文档', imported: true });
      store.appendEvent('links', 'reply', 'message.delta', { text: [
        `交接文档：[${label}](${destination})。`,
        '**https://docs.example.com/run/**（进程）',
        'www.docs.example.com/help，继续。',
        `[本地文件：恢复步骤](${localFile})`,
      ].join('\n\n') });
      store.appendEvent('links', 'reply', 'turn.done', { stopReason: 'end_turn', imported: true });
    } finally { store.close(); }
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        await context.addInitScript(() => localStorage.setItem('calli_onboard_dismissed', '1'));
        // External navigation is real, but its response is local and deterministic.
        await context.route('**://docs.example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<p>Document opened</p>' }));
        const page = await context.newPage();
        const fileRequests = [], errors = [];
        page.on('request', request => {
          if (/\/(?:text|image)\?/.test(request.url())) fileRequests.push(new URL(request.url()).searchParams.get('path'));
        });
        page.on('pageerror', error => errors.push(error.message));
        await openUi(page, daemon);
        await page.goto(`${daemon.base}/#/thread/links`);
        const answer = page.locator('.row.assistant .markdown');
        const link = answer.getByRole('link', { name: label, exact: true });
        await link.waitFor();
        assert.equal(await link.textContent(), label);
        assert.equal(await link.getAttribute('href'), destination);
        assert.equal(await link.getAttribute('target'), '_blank');
        assert.equal(await link.getAttribute('role'), null);
        const originalPage = page.url();
        const popupPromise = page.waitForEvent('popup');
        await link.click();
        const popup = await popupPromise;
        await popup.getByText('Document opened', { exact: true }).waitFor();
        assert.equal(popup.url(), destination);
        assert.equal(await popup.evaluate(() => window.opener === null), true);
        await popup.close();
        assert.equal(page.url(), originalPage);
        assert.deepEqual(fileRequests, [], 'an external document never enters the local-file API');
        assert.equal(await page.locator('.file-link-note').count(), 0);
        assert.equal(await answer.locator('strong a').getAttribute('href'), 'https://docs.example.com/run/');
        assert.equal(await answer.getByRole('link', { name: 'www.docs.example.com/help', exact: true }).getAttribute('href'), 'http://www.docs.example.com/help');
        assert.ok((await answer.textContent()).includes('（进程）'));
        assert.ok(!(await answer.textContent()).includes('**'));
        if (process.env.CALLI_E2E_SHOTS_DIR) {
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `markdown-links-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        }
        await answer.getByRole('button', { name: '本地文件：恢复步骤', exact: true }).click();
        await page.locator('.files-panel').getByText('Restore the code first.', { exact: false }).waitFor();
        assert.ok(fileRequests.includes(localFile), 'local links still open the file viewer');
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally {
    await browser?.close();
    await daemon?.stop();
  }
});
