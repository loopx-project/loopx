import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
const shots = '/tmp/calli-team-shots';
before(async () => {
  daemon = await startDaemon();
  mkdirSync(shots, { recursive: true });
  writeFileSync(`${shots}/runtime.json`, JSON.stringify({ pid: daemon.pid, base: daemon.base, dataDir: daemon.dataDir }));
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const phone of [false, true]) for (const theme of ['light', 'dark']) test(`teams: ${phone ? 'phone' : 'desktop'} ${theme} editor, recipient and confirmation`, async () => {
  const label = `${phone ? 'phone' : 'desktop'}-${theme}`;
  const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, colorScheme: theme });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  try {
    await page.addInitScript(theme => localStorage.setItem('calli.appearance', JSON.stringify({ mode: theme, palette: 'paper' })), theme);
    await openUi(page, daemon);
    if (!phone) {
      await page.getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-nav').getByRole('button', { name: '团队', exact: true }).click();
    } else await page.goto(`${daemon.base}/#/teams`);
    await page.locator('.teams-page').waitFor();
    await page.getByRole('button', { name: '从编码队模板创建', exact: true }).click();
    await page.getByLabel('团队名称', { exact: true }).fill(`Coding ${label}`);
    const roles = page.locator('.team-member-editor');
    assert.equal(await roles.count(), 3);
    for (const role of await roles.all()) {
      await role.getByRole('button', { name: '实例', exact: true }).click();
      await page.getByRole('menuitemradio', { name: 'pi', exact: true }).click();
    }
    await roles.first().getByRole('button', { name: '模型', exact: true }).click();
    await page.getByRole('menu', { name: '模型', exact: true }).getByRole('button', { name: '默认', exact: true }).click();
    await page.screenshot({ path: `${shots}/${label}-editor-detail.png`, fullPage: true, animations: 'disabled' });
    await page.locator('.settings-detail, .scroller').evaluate(el => el.scrollTop = 0);
    await page.screenshot({ path: `${shots}/${label}-editor.png`, fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '保存团队', exact: true }).click();
    await page.locator('.team-definition', { hasText: `Coding ${label}` }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.teams-actions button')].every(el => !el.disabled));
    await page.locator('.settings-detail, .scroller').evaluate(el => el.scrollTop = 0);
    await page.screenshot({ path: `${shots}/${label}-list.png`, fullPage: true, animations: 'disabled' });
    assert.equal(await page.locator('.teams-page').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    await page.goto(daemon.base);
    await page.locator('.agent-btn').waitFor();
    await page.locator('.agent-btn').click();
    await page.getByRole('menuitemradio', { name: `Coding ${label}`, exact: true }).waitFor();
    await page.screenshot({ path: `${shots}/${label}-picker.png`, fullPage: true, animations: 'disabled' });
    await page.getByRole('menuitemradio', { name: `Coding ${label}`, exact: true }).click();
    assert.equal(await page.locator('.team-text-only').count(), 0);
    await page.locator('.composer textarea').fill('TEAM_FLOW');
    await page.locator('.send-btn').click();
    await page.getByRole('button', { name: '确认完成', exact: true }).waitFor({ timeout: 15000 });
    assert.match(await page.locator('.team-task-status').textContent(), /待确认/);
    assert.equal(await page.locator('.team-task-notice').textContent(), '确认完成');
    assert.equal(await page.locator('body').evaluate(el => el.scrollWidth <= window.innerWidth + 1), true);
    await page.screenshot({ path: `${shots}/${label}-task.png`, fullPage: true, animations: 'disabled' });
    await page.getByRole('button', { name: '确认完成', exact: true }).click();
    await page.locator('.team-task-status', { hasText: '已完成' }).waitFor();
    await page.locator('.composer textarea').fill('TEAM_MEMBER_ERROR');
    await page.locator('.send-btn').click();
    await page.locator('.team-task-notice', { hasText: 'provider exploded' }).waitFor();
    assert.equal(await page.locator('.team-task-notice').textContent(), 'provider exploded');
    await page.screenshot({ path: `${shots}/${label}-attention.png`, fullPage: true, animations: 'disabled' });
    await page.locator('.composer textarea').fill('TEAM_FINISH');
    await page.locator('.send-btn').click();
    await page.getByRole('button', { name: '确认完成', exact: true }).waitFor();
    assert.equal(await page.locator('.team-task-notice').textContent(), '确认完成');
    assert.deepEqual(errors, []);
    const teams = await (await api(daemon, '/api/teams')).json();
    const team = teams.find(t => t.name === `Coding ${label}`);
    assert.ok(team);
  } finally { await context.close(); }
});


test('teams: edit, export, import validation and delete remain usable', async () => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  try {
    await openUi(page, daemon); await page.goto(`${daemon.base}/#/teams`);
    const card = page.locator('.team-definition').first();
    await card.getByRole('button', { name: '复制 JSON', exact: true }).click();
    await card.getByTitle('已复制', { exact: true }).waitFor();
    await card.getByRole('button', { name: '编辑', exact: true }).click();
    await page.getByLabel('团队说明', { exact: true }).fill('Updated description');
    await page.getByRole('button', { name: '保存团队', exact: true }).click();
    await page.locator('.team-definition', { hasText: 'Updated description' }).waitFor();
    const teams = await (await api(daemon, '/api/teams')).json();
    const exported = await (await api(daemon, `/api/teams/${teams[0].id}/export`)).json();
    await page.getByRole('button', { name: '导入 JSON', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '导入 JSON' });
    await dialog.getByLabel('粘贴团队 JSON').fill('{}');
    await dialog.getByRole('button', { name: '导入 JSON', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    await dialog.getByLabel('粘贴团队 JSON').fill(JSON.stringify({ ...exported, name: 'Imported team' }));
    await dialog.getByRole('button', { name: '导入 JSON', exact: true }).click();
    const imported = page.locator('.team-definition', { hasText: 'Imported team' }); await imported.waitFor();
    await imported.getByRole('button', { name: '删除团队', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '删除团队', exact: true }).click();
    await imported.waitFor({ state: 'detached' });
  } finally { await page.close(); }
});
