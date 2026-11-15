import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { openCollaboration, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });
const post = async (path, body) => {
  const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};
const waitIdle = async id => {
  for (let n = 0; n < 200; n++) {
    const thread = await (await api(daemon, `/api/threads/${id}`)).json();
    if (thread.status !== 'running') return;
    await new Promise(r => setTimeout(r, 50));
  }
  throw new Error('thread did not finish');
};

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`collaboration picker, output and draft preservation: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    try {
      const title = `文档协作 ${phone ? '手机' : '桌面'} ${colorScheme}`;
      const root = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title });
      await openUi(page, daemon);
      if (phone) await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: title }).click();
      // On the desktop collaboration is switched on first; on the phone the @ alone starts it.
      if (!phone) {
        await openCollaboration(page);
        await page.locator('.coord-picker-head button').click();
        await page.getByRole('button', { name: '启用协作', exact: true }).click();
        // Nobody is in the team yet: the button says it is on, and no line is added above the box.
        await page.locator('.composer-add.on').waitFor();
        assert.equal(await page.locator('.coord-bar').count(), 0);
      }
      const box = page.getByRole('textbox', { name: '消息', exact: true });
      const list = page.getByRole('listbox', { name: '选择协作智能体' });
      // An @ is only a character until an agent is picked: a word nobody goes by, an address, and a
      // list turned away all leave it as text, and the list stays away while that word is typed.
      await box.fill('@nobody');
      assert.equal(await list.count(), 0);
      await box.fill('mail a@b');
      assert.equal(await list.count(), 0);
      await box.fill('@');
      await list.waitFor();
      await list.getByRole('button', { name: '不选择智能体' }).click();
      await list.waitFor({ state: 'detached' });
      await box.pressSequentially('c');
      assert.equal(await box.inputValue(), '@c');
      assert.equal(await list.count(), 0);
      if (!phone) {
        await box.fill('');
        await box.fill('@');
        await list.waitFor();
        await box.press('Escape');
        await list.waitFor({ state: 'detached' });
        await box.pressSequentially('c');
        assert.equal(await list.count(), 0);
      }
      assert.equal(await page.locator('.coord-row').count(), 0);
      await box.fill('');
      await box.fill('@');
      await list.waitFor();
      assert.ok(await page.getByRole('option', { name: /claude/i }).first().locator('svg').count());
      const shots = '/tmp/calli-coordination-shots'; mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-menu.png` });
      await page.getByRole('option', { name: /claude/i }).first().click();
      // The agent gets a line of its own, and the caret is there for its task; Enter goes back to the message.
      const row = page.locator('.coord-row');
      assert.match(await row.innerText(), /claude/i);
      const task = row.getByRole('textbox');
      assert.ok(await task.evaluate(el => el === document.activeElement));
      await task.fill('只看异常处理');
      // The model its member starts with is a quiet mark at the end of the line, worded once chosen.
      const quiet = row.getByRole('button', { name: /的模型与思考强度$/ });
      assert.equal(await quiet.getAttribute('aria-haspopup'), 'menu');
      await quiet.click();
      const control = page.locator('.model-control-panel');
      const toModels = control.getByRole('button', { name: /切换模型/ });
      await control.getByRole('menuitemradio').or(toModels).first().waitFor();
      if (await toModels.count()) await toModels.click();
      const choice = control.getByRole('menuitemradio').nth(1);
      const picked = (await choice.locator('.pop-item-label').innerText()).trim();
      await choice.click();
      await page.keyboard.press('Escape');
      await control.waitFor({ state: 'detached' });
      await row.locator('.member-model.set', { hasText: picked }).waitFor();
      // Its level sits beside the model: full access unless changed, here to asking first.
      const level = row.getByRole('button', { name: /的权限$/ });
      assert.match(await level.getAttribute('title'), /不再询问/);
      await level.click();
      await page.getByRole('menuitemradio', { name: /^请求批准/ }).click();
      await row.locator('button[title*="都先询问"]').waitFor();
      await task.press('Enter');
      assert.ok(await box.evaluate(el => el === document.activeElement));
      await box.fill('请审查接口文档');
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-rows.png` });
      const sent = page.waitForRequest(req => req.url().endsWith(`/threads/${root.id}/turns`) && req.method() === 'POST');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const mention = (await sent).postDataJSON().mentions[0];
      assert.equal(mention.id, 'claude');
      assert.equal(mention.task, '只看异常处理');
      await waitIdle(root.id);
      await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === '');
      await page.locator('.composer-add.on').waitFor();
      await page.locator('.coord-row').waitFor({ state: 'detached' });
      await page.locator('.row.user .bubble', { hasText: '只看异常处理' }).waitFor();

      // The fixture invokes the actual injected CLI, creating a normal member thread.
      const directive = Buffer.from(JSON.stringify({ args: ['spawn', '--agent', 'claude', '--name', '文档审查', '--message-file', '-', '--request-id', 'review'], text: '检查接口与异常处理 CALLI_REPORT' })).toString('base64');
      await post(`/api/threads/${root.id}/turns`, { text: `CALLI_COMMAND:${directive}` });
      await box.fill('保留这段主线程草稿');
      await openCollaboration(page);
      await page.getByRole('button', { name: '协作设置', exact: true }).click();
      await page.getByRole('button', { name: '查看智能体', exact: true }).click();
      const panel = page.getByRole('region', { name: '智能体面板' });
      const card = panel.locator('.agent-member', { hasText: '文档审查' });
      await card.waitFor();
      await page.waitForFunction(() => document.querySelector('.agent-member')?.textContent?.includes('已返回'));
      const team = await (await api(daemon, `/api/threads/${root.id}/coordination`)).json();
      const child = team.members[0].id;
      if (!phone) {
        // Members are rows under their lead in the sidebar, saying what they did, not threads of their own.
        const lead = page.locator('.sidebar .team-group', { has: page.locator('.thread-title', { hasText: title }) });
        await lead.waitFor();
        if (!(await lead.locator('.handover-member').count())) await lead.locator('.handover-toggle').click();
        await lead.locator('.handover-member', { hasText: '文档审查完成' }).waitFor();
        assert.equal(await page.locator('.sidebar .thread-row').filter({ hasText: '文档审查完成' }).count(), 0);
        await page.screenshot({ path: `${shots}/desktop-${colorScheme}-sidebar-team.png` });
      }
      // The member was made with what was chosen on its line, whether the team existed yet or not.
      // The member runs at the level chosen on its line, not the team's full access.
      assert.equal((await (await api(daemon, `/api/threads/${child}`)).json()).permissionMode, 'ask');
      const offered = await (await api(daemon, '/api/agents/claude/models')).json();
      assert.equal((await (await api(daemon, `/api/threads/${child}`)).json()).model, offered.find(m => m.label === picked).id);
      if (!phone) {
        // On a wide screen the panel's edge drags; a double click gives the width back.
        const edge = panel.getByRole('separator', { name: '调整宽度' });
        const wide = async () => Math.round((await panel.boundingBox()).width);
        const usual = await wide();
        const at = await edge.boundingBox();
        await page.mouse.move(at.x + 2, at.y + 200);
        await page.mouse.down();
        await page.mouse.move(at.x - 118, at.y + 200, { steps: 4 });
        await page.mouse.up();
        assert.ok(Math.abs((await wide()) - usual - 120) <= 2, `the panel grew by the drag (${usual} to ${await wide()})`);
        await edge.dblclick();
        assert.equal(await wide(), usual);
      }
      const before = await (await api(daemon, `/api/threads/${child}/events`)).json();
      // The detail retries even if its first history request fails.
      await page.route(`**/threads/${child}/events?tail=1`, route => route.fulfill({ status: 503, body: '{"error":"offline"}' }), { times: 1 });
      await card.click();
      await panel.getByText('文档审查完成。', { exact: false }).waitFor();
      const own = panel.locator('.agent-panel-head .member-model.set', { hasText: picked });
      await own.waitFor();
      // Opened from the top of the panel, the member's own setting is all on screen.
      await own.click();
      const ownPanel = page.getByRole('menu', { name: '模型与思考强度', exact: true });
      await ownPanel.waitFor();
      await page.waitForTimeout(250);
      const shown = await ownPanel.boundingBox();
      const screen = page.viewportSize();
      assert.ok(shown.y >= 0 && shown.x >= 0 && shown.y + shown.height <= screen.height + 1 && shown.x + shown.width <= screen.width + 1, `the setting fits the screen (${JSON.stringify(shown)})`);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-own-model.png` });
      await page.keyboard.press('Escape');
      await ownPanel.waitFor({ state: 'detached' });
      // A member has a box of its own, saying whom it writes to.
      await panel.getByRole('textbox', { name: '消息', exact: true }).waitFor();
      assert.equal(await panel.getByRole('textbox', { name: '消息', exact: true }).getAttribute('placeholder'), '发给 文档审查…');
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), colorScheme);
      if (phone) {
        const rect = await panel.boundingBox();
        assert.ok(rect && rect.x === 0 && Math.abs(rect.width - 390) < 2);
      }
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-detail.png` });
      await panel.getByRole('button', { name: '返回智能体列表' }).click();
      await card.waitFor();
      await panel.locator('.coord-unread').waitFor({ state: 'detached' });
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-list.png` });
      // The team's messages read like a chat: the task with who sent it, and its answer folded under it.
      await panel.getByRole('tab', { name: /^消息/ }).click();
      const talk = panel.locator('.talk');
      assert.equal(await talk.count(), 1);
      assert.match(await talk.locator('.talk-said').first().innerText(), /主智能体[\s\S]*发给 文档审查[\s\S]*检查接口与异常处理/);
      assert.equal(await talk.locator('.talk-mark svg').first().count(), 1);
      const replies = talk.getByRole('button', { name: /1 条回复/ });
      assert.equal(await replies.getAttribute('aria-expanded'), 'true');
      assert.match(await talk.locator('.talk-thread').innerText(), /文档审查[\s\S]*文档审查完成。/);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-talk.png` });
      await replies.click();
      assert.equal(await talk.locator('.talk-thread').count(), 0);
      await panel.getByRole('tab', { name: /^成员/ }).click();
      await card.waitFor();
      await panel.getByRole('button', { name: '关闭智能体面板' }).click();
      await panel.waitFor({ state: 'detached' });
      assert.equal(await box.inputValue(), '保留这段主线程草稿');
      // What the member returned sits in the thread as a folded card, opened to its text as Markdown.
      const delivered = page.locator('.row.delivered', { hasText: '文档审查' });
      await delivered.waitFor();
      assert.equal(await delivered.locator('.delivered-body').count(), 0);
      assert.match(await delivered.locator('.delivered-sum').innerText(), /^文档审查完成。/);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-result.png` });
      await delivered.getByRole('button').click();
      assert.equal(await delivered.locator('.delivered-body p').count(), 2);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-result-open.png` });
      const after = await (await api(daemon, `/api/threads/${child}/events`)).json();
      assert.equal(after.filter(e => e.type === 'turn.started').length, before.filter(e => e.type === 'turn.started').length, 'viewing output does not start work');
      await openCollaboration(page);
      await page.getByRole('button', { name: '协作设置', exact: true }).click();
      await page.getByRole('button', { name: '查看智能体', exact: true }).click();
      await card.click();
      if (phone) {
        await page.goBack();
        await card.waitFor();
        await page.goBack();
        await panel.waitFor({ state: 'detached' });
      } else {
        await page.keyboard.press('Escape');
        await panel.waitFor({ state: 'detached' });
      }

      // Written to directly: the member answers in its own thread, and the lead is not woken.
      const leadTurns = async () => (await (await api(daemon, `/api/threads/${root.id}/events`)).json()).filter(e => e.type === 'turn.started').length;
      const leadBefore = await leadTurns();
      await openCollaboration(page);
      await page.getByRole('button', { name: '协作设置', exact: true }).click();
      await page.getByRole('button', { name: '查看智能体', exact: true }).click();
      await card.click();
      const direct = panel.getByRole('textbox', { name: '消息', exact: true });
      await direct.fill('slow 把结论压到三条');
      const toMember = page.waitForRequest(req => req.url().endsWith(`/threads/${child}/turns`) && req.method() === 'POST');
      await panel.getByRole('button', { name: '发送', exact: true }).click();
      await toMember;
      await panel.locator('.row.user .bubble', { hasText: '把结论压到三条' }).waitFor();
      // Running, more is queued behind the turn, with stop where the lead's box has it.
      await page.waitForFunction(() => document.querySelector('.agent-panel textarea')?.getAttribute('placeholder')?.includes('这一轮结束后'));
      await direct.fill('再补一条风险');
      await panel.getByRole('button', { name: /^排队发送|^发送/ }).click();
      await panel.locator('.queue-item', { hasText: '再补一条风险' }).waitFor();
      await direct.fill('先写一半');
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-direct-running.png` });
      await panel.getByRole('button', { name: '停止', exact: true }).click();
      // The stop hands what was waiting back to the box, ahead of what was being written.
      await page.waitForFunction(() => document.querySelector('.agent-panel textarea')?.value.includes('再补一条风险'));
      assert.match(await direct.inputValue(), /再补一条风险[\s\S]*先写一半/);
      await waitIdle(child);
      assert.equal(await leadTurns(), leadBefore, 'talking to a member does not wake the lead');
      await panel.getByRole('button', { name: '返回智能体列表' }).click();
      await panel.getByRole('tab', { name: /^消息/ }).click();
      assert.match(await panel.locator('.talk').last().innerText(), /你[\s\S]*发给 文档审查[\s\S]*把结论压到三条/);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-direct-talk.png` });
      await panel.getByRole('tab', { name: /^成员/ }).click();
      await card.click();
      await direct.fill('补一条测试要求');
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-direct-idle.png` });
      // Going through the lead instead carries the words along, and leaves the member's box empty.
      await panel.getByRole('button', { name: '让主智能体调整', exact: true }).click();
      await panel.waitFor({ state: 'detached' });
      // The words land in the lead's box a render after the panel goes.
      const carried = '请调整「文档审查」的任务：补一条测试要求\n\n保留这段主线程草稿';
      for (let n = 0; n < 50 && await box.inputValue() !== carried; n++) await page.waitForTimeout(50);
      assert.equal(await box.inputValue(), carried);
      await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-via-lead.png` });
      await openCollaboration(page);
      await page.getByRole('button', { name: '协作设置', exact: true }).click();
      await page.getByRole('button', { name: '查看智能体', exact: true }).click();
      await card.click();
      assert.equal(await direct.inputValue(), '');
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
