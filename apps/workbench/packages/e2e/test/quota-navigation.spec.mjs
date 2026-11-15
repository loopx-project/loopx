import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

test('chat quota and account pages share refreshes and preserve the conversation across layouts', { timeout: 180000 }, async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'codex', adapter: 'codex', label: 'Primary', command },
    { id: 'backup', adapter: 'codex', label: 'Backup', command },
    { id: 'hidden', adapter: 'codex', label: 'Hidden', hidden: true, command },
  ] }) });
  const browser = await launchBrowser();
  const shots = process.env.CALLI_E2E_SHOTS_DIR || '/tmp/calli-quota-navigation'; mkdirSync(shots, { recursive: true });
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title: 'Quota navigation return' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: Array.from({ length: 60 }, (_, index) => `Review paragraph ${index + 1}`).join('\n\n') }) });
    for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
      const mobile = width < 700;
      const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      const page = await context.newPage(); page.setDefaultTimeout(15000);
      const errors = [], requests = []; let used = 82, fail = false, backupFailed = true, missingShort = false, empty = false;
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/agents/*/quota*', async route => {
        const url = new URL(route.request().url()); const agent = url.pathname.split('/')[3]; requests.push(agent);
        if (agent === 'codex' && fail || agent === 'backup' && backupFailed) return route.fulfill({ status: 503, json: { error: 'Offline for test' } });
        if (url.searchParams.has('refresh') && agent === 'codex') used += 1;
        return route.fulfill({ json: { plan: { id: 'test-plan', label: 'Test plan' }, checkedAt: new Date().toISOString(), windows: agent === 'backup' || empty ? [] : [
          ...(!missingShort ? [{ label: '5H', usedPercent: 25, resetsAt: new Date(Date.now() + 2 * 3600000).toISOString() }] : []),
          // An older server or cached reading still names the global weekly scope this way.
          { label: '7D', scope: '全部模型', usedPercent: used, resetsAt: new Date(Date.now() + 3 * 86400000).toISOString() },
          { label: '7D', scope: 'Review', usedPercent: 10 },
        ] } });
      });
      await openUi(page, daemon); await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar .thread-item', { hasText: 'Quota navigation return' }).click();
      await page.locator('.composer textarea').fill('Draft survives quota');
      assert.equal(requests.length, 0, 'chat does not subscribe to quotas before opening the menu');
      assert.equal(await page.locator('.topbar [role=meter], .chat-quota-preview').count(), 0);
      await page.screenshot({ path: `${shots}/header-${width}-${theme}.png` });
      assert.equal(await page.locator('.composer .chat-quota-trigger').count(), 0);
      assert.equal(requests.includes('backup'), false, 'chat does not load other account quotas');
      assert.equal(requests.includes('hidden'), false);
      await page.waitForFunction(() => document.querySelector('.scroller')?.scrollHeight > innerHeight);
      await page.locator('.scroller').hover(); await page.mouse.wheel(0, -350);
      await page.waitForTimeout(150); const scroll = await page.locator('.scroller').evaluate(el => el.scrollTop);
      const openQuick = async () => {
        await page.getByRole('button', { name: '这个线程的更多操作', exact: true }).click();
      };
      await openQuick(); const quick = page.locator('.quota-menu-preview');
      await quick.getByRole('meter').filter({ hasText: '18%' }).waitFor();
      assert.deepEqual([...new Set(requests)], ['codex'], 'opening the menu reads only the current account');
      assert.equal(await quick.getByRole('meter').count(), 2, 'both main periods are visible immediately');
      assert.equal(await quick.locator(':scope > .quota-preview > .quota-pair .quota-missing').count(), 0, 'legacy account-wide periods are not split into false missing pairs');
      assert.equal(await quick.locator('summary, .quota-meter-reset').count(), 0, 'reset details and other pools are reached through the full page');
      assert.equal(await page.getByRole('dialog').count(), 0);
      const bounds = await page.locator('.menu-float').boundingBox(); assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1);
      await page.screenshot({ path: `${shots}/quick-${width}-${theme}.png` });
      await quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true }).click();
      await quick.getByRole('meter').filter({ hasText: '17%' }).waitFor();
      await quick.getByRole('menuitem', { name: '查看全部额度', exact: true }).click();
      const list = page.getByRole('region', { name: '额度', exact: true }); await list.waitFor();
      assert.equal(await page.locator('.jump-btn').count(), 0, 'chat controls stay out of the settings page');
      assert.equal(await list.locator('.quota-page-filters').evaluate(el => el.scrollWidth > el.clientWidth + 1), false, 'search and filters stay inside the phone page');
      assert.equal(await page.locator('.sidebar').isVisible(), false);
      await list.locator('.quota-account-row', { hasText: 'Primary' }).getByRole('meter').filter({ hasText: '17%' }).waitFor();
      assert.equal(await list.locator('.quota-account-row', { hasText: 'Hidden' }).count(), 0);
      assert.equal(requests.includes('hidden'), false);
      await list.getByRole('searchbox', { name: '搜索账号', exact: true }).fill('not-an-account');
      await list.getByText('没有匹配的账号', { exact: true }).waitFor();
      await list.getByRole('searchbox', { name: '搜索账号', exact: true }).fill('');
      await page.screenshot({ path: `${shots}/list-${width}-${theme}.png` });
      const primary = list.locator('.quota-account-row', { hasText: 'Primary' });
      assert.equal(await primary.getByRole('meter').count(), 2);
      assert.equal(await primary.evaluate(el => el.scrollWidth > el.clientWidth + 1), false, 'the account row fits without clipping either period');
      await primary.locator('summary').click();
      assert.equal(await primary.getByRole('meter').count(), 3);
      await primary.getByRole('button', { name: '查看 Primary 的额度详情', exact: true }).click();
      const detail = page.getByRole('region', { name: 'Primary', exact: true }); await detail.waitFor();
      assert.equal(await detail.getByRole('meter').count(), 3);
      fail = true; await detail.getByRole('button', { name: '刷新 Primary 的额度', exact: true }).click();
      await detail.getByText('刷新失败，显示上次结果', { exact: true }).waitFor();
      assert.ok((await detail.innerText()).includes('17%'));
      fail = false; await detail.getByRole('button', { name: '重试', exact: true }).click();
      await detail.getByRole('meter').filter({ hasText: '16%' }).waitFor();
      await page.screenshot({ path: `${shots}/detail-${width}-${theme}.png` });
      await page.goBack(); await list.waitFor();
      await list.getByRole('button', { name: '查看 Backup 的额度详情', exact: true }).click();
      const backup = page.getByRole('region', { name: 'Backup', exact: true });
      await backup.getByText('额度暂不可用', { exact: true }).waitFor();
      backupFailed = false; await backup.getByRole('button', { name: '重试', exact: true }).click();
      await backup.getByText('服务未提供额度信息', { exact: true }).waitFor(); assert.equal(await backup.getByRole('meter').count(), 0);
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click(); await list.waitFor();
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await page.locator('.composer textarea').waitFor();
      assert.equal(await page.locator('.composer textarea').inputValue(), 'Draft survives quota');
      await openQuick();
      await quick.getByRole('meter').filter({ hasText: '16%' }).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.topbar [role=meter], .chat-quota-preview').count(), 0);
      assert.ok(Math.abs(await page.locator('.scroller').evaluate(el => el.scrollTop) - scroll) < 2, 'reading position is restored');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (width === 320 && theme === 'dark') {
        await openQuick();
        fail = true;
        await quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true }).click();
        await quick.getByText('刷新失败 · 旧值', { exact: true }).waitFor();
        assert.equal(await quick.getByRole('meter').count(), 2, 'failed reads retain both cached periods');
        fail = false; used = 99;
        await quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true }).click();
        await quick.getByRole('meter').filter({ hasText: /^每周0%/ }).waitFor();
        assert.equal(await quick.getByRole('meter', { name: '全部模型 · 每周', exact: true }).getAttribute('aria-valuenow'), '0');
        missingShort = true;
        await quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true }).click();
        await quick.locator(':scope > .quota-preview > .quota-pair').getByText('未提供', { exact: true }).waitFor();
        assert.equal(await quick.getByRole('meter').count(), 1, 'a missing short period has no invented zero meter');
        empty = true;
        await quick.getByRole('menuitem', { name: '刷新 Primary 的额度', exact: true }).click();
        await quick.getByText('服务未提供额度信息', { exact: true }).waitFor();
        assert.equal(await quick.getByRole('meter').count(), 0);
        await page.keyboard.press('Escape');
      }
      assert.deepEqual(errors, []); await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});
