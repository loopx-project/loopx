import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { openCollaboration, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const shots = process.env.CALLI_E2E_SHOTS_DIR ?? '/tmp/calli-board-collaboration-shots';
test('running unconfigured collaboration queues and restores target tasks in all layouts', { timeout: 90000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
  const post = async (path, body) => {
    const res = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(res.ok, await res.clone().text());
    return res.status === 204 ? null : res.json();
  };
  try {
    for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
      let page;
      try {
        const title = `运行中协作 ${phone}-${colorScheme}`;
        const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
        await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
        page = await context.newPage();
        page.setDefaultTimeout(10000);
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.thread-row', { hasText: title }).click();
        const box = page.getByRole('textbox', { name: '消息', exact: true });
        await box.fill('请一起分析');
        await openCollaboration(page);
        await page.getByRole('menuitem', { name: '给 Claude Code 分配任务', exact: true }).click();
        const task = page.getByRole('textbox', { name: '给 Claude Code 的任务', exact: true });
        await task.fill('先总结经验，只给建议');
        await page.getByRole('button', { name: '排队发送', exact: true }).click();
        await page.locator('.queue-targets', { hasText: 'Claude Code' }).waitFor();
        let state = await (await api(daemon, `/api/threads/${thread.id}`)).json();
        assert.equal(state.status, 'running');
        assert.equal(state.coordination, undefined, 'queueing must not enable collaboration early');
        assert.ok(await page.locator('.queue-text').evaluate(el => el.clientWidth >= 68), 'message text keeps enough space to read');
        if (phone) assert.equal(await page.locator('.queue-target-count').innerText(), '@1');
        await page.screenshot({ path: join(shots, `queued-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        await box.fill('已有草稿');
        await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
        await task.waitFor();
        assert.equal(await task.inputValue(), '先总结经验，只给建议');
        assert.equal(await box.inputValue(), '请一起分析\n\n已有草稿');
        await task.fill('修改后只审查接口');
        await page.getByRole('button', { name: '排队发送', exact: true }).click();
        await page.locator('.queue-targets').waitFor();
        state = await (await api(daemon, `/api/threads/${thread.id}`)).json();
        await post(`/api/threads/${thread.id}/queue/${state.queued[0].id}/now`, {});
        await page.locator('.composer-add.on').waitFor();
        await page.getByRole('button', { name: '排队发送', exact: true }).waitFor({ state: 'hidden' });
        const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
        const rows = Array.isArray(events) ? events : events.events;
        assert.ok(rows.some(e => e.type === 'turn.started' && String(e.data.text).includes('修改后只审查接口')));
      } catch (error) {
        await page?.screenshot({ path: join(shots, `failure-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        throw error;
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('deferred setup failures stay visible and keep text and targets for editing', { timeout: 60000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
  const post = async (path, body) => {
    const res = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(res.ok, await res.clone().text());
    return res.status === 204 ? null : res.json();
  };
  try {
    for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
      try {
        const extra = join(daemon.workdir, 'temporary-collaboration-directory');
        mkdirSync(extra);
        const title = `协作排队失败 ${phone}-${colorScheme}`;
        const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
        await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
        const queued = await post(`/api/threads/${thread.id}/turns`, { text: '保留原文', queue: true,
          mentions: [{ id: 'claude', kind: 'agent', label: 'Claude Code', task: '保留任务' }],
          collaboration: { directories: [daemon.workdir, extra] } });
        rmSync(extra, { recursive: true });
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        await openUi(page, daemon);
        if (phone) await page.locator('.menu-btn').click();
        await page.locator('.thread-row', { hasText: title }).click();
        await post(`/api/threads/${thread.id}/queue/${queued.id}/now`, {});
        await page.locator('.queue-error').waitFor();
        assert.match(await page.locator('.queue-error').textContent(), /发送失败/);
        assert.equal(await page.locator('.queue-text').textContent(), '保留原文');
        assert.equal(await page.locator('.queue-targets').getAttribute('aria-label'), '协作：Claude Code');
        assert.equal(await page.locator('.queue-badge').textContent(), '未发出');
        await page.getByRole('button', { name: '重试', exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: '立即发送', exact: true }).count(), 0);
        assert.match(await page.locator('.queue-error').getAttribute('title'), /ENOENT/);
        if (phone) assert.ok(await page.locator('.queue-error-text').evaluate(el => el.getBoundingClientRect().height <= parseFloat(getComputedStyle(el).lineHeight) * 2 + 1));
        assert.ok(await page.locator('.queue-text').evaluate(el => el.clientWidth >= 68), 'failed message text stays readable');
        await page.screenshot({ path: join(shots, `queue-error-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
        await page.getByRole('button', { name: '撤回到输入框里编辑', exact: true }).click();
        const task = page.getByRole('textbox', { name: '给 Claude Code 的任务', exact: true });
        await task.waitFor();
        assert.equal(await task.inputValue(), '保留任务');
        assert.equal(await page.getByRole('textbox', { name: '消息', exact: true }).inputValue(), '保留原文');
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
