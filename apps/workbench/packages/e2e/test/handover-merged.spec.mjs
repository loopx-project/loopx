import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const shots = '/tmp/calli-handover-merged-shots';
const chainDialog = async page => {
  await page.getByLabel('这个线程的更多操作').click();
  await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
  await page.getByRole('dialog', { name: '交接记录', exact: true }).waitFor();
};
test('three handover segments load on upward navigation, keep their action targets and send to the latest recipient', async () => {
  const hub = await startDaemon();
  let browser;
  try {
    const post = async (path, body) => {
      const response = await api(hub, path, { method: 'POST', body: JSON.stringify(body) });
      assert.equal(response.status, 201); return response.json();
    };
    const a = await post('/api/threads', { agent: 'pi', cwd: hub.workdir, title: '交接链连续对话' });
    const b = await post(`/api/threads/${a.id}/handover-thread`, { agent: 'claude', mode: 'continue' });
    const c = await post(`/api/threads/${b.id}/handover-thread`, { agent: 'agy', mode: 'continue' });
    const forked = await post('/api/threads', { agent: 'pi', cwd: hub.workdir, title: '分叉目标' });
    const db = new DatabaseSync(join(hub.dataDir, 'hub.db'));
    const insert = db.prepare('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)');
    for (const [segment, count, name] of [[a, 4, 'A'], [b, 10, 'B'], [c, 10, 'C']]) {
      let seq = Number(db.prepare('SELECT MAX(seq) AS n FROM events WHERE thread_id=?').get(segment.id).n ?? 0);
      const event = (turn, type, data) => insert.run(segment.id, ++seq, turn, type, new Date(1760000000000 + seq * 1000).toISOString(), JSON.stringify(data));
      for (let i = 1; i <= count; i++) {
        const turn = `${name}-${i}`;
        event(turn, 'turn.started', { text: i === 1 && name !== 'A' ? `交接正文 ${name}\n保留原工作目录、分支和未提交改动。\n` + 'DEFERRED_HANDOVER '.repeat(5000) : `${name} 问题 ${i}` });
        event(turn, 'message.delta', { text: `${name} 回答 ${i}：已检查实现和验证范围。\n\n继续完善按需读取，保留原会话的操作归属。` });
        event(turn, 'turn.done', {});
      }
      db.prepare('UPDATE threads SET agent_session_id=? WHERE id=?').run(`fake-${name}`, segment.id);
    }
    // Only B's last turn exhausted its plan. C's divider uses the code, independent of instance type.
    const seq = Number(db.prepare('SELECT MAX(seq) AS n FROM events WHERE thread_id=?').get(b.id).n);
    db.prepare("UPDATE events SET type='turn.error', data=? WHERE thread_id=? AND seq=?").run(JSON.stringify({ code: 'plan_exhausted', message: 'usage limit' }), b.id, seq);
    // These seeded turns happened before the handovers, so retain their source boundaries.
    for (const [recipient, source] of [[b, a], [c, b]]) db.prepare("UPDATE threads SET handover=json_set(handover, '$.sourceSeq', (SELECT MAX(seq) FROM events WHERE thread_id=?)) WHERE id=?").run(source.id, recipient.id);
    db.close();
    const agents = await (await api(hub, '/api/agents')).json();
    const label = id => agents.find(agent => agent.id === id).label;
    browser = await launchBrowser(); mkdirSync(shots, { recursive: true });
    for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
      try {
        const page = await context.newPage(); const requests = [], errors = [];
        page.on('request', req => requests.push(req.url())); page.on('pageerror', error => errors.push(error.message));
        const openLatest = async () => {
          if (phone) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
          await page.locator('.handover-group > .thread-row .thread-item').click();
          await page.getByText('C 问题 10', { exact: true }).waitFor();
          await page.waitForTimeout(150);
        };
        const scrollUp = async () => {
          await page.locator('.scroller').evaluate(el => { el.dispatchEvent(new WheelEvent('wheel', { deltaY: -300, bubbles: true })); el.scrollTop = 0; });
        };
        await openUi(page, hub); await openLatest();
        assert.equal(requests.filter(url => url.includes(`/threads/${b.id}/history`)).length, 0, 'opening never fetches ancestors');
        assert.equal(requests.filter(url => url.includes('/history/handover?text=1')).length, 0);
        await scrollUp();
        const cBoundary = page.locator(`[data-handover-thread="${c.id}"]`);
        await cBoundary.getByText(`接手自 ${label('claude')} · 额度用完`, { exact: true }).waitFor();
        assert.equal(requests.filter(url => url.includes(`/threads/${b.id}/history`)).length, 0, 'own earlier page arrives before the source');
        assert.equal(await page.locator('.bubble', { hasText: 'DEFERRED_HANDOVER' }).count(), 0);
        await cBoundary.locator('.handover-card-head').click();
        await cBoundary.locator('.handover-card-body', { hasText: 'DEFERRED_HANDOVER' }).waitFor();
        assert.equal(requests.filter(url => url.includes(`/threads/${c.id}/history/handover?text=1`)).length, 1);
        await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${theme}-expanded.png` });
        await cBoundary.locator('.handover-card-body').evaluate(el => { el.scrollTop = 300; el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, bubbles: true })); });
        await page.waitForTimeout(100);
        assert.equal(requests.filter(url => url.includes(`/threads/${b.id}/history`)).length, 0, 'scrolling inside the summary does not load the source');
        await cBoundary.locator('.handover-card-head').click();
        await page.waitForTimeout(100);
        assert.equal(requests.filter(url => url.includes(`/threads/${b.id}/history`)).length, 0, 'collapsing a long summary is not upward navigation');
        await scrollUp(); await page.getByText('B 问题 10', { exact: true }).waitFor();
        assert.equal(requests.filter(url => url.includes(`/threads/${a.id}/history`)).length, 0);
        await scrollUp();
        const bBoundary = page.locator(`[data-handover-thread="${b.id}"]`);
        await bBoundary.getByText(`接手自 ${label('pi')}`, { exact: true }).waitFor();
        // A transient failure stays different from an empty history, and retry is explicit.
        let fail = true;
        await page.route(`**/api/threads/${a.id}/history?*`, async route => {
          if (fail) { fail = false; await route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"test unavailable"}' }); }
          else await route.continue();
        });
        await scrollUp(); await page.getByText('上一段加载失败', { exact: true }).waitFor();
        await page.locator('.older-messages').click();
        await page.getByText('A 问题 1', { exact: true }).waitFor();
        await page.getByText('已到交接链开头', { exact: true }).waitFor();
        assert.equal(await page.locator('.history-segment').count(), 3);
        await bBoundary.scrollIntoViewIfNeeded(); await page.waitForTimeout(150);
        await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${theme}-chain.png` });
        if (!phone) {
          await page.locator('.tick').first().click();
          await cBoundary.locator('.found').or(page.locator(`[data-handover-thread="${c.id}"].found`)).waitFor();
        }
        await cBoundary.scrollIntoViewIfNeeded(); await page.waitForTimeout(150);
        await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${theme}-exhausted.png` });
        // Every action under A's answer must use A's id even while C is selected.
        let forkRequest;
        await page.route(`**/api/threads/${a.id}/fork`, async route => {
          forkRequest = { url: route.request().url(), body: route.request().postDataJSON() };
          await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(forked) });
        });
        const source = page.locator(`[data-history-thread="${a.id}"]`);
        await source.getByRole('button', { name: '分叉', exact: true }).last().click();
        await page.waitForFunction(() => document.querySelector('.topbar-title')?.textContent?.includes('分叉目标'));
        assert.ok(forkRequest.url.endsWith(`/threads/${a.id}/fork`));
        assert.ok(JSON.stringify(forkRequest.body).includes('A-4'));
        await openLatest();
        await chainDialog(page); await page.getByRole('menuitem', { name: '只看这一段', exact: true }).click();
        await page.getByText('C 问题 10', { exact: true }).waitFor();
        await scrollUp(); await page.locator('.bubble', { hasText: 'DEFERRED_HANDOVER' }).waitFor();
        assert.equal(await page.locator('.handover-boundary').count(), 0);
        await chainDialog(page); await page.getByRole('menuitem', { name: '显示整条链', exact: true }).click();
        await page.getByText('C 问题 10', { exact: true }).waitFor();
        assert.equal(await page.locator('.bubble', { hasText: 'DEFERRED_HANDOVER' }).count(), 0);
        // Sidebar member navigation opens that segment's merged view, with C as input recipient.
        if (phone) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
        await page.locator('.handover-toggle').click();
        await page.locator('.handover-member').filter({ hasText: '原始' }).click();
        await page.getByText('A 问题 4', { exact: true }).waitFor();
        await page.getByText('前段只读；消息发送给最新一段', { exact: true }).waitFor();
        await page.waitForTimeout(300);
        await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${theme}-earlier.png` });
        // Reading on from the earlier segment needs no click: the next ones load, from their beginning, as the end comes into view.
        // (The phone's floating assistant button can end up over the send button after this much scrolling, so only the desktop runs it.)
        if (!phone) {
          for (let i = 0; i < 10 && await page.getByText('C 问题 10', { exact: true }).count() === 0; i++) {
            await page.locator('.scroller').evaluate(el => { el.scrollTop = el.scrollHeight; });
            await page.waitForTimeout(250);
          }
          await page.getByText('C 问题 10', { exact: true }).waitFor();
          assert.equal(await page.locator('.history-segment').count(), 3, 'the segments after the one on screen follow it');
          await page.getByText('B 回答 1：已检查实现和验证范围。', { exact: true }).first().waitFor();
          await page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${theme}-earlier-forward.png` });
        }
        const sendText = `从早段发送 ${phone}-${theme}`;
        const targetRequest = page.waitForRequest(req => req.method() === 'POST' && req.url().endsWith(`/threads/${c.id}/turns`));
        await page.locator('.composer textarea').fill(sendText);
        await page.locator('.composer .send-btn:not(.stop)').click();
        assert.equal((await targetRequest).postDataJSON().text, sendText);
        await page.getByText(sendText, { exact: true }).waitFor();
        assert.deepEqual(errors, []);
      } catch (error) {
        const page = context.pages()[0];
        await page?.screenshot({ path: `${shots}/failure.png` });
        console.log((await page?.locator('.scroller').innerText())?.slice(0, 1500));
        throw error;
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await hub.stop(); }
});
