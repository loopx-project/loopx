// The assistant's home: the conversation is the page, the desk a drawer. What the drawer does
// to the layout, what the phone does with it, and the one decision a dispatch card carries.
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

describe('legacy assistant workspace', { skip: 'LoopX Workbench replaces the legacy assistant and plans pages with the steward and Goals.' }, () => {

const json = async (daemon, path, method = 'GET', body) => {
  const response = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, `${method} ${path}: ${response.status}`);
  return response.status === 204 ? null : response.json();
};
const waitFor = async (check, what, ms = 60_000) => {
  const deadline = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    assert.ok(Date.now() < deadline, `timed out: ${what}`);
    await new Promise(resolve => setTimeout(resolve, 300));
  }
};
const configurePersonal = async (daemon, text = '先记一件事') => {
  await json(daemon, '/api/personal/settings', 'PUT', { agent: 'pi', directory: daemon.workdir });
  const { threadId } = await json(daemon, '/api/personal/say', 'POST', { text });
  await waitFor(async () => (await json(daemon, `/api/threads/${threadId}`)).status === 'idle', 'the Router turn');
  return threadId;
};

test('assistant thread links navigate from paragraphs, tables and inferred references on desktop and phone', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const target = await json(daemon, '/api/threads', 'POST', { agent: 'pi', cwd: daemon.workdir, title: '助手链接目标' });
    await json(daemon, `/api/threads/${target.id}/turns`, 'POST', { text: '目标会话正文' });
    await waitFor(async () => (await json(daemon, `/api/threads/${target.id}`)).status === 'idle', 'target reply');
    const markdown = [
      `普通段落：[打开目标会话](thread://${target.id})。`,
      '',
      '| 会话 | 操作 |',
      '| --- | --- |',
      `| 目标 | [查看会话](thread://${target.id}) |`,
      '',
      `已知引用：「${target.title}」（${target.id.slice(0, 8)}）。`,
      '',
      `完整 ID：${target.id}`,
    ].join('\n');
    await configurePersonal(daemon, `router-fixture-markdown ${JSON.stringify(markdown)}`);
    const shots = join(import.meta.dirname, '../shots/assistant-thread-links');
    mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({
        viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 900 },
        isMobile: phone, hasTouch: phone, colorScheme: theme,
      });
      try {
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        await openUi(page, daemon);
        for (const kind of ['paragraph', 'table', 'title', 'id', 'keyboard']) {
          await page.evaluate(() => { location.hash = '#/desk'; });
          const answer = page.locator('.chat-flow .row.assistant');
          const link = kind === 'table' ? answer.locator('td a.thread-link')
            : kind === 'title' ? answer.locator('p', { hasText: '已知引用：' }).locator('a.thread-link')
            : kind === 'id' ? answer.locator('p', { hasText: '完整 ID：' }).locator('a.thread-link')
            : answer.locator('p a.thread-link', { hasText: '打开目标会话' });
          await link.waitFor();
          assert.equal(await link.count(), 1, `${kind} renders one actionable link`);
          if (kind === 'paragraph') await page.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}.png`) });
          if (kind === 'keyboard') { await link.focus(); await page.keyboard.press('Enter'); }
          else if (phone) await link.tap();
          else await link.click();
          await page.locator('.chat-home').waitFor({ state: 'detached' });
          await page.locator('.topbar-title', { hasText: target.title }).waitFor();
          await page.locator('.row.user', { hasText: '目标会话正文' }).waitFor();
          assert.equal(await page.locator('.thread-row.active .thread-title').textContent(), target.title);
        }
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('the desk drawer rides beside the conversation on a wide screen and never moves its reading', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon, '核对发布清单与回归结果。'.repeat(300));
    await json(daemon, '/api/todos', 'POST', { text: '整理实验结论', lane: 'you', importance: 'high', dueAt: new Date().toISOString() });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    await page.locator('.chat-home').waitFor();
    await page.locator('.chat-composer textarea').waitFor();
    assert.equal(await page.locator('.chat-drawer').count(), 0, 'the home opens on the conversation alone');
    await page.locator('.chat-strip').getByText('需要你 1').waitFor();

    // '/' finds her composer from the page.
    await page.keyboard.press('/');
    await page.waitForFunction(() => Boolean(document.activeElement.closest('.chat-composer')));

    // The drawer takes real width beside the talk: no cover, no scrim, the composer stays.
    await page.locator('.chat-entries').getByRole('button', { name: '桌面', exact: true }).click();
    await page.locator('.chat-drawer.side .desk-header').waitFor();
    assert.equal(await page.locator('.chat-drawer-scrim').count(), 0, 'a wide screen gives the drawer real width, not a cover');
    assert.equal(await page.locator('.chat-composer').count(), 1);
    assert.equal(new URL(page.url()).hash, '#/desk/desk', 'the address names the open tab');

    // Closing leaves the conversation where it was read; a reload lands with the drawer as it was left.
    const flow = page.locator('.chat-flow');
    await flow.evaluate(el => { el.scrollTop = 40; });
    const reading = await flow.evaluate(el => el.scrollTop);
    assert.ok(reading > 0, 'the conversation really scrolls, so preserving its reading is exercised');
    await page.locator('.chat-drawer-head button').focus();
    await page.keyboard.press('Escape');
    await page.locator('.chat-drawer').waitFor({ state: 'detached' });
    assert.equal(await flow.evaluate(el => el.scrollTop), reading, 'closing the drawer does not scroll the conversation');
    assert.equal(new URL(page.url()).hash, '#/desk');
    await page.locator('.chat-entries').getByRole('button', { name: '桌面', exact: true }).click();
    await page.waitForURL('**/#/desk/desk');
    await page.reload();
    await page.locator('.chat-drawer.side .desk-header').waitFor();
    assert.equal(await page.locator('.chat-drawer-scrim').count(), 0);
    await page.locator('.chat-composer textarea').waitFor();
    await page.locator('.chat-drawer-head').getByRole('button', { name: '关闭', exact: true }).click();
    await page.locator('.chat-drawer').waitFor({ state: 'detached' });

    // Her session list is one drawer tab away; the router thread itself is not listed again.
    await page.locator('.chat-entries').getByRole('button', { name: '会话', exact: true }).click();
    await page.locator('.chat-drawer .chat-sessions').waitFor();
    assert.equal(await page.locator('.chat-sessions li button').count(), 0, 'the router thread itself is the conversation, not listed again');
    assert.match(await page.locator('.chat-drawer-empty').textContent(), /还没有别的会话/);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('a narrower screen covers the talk with the drawer; a phone gives it the whole page', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon);
    await json(daemon, '/api/todos', 'POST', { text: '确认合同修改', lane: 'you' });

    const page = await browser.newPage({ viewport: { width: 900, height: 800 } });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    await page.locator('.chat-home').waitFor();
    await page.locator('.chat-entries').getByRole('button', { name: '事项', exact: true }).click();
    await page.locator('.chat-drawer.over').waitFor();
    await page.locator('.chat-drawer-scrim').waitFor();
    const overlay = await page.locator('.chat-drawer.over').boundingBox();
    assert.ok(overlay.x > 0 && overlay.width < 900, 'the drawer covers only part of a mid-width screen');
    await page.locator('.chat-drawer-scrim').click({ position: { x: 5, y: 100 } });
    await page.locator('.chat-drawer').waitFor({ state: 'detached' });

    // A phone: the entries are icons, the drawer is the whole width of the page.
    const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await openUi(phone, daemon);
    await phone.evaluate(() => { location.hash = '#/desk'; });
    await phone.locator('.chat-home').waitFor();
    const label = await phone.locator('.chat-entry-label').first().boundingBox();
    assert.ok(label.width <= 2, 'a phone folds the entry labels away but keeps their names');
    await phone.locator('.chat-entries').getByRole('button', { name: '桌面', exact: true }).tap();
    await phone.locator('.chat-drawer.over .desk-matrix').waitFor();
    await phone.locator('.chat-drawer.over').evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)));
    const full = await phone.locator('.chat-drawer.over').boundingBox();
    assert.equal(Math.round(full.width), 390, 'the phone drawer takes the whole page width');
    assert.equal(full.x, 0);
    assert.equal(full.y, 0);
    assert.equal(Math.round(full.height), 844, 'the phone drawer covers the global header too');
    await phone.locator('.chat-drawer-head').getByRole('button', { name: '关闭', exact: true }).tap();
    await phone.locator('.chat-drawer').waitFor({ state: 'detached' });
    await phone.locator('.chat-composer textarea').waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});

test('a dispatch card rides in the conversation; starting it lands in its thread', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon);
    const todo = await json(daemon, '/api/todos', 'POST', { text: '检查发布结果', lane: 'you' });
    const { threadId } = await json(daemon, '/api/personal/say', 'POST', {
      text: `router-fixture-dispatch ${JSON.stringify({ agent: 'pi', cwd: daemon.workdir, task: '对照清单检查发布结果', title: '检查发布结果', matter: todo.id })}`,
    });
    const run = await waitFor(async () => (await json(daemon, '/api/personal/proactive')).runs.find(item => item.kind === 'dispatch' && item.status === 'asking'), 'the dispatch proposal');
    assert.equal(run.threadId, '');

    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    await page.locator('.chat-home').waitFor();
    const card = page.locator('.chat-ask.dispatch');
    await card.waitFor();
    await card.getByText('派活给 pi').waitFor();
    await card.getByText('对照清单检查发布结果').waitFor();

    await card.getByRole('button', { name: '开始', exact: true }).click();
    await page.locator('.chat-home').waitFor({ state: 'detached' });
    await page.getByText('对照清单检查发布结果').first().waitFor({ timeout: 15_000 });
    const started = (await json(daemon, '/api/personal/proactive')).runs.find(item => item.id === run.id);
    assert.equal(started.status, 'answered');
    assert.notEqual(started.threadId, '');
    assert.notEqual(started.threadId, threadId, 'the work gets a thread of its own');
    await page.locator('.composer textarea').waitFor();
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

test('a phone keeps one bar: the entries sit in the app bar and their badges carry the counts', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon);
    await json(daemon, '/api/todos', 'POST', { text: '检查发布清单', lane: 'you', importance: 'high', dueAt: new Date().toISOString() });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route(/\/api\/todos$/, async route => { await gate; await route.continue(); });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    const entries = page.locator('.topbar .topbar-desk .chat-entries');
    await entries.waitFor();
    assert.equal(await page.locator('.chat-top').count(), 0, 'no second bar under the app bar');
    assert.equal(await page.locator('.chat-strip').count(), 0, 'no strip row on a phone');
    assert.equal(await entries.locator('.chat-badge').count(), 0, 'an unknown count shows no badge, never a zero');
    release();
    const matters = entries.getByRole('button', { name: /事项/ });
    await matters.locator('.chat-badge').getByText('1', { exact: true }).waitFor();
    for (const [name, tab] of [[/事项/, 'matters'], [/会话/, 'sessions']]) {
      await entries.getByRole('button', { name }).tap();
      await page.locator('.chat-drawer.over').waitFor();
      assert.equal(new URL(page.url()).hash, `#/desk/${tab}`);
      await page.keyboard.press('Escape');
      await page.locator('.chat-drawer').waitFor({ state: 'detached' });
    }
    const flowTop = await page.locator('.chat-flow, .chat-fresh').first().evaluate(element => element.getBoundingClientRect().top);
    const barBottom = await page.locator('.topbar').evaluate(element => element.getBoundingClientRect().bottom);
    assert.ok(flowTop <= barBottom + 1, `the conversation starts right under the bar (${flowTop} vs ${barBottom})`);
    for (const locale of ['zh', 'en', 'ja']) {
      await page.evaluate(locale => localStorage.setItem('calli.locale', locale), locale);
      await page.reload();
      await entries.waitFor();
      const fit = await page.locator('.topbar').evaluate(bar => {
        const bounds = bar.getBoundingClientRect();
        return [...bar.querySelectorAll('button')].every(button => { const rect = button.getBoundingClientRect(); return rect.left >= bounds.left && rect.right <= bounds.right && rect.right <= innerWidth; });
      });
      assert.ok(fit, `${locale}: the bar fits a phone without clipping`);
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('dispatch can be declined without creating work, and a rejected start remains retryable', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon);
    const propose = async title => {
      await json(daemon, '/api/personal/say', 'POST', { text: `router-fixture-dispatch ${JSON.stringify({ agent: 'pi', cwd: daemon.workdir, task: title, title })}` });
      return waitFor(async () => (await json(daemon, '/api/personal/proactive')).runs.find(run => run.title === title && run.status === 'asking'), title);
    };
    const declined = await propose('先不用检查依赖');
    const retry = await propose('检查失败后可以重试');
    const count = (await json(daemon, '/api/threads')).length;
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    // Two asks fold into one tray line: open it, then open the one ask to act on.
    await page.locator('.chat-tray-head').getByText('2 个等你回答').tap();
    const rejected = page.locator(`.chat-ask[data-run="${declined.id}"]`);
    assert.equal(await page.locator('.chat-ask-detail').count(), 0, 'no ask opens by itself while several wait');
    await rejected.locator('.chat-ask-row').tap();
    await rejected.getByRole('button', { name: '不用', exact: true }).tap();
    await rejected.waitFor({ state: 'detached' });
    assert.equal((await json(daemon, '/api/threads')).length, count, 'declining starts no work thread');
    assert.equal((await json(daemon, '/api/personal/proactive')).runs.find(run => run.id === declined.id).status, 'discarded');
    let attempt = 0;
    await page.route(`**/api/personal/proactive/runs/${retry.id}/start`, route => ++attempt === 1
      ? route.fulfill({ status: 409, json: { error: 'Agent 暂时不可用', run: retry } }) : route.continue());
    // The last ask left stands open on its own.
    const card = page.locator(`.chat-ask[data-run="${retry.id}"]`);
    assert.equal(await page.locator('.chat-tray-head').count(), 0);
    await card.getByRole('button', { name: '开始', exact: true }).tap();
    await card.getByRole('alert').getByText('Agent 暂时不可用', { exact: true }).waitFor();
    assert.equal(await card.getByRole('button', { name: '开始', exact: true }).isEnabled(), true);
    await card.getByRole('button', { name: '开始', exact: true }).tap();
    await page.locator('.chat-home').waitFor({ state: 'detached' });
    assert.equal(attempt, 2);
  } finally { await browser.close(); await daemon.stop(); }
});

// Capture the selected D1 layout using real messages and matters; no screenshot-only UI.
if (process.env.LOOCHAT_ASSISTANT_SHOTS) test('assistant chat screenshot matrix', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const directory = process.env.LOOCHAT_ASSISTANT_SHOTS;
  mkdirSync(directory, { recursive: true });
  try {
    await json(daemon, '/api/personal/settings', 'PUT', { agent: 'pi', directory: daemon.workdir });
    await json(daemon, '/api/todos', 'POST', { text: '确认发布窗口', lane: 'you', importance: 'high', dueAt: new Date().toISOString() });
    await json(daemon, '/api/todos', 'POST', { text: '整理回归测试结果', lane: 'doing' });
    for (const conversation of ['fresh', 'conversation']) {
      if (conversation === 'conversation') {
        const { threadId } = await json(daemon, '/api/personal/say', 'POST', { text: '先看看今天有哪些事情需要我决定。' });
        await waitFor(async () => (await json(daemon, `/api/threads/${threadId}`)).status === 'idle', 'assistant reply');
      }
      for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) {
        const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, colorScheme });
        await openUi(page, daemon);
        await page.evaluate(() => { location.hash = '#/desk'; });
        await page.locator(conversation === 'fresh' ? '.chat-fresh' : '.chat-flow .row').first().waitFor();
        if (width === 390) await page.locator('.chat-entries .chat-badge').getByText('1', { exact: true }).waitFor();
        else await page.locator('.chat-strip').getByRole('button', { name: '需要你 1', exact: true }).waitFor();
        await page.locator('.chat-composer textarea').waitFor();
        for (const drawer of ['closed', 'open']) {
          if (drawer === 'open') {
            await page.locator('.chat-entries').getByRole('button', { name: '桌面', exact: true }).click();
            await page.locator('.chat-drawer .desk-header').waitFor();
          }
          await page.mouse.move(0, 0);
          await page.screenshot({ path: join(directory, `${conversation}-${width}-${colorScheme}-${drawer}.png`), fullPage: true, animations: 'disabled' });
        }
        await page.close();
      }
    }
  } finally { await browser.close(); await daemon.stop(); }
});

for (const state of ['answered', 'starting']) test(`a dispatch start conflict ${state} opens only the existing work thread`, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await configurePersonal(daemon);
    await json(daemon, '/api/personal/say', 'POST', { text: `router-fixture-dispatch ${JSON.stringify({ agent: 'pi', cwd: daemon.workdir, task: '核对发布清单', title: '核对发布清单' })}` });
    const run = await waitFor(async () => (await json(daemon, '/api/personal/proactive')).runs.find(item => item.kind === 'dispatch' && item.status === 'asking'), 'dispatch');
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await openUi(page, daemon);
    await page.evaluate(() => { location.hash = '#/desk'; });
    const card = page.locator(`.chat-ask[data-run="${run.id}"]`);
    await card.waitFor();
    let started;
    let posts = 0;
    await page.route(`**/api/personal/proactive/runs/${run.id}/start`, async route => {
      posts++;
      if (state === 'answered') started = await json(daemon, `/api/personal/proactive/runs/${run.id}/start`, 'POST');
      await route.fulfill({ status: 409, json: { error: state === 'starting' ? 'Starting elsewhere' : '已经开始', ...(state === 'starting' ? { starting: true } : {}), run: started ?? run } });
    });
    await card.getByRole('button', { name: '开始', exact: true }).click();
    if (state === 'starting') {
      await card.locator('button[aria-busy="true"]:disabled').waitFor();
      assert.equal(new URL(page.url()).hash, '#/desk', 'no thread to navigate to until the concurrent start commits');
      started = await json(daemon, `/api/personal/proactive/runs/${run.id}/start`, 'POST');
    }
    await page.locator('.chat-home').waitFor({ state: 'detached' });
    await page.locator('.composer textarea').waitFor();
    assert.equal(posts, 1, 'recovering a conflict never repeats POST start');
    assert.ok(started.threadId);
    assert.equal((await json(daemon, '/api/personal/proactive')).runs.find(item => item.id === run.id).threadId, started.threadId);
  } finally { await browser.close(); await daemon.stop(); }
});

});
