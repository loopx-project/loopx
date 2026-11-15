// The overview panel: one top-bar button, the team and the thread's work under it, a change's
// whole history in a dialog, and the page preview beside the row. Checked against a live daemon
// for the reading, and against the demo workspace for the tall panel that must not scroll.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, openUi, api, openCollaboration } from '../harness.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Waits for a thread to stop running, and stay stopped. */
async function idle(daemon, threadId) {
  for (let i = 0; i < 300; i++) {
    const res = await api(daemon, `/api/threads/${threadId}`);
    const thread = await res.json();
    if (thread.status !== 'running') {
      await sleep(300);
      if ((await (await api(daemon, `/api/threads/${threadId}`)).json()).status !== 'running') return thread;
    } else await sleep(100);
  }
  throw new Error(`thread ${threadId} never settled`);
}

test('the overview panel reads the thread from one top-bar button, and a change opens its diff', { timeout: 180000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const made = await (await api(daemon, '/api/threads', {
      method: 'POST', body: JSON.stringify({ agent: 'claude', cwd: daemon.workdir, title: '概况面板' }),
    })).json();
    await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'edit file make outputs plan' }) });
    await idle(daemon, made.id);
    // The same file edited again in a later turn: its row holds both turns' diffs. The plan comes
    // with it, or the newest turn would stand between the panel and the plan before it.
    await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'edit file again plan' }) });
    await idle(daemon, made.id);

    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '概况面板' }).click();

    // Files live in the more menu; only the overview and more stay in the desktop bar.
    await page.locator('.topbar-overview').waitFor();
    assert.equal(await page.locator('.topbar-actions button.icon-btn').count(), 2, 'two top-bar buttons');
    assert.equal(await page.locator('.topbar-agents, .topbar-plan, .topbar-trace').count(), 0, 'no old buttons came back');

    // The panel: open by default, standing beside the conversation, plan, changes, images and
    // files in that order.
    const panel = page.locator('.overview-panel.overview-dock');
    await panel.waitFor();
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'true');
    // The column's move lasts 0.3s; measure it at rest.
    await page.waitForTimeout(450);
    // It does not lie over the conversation: the column made room for it.
    const room = await page.evaluate(() => {
      const column = document.querySelector('.thread').getBoundingClientRect();
      const dock = document.querySelector('.overview-panel').getBoundingClientRect();
      return { gap: dock.left - column.right, dockLeft: dock.left, columnRight: column.right };
    });
    assert.ok(room.gap >= 12, `the panel leaves ${room.gap}px beside the column`);
    await panel.locator('.overview-row', { hasText: 'app.ts' }).waitFor();
    // History can arrive after outputs. Reopen after the full list knows the plan, so this
    // assertion checks a fresh preview rather than depending on the order of two requests.
    await panel.locator('.overview-foot .overview-trace').click();
    const all = page.locator('.overview-details-page');
    await all.locator('.overview-plan .plan-entry').first().waitFor();
    await all.locator('.overview-details-head button').click();
    await panel.waitFor();
    const groups = await panel.locator('.overview-toggle strong').allTextContents();
    assert.deepEqual(groups.slice(0, 4), ['计划', '变更', '图片', '文件']);
    assert.equal(await panel.locator('.overview-plan .plan-entry').count(), 3);
    assert.ok(await panel.locator('.overview-picture-row img').count() > 0, 'the chart shows a thumbnail');

    // A change row opens every turn's diff for that file, read from history/detail.
    await panel.locator('.overview-row', { hasText: 'app.ts' }).click();
    const diff = page.locator('.change-diff-dialog');
    await diff.waitFor();
    await diff.locator('.diff-line.add', { hasText: 'const c = 4' }).first().waitFor();
    const head = await diff.locator('.change-diff-head').innerText();
    assert.match(head, /app\.ts/);
    assert.match(head, /\+4/);
    assert.match(head, /共 2 轮/);
    // Newest turn first, one section each.
    assert.deepEqual(await diff.locator('.change-diff-turn h3').allTextContents(), ['第 2 轮', '第 1 轮']);
    await diff.locator('.change-diff-head .icon-btn').click();
    await diff.waitFor({ state: 'detached' });

    // The page has its own preview button beside the row, apart from opening the file. The dock
    // stayed where it was when the dialog closed.
    const htmlRow = page.locator('.overview-rowwrap', { hasText: 'report.html' }).first();
    await htmlRow.locator('.overview-row').waitFor();
    await htmlRow.locator('.overview-row').hover();
    await htmlRow.locator('.overview-preview').click();
    await page.locator('.preview-scrim').waitFor();
    assert.match(await page.locator('.preview-name').innerText(), /report\.html/);
    await page.locator('.preview-bar').getByRole('button', { name: '关闭预览' }).click();
    await page.locator('.preview-scrim').waitFor({ state: 'detached' });

    // The more menu keeps the terminal and the trace beside the thread.
    await page.locator('.topbar-more').click();
    await page.getByRole('menuitem', { name: '打开终端', exact: true }).waitFor();
    await page.getByRole('menuitem', { name: '轨迹', exact: true }).waitFor();
    await page.keyboard.press('Escape');

    // The standing panel stays through the conversation, the input and Escape; only its own
    // button above it puts it away, and the choice survives a reload.
    await page.locator('.main > .scroller').click({ position: { x: 12, y: 160 } });
    await panel.waitFor();
    await page.locator('.composer textarea').click();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
    await panel.waitFor();
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'true');

    // A side panel takes the same edge: the overview steps aside while it is open, but the
    // person's switch is untouched — it comes back when the side panel closes.
    await page.locator('.topbar-more').click();
    await page.getByRole('menuitem', { name: '文件', exact: true }).click();
    await page.locator('.files-panel').waitFor();
    await panel.waitFor({ state: 'detached' });
    assert.notEqual(await page.evaluate(() => localStorage.getItem('calli.overview.open')), '0', 'a side panel does not record the panel as closed');
    await page.locator('.files-panel .files-head button[aria-label="关闭文件"]').click();
    await page.locator('.files-panel').waitFor({ state: 'detached' });
    await panel.waitFor();
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'true');

    await page.locator('.topbar-overview').click();
    await panel.waitFor({ state: 'detached' });
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'false');
    assert.equal(await page.evaluate(() => localStorage.getItem('calli.overview.open')), '0');

    await page.reload();
    await page.locator('.thread-row', { hasText: '概况面板' }).click();
    await page.locator('.topbar-overview').waitFor();
    await page.waitForTimeout(300);
    assert.equal(await page.locator('.overview-panel').count(), 0, 'a closed panel stays closed');
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'false');

    await page.locator('.topbar-overview').click();
    await page.locator('.overview-panel.overview-dock').waitFor();
    await page.reload();
    await page.locator('.thread-row', { hasText: '概况面板' }).click();
    await page.locator('.overview-panel.overview-dock').waitFor({ timeout: 15000 });
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), 'true');

    assert.deepEqual(errors, []);
    await context.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});

test('a full team and thirteen changes fold into the room the panel has, with no scrollbar', { timeout: 120000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    // The demo workspace: four members (one needing the user, one failed, one running), a plan,
    // thirteen changes, nine pictures and five files — more than the panel can show at once.
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await page.goto(`${daemon.base}/`);
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    const panel = page.locator('.overview-panel.overview-dock');
    await panel.waitFor();
    await panel.locator('.overview-member', { hasText: '深色核对' }).waitFor();
    // The badge counts only what asks for the person.
    assert.equal((await page.locator('.topbar-overview .topbar-badge').innerText()).trim(), '1');
    // A tall overview keeps one real member, with needs and failures visible in its summary.
    assert.equal(await panel.locator('.overview-member').count(), 1);
    await panel.locator('.overview-more').first().waitFor();
    // Preview counts shrink rather than the panel growing a scrollbar.
    const fit = await panel.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight, bottom: el.getBoundingClientRect().bottom }));
    assert.ok(fit.scroll <= fit.client + 1, `panel scrolls: ${fit.scroll} > ${fit.client}`);
    assert.ok(fit.bottom <= 900, `panel runs past the window: ${fit.bottom}`);
    // All groups keep a real preview and an explicit full-list entry.
    assert.equal(await panel.locator('.overview-group.open .overview-member').count(), 1, 'the first member stays visible');
    assert.match(await panel.locator('.overview-summary').first().innerText(), /需要你.*失败/, 'other important states stay visible in the group summary');
    await context.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});

test('below the dock width the overview is the old popover, and a phone tap outside puts it away', { timeout: 120000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    // Below the dock width the overview is the popover again, closed by default even on a device
    // that last left the standing panel open.
    const wide = await browser.newContext({ viewport: { width: 1100, height: 900 } });
    await wide.addInitScript(() => localStorage.setItem('calli.overview.open', '1'));
    const page = await wide.newPage();
    await page.goto(`${daemon.base}/`);
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await page.waitForTimeout(500);
    assert.equal(await page.locator('.overview-panel').count(), 0, 'the popover starts closed');
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-pressed'), null);
    assert.equal(await page.locator('.topbar-overview').getAttribute('aria-expanded'), 'false');
    await page.locator('.topbar-overview').click();
    const panel = page.locator('.overview-panel');
    await panel.waitFor();
    assert.ok(!(await panel.getAttribute('class')).includes('overview-dock'), 'not the standing panel');
    await page.mouse.click(200, 500);
    await panel.waitFor({ state: 'detached' });
    await wide.close();

    // A phone's anchored popover also closes on an outside tap.
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const small = await phone.newPage();
    await small.goto(`${daemon.base}/`);
    await small.locator('.menu-btn').click();
    await small.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await small.waitForTimeout(500);
    assert.equal(await small.locator('.overview-panel').count(), 0, 'the phone starts collapsed');
    await small.locator('.topbar-overview').click();
    await small.locator('.overview-panel').waitFor();
    await small.touchscreen.tap(2, 100);
    await small.locator('.overview-panel').waitFor({ state: 'detached' });
    await phone.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});

test('a team that lands after the panel joins it without a reopen, and the readings open the trace', { timeout: 180000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    // A real daemon, two real member threads: the team exists server-side before the page loads,
    // so the only thing late is the coordination answer the page is still waiting on. Two members
    // is the case that used to overflow: the panel fitted without the group, then the rows landed
    // a frame later and spilled over the footer.
    const post = async (path, body) => { const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) }); assert.ok(response.ok, await response.clone().text()); return response.json(); };
    const root = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: '晚到的团队' });
    const setup = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const preparer = await setup.newPage();
    await openUi(preparer, daemon);
    await preparer.locator('.thread-row', { hasText: '晚到的团队' }).click();
    await openCollaboration(preparer);
    await preparer.locator('.coord-picker-head button').click();
    await preparer.getByRole('button', { name: '启用协作', exact: true }).click();
    await preparer.locator('.composer-add.on').waitFor();
    for (const [name, requestId] of [['文档审查', 'late-team-1'], ['复核', 'late-team-2']]) {
      const directive = Buffer.from(JSON.stringify({ args: ['spawn', '--agent', 'claude', '--name', name, '--message-file', '-', '--request-id', requestId], text: '检查接口 CALLI_REPORT' })).toString('base64');
      // One spawn turn at a time: the second directive waits for the first member to exist.
      for (let n = 0; n < 300; n++) {
        const team = await (await api(daemon, `/api/threads/${root.id}/coordination`)).json();
        if (team?.members?.some(m => m.name === name) || team?.members?.length >= 2) break;
        await sleep(100);
      }
      await post(`/api/threads/${root.id}/turns`, { text: `CALLI_COMMAND:${directive}` });
      for (let n = 0; n < 300; n++) {
        const team = await (await api(daemon, `/api/threads/${root.id}/coordination`)).json();
        if (team?.members?.some(m => m.name === name)) break;
        await sleep(100);
      }
    }
    await setup.close();

    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    let release = () => {};
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/api/threads/*/coordination*', async route => { await gate; await route.continue(); });
    await openUi(page, daemon);
    await page.locator('.thread-row', { hasText: '晚到的团队' }).click();
    const panel = page.locator('.overview-panel.overview-dock');
    await panel.waitFor();
    await panel.locator('[data-ready=true]').waitFor();
    assert.equal(await panel.locator('.overview-group', { hasText: '子智能体' }).count(), 0, 'the team is still on its way');
    release();
    // No reopen, no second click: the section joins the panel where it fits.
    await panel.locator('.overview-group', { hasText: '子智能体' }).waitFor({ timeout: 10_000 });
    await panel.locator('.overview-member').nth(1).waitFor({ timeout: 10_000 });
    // The panel grew with the group instead of keeping its old pinned height: no group may run
    // under the readings line, and every row must be inside the panel.
    const fit = await panel.evaluate(el => {
      const foot = el.querySelector('.overview-foot').getBoundingClientRect();
      const groups = [...el.querySelectorAll('.overview-group')];
      const last = groups.at(-1).getBoundingClientRect();
      return { overlap: Math.round(last.bottom - foot.top), rows: el.querySelectorAll('.overview-member').length };
    });
    assert.equal(fit.rows, 2, 'both members are on screen');
    assert.ok(fit.overlap <= 0, `the last group runs ${fit.overlap}px over the readings line`);
    // The readings line is the quiet way to the trace.
    await panel.locator('.overview-readings-link').click();
    await page.locator('.trace-side').waitFor();
    await context.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});
