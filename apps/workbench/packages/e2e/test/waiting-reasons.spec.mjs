import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
const cases = [
  { title: '确认页面范围', text: 'plan-question', reason: 'question', label: '等待回答' },
  { title: '页面实施计划', text: 'plan-approval', reason: 'plan', label: '计划待确认' },
  { title: '运行项目命令', text: 'please approve', reason: 'approval', label: '等待批准' },
];

before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'claude', adapter: 'claude', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-acp.mjs')] },
  ] }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
  for (const item of cases) {
    const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', title: item.title, cwd: daemon.workdir, workMode: item.reason === 'approval' ? 'execute' : 'plan', permissionMode: 'ask' }) });
    assert.equal(created.status, 201);
    const thread = await created.json();
    item.id = thread.id;
    const sent = await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: item.text }) });
    assert.equal(sent.status, 202);
  }
});
after(async () => { await browser?.close(); await daemon?.stop(); });

test('pending questions, plans and permissions have distinct labels in both sidebar views and themes', { timeout: 60000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      for (const activity of [false, true]) {
        if (activity) await page.getByRole('button', { name: '按活动查看', exact: true }).click();
        for (const item of cases) {
          const row = page.locator('.thread-row').filter({ has: page.locator('.thread-title', { hasText: item.title }) });
          await row.getByText(item.label, { exact: true }).waitFor();
          assert.equal(await row.locator('.dot.waiting').getAttribute('aria-label'), item.label);
          const view = await (await api(daemon, `/api/threads/${item.id}`)).json();
          assert.equal(view.waitingReason, item.reason);
          assert.equal(await row.locator('.thread-note').evaluate(el => el.scrollWidth <= el.clientWidth), true);
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
        if (shots && activity) await page.screenshot({ path: join(shots, `waiting-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      }
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      for (const item of cases) await page.locator('.thread-row').filter({ hasText: item.title }).getByText(item.label, { exact: true }).waitFor();
    } finally { await context.close(); }
  }
});

test('an optional middle step of a form proceeds without input', { timeout: 30000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    await openUi(page, daemon);
    const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude', title: '分步表单', cwd: daemon.workdir, workMode: 'plan', permissionMode: 'ask' }) });
    assert.equal(created.status, 201);
    const thread = await created.json();
    assert.equal((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'plan-question-steps' }) })).status, 202);
    await page.locator('.menu-btn').click();
    await page.locator('.thread-title', { hasText: thread.title }).click();
    const sheet = page.getByRole('dialog', { name: '智能体提问' });
    await sheet.waitFor();
    await sheet.getByRole('radio', { name: /桌面和手机/ }).click();
    // The middle step is an optional free-text note: moving on without typing must be possible.
    await sheet.getByText('补充说明', { exact: false }).first().waitFor();
    const next = sheet.getByRole('button', { name: '下一步', exact: true });
    assert.equal(await next.isEnabled(), true);
    await next.click();
    await sheet.getByRole('radio', { name: /需要截图/ }).click();
    await sheet.waitFor({ state: 'detached' });
    let answer = '';
    for (let i = 0; i < 100; i++) {
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      answer = events.filter(e => e.type === 'message.delta').map(e => e.data.text).join('');
      if (answer.includes('answer:')) break;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    assert.ok(answer.includes('桌面和手机') && answer.includes('需要截图'), answer);
    assert.ok(!answer.includes('question_0_custom'), `the empty optional field is not sent to the agent: ${answer}`);
  } finally { await context.close(); }
});

test('answering and cancelling remove the waiting labels', { timeout: 30000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  try {
    const page = await context.newPage();
    await openUi(page, daemon);
    await page.locator('.thread-title', { hasText: cases[0].title }).click();
    // The question is asked in the sheet over the composer. The form's choice goes first; its
    // optional free-text follow-up is asked next, and 提交 sends the whole form.
    const sheet = page.getByRole('dialog', { name: '智能体提问' });
    await sheet.getByRole('radio', { name: /桌面和手机/ }).click();
    await sheet.getByRole('button', { name: '提交', exact: true }).click();
    await page.locator('.action-sheet').waitFor({ state: 'detached' });
    await page.locator('.thread-row').filter({ hasText: cases[0].title }).locator('.thread-note').waitFor({ state: 'detached' });
    for (const item of cases.slice(1)) {
      assert.equal((await api(daemon, `/api/threads/${item.id}/cancel`, { method: 'POST', body: '{}' })).status, 200);
      await page.locator('.thread-row').filter({ hasText: item.title }).locator('.thread-note').waitFor({ state: 'detached' });
    }
  } finally { await context.close(); }
});
