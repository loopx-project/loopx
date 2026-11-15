import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

test('renaming preserves caret, selection and focus through streamed task updates', async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const title = `重命名验收 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) })).json();
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      const openRename = async () => {
        // Renaming sits in the open thread's own "…", beside its list tools.
        await page.getByLabel('这个线程的更多操作').click();
        await page.getByRole('menuitem', { name: '重命名', exact: true }).click();
      };
      await openRename();
      const input = page.getByRole('textbox', { name: '线程名称', exact: true });
      const selection = () => input.evaluate(el => [el.selectionStart, el.selectionEnd]);
      assert.deepEqual(await selection(), [0, title.length], 'select the original title on opening');
      await input.fill('新的任务名称');
      await input.press('ArrowLeft');
      const caret = await selection();
      // Start the stream after editing, so its updates necessarily arrive with the dialog open.
      assert.ok((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slowthink rename' }) })).ok);
      await page.getByText('hi there slowthink rename', { exact: true }).waitFor();
      assert.deepEqual(await selection(), caret, 'streamed text must not select the draft again');
      await input.press('x');
      assert.equal(await input.inputValue(), '新的任务名x称', 'typing inserts at the preserved caret');
      await input.evaluate(el => el.setSelectionRange(1, 3));
      await page.locator('.thread .work-body .step', { hasText: 'npm test' }).waitFor();
      assert.deepEqual(await selection(), [1, 3], 'tool updates preserve the user selection');
      const cancel = page.getByRole('button', { name: '取消', exact: true });
      await cancel.focus();
      await page.locator('.thread .work-toggle', { hasText: '用时' }).waitFor();
      assert.equal(await cancel.evaluate(el => document.activeElement === el), true, 'completion must not steal focus');
      if (process.env.CALLI_E2E_SHOTS_DIR) {
        mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `rename-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      }
      await page.getByRole('button', { name: '保存', exact: true }).click();
      await input.waitFor({ state: 'hidden' });
      assert.equal((await (await api(daemon, `/api/threads/${thread.id}`)).json()).title, '新的任务名x称');
      await openRename();
      assert.deepEqual(await selection(), [0, '新的任务名x称'.length], 'reopening selects the saved title');
      await page.keyboard.press('Escape');
      await input.waitFor({ state: 'hidden' });
    } finally { await context.close(); }
  }
});
