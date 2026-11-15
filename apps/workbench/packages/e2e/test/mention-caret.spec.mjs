import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });
const post = async (path, body) => {
  const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`caret mentions distinguish existing and new agents: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    const page = await context.newPage();
    try {
      const title = `光标协作 ${phone} ${colorScheme}`;
      const root = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
      await post(`/api/threads/${root.id}/coordination`, { directories: [daemon.workdir] });
      const directive = Buffer.from(JSON.stringify({ args: ['spawn', '--agent', 'claude', '--name', 'claude', '--message-file', '-', '--request-id', 'caret-review'], text: '检查接口 CALLI_REPORT' })).toString('base64');
      await post(`/api/threads/${root.id}/turns`, { text: `CALLI_COMMAND:${directive}` });
      let team;
      for (let n = 0; n < 200; n++) {
        team = await (await api(daemon, `/api/threads/${root.id}/coordination`)).json();
        if (team.members.length && team.members[0].latest?.state === 'returned') break;
        await new Promise(r => setTimeout(r, 50));
      }
      assert.equal(team.members.length, 1);
      const child = team.members[0].id;
      await openUi(page, daemon);
      if (phone) await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: title }).click();
      const box = page.getByRole('textbox', { name: '消息', exact: true });
      const list = page.getByRole('listbox', { name: '选择协作智能体' });
      await box.fill('@claude 后文');
      assert.equal(await list.count(), 0);
      await box.evaluate(el => el.setSelectionRange(7, 7));
      await box.press('ArrowLeft');
      await list.waitFor();
      const draft = '另外，请审查当前方案，包括代码和测试。';
      await box.fill(draft);
      await box.evaluate(el => { el.focus(); el.setSelectionRange(0, 0); });
      await box.pressSequentially('@');
      await list.waitFor();
      await list.getByText('已有成员', { exact: true }).waitFor();
      await list.getByText('新增智能体', { exact: true }).waitFor();
      const existing = list.getByRole('option', { name: 'claude 给当前成员追加任务', exact: true });
      const fresh = list.getByRole('option', { name: /claude.*创建新成员/i });
      await existing.waitFor();
      await fresh.waitFor();
      assert.equal(await box.getAttribute('aria-expanded'), 'true');
      // A reduced visual viewport represents the space left above a phone keyboard.
      if (phone) await page.setViewportSize({ width: 390, height: 520 });
      const shots = '/tmp/calli-mention-caret-shots'; mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}.png` });
      const rect = await list.boundingBox();
      assert.ok(rect.y >= 0 && rect.y + rect.height <= page.viewportSize().height, `mention menu fits: ${JSON.stringify(rect)}`);
      await box.press('ArrowUp');
      const last = list.getByRole('option').last();
      assert.equal(await last.getAttribute('aria-selected'), 'true');
      const lastRect = await last.boundingBox();
      assert.ok(lastRect.y >= rect.y && lastRect.y + lastRect.height <= rect.y + rect.height + 1, 'keyboard selection scrolls into view');
      await box.press('ArrowDown');
      await existing.click();
      assert.equal(await box.inputValue(), draft);
      await page.locator('.coord-row textarea').fill('继续检查已有结论');
      // Inserting a query in the middle preserves its suffix, and Tab chooses the new instance.
      const middle = '先看代码  再看测试';
      await box.fill(middle);
      await box.evaluate(el => el.setSelectionRange(5, 5));
      await box.pressSequentially('@claude');
      await fresh.waitFor();
      assert.equal(await existing.count(), 0, 'the selected existing member is not offered twice');
      await box.press('Tab');
      assert.equal(await box.inputValue(), middle);
      assert.equal(await page.locator('.coord-row').count(), 2);
      await page.locator('.coord-row textarea').nth(1).fill('独立审查方案');
      const sent = page.waitForRequest(req => req.url().endsWith(`/threads/${root.id}/turns`) && req.method() === 'POST');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const payload = (await sent).postDataJSON();
      assert.deepEqual(payload.mentions.map(({ id, kind, task }) => ({ id, kind, task })), [
        { id: child, kind: 'member', task: '继续检查已有结论' },
        { id: 'claude', kind: 'agent', task: '独立审查方案' },
      ]);
    } finally { await context.close(); }
  });
}
