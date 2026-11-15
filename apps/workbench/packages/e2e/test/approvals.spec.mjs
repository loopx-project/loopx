import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] }] }) });
  console.log(`Approval test daemon PID: ${daemon.pid}`);
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

const create = async title => {
  const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', title, cwd: daemon.workdir, permissionMode: 'ask' }) });
  assert.equal(res.status, 201);
  return res.json();
};
const start = async (thread, text = 'long-approve') => {
  assert.equal((await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text }) })).status, 202);
  for (let i = 0; i < 150; i++) {
    const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
    const request = events.find(event => event.type === 'approval.request');
    if (request) return request;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  assert.fail('approval did not arrive');
};
const select = async (page, thread, mobile = false) => {
  await openUi(page, daemon);
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: thread.title }).first().click();
  return page.getByRole('dialog', { name: '权限审批' });
};
const bounds = async sheet => sheet.evaluate(el => {
  const card = el.getBoundingClientRect();
  const actions = el.querySelector('.sheet-approval-actions').getBoundingClientRect();
  return { top: card.top, bottom: card.bottom, height: card.height, actionTop: actions.top, actionBottom: actions.bottom, viewport: window.visualViewport?.height ?? window.innerHeight };
});
const settleViewport = async page => {
  // Playwright's resize resolves before the visual viewport's resize listener and next paint.
  await page.waitForFunction(() => Math.abs(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--vvh')) - window.visualViewport.height) < 1);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
};
const checkBounds = async sheet => {
  const b = await bounds(sheet);
  assert.ok(b.top >= 0 && b.bottom <= b.viewport + 1, JSON.stringify(b));
  assert.ok(b.actionTop >= b.top && b.actionBottom <= b.bottom, 'decision actions remain visible');
  return b;
};

test('long approval stays compact, complete and operable on desktop and phone in both themes', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const thread = await create(`长命令 ${mobile} ${colorScheme}`);
      const request = await start(thread);
      // ACP-style requests can use the complete command as their title as well as their raw input.
      await page.route(`**/api/threads/${thread.id}/history`, async route => {
        const response = await route.fetch();
        const page = await response.json();
        for (const event of page.events) {
          const command = event.data.toolCall?.rawInput?.command;
          if (event.type === 'approval.request' && typeof command === 'string') event.data.title = command;
        }
        await route.fulfill({ response, json: page });
      });
      const sheet = await select(page, thread, mobile);
      await sheet.waitFor();
      assert.equal(await sheet.locator('.sheet-title').textContent(), '运行命令');
      assert.equal(await sheet.locator('.sheet-command').textContent(), request.data.toolCall.rawInput.command);
      assert.equal(await sheet.getByRole('button', { name: '拒绝', exact: true }).count(), 1);
      assert.equal(await sheet.getByRole('button', { name: '允许这一次', exact: true }).count(), 1);
      await checkBounds(sheet);
      const command = sheet.locator('.sheet-command');
      assert.ok(await command.evaluate(el => el.scrollHeight > el.clientHeight));
      await command.focus();
      await page.keyboard.press('PageDown');
      await page.waitForFunction(el => el.scrollTop > 0, await command.elementHandle());
      // Keyboard scrolling animates; let it settle before resetting for a screenshot.
      await command.evaluate(el => new Promise(resolve => {
        let last = el.scrollTop, since = performance.now();
        const tick = () => {
          if (el.scrollTop !== last) { last = el.scrollTop; since = performance.now(); }
          if (performance.now() - since >= 200) resolve();
          else requestAnimationFrame(tick);
        };
        tick();
      }));
      await checkBounds(sheet);
      await command.evaluate(el => { el.blur(); el.scrollTo({ top: 0, behavior: 'instant' }); });
      await settleViewport(page);
      assert.equal(await command.evaluate(el => el.scrollTop), 0);
      if (shots) await page.screenshot({ path: join(shots, `approval-long-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });
      if (mobile) {
        await page.setViewportSize({ width: 320, height: 460 });
        await settleViewport(page);
        await checkBounds(sheet);
        if (shots) await page.screenshot({ path: join(shots, `approval-small-${colorScheme}.png`), animations: 'disabled' });
      }
      await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
      await sheet.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});

test('submission locks synchronously, retries a local error and recovers without SSE while retaining the draft', { timeout: 45000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    const thread = await create('审批重试');
    await select(page, thread, true);
    await page.locator('.composer textarea').fill('keep this draft');
    const request = await start(thread);
    // Reload closes the existing stream. Block every new history stream before the approval opens.
    await page.route('**/stream?view=history*', route => route.abort());
    const sheet = await select(page, thread, true);
    await sheet.waitFor();
    assert.equal(await page.locator('.composer textarea').inputValue(), 'keep this draft');
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let posts = 0;
    await page.route(`**/api/approvals/${request.data.approvalId}`, async route => {
      posts++;
      if (posts === 1) {
        await held;
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'temporary approval failure' }) });
      } else await route.continue();
    });
    const before = await checkBounds(sheet);
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).evaluate(el => { el.click(); el.click(); });
    await sheet.getByRole('status').getByText('提交中', { exact: true }).waitFor();
    assert.equal(posts, 1);
    assert.ok(await sheet.locator('.sheet-approval-actions button').evaluateAll(buttons => buttons.every(button => button.disabled)));
    const busy = await checkBounds(sheet);
    assert.ok(Math.abs(before.height - busy.height) <= 1, 'submission progress does not add another row');
    if (shots) await page.screenshot({ path: join(shots, 'approval-busy-phone.png'), animations: 'disabled' });
    release();
    await sheet.getByRole('alert').getByText('temporary approval failure', { exact: true }).waitFor();
    assert.equal(await page.locator('.error-banner').count(), 0);
    await checkBounds(sheet);
    if (shots) await page.screenshot({ path: join(shots, 'approval-error-phone.png'), animations: 'disabled' });
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
    await sheet.waitFor({ state: 'detached' });
    assert.equal(posts, 2);
    assert.equal(await page.locator('.composer textarea').inputValue(), 'keep this draft');
  } finally { await context.close(); }
});

test('native rejection and persistent permission scopes keep their original decisions', { timeout: 60000 }, async () => {
  const cases = [
    { text: 'deny', action: '拒绝', scope: '本会话内不再询问', body: { optionId: 'decline' }, decision: { optionId: 'decline' }, reply: 'denied' },
    { text: 'deny', action: '始终允许', scope: '本会话内不再询问', body: { optionId: 'acceptForSession' }, decision: { optionId: 'acceptForSession' }, reply: 'approved:acceptForSession' },
    { text: 'approve', action: '始终允许', scope: '同类命令不再询问', body: { optionId: 'acceptWithExecpolicyAmendment' }, decision: { optionId: 'acceptWithExecpolicyAmendment' }, reply: 'approved:acceptWithExecpolicyAmendment' },
    { text: 'approve', action: '拒绝', scope: '同类命令不再询问', body: { cancel: true }, decision: { cancelled: true } },
  ];
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    for (const [index, scenario] of cases.entries()) {
      const thread = await create(`原生审批选项 ${index}`);
      const request = await start(thread, scenario.text);
      const posts = [];
      await page.route(`**/api/approvals/${request.data.approvalId}`, async route => {
        posts.push(route.request().postDataJSON());
        await route.continue();
      });
      const sheet = await select(page, thread, true);
      await sheet.waitFor();
      assert.equal(await sheet.getByRole('button', { name: '拒绝', exact: true }).count(), 1);
      await sheet.locator('.sheet-option-desc').getByText(scenario.scope, { exact: true }).waitFor();
      await checkBounds(sheet);
      await sheet.getByRole('button', { name: scenario.action, exact: scenario.action === '拒绝' }).click();
      await sheet.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.deepEqual(posts, [scenario.body]);
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.deepEqual(events.find(event => event.type === 'approval.resolved' && event.data.approvalId === request.data.approvalId).data.decision, scenario.decision);
      if (scenario.reply) {
        const reply = events.filter(event => event.type === 'message.delta' && event.turnId === request.turnId).map(event => event.data.text).join('');
        assert.equal(reply, scenario.reply, 'the native agent receives the original decision and streams its reply');
      }
    }
  } finally { await context.close(); }
});

test('a successful decision with failed status sync offers a local retry that checks the server result', { timeout: 45000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
  try {
    const page = await context.newPage();
    const thread = await create('审批状态重试');
    const request = await start(thread);
    await page.route('**/stream?view=history*', route => route.abort());
    let failHistory = false;
    await page.route(`**/api/threads/${thread.id}/history`, async route => {
      if (failHistory) await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'temporary history failure' }) });
      else await route.continue();
    });
    const posts = [];
    await page.route(`**/api/approvals/${request.data.approvalId}`, async route => {
      posts.push(route.request().postDataJSON());
      await route.continue();
    });
    const sheet = await select(page, thread, true);
    await sheet.waitFor();
    failHistory = true;
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
    await sheet.getByRole('alert').getByText('决定已提交，但状态同步失败；请重试以核对结果。', { exact: true }).waitFor();
    assert.equal(await page.locator('.error-banner').count(), 0);
    assert.equal(await sheet.getByRole('button', { name: '允许这一次', exact: true }).isEnabled(), true);
    await checkBounds(sheet);
    if (shots) await page.screenshot({ path: join(shots, 'approval-sync-error-phone-dark.png'), animations: 'disabled' });
    failHistory = false;
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
    await sheet.waitFor({ state: 'detached' });
    assert.deepEqual(posts, [{ optionId: 'accept' }, { optionId: 'accept' }]);
    const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
    assert.equal(events.filter(event => event.type === 'approval.resolved' && event.data.approvalId === request.data.approvalId).length, 1);
  } finally { await context.close(); }
});

test('a decision processed before its response times out is recovered without sending another decision', { timeout: 45000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  let release;
  try {
    const page = await context.newPage();
    const thread = await create('提交响应丢失');
    const request = await start(thread);
    await page.route('**/stream?view=history*', route => route.abort());
    const held = new Promise(resolve => { release = resolve; });
    let posts = 0;
    await page.route(`**/api/approvals/${request.data.approvalId}`, async route => {
      posts++;
      const response = await route.fetch();
      assert.equal(response.status(), 204);
      await held;
      await route.abort().catch(() => {});
    });
    const sheet = await select(page, thread, true);
    await sheet.waitFor();
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
    await sheet.getByRole('status').getByText('提交中', { exact: true }).waitFor();
    await sheet.waitFor({ state: 'detached', timeout: 30000 });
    assert.equal(posts, 1);
    assert.equal(await page.locator('.error-banner').count(), 0);
    const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
    assert.deepEqual(events.find(event => event.type === 'approval.resolved' && event.data.approvalId === request.data.approvalId).data.decision, { optionId: 'accept' });
    assert.equal(await page.locator('.composer-box').evaluate(el => getComputedStyle(el).visibility), 'visible');
  } finally { release?.(); await context.close(); }
});

test('an approval resolved elsewhere clears after its stale POST rather than trapping the composer', { timeout: 45000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'dark' });
  try {
    const page = await context.newPage();
    const thread = await create('远端取消');
    const request = await start(thread);
    await page.route('**/stream?view=history*', route => route.abort());
    const sheet = await select(page, thread);
    await sheet.waitFor();
    assert.equal((await api(daemon, `/api/threads/${thread.id}/cancel`, { method: 'POST', body: '{}' })).status, 200);
    await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
    await sheet.waitFor({ state: 'detached' });
    const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
    assert.deepEqual(events.find(event => event.type === 'approval.resolved' && event.data.approvalId === request.data.approvalId).data.decision, { cancelled: true });
    assert.equal(await page.locator('.composer-box').evaluate(el => getComputedStyle(el).visibility), 'visible');
  } finally { await context.close(); }
});
