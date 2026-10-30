// Packaged Chat and real SQLite/Inbox; synthetic data, no model or external effects.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {testPythonCommand} from '../../../../scripts/test-python.mjs';
import {launchBrowser, loadPlaywright} from '../../../../examples/dashboard-browser-smoke-support.mjs';
const repo = resolve(import.meta.dirname, '../../../..');
const output = resolve(repo, 'output/playwright/delegation-search');
await mkdir(output, {recursive: true});
const fixturePath = resolve(repo, 'examples/personal-workspace-browser/steward_requests_fixture.py');
const command = testPythonCommand(`import runpy; runpy.run_path(${JSON.stringify(fixturePath)}, run_name="__main__")`, {repoRoot: repo});
const child = spawn(command.command, [...command.args, '--status-file', resolve(repo, 'examples/status.example.json'),
  ...(process.env.LOOPX_SEARCH_ASSETS ? ['--assets-dir', process.env.LOOPX_SEARCH_ASSETS] : [])],
  {cwd: repo, env: {...process.env, LOOPX_USAGE_PING: '0'}, stdio: ['pipe', 'pipe', 'pipe']});
let stderr = '';
child.stderr.on('data', value => stderr += value);
const queue = [], waiting = [];
createInterface({input: child.stdout}).on('line', line => {
  try {const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else queue.push(value);} catch { /* startup banner */ }
});
const next = () => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(Error(`Fixture timeout: ${stderr}`)), 90000);
  const receive = value => {clearTimeout(timer); resolve(value);};
  if (queue.length) receive(queue.shift()); else waiting.push(receive);
});
async function fixture(action) {child.stdin.write(action + '\n'); return next();}
let browser, page;
try {
  const ready = await next(); assert.equal(ready.event, 'ready');
  const source = await fetch(ready.origin + '/api/chat/app-steward');
  assert.equal(source.status, 200, 'The fixture must install the real conversation source owner');
  assert.deepEqual(await source.json(), {ok: true, binding_id: null, session_id: null});
  browser = await launchBrowser(loadPlaywright().chromium);
  page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await page.addInitScript(() => {if (!localStorage.getItem('loopx-pw-locale')) localStorage.setItem('loopx-pw-locale', 'zh-CN');});
  const writes = []; page.on('request', request => {if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname);});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  // The fixture serves the conversation source owner, so the page reads the
  // real answer. An interception here would hide the later source refusal.
  await page.goto(ready.origin + '/chat/');
  await page.getByRole('navigation', {name: '管家视图'}).getByRole('button', {name: /^(Chat|对话)$/}).click();
  await page.getByText('复核迁移说明中的示例', {exact: true}).filter({visible: true}).first().waitFor();
  const trigger = page.getByRole('button', {name: /委派，.*条待跟进/});
  await trigger.click();
  const dialog = page.getByRole('dialog', {name: '委派', exact: true});
  await dialog.locator('.conversation-request-row').first().waitFor();
  if (process.env.LOOPX_SEARCH_BEFORE === '1') {
    await page.screenshot({path: resolve(output, 'before-desktop.png')});
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: resolve(output, 'before-narrow.png')});
  } else {
    const input = dialog.getByRole('searchbox', {name: '搜索委派'});
    const search = async text => {await input.fill(text); await dialog.getByRole('button', {name: '搜索', exact: true}).click();};
    const rows = count => page.waitForFunction(expected => document.querySelectorAll('.conversation-request-row').length === expected && document.querySelector('.conversation-requests-body')?.getAttribute('aria-busy') === 'false', count);
    const settled = () => page.waitForFunction(() => document.querySelector('.conversation-requests-body')?.getAttribute('aria-busy') === 'false');
    await settled(); await page.screenshot({path: resolve(output, 'after-desktop.png')});
    await rows(12);
    await search('核对账单导出的字段映射'); await rows(1);
    await search('核对账单导出的字段映射'); await settled();
    await rows(1);
    assert.equal(await trigger.getAttribute('aria-label'), '委派，13 条待跟进');
    await dialog.locator('.conversation-request-row > summary').click();
    await dialog.getByText('原对话', {exact: true}).click();
    await dialog.locator('.conversation-request-detail article').first().waitFor();
    await page.screenshot({path: resolve(output, 'search-original.png')});
    await search('no matching request'); await settled();
    await dialog.getByText('未找到匹配的委派。', {exact: true}).waitFor();
    await dialog.getByRole('button', {name: '清空', exact: true}).click(); await settled();
    await rows(12);
    await page.setViewportSize({width: 390, height: 844});
    const more = dialog.getByRole('button', {name: '更多记录', exact: true});
    assert.ok(await more.evaluate(node => {
      const box = node.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight;
    }), 'The next page must be reachable without scrolling through the current page');
    await more.focus(); await page.keyboard.press('Enter'); await settled();
    await rows(13);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), '已显示 13 / 13',
      'Finishing keyboard pagination keeps focus on its result');
    await page.setViewportSize({width: 1440, height: 1000});
    await fixture('fault'); await search('reviewer'); await settled();
    await dialog.getByRole('alert').waitFor();
    await rows(0);
    await page.screenshot({path: resolve(output, 'read-failure.png')});
    await fixture('recover'); await dialog.getByRole('button', {name: '重试', exact: true}).click(); await settled();
    await rows(4);
    await fixture('local-source-unavailable');
    const denied = await fetch(`${ready.origin}/api/chat/request-inventory?session_id=${ready.session_id}`);
    assert.equal(denied.status, 400);
    assert.equal((await denied.json()).error_code, 'request_inventory_unavailable');
    await dialog.getByRole('button', {name: '刷新委派', exact: true}).click(); await settled();
    await dialog.getByRole('alert').waitFor(); await rows(0);
    assert.equal(await trigger.getAttribute('aria-label'), '委派，— 条待跟进');
    assert.equal(await dialog.locator('.conversation-requests-pagination').count(), 0);
    // Changing the filter must not turn the same missing source into a new
    // empty workspace, nor may an unsuccessful retry lose that distinction.
    await search('builder'); await settled(); await dialog.getByRole('alert').waitFor();
    await dialog.getByRole('button', {name: '重试', exact: true}).click(); await settled();
    await dialog.getByRole('alert').waitFor(); await rows(0);
    await fixture('local-source-recover');
    await dialog.getByRole('button', {name: '重试', exact: true}).click(); await settled(); await rows(9);
    await search('reviewer'); await rows(4);
    assert.equal((await fixture('inspect')).source_unchanged, true);
    await fixture('deliver'); await search('复核迁移说明中的示例'); await settled();
    await dialog.getByText('未找到匹配的委派。', {exact: true}).waitFor();
    await dialog.getByRole('button', {name: '全部记录', exact: true}).click(); await settled();
    await rows(1);
    await dialog.getByText('已回传', {exact: true}).waitFor();
    let release, observed;
    const seen = new Promise(resolve => observed = resolve), hold = new Promise(resolve => release = resolve);
    await page.route('**/api/chat/request-inventory?*', async route => {
      if (new URL(route.request().url()).searchParams.get('search') !== 'builder') return route.continue();
      const response = await route.fetch(); observed(); await hold;
      await route.fulfill({response}).catch(() => {});
    });
    await search('builder'); await seen;
    await dialog.getByRole('button', {name: '清空', exact: true}).click(); await settled();
    release(); await page.unroute('**/api/chat/request-inventory?*');
    assert.equal(await input.inputValue(), '');
    await rows(12);
    await page.keyboard.press('Escape'); assert.equal(await trigger.evaluate(node => node === document.activeElement), true);
    await trigger.click(); await settled(); await search('reviewer'); await settled();
    await page.keyboard.press('Escape');
    await page.locator('.personal-goal-link').first().click();
    await page.locator('.personal-manager-link').click();
    await trigger.click(); await rows(12);
    assert.equal(await input.inputValue(), '', 'a different workspace must not retain the query');
    await search('reviewer'); await rows(4);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
    await page.screenshot({path: resolve(output, 'after-narrow.png')});
    await page.keyboard.press('Escape');
    await page.evaluate(() => localStorage.setItem('loopx-pw-locale', 'en'));
    await page.reload(); await page.getByRole('button', {name: /Delegations,.*awaiting follow-up/}).click();
    await page.getByRole('searchbox', {name: 'Search delegations'}).waitFor();
    await fixture('source-unavailable'); await page.reload();
    assert.equal((await fetch(ready.origin + '/api/chat/app-steward')).status, 409);
    await page.getByRole('alert').filter({hasText: 'The original steward conversation is unavailable.'}).waitFor();
    assert.equal(await page.getByRole('button', {name: /Delegations,.*awaiting follow-up/}).count(), 0);
    await fixture('source-recover'); await page.getByRole('button', {name: 'Retry history', exact: true}).click();
    await page.getByRole('button', {name: /Delegations,.*awaiting follow-up/}).click();
    await page.getByRole('searchbox', {name: 'Search delegations'}).waitFor();
    const english = page.getByRole('dialog', {name: 'Delegations', exact: true});
    await english.getByRole('button', {name: 'All records', exact: true}).click(); await rows(12);
    // A real return changes ordering and the snapshot while More is reading.
    // Rebuild from the beginning, but fulfill this click's requested extent;
    // the parent message refresh must not replace it with the old page count.
    await fixture('report-window');
    const nextPage = english.getByRole('button', {name: 'More records', exact: true});
    await nextPage.focus(); await page.keyboard.press('Enter'); await rows(13);
    assert.equal(await english.getByRole('button', {name: 'More records', exact: true}).count(), 0);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), '13 / 13 shown');
    await english.getByRole('button', {name: 'Awaiting follow-up', exact: true}).click(); await rows(11);
    assert.equal(writes.some(path => /request-(?:inventory|control)|team-work/.test(path)), false);
    assert.deepEqual(errors, []);
    console.log('PASS: packaged search, pre-pagination match, count, original conversation, clear/paging, failure/retry, returned history, stale response, Escape/focus, workspace isolation, narrow layout, English, source refusal/recovery, changed-snapshot More and read-only receipts.');
  }
} catch (error) {
  if (page) {await page.screenshot({path: resolve(output, 'failure.png')}).catch(() => {}); await writeFile(resolve(output, 'failure.txt'), await page.locator('body').innerText());}
  throw error;
} finally {await browser?.close(); child.stdin.write('stop\n'); child.stdin.end();}
