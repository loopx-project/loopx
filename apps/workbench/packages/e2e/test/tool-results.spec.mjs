import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

test('tool results stay at the bottom right of expanded details, including empty and scrolling output', async () => {
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] }] }) });
  let browser;
  try {
    browser = await launchBrowser();
    const make = async (title, text, running = false) => {
      const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title }) })).json();
      assert.ok((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) })).ok);
      if (running) return;
      const deadline = Date.now() + 10_000;
      let state;
      do {
        state = await (await api(daemon, `/api/threads/${thread.id}`)).json();
        assert.ok(Date.now() < deadline, 'fake agent finished');
        if (state.status === 'running') await new Promise(resolve => setTimeout(resolve, 50));
      } while (state.status === 'running');
      assert.equal(state.status, 'idle', 'a failed tool need not fail the turn');
    };
    await make('工具执行记录', 'tool longoutput fail emptyfailure emptysuccess');
    await make('单条工具记录', 'fail');
    await make('修改文件记录', 'edit');
    await make('正在执行记录', 'livecommand slow', true);
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const shot = async name => {
          if (!process.env.CALLI_E2E_SHOTS_DIR) return;
          mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ animations: 'disabled', path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        };
        const select = async title => {
          if (mobile) await page.locator('.menu-btn').click();
          await page.locator('.thread-item', { hasText: title }).click();
        };
        const checkResult = async (step, label) => {
          const result = step.locator('.step-result');
          await result.waitFor();
          assert.equal(await result.innerText(), label);
          assert.equal(await result.evaluate(el => getComputedStyle(el).textAlign), 'right');
          const body = await step.locator('.step-detail-content').boundingBox();
          const footer = await result.boundingBox();
          assert.ok(footer.y >= body.y + body.height - 1, 'the result is below the scrollable content');
          assert.equal(await step.locator('.step-detail-content .step-result').count(), 0);
        };
        await openUi(page, daemon);
        await select('工具执行记录');
        await page.locator('.work-toggle').click();
        const group = page.locator('.run-head');
        await group.waitFor();
        assert.doesNotMatch(await group.innerText(), /失败/);
        assert.equal(await page.locator('.step-result').count(), 0);
        await shot('tools-summary');
        await group.click();
        const failed = page.locator('.step.failed').filter({ hasText: '运行了 false' });
        assert.equal(await failed.count(), 1, 'the actual result remains failed');
        assert.doesNotMatch(await failed.locator('.step-head').innerText(), /失败/);
        assert.equal(await page.locator('.step-result').count(), 0);
        assert.equal(await failed.locator('.step-status').evaluate(el => getComputedStyle(el).color), await page.locator('.step.completed .step-status').first().evaluate(el => getComputedStyle(el).color));
        await shot('tools-list');
        await failed.locator('.step-head').click();
        await checkResult(failed, '失败');
        const resultColor = await failed.locator('.step-result').evaluate(el => getComputedStyle(el).color);
        assert.match(await failed.locator('.step-detail').innerText(), /boom\s+\(exit 1\)/);
        await shot('tools-detail');
        await failed.locator('.step-head').click();
        const empty = page.locator('.step.failed').filter({ hasText: 'fixture · no-detail' });
        await empty.locator('.step-head').click();
        await empty.getByText('工具未返回详细信息', { exact: true }).waitFor();
        await checkResult(empty, '失败');
        await shot('tools-empty-failure');
        await empty.locator('.step-head').click();
        const emptySuccess = page.locator('.step.completed').filter({ hasText: 'fixture · no-output' });
        await emptySuccess.locator('.step-head').click();
        await emptySuccess.getByText('工具未返回详细信息', { exact: true }).waitFor();
        await checkResult(emptySuccess, '成功');
        await emptySuccess.locator('.step-head').click();
        const success = page.locator('.step.completed').filter({ hasText: '运行了 npm test' });
        await success.locator('.step-head').click();
        await checkResult(success, '成功');
        assert.equal(await success.locator('.step-result').evaluate(el => getComputedStyle(el).color), resultColor);
        assert.match(await success.locator('.step-detail').innerText(), /partial out/);
        await shot('tools-success');
        const before = await success.locator('.step-result').boundingBox();
        const scrollTop = await success.locator('.step-detail-content').evaluate(el => { el.scrollTop = el.scrollHeight; return el.scrollTop; });
        assert.ok(scrollTop > 0, 'long output scrolls inside the card');
        assert.equal((await success.locator('.step-result').boundingBox()).y, before.y, 'scrolling does not move the result');
        await shot('tools-scrolled');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await select('单条工具记录');
        await page.locator('.work-toggle').click();
        assert.equal(await page.locator('.run-head').count(), 0);
        assert.doesNotMatch(await page.locator('.step-head').innerText(), /失败/);
        await page.locator('.step-head').click();
        await checkResult(page.locator('.step'), '失败');
        await select('修改文件记录');
        await page.locator('.work-toggle').click();
        await page.locator('.step-head').click();
        await page.locator('.step-detail .diff').waitFor();
        await checkResult(page.locator('.step'), '成功');
        await shot('tools-diff');
        await select('正在执行记录');
        // A running turn opens its steps by itself once the thread has drawn.
        await page.locator('.work-toggle[aria-expanded="true"]').waitFor();
        await page.locator('.step-head').click();
        await page.locator('.step-detail').getByText('Compiling…', { exact: true }).waitFor();
        assert.equal(await page.locator('.step-result').count(), 0, 'running calls have no final result');
        assert.equal(await page.locator('.step-status').innerText(), '运行中');
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
