// The proactive surfaces against a scripted API: every state of a run, including the ones a real
// run takes luck to reach. proactive-flow.spec.mjs walks the same surfaces through the real backend.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, launchBrowser, openUi } from '../harness.mjs';
import { arrangeProactive, routes } from './proactive-fixture.mjs';

const openDesk = async page => {
  // The desk is the drawer's own tab on the assistant's home; its address opens it directly.
  await page.evaluate(() => { location.hash = '#/desk/desk'; });
  await page.locator('.desk-matrix').waitFor();
  await page.getByRole('button', { name: '按进度', exact: true }).click();
  await page.locator('.desk-lanes').waitFor();
};

test('a sandbox card moves through merge and revert in place, and a failed action can be retried', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const { sandboxTodo, prepareTodo, runs, requests, failNext } = await arrangeProactive(daemon);
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await routes(page, { runs, requests, failNext });
    await openUi(page, daemon); await openDesk(page);
    assert.deepEqual(requests.searches, [''], 'the conversation reads the runs once; covers read nothing');

    await page.locator(`#desk-pile-${sandboxTodo.id}`).click();
    const card = page.locator('.proactive-card[data-run="sandbox-1"]');
    await card.getByText('已在沙盒做好').waitFor();
    assert.deepEqual(requests.searches, ['', `?matterId=${sandboxTodo.id}`], 'a spread reads only its own matter');
    await card.getByText('补好导出的边界测试').waitFor();
    await card.getByText('npm test: 12 passed').waitFor();
    await card.getByText('2 个文件').waitFor();
    assert.equal(requests.diffs, 0, 'the diff loads only when asked for');

    await card.getByRole('button', { name: '看改动', exact: true }).click();
    const diff = page.locator('.proactive-diff');
    await diff.getByText('src/export.ts').waitFor();
    assert.match(await diff.locator('li').first().textContent(), /\+10-2/);
    assert.equal(requests.diffs, 1);
    await diff.getByRole('button', { name: '关闭', exact: true }).click();

    failNext.merge = '工作区不干净，没有合入';
    await card.getByRole('button', { name: '合入', exact: true }).click();
    await card.getByRole('alert').getByText('工作区不干净，没有合入').waitFor();
    await card.getByRole('button', { name: '合入', exact: true }).click();
    await card.getByText('已合入').waitFor();
    assert.equal(await card.getByRole('alert').count(), 0, 'a retry clears the failure');
    assert.equal(await card.getByRole('button', { name: '丢弃', exact: true }).count(), 0);
    await card.getByRole('button', { name: '撤回', exact: true }).click();
    await card.getByText('已撤回').waitFor();
    assert.equal(await card.getByRole('button').count(), 0);
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });

    await page.locator(`#desk-pile-${prepareTodo.id}`).click();
    const material = page.locator('.proactive-card.prepared');
    await material.getByText('已备好').waitFor();
    await material.getByText('outline.md').waitFor();
    assert.equal(await material.getByRole('button').count(), 0, 'materials ask for no decision');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('discarding asks first; a failed run says why; the filter lists matters and keeps revert in reach', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const { runs, requests, failNext, failedTodo } = await arrangeProactive(daemon);
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
    await routes(page, { runs, requests, failNext });
    await openUi(page, daemon); await openDesk(page);

    await page.locator(`#desk-pile-${failedTodo.id}`).click();
    const failed = page.locator('.proactive-card[data-run="failed-1"]');
    await failed.getByText('没做成').waitFor();
    await failed.getByText('测试没跑起来').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state: 'detached' });

    const filter = page.locator('.desk-filter');
    assert.match(await filter.textContent(), /助手主动做的\s*4/);
    await filter.click();
    assert.equal(await filter.getAttribute('aria-pressed'), 'true');
    const list = page.locator('.proactive-list');
    await list.locator('[data-matter]').first().waitFor();
    assert.equal(requests.searches.at(-1), '', 'the filter reads every run');
    assert.equal(await list.locator('[data-matter]').count(), 4);
    const merged = list.locator('li', { hasText: '已经合入的事' });
    await merged.getByRole('button', { name: '撤回', exact: true }).click();
    await merged.getByText('已撤回').waitFor();

    const ready = list.locator('li', { hasText: '修导出边界' });
    await ready.getByRole('button', { name: '丢弃', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: '丢弃这次改动？' });
    await confirm.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(requests.actions.includes('sandbox-1/discard'), false, 'cancelling discards nothing');
    await ready.getByRole('button', { name: '丢弃', exact: true }).click();
    await confirm.getByRole('button', { name: '丢弃', exact: true }).click();
    await ready.waitFor({ state: 'detached' });
    assert.equal(requests.actions.includes('sandbox-1/discard'), true);

    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator('.desk-lanes').waitFor();
    assert.equal(await page.locator('.proactive-list').count(), 0);
  } finally { await browser.close(); await daemon.stop(); }
});

test('the settings section stays one line until turned on, and a rejected repository is visible', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await arrangeProactive(daemon);
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const saved = [];
    let reject = '目录不是可用的 git 仓库：/not/a/repo';
    await page.route('**/api/personal/proactive?summary=true', route => route.fulfill({ json: { settings: { mode: 'off', enabled: false, repos: ['/not/a/repo'] }, counts: {} } }));
    await page.route('**/api/personal/proactive/settings', async route => {
      saved.push(route.request().postDataJSON());
      if (reject) { const error = reject; reject = ''; return route.fulfill({ status: 400, json: { error } }); }
      return route.fulfill({ json: saved.at(-1) });
    });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/todos'; });
    await page.locator('.personal-summary').click();
    await page.getByRole('button', { name: /主动行动/ }).click();
    const mode = name => page.locator('.proactive-mode').getByRole('button', { name, exact: true });
    await mode('关').waitFor();
    assert.equal(await mode('关').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.proactive-body').count(), 0, 'off: no repositories, no limits');
    await mode('观察').click();
    await page.getByText('只记下她本来会做什么').waitFor();
    await mode('自动').click();
    await page.locator('.proactive-repo').getByText('/not/a/repo').waitFor();
    await page.getByText('每天最多备 5 份材料').waitFor();
    assert.equal(saved.length, 0, 'nothing is saved before Save');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.locator('.personal-settings [role=alert]').getByText('目录不是可用的 git 仓库').waitFor();
    await page.locator('.proactive-repo').getByRole('button', { name: '移除 /not/a/repo', exact: true }).click();
    assert.equal(await page.locator('.proactive-repo').count(), 0);
    await page.getByText('没有仓库时也会备材料').waitFor();
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.locator('.personal-settings').getByText('已保存', { exact: true }).waitFor();
    assert.deepEqual(saved.at(-1), { mode: 'auto', repos: [] }, 'enabled is never sent');
  } finally { await browser.close(); await daemon.stop(); }
});
