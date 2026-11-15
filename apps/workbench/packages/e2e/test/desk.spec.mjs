import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
};
const openDesk = async page => {
  // The desk is the drawer's own tab on the assistant's home; its address opens it directly.
  await page.evaluate(() => { location.hash = '#/desk/desk'; });
  await page.locator('.desk-matrix').waitFor();
  await page.getByRole('button', { name: '按进度', exact: true }).click();
  await page.locator('.desk-lanes').waitFor();
};

test('the desk loads notes on demand, saves lanes, and returns keyboard focus after a checklist request', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const todo = await json(daemon, '/api/todos', 'POST', { text: '整理实验结论', lane: 'you' });
    const child = await json(daemon, '/api/todos', 'POST', { text: '补齐对照组', parentId: todo.id });
    await json(daemon, `/api/todos/${todo.id}/note`, 'PUT', { text: '## 做到哪\n实验已经跑完。\n## 下一步\n写成一页纸。' });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    let noteRequests = 0;
    page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/note')) noteRequests++; });
    await openUi(page, daemon);
    await openDesk(page);
    assert.equal(noteRequests, 0, 'covers use the short preview without requesting notes');
    const pile = page.locator(`#desk-pile-${todo.id}`);
    await pile.click();
    await page.locator('.desk-note-section.next').waitFor();
    assert.equal(noteRequests, 1);
    assert.equal(await page.locator('.desk-note-section h3').first().textContent(), '下一步');
    await page.getByRole('checkbox', { name: child.text }).click();
    await page.locator('.desk-checklist [aria-checked="true"]').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    assert.equal(await page.evaluate(() => document.activeElement.id), `desk-pile-${todo.id}`);
    assert.equal((await json(daemon, '/api/todos')).find(item => item.id === child.id).done, true);
    await pile.click();
    await page.locator('.desk-lane-picker').click();
    await page.getByRole('menuitemradio', { name: '等别人', exact: true }).click();
    await page.locator('.desk-lane-picker', { hasText: '等别人' }).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    assert.equal((await json(daemon, '/api/todos')).find(item => item.id === todo.id).lane, 'wait');
    await page.getByRole('button', { name: '按时间', exact: true }).click();
    await page.reload();
    await page.locator('.desk-lanes.time').waitFor();
    assert.equal(new URL(page.url()).hash, '#/desk/desk');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('the phone desk exposes proposal decisions, chart details and delegate destinations', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    for (const text of ['周五前交报销', '确认合同修改']) await json(daemon, '/api/todos', 'POST', { text });
    const settings = await json(daemon, '/api/personal/settings');
    const thread = await json(daemon, '/api/threads', 'POST', { agent: 'pi', cwd: settings.directory });
    await json(daemon, `/api/threads/${thread.id}/turns`, 'POST', { text: 'personal-ui' });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
    await openUi(page, daemon); await openDesk(page);
    await page.locator('#desk-stack-inbox').waitFor();
    await page.locator('.desk-chart-day').first().tap();
    await page.locator('.desk-chart-tip').waitFor();
    await page.locator('#desk-stack-inbox').click();
    const accept = page.locator('.desk-card', { hasText: '预约护照续签' });
    const dismiss = page.locator('.desk-card', { hasText: '查收快递' });
    await accept.getByRole('button', { name: '跟踪', exact: true }).click();
    await accept.waitFor({ state: 'detached' });
    await dismiss.getByRole('button', { name: '不是我的事', exact: true }).click();
    await page.getByText('新发现都看过了。', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    const todos = await json(daemon, '/api/todos');
    assert.equal(todos.find(todo => todo.text === '预约护照续签').proposed, false);
    assert.ok(!todos.some(todo => todo.text === '查收快递'));
    const todo = todos.find(todo => todo.text === '周五前交报销');
    await page.locator(`#desk-pile-${todo.id}`).click();
    await page.getByRole('button', { name: '交给 agent', exact: true }).click();
    await page.getByRole('menuitem', { name: '新开私人对话', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.desk-spread').count(), 1, 'Escape only closes the nested menu');
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  } finally { await browser.close(); await daemon.stop(); }
});

test('by weight is the default view: a matter moves as it is marked and pinned, and shows where it came from', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const source = { type: 'lark-message', ref: 'm1', url: 'https://example.test/m1', from: '老板' };
    const todo = await json(daemon, '/api/todos', 'POST', { text: '回复老板的问题', source });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    await page.locator('#desk-stack-later').click();
    await page.locator(`#desk-pile-${todo.id}`).click();
    await page.locator('.desk-source-card a[href="https://example.test/m1"]').waitFor();
    await page.locator('.desk-important').click();
    await page.locator('.desk-important[aria-pressed="true"]').waitFor();
    await page.getByRole('button', { name: '钉为紧急' }).click();
    await page.getByRole('button', { name: '到今天', exact: true }).click();
    await page.locator('.desk-quiet.on', { hasText: '钉为紧急' }).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });
    await page.locator(`.desk-quad.now #desk-pile-${todo.id}`).waitFor();
    assert.match(await page.locator(`#desk-pile-${todo.id} .desk-cover-meta`).textContent(), /你钉的/);
    assert.equal(await page.locator('.desk-first li').count(), 1);
    const saved = (await json(daemon, '/api/todos')).find(item => item.id === todo.id);
    assert.equal(saved.importance, 'high');
    assert.equal(saved.urgency.reason, 'pinned');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('private settings: Cancel keeps Lark and brief changes out, Save puts them in', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const before = await json(daemon, '/api/personal/channels/lark');
    await json(daemon, '/api/personal/channels/lark', 'PUT', { ...before, enabled: true });
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    const open = async () => { await page.locator('.desk-lark button').click(); await page.locator('.brief-settings').waitFor(); };
    await open();
    const brief = page.getByRole('switch', { name: '早晚报' });
    await brief.click();
    await page.locator('.brief-times').waitFor();
    assert.equal((await json(daemon, '/api/personal/brief/settings')).enabled, false, 'nothing is saved before Save');
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.locator('.personal-settings').waitFor({ state: 'detached' });
    assert.equal((await json(daemon, '/api/personal/brief/settings')).enabled, false);
    await open();
    await brief.click();
    await page.getByRole('switch', { name: '飞书' }).click();
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.locator('.personal-settings').waitFor({ state: 'detached' });
    assert.equal((await json(daemon, '/api/personal/brief/settings')).enabled, true);
    assert.equal((await json(daemon, '/api/personal/channels/lark')).enabled, false);
    await page.locator('.desk-lark').waitFor({ state: 'detached' });
  } finally { await browser.close(); await daemon.stop(); }
});

test('what I care about: written by hand from the private settings, and a change elsewhere never loses the words', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const lark = await json(daemon, '/api/personal/channels/lark');
    await json(daemon, '/api/personal/channels/lark', 'PUT', { ...lark, enabled: true });
    const first = await json(daemon, '/api/personal/profile');
    assert.equal(first.isTemplate, true);
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk/desk'; });
    await page.locator('.desk-lark button').click();
    await page.locator('.profile-open', { hasText: '写下' }).click();
    const field = page.locator('.profile-field');
    await field.waitFor();
    assert.match(await field.inputValue(), /## 我在意什么/);
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await page.locator('.profile-field').waitFor({ state: 'detached' });
    await page.locator('.profile-open').click();
    await field.fill(`${first.text}\n在意按时交付`);
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '继续写', exact: true }).click();
    assert.match(await field.inputValue(), /按时交付/);
    await json(daemon, '/api/personal/profile', 'PUT', { text: `${first.text}\n别处改的`, rev: first.rev });
    await page.locator('.profile-dialog .primary').click();
    await page.getByRole('button', { name: '仍然保存我写的', exact: true }).waitFor();
    assert.match(await field.inputValue(), /按时交付/, 'what was written stays through the conflict');
    await page.getByRole('button', { name: '仍然保存我写的', exact: true }).click();
    await page.locator('.profile-field').waitFor({ state: 'detached' });
    const saved = await json(daemon, '/api/personal/profile');
    assert.match(saved.text, /按时交付/);
    assert.equal(saved.isTemplate, false);
    await page.locator('.profile-open', { hasText: '编辑' }).waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});
