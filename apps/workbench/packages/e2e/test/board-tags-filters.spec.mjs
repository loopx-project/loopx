import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR ?? '/tmp/calli-board-collaboration-shots';
async function json(path, body) {
  const res = await api(daemon, path, body ? { method: 'POST', body: JSON.stringify(body) } : undefined);
  assert.ok(res.ok, await res.clone().text());
  return res.json();
}
before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
  const board = (await json('/api/extensions')).find(e => e.manifest.id === 'board');
  await json('/api/extensions/board/install', { granted: board.manifest.permissions });
  for (const [title, agent] of [['[Flux][发布][周报] 推动新发布', 'pi'], ['【flux】 每周回顾', 'claude'], ['[周报] 看看最新的看板', 'pi'], ['标题 [object Object]', 'pi'], ...Array.from({ length: 24 }, (_, i) => [`[tag-${String(i).padStart(2, '0')}] 示例 ${i}`, 'pi'])]) {
    await json('/api/threads', { title, agent, cwd: daemon.workdir });
  }
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`board tags and filter popovers: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    try {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/board');
      if (phone) await page.addStyleTag({ content: '.pop-panel { --safe-bottom: 34px; }' });
      await page.locator('.ext-board-card-title', { hasText: '推动新发布' }).waitFor();
      const first = page.locator('.ext-board-card').filter({ has: page.getByText('推动新发布', { exact: true }) });
      assert.equal(await first.locator('.ext-board-card-title').textContent(), '推动新发布');
      assert.deepEqual(await first.locator('.ext-board-tag').allTextContents(), ['Flux', '发布', '+1']);
      assert.equal(await page.locator('.ext-board-card-title', { hasText: '标题 [object Object]' }).count(), 1);
      const filters = page.locator('.ext-board-filters');
      const open = async name => {
        await filters.getByRole('button', { name: new RegExp(`^${name}`) }).click();
        const menu = page.getByRole('menu', { name, exact: true });
        await menu.waitFor();
        await page.waitForTimeout(300);
        const rect = await menu.boundingBox();
        assert.ok(rect.x >= 0 && rect.x + rect.width <= page.viewportSize().width + 1);
        assert.ok(rect.y >= 0 && rect.y + rect.height <= page.viewportSize().height + 1, JSON.stringify(rect));
        if (!phone) {
          const trigger = await filters.getByRole('button', { name: new RegExp(`^${name}`) }).boundingBox();
          assert.ok(rect.y >= trigger.y + trigger.height, 'opens below the trigger');
        }
        return menu;
      };
      const dismiss = async () => {
        if (phone) await page.locator('.topbar').click({ position: { x: 150, y: 3 } });
        else await page.keyboard.press('Escape');
        await page.getByRole('menu').waitFor({ state: 'hidden' });
      };
      let menu = await open('标签');
      assert.ok(await menu.evaluate(el => el.scrollHeight > el.clientHeight), 'many tags scroll inside the panel');
      assert.equal(await menu.getByRole('menuitemcheckbox', { name: '全部', exact: true }).getAttribute('aria-checked'), 'true');
      await menu.getByRole('menuitemcheckbox', { name: 'Flux 2', exact: true }).click();
      await menu.getByRole('menuitemcheckbox', { name: '周报 2', exact: true }).click();
      const checked = menu.getByRole('menuitemcheckbox', { checked: true });
      assert.equal(await checked.count(), 2);
      assert.equal(await checked.locator('.pop-item-check svg').count(), 2);
      assert.equal(await page.locator('.ext-board-card').count(), 3, 'tags are OR');
      await page.screenshot({ path: join(shots, `tags-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      await dismiss();
      await page.screenshot({ path: join(shots, `tagged-cards-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      menu = await open('实例');
      await menu.getByRole('menuitemcheckbox', { name: 'Claude Code', exact: true }).click();
      assert.equal(await page.locator('.ext-board-card').count(), 1, 'different filters are AND');
      await menu.getByRole('menuitemcheckbox', { name: '全部', exact: true }).click();
      assert.equal(await page.locator('.ext-board-card').count(), 3);
      await dismiss();
      await page.reload();
      if (phone) await page.addStyleTag({ content: '.pop-panel { --safe-bottom: 34px; }' });
      await page.locator('.ext-board-card').first().waitFor();
      assert.equal(await page.locator('.ext-board-card').count(), 3, 'filters survive reload');
      menu = await open('项目');
      const project = menu.getByRole('menuitemcheckbox').nth(1);
      await project.click();
      assert.equal(await project.getAttribute('aria-checked'), 'true');
      await menu.getByRole('menuitemcheckbox', { name: '全部', exact: true }).click();
      await dismiss();
      menu = await open('是否定时');
      await menu.getByRole('menuitemcheckbox', { name: '定时', exact: true }).click();
      await menu.getByRole('menuitemcheckbox', { name: '非定时', exact: true }).click();
      assert.equal(await menu.getByRole('menuitemcheckbox', { checked: true }).count(), 2);
      await dismiss();
      if (phone) assert.ok(await filters.evaluate(el => el.scrollWidth > el.clientWidth), 'filters scroll on one line');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await filters.getByRole('button', { name: '清除筛选', exact: true }).click();
      await page.screenshot({ path: join(shots, `cards-${phone ? 'phone' : 'desktop'}-${colorScheme}.png`) });
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
