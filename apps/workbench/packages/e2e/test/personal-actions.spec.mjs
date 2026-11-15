import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
};
const plans = async page => { await page.evaluate(() => { location.hash = '#/todos'; }); await page.locator('.todos-page').waitFor(); };

test('delegation destinations, searchable mobile models, compact empty notes and backend update errors work in Chromium', async () => {
  const daemon = await startDaemon({ FAKE_PI_MANY_MODELS: '1' });
  const browser = await launchBrowser();
  const shots = resolve(import.meta.dirname, '../shots/personal'); mkdirSync(shots, { recursive: true });
  try {
    const conversation = await json(daemon, '/api/threads', 'POST', { agent: 'pi', cwd: daemon.workdir, title: '讨论发布安排' });
    const first = await json(daemon, '/api/todos', 'POST', { text: '核对周末安排' });
    const second = await json(daemon, '/api/todos', 'POST', { text: '整理阅读清单' });
    await json(daemon, `/api/todos/${second.id}/note`, 'PUT', { text: '' });
    for (let i = 0; i < 6; i++) {
      const cwd = join(daemon.workdir, `project-${i}`); mkdirSync(cwd);
      await json(daemon, '/api/threads', 'POST', { agent: 'pi', cwd, title: `项目 ${i}` });
    }
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 }, colorScheme: theme, isMobile: mobile, hasTouch: mobile });
      const page = await context.newPage();
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      const suffix = `${mobile ? 'mobile' : 'desktop'}-${theme}`;
      await openUi(page, daemon); await plans(page);
      const row = id => page.locator(`.todo[data-id="${id}"]`);
      const expand = async id => { await row(id).locator('.todo-line').click({ position: { x: 200, y: 1 } }); await row(id).getByText('还没有说明', { exact: false }).waitFor(); };
      await expand(first.id); await expand(second.id);
      assert.equal(await page.locator('.todo-details').count(), mobile ? 1 : 2);
      assert.equal(await row(second.id).locator('.todo-details p').count(), 0);
      assert.equal(await row(second.id).locator('.todo-details-toggle').count(), 0, 'cleared notes have no document icon');
      assert.match(await row(second.id).locator('.todo-details').innerText(), /^还没有说明\s*·\s*设跟进$/);
      await page.screenshot({ animations: 'disabled', path: join(shots, `empty-note-${suffix}.png`) });
      await row(second.id).getByRole('button', { name: '交给 agent', exact: true }).click();
      await page.getByRole('menuitem', { name: '新开私人对话', exact: true }).waitFor();
      await page.screenshot({ animations: 'disabled', path: join(shots, `delegate-menu-${suffix}.png`) });
      await page.getByRole('menuitem', { name: '在项目里开…', exact: true }).click();
      await page.getByRole('menuitem', { name: '选择目录…', exact: true }).waitFor();
      assert.equal(await page.getByRole('menuitem').count(), 7, 'five recent folders, back and browse');
      await page.keyboard.press('Escape');
      await page.getByLabel('新计划', { exact: true }).fill(`确认日程 ${suffix}`);
      await page.locator('.todo-delegate-new').click();
      await page.getByRole('menuitem', { name: '关联已有对话…', exact: true }).click();
      const picker = page.getByRole('dialog', { name: '关联已有对话', exact: true });
      await picker.getByLabel('搜索线程', { exact: true }).fill('发布');
      await page.screenshot({ animations: 'disabled', path: join(shots, `link-existing-${suffix}.png`) });
      await picker.getByRole('button', { name: '讨论发布安排', exact: true }).click();
      await picker.waitFor({ state: 'detached' });
      const todos = await json(daemon, '/api/todos');
      const linked = todos.find(todo => todo.text === `确认日程 ${suffix}`);
      assert.deepEqual(linked.threads.map(thread => thread.id), [conversation.id]);
      assert.equal((await json(daemon, `/api/threads/${conversation.id}/events`)).filter(event => event.type === 'turn.started').length, 0);
      await json(daemon, `/api/todos/${linked.id}`, 'DELETE');
      // Opening the same model picker works in a bottom drawer and a desktop dialog.
      await page.locator('.personal-summary').click();
      const settings = page.getByRole('region', { name: '私人设置', exact: true });
      await settings.locator('.personal-setting-line').nth(2).getByRole('button').click();
      const models = page.getByRole('menu', { name: '模型', exact: true });
      await models.getByLabel('筛选模型').waitFor();
      assert.equal(await models.getByRole('menuitemradio').count(), 44);
      const scroll = await models.locator('.pop-scroll').evaluate(el => ({ client: el.clientHeight, scroll: el.scrollHeight }));
      assert.ok(scroll.scroll > scroll.client);
      await page.screenshot({ animations: 'disabled', path: join(shots, `models-long-${suffix}.png`) });
      await models.getByLabel('筛选模型').fill('Model 40');
      await models.getByRole('menuitemradio', { name: 'Model 40', exact: true }).click();
      await settings.getByRole('button', { name: '保存', exact: true }).click();
      await settings.waitFor({ state: 'detached' });
      assert.equal((await json(daemon, '/api/personal/settings')).model, 'fake/model-40');
      assert.deepEqual(errors, []);
      await context.close();
    }
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await openUi(page, daemon); await plans(page);
    // A project directory chosen in the shared picker creates a normal project conversation.
    await page.getByLabel('新计划', { exact: true }).fill('检查项目配置');
    await page.locator('.todo-delegate-new').click();
    await page.getByRole('menuitem', { name: '在项目里开…', exact: true }).click();
    await page.getByRole('menuitem', { name: '选择目录…', exact: true }).click();
    await page.getByRole('button', { name: '用这个目录', exact: true }).click();
    await page.locator('.todos-page').waitFor({ state: 'detached' });
    const projectTodo = (await json(daemon, '/api/todos')).find(todo => todo.text === '检查项目配置');
    assert.equal(projectTodo.threads.length, 1); assert.equal(projectTodo.threads[0].cwd, daemon.workdir);
    await plans(page);
    // Failed and slow model loads remain explicit, and a retry can recover.
    let loads = 0;
    await page.route('**/api/agents/pi/models', async route => {
      loads++;
      if (loads === 1) { await new Promise(r => setTimeout(r, 600)); await route.fulfill({ status: 503, json: { error: 'fixture model probe failed' } }); }
      else await route.continue();
    });
    await page.locator('.personal-summary').click();
    let settings = page.getByRole('region', { name: '私人设置' });
    await settings.locator('.personal-setting-line').nth(2).getByRole('button').click();
    await page.getByRole('menu', { name: '模型', exact: true }).getByRole('status').waitFor();
    await page.getByRole('menu', { name: '模型', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
    await page.getByLabel('筛选模型').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
    await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
    await page.route('**/api/push/settings', route => route.fulfill({ status: 404, json: { error: 'not found' } }));
    await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
    await page.getByRole('button', { name: '配置', exact: true }).click();
    await page.locator('.settings-nav').getByRole('button', { name: '推送', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '后台还没更新到这个版本，重启 hub 后再试' }).waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});
