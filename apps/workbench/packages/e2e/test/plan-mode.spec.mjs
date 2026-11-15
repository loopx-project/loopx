import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';
let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['claude', 'codex', 'traex', 'zcode', 'agy', 'pi'].map(id => ({ id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon/test/fixtures', `fake-${['claude', 'traex'].includes(id) ? 'acp' : id}.mjs`)] })) }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

const planChoice = page => page.getByRole(page.viewportSize().width <= 640 ? 'menuitemcheckbox' : 'option', { name: /^计划模式/ });
const attachChoice = page => page.getByRole(page.viewportSize().width <= 640 ? 'menuitem' : 'option', { name: '添加附件', exact: true });
const questionSheet = page => page.getByRole('dialog', { name: '智能体提问' });
const planSheet = page => page.getByRole('dialog', { name: '计划审批' });
const composerHidden = page => page.locator('.composer-box').evaluate(el => getComputedStyle(el).visibility === 'hidden');

const create = async (agent, title, workMode = 'plan', permissionMode = 'full_access') => {
  const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, title, cwd: daemon.workdir, workMode, permissionMode }) });
  assert.equal(res.status, 201); return res.json();
};
const select = async (page, title, mobile) => {
  await openUi(page, daemon);
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
};
const send = async (page, text) => {
  await page.locator('.composer textarea').fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === '');
};
const events = async threadId => (await (await api(daemon, `/api/threads/${threadId}/events`)).json());
const waitIdle = async threadId => {
  for (let i = 0; i < 200; i++) {
    const view = await (await api(daemon, `/api/threads/${threadId}`)).json();
    if (view.status !== 'running') return view;
    await new Promise(r => setTimeout(r, 50));
  }
  assert.fail('turn did not finish');
};

test('pi plans show the card and the sheet; confirm, reject and skip each go their way', { timeout: 180000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const device = mobile ? 'phone' : 'desktop';
      const title = `pi 设计方案 ${mobile} ${colorScheme}`;
      const thread = await create('pi', title, 'execute');
      await select(page, title, mobile);
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await planChoice(page).click();
      // Plan mode is on: the pill says so, and × is the way off.
      await page.locator('.plan-pill[aria-label="退出计划模式"]').waitFor();
      await page.locator('.composer textarea[placeholder="先分析和讨论方案"]').waitFor();
      if (shots) await page.screenshot({ path: join(shots, `pill-${device}-${colorScheme}.png`), animations: 'disabled' });
      await send(page, '设计一个简洁的登录页');
      // pi's answer is the plan: a small card, and the decision asked in the sheet over the composer.
      await page.locator('.proposal').getByText('设计方案：', { exact: true }).waitFor();
      await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
      await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).waitFor();
      assert.equal(await composerHidden(page), true, 'the composer steps out of sight for the sheet');
      if (shots) await page.screenshot({ path: join(shots, `pi-plan-${device}-${colorScheme}.png`), animations: 'disabled' });
      const before = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).waitFor();
      await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).click();
      await page.getByText(/按我们讨论的计划开始执行/).first().waitFor();
      await page.locator('.action-sheet').waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      const after = await waitIdle(thread.id);
      assert.equal(after.workMode, 'execute');
      assert.equal(after.agentSessionId, before.agentSessionId);

      // A plan sent back with what must change: the thread plans again.
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await planChoice(page).click();
      await page.locator('.plan-pill[aria-label="退出计划模式"]').waitFor();
      await send(page, '再规划一次注册页');
      await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
      await planSheet(page).getByRole('button', { name: '跳过', exact: true }).waitFor();
      await planSheet(page).locator('.sheet-input').fill('把登录改成手机号验证码');
      if (shots) await page.screenshot({ path: join(shots, `pi-reject-${device}-${colorScheme}.png`), animations: 'disabled' });
      await planSheet(page).getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText('把登录改成手机号验证码').first().waitFor();
      const planning = await events(thread.id);
      assert.equal(planning.filter(e => e.type === 'turn.started').at(-1).data.workMode, 'plan', 'the rejection starts its turn in plan mode');
      // The replanned turn ends in a plan again, asked about once more; this time it is skipped.
      await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
      await planSheet(page).getByRole('button', { name: '跳过', exact: true }).click();
      await page.locator('.action-sheet').waitFor({ state: 'detached' });
      assert.equal(await composerHidden(page), false, 'with the plan skipped, the composer is back');
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await page.locator('.composer textarea').waitFor();
      assert.equal(await page.locator('.action-sheet').count(), 0, 'a skipped plan is not asked about again after a refresh');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});

test('questions answer from the sheet, leave a faint line, and the plan opens at full size', { timeout: 180000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const device = mobile ? 'phone' : 'desktop';
      const title = `计划测试 ${mobile} ${colorScheme}`;
      const thread = await create('codex', title);
      await select(page, title, mobile);
      await page.getByRole('button', { name: '添加与模式', exact: true }).waitFor();
      await send(page, 'plan-question');
      await questionSheet(page).getByText('优先支持哪一端？', { exact: true }).waitFor();
      await questionSheet(page).getByRole('radio', { name: /桌面和手机/ }).waitFor();
      if (shots) await page.screenshot({ path: join(shots, `question-${device}-${colorScheme}.png`), animations: 'disabled' });
      // One question, one tap: the option is the answer.
      await questionSheet(page).getByRole('radio', { name: /桌面和手机/ }).click();
      await questionSheet(page).waitFor({ state: 'detached' });
      // The question leaves a trace, and what the agent says after it lands below that trace.
      const trace = page.locator('.trace-line', { hasText: '已回答 · 范围：桌面和手机' });
      await trace.waitFor();
      const answer = page.getByText(/answer:/).first();
      await answer.waitFor();
      const order = await page.evaluate(() => {
        const of = sel => document.querySelector(sel)?.getBoundingClientRect().top;
        return { trace: of('.trace-line'), card: of('.proposal') };
      });
      assert.ok(order.trace >= 0 && order.trace < order.card, 'the trace stands above the plan that follows it');
      if (shots) await page.screenshot({ path: join(shots, `trace-${device}-${colorScheme}.png`), animations: 'disabled' });
      await page.locator('.proposal').getByText('实施计划', { exact: true }).last().waitFor();
      // The plan is in: plan mode is over (the box may be hidden behind the sheet asking about it).
      await page.locator('.composer textarea[placeholder="继续这个线程"]').waitFor({ state: 'attached' });
      // The plan is in: plan mode is over, and whether to run it is asked in the sheet.
      await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
      // The plan is a document in the thread: cut where it runs long, opened to the whole of it
      // in place — no overlay, no separate screen.
      const more = page.locator('.proposal-more').first();
      await more.waitFor();
      await more.getByText('查看整份', { exact: true }).waitFor();
      if (shots) await page.screenshot({ path: join(shots, `plan-cut-${device}-${colorScheme}.png`), animations: 'disabled' });
      await more.click();
      await page.locator('.proposal.full').first().waitFor();
      await page.getByText('保留当前会话和权限设置。').first().waitFor();
      if (shots) await page.screenshot({ path: join(shots, `plan-open-${device}-${colorScheme}.png`), animations: 'disabled' });
      await page.locator('.proposal-more').first().click();
      await page.locator('.proposal.full').first().waitFor({ state: 'detached' });
      const before = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).click();
      await page.getByText(/按我们讨论的计划开始执行/).first().waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      const after = await waitIdle(thread.id);
      assert.equal(after.workMode, 'execute'); assert.equal(after.permissionMode, 'full_access'); assert.equal(after.agentSessionId, before.agentSessionId);
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await planChoice(page).click();
      await page.locator('.plan-pill[aria-label="退出计划模式"]').waitFor();
      // Plan mode on again in a thread with history: nothing to start until there is a new plan.
      assert.equal(await page.getByRole('button', { name: '确认执行该方案', exact: true }).count(), 0);
      await page.locator('.plan-pill[aria-label="退出计划模式"]').click();
      await page.locator('.composer textarea[placeholder="继续这个线程"]').waitFor();
      assert.equal(await (await api(daemon, `/api/threads/${thread.id}`)).json().then(t => t.workMode), 'execute');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});

test('native plan approvals and stopped questions leave their trace', { timeout: 60000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const page = await context.newPage();
    // Full access carries Claude's plan gate itself: the plan is the card, the trace says it ran,
    // and no sheet ever waits for a person.
    const auto = await create('claude', '原生计划审批 claude');
    await select(page, '原生计划审批 claude', false);
    await send(page, 'plan-approval');
    await page.locator('.proposal').getByText('实施计划').first().waitFor();
    await page.getByText('已自动执行 · 完全访问', { exact: true }).waitFor();
    assert.equal(await page.locator('.action-sheet').count(), 0, 'full access asks nobody about the plan');
    await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    const ranByItself = await waitIdle(auto.id);
    assert.equal(ranByItself.workMode, 'execute');
    assert.equal((await events(auto.id)).filter(e => e.type === 'turn.started').length, 1);
    // Asked the ordinary way — zcode has no permission levels — the sheet does the deciding.
    const title = '原生计划审批 zcode';
    const thread = await create('zcode', title);
    await select(page, title, false);
    await send(page, 'plan-approval');
    // The plan itself is the card in the thread; the decision is the sheet.
    await page.locator('.proposal').getByText('实施计划').first().waitFor();
    await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
    await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).click();
    await page.getByText('已确认执行', { exact: true }).waitFor();
    await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    const updated = await waitIdle(thread.id);
    assert.equal(updated.workMode, 'execute');
    assert.equal((await events(thread.id)).filter(e => e.type === 'turn.started').length, 1);
    // Claude asked at a lesser level still reads the plan before it runs.
    const asked = await create('claude', '原生计划审批 提问', 'plan', 'ask');
    await select(page, '原生计划审批 提问', false);
    await send(page, 'plan-approval');
    await page.locator('.proposal').getByText('实施计划').first().waitFor();
    await planSheet(page).getByText('是否执行该方案？', { exact: true }).waitFor();
    await planSheet(page).getByRole('button', { name: '确认执行该方案', exact: true }).click();
    await page.getByText('已确认执行', { exact: true }).waitFor();
    await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    assert.equal((await waitIdle(asked.id)).workMode, 'execute');
    // A question the turn ran out under says so; one skipped says that instead.
    const stopTitle = '取消计划提问';
    const stopped = await create('codex', stopTitle);
    await select(page, stopTitle, false); await send(page, 'plan-question');
    await questionSheet(page).getByRole('radio', { name: /桌面和手机/ }).waitFor();
    assert.equal((await api(daemon, `/api/threads/${stopped.id}/cancel`, { method: 'POST', body: '{}' })).status, 200);
    await page.getByText('提问已结束 · 优先支持哪一端？', { exact: true }).waitFor();
    await create('pi', '普通 Pi', 'execute'); await select(page, '普通 Pi', false);
    await page.getByRole('button', { name: '添加与模式', exact: true }).click();
    assert.equal(await planChoice(page).count(), 1);
    assert.equal(await attachChoice(page).count(), 1);
  } finally { await context.close(); }
});

test('tool approvals are asked in the sheet, with the command one tap away', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const title = `权限审批 ${mobile} ${colorScheme}`;
      const thread = await create('codex', title, 'execute');
      await api(daemon, `/api/threads/${thread.id}`, { method: 'PATCH', body: JSON.stringify({ permissionMode: 'ask' }) });
      await select(page, title, mobile);
      await send(page, 'please approve');
      const sheet = page.getByRole('dialog', { name: '权限审批' });
      await sheet.waitFor();
      await sheet.getByText('运行命令', { exact: true }).waitFor();
      await sheet.locator('.sheet-command').getByText('rm -rf /tmp/one/build').waitFor();
      await sheet.getByRole('button', { name: '允许这一次', exact: true }).waitFor();
      if (shots) await page.screenshot({ path: join(shots, `approval-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await sheet.getByRole('button', { name: '允许这一次', exact: true }).click();
      await page.getByText('已允许 · 运行命令', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    } finally { await context.close(); }
  }
});

test('add menu keeps Plan and attachments accessible and the crowded mobile model dial circular', { timeout: 180000 }, async () => {
  for (const width of [1440, 390, 360]) for (const colorScheme of ['light', 'dark']) {
    const mobile = width < 640;
    const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const title = `输入框 ${width} ${colorScheme}`;
      const thread = await create('codex', title, 'execute');
      await select(page, title, mobile);
      const add = page.getByRole('button', { name: '添加与模式', exact: true });
      const plan = planChoice(page);
      const input = page.locator('.composer textarea');
      assert.equal(await page.getByRole('button', { name: '工作模式', exact: true }).count(), 0);
      await add.click();
      assert.equal(await plan.getAttribute('aria-checked'), 'false');
      await plan.click();
      await page.locator('.plan-pill[aria-label="退出计划模式"]').waitFor();
      await page.locator('.composer textarea[placeholder="先分析和讨论方案"]').waitFor();
      await add.click();
      assert.equal(await plan.getAttribute('aria-checked'), 'true');
      if (shots) await page.screenshot({ path: join(shots, `add-${width}-${colorScheme}.png`), animations: 'disabled' });
      await page.keyboard.press('Escape');
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await page.locator('.plan-pill[aria-label="退出计划模式"]').waitFor();
      await add.click();
      assert.equal(await plan.getAttribute('aria-checked'), 'true');
      await page.keyboard.press('Escape');
      // The pill is the other way off plan mode.
      await page.locator('.plan-pill[aria-label="退出计划模式"]').click();
      await page.locator('.composer textarea[placeholder="先分析和讨论方案"]').waitFor({ state: 'hidden' });
      const updated = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      assert.equal(updated.workMode, 'execute');
      assert.equal(updated.permissionMode, 'full_access');
      await add.click();
      const chooser = page.waitForEvent('filechooser');
      await attachChoice(page).click();
      await (await chooser).setFiles({ name: 'pixel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6r0AAAAASUVORK5CYII=', 'base64') });
      await page.locator('.attachment img').waitFor();
      await page.locator('.attachment-remove').click();
      await send(page, 'usage');
      await page.getByText('echo:usage', { exact: true }).waitFor();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      await send(page, 'slow');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor();
      await input.fill('继续检查布局，保持模型按钮为圆形。');
      await page.getByRole('button', { name: '排队发送', exact: true }).waitFor();
      assert.equal(await page.locator('.composer-bar .coord-tool').count(), 0, 'collaboration lives in the add menu');
      await page.locator('.composer-bar .model-control-trigger').waitFor();
      // A shorter viewport exercises the available space above an on-screen keyboard.
      if (mobile) await page.setViewportSize({ width, height: 450 });
      await input.focus();
      const dial = page.getByRole('button', { name: /^模型与思考强度：/ });
      const rect = await dial.boundingBox();
      if (mobile) assert.equal(rect.width, rect.height, `circular dial at ${width}px`);
      const layout = await page.locator('.composer-bar').evaluate(bar => {
        const bounds = bar.getBoundingClientRect();
        const buttons = [...bar.querySelectorAll('button')].map(el => ({ label: el.getAttribute('aria-label') || el.textContent, rect: el.getBoundingClientRect() })).filter(b => b.rect.width > 0);
        return {
          overflow: buttons.filter(b => b.rect.left < bounds.left - 1 || b.rect.right > bounds.right + 1).map(b => b.label),
          overlap: buttons.slice(1).filter((b, i) => b.rect.left < buttons[i].rect.right - 1).map(b => b.label),
        };
      });
      assert.deepEqual(layout, { overflow: [], overlap: [] });
      if (shots) await page.screenshot({ path: join(shots, `toolbar-${width}-${colorScheme}.png`), animations: 'disabled' });
      await add.click();
      assert.equal(await plan.isDisabled(), true);
      assert.equal(await attachChoice(page).isEnabled(), true);
      await page.waitForFunction(mobile => {
        const menu = document.querySelector(mobile ? '.composer-add-menu' : '.command-menu')?.getBoundingClientRect();
        const add = document.querySelector('.composer-add')?.getBoundingClientRect();
        return menu && add && menu.y >= 0 && menu.bottom <= add.y;
      }, mobile);
      // The placement check above reads menu and trigger in the same browser frame.
      if (shots) await page.screenshot({ path: join(shots, `add-running-${width}-${colorScheme}.png`), animations: 'disabled' });
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '停止', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
    } finally { await context.close(); }
  }
});

test('a phone folds the empty composer to one line while a turn runs, and opens it on touch', { timeout: 120000 }, async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme });
    try {
      const page = await context.newPage();
      const title = `折叠输入框 ${colorScheme}`;
      const thread = await create('codex', title, 'execute');
      await select(page, title, true);
      await send(page, 'slow');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor();
      await page.locator('.composer textarea').blur();
      await page.locator('.composer-box.collapsed').waitFor();
      await page.locator('.composer-box.collapsed textarea[placeholder="追加一条…"]').waitFor();
      await page.locator('.composer-box.collapsed .composer-add').waitFor();
      await page.locator('.composer-box.collapsed .send-btn.stop').waitFor();
      assert.equal(await page.getByRole('button', { name: '协作', exact: true }).count(), 0, 'the folded line keeps only add, write and stop');
      if (shots) await page.screenshot({ path: join(shots, `composer-collapsed-phone-${colorScheme}.png`), animations: 'disabled' });
      // Touching the line is focus: the box comes back whole before the keyboard can cover it.
      await page.locator('.composer-box.collapsed textarea').tap();
      await page.locator('.composer-box.collapsed').waitFor({ state: 'detached' });
      await page.locator('.composer textarea[placeholder="追加一条，这一轮结束后发送"]').waitFor();
      assert.equal(await page.locator('.composer-bar .coord-tool').count(), 0, 'collaboration lives in the add menu');
      await page.getByRole('button', { name: '停止', exact: true }).click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      // Idle, it folds the same way: nothing being written is one line.
      await page.locator('.composer textarea').focus();
      await page.locator('.composer textarea').blur();
      await page.locator('.composer-box.collapsed textarea[placeholder="继续这个线程"]').waitFor();
      assert.equal(await page.getByRole('button', { name: '协作', exact: true }).count(), 0);
      if (shots) await page.screenshot({ path: join(shots, `composer-collapsed-idle-phone-${colorScheme}.png`), animations: 'disabled' });
      await page.locator('.composer-box.collapsed textarea').tap();
      assert.equal(await page.locator('.composer-bar .coord-tool').count(), 0, 'collaboration lives in the add menu');
      // A new thread keeps its strip of agent and directory in view: that is the choice being made.
      await openUi(page, daemon);
      await page.locator('.composer .composer-context').waitFor();
      await page.locator('.composer textarea').blur();
      assert.equal(await page.locator('.composer-box.collapsed').count(), 0);
    } finally { await context.close(); }
  }
  // Desktop keeps the whole box, running or not.
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  try {
    const page = await context.newPage();
    const thread = await create('codex', '折叠输入框 桌面', 'execute');
    await select(page, '折叠输入框 桌面', false);
    await send(page, 'slow');
    await page.getByRole('button', { name: '停止', exact: true }).waitFor();
    await page.locator('.composer textarea').blur();
    assert.equal(await page.locator('.composer-box.collapsed').count(), 0);
    await page.getByRole('button', { name: '停止', exact: true }).click();
  } finally { await context.close(); }
});
