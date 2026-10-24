// RFC workspace V6/V9: navigation is keyboard accessible and never executes work.
// Synthetic browser coverage plus real disposable Todo/status readback below.
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { spawn, execFile } from 'node:child_process';
import { createInterface } from 'node:readline';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startServer, port, outputDir, repoRoot } from '../../../../examples/personal-workspace-browser/fixture.mjs';
import { launchBrowser, loadPlaywright, waitForHttp } from '../../../../examples/dashboard-browser-smoke-support.mjs';
import { openWorkspacePage, fetchWorkspaceBackend } from '../../../../examples/personal-workspace-browser/scenario-context.mjs';
import { installShowcaseWorkspace, openShowcaseGoal } from '../../../../examples/personal-workspace-browser/showcase-workspace.mjs';
import { resolveTestPython } from '../../../../scripts/test-python.mjs';

const server = await startServer();
let browser;
const url = `http://127.0.0.1:${port}/chat/?statusUrl=/status.json`;
const preview = {
  schema_version: 'loopx_chat_action_proposal_v1', proposal_id: 'palette-review-draft', action_kind: 'todo.create',
  summary: 'Review the export checklist', normalized_parameters: {goal_id: 'progress-projection', text: 'Review the export checklist'},
  context: {kind: 'goal', goal_id: 'progress-projection'}, expected_state_fingerprint: 'synthetic-palette-basis',
  permission_classification: 'durable_write', validation_evidence: ['Synthetic pending preview'],
  available_transitions: ['apply', 'cancel', 'regenerate'], status: 'preview_ready', receipt: null, stale: null,
  created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z',
};
const dialogFor = page => page.locator('dialog.workspace-command-palette');
async function openPalette(page, shortcut = 'Control+k') {
  await page.keyboard.press(shortcut);
  await dialogFor(page).waitFor({state: 'visible'});
  return dialogFor(page).getByRole('combobox');
}
try {
  await waitForHttp(url);
  await mkdir(outputDir, {recursive: true});
  browser = await launchBrowser(loadPlaywright().chromium);
  for (const locale of ['en', 'zh-CN']) {
    const session = await openWorkspacePage(browser, url, {apiOptions: {initialActionProposals: [preview]}, beforeGoto: async (api, page) => {
      await page.addInitScript(locale => localStorage.setItem('loopx-pw-locale', locale), locale);
      await installShowcaseWorkspace(api, page, {done: 23, cost: 24.8, deliveryFails: false});
    }});
    const {page} = session;
    try {
      await openShowcaseGoal(page);
      assert.equal(await dialogFor(page).count(), 0, "Closed palette must not retain searchable labels/results");
      await page.screenshot({path: resolve(outputDir, `commands-closed-${locale}.png`), animations: 'disabled'});
      const launcher = page.getByRole('button', {name: locale === 'en' ? 'Quick navigation' : '快速导航', exact: true});
      await launcher.focus();
      const writes = [];
      page.on('request', request => {if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(new URL(request.url()).pathname);});
      const search = await openPalette(page, locale === 'en' ? 'Meta+k' : 'Control+k');
      assert.equal(await search.evaluate(element => element === document.activeElement), true);
      const initialId = await search.getAttribute('aria-activedescendant');
      await search.press('ArrowDown');
      assert.notEqual(await search.getAttribute('aria-activedescendant'), initialId);
      await search.press('ArrowUp');
      assert.equal(await search.getAttribute('aria-activedescendant'), initialId);
      await search.fill('no-such-destination');
      assert.equal(await dialogFor(page).getByRole('option').count(), 0);
      assert.equal(await search.getAttribute('aria-activedescendant'), null);
      await search.press('Enter');
      assert.equal(await dialogFor(page).isVisible(), true);
      await search.fill('map legacy');
      assert.equal(await dialogFor(page).getByRole('option').count(), 1);
      await search.dispatchEvent('compositionstart');
      await search.dispatchEvent('keydown', {key: 'Enter', keyCode: 229, isComposing: true});
      await search.dispatchEvent('keydown', {key: 'Escape', keyCode: 229, isComposing: true});
      assert.equal(await dialogFor(page).isVisible(), true, 'IME confirmation must not navigate or close');
      await search.dispatchEvent('compositionend');
      await search.press('Escape');
      await dialogFor(page).waitFor({state: 'hidden'});
      assert.equal(await launcher.evaluate(element => element === document.activeElement), true);
      await launcher.click();
      await dialogFor(page).waitFor({state: 'visible'});
      await dialogFor(page).getByRole('combobox').fill('map legacy');
      await page.keyboard.press('Enter');
      await dialogFor(page).waitFor({state: 'hidden'});
      await page.waitForURL(/todo=showcase-map-columns/);
      await page.locator('[data-context-drawer]').waitFor({state: 'visible'});
      assert.match(page.url(), /todo=showcase-map-columns/);
      // The palette on top of an inspector must not close that inspector on Escape.
      await openPalette(page);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-context-drawer]').isVisible(), true);
      await page.keyboard.press('Escape');
      await page.locator('[data-context-drawer]').waitFor({state: 'hidden'});
      const previewSearch = await openPalette(page);
      await previewSearch.fill('palette-review-draft');
      await dialogFor(page).getByRole('option').waitFor({state: 'visible'});
      await previewSearch.press('Enter');
      await page.locator('[data-context-kind="proposal"]').waitFor({state: 'visible'});
      assert.deepEqual(session.api.actionApplies, [], 'opening a stored preview cannot apply it');
      assert.deepEqual(session.api.actionPreviews, [], 'opening does not create another preview');
      await page.keyboard.press('Escape');
      await openPalette(page);
      await page.screenshot({path: resolve(outputDir, `commands-open-${locale}.png`), animations: 'disabled'});
      await page.setViewportSize({width: 390, height: 844});
      await page.emulateMedia({reducedMotion: 'reduce'});
      const box = await dialogFor(page).boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.height <= 844);
      assert.equal(await dialogFor(page).evaluate(element => element.scrollWidth <= element.clientWidth), true);
      await page.screenshot({path: resolve(outputDir, `commands-narrow-${locale}.png`), animations: 'disabled'});
      // Native modal keeps keyboard focus inside it.
      for (let index = 0; index < 4; index++) {
        await page.keyboard.press('Tab');
        assert.equal(await dialogFor(page).evaluate(element => element.contains(document.activeElement)), true);
      }
      if (await page.locator('.context-help-popover:popover-open').count()) {
        await page.keyboard.press('Escape');
        assert.equal(await dialogFor(page).isVisible(), true, 'Tab-focused help closes before the palette');
      }
      await page.keyboard.press('Escape');
      await page.locator('.personal-mobile-menu').click();
      await launcher.click();
      await dialogFor(page).waitFor({state: 'visible'});
      assert.equal(await dialogFor(page).getByRole('combobox').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('Escape');
      await page.setViewportSize({width: 1440, height: 900});
      await openPalette(page);
      await dialogFor(page).getByRole('combobox').fill('Onboarding email');
      await page.keyboard.press('Enter');
      await dialogFor(page).waitFor({state: 'hidden'});
      await page.waitForURL(/goalId=product-release/);
      await openPalette(page);
      await dialogFor(page).getByRole('combobox').fill('map legacy');
      assert.equal(await dialogFor(page).getByRole('option').count(), 0, 'old Goal tasks disappear');
      // Browser history changes the Goal while the palette is open. Its old
      // search and selection must not reopen when that Goal is revisited.
      await page.goBack();
      await dialogFor(page).waitFor({state: 'hidden'});
      await page.goForward();
      await page.waitForURL(/goalId=product-release/);
      assert.equal(await dialogFor(page).isVisible(), false);
      assert.deepEqual(writes, [], 'open/search/navigation must not submit any request');
      await page.keyboard.press('Escape');
      const sessionRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/chat/sessions' && request.method() === 'POST');
      await page.getByRole('navigation', {name: /^(Goal 视图|Goal view)$/}).getByRole('button', {name: /^(Chat|对话)$/}).click();
      await sessionRequest;
      await page.getByRole('navigation', {name: /^(Goal 视图|Goal view)$/}).getByRole('button', {name: /^(Overview|概览)$/}).click();
      const resultSearch = await openPalette(page);
      await resultSearch.fill('publication-workspace-smoke');
      await dialogFor(page).getByRole('option').waitFor({state: 'visible'});
      await resultSearch.press('Enter');
      await page.locator('[data-context-kind="output"]').waitFor({state: 'visible'});
    } finally {await session.close();}
  }

  const remote = await openWorkspacePage(browser, `http://127.0.0.1:${port}/chat/?statusUrl=http%3A%2F%2F127.0.0.1%3A8976%2Fstatus.json`, {beforeGoto: async (_api, page) => {
    const snapshot = JSON.parse(await readFile(resolve(repoRoot, 'examples/status.example.json'), 'utf8'));
    await page.route('http://127.0.0.1:8976/status.json*', route => route.fulfill({json: snapshot}));
  }});
  try {
    await remote.page.locator('.personal-goal-row').first().locator('.personal-goal-link').click();
    assert.equal(await remote.page.getByRole('button', {name:/^(Settings|设置)$/}).count(), 0, 'a read-only host does not expose local management');
    await openPalette(remote.page);
    const remoteHelp = dialogFor(remote.page).getByRole('button', {name:/Navigation help|导航说明/});
    await remoteHelp.focus();
    assert.match(await remote.page.locator('.context-help-popover:popover-open').innerText(), /Read-only source|只读来源/);
    await remote.page.keyboard.press('Escape');
    assert.equal(await dialogFor(remote.page).isVisible(), true, 'Help closes before the surrounding palette');
    assert.equal(await dialogFor(remote.page).getByRole('option', {name: /Review preview|查看预览/}).count(), 0);
  } finally {await remote.close();}

  // A real owner, disposable state and an interrupted status read establish
  // more than fixture-only component coverage. No Agent/model is launched.
  const python = resolveTestPython({repoRoot});
  const root = await mkdtemp(join(tmpdir(), 'loopx-command-owner-'));
  const child = spawn(python, ['-u', resolve(repoRoot, 'examples/personal-workspace-browser/goal-workspace-todo-backend.py'), root], {
    cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot}, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const closed = once(child, 'close');
  const lines = createInterface({input: child.stdout});
  let diagnostic = '', session;
  child.stderr.on('data', chunk => {diagnostic = (diagnostic + chunk).slice(-4000);});
  try {
    const ready = await Promise.race([once(lines, 'line').then(([line]) => JSON.parse(line)), closed.then(() => {throw new Error(diagnostic);})]);
    const backend = `http://127.0.0.1:${ready.port}`;
    const cli = async (...args) => JSON.parse((await promisify(execFile)(python,
      ['-c', 'from loopx.entrypoint import main; main()', '--format', 'json', '--registry', ready.registry, 'todo', ...args],
      {cwd: repoRoot, env: {...process.env, PYTHONPATH: repoRoot}})).stdout);
    const text = '核对导出报告 Validate export report';
    const added = await cli('add', '--goal-id', 'progress-projection', '--role', 'agent', '--task-class', 'advancement_task', '--text', text);
    assert.equal(added.ok, true);
    const userTodos = [];
    for (const [taskClass, text] of [['user_gate', 'Approve export scope'], ['user_action', 'Provide export sample']]) {
      const result = await cli('add', '--goal-id', 'progress-projection', '--role', 'user', '--task-class', taskClass, '--text', text);
      assert.equal(result.ok, true);
      userTodos.push({todoId: result.todo_id, text});
    }
    let failStatus = false;
    session = await openWorkspacePage(browser, url, {beforeGoto: async (_api, page) => {
      await page.route(target => target.pathname === '/status.json', async route => {
        if (failStatus) return route.fulfill({status: 503, json: {error: 'Synthetic source outage'}});
        const response = await fetchWorkspaceBackend(route, backend);
        await route.fulfill({response});
      });
      await page.route('**/api/chat/todo/detail?*', async route => {
        const response = await fetchWorkspaceBackend(route, backend);
        await route.fulfill({response});
      });
    }});
    const {page} = session;
    await page.locator('.personal-goal-row').first().locator('.personal-goal-link').click();
    await page.getByTestId('goal-vitals').waitFor({state: 'visible'});
    const writes = [];
    page.on('request', request => {if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push(new URL(request.url()).pathname);});
    async function checkUserNavigation(target, readOnly = false) {
      for (const item of userTodos) {
        for (const query of [item.text, item.todoId]) {
          const search = await openPalette(target);
          await search.fill(query);
          assert.equal(await dialogFor(target).getByRole('option').count(), 1);
          await search.press('Enter');
          const drawer = target.locator('[data-context-kind="attention"]');
          await drawer.waitFor({state: 'visible'});
          assert.ok((await drawer.innerText()).includes(item.text));
          await drawer.locator('.personal-attention-identifiers summary').click();
          assert.equal(await drawer.locator('.personal-attention-identifiers dd').first().innerText(), item.todoId);
          if (readOnly) {
            assert.equal(await drawer.locator('.personal-decision-bar button').count(), 0);
            assert.match(await drawer.innerText(), /Read-only|read-only|只读/);
          }
          await target.keyboard.press('Escape');
          await drawer.waitFor({state: 'hidden'});
        }
      }
    }
    await checkUserNavigation(page);
    const search = await openPalette(page);
    await search.fill('核对');
    await dialogFor(page).getByRole('option', {name: /核对导出报告/}).waitFor({state: 'visible'});
    const detailRead = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chat/todo/detail');
    await search.press('Enter');
    await page.locator('[data-context-drawer]').waitFor({state: 'visible'});
    const detail = await (await detailRead).json();
    assert.equal(detail.ok, true);
    assert.equal(detail.todo_id, added.todo_id);
    assert.ok(detail.text.endsWith(text), 'the real Todo owner preserves the request after its priority marker');
    await page.locator('.personal-task-request').getByText(text, {exact: false}).waitFor({state: 'visible'});
    assert.match(page.url(), new RegExp(`todo=${added.todo_id}`));
    await page.keyboard.press('Escape');
    failStatus = true;
    await page.getByRole('button', {name: /^(Refresh status|刷新状态)$/}).click();
    await page.waitForTimeout(1500);
    await openPalette(page);
    await page.waitForFunction(() => document.querySelector('dialog[open] [role="listbox"]')?.children.length === 0);
    assert.match(await dialogFor(page).innerText(), /Source unavailable|来源不可用/);
    await page.keyboard.press('Escape');
    failStatus = false;
    await page.getByRole('button', {name: /^(Refresh status|刷新状态)$/}).click();
    await page.getByTestId('goal-vitals').waitFor({state: 'visible'});
    await openPalette(page);
    await dialogFor(page).getByRole('combobox').fill('核对');
    await dialogFor(page).getByRole('option', {name: /核对导出报告/}).waitFor({state: 'visible'});
    assert.deepEqual(writes, [], 'real user/agent Todo navigation and recovery make zero writes');
    const snapshotResponse = await fetch(`${backend}/status.json`);
    assert.equal(snapshotResponse.ok, true);
    const snapshot = await snapshotResponse.json();
    const readOnlySession = await openWorkspacePage(browser,
      `http://127.0.0.1:${port}/chat/?statusUrl=http%3A%2F%2F127.0.0.1%3A8976%2Fstatus.json`, {
        beforeGoto: async (_api, target) => {
          await target.route('http://127.0.0.1:8976/status.json*', route => route.fulfill({json: snapshot}));
        },
      });
    try {
      const target = readOnlySession.page;
      await target.locator('.personal-goal-row').first().locator('.personal-goal-link').click();
      const remoteWrites = [];
      target.on('request', request => {if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) remoteWrites.push(request.method());});
      await checkUserNavigation(target, true);
      assert.deepEqual(remoteWrites, [], 'read-only attention navigation makes zero writes');
    } finally {await readOnlySession.close();}
  } finally {
    await session?.close(); lines.close();
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM'); const kill = setTimeout(() => child.kill('SIGKILL'), 5000);
      try {await closed;} finally {clearTimeout(kill);}
    }
  }
  console.log('workspace command palette: en/zh, keyboard/IME/focus, narrow/reduced motion, navigation/no writes, real Agent/User Todo title/ID navigation, read-only attention and readback/recovery passed');
} finally {await browser?.close(); server.kill('SIGTERM');}
