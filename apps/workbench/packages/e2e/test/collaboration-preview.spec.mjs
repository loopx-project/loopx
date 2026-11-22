import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon({ FAKE_PI_VISIBLE_ECHO: '1' }); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });
const json = async (path, body) => {
  const response = await api(daemon, path, body ? { method: 'POST', body: JSON.stringify(body) } : {});
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};
const idle = async id => {
  for (let n = 0; n < 200; n++) {
    if ((await json(`/api/threads/${id}`)).status === 'idle') return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail('conversation did not become idle');
};

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`referenced conversation preserves the original reader and draft: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(15_000);
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      const target = await json('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: `接口审查 ${phone ? '手机' : '桌面'} · ${colorScheme}` });
      await json(`/api/threads/${target.id}/turns`, { text: Array.from({ length: 30 }, (_, i) => `审查记录 ${i + 1}：检查接口约定与异常处理。`).join('\n\n') });
      await idle(target.id);
      const root = await json('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: `协作主对话 ${phone ? '手机' : '桌面'} · ${colorScheme}` });
      await json(`/api/threads/${root.id}/turns`, { text: `查看相关工作：[阅读审查](thread://${target.id})。\n\n| 交付物 | 讨论 |\n| --- | --- |\n| 接口 | [表格引用](thread://${target.id}) |` });
      await idle(root.id);
      await openUi(page, daemon);
      if (phone) await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: root.title }).click();
      const main = page.locator('main');
      const box = main.getByRole('textbox', { name: '消息', exact: true });
      await box.fill('主对话未发送的草稿');
      const link = main.locator('.row.assistant .thread-link', { hasText: '阅读审查' });
      await link.scrollIntoViewIfNeeded();
      const scrollTop = await main.locator('.scroller').evaluate(el => el.scrollTop);
      const count = (await json(`/api/threads/${target.id}/events`)).filter(e => e.type === 'turn.started').length;
      // Failure is visible and retriable within the panel, without navigating away.
      await page.route(`**/api/threads/${target.id}`, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"temporarily unavailable"}' }), { times: 1 });
      await link.click();
      const panel = page.locator('.agent-panel');
      await panel.getByRole('alert').waitFor();
      assert.equal(await box.inputValue(), '主对话未发送的草稿');
      await panel.getByRole('button', { name: '重试', exact: true }).click();
      await panel.getByText('审查记录 30：检查接口约定与异常处理。', { exact: false }).last().waitFor();
      const memberBox = panel.getByRole('textbox', { name: '消息', exact: true });
      await memberBox.fill('审查对话自己的草稿');
      assert.equal((await json(`/api/threads/${target.id}/events`)).filter(e => e.type === 'turn.started').length, count, 'opening the reader never starts work');
      if (phone) {
        assert.equal(await main.evaluate(el => el.inert), true);
        const bounds = await panel.boundingBox();
        assert.ok(bounds.x === 0 && Math.abs(bounds.width - 390) < 2);
      } else {
        assert.equal(await main.evaluate(el => el.inert), false);
        const body = panel.locator('.agent-panel-body');
        await body.evaluate(el => { el.scrollTop = 0; });
        assert.equal(await main.locator('.scroller').evaluate(el => el.scrollTop), scrollTop, 'each reader owns its scroll');
      }
      const shots = process.env.LOOCHAT_E2E_SHOTS_DIR ?? '/tmp/loochat-collaboration-preview';
      mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${colorScheme}-reference.png`) });
      await panel.getByRole('button', { name: '返回对话', exact: true }).click();
      await panel.waitFor({ state: 'detached' });
      assert.equal(await main.evaluate(el => el.inert), false);
      assert.equal(await box.inputValue(), '主对话未发送的草稿');
      assert.ok(Math.abs(await main.locator('.scroller').evaluate(el => el.scrollTop) - scrollTop) <= 2);
      assert.equal(await link.evaluate(el => el === document.activeElement), true);
      // Keyboard and table links use the same reader; the two drafts remain independent.
      const tableLink = main.locator('.row.assistant .thread-link', { hasText: '表格引用' });
      await tableLink.focus(); await tableLink.press('Enter');
      await memberBox.waitFor();
      assert.equal(await memberBox.inputValue(), '审查对话自己的草稿');
      const sent = page.waitForRequest(req => req.url().endsWith(`/threads/${target.id}/turns`) && req.method() === 'POST');
      await panel.getByRole('button', { name: '发送', exact: true }).click();
      assert.equal((await sent).postDataJSON().text, '审查对话自己的草稿');
      await idle(target.id);
      await page.keyboard.press('Escape');
      await panel.waitFor({ state: 'detached' });
      assert.equal(await box.inputValue(), '主对话未发送的草稿');
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
