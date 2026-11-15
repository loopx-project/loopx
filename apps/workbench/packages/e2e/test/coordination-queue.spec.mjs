import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { openCollaboration, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('queued collaboration restores text and target tasks separately in all viewports', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const post = async (path, body) => { const res = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) }); assert.ok(res.ok, await res.clone().text()); return res.json(); };
  try {
    for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
      try {
        const title = `协作撤回 ${phone}-${colorScheme}`;
        const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
        const page = await context.newPage();
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.thread-row', { hasText: title }).click();
        await openCollaboration(page);
        await page.getByRole('button', { name: '协作设置', exact: true }).click();
        await page.getByRole('button', { name: '启用协作', exact: true }).click();
        await page.locator('.composer-add.on').waitFor();
        await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
        const mentions = [{ id: 'claude', label: 'Claude Code', kind: 'agent', cwd: daemon.workdir, task: '总结经验，先不要自己做' }];
        await post(`/api/threads/${thread.id}/turns`, { text: '让 Claude 参与分析', mentions, queue: true });
        const box = page.getByRole('textbox', { name: '消息', exact: true });
        await box.fill('已有草稿');
        await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
        const task = page.getByRole('textbox', { name: '给 Claude Code 的任务', exact: true });
        await task.waitFor();
        assert.equal(await task.inputValue(), mentions[0].task);
        assert.equal(await box.inputValue(), '让 Claude 参与分析\n\n已有草稿');
        await task.fill('修改后只给建议');
        const sent = page.waitForRequest(r => r.url().endsWith(`/threads/${thread.id}/turns`) && r.method() === 'POST');
        await page.getByRole('button', { name: '排队发送', exact: true }).click();
        const body = (await sent).postDataJSON();
        assert.equal(body.text.includes('[指定协作对象]'), false);
        assert.equal(body.mentions[0].task, '修改后只给建议');
        await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
        await task.waitFor();
        await page.waitForTimeout(500);
        mkdirSync('/tmp/mention-queue-shots', { recursive: true });
        await page.screenshot({ path: `/tmp/mention-queue-shots/${phone ? 'phone' : 'desktop'}-${colorScheme}.png` });
        await api(daemon, `/api/threads/${thread.id}/cancel`, { method: 'POST' });
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
