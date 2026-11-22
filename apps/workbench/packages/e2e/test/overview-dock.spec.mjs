import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

let hub, browser, thread;
const json = async (path, body, method = body ? 'POST' : 'GET') => {
  const response = await api(hub, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  assert.ok(response.ok, await response.clone().text()); return response.json();
};
const idle = async id => {
  for (let n = 0; n < 200; n++) {
    if ((await json(`/api/threads/${id}`)).status !== 'running') return;
    await new Promise(r => setTimeout(r, 50));
  }
  assert.fail('thread did not settle');
};
before(async () => {
  hub = await startDaemon({ FAKE_ACP_VISIBLE_ECHO: '1' }); browser = await launchBrowser();
  thread = await json('/api/threads', { agent: 'claude', cwd: hub.workdir, title: '变更与产物' });
  await json(`/api/threads/${thread.id}/turns`, { text: 'edit file make outputs plan' }); await idle(thread.id);
});
after(async () => { await browser?.close(); await hub?.stop(); });
const shots = process.env.LOOCHAT_E2E_SHOTS_DIR;
const capture = async (page, name) => { if (shots) { mkdirSync(shots, { recursive: true }); await page.screenshot({ path: join(shots, name + '.png'), animations: 'disabled' }); } };
const openGroup = async (page, name) => {
  const trigger = page.locator('.topbar-overview');
  if (!(await page.locator('.overview-panel:visible').count()) && await trigger.getAttribute('aria-pressed') !== 'true') await trigger.click();
  await page.locator('.overview-panel .thread-overview[data-ready=true]').waitFor();
  const group = page.locator('.overview-group').filter({ has: page.locator('.overview-toggle strong', { hasText: new RegExp(`^${name}$`) }) });
  const all = group.getByRole('button', { name: '查看全部', exact: true });
  if (await all.isVisible()) await all.click(); else await group.locator('.overview-toggle').click();
  await page.locator('.overview-details-page').waitFor();
};

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`overview details, diffs, pages and pictures keep their owner: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme });
    const page = await context.newPage(); page.setDefaultTimeout(12_000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const name = `${phone ? 'phone' : 'desktop'}-${colorScheme}`;
    try {
      await openUi(page, hub);
      if (phone) await page.locator('.menu-btn').click();
      await page.locator('.thread-row', { hasText: thread.title }).click();
      const draft = page.locator('main .composer textarea');
      await draft.fill('保留主对话的草稿');
      await openGroup(page, '变更');
      const list = page.locator('.overview-details-page');
      assert.equal(await list.locator('..').evaluate(el => el.classList.contains('dock-panel')), !phone);
      await list.locator('.overview-row', { hasText: 'app.ts' }).click();
      const diff = page.locator('.change-diff-dialog');
      await diff.locator('.diff-line.add', { hasText: 'const c = 4' }).waitFor();
      assert.equal(await page.locator('.dialog-scrim:visible').count(), phone ? 1 : 0);
      assert.equal(await page.locator('.dock-panel:visible').count(), phone ? 0 : 1);
      assert.equal(await draft.inputValue(), '保留主对话的草稿');
      await capture(page, `${name}-diff`);
      if (!phone) {
        const dock = page.locator('.dock-panel:visible'); const width = (await dock.boundingBox()).width;
        const edge = await dock.getByRole('separator').boundingBox();
        await page.mouse.move(edge.x + 3, edge.y + 180); await page.mouse.down();
        await page.mouse.move(edge.x + 53, edge.y + 180, { steps: 3 }); await page.mouse.up();
        assert.ok((await dock.boundingBox()).width < width - 40);
        await diff.getByRole('button', { name: '返回', exact: true }).click();
      } else await diff.getByRole('button', { name: '关闭', exact: true }).click();
      await list.waitFor();
      if (!phone) assert.ok(Math.abs((await page.locator('.dock-panel:visible').boundingBox()).width - await page.evaluate(() => Number(localStorage.getItem('calli.files.width')))) <= 2, 'returning to the list keeps the resized shared width');
      await list.locator('.overview-details-head button').click(); await page.locator('.overview-panel:visible').waitFor();
      await openGroup(page, '文件');
      // An unavailable HTML preview retries in place and keeps its frame through parent updates.
      await page.route(`**/threads/${thread.id}/preview-link?*`, route => route.fulfill({ status: 503, json: { error: 'preview temporarily unavailable' } }), { times: 1 });
      await list.locator('.overview-row', { hasText: 'report.html' }).click();
      const preview = page.locator('.preview');
      await preview.getByRole('alert').waitFor();
      await preview.getByRole('button', { name: '重试', exact: true }).click();
      await page.frameLocator('.preview-frame').getByRole('heading', { name: 'Thread report' }).waitFor();
      assert.equal(await page.getByText('preview temporarily unavailable', { exact: true }).count(), 0, 'successful retry clears the error everywhere');
      const frame = await page.locator('.preview-frame').elementHandle();
      await json(`/api/threads/${thread.id}`, { title: thread.title }, 'PATCH');
      await page.waitForTimeout(250);
      assert.equal(await frame.evaluate(el => el.isConnected), true, 'streaming metadata must not reload the preview');
      if (!phone) assert.equal(await page.locator('.preview-scrim').count(), 0);
      await capture(page, `${name}-page`);
      await preview.getByRole('button', { name: phone ? '关闭预览' : '返回', exact: true }).click();
      await list.waitFor(); await list.locator('.overview-details-head button').click(); await page.locator('.overview-panel:visible').waitFor();
      await openGroup(page, '图片');
      await list.locator('.overview-picture-row img').waitFor();
      if (!phone) await page.route(`**/threads/${thread.id}/files?*`, route => route.fulfill({ status: 503, json: { error: 'image temporarily unavailable' } }), { times: 1 });
      await list.locator('.overview-picture-row').click();
      if (!phone) {
        const picture = page.locator('.picture-dock');
        await picture.getByRole('alert').waitFor();
        await picture.getByRole('button', { name: '重试', exact: true }).click();
        await picture.locator('img').waitFor();
        assert.equal(await page.locator('.lightbox').count(), 0);
        await capture(page, `${name}-picture`);
        await picture.getByRole('button', { name: '放大查看', exact: true }).click();
      }
      await page.locator('.lightbox').waitFor();
      await page.locator('.lightbox-close').click();
      if (!phone) { await page.keyboard.press('Escape'); await list.waitFor(); }
      await list.locator('.overview-details-head button').click(); await page.locator('.overview-panel:visible').waitFor();
      assert.equal(await draft.inputValue(), '保留主对话的草稿');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(errors, []);
    } catch (error) { await capture(page, `${name}-failure`); throw error; }
    finally { await context.close(); }
  });
}

test('overview chain jumps read the handover boundary and explicitly expose follow-ups', async () => {
  const source = await json('/api/threads', { agent: 'claude', cwd: hub.workdir, title: '交接前的工作' });
  const next = await json(`/api/threads/${source.id}/handover-thread`, { agent: 'pi', mode: 'continue' });
  const db = new DatabaseSync(join(hub.dataDir, 'hub.db'));
  const insert = db.prepare('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)');
  let seq = Number(db.prepare('SELECT MAX(seq) AS n FROM events').get().n ?? 0);
  const event = (id, turn, type, data) => insert.run(id, ++seq, turn, type, new Date(1760000000000 + seq * 1000).toISOString(), JSON.stringify(data));
  for (let n = 1; n <= 12; n++) {
    event(source.id, `old-${n}`, 'turn.started', { text: `交接前问题 ${n}` });
    event(source.id, `old-${n}`, 'message.delta', { text: `交接前回答 ${n}` });
    event(source.id, `old-${n}`, 'turn.done', {});
  }
  const boundary = seq;
  db.prepare("UPDATE threads SET handover=json_set(handover, '$.sourceSeq', ?) WHERE id=?").run(boundary, next.id);
  event(source.id, 'later', 'turn.started', { text: '交接后的补充' });
  event(source.id, 'later', 'message.delta', { text: '后续结果应按需查看' });
  event(source.id, 'later', 'turn.done', {});
  event(next.id, 'new', 'turn.started', { text: '继续工作' });
  event(next.id, 'new', 'message.delta', { text: '接手后的回答' });
  event(next.id, 'new', 'turn.done', {});
  db.close();
  for (const phone of [false, true]) {
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone });
    const page = await context.newPage(); page.setDefaultTimeout(12_000);
    const requests = []; page.on('request', req => requests.push(req.url()));
    try {
      await openUi(page, hub);
      await page.evaluate(id => { location.hash = `#/thread/${id}`; }, next.id);
      await page.getByText('接手后的回答', { exact: true }).waitFor();
      await openGroup(page, '接力');
      await page.locator('.overview-details-page .overview-member[title="交接前的工作"]').click();
      await page.getByText('交接前回答 12', { exact: true }).waitFor();
      assert.equal(await page.getByText('后续结果应按需查看', { exact: true }).count(), 0);
      assert.ok(requests.some(url => url.includes(`/threads/${source.id}/history?`) && new URL(url).searchParams.get('before') === String(boundary + 1)));
      if (!phone) await page.locator('.dock-panel .overview-member[aria-current=true]', { hasText: source.title }).waitFor();
      await page.getByRole('button', { name: '查看交接后的后续消息', exact: true }).click();
      await page.getByText('后续结果应按需查看', { exact: true }).waitFor();
    } catch (error) { await capture(page, `chain-${phone}-failure`); throw error; }
    finally { await context.close(); }
  }
});
