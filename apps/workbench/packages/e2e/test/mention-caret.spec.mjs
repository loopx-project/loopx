import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser, openCollaboration } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });
const post = async (path, body) => {
  const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`collaboration menu distinguishes existing and new agents and preserves the draft: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    const page = await context.newPage();
    try {
      const title = `光标协作 ${phone} ${colorScheme}`;
      const root = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
      await post(`/api/threads/${root.id}/coordination`, { directories: [daemon.workdir] });
      const directive = Buffer.from(JSON.stringify({ args: ['spawn', '--agent', 'claude', '--name', 'claude', '--message-file', '-', '--request-id', 'caret-review'], text: '检查接口 LOOCHAT_REPORT' })).toString('base64');
      await post(`/api/threads/${root.id}/turns`, { text: `LOOCHAT_COMMAND:${directive}` });
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
      const draft = '先看代码，再看测试。';
      await box.fill(draft);
      await openCollaboration(page);
      const list = page.getByRole('menu', { name: '协作', exact: true });
      await list.getByText('当前成员', { exact: true }).waitFor();
      await list.getByText('添加助手', { exact: true }).waitFor();
      const groups = list.locator('.coord-picker-list > div');
      const existing = groups.nth(0).getByRole('menuitem', { name: /claude/i });
      const fresh = groups.nth(1).getByRole('menuitem', { name: /claude/i });
      await existing.waitFor();
      await fresh.waitFor();
      if (phone) await page.setViewportSize({ width: 390, height: 520 });
      const shots = '/tmp/loochat-mention-caret-shots'; mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}.png` });
      const rect = await list.boundingBox();
      assert.ok(rect.y >= 0 && rect.y + rect.height <= page.viewportSize().height, `collaboration menu fits: ${JSON.stringify(rect)}`);
      await existing.click();
      assert.equal(await box.inputValue(), draft);
      await page.locator('.coord-row textarea').fill('继续检查已有结论');
      await openCollaboration(page);
      await groups.nth(0).locator('.coord-pick.chosen').waitFor();
      await fresh.click();
      assert.equal(await box.inputValue(), draft);
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
