import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

const named = name => ({ name, exact: true });

test('the compact thread menu preserves nested actions, keyboard return and the draft', { timeout: 180000 }, async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', label: 'Primary', command }] }) });
  const browser = await launchBrowser();
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  try {
    const section = await (await api(daemon, '/api/sections', { method: 'POST', body: JSON.stringify({ name: 'Menu review' }) })).json();
    const todo = await (await api(daemon, '/api/todos', { method: 'POST', body: JSON.stringify({ text: 'Verify nested actions' }) })).json();
    for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
      const mobile = width < 900;
      const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title: `Menu ${width} ${theme}` }) })).json();
      const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      try {
        const page = await context.newPage(); page.setDefaultTimeout(12000);
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        await page.route('**/api/agents/*/quota*', route => route.fulfill({ json: { checkedAt: new Date().toISOString(), windows: [
          { label: '5H', usedPercent: 25 }, { label: '7D', usedPercent: 80 }, { label: '7D', scope: 'Review', usedPercent: 95, loadPercent: 100 },
        ] } }));
        await openUi(page, daemon);
        await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
        await page.locator('.composer textarea').fill('Draft survives nested menus');
        const root = page.locator('.menu-float');
        const branch = page.locator(mobile ? '.menu-float' : '.menu-branch');
        // The open thread's "…" serves the conversation; the list's row menu manages the row.
        const closeList = async () => {
          if (!mobile || !(await page.evaluate(() => document.documentElement.classList.contains('list-open')))) return;
          // Picking the open thread's own row folds the list away and stays where it is.
          await page.locator('.sidebar .thread-row.active .thread-item').click();
          await page.waitForFunction(() => !document.documentElement.classList.contains('list-open'));
        };
        const open = async () => { await closeList(); await page.locator('.topbar-more').click(); await root.waitFor(); };
        const openList = async () => {
          if (mobile && !(await page.evaluate(() => document.documentElement.classList.contains('list-open')))) await page.locator('.menu-btn').click();
          // The open thread's row, by state: the rename below changes the title mid-test.
          await page.locator('.sidebar .thread-row.active').click({ button: 'right' });
          await root.waitFor();
        };
        await open();
        await root.locator('.quota-preview[aria-busy=false]').waitFor();
        assert.deepEqual(await root.locator(':scope > .menu-cell .menu-label').allTextContents(),
          mobile ? ['文件', '打开终端', '置顶', '标记为未读', '重命名', '复制', '分叉与交接', '归档', '永久删除'] : ['文件', '打开终端', '置顶', '标记为未读', '重命名', '复制', '分叉与交接', '轨迹', '归档', '永久删除']);
        // Thread items now sit at the first level instead of behind "整理", so the page menu grew
        // by a row; it still fits a phone screen without scrolling.
        const pageMenu = await root.boundingBox();
        assert.ok(pageMenu.height <= (mobile ? 565 : 515), 'the page menu stays within the accepted footprint');
        assert.ok(pageMenu.y + pageMenu.height <= page.viewportSize().height, 'the whole page menu is on screen');
        assert.equal(await root.locator('.menu-row').first().evaluate(el => el.offsetHeight), mobile ? 44 : 36);
        assert.equal(await root.getByRole('meter').count(), 2, 'only the main pair occupies the root menu');
        assert.ok((await root.locator('[role=status]').innerText()).includes('2 项偏低'), 'warnings include hidden pools');
        assert.ok((await root.locator('[role=status]').innerText()).includes('模型繁忙'), 'busy models remain visible');
        const headline = await root.locator('.quota-menu-open strong').boundingBox(), account = await root.locator('.quota-menu-open > span').boundingBox();
        assert.ok(headline.x + headline.width <= account.x + 1, 'warnings and the account label do not overlap');
        if (shots) await page.screenshot({ path: join(shots, `menu-root-${width}-${theme}.png`) });

        await openList();
        assert.deepEqual(await root.locator(':scope > .menu-cell .menu-label').allTextContents(), ['置顶', '标记为未读', '重命名', '移到分区', '关联到计划', '归档', '永久删除']);
        assert.equal(await root.getByRole('meter').count(), 0, 'a row menu carries no quota header');
        if (shots) await page.screenshot({ path: join(shots, `menu-list-${width}-${theme}.png`) });
        await page.keyboard.press('Escape'); await root.waitFor({ state: 'detached' });

        // The child has its own keyboard scope. Escape returns one level and restores the trigger.
        await open();
        await root.getByRole('menuitem', named('复制')).focus();
        await page.keyboard.press('ArrowRight');
        await page.waitForFunction(() => document.activeElement?.classList.contains('menu-back'));
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.activeElement?.dataset.menuId === 'copy');
        assert.equal(await root.getByRole('menuitem', named('复制')).evaluate(el => el === document.activeElement), true);
        await page.keyboard.press('Escape'); await root.waitFor({ state: 'detached' });

        await openList();
        await root.getByRole('menuitem', named('移到分区')).focus();
        await page.keyboard.press('ArrowRight');
        await branch.getByRole('menuitemradio', named(section.name)).waitFor();
        const bounds = await branch.boundingBox();
        assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1);
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.activeElement?.dataset.menuId === 'section');
        assert.equal(await root.getByRole('menuitem', named('移到分区')).evaluate(el => el === document.activeElement), true);
        await page.keyboard.press('Escape'); await root.waitFor({ state: 'detached' });

        await open();
        await root.getByRole('menuitem', named('重命名')).click();
        const dialog = page.getByRole('dialog', named('重命名线程'));
        await dialog.getByLabel('线程名称').fill(thread.title + ' renamed');
        await dialog.getByRole('button', named('保存')).click();
        await page.locator('.topbar-title', { hasText: thread.title + ' renamed' }).waitFor();

        await openList();
        const pinned = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().endsWith('/api/threads/' + thread.id));
        await root.getByRole('menuitem', named('置顶')).click(); await pinned;
        assert.equal((await (await api(daemon, '/api/threads/' + thread.id)).json()).pinned, true);
        await openList();
        await root.getByRole('menuitem', named('移到分区')).click();
        const filed = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().endsWith('/api/threads/' + thread.id));
        await branch.getByRole('menuitemradio', named(section.name)).click(); await filed;
        assert.equal((await (await api(daemon, '/api/threads/' + thread.id)).json()).sectionId, section.id);
        await openList();
        await root.getByRole('menuitem', named('关联到计划')).click();
        const linked = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/todos/' + todo.id + '/threads'));
        await branch.getByRole('menuitemradio', named(todo.text)).click(); await linked;

        await open();
        await root.getByRole('menuitem', named('复制')).click();
        await branch.getByRole('menuitem', named('工作目录')).waitFor();
        assert.equal(await branch.getByRole('menuitem').count(), 5, 'back plus every original copy action');
        await page.waitForFunction(() => document.activeElement?.classList.contains('menu-back'));
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.activeElement?.dataset.menuId === 'copy');
        await root.getByRole('menuitem', named('分叉与交接')).click();
        await branch.getByRole('menuitem', named('分叉')).waitFor();
        await branch.getByRole('menuitem', named('交接')).waitFor();
        await page.waitForFunction(() => document.activeElement?.classList.contains('menu-back'));
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => document.activeElement?.dataset.menuId === 'continuation');
        await page.keyboard.press('Escape');
        await root.waitFor({ state: 'detached' });

        await openList();
        await root.getByRole('menuitem', named('永久删除')).click();
        const removal = page.getByRole('dialog'); await removal.waitFor();
        await removal.getByRole('button', named('取消')).click();
        assert.equal(await page.locator('.composer textarea').inputValue(), 'Draft survives nested menus');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('long quota alerts fit the compact header across languages, including cached failures', { timeout: 90000 }, async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', label: 'A long account name', command }] }) });
  const browser = await launchBrowser();
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', cwd: daemon.workdir, title: 'Quota alert wrapping' }) })).json();
    for (const locale of ['zh', 'en', 'ja']) for (const width of [320, 1440]) {
      const mobile = width < 900, context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: mobile, hasTouch: mobile });
      try {
        await context.addInitScript(locale => localStorage.setItem('calli.locale', locale), locale);
        const page = await context.newPage(); let failed = false;
        await page.route('**/api/agents/*/quota*', route => failed ? route.fulfill({ status: 503, json: { error: 'Offline for test' } }) : route.fulfill({ json: { checkedAt: new Date().toISOString(), plan: { id: 'pro', label: 'Pro' }, windows: [
          { label: '5H', usedPercent: 90 }, { label: '7D', usedPercent: 80, loadPercent: 100 },
        ] } }));
        // A fresh authenticated context lets the selected locale load before the app starts.
        await context.addInitScript(token => localStorage.setItem('calli.token', token), daemon.token);
        await page.goto(daemon.base + '/#/thread/' + thread.id);
        await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
        await page.locator('.topbar-more').click();
        const quick = page.locator('.quota-menu-preview');
        await quick.locator('.quota-preview[aria-busy=false]').waitFor();
        const root = page.locator('.menu-float'), before = await root.boundingBox();
        const fits = async () => {
          const heading = await quick.locator('.quota-menu-head').boundingBox(), title = await quick.locator('.quota-menu-open strong').boundingBox(), account = await quick.locator('.quota-menu-open > span').boundingBox();
          assert.ok(title.y >= heading.y - 1 && title.y + title.height <= heading.y + heading.height + 1, `${locale}: alert stays in the fixed header`);
          assert.ok(title.x + title.width <= account.x + 1, `${locale}: alert does not overlap the account`);
          assert.equal(await root.evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
        };
        await fits();
        failed = true; await quick.locator('.quota-menu-head > .icon-btn').click();
        await page.waitForFunction(marker => document.querySelector('.quota-menu-head').textContent.includes(marker), ({zh:'旧值',en:'Cached',ja:'前回'})[locale]);
        await fits();
        const after = await root.boundingBox();
        assert.equal(after.height, before.height, 'an important refresh failure uses the same header space');
        const shots = process.env.CALLI_E2E_SHOTS_DIR;
        if (shots) await page.screenshot({ path: join(shots, `menu-cached-${locale}-${width}.png`) });
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
