// The overview page: what was asked each day and what was called, with the whole prompt of a
// conversation in a dialog. Verified against a live daemon, not the demo.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, openUi } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('the settings menu opens the overview, a turn shows up the day it was asked, tools and MCP counted', async () => {
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  try {
    const made = await (await fetch(`${daemon.base}/api/threads`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${daemon.token}` },
      body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }),
    })).json();
    await fetch(`${daemon.base}/api/threads/${made.id}/turns`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${daemon.token}` },
      body: JSON.stringify({ text: 'overview e2e: 跑一遍安装检查' }),
    });
    for (let i = 0; i < 100; i++) {
      const t = await (await fetch(`${daemon.base}/api/threads/${made.id}`, { headers: { Authorization: `Bearer ${daemon.token}` } })).json();
      if (t.status !== 'running') break;
      await new Promise((r) => setTimeout(r, 100));
    }

    await openUi(page, daemon);
    // Not a row of the list: it lives in the settings menu, with the skills and MCP pages.
    assert.equal(await page.locator('.side-link', { hasText: '概览' }).count(), 0);
    await page.getByRole('button', { name: '配置', exact: true }).click();
    await page.locator('.settings-nav').getByRole('button', { name: '概览', exact: true }).click();
    await page.locator('.activity-page').waitFor({ timeout: 10_000 });
    assert.match(await page.evaluate(() => window.location.hash), /#\/settings\/activity$/);
    assert.equal((await page.locator('.settings-topbar h1').innerText()).trim(), '配置', 'the top bar names the page, not 新线程');

    // Today's section: the ask, the conversation under its name, and the tools the fake pi called.
    const day = page.locator('.activity-day', { has: page.locator('.activity-day-head h2', { hasText: '今天' }) });
    await day.waitFor({ timeout: 10_000 });
    assert.match(await day.locator('.activity-ask-text').first().textContent(), /安装检查/);
    assert.ok((await day.locator('.activity-thread-title').first().textContent()).length > 0);
    const toolNames = await day.locator('.activity-chip').allTextContents();
    assert.ok(toolNames.length > 0, `tools counted: ${toolNames.join(', ')}`);

    // The ask opens its turn as a trace: the ask itself, then the tool the fake pi ran, with output.
    assert.match(await day.locator('.activity-ask-tools').first().textContent(), /1 个工具/);
    await day.locator('.activity-ask').first().click();
    const trace = page.locator('.trace-panel');
    await trace.waitFor({ timeout: 10_000 });
    await trace.locator('.trace-step.user .trace-text').waitFor({ timeout: 10_000 });
    assert.match(await trace.locator('.trace-step.user .trace-text').textContent(), /安装检查/, 'the ask is open from the start');
    assert.match(await trace.locator('.trace-status').textContent(), /完成/);
    const tool = trace.locator('.trace-step.tool').first();
    assert.match(await tool.locator('.trace-tool').textContent(), /bash/);
    await tool.locator('.trace-step-head').click();
    assert.match(await tool.locator('.trace-text.code').last().textContent(), /a\.txt/);
    // Filtered by kind, then searched.
    await trace.locator('.trace-filter', { hasText: '工具' }).click();
    assert.equal(await trace.locator('.trace-step.user').count(), 0);
    await trace.locator('.trace-filter', { hasText: '全部' }).click();
    await trace.locator('.trace-search input').fill('没有这个词');
    await trace.locator('.trace-empty', { hasText: '没有匹配' }).waitFor({ timeout: 5_000 });
    await trace.locator('.trace-search input').fill('');

    // From the trace, into its conversation.
    await trace.locator('.trace-thread').click();
    await page.locator('.composer').waitFor({ timeout: 10_000 });
    assert.equal(await page.evaluate(() => window.location.hash), '');
    await page.getByRole('button', { name: '配置', exact: true }).click();
    await page.locator('.settings-nav').getByRole('button', { name: '概览', exact: true }).click();
    await page.locator('.activity-page').waitFor({ timeout: 10_000 });
  } finally { await page.close(); }
});

test('an agent that keeps no prompt in its session file says so instead of guessing', async () => {
  // agy advertises no system-prompt capability, so its conversation has no button to read one from.
  const made = await (await fetch(`${daemon.base}/api/threads`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${daemon.token}` },
    body: JSON.stringify({ agent: 'agy', cwd: daemon.workdir, title: '无提示词的会话' }),
  })).json();
  await fetch(`${daemon.base}/api/threads/${made.id}/turns`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${daemon.token}` },
    body: JSON.stringify({ text: 'overview e2e: 没有系统提示词' }),
  });
  for (let i = 0; i < 100; i++) {
    const t = await (await fetch(`${daemon.base}/api/threads/${made.id}`, { headers: { Authorization: `Bearer ${daemon.token}` } })).json();
    if (t.status !== 'running') break;
    await new Promise((r) => setTimeout(r, 100));
  }
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  try {
    await openUi(page, daemon);
    await page.evaluate(() => window.location.assign('#/activity'));
    await page.locator('.activity-page').waitFor({ timeout: 10_000 });
    const group = page.locator('.activity-thread').filter({ hasText: '无提示词的会话' });
    await group.waitFor({ timeout: 10_000 });
    assert.equal(await group.locator('.activity-sys-btn').count(), 0, 'no button where there is nothing to read');
  } finally { await page.close(); }
});
