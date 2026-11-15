import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
before(async () => {
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['codex', 'claude', 'zcode', 'pi'].map(id => ({ id, adapter: id, command: [process.execPath, join(repoRoot, 'daemon/test/fixtures', `fake-${id === 'claude' ? 'acp' : id}.mjs`)] })) }) });
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

const add = page => page.getByRole('button', { name: '添加与模式', exact: true });
const input = page => page.locator('.composer textarea');
const mobileMenu = page => page.viewportSize().width <= 640;
const item = (page, name) => {
  const options = { name: typeof name === 'string' ? new RegExp(`^${name}(?: |$)`) : name };
  return page.getByRole('menuitem', options).or(page.getByRole('option', options));
};
const search = async (page, section, query) => {
  if (mobileMenu(page)) await item(page, section).click();
  await page.getByRole('textbox', { name: mobileMenu(page) ? `搜索${section}` : '搜索操作与技能', exact: true }).fill(query);
};
const openGoal = async page => {
  await add(page).click();
  if (!mobileMenu(page)) await page.getByRole('textbox', { name: '搜索操作与技能' }).fill('目标');
};
const activate = async (page, target) => {
  if (!mobileMenu(page)) return target.click();
  await target.scrollIntoViewIfNeeded();
  const bounds = await target.boundingBox();
  // The phone's quick-switch pad forwards actual taps to controls beneath it.
  await page.touchscreen.tap(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
};
const goalAction = async (page, name) => {
  if (name === '设置目标') { await openGoal(page); await item(page, name).click(); }
  else await activate(page, page.locator('.goal-card').getByRole('button', { name, exact: true }));
};
const startGoal = async (page, objective) => {
  await goalAction(page, '设置目标');
  await input(page).fill(`/goal ${objective}`);
  await page.getByRole('button', { name: '发送', exact: true }).click();
};
const create = async (page, agent, title, mobile) => {
  const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent, title, cwd: daemon.workdir, permissionMode: 'full_access' }) });
  assert.equal(res.status, 201);
  const thread = await res.json();
  await openUi(page, daemon);
  await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
  return thread;
};
const screenshot = async (page, name) => { if (shots) await page.screenshot({ path: join(shots, `${name}.png`), animations: 'disabled' }); };
const waitForPaletteWidth = page => page.waitForFunction(() => {
  const menu = document.querySelector('.command-menu')?.getBoundingClientRect();
  const composer = document.querySelector('.composer-box')?.getBoundingClientRect();
  return menu && composer && Math.abs(menu.width - composer.width) < 0.5;
});

test('long catalogs scroll by touch and wheel without losing the draft or opening the phone sidebar', { timeout: 180000 }, async () => {
  const commands = ['command', 'skill'].flatMap(kind => Array.from({ length: 40 }, (_, i) => ({
    name: `scroll-${kind}-${String(i + 1).padStart(2, '0')}`,
    description: `回归测试第 ${i + 1} 项，检查长列表滚动后仍能选择并保留草稿`,
    kind,
  })));
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      await page.route('**/api/agents/*/commands*', route => route.fulfill({ json: commands }));
      await openUi(page, daemon);
      const cdp = mobile ? await context.newCDPSession(page) : null;
      for (const height of mobile ? [844, 450] : [900]) for (const kind of ['command', 'skill']) {
        // Open the phone home menu before shrinking to the keyboard-sized viewport.
        if (mobile) await page.setViewportSize({ width: 390, height: 844 });
        await input(page).fill('保留滚动前的草稿');
        await add(page).click();
        await search(page, kind === 'skill' ? '技能' : '操作', mobile ? '' : `scroll-${kind}`);
        if (mobile) await page.setViewportSize({ width: 390, height });
        await waitForPaletteWidth(page);
        const palette = page.locator('.command-menu');
        const query = palette.getByRole('textbox');
        await item(page, new RegExp(`^/scroll-${kind}-40`)).waitFor({ state: 'attached' });
        const metrics = await palette.evaluate(el => ({ client: el.clientHeight, scroll: el.scrollHeight }));
        assert.ok(metrics.scroll > metrics.client * 3, 'the test contains several screens of entries');
        const bounds = await palette.boundingBox();
        assert.ok(bounds.y >= 0, 'the catalog fits above the composer');
        const from = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height - (kind === 'skill' && mobile ? 60 : 20) };
        if (mobile) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
          for (let i = 1; i <= 6; i++) {
            await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x, y: from.y - i * 12 }] });
          }
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        } else {
          await page.mouse.move(from.x, from.y);
          await page.mouse.wheel(0, 150);
        }
        await page.waitForFunction(() => document.querySelector('.command-menu')?.scrollTop > 30);
        assert.equal(await input(page).inputValue(), '保留滚动前的草稿', 'swiping does not choose an entry');
        await screenshot(page, `scroll-middle-${mobile ? 'phone' : 'desktop'}-${colorScheme}-${kind}-${height}`);
        if (mobile) {
          for (let swipe = 0; swipe < 100; swipe++) {
            if (await palette.evaluate(el => el.scrollTop + el.clientHeight >= el.scrollHeight - 2)) break;
            await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
            const distance = from.y - (bounds.y + 58);
            for (let i = 1; i <= 6; i++) {
              await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x, y: from.y - distance * i / 6 }] });
            }
            await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
            await page.waitForTimeout(60);
          }
        } else await page.mouse.wheel(0, metrics.scroll);
        await page.waitForFunction(() => {
          const el = document.querySelector('.command-menu');
          return el && el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
        });
        const searchBounds = await query.boundingBox();
        assert.ok(searchBounds.y >= bounds.y && searchBounds.y + searchBounds.height < bounds.y + bounds.height, 'search stays on screen after scrolling');
        const last = item(page, new RegExp(`^/scroll-${kind}-40`));
        const lastBounds = await last.boundingBox();
        assert.ok(lastBounds.y >= searchBounds.y + searchBounds.height && lastBounds.y + lastBounds.height <= bounds.y + bounds.height, 'the last entry is visible without auto-scrolling it');
        await screenshot(page, `scroll-bottom-${mobile ? 'phone' : 'desktop'}-${colorScheme}-${kind}-${height}`);
        // Let the browser finish scrolling before tapping; a tap during momentum stops it.
        if (mobile) await page.waitForTimeout(350);
        await (mobile ? last.tap() : last.click());
        await palette.waitFor({ state: 'hidden' });
        assert.equal(await input(page).inputValue(), `/scroll-${kind}-40 保留滚动前的草稿`);

        // Keyboard reveal still works, and filtering returns the scroll position to the top.
        if (mobile) await page.setViewportSize({ width: 390, height: 844 });
        await add(page).click();
        await search(page, kind === 'skill' ? '技能' : '操作', `scroll-${kind}`);
        await query.press('ArrowUp');
        await page.waitForFunction(() => document.querySelector('.command-menu')?.scrollTop > 500);
        assert.equal(await last.getAttribute('aria-selected'), 'true');
        await query.fill(`scroll-${kind}-01`);
        await page.waitForFunction(() => document.querySelector('.command-menu')?.scrollTop === 0);
        await query.press('Escape');
      }
      if (mobile) {
        await add(page).click();
        await search(page, '操作', '');
        await waitForPaletteWidth(page);
        const bounds = await page.locator('.command-menu').boundingBox();
        const from = { x: bounds.x + 12, y: bounds.y + 100 };
        // A sideways gesture starting in the catalog must not navigate to the sidebar.
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] });
        for (let i = 1; i <= 6; i++) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + i * 22, y: from.y - i * 3 }] });
        }
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        assert.equal(await page.locator('.app.drawer-open').count(), 0, 'the list swipe does not open the sidebar');
        await page.keyboard.press('Escape');
      }
    } finally { await context.close(); }
  }
});

test('desktop plus and slash share a full-width catalog with search, keyboard selection and dismissal', { timeout: 60000 }, async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme });
    try {
      const page = await context.newPage();
      const thread = await create(page, 'codex', `桌面命令面板 ${colorScheme}`, false);
      await input(page).fill('/');
      await item(page, /^\/inspect/).waitFor();
      const slashEntries = await page.getByRole('option').allTextContents();
      await add(page).click();
      const query = page.getByRole('textbox', { name: '搜索操作与技能' });
      await query.waitFor();
      assert.deepEqual(await page.getByRole('option').allTextContents(), slashEntries);
      assert.equal(await query.evaluate(el => el === document.activeElement), true);
      await waitForPaletteWidth(page);
      const bounds = await page.locator('.command-menu').boundingBox();
      const composer = await page.locator('.composer-box').boundingBox();
      assert.equal(Math.round(bounds.width), Math.round(composer.width));
      assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= composer.y);
      await query.press('Escape');
      await page.locator('.command-menu').waitFor({ state: 'hidden' });
      assert.equal(await input(page).inputValue(), '/');

      await input(page).fill('保留原来的任务');
      await add(page).click();
      await query.fill('不存在的命令');
      await page.getByText('没有匹配的结果', { exact: true }).waitFor();
      await query.press('ArrowDown');
      await query.press('Enter');
      assert.equal(await input(page).inputValue(), '保留原来的任务');
      assert.equal((await (await api(daemon, `/api/threads/${thread.id}/events`)).json()).filter(e => e.type === 'turn.started').length, 0);
      await query.fill('inspect');
      await query.press('ArrowUp');
      await query.press('Tab');
      assert.equal(await input(page).inputValue(), '/inspect 保留原来的任务');
      await add(page).click();
      await query.fill('review');
      await query.press('Enter');
      assert.equal(await input(page).inputValue(), '/review 保留原来的任务');

      await add(page).click();
      await input(page).click();
      await page.locator('.command-menu').waitFor({ state: 'hidden' });
      await add(page).click();
      await add(page).click();
      await page.locator('.command-menu').waitFor({ state: 'hidden' });
      assert.equal(await input(page).inputValue(), '/review 保留原来的任务');
      await add(page).click();
      await screenshot(page, `palette-desktop-${colorScheme}`);
    } finally { await context.close(); }
  }
});

test('add menu offers searchable actions and skills and controls goals without losing drafts in four layouts', { timeout: 180000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const key = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
      const thread = await create(page, 'codex', `加号菜单 ${key}`, mobile);
      await input(page).fill('检查错误处理');
      await add(page).click();
      await screenshot(page, `home-${key}`);
      await search(page, '技能', 'inspect');
      if (mobile) {
        await page.setViewportSize({ width: 390, height: 450 });
        await waitForPaletteWidth(page);
        const bounds = await page.locator('.command-menu').boundingBox();
        const composer = await page.locator('.composer-box').boundingBox();
        assert.equal(Math.round(bounds.width), Math.round(composer.width), 'skills span the composer width');
        assert.equal(Math.round(bounds.x), Math.round(composer.x));
        assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= composer.y, 'skills fit above the composer with a keyboard');
        assert.equal(await page.locator('.pop-scrim').count(), 0, 'the skills list does not dim the conversation');
        assert.equal(await page.getByRole('textbox', { name: '搜索技能' }).evaluate(el => el === document.activeElement), true);
      }
      await item(page, /^(?:\/)?inspect/).waitFor();
      await screenshot(page, `skills-${key}`);
      await item(page, /^(?:\/)?inspect/).click();
      if (mobile) await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await input(page).inputValue(), '/inspect 检查错误处理');
      await page.getByRole('button', { name: '发送', exact: true }).click();
      await page.getByText(/skills:inspect@/).waitFor();
      await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === '');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });

      await input(page).fill('保留我的草稿');
      await add(page).click();
      await search(page, '操作', '模型');
      if (mobile) {
        await waitForPaletteWidth(page);
        const bounds = await page.locator('.command-menu').boundingBox();
        const composer = await page.locator('.composer-box').boundingBox();
        assert.equal(Math.round(bounds.width), Math.round(composer.width), 'actions use the same wide palette');
        await screenshot(page, `actions-wide-${key}`);
      }
      await item(page, /^模型与思考强度/).click();
      await page.getByRole('menu', { name: '模型与思考强度', exact: true }).waitFor();
      assert.equal(await input(page).inputValue(), '保留我的草稿');
      await page.keyboard.press('Escape');
      await add(page).click();
      await search(page, '操作', '审查');
      await screenshot(page, `actions-${key}`);
      await item(page, /^(审查代码|\/review)/).click();
      assert.equal(await input(page).inputValue(), '/review 保留我的草稿');
      await input(page).fill('保留我的草稿');

      await goalAction(page, '设置目标');
      assert.equal(await input(page).inputValue(), '/goal 保留我的草稿');
      await screenshot(page, `goal-compose-${key}`);
      await input(page).fill('保留我的草稿');
      await startGoal(page, 'slow');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor();
      await input(page).fill('保留我的草稿');
      // Control commands must not consume or send pending attachments.
      await add(page).click();
      const chooser = page.waitForEvent('filechooser');
      await item(page, '添加附件').click();
      await (await chooser).setFiles({ name: 'pixel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6r0AAAAASUVORK5CYII=', 'base64') });
      await page.locator('.attachment img').waitFor();
      if (mobile) {
        await page.setViewportSize({ width: 390, height: 450 });
        await add(page).click();
        const menu = await page.getByRole('menu', { name: '添加与模式', exact: true }).boundingBox();
        const skills = page.getByRole('menuitem', { name: '技能', exact: true });
        await skills.scrollIntoViewIfNeeded();
        const last = await skills.boundingBox();
        assert.ok(menu.y >= 0 && last.y + last.height <= menu.y + menu.height + 1, 'the last menu entry can be scrolled into view above the keyboard');
        await screenshot(page, `home-keyboard-${key}`);
        await page.keyboard.press('Escape');
      }
      await openGoal(page);
      assert.equal(await item(page, /^设置目标/).isDisabled(), true);
      await screenshot(page, `goal-running-${key}`);
      assert.equal(await item(page, '查看目标').count(), 0);
      await page.keyboard.press('Escape');
      await activate(page, page.locator('.goal-card').getByRole('button', { name: '目标详情', exact: true }));
      await page.locator('.goal-full', { hasText: 'slow' }).waitFor();
      assert.equal(await input(page).inputValue(), '保留我的草稿');
      assert.equal(await page.locator('.attachment img').count(), 1);
      assert.equal(await page.locator('.queue-item').count(), 0);
      await goalAction(page, '暂停目标');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      await goalAction(page, '继续目标');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor();
      await goalAction(page, '暂停目标');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      await goalAction(page, '清除目标');
      await page.locator('.goal-card').waitFor({ state: 'detached' });
      assert.equal(await input(page).inputValue(), '保留我的草稿');
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.ok(events.some(e => e.type === 'turn.started' && e.data.text === '/goal slow'));
      assert.equal(events.filter(e => e.type === 'turn.started' && e.data.text?.startsWith('/goal')).some(e => e.data.images?.length), false);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});

test('goal actions follow capabilities, Plan blocks starting a goal, and Pi skills keep their native command', { timeout: 60000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  try {
    const page = await context.newPage();
    await create(page, 'claude', 'Claude 目标菜单', false);
    await openGoal(page);
    assert.equal(await item(page, '暂停目标').count(), 0);
    assert.equal(await item(page, '继续目标').count(), 0);
    await page.keyboard.press('Escape');
    await startGoal(page, 'slow');
    await page.getByRole('button', { name: '停止', exact: true }).waitFor();
    await openGoal(page);
    assert.equal(await item(page, /^查看目标/).isDisabled(), true);
    assert.equal(await item(page, /^设置目标/).isDisabled(), true);
    await item(page, '清除目标').click();
    await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });

    await create(page, 'codex', '计划内的目标菜单', false);
    await add(page).click();
    await page.getByRole(mobileMenu(page) ? 'menuitemcheckbox' : 'option', { name: /^计划模式/ }).click();
    await page.locator('.composer textarea[placeholder="先分析和讨论方案"]').waitFor();
    await openGoal(page);
    assert.equal(await item(page, /^设置目标/).isDisabled(), true);
    assert.equal(await item(page, /^继续目标/).isDisabled(), true);
    const query = page.getByRole('textbox', { name: '搜索操作与技能' });
    await query.fill('设置目标');
    await query.press('ArrowDown');
    await query.press('Enter');
    assert.equal(await page.getByRole('dialog', { name: '设置目标', exact: true }).count(), 0);
    await query.fill('继续目标');
    await query.press('ArrowUp');
    assert.equal(await page.getByRole('option', { selected: true }).count(), 0, 'disabled goal action cannot be selected');

    await create(page, 'pi', 'Pi 技能菜单', false);
    await input(page).fill('整理文档');
    await add(page).click();
    assert.equal(await item(page, /^(目标模式|设置目标)/).count(), 0);
    await search(page, '技能', 'skill:pdf');
    await item(page, /^(?:\/)?skill:pdf/).click();
    assert.equal(await input(page).inputValue(), '/skill:pdf 整理文档');
  } finally { await context.close(); }
});


test('goals without a live card retain visible controls and drafts in all four layouts', { timeout: 120000 }, async () => {
  for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      const thread = await create(page, 'claude', `目标操作 ${mobile} ${colorScheme}`, mobile);
      await startGoal(page, 'slow');
      await page.getByRole('button', { name: '停止', exact: true }).waitFor();
      await input(page).fill('保留我的草稿');
      await openGoal(page);
      assert.equal(await item(page, '查看目标').isDisabled(), true);
      assert.equal(await item(page, '清除目标').isEnabled(), true);
      assert.equal(await item(page, '暂停目标').count(), 0);
      await screenshot(page, `goal-fallback-running-${mobile ? 'phone' : 'desktop'}-${colorScheme}`);
      await item(page, '清除目标').click();
      await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await input(page).inputValue(), '保留我的草稿');
      assert.equal(await page.locator('.queue-item').count(), 0);
      await page.reload();
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.thread-title', { hasText: thread.title }).first().click();
      await openGoal(page);
      assert.equal(await item(page, '查看目标').isEnabled(), true);
      await screenshot(page, `goal-fallback-idle-${mobile ? 'phone' : 'desktop'}-${colorScheme}`);
      await item(page, '查看目标').click();
      await page.getByText('当前没有目标。', { exact: true }).last().waitFor();
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      assert.ok(events.some(e => e.type === 'turn.started' && e.data.text === '/goal clear' && e.data.control));
      assert.ok(events.some(e => e.type === 'turn.started' && e.data.text === '/goal'));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    } finally { await context.close(); }
  }
});
